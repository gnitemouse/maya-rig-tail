'''
# rig_tail_orient.py
author: Daisy Jane @gnitemouse

Fix tail joint orientation so each tail bends in a plane and matching
left/right tails move as mirror images.

Two distinct orientation problems, two operations:

  1. Intra-chain twist (within ONE chain). An autorigger can leave each
     joint's up-axis pointing a different way down the chain, so the
     tail bends along a corkscrew instead of a plane. aim_frames() re-
     aims every joint down its own chain with a single up-axis (the
     chain's best-fit plane normal), which removes the twist.

  2. Left/right coherence (BETWEEN two chains). Anim FX (wave/curl/noise)
     and control rotations apply in each driver joint's LOCAL frame
     (rig_tail_matrix pre-multiplies the driver worldMatrix), so an L
     and R tail only move as mirror images when their frames are
     behavior-mirrored. mirror_frames() reflects the (already twist-
     fixed) source-side frames onto the target side.

Mirroring alone does NOT fix twist -- it copies the source orientation,
twist and all, onto the target. So the source side is aim-oriented
first, then mirrored onto the target; center and unpaired chains are
just aim-oriented.

Orientation only: joint world POSITIONS are never changed. _apply_frames
sets each joint root->tip using its own captured world position, so re-
orienting a parent cannot drag a child off its spot. World orientation
is written into jointOrient with rotate left at zero.

Runs at the start of setup_rig, the one safe window: the FK/IK driver
chains are still free duplicates (not yet wired to controls/spline) and
cleanup_rig has unbound geometry, so re-orienting drags neither skin nor
a live rig; the connect phase rebinds at the new rest.

Gating: rt_cst.MIRROR_ORIENT enables it (default OFF -- opt in once
verified), and rt_cst.MIRROR_ORIENT_DRYRUN (default ON) only logs the
intended changes without modifying anything, so the first run is always
a safe preview. Because rig_tail_constants is never reloaded, toggle
these at runtime with e.g. `rig_tail_constants.MIRROR_ORIENT = True`
rather than by editing the file mid-session.
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import math
import re

logger = logger_setup(__name__)

# rig_tail_constants is never reloaded (it holds session state), so a
# session started before this feature existed lacks these settings.
# This module IS reloaded every run: install any missing default onto
# rt_cst so the build works without a Maya restart, never overwriting a
# value a restarted/customized session already provides.
_CST_DEFAULTS = {
    'MIRROR_ORIENT': False,         # aim-orient + mirror L/R chains in setup
    'MIRROR_ORIENT_DRYRUN': True,   # only log intended changes; do not modify
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
    ''' Read a setting, falling back to the installed default. '''
    return getattr(rt_cst, name, _CST_DEFAULTS.get(name))


# PAIRING ==============================================================

def find_mirror_pairs(rigparts):
    '''
    Group rig parts into (source, target) pairs by side prefix.

    A pair exists when both an 'L_<base>' and an 'R_<base>' rig part are
    present (prefix match is case-insensitive; the base must match). The
    source side is rt_cst.MIRROR_SOURCE_SIDE (default 'R'); the other
    side is the target, overwritten with the source's mirror.

    Arguments
        rigparts (list): RIGPARTS names

    Return
        tuple: (pairs, paired_names)
            pairs (list): [(source_rigname, target_rigname), ...]
            paired_names (set): every rigname that is in some pair
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


# VECTORS ==============================================================

def _sub(a, b): return [a[i] - b[i] for i in range(3)]
def _add(a, b): return [a[i] + b[i] for i in range(3)]
def _scale(a, s): return [x * s for x in a]
def _dot(a, b): return sum(a[i] * b[i] for i in range(3))
def _cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def _length(a): return math.sqrt(_dot(a, a))


def _norm(a):
    l = _length(a)
    return [x / l for x in a] if l > _EPS else [0.0, 0.0, 0.0]


def _world_axis_perp(aim):
    ''' World axis least aligned with aim, made perpendicular to it. '''
    best = min(([1, 0, 0], [0, 1, 0], [0, 0, 1]), key=lambda ax: abs(_dot(ax, aim)))
    return _norm(_sub(best, _scale(aim, _dot(best, aim))))


# FRAMES ===============================================================

