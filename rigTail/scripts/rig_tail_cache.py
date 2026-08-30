"""
rig_tail_cache.py
author: Daisy Jane @gnitemouse

Cache operations for Rig Tail.
Centralized cache management using rig_tail_constants for persistent state.

Decide upon re-rig whether the previous rig can be reused
or must be fully torn down. Rebuild is needed (joints changed) if any of:

    1. There's no cached BN joint list for this rigname in rt_constants.JOINTS_BN
        (fresh session or first build)
    2. Any cached BN/FK/IK joint no longer exists in scene
    3. Any BN joint moved more than JOINT_POS_TOLERANCE (scene units) from
        the positions stored in rt_constants.LAST_BUILD['joints_pos'] at the last
        build. Stored positions are refreshed on every call, so float drift
        from the rig driving the joints never accumulates into a rebuild.

Also owns the rig-part roster question every phase asks: which parts is
this run acting on (active_parts), and which are held back (excluded_parts).
Both live here because this is the leaf module Setup, cleanup, build and
connect all already import.

Functions:
    active_parts: Parts the run acts on (RIGPARTS minus RIGPARTS_EXCLUDE)
    excluded_parts: Parts held back from this run
    include_parts: Lift the exclusion on explicitly named parts
    validate_cache: Clear cache if RIGPARTS or ROOT changed
    validate_cache_structure: Check if control counts changed
    validate_cache_joints: Check if cached joints still exist
    cache_controls_ik: Cache IK controls for a rigname
    clear_control_cache: Clear all cached controls
    get_cached_controls_ik: Get cached IK controls
"""

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import math

logger = logger_setup(__name__)

# Module-level control cache
_CONTROL_CACHE = {}


def active_parts():
    """
    Rig parts the build should act on: RIGPARTS minus RIGPARTS_EXCLUDE.

    Every build phase (cleanup, setup, build, connect) iterates this
    instead of RIGPARTS, so an excluded part is left exactly as it is -
    neither torn down nor rebuilt. Excluded names stay in RIGPARTS, so
    the roster-level questions (is the dashboard warranted, which cog
    attributes exist, which override conditions are stale) still see them.

    Falls back to the full RIGPARTS list on a session started before
    active_rigparts existed, since rig_tail_constants is never reloaded.

    Return:
        list: included rig part names, in RIGPARTS order
    """
    getter = rt_constants.active_rigparts
    return getter() if callable(getter) else list(rt_constants.RIGPARTS)


def excluded_parts():
    """
    Rig parts held back from the build, in RIGPARTS order.

    Return:
        list: excluded rig part names
    """
    active = set(active_parts())
    return [p for p in rt_constants.RIGPARTS if p not in active]


def include_parts(rignames):
    """
    Lift the exclusion on the given parts.

    The entry points that name their parts outright (rig_tail_single,
    rig_tail_selected) must build what was asked for: a stale entry in
    RIGPARTS_EXCLUDE would otherwise make the build a silent no-op. The
    roster-wide build (rig_tail_multiple) does not call this - there the
    exclusion is the user's current choice.

    Arguments:
        rignames (list): Rig part names to include
    """
    excluded = rt_constants.RIGPARTS_EXCLUDE or []
    named = set(rignames)
    keep = [p for p in excluded if p not in named]
    if len(keep) != len(excluded):
        lifted = [p for p in excluded if p in named]
        logger.debug(f"Building named parts: lifting exclusion on "
                     f"{', '.join(lifted)}")
        rt_constants.RIGPARTS_EXCLUDE = keep


def validate_cache():
    """
    Clear cache if RIGPARTS or ROOT changed since last build.
    Compares current values against LAST_BUILD state.
    """
    # Check if RIGPARTS changed
    if set(rt_constants.RIGPARTS) != set(rt_constants.LAST_BUILD['rigparts']):
        removed = set(rt_constants.LAST_BUILD['rigparts']) - set(rt_constants.RIGPARTS)
        added = set(rt_constants.RIGPARTS) - set(rt_constants.LAST_BUILD['rigparts'])

        # Clear data for removed rigparts
        for rigname in removed:
            rt_constants.JOINTS_FK.pop(rigname, None)
            rt_constants.JOINTS_IK.pop(rigname, None)
            rt_constants.JOINTS_BN.pop(rigname, None)

        logger.debug(f'RIGPARTS changed. Removed: {removed}, Added: {added}')
        rt_constants.LAST_BUILD['rigparts'] = rt_constants.RIGPARTS.copy()

    # Check if ROOT changed
    if rt_constants.ROOT != rt_constants.LAST_BUILD['root']:
        logger.debug(f"ROOT changed: '{rt_constants.LAST_BUILD['root']}' -> '{rt_constants.ROOT}'")
        rt_constants.LAST_BUILD['root'] = rt_constants.ROOT


