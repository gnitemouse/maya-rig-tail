"""
rig_tail_chain_build.py
author: Daisy Jane @gnitemouse

Maya I/O layer for Joint Chain Builder.
Detects, guards, creates and rewrites BN joint chains before the Setup
phase. Sessions cache the original chain shape so repeated count changes
do not compound distortion.

This module imports from the core one-way only: existing modules may
never import this one.

Functions:
    build_new: create a new chain between two transforms
    rebuild: re-space an existing chain at a different joint count
    rebuild_selected: rebuild the chain(s) in the current selection
    resolve_selection: interpret viewport selection as chain spec(s)
    chain_root: the root of the chain a joint belongs to
    clear_cache: forget session original cache for one or all chains

Settings deliberately live nowhere: every option is a UI widget read at
click time. No config file, no preferences — the tool has six controls and
each click is a single undo.
"""

import math

import maya.cmds as cmds
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_joint as rt_joint
import rig_tail_math as rt_math
import rig_tail_chain_spacing as rt_chain_spacing
import rig_tail_maya as rt_maya

logger = logger_setup(__name__)


# SESSION ORIGINAL CACHE ================================================
# Module global, dies on reload, no scene metadata.
# Keyed on the root joint's long DAG path. Each entry stores the
# original positions (the first time the chain was touched this session)
# and the last-written positions.

_ORIGINALS = {}


# SELECTION RESOLUTION ==================================================

class ChainSpec:
    '''Describes one chain operation from a selection.'''

    def __init__(self, root=None, start=None, end=None, rigname=None,
                 is_new=False):
        self.root = root          # root joint (rebuild) or None (new)
        self.start = start        # start transform
        self.end = end            # end transform
        self.rigname = rigname    # rig part name
        self.is_new = is_new      # True = build new, False = rebuild


def resolve_selection():
    """
    Interpret the current viewport selection as one or more chain specs.

    Returns:
        list of ChainSpec: resolved chain operations.

    Selection rules:
        - Two joints, one ancestor of the other: rebuild the span.
        - Two unrelated transforms: build new chain between them.
        - One joint: walk up to chain root, rebuild.
        - Many joints: resolve each to its root, deduplicate.
        - Nothing / non-transforms: abort.
    """
    sel = cmds.ls(selection=True, transforms=True)
    if not sel:
        abort_build(logger, 'Nothing selected. Select joints or transforms '
                           'to build or rebuild a chain.')

    # Filter to joints only vs any transforms
    joints = [n for n in sel if cmds.nodeType(n) == 'joint']

    if joints:
        # Any joint in the selection means rebuild.  Resolving every joint
        # to its own chain root and de-duplicating handles one joint, a whole
        # chain, and joints picked across several tails with one rule — the
        # roots collapse to one entry per chain however many of its joints
        # are selected.
        roots = {}
        for j in joints:
            root = _walk_to_root(j)
            key = (cmds.ls(root, long=True) or [root])[0]
            if key not in roots:
                roots[key] = ChainSpec(root=root, rigname=_guess_rigname(root))
        return list(roots.values())

    # No joints at all: two plain transforms mark the ends of a new chain.
    if len(sel) >= 2:
        return [ChainSpec(start=sel[0], end=sel[1], is_new=True)]

    abort_build(logger, 'Could not interpret selection. Select a joint of '
                       'each chain to rebuild, or two transforms to build a '
                       'new chain between them.')


def chain_root(joint):
    """
    The root joint of the chain the given joint belongs to.

    Arguments:
        joint (str): any joint of a chain

    Return:
        str: the chain's root joint, or None if the node is not a joint
    """
    if not cmds.objExists(joint) or cmds.nodeType(joint) != 'joint':
        return None
    return _walk_to_root(joint)


def _walk_to_root(joint):
    '''Walk up from joint until the parent is not a joint or is a branch
    point (has more than one joint child).'''
    j = joint
    while True:
        parent = cmds.listRelatives(j, parent=True, typ='joint')
        if not parent:
            return j
        siblings = cmds.listRelatives(parent[0], children=True, typ='joint') or []
        if len(siblings) > 1:
            return j
        j = parent[0]


