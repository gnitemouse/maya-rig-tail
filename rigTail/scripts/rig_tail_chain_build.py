"""
rig_tail_chain_build.py
author: Daisy Jane @gnitemouse

Maya I/O layer for Joint Chain Builder.
Detects, guards, creates and rewrites BN joint chains before the Setup
phase. A session cache holds each chain's original shape so repeated
count changes do not compound distortion.

Imports from the core one-way only: no core module may import this one.

Functions:
    build_new: create a new chain between two transforms
    rebuild: re-space an existing chain at a different joint count
    rebuild_selected: rebuild the chain(s) in the current selection
    rename_chain: move ONE chain onto a different rig part name
    resolve_selection: interpret viewport selection as chain spec(s)
    chain_root: the root of the chain a joint belongs to
    clear_cache: forget session original cache for one or all chains

Joints are addressed by full DAG path throughout: a scene may hold two
chains with the same joint names, and a short name cannot say which is
meant.

Settings live nowhere. Every option is a UI widget read at click time,
and each click is a single undo.
"""

import math
import re

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
# Keyed on the root joint's long DAG path; dies on reload, nothing written
# to the scene. Each entry holds the chain's positions the first time it
# was touched this session, plus the last ones written.

_ORIGINALS = {}

# Radius ceiling every rebuild applies: half the mean segment, so adjacent
# joint spheres just touch however dense the chain gets. Without it a chain
# taken from 21 joints to 80 draws as one solid blob.
RADIUS_SEGMENT_FRACTION = 0.5


# SELECTION RESOLUTION ==================================================

class ChainSpec:
    '''Describes one chain operation from a selection.'''

    def __init__(self, root=None, start=None, end=None, rigname=None,
                 is_new=False, selected=None):
        self.root = root          # root joint (rebuild) or None (new)
        self.start = start        # start transform
        self.end = end            # end transform
        self.rigname = rigname    # rig part name
        self.is_new = is_new      # True = build new, False = rebuild
        # The joint picked in the viewport, which 'Build from selected
        # joint' rebuilds down from. Equals root when the root was picked.
        self.selected = selected


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
    # Full paths: the selection is the one place the tool learns which of
    # two identically named chains is meant.
    sel = cmds.ls(selection=True, transforms=True, long=True)
    if not sel:
        abort_build(logger, 'Nothing selected. Select joints or transforms '
                           'to build or rebuild a chain.')

    # Filter to joints only vs any transforms
    joints = [n for n in sel if cmds.nodeType(n) == 'joint']

    if joints:
        # Any joint means rebuild. Resolving each to its chain root and
        # de-duplicating collapses one joint, a whole chain, or joints
        # picked across several tails to one entry per chain.
        roots = {}
        for j in joints:
            root = _walk_to_root(j)
            key = (cmds.ls(root, long=True) or [root])[0]
            spec = roots.get(key)
            if spec is None:
                roots[key] = ChainSpec(root=root, rigname=_guess_rigname(root),
                                       selected=j)
            elif _depth(j) < _depth(spec.selected):
                # Several joints of one chain picked: rebuild from the
                # highest, so the whole picked run is covered.
                spec.selected = j
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
    joint = rt_maya.unique_path(joint)
    if not joint or cmds.nodeType(joint) != 'joint':
        return None
    return _walk_to_root(joint)


def _walk_to_root(joint):
    '''Walk up from joint until the parent is not a joint, is a branch
    point, or belongs to a different rig part.

    The rig-part test stops a tail parented under a spine from reporting a
    spine joint as its base and pulling spine joints into the rebuild.
    Works in full paths, so a duplicated chain still finds its own root.
    '''
    j = rt_maya.unique_path(joint)
    if not j:
        return None
    while True:
        parent = cmds.listRelatives(j, parent=True, typ='joint',
                                    fullPath=True)
        if not parent:
            return j
        siblings = cmds.listRelatives(parent[0], children=True, typ='joint',
                                      fullPath=True) or []
        if len(siblings) > 1:
            return j
        here, above = _guess_rigname(j), _guess_rigname(parent[0])
        if here and above and here != above:
            return j
        j = parent[0]


def _depth(joint):
    '''How far down the DAG a joint sits, for picking the highest of
    several selected joints in one chain.'''
    if not joint:
        return -1
    return (cmds.ls(joint, long=True) or [joint])[0].count('|')


def _guess_rigname(joint):
    '''Extract a rig part name from a joint name using the naming template,
    falling back to a sanitised version of the root joint name sans index.'''
    try:
        # Strict first, then lenient (accepts the convention with its type
        # labels left off, 'BN_C_fintail_1'). Both must agree: the naming
        # decision is made on the lenient parse but the names are built
        # from whatever this returns.
        for lenient in (False, True):
            rigname = rt_naming.get_rigname(joint, rt_constants.JOINT,
                                            lenient=lenient)
            if rigname:
                return rigname
    except Exception:
        pass
    # Fallback: strip the type tags and the index off the joint's own name.
    types = (rt_constants.TYPE_BN, rt_constants.TYPE_IK, rt_constants.TYPE_FK,
             rt_constants.TYPE_FX)
    name = rt_maya.leaf(joint)
    parts = name.split('_')
    # Remove known trailing type tags
    while parts and parts[-1] in (rt_constants.JNT,) + types:
        parts.pop()
    # Remove trailing digits (index)
    while parts and parts[-1].isdigit():
        parts.pop()
    # And the LEADING type tag, or an unparsable name yields 'BN_R_fintail'
    # and the rebuild names its joints 'BN_BN_R_fintail_00_jnt'.
    while len(parts) > 1 and parts[0] in types:
        parts.pop(0)
    return '_'.join(parts) if parts else name


