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
    rename_chain: move ONE chain onto a different rig part name
    resolve_selection: interpret viewport selection as chain spec(s)
    chain_root: the root of the chain a joint belongs to
    clear_cache: forget session original cache for one or all chains

Joints are addressed by full DAG path throughout. A scene may hold two
chains with the same joint names - a replacement tail built alongside the
one it will replace - and a short name cannot say which is meant; Maya
answers an ambiguous one with 'More than one object matches name'.

Settings deliberately live nowhere: every option is a UI widget read at
click time. No config file, no preferences — the tool is a handful of
controls and each click is a single undo.
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
# Module global, dies on reload, no scene metadata.
# Keyed on the root joint's long DAG path. Each entry stores the
# original positions (the first time the chain was touched this session)
# and the last-written positions.

_ORIGINALS = {}

# Display radius given to a chain that has none worth keeping, and the ceiling
# every rebuild applies: half the mean segment, so the spheres of adjacent
# joints just touch however dense the chain gets.  Without it a chain taken
# from 21 joints to 80 keeps a radius set for the old spacing and draws as one
# solid blob.
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
        # The joint actually picked in the viewport, which is what
        # 'Build from selected joint' rebuilds down from.  Equal to root
        # whenever the root itself was the pick.
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
    # Full paths: the selection is the ONE place the tool learns which of
    # two identically named chains the artist means, so the answer must
    # survive being written down.
    sel = cmds.ls(selection=True, transforms=True, long=True)
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
            spec = roots.get(key)
            if spec is None:
                roots[key] = ChainSpec(root=root, rigname=_guess_rigname(root),
                                       selected=j)
            elif _depth(j) < _depth(spec.selected):
                # Several joints of one chain picked: the highest one is what
                # 'from selected joint' rebuilds down from, so the whole
                # picked run is covered rather than its tail end.
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
    point (has more than one joint child), or belongs to a different rig
    part.

    The rig-part test is what stops a tail parented under a spine from
    reporting a spine joint as its base: the spine has one child, is a
    joint, and would otherwise be walked straight through, putting spine
    joints inside the chain a rebuild re-spaces.

    Works in full DAG paths, so a chain whose joint names are duplicated
    elsewhere in the scene still walks to ITS OWN root.
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
        rigname = rt_naming.get_rigname(joint, rt_constants.JOINT)
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
    # And the LEADING type tag. Without this a joint whose name the template
    # cannot parse yields 'BN_R_fintail' rather than 'R_fintail', and the
    # rebuild goes on to name its new joints 'BN_BN_R_fintail_00_jnt'.
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
        # Joints arrive as full DAG paths (see rt_joint.get_joint_chain).
        # Messages name the leaf, which is what the artist sees in the
        # Outliner, but every cmds call keeps the path.
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

    The artist's own radius, never larger than half the new mean segment.
    The cap is what stops a chain looking like a single blob once the count
    goes up: the joints get closer together, so the spheres have to get
    smaller with them.  `baseline` comes from the session original cache,
    not from the chain as it stands, so lowering the count again restores
    the radius rather than ratcheting it down for good.

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