def _guess_rigname(joint):
    '''Extract a rig part name from a joint name using the naming template,
    falling back to a sanitised version of the root joint name sans index.'''
    try:
        rigname = rt_naming.get_rigname(joint, rt_constants.JOINT)
        if rigname:
            return rigname
    except Exception:
        pass
    # Fallback: strip trailing _## suffix (the index) and _BN / _jnt
    name = joint.split('|')[-1]
    parts = name.split('_')
    # Remove known trailing type tags
    while parts and parts[-1] in (rt_constants.JNT, rt_constants.TYPE_BN, rt_constants.TYPE_IK,
                                  rt_constants.TYPE_FK):
        parts.pop()
    # Remove trailing digits (index)
    while parts and parts[-1].isdigit():
        parts.pop()
    return '_'.join(parts) if parts else name


# GUARDS ================================================================

def _guard_chain(joints):
    '''
    Abort if any joint in the chain is unsafe to modify.
    Checks: skinCluster influence, incoming translate/OPM connections,
    branching children, locked translates.
    '''
    for j in joints:
        skin = _find_influence_skin(j)
        if skin:
            abort_build(logger,
                f'Joint {j} is an influence of skinCluster "{skin}". '
                'Unbind before re-spacing the chain.')
        # Check incoming translate connections (driven by rig)
        for attr in ('translate', 'translateX', 'translateY', 'translateZ',
                     'offsetParentMatrix'):
            conns = cmds.listConnections(f'{j}.{attr}', source=True,
                                         destination=False, plugs=True) or []
            if conns:
                abort_build(logger,
                    f'Joint {j}.{attr} has incoming connections — it is '
                    'being driven by a built rig. Remove the rig first.')
        # Locked translates
        for attr in ('translateX', 'translateY', 'translateZ'):
            if cmds.getAttr(f'{j}.{attr}', lock=True):
                abort_build(logger,
                    f'Joint {j}.{attr} is locked. Unlock it first.')

    # Any branch can be orphaned by shrinking, not just one at the tip.
    for index, joint in enumerate(joints):
        children = cmds.listRelatives(joint, children=True, typ='joint') or []
        continuation = joints[index + 1] if index + 1 < len(joints) else None
        extras = [child for child in children
                  if child != continuation and '_ee_' not in child]
        if extras:
            abort_build(logger,
                f'Joint {joint} has branch children ({", ".join(extras)}) '
                'that would be orphaned. Remove or re-parent them before '
                're-spacing.')


def _find_influence_skin(joint):
    '''Return the first skinCluster this joint is an influence of.

    A skinCluster sits DOWNSTREAM of its influences -- joint.worldMatrix
    feeds skinCluster.matrix -- so the lookup follows the joint's future.
    Walking its history instead finds nothing however tightly the joint is
    bound, which is the difference between guarding a skinned chain and
    silently re-spacing one.
    '''
    skins = cmds.listConnections(f'{joint}.worldMatrix', source=False,
                                 destination=True, type='skinCluster') or []
    return skins[0] if skins else None


def _guard_min_length(joints):
    '''Abort if the chain is too short or degenerate.'''
    if len(joints) < 2:
        abort_build(logger, 'Chain has fewer than 2 joints.')
    total = sum(
        rt_math.get_vec_length(joints[i], joints[i + 1])
        for i in range(len(joints) - 1))
    if total < rt_chain_spacing.EPS:
        abort_build(logger, 'All joints in the chain are coincident '
                           '(total length < EPS).')


# WRITING THE RESULT ====================================================

def _write_chain(joints, positions, rigname):
    '''
    Move existing joints to new positions. Handles count changes by
    creating or deleting joints as needed, preserving the _ee_ end joint.

    Arguments:
        joints (list): current chain joints (including _ee_ if present)
        positions (list of [x, y, z]): target positions for BN joints
        rigname (str): rig part name for naming new joints

    Return:
        list: new chain joints (BN only, no _ee_)
    '''
    n = len(positions)
    bn = [j for j in joints if '_ee_' not in j]
    old_n = len(bn)
    ee = _find_ee(joints)
    ee_distance = rt_math.get_vec_length(ee, bn[-1]) if ee else 0.0

    if n == old_n:
        # Names and hierarchy stay exactly as they are; only positions move.
        # The _ee_ still has to follow the tip: a same-count re-space (say
        # Keep -> Uniform) moves the last joint, and an _ee_ left behind
        # would give Setup a bogus final aim direction.
        for j, pos in zip(bn, positions):
            cmds.xform(j, ws=True, t=pos)
        _place_ee(ee, positions, ee_distance)
        return list(bn)

    # Renumber only conventional chains.  Arbitrary artist names are kept
    # positionally; a count change must never silently replace them.
    if n != old_n and _can_renumber(joints, rigname):
        joints = _renumber_chain(joints, rigname)

    if n < old_n:
        return _shrink_chain(joints, positions, rigname, ee_distance)
    else:
        return _grow_chain(joints, positions, rigname, ee_distance)