# GUARDS ================================================================

def _guard_chain(joints):
    '''
    Abort if any joint in the chain is unsafe to modify.
    Checks: skinCluster influence, incoming translate/OPM connections,
    branching children, locked translates.
    '''
    for j in joints:
        # Messages name the leaf, which is what the Outliner shows; every
        # cmds call keeps the full path.
        name = rt_maya.leaf(j)
        skin = _find_influence_skin(j)
        if skin:
            abort_build(logger,
                f'Joint {name} is an influence of skinCluster "{skin}". '
                'Unbind before re-spacing the chain.')
        # Check incoming translate connections (driven by rig)
        for attr in ('translate', 'translateX', 'translateY', 'translateZ',
                     'offsetParentMatrix'):
            conns = cmds.listConnections(f'{j}.{attr}', source=True,
                                         destination=False, plugs=True) or []
            if conns:
                abort_build(logger,
                    f'Joint {name}.{attr} has incoming connections — it is '
                    'being driven by a built rig. Remove the rig first.')
        # Locked translates
        for attr in ('translateX', 'translateY', 'translateZ'):
            if cmds.getAttr(f'{j}.{attr}', lock=True):
                abort_build(logger,
                    f'Joint {name}.{attr} is locked. Unlock it first.')

    # Any branch can be orphaned by shrinking, not just one at the tip.
    for index, joint in enumerate(joints):
        children = cmds.listRelatives(joint, children=True, typ='joint',
                                      fullPath=True) or []
        continuation = joints[index + 1] if index + 1 < len(joints) else None
        extras = [child for child in children
                  if child != continuation and not rt_maya.is_end_joint(child)]
        if extras:
            abort_build(logger,
                f'Joint {rt_maya.leaf(joint)} has branch children '
                f'({", ".join(rt_maya.leaf(e) for e in extras)}) that would '
                'be orphaned. Remove or re-parent them before re-spacing.')


def _find_influence_skin(joint):
    '''Return the first skinCluster this joint is an influence of.

    A skinCluster sits DOWNSTREAM of its influences (joint.worldMatrix
    feeds skinCluster.matrix), so the lookup goes forward. Walking history
    instead finds nothing however tightly the joint is bound.
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


# JOINT DISPLAY RADIUS ==================================================

def _joint_radius(joint):
    '''A joint's display radius, or None if it cannot be read.'''
    if not joint:
        return None
    try:
        return cmds.getAttr(f'{joint}.radius')
    except (RuntimeError, ValueError) as exc:
        logger.debug(f'Could not read radius of {joint}: {exc}')
        return None


def _mean_segment(positions):
    '''Mean distance between consecutive positions.'''
    if len(positions) < 2:
        return 0.0
    total = sum(_length_vec(_sub(positions[i + 1], positions[i]))
                for i in range(len(positions) - 1))
    return total / (len(positions) - 1)


def _fit_radius(baseline, positions):
    '''
    The one radius the whole chain should draw at.

    The artist's own radius, never larger than half the new mean segment,
    so a denser chain does not draw as one blob. `baseline` comes from the
    session original cache rather than the chain as it stands, so lowering
    the count again restores the radius instead of ratcheting it down.

    Arguments:
        baseline (float): the chain's own radius, or None if unreadable
        positions (list of [x, y, z]): the positions about to be written

    Return:
        float: radius for every joint in the chain
    '''
    step = _mean_segment(positions) * RADIUS_SEGMENT_FRACTION
    if baseline is None or baseline <= rt_chain_spacing.EPS:
        return step
    return min(baseline, step) if step > rt_chain_spacing.EPS else baseline


def _apply_radius(joints, radius):
    '''Give every joint in the list the same display radius.

    Skips locked or driven radii rather than aborting: the radius is a
    display attribute, and losing it on one joint is not worth failing a
    rebuild over.
    '''
    if radius is None or radius <= rt_chain_spacing.EPS:
        return
    for joint in joints:
        if not joint:
            continue
        try:
            if (cmds.getAttr(f'{joint}.radius', lock=True) or
                    cmds.listConnections(f'{joint}.radius', source=True,
                                         destination=False)):
                continue
            cmds.setAttr(f'{joint}.radius', radius)
        except (RuntimeError, ValueError) as exc:
            logger.debug(f'Could not set radius on {joint}: {exc}')


# WRITING THE RESULT ====================================================

def _clear_rest_pose(joints):
    '''
    Drop the stored rest pose from joints Chain Builder is about to move.

    Works on the joints in hand rather than calling
    rig_tail_restpose.clear_rest_pose, which reads rt_constants.JOINTS_BN:
    Chain Builder runs off the viewport selection and may be pointed at a
    chain that is not in the roster, or in a session where that cache was
    never filled, and either way the cache-driven version would silently
    clear nothing. Best-effort; logs and continues on failure.

    Arguments
        joints (list): chain joints as full DAG paths, _ee_ included.
    '''
    try:
        import rig_tail_restpose as rt_rest
        attr = rt_rest.REST_ATTR
    except Exception as err:
        logger.warning(f'Chain Builder: could not load the rest pose module: {err}')
        return
    for jnt in joints:
        try:
            if not cmds.objExists(jnt):
                continue
            if not cmds.attributeQuery(attr, node=jnt, exists=True):
                continue
            cmds.setAttr(f'{jnt}.{attr}', lock=False)
            cmds.deleteAttr(f'{jnt}.{attr}')
            logger.trace(f'Cleared rest pose on {jnt}')
        except Exception as err:
            logger.warning(f"Chain Builder: could not clear rest pose on '{jnt}': {err}")