def _assign_rows(aim, up, aim_axis, up_axis):
    '''
    Build a world rotation as three axis rows (Maya order: rows 0/1/2 are
    the local X/Y/Z axes in world) from an aim and up direction, placing
    aim on aim_axis, up on up_axis and the remaining axis on the third,
    right-handed.

    Arguments
        aim (list): unit aim direction (down the chain)
        up (list): unit up direction (perpendicular to aim)
        aim_axis (str): 'x' | 'y' | 'z' local axis for aim
        up_axis (str): 'x' | 'y' | 'z' local axis for up

    Return
        list: [X_row, Y_row, Z_row]
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    a, u = idx[aim_axis], idx[up_axis]
    t = 3 - a - u  # remaining axis index
    third = _cross(aim, up)
    rows = [None, None, None]
    rows[a] = aim
    rows[u] = up
    rows[t] = third
    # Ensure right-handed (X cross Y == Z); flip the third axis if needed
    if _dot(_cross(rows[0], rows[1]), rows[2]) < 0:
        rows[t] = _scale(third, -1.0)
    return rows


def aim_frames(positions, aim_axis, up_axis):
    '''
    World orientation for each joint that aims down the chain with a
    single, twist-free up-axis (the chain's best-fit plane normal).

    The normal is the summed cross product of consecutive segments, which
    is stable for a near-planar chain; a straight/degenerate chain falls
    back to the world axis most perpendicular to the first segment. Each
    joint's up is that normal projected perpendicular to its own aim, so
    the up-axis stays consistent and the tail bends in one plane.

    Arguments
        positions (list): [[x, y, z], ...] joint world positions
        aim_axis (str): local axis aimed down the chain
        up_axis (str): local axis aligned to the plane normal

    Return
        list: one [X_row, Y_row, Z_row] frame per joint
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

    Each source frame's axis rows have the two components orthogonal to
    the symmetry-plane normal negated (an even, det +1 operation, so the
    frame stays right-handed). Equal values on both sides then produce
    symmetric motion.

    Arguments
        src_matrices (list): per-joint source world matrices (16 floats)
        axis (str): symmetry-plane normal, 'x' | 'y' | 'z'

    Return
        list: one [X_row, Y_row, Z_row] frame per joint
    '''
    keep = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower(), 0)
    frames = []
    for m in src_matrices:
        rows = ([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]])
        frames.append([[r[c] if c == keep else -r[c] for c in range(3)]
                       for r in rows])
    return frames


# APPLY ================================================================

def _world_matrix(rows, pos):
    ''' 16-float row-major world matrix from axis rows and a position. '''
    return [rows[0][0], rows[0][1], rows[0][2], 0.0,
            rows[1][0], rows[1][1], rows[1][2], 0.0,
            rows[2][0], rows[2][1], rows[2][2], 0.0,
            pos[0], pos[1], pos[2], 1.0]


def _apply_frames(joints, frames, dry_run):
    '''
    Write each joint's world orientation from frames while preserving its
    world position. Processed root->tip using each joint's own captured
    position, so re-orienting a parent cannot move a child; orientation
    lands in jointOrient with rotate zeroed. No re-parenting, so nothing
    can drift.

    The full world matrix is set directly (unambiguous, no euler-order
    dependence); with jointOrient zeroed, the resulting orientation sits
    in rotate and is then moved into jointOrient with rotate cleared.

    Arguments
        joints (list): chain joints, root first
        frames (list): matching [X_row, Y_row, Z_row] world frames
        dry_run (bool): only log, do not modify

    Return
        int: joints re-oriented (or that would be)
    '''
    n = min(len(joints), len(frames))
    positions = [cmds.xform(joints[i], q=True, ws=True, translation=True)
                 for i in range(n)]
    if dry_run:
        for i in range(n):
            before = cmds.xform(joints[i], q=True, ws=True, ro=True)
            logger.info(f'  [dry-run] {joints[i]}: world rot '
                        f'{[round(v, 2) for v in before]} -> aligned')
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


# ENTRY ================================================================

def _chain_dicts(fk, ik):
    ''' Joint dicts to re-orient for the current build options. '''
    dicts = [rt_cst.JOINTS_BN]
    if fk:
        dicts.append(rt_cst.JOINTS_FK)
    if ik:
        dicts.append(rt_cst.JOINTS_IK)
    return dicts


def orient_all(fk, ik):
    '''
    Aim-orient every tail chain (twist fix), then behavior-mirror each
    L/R pair's target side from its aim-oriented source.

    Called at the start of setup_rig. No-op when MIRROR_ORIENT is off.
    Center and unpaired chains are only aim-oriented; a pair's source is
    aim-oriented and its target mirrored. BN, and per build options FK/IK,
    are all re-oriented (co-located duplicates), so every layer agrees.

    Arguments
        fk (bool): FK chains are being built
        ik (bool): IK chains are being built
    '''
    if not _cst('MIRROR_ORIENT'):
        logger.debug('Orient: disabled (MIRROR_ORIENT off)')
        return

    dry_run = bool(_cst('MIRROR_ORIENT_DRYRUN'))
    axis = _cst('MIRROR_AXIS')
    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    mode = ' [dry-run]' if dry_run else ''

    pairs, paired = find_mirror_pairs(rt_cst.RIGPARTS)
    target_source = {tgt: src for src, tgt in pairs}
    dicts = _chain_dicts(fk, ik)
    logger.info(f'Orient{mode}: aim-orient chains'
                + (f', mirror {len(pairs)} L/R pair(s) '
                   f'(axis={axis}, source={_cst("MIRROR_SOURCE_SIDE")})'
                   if pairs else ' (no L/R pairs)'))

    # Pass 1: aim-orient every chain that is NOT a mirror target
    for rigname in rt_cst.RIGPARTS:
        if rigname in target_source:
            continue
        for jdict in dicts:
            joints = jdict.get(rigname)
            if not joints or len(joints) < 2:
                continue
            try:
                positions = [cmds.xform(j, q=True, ws=True, translation=True)
                             for j in joints]
                frames = aim_frames(positions, aim_axis, up_axis)
                logger.info(f'Orient{mode}: aim {rigname} ({len(joints)} jnts)')
                _apply_frames(joints, frames, dry_run)
            except Exception as err:
                logger.error(f'Orient: aim failed on {rigname}: {err}')

    # Pass 2: mirror each target from its (now aim-oriented) source
    for target, source in target_source.items():
        for jdict in dicts:
            src = jdict.get(source)
            tgt = jdict.get(target)
            if not src or not tgt:
                continue
            try:
                src_mats = [cmds.xform(j, q=True, ws=True, matrix=True)
                            for j in src]
                frames = mirror_frames(src_mats, axis)
                logger.info(f'Orient{mode}: mirror {source} -> {target}')
                _apply_frames(tgt, frames, dry_run)
            except Exception as err:
                logger.error(f'Orient: mirror failed on '
                             f'{source}->{target}: {err}')


# Backwards-compatible alias (setup_rig calls this name)
def mirror_orient_all(fk, ik):
    ''' Alias for orient_all (kept for the setup_rig call site). '''
    orient_all(fk, ik)
