"""
rig_tail_cache.py
author: Daisy Jane @gnitemouse

Cache operations for Rig Tail.
Centralized cache management using rig_tail_constants for persistent state.

Decide upon re-rig whether the previous rig can be reused
or must be fully torn down. Rebuild is needed (joints changed) if any of:

    1. There's no cached BN joint list for this rigname in rt_cst.JOINTS_BN
        (fresh session or first build)
    2. Any cached BN/FK/IK joint no longer exists in scene
    3. Any BN joint moved more than JOINT_POS_TOLERANCE (scene units) from
        the positions stored in rt_cst.LAST_BUILD['joints_pos'] at the last
        build. Stored positions are refreshed on every call, so float drift
        from the rig driving the joints never accumulates into a rebuild.

Functions:
    validate_cache: Clear cache if RIGPARTS or ROOT changed
    validate_cache_structure: Check if control counts changed
    validate_cache_joints: Check if cached joints still exist
    cache_controls_ik: Cache IK controls for a rigname
    clear_control_cache: Clear all cached controls
    get_cached_controls_ik: Get cached IK controls
"""

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
import math

logger = logger_setup(__name__)

# Module-level control cache
_CONTROL_CACHE = {}


def validate_cache():
    """
    Clear cache if RIGPARTS or ROOT changed since last build.
    Compares current values against LAST_BUILD state.
    """
    # Check if RIGPARTS changed
    if set(rt_cst.RIGPARTS) != set(rt_cst.LAST_BUILD['rigparts']):
        removed = set(rt_cst.LAST_BUILD['rigparts']) - set(rt_cst.RIGPARTS)
        added = set(rt_cst.RIGPARTS) - set(rt_cst.LAST_BUILD['rigparts'])

        # Clear data for removed rigparts
        for rigname in removed:
            rt_cst.JOINTS_FK.pop(rigname, None)
            rt_cst.JOINTS_IK.pop(rigname, None)
            rt_cst.JOINTS_BN.pop(rigname, None)

        logger.info(f'RIGPARTS changed. Removed: {removed}, Added: {added}')
        rt_cst.LAST_BUILD['rigparts'] = rt_cst.RIGPARTS.copy()

    # Check if ROOT changed
    if rt_cst.ROOT != rt_cst.LAST_BUILD['root']:
        logger.info(f"ROOT changed: '{rt_cst.LAST_BUILD['root']}' -> '{rt_cst.ROOT}'")
        rt_cst.LAST_BUILD['root'] = rt_cst.ROOT


def validate_cache_structure():
    """
    Check whether the rig structure constants changed since the last
    build. Changing NUM_CTRL_FK / NUM_CTRL_IK alters the SDK group,
    curve CV and cluster layout, and toggling INDIV_FK adds/removes the
    per-joint FK controls, so reusing the previous nodes (light cleanup
    path) would mix old and new layouts and corrupt the build; a change
    forces the full teardown path instead.

    Stored values are refreshed on every call.

    Return:
        bool: True if the structure changed (full rebuild needed)
    """
    prev_fk = rt_cst.LAST_BUILD.get('num_ctrl_fk')
    prev_ik = rt_cst.LAST_BUILD.get('num_ctrl_ik')
    prev_indiv = rt_cst.LAST_BUILD.get('indiv_fk')
    rt_cst.LAST_BUILD['num_ctrl_fk'] = rt_cst.NUM_CTRL_FK
    rt_cst.LAST_BUILD['num_ctrl_ik'] = rt_cst.NUM_CTRL_IK
    rt_cst.LAST_BUILD['indiv_fk'] = rt_cst.INDIV_FK

    if prev_fk is None and prev_ik is None:
        # No recorded build in this session: joint validation decides
        return False
    changed = (prev_fk != rt_cst.NUM_CTRL_FK
               or prev_ik != rt_cst.NUM_CTRL_IK
               or prev_indiv != rt_cst.INDIV_FK)
    if changed:
        logger.info(f'Structure changed: NUM_CTRL_FK '
                    f'{prev_fk} -> {rt_cst.NUM_CTRL_FK}, NUM_CTRL_IK '
                    f'{prev_ik} -> {rt_cst.NUM_CTRL_IK}, INDIV_FK '
                    f'{prev_indiv} -> {rt_cst.INDIV_FK}. Full rebuild.')
    return changed


def validate_cache_joints(rigname, tol=None):
    """
    Check if cached joints still exist and match scene.
    Positions are compared per joint by Euclidean distance within tol,
    not exact equality: building the rig drives the BN joints through the
    OPM network, which perturbs world positions by float noise.

    Arguments:
        rigname (str): Name of rig component
        tol (float): Max per-joint position drift in scene units.
            Defaults to rt_cst.JOINT_POS_TOLERANCE.

    Return:
        bool: True if joints changed (full rebuild needed)
    """
    if tol is None:
        tol = rt_cst.JOINT_POS_TOLERANCE

    if rigname not in rt_cst.JOINTS_BN:
        return True  # No cache, need rebuild

    # Check if cached joints exist in scene
    for joint_dict in [rt_cst.JOINTS_BN, rt_cst.JOINTS_FK, rt_cst.JOINTS_IK]:
        if rigname in joint_dict:
            for jnt in joint_dict[rigname]:
                if not cmds.objExists(jnt):
                    logger.warning(f"Cached joint '{jnt}' no longer exists")
                    return True  # Joints changed

    # Compare current joint positions against last build within tolerance
    stored_pos = rt_cst.LAST_BUILD.get('joints_pos', {}).get(rigname)
    current_pos = [cmds.xform(j, q=1, ws=1, t=1)
                   for j in rt_cst.JOINTS_BN[rigname]]
    rt_cst.LAST_BUILD.setdefault('joints_pos', {})[rigname] = current_pos

    if stored_pos is None or len(stored_pos) != len(current_pos):
        logger.info(f'{rigname}: No stored joint positions, rebuild needed')
        return True

    for jnt, old, new in zip(rt_cst.JOINTS_BN[rigname], stored_pos, current_pos):
        dist = math.dist(old, new)
        if dist > tol:
            logger.info(f"{rigname}: '{jnt}' moved {dist:.4f} (tol {tol})")
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
        from rig_tail_control import get_controls_ik
        _CONTROL_CACHE[rigname] = get_controls_ik(rigname)
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
