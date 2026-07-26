'''
rig_tail_setup.py
author: Daisy Jane @gnitemouse

Setup phase: prepare the tail skeleton before the build.

Runs on the BN skeleton only, before any rig components exist. The build
later duplicates the FK and IK chains from this skeleton, so orienting it
here is enough. This phase is optional and never runs during the build;
launch it from the Tail Rig Setup UI (rig_tail_setup_ui) or call
setup_tails() directly, then build as usual.

Two families of operations. The batch ones run from setup_tails and each
have a toggle in rig_tail_constants:
    ORIENT_JOINTS  aim-orient each chain so a tail bends in one plane
                   (removes intra-chain twist). No mirroring; both sides
                   are oriented from their own geometry.
    MIRROR_ORIENT  reflect matching 'L_'/'R_' pairs' ORIENTATION across the
                   symmetry plane, so the two sides face as mirror images.
                   Positions unchanged.
    MIRROR_JOINTS  reflect matching 'L_'/'R_' pairs' POSITIONS across the
                   symmetry plane, so the target side's joints sit at the
                   exact mirror of the source side's.
MIRROR_ORIENT and MIRROR_JOINTS are independent (either, both, or neither);
run ORIENT_JOINTS first so a mirror copies a clean source. A typical run
enables ORIENT_JOINTS + MIRROR_ORIENT (+ MIRROR_JOINTS if the sides are
positionally off).

The remaining operation is an interactive per-chain fix-up, not a batch
toggle:
    roll_chain     roll ONE chain about its aim axis by an angle, to turn a
                   correctly-oriented-but-wrong-facing chain onto the right
                   plane. Applied on demand from the UI after the batch run;
                   no constant. Positions never change.

Orientation changes are written into jointOrient with rotate left at zero;
MIRROR_JOINTS additionally moves world positions. MIRROR_DRYRUN logs the
intended batch changes without touching anything.

Re-orienting or moving a bound joint would drag the mesh, so the affected
geometry is unbound first and left for the build to rebind.

Functions:
    setup_tails: entry point; detect joints, unbind geo, run the phase
    run_setup: run the enabled batch orient/mirror steps on rt_cst.JOINTS_BN
    orient_chains: aim-orient every BN chain to remove twist (ORIENT_JOINTS)
    mirror_chains: reflect each L/R pair's orientation and/or positions
        (MIRROR_ORIENT / MIRROR_JOINTS)
    roll_chain: interactively roll one chain about its aim axis
    rigname_from_selection: resolve the RIGPART of the selected joint (UI)
    show_joint_orients: toggle the joints' local-axis display (UI check)
    find_mirror_pairs: pair rig parts by 'L_'/'R_' prefix
    aim_frames: per-joint world frames aimed down a chain, twist-free
    mirror_frames: reflect source world orientations for the target
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_cleanup as rt_cln
import rig_tail_maya as rt_mya
import math
import re

logger = logger_setup(__name__)

# rig_tail_constants is never reloaded (it holds session state), so a
# session started before this feature existed lacks these settings. This
# module IS reloaded every run: install any missing default onto rt_cst so
# Setup works without a Maya restart, never overwriting a value a
# restarted or customized session already provides.
_CST_DEFAULTS = {
    'ORIENT_JOINTS': True,          # aim-orient chains (remove twist)
    'MIRROR_ORIENT': False,         # reflect L/R pair ORIENTATION across plane
    'MIRROR_JOINTS': False,         # reflect L/R pair POSITIONS across plane
    'MIRROR_DRYRUN': False,         # only log intended changes; do not modify
    'MIRROR_AXIS': 'x',             # symmetry-plane normal (x = YZ plane)
    'MIRROR_SOURCE_SIDE': 'R',      # authored side; the other is overwritten
    'ORIENT_AIM_AXIS': 'x',         # local axis aimed down the chain
    'ORIENT_UP_AXIS': 'z',          # local axis aligned to the plane normal
}
for _name, _value in _CST_DEFAULTS.items():
    if not hasattr(rt_cst, _name):
        setattr(rt_cst, _name, _value)

# Rig part prefix that marks a mirrored side, e.g. 'L_fintail'.
_SIDE_RE = re.compile(r'^([LlRr])_(.+)$')
_EPS = 1e-9


def _cst(name):
    ''' Read a Setup setting, falling back to the installed default. '''
    return getattr(rt_cst, name, _CST_DEFAULTS.get(name))


# ENTRY ================================================================

def setup_tails(root=None, dry_run=None):
    '''
    Run the Setup phase on the tail skeleton.

    Detects the BN chains for every RIGPART, then runs the enabled
    orientation steps (run_setup). Re-orienting a bound joint distorts the
    mesh, so the affected geometry is unbound first and left for the build
    to rebind. Run this once on the raw skeleton, verify, then build.

    Usage:
        import rig_tail_setup as rt_set
        rt_set.setup_tails('squid')          # apply
        rt_set.setup_tails('squid', True)    # preview only

    Arguments
        root (str): Rig root name (sets rt_cst.ROOT); skipped when None.
        dry_run (bool): Override MIRROR_DRYRUN; None uses the setting.
            When true nothing is unbound or modified.

    Return
        dict: summary from run_setup, {'oriented', 'mirrored', 'dry_run'}.
    '''
    if root:
        rt_cln.set_root(root)

    found = rt_cln.detect_joints_bn()
    if not found:
        logger.warning('Setup: no BN joints found for any RIGPART')
        return {'oriented': 0, 'mirrored': 0, 'dry_run': True, 'missing_geo': []}

    # Warn about parts whose mesh does not follow the naming convention:
    # they are not unbound before re-orienting (so their mesh distorts) nor
    # rebound by the build. Reported so the meshes can be renamed.
    missing_geo = rt_mya.report_missing_geometry(found)

    preview = dry_run if dry_run is not None \
        else bool(_cst('MIRROR_DRYRUN'))
    if not preview:
        for rigname in found:
            rt_mya.unbind_geometry(rigname)

    result = run_setup(dry_run=dry_run)
    result['missing_geo'] = missing_geo
    return result


def run_setup(dry_run=None):
    '''
    Run the enabled orient and mirror steps on the BN skeleton.

    orient_chains runs first when ORIENT_JOINTS is on, then mirror_chains
    when MIRROR_ORIENT and/or MIRROR_JOINTS is on, so each L/R pair mirrors
    a clean source. Expects rt_cst.JOINTS_BN to be populated (setup_tails
    does this) and the affected geometry unbound. Orientation-only steps
    keep positions; MIRROR_JOINTS moves the target side's joints.

    Arguments
        dry_run (bool): Override MIRROR_DRYRUN; None uses the setting.

    Return
        dict: {'oriented': n, 'mirrored': n, 'dry_run': bool}.
    '''
    if dry_run is None:
        dry_run = bool(_cst('MIRROR_DRYRUN'))
    do_orient = bool(_cst('ORIENT_JOINTS'))
    mir_orient = bool(_cst('MIRROR_ORIENT'))
    mir_joints = bool(_cst('MIRROR_JOINTS'))
    mode = ' [dry-run]' if dry_run else ''
    logger.info(f'Setup{mode}: orient_joints={do_orient}, '
                f'mirror_orient={mir_orient}, mirror_joints={mir_joints}')

    oriented = orient_chains(dry_run) if do_orient else 0
    mirrored = mirror_chains(dry_run, mir_orient, mir_joints) \
        if (mir_orient or mir_joints) else 0
    if not (do_orient or mir_orient or mir_joints):
        logger.info('Setup: nothing enabled (ORIENT_JOINTS, MIRROR_ORIENT '
                    'and MIRROR_JOINTS all off)')

    # The raw skeleton is the user's to inspect and pose during Setup, so
    # keep every joint keyable and visible (the build later makes the rig
    # joints non-keyable). Skipped on a dry run, which changes nothing.
    if not dry_run:
        rt_mya.finalize_joint_channels(
            keyable=True, visibility=1, joint_dicts=[rt_cst.JOINTS_BN])
        _clear_rest_pose()

    return {'oriented': oriented, 'mirrored': mirrored, 'dry_run': dry_run}


def _clear_rest_pose():
    '''
    Clear any rest pose a previous build stamped on the BN joints.

    Re-orienting or moving the skeleton invalidates the stored rest pose
    (Method D, rig_tail_restpose): the stored restMatrix describes the OLD
    orientation, so the next build would drive the IK curve from a stale
    pose and the chain would jump. Clearing it makes the build recapture
    from the corrected skeleton. Best-effort; logs and continues on failure.
    '''
    try:
        import rig_tail_restpose as rt_rest
        rt_rest.clear_rest_pose()
    except Exception as err:
        logger.warning(f'Setup: could not clear stored rest pose: {err}')


def show_joint_orients(show=True):
    '''
    Toggle the local-rotation-axis display on every BN chain joint.

    A quick visual check of the orient result: Maya draws each joint's
    local X/Y/Z as a coloured cross (the joint's displayLocalAxis). Detects
    the BN joints for the current RIGPARTS first, so it works before or
    after a run and never modifies orientation.

    Arguments
        show (bool): True to show the axes, False to hide them.

    Return
        int: number of joints toggled.
    '''
    rt_cln.detect_joints_bn()
    val = 1 if show else 0
    count = 0
    for joints in rt_cst.JOINTS_BN.values():
        for jnt in joints:
            if cmds.objExists(jnt) and \
                    cmds.attributeQuery('displayLocalAxis', node=jnt, exists=True):
                try:
                    cmds.setAttr(f'{jnt}.displayLocalAxis', val)
                    count += 1
                except Exception:
                    pass
    logger.info(f'Setup: joint local axes '
                f'{"shown" if show else "hidden"} on {count} BN joints')
    return count


# OPERATIONS ===========================================================
# Both operate on the BN skeleton only (rt_cst.JOINTS_BN).

def orient_chains(dry_run):
    '''
    Aim-orient every BN chain to remove intra-chain twist.

    Re-aims each joint down its own chain with a single up-axis (the chain
    plane normal), so the tail bends in one plane. Applies to every rig
    part, both sides. Skips chains with fewer than two joints. To turn a
    single chain onto a different plane afterwards, use roll_chain.

    Arguments
        dry_run (bool): only log the intended changes, do not modify.

    Return
        int: joints re-oriented (or that would be, in a dry run).
    '''
    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    mode = ' [dry-run]' if dry_run else ''
    count = 0
    for rigname in rt_cst.RIGPARTS:
        joints = rt_cst.JOINTS_BN.get(rigname)
        if not joints or len(joints) < 2:
            continue
        try:
            positions = [cmds.xform(j, q=True, ws=True, translation=True)
                         for j in joints]
            frames = aim_frames(positions, aim_axis, up_axis)
            logger.info(f'Orient{mode}: aim {rigname} ({len(joints)} jnts)')
            count += _apply_frames(joints, frames, dry_run)
            # The end ('_ee_') joint is excluded from the chain, so align it
            # to the chain's final frame or it keeps the stale orientation.
            _orient_end_joint(joints[-1], frames[-1], dry_run)
            if not dry_run:
                _report_twist(rigname, joints, positions, aim_axis, up_axis)
        except Exception as err:
            logger.error(f'Orient: aim failed on {rigname}: {err}')
    return count


def _report_twist(rigname, joints, positions, aim_axis, up_axis):
    '''
    Log a chain's planarity and its residual per-joint twist AFTER orienting.

    Reads back each joint's actual world matrix, so it reports what the
    skeleton really ended up as (not what was intended). Twist is the roll
    of the up axis about the aim axis between consecutive joints; a clean
    aim-orient keeps it near zero. Planarity is the summed segment-normal
    length over the summed segment length (0 = perfectly planar chain, near
    1 = highly non-planar) - a non-planar chain cannot be made fully
    twist-free about one plane normal, so a high value explains residual
    twist that is not a bug.
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    ai, ui = idx.get(aim_axis, 0), idx.get(up_axis, 2)

    # Planarity from positions.
    segs = [_sub(positions[i + 1], positions[i]) for i in range(len(positions) - 1)]
    seg_len = sum(_length(s) for s in segs) or 1.0
    normal = [0.0, 0.0, 0.0]
    for i in range(len(segs) - 1):
        normal = _add(normal, _cross(segs[i], segs[i + 1]))
    planarity = _length(normal) / (seg_len * seg_len)

    # Residual twist from the joints' actual world axes.
    rows = []
    for j in joints:
        m = cmds.xform(j, q=True, ws=True, matrix=True)
        rows.append(([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]))
    aims = [_norm(r[ai]) for r in rows]
    ups = [_norm(r[ui]) for r in rows]
    rolls = []
    for i in range(len(joints) - 1):
        a = aims[i]
        u0 = _norm(_sub(ups[i], _scale(a, _dot(ups[i], a))))
        u1 = _norm(_sub(ups[i + 1], _scale(a, _dot(ups[i + 1], a))))
        if _length(u0) > _EPS and _length(u1) > _EPS:
            rolls.append(math.degrees(math.acos(max(-1.0, min(1.0, _dot(u0, u1))))))
    total = sum(rolls)
    mx = max(rolls) if rolls else 0.0
    logger.info(f'Orient: {rigname} residual twist total={total:.1f} '
                f'max/seg={mx:.1f} deg, planarity={planarity:.3f} '
                f'(aim={aim_axis}, up={up_axis}, {len(rolls)} segs)')


def mirror_chains(dry_run, do_orient, do_positions):
    '''
    Reflect each L/R pair's BN chain across the symmetry plane.

    Overwrites the target side (rt_cst.MIRROR_SOURCE_SIDE picks the source)
    with the mirror of the source. Two independent effects, per the flags:
        do_orient    reflect the source ORIENTATION onto the target, so the
                     target's joints face as mirror images. Positions kept.
        do_positions reflect the source POSITIONS onto the target, so the
                     target's joints sit at the exact mirror of the source.

    The symmetry plane is assumed to pass through the world origin, with
    MIRROR_AXIS as its normal (the standard rig convention: 'x' = the YZ
    plane at x=0). Orientation is copied as-is, so it does not remove twist;
    run orient_chains first for a clean source. No-op when RIGPARTS has no
    L/R pair.

    Arguments
        dry_run (bool): only log the intended changes, do not modify.
        do_orient (bool): reflect orientation (MIRROR_ORIENT).
        do_positions (bool): reflect positions (MIRROR_JOINTS).

    Return
        int: joints changed (or that would be, in a dry run).
    '''
    axis = _cst('MIRROR_AXIS')
    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    keep = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower(), 0)
    mode = ' [dry-run]' if dry_run else ''
    what = '+'.join(w for w, on in (('orient', do_orient),
                                    ('positions', do_positions)) if on)
    pairs, _ = find_mirror_pairs(rt_cst.RIGPARTS)
    if not pairs:
        logger.info(f'Mirror{mode}: no L/R pairs in RIGPARTS, skipping')
        return 0
    count = 0
    for source, target in pairs:
        src = rt_cst.JOINTS_BN.get(source)
        tgt = rt_cst.JOINTS_BN.get(target)
        if not src or not tgt:
            logger.warning(f'Mirror: {source} or {target} has no BN joints')
            continue
        if len(src) != len(tgt):
            logger.warning(f'Mirror: {source} ({len(src)}) and {target} '
                           f'({len(tgt)}) differ in joint count; mirroring '
                           f'the first {min(len(src), len(tgt))}')
        try:
            src_mats = [cmds.xform(j, q=True, ws=True, matrix=True)
                        for j in src]
            before = [cmds.xform(j, q=True, ws=True, matrix=True) for j in tgt]

            # Target orientation: the mirror of the source, or (orient off)
            # the target's own current orientation, kept unchanged.
            if do_orient:
                frames = mirror_frames(src_mats, axis, aim_axis, up_axis)
            else:
                frames = [([m[0], m[1], m[2]], [m[4], m[5], m[6]],
                           [m[8], m[9], m[10]]) for m in before]

            # Target positions: the reflected source positions, or (positions
            # off) None so _apply_frames keeps each joint where it is.
            positions = [_reflect([m[12], m[13], m[14]], keep)
                         for m in src_mats] if do_positions else None

            logger.info(f'Mirror{mode}: {source} to {target} '
                        f'(axis={axis}, {what})')
            count += _apply_frames(tgt, frames, dry_run, positions=positions)

            # End joint: orient to the chain's final frame; move to the
            # mirrored source-end position only when positions are mirrored.
            ee_pos = None
            if do_positions:
                src_ee = _find_end_joint(src[-1])
                if src_ee:
                    sp = cmds.xform(src_ee, q=True, ws=True, translation=True)
                    ee_pos = _reflect(sp, keep)
            _orient_end_joint(tgt[-1], frames[-1], dry_run, position=ee_pos)

            if not dry_run:
                _report_mirror_delta(target, tgt, before)
        except Exception as err:
            logger.error(f'Mirror: failed on {source} to {target}: {err}')
    return count