def _write_chain(joints, positions, rigname, start_index=0, ee_pos=None,
                 reserved=None):
    '''
    Move existing joints to new positions. Handles count changes by
    creating or deleting joints as needed, preserving the _ee_ end joint.

    Every write ends with the span's indices running start_index,
    start_index+1, ... down the chain, whatever the count did. The
    numbering is guaranteed rather than a side effect of adding or removing
    joints, so a same-count re-space still fixes a mis-numbered chain.

    Arguments:
        joints (list): current chain joints as full DAG paths (including
            _ee_ if present)
        positions (list of [x, y, z]): target positions for BN joints
        rigname (str): rig part name for naming new joints
        start_index (int): the index the span's FIRST joint carries. 0 for a
            rebuild from the base; the selected joint's own index for one
            that starts partway down, so the run continues the numbering
            above it instead of restarting.
        ee_pos ([x, y, z]): world position for the _ee_ end joint, which is
            the resample's own final point when the _ee_ took part in it.
            None falls back to holding the _ee_'s original distance out along
            the new final segment.
        reserved (set): leaf names held by joints ABOVE the span, which the
            renumber must not collide with

    Return:
        list: new chain joints as full DAG paths (BN only, no _ee_)
    '''
    n = len(positions)
    bn = [j for j in joints if not rt_maya.is_end_joint(j)]
    old_n = len(bn)
    ee = _find_ee(joints)
    ee_distance = rt_math.get_vec_length(ee, bn[-1]) if ee else 0.0

    # Every write reaches here, so this is the one place that has to drop the
    # rest anchor (rig_tail_restpose). A stored restMatrix describes where the
    # joints WERE, and the build treats it as the definition of rest and never
    # re-captures - so a chain re-spaced under a surviving anchor rebuilds to
    # its old shape and looks correct doing it. Grow is accidentally safe (the
    # new joints carry no attribute and rest_positions is all-or-nothing), but
    # a same-count re-space and a shrink both leave a complete, stale list.
    _clear_rest_pose(joints)

    # Renumber BEFORE the count changes, so the rename pass only ever sees
    # joints that already exist; _grow_chain names what it creates itself.
    joints = _renumber_chain(joints, rigname, start_index, reserved)

    if n == old_n:
        # Hierarchy unchanged, positions only. The _ee_ still follows the
        # tip - a same-count re-space moves the last joint, and an _ee_
        # left behind gives Setup a bogus final aim direction.
        bn = [j for j in joints if not rt_maya.is_end_joint(j)]
        for j, pos in zip(bn, positions):
            cmds.xform(j, ws=True, t=pos)
        _place_ee(_find_ee(joints), positions, ee_distance, ee_pos)
        return list(bn)

    if n < old_n:
        return _shrink_chain(joints, positions, rigname, ee_distance, ee_pos)
    else:
        return _grow_chain(joints, positions, rigname, ee_distance,
                           start_index, ee_pos)


def _renumber_chain(joints, rigname, start_index=0, reserved=None):
    '''
    Rewrite the span's joint indices so they increment by one from
    start_index, and return the span with its joints re-resolved.

    Two naming modes, chosen for the span as a whole: a conventional chain
    is rebuilt from the naming template, an unconventional one keeps its own
    names and has only its index token rewritten. Deciding per chain rather
    than per joint stops a span coming out half template-named and half
    artist-named, and costs nothing either way.

    Arguments:
        joints (list): span joints as full DAG paths, _ee_ included
        rigname (str): rig part name
        start_index (int): index for the span's first joint
        reserved (set): leaf names held by joints ABOVE the span. A
            from-selected rebuild leaves those untouched, so a target that
            lands on one would create a second joint of that name in the
            same chain - reported rather than prevented, since the fix is to
            renumber the run above and that is not this rebuild's to do.

    Return:
        list: the span's joints, full DAG paths, _ee_ last if there is one
    '''
    bn_joints = [j for j in joints if not rt_maya.is_end_joint(j)]
    ee = _find_ee(joints)
    if not bn_joints:
        return list(joints)

    targets, mode = _index_targets(bn_joints, rigname, start_index)
    if not targets:
        return list(joints)

    clashes = sorted(set(targets) & set(reserved or ()))
    if clashes:
        logger.warning(
            f'Renumbering from {start_index} reuses {len(clashes)} name(s) '
            f'already held further up the chain ({", ".join(clashes[:3])}). '
            'The joints above the rebuilt span are numbered inconsistently; '
            'rebuild from the base joint to renumber the whole chain.')

    # Track by UUID and re-resolve the path at every step: renaming a joint
    # invalidates the stored paths of everything below it, so the list this
    # started with goes stale immediately. That includes the _ee_ even when
    # its own name never changes.
    uuids = [(cmds.ls(j, uuid=True) or [None])[0] for j in bn_joints]
    originals = [rt_maya.leaf(j) for j in bn_joints]
    ee_uuid = (cmds.ls(ee, uuid=True) or [None])[0] if ee else None

    # Maya cannot swap names in-place, and a shifted run's targets overlap
    # its current names. EVERY joint goes through a temporary name first,
    # so no target collides with one not yet moved out of the way.
    for i, uuid in enumerate(uuids):
        current = cmds.ls(uuid, long=True) if uuid else None
        if current:
            cmds.rename(current[0], f'__tailChainTmp_{i}__')

    final_bn = []
    renamed = 0
    for uuid, target, before in zip(uuids, targets, originals):
        current = cmds.ls(uuid, long=True) if uuid else None
        if not current:
            continue
        cmds.rename(current[0], target)
        if before != target:
            logger.debug(f'Renaming {before} -> {target}')
            renamed += 1
        final_bn.append((cmds.ls(uuid, long=True) or [None])[0])

    # The end joint is part of the convention too. Only template mode knows
    # what to call it; in-place mode leaves the artist's name alone.
    if ee_uuid and mode == 'template':
        ee_target = rt_naming.fstr(rigname, rt_constants.JOINT,
                                   rt_constants.TYPE_BN, 'ee')
        current = cmds.ls(ee_uuid, long=True)
        if current and rt_maya.leaf(current[0]) != ee_target:
            logger.debug(f'Renaming {rt_maya.leaf(current[0])} -> {ee_target}')
            cmds.rename(current[0], ee_target)
            renamed += 1

    if renamed:
        logger.info(f'Renumbered {renamed} joint(s) from {start_index} '
                    f'({mode} naming).')
    # Read every path back off its UUID: the renames above moved all of them.
    final_bn = [p for p in final_bn if p]
    ee = (cmds.ls(ee_uuid, long=True) or [None])[0] if ee_uuid else None
    return final_bn + ([ee] if ee else [])