def _write_chain(joints, positions, rigname, start_index=0, ee_pos=None,
                 reserved=None):
    '''
    Move existing joints to new positions. Handles count changes by
    creating or deleting joints as needed, preserving the _ee_ end joint.

    Every write ends with the span's indices running start_index,
    start_index+1, ... down the chain, whatever the count did. Numbering is
    part of what the tool guarantees, not a side effect of adding or
    removing joints: a chain that was mis-numbered before a same-count
    re-space used to stay mis-numbered, because there was no rename pass to
    ride along with.

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
            renumber must not collide with. See _renumber_chain.

    Return:
        list: new chain joints as full DAG paths (BN only, no _ee_)
    '''
    n = len(positions)
    bn = [j for j in joints if not rt_maya.is_end_joint(j)]
    old_n = len(bn)
    ee = _find_ee(joints)
    ee_distance = rt_math.get_vec_length(ee, bn[-1]) if ee else 0.0

    # Renumber BEFORE the count changes, so the rename pass only ever sees
    # joints that already exist; _grow_chain names what it creates itself.
    joints = _renumber_chain(joints, rigname, start_index, reserved)

    if n == old_n:
        # Hierarchy stays exactly as it is; only positions move. The _ee_
        # still has to follow the tip: a same-count re-space (say Keep ->
        # Uniform) moves the last joint, and an _ee_ left behind would give
        # Setup a bogus final aim direction.
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

    Two naming modes, chosen for the span as a whole by _index_targets: a
    conventional chain is rebuilt from the naming template, an
    unconventional one keeps its own names and has only its index token
    rewritten. Deciding per chain rather than per joint is what stops a span
    coming out half template-named and half artist-named; it costs nothing,
    because rewriting the index of an already-conventional name produces
    exactly the template name anyway.

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

    # UUIDs survive renames, and the path is re-resolved from one at every
    # step: renaming a joint invalidates the stored paths of everything
    # below it, so the list this started with goes stale immediately. The
    # _ee_ is tracked the same way even when its own name does not change -
    # it sits at the bottom of the chain, so EVERY rename above it moves it,
    # and looking a stale path up again by name is what the whole change is
    # here to stop.
    uuids = [(cmds.ls(j, uuid=True) or [None])[0] for j in bn_joints]
    originals = [rt_maya.leaf(j) for j in bn_joints]
    ee_uuid = (cmds.ls(ee, uuid=True) or [None])[0] if ee else None

    # Maya cannot swap names in-place, and the span's targets overlap its
    # current names whenever the run shifts by anything but zero. EVERY
    # joint goes through a unique temporary name first - not just the ones
    # whose name changes - so no target can collide with a name the pass has
    # not moved out of the way yet.
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
    carries `rigname`: a rename moves a conventional chain onto a NEW rig
    part name, and it is still the template that says what the joints are
    then called.
    """
    try:
        parsed = {rt_naming.get_rigname(j, rt_constants.JOINT)
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

    # Reparent the _ee_ to the new last joint BEFORE deleting the surplus —
    # its old parent is among them. cmds.parent returns a short name, so the
    # result is re-resolved: that name may well match a joint of another
    # chain, and _place_ee has to move THIS one.
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

    # What to call the joints about to be created. The span's naming mode is
    # settled once, here, rather than per joint: a chain whose names the
    # template cannot parse grows joints that continue ITS naming instead of
    # reverting to the template halfway down.
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


def _grown_name(bn, rigname, mode, start_index, index):
    '''
    What to call a joint the rebuild is about to create, at chain position
    `index`.

    Follows the span's own naming: the template when the existing joints
    parse as this rig part, otherwise the last existing joint's name with
    its index token rewritten. Without the second case a chain with artist
    names grows joints named from the template, so raising the count leaves
    it named two different ways down its length. A last joint with no index
    token to rewrite has no stem to follow, so the template is the only
    answer left.
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

    Created parented rather than created-then-parented, because the name
    cmds.createNode answers with is not safe to hand back to cmds.parent: a
    chain being grown to 50 joints creates 'BN_R_fintail_17_jnt' while the
    chain it is replacing still has one, and the very next call is given a
    name that now matches two nodes.

    The path is built from the name Maya actually used, which is not always
    the name asked for - a clash under one parent gets uniquified.
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

    cmds.parent answers with a short name, which is exactly the name that is
    not safe to keep hold of: the scene may well hold another joint called
    the same thing in the chain this one is replacing.
    '''
    moved = cmds.parent(node, parent)
    if not moved:
        return (cmds.ls(node, long=True) or [node])[0]
    parent_path = (cmds.ls(parent, long=True) or [parent])[0]
    return f'{parent_path}|{rt_maya.leaf(moved[0])}'


def _copy_joint_attrs(src, dst):
    '''Carry rotateOrder, preferredAngle and the display radius from the
    nearest surviving neighbour onto a newly created joint, so a grown chain
    stays uniform in the channels the rest of the pipeline reads.

    radius matters visually rather than mechanically: a fresh joint draws at
    Maya's default 1.0, which is why growing a chain used to fill it with
    joints far larger than the ones already there.  The chain-wide pass in
    rebuild settles the final value; this keeps the joint sane in between.
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

    `target` is the resample's own final point: the _ee_ marks the end of the
    tail's length, so it takes part in the resample and lands back exactly
    where the artist left it, whatever the joint count. Without one -- no
    _ee_ took part, because it sits on top of the tip -- fall back to the old
    rule: its original distance out along the new final segment, so it still
    hands Setup a sane final aim direction.
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

    rt_joint.get_joint_chain stops BEFORE the _ee_, so it never appears in the
    chain list and has to be looked up from the tip.  Setup's end-joint
    handling depends on it existing and being sensibly placed, so a rebuild
    that ignored it would leave it behind at the old tip.
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

        # Create joints. A new chain is numbered from 00 by definition, and
        # the joints are tracked by full DAG path from the moment they are
        # parented - cmds.createNode and cmds.parent both answer with short
        # names, which say nothing about WHICH node they mean once the scene
        # holds a second chain named the same way.
        joints = []
        parent = cmds.listRelatives(start, parent=True, fullPath=True) or None
        for i, pos in enumerate(positions):
            name = rt_naming.fstr(rigname, rt_constants.JOINT,
                                  rt_constants.TYPE_BN, i)
            j = _create_joint(name, joints[-1] if joints else
                              (parent[0] if parent else None))
            cmds.xform(j, ws=True, t=pos)
            joints.append(j)

        # Every joint is brand new, so there is no artist radius to keep and
        # Maya's default 1.0 has nothing to do with the chain's scale. Fit it
        # to the spacing instead.
        _apply_radius(joints, _fit_radius(_joint_radius(start), positions))

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
    # The _ee_ position MUST be read before the chain is re-oriented. It is a
    # child excluded from the chain, so nothing re-places it: it simply swings
    # with its parent, because its local translate is a fixed offset in the
    # parent's space. Re-aiming the tip therefore moves the _ee_ in world by
    # the same rotation, and reading its position afterwards bakes that swing
    # in — which is the _ee_ standing perpendicular to the chain it should
    # continue. _place_ee has already put it on the final segment; this pins
    # it there. Same rule as rt_setup._end_joint_position.
    ee_pos = cmds.xform(ee, q=True, ws=True, t=True) if ee else None
    for joint, frame, pos in zip(joints, frames, positions):
        _write_frame(joint, frame, pos)
    # The _ee_ is excluded from the chain, so it would keep a stale
    # orientation pointing a different way from everything above it. Give it
    # the last real joint's frame, as Setup does.
    if ee:
        _write_frame(ee, frames[-1], ee_pos)
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
            orient=False, start_joint=None):
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
    # same thing. A from-selected rebuild leaves the run above untouched, so
    # the span has to continue THAT run's numbering: taking the position
    # instead renumbers a span picked at '_05_jnt' of a chain numbered from
    # 01 as if it started at 04, colliding with the joint above it and
    # leaving two joints of that name in one chain. From the base the answer
    # is 0 by definition - that is what 'base joint starts at 00' means.
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
        # Keyed on the span's own first joint, so a from-the-base rebuild and
        # a from-partway one keep separate baselines instead of resampling
        # each other's positions. Already a full DAG path, which is what
        # makes the key tell two identically named chains apart.
        key = chain[0]
        current_positions = [cmds.xform(j, q=True, ws=True, t=True)
                             for j in chain if not rt_maya.is_end_joint(j)]
        # The _ee_ is where the tail actually ends, so it is the last point of
        # the resample rather than something dragged along behind the tip.
        # That pins it in world and spreads the BN joints over the WHOLE
        # length up to it: the gap between the last BN joint and the _ee_ is
        # one segment of the new count, so it narrows as the count rises
        # instead of the tail stopping ever further short of its own end.
        # Without an _ee_ the last BN joint is the end and is pinned instead.
        ee = _end_joint(chain[-1])
        ee_source = cmds.xform(ee, q=True, ws=True, t=True) if ee else None
        if ee_source and _length_vec(
                _sub(ee_source, current_positions[-1])) < rt_chain_spacing.EPS:
            # An _ee_ sitting on top of the tip adds no length and would give
            # the resample a zero-length final span. Leave it out and let
            # _place_ee follow the tip the old way.
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
            # Compare against what this tool last wrote.  Positions are plain
            # lists here, not nodes, so the distance is computed inline —
            # rt_math.get_vec_length takes node names.
            drift = max(
                _length_vec(_sub(current_positions[i], written[i]))
                for i in range(min(len(current_positions), len(written)))
            ) if written else float('inf')
            if (drift > rt_constants.JOINT_POS_TOLERANCE or
                    _ORIGINALS[key].get('has_ee') != bool(ee_source)):
                # Hand-edited, or the _ee_ came or went since the baseline was
                # taken — either way the cached source no longer describes the
                # length being re-spaced. Re-baseline.
                _ORIGINALS[key]['positions'] = [list(p) for p in current_positions]
                _ORIGINALS[key]['radius'] = _joint_radius(chain[0])
                _ORIGINALS[key]['has_ee'] = bool(ee_source)
            source = _ORIGINALS[key]['positions']
        else:
            _ORIGINALS[key] = {
                'positions': [list(p) for p in current_positions],
                'written': None,
                # Whether that baseline runs all the way out to an _ee_, so a
                # later rebuild can tell a stale source from a live one.
                'has_ee': bool(ee_source),
                # The radius as the artist left it, cached for the same
                # reason the positions are: every later rebuild fits its
                # radius to this, so raising the count and lowering it again
                # comes back to the size it started at.
                'radius': _joint_radius(chain[0]),
            }
            source = current_positions

        # Resample.  With an _ee_ in the source the chain needs one more point
        # than it has BN joints: the last one belongs to the _ee_.
        positions, snapped = rt_chain_spacing.resample(
            source, n + 1 if ee_source else n, mode,
            param=param, invert=invert, snap=snap)
        ee_pos = positions.pop() if ee_source else None

        # Write.  get_joint_chain stops before the _ee_, so append it here:
        # _write_chain re-parents it onto the new tip and re-places it.
        all_joints = list(chain)
        if ee:
            all_joints.append(ee)
        result = _write_chain(all_joints, positions, rigname, start_index,
                              ee_pos, reserved)

        # One radius for the whole span, sized to the spacing it ended up
        # with. Without this a grown chain mixes the artist's radius with
        # Maya's default 1.0 on the joints that were just created, which is
        # what makes a denser chain look like it grew fat rather than long.
        ee = _end_joint(result[-1])
        _apply_radius(result + ([ee] if ee else []),
                      _fit_radius(_ORIGINALS[key].get('radius'), positions))

        if orient:
            # The _ee_ was re-read above: a count change re-parents it, and a
            # shrink gave it a new parent entirely.
            _orient_chain(result, positions, ee, up_ref)

        # Update cache
        # A count change can rename the root.  Move the cache to its new
        # long DAG path so subsequent edits still use the original source.
        result_key = (cmds.ls(result[0], long=True) or [result[0]])[0]
        entry = _ORIGINALS.pop(key)
        # Written mirrors current_positions point for point, _ee_ included, so
        # the next rebuild's drift check notices a hand-moved _ee_ as readily
        # as a hand-moved joint.
        entry['written'] = ([list(p) for p in positions] +
                            ([list(ee_pos)] if ee_pos else []))
        _ORIGINALS[result_key] = entry

        # result[0] rather than chain[0]: the renumber may have renamed it.
        span = 'base' if not span_at else f'joint {rt_maya.leaf(result[0])}'
        logger.info(f'Rebuilt chain {rigname} from {span}: {len(chain)} -> '
                    f'{n} joints, numbered from {start_index}'
                    f'{", oriented" if orient else ""}.')
        return result


def rebuild_selected(n, mode='keep', param=None, invert=False, snap=True,
                     orient=False, from_selected=False):
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
                                   orient,
                                   spec.selected if from_selected else None))
    return results


def rename_chain(root_joint, rigname):
    """
    Rename ONE chain's joints onto a new rig part name, in place.

    Scoped to the chain given, which is what makes it usable while a second
    chain still answers to the old rig part name: rt_cleanup.rename_rigpart
    sweeps the whole scene by name token, so it would rename both chains and
    leave the collision exactly as it was. Here the chain is addressed by
    DAG path, so the one that was picked in the viewport is the one that
    moves.

    The point of it is to park a chain out of the roster without deleting
    it. A rig part name that RIGPARTS does not list is ignored by Setup and
    by the build, so the old tail can sit in the scene, skin and all, while
    its replacement takes over the name.

    Joints are renumbered from 00 as they are renamed, so the parked chain
    comes out conventional whatever it was before.

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