def _renumber_chain(joints, rigname):
    '''Renumber the chain's BN joints sequentially from the naming template.'''
    bn_joints = [j for j in joints if '_ee_' not in j]
    targets = [rt_naming.fstr(rigname, rt_constants.JOINT, rt_constants.TYPE_BN, i)
               for i in range(len(bn_joints))]
    # Maya cannot swap names in-place.  Move every changing name through a
    # unique temporary name first, then assign the final sequence.
    renamed = []
    for i, (joint, target) in enumerate(zip(bn_joints, targets)):
        if joint != target:
            joint = cmds.rename(joint, f'__tailChainTmp_{i}__')
        renamed.append(joint)
    final_bn = []
    for joint, target in zip(renamed, targets):
        if joint != target:
            logger.info(f'Renaming {joint} -> {target}')
            joint = cmds.rename(joint, target)
        final_bn.append(joint)
    ee = _find_ee(joints)
    return final_bn + ([ee] if ee else [])


def _can_renumber(joints, rigname):
    """Whether every BN joint follows one consistent configured template."""
    bn_joints = [j for j in joints if '_ee_' not in j]
    try:
        return all(rt_naming.get_rigname(j, rt_constants.JOINT) == rigname and
                   isinstance(rt_naming.get_index_from_name(j), int)
                   for j in bn_joints)
    except Exception:
        return False


def _shrink_chain(joints, positions, rigname, ee_distance):
    '''Delete surplus joints, keep first n, reposition.'''
    bn = [j for j in joints if '_ee_' not in j]
    keep = bn[:len(positions)]
    delete = bn[len(positions):]

    # Move keep joints
    for j, pos in zip(keep, positions):
        cmds.xform(j, ws=True, t=pos)

    # Reparent the _ee_ to the new last joint BEFORE deleting the surplus —
    # its old parent is among them. cmds.parent returns the node's new name,
    # which is what _place_ee has to move.
    ee = _find_ee(joints)
    if ee:
        ee = (cmds.parent(ee, keep[-1] if keep else joints[0]) or [ee])[0]

    for j in delete:
        children = cmds.listRelatives(j, children=True, typ='joint') or []
        for child in children:
            cmds.parent(child, keep[-1] if keep else joints[0])
        cmds.delete(j)

    _place_ee(ee, positions, ee_distance)
    return cmds.ls(keep, type='joint') or keep


def _grow_chain(joints, positions, rigname, ee_distance):
    '''Create new joints to reach target count, reposition all.'''
    bn = [j for j in joints if '_ee_' not in j]
    old_n = len(bn)

    # Move existing
    for j, pos in zip(bn, positions[:old_n]):
        cmds.xform(j, ws=True, t=pos)

    # Create new joints
    result = list(bn)
    for i in range(old_n, len(positions)):
        parent = result[-1]
        new_name = rt_naming.fstr(rigname, rt_constants.JOINT, rt_constants.TYPE_BN, i)
        j = cmds.createNode('joint', name=new_name)
        cmds.parent(j, parent)
        cmds.xform(j, ws=True, t=positions[i])
        _copy_joint_attrs(parent, j)
        result.append(j)

    # Move the _ee_ onto the new tip and out along the new final segment.
    ee = _find_ee(joints)
    if ee:
        ee = (cmds.parent(ee, result[-1]) or [ee])[0]
        _place_ee(ee, positions, ee_distance)

    return result