def _index_targets(bn_joints, rigname, start_index=0):
    """
    The target leaf name for every BN joint of a span, indices incrementing
    by one from start_index.

    Return:
        tuple: (targets, mode). mode is 'template' when every joint parses
        under the configured naming template as ONE rig part, so the names
        are rebuilt from it; 'in-place' otherwise, where each joint keeps its
        own name and only its index token is rewritten. A joint with no
        index token at all keeps its name unchanged and the run counts on
        past it. (None, None) when the names cannot be read at all.

    The test is that the chain parses consistently, not that it already
    carries `rigname` - a rename moves a conventional chain onto a NEW rig
    part name, and the template still says what its joints are called.

    The parse is LENIENT, so the convention with its type labels left off
    ('BN_C_fintail_1') is recognised and conformed rather than left
    half-named. The leniency stops there: a TYPE prefix and an index are
    both still required, so 'tentacle_bone_01' keeps its artist name.
    """
    try:
        parsed = {rt_naming.get_rigname(j, rt_constants.JOINT, lenient=True)
                  for j in bn_joints}
        conventional = (
            len(parsed) == 1 and None not in parsed and
            all(isinstance(rt_naming.get_index_from_name(j), int)
                for j in bn_joints))

        if conventional:
            return [rt_naming.fstr(rigname, rt_constants.JOINT,
                                   rt_constants.TYPE_BN, start_index + i)
                    for i in range(len(bn_joints))], 'template'

        return [rt_naming.replace_index_in_name(j, start_index + i)
                for i, j in enumerate(bn_joints)], 'in-place'
    except Exception as exc:
        logger.warning(f'Could not work out joint names to renumber to: '
                       f'{exc}. Names left as they are.')
        return None, None


def _shrink_chain(joints, positions, rigname, ee_distance, ee_pos=None):
    '''Delete surplus joints, keep first n, reposition.'''
    bn = [j for j in joints if not rt_maya.is_end_joint(j)]
    keep = bn[:len(positions)]
    delete = bn[len(positions):]

    # Move keep joints
    for j, pos in zip(keep, positions):
        cmds.xform(j, ws=True, t=pos)

    # Reparent the _ee_ BEFORE deleting the surplus - its old parent is
    # among them. The result is re-resolved to a path, since _place_ee has
    # to move THIS _ee_ and not a same-named one in another chain.
    anchor = keep[-1] if keep else joints[0]
    ee = _find_ee(joints)
    if ee:
        ee = _reparent(ee, anchor)

    for j in delete:
        children = cmds.listRelatives(j, children=True, typ='joint',
                                      fullPath=True) or []
        for child in children:
            _reparent(child, anchor)
        cmds.delete(j)

    _place_ee(ee, positions, ee_distance, ee_pos)
    return [(cmds.ls(j, long=True) or [j])[0] for j in keep]


def _grow_chain(joints, positions, rigname, ee_distance, start_index=0,
                ee_pos=None):
    '''Create new joints to reach target count, reposition all.'''
    bn = [j for j in joints if not rt_maya.is_end_joint(j)]
    old_n = len(bn)

    # Move existing
    for j, pos in zip(bn, positions[:old_n]):
        cmds.xform(j, ws=True, t=pos)

    # Naming mode is settled once for the span, so a chain the template
    # cannot parse grows joints continuing ITS naming rather than reverting
    # to the template halfway down.
    _, mode = _index_targets(bn, rigname, start_index)

    # Create new joints
    result = list(bn)
    for i in range(old_n, len(positions)):
        parent = result[-1]
        new_name = _grown_name(bn, rigname, mode, start_index, i)
        j = _create_joint(new_name, parent)
        cmds.xform(j, ws=True, t=positions[i])
        _copy_joint_attrs(parent, j)
        result.append(j)

    # Move the _ee_ onto the new tip and out along the new final segment.
    ee = _find_ee(joints)
    if ee:
        ee = _reparent(ee, result[-1])
        _place_ee(ee, positions, ee_distance, ee_pos)

    return [(cmds.ls(j, long=True) or [j])[0] for j in result]


