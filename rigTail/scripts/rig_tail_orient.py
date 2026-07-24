'''
# rig_tail_orient.py
author: Daisy Jane @gnitemouse

Mirror-orient joint chains so left/right tails move coherently.

Anim FX (wave/curl/noise) and control rotations are applied in each
driver joint's LOCAL frame (rig_tail_matrix pre-multiplies the driver
worldMatrix), so two tails only move as mirror images of each other
when their joint frames are behavior-mirrored. This module finds
matching 'L_'/'R_' rig parts (case-insensitive prefix) and overwrites
the target side's orientation with the behavior-mirror of the source
side, leaving positions untouched.

Behavior mirror across the character's symmetry plane (default YZ, i.e.
left/right split along world X): the target joint's world orientation
is the source joint's world orientation with the two axis components
orthogonal to the plane normal negated. Equal slider/FX values on both
sides then produce symmetric (butterfly) motion -- the standard result
of Maya's Mirror Joint -> Behavior, done in place on the existing
chains rather than by duplication.

Runs at the start of setup_rig, the one safe window: the FK/IK driver
chains exist as free duplicates (not yet wired to controls/spline),
and geometry has been unbound by cleanup_rig, so re-orienting cannot
drag skin or a live rig. The bind joints (BN) are re-oriented too for a
consistent-looking skeleton; the matrix build zeroes their jointOrient
and drives them from the mirrored drivers regardless, and the fresh
bind in the connect phase captures the mirrored rest, so the mesh does
not shift (positions are preserved).

Gated by rt_cst.MIRROR_ORIENT (default on) and only acts when at least
one L/R pair is present. Idempotent: re-mirroring the target from the
source yields the same result, so it is safe to run every build. Set
rt_cst.MIRROR_ORIENT_DRYRUN True to log intended changes without
modifying anything.
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import re

logger = logger_setup(__name__)

# rig_tail_constants is never reloaded (it holds session state), so a
# session started before this feature existed lacks these settings.
# This module IS reloaded every run: install any missing default onto
# rt_cst so the build works without a Maya restart, never overwriting a
# value a restarted/customized session already provides.
_CST_DEFAULTS = {
    'MIRROR_ORIENT': True,        # behavior-mirror L/R chains in setup
    'MIRROR_ORIENT_DRYRUN': False,  # log intended changes, do not modify
    'MIRROR_AXIS': 'x',           # symmetry-plane normal (x = YZ plane)
    'MIRROR_SOURCE_SIDE': 'R',    # authored side; the other is overwritten
}
for _name, _value in _CST_DEFAULTS.items():
    if not hasattr(rt_cst, _name):
        setattr(rt_cst, _name, _value)

# Rig part prefix that marks a mirrored side, e.g. 'L_fintail'.
_SIDE_RE = re.compile(r'^([LlRr])_(.+)$')


def _cst(name):
    ''' Read a mirror setting, falling back to the installed default. '''
    return getattr(rt_cst, name, _CST_DEFAULTS.get(name))


# PAIRING ==============================================================

def find_mirror_pairs(rigparts):
    '''
    Group rig parts into (source, target) pairs by their side prefix.

    A pair exists when both an 'L_<base>' and an 'R_<base>' rig part are
    present (prefix match is case-insensitive; the base after the prefix
    must be identical). The source side is rt_cst.MIRROR_SOURCE_SIDE
    (default 'R'); the other side is the target that gets overwritten.
    Center parts ('C_...') and unpaired sides are ignored -- they have
    nothing to mirror against.

    Arguments
        rigparts (list): RIGPARTS names

    Return
        list: [(source_rigname, target_rigname), ...]
    '''
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    groups = {}
    for rp in rigparts:
        m = _SIDE_RE.match(rp)
        if not m:
            continue
        side = m.group(1).upper()
        base = m.group(2)
        groups.setdefault(base, {})[side] = rp

    pairs = []
    for base, sides in groups.items():
        if 'L' in sides and 'R' in sides:
            target_side = 'L' if source_side == 'R' else 'R'
            pairs.append((sides[source_side], sides[target_side]))
        else:
            logger.trace(f"Mirror: '{base}' has only side(s) "
                         f"{sorted(sides)}, skipping")
    return pairs


# MATRIX ===============================================================

def _mirror_world_matrix(src_matrix, tgt_pos, axis):
    '''
    Behavior-mirror a source world matrix, keeping the target position.

    The three local axis vectors (rows 0/1/2, 4/5/6, 8/9/10 of Maya's
    row-major world matrix) each have the two components orthogonal to
    the plane normal negated. Negating two components is an even
    (det +1) operation, so the frame stays right-handed -- a valid joint
    orientation. Translation is replaced with the target joint's own
    world position so only orientation is mirrored.

    Arguments
        src_matrix (list): 16 floats, source joint world matrix
        tgt_pos (list): 3 floats, target joint world position (kept)
        axis (str): symmetry-plane normal, 'x' | 'y' | 'z'

    Return
        list: 16 floats, mirrored world matrix for the target joint
    '''
    keep = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower(), 0)
    m = list(src_matrix)
    for row_start in (0, 4, 8):
        for comp in range(3):
            if comp != keep:
                m[row_start + comp] = -m[row_start + comp]
    m[12], m[13], m[14], m[15] = tgt_pos[0], tgt_pos[1], tgt_pos[2], 1.0
    return m


def _angle_delta(a, b):
    ''' Max absolute per-channel euler difference (deg), for reporting. '''
    return max(abs(x - y) for x, y in zip(a, b))


# MIRROR CHAIN =========================================================

def _mirror_chain(src_joints, tgt_joints, axis, dry_run):
    '''
    Overwrite each target joint's orientation with the behavior-mirror
    of the matching source joint, preserving target positions.

    Children are detached to world before writing orientations so that
    re-orienting a parent cannot drag its children off their (mirror-
    symmetric) positions; the chain is re-parented afterwards, which
    preserves the world orientation just written. World orientation is
    moved into jointOrient with rotate left at zero, matching how the
    rest of the rig expects joints to carry their rest orientation.

    Arguments
        src_joints (list): source chain joints (reference side)
        tgt_joints (list): target chain joints (overwritten side)
        axis (str): symmetry-plane normal
        dry_run (bool): only log intended changes when True

    Return
        int: number of joints re-oriented (or that would be, in dry run)
    '''
    n = min(len(src_joints), len(tgt_joints))
    if len(src_joints) != len(tgt_joints):
        logger.warning(f'Mirror: chain length mismatch '
                       f'({len(src_joints)} vs {len(tgt_joints)}), '
                       f'mirroring the first {n} joints')

    targets = []
    for i in range(n):
        if not (cmds.objExists(src_joints[i]) and cmds.objExists(tgt_joints[i])):
            logger.warning(f'Mirror: missing joint at index {i} '
                           f'({src_joints[i]} / {tgt_joints[i]})')
            continue
        src_m = cmds.xform(src_joints[i], q=True, ws=True, matrix=True)
        tgt_p = cmds.xform(tgt_joints[i], q=True, ws=True, translation=True)
        targets.append((tgt_joints[i], _mirror_world_matrix(src_m, tgt_p, axis)))

    if dry_run:
        for jnt, world in targets:
            before = cmds.xform(jnt, q=True, ws=True, ro=True)
            logger.info(f'Mirror [dry-run] would re-orient {jnt} '
                        f'(current world rot {[round(v, 2) for v in before]})')
        return len(targets)

    # Detach children so re-orienting a parent cannot move them
    detached = []
    for jnt, _ in targets[1:]:
        parent = cmds.listRelatives(jnt, parent=True, type='joint') or []
        if parent:
            cmds.parent(jnt, world=True)
            detached.append(jnt)

    for jnt, world in targets:
        cmds.setAttr(f'{jnt}.jointOrient', 0, 0, 0)
        cmds.xform(jnt, ws=True, matrix=world)
        # jointOrient was zeroed, so the world orientation now sits in
        # rotate; move it into jointOrient and clear rotate. This holds
        # for any parent (both are local, applied parent*jointOrient*rotate)
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        cmds.setAttr(f'{jnt}.jointOrient', rot[0], rot[1], rot[2])
        cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)

    # Re-parent the chain root->tip; cmds.parent preserves the world
    # orientation just written (re-solving jointOrient, rotate stays 0)
    for i in range(1, n):
        child = tgt_joints[i]
        if child not in detached:
            continue
        parent = cmds.listRelatives(child, parent=True, type='joint') or []
        if not parent or parent[0] != tgt_joints[i - 1]:
            cmds.parent(child, tgt_joints[i - 1])

    logger.debug(f'Mirror: re-oriented {len(targets)} joints '
                 f'({tgt_joints[0]} ...)')
    return len(targets)


# ENTRY ================================================================

def mirror_orient_all(fk, ik):
    '''
    Behavior-mirror every L/R rig-part pair's joint chains.

    Called at the start of setup_rig. No-op when MIRROR_ORIENT is off or
    no L/R pair is present. For each pair the BN chain is mirrored (for a
    consistent-looking skeleton) and, per build options, the FK and IK
    driver chains -- the ones whose orientation actually maps the FX and
    control rotations, so the two sides move as mirror images.

    Arguments
        fk (bool): FK chains are being built
        ik (bool): IK chains are being built
    '''
    if not _cst('MIRROR_ORIENT'):
        logger.debug('Mirror-orient disabled (MIRROR_ORIENT off)')
        return

    pairs = find_mirror_pairs(rt_cst.RIGPARTS)
    if not pairs:
        logger.debug('Mirror-orient: no L/R pairs in RIGPARTS, skipping')
        return

    axis = _cst('MIRROR_AXIS')
    dry_run = bool(_cst('MIRROR_ORIENT_DRYRUN'))
    mode = ' [dry-run]' if dry_run else ''
    logger.info(f'Mirror-orient{mode}: {len(pairs)} L/R pair(s), '
                f'axis={axis}, source={_cst("MIRROR_SOURCE_SIDE")}')

    chains = [rt_cst.JOINTS_BN]
    if fk:
        chains.append(rt_cst.JOINTS_FK)
    if ik:
        chains.append(rt_cst.JOINTS_IK)

    for source, target in pairs:
        logger.info(f'Mirror-orient{mode}: {source} -> {target}')
        for joints_dict in chains:
            src = joints_dict.get(source)
            tgt = joints_dict.get(target)
            if not src or not tgt:
                logger.trace(f'Mirror: {source}/{target} not in a joint '
                             f'dict, skipping that chain')
                continue
            try:
                _mirror_chain(src, tgt, axis, dry_run)
            except Exception as err:
                # One bad pair/chain must not abort the whole build
                logger.error(f'Mirror: failed on {source}->{target}: {err}')