def _report_mirror_delta(rigname, joints, before_mats):
    '''
    Log how much a mirror actually changed the target chain.

    Reports BOTH effects separately, because each mirror flag moves only one
    of them: the per-joint angular change (MIRROR_ORIENT) and the per-joint
    world distance moved (MIRROR_JOINTS). Reporting only one made a
    positions-only mirror read '0 joints changed' and look like the chain had
    been skipped.

    A near-zero delta on an enabled effect is still normal and does NOT mean
    the chain was skipped: it means the target already matched the source's
    mirror (common on an already-symmetric skeleton, or after orient). The
    'Mirror: <source> to <target>' line above it is what confirms the chain
    was processed.
    '''
    rot_deltas = []
    pos_deltas = []
    for jnt, m0 in zip(joints, before_mats):
        m1 = cmds.xform(jnt, q=True, ws=True, matrix=True)
        # relative rotation angle from the trace of R0^T * R1 (rows are axes)
        r0 = ([m0[0], m0[1], m0[2]], [m0[4], m0[5], m0[6]], [m0[8], m0[9], m0[10]])
        r1 = ([m1[0], m1[1], m1[2]], [m1[4], m1[5], m1[6]], [m1[8], m1[9], m1[10]])
        trace = sum(_dot(r0[k], r1[k]) for k in range(3))
        rot_deltas.append(
            math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))))
        pos_deltas.append(_length(_sub([m1[12], m1[13], m1[14]],
                                       [m0[12], m0[13], m0[14]])))
    n = len(rot_deltas)
    rot_moved = sum(1 for d in rot_deltas if d > 0.5)
    pos_moved = sum(1 for d in pos_deltas if d > 1e-4)
    rot_max = max(rot_deltas) if rot_deltas else 0.0
    pos_max = max(pos_deltas) if pos_deltas else 0.0
    logger.info(f'Mirror: {rigname} re-oriented {rot_moved}/{n} joints '
                f'(max {rot_max:.1f} deg), moved {pos_moved}/{n} joints '
                f'(max {pos_max:.4f} units)')