def _add_end_joint(joints, positions, rigname):
    '''
    Give a chain that has no '_ee_' one, a segment out past its tip.

    The new joint continues the chain's final direction at the length of its
    final segment, so the tail gets one segment longer rather than its
    joints being re-spread to make room - asking for an end joint marks
    where the tail carries on to, it does not move what is already placed.

    Downstream, Setup aims the last real joint at the '_ee_' instead of
    guessing a final direction, and a later rebuild treats it as the end of
    the tail's length, so the chain re-spaces up to here from now on.

    Arguments:
        joints (list): the chain's BN joints, full DAG paths, root first
        positions (list): the world positions just written to them
        rigname (str): rig part name, for the template naming

    Return:
        str or None: the new joint's DAG path, or None when there is
        nothing sensible to extend (fewer than two joints, or a final
        segment of no length)
    '''
    if len(joints) < 2 or len(positions) < 2:
        logger.warning('Cannot add an end joint to a chain of fewer than two '
                       'joints: there is no final direction to continue.')
        return None
    step = _sub(positions[-1], positions[-2])
    distance = _length_vec(step)
    if distance < rt_chain_spacing.EPS:
        logger.warning('Cannot add an end joint: the chain\'s final segment '
                       'has no length to continue along.')
        return None

    _, mode = _index_targets(joints, rigname, 0)
    name = _end_joint_name(joints[-1], rigname, mode)
    ee = _create_joint(name, joints[-1])
    cmds.xform(ee, ws=True,
               t=_add(positions[-1], _scale(_norm_vec(step), distance)))
    _copy_joint_attrs(joints[-1], ee)
    logger.info(f'Added end joint {rt_maya.leaf(ee)}, one segment '
                f'({distance:.4f}) past the tip.')
    return ee


def _end_joint_name(last_bn, rigname, mode):
    '''
    What to call a chain's '_ee_' end joint.

    Template mode takes it from the naming template. In-place mode swaps the
    chain's own index token for 'ee', so an artist-named chain does not
    sprout one joint following a convention the rest of it does not. With no
    index token to swap the template is the fallback - a conventional name
    beats two joints called the same thing.
    '''
    if mode == 'in-place':
        name = rt_naming.replace_index_in_name(last_bn, 'ee')
        if name != rt_maya.leaf(last_bn) and rt_maya.is_end_joint(name):
            return name
    return rt_naming.fstr(rigname, rt_constants.JOINT, rt_constants.TYPE_BN,
                          'ee')


def _grown_name(bn, rigname, mode, start_index, index):
    '''
    What to call a joint the rebuild is about to create, at chain position
    `index`.

    Follows the span's own naming: the template when the existing joints
    parse as this rig part, otherwise the last joint's name with its index
    token rewritten - without which raising the count would leave an
    artist-named chain named two ways down its length. With no index token
    to rewrite there is no stem to follow, so the template is all that is
    left.
    '''
    if mode == 'in-place' and bn:
        grown = rt_naming.replace_index_in_name(bn[-1], start_index + index)
        if grown != rt_maya.leaf(bn[-1]):
            return grown
    return rt_naming.fstr(rigname, rt_constants.JOINT, rt_constants.TYPE_BN,
                          start_index + index)


def _create_joint(name, parent=None):
    '''
    Create a joint under `parent` and return its full DAG path.

    Created already parented, since the short name cmds.createNode answers
    with is not safe to hand back to cmds.parent while another chain holds
    a joint of that name. The path is built from the name Maya actually
    used, which a clash under one parent may have uniquified.
    '''
    made = cmds.createNode('joint', name=name, parent=parent) if parent \
        else cmds.createNode('joint', name=name)
    leaf = rt_maya.leaf(made)
    if parent:
        parent_path = (cmds.ls(parent, long=True) or [parent])[0]
        return f'{parent_path}|{leaf}'
    return f'|{leaf}'


def _reparent(node, parent):
    '''
    Parent a node and return its new full DAG path.

    cmds.parent answers with a short name, which is the one name not safe to
    keep hold of: another chain may hold a joint called the same thing.
    '''
    moved = cmds.parent(node, parent)
    if not moved:
        return (cmds.ls(node, long=True) or [node])[0]
    parent_path = (cmds.ls(parent, long=True) or [parent])[0]
    return f'{parent_path}|{rt_maya.leaf(moved[0])}'


def _copy_joint_attrs(src, dst):
    '''Carry rotateOrder, preferredAngle and radius from the nearest
    surviving neighbour onto a new joint, so a grown chain stays uniform in
    the channels the rest of the pipeline reads. radius is visual only, but
    a fresh joint draws at Maya's default 1.0 and swamps the chain until the
    chain-wide pass settles the final value.
    '''
    if not src:
        return
    for attr in ('rotateOrder', 'preferredAngleX', 'preferredAngleY',
                 'preferredAngleZ', 'radius'):
        try:
            cmds.setAttr(f'{dst}.{attr}', cmds.getAttr(f'{src}.{attr}'))
        except (RuntimeError, ValueError) as exc:
            logger.debug(f'Could not copy {attr} {src} -> {dst}: {exc}')


def _place_ee(ee, positions, distance, target=None):
    '''Put the _ee_ end joint where the rebuild wants it.

    `target` is the resample's own final point: the _ee_ marks the end of
    the tail's length, so it takes part in the resample and lands back
    where it was, whatever the joint count. Without one, fall back to its
    original distance out along the new final segment, which still gives
    Setup a sane final aim direction.
    '''
    if not ee:
        return
    if target is not None:
        cmds.xform(ee, ws=True, t=list(target))
        return
    if len(positions) < 2:
        return
    last_dir = _norm_vec(_sub(positions[-1], positions[-2]))
    cmds.xform(ee, ws=True,
               t=_add(positions[-1], _scale(last_dir, distance)))


