"""
rig_tail_chain_build.py
author: Daisy Jane @gnitemouse

Maya I/O layer for the Tail Chain Builder.
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
    clear_cache: forget session original cache for one or all chains
"""

import os
import json
import math

import maya.cmds as cmds
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_joint as rt_jnt
import rig_tail_math as rt_math
import rig_tail_chain_spacing as rt_spc
import rig_tail_maya as rt_mya

logger = logger_setup(__name__)


# SESSION ORIGINAL CACHE ================================================
# Module global, dies on reload, no scene metadata.
# Keyed on the root joint's long DAG path. Each entry stores the
# original positions (the first time the chain was touched this session)
# and the last-written positions.

_ORIGINALS = {}

# Settings path — own file, never rt_cst.get_user_editable_config().
PREFS_FILE = os.path.join(os.path.dirname(__file__),
                          'rig_tail_chain_config.json')
DEFAULTS = {'mode': 'keep', 'power': 1.7, 'ratio': 0.90,
            'invert': False, 'snap': True, 'orient_on_create': False}


def _load_prefs():
    try:
        with open(PREFS_FILE) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return dict(DEFAULTS)


def _save_prefs(prefs):
    try:
        with open(PREFS_FILE, 'w') as f:
            json.dump(prefs, f, indent=2)
    except (IOError, OSError) as exc:
        logger.warning(f'Could not save chain builder prefs: {exc}')


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
    if len(joints) == 1:
        # Single joint: walk up to root, then rebuild
        root = _walk_to_root(joints[0])
        rigname = _guess_rigname(root)
        return [ChainSpec(root=root, rigname=rigname)]

    if len(joints) >= 2:
        # Check if they form a hierarchy
        ancestors = cmds.ls(joints[0], dag=True, type='joint') or []
        if joints[1] in ancestors:
            # Two joints in hierarchy: rebuild span
            root = _walk_to_root(joints[0])
            rigname = _guess_rigname(root)
            return [ChainSpec(root=root, start=joints[0],
                              end=joints[1], rigname=rigname)]

        # Many joints: resolve each to root, deduplicate
        roots = {}
        for j in joints:
            root = _walk_to_root(j)
            if root not in roots:
                rigname = _guess_rigname(root)
                roots[root] = ChainSpec(root=root, rigname=rigname)
        return list(roots.values())

    # Two unrelated transforms: build new
    if len(sel) >= 2:
        return [ChainSpec(start=sel[0], end=sel[1], is_new=True)]

    abort_build(logger, 'Could not interpret selection. Select two joints '
                       'in a chain, one joint, or two transforms.')


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
        rigname = rt_nam.get_rigname(joint, rt_cst.JOINT)
        if rigname:
            return rigname
    except Exception:
        pass
    # Fallback: strip trailing _## suffix (the index) and _BN / _jnt
    name = joint.split('|')[-1]
    parts = name.split('_')
    # Remove known trailing type tags
    while parts and parts[-1] in (rt_cst.JNT, rt_cst.TYPE_BN, rt_cst.TYPE_IK,
                                  rt_cst.TYPE_FK):
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
    '''Return the first skinCluster that lists this joint as an influence.'''
    history = cmds.listHistory(joint, type='skinCluster') or []
    for skin in history:
        influences = cmds.skinCluster(skin, q=True, inf=True) or []
        if any(joint.split('|')[-1] in (inf.split('|')[-1]) for inf in influences):
            return skin
    return None


def _guard_min_length(joints):
    '''Abort if the chain is too short or degenerate.'''
    if len(joints) < 2:
        abort_build(logger, 'Chain has fewer than 2 joints.')
    positions = [cmds.xform(j, q=True, ws=True, t=True) for j in joints]
    total = sum(
        rt_math.get_vec_length(joints[i], joints[i + 1])
        for i in range(len(joints) - 1))
    if total < rt_spc.EPS:
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
    old_n = len([j for j in joints if '_ee_' not in j])
    ee = _find_ee(joints)
    old_tip = [j for j in joints if '_ee_' not in j][-1]
    ee_distance = rt_math.get_vec_length(ee, old_tip) if ee else 0.0

    if n == old_n:
        for j, pos in zip(joints, positions):
            cmds.xform(j, ws=True, t=pos)
        return list(joints)

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
    targets = [rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, i)
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
        return all(rt_nam.get_rigname(j, rt_cst.JOINT) == rigname and
                   isinstance(rt_nam.get_index_from_name(j), int)
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

    # Reparent the _ee_ to the new last joint, then delete surplus
    ee = _find_ee(joints)
    if ee:
        cmds.parent(ee, keep[-1] if keep else joints[0])

    for j in delete:
        children = cmds.listRelatives(j, children=True, typ='joint') or []
        for child in children:
            cmds.parent(child, keep[-1] if keep else joints[0])
        cmds.delete(j)

    # Reposition _ee_
    if ee and len(positions) >= 2:
        last_dir = _norm_vec(_sub(positions[-1], positions[-2]))
        new_pos = _add(positions[-1], _scale(last_dir, ee_distance))
        cmds.xform(ee, ws=True, t=new_pos)

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
        new_name = rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, i)
        j = cmds.createNode('joint', name=new_name)
        cmds.parent(j, parent)
        cmds.xform(j, ws=True, t=positions[i])
        # Copy rotateOrder and preferredAngle from nearest neighbour
        src = parent if parent != j else result[-2] if len(result) >= 2 else None
        if src:
            ro = cmds.getAttr(f'{src}.rotateOrder')
            cmds.setAttr(f'{j}.rotateOrder', ro)
        result.append(j)

    # Reparent _ee_
    ee = _find_ee(joints)
    if ee and len(result) >= 1:
        cmds.parent(ee, result[-1])
        if len(positions) >= 2:
            last_dir = _norm_vec(_sub(positions[-1], positions[-2]))
            cmds.xform(ee, ws=True,
                       t=_add(positions[-1], _scale(last_dir, ee_distance)))

    return result