def _copy_joint_attrs(src, dst):
    '''Carry rotateOrder and preferredAngle from the nearest surviving
    neighbour onto a newly created joint, so a grown chain stays uniform in
    the channels the rest of the pipeline reads.'''
    if not src:
        return
    for attr in ('rotateOrder', 'preferredAngleX', 'preferredAngleY',
                 'preferredAngleZ'):
        try:
            cmds.setAttr(f'{dst}.{attr}', cmds.getAttr(f'{src}.{attr}'))
        except (RuntimeError, ValueError) as exc:
            logger.debug(f'Could not copy {attr} {src} -> {dst}: {exc}')


def _place_ee(ee, positions, distance):
    '''Put the _ee_ end joint on the new final segment's direction, at its
    original distance from the old tip.'''
    if not ee or len(positions) < 2:
        return
    last_dir = _norm_vec(_sub(positions[-1], positions[-2]))
    cmds.xform(ee, ws=True,
               t=_add(positions[-1], _scale(last_dir, distance)))


def _find_ee(joints):
    '''Return the _ee_ joint among the list, if any.'''
    for j in joints:
        if '_ee_' in j:
            return j
    return None


def _end_joint(last_bn):
    '''
    The _ee_ end joint hanging off a chain's last BN joint, if any.

    rt_joint.get_joint_chain stops BEFORE the _ee_, so it never appears in the
    chain list and has to be looked up from the tip.  Setup's end-joint
    handling depends on it existing and being sensibly placed, so a rebuild
    that ignored it would leave it behind at the old tip.
    '''
    if not last_bn:
        return None
    for child in cmds.listRelatives(last_bn, children=True, typ='joint') or []:
        if '_ee_' in child:
            return child
    return None


# MATH SHORTCUTS ========================================================

def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _scale(v, s):
    return [v[i] * s for i in range(3)]


def _length_vec(v):
    return math.sqrt(sum(x * x for x in v))


def _norm_vec(v):
    d = _length_vec(v)
    return [x / d for x in v] if d > 1e-12 else v


def _world_matrix(rows, pos):
    ''' 16-float row-major world matrix from axis rows and a position. '''
    return [rows[0][0], rows[0][1], rows[0][2], 0.0,
            rows[1][0], rows[1][1], rows[1][2], 0.0,
            rows[2][0], rows[2][1], rows[2][2], 0.0,
            pos[0], pos[1], pos[2], 1.0]


# PUBLIC API ============================================================

def build_new(start, end, n, rigname=None, mode='uniform', param=None,
              invert=False, orient=False):
    """
    Create a new BN chain between two transforms.

    The two transforms are left untouched — they mark the ends, they do not
    become part of the chain. The new root is parented under start's parent.

    Arguments:
        start (str): start transform name
        end (str): end transform name
        n (int): number of BN joints (n >= 2)
        rigname (str): rig part name for naming, or None to take one from
            start's own name. Nothing depends on getting this right — the
            joints can be renamed in Maya afterwards like any others.
        mode (str): spacing mode
        param (float): mode parameter
        invert (bool): invert distribution
        orient (bool): aim-orient the new joints down the chain instead of
            leaving Maya's default

    Return:
        list: the new BN joints, root first
    """
    if n < 2:
        abort_build(logger, 'Joint count must be at least 2.')
    rigname = rigname or _guess_rigname(start)

    start_pos = cmds.xform(start, q=True, ws=True, t=True)
    end_pos = cmds.xform(end, q=True, ws=True, t=True)
    if _length_vec(_sub(end_pos, start_pos)) < rt_chain_spacing.EPS:
        abort_build(logger, f'{start} and {end} are at the same position — '
                           'a chain needs two distinct ends.')

    with rt_maya.build_performance_scope(name='Joint Chain Builder'):
        positions, _ = rt_chain_spacing.resample(
            [start_pos, end_pos], n, mode, param=param, invert=invert, snap=False)

        # Create joints
        joints = []
        parent = cmds.listRelatives(start, parent=True) or None
        for i, pos in enumerate(positions):
            j = cmds.createNode('joint', name=rt_naming.fstr(
                rigname, rt_constants.JOINT, rt_constants.TYPE_BN, i))
            if joints:
                cmds.parent(j, joints[-1])
            elif parent:
                cmds.parent(j, parent[0])
            cmds.xform(j, ws=True, t=pos)
            joints.append(j)

        if orient:
            _orient_chain(joints, positions)

        logger.info(f'Created new chain {rigname} with {n} joints '
                    f'({mode}{", oriented" if orient else ""}).')
        return joints