def _find_ee(joints):
    '''Return the _ee_ joint among the list, if any.'''
    for j in joints:
        if rt_maya.is_end_joint(j):
            return j
    return None


def _end_joint(last_bn):
    '''
    The _ee_ end joint hanging off a chain's last BN joint, if any.

    Chain detection stops BEFORE the _ee_, so it never appears in the chain
    list and has to be looked up from the tip. A rebuild that ignored it
    would leave it behind at the old tip, which Setup then aims at.
    '''
    if not last_bn:
        return None
    for child in cmds.listRelatives(last_bn, children=True, typ='joint',
                                    fullPath=True) or []:
        if rt_maya.is_end_joint(child):
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
              invert=False, orient=False, add_ee=False):
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
        add_ee (bool): finish the chain with an '_ee_' end joint, a segment
            out past the tip

    Return:
        list: the new BN joints, root first ('_ee_' excluded, as everywhere)
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

        # A new chain is numbered from 00 by definition, and tracked by full
        # DAG path from the moment its joints are parented.
        joints = []
        parent = cmds.listRelatives(start, parent=True, fullPath=True) or None
        for i, pos in enumerate(positions):
            name = rt_naming.fstr(rigname, rt_constants.JOINT,
                                  rt_constants.TYPE_BN, i)
            j = _create_joint(name, joints[-1] if joints else
                              (parent[0] if parent else None))
            cmds.xform(j, ws=True, t=pos)
            joints.append(j)

        ee = _add_end_joint(joints, positions, rigname) if add_ee else None

        # Nothing to keep on a brand-new chain, and Maya's default 1.0 has
        # nothing to do with its scale, so fit the radius to the spacing.
        _apply_radius(joints + ([ee] if ee else []),
                      _fit_radius(_joint_radius(start), positions))

        if orient:
            _orient_chain(joints, positions, ee)

        logger.info(f'Created new chain {rigname} with {n} joints '
                    f'({mode}{", oriented" if orient else ""}'
                    f'{", end joint" if ee else ""}).')
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

    Produces the same frames Setup's Orient Joints does, so the two agree
    rather than fighting. up_ref None is Setup's 'best-fit' (roll from the
    chain's bend plane), right for a brand-new chain; passing the chain's
    current up is its 'cascade', which keeps the roll it already had.

    Setup is imported here, not at module scope, so the rest of the tool
    still works without it.

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
    # Read the _ee_ position BEFORE re-orienting. Nothing re-places the
    # _ee_, so it swings with its parent when the tip is re-aimed, and
    # reading it afterwards bakes that swing in - leaving it perpendicular
    # to the chain it should continue. _place_ee already put it on the
    # final segment; this pins it there.
    ee_pos = cmds.xform(ee, q=True, ws=True, t=True) if ee else None
    for joint, frame, pos in zip(joints, frames, positions):
        _write_frame(joint, frame, pos)
    # Excluded from the chain, the _ee_ would otherwise keep a stale
    # orientation, so give it the last real joint's frame.
    if ee:
        _write_frame(ee, frames[-1], ee_pos)
    mode = 'cascade' if up_ref else 'best-fit'
    logger.info(f'Oriented {len(joints)} joints '
                f'(aim {aim_axis}, up {up_axis}, {mode}).')


def _write_frame(joint, frame, pos):
    '''Set a joint's world orientation, landing it in jointOrient.

    The world matrix is set directly (no euler-order dependence); with
    jointOrient zeroed the orientation arrives in rotate, which is then
    moved into jointOrient, leaving the joint at a clean rest pose.
    '''
    cmds.setAttr(f'{joint}.rotate', 0, 0, 0)
    cmds.setAttr(f'{joint}.jointOrient', 0, 0, 0)
    cmds.xform(joint, ws=True, matrix=_world_matrix(frame, pos))
    rot = cmds.getAttr(f'{joint}.rotate')[0]
    cmds.setAttr(f'{joint}.jointOrient', rot[0], rot[1], rot[2])
    cmds.setAttr(f'{joint}.rotate', 0, 0, 0)