# PAIRING ==============================================================

def find_mirror_pairs(rigparts):
    '''
    Pair rig parts into (source, target) by their side prefix.

    A pair exists when both an 'L_<base>' and an 'R_<base>' rig part are
    present (prefix match is case-insensitive; the base must be identical).
    The source side is rt_cst.MIRROR_SOURCE_SIDE (default 'R'); the other
    side is the target that gets overwritten. Center and unpaired parts are
    ignored.

    Arguments
        rigparts (list): RIGPARTS names.

    Return
        tuple: (pairs, paired_names).
            pairs (list): [(source_rigname, target_rigname), ...].
            paired_names (set): every rigname that belongs to a pair.
    '''
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    groups = {}
    for rp in rigparts:
        m = _SIDE_RE.match(rp)
        if not m:
            continue
        groups.setdefault(m.group(2), {})[m.group(1).upper()] = rp

    pairs = []
    paired = set()
    for base, sides in groups.items():
        if 'L' in sides and 'R' in sides:
            target_side = 'L' if source_side == 'R' else 'R'
            pairs.append((sides[source_side], sides[target_side]))
            paired.add(sides['L'])
            paired.add(sides['R'])
    return pairs, paired


# FRAMES ===============================================================

def aim_frames(positions, aim_axis, up_axis):
    '''
    Per-joint world frames that aim down the chain with a twist-free up.

    The up reference is the chain's best-fit plane normal (the summed cross
    product of consecutive segments), stable for a near-planar chain. A
    straight or degenerate chain falls back to the world axis most
    perpendicular to the first segment. Each joint's up is that normal made
    perpendicular to its own aim, so the up-axis stays consistent and the
    tail bends in one plane. To turn the whole chain onto a different plane
    afterwards, roll_chain rolls it about the aim axis.

    Arguments
        positions (list): [[x, y, z], ...] joint world positions.
        aim_axis (str): local axis aimed down the chain, 'x'|'y'|'z'.
        up_axis (str): local axis aligned to the plane normal, 'x'|'y'|'z'.

    Return
        list: one [X_row, Y_row, Z_row] world frame per joint.
    '''
    n = len(positions)
    segs = [_sub(positions[i + 1], positions[i]) for i in range(n - 1)]
    normal = [0.0, 0.0, 0.0]
    for i in range(len(segs) - 1):
        normal = _add(normal, _cross(segs[i], segs[i + 1]))
    if _length(normal) < _EPS:
        normal = _world_axis_perp(_norm(segs[0])) if segs else [0, 0, 1]
    normal = _norm(normal)

    frames = []
    prev_up = None
    for i in range(n):
        aim = _norm(segs[i] if i < n - 1 else segs[-1])
        up = _sub(normal, _scale(aim, _dot(normal, aim)))
        if _length(up) <= _EPS:
            # aim nearly parallel to the plane normal (a joint that bends out
            # of plane, often the tip): reuse the previous joint's up so the
            # frame stays continuous. Snapping to a world axis instead would
            # flip that one joint AND break L/R mirror symmetry (a world axis
            # is not mirrored between sides).
            ref = prev_up if prev_up is not None else _world_axis_perp(aim)
            up = _sub(ref, _scale(aim, _dot(ref, aim)))
            if _length(up) <= _EPS:
                up = _world_axis_perp(aim)
        up = _norm(up)
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
        prev_up = up
    return frames