def _up_axis_of(joint, up_axis):
    '''
    A joint's current world up-axis direction, or None if unreadable.

    The seed for a cascade orient: it carries whatever roll the chain
    already has (a mirror, a roll_chain fix-up) through the re-aim, so
    orienting a rebuilt chain does not throw that away. Must be read BEFORE
    anything moves the joints.
    '''
    index = {'x': 0, 'y': 1, 'z': 2}.get(up_axis, 2)
    try:
        matrix = cmds.xform(joint, q=True, ws=True, matrix=True)
    except (RuntimeError, ValueError) as exc:
        logger.warning(f'Could not read up axis of {joint}: {exc}')
        return None
    row = matrix[index * 4:index * 4 + 3]
    return row if _length_vec(row) > rt_chain_spacing.EPS else None


def _orient_chain(joints, positions, ee=None, up_ref=None):
    '''
    Aim-orient a chain down its own length.

    Read-only use of rt_setup.aim_frames — the same frames Setup's Orient
    Joints produces, so the two agree rather than fighting. up_ref None is
    Setup's 'best-fit' (roll from the chain's bend plane), which is right for
    a brand-new chain; passing the chain's current up is its 'cascade', which
    keeps the roll an existing chain already had.

    Imported here rather than at module scope so the rest of the tool stays
    usable if Setup is unavailable.

    Arguments:
        joints (list): chain joints, root first.
        positions (list): matching world positions, already written.
        ee (str): the _ee_ end joint to align to the final frame, if any.
        up_ref (list): world up seed, or None for best-fit.
    '''
    import rig_tail_setup as rt_setup

    aim_axis = getattr(rt_constants, 'ORIENT_AIM_AXIS', 'x')
    up_axis = getattr(rt_constants, 'ORIENT_UP_AXIS', 'z')
    frames = rt_setup.aim_frames(positions, aim_axis, up_axis, up_ref)
    for joint, frame, pos in zip(joints, frames, positions):
        _write_frame(joint, frame, pos)
    # The _ee_ is excluded from the chain, so it would keep a stale
    # orientation pointing a different way from everything above it. Give it
    # the last real joint's frame, as Setup does.
    if ee:
        _write_frame(ee, frames[-1],
                     cmds.xform(ee, q=True, ws=True, t=True))
    mode = 'cascade' if up_ref else 'best-fit'
    logger.info(f'Oriented {len(joints)} joints '
                f'(aim {aim_axis}, up {up_axis}, {mode}).')


def _write_frame(joint, frame, pos):
    '''Set a joint's world orientation, landing it in jointOrient.

    The full world matrix is set directly (unambiguous, no euler-order
    dependence); with jointOrient zeroed the orientation arrives in rotate,
    which is then moved into jointOrient with rotate cleared, so the joint
    reads as a clean rest pose.
    '''
    cmds.setAttr(f'{joint}.rotate', 0, 0, 0)
    cmds.setAttr(f'{joint}.jointOrient', 0, 0, 0)
    cmds.xform(joint, ws=True, matrix=_world_matrix(frame, pos))
    rot = cmds.getAttr(f'{joint}.rotate')[0]
    cmds.setAttr(f'{joint}.jointOrient', rot[0], rot[1], rot[2])
    cmds.setAttr(f'{joint}.rotate', 0, 0, 0)


