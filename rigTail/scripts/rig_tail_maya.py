"""
rig_tail_maya.py
author: Daisy Jane @gnitemouse

Consolidated Maya wrappers and scene helpers for Rig Tail.

Functions:
    obj_exists: Check if object exists
    remove: Delete object safely
    parent_to: Parent node to given parent
    is_parent: Check if node is already parent
    get_constraint: Get constraints on node
    get_geometry_from_scene: Collect mesh geometry from scene
    get_joints_from_scene: Collect top-level joints from scene
    get_controls_from_scene: Collect controls from scene
    is_control: Check if node is curve control
    is_geometry: Check if node is mesh
    disconnect_all: Disconnect all connections from node
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
    create_group: Create transform group
    create_condition: Create condition node
    create_condition_multi: Create multi-output condition
    get_num_cv: Get curve CV count
    create_curveinfo: Create curveInfo node
    sdk: Create set driven key
    add_attribute_enum: Add enum attribute
    list_hierarchy: Iterative traversal helper
    has_non_default_locked_attributes: Check for locked attributes
    bind_geometry: Bind geometry to BN joints
    unbind_geometry: Unbind geometry from rig
    unbind_geometry_all: Unbind all geometry in scene
    bind_skincluster: Create skinCluster binding
    unbind_skincluster: Unbind skinCluster from node
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import re

logger = logger_setup(__name__)


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
        conns = cmds.listConnections(node, s=0, d=1) or []
        for c in conns:
            if cmds.nodeType(c) == 'curveInfo':
                cmds.delete(c)
        if 'Constraint' not in cmds.objectType(node):
            disconnect_all(node)
        cmds.delete(node)


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
    cmds.setAttr(plug, l=0)
    if cmds.connectionInfo(plug, id=True):
        plug = cmds.connectionInfo(plug, ged=True)
        readonly = cmds.ls(plug, ro=True)
        if readonly:
            source = cmds.connectionInfo(plug, sfd=True)
            cmds.disconnectAttr(source, plug)
        else:
            cmds.delete(plug, icn=True)


# TRANSFORM OPERATIONS =================================================

def opm(node):
    """
    Move transform values to Offset Parent Matrix.
    Node must be transform or joint type and have attributes unlocked.

    Arguments:
        node (str): Node to bake transforms
    """
    if has_non_default_locked_attributes(node):
        abort_build(logger, f'Node {node} has at least one non default locked attribute(s)')

    local_matrix = om.MMatrix(cmds.xform(node, q=1, m=1, os=1))
    offset_parent_matrix = om.MMatrix(cmds.getAttr(f"{node}.offsetParentMatrix"))
    baked_matrix = local_matrix * offset_parent_matrix
    cmds.setAttr(f"{node}.offsetParentMatrix", baked_matrix, typ='matrix')
    reset_transforms(node)


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


def reset_transforms(node, unlock=True):
    """
    Reset translate, rotate, scale, shear, jointOrient to defaults.

    Arguments:
        node (str): Node to reset
        unlock (bool): If True, unlock and break connections
    """
    for attribute in ['translate', 'rotate', 'scale', 'shear', 'jointOrient']:
        default_value = 1 if attribute == "scale" else 0
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=node, ex=1):
                plug = f"{node}.{attribute}{axis}"
                if unlock:
                    break_connection(plug)
                if not cmds.getAttr(plug, lock=True):
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
        child_uids = cmds.ls(src_children, uuid=True) if src_children else []
        tmp_grp = cmds.group(em=True, n=f"{source}_tmp")
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


def has_non_default_locked_attributes(node, attrcheck=None):
    """
    Check whether node has locked non-default attributes.

    Arguments:
        node (str): Node to check
        attrcheck (list): Specific attributes to check

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

    for attribute in attrcheck:
        default_value = 1 if attribute == "scale" else 0
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=node, ex=1):
                plug = f"{node}.{attribute}{axis}"
                current_value = cmds.getAttr(plug)
                if cmds.getAttr(plug, lock=True) and current_value != default_value:
                    return True
    return False


# VISIBILITY ===========================================================

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
    if not cmds.attributeQuery('visibility', n=node, ex=1):
        logger.error(f"'{node}.visibility' does not exist.")
        return
    cmds.setAttr(f"{node}.visibility", l=0)
    cmds.setAttr(f"{node}.visibility", value)
    cmds.setAttr(f"{node}.visibility", k=k, cb=cb, l=l)


def set_transform_visibility(node, k=1, cb=1, l=0):
    """
    Set translate, rotate show/hide or lock attribute.

    Arguments:
        node (str): Node to modify
        k (int): Keyable flag
        cb (int): Channel box flag
        l (int): Lock flag
    """
    for attribute in ['translate', 'rotate']:
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=node, ex=1):
                cmds.setAttr(f"{node}.{attribute}{axis}", k=k, cb=cb, l=l)