def roll_chain(rigname, degrees):
    '''
    Roll one chain about its aim axis by an angle (interactive fix-up).

    Turns a chain that is correctly oriented but facing the wrong way onto
    the right plane, without moving any joint. Reads each joint's CURRENT
    world orientation and rolls it in place about the aim axis, so it
    preserves whatever the batch orient/mirror produced and just adds the
    roll (unlike orient_chains, which re-derives the frame from positions).
    A uniform roll adds no relative twist between joints.

    Meant to be run on demand from the UI after the batch orient/mirror.
    Unbinds the chain's geometry first (a bound joint would drag the mesh)
    and clears any stored rest pose, both left for the build to redo.

    Arguments
        rigname (str): the RIGPART whose chain to roll.
        degrees (float): roll angle about the aim axis.

    Return
        int: joints rolled (0 if none, or on a no-op angle).
    '''
    rt_cln.detect_joints_bn()
    joints = rt_cst.JOINTS_BN.get(rigname)
    if not joints or len(joints) < 2:
        logger.warning(f'Roll: {rigname} has no BN chain to roll')
        return 0
    if not degrees:
        logger.info(f'Roll: {rigname} angle is 0, nothing to do')
        return 0

    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    idx = {'x': 0, 'y': 1, 'z': 2}
    ai, ui = idx.get(aim_axis, 0), idx.get(up_axis, 2)

    rt_mya.unbind_geometry(rigname)

    # Roll each joint's current frame about its own aim axis.
    frames = []
    for j in joints:
        m = cmds.xform(j, q=True, ws=True, matrix=True)
        rows = ([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]])
        aim = _norm(rows[ai])
        up = _roll_about(_norm(rows[ui]), aim, degrees)
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
    count = _apply_frames(joints, frames, dry_run=False)
    _orient_end_joint(joints[-1], frames[-1], dry_run=False)
    _clear_rest_pose()
    logger.info(f'Roll: {rigname} rolled {degrees:g} deg about {aim_axis} '
                f'({count} joints)')
    return count


