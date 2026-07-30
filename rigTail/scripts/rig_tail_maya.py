"""
rig_tail_maya.py
author: Daisy Jane @gnitemouse

Consolidated Maya wrappers and scene helpers for Rig Tail.

Functions:
    build_performance_scope: Suspend refresh/EM for the duration of a build
    timed: Time one step of the build in progress (see build_timer)
    build_timer: Time each build phase and report them in one line
    obj_exists: Check if object exists
    remove: Delete object safely
    remove_nodes: remove() for a list of nodes, in a handful of commands
    set_channel_flags: keyable/channel-box/lock flags via the API
    parent_to: Parent node to given parent
    is_parent: Check if node is already parent
    get_constraint: Get constraints on node
    get_geometry_from_scene: Collect mesh geometry from scene
    get_joints_from_scene: Collect top-level joints from scene
    get_controls_from_scene: Collect controls from scene
    is_control: Check if node is curve control
    is_geometry: Check if node is mesh
    disconnect_all: Disconnect all connections from node
    disconnect_nodes: disconnect_all for a list of nodes, in two commands
    break_connection: Break single plug connection
    opm: Move transforms to offsetParentMatrix
    reset_opm: Reset offsetParentMatrix to identity
    reset_transforms: Reset TRS to defaults
    match_transform: Match transforms between nodes
    set_visibility: Set visibility attribute
    set_transform_visibility: Set transform attribute visibility
    set_curve_visibility: Set curve visibility
    set_group_visibility: Set group visibility
    set_joint_channels: Set a joint's channels (non-)keyable, no locking
    finalize_joint_channels: Apply set_joint_channels to all rig joints
    set_joint_color: Colour a joint's wireframe via the drawing override
    color_skeletons: Colour BN/IK/FK skeletons by type
    create_group: Create transform group
    create_condition: Create condition node
    create_condition_multi: Create multi-output condition
    get_num_cv: Get curve CV count
    create_curveinfo: Create curveInfo node
    sdk: Create set driven key
    add_attribute_enum: Add enum attribute
    set_attr_value: Set an attribute unless it is locked or driven
    list_hierarchy: Iterative traversal helper
    has_non_default_locked_attributes: Check for locked attributes
    bind_geometry: Bind geometry to BN joints
    geometry_transforms: Mesh transforms under the geometry group
    find_geometry_for_rigname: Geometry matching a rig part by name
    report_missing_geometry: Warn about rig parts with no matching mesh
    unbind_geometry: Unbind geometry from rig
    unbind_geometry_all: Unbind all geometry in scene
    preserve_skin: Read the PRESERVE_SKIN setting
    find_skincluster: First skinCluster in a node's history
    skin_influence_indices: Influence -> sparse logical index map
    add_missing_influences: Add rig joints to a cluster at weight 0
    rebaseline_skin: Accept the joints' current pose as the skin's rest
    bind_skincluster: Create skinCluster binding
    unbind_skincluster: Unbind skinCluster from node
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
from contextlib import contextmanager
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import re
import time

logger = logger_setup(__name__)


# BUILD PERFORMANCE SCOPE ==============================================

# True while build_performance_scope() has the viewport refresh suspended.
# cmds.refresh has no query for the suspend state, so it is tracked here
# for force_refresh() to restore correctly.
_REFRESH_SUSPENDED = False

def _refresh(**kwargs):
    """
    cmds.refresh that tolerates batch/mayapy sessions, where refresh can
    raise instead of no-op.
    """
    try:
        cmds.refresh(**kwargs)
    except RuntimeError as err:
        logger.trace(f'refresh skipped: {err}')

@contextmanager
def build_performance_scope(name='rig_tail build'):
    """
    Context manager that makes a full build run fast:

    - Viewport refresh suspended: the build touches thousands of nodes
      and every one of them would otherwise trigger redraw work.
    - Evaluation manager set to DG mode for the duration: in serial or
      parallel mode each createNode/connectAttr invalidates the EM graph,
      which is rebuilt over and over while the rig is assembled. The
      previous mode is restored afterwards (one rebuild instead of many).
    - One undo chunk: the build undoes as a single step instead of
      flooding the undo queue with thousands of entries.

    Re-entrant: a scope opened inside another one is a no-op, so the
    entry points in rig_tail.py can each wrap their own pipeline.
    Everything is restored in a finally block, so an aborted build
    (abort_build raises) cannot leave the viewport suspended.
    """
    global _REFRESH_SUSPENDED
    if _REFRESH_SUSPENDED:
        yield
        return

    try:
        em_mode = (cmds.evaluationManager(q=True, mode=True) or ['off'])[0]
    except (RuntimeError, AttributeError):
        em_mode = 'off'
    cmds.undoInfo(openChunk=True, chunkName=name)
    _refresh(suspend=True)
    _REFRESH_SUSPENDED = True
    if em_mode != 'off':
        cmds.evaluationManager(mode='off')
    try:
        yield
    finally:
        if em_mode != 'off':
            cmds.evaluationManager(mode=em_mode)
        _REFRESH_SUSPENDED = False
        _refresh(suspend=False)
        cmds.undoInfo(closeChunk=True)
        _refresh(force=True)

# The build_timer of the run in progress, so any step anywhere in the build
# can time itself with timed() without every function in between having to
# take a timer argument. None outside a build; nested timers restore the
# outer one on exit.
_ACTIVE_TIMER = None


@contextmanager
def build_timer(name='build'):
    """
    Time each phase of a run and report them as one line at the end.

    Without this a slow build is a single opaque wait: the phases differ by
    an order of magnitude in cost and which one dominates depends entirely
    on the scene (part count, chain length, how much of the previous rig
    can be reused). Reported at INFO so it shows up in a normal run.

    Usage:
        with build_timer('rig_tail_multiple') as timer:
            with timer.phase('cleanup'):
                ...

    Steps inside a phase are timed with the module-level timed(), which
    finds this timer on its own and reports on a second line.

    Arguments:
        name (str): Label for the run, normally the entry point's name

    Yield:
        _PhaseTimer: Call .phase(label) around each phase
    """
    global _ACTIVE_TIMER
    timer = _PhaseTimer(name)
    previous = _ACTIVE_TIMER
    _ACTIVE_TIMER = timer
    try:
        yield timer
    finally:
        _ACTIVE_TIMER = previous
        # Report even on an aborted build: knowing which phase it died in,
        # and how long it had been running, is the point
        timer.report()


@contextmanager
def timed(label):
    """
    Time one step of the build in progress; a no-op outside a build_timer.

    The phase totals say which phase is slow, never which step inside it,
    and the steps that dominate are not the ones anyone predicts -- they are
    whichever ones repeat per rig part per joint. Wrapping the suspects
    turns the next build into the measurement:

        with rt_maya.timed('cleanup.unbind'):
            rt_maya.unbind_geometry(rigname)

    Re-entering a label accumulates, and the call count is reported with
    the total, so 'per part' costs are visible as such. Labels are free
    text; the convention is '<phase>.<step>'.

    Arguments:
        label (str): Step name, e.g. 'cleanup.unbind'
    """
    timer = _ACTIVE_TIMER
    if timer is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        total, count = timer.steps.get(label, (0.0, 0))
        timer.steps[label] = (total + elapsed, count + 1)


class _PhaseTimer:
    """Accumulates wall-clock time per phase label. See build_timer."""

    def __init__(self, name):
        self.name = name
        self.phases = []          # (label, seconds), in the order run
        self.steps = {}           # label -> (seconds, calls), from timed()
        self.start = time.perf_counter()

    @contextmanager
    def phase(self, label):
        """Time one phase. Re-entering a label adds to its total."""
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            for i, (existing, total) in enumerate(self.phases):
                if existing == label:
                    self.phases[i] = (label, total + elapsed)
                    break
            else:
                self.phases.append((label, elapsed))

    def report(self, top=12):
        """
        Log the summary: per-phase times and the total, then the slowest
        timed() steps (they overlap the phases, and each other where a step
        wraps another, so they are listed rather than summed).

        Arguments:
            top (int): How many steps to list, slowest first
        """
        total = time.perf_counter() - self.start
        if not self.phases:
            logger.info(f'{self.name}: {total:.1f}s')
        else:
            parts = ' | '.join(f'{label} {secs:.1f}s'
                               for label, secs in self.phases)
            logger.info(f'{self.name} timing: {parts} | total {total:.1f}s')
        if not self.steps:
            return
        ranked = sorted(self.steps.items(), key=lambda kv: -kv[1][0])[:top]
        steps = ' | '.join(f'{label} {secs:.1f}s x{count}'
                           for label, (secs, count) in ranked)
        logger.info(f'{self.name} steps: {steps}')


def force_refresh():
    """
    Force a full evaluation/redraw even while build_performance_scope has
    refresh suspended (temporarily resumes it), for the places that need
    the DG settled mid-build (e.g. match_fk_to_ik_rest reading the IK
    joints' final rest).
    """
    if _REFRESH_SUSPENDED:
        _refresh(suspend=False)
    try:
        _refresh(force=True)
    finally:
        if _REFRESH_SUSPENDED:
            _refresh(suspend=True)


# PLUGINS ==============================================================

def ensure_plugins(plugins=('matrixNodes',)):
    """
    Load plugins the rig build depends on. When a node type's plugin is
    unloaded (common in mayapy/batch sessions), cmds.createNode does
    not error -- it silently creates a useless 'unknown' placeholder
    node with no attributes -- so required plugins must be loaded
    before any nodes are created.

    Arguments:
        plugins (tuple): Plugin names to load
    """
    for plugin in plugins:
        if not cmds.pluginInfo(plugin, q=True, loaded=True):
            try:
                cmds.loadPlugin(plugin, quiet=True)
                logger.debug(f"Loaded required plugin '{plugin}'")
            except RuntimeError as err:
                logger.warning(f"Could not load plugin '{plugin}': {err}")


# OBJECT EXISTENCE AND DELETION ========================================

def obj_exists(node):
    """
    Check if Maya object exists.

    Arguments:
        node (str): Node name

    Return:
        bool: True if object exists
    """
    return cmds.objExists(node)


def remove(node):
    """
    Delete Maya object safely, handling connected nodes.

    Arguments:
        node (str): Node to delete
    """
    if cmds.objExists(node):
        consumers = curveinfo_consumers([node])
        if consumers:
            cmds.delete(consumers)
        if 'Constraint' not in cmds.objectType(node):
            disconnect_all(node)
        cmds.delete(node)


def curveinfo_consumers(nodes):
    """
    The curveInfo nodes fed by a list of nodes, INCLUDING through their
    shapes.

    A curve's connection to a curveInfo is 'curveShape.worldSpace[0] ->
    curveInfo.inputCurve', so it hangs off the SHAPE. Asking the transform
    for its connections never sees it, and deleting the transform therefore
    left a curveInfo with no input behind - which then prints
    'curveInfoNN (Curve Info): No valid NURBS curve' on every evaluation for
    the rest of the session. cmds.ikHandle creates one of these per spline
    build (on the temporary curve it makes and this rig throws away), so
    they accumulated one per rig part per build.

    Arguments:
        nodes (list): Nodes about to be deleted

    Return:
        list: curveInfo nodes that would be orphaned
    """
    nodes = [n for n in nodes if n]
    if not nodes:
        return []
    # listRelatives raises on a DG node ('not a DAG object'), and this is
    # called with lists of utility nodes, so ask which are DAG first
    dag = cmds.ls(nodes, dag=True) or []
    shapes = (cmds.listRelatives(dag, s=True, f=True) or []) if dag else []
    return list(dict.fromkeys(
        cmds.listConnections(nodes + shapes, s=False, d=True,
                             type='curveInfo') or []))


def remove_nodes(nodes):
    """
    remove() for a whole list of nodes, in a handful of commands.

    Same safety rule as remove() - disconnect everything before deleting, so
    a delete cannot cascade through a connection web (deleting a connected
    expression takes its loop network, its sibling FX expressions and their
    composeMatrix nodes with it) - but the disconnect is one pass for the
    whole list (see disconnect_nodes) and the delete is a single call.

    That matters because teardown deletes utility nodes by the thousand: the
    FK falloff and curve-info networks alone are hundreds of nodes per rig
    part, and per-node remove() spent ~8 commands on each of them. A command
    profile of a 12-part rebuild put delete at 18.8k calls / 6.3s.

    Constraints keep remove()'s exemption from the disconnect pass.

    Arguments:
        nodes (list): Nodes to delete

    Return:
        int: nodes deleted
    """
    nodes = existing(nodes)
    if not nodes:
        return 0

    # Downstream curveInfo nodes go too, as remove() does
    targets = list(dict.fromkeys(nodes + curveinfo_consumers(nodes)))
    constraints = set(cmds.ls(targets, type='constraint') or [])
    disconnect_nodes([n for n in targets if n not in constraints],
                     verified=True)
    # A delete can cascade (an expression takes its network with it), so
    # re-check before deleting - one command for the whole list
    targets = existing(targets)
    if targets:
        cmds.delete(targets)
    return len(targets)


def existing(nodes):
    """
    The nodes in a list that exist, in ONE command.

    cmds.ls resolves a whole list at once, where objExists asks per node.
    The teardown filters lists of hundreds of nodes, so the per-node version
    was the most-called command in a build (68k calls in a 12-part profile).

    Arguments:
        nodes (list): Node names, possibly with duplicates or None

    Return:
        list: the existing ones, deduped
    """
    nodes = [n for n in dict.fromkeys(nodes) if n]
    if not nodes:
        # cmds.ls with no arguments returns the whole scene, and this feeds
        # cmds.delete
        return []
    return cmds.ls(nodes) or []


# PARENT OPERATIONS ====================================================

def parent_to(node, parent, a=False, r=False):
    """
    Parent node to given parent. Check first if already parent.

    Arguments:
        node (str): Node to parent
        parent (str): Parent node
        a (bool): Absolute mode
        r (bool): Relative mode
    """
    if not is_parent(node, parent):
        if a:
            cmds.parent(node, parent, a=1)
        elif r:
            cmds.parent(node, parent, r=1)
        else:
            cmds.parent(node, parent)


def is_parent(node, parent):
    """
    Check if node is already a child of parent.

    Arguments:
        node (str): Child node
        parent (str): Parent node

    Return:
        bool: True if already parented
    """
    node_parent = cmds.listRelatives(node, p=True, typ='transform') or []
    if parent in node_parent:
        return True
    return False


# SCENE QUERIES ========================================================

def get_constraint(node, typ=None):
    """
    Get constraints on a node.

    Arguments:
        node (str): Node to query
        typ (str): Specific constraint type or None for any

    Return:
        list: List of constraint names
    """
    constraint_types = ['parentConstraint', 'pointConstraint', 'orientConstraint',
                        'scaleConstraint', 'aimConstraint']
    if typ:
        if typ in constraint_types:
            return cmds.listRelatives(node, typ=typ) or []
        else:
            logger.error(f"typ '{typ}' must be a valid constraint type.")
    else:
        return cmds.listRelatives(node, typ='constraint') or []


def get_geometry_from_scene():
    """
    Collect ungrouped geometry from scene (top-level mesh transforms).

    Return:
        list: List of mesh transform names
    """
    geometry = []
    objects = cmds.ls(assemblies=True)
    for obj in objects:
        if is_geometry(obj):
            geometry.append(obj)
    return geometry


def get_joints_from_scene():
    """
    Collect ungrouped joints from scene (top-level joints).

    Return:
        list: List of joint names
    """
    joints = []
    objects = cmds.ls(assemblies=True)
    for obj in objects:
        if cmds.objectType(obj, i='joint'):
            joints.append(obj)
    return joints


def get_controls_from_scene():
    """
    Collect ungrouped controls from scene (top-level curves not used by skinCluster).

    Return:
        list: List of control names
    """
    controls = []
    objects = cmds.ls(assemblies=True)
    for obj in objects:
        if is_control(obj):
            controls.append(obj)
    return controls


def is_control(node):
    """
    Check if node is a control (nurbsCurve not used by skinCluster).

    Arguments:
        node (str): Node to check

    Return:
        bool: True if node is curve control
    """
    if cmds.objectType(node, i='nurbsCurve'):
        if not cmds.listConnections(node, d=False, t='skinCluster'):
            return True
    elif cmds.objectType(node, i='transform'):
        # Full paths: short shape names are ambiguous when the scene
        # contains duplicate node names
        shapes = cmds.listRelatives(node, s=True, f=True) or []
        for shp in shapes:
            if cmds.objectType(shp, i='nurbsCurve'):
                if not cmds.listConnections(shp, d=False, t='skinCluster'):
                    return True
    return False


def is_geometry(node):
    """
    Check if node is or contains mesh geometry.

    Arguments:
        node (str): Node to check

    Return:
        bool: True if node is mesh or transform with mesh shape
    """
    if cmds.objectType(node, i='mesh'):
        return True
    elif cmds.objectType(node, i='transform'):
        # Full paths: short shape names are ambiguous when the scene
        # contains duplicate node names ('rivetsShape' under two
        # different 'rivets' transforms)
        shapes = cmds.listRelatives(node, s=True, f=True) or []
        for shp in shapes:
            if cmds.objectType(shp, i='mesh'):
                return True
    return False


def list_hierarchy(root, end=None, predicate=None):
    """
    Iterative traversal of transform hierarchy.

    Arguments:
        root (str): Starting transform
        end (str): Optional stop node
        predicate (callable): Optional filter function

    Return:
        list: List of transforms matching criteria
    """
    result = []
    stack = [root]
    
    while stack:
        node = stack.pop()
        if node == end:
            break
        
        if predicate is None or predicate(node):
            result.append(node)
        
        children = cmds.listRelatives(node, typ='transform') or []
        stack.extend(reversed(children))
    
    return result


# CONNECTION OPERATIONS ================================================

def disconnect_all(node, source=True, destination=True, attrs=None):
    """
    Disconnect all connections from/to a node.

    Arguments:
        node (str): Node to disconnect
        source (bool): Disconnect incoming connections
        destination (bool): Disconnect outgoing connections
        attrs (list): Only disconnect specific attributes
    """
    if not cmds.objExists(node):
        return

    def disconnect(src, dst):
        # One undisconnectable pair (locked plug, connection into an
        # unknown node such as Node Editor bookkeeping) must not abort
        # the whole cleanup
        try:
            cmds.disconnectAttr(src, dst)
        except RuntimeError as err:
            logger.trace(f"Skip disconnect '{src}' -> '{dst}': {err}")

    if source:
        if attrs:
            for attr in attrs:
                conns = cmds.listConnections(f'{node}.{attr}', s=True, d=False, p=True, c=True) or []
                for i in range(0, len(conns), 2):
                    disconnect(conns[i + 1], conns[i])
        else:
            conns = cmds.listConnections(node, s=True, d=False, p=True, c=True) or []
            for i in range(0, len(conns), 2):
                disconnect(conns[i + 1], conns[i])

    if destination:
        if attrs:
            for attr in attrs:
                conns = cmds.listConnections(f'{node}.{attr}', s=False, d=True, p=True, c=True) or []
                for i in range(0, len(conns), 2):
                    disconnect(conns[i], conns[i + 1])
        else:
            conns = cmds.listConnections(node, s=False, d=True, p=True, c=True) or []
            for i in range(0, len(conns), 2):
                disconnect(conns[i], conns[i + 1])


def disconnect_nodes(nodes, source=True, destination=True, verified=False):
    """
    disconnect_all for a whole list of nodes, in two commands.

    cmds.listConnections takes a list of nodes and answers for all of them
    at once, so a chain of joints costs two queries instead of two per
    joint. Teardown calls this for every joint of every chain of every rig
    part, which is where the command count adds up.

    Arguments:
        nodes (list): Nodes to disconnect
        source (bool): Disconnect incoming connections
        destination (bool): Disconnect outgoing connections
        verified (bool): The caller already dropped missing nodes

    Return:
        int: connections broken
    """
    nodes = list(nodes) if verified else existing(nodes)
    if not nodes:
        return 0

    broken = 0
    def disconnect(src, dst):
        # One undisconnectable pair must not abort the cleanup; same rule
        # as disconnect_all
        nonlocal broken
        try:
            cmds.disconnectAttr(src, dst)
            broken += 1
        except RuntimeError as err:
            logger.trace(f"Skip disconnect '{src}' -> '{dst}': {err}")

    if source:
        conns = cmds.listConnections(nodes, s=True, d=False, p=True, c=True) or []
        for i in range(0, len(conns), 2):
            disconnect(conns[i + 1], conns[i])
    if destination:
        conns = cmds.listConnections(nodes, s=False, d=True, p=True, c=True) or []
        for i in range(0, len(conns), 2):
            disconnect(conns[i], conns[i + 1])
    return broken


def ensure_connect(src, dst):
    """
    Connect src -> dst plug unless already connected (rebuild-safe).
    Skips unitConversion nodes when comparing existing sources.

    Arguments:
        src (str): Source plug (node.attribute)
        dst (str): Destination plug (node.attribute)
    """
    existing = cmds.listConnections(dst, s=True, d=False, p=True, scn=True) or []
    if src in existing:
        return
    cmds.connectAttr(src, dst, f=1)


def break_connection(plug):
    """
    Break plug connection.

    Arguments:
        plug (str): Attribute plug to disconnect
    """
    # The unlock goes through the API: this is called per plug from
    # reset_opm and reset_transforms, and on a plug that is not locked (the
    # usual case) the setAttr was a command spent to change nothing
    node, _, attr = plug.partition('.')
    set_channel_flags(node, [attr], l=False, compound=True)
    if not cmds.connectionInfo(plug, id=True):
        return
    plug = cmds.connectionInfo(plug, ged=True)

    # -icn takes the driver node down with the connection (an animCurve, a
    # conversion node), which is what keeps rebuilds from accumulating
    # orphans. It cannot touch a read-only destination though - a plug on a
    # referenced node - and the cmds.ls(-ro) that used to test for that
    # cost 0.8ms a call, thousands of times a build (7.9% of a profiled
    # build was in ls). So attempt the delete and check the result: a plug
    # still connected afterwards gets a plain disconnect, which needs no
    # up-front test and costs two cheap connectionInfo queries.
    try:
        cmds.delete(plug, icn=True)
    except RuntimeError as err:
        logger.trace(f"delete -icn on '{plug}' refused: {err}")
    if cmds.connectionInfo(plug, id=True):
        source = cmds.connectionInfo(plug, sfd=True)
        if source:
            cmds.disconnectAttr(source, plug)


# TRANSFORM OPERATIONS =================================================

IDENTITY_MATRIX = om.MMatrix()


def opm(node):
    """
    Move transform values to Offset Parent Matrix.
    Node must be transform or joint type and have attributes unlocked.

    A node whose local matrix is ALREADY identity has nothing to bake:
    identity * opm == opm, so the write would set the value it already
    holds, and every channel is already at its default so there is nothing
    to reset either. That case is not an edge case - it is most of the
    build. create_sdk_groups matches and bakes every SDK group the moment
    it creates it (NUM_CTRL_FK + 1 per joint), and a freshly created group
    is at identity. The full path costs ~35 Maya commands (a locked-attr
    scan, the bake, then a reset of twelve channels); the check costs
    three, and a command profile of a 12-part build put this function's
    step at 13.7s of a 51s build.

    The skip is only taken when nothing is locked and nothing drives the
    node, because clearing those is the other half of what reset_transforms
    does and a caller may be relying on it (falloff_rotation connects to an
    SDK group's rotate, which fails on a plug left locked).

    Arguments:
        node (str): Node to bake transforms
    """
    local_matrix = om.MMatrix(cmds.xform(node, q=1, m=1, os=1))
    if local_matrix.isEquivalent(IDENTITY_MATRIX):
        # Nothing to bake, and no locked plug can be off its default either:
        # an identity local matrix IS every channel at its default, which is
        # the whole of what has_non_default_locked_attributes tests for.
        # reset_transforms still runs - it is what clears locks and incoming
        # connections - and takes its own fast path when there is nothing
        # left to clear.
        logger.trace(f"'{node}' is already at identity, nothing to bake")
        reset_transforms(node, local_matrix=local_matrix)
        return

    locked = cmds.listAttr(node, locked=True) or []
    if has_non_default_locked_attributes(node, locked=locked):
        abort_build(logger, f'Node {node} has at least one non default locked attribute(s)')

    offset_parent_matrix = om.MMatrix(cmds.getAttr(f"{node}.offsetParentMatrix"))
    baked_matrix = local_matrix * offset_parent_matrix
    cmds.setAttr(f"{node}.offsetParentMatrix", baked_matrix, typ='matrix')
    reset_transforms(node, local_matrix=local_matrix, locked=locked)


def reset_opm(node, unlock=True):
    """
    Reset offset parent matrix to identity.

    Arguments:
        node (str): Node to reset
        unlock (bool): If True, unlock and break connections
    """
    identity_mtx = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    opm_attr = f"{node}.offsetParentMatrix"
    if unlock:
        break_connection(opm_attr)
    if not cmds.getAttr(opm_attr, lock=True):
        cmds.setAttr(opm_attr, *identity_mtx, type='matrix')


def reset_transforms(node, unlock=True, local_matrix=None, locked=None):
    """
    Reset translate, rotate, scale, shear, jointOrient to defaults.

    Arguments:
        node (str): Node to reset
        unlock (bool): If True, unlock and break connections
        local_matrix (MMatrix): The node's local matrix if the caller
            already read it, so opm() does not pay for it twice
        locked (list): The node's locked attributes if already queried
    """
    # Node-level lock/connection queries once, instead of an existence
    # check, unlock and connection lookup per plug (this runs inside
    # opm(), which the build calls constantly). translate/rotate/scale
    # exist on every transform, jointOrient only on joints. Shear is not
    # reset: its children are shearXY/XZ/YZ, so the old per-axis
    # existence check never matched it anyway.
    conns = cmds.listConnections(node, s=True, d=False, p=True, c=True) or []
    connected = {conns[i].split('.', 1)[-1] for i in range(0, len(conns), 2)}
    if locked is None:
        locked = cmds.listAttr(node, locked=True) or []
    locked = set(locked)

    # Nothing driven and already at identity: every channel holds its
    # default, so the twelve value writes below would all be no-ops. Only
    # the unlock is still owed - callers rely on it (falloff_rotation
    # connects to an SDK group's rotate, which fails on a locked plug) - and
    # that is an API write, not a command. See opm(): this is the common
    # case, because a freshly created group is at identity and create_group
    # locks its channels.
    if not connected:
        if local_matrix is None:
            local_matrix = om.MMatrix(cmds.xform(node, q=1, m=1, os=1))
        if local_matrix.isEquivalent(IDENTITY_MATRIX):
            if unlock and locked:
                # jointOrient is passed unconditionally: a node that does
                # not have it costs nothing to skip (see set_channel_flags),
                # which saves asking whether this is a joint
                set_channel_flags(node, ['translate', 'rotate', 'scale',
                                         'jointOrient'],
                                  l=False, compound=True)
            return

    attributes = ['translate', 'rotate', 'scale']
    if cmds.objectType(node, i='joint'):
        attributes.append('jointOrient')

    # One API pass unlocks every channel and its compound parent, replacing
    # a setAttr(l=0) per plug and the getAttr that had to follow it to catch
    # a plug still locked through its compound. A plug that stays locked
    # anyway (referenced node) now shows up as the setAttr below failing,
    # which is handled the same way: skip it.
    if unlock and locked:
        set_channel_flags(node, attributes, l=False, compound=True)

    for attribute in attributes:
        default_value = 1 if attribute == "scale" else 0
        for axis in 'XYZ':
            attr = f"{attribute}{axis}"
            plug = f"{node}.{attr}"
            # A lock or connection on the compound parent also blocks the
            # child plug: a constraint drives '.translate', which reports as
            # 'translate' here, never 'translateX'. Both names must be
            # checked or the plug looks free and setAttr raises
            # "locked or connected".
            maybe_locked = attr in locked or attribute in locked
            maybe_connected = attr in connected or attribute in connected
            if unlock:
                if maybe_connected:
                    break_connection(plug)
                try:
                    cmds.setAttr(plug, default_value)
                except RuntimeError as err:
                    logger.trace(f"Could not reset '{plug}': {err}")
            elif not maybe_locked and not maybe_connected:
                # Not unlocking, so a driven plug is left as it is rather
                # than raising.
                cmds.setAttr(plug, default_value)


def match_transform(source, target, pos=False, rot=False, scl=False, moc=False, unlock=True):
    """
    Match transforms from source to target.

    Arguments:
        source (str): Source node to modify
        target (str): Target node to match
        pos (bool): Match position
        rot (bool): Match rotation
        scl (bool): Match scale
        moc (bool): Maintain offset for children
        unlock (bool): Unlock attributes before matching
    """
    def apply_transform(source, target, pos, rot, scl):
        if not (pos or rot or scl):
            cmds.matchTransform(source, target)
        else:
            cmds.matchTransform(source, target, pos=pos, rot=rot, scl=scl)

    logger.trace(f"'{source}'->'{target}'")
    if unlock:
        disconnect_all(source, source=True)
    if moc:
        # Children are tracked by UUID and their path re-read before each
        # use. No name survives this stretch reliably: reparenting renames
        # on a name clash and the ungroup below moves the node again, so a
        # stored name can come back pointing at a different node, or none
        src_children = cmds.listRelatives(source, typ='transform', f=True) or []
        if not src_children:
            # Nothing to hold in place, so the maintain-offset dance (temp
            # group, re-parent every child out and back, ungroup) is pure
            # overhead. The build hits this constantly: create_sdk_groups
            # matches every SDK group the moment it is created, while it
            # is still empty.
            apply_transform(source, target, pos, rot, scl)
            opm(source)
            return
        child_uids = cmds.ls(src_children, uuid=True)
        # createNode, not cmds.group(em=True): the same empty transform at
        # the world origin for a sixth of the cost (0.13ms against 0.76ms),
        # and this runs per node in the maintain-offset path
        tmp_grp = cmds.createNode('transform', n=f"{source}_tmp", ss=1)
        apply_transform(tmp_grp, target, pos, rot, scl)

        for uid in child_uids:
            child = cmds.ls(uid, long=True)[0]
            if unlock and 'Constraint' not in child.split('|')[-1]:
                disconnect_all(child, source=True)
            cmds.parent(child, tmp_grp, a=1)

        apply_transform(tmp_grp, target, pos, rot, scl)
        opm(source)

        for uid in child_uids:
            cmds.parent(cmds.ls(uid, long=True)[0], source, a=1)
            child = cmds.ls(uid, long=True)[0]
            if cmds.objectType(child, i='joint'):
                transf = cmds.listRelatives(child, p=True, typ='transform', f=True)[0]
                if 'transform' in transf:
                    cmds.ungroup(transf)
                    child = cmds.ls(uid, long=True)[0]
            opm(child)
        cmds.delete(tmp_grp)
    else:
        apply_transform(source, target, pos, rot, scl)
        opm(source)


def has_non_default_locked_attributes(node, attrcheck=None, locked=None):
    """
    Check whether node has locked non-default attributes.

    Arguments:
        node (str): Node to check
        attrcheck (list): Specific attributes to check
        locked (list): The node's locked attributes if the caller already
            queried them (opm does), to save the repeat query

    Return:
        bool: True if locked non-default attributes exist
    """
    attrvalid = ['translate', 'rotate', 'scale', 'shear', 'jointOrient']
    if not attrcheck:
        attrcheck = attrvalid
    else:
        for attribute in attrcheck:
            if attribute not in attrvalid:
                logger.error(f"Attribute invalid '{attribute}'")

    # Only a locked plug can make this True, so query the locked
    # attributes once instead of value+lock reads on every plug. This
    # runs inside opm(), which the build calls for every SDK group and
    # control, so the per-plug version dominated build time.
    if locked is None:
        locked = cmds.listAttr(node, locked=True) or []
    if not locked:
        return False
    defaults = {f'{attribute}{axis}': (1 if attribute == 'scale' else 0)
                for attribute in attrcheck for axis in 'XYZ'}
    for attr in locked:
        default_value = defaults.get(attr)
        if default_value is None:
            continue
        if cmds.getAttr(f'{node}.{attr}') != default_value:
            return True
    return False


# VISIBILITY ===========================================================

# Channel flags (keyable / channel-box / lock) are the one part of the
# build that is pure per-plug bookkeeping: nine plugs per group, on every
# group the build creates, and the FK SDK stack alone is NUM_CTRL_FK + 1
# groups per joint. A command profile of a 12-part build put 75k setAttr
# calls at 6.9s of a 51s build, and a fifth of those were flag writes.
#
# The API sets a flag directly on the plug with no command engine in the
# way, which is roughly two orders of magnitude cheaper. The trade is that
# API writes are NOT undoable: undoing a build restores the nodes but
# leaves these display flags where the build put them. That is acceptable
# for keyable/channel-box/lock (cosmetic, and reset by the next build) and
# is why VALUES still go through cmds.setAttr, which is undoable.
_API_FLAGS_AVAILABLE = True


def set_channel_flags(node, attrs, k=None, cb=None, l=None,
                      compound=False):
    """
    Set keyable / channel-box / lock flags on a node's plugs via the API.

    A compound name ('translate') flags its CHILDREN - translateX/Y/Z -
    which is exactly what the per-axis cmds.setAttr loops this replaces
    did, so the resulting channel box is unchanged. The compound itself is
    only touched with compound=True, used when unlocking: a lock on the
    compound blocks its children too, and the old per-axis unlock could not
    clear it (it queried the lock afterwards and gave up).

    Keyable is written before channel-box on purpose: Maya treats a keyable
    plug as being in the channel box regardless, so the order decides the
    final state of a plug set non-keyable and channel-box visible (the
    'shown but not settable' state the rig uses on joints and groups).

    Falls back to cmds.setAttr for the whole call if the API path fails, so
    a plug this does not understand is still set (just slower).

    Arguments:
        node (str): Node whose plugs to flag
        attrs (list): Attribute names, compound or leaf
        k (bool|int|None): Keyable, None to leave alone
        cb (bool|int|None): Show in channel box, None to leave alone
        l (bool|int|None): Locked, None to leave alone
        compound (bool): Also flag the compound plug itself, not only its
            per-axis children

    Return:
        bool: True when the flags were applied
    """
    global _API_FLAGS_AVAILABLE
    if _API_FLAGS_AVAILABLE:
        try:
            sel = om.MSelectionList()
            sel.add(node)
            fn = om.MFnDependencyNode(sel.getDependNode(0))
            plugs = []
            for attr in attrs:
                try:
                    plug = fn.findPlug(attr, False)
                except RuntimeError:
                    # Attribute this node does not have ('radius' on a
                    # plain transform): the same skip the callers used to
                    # pay an attributeQuery for
                    continue
                if plug.isCompound:
                    if compound:
                        plugs.append(plug)
                    plugs.extend(plug.child(i)
                                 for i in range(plug.numChildren()))
                else:
                    plugs.append(plug)
            for plug in plugs:
                # Unlock first: a locked plug rejects nothing here, but
                # leaving the lock for last matches the cmds call order
                if l is not None and not l:
                    plug.isLocked = False
                if k is not None:
                    plug.isKeyable = bool(k)
                if cb is not None:
                    plug.isChannelBox = bool(cb)
                if l:
                    plug.isLocked = True
            return True
        except (AttributeError, TypeError) as err:
            # The API itself is not behaving as expected (a Maya version
            # without one of these properties): stop trying it altogether
            logger.debug(f"API channel flags unavailable ({err}); "
                         f'falling back to setAttr for the session')
            _API_FLAGS_AVAILABLE = False
        except Exception as err:
            # This NODE could not be resolved - most often an ambiguous
            # short name in a scene with duplicates. Fall back for this
            # call only: disabling the API path here would quietly slow
            # every remaining flag write in the session.
            logger.trace(f"API channel flags on '{node}' failed ({err}); "
                         f'using setAttr')

    flags = {}
    if k is not None:
        flags['k'] = int(bool(k))
    if cb is not None:
        flags['cb'] = int(bool(cb))
    if l is not None:
        flags['l'] = int(bool(l))
    for attr in attrs:
        if not cmds.attributeQuery(attr, n=node, ex=1):
            continue
        for plug in plug_and_children(node, attr, compound=compound):
            try:
                cmds.setAttr(plug, **flags)
            except RuntimeError as err:
                logger.trace(f"Skip flags on '{plug}': {err}")
    return True


def plug_and_children(node, attr, compound=False):
    """
    A plug's per-axis children (and the plug itself when compound), for the
    cmds fallback path of set_channel_flags.

    Arguments:
        node (str): Node name
        attr (str): Attribute name
        compound (bool): Include the compound plug itself

    Return:
        list: plug strings
    """
    if attr in ('translate', 'rotate', 'scale', 'jointOrient', 'shear'):
        plugs = [f'{node}.{attr}{axis}' for axis in 'XYZ']
        if compound:
            plugs.insert(0, f'{node}.{attr}')
        return plugs
    return [f'{node}.{attr}']


def set_visibility(node, value, k=1, cb=1, l=0):
    """
    Set Visibility on/off and show/hide or lock attribute.

    Arguments:
        node (str): Node to set visibility
        value (int): Visibility value (0 or 1)
        k (int): Keyable flag
        cb (int): Channel box flag
        l (int): Lock flag
    """
    if not cmds.objExists(node):
        logger.error(f"'{node}' does not exist.")
        return
    # No attributeQuery for 'visibility': every DAG node has it, and this
    # runs on every group, control and joint the build touches. The value
    # is a real scene change so it goes through cmds (undoable); the flags
    # go through the API (see set_channel_flags).
    set_channel_flags(node, ['visibility'], l=False)
    cmds.setAttr(f"{node}.visibility", value)
    set_channel_flags(node, ['visibility'], k=k, cb=cb, l=l)


def set_transform_visibility(node, k=1, cb=1, l=0):
    """
    Set translate, rotate show/hide or lock attribute.

    Arguments:
        node (str): Node to modify
        k (int): Keyable flag
        cb (int): Channel box flag
        l (int): Lock flag
    """
    # translate/rotate exist on every transform-derived node, so no
    # per-axis attributeQuery (same reasoning as set_joint_channels)
    set_channel_flags(node, ['translate', 'rotate'], k=k, cb=cb, l=l)


def set_curve_visibility(curve, visibility=1):
    """
    Set curve visibility nonkeyable, attributes nonkeyable.

    Arguments:
        curve (str): Curve transform
        visibility (int): Visibility value
    """
    set_channel_flags(curve, ['translate', 'rotate', 'scale'],
                      k=False, cb=False, l=True)
    set_visibility(curve, visibility, k=1, cb=0, l=0)


def set_group_visibility(group, visibility=1):
    """
    Set group visibility nonkeyable, hide attributes.

    Arguments:
        group (str): Group transform
        visibility (int): Visibility value
    """
    # create_group calls this for every group the build makes, and the FK
    # SDK stack alone is (NUM_CTRL_FK + 1) groups per joint - twelve plug
    # flags each, which is why they go through the API
    set_channel_flags(group, ['translate', 'rotate', 'scale'],
                      k=False, cb=False, l=True)
    set_visibility(group, visibility, k=0, cb=1, l=0)


def set_joint_channels(joint, keyable, visibility=None):
    """
    Set the keyable state of a joint's channels (translate, rotate, scale,
    radius) WITHOUT locking them, so rig-driven connections (offsetParent-
    Matrix, constraints, SDKs) stay intact -- locking would break an
    incoming connection, but toggling the keyable flag does not.

    keyable=False leaves the channels shown in the channel box (cb=1) but
    non-keyable, so they can be read yet not accidentally keyed in
    animation. keyable=True restores them to keyable.

    Arguments:
        joint (str): Joint transform.
        keyable (bool): Keyable state to apply.
        visibility (int|None): When not None, also set the joint's
            visibility to this value (0/1); its keyable flag follows
            `keyable`.
    """
    if not cmds.objExists(joint):
        logger.error(f"'{joint}' does not exist.")
        return
    k = 1 if keyable else 0
    cb = 0 if keyable else 1
    # translate/rotate/scale exist on every transform-derived node, and so
    # does radius on a joint, so no per-axis or per-attribute
    # attributeQuery. This runs on every rig joint at the end of every
    # build - ~700 joints on a 12-part roster, ten plug flags each - so the
    # flags go through the API (see set_channel_flags).
    set_channel_flags(joint, ['translate', 'rotate', 'scale', 'radius'],
                      k=k, cb=cb)
    if visibility is not None:
        set_visibility(joint, visibility, k=k, cb=1, l=0)


def finalize_joint_channels(keyable, visibility=None, joint_dicts=None):
    """
    Apply set_joint_channels across cached rig joints.

    The build calls this with keyable=False (all four caches) so the
    deformation/rig joints are non-keyable and cannot be accidentally keyed
    in animation; the Setup phase calls it with keyable=True, visibility=1
    and joint_dicts=[JOINTS_BN] so the raw skeleton stays fully keyable and
    visible while it is being prepared, without touching a prior build's
    IK/FK joints.

    Arguments:
        keyable (bool): Keyable state to apply to every joint.
        visibility (int|None): When not None, force every joint's
            visibility to this value.
        joint_dicts (list|None): Joint-cache dicts to process; defaults to
            all four (BN, IK, FK, FX).

    Return
        int: number of joints processed.
    """
    if joint_dicts is None:
        joint_dicts = [rt_constants.JOINTS_BN, rt_constants.JOINTS_IK,
                       rt_constants.JOINTS_FK, rt_constants.JOINTS_FX]
    count = 0
    for jdict in joint_dicts:
        for joints in jdict.values():
            for jnt in joints:
                if cmds.objExists(jnt):
                    set_joint_channels(jnt, keyable, visibility)
                    count += 1
    state = 'keyable' if keyable else 'non-keyable'
    vis = f', visibility={visibility}' if visibility is not None else ''
    logger.debug(f'Set {count} joints {state}{vis}')
    return count


def set_joint_color(joint, color):
    """
    Colour a joint's viewport wireframe via the drawing override.

    Uses overrideColor (the index / "Wireframe - Index" colour), the same
    mechanism controls use -- NOT the outliner colour, which only tints the
    outliner text and does nothing in the viewport. A joint draws from its
    own transform (no separate shape), so the override goes on the joint
    itself. Locked or connected override plugs are skipped rather than
    erroring.

    Arguments:
        joint (str): Joint node.
        color (str|int): COLOR_OVERRIDE name, or a raw override index.
    """
    if not cmds.objExists(joint):
        return
    index = rt_constants.COLOR_OVERRIDE.get(color, color) if isinstance(color, str) \
        else color
    # The drawing-override plugs exist on every DAG node, so there is
    # nothing to check for but settability - and asking costs as much as
    # setting, so set and skip the ones that refuse. colour_skeletons calls
    # this for every rig joint of every part at the end of every build, so
    # the getAttr it used to pay per plug was a third of the pass.
    for plug, value in (('overrideEnabled', 1),
                        ('overrideRGBColors', 0),
                        ('overrideColor', index)):
        try:
            cmds.setAttr(f'{joint}.{plug}', value)
        except RuntimeError as err:
            # Locked, or driven by a display layer / referenced override
            logger.trace(f"Skip '{joint}.{plug}': {err}")


def color_skeletons(bn_color=None, ik_color=None, fk_color=None):
    """
    Colour each cached rig joint by its chain type (BN / IK / FK; FX
    follows the IK colour), so the three skeletons read apart at a glance.

    Colours default to the rt_constants.*_COLOR settings; getattr fallbacks keep
    it working in a session started before those constants existed
    (rig_tail_constants is never reloaded).

    Arguments:
        bn_color/ik_color/fk_color (str|int|None): override the defaults.

    Return
        int: number of joints coloured.
    """
    bn = bn_color or getattr(rt_constants, 'BN_COLOR', 'blue')
    ik = ik_color or getattr(rt_constants, 'IK_COLOR', 'orange')
    fk = fk_color or getattr(rt_constants, 'FK_COLOR', 'purple')
    mapping = [(rt_constants.JOINTS_BN, bn), (rt_constants.JOINTS_IK, ik),
               (rt_constants.JOINTS_FK, fk), (rt_constants.JOINTS_FX, ik)]
    count = 0
    for jdict, color in mapping:
        for joints in jdict.values():
            for jnt in joints:
                if cmds.objExists(jnt):
                    set_joint_color(jnt, color)
                    count += 1
    logger.debug(f'Coloured {count} joints (BN={bn}, IK={ik}, FK={fk})')
    return count


def swap_shapes(target, source):
    """
    Replace target's shape nodes with source's.

    The target transform is never deleted, so anything parented under it
    stays parented. That matters because the alternative - deleting the
    transform and rebuilding it - has to detach the children first and
    re-find them afterwards, and a node cannot be re-found reliably by
    name: parenting to the world renames on a name clash, and duplicate
    short names elsewhere in the scene resolve to the wrong node.

    Every shape moves, so a control built from several shapes (a sphere
    is three circles) transfers whole.

    Full DAG paths are used throughout, and an ambiguous target or source
    is an error rather than a guess: short shape names collide easily, and
    the source is usually a temporary copy of the target.

    Arguments:
        target (str): Transform receiving the shapes
        source (str): Transform whose shapes are moved onto target

    Return:
        list: Full paths of the target's shapes after the swap
    """
    target_paths = cmds.ls(target, long=True, type='transform') or []
    source_paths = cmds.ls(source, long=True, type='transform') or []
    if len(target_paths) != 1:
        logger.error(f"'{target}' matches {len(target_paths)} transforms, "
                     f'cannot swap shapes')
        return []
    if len(source_paths) != 1:
        logger.error(f"'{source}' matches {len(source_paths)} transforms, "
                     f'cannot swap shapes')
        return []
    target, source = target_paths[0], source_paths[0]

    new_shapes = cmds.listRelatives(source, s=True, f=True) or []
    if not new_shapes:
        logger.error(f"'{source}' has no shapes to move onto '{target}'")
        return []

    # Old shapes go first so the incoming ones cannot collide by name
    old_shapes = cmds.listRelatives(target, s=True, f=True) or []
    if old_shapes:
        cmds.delete(old_shapes)
    for shape in new_shapes:
        # -r keeps the shape's local CVs, so it draws in the target's
        # space exactly as it did in the source's
        cmds.parent(shape, target, r=True, s=True)

    shapes = cmds.listRelatives(target, s=True, f=True) or []
    logger.trace(f"swapped {len(shapes)} shape(s) onto '{target}'")
    return shapes


# CREATE NODES =========================================================

def create_group(group, parent=None):
    """
    Create transform group.

    Arguments:
        group (str): Group name
        parent (str): Optional parent

    Return:
        str: Group name
    """
    if not cmds.objExists(group):
        group = cmds.createNode('transform', n=group, s=1, ss=1)
        set_group_visibility(group)

    if parent and cmds.objExists(parent):
        if not is_parent(group, parent):
            cmds.parent(group, parent)
    return group


def create_condition(node, firstTerm=None, secondTerm=0, op=0):
    """
    Create condition node.

    Arguments:
        node (str): Condition node name
        firstTerm (str): FirstTerm input connection
        secondTerm (float): SecondTerm value
        op (int): Operation (0=equal, 1=not equal, 2=greater, 3=greater or equal,
                  4=less, 5=less or equal)

    Return:
        str: Condition node name
    """
    cmds.createNode('condition', n=node, s=1, ss=1)
    cmds.setAttr(f"{node}.operation", op)
    cmds.setAttr(f"{node}.secondTerm", secondTerm)
    cmds.setAttr(f"{node}.colorIfTrueR", 1)
    cmds.setAttr(f"{node}.colorIfFalseR", 0)
    cmds.setAttr(f"{node}.colorIfTrueG", 0)
    cmds.setAttr(f"{node}.colorIfFalseG", 1)
    if firstTerm:
        cmds.connectAttr(firstTerm, f"{node}.firstTerm", f=1)
    return node


def create_condition_multi(driveattr, drivenattrs, node=None, secondTerm=0, op=0):
    """
    Create condition node with On/Off values to multiple nodes.

    Arguments:
        driveattr (str): FirstTerm driver attribute
        drivenattrs (list): List of driven attributes
        node (str): Optional condition node name
        secondTerm (float): SecondTerm value for On state
        op (int): Operation

    Return:
        str: Condition node name
    """
    driveattr_nn = driveattr.replace('.', '_')
    driven_nn = drivenattrs[0].replace('.', '_')
    if node:
        cond = cmds.createNode('condition', n=node, s=1, ss=1)
    else:
        cond = cmds.createNode('condition', n=f"{driveattr_nn}_{driven_nn}", s=1, ss=1)
    if cond:
        cmds.setAttr(f"{cond}.operation", op)
        cmds.setAttr(f"{cond}.secondTerm", secondTerm)
        cmds.setAttr(f"{cond}.colorIfTrueR", 1)
        cmds.setAttr(f"{cond}.colorIfFalseR", 0)
        cmds.setAttr(f"{cond}.colorIfTrueG", 0)
        cmds.setAttr(f"{cond}.colorIfFalseG", 1)
    else:
        cond = node
    cmds.connectAttr(driveattr, f"{cond}.firstTerm", f=1)
    for attr in drivenattrs:
        cmds.connectAttr(f"{cond}.outColor.outColorR", attr, f=1)
    return cond


# CURVE UTILITIES ======================================================

def get_num_cv(curve):
    """
    Get curve CV information.

    Arguments:
        curve (str): Curve name

    Return:
        tuple: (num_cv, spans, degree)
    """
    spans = cmds.getAttr(f"{curve}.spans")
    degree = cmds.getAttr(f"{curve}.degree")
    form = cmds.getAttr(f"{curve}.form")
    num_cv = spans + degree
    if form == 2:
        num_cv -= degree
    logger.trace(f"numcv:{num_cv} spans:{spans} degree:{degree} form:{form}")
    return num_cv, spans, degree


def create_curveinfo(rigname, curve, typ=''):
    """
    Create curveInfo node for curve length measurement.

    Arguments:
        rigname (str): Rig component name
        curve (str): Curve name
        typ (str): Type prefix

    Return:
        str: CurveInfo node name
    """
    curveinfo = rt_naming.fstr(rigname, rt_constants.CURVEINFO, typ)
    if cmds.objExists(curveinfo):
        return curveinfo
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0]
    connections = cmds.listConnections(f"{crvshape}.worldSpace[0]") or []
    bool_create_curveinfo = True
    for cnt in connections:
        if cmds.nodeType(cnt) == 'curveInfo':
            cmds.rename(cnt, curveinfo)
            bool_create_curveinfo = False
    if bool_create_curveinfo:
        cmds.createNode('curveInfo', n=curveinfo, s=1, ss=1)
        cmds.connectAttr(f"{crvshape}.worldSpace[0]", f"{curveinfo}.inputCurve", f=1)
    return curveinfo


# SDK ==================================================================

def sdk(driver, driven, dv, v):
    """
    Create a Set Driven Key entry.

    Arguments:
        driver (str): Attribute driving the SDK
        driven (str): Attribute controlled by the SDK
        dv (int): Driver value
        v (int): Driven value
    """
    cmds.setAttr(driver, k=1)
    cmds.setAttr(driven, k=1)
    cmds.setDrivenKeyframe(driven, cd=driver, dv=dv, v=v)


# ATTRIBUTES ===========================================================

def attribute_is_reusable(node, attr, pxy=None):
    """
    Whether an existing attribute can be updated in place by addAttr -e.

    Two definitions cannot be reached by editing, so the attribute has to
    be deleted and rebuilt instead:

    1. Proxy state. There is no addAttr -e flag to set or clear
       'usedAsProxy'. An attribute left over from an earlier rig can still
       be flagged as a proxy after its master was deleted, which leaves it
       stuck at its default value and silently freezes everything the
       attribute drives. Editing it cannot repair that, and an attribute
       asked to become a proxy cannot gain the flag by editing either.
       The one reusable case: an attribute that already IS a proxy of the
       wanted master needs no rebuild at all -- and keeping it also keeps
       its channel-box position, where a delete/re-add would move it to
       the end of the list on every rebuild.
    2. Type. An existing attribute of another type (a float ikfk switch
       from a hand-built rig, say) cannot be edited into an enum.

    Arguments:
        node (str): Node name
        attr (str): Attribute long name (must exist on node)
        pxy (str): Proxy source plug, when the attribute should be a proxy

    Return:
        bool: True if addAttr -e can express the wanted definition
    """
    if pxy:
        # Already a proxy of the wanted master: keep it as is. Anything
        # else must be rebuilt to attach a fresh proxy link.
        if attribute_is_proxy(node, attr):
            src = cmds.listConnections(f'{node}.{attr}', s=1, d=0, p=1) or []
            if pxy in src:
                return True
        return False
    if attribute_is_proxy(node, attr):
        return False  # Rebuild to shed the flag; -e cannot clear it
    try:
        return cmds.getAttr(f'{node}.{attr}', type=1) == 'enum'
    except RuntimeError:
        return False  # Unreadable type (message, compound): rebuild

def attribute_is_proxy(node, attr):
    """
    Whether an attribute carries Maya's 'usedAsProxy' flag.

    Read through the API because addAttr exposes usedAsProxy on create
    only, so there is no command-level query for it.

    Arguments:
        node (str): Node name
        attr (str): Attribute long name (must exist on node)

    Return:
        bool: True if the attribute is flagged as a proxy
    """
    sel = om.MSelectionList()
    sel.add(node)
    mfn = om.MFnDependencyNode(sel.getDependNode(0))
    try:
        return om.MFnAttribute(mfn.attribute(attr)).isProxyAttribute
    except (AttributeError, RuntimeError):
        # isProxyAttribute is Maya 2019+. On older versions report False
        # so the attribute is edited in place, matching previous behaviour
        logger.trace(f"cannot query proxy state of '{node}.{attr}'")
        return False

def set_attr_value(plug, value):
    """
    Set an attribute, skipping it when Maya will not accept the value.

    Used for the post-build states the build forces onto existing controls
    (visibility toggles, the IKFK mode). A locked or driven channel is not
    worth failing a build over - by the time these are applied the rig is
    already wired - so warn and carry on rather than abort.

    Arguments:
        plug (str): node.attribute
        value: Value to set

    Return:
        bool: True if the value was set
    """
    if not cmds.objExists(plug):
        logger.warning(f"'{plug}' does not exist; cannot set it")
        return False
    if not cmds.getAttr(plug, settable=True):
        logger.warning(f"'{plug}' is locked or driven; leaving it unchanged")
        return False
    try:
        cmds.setAttr(plug, value)
        return True
    except RuntimeError as err:
        logger.warning(f"could not set '{plug}' to {value}: {err}")
        return False

def remove_attribute(node, attr):
    """
    Delete a dynamic attribute, unlocking it and its incoming connection
    first so the delete cannot fail on a locked or driven attribute.

    Static attributes are never touched.

    Arguments:
        node (str): Node name
        attr (str): Attribute long name

    Return:
        bool: True if the attribute was deleted
    """
    plug = f'{node}.{attr}'
    if attr not in (cmds.listAttr(node, ud=1) or []):
        logger.warning(f"'{plug}' is not a dynamic attribute, not deleting")
        return False
    try:
        cmds.setAttr(plug, l=0)
    except RuntimeError:
        pass
    for src in cmds.listConnections(plug, s=1, d=0, p=1) or []:
        cmds.disconnectAttr(src, plug)
    cmds.deleteAttr(plug)
    return True

def add_attribute_enum(plug, ln, nn, en=None, dv=0, pxy=None):
    """
    Add Enum attribute to node.

    An attribute that already exists is updated in place where possible.
    When the existing definition cannot be edited into the wanted one
    (see attribute_is_reusable) it is deleted and rebuilt, so a rebuild
    over a previously rigged scene always ends with the attribute this
    function was asked for rather than a leftover from the old rig.

    Arguments:
        plug (str): Node plug (e.g., control.attribute)
        ln (str): Attribute long name
        nn (str): Attribute nice name
        en (str): Enum name options
        dv (int): Default value
        pxy (str): Proxy attribute
    """
    logger.trace(f"plug:'{plug}' ln:'{ln}' nn:'{nn}' en:'{en}' pxy:'{pxy}'")
    re_divider = re.search(r'(?i)[^-_\s]+(?=[-_\s]*divider)', ln)
    if '.' in plug:
        node, node_attr = plug.split('.', 1)
    else:
        node, node_attr = plug, ln
        plug = f"{node}.{ln}"

    exists = bool(cmds.attributeQuery(node_attr, n=node, ex=1))
    if exists and node_attr != ln:
        cmds.setAttr(plug, l=0)
        cmds.renameAttr(plug, ln)
        plug = f"{node}.{ln}"
        node_attr = ln
    if exists and not attribute_is_reusable(node, node_attr, pxy):
        logger.trace(f"cannot edit '{plug}' into wanted definition, rebuilding")
        exists = not remove_attribute(node, node_attr)
        if exists and pxy:
            # Undeletable (static) attribute in the way of a proxy. Editing
            # it cannot produce a proxy, so say so instead of leaving a
            # plain enum that looks right and drives nothing.
            logger.error(f"'{plug}' cannot be replaced by a proxy of "
                         f"'{pxy}'; leaving it unchanged")
            return

    if exists:
        if pxy:
            # attribute_is_reusable only lets an existing attribute
            # through with pxy when it is already a proxy of the wanted
            # master; its definition mirrors the master, nothing to edit
            logger.trace(f'kept proxy attribute {plug}')
            return
        if re_divider:
            if en:
                cmds.addAttr(plug, nn=nn, at='enum', e=1, en=en)
            else:
                cmds.addAttr(plug, nn='----------', at='enum', e=1, en=nn)
            cmds.setAttr(plug, cb=1, l=1)
        elif en:
            cmds.addAttr(plug, nn=nn, at='enum', e=1, en=en, dv=dv, k=1)
        else:
            cmds.addAttr(plug, nn=nn, at='enum', e=1, en='Hide:Show', dv=dv, k=1)
        logger.trace(f'edited attribute {plug}')
    else:
        if re_divider:
            if en:
                cmds.addAttr(node, ln=ln, nn=nn, at='enum', en=en, k=1)
            else:
                cmds.addAttr(node, ln=ln, nn='----------', at='enum', en=nn, k=1)
            cmds.setAttr(f"{node}.{ln}", cb=1, l=1)
        elif pxy:
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', pxy=pxy, k=1)
        elif en:
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', en=en, dv=dv, k=1)
        else:
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', en='Hide:Show', dv=dv, k=1)
        logger.trace(f"added attribute {node}.{ln}")


# GEOMETRY BINDING =====================================================

def geometry_matches_rigname(rigname, geo, warn=False):
    '''
    Check if a geometry name belongs to the rig part. Preferred naming
    is '<rigname>_geo' (with optional numeric indices: 'tail_01_geo');
    a bare '<rigname>' / '<rigname>_01' also matches, optionally with
    a warning that it does not follow the naming convention. Names of
    other parts never match ('R_tail_geo' does not belong to 'tail').

    Arguments:
        rigname (str): Rig component name
        geo (str): Geometry transform name
        warn (bool): Warn when the name lacks a geo/mesh term

    Return:
        bool: True if geo belongs to the rig part
    '''
    if rt_naming.name_contains_rigname_terms(rigname, geo,
                                          terms=r'mesh|geo|geometry'):
        return True
    if rt_naming.name_matches_rigname(rigname, geo):
        if warn:
            logger.warning(
                f"Geometry '{geo}' matches rig part '{rigname}' but not "
                f"the expected naming convention '<rigname>_geo'. "
                f"Binding it anyway; consider renaming.")
        return True
    return False


def bind_geometry(rigname):
    '''
    Search for geometry named after the rig part and bind to BN joints.
    Binds every match, so multi-mesh parts work: rigname 'tail' binds
    'tail_geo', 'tail_01_geo', and 'tail_02_geo'. Meshes named after
    the part but missing the geo term ('tail', 'tail_01') are bound
    with a naming warning. Skips meshes that belong to other parts
    ('R_tail_geo' is not bound by 'tail').
    Skips if no geometry found.

    Arguments:
        rigname (str): Rig component name
    '''
    if rigname not in rt_constants.JOINTS_BN or not rt_constants.JOINTS_BN[rigname]:
        logger.warning(f'No BN joints found for {rigname}, skipping geometry bind')
        return

    geos = geometry_transforms()
    if not geos:
        logger.trace('No geometry under the geometry group, skip bind')
        return

    bound = []
    for geo in geos:
        if geometry_matches_rigname(rigname, geo, warn=True):
            geo_leaf = geo.split('|')[-1]
            bind_skincluster(rt_constants.JOINTS_BN[rigname], geo,
                             f'{geo_leaf}_skinCluster',
                             preserve=preserve_skin())
            bound.append(geo_leaf)
    if not bound:
        logger.trace(f'{rigname}: No geometry named after rig part, skip bind')


def geometry_transforms(root=None):
    '''
    Mesh transforms under the geometry group (or any given root).

    ONE typed listRelatives for the whole subtree, and the transforms come
    from the mesh shapes it returns. The obvious version - list every
    descendant transform, then ask is_geometry(t) about each - costs two
    more commands per transform, and it is called once per rig part in
    cleanup (unbind), in connect (bind) and again in report_missing_
    geometry, so on a character with a few hundred meshes that walk was a
    measurable slice of both phases.

    Intermediate shapes (the 'Orig' mesh a skinCluster leaves behind) share
    their transform with the visible shape, so the result is deduped;
    dict.fromkeys keeps the scene order the callers used to see.

    Arguments:
        root (str): Subtree to search, defaulting to the rig geometry group.

    Return:
        list: full paths of mesh transforms (empty when there is no root).
    '''
    root = root or rt_naming.fstr('', rt_constants.GEOMETRY_GRP)
    if not cmds.objExists(root):
        return []
    # Full paths: descendant short names are frequently ambiguous under a
    # geometry group (L_fin|body and R_fin|body both come back as 'body'),
    # and an ambiguous name binds the skinCluster to the wrong mesh
    shapes = cmds.listRelatives(root, typ='mesh', ad=1, f=1) or []
    return list(dict.fromkeys(s.rsplit('|', 1)[0] for s in shapes))


def find_geometry_for_rigname(rigname):
    '''
    Geometry transforms under the geometry group that match a rig part.

    Uses the same name rule as bind_geometry/unbind_geometry
    (geometry_matches_rigname: '<rigname>_geo', '<rigname>', or
    '<rigname>_NN'). Full DAG paths, since descendant short names are
    frequently ambiguous under a geometry group.

    Arguments:
        rigname (str): Rig component name.

    Return:
        list: full paths of matching geometry transforms (empty if none).
    '''
    return [g for g in geometry_transforms()
            if geometry_matches_rigname(rigname, g)]


def report_missing_geometry(rignames):
    '''
    Warn about rig parts with no geometry matching the naming convention.

    A part whose mesh is not named '<rigname>_geo' / '<rigname>' /
    '<rigname>_NN' cannot be matched, so it is never unbound before Setup
    re-orients (its mesh distorts) nor rebound by the build. Rather than
    guess, this logs one consolidated warning listing every unmatched part
    so its mesh can be renamed. No-op when there is no geometry group
    (nothing to bind against).

    Arguments:
        rignames (list): Rig parts to check.

    Return:
        list: rignames with no matching geometry.
    '''
    if not cmds.objExists(rt_naming.fstr('', rt_constants.GEOMETRY_GRP)):
        return []
    # One subtree scan for every part, not one per part
    geos = geometry_transforms()
    missing = [rn for rn in rignames
               if not any(geometry_matches_rigname(rn, g) for g in geos)]
    if missing:
        logger.warning(
            f'Geometry not found for {len(missing)} rig part(s): '
            f'{", ".join(missing)}. Their meshes do not follow the naming '
            "convention ('<rigname>_geo', '<rigname>', or '<rigname>_NN'), "
            'so they will not bind or deform - rename the meshes to match.')
    return missing


def unbind_geometry(rigname, force=False):
    '''
    Get geometry and unbind skinclusters.

    With rt_constants.PRESERVE_SKIN on, geometry that already carries a
    skinCluster is LEFT BOUND and returned instead: its weights are paint
    work that an unbind destroys. The caller is responsible for the pose
    those meshes are left in -- Setup re-baselines them (rebaseline_skin)
    after moving the joints, and the build's bind_geometry reuses the
    cluster rather than rebuilding it.

    Arguments:
        rigname (str): Rig component name
        force (bool): Unbind even when PRESERVE_SKIN is on

    Return:
        list: geometry deliberately left bound (empty when everything was
        unbound).
    '''
    logger.trace(f"{rigname}: Unbind geometry")
    preserve = not force and preserve_skin()
    kept = []
    for geo in find_geometry_for_rigname(rigname):
        if preserve and find_skincluster(geo):
            kept.append(geo)
            continue
        unbind_skincluster(geo)
    if kept:
        logger.debug(f'{rigname}: PRESERVE_SKIN, keeping the skin on '
                     f'{len(kept)} mesh(es): '
                     f'{", ".join(g.split("|")[-1] for g in kept)}')
    return kept


def unbind_geometry_all():
    '''
    Get geometry and unbind all skinclusters.
    '''
    logger.trace(f"Unbind geometry")
    geos = get_geometry_from_scene()
    for geo in geos:
        if is_geometry(geo):
            unbind_skincluster(geo)
    for geo in geometry_transforms():
        unbind_skincluster(geo)


def _delete_orphan_bindposes(poses):
    '''
    Delete bindPose (dagPose) nodes no longer used by any skinCluster.
    Called after unbinding: Maya creates a bindPose per bind and never
    removes it, so rebuilds accumulate one orphan per rebind.

    Arguments:
        poses (list): Candidate dagPose node names
    '''
    for pose in poses:
        if not cmds.objExists(pose):
            continue
        users = cmds.listConnections(f'{pose}.message', s=False, d=True,
                                     t='skinCluster') or []
        if not users:
            cmds.delete(pose)


# SKIN PRESERVATION ----------------------------------------------------

def preserve_skin():
    '''
    Read rt_constants.PRESERVE_SKIN, defaulting to on.

    rig_tail_constants is never reloaded (it holds session state), so a
    Maya session started before this setting existed does not have it;
    fall back to the shipped default rather than silently rebinding.

    Return:
        bool: True when existing skinClusters must be kept.
    '''
    return bool(getattr(rt_constants, 'PRESERVE_SKIN', True))


def find_skincluster(node):
    '''
    First skinCluster in a node's history.

    Arguments:
        node (str): Geometry transform or shape

    Return:
        str or None: skinCluster node name.
    '''
    if not cmds.objExists(node):
        return None
    skins = cmds.ls(cmds.listHistory(node), type='skinCluster') or []
    return skins[0] if skins else None


def _leaf(name):
    ''' Short name of a DAG path ('|geo|body' -> 'body'). '''
    return name.split('|')[-1]


def skin_influence_indices(skincluster):
    '''
    Map each influence to its logical index on the skinCluster.

    Influence indices are SPARSE -- a mesh that has had influences added
    and removed over its life ends up with gaps (0,1,2,3,14,15,26...) --
    so bindPreMatrix[i] has to be found through the matrix connections,
    never by counting the influence list.

    Arguments:
        skincluster (str): skinCluster node

    Return:
        dict: {short joint name: logical index}
    '''
    conns = cmds.listConnections(f'{skincluster}.matrix', s=True, d=False,
                                 p=True, c=True) or []
    indices = {}
    # Pairs: [<skinCluster>.matrix[i], <joint>.worldMatrix[0], ...]
    for i in range(0, len(conns) - 1, 2):
        dest, src = conns[i], conns[i + 1]
        try:
            index = int(dest.rsplit('[', 1)[-1].rstrip(']'))
        except ValueError:
            continue
        indices[_leaf(src.split('.')[0])] = index
    return indices


def add_missing_influences(skincluster, joints):
    '''
    Add joints that are not yet influences of the skinCluster, at weight 0.

    Adding at weight 0 leaves every existing weight exactly as painted:
    the new joints simply do nothing until they are painted in. The
    alternative -- deleting the cluster and rebinding -- is what destroys
    an artist's work on a shared mesh.

    Arguments:
        skincluster (str): skinCluster node
        joints (list): Joints that should influence the mesh

    Return:
        list: joints actually added
    '''
    existing = {_leaf(i) for i in
                (cmds.skinCluster(skincluster, q=True, inf=True) or [])}
    added = []
    for jnt in joints:
        if _leaf(jnt) in existing or not cmds.objExists(jnt):
            continue
        try:
            # lw/wt: join the cluster contributing nothing. The lock is
            # released again so the weights stay paintable.
            cmds.skinCluster(skincluster, e=True, ai=jnt, lw=True, wt=0.0)
            if cmds.attributeQuery('liw', node=jnt, exists=True):
                cmds.setAttr(f'{jnt}.liw', 0)
            added.append(jnt)
        except Exception as err:
            logger.warning(f"Could not add '{jnt}' as an influence of "
                           f"'{skincluster}': {err}")
    return added


def _chain_influence_joints(rigname):
    '''
    The rig part's BN joints plus its end ('ee') joint.

    get_joint_chain stops before the end joint, so JOINTS_BN excludes it --
    but Setup re-orients it too and it is frequently an influence, so a
    re-baseline that skipped it would leave the mesh's tip behind.

    Arguments:
        rigname (str): Rig component name

    Return:
        list: joints to re-baseline
    '''
    joints = list(rt_constants.JOINTS_BN.get(rigname) or [])
    if not joints:
        return []
    for child in cmds.listRelatives(joints[-1], c=True, typ='joint') or []:
        if child not in joints:
            joints.append(child)
    return joints


def _rest_drift(bind_pre, joint):
    '''
    How far a joint has moved since the skinCluster was baselined.

    bindPreMatrix holds the inverse of the joint's world matrix at bind
    time, so bindPreMatrix * worldMatrix is the identity while the joint
    sits where it was bound; its translation is the drift.

    Arguments:
        bind_pre (list): Stored bindPreMatrix (16 floats)
        joint (str): Influence joint

    Return:
        tuple: (position drift in scene units, max rotation-term delta)
    '''
    delta = om.MMatrix(bind_pre) * om.MMatrix(
        cmds.getAttr(f'{joint}.worldMatrix[0]'))
    pos = om.MVector(delta[12], delta[13], delta[14]).length()
    rot = max(abs(delta[r * 4 + c] - (1.0 if r == c else 0.0))
              for r in range(3) for c in range(3))
    return pos, rot


def rebaseline_skin(rigname, tolerance=None):
    '''
    Accept the joints' CURRENT pose as the skin's rest pose.

    Setup re-orients, and may move, the BN joints. With the skin left
    bound that drags the mesh -- so instead of unbinding (which throws the
    painted weights away), write each moved influence's new world matrix
    into the skinCluster's bindPreMatrix. The skinCluster then reads the
    new pose as the pose it was bound in, and the mesh snaps back to its
    modelled shape with every weight intact.

    Only the rig part's own joints are re-baselined. Other influences on a
    shared mesh (a body skinned to head and limb joints as well) did not
    move and are left alone. Joints still within tolerance are skipped, so
    a run that changed nothing writes nothing.

    Arguments:
        rigname (str): Rig component name
        tolerance (float): Position drift treated as unchanged. Defaults
            to rt_constants.JOINT_POS_TOLERANCE.

    Return:
        int: influences re-baselined
    '''
    joints = _chain_influence_joints(rigname)
    if not joints:
        return 0
    if tolerance is None:
        tolerance = getattr(rt_constants, 'JOINT_POS_TOLERANCE', 0.001)

    total = 0
    for geo in find_geometry_for_rigname(rigname):
        skincluster = find_skincluster(geo)
        if not skincluster:
            continue
        indices = skin_influence_indices(skincluster)
        updated, max_drift = 0, 0.0
        for jnt in joints:
            index = indices.get(_leaf(jnt))
            if index is None:
                continue
            plug = f'{skincluster}.bindPreMatrix[{index}]'
            pos, rot = _rest_drift(cmds.getAttr(plug), jnt)
            max_drift = max(max_drift, pos)
            if pos <= tolerance and rot <= 1e-5:
                continue
            cmds.setAttr(plug, cmds.getAttr(f'{jnt}.worldInverseMatrix[0]'),
                         type='matrix')
            updated += 1
        _reset_bindpose(skincluster, joints)
        total += updated
        if updated:
            logger.info(f'{rigname}: re-baselined {updated} influence(s) on '
                        f"'{_leaf(geo)}' (max move {max_drift:.4f}); "
                        f'skin weights kept')
        else:
            logger.debug(f"{rigname}: '{_leaf(geo)}' rest pose unchanged, "
                         f'no re-baseline needed')
    return total


def _reset_bindpose(skincluster, joints):
    '''
    Re-stamp the bindPose for joints whose rest pose was just rewritten,
    so 'Go to Bind Pose' and any later rebind agree with the skinCluster.
    Best-effort: a missing or shared bindPose is not worth failing over.

    Arguments:
        skincluster (str): skinCluster node
        joints (list): Joints that moved
    '''
    poses = cmds.listConnections(f'{skincluster}.bindPose',
                                 s=True, d=False) or []
    for pose in poses:
        try:
            cmds.dagPose(*joints, reset=True, n=pose)
        except Exception as err:
            logger.debug(f"Could not reset bindPose '{pose}': {err}")


def bind_skincluster(joints, node, name, preserve=False):
    '''
    Bind joints to the node (an object such as curve or geo),
    creating a skinCluster with the given name.

    skinCluster Options:
        normalizeWeights: interactive
        bindMethod: closest distance between joint and point on geo
        skinMethod: classic linear
        maximumInfluences: 4
        toSelectedBones: True

    An existing skinCluster with exactly these influences is always
    reused. One with a DIFFERENT influence set is deleted and rebuilt,
    unless preserve is on -- then the rig joints are added to it at
    weight 0 and its weights survive. Only geometry passes preserve: the
    IK/FK driver curves are rig-owned, rebuilt with the rig, and must be
    bound to exactly their own joints.

    Arguments:
        joints (list): List of joints
        node (str): Object to bind
        name (str): Name for skinCluster
        preserve (bool): Keep an existing cluster with other influences

    Return:
        str: skinCluster node name
    '''
    if not cmds.objExists(node):
        logger.warning(f'Node does not exist: {node}')
        return None
    logger.trace(f"Bind skinCluster '{name}' to object '{node}'")

    # Check if skinCluster already exists
    existing_skin = cmds.ls(cmds.listHistory(node), type='skinCluster')
    if existing_skin:
        logger.trace(f'SkinCluster already exists on {node}: {existing_skin[0]}')

        # Check if it's the same joints
        existing_influences = cmds.skinCluster(existing_skin[0], q=True, inf=True)
        if existing_influences is not None and set(existing_influences) == set(joints):
            logger.debug(f'Reusing existing skinCluster: {existing_skin[0]}')
            return existing_skin[0]
        elif existing_influences is not None and preserve:
            # A different influence set is the normal case for a mesh the
            # rig shares with the rest of the character, or one whose
            # weights have been painted. Join the existing cluster instead
            # of replacing it: deleting it here is what destroys the paint.
            added = add_missing_influences(existing_skin[0], joints)
            if added:
                logger.warning(
                    f"'{_leaf(node)}' is already skinned "
                    f"('{existing_skin[0]}'), so PRESERVE_SKIN kept its "
                    f"weights and added {len(added)} rig joint(s) as "
                    f"influences at WEIGHT 0 - the mesh will not follow "
                    f"this rig part until they are painted in. Set "
                    f"PRESERVE_SKIN = False to rebind from scratch instead "
                    f"(which deletes the existing weights).")
            else:
                logger.debug(f'Reusing existing skinCluster (superset of the '
                             f'rig joints): {existing_skin[0]}')
            return existing_skin[0]
        else:
            # Different joints or failed query, need to unbind first
            logger.debug(f'Removing old skinCluster {existing_skin[0]} (different joints or invalid)')
            poses = cmds.listConnections(f'{existing_skin[0]}.bindPose',
                                         s=True, d=False) or []
            cmds.delete(existing_skin[0])
            _delete_orphan_bindposes(poses)

    # Create skinCluster - Bind method is closest distance
    return cmds.skinCluster(joints, node, n=name, nw=1, bm=0, sm=0, mi=4, tsb=True)


def unbind_skincluster(node, delete_history=True):
    '''
    Unbind skinClusters on given node and optionally delete history.

    Arguments:
        node (str): Object to unbind
        delete_history (bool): Delete history after unbind
    '''
    if not cmds.objExists(node):
        return
    # Full paths: short shape names are ambiguous when the scene
    # contains duplicate node names
    shapes = cmds.listRelatives(node, s=1, ni=1, f=1) or []
    if not shapes:
        logger.warning(f"No shapes found for '{node}'")
        return

    for shape in shapes:
        # Get skinClusters connected to shape
        skinclusters = cmds.listConnections(shape, d=0, t='skinCluster') or []
        for skincluster in skinclusters:
            poses = cmds.listConnections(f'{skincluster}.bindPose',
                                         s=True, d=False) or []
            try:
                cmds.skinCluster(node, e=1, ub=1)
            except Exception as err:
                logger.warning(f"Failed to unbind '{skincluster}' from '{node}': {err}")
            _delete_orphan_bindposes(poses)

    if delete_history:
        cmds.delete(node, ch=1)

