'''
# rig_tail_util.py
author: Daisy Jane @dayzl

Helper functions for Rig Tail.
Focus: Transforms, connections, constraints, attributes, parenting, checking.

Functions:
    parent_to: Parent node to given parent
    is_parent: Check if node is already parent
    get_constraint: Get constraints on node
    list_hierarchy: Iterative traversal helper
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
    sdk: Create set driven key
    add_attribute_enum: Add enum attribute
    has_non_default_locked_attributes: Check for locked attributes
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
from rig_tail_constants import *
import rig_tail_constants as rt_cst
import math
import re

logger = logger_setup(__name__)


# CACHE VALIDATION =====================================================

def validate_cache():
    '''
    Clear cache if RIGPARTS or ROOT changed since last build.
    '''
    # Check if RIGPARTS changed
    if set(RIGPARTS) != set(rt_cst.LAST_BUILD['rigparts']):
        removed = set(rt_cst.LAST_BUILD['rigparts']) - set(RIGPARTS)
        added = set(RIGPARTS) - set(rt_cst.LAST_BUILD['rigparts'])

        # Clear data for removed rigparts
        for rigname in removed:
            rt_cst.JOINTS_FK.pop(rigname, None)
            rt_cst.JOINTS_IK.pop(rigname, None)
            rt_cst.JOINTS_BN.pop(rigname, None)

        logger.info(f'RIGPARTS changed. Removed: {removed}, Added: {added}')
        rt_cst.LAST_BUILD['rigparts'] = RIGPARTS.copy()

    # Check if ROOT changed
    if ROOT != rt_cst.LAST_BUILD['root']:
        logger.info(f"ROOT changed: '{rt_cst.LAST_BUILD['root']}' -> '{ROOT}'")
        rt_cst.LAST_BUILD['root'] = ROOT

def validate_cache_joints(rigname):
    '''
    Check if cached joints still exist and match scene.
    Return True if joints changed (full rebuild needed).
    '''
    if rigname not in rt_cst.JOINTS_BN:
        return True # No cache, need rebuild

    # Check if cached joints exist in scene
    for joint_dict in [rt_cst.JOINTS_BN, rt_cst.JOINTS_FK, rt_cst.JOINTS_IK]:
        if rigname in joint_dict:
            for jnt in joint_dict[rigname]:
                if not cmds.objExists(jnt):
                    logger.warning(f"Cached joint '{jnt}' no longer exists")
                    return True  # Joints changed

    # Compute hash of current joint positions
    stored_hash = rt_cst.LAST_BUILD.get('joints_hash', {}).get(rigname)
    current_hash = hash(tuple(
        tuple(cmds.xform(j, q=1, ws=1, t=1))
        for j in rt_cst.JOINTS_BN[rigname]
    ))

    if current_hash != stored_hash:
        logger.info(f'{rigname}: Joint positions changed')
        rt_cst.LAST_BUILD.setdefault('joints_hash', {})[rigname] = current_hash
        return True

    return False  # Joints unchanged


# NAMING ===============================================================

def fstr(rigname, template, TYPE='', NN='', nn='', TAG=''):
    '''
    Evaluate fstring template.
    Naming Convention follows template.
    '''
    if '{ROOT}' in template:
        template = template.replace('{ROOT}', rt_cst.ROOT)
    if NN!='' and NN!='ee':
        NN = f"{DFORMAT.format(int(NN))}"
    if nn!='' and nn!='ee':
        nn = f"{DFORMAT.format(int(nn))}"
    name_eval = eval(f"f'''{template}'''")
    # Clean double / leading / trailing underscores
    parts = name_eval.split('_')
    name = '_'.join([p for p in parts if p])
    return name

def get_rigname(node, template):
    '''
    Get rigname from node, provided a naming template.
    Node name must follow the naming convention from template.
    Example
        jnt = 'FK_L_tail3_00_jnt' # node
        template = '{TYPE}{rigname}_{NN:02d}{JNT}'
        get_rigname(jnt, template) = 'L_tail3'
    '''
    if '{rigname}' not in template:
        logger.warning('Invalid naming template. Ensure template includes {rigname}.')
        return None

    regex = compile_template_to_regex(template)
    m = regex.match(node)
    return m.group('rigname') if m else None

def compile_template_to_regex(template):
    # Break into tokens: {placeholder} or literal text
    tokens = re.findall(r'\{[^}]+\}|[^{}]+', template)
    parts = []

    for tok in tokens:
        if tok.startswith('{') and tok.endswith('}'):
            raw = tok[1:-1]
            leading, name, trailing = parse_placeholder(raw)

            # Insert leading literal
            if leading:
                parts.append(re.escape(leading))

            # Capture rigname, wildcard everything else
            if name == 'rigname':
                parts.append(r'(?P<rigname>[^_]+)')
            else:
                parts.append(r'.+?')

            # Insert trailing literal
            if trailing:
                parts.append(re.escape(trailing))

        else:
            # Literal text outside placeholders
            parts.append(re.escape(tok))

    pattern = ''.join(parts)
    return re.compile(pattern + r'\Z')

def parse_placeholder(raw):
    '''
    raw = content inside { ... }
    returns (leading_literal, name, trailing_literal)
    '''
    # Strip formatting, e.g. NN_:02d → NN_
    raw_no_fmt = raw.split(':')[0]

    # Leading literal = non-alphanumeric prefix
    m = re.match(r'(\W+)([\w\W]+)', raw_no_fmt)
    if m:
        leading, rest = m.groups()
    else:
        leading = ''
        rest = raw_no_fmt

    # Trailing literal = non-alphanumeric suffix
    m = re.match(r'([A-Za-z0-9]+)(\W+)$', rest)
    if m:
        name, trailing = m.groups()
    else:
        name = rest
        trailing = ''

    return leading, name, trailing

def get_index_from_name(node, first=False, underscore=True):
    '''
    Extract a numerical index (int) from a node name.
    Return None if index not found.

    Arguments
        node (str): Node name
        first (bool): Search first/last
                If first=True:
                    Return the first valid index.
                Else:
                    Return the last valid index.
        underscore (bool): Index preceded by whitespace
                If underscore=True:
                    The index must be either:
                        - at the start of the string, OR
                        - preceded by whitespace, underscore, or dash
                    Examples that match:
                        '01_tail_jnt'    -> 01 (start of string)
                        'R_tail_01_jnt'  -> 01 (preceded by '_')
                        'R tail 01 jnt'  -> 01 (preceded by whitespace)
                        'R-tail-01-jnt'  -> 01 (preceded by '-')
                    Examples that do NOT match:
                        'Rtail01_jnt'    -> none (preceded by 'l')
                        'ctrl01'         -> none (preceded by 'l')
                If underscore=False:
                    Any digit sequence is accepted.
    '''
    if underscore:
        pattern = (
            r'(?:(?<=\s)(?:\d+|ee)|'
            r'(?<=_)(?:\d+|ee)|'
            r'(?<=-)(?:\d+|ee)|'
            r'^(?:\d+|ee))'
        )
    else:
        pattern = r'(?:\d+|ee'

    if first:
        m = re.search(pattern, node)
        if not m:
            return None
        token = m.group()
        return token if token=='ee' else int(token)

    matches = re.findall(pattern, node)
    if not matches:
        return None
    token = matches[-1]
    return token if token=='ee' else int(token)

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


# GENERAL UTIL =========================================================

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
        nodes (list): List of objects
    Return
        up_axis (str): '+x', '+y', or '+z'
            Preferred up-axis based on shortest edge of bounding box

    if Up Axis (secondary_axis=False):
        The longest edge of a bounding box is the up-axis
    if Secondary Axis (secondary_axis=True):
        If the shortest edge is 'y', then return 'z' else 'y'
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

def get_local_orientation(nodes):
    '''
    Determine which local axis the joint chain extends along.
    Compares first joint's aim to second joint.

    Arguments
        nodes (list): List of objects

    Return
        str: '+x', '+y', '+z', '-x', '-y', or '-z'
    '''
    if len(nodes) < 2:
        logger.warning('Need at least 2 nodes to determine chain direction')
        return '+x'

    # Get world positions
    pos1 = cmds.xform(nodes[0], q=1, ws=1, t=1)
    pos2 = cmds.xform(nodes[1], q=1, ws=1, t=1)

    # Get world space direction vector
    world_vec = [pos2[i] - pos1[i] for i in range(3)]

    # Get joint's world matrix
    matrix = cmds.xform(nodes[0], q=1, ws=1, m=1)

    # Extract local axes from matrix (each axis is 3 values)
    local_x = [matrix[0], matrix[1], matrix[2]]
    local_y = [matrix[4], matrix[5], matrix[6]]
    local_z = [matrix[8], matrix[9], matrix[10]]

    # Normalize world_vec
    mag = math.sqrt(sum(v*v for v in world_vec))
    if mag > 0:
        world_vec = [v/mag for v in world_vec]

    # Dot product to find which local axis aligns with world direction
    def dot(a, b):
        return sum(a[i]*b[i] for i in range(3))

    dot_x = dot(world_vec, local_x)
    dot_y = dot(world_vec, local_y)
    dot_z = dot(world_vec, local_z)

    # Find max alignment
    dots = {'x': dot_x, 'y': dot_y, 'z': dot_z}
    max_axis = max(dots, key=lambda k: abs(dots[k]))
    sign = '+' if dots[max_axis] > 0 else '-'

    result = f'{sign}{max_axis}'
    logger.debug(f'Joint chain direction: {result}')
    return result


# PARENT ===============================================================

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


# SCENE QUERIES (Helpers) ==============================================

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

    if source:
        if attrs:
            for attr in attrs:
                conns = cmds.listConnections(f'{node}.{attr}', s=True, d=False, p=True, c=True) or []
                for i in range(0, len(conns), 2):
                    cmds.disconnectAttr(conns[i + 1], conns[i])
        else:
            conns = cmds.listConnections(node, s=True, d=False, p=True, c=True) or []
            for i in range(0, len(conns), 2):
                cmds.disconnectAttr(conns[i + 1], conns[i])

    if destination:
        if attrs:
            for attr in attrs:
                conns = cmds.listConnections(f'{node}.{attr}', s=False, d=True, p=True, c=True) or []
                for i in range(0, len(conns), 2):
                    cmds.disconnectAttr(conns[i], conns[i + 1])
        else:
            conns = cmds.listConnections(node, s=False, d=True, p=True, c=True) or []
            for i in range(0, len(conns), 2):
                cmds.disconnectAttr(conns[i], conns[i + 1])


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
        logger.error(f'Node {node} has at least one non default locked attribute(s)')

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

    logger.debug(f"'{source}'->'{target}'")
    if unlock:
        disconnect_all(source, source=True)
    if moc:
        src_children = cmds.listRelatives(source, typ='transform') or []
        tmp_grp = cmds.group(em=True, n=f"{source}_tmp")
        apply_transform(tmp_grp, target, pos, rot, scl)

        for child in src_children:
            if unlock and 'Constraint' not in child:
                disconnect_all(child, source=True)
            cmds.parent(child, tmp_grp, a=1)

        apply_transform(tmp_grp, target, pos, rot, scl)
        opm(source)

        for child in src_children:
            cmds.parent(child, source, a=1)
            if cmds.objectType(child, i='joint'):
                transf = cmds.listRelatives(child, p=True, typ='transform')[0]
                if 'transform' in transf:
                    cmds.ungroup(transf)
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

def add_attribute_enum(plug, ln, nn, en=None, dv=0, pxy=None):
    """
    Add Enum attribute to node.

    Arguments:
        plug (str): Node plug (e.g., control.attribute)
        ln (str): Attribute long name
        nn (str): Attribute nice name
        en (str): Enum name options
        dv (int): Default value
        pxy (str): Proxy attribute
    """
    logger.debug(f"plug:'{plug}' ln:'{ln}' nn:'{nn}' en:'{en}' pxy:'{pxy}'")
    re_divider = re.search(r'(?i)[^-_\s]+(?=[-_\s]*divider)', ln)
    if '.' in plug:
        node, node_attr = plug.split('.', 1)
    else:
        node, node_attr = plug, ln
        plug = f"{node}.{ln}"
    if cmds.attributeQuery(node_attr, n=node, ex=1):
        if node_attr != ln:
            cmds.setAttr(plug, l=0)
            cmds.renameAttr(plug, ln)
            plug = f"{node}.{ln}"
        if re_divider:
            if en:
                cmds.addAttr(plug, nn=nn, at='enum', e=1, en=en)
            else:
                cmds.addAttr(plug, nn='----------', at='enum', e=1, en=nn)
            cmds.setAttr(plug, cb=1, l=1)
        elif pxy:
            cmds.deleteAttr(plug)
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', pxy=pxy, k=1)
        elif en:
            cmds.addAttr(plug, nn=nn, at='enum', e=1, en=en, dv=dv, k=1)
        else:
            cmds.addAttr(plug, nn=nn, at='enum', e=1, en='Hide:Show', dv=dv, k=1)
        logger.debug(f'edited attribute {plug}')
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
        logger.debug(f"added attribute {node}.{ln}")




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


# JOINT UTIL ===========================================================

def get_joint_chain(start, end=None):
    chain = [start]
    j = start
    while True:
        children = cmds.listRelatives(j, typ='joint', c=True) or []
        if not children:
            break
        j = children[0]
        if '_ee_' in j:
            return chain
        chain.append(j)
        if j == end:
            return chain

def get_joint_hierarchy(start_jnt, end_jnt=None):
    '''
    Return joints in deterministic DAG order (parent before child).
    '''
    dag = cmds.ls(start_jnt, dag=True, type='joint')
    if not dag:
        return []

    joints = []
    for j in dag:
        if '_ee_' in j:
            break
        joints.append(j)
        if end_jnt and j == end_jnt:
            break
    return joints

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
    logger.debug('Label joint positions')
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
    logger.debug('Reset FK joint rotations')
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
    logger.debug('Reset FK joints to match BN joints')
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