def rigname_from_selection():
    '''
    Resolve the RIGPART of the first selected node (for the UI Select button).

    Maps the first node selected in Maya back to a RIGPART via the naming
    convention, so a chain can be picked by clicking a joint instead of
    browsing the list. Any joint of the chain works - BN/FK/IK/FX or the end
    'ee' joint - since the name resolves to the same rigname.

    Return
        str or None: the matching RIGPART, or None when nothing is selected
        or the selection is not a recognized rig part.
    '''
    import rig_tail_naming as rt_nam
    sel = cmds.ls(selection=True) or []
    if not sel:
        return None
    node = sel[0].split('|')[-1]
    rigname = rt_nam.get_rigname(node, rt_cst.JOINT)
    if rigname and rigname in rt_cst.RIGPARTS:
        return rigname
    return None


def mirror_frames(src_matrices, axis, aim_axis, up_axis):
    '''
    Mirror source world orientations across the symmetry plane.

    Reflects each axis vector ACROSS the symmetry plane (negates its
    component along the plane normal). A reflection flips handedness, so
    only the aim and up are reflected and the frame is then reassembled with
    _assign_rows - the same right-handed assembly aim_frames uses - which
    rebuilds the third axis. The mirrored side therefore aims outward
    symmetrically and stays consistent with an oriented source.

    Note this is a reflection, not a 180-degree rotation about the normal:
    rotating would leave the aim pointing the same way as the source (into
    the body) instead of to the opposite side.

    Arguments
        src_matrices (list): per-joint source world matrices (16 floats).
        axis (str): symmetry-plane normal, 'x'|'y'|'z'.
        aim_axis (str): local axis aimed down the chain, 'x'|'y'|'z'.
        up_axis (str): local axis aligned to the plane normal, 'x'|'y'|'z'.

    Return
        list: one [X_row, Y_row, Z_row] world frame per joint.
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    keep = idx.get(str(axis).lower(), 0)
    ai, ui = idx[aim_axis], idx[up_axis]
    frames = []
    for m in src_matrices:
        rows = ([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]])
        aim = _norm(_reflect(rows[ai], keep))
        up = _norm(_reflect(rows[ui], keep))
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
    return frames


def _reflect(vec, keep):
    ''' Reflect a vector across the plane whose normal is axis index keep
    (negate that one component). '''
    return [(-v if i == keep else v) for i, v in enumerate(vec)]


def _assign_rows(aim, up, aim_axis, up_axis):
    '''
    Build a right-handed world rotation from an aim and up direction.

    Returns three axis rows (Maya order: rows 0/1/2 are the local X/Y/Z
    axes in world), placing aim on aim_axis, up on up_axis, and the cross
    product on the remaining axis, flipped if needed to stay right-handed.

    Arguments
        aim (list): unit aim direction (down the chain).
        up (list): unit up direction (perpendicular to aim).
        aim_axis (str): local axis for aim, 'x'|'y'|'z'.
        up_axis (str): local axis for up, 'x'|'y'|'z'.

    Return
        list: [X_row, Y_row, Z_row].
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    a, u = idx[aim_axis], idx[up_axis]
    t = 3 - a - u  # remaining axis index
    third = _cross(aim, up)
    rows = [None, None, None]
    rows[a] = aim
    rows[u] = up
    rows[t] = third
    # Keep right-handed (X cross Y == Z); flip the third axis if not.
    if _dot(_cross(rows[0], rows[1]), rows[2]) < 0:
        rows[t] = _scale(third, -1.0)
    return rows