def validate_cache_structure(fk=None, ik=None):
    """
    Check whether the rig structure changed since the last build.
    Changing NUM_CTRL_FK / NUM_CTRL_IK alters the SDK group, curve CV and
    cluster layout, and toggling INDIV_FK adds/removes the per-joint FK
    controls, so reusing the previous nodes (light cleanup path) would
    mix old and new layouts and corrupt the build; a change forces the
    full teardown path instead.

    The BUILD MODE counts as structure too. cleanup_rigname only tears
    down the modes it is asked to build, so switching FK+IK -> FK-only
    down the light path left the previous run's IK curves, clusters and
    spline handles behind; the next FK+IK build then met half an IK
    system it had not created and aborted on the missing pieces. A mode
    change forces the full teardown, which cleanup_rig runs across BOTH
    modes.

    Stored values are refreshed on every call.

    Arguments:
        fk (bool): FK is being built this run; None skips the mode check
        ik (bool): IK is being built this run; None skips the mode check

    Return:
        bool: True if the structure changed (full rebuild needed)
    """
    prev_fk = rt_constants.LAST_BUILD.get('num_ctrl_fk')
    prev_ik = rt_constants.LAST_BUILD.get('num_ctrl_ik')
    prev_indiv = rt_constants.LAST_BUILD.get('indiv_fk')
    prev_mode = rt_constants.LAST_BUILD.get('build_mode')
    rt_constants.LAST_BUILD['num_ctrl_fk'] = rt_constants.NUM_CTRL_FK
    rt_constants.LAST_BUILD['num_ctrl_ik'] = rt_constants.NUM_CTRL_IK
    rt_constants.LAST_BUILD['indiv_fk'] = rt_constants.INDIV_FK
    mode = None if fk is None and ik is None else (bool(fk), bool(ik))
    if mode is not None:
        rt_constants.LAST_BUILD['build_mode'] = mode

    if prev_fk is None and prev_ik is None:
        # No recorded build in this session: joint validation decides
        return False
    changed = (prev_fk != rt_constants.NUM_CTRL_FK
               or prev_ik != rt_constants.NUM_CTRL_IK
               or prev_indiv != rt_constants.INDIV_FK)
    mode_changed = (mode is not None and prev_mode is not None
                    and prev_mode != mode)
    if mode_changed:
        logger.debug(f'Build mode changed: (fk, ik) {prev_mode} -> {mode}. '
                     f'Full rebuild.')
    if changed:
        logger.debug(f'Structure changed: NUM_CTRL_FK '
                    f'{prev_fk} -> {rt_constants.NUM_CTRL_FK}, NUM_CTRL_IK '
                    f'{prev_ik} -> {rt_constants.NUM_CTRL_IK}, INDIV_FK '
                    f'{prev_indiv} -> {rt_constants.INDIV_FK}. Full rebuild.')
    return changed or mode_changed


def validate_cache_joints(rigname, tol=None):
    """
    Check if cached joints still exist and match scene.
    Positions are compared per joint by Euclidean distance within tol,
    not exact equality: building the rig drives the BN joints through the
    OPM network, which perturbs world positions by float noise.

    Arguments:
        rigname (str): Name of rig component
        tol (float): Max per-joint position drift in scene units.
            Defaults to rt_constants.JOINT_POS_TOLERANCE.

    Return:
        bool: True if joints changed (full rebuild needed)
    """
    if tol is None:
        tol = rt_constants.JOINT_POS_TOLERANCE

    if rigname not in rt_constants.JOINTS_BN:
        return True  # No cache, need rebuild

    # Check if cached joints exist in scene
    for joint_dict in [rt_constants.JOINTS_BN, rt_constants.JOINTS_FK, rt_constants.JOINTS_IK]:
        if rigname in joint_dict:
            for jnt in joint_dict[rigname]:
                if not cmds.objExists(jnt):
                    logger.warning(f"Cached joint '{jnt}' no longer exists")
                    return True  # Joints changed

    # Compare current joint positions against last build within tolerance
    stored_pos = rt_constants.LAST_BUILD.get('joints_pos', {}).get(rigname)
    current_pos = [cmds.xform(j, q=1, ws=1, t=1)
                   for j in rt_constants.JOINTS_BN[rigname]]
    rt_constants.LAST_BUILD.setdefault('joints_pos', {})[rigname] = current_pos

    if stored_pos is None or len(stored_pos) != len(current_pos):
        logger.debug(f'{rigname}: No stored joint positions, rebuild needed')
        return True

    for jnt, old, new in zip(rt_constants.JOINTS_BN[rigname], stored_pos, current_pos):
        dist = math.dist(old, new)
        if dist > tol:
            logger.debug(f"{rigname}: '{jnt}' moved {dist:.4f} (tol {tol})")
            return True

    return False  # Joints unchanged


def cache_controls_ik(rigname):
    """
    Cache IK controls for a rigname.
    Imports get_controls_ik dynamically to avoid circular imports.

    Arguments:
        rigname (str): Name of rig component

    Return:
        tuple: (ik_controls, ik_ctrlgrps) dicts
    """
    global _CONTROL_CACHE
    if rigname not in _CONTROL_CACHE:
        import rig_tail_control as rt_control
        _CONTROL_CACHE[rigname] = rt_control.get_controls_ik(rigname)
    return _CONTROL_CACHE[rigname]


def clear_control_cache():
    """
    Clear all cached controls.
    Call this before rig rebuild.
    """
    global _CONTROL_CACHE
    _CONTROL_CACHE.clear()


def get_cached_controls_ik(rigname):
    """
    Get cached IK controls.
    Wrapper for cache_controls_ik for clarity.

    Arguments:
        rigname (str): Name of rig component

    Return:
        tuple: (ik_controls, ik_ctrlgrps) dicts
    """
    return cache_controls_ik(rigname)
