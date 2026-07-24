'''
rig_tail_setup.py
author: Daisy Jane @gnitemouse

Setup phase: prepare the tail skeleton before the build.

Runs on the BN skeleton only, before any rig components exist. The build
later duplicates the FK and IK chains from this skeleton, so orienting it
here is enough. This phase is optional and never runs during the build;
launch it from the Tail Rig Setup UI (rig_tail_setup_ui) or call
setup_tails() directly, then build as usual.

Two independent operations, each with its own toggle in rig_tail_constants:
    MIRROR_ORIENT  aim-orient each chain so a tail bends in one plane
                   (removes intra-chain twist)
    MIRROR_JOINTS  behavior-mirror matching 'L_'/'R_' pairs so the two
                   sides move as mirror images
Enable both for planar, symmetric tails; orient runs first so mirror
copies a clean source. Mirroring on its own does not remove twist.

Only joint orientation changes; world positions are always preserved.
Orientation is written into jointOrient with rotate left at zero.
MIRROR_DRYRUN logs the intended changes (for both operations) without
touching anything.

Re-orienting a bound joint would drag the mesh, so setup_tails unbinds the
affected geometry first and leaves it for the build to rebind.

Functions:
    setup_tails: entry point; detect joints, unbind geo, run the phase
    run_setup: run the enabled orient/mirror steps on rt_cst.JOINTS_BN
    orient_chains: aim-orient every BN chain to remove twist
    mirror_joints: behavior-mirror each L/R pair's BN chain
    find_mirror_pairs: pair rig parts by 'L_'/'R_' prefix
    aim_frames: per-joint world frames aimed down a chain, twist-free
    mirror_frames: behavior-mirror source world matrices for the target
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
    'MIRROR_ORIENT': True,          # aim-orient chains (remove twist)
    'MIRROR_JOINTS': True,          # behavior-mirror L/R pairs
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
        return {'oriented': 0, 'mirrored': 0, 'dry_run': True}

    preview = dry_run if dry_run is not None \
        else bool(_cst('MIRROR_DRYRUN'))
    if not preview:
        for rigname in found:
            rt_mya.unbind_geometry(rigname)

    return run_setup(dry_run=dry_run)


def run_setup(dry_run=None):
    '''
    Run the enabled orient and mirror steps on the BN skeleton.

    orient_chains runs first when MIRROR_ORIENT is on, then mirror_joints
    when MIRROR_JOINTS is on, so each L/R pair mirrors a clean source.
    Expects rt_cst.JOINTS_BN to be populated (setup_tails does this) and
    the affected geometry unbound. Positions are never changed.

    Arguments
        dry_run (bool): Override MIRROR_DRYRUN; None uses the setting.

    Return
        dict: {'oriented': n, 'mirrored': n, 'dry_run': bool}.
    '''
    if dry_run is None:
        dry_run = bool(_cst('MIRROR_DRYRUN'))
    do_orient = bool(_cst('MIRROR_ORIENT'))
    do_mirror = bool(_cst('MIRROR_JOINTS'))
    mode = ' [dry-run]' if dry_run else ''
    logger.info(f'Setup{mode}: orient={do_orient}, mirror={do_mirror}')

    oriented = orient_chains(dry_run) if do_orient else 0
    mirrored = mirror_joints(dry_run) if do_mirror else 0
    if not (do_orient or do_mirror):
        logger.info('Setup: nothing enabled '
                    '(MIRROR_ORIENT and MIRROR_JOINTS both off)')

    # The raw skeleton is the user's to inspect and pose during Setup, so
    # keep every joint keyable and visible (the build later makes the rig
    # joints non-keyable). Skipped on a dry run, which changes nothing.
    if not dry_run:
        rt_mya.finalize_joint_channels(
            keyable=True, visibility=1, joint_dicts=[rt_cst.JOINTS_BN])

    return {'oriented': oriented, 'mirrored': mirrored, 'dry_run': dry_run}


# OPERATIONS ===========================================================
# Both operate on the BN skeleton only (rt_cst.JOINTS_BN).

def orient_chains(dry_run):
    '''
    Aim-orient every BN chain to remove intra-chain twist.

    Re-aims each joint down its own chain with a single up-axis (the chain
    plane normal), so the tail bends in one plane. Applies to every rig
    part, both sides. Skips chains with fewer than two joints.

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
        except Exception as err:
            logger.error(f'Orient: aim failed on {rigname}: {err}')
    return count


def mirror_joints(dry_run):
    '''
    Behavior-mirror each L/R pair's BN chain.

    Overwrites the target side with the mirror of the source side
    (rt_cst.MIRROR_SOURCE_SIDE). Copies the source orientation as is, so it
    does not remove twist; run orient_chains first for a clean source.
    No-op when RIGPARTS has no L/R pair.

    Arguments
        dry_run (bool): only log the intended changes, do not modify.

    Return
        int: joints re-oriented (or that would be, in a dry run).
    '''
    axis = _cst('MIRROR_AXIS')
    mode = ' [dry-run]' if dry_run else ''
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
        try:
            src_mats = [cmds.xform(j, q=True, ws=True, matrix=True)
                        for j in src]
            frames = mirror_frames(src_mats, axis)
            logger.info(f'Mirror{mode}: {source} to {target} (axis={axis})')
            count += _apply_frames(tgt, frames, dry_run)
        except Exception as err:
            logger.error(f'Mirror: failed on {source} to {target}: {err}')
    return count


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
    tail bends in one plane.

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
    for i in range(n):
        aim = _norm(segs[i] if i < n - 1 else segs[-1])
        up = _sub(normal, _scale(aim, _dot(normal, aim)))
        up = _norm(up) if _length(up) > _EPS else _world_axis_perp(aim)
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
    return frames


def mirror_frames(src_matrices, axis):
    '''
    Behavior-mirror source world orientations for the target side.

    Negates the two axis components orthogonal to the symmetry-plane
    normal in each source frame. That is an even (det +1) operation, so the
    frame stays right-handed. Equal values on both sides then produce
    symmetric motion.

    Arguments
        src_matrices (list): per-joint source world matrices (16 floats).
        axis (str): symmetry-plane normal, 'x'|'y'|'z'.

    Return
        list: one [X_row, Y_row, Z_row] world frame per joint.
    '''
    keep = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower(), 0)
    frames = []
    for m in src_matrices:
        rows = ([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]])
        frames.append([[r[c] if c == keep else -r[c] for c in range(3)]
                       for r in rows])
    return frames


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

def _apply_frames(joints, frames, dry_run):
    '''
    Write world orientations onto joints, preserving world positions.

    Processes root to tip using each joint's own captured position, so
    re-orienting a parent cannot move a child, and there is no re-parenting
    to drift. The full world matrix is set directly (unambiguous, no
    euler-order dependence); with jointOrient zeroed the orientation lands
    in rotate, which is then moved into jointOrient with rotate cleared.

    Arguments
        joints (list): chain joints, root first.
        frames (list): matching [X_row, Y_row, Z_row] world frames.
        dry_run (bool): only log, do not modify.

    Return
        int: joints re-oriented (or that would be, in a dry run).
    '''
    n = min(len(joints), len(frames))
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


def _world_axis_perp(aim):
    ''' Unit world axis least aligned with aim, made perpendicular to it. '''
    best = min(([1, 0, 0], [0, 1, 0], [0, 0, 1]), key=lambda ax: abs(_dot(ax, aim)))
    return _norm(_sub(best, _scale(aim, _dot(best, aim))))