# APPLY ================================================================

def _find_end_joint(parent):
    ''' The '_ee_' child of a joint (the end/tip marker), or None. '''
    for c in cmds.listRelatives(parent, typ='joint', children=True) or []:
        if '_ee_' in c:
            return c
    return None


def _orient_end_joint(parent, frame, dry_run, position=None):
    '''
    Orient the end ('_ee_') joint to continue the chain.

    get_joint_chain stops before the '_ee_' joint, so orient_chains and
    mirror_chains never touch it and it keeps the autorigger's stale
    orientation - pointing a different way from the re-oriented chain. This
    gives the end joint the last real joint's frame (the chain's final aim
    and up), so it lines up with the chain. Same driver-detach as
    _apply_frames, since a built end joint is opm-driven.

    Arguments
        parent (str): last real joint of the chain.
        frame (list): [X_row, Y_row, Z_row] to apply (the last joint's).
        dry_run (bool): only log, do not modify.
        position (list): world position to move the end joint to; None keeps
            its current position (used when a mirror also reflects positions).

    Return
        int: 1 if an end joint was oriented (or would be), else 0.
    '''
    ee = _find_end_joint(parent)
    if not ee:
        return 0
    if dry_run:
        logger.info(f'  [dry-run] {ee}: end joint aligned to chain')
        return 1
    pos = position if position is not None \
        else cmds.xform(ee, q=True, ws=True, translation=True)
    rt_mya.disconnect_all(ee, source=True, destination=False)
    rt_mya.reset_opm(ee)
    cmds.setAttr(f'{ee}.rotate', 0, 0, 0)
    cmds.setAttr(f'{ee}.jointOrient', 0, 0, 0)
    cmds.xform(ee, ws=True, matrix=_world_matrix(frame, pos))
    rot = cmds.getAttr(f'{ee}.rotate')[0]
    cmds.setAttr(f'{ee}.jointOrient', rot[0], rot[1], rot[2])
    cmds.setAttr(f'{ee}.rotate', 0, 0, 0)
    logger.debug(f'  aligned end joint {ee} to chain')
    return 1