def rebuild(root_joint, n, mode='power', param=None, invert=False, snap=True,
            orient=False, start_joint=None, add_ee=False):
    """
    Re-space an existing BN chain at a different joint count.

    Uses the session original cache to avoid compounding distortion.

    Where the chain ends depends on whether it has an _ee_ end joint. With
    one, the _ee_ is the end: it stays put at every count and the BN joints
    are spread over the full length up to it, so the last BN joint stops one
    segment short of the _ee_ and that gap narrows as the count rises.
    Without one, the last BN joint is the end and is pinned there itself.

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
        start_joint (str): rebuild only the span from this joint down to the
            tip, leaving everything above it untouched. None (the default)
            rebuilds the whole chain, base to tip. The start joint itself
            never moves — it is an endpoint of the resample — so the joint
            above it keeps aiming at exactly where it was.

        add_ee (bool): give the chain an '_ee_' end joint if it has not got
            one, a segment out past the tip. Ignored when the chain already
            has one - that joint is the tail's end, and is never replaced
            or removed.

    Numbering: the rebuilt span always comes out with its indices
    incrementing by one down the chain. From the base that run starts at 00;
    from a start_joint it starts at that joint's OWN index, so it continues
    the numbering of the untouched run above rather than restarting under it.

    Return:
        list: BN joints after rebuild, the span's own joints only, as full
        DAG paths
    """
    if n < 2:
        abort_build(logger, 'Joint count must be at least 2.')

    # Resolve the chain, then narrow it to the span actually being rebuilt.
    # Guards and the min-length check run on the span: joints above it are
    # never read or written, so a skinned joint up there is not this
    # rebuild's problem.
    full_chain = rt_joint.get_joint_chain(root_joint)
    if not full_chain:
        abort_build(logger, f'No chain found from {root_joint}. If the name '
                            'is shared by more than one joint, select the '
                            'chain in the viewport instead of typing a name.')
    # Where the rebuilt span begins, as a position in the chain...
    span_at = 0
    if start_joint:
        resolved = rt_maya.unique_path(start_joint)
        if resolved and resolved in full_chain:
            span_at = full_chain.index(resolved)
        else:
            logger.warning(f'{rt_maya.leaf(start_joint)} is not in the chain '
                           f'under {rt_maya.leaf(root_joint)}; rebuilding '
                           'from the base instead.')
    chain = full_chain[span_at:]
    if len(chain) < 2:
        abort_build(logger,
            f'Nothing to rebuild from {rt_maya.leaf(start_joint)}: it is the '
            'last joint of the chain. Pick a joint further up, or build from '
            'the base.')

    # ...and the index its first joint is NUMBERED with, which is not the
    # same thing. The span has to continue the untouched run's numbering:
    # using the position instead renumbers a span picked at '_05_jnt' of a
    # chain numbered from 01 as if it started at 04, colliding with the
    # joint above it. From the base the answer is 0 by definition.
    start_index = 0
    if span_at:
        named = rt_naming.get_index_from_name(chain[0])
        start_index = named if isinstance(named, int) else span_at
        if not isinstance(named, int):
            logger.debug(f'{rt_maya.leaf(chain[0])} carries no index to '
                         f'continue from; numbering the span from {span_at}.')
    # The names the run above holds, so the renumber can report a collision
    # rather than quietly making a duplicate.
    reserved = {rt_maya.leaf(j) for j in full_chain[:span_at]}
    _guard_chain(chain)
    _guard_min_length(chain)

    with rt_maya.build_performance_scope(name='Joint Chain Builder'):
        # Keyed on the span's own first joint, so a from-the-base rebuild
        # and a from-partway one keep separate baselines. Already a full
        # DAG path, which is what tells two same-named chains apart.
        key = chain[0]
        current_positions = [cmds.xform(j, q=True, ws=True, t=True)
                             for j in chain if not rt_maya.is_end_joint(j)]
        # The _ee_ is where the tail ends, so it takes part in the resample
        # as its last point rather than being dragged behind the tip. That
        # pins it in world and spreads the BN joints over the whole length
        # up to it. Without one, the last BN joint is the end and is pinned.
        ee = _end_joint(chain[-1])
        ee_source = cmds.xform(ee, q=True, ws=True, t=True) if ee else None
        if ee_source and _length_vec(
                _sub(ee_source, current_positions[-1])) < rt_chain_spacing.EPS:
            # An _ee_ sitting on top of the tip adds no length and would
            # give the resample a zero-length final span. Leave it out and
            # let it follow the tip instead.
            ee_source = None
        if ee_source:
            current_positions.append(ee_source)
        rigname = _guess_rigname(root_joint)
        # Read the cascade seed while the chain still stands as it was:
        # re-spacing moves the joints, so its own roll is unreadable after.
        up_ref = _up_axis_of(
            chain[0], getattr(rt_constants, 'ORIENT_UP_AXIS', 'z')) if orient else None

        # Session original cache: resample from the first-seen positions
        # when the chain has not been hand-edited since the last write.
        if key in _ORIGINALS:
            written = _ORIGINALS[key]['written']
            # Compare against what this tool last wrote. These are plain
            # position lists, not nodes, so the distance is inline.
            drift = max(
                _length_vec(_sub(current_positions[i], written[i]))
                for i in range(min(len(current_positions), len(written)))
            ) if written else float('inf')
            if (drift > rt_constants.JOINT_POS_TOLERANCE or
                    _ORIGINALS[key].get('has_ee') != bool(ee_source)):
                # Hand-edited, or the _ee_ came or went: either way the
                # cached source no longer describes the length being
                # re-spaced, so re-baseline.
                _ORIGINALS[key]['positions'] = [list(p) for p in current_positions]
                _ORIGINALS[key]['radius'] = _joint_radius(chain[0])
                _ORIGINALS[key]['has_ee'] = bool(ee_source)
            source = _ORIGINALS[key]['positions']
        else:
            _ORIGINALS[key] = {
                'positions': [list(p) for p in current_positions],
                'written': None,
                # Whether that baseline runs out to an _ee_, so a later
                # rebuild can tell a stale source from a live one.
                'has_ee': bool(ee_source),
                # The radius as the artist left it, cached for the same
                # reason the positions are: every rebuild fits to this, so
                # raising the count and lowering it again comes back.
                'radius': _joint_radius(chain[0]),
            }
            source = current_positions

        # With an _ee_ in the source the chain needs one more point than it
        # has BN joints: the last one belongs to the _ee_.
        positions, snapped = rt_chain_spacing.resample(
            source, n + 1 if ee_source else n, mode,
            param=param, invert=invert, snap=snap)
        ee_pos = positions.pop() if ee_source else None

        # Chain detection stops before the _ee_, so append it here for
        # _write_chain to re-parent onto the new tip and re-place.
        all_joints = list(chain)
        if ee:
            all_joints.append(ee)
        result = _write_chain(all_joints, positions, rigname, start_index,
                              ee_pos, reserved)

        # An end joint the chain never had, if asked for. After the write:
        # it took no part in the resample, but it must exist before the
        # radius and orient passes below, which both read it.
        if add_ee and not ee:
            _add_end_joint(result, positions, rigname)

        # One radius for the whole span, sized to the spacing it ended up
        # with, so a grown chain does not mix the artist's radius with
        # Maya's default 1.0 on the joints just created.
        ee = _end_joint(result[-1])
        _apply_radius(result + ([ee] if ee else []),
                      _fit_radius(_ORIGINALS[key].get('radius'), positions))

        if orient:
            # ee was re-read above: a count change re-parents it.
            _orient_chain(result, positions, ee, up_ref)

        # A count change can rename the root, so move the cache entry to its
        # new path or later edits lose the original source.
        result_key = (cmds.ls(result[0], long=True) or [result[0]])[0]
        entry = _ORIGINALS.pop(key)
        # 'written' mirrors current_positions point for point, _ee_
        # included, so the next drift check notices a hand-moved _ee_ too.
        entry['written'] = ([list(p) for p in positions] +
                            ([list(ee_pos)] if ee_pos else []))
        _ORIGINALS[result_key] = entry

        # result[0] rather than chain[0]: the renumber may have renamed it.
        span = 'base' if not span_at else f'joint {rt_maya.leaf(result[0])}'
        logger.info(f'Rebuilt chain {rigname} from {span}: {len(chain)} -> '
                    f'{n} joints, numbered from {start_index}'
                    f'{", oriented" if orient else ""}.')
        return result