def set_curve_visibility(curve, visibility=1):
    """
    Set curve visibility nonkeyable, attributes nonkeyable.

    Arguments:
        curve (str): Curve transform
        visibility (int): Visibility value
    """
    for attribute in ['translate', 'rotate', 'scale']:
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=curve, ex=1):
                cmds.setAttr(f"{curve}.{attribute}{axis}", k=0, cb=0, l=1)
    set_visibility(curve, visibility, k=1, cb=0, l=0)


def set_group_visibility(group, visibility=1):
    """
    Set group visibility nonkeyable, hide attributes.

    Arguments:
        group (str): Group transform
        visibility (int): Visibility value
    """
    for attribute in ['translate', 'rotate', 'scale']:
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=group, ex=1):
                cmds.setAttr(f"{group}.{attribute}{axis}", k=0, cb=0, l=1)
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
    for attribute in ['translate', 'rotate', 'scale']:
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=joint, ex=1):
                cmds.setAttr(f"{joint}.{attribute}{axis}", k=k, cb=cb)
    if cmds.attributeQuery('radius', n=joint, ex=1):
        cmds.setAttr(f"{joint}.radius", k=k, cb=cb)
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
        joint_dicts = [rt_cst.JOINTS_BN, rt_cst.JOINTS_IK,
                       rt_cst.JOINTS_FK, rt_cst.JOINTS_FX]
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
    from rig_tail_naming import fstr
    curveinfo = fstr(rigname, rt_cst.CURVEINFO, typ)
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
    if rt_nam.name_contains_rigname_terms(rigname, geo,
                                          terms=r'mesh|geo|geometry'):
        return True
    if rt_nam.name_matches_rigname(rigname, geo):
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
    if rigname not in rt_cst.JOINTS_BN or not rt_cst.JOINTS_BN[rigname]:
        logger.warning(f'No BN joints found for {rigname}, skipping geometry bind')
        return

    geometry_grp = rt_nam.fstr('', rt_cst.GEOMETRY_GRP)
    if not cmds.objExists(geometry_grp):
        logger.trace(f'Geometry group not found, skip bind')
        return

    # Full paths: descendant short names are frequently ambiguous under a
    # geometry group (L_fin|body and R_fin|body both come back as 'body'),
    # and an ambiguous name binds the skinCluster to the wrong mesh
    geos = cmds.listRelatives(geometry_grp, typ='transform', ad=1, f=1) or []
    if not geos:
        logger.trace(f'No geometry under {geometry_grp}, skip bind')
        return

    bound = []
    for geo in geos:
        if geometry_matches_rigname(rigname, geo, warn=True):
            if is_geometry(geo):
                geo_leaf = geo.split('|')[-1]
                bind_skincluster(rt_cst.JOINTS_BN[rigname], geo,
                                 f'{geo_leaf}_skinCluster')
                bound.append(geo_leaf)
    if not bound:
        logger.trace(f'{rigname}: No geometry named after rig part, skip bind')


def unbind_geometry(rigname):
    '''
    Get geometry and unbind skinclusters.

    Arguments:
        rigname (str): Rig component name
    '''
    logger.trace(f"{rigname}: Unbind geometry")
    geometry_grp = rt_nam.fstr('', rt_cst.GEOMETRY_GRP)
    if cmds.objExists(geometry_grp):
        # Full paths: descendant short names are frequently ambiguous
        # under a geometry group (L_fin|body and R_fin|body both come
        # back as 'body'), and an ambiguous name hits the wrong mesh
        geos = cmds.listRelatives(geometry_grp, typ='transform', ad=1, f=1) or []
        for geo in geos:
            if geometry_matches_rigname(rigname, geo):
                if is_geometry(geo):
                    unbind_skincluster(geo)


def unbind_geometry_all():
    '''
    Get geometry and unbind all skinclusters.
    '''
    logger.trace(f"Unbind geometry")
    geos = get_geometry_from_scene()
    for geo in geos:
        if is_geometry(geo):
            unbind_skincluster(geo)
    geometry_grp = rt_nam.fstr('', rt_cst.GEOMETRY_GRP)
    if cmds.objExists(geometry_grp):
        # Full paths: descendant short names are frequently ambiguous
        # under a geometry group (L_fin|body and R_fin|body both come
        # back as 'body'), and an ambiguous name hits the wrong mesh
        geos = cmds.listRelatives(geometry_grp, typ='transform', ad=1, f=1) or []
        for geo in geos:
            if is_geometry(geo):
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


def bind_skincluster(joints, node, name):
    '''
    Bind joints to the node (an object such as curve or geo),
    creating a skinCluster with the given name.

    skinCluster Options:
        normalizeWeights: interactive
        bindMethod: closest distance between joint and point on geo
        skinMethod: classic linear
        maximumInfluences: 4
        toSelectedBones: True

    Arguments:
        joints (list): List of joints
        node (str): Object to bind
        name (str): Name for skinCluster

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