def _apply_frames(joints, frames, dry_run, positions=None):
    '''
    Write world orientations onto joints, at kept or given world positions.

    Processes root to tip using each joint's own position, so re-orienting a
    parent cannot move a child, and there is no re-parenting to drift. The
    full world matrix is set directly (unambiguous, no euler-order
    dependence); with jointOrient zeroed the orientation lands in rotate,
    which is then moved into jointOrient with rotate cleared.

    By default each joint keeps its current world position (orientation-only
    change). Pass positions to move the joints instead - used when a mirror
    also reflects positions - one world position per joint, root first.

    If the chain is already built, its BN joints are driven by the build's
    offsetParentMatrix network. Setting a world matrix while opm is live
    would bake the opm rotation into jointOrient (flipping the result) and
    leave the build in a confused state. So each joint's INCOMING drivers
    are detached and its opm reset to identity first, turning it back into a
    plain joint - the geometry bind on the OUTGOING side is preserved, and
    the build rebuilds the opm network afterwards. Current world positions
    are captured up front, while the drivers are still live, so they are
    exact.

    Arguments
        joints (list): chain joints, root first.
        frames (list): matching [X_row, Y_row, Z_row] world frames.
        dry_run (bool): only log, do not modify.
        positions (list): world position per joint to move to; None keeps
            each joint's current position.

    Return
        int: joints re-oriented (or that would be, in a dry run).
    '''
    n = min(len(joints), len(frames))
    if positions is not None:
        n = min(n, len(positions))
    else:
        positions = [cmds.xform(joints[i], q=True, ws=True, translation=True)
                     for i in range(n)]
    if dry_run:
        for i in range(n):
            before = cmds.xform(joints[i], q=True, ws=True, ro=True)
            logger.info(f'  [dry-run] {joints[i]}: world rot '
                        f'{[round(v, 2) for v in before]} to aligned')
        return n

    for i in range(n):
        jnt = joints[i]
        # Detach build drivers (incoming only: keep the outgoing
        # worldMatrix -> skinCluster geometry bind) and clear opm so the
        # joint re-orients as a plain joint, root to tip.
        rt_mya.disconnect_all(jnt, source=True, destination=False)
        rt_mya.reset_opm(jnt)
        cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)
        cmds.setAttr(f'{jnt}.jointOrient', 0, 0, 0)
        cmds.xform(jnt, ws=True, matrix=_world_matrix(frames[i], positions[i]))
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        cmds.setAttr(f'{jnt}.jointOrient', rot[0], rot[1], rot[2])
        cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)
    logger.debug(f'  re-oriented {n} joints ({joints[0]} ...)')
    return n


