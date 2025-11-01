'''
# rig_tail_util.py
author: Daisy Jane Lee @dayzl

Utilities for Rig Tail
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import rig_tail_constants as cst
from rig_tail_constants import *
from rig_tail_control import get_controls_all
import re

logger = logger_setup(__name__)


# NAMING UTILITY =======================================================

def fstr(rigname, template, TYPE='', NN='', nn='', TAG=''):
    '''
    Evaluate fstring template.
    Naming Convention follows template above.
    '''
    if '{ROOT}' in template:
        template = template.replace('{ROOT}', cst.ROOT)
    _NN = f"_{DFORMAT.format(int(NN))}" if NN!='' else ''
    _nn = f"_{DFORMAT.format(int(nn))}" if nn!='' else ''
    return eval(f"f'''{template}'''")

def get_rigname(node, template, underscore=True):
    '''
    Get rigname from node, provided a naming template.
    Node name must follow the naming convention from template.
    Example
        jnt = 'FK_L_tail3_00_jnt' # node
        FK_JNT = '{TYPE}{rigname}_{NN:02d}{JNT}' # template
        get_rigname(jnt, FK_JNT) = 'L_tail3'
    '''
    filter_prefix = template.split('{rigname}', 1)[0]
    filter_suffix = template.split('{rigname}', 1)[-1]
    NN = get_index_from_name(node, first=True, underscore=underscore)
    nn = get_index_from_name(node, first=False, underscore=underscore)
    rigtype = ''
    for typ in [TYPE_BN, TYPE_FK, TYPE_IK]: # Detect TYPE
        if typ in node:
            rigtype = typ
            break
    prefix = fstr('', filter_prefix, rigtype, NN, nn)
    suffix = fstr('', filter_suffix, rigtype, NN, nn)
    regex = re.search(fr'(?<={prefix})(\S+)(?={suffix})', node)
    return regex.group() if regex else None

def get_index_from_name(node, first=True, underscore=True):
    '''
    Extract numerical index (int) from string name.
    Return None if index not found.

    Arguments
        node (str): Node name
        first (bool): Search first/last
            True - Return first matching number
                e.g) 'FK_R_sidetail_00_01_sdk' -> '00'
            False - Return last matching number
                e.g) 'FK_R_sidetail_00_01_sdk' -> '01'
        underscore (bool):
            Index is preceded by whitespace, underscore, or dash
            True - Index must be preceded by whitespace, underscore, dash
                e.g) 'R_sidetail_01_ctrl' -> '01'
            False - Any index
                e.g) 'R_sidetail01_ctrl' -> '01'
    '''
    regex = None
    if first: # Search first
        if underscore: # Must be preceded by whitespace, underscore, or dash
            regex = re.search(r'(?<![^\s_-])\d+', node)
        else:
            regex = re.search(r'\d+', node)
        return int(regex.group()) if regex else None
    else: # Search last
        if underscore:
            regex = re.findall(r'(?<![^\s_-])\d+', node)
        else:
            regex = re.findall(r'\d+', node)
        return int(regex[-1]) if regex else None

def titlecase(text, underscore=True):
    words = ''
    if underscore:
        words = text.split('_')
    else:
        words = text.split()
    return ' '.join(word.title() for word in words)

def name_contains_rigname_terms(rigname, name, terms=r'mesh|geo|geometry'):
    # Valid whitespace: underscore, dash, space
    sep = r'[_\-\s]*'
    # Regex Pattern: one part must match rigame exactly,
    # other part matches terms (case insensitive), and
    # both can appear in any order, separated by any number of characters
    pattern = (
        rf'(?i)(?=.*\b{re.escape(rigname)}{sep}({terms})\b)'
        rf'|(?=.*\b({terms}){sep}{re.escape(rigname)}\b)'
        )
    return re.search(pattern, name) is not None

def rename_shapes(node, typ='ctrl', prefix='', suffix='Shape'):
    shapes = cmds.listRelatives(node, s=True, f=True) or []
    name = node.lstrip('|').rsplit(f"_{typ}", 1)[0]
    i = 0
    for shape in shapes:
        if 'Orig' in shape:
            cmds.rename(shape, f"{prefix}{name}_{typ}{suffix}Orig")
        else:
            if i > 0:
                cmds.rename(shapes[i], f"{prefix}{name}_{i:02}_{typ}{suffix}")
            else:
                cmds.rename(shapes[0], f"{prefix}{name}_{typ}{suffix}")
            i += 1


# GENERAL UTILITY ======================================================

def get_constraint(node, typ=None):
    constraint_types = ['parentConstraint', 'pointConstraint', 'orientConstraint',
                        'scaleConstraint', 'aimConstraint']
    if typ: # Specified constraint type
        if typ in constraint_types:
            return cmds.listRelatives(node, typ=typ) or []
        else:
            logger.error(f"typ '{typ}' must be a valid constraint type.")
    else: # Any constraint
        return cmds.listRelatives(node, typ='constraint') or []

def linspace(start, stop, n):
    if n==1:
        yield stop
        return
    h = (stop-start) / (n-1)
    for i in range(n):
        yield start + h*i

def get_axis_orientation(nodes, secondary_axis=False):
    '''
    Guess axis orientation for a list of objects based on pivot world positions.

    Arguments
        nodes (list): list of objects
    Return
        up_axis (str): x,y,z preferred up-axis based on shortest edge of bounding box

    if Up Axis (secondary_axis=False):
        The shortest edge of a bounding box is the up-axis
    if Secondary Axis (secondary_axis=True):
        If the longest edge is 'y', then return 'z' else 'y'
    Up axis is always positive.
    '''
    node_pos = list()
    for node in nodes: # Get world pos of pivots
        node_pos.append(cmds.xform(node, q=1, ws=1, rp=1))

    # Get range of XYZ values
    range_xyz = list()
    for values in zip(*node_pos):
        floats = sorted(values)
        min_val = round(floats[0], 5)
        max_val = round(floats[-1], 5)
        axis_range = max_val - min_val
        range_xyz.append(axis_range)

    # Compare edges of bounding box
    if range_xyz[0] > range_xyz[1] and range_xyz[0] > range_xyz[2]: # x < y < z
        if not secondary_axis:
            return '+x'
        else:
            return '+y'
    elif range_xyz[1] > range_xyz[0] and range_xyz[1] > range_xyz[2]: # y < x < z
        if not secondary_axis:
            return '+y'
        else:
            return '+z'
    else: # z < x < y
        if not secondary_axis:
            return '+z'
        else:
            return '+y'

def get_num_cv(curve):
    '''
    Return num_cv, spans, degree
    '''
    spans = cmds.getAttr(f"{curve}.spans")
    degree = cmds.getAttr(f"{curve}.degree")
    form = cmds.getAttr(f"{curve}.form")
    num_cv = spans + degree
    if form == 2:
        num_cv -= degree
    logger.debug(f"numcv:{num_cv} spans:{spans} degree:{degree} form:{form}")
    return num_cv, spans, degree


# CLEANUP NODES ========================================================

def remove(node):
    '''
    Remove object.
    '''
    if cmds.objExists(node):
        conns = cmds.listConnections(node, s=0, d=1) or []
        for c in conns:
            if cmds.nodeType(c) == 'curveInfo':
                cmds.delete(c)
        if not 'Constraint' in cmds.objectType(node):
            disconnect_all(node)
        cmds.delete(node)

def parent_to(node, parent, a=False, r=False):
    '''
    Parent node to given parent. Check first if already parent.
    '''
    logger.debug(f"{node} {parent}")
    if not is_parent(node, parent):
        if a:
            cmds.parent(node, parent, a=1)
        elif r:
            cmds.parent(node, parent, r=1)
        else:
            cmds.parent(node, parent)

def is_parent(node, parent):
    node_parent = cmds.listRelatives(node, p=True, typ='transform') or []
    if parent in node_parent:
        return True # is node parent
    return False # is not node parent


# BIND SKIN ============================================================

def bind_skincluster(joints, node, name):
    '''
    Bind joints to the node (an object such as curve or geo),
    creating a skinCluster with the given name.
    Arguments
        joints (str list): list of joints
        node (str): object to bind
        name (str): name of skinCluster

    skinCluster Options - [
        normalizeWeights: interactive,
        bindMethod: closest distance between joint and point on geo,
        skinMethod: classic linear,
        maximumInfluences: 4,
        toSelectedBones: True]
    '''
    logger.info(f"Bind jnts to '{node}' - skinCluster '{name}'")
    try: # Bind method - surface heat map diffusion
        return cmds.skinCluster(joints, node, n=name, nw=1, bm=2, sm=0, mi=4, tsb=True)
    except: # Bind method - Closest distance between joint and a point of the geometry
        return cmds.skinCluster(joints, node, n=name, nw=1, bm=0, sm=0, mi=4, tsb=True)

def unbind_skincluster(node, typ='crv'):
    '''
    Rename shapes, unbind skinClusters and delete history
    Arguments
        node (str): object to unbind
        typ (str): object type 'crv' or 'geo'
    '''
    rename_shapes(node, typ=typ) # Rename shapes
    shape = cmds.listRelatives(node, s=True)[0]
    # Get skinCluster
    skinclusters = cmds.listConnections(shape, d=False, t='skinCluster') or []
    for skincluster in skinclusters:
        # Unbind geometry, Delete skin history
        cmds.skinCluster(node, e=1, ub=1)

def bind_geometry(rigname):
    logger.info(f"Bind Geometry '{rigname}'")
    geometry_grp = fstr('', GEOMETRY_GRP)
    joints = cst.JOINTS_BN[rigname]
    geos = cmds.listRelatives(geometry_grp, typ='transform') or []
    for geo in geos:
        if name_contains_rigname_terms(rigname, geo, terms=r'mesh|geo|geometry'):
            bind_skincluster(joints, geo, f"{rigname}_skinCluster")
            return
    logger.info(f"Geo named '{rigname}' not found.")


# OPM & TRANSFORMS =====================================================

def opm(node):
    '''
    Move transform values to Offset Parent Matrix.
    Node must be transform or joint type and have attributes unlocked.
    '''
    if has_non_default_locked_attributes(node):
        logger.error('Node {node} has at least one non default locked attribute(s)')

    local_matrix = om.MMatrix(cmds.xform(node, q=1, m=1, os=1))
    offset_parent_matrix = om.MMatrix(cmds.getAttr(f"{node}.offsetParentMatrix"))
    baked_matrix = local_matrix * offset_parent_matrix
    cmds.setAttr(f"{node}.offsetParentMatrix", baked_matrix, typ='matrix')
    reset_transforms(node)

def has_non_default_locked_attributes(node, attrcheck=None):
    '''
    Check whether node has locked non-default attributes.
    '''
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

def disconnect_all(plug, source=True, destination=False):
    '''
    Disconnect all plug connections from source or to destination.
    Note that default option is True for both.
    '''
    connections = list()

    if source:
        source_connections = cmds.listConnections(plug, p=1, c=1, d=0)
        if source_connections:
            connections.extend(zip(source_connections[1::2], source_connections[::2]))

    if destination:
        dest_connections = cmds.listConnections(plug, p=1, c=1, s=0)
        if dest_connections:
            connections.extend(zip(dest_connections[::2], dest_connections[1::2]))

    for source_attr, dest_attr in connections:
        logger.debug(f"src:'{source_attr}' dst:'{dest_attr}'")
        cmds.setAttr(source_attr, l=0) # Unlock attribute
        cmds.disconnectAttr(source_attr, dest_attr)

def break_connection(plug):
    '''
    Break plug connection.
    '''
    cmds.setAttr(plug, l=0) # Unlock
    if cmds.connectionInfo(plug, id=True):
        plug = cmds.connectionInfo(plug, ged=True)
        readonly = cmds.ls(plug, ro=True)
        if readonly:
            source = cmds.connectionInfo(plug, sfd=True)
            cmds.disconnectAttr(source, plug)
        else:
            cmds.delete(plug, icn=True)

def reset_transforms(node, unlock=True):
    '''
    Reset attributes translate, rotate, scale, shear, jointOrient.
    If unlock=True, unlock attributes and break input connections.
    makeIdentity except also unlocks attributes.
    '''
    for attribute in ['translate', 'rotate', 'scale', 'shear', 'jointOrient']:
        default_value = 1 if attribute == "scale" else 0
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=node, ex=1):
                plug = f"{node}.{attribute}{axis}"
                if unlock:
                    break_connection(plug)
                if not cmds.getAttr(plug, lock=True):
                    cmds.setAttr(plug, default_value)

def reset_opm(node, unlock=True):
    '''
    Reset offset parent matrix.
    If unlock=True, unlock opm and break input connections.
    '''
    identity_mtx = [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
    opm_attr = f"{node}.offsetParentMatrix"
    if unlock:
        break_connection(opm_attr)
    if not cmds.getAttr(opm_attr, lock=True):
        cmds.setAttr(opm_attr, *identity_mtx, type='matrix')

def match_transform(source, target, pos=False, rot=False, scl=False, moc=False, unlock=True):
    '''
    Match transforms from source to target.
    Matches arguments set to True among pos(position), rot(rotation), scl(scale).
    Otherwise matches all pos,rot,scl if left as False by default.
    If moc=True, keep children in their positions (maintain offset for children).
    If unlock=True, unlock attributes and break input connections.
    '''
    # Apply match transform - Helper function
    def apply_transform(source, target, pos, rot, scl):
        if not (pos or rot or scl):
            cmds.matchTransform(source, target)
        else:
            cmds.matchTransform(source, target, pos=pos, rot=rot, scl=scl)

    logger.debug(f"'{source}'->'{target}'")
    if unlock: # Unlock source
        disconnect_all(source, source=True)
    if moc:
        src_children = cmds.listRelatives(source, typ='transform') or []
        # Create temporary group to preserve children during transform
        tmp_grp = cmds.group(em=True, n=f"{source}_tmp")
        # Match transform to tmp group
        apply_transform(tmp_grp, target, pos, rot, scl)

        # Parent children to tmp group to maintain their offsets
        for child in src_children:
            if unlock and 'Constraint' not in child:
                disconnect_all(child, source=True)
            cmds.parent(child, tmp_grp, a=1) # Absolute parent

        # Match transform to tmp group
        apply_transform(tmp_grp, target, pos, rot, scl)
        opm(source)

        # Re-parent children back to original source
        for child in src_children:
            cmds.parent(child, source, a=1)
            if cmds.objectType(child, i='joint'):
                # Transform created by re-parenting
                transf = cmds.listRelatives(child, p=True, typ='transform')[0]
                if 'transform' in transf:
                    cmds.ungroup(transf)
            opm(child)
        cmds.delete(tmp_grp)

    else: # Apply transform directly
        apply_transform(source, target, pos, rot, scl)
        opm(source)


# POSITION & VECTOR ====================================================

def get_local_pos(node):
    return cmds.xform(node, q=1, os=1, t=1)

def get_world_pos(node):
    return cmds.xform(node, q=1, ws=1, t=1)

def get_local_vec_to_worldspace(node, vec=om.MVector.kXaxisVector):
    matrix = om.MGlobal.getSelectionListByName(node).getDagPath(0).inclusiveMatrix()
    vec = (vec * matrix).normal()
    return vec

def get_local_vec(start_node, end_node):
    start = om.MVector(get_world_pos(start_node))
    end = om.MVector(get_world_pos(end_node))
    vec = om.MVector(end-start).normal()
    return vec

def get_vec_length(start_node, end_node):
    start = om.MVector(get_world_pos(start_node))
    end = om.MVector(get_world_pos(end_node))
    length = om.MVector(end-start).length()
    return length

def axis_vector_colinearity(node, vec):
    vec = om.MVector(vec)
    x = vec * get_local_vec_to_worldspace(node, vec=om.MVector.kXaxisVector)
    y = vec * get_local_vec_to_worldspace(node, vec=om.MVector.kYaxisVector)
    z = vec * get_local_vec_to_worldspace(node, vec=om.MVector.kZaxisVector)

    maxi = max(x,y,z)
    if maxi == x:
        return 'x'
    elif maxi == y:
        return 'y'
    elif maxi == z:
        return 'z'
    else:
        logger.error('Failed to compute axis vector colinearity.')


# CREATE NODES =========================================================

def create_group(node):
    if cmds.objExists(node):
        set_group_visibility(node)
        return node
    else:
        logger.debug(f"Create group '{node}'")
        group = cmds.createNode('transform', n=node, s=1, ss=1)
        set_group_visibility(group)
        return group

def create_condition(node, firstTerm=None, secondTerm=0, op=0):
    '''
    Create condition node.

    Arguments
        node (str): condition node name
        firstTerm (str): firstTerm value, input connection
        secondTerm (float): secondTerm value
        op (int): operation of comparison
                  {0:'equal', 1:'not equal',
                   2:'greater than', 3='greater or equal',
                   4:'less than', 5:'less or equal'}
    '''
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
    '''
    Create condition node with On/Off values to multiple nodes.
    Can use multiple times on same driveattr.

    Arguments
        driveattr (str): firstTerm value, driver attribute 'node.attr'
        drivenattrs (str list): list of attributes [obj1.attr, obj2.attr, etc]
        node (str): condition node name
        secondTerm (float): secondTerm value for On state
    '''
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

def create_curveinfo(rigname, curve, typ=''):
    curveinfo = fstr(rigname, CURVEINFO, typ) # curveInfo
    if cmds.objExists(curveinfo): # Return curveInfo if it exists
        return curveinfo
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0] # Curve shape
    connections = cmds.listConnections(f"{crvshape}.worldSpace[0]") or []
    # Check if curveInfo exists under different name
    bool_create_curveinfo = True
    for cnt in connections:
        if cmds.nodeType(cnt) == 'curveInfo':
            cmds.rename(cnt, curveinfo)
            bool_create_curveinfo = False
    # Otherwise create new curveInfo node
    if bool_create_curveinfo:
        # Create curveInfo to measure length of curve
        cmds.createNode('curveInfo', n=curveinfo, s=1, ss=1)
        cmds.connectAttr(f"{crvshape}.worldSpace[0]", f"{curveinfo}.inputCurve", f=1)
    return curveinfo


# ADD ATTRIBUTES =======================================================

def add_attribute_enum(plug, ln, nn, en=None, dv=0, pxy=None):
    '''
    Helper function to add Enum attribute.
    Query if attribute exists, and edit existing attribute or create new attribute.
    Attribute Template: (longName, niceName, enumName, dv)

    Arguments
        plug (str): node plug; e.g. control.attribute
        ln (str): attribute long name
        nn (str): attribute nice name
        en (str): enum name
        pxy (str): proxy

    Examples
        add_attribute_enum(basectrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(basectrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f"{cog_ctrl}.{ikfk_switch}")
    '''
    logger.debug(f"plug:'{plug}' ln:'{ln}' nn:'{nn}' en:'{en}' pxy:'{pxy}'")
    re_divider = re.search(r'(?i)[^-_\s]+(?=[-_\s]*divider)', ln)
    if '.' in plug: # Plug with attribute
        node, node_attr = node.split('.', 1)
    else:
        node, node_attr = plug, ln
        plug = f"{node}.{ln}"
    if cmds.attributeQuery(node_attr, n=node, ex=1): # Attribute exists
        if node_attr != ln: # Change attribute name
            cmds.setAttr(plug, l=0) # Unlock attribute
            cmds.renameAttr(plug, ln)
            plug = f"{node}.{ln}"
        if re_divider: # Divider attribute
            if en:
                cmds.addAttr(plug, nn=nn, at='enum', e=1, en=en)
            else:
                cmds.addAttr(plug, nn='----------', at='enum', e=1, en=nn)
            cmds.setAttr(plug, cb=1, l=1)
        elif pxy: # Proxy attribute
            cmds.deleteAttr(plug)
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', pxy=pxy, k=1)
        elif en: # Custom Enum
            cmds.addAttr(plug, nn=nn, at='enum', e=1, en=en, dv=dv, k=1)
        else:
            cmds.addAttr(plug, nn=nn, at='enum', e=1, en='Hide:Show', dv=dv, k=1)
        logger.debug('edited attribute {plug}')
    else: # Attribute doesn't exist
        if re_divider: # Divider attribute
            if en:
                cmds.addAttr(node, ln=ln, nn=nn, at='enum', en=en, k=1)
            else:
                cmds.addAttr(node, ln=ln, nn='----------', at='enum', en=nn, k=1)
            cmds.setAttr(f"{node}.{ln}", cb=1, l=1)
        elif pxy: # Proxy attribute
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', pxy=pxy, k=1)
        elif en: # Custom Enum
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', en=en, dv=dv, k=1)
        else: # Default Enum
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', en='Hide:Show', dv=dv, k=1)
        logger.debug(f"added attribute {node}.{ln}")


# VISIBILITY ===========================================================

def set_visibility(node, value, k=1, cb=1, l=0):
    '''
    Set Visibility on/off and show/hide or lock attribute.
    '''
    if not cmds.objExists(node):
        logger.error(f"'{node}' does not exist.")
    elif not cmds.attributeQuery('visibility', n=node, ex=1):
        logger.error(f"'{node}.visibility' does not exist.")
    cmds.setAttr(f"{node}.visibility", l=0) # Unlock
    cmds.setAttr(f"{node}.visibility", value)
    cmds.setAttr(f"{node}.visibility", k=k, cb=cb, l=l)

def set_transform_visibility(node, k=1, cb=1, l=0):
    '''
    Translate, Rotate, Scale show/hide or lock attribute.
    '''
    for attribute in ['translate', 'rotate', 'scale']:
        for axis in 'XYZ':
            if cmds.attributeQuery(f"{attribute}{axis}", n=node, ex=1):
                cmds.setAttr(f"{node}.{attribute}{axis}", k=k, cb=cb, l=l)

def set_control_visibility(fk, ik):
    '''
    Get all controls and set visibility keyable.
    '''
    for control in get_controls_all(fk, ik, bn=False, include_cog=True):
        set_visibility(control, 1, k=1, cb=0, l=0) # Unlock and keyable

def set_curve_visibility(curve):
    '''
    Set curve visibility nonkeyable, attributes nonkeyable.
    '''
    set_transform_visibility(curve, k=0, cb=0, l=1) # Lock and hide transforms
    set_visibility(curve, 1, k=1, cb=0, l=0) # Unlock and keyable

def set_group_visibility(group):
    '''
    Set group visibility nonkeyable, hide attributes.
    '''
    set_transform_visibility(group, k=0, cb=0, l=1) # Lock and hide
    set_visibility(group, 1, k=0, cb=1, l=0) # Unlock and show cb


# JOINT FUNCTIONS ======================================================

def get_joint_position_from_list(joints):
    '''
    Get list of joint names and joint positions

    Arguments
        jnt_list (list): List of joints
    '''
    jnt_pos = list()
    for jnt in joints:
        jnt_pos.append(get_world_pos(jnt))
    return jnt_pos

def is_equal_joint(joint1, joint2, tolerance=0.1):
    '''
    Check if two joints are equal in translation and rotation
    Return (bool): True if joints are equal within tolerance
    '''
    if not cmds.objExists(joint1):
        logger.error(f"joint '{joint1}' does not exist")
    if not cmds.objExists(joint2):
        logger.error(f"joint '{joint2}' does not exist")

    # Get translation
    jnt1_tr = cmds.xform(joint1, q=1, ws=1, t=1)
    jnt2_tr = cmds.xform(joint2, q=1, ws=1, t=1)
    # Get rotation
    jnt1_ro = cmds.xform(joint1, q=1, ws=1, ro=1)
    jnt2_ro = cmds.xform(joint2, q=1, ws=1, ro=1)
    logger.debug(f"{joint1} tr{jnt1_tr} ro{jnt1_ro}")
    logger.debug(f"{joint2} tr{jnt2_tr} ro{jnt2_ro}")

    if not all(abs(t1-t2) < tolerance for t1,t2 in zip(jnt1_tr, jnt2_tr)):
        return False
    if not all(abs(r1-r2) < tolerance for r1,r2 in zip(jnt1_ro, jnt2_ro)):
        return False
    return True

def set_joint_attributes(joints):
    '''
    Label joint positions.
    Add attribute to each joint labeling their V value.
    Surface V value ranges from 0 to 1 along ribbon's length.
    '''
    logger.info('Labeling joint positions')
    if not joints:
        logger.error('No joints provided')
    value = list()
    end = joints[-1]
    fullv = get_vec_length(joints[0], end)
    for jnt in joints:
        length = get_vec_length(jnt, end)
        logger.debug(f"joint '{jnt}' length {length} fullv {fullv}")
        if length == 0:
            v = 0
        elif fullv == 0:
            logger.error(f"joint '{jnt}' - length {length} fullv {fullv}")
        else:
            v = length/fullv
        value.append(v)

        if cmds.attributeQuery('joint_pos', n=jnt, ex=1):
            cmds.setAttr(f"{jnt}.joint_pos", l=0) # Unlock
            cmds.addAttr(f"{jnt}.joint_pos", e=1, dv=v)
            cmds.setAttr(f"{jnt}.joint_pos", cb=1, l=1) # Channel box, lock
        else:
            cmds.addAttr(jnt, ln='joint_pos', nn='Joint Pos', at='float',
                min=0, max=1, h=False, dv=v)
            cmds.setAttr(f"{jnt}.joint_pos", cb=1, l=1)

# UNUSED ===============================================================
# Make sure fk joints match BN joints and rotations are zero
#reset_joint_rotations(JOINTS_FK[rigname])
#reset_fk_joints(rigname, JOINTS_FK[rigname])

def match_orient(source, target):
    '''
    Match transforms to target but keep orientation of shapes.
    Also keep children of source.
    Result is actually a new transform with original shapes.
    '''
    logger.debug(f"'{source}'->'{target}'")
    # Create temporary group to keep shapes
    tmp_grp = cmds.group(em=True, n=f"{source}_tmp")
    # Match tmp_grp to target
    match_transform(tmp_grp, target, moc=True)
    # Move shapes to tmp group
    shapes = cmds.listRelatives(source, s=True)
    for shp in shapes:
        cmds.parent(shp, tmp_grp, a=True, s=True)
        # Transform created by parenting
        transf = cmds.listRelatives(shp, p=True, typ='transform')[0]
        cmds.makeIdentity(transf, apply=1, t=1, r=1, s=1, jo=1)
    # Match source to target
    match_transform(source, target, moc=True)
    # Move shapes back to source
    for shp in shapes:
        cmds.parent(shp, source, r=True, s=True)
    cmds.delete(tmp_grp)

def reset_joint_rotations(joints):
    '''
    Make joint rotations zero.
    '''
    logger.info('Reset FK joints..')
    for jnt in joints:
        disconnect_all(jnt, source=True)
        children = cmds.listRelatives(jnt) or []
        for child in children:
            cmds.parent(child, w=1) # Unparent
        reset_opm(jnt)
        cmds.makeIdentity(jnt, r=True, jo=True)
        for child in children:
            cmds.parent(child, jnt, a=1) # Re-parent
            transf = cmds.listRelatives(child, p=True, typ='transform')[0]
            if 'transform' in transf:
                cmds.ungroup(transf)

def reset_fk_joints(rigname, joints, typ=TYPE_BN):
    '''
    Match FK joints to BN joints.
    Make joint rotations zero.
    '''
    logger.info('Reset FK joints')
    for fk_jnt in joints:
        NN = get_index_from_name(fk_jnt)
        jnt = fstr(rigname, JOINT, typ, NN)
        logger.debug(f"{fk_jnt} -> {jnt}")
        disconnect_all(fk_jnt, source=True)
        fk_children = cmds.listRelatives(fk_jnt, typ='transform') or []
        tmp_grp = cmds.group(em=True, n=f"fk_jnt_tmp")
        cmds.matchTransform(tmp_grp, jnt)
        for child in fk_children:
            if not 'Constraint' in child:
                disconnect_all(child, source=True)
            cmds.parent(child, tmp_grp, a=1) # Absolute parent
        reset_opm(fk_jnt)
        cmds.matchTransform(fk_jnt, jnt)
        cmds.makeIdentity(fk_jnt, r=True, jo=True)
        for child in fk_children:
            cmds.parent(child, fk_jnt, a=1)
            if cmds.objectType(child, i='joint'):
                transf = cmds.listRelatives(child, p=True, typ='transform')[0]
                if 'transform' in transf:
                    cmds.ungroup(transf)
        cmds.delete(tmp_grp)