def _find_ee(joints):
    '''Return the _ee_ joint among the list, if any.'''
    for j in joints:
        if '_ee_' in j:
            return j
    return None


# MATH SHORTCUTS ========================================================

def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _scale(v, s):
    return [v[i] * s for i in range(3)]


def _norm_vec(v):
    d = math.sqrt(sum(x * x for x in v))
    return [x / d for x in v] if d > 1e-12 else v


# PUBLIC API ============================================================

def build_new(start, end, n, rigname, mode='uniform', param=None,
              invert=False):
    """
    Create a new BN chain between two transforms.

    Arguments:
        start (str): start transform name
        end (str): end transform name
        n (int): number of BN joints (n >= 2)
        rigname (str): rig part name for naming
        mode (str): spacing mode
        param (float): mode parameter
        invert (bool): invert distribution
    """
    if n < 2:
        abort_build(logger, 'Joint count must be at least 2.')

    start_pos = cmds.xform(start, q=True, ws=True, t=True)
    end_pos = cmds.xform(end, q=True, ws=True, t=True)

    with rt_mya.build_performance_scope(name='Tail Chain Build'):
        positions, _ = rt_spc.resample(
            [start_pos, end_pos], n, mode, param=param, invert=invert, snap=False)

        # Create joints
        joints = []
        parent = cmds.listRelatives(start, parent=True) or None
        for i, pos in enumerate(positions):
            j = cmds.createNode('joint', name=rt_nam.fstr(
                rigname, rt_cst.JOINT, rt_cst.TYPE_BN, i))
            if parent and i == 0:
                cmds.parent(j, parent)
            elif joints:
                cmds.parent(j, joints[-1])
            cmds.xform(j, ws=True, t=pos)
            joints.append(j)

        logger.info(f'Created new chain {rigname} with {n} joints.')
        return joints


def rebuild(root_joint, n, mode='keep', param=None, invert=False, snap=True):
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

    Return:
        list: BN joints after rebuild
    """
    if n < 2:
        abort_build(logger, 'Joint count must be at least 2.')

    # Resolve the full chain
    chain = rt_jnt.get_joint_chain(root_joint)
    _guard_chain(chain)
    _guard_min_length(chain)

    with rt_mya.build_performance_scope(name='Tail Chain Build'):
        key = (cmds.ls(root_joint, long=True) or [root_joint])[0]
        current_positions = [cmds.xform(j, q=True, ws=True, t=True)
                             for j in chain if '_ee_' not in j]
        rigname = _guess_rigname(root_joint)

        # Session original cache: resample from the first-seen positions
        # when the chain has not been hand-edited since the last write.
        if key in _ORIGINALS:
            written = _ORIGINALS[key]['written']
            drift = max(
                rt_math.get_vec_length(
                    [0, 0, 0],
                    _sub(current_positions[i], written[i]))
                for i in range(min(len(current_positions), len(written)))
            ) if written else float('inf')
            if drift > rt_cst.JOINT_POS_TOLERANCE:
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
        positions, snapped = rt_spc.resample(
            source, n, mode, param=param, invert=invert, snap=snap)

        # Write
        ee = _find_ee(chain)
        all_joints = list(chain)
        if ee:
            all_joints.append(ee)
        result = _write_chain(all_joints, positions, rigname)

        # Update cache
        # A count change can rename the root.  Move the cache to its new
        # long DAG path so subsequent edits still use the original source.
        result_key = (cmds.ls(result[0], long=True) or [result[0]])[0]
        entry = _ORIGINALS.pop(key)
        entry['written'] = [list(p) for p in positions]
        _ORIGINALS[result_key] = entry

        logger.info(f'Rebuilt chain {rigname}: {len(chain)} -> {n} joints.')
        return result


def rebuild_selected(n, mode='keep', param=None, invert=False, snap=True):
    """
    Re-space the chain(s) in the current selection.

    Arguments:
        n (int): target joint count
        mode (str): spacing mode
        param (float): mode parameter
        invert (bool): invert distribution
        snap (bool): enable snap-to-existing

    Return:
        list of list: BN joints per rebuilt chain
    """
    specs = resolve_selection()
    results = []
    for spec in specs:
        if spec.is_new:
            results.append(build_new(spec.start, spec.end, n,
                                     spec.rigname, mode, param, invert))
        else:
            results.append(rebuild(spec.root, n, mode, param, invert, snap))
    return results


def clear_cache(root=None):
    """
    Forget the session original cache for one or all chains.

    Arguments:
        root (str): root joint DAG path, or None for all
    """
    if root is None:
        _ORIGINALS.clear()
        logger.info('Cleared all chain original caches.')
    elif root in _ORIGINALS:
        del _ORIGINALS[root]
        logger.info(f'Cleared cache for {root}.')