def _world_matrix(rows, pos):
    ''' 16-float row-major world matrix from axis rows and a position. '''
    return [rows[0][0], rows[0][1], rows[0][2], 0.0,
            rows[1][0], rows[1][1], rows[1][2], 0.0,
            rows[2][0], rows[2][1], rows[2][2], 0.0,
            pos[0], pos[1], pos[2], 1.0]


# VECTORS ==============================================================

def _sub(a, b): return [a[i] - b[i] for i in range(3)]
def _add(a, b): return [a[i] + b[i] for i in range(3)]
def _scale(a, s): return [x * s for x in a]
def _dot(a, b): return sum(a[i] * b[i] for i in range(3))
def _cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def _length(a): return math.sqrt(_dot(a, a))


def _norm(a):
    ''' Normalize a vector; returns a zero vector when its length is ~0. '''
    l = _length(a)
    return [x / l for x in a] if l > _EPS else [0.0, 0.0, 0.0]


def _roll_about(vec, axis, degrees):
    '''
    Rotate vec about a unit axis by an angle (Rodrigues' rotation).

    Full Rodrigues (does not assume vec is perpendicular to axis), so it is
    safe for any input. axis is expected to be unit length. Returns a
    normalized vector.
    '''
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    cr = _cross(axis, vec)
    d = _dot(axis, vec)
    rotated = [vec[k] * c + cr[k] * s + axis[k] * d * (1.0 - c)
               for k in range(3)]
    return _norm(rotated)


def _world_axis_perp(aim):
    ''' Unit world axis least aligned with aim, made perpendicular to it. '''
    best = min(([1, 0, 0], [0, 1, 0], [0, 0, 1]), key=lambda ax: abs(_dot(ax, aim)))
    return _norm(_sub(best, _scale(aim, _dot(best, aim))))