def rebuild(root_joint, n, mode='keep', param=None, invert=False, snap=True,
            orient=False):
    """
    Re-space an existing BN chain at a different joint count.

    Uses the session original cache to avoid compounding distortion.

    Arguments:
        root_joint (str): root joint of the chain
        n (int): target joint count (n >= 2)
        mode (str): spacing mode
        param (float): mode parameter
        invert (bool): invert distribution
        snap (bool): enable snap-to-existing
        orient (bool): aim-orient the chain after moving it. Off by default
            — Setup owns orientation, and the surviving joints otherwise keep
            the aim they had toward their old neighbours. Uses Setup's
            'cascade' up so the chain's existing roll survives.

    Return:
        list: BN joints after rebuild
    """
    if n < 2:
        abort_build(logger, 'Joint count must be at least 2.')

    # Resolve the full chain
    chain = rt_joint.get_joint_chain(root_joint)
    _guard_chain(chain)
    _guard_min_length(chain)

    with rt_maya.build_performance_scope(name='Joint Chain Builder'):
        key = (cmds.ls(root_joint, long=True) or [root_joint])[0]
        current_positions = [cmds.xform(j, q=True, ws=True, t=True)
                             for j in chain if '_ee_' not in j]
        rigname = _guess_rigname(root_joint)
        # Read the cascade seed while the chain still stands as it was:
        # re-spacing moves the joints, so its own roll is unreadable after.
        up_ref = _up_axis_of(
            chain[0], getattr(rt_constants, 'ORIENT_UP_AXIS', 'z')) if orient else None

        # Session original cache: resample from the first-seen positions
        # when the chain has not been hand-edited since the last write.
        if key in _ORIGINALS:
            written = _ORIGINALS[key]['written']
            # Compare against what this tool last wrote.  Positions are plain
            # lists here, not nodes, so the distance is computed inline —
            # rt_math.get_vec_length takes node names.
            drift = max(
                _length_vec(_sub(current_positions[i], written[i]))
                for i in range(min(len(current_positions), len(written)))
            ) if written else float('inf')
            if drift > rt_constants.JOINT_POS_TOLERANCE:
                # Hand-edited — re-baseline
                _ORIGINALS[key]['positions'] = [list(p) for p in current_positions]
            source = _ORIGINALS[key]['positions']
        else:
            _ORIGINALS[key] = {
                'positions': [list(p) for p in current_positions],
                'written': None,
            }
            source = current_positions

        # Resample
        positions, snapped = rt_chain_spacing.resample(
            source, n, mode, param=param, invert=invert, snap=snap)

        # Write.  get_joint_chain stops before the _ee_, so append it here:
        # _write_chain re-parents and re-places it along the new final
        # segment.
        all_joints = list(chain)
        ee = _end_joint(chain[-1])
        if ee:
            all_joints.append(ee)
        result = _write_chain(all_joints, positions, rigname)

        if orient:
            # Re-read the _ee_: a count change re-parents it, and a shrink
            # gave it a new parent entirely.
            _orient_chain(result, positions, _end_joint(result[-1]), up_ref)

        # Update cache
        # A count change can rename the root.  Move the cache to its new
        # long DAG path so subsequent edits still use the original source.
        result_key = (cmds.ls(result[0], long=True) or [result[0]])[0]
        entry = _ORIGINALS.pop(key)
        entry['written'] = [list(p) for p in positions]
        _ORIGINALS[result_key] = entry

        logger.info(f'Rebuilt chain {rigname}: {len(chain)} -> {n} joints'
                    f'{", oriented" if orient else ""}.')
        return result


def rebuild_selected(n, mode='keep', param=None, invert=False, snap=True,
                     orient=False):
    """
    Re-space the chain(s) in the current selection.

    Arguments:
        n (int): target joint count
        mode (str): spacing mode
        param (float): mode parameter
        invert (bool): invert distribution
        snap (bool): enable snap-to-existing
        orient (bool): aim-orient the result, new chains and rebuilds alike

    Return:
        list of list: BN joints per rebuilt chain
    """
    specs = resolve_selection()
    results = []
    for spec in specs:
        if spec.is_new:
            results.append(build_new(spec.start, spec.end, n, spec.rigname,
                                     mode, param, invert, orient))
        else:
            results.append(rebuild(spec.root, n, mode, param, invert, snap,
                                   orient))
    return results


def clear_cache(root=None):
    """
    Forget the session original cache for one or all chains.

    The next rebuild then treats the chain's current positions as the
    original to resample from.

    Arguments:
        root (str): any joint of the chain to forget, or None for every
            cached chain

    Return:
        int: cache entries removed
    """
    if root is None:
        count = len(_ORIGINALS)
        _ORIGINALS.clear()
        logger.info(f'Cleared all {count} chain original caches.')
        return count

    # Accept any joint of the chain, short or long name: the cache is keyed
    # on the root's long DAG path, which is not what a UI has to hand.
    resolved = chain_root(root) or root
    key = (cmds.ls(resolved, long=True) or [resolved])[0]
    if key in _ORIGINALS:
        del _ORIGINALS[key]
        logger.info(f'Cleared cached original for {key}.')
        return 1
    logger.info(f'No cached original for {key}; nothing to clear.')
    return 0