def rebuild_selected(n, mode='power', param=None, invert=False, snap=True,
                     orient=False, from_selected=False, add_ee=False):
    """
    Re-space the chain(s) in the current selection.

    Arguments:
        n (int): target joint count
        mode (str): spacing mode
        param (float): mode parameter
        invert (bool): invert distribution
        snap (bool): enable snap-to-existing
        orient (bool): aim-orient the result, new chains and rebuilds alike
        from_selected (bool): rebuild each chain from the joint that was
            picked rather than from its base joint
        add_ee (bool): give each chain an '_ee_' end joint if it has not got
            one, a segment out past the tip

    Return:
        list of list: BN joints per rebuilt chain
    """
    specs = resolve_selection()
    results = []
    for spec in specs:
        if spec.is_new:
            results.append(build_new(spec.start, spec.end, n, spec.rigname,
                                     mode, param, invert, orient, add_ee))
        else:
            results.append(rebuild(spec.root, n, mode, param, invert, snap,
                                   orient,
                                   spec.selected if from_selected else None,
                                   add_ee))
    return results


def rename_chain(root_joint, rigname):
    """
    Rename ONE chain's joints onto a new rig part name, in place.

    Scoped to the one chain by DAG path, which is what makes it usable while
    a second chain still answers to the old rig part name - a scene-wide
    rename by name token would move both and leave the collision as it was.

    The point is to park a chain out of the roster without deleting it. A
    rig part name RIGPARTS does not list is ignored by Setup and by the
    build, so the old tail can sit in the scene, skin and all, while its
    replacement takes over the name. Joints are renumbered from 00 as they
    are renamed, so the parked chain comes out conventional either way.

    Arguments:
        root_joint (str): any joint of the chain (a DAG path when the name
            is shared)
        rigname (str): the new rig part name, e.g. 'R_fintailOld'

    Return:
        list: the chain's joints after renaming, as full DAG paths

    Raises:
        RuntimeError: via abort_build, when the chain cannot be resolved or
        the new name is already carried by joints outside this chain.
    """
    rigname = (rigname or '').strip()
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', rigname):
        abort_build(logger, f'Invalid rig part name "{rigname}". Use letters, '
                            'digits and underscores; do not start with a '
                            'digit.')

    root = chain_root(root_joint)
    if not root:
        abort_build(logger, f'No chain found from {rt_maya.leaf(root_joint)}. '
                            'Select a joint of the chain to rename.')
    chain = rt_joint.get_joint_chain(root)
    if not chain:
        abort_build(logger, f'No chain found from {rt_maya.leaf(root)}.')
    ee = _end_joint(chain[-1])
    span = list(chain) + ([ee] if ee else [])

    # Refuse to rename INTO a collision. Joints of this chain are excluded:
    # renaming a chain onto the name it already has is a renumber, not a
    # clash.
    mine = set(span)
    taken = []
    for i in range(len(chain)):
        target = rt_naming.fstr(rigname, rt_constants.JOINT,
                                rt_constants.TYPE_BN, i)
        taken += [m for m in (cmds.ls(target, long=True) or [])
                  if m not in mine]
    if taken:
        abort_build(logger,
            f'"{rigname}" is already used by {len(taken)} joint(s) elsewhere '
            f'({", ".join(taken[:3])}). Pick a name no other chain uses.')

    with rt_maya.build_performance_scope(name='Joint Chain Builder'):
        renamed = _renumber_chain(span, rigname, 0, reserved=None)
        # The cache is keyed on the root's path, which the rename just
        # changed. Carry the entry across so the chain does not lose its
        # baseline shape by being renamed.
        old_key, new_key = chain[0], renamed[0] if renamed else None
        if old_key in _ORIGINALS and new_key:
            _ORIGINALS[new_key] = _ORIGINALS.pop(old_key)
        logger.info(f'Renamed chain to rig part "{rigname}" '
                    f'({len(chain)} joints, numbered from 0).')
        return [j for j in renamed if not rt_maya.is_end_joint(j)]


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
