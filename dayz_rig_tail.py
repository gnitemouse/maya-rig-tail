'''
# dayz_rig_tail.py
author: Daisy Jane Lee @dayzl

Use script to rig variable FK tail.
Rotate FK controls to move the tail.
Use Position attribute to move FK controls along curve.
Use Falloff attribute to adjust the range of joints affected.

Description
    Provided one or more FK joint chains,
    creates and binds NURBS curves,
    creates matching FK controls (N=3 by default),
    and pads FK joint chains with SDK groups
    which are used to slide the controls along the curve.

Credits
- Variable FK based on elephant trunk rig by Jeff Brodsky (vimeo.com/72424469)
- Test world colinearity by Chris Evans
  (http://www.chrisevans3d.com/pub_blog/maya-python-vector-math-primer/)

Run in Maya Script Editor (Python):
import dayz_rig_tail as rt
import importlib as il
il.reload(rt)
rt.main()

By default,
Assumes the following structure or naming convention:
    controls                       (CONTROL_GRP)
      - {rigname}_ctrl_grp         (BASECTRL_GRP)
      -- {rigname}_base_ctrl       (BASECTRL)
      --- FK_{rigname}_root_grp    (CTRLROOT_GRP)
      ---- {rigname}_##_ctrl_grp   (CTRL_GRP)
      ----- {rigname}_##_ctrl      (CTRL)
      ---- etc.
    FK_skeleton                    (fk_SKELETON_GRP)
      - FK_{rigname}_grp           (fk_GRP)
      -- FK_{rigname}_##_01_sdk    (first_SDK_GRP)
      --- FK_{rigname}_##_02_sdk   ( ... SDK_GRP)
      ---- FK_{rigname}_##_02_sdk  (last_SDK_GRP)
      ----- FK_{rigname}_##_jnt    (JNT)
      ------ etc.
Naming convention can be changed under Naming Template.
Rig components {rigname}s can be changed under RIGPARTS.

'''

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import logging
import re

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False
cmds.scriptEditorInfo(sw=1) # Maya suppressWarnings

class ExitHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        if record.levelno in (logging.ERROR, logging.CRITICAL):
            raise SystemExit(-1)
if not logger.handlers:
    exit_handler = ExitHandler()
    fmt = logging.Formatter('%(levelname)s: %(funcName)s: %(message)s')
    exit_handler.setFormatter(fmt)
    logger.addHandler(exit_handler)

'''
CHECKLIST
- bind geo to BN joints
- scale FK and BN joints
- fixed length / non-stretchy
- UI
'''

# USER VARIABLES =======================================================

# Rig Components
# List all parts that will be rigged with a tail
# RIGPARTS = ['L_fintail', 'R_fintail', 'C_fintail',
#     'L_sidetail', 'R_sidetail',
#     'L_tail3', 'L_tail2', 'L_tail1', 'C_tail',
#     'R_tail1', 'R_tail2', 'R_tail3']
RIGPARTS = ['R_tail3']

# Joints Dict: rigname -> joint list
JOINTS_FK = dict()
JOINTS_IK = dict()
JOINTS_BN = dict()


# NAMING TEMPLATE ======================================================
# Change name templates as necessary.
# If you change formatting of indices(_NN),
# make sure to update DFORMAT and get_index_from_name()

# Decimal formatting for indices
DFORMAT = '{:02d}'

# Naming Template: Type Labels
TYPE_BN = 'BN_'
TYPE_IK = 'IK_'
TYPE_FK = 'FK_'
_GRP = '_grp'
_CTRL = '_ctrl'
_JNT = '_jnt'
_SDK = '_sdk'
_CRV = '_crv'
_CSR = '_cluster'
_HDL = 'Handle'
_EFF = '_effector'
_VIS = '_visibility'
_CST = '_constraint'
_CRVI = '_curveInfo'
_POCI = '_pointOnCurveInfo'
_COND = '_condition'

# Naming Template: tail
BASECTRL_GRP = '{TYPE}{rigname}_base{TAG}{_CTRL}{_GRP}'
BASECTRL = '{TYPE}{rigname}_base{TAG}{_CTRL}'
CTRLROOT_GRP = '{TYPE}{rigname}_root{TAG}{_GRP}'
CTRL_GRP = '{TYPE}{rigname}{TAG}{_NN}{_CTRL}{_GRP}'
CTRL = '{TYPE}{rigname}{TAG}{_NN}{_CTRL}'
GRP = '{TYPE}{rigname}{TAG}{_NN}{_GRP}'
JNT = '{TYPE}{rigname}{TAG}{_NN}{_JNT}'
SDK_GRP = '{TYPE}{rigname}{TAG}{_NN}{_nn}{_SDK}'
SDK_CTRL = '{TYPE}{rigname}{TAG}{_NN}{_CTRL}{_SDK}'

# Naming Template: curve, clusters
CURVE = '{TYPE}{rigname}{TAG}{_CRV}'
CURVE_SCALE = '{TYPE}{rigname}_scale{TAG}{_CRV}'
CURVEINFO = '{TYPE}{rigname}{TAG}{_CRVI}'
CLUSTER_GRP = '{TYPE}{rigname}{TAG}{_NN}{_CSR}{_GRP}'
CLUSTER = '{TYPE}{rigname}{TAG}{_NN}{_CSR}'
CLUSTER_HANDLE = '{TYPE}{rigname}{TAG}{_NN}{_CSR}{_HDL}'
# Upvec
UPV_CTRL = '{TYPE}{rigname}_upvec{TAG}{_CTRL}'
UPV_CTRLGRP = '{TYPE}{rigname}_upvec{TAG}{_CTRL}{_GRP}'
CLUSTER_UPV = '{TYPE}{rigname}_upvec{TAG}{_NN}{_CSR}'
CLUSTER_UPV_HANDLE = '{TYPE}{rigname}_upvec{TAG}{_NN}{_CSR}{_HDL}'

# Naming Template: spline
SPLINE_GRP = '{TYPE}{rigname}_spline{TAG}{_GRP}'
SPLINE_HANDLE = '{TYPE}{rigname}_spline{TAG}{_HDL}'
SPLINE_EFFECTOR = '{TYPE}{rigname}_spline{TAG}{_EFF}'
SCALE_GRP = '{TYPE}{rigname}_scale{TAG}{_GRP}'
# Spline Controls
SPLINE_IK_CTRL = '{TYPE}{rigname}_ik{TAG}{_NN}{_CTRL}'
SPLINE_FLOAT_CTRL = '{TYPE}{rigname}_float{TAG}{_NN}{_CTRL}'
SPLINE_BOT = '{TYPE}{rigname}_spline_bot{_CTRL}'
SPLINE_BOT_SML = '{TYPE}{rigname}_spline_bot_sml{_CTRL}'
SPLINE_MID_ROT = '{TYPE}{rigname}_spline_mid_rot{_CTRL}'
SPLINE_MID = '{TYPE}{rigname}_spline_mid{_CTRL}'
SPLINE_TOP_SML = '{TYPE}{rigname}_spline_top_sml{_CTRL}'
SPLINE_TOP = '{TYPE}{rigname}_spline_top{_CTRL}'
SPLINE_CONTROLS = [SPLINE_BOT, SPLINE_BOT_SML, SPLINE_MID,
                   SPLINE_TOP_SML, SPLINE_TOP, SPLINE_MID_ROT]

# Naming Template: groups
ROOT = ''
ROOT_GRP = '{ROOT}{TAG}{_GRP}'
ROOT_CTRL = '{ROOT}{TAG}{_CTRL}'
COG_CTRL = 'cog{TAG}{_CTRL}'
GEOMETRY_GRP = 'geometry{TAG}'
CONTROL_GRP = '{TYPE}controls{TAG}'
SKELETON_GRP = '{TYPE}skeleton{TAG}'
RIG_SYSTEMS_GRP = 'rig_systems{TAG}'
LOCATORS_GRP = 'locators{TAG}'
IKFK = '{rigname}{TAG}_ikfk'
IKFK_COND = '{rigname}_ikfk{TAG}_condition'
SCALE_MULT = '{rigname}_scale{TAG}{_NN}_multiplyDivide'
SCALE_COND = '{rigname}_scale{TAG}{_NN}_condition'

# Attribute Template: (longName, niceName, enumName, dv)
# Switch
HIER_SWITCH = ('hierarchySwitch', 'Hierarchy Switch', 'spine:fk:float:revFk', 0)
IKFK_SWITCH = ('ikfk_switch', 'IKFK Switch', 'SplineIK:IK:Float:FK', 0)
# Divider
IKFK_DIVIDER = ('ikfk_divider', '----------', 'IKFK')
SCALE_DIVIDER = ('scale_divider', '----------', 'JNT SCALE')
# Proxy Attributes
ATTR_PROXY = ['squash', 'stretch', 'twist', 'roll', 'offset']


# CONSTANTS ============================================================

# Number of controls
NUM_CTRL_FK = 3
NUM_CTRL_IK = 5

# Control Size
ROOT_CTRL_SZ = 30
COG_CTRL_SZ = 20
BASE_CTRL_SZ = 2
VARFK_CTRL_SZ = 1.2
FK_CTRL_SZ = 0.4
IK_CTRL_SZ = 1.0
SPLINE_UPV_SZ = 0.4
SPLINE_BOT_SZ = 1.4
SPLINE_BOT_SML_SZ = 0.8
SPLINE_MID_ROT_SZ = 1
SPLINE_MID_SZ = 1
SPLINE_TOP_SML_SZ = 0.6
SPLINE_TOP_SZ = 1
SPLINE_CONTROLS_SZ = [SPLINE_BOT_SZ, SPLINE_BOT_SML_SZ, SPLINE_MID_SZ,
                      SPLINE_TOP_SML_SZ, SPLINE_TOP_SZ, SPLINE_MID_ROT_SZ]

ROT_AXIS_DICT = {
    '+x': (0, -90, 90),
    '-x': (0, 90, 90),
    '+y': (0, 0, 0),
    '-y': (0, 180, 0),
    '+z': (90, 0, 0),
    '-z': (-90, 0, 0)
    }

# Coordinates used to create cube control
CUBE_CTRL_PTS = [\
    [-0.5, -0.5, -0.5], [-0.5, -0.5, 0.5], [-0.5, 0.5, 0.5], [-0.5, -0.5, 0.5],
    [0.5, -0.5, 0.5], [0.5, -0.5, -0.5], [-0.5, -0.5, -0.5], [-0.5, 0.5, -0.5],
    [-0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [0.5, -0.5, 0.5], [0.5, -0.5, -0.5],
    [0.5, 0.5, -0.5], [-0.5, 0.5, -0.5], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5]]
# Color override index for controls
COLOR_OVERRIDE = {\
    'black':1, 'darkgrey':2, 'lightgrey':3, 'darkred':4, 'darkblue':5,
    'neonblue':6, 'darkgreen':7, 'blueblack':8, 'magenta':9, 'brown':10,
    'darkbrown':11, 'red':12, 'neonred':13, 'neongreen':14, 'blue':15,
    'white':16, 'lightyellow':17, 'lightblue':18, 'lightgreen':19, 'lightpink':20,
    'lightorange':21, 'neonyellow':22, 'green':23, 'orange':24, 'yellow':25,
    'yellowgreen':26, 'lightgreen':27, 'cyan':28, 'darkcyan':29, 'purple':30,
    'pink':31 }


# NAMING UTILITY =======================================================

def fstr(rigname, template, TYPE='', NN='', nn='', TAG=''):
    '''
    Evaluate fstring template.
    Naming Convention follows template above.
    '''
    _NN = f'_{DFORMAT.format(int(NN))}' if NN!='' else ''
    _nn = f'_{DFORMAT.format(int(nn))}' if nn!='' else ''
    return eval(f'f"""{template}"""')

def get_rigname(node, template, underscore=True):
    '''
    Get rigname from node, provided a naming template.
    Node name must follow the naming convention from template.
    Example
        jnt = 'FK_L_tail3_00_jnt' # node
        FK_JNT = '{TYPE}{rigname}_{NN:02d}{_JNT}' # template
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


# SETUP ================================================================

def set_joints(rigname, start_jnt=None, end_jnt=None):
    '''
    Set start and end joints.
    Assume that joints follow Naming Template
    '''
    global JOINTS_FK, JOINTS_IK, JOINTS_BN
    logger.info(f"{rigname} - start_jnt:'{start_jnt}' end_jnt:'{end_jnt}'")

    if not start_jnt: # Assume type is FK joint if start_jnt not provided
        start_jnt = fstr(rigname, JNT, TYPE_FK, NN=0)
        jnt_type = TYPE_FK
        logger.info(f"start_jnt not provided. Assume FK start_jnt is '{start_jnt}'")
    else: # Get TYPE
        if TYPE_FK in start_jnt:
            jnt_type = TYPE_FK
        elif TYPE_IK in start_jnt:
            jnt_type = TYPE_IK
        elif TYPE_BN in start_jnt:
            jnt_type = TYPE_BN
        else:
            logger.error(f"Could not determine TYPE of start_jnt '{start_jnt}'.\n"\
                         'Make sure that Type Labels are set correctly.')

    if start_jnt and not cmds.objExists(start_jnt):
        logger.error(f"start_jnt '{start_jnt}' does not exist.\n"\
                'Please select start joint.')
    if end_jnt and not cmds.objExists(end_jnt):
        logger.error(f"end_jnt '{end_jnt}' does not exist.\n"\
                'Please select end joint.')

    joints = get_joint_hierarchy(start_jnt, end_jnt)
    JOINTS_FK[rigname] = rename_joints(rigname, joints, TYPE_FK)
    JOINTS_IK[rigname] = rename_joints(rigname, joints, TYPE_IK)
    JOINTS_BN[rigname] = rename_joints(rigname, joints, TYPE_BN)
    logger.debug(f"JOINTS_FK[{rigname}] = {JOINTS_FK[rigname]}")
    logger.debug(f"JOINTS_IK[{rigname}] = {JOINTS_IK[rigname]}")
    logger.debug(f"JOINTS_BN[{rigname}] = {JOINTS_BN[rigname]}")

def set_root(new_root):
    global ROOT
    if new_root:
        ROOT = new_root.split(_GRP, 1)[0]
        root_grp = fstr('', ROOT_GRP)
        logger.info(f"Set ROOT '{ROOT}', root_grp '{root_grp}'")
        if cmds.objExists(new_root):
            if new_root != root_grp:
                cmds.rename(new_root, root_grp)
    else:
        logger.error(f"root '{new_root}' does not exist.")

def setup_rig_components(fk, ik):
    '''
    Create groups, root control, cog control.
    Connect root and cog.
    Rename components for IK if necessary.
    Make sure that RIGPARTS is set.
    '''
    logger.info('Setup rig components')
    root_grp = fstr('', ROOT_GRP)
    root_ctrl = fstr('', ROOT_CTRL)
    cog_ctrl = fstr('', COG_CTRL)
    geometry_grp = fstr('', GEOMETRY_GRP)
    control_grp = fstr('', CONTROL_GRP)
    skeleton_grp = fstr('', SKELETON_GRP)
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)
    locators_grp = fstr('', LOCATORS_GRP)

    if fk and not ik:
        groups = [geometry_grp, control_grp, skeleton_grp, rig_systems_grp, locators_grp]
    else:
        fk_skeleton_grp = fstr('', SKELETON_GRP, TYPE_FK)
        ik_skeleton_grp = fstr('', SKELETON_GRP, TYPE_IK)
        groups = [geometry_grp, control_grp, skeleton_grp,
                  fk_skeleton_grp, ik_skeleton_grp,
                  rig_systems_grp, locators_grp]

    # Root group must exist
    create_group(root_grp)
    # Create groups
    for group in groups:
        if group: # Skip empty group names
            if not cmds.objExists(group):
                group = cmds.group(em=True, n=group)
                logger.debug(f'created group {group}')
                if group == geometry_grp:
                    meshes = get_geometry_from_scene()
                    for geo in meshes:
                        unbind_skin(geo, typ='geo')
                        parent_to(geo, geometry_grp)
                elif group == control_grp:
                    controls = get_controls_from_scene()
                    for ctrl in controls:
                        parent_to(ctrl, control_grp)
                elif group == skeleton_grp:
                    joints = get_joints_from_scene()
                    for joint in joints:
                        parent_to(joint, skeleton_grp)
            parent_to(group, root_grp)

    if ik:
        rename_components() # Replace names
    if not cmds.objExists(root_ctrl): # Create root_ctrl
        create_circle_control(root_ctrl, ROOT_CTRL_SZ, nr=(0,1,0), color='lightgreen')
        parent_to(root_ctrl, control_grp)
    if not cmds.objExists(cog_ctrl): # Create cog_ctrl
        create_circle_control(cog_ctrl, COG_CTRL_SZ, nr=(0,1,0), color='cyan')
        parent_to(cog_ctrl, root_ctrl)


# GENERAL UTILITY ======================================================

def get_geometry_from_scene():
    '''
    Collect ungrouped geometry from scene
    '''
    geometry = list()
    objects = cmds.ls(assemblies=True) # Top level Dag objects
    for obj in objects:
        if is_geometry(obj):
            geometry.append(obj)
    return geometry

def get_joints_from_scene():
    '''
    Collect ungrouped joints from scene
    '''
    joints = list()
    objects = cmds.ls(assemblies=True) # Top level Dag objects
    for obj in objects:
        if cmds.objectType(obj, i='joint'):
            joints.append(obj)
    return joints

def get_controls_from_scene():
    '''
    Collect ungrouped controls from scene
    '''
    controls = list()
    objects = cmds.ls(assemblies=True) # Top level Dag objects
    for obj in objects:
        if is_control(obj):
            controls.append(obj)
    return controls

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


# QUERY ================================================================

def is_parent(node, parent):
    node_parent = cmds.listRelatives(node, p=True, typ='transform') or []
    if parent in node_parent:
        return True # is node parent
    return False # is not node parent

def is_geometry(node):
    if cmds.objectType(node, i='mesh'):
        return True
    elif cmds.objectType(node, i='transform'):
        shapes = cmds.listRelatives(node, s=True) or []
        for shp in shapes:
            if cmds.objectType(shp, i='mesh'):
                return True
    return False

def is_control(node):
    if cmds.objectType(node, i='nurbsCurve'):
        if not cmds.listConnections(node, d=False, t='skinCluster'):
            return True
    elif cmds.objectType(node, i='transform'):
        shapes = cmds.listRelatives(node, s=True) or []
        for shp in shapes:
            if cmds.objectType(shp, i='nurbsCurve'):
                if not cmds.listConnections(shp, d=False, t='skinCluster'):
                    return True
    return False


# CLEANUP NODES ========================================================

def remove(node):
    '''
    Remove object.
    '''
    if cmds.objExists(node):
        if not 'Constraint' in cmds.objectType(node):
            disconnect_all(node)
        cmds.delete(node)

def parent_to(node, parent, a=False, r=False):
    '''
    Parent node to given parent. Check first if already parent.
    '''
    logger.debug(f'{node} {parent}')
    if not is_parent(node, parent):
        if a:
            cmds.parent(node, parent, a=1)
        elif r:
            cmds.parent(node, parent, r=1)
        else:
            cmds.parent(node, parent)

def unbind_skin(node, typ='crv'):
    '''
    Rename shapes, unbind skinClusters and delete history
    '''
    rename_shapes(node, typ=typ) # Rename shapes
    shape = cmds.listRelatives(node, s=True)[0]
    # Get skinCluster
    skinclusters = cmds.listConnections(shape, d=False, t='skinCluster') or []
    for skincluster in skinclusters:
        # Unbind geometry, Delete skin history
        cmds.skinCluster(node, e=1, ub=1)


# OPM & TRANSFORMS =====================================================

def opm(node):
    '''
    Move transform values to Offset Parent Matrix.
    Node must be transform or joint type and have attributes unlocked.
    '''
    if has_non_default_locked_attributes(node):
        logger.error('Node {node} has at least one non default locked attribute(s)')

    local_matrix = om.MMatrix(cmds.xform(node, q=1, m=1, os=1))
    offset_parent_matrix = om.MMatrix(cmds.getAttr(f'{node}.offsetParentMatrix'))
    baked_matrix = local_matrix * offset_parent_matrix
    cmds.setAttr(f'{node}.offsetParentMatrix', baked_matrix, typ='matrix')
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
            if cmds.attributeQuery(attribute + axis, n=node, ex=1):
                plug = f'{node}.{attribute}{axis}'
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
            if cmds.attributeQuery(attribute + axis, n=node, ex=1):
                plug = f'{node}.{attribute}{axis}'
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
    opm_attr = f'{node}.offsetParentMatrix'
    if unlock:
        break_connection(opm_attr)
    if not cmds.getAttr(opm_attr, lock=True):
        cmds.setAttr(opm_attr, *identity_mtx, type='matrix')

def match_transform(source, target, moc=True, unlock=True):
    '''
    Match transforms from source to target.
    By default matches position, rotation, and scaling.
    If moc=True, keep children in their positions (maintain offset for children).
    If unlock=True, unlock attributes and break input connections.
    '''
    logger.debug(f"'{source}'->'{target}'")
    if unlock: # Unlock source
        disconnect_all(source, source=True)
    if moc:
        src_children = cmds.listRelatives(source, typ='transform') or []
        # Create temporary group to keep children
        tmp_grp = cmds.group(em=True, n=f'{source}_tmp')
        cmds.matchTransform(tmp_grp, target)
        for child in src_children:
            if unlock:
                if not 'Constraint' in child:
                    disconnect_all(child, source=True)
            cmds.parent(child, tmp_grp, a=1) # Absolute parent
        cmds.matchTransform(source, target)
        opm(source)
        for child in src_children:
            cmds.parent(child, source, a=1)
            if cmds.objectType(child, i='joint'):
                # Transform created by re-parenting
                transf = cmds.listRelatives(child, p=True, typ='transform')[0]
                if 'transform' in transf:
                    cmds.ungroup(transf)
            opm(child)
        cmds.delete(tmp_grp)
    else:
        cmds.matchTransform(source, target)
        opm(source)

def match_orient(source, target): # [UNUSED]
    '''
    Match transforms to target but keep orientation of shapes.
    Also keep children of source.
    Result is actually a new transform with original shapes.
    '''
    logger.debug(f"'{source}'->'{target}'")
    # Create temporary group to keep shapes
    tmp_grp = cmds.group(em=True, n=f'{source}_tmp')
    # Match tmp_grp to target
    match_transform(tmp_grp, target)
    # Move shapes to tmp group
    shapes = cmds.listRelatives(source, s=True)
    for shp in shapes:
        cmds.parent(shp, tmp_grp, a=True, s=True)
        # Transform created by parenting
        transf = cmds.listRelatives(shp, p=True, typ='transform')[0]
        cmds.makeIdentity(transf, apply=1, t=1, r=1, s=1, jo=1)
    # Match source to target
    match_transform(source, target)
    # Move shapes back to source
    for shp in shapes:
        cmds.parent(shp, source, r=True, s=True)
    cmds.delete(tmp_grp)

def reset_joint_rotations(joints): # [UNUSED]
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

def reset_fk_joints(rigname, joints, match_type=TYPE_BN): # [UNUSED]
    '''
    Match FK joints to BN joints.
    Make joint rotations zero.
    '''
    logger.info('Reset FK joints')
    for fk_jnt in joints:
        NN = get_index_from_name(fk_jnt)
        jnt = fstr(rigname, JNT, match_type, NN)
        logger.debug(f'{fk_jnt} -> {jnt}')
        disconnect_all(fk_jnt, source=True)
        fk_children = cmds.listRelatives(fk_jnt, typ='transform') or []
        tmp_grp = cmds.group(em=True, n=f'fk_jnt_tmp')
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
        return node
    else:
        logger.debug(f"Create group '{node}'")
        return cmds.createNode('transform', n=node, s=True, ss=True)

def create_condition(node, op=0, firstTerm=None, secondTerm=0):
    '''
    Create condition node.
    Arguments
        node (str): condition node name
        op (int): operation of comparison
                  {0:'equal', 1:'not equal',
                   2:'greater than', 3='greater or equal',
                   4:'less than', 5:'less or equal'}
        secondTerm (float): second term value
    '''
    if cmds.objExists(node):
        logger.info(f"Condition '{node}' already exists.")
    else:
        cmds.createNode('condition', n=node, s=True, ss=True)
    cmds.setAttr(f'{node}.operation', op)
    cmds.setAttr(f'{node}.secondTerm', secondTerm)
    cmds.setAttr(f'{node}.colorIfTrueR', 1)
    cmds.setAttr(f'{node}.colorIfFalseR', 0)
    cmds.setAttr(f'{node}.colorIfTrueG', 0)
    cmds.setAttr(f'{node}.colorIfFalseG', 1)
    if firstTerm:
        cmds.connectAttr(firstTerm, f'{node}.firstTerm', f=1)
    return node

def create_condition_multi(driveattr, drivenattrs, value, name=None):
    '''
    Create condition node with on/off values to multiple nodes.
    Can use multiple times on same driveattr.
    Arguments
        driveattr (str): name of driver attr 'node.attr'
        drivenattrs (str list): list of attributes [obj1.attr, obj2.attr, etc]
        value (float): secondTerm value for on state
    '''
    driveattr_nn = driveattr.replace('.', '_')
    driven_nn = drivenattrs[0].replace('.', '_')
    # Create condition node
    if name:
        cond = cmds.createNode('condition', n=name)
    else:
        cond = cmds.createNode('condition', n=f'{driveattr_nn}_{driven_nn}')
    # Set node attributes
    cmds.setAttr(f'{cond}.secondTerm', value)
    cmds.setAttr(f'{cond}.colorIfTrueR', 1)
    cmds.setAttr(f'{cond}.colorIfFalseR', 0)
    cmds.connectAttr(driveattr, f'{cond}.firstTerm')
    for attr in drivenattrs:
        cmds.connectAttr(f'{cond}.outColor.outColorR', attr)
    return cond

def set_control_visibility(fk, ik):
    '''
    Set control visibility attribute to be non-keyable
    '''
    logger.info('Set control visibility')
    for control in get_controls_all(fk, ik, bn=False, include_cog=True):
        set_visibility(control, 1, k=1, cb=1, l=0)

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
                           pxy=f'{cog_ctrl}.{ikfk_switch}')
    '''
    logger.debug(f"plug:'{plug}' ln:'{ln}' nn:'{nn}' en:'{en}' pxy:'{pxy}'")
    re_divider = re.search(r'(?i)[^-_\s]+(?=[-_\s]*divider)', ln)
    if '.' in plug: # Plug with attribute
        node, node_attr = node.split('.', 1)
    else:
        node, node_attr = plug, ln
        plug = f'{node}.{ln}'
    if cmds.attributeQuery(node_attr, n=node, ex=1): # Attribute exists
        if node_attr != ln: # Change attribute name
            cmds.setAttr(plug, l=0) # Unlock attribute
            cmds.renameAttr(plug, ln)
            plug = f'{node}.{ln}'
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
            cmds.setAttr(f'{node}.{ln}', cb=1, l=1)
        elif pxy: # Proxy attribute
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', pxy=pxy, k=1)
        elif en: # Custom Enum
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', en=en, dv=dv, k=1)
        else: # Default Enum
            cmds.addAttr(node, ln=ln, nn=nn, at='enum', en='Hide:Show', dv=dv, k=1)
        logger.debug(f'added attribute {node}.{ln}')

def add_attribute_basectrl(rigname, jnt_scales):
    '''
    Add twist, offset, roll, scale attributes to basectrl
    '''
    spline_handle = fstr(rigname, SPLINE_HANDLE)
    basectrl = fstr(rigname, BASECTRL)
    if not cmds.attributeQuery('twist', n=basectrl, ex=1):
        cmds.addAttr(basectrl, ln='twist', at='float', k=1, dv=0)
    if not cmds.attributeQuery('roll', n=basectrl, ex=1):
        cmds.addAttr(basectrl, ln='roll', at='float', k=1, dv=0)
    if not cmds.attributeQuery('offset', n=basectrl, ex=1):
        cmds.addAttr(basectrl, ln='offset', at='float', k=1, dv=0)
    cmds.connectAttr(f'{basectrl}.twist', f'{spline_handle}.twist', f=1)
    cmds.connectAttr(f'{basectrl}.roll', f'{spline_handle}.roll', f=1)
    cmds.connectAttr(f'{basectrl}.offset', f'{spline_handle}.offset', f=1)
    # Scale stretchy
    add_attribute_basectrl_scale(rigname, basectrl, jnt_scales)

def add_attribute_basectrl_scale(rigname, basectrl, jnt_scales):
    '''
    Add jntScaleY and jntScaleZ attributes to basectrl
    '''
    cog_ctrl = fstr(rigname, COG_CTRL)
    ikfk_switch = fstr(rigname, IKFK)
    # IKFK Divider
    add_attribute_enum(basectrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
    # Proxy IKFK Switch attribute from Cog
    add_attribute_enum(basectrl, IKFK_SWITCH[0], IKFK_SWITCH[1],
                       pxy=f'{cog_ctrl}.{ikfk_switch}')

    add_attribute_enum(basectrl, SCALE_DIVIDER[0], SCALE_DIVIDER[1], SCALE_DIVIDER[2])
    for i, scale in enumerate(jnt_scales):
        scale_mult = fstr(rigname, SCALE_MULT, NN=i)
        if not cmds.attributeQuery(f'jntScaleY{i:02}', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln=f'jntScaleY{i:02}', at='float', dv=scale[1], k=1)
        if not cmds.attributeQuery(f'jntScaleZ{i:02}', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln=f'jntScaleZ{i:02}', at='float', dv=scale[2], k=1)
        cmds.connectAttr(f'{basectrl}.jntScaleY{i:02}', f'{scale_mult}.input2Y', f=1)
        cmds.connectAttr(f'{basectrl}.jntScaleZ{i:02}', f'{scale_mult}.input2Z', f=1)

def add_attribute_proxy(rigname, fk, ik, attr=ATTR_PROXY):
    '''
    Add twist, offset, roll, scale attributes to IK controls.
    Proxy attributes from cog control.
    ATTR_PROXY = ['squash', 'stretch', 'twist', 'roll', 'offset'] # proxy attributes
    IKFK_SWITCH = ('ikfk_switch', 'IKFK Switch', 'SplineIK:IK:Float:FK', 0) # ln,nn,en,dv
    '''
    logger.info('Add attribute proxy')
    logger.info(f'{fk} {ik} {attr}')

    basectrl = fstr(rigname, BASECTRL)
    # Add proxy attributes to controls
    for ctrl in get_controls_all(fk=False, ik=True, bn=False, include_cog=False):
        for atr in attr:
            logger.debug(f"Proxy '{basectrl}.{atr}' to '{ctrl}'")
            add_attribute_enum(ctrl, ln=atr, nn=titlecase(atr), pxy=f'{basectrl}.{atr}')

def set_visibility(node, value, k=1, cb=1, l=0):
    if not cmds.objExists(node):
        logger.error(f"'{node}' does not exist.")
    elif not cmds.attributeQuery('visibility', n=node, ex=1):
        logger.error(f"'{node}.visibility' does not exist.")
    cmds.setAttr(f'{node}.visibility', l=0) # Unlock
    cmds.setAttr(f'{node}.visibility', value)
    cmds.setAttr(f'{node}.visibility', k=k, cb=cb, l=l)

# SPLINE TWIST (IK) ====================================================

def build_advanced_twist(ikhandle, start_obj, end_obj, start_vec, end_vec):
    '''
    Build Spline IK advanced twist
    Arguments
        ikhandle (str): spline ik handle
        start_obj (str): First obj (cluster transform) for twist. clusters[0][1]
        end_obj (str): Last obj (cluster transform) for twist. clusters[-1][1]
        start_vec (tuple): Start up vector
        end_vec (tuple): End up vector
    '''
    # advancedSplineIkTwist
    cmds.setAttr(f'{ikhandle}.dTwistControlEnable', 1) # Enable advanced twist
    cmds.setAttr(f'{ikhandle}.dWorldUpType', 4) # Rot up start/end
    cmds.setAttr(f'{ikhandle}.dWorldUpAxis', 3) # Up Axis to pos z
    # Start / end obj
    cmds.connectAttr(f'{start_obj}.xformMatrix', f'{ikhandle}.dWorldUpMatrix', f=1)
    cmds.connectAttr(f'{end_obj}.xformMatrix', f'{ikhandle}.dWorldUpMatrixEnd', f=1)
    # Start / end vec
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorX', start_vec[0])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorY', start_vec[1])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorZ', start_vec[2])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorEndX', end_vec[0])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorEndY', end_vec[1])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorEndZ', end_vec[2])


# JOINT FUNCTIONS ======================================================

def get_joint_hierarchy(jnt, end_jnt=None):
    '''
    Get joints in hierarchy. Return list of joints.
    '''
    joints = list()
    if 'ee' in jnt:
        return joints
    elif jnt == end_jnt:
        return joints
    elif cmds.objectType(jnt, i='joint'):
        joints = [jnt]

    children = cmds.listRelatives(jnt, typ='joint') or []
    for child in children:
        jn = get_joint_hierarchy(child, end_jnt)
        joints.extend(jn)
    return joints

def rename_joints(rigname, joints, jnt_type):
    logger.debug(f"rigname:'{rigname}' joints:'{jnt_type}'")
    jnts = list()
    for jnt in joints:
        NN = get_index_from_name(jnt)
        jnt_name = fstr(rigname, JNT, jnt_type, NN)
        logger.debug(f"jnt:'{jnt}' jnt_name:'{jnt_name}' jnt_type:'{jnt_type}'")
        if jnt_type in jnt: # joint has same TYPE
            if jnt == jnt_name:
                jnt = cmds.rename(jnt, jnt_name)
        else: # different TYPE
            if not cmds.objExists(jnt_name):
                jnt = cmds.duplicate(jnt, n=jnt_name, po=True)[0]
        if jnts:
            parent_to(jnt_name, jnts[-1])
        jnts.append(jnt_name)
    return jnts

def get_joint_position_from_hierarchy(jnt, end_jnt):
    '''
    Get list of joint names and joint positions

    Arguments
        jnt (str): Start joint
        end_jnt (str): End joint
    '''
    if cmds.objectType(jnt, i='joint'):
        jnt_name = [jnt]
        jnt_pos = [get_world_pos(jnt)]
    else:
        jnt_name = list()
        jnt_pos = list()

    if jnt != end_jnt:
        children = cmds.listRelatives(jnt) or []
        for child in children:
            jn, jp = get_joint_position_from_hierarchy(child, end_jnt)
            jnt_name.extend(jn)
            jnt_pos.extend(jp)
    return jnt_name, jnt_pos

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
            cmds.setAttr(f'{jnt}.joint_pos', l=0) # Unlock
            cmds.addAttr(f'{jnt}.joint_pos', e=1, dv=v)
            cmds.setAttr(f'{jnt}.joint_pos', cb=1, l=1) # Channel box, lock
        else:
            cmds.addAttr(jnt, ln='joint_pos', nn='Joint Pos', at='float',
                min=0, max=1, h=False, dv=v)
            cmds.setAttr(f'{jnt}.joint_pos', cb=1, l=1)


# CONTROL FUNCTIONS ====================================================

def get_control_hierarchy(control, end_control=None):
    '''
    Get controls in hierarchy. Return list of controls.
    '''
    controls = list()
    if control == end_control:
        return controls
    elif cmds.objectType(control, i='transform') and _CTRL in control:
        controls = [control]

    children = cmds.listRelatives(control, typ='transform') or []
    for child in children:
        ct = get_control_hierarchy(child, end_control)
        controls.extend(ct)
    return controls

def get_control_position(control, joints):
    '''
    Get control position relative to the length of the joint chain
    '''
    end = joints[-1]
    fullv = get_vec_length(joints[0], end)
    length = get_vec_length(control, end)
    logger.debug(f"ctrl '{control}' - length {length} fullv {fullv}")
    if length == 0:
        v = 0
    elif fullv == 0:
        logger.error(f"ctrl '{control}' - length {length} fullv {fullv}")
    else:
        v = length/fullv

    logger.debug(f'{control} V: {v}')
    return v

def get_controls_all(fk=True, ik=True, bn=False, include_cog=False):
    '''
    Get FK, IK, BN controls under root grp
    '''
    controls = list()
    root_grp = fstr('', ROOT_GRP)
    cog_ctrl = fstr('', COG_CTRL)
    all_controls = get_control_hierarchy(root_grp)
    for ctrl in all_controls:
        if fk and TYPE_FK in ctrl:
            controls.append(ctrl)
        if ik and TYPE_IK in ctrl:
            controls.append(ctrl)
        if bn and TYPE_BN in ctrl:
            controls.append(ctrl)
        if include_cog and ctrl == cog_ctrl:
            controls.append(ctrl)
    return controls

def get_controls_ik(rigname):
    '''
    Get IK controls and IK control groups.
    '''
    ik_controls = {'ik':[], 'float':[], 'spline': [], 'upvec':[]}
    ik_ctrlgrps = {'ik':[], 'float':[], 'spline': [], 'upvec':[]}

    # Type ik, float
    ctrl_template = [SPLINE_IK_CTRL, SPLINE_FLOAT_CTRL]
    for i, ctrltyp in enumerate(['ik', 'float']):
        controls = list()
        ctrlgrps = list()
        for num in range(NUM_CTRL_IK):
            NN = num + 1
            ctrl = fstr(rigname, ctrl_template[i], TYPE_IK, NN)
            ctrlgrp = f'{ctrl}{_GRP}'
            if cmds.objExists(ctrl):
                controls.append(ctrl)
            else:
                logger.warning(f"ctrl '{ctrl}' does not exist")
            if cmds.objExists(ctrlgrp):
                ctrlgrps.append(ctrlgrp)
            else:
                logger.warning(f"ctrlgrp '{ctrlgrp}' does not exist")
        ik_controls[ctrltyp] = controls
        ik_ctrlgrps[ctrltyp] = ctrlgrps

    # Type spline
    spline_controls = list()
    spline_ctrlgrps = list()
    for ctrl_template in SPLINE_CONTROLS:
        ctrl = fstr(rigname, ctrl_template, TYPE_IK)
        ctrlgrp = f'{ctrl}{_GRP}'
        if cmds.objExists(ctrl):
            spline_controls.append(ctrl)
        else:
            logger.warning(f"ctrl '{ctrl}' does not exist")
        if cmds.objExists(ctrlgrp):
            spline_ctrlgrps.append(ctrlgrp)
        else:
            logger.warning(f"ctrlgrp '{ctrlgrp}' does not exist")
    ik_controls['spline' ] = spline_controls
    ik_ctrlgrps['spline'] = spline_ctrlgrps

    # Type upvec
    upvec_bsectrl = fstr(rigname, UPV_CTRL, TAG='_base')
    upvec_endctrl = fstr(rigname, UPV_CTRL, TAG='_end')
    upvec_bsegrp = fstr(rigname, UPV_CTRLGRP, TAG='_base')
    upvec_endgrp = fstr(rigname, UPV_CTRLGRP, TAG='_end')
    ik_controls['upvec'] = [upvec_bsectrl, upvec_endctrl]
    ik_ctrlgrps['upvec'] = [upvec_bsegrp, upvec_endgrp]

    return ik_controls, ik_ctrlgrps

def set_control_attributes(controls, joints):
    '''
    Add Control Attributes..
    Called after creating controls for the FK chain.
    Attributes: falloff, position, num_joints
        falloff (float): 0 to 10
        position (float): 0 to 10
        num_joints (float): 0 to joints-1
    '''
    logger.info('Adding control attributes')
    for ctrl in controls:
        # CleanUp
        if cmds.attributeQuery('number_joints', n=ctrl, ex=1):
            cmds.deleteAttr(ctrl, at='number_joints') # REMOVE

        # Attribute: Original Position
        ctrlpos = get_control_position(ctrl, joints)
        if cmds.attributeQuery('orig_position', n=ctrl, ex=1):
            cmds.addAttr(f'{ctrl}.orig_position', e=1, nn='Orig Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        else:
            cmds.addAttr(ctrl, ln='orig_position', nn='Orig Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        cmds.setAttr(f'{ctrl}.orig_position', cb=True, l=True)

        # Attribute: Position
        ctrlpos = get_control_position(ctrl, joints)
        if cmds.attributeQuery('position', n=ctrl, ex=1):
            cmds.addAttr(f'{ctrl}.position', e=1, nn='Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        else:
            cmds.addAttr(ctrl, ln='position', nn='Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        cmds.setAttr(f'{ctrl}.position', k=True, l=False)

        # Attribute: Falloff
        if cmds.attributeQuery('falloff', n=ctrl, ex=1):
            cmds.addAttr(f'{ctrl}.falloff', e=1, nn='Falloff',
                         at='float', min=0.1, max=10, dv=2)
        else:
            cmds.addAttr(ctrl, ln='falloff', nn='Falloff',
                         at='float', min=0.1, max=10, dv=2)
        cmds.setAttr(f'{ctrl}.falloff', k=True, l=False)

        # Attribute: Num Joints
        if cmds.attributeQuery('num_joints', n=ctrl, ex=1):
            cmds.addAttr(f'{ctrl}.num_joints', e=1, nn='Num Joints',
                         at='float', min=0, max=len(joints)-1)
        else:
            cmds.addAttr(ctrl, ln='num_joints', nn='Num Joints',
                         at='float', min=0, max=len(joints)-1)
        cmds.setAttr(f'{ctrl}.num_joints', cb=True, l=False)

        logger.debug(f'Added attributes to ctrl {ctrl}.')

def parent_group_controls(controls, groups, reverse=False, long=False):
    '''
    Parent controls under groups in a simple hierarchy.
    Zero the controls, option to reverse order.
    '''
    if reverse:
        controls.reverse()
        groups.reverse()
    for i, ctrl in enumerate(controls):
        if i:
            groups[i] = cmds.parent(groups[i], controls[i-1])[0]
            controls[i] = cmds.listRelatives(groups[i], typ='transform', f=long)[0]
    if long:
        controls = cmds.ls(controls, long=True)
        groups = cmds.ls(groups, long=True)
    return controls, groups

def orient_control_aims(controls, orient_world=None):
    '''
    Orient controls so that they aim toward each other.
    orient_world dictates overall object up for the scene.
    Aims controls on +y, and world up is -z.
    Invoke function orient_aim_controls_nulls.

    Arguments
        controls (str list): list of controls
        orient_world (str): object for aim constraint world up
            world up is -z of orient_world or first control if not provided
    '''
    if not orient_world: # If None, use first control
        orient_world = controls[0]
    orient_nulls = orient_aim_controls_nulls(controls, orient_world)
    # Match controls
    for ctrl, ctrl_null in zip(controls, orient_nulls):
        cmds.matchTransform(ctrl, ctrl_null, pos=1, rot=1, scl=0, piv=0)
    cmds.delete(orient_nulls)

def orient_aim_controls_nulls(controls, orient_world):
    '''
    Creates nulls/empty groups for orient matching via aims.
    Determine global up vector from orient_world object.
    Aim controls on +y.

    Arguments
        controls (str list): controls for locator positions
        orient_world (str list): object that dictates world up, up vector
    Return
        nulls (str list): list of created locators
    '''
    nulls = list()
    for i, obj in enumerate(controls):
        tmp_grp = cmds.group(em=True, n=f'null_{i:02}_tmp', w=1)
        nulls.append(tmp_grp)
        cmds.matchTransform(nulls[i], obj, pos=1, rot=1, scl=0, piv=0)
    # Get vector of the global_orient_obj
    world_matrix = cmds.xform(orient_world, q=1, ws=1, m=1)
    # world_x_vec = world_matrix[:3]
    # world_y_vec = world_matrix[4:7]
    world_z_vec = world_matrix[8:11]

    # Aim nulls at each other
    for i, null in enumerate(nulls):
        if i+1 == len(nulls):
            cmds.aimConstraint([nulls[i-1], null],
                               worldUpVector=world_z_vec,
                               upVector=(0,0,1),
                               aimVector=(0,-1,0),
                               maintainOffset=False)
        else:
            cmds.aimConstraint([nulls[i+1], null],
                               worldUpVector=world_z_vec,
                               upVector=(0,0,1),
                               aimVector=(0,1,0),
                               maintainOffset=False)
    return nulls

def rename_shapes(node, typ='ctrl', prefix='', suffix='Shape'):
    shapes = cmds.listRelatives(node, s=True, f=True) or []
    name = node.lstrip('|').rsplit(f'_{typ}', 1)[0]
    i = 0
    for shape in shapes:
        if 'Orig' in shape:
            cmds.rename(shape, f'{prefix}{name}_{typ}{suffix}Orig')
        else:
            if i > 0:
                cmds.rename(shapes[i], f'{prefix}{name}_{i:02}_{typ}{suffix}')
            else:
                cmds.rename(shapes[0], f'{prefix}{name}_{typ}{suffix}')
            i += 1

def set_control_color(control, color='neonblue'):
    cmds.setAttr(f'{control}.overrideEnabled', 1)
    cmds.setAttr(f'{control}.overrideRGBColors', 0)
    cmds.setAttr(f'{control}.overrideColor', COLOR_OVERRIDE[color])


# CREATE CONTROLS ======================================================

def create_control(control, group=None, match_to=None, parent=None,
             size=1, nr=(1,0,0), color='darkcyan', shape=None, overwrite=True):
    logger.debug(f'{control}, {match_to}, {parent}, {size}, {nr}, {color}, {shape}')
    # Create control group
    groupname = group if group else f'{control}{_GRP}'
    group = create_group(groupname)
    if parent:
        parent_to(group, parent)
    if match_to: # Match transforms position and rotation
        reset_opm(group)
        reset_transforms(group)
        cmds.matchTransform(group, match_to)
        # cmds.matchTransform(group, match_to, pos=1, rot=1, scl=0, piv=0)
    # Create control
    bool_create_control = True
    if cmds.objExists(control):
        logger.info(f"Control exists '{control}'")
        if overwrite:
            remove(control) # Delete control and make new control
        else:
            parent_to(control, group)
            reset_opm(control)
            reset_transforms(control)
            bool_create_control = False
    if bool_create_control:
        if shape == 'circle':
            create_circle_control(control, size, nr=nr, color=color)
        elif shape == 'sphere':
            create_sphere_control(control, size, color=color)
        elif shape == 'cube':
            create_cube_control(control, size, color=color)
        else:
            create_group(control)
        parent_to(control, group, r=1)
    return control, group

def create_control_match_list(rigname, matchlist, template_ctrl=None, template_grp=None,
                              typ='', parent=None, nest_controls=False,
                              size=1, color='darkcyan', shape=None):
    """
    Arguments
        rigname (str) : Name of rig
        matchlist (str): Names of objects to match transforms, such as a list of joints
        template_ctrl (str): Naming template for controls
        template_grp (str): Naming template for control groups
        typ (str): type option TYPE_FK, TYPE_IK, TYPE_BN, ''
        parent (str): Name of parent object
        nest_controls (bool): Whether to nest controls
        size (float): Size of controls created
        color (string): Color of controls created
        shape (string): Shape of controls such as 'circle', 'sphere', 'cube'

    Return list of controls and control groups.
    """
    controls = list()
    groups = list()
    if not template_ctrl:
        template_ctrl = '{TYPE}{rigname}{_NN}{_CTRL}'
    for i, obj in enumerate(matchlist):
        ctrl = fstr(rigname, template_ctrl, typ, NN=i+1)
        grp = fstr(rigname, template_grp, typ, NN=i+1) if template_grp else None
        control, group = create_control(ctrl, grp, match_to=obj, parent=parent,
                                        size=size, color=color, shape=shape)
        if nest_controls:
            parent = control
        controls.append(control)
        groups.append(group)
    return controls, groups

def create_circle_control(name, size, nr=(1,0,0), color='darkgrey'):
    circle_ctrl = cmds.circle(n=name, nr=nr, r=size)[0]
    rename_shapes(circle_ctrl, typ='ctrl')
    set_control_color(circle_ctrl, color=color)
    return circle_ctrl

def create_sphere_control(name, size=1, color='darkgrey'):
    sphere_ctrl = cmds.circle(n=name, nr=(1,0,0), r=size)[0]
    circle_01 = cmds.circle(n=name, nr=(0,1,0), r=size)[0]
    circle_02 = cmds.circle(n=name, nr=(0,0,1), r=size)[0]
    shape_01 = cmds.listRelatives(circle_01, s=1)[0]
    shape_02 = cmds.listRelatives(circle_02, s=1)[0]
    cmds.parent(shape_01, sphere_ctrl, r=1, s=1)
    cmds.parent(shape_02, sphere_ctrl, r=1, s=1)
    cmds.delete(circle_01)
    cmds.delete(circle_02)
    rename_shapes(sphere_ctrl, typ='ctrl')
    set_control_color(sphere_ctrl, color=color)
    return sphere_ctrl

def create_cube_control(name, size, color='darkgrey'):
    cube_ctrl = cmds.curve(d=1, p=CUBE_CTRL_PTS, n=name)
    rename_shapes(cube_ctrl, typ='ctrl')
    set_control_color(cube_ctrl, color=color)
    cmds.xform(cube_ctrl, s=(size,size,size))
    cmds.makeIdentity(cube_ctrl, apply=1, t=1, r=1, s=1, jo=1)
    return cube_ctrl

def create_basectrl(rigname, basejnt, up_axis=None):
    cog_ctrl = fstr('', COG_CTRL)
    basectrl_grp = fstr(rigname, BASECTRL_GRP)
    basectrl = fstr(rigname, BASECTRL)
    basectrl, basectrl_grp = create_control(basectrl, group=basectrl_grp,
                                            match_to=basejnt, parent=cog_ctrl,
                                            size=BASE_CTRL_SZ, nr=(1,0,0),
                                            color='magenta', shape='circle',
                                            overwrite=False)
    opm(basectrl_grp)
    if up_axis:
        rot_offset = ROT_AXIS_DICT[up_axis]
        cmds.setAttr(f'{basectrl_grp}.rotate', rot_offset[0], rot_offset[1], rot_offset[2])
    return basectrl, basectrl_grp

def create_controls_fk(rigname, joints, jnt_pos):
    '''
    Create NUM_CTRL_FK controls at equal distance along FK chain.
    Also create individual controls on each FK joint.

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        jnt_pos (float list) position of joints

    Return list of created controls
    '''
    logger.info('Creating FK controls')
    # Auto orient axis
    up_axis = get_axis_orientation(joints)
    # Create base control
    basectrl, basectrl_grp = create_basectrl(rigname, joints[0], up_axis)

    fkroot_grp = fstr(rigname, CTRLROOT_GRP, TYPE_FK)
    fkjnt_grp = fstr(rigname, GRP, TYPE_FK)

    # Cleanup
    old_fkroot_grp = fstr(rigname, '{TYPE}{rigname}_root{_CTRL}{_GRP}', TYPE_FK)
    if cmds.objExists(old_fkroot_grp):
        remove(old_fkroot_grp) # REMOVE
    if cmds.objExists(fkjnt_grp):
        # Delete constraint on fkjnt_grp
        fkjnt_constraint = cmds.listRelatives(fkjnt_grp, typ='constraint') or []
        for constraint in fkjnt_constraint:
            cmds.delete(constraint)

    # Create FK root group
    create_group(fkroot_grp)
    parent_to(fkroot_grp, basectrl) # Move fkroot_grp under basectrl_grp
    match_transform(fkroot_grp, basectrl)

    # Calculate indices evenly distributed throughout FK chain
    indices = list(linspace(0, len(jnt_pos)-1, NUM_CTRL_FK))
    matchjoints = [joints[round(indices[num])] for num in range(NUM_CTRL_FK)]
    # Create variable FK controls
    varfk_ctrls, varfk_ctrl_grps = create_control_match_list(rigname,
                                                             matchjoints,
                                                             template_ctrl=CTRL,
                                                             template_grp=CTRL_GRP,
                                                             typ='',
                                                             parent=fkroot_grp,
                                                             nest_controls=False,
                                                             size=VARFK_CTRL_SZ,
                                                             color='lightpink',
                                                             shape='cube')
    for varfk_ctrl in varfk_ctrls:
        for attr in ['tx', 'ty', 'tz']: # Hide translate
            cmds.setAttr(f'{varfk_ctrl}.{attr}', k=0, cb=0, l=1)

    # Create individual FK controls
    fk_ctrls, fk_ctrl_grps = create_control_match_list(rigname,
                                                       joints,
                                                       template_ctrl=CTRL,
                                                       template_grp=CTRL_GRP,
                                                       typ=TYPE_FK,
                                                       parent=fkroot_grp,
                                                       nest_controls=True,
                                                       size=FK_CTRL_SZ,
                                                       color='pink',
                                                       shape='circle')
    return varfk_ctrls

def create_controls_ik(rigname, joints, clusters, scale=1):
    '''
    Create NUM_CTRL_IK controls to drive the IK spline.

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        clusters (tuple list): list of clusters [cluster_name, cluster_handle_name]

    Return
        ik_controls (dict): ik control type (ik, float, spline, upvec) -> list of controls
        ik_ctrlgrps (dict): ik control group type (ik, float, spline, upvec) -> list of control groups
    '''
    logger.info('Creating IK controls')
    # Auto orient axis
    cluster_handles = [x[1] for x in clusters]
    up_axis = get_axis_orientation(cluster_handles)
    # Create base control
    basectrl, basectrl_grp = create_basectrl(rigname, joints[0], up_axis)

    # Create spline controls
    controls_ik, groups_ik = create_spline_controls_ik(
            rigname, cluster_handles[1:-1], basectrl, scale)
    controls_float, groups_float = create_spline_controls_float(
            rigname, cluster_handles[1:-1], basectrl, scale)
    controls_spline, groups_spline = create_spline_controls_spline(
            rigname, cluster_handles[1:-1], basectrl, scale)

    # Up vector controls
    controls_upv, groups_upv = create_spline_up_vectors(rigname, cluster_handles, scale)

    ik_controls = {\
        'ik': controls_ik,
        'float': controls_float,
        'spline': controls_spline,
        'upvec': controls_upv
        }
    ik_ctrlgrps = {\
        'ik': groups_ik,
        'float': groups_float,
        'spline': groups_spline,
        'upvec': groups_upv
        }
    return ik_controls, ik_ctrlgrps

def create_spline_controls_ik(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build controls for ik spline clusters
    5 clusters total, world up is -z of orient_world.
    '''
    basectrl = fstr(rigname, BASECTRL)
    controls, groups = create_control_match_list(rigname,
                                                 cluster_handles,
                                                 template_ctrl=SPLINE_IK_CTRL,
                                                 typ=TYPE_IK,
                                                 parent=basectrl,
                                                 nest_controls=True,
                                                 size=IK_CTRL_SZ*scale,
                                                 color='neonyellow',
                                                 shape='cube')
    orient_control_aims(groups, orient_world)
    # parent_group_controls(controls, groups)
    return controls, groups

def create_spline_controls_float(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build controls for float spline clusters
    5 clusters total, world up is -z of orient_world.
    '''
    basectrl = fstr(rigname, BASECTRL)
    controls, groups = create_control_match_list(rigname,
                                                 cluster_handles,
                                                 template_ctrl=SPLINE_FLOAT_CTRL,
                                                 typ=TYPE_IK,
                                                 parent=basectrl,
                                                 nest_controls=False,
                                                 size=IK_CTRL_SZ*scale,
                                                 color='cyan',
                                                 shape='cube')
    orient_control_aims(groups, orient_world)
    return controls, groups

def create_spline_controls_spline(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build controls for spline spline clusters
    5 clusters total, clusters list should match the 5 clusters of the main controls.
    There are 5 main controls plus a rotation offset mid control.
    SPLINE_CONTROLS = [SPLINE_BOT, SPLINE_BOT_SML, SPLINE_MID,
                       SPLINE_TOP_SML, SPLINE_TOP, SPLINE_MID_ROT]
    Constraints are built under constrain_spline_start_end_mid

    Arguments
        rigname (str): name of rig part
        cluster_handles (str list): cluster handle names
        orient_world (str): object for aim constraint world up, uses obj -z
        scale (float): scale multiplier for controls
    '''
    basectrl = fstr(rigname, BASECTRL)
    logger.debug(f'Cluster Handles {cluster_handles}')

    controls = list()
    groups = list()
    # Create spline controls matched to clusters
    for i, template in enumerate(SPLINE_CONTROLS):
        ctrlname = fstr(rigname, template, TYPE_IK)
        if 'mid_rot' in ctrlname: # Match to spline_mid position
            control, group = create_control(ctrlname, match_to=cluster_handles[2],
                                            parent=basectrl,
                                            size=SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neongreen', shape='sphere')
        else:
            control, group = create_control(ctrlname, match_to=cluster_handles[i],
                                            parent=basectrl,
                                            size=SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neonred', shape='cube')
        controls.append(control)
        groups.append(group)
    orient_control_aims(groups, orient_world)

    parent_to(groups[1], controls[0]) # Parent bot_sml to bot
    parent_to(groups[3], controls[4]) # Parent top_sml to top
    parent_to(groups[2], controls[5]) # Parent mid to mid_rot

    # constrain_spline_start_end_mid(rigname, spline_controls, spline_control_groups)
    return controls, groups

def create_spline_up_vectors(rigname, cluster_handles, scale=1):
    '''
    Build up-vector controls for spline rig based on global variable settings.
    Up-vector goes on the first and last controls with constraints.

    Return
        controls_upv (str list): [upvec_bsectrl, upvec_endctrl]
            up vector base control, up vector end control
        groups_upv (str list): [upvec_bsegrp, upvec_endgrp]
            up vector base group, up vector end group
    '''
    basectrl = fstr(rigname, BASECTRL)
    upvec_bsectrl = fstr(rigname, UPV_CTRL, TAG='_base')
    upvec_endctrl = fstr(rigname, UPV_CTRL, TAG='_end')
    upvec_bsegrp = fstr(rigname, UPV_CTRLGRP, TAG='_base')
    upvec_endgrp = fstr(rigname, UPV_CTRLGRP, TAG='_end')

    upvec_bsectrl, upvec_bsegrp = create_control(upvec_bsectrl,
                                                 group=upvec_bsegrp,
                                                 match_to=cluster_handles[0],
                                                 parent=basectrl,
                                                 size=SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere')
    upvec_endctrl, upvec_endgrp = create_control(upvec_endctrl,
                                                 group=upvec_endgrp,
                                                 match_to=cluster_handles[-1],
                                                 parent=basectrl,
                                                 size=SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere')

    upvec_bsectrl_shapes = cmds.listRelatives(upvec_bsectrl, s=True)
    upvec_endctrl_shapes = cmds.listRelatives(upvec_endctrl, s=True)
    # Offset shape CVs
    for shape in upvec_bsectrl_shapes:
        tr = (SPLINE_BOT_SZ + 0.5) * -scale
        cmds.move(0,-tr,0, f'{shape}.cv[*]', r=True, objectSpace=True)
    for shape in upvec_endctrl_shapes:
        tr = (SPLINE_TOP_SZ + 0.5) * -scale
        cmds.move(0,tr,0, f'{shape}.cv[*]', r=True, objectSpace=True)
    return [upvec_bsectrl, upvec_endctrl], [upvec_bsegrp, upvec_endgrp]


# ADD CURVEINFO (FK) ===================================================

def create_curveinfo(rigname, curve, typ=''):
    curveinfo = fstr(rigname, CURVEINFO, typ) # curveInfo
    if cmds.objExists(curveinfo): # Return curveInfo if it exists
        return curveinfo
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0] # Curve shape
    connections = cmds.listConnections(f'{crvshape}.worldSpace[0]') or []
    # Check if curveInfo exists under different name
    bool_create_curveinfo = True
    for cnt in connections:
        if cmds.nodeType(cnt) == 'curveInfo':
            cmds.rename(cnt, curveinfo)
            bool_create_curveinfo = False
    # Otherwise create new curveInfo node
    if bool_create_curveinfo:
        # Create curveInfo to measure length of curve
        cmds.createNode('curveInfo', n=curveinfo, s=True, ss=True)
        cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{curveinfo}.inputCurve', f=1)
    return curveinfo

def set_curveinfo_fk(rigname, curve, controls):
    '''
    Parameterize control position to the length of the curve.
    Create curveInfo and pointOnCurveInfo nodes.
    Connect position attribute on control to parameter on pointOnCurveInfo.
    Called after setting control attributes.

    Arguments
        rigname (str): name of rig part
        curve (str): nurbs curve along joint chain
        controls (str list): list of FK controls
    '''
    logger.info('Adding control curveInfo')

    basectrl = fstr(rigname, BASECTRL)
    curveinfo = create_curveinfo(rigname, curve, TYPE_FK) # curveInfo FK
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0] # Curve shape

    for ctrl in controls:
        ctrlname = ctrl.rsplit(_CTRL, 1)[0]

        # (pointOnCurveInfo) poci FK
        poci = f'{ctrlname}{_POCI}'
        cmds.createNode('pointOnCurveInfo', n=poci, s=True, ss=True)
        cmds.setAttr(f'{ctrlname}{_POCI}.turnOnPercentage', 1)
        cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{poci}.inputCurve', f=1)
        # Get parent grp above ctrl
        ctrlgrp = cmds.listRelatives(ctrl, p=True, typ='transform')
        if ctrlgrp:
            ctrlgrp = ctrlgrp[0]
        else:
            logger.error(f'Could not get parent of control {ctrl}.')

        # (multDoubleLinear) ctrlpos - Scale control position to range(0,1)
        ctrlpos = f'{ctrlname}_control_position_multDoubleLinear'
        cmds.createNode('multDoubleLinear', n=ctrlpos, s=True, ss=True)
        cmds.connectAttr(f'{ctrl}.position', f'{ctrlpos}.input1', f=1)
        cmds.setAttr(f'{ctrlpos}.input2', 0.1)
        # Use f'{ctrlpos}.output' for ctrl position

        # (plusMinusAverage) pma - Create parameter plusMinusAverage node
        pma = f'{ctrlname}_parameter_plusMinusAverage'
        cmds.createNode('plusMinusAverage', n=pma, s=True, ss=True)
        cmds.setAttr(f'{pma}.operation', 2) # Subtract
        cmds.setAttr(f'{pma}.input1D[0]', 1) # Subtract from 1
        cmds.connectAttr(f'{ctrlpos}.output', f'{pma}.input1D[1]', f=1)
        cmds.connectAttr(f'{pma}.output1D', f'{poci}.parameter', f=1)

        # (pointMatrixMult) pmm - Create pointMatrixMult node
        pmm = f'{ctrlname}_pointMatrixMult'
        cmds.createNode('pointMatrixMult', n=pmm, s=True, ss=True)
        cmds.connectAttr(f'{basectrl}.worldInverseMatrix[0]', f'{pmm}.inMatrix', f=1)
        cmds.connectAttr(f'{poci}.position', f'{pmm}.inPoint', f=1)
        cmds.connectAttr(f'{pmm}.output', f'{ctrlgrp}.translate', f=1)

def set_curveinfo_stretch(rigname, curve, typ='', duplicate_ends=True):
    '''
    VERSION 2
    Measure the arc length between specified control points on a curve.
    Returns the curveInfo node and a remapValue node that outputs the partial arc length.

    Arguments:
        rigname (str): Name of rig part
        curve (str): Name of the curve
        typ (str): TYPE (FK,IK,BN)
        duplicate_ends (bool): Whether to skip the first and last CVs

    Returns:
        tuple: (curveInfo node, remapValue node for partial length)
    '''
    # Create curveInfo node to get the total curve length
    curveinfo = create_curveinfo(rigname, curve, typ)
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0]

    # Get curve information
    num_cv, spans, degree = get_num_cv(curve)
    if duplicate_ends:
        srt_cv_i = 1
        end_cv_i = num_cv-2
    else:
        srt_cv_i = 0
        end_cv_i = num_cv-1

    # Create pointOnCurveInfo nodes to track positions at parameters
    srt_poci = cmds.createNode('pointOnCurveInfo', n=f'{typ}{rigname}_start_pointOnCurveInfo', ss=True)
    end_poci = cmds.createNode('pointOnCurveInfo', n=f'{typ}{rigname}_end_pointOnCurveInfo', ss=True)

    # Connect curve to pointOnCurveInfo nodes
    cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{srt_poci}.inputCurve', f=True)
    cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{end_poci}.inputCurve', f=True)

    # Calculate initial parameter values based on CV positions
    # For a standard curve with degree 3, parameter range is [0, spans]
    srt_param_value = float(srt_cv_i) / (num_cv - 1) * spans
    end_param_value = float(end_cv_i) / (num_cv - 1) * spans

    # Set initial parameters
    cmds.setAttr(f'{srt_poci}.parameter', srt_param_value)
    cmds.setAttr(f'{end_poci}.parameter', end_param_value)

    # Create a detached curve between the two points
    # This will allow us to directly measure the arc length between them
    detach_curve = cmds.createNode('detachCurve', n=f'{typ}{rigname}_detach_curve', s=True, ss=True)
    cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{detach_curve}.inputCurve', f=True)
    cmds.setAttr(f'{detach_curve}.parameter[0]', srt_param_value)
    cmds.setAttr(f'{detach_curve}.parameter[1]', end_param_value)

    # Create a second curveInfo to measure the partial arc length
    partial_curveinfo = cmds.createNode('curveInfo', n=f'{typ}{rigname}_partial_curveInfo', s=True, ss=True)
    cmds.connectAttr(f'{detach_curve}.outputCurve[0]', f'{partial_curveinfo}.inputCurve', f=True)

    # Create dynamic parameter update based on CV movement
    # We'll use a simple approach that updates parameters based on CV position changes
    cv_tracker = cmds.createNode('transform', n=f'{typ}{rigname}_cv_tracker', s=True, ss=True)

    # Create cluster handles if they don't exist to track CV movement
    # TODO: replace with upvec cluster
    srt_cluster = fstr(rigname, CLUSTER_UPV, TAG='_base') # cluster_upv_base
    srt_cluster_handle = fstr(rigname, CLUSTER_UPV_HANDLE, TAG='_base') # cluster_handle_upv_base
    end_cluster = fstr(rigname, CLUSTER_UPV, TAG='_end') # cluster_upv_end
    end_cluster_handle = fstr(rigname, CLUSTER_UPV_HANDLE, TAG='_end') # cluster_handle_upv_end
    #srt_cluster = f'{curve}_cv{srt_cv_i}_cluster'
    #end_cluster = f'{curve}_cv{end_cv_i}_cluster'

    # Check if clusters exist, create them if not
    if not cmds.objExists(srt_cluster):
        srt_cluster = cmds.cluster(f'{curve}.cv[{srt_cv_i}]', n=f'{curve}_cv{srt_cv_i}_cluster')[1]
    if not cmds.objExists(end_cluster):
        end_cluster = cmds.cluster(f'{curve}.cv[{end_cv_i}]', n=f'{curve}_cv{end_cv_i}_cluster')[1]

    # Use expressions to update parameters when CVs move
    srt_expr = cmds.createNode('expression', n=f'{typ}{rigname}_cv_start_param_expression', ss=True)
    end_expr = cmds.createNode('expression', n=f'{typ}{rigname}_cv_end_param_expression', ss=True)

    srt_expr_code = f'''
    // Update the start parameter based on CV position changes
    float $param = {srt_poci}.parameter;

    // Move parameter slightly to follow the CV
    if ({srt_cluster}.translateX != 0 || {srt_cluster}.translateY != 0 || {srt_cluster}.translateZ != 0) {{
        // Adjust parameter - increase/decrease based on translation direction
        if ({srt_cluster}.translateX > 0) {{
            $param += {srt_cluster}.translateX * 0.01;
        }} else {{
            $param += {srt_cluster}.translateX * 0.01;
        }}

        // Ensure parameter stays in valid range
        if ($param < 0) $param = 0;
        if ($param > {end_poci}.parameter) $param = {end_poci}.parameter - 0.01;

        // Update the parameter
        {srt_poci}.parameter = $param;
        {detach_curve}.parameter[0] = $param;
    }}
    '''

    end_expr_code = f'''
    // Update the end parameter based on CV position changes
    float $param = {end_poci}.parameter;

    // Move parameter slightly to follow the CV
    if ({end_cluster}.translateX != 0 || {end_cluster}.translateY != 0 || {end_cluster}.translateZ != 0) {{
        // Adjust parameter - increase/decrease based on translation direction
        if ({end_cluster}.translateX > 0) {{
            $param += {end_cluster}.translateX * 0.01;
        }} else {{
            $param += {end_cluster}.translateX * 0.01;
        }}

        // Ensure parameter stays in valid range
        if ($param > {spans}) $param = {spans};
        if ($param < {srt_poci}.parameter) $param = {srt_poci}.parameter + 0.01;

        // Update the parameter
        {end_poci}.parameter = $param;
        {detach_curve}.parameter[1] = $param;
    }}
    '''

    # Set expressions
    cmds.expression(srt_expr, e=True, s=srt_expr_code, ae=True, uc="all")
    cmds.expression(end_expr, e=True, s=end_expr_code, ae=True, uc="all")

    # Create remapValue to scale and offset the curve length as needed
    remap = cmds.createNode('remapValue', n=f'{typ}{rigname}_spline_parameter_remapValue', s=True, ss=True)

    # Connect partial curve length to remap
    cmds.connectAttr(f'{partial_curveinfo}.arcLength', f'{remap}.inputValue', f=True)

    # Set remap input range based on initial partial length
    partial_length = cmds.getAttr(f'{partial_curveinfo}.arcLength')
    if partial_length <= 0:
        # Fallback if the partial length is zero or negative
        logger.warning(f"Initial partial curve length is zero or negative, using fallback value")
        partial_length = cmds.getAttr(f'{curveinfo}.arcLength') * ((end_cv_i - srt_cv_i) / float(num_cv - 1))
        if partial_length <= 0:
            # Last resort fallback
            logger.warning(f"Fallback length is still zero, using hardcoded value")
            partial_length = 38.866

    # Configure remapValue node
    cmds.setAttr(f'{remap}.inputMin', 0)
    cmds.setAttr(f'{remap}.inputMax', partial_length)
    cmds.setAttr(f'{remap}.outputMin', 0)
    cmds.setAttr(f'{remap}.outputMax', partial_length)

    # Log the initial values for debugging
    logger.info(f'Initial curve length: full={cmds.getAttr(f"{curveinfo}.arcLength")}, partial={partial_length}')

    return curveinfo, remap

# def set_curveinfo_stretch(rigname, curve, typ='', duplicate_ends=True):
#     '''
#     VERSION 1
#     Parameterize start and end CV positions to the length of the curve.
#     Get the curve length between start and end CV.
#     '''
#     curveinfo = create_curveinfo(rigname, curve, typ) # curveInfo IK
#     crvshape = cmds.listRelatives(curve, s=True, ni=True)[0] # Curve shape
#
#     # Get curve information
#     num_cv, spans, degree = get_num_cv(curve)
#     if duplicate_ends:
#         srt_cv_i = 1
#         end_cv_i = num_cv-2
#     else:
#         srt_cv_i = 0
#         end_cv_i = num_cv-1
#
#     # Get CV start and end positions
#     cv_srt_pos = get_world_pos(f'{curve}.cv[{srt_cv_i}]')
#     cv_end_pos = get_world_pos(f'{curve}.cv[{end_cv_i}]')
#
#     # Create direct parameter nodes for start and end
#     srt_param = cmds.createNode('floatConstant', n=f'{typ}{rigname}_start_parameter', ss=True)
#     end_param = cmds.createNode('floatConstant', n=f'{typ}{rigname}_end_parameter', ss=True)
#
#     # Initialize parameters based on CV index
#     # For a standard curve with degree 3, the parameter range is [0, spans]
#     # The parameter of a CV is approximately its index in the spans space
#     srt_param_value = float(srt_cv_i) / (num_cv - 1) * spans
#     end_param_value = float(end_cv_i) / (num_cv - 1) * spans
#
#     cmds.setAttr(f'{srt_param}.inFloat', srt_param_value)
#     cmds.setAttr(f'{end_param}.inFloat', end_param_value)
#
#     # Create parameter difference node
#     param_pma = cmds.createNode('plusMinusAverage', n=f'{typ}{rigname}_spline_parameter_plusMinusAverage', ss=True)
#     cmds.setAttr(f'{param_pma}.operation', 2)  # Subtraction
#     cmds.connectAttr(f'{end_param}.outFloat', f'{param_pma}.input1D[0]', f=True)
#     cmds.connectAttr(f'{srt_param}.outFloat', f'{param_pma}.input1D[1]', f=True)
#
#     # Setup expressions to update the parameters when CVs move
#     srt_expr = cmds.createNode('expression', n=f'{typ}{rigname}_cv_start_param_expression', s=True, ss=True)
#     end_expr = cmds.createNode('expression', n=f'{typ}{rigname}_cv_end_param_expression', s=True, ss=True)
#
#     # Create detectors for CV movement that update parameters
#     # Instead of trying to calculate exact parameter values, we'll use the relative movement
#     # of the CVs to adjust the parameter values proportionally
#     srt_expr_code = f'''
#     vector $cvPos = <<{curve}.cv[{srt_cv_i}].xValue, {curve}.cv[{srt_cv_i}].yValue, {curve}.cv[{srt_cv_i}].zValue>>;
#
#     // Store a reference position for the CV
#     vector $refPos = <<{cv_srt_pos[0]}, {cv_srt_pos[1]}, {cv_srt_pos[2]}>>;
#
#     // Calculate parameter adjustment based on movement along the curve
#     // This is a simplified approximation that works well for most cases
#     float $movement = mag($cvPos - $refPos) * 0.1;
#
#     // Adjust the parameter - the sign is determined by which end of the curve moved
#     if ($cvPos.x > $refPos.x) {{
#         {srt_param}.inFloat = {srt_param_value} + $movement;
#     }} else {{
#         {srt_param}.inFloat = {srt_param_value} - $movement;
#     }}
#
#     // Clamp parameter to valid range
#     if ({srt_param}.inFloat < 0) {srt_param}.inFloat = 0;
#     if ({srt_param}.inFloat > {spans}) {srt_param}.inFloat = {spans};
#     '''
#
#     end_expr_code = f'''
#     vector $cvPos = <<{curve}.cv[{end_cv_i}].xValue, {curve}.cv[{end_cv_i}].yValue, {curve}.cv[{end_cv_i}].zValue>>;
#
#     // Store a reference position for the CV
#     vector $refPos = <<{cv_end_pos[0]}, {cv_end_pos[1]}, {cv_end_pos[2]}>>;
#
#     // Calculate parameter adjustment based on movement along the curve
#     float $movement = mag($cvPos - $refPos) * 0.1;
#
#     // Adjust the parameter - the sign is determined by which end of the curve moved
#     if ($cvPos.x > $refPos.x) {{
#         {end_param}.inFloat = {end_param_value} + $movement;
#     }} else {{
#         {end_param}.inFloat = {end_param_value} - $movement;
#     }}
#
#     // Clamp parameter to valid range
#     if ({end_param}.inFloat < 0) {end_param}.inFloat = 0;
#     if ({end_param}.inFloat > {spans}) {end_param}.inFloat = {spans};
#     '''
#
#     # Set expressions
#     cmds.expression(srt_expr, e=1, s=srt_expr_code, ae=1, uc=all)
#     cmds.expression(end_expr, e=1, s=end_expr_code, ae=1, uc=all)
#
#     # Create pointOnCurveInfo nodes to get positions at parameters
#     srt_poci = cmds.createNode('pointOnCurveInfo', n=f'{typ}{rigname}_start_pointOnCurveInfo', ss=True)
#     end_poci = cmds.createNode('pointOnCurveInfo', n=f'{typ}{rigname}_end_pointOnCurveInfo', ss=True)
#
#     # Connect curve and parameters
#     cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{srt_poci}.inputCurve', f=True)
#     cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{end_poci}.inputCurve', f=True)
#     cmds.connectAttr(f'{srt_param}.outFloat', f'{srt_poci}.parameter', f=True)
#     cmds.connectAttr(f'{end_param}.outFloat', f'{end_poci}.parameter', f=True)
#
#     # Normalize parameter difference to [0,1] range
#     normalize = cmds.createNode('multiplyDivide', n=f'{typ}{rigname}_normalize_parameter', ss=True)
#     cmds.setAttr(f'{normalize}.operation', 2)  # Division
#     cmds.connectAttr(f'{param_pma}.output1D', f'{normalize}.input1X', f=True)
#     cmds.setAttr(f'{normalize}.input2X', spans)  # Divide by max parameter value
#
#     # Create remapValue node to scale the parameter difference to curve length
#     remap = cmds.createNode('remapValue', n=f'{typ}{rigname}_spline_parameter_remapValue', ss=True)
#     init_crvlen = cmds.getAttr(f'{curveinfo}.arcLength') / (num_cv-1)
#     cmds.setAttr(f'{remap}.inputMin', 0)
#     cmds.setAttr(f'{remap}.inputMax', 1)
#     cmds.setAttr(f'{remap}.outputMin', 0)
#     cmds.setAttr(f'{remap}.outputMax', init_crvlen)
#     cmds.connectAttr(f'{normalize}.outputX', f'{remap}.inputValue', f=True)
#
#     return curveinfo, remap
#

# def set_curveinfo_stretch(rigname, curve, typ='', duplicate_ends=True):
#     '''
#     Parameterize start and end CV positions to the length of the curve.
#     Use scriptNodes and MEL expressions to get closestPointOnCurve.
#     Get the curve length between start and end CV.
#     '''
#     curveinfo = create_curveinfo(rigname, curve, typ) # curveInfo IK
#     crvshape = cmds.listRelatives(curve, s=True, ni=True)[0] # Curve shape
#
#     num_cv, spans, degree = get_num_cv(curve)
#     if duplicate_ends:
#         srt_cv_i = 1
#         end_cv_i = num_cv-2
#     else:
#         srt_cv_i = 0
#         end_cv_i = num_cv-1
#
#     # Get CV start and end positions
#     cv_srt_pos = get_world_pos(f'{curve}.cv[{srt_cv_i}]')
#     cv_end_pos = get_world_pos(f'{curve}.cv[{end_cv_i}]')
#
#     # (plusMinusAverage) parameter difference node
#     param_pma = cmds.createNode('plusMinusAverage', n=f'{typ}{rigname}_spline_parameter_plusMinusAverage', s=True, ss=True)
#     cmds.setAttr(f'{param_pma}.operation', 2) # Subtraction
#     # Output f'{param_pma}.output1D'
#
#     # (pointOnCurveInfo) start and end poci
#     srt_poci = cmds.createNode('pointOnCurveInfo', n=f'{typ}{rigname}_start{_POCI}', s=True, ss=True)
#     cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{srt_poci}.inputCurve', f=1)
#     end_poci = cmds.createNode('pointOnCurveInfo', n=f'{typ}{rigname}_end{_POCI}', s=True, ss=True)
#     cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{end_poci}.inputCurve', f=1)
#
#     # (scriptNode) Define procedure names
#     srt_proc_name = f'{typ}{rigname}_start_param_update'
#     end_proc_name = f'{typ}{rigname}_end_param_update'
#
#     # Start CV procedure
#     srt_proc = f'''
#     // This scriptNode finds the parameter value for the start CV position
#     global proc {srt_proc_name}() {{
#         string $curve = "{curve}";
#         int $cv_i = {srt_cv_i};
#         string $poci = "{srt_poci}";
#         string $pma = "{param_pma}";
#
#         // Get CV position
#         float $pos[] = `xform -q -ws -t ($curve + ".cv[" + $cv_i + "]")`;
#         // Call MEL closestPointOnCurve
#         float $param[] = `closestPointOnCurve $curve $pos[0] $pos[1] $pos[2]`;
#         // Set the parameter on the pointOnCurveInfo
#         setAttr ($poci + ".parameter") $param[0];
#         // Update parameter difference node
#         setAttr ($pma + ".input1D[0]") $param[0];
#     }}
#     '''
#
#     # End CV procedure
#     end_proc = f'''
#     // This scriptNode finds the parameter value for the end CV position
#     global proc {end_proc_name}() {{
#         string $curve = "{curve}";
#         int $cv_i = {end_cv_i};
#         string $poci = "{end_poci}";
#         string $pma = "{param_pma}";
#
#         // Get CV position
#         float $pos[] = `xform -q -ws -t ($curve + ".cv[" + $cv_i + "]")`;
#         // Call MEL closestPointOnCurve
#         float $param[] = `closestPointOnCurve $curve $pos[0] $pos[1] $pos[2]`;
#         // Set the parameter on the pointOnCurveInfo
#         setAttr ($poci + ".parameter") $param[0];
#         // Update parameter difference node
#         setAttr ($pma + ".input1D[1]") $param[0];
#     }}
#     '''
#
#     # Execute MEL to create the procedures
#     mel.eval(srt_proc)
#     mel.eval(end_proc)
#
#     # Create scriptNodes
#     srt_script = cmds.scriptNode(n=f'{typ}{rigname}_start_param_scriptNode', st=2, bs='')
#     end_script = cmds.scriptNode(n=f'{typ}{rigname}_end_param_scriptNode', st=2, bs='')
#
#     # Set scriptNode content after creation
#     srt_script_code = f'{srt_proc_name}();'
#     end_script_code = f'{end_proc_name}();'
#     cmds.scriptNode(srt_script, e=True, bs=srt_script_code)
#     cmds.scriptNode(end_script, e=True, bs=end_script_code)
#
#     # Create expressions to trigger scriptNodes when CVs move
#     srt_expr = cmds.createNode('expression', n=f'{typ}{rigname}_start_update_expression', s=True, ss=True)
#     end_expr = cmds.createNode('expression', n=f'{typ}{rigname}_end_update_expression', s=True, ss=True)
#
#     srt_expression = f'''
#     // Get CV position to trigger updates
#     float $srt_xyz = {curve}.cv[{srt_cv_i}].xValue + {curve}.cv[{srt_cv_i}].yValue + {curve}.cv[{srt_cv_i}].zValue;
#     // Execute scriptNode
#     {srt_script}.nodeState = 0; // Force evaluation
#     {srt_script}.nodeState = 1; // Reset state
#     '''
#     end_expression = f'''
#     // Get CV position to trigger updates
#     float $end_xyz = {curve}.cv[{end_cv_i}].xValue + {curve}.cv[{end_cv_i}].yValue + {curve}.cv[{end_cv_i}].zValue;
#     // Execute scriptNode
#     {end_script}.nodeState = 0; // Force evaluation
#     {end_script}.nodeState = 1; // Reset state
#     '''
#
#     # Set expressions
#     cmds.expression(n=srt_expr, s=srt_expression, o='', ae=1, uc=all)
#     cmds.expression(n=end_expr, s=end_expression, o='', ae=1, uc=all)
#
#     # (remapValue) Scale parameter difference to curve length
#     remap = cmds.createNode('remapValue', n=f'{typ}{rigname}_parameter_remapValue', s=True, ss=True)
#     init_crvlen = cmds.getAttr(f'{curveinfo}.arcLength') / (num_cv-1)
#     cmds.setAttr(f'{remap}.inputMin', 0)
#     cmds.setAttr(f'{remap}.inputMax', 1)
#     cmds.setAttr(f'{remap}.outputMin', 0)
#     cmds.setAttr(f'{remap}.outputMax', init_crvlen)
#     cmds.connectAttr(f'{param_pma}.output1D', f'{remap}.inputValue', f=1)
#     # Output f'{remap}.outValue'
#
#     # Execute the procedures once to initialize the parameters
#     mel.eval(f'{srt_proc_name}();')
#     mel.eval(f'{end_proc_name}();')
#
#     return curveinfo, remap


# SPLINE / NURBS CURVE / CLUSTERS (IK) =================================

def create_curve(rigname, jnt_pos, typ='', degree=3):
    '''
    Create NURBS curve for FK or IK rig

    Arguments
        rigname (str): name of rig
        jnt_pos (float tuple list): joint positions
        degree (int): degree of the curve
        typ (str): TYPE (FK,IK,BN)

    Return NURBS curve
    '''
    curve = fstr(rigname, CURVE, typ) # Curve
    if cmds.objExists(curve):
        logger.info(f"Curve '{curve}' already exists")
        unbind_skin(curve, typ='crv')
    else:
        logger.info(f"Create curve '{curve}'")
        logger.info(f'jnt_pos {len(jnt_pos)} {jnt_pos}')
        if typ == TYPE_FK:
            curve = create_curve_on_joint(curve, jnt_pos, 3, False)
        else:
            # Calculate indices evenly distributed throughout IK chain
            indices = list(linspace(0, len(jnt_pos)-1, NUM_CTRL_IK))
            pos = [jnt_pos[round(indices[num])] for num in range(NUM_CTRL_IK)]
            curve = create_curve_on_joint(curve, pos, 3, True)
        cmds.rebuildCurve(curve,
                          ch=0,   # No construction history
                          rpo=1,  # Retain position
                          rt=0,   # Uniform parameterization
                          end=1,  # Keep end points
                          kr=0,   # Keep range
                          kcp=1,  # Keep control points
                          kep=1,  # Keep end positions
                          kt=0,   # Keep tangents
                          d=3,    # Degree 3
                          tol=0.01)
        cmds.delete(curve, ch=1) # Delete construction history
    return curve

def create_curve_on_joint(name, jnt_pos, degree=3, duplicate_ends=True):
    '''
    Create NURBS Curve with CVs at given joint positions.
    '''
    if len(jnt_pos) < degree + 1:
        logger.error('At least degree+1 ({degree+1}) jnt_pos are required for a degree {degree} curve.\n{jnt_pos}')
    logger.info(f"'{name}' degree={degree} duplicate_ends={duplicate_ends}")

    cv_pos = list()
    if duplicate_ends:
        cv_pos.append(jnt_pos[0])
        cv_pos.extend(jnt_pos)
        cv_pos.append(jnt_pos[-1])
    else:
        cv_pos = jnt_pos
    logger.info(f'cv_pos {len(cv_pos)} {cv_pos}')
    knot_vector = create_uniform_knot_vector(len(jnt_pos), degree, duplicate_ends)
    curve = cmds.curve(n=name, d=degree, p=cv_pos, k=knot_vector)
    rename_shapes(curve, typ='crv')
    set_visibility(curve, 0, k=1, cb=0, l=0) # Lock curve and hide
    logger.info('Created NURBS curve..')
    return curve

def create_uniform_knot_vector(num_jnt, degree=3, duplicate_ends=True):
    '''
    Create a uniform, non-periodic knot vector.

    Arguments
        num_jnt (int): number of joints
        degree (int): degree of the curve

    Return a list of floats representing the knot vector
    '''
    num_cv = num_jnt+2 if duplicate_ends else num_jnt
    num_knots = num_cv + degree - 1 # Total knots

    knot_vector = list()

    if duplicate_ends:
        # Start with degree+1 initial knots of 0
        knot_vector.extend([0] * (degree+1))
        # Create uniform distribution of internal_knots between 0 and 1
        internal_knots = num_cv - degree - 3
        for i in range(internal_knots):
            knot_vector.append((i+1) / (internal_knots+1))
        # End with degree+1 final knots of 1
        knot_vector.extend([1] * (degree+1))
    else:
        if num_knots <= 1:
            knot_vector = [0]
        else:
            for i in range(num_knots):
                knot_vector.append(float(i) / (num_knots-1))

    logger.info(f'knot_vector {len(knot_vector)} {knot_vector}')
    return knot_vector

def create_surface_from_curve(name, ncurve, orient='y', offset=0.2): # [UNUSED]
    '''
    Create a NURBS Surface from the given NURBS Curve
    '''
    logger.debug('Creating NURBS surface')
    # Generate four boundary curves
    ncurve1 = f'{name}_crv1'
    ncurve2 = f'{name}_crv2'
    ncurve3 = f'{name}_crv3'
    ncurve4 = f'{name}_crv4'

    # Curves 1 and 3 are offset from original ncurve
    transform1 = tuple()
    transform3 = tuple()
    if orient=='x':
        logger.debug('orient x')
        transform1 = (0,0,offset)
        transform3 = (0,0,-offset)
    elif orient=='y':
        logger.debug('orient y')
        transform1 = (offset,0,0)
        transform3 = (-offset,0,0)
    elif orient=='z':
        logger.debug('orient z')
        transform1 = (offset,0,0)
        transform3 = (-offset,0,0)

    cmds.duplicateCurve(ncurve, n=ncurve1, ch=False, o=True)
    cmds.xform(ncurve1, r=True, t=transform1)
    cmds.duplicateCurve(ncurve, n=ncurve3, ch=False, o=True)
    cmds.xform(ncurve3, r=True, t=transform3)

    # Curves 2 and 4 connect start and end points
    cvs = cmds.getAttr(f'{ncurve}.cv[*]')
    spt = cvs[0] # curve start point
    ept = cvs[-1] # curve end point
    ncurve1_cv = cmds.getAttr(f'{ncurve1}.cv[*]')
    ncurve3_cv = cmds.getAttr(f'{ncurve3}.cv[*]')
    if orient=='x':
        cmds.curve(n=ncurve2, d=1,
            p=[(spt[0],spt[1],spt[2]+1), (spt[0],spt[1],spt[2]-1)])
        cmds.curve(n=ncurve4, d=1,
            p=[(ept[0],ept[1],ept[2]+1), (ept[0],ept[1],ept[2]-1)])
    elif orient=='y':
        cmds.curve(n=ncurve2, d=1,
            p=[(spt[0],spt[1]+1,spt[2]), (spt[0],spt[1]-1,spt[2])])
        cmds.curve(n=ncurve4, d=1,
            p=[(ept[0],ept[1]+1,ept[2]), (ept[0],ept[1]-1,ept[2])])
    elif orient=='z':
        cmds.curve(n=ncurve2, d=1,
            p=[(spt[0]+1,spt[1],spt[2]), (spt[0]-1,spt[1],spt[2])])
        cmds.curve(n=ncurve4, d=1,
            p=[(ept[0]+1,ept[1],ept[2]), (ept[0]-1,ept[1],ept[2])])
    logger.debug('Done creating boundary curves.')

    # Create square nurbs surface defined by boundary curves
    nsurface = cmds.squareSurface(ncurve1, ncurve2, ncurve3, ncurve4,
        n=f'{name}_ribbon', ch=False, o=True, po=0, ct1=1, ct2=1, ct3=1, ct4=1)
    # Center pivot
    cmds.xform(nsurface, cpc=True)
    logger.info('Created NURBS surface')

    # Cleanup
    #cmds.delete(ncurve)
    cmds.delete(ncurve1)
    cmds.delete(ncurve2)
    cmds.delete(ncurve3)
    cmds.delete(ncurve4)
    return nsurface

def create_spline_handle(rigname, joints, curve, typ=TYPE_IK):
    '''
    Arguments
        rigname (str): name of rig part
        joints (list): list of joints
        curve (str): name of IK curve
        curvespans (int): number of curve spans to be built

    Return
        spline_list (list): ikhandle object [ikhandle, effector, curve]
    '''
    if len(joints) < 3:
        logger.error(f"Must have more than 3 joints '{joints}'")
    if not cmds.objExists(curve):
        logger.error(f"Curve '{curve}' does not exist")

    # IK handle object [ikhandle, effector, curve]
    spline_list = get_spline_handle(rigname, joints, existing=True)
    for obj in spline_list:
        cmds.delete(obj)

    # Create new IK handle
    if cmds.objExists(curve):
        spline_list = cmds.ikHandle(n=fstr(rigname, SPLINE_HANDLE),
                                    c=curve, fj=1,
                                    sj=joints[0], ee=joints[-1],
                                    sol='ikSplineSolver')
    else:
        curvespans = NUM_CTRL_IK - 1
        spline_list = cmds.ikHandle(n=fstr(rigname, SPLINE_HANDLE),
                                    c=curve, fj=1,
                                    sj=joints[0], ee=joints[-1],
                                    sol='ikSplineSolver', ns=curvespans)

    return rename_spline_handle(rigname, spline_list, typ)

def rename_spline_handle(rigname, spline_list, typ=TYPE_IK):
    spline_handle = fstr(rigname, SPLINE_HANDLE) # Handle
    spline_effector = fstr(rigname, SPLINE_EFFECTOR) # Effector
    curve = fstr(rigname, CURVE, typ) # Curve

    # Rename Handle, Effector, Curve
    logger.info(f"Original spline_list {spline_list}")
    cmds.rename(spline_list[0], spline_handle)
    cmds.rename(spline_list[1], spline_effector)
    cmds.rename(spline_list[2], curve)
    rename_shapes(curve, typ='crv')
    set_visibility(curve, 0, k=1, cb=0, l=1) # Lock curve and hide
    spline_list = [spline_handle, spline_effector, curve]
    logger.info(f"Renamed spline_list {spline_list}")
    return spline_list

def get_spline_handle(rigname, joints=None, existing=False):
    '''
    Return
        spline_list (list): IK handle object [ikhandle, effector, curve]
                            spline components
    '''
    spline_handle = fstr(rigname, SPLINE_HANDLE) # Handle
    spline_effector = fstr(rigname, SPLINE_EFFECTOR) # Effector
    curve = fstr(rigname, CURVE, TYPE_IK) # Curve

    if existing:
        # Get existing IK handle if it already exists
        ex_ikhandles = cmds.ls(typ='ikHandle')
        for i, ikh in enumerate(ex_ikhandles):
            if i==0 and not joints:
                # Return first find if joints not provided
                return [ikh, cmds.ikHandle(ikh, q=1, ee=1),
                        cmds.ikHandle(ikh, q=1, c=1).split('|')[-2]]
            jl = cmds.ikHandle(ikh, q=1, jl=1)
            if is_equal_joint(jl[0], joints[0]) and is_equal_joint(jl[-1], joints[-2]):
                # Return IK handle with matching start end joints
                spline_list = [ikh, cmds.ikHandle(ikh, q=1, ee=1),
                               cmds.ikHandle(ikh, q=1, c=1).split('|')[-2]]
                logger.info(f'Found existing match {spline_list}')
                return spline_list
        logger.info(f'No existing spline handles match')
        return None
    else:
        if not cmds.objExists(spline_handle):
            spline_handle = None
        if not cmds.objExists(spline_effector):
            spline_effector = None
        if not cmds.objExists(curve):
            curve = None
        return [spline_handle, spline_effector, curve]

def create_clusters_on_curve(rigname, curve, relative=False, show_handle=False):
    '''
    Creates clusters on NURBS curve, adding the clusters to each CV in order.

    Arguments
        rigname (str): name of rig part
        curve (str): NURBS curve
        relative (bool): relative mode. only use transforms directly above cluster
        show_handle (bool): show Maya handles display mode

    Return
        clusters (list): list of clusters created (cluster node name, cluster handle name)
    '''
    logger.info(f"Create clusters on curve '{curve}'")
    clusters = list()

    cluster_grp = create_group(fstr(rigname, CLUSTER_GRP))
    # Get clusters from curve shape
    crvshape = cmds.listConnections(curve, shapes=True) or []
    for shape in crvshape:
        deformers = cmds.listHistory(shape) or []
        for node in deformers:
            # Remove old clusters
            if cmds.objExists(node) and cmds.nodeType(node) == 'cluster':
                cmds.delete(node)

    num_cv, _spans, _degree = get_num_cv(curve)
    for NN in range(num_cv):
        if NN == 0:
            cluster_node = fstr(rigname, CLUSTER_UPV, TAG='_base')
            cluster_handle = fstr(rigname, CLUSTER_UPV_HANDLE, TAG='_base')
        elif NN == num_cv-1:
            cluster_node = fstr(rigname, CLUSTER_UPV, TAG='_end')
            cluster_handle = fstr(rigname, CLUSTER_UPV_HANDLE, TAG='_end')
        else:
            cluster_node = fstr(rigname, CLUSTER, '', NN)
            cluster_handle = fstr(rigname, CLUSTER_HANDLE, '', NN)
        if cmds.objExists(cluster_node):
            logger.info(f'Cluster exists {cluster_node}')
            cluster = [cluster_node, cluster_handle]
        else:
            logger.info(f'Create cluster {cluster_node}')
            cluster = cmds.cluster(f'{curve}.cv[{NN}]', n=cluster_node, rel=relative)
        logger.info(cluster)
        parent_to(cluster[1], cluster_grp)
        clusters.append(cluster)
    if show_handle:
        for cluster in clusters: # cluster = [cluster node, cluster handle]
            cmds.setAttr(f'{cluster[1]}.displayHandle', 1)
    cmds.select(clear=True) # Deselect all
    return clusters

def get_num_cv(curve):
    '''
    Return num_cv, spans, degree
    '''
    spans = cmds.getAttr(f'{curve}.spans')
    degree = cmds.getAttr(f'{curve}.degree')
    form = cmds.getAttr(f'{curve}.form')
    num_cv = spans + degree
    if form == 2:
        num_cv -= degree
    logger.info(f'numcv:{num_cv} spans:{spans} degree:{degree} form:{form}')
    return num_cv, spans, degree


# FALLOFF ROTATION (FK) ================================================

def falloff_rotation(rigname, n, joints, sdks):
    '''
    Remap control's rotation to joint rotations with falloff.

    Arguments
        rigname (str): name of rig part
        n (int): index of varFK control
        joints (list): list of FK joints
        sdks (list): SDK groups corresponding to control

    Control Attributes
        control.position ranges from 0 to 10.
        control.falloff ranges from 0 to 10.
        Multiply by 0.1 to get a range fom 0 to 1.

    Range of Falloff
        ctrl+falloff .. ctrl .. ctrl-falloff

    Equation
    (+): falloff_pos > jntpos > ctrlpos
        falloff_pos = ctrl + falloff
        rotmult_pos = (jnt-ctrl) / falloff
    (-): ctrlpos > jntpos > falloff_neg
        falloff_neg = ctrl - falloff
        rotmult_neg = (ctrl-jnt) / falloff
        num_joints = rotmult * (1/joints_affected)
    '''
    control = fstr(rigname, CTRL, '', n+1) # varfk_ctrl
    control_grp = fstr(rigname, CTRL_GRP, '', n+1) # varfk_ctrlgrp
    logger.info(f"Setup Falloff Rotations for control '{control}'")
    if len(sdks) != len(joints):
        logger.error('Lists of sdk groups and joints should match in length.')
    ctrlname = control.rsplit(_CTRL, 1)[0]

    # CleanUp
    mult1 = f'{ctrlname}_multiplyDivide'
    remove(mult1) # REMOVE
    falloff_pos = f'{ctrlname}_falloff_pos_plusMinusAverage'
    falloff_neg = f'{ctrlname}_falloff_neg_plusMinusAverage'
    remove(falloff_pos) # REMOVE
    remove(falloff_neg) # REMOVE

    # (multiplyDivide) minusrot - Remove double rotation from control
    minusrot = f'{ctrlname}_minusrot_multiplyDivide'
    cmds.createNode('multiplyDivide', n=minusrot, s=True, ss=True)
    cmds.setAttr(f'{minusrot}.operation', 1) # multiply
    cmds.setAttr(f'{minusrot}.input2', -0.5,-0.5,-0.5)
    cmds.connectAttr(f'{control}.rotate', f'{minusrot}.input1', f=1)
    # Output f'{minusrot}.output'
    cmds.connectAttr(f'{minusrot}.output', f'{control_grp}.rotate', f=1)

    # (plusMinusAverage) plusrot - Add rotation from controls above
    if n > 0:
        plusrot = f'{ctrlname}_plusrot_plusMinusAverage'
        cmds.createNode('plusMinusAverage', n=plusrot, s=True, ss=True)
        cmds.setAttr(f'{plusrot}.operation', 1) # Add
        cmds.connectAttr(f'{minusrot}.output', f'{plusrot}.input3D[0]', f=1)
        prev_ctrl = fstr(rigname, CTRL, '', n)
        if n > 1:
            prev_ctrlname = prev_ctrl.rsplit(_CTRL, 1)[0]
            prev_plusrot = f'{prev_ctrlname}_plusrot_plusMinusAverage'
            cmds.connectAttr(f'{prev_plusrot}.output3D', f'{plusrot}.input3D[1]', f=1)
        else:
            cmds.connectAttr(f'{prev_ctrl}.rotate', f'{plusrot}.input3D[1]', f=1)
        cmds.connectAttr(f'{plusrot}.output3D', f'{control_grp}.rotate', f=1)

    # (multDoubleLinear) ctrlpos - Scale control position to range(0,1)
    ctrlpos = f'{ctrlname}_control_position_multDoubleLinear'
    if not cmds.objExists(ctrlpos):
        cmds.createNode('multDoubleLinear', n=ctrlpos, s=True, ss=True)
        cmds.connectAttr(f'{control}.position', f'{ctrlpos}.input1', f=1)
        cmds.setAttr(f'{ctrlpos}.input2', 0.1)
    # Output f'{ctrlpos}.output' for ctrl position

    # (multDoubleLinear) falloff - Scale control falloff to range(0,1)
    falloff = f'{ctrlname}_control_falloff_multDoubleLinear'
    if not cmds.objExists(falloff):
        cmds.createNode('multDoubleLinear', n=falloff, s=True, ss=True)
        cmds.connectAttr(f'{control}.falloff', f'{falloff}.input1', f=1)
        cmds.setAttr(f'{falloff}.input2', 0.1)
    # Output f'{falloff}.output' for ctrl falloff

    # Remap range(0,1) to range(0,num_jnts)
    # (setRange) old_min:0 old_max:1 -> new_min:1 new_max:num_jnts
    setrange = f'{ctrlname}_setRange'
    cmds.createNode('setRange', n=setrange, s=True, ss=True)
    cmds.setAttr(f'{setrange}.oldMinX', 0)
    cmds.setAttr(f'{setrange}.oldMaxX', 1)
    cmds.setAttr(f'{setrange}.minX', 1)
    cmds.setAttr(f'{setrange}.maxX', len(joints))
    # ctrl.falloff -> setrange.valueX
    cmds.connectAttr(f'{falloff}.output', f'{setrange}.valueX', f=1)
    # setrange.outValueX -> control
    cmds.connectAttr(f'{setrange}.outValueX', f'{control}.num_joints', f=1)

    # Iterate through joints, connect nodes
    for idx in range(len(joints)):
        sdk = sdks[idx] # sdk group above joint
        jnt = joints[idx] # each joint
        sdk_name = sdk.lstrip(TYPE_FK).rsplit(_SDK, 1)[0]

        # CleanUp
        jntfalloff_pos = f'{sdk_name}_jntfalloff_pos_plusMinusAverage'
        jntfalloff_neg = f'{sdk_name}_jntfalloff_neg_plusMinusAverage'
        remove(jntfalloff_pos) # REMOVE
        remove(jntfalloff_neg) # REMOVE
        ctrlfalloff_pos = f'{sdk_name}_ctrlfalloff_pos_plusMinusAverage'
        ctrlfalloff_neg = f'{sdk_name}_ctrlfalloff_neg_plusMinusAverage'
        remove(ctrlfalloff_pos) # REMOVE
        remove(ctrlfalloff_neg) # REMOVE
        rotmult = f'{sdk_name}_rotation_multiplyDivide'
        remove(rotmult) # REMOVE
        mult2 = f'{sdk_name}_mult2_multiplyDivide'
        remove(mult2) # REMOVE
        finalcond = f'{sdk_name}_final{_COND}'
        remove(finalcond) # REMOVE

        # rotmult_pos = (ctrl - jnt) / falloff
        ctrl_minus_jnt = f'{sdk_name}_ctrl_minus_jnt_plusMinusAverage'
        numerator_pos = f'{sdk_name}_numerator_pos_plusMinusAverage'
        # (+) ctrl_minus_jnt
        # (ctrl - jnt)
        cmds.createNode('plusMinusAverage', n=ctrl_minus_jnt, s=True, ss=True)
        cmds.setAttr(f'{ctrl_minus_jnt}.operation', 2) # subtract
        cmds.connectAttr(f'{ctrlpos}.output', f'{ctrl_minus_jnt}.input1D[0]', f=1)
        cmds.connectAttr(f'{jnt}.joint_pos', f'{ctrl_minus_jnt}.input1D[1]', f=1)
        # (+) numerator_pos
        # (ctrl - jnt + falloff)
        cmds.createNode('plusMinusAverage', n=numerator_pos, s=True, ss=True)
        cmds.setAttr(f'{numerator_pos}.operation', 1) # add
        cmds.connectAttr(f'{ctrl_minus_jnt}.output1D', f'{numerator_pos}.input1D[0]', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{numerator_pos}.input1D[1]', f=1)
        # Output f'{numerator_pos}.output1D'

        # rotmult_neg = (jnt - ctrl) / falloff
        jnt_minus_ctrl = f'{sdk_name}_jnt_minus_ctrl_plusMinusAverage'
        numerator_neg = f'{sdk_name}_numerator_neg_plusMinusAverage'
        # (-) jnt_minus_ctrl
        # (jnt - ctrl)
        cmds.createNode('plusMinusAverage', n=jnt_minus_ctrl, s=True, ss=True)
        cmds.setAttr(f'{jnt_minus_ctrl}.operation', 2) # subtract
        cmds.connectAttr(f'{jnt}.joint_pos', f'{jnt_minus_ctrl}.input1D[0]', f=1)
        cmds.connectAttr(f'{ctrlpos}.output', f'{jnt_minus_ctrl}.input1D[1]', f=1)
        # (-) numerator_neg
        # (jnt - ctrl + falloff)
        cmds.createNode('plusMinusAverage', n=numerator_neg, s=True, ss=True)
        cmds.setAttr(f'{numerator_neg}.operation', 1) # add
        cmds.connectAttr(f'{jnt_minus_ctrl}.output1D', f'{numerator_neg}.input1D[0]', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{numerator_neg}.input1D[1]', f=1)
        # Output f'{numerator_neg}.output1D'

        # rotmult_pos = (ctrl - jnt + falloff) / falloff
        rotmult_pos= f'{sdk_name}_rotmult_pos_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult_pos, s=True, ss=True)
        cmds.setAttr(f'{rotmult_pos}.operation', 2) # divide
        cmds.connectAttr(f'{numerator_pos}.output1D', f'{rotmult_pos}.input1X', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{rotmult_pos}.input2X', f=1)
        # Output f'{rotmult_pos}.outputX'

        # rotmult_neg = (jnt - ctrl + falloff) / falloff
        rotmult_neg= f'{sdk_name}_rotmult_neg_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult_neg, s=True, ss=True)
        cmds.setAttr(f'{rotmult_neg}.operation',2) # divide
        cmds.connectAttr(f'{numerator_neg}.output1D', f'{rotmult_neg}.input1X', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{rotmult_neg}.input2X', f=1)
        # Output f'{rotmult_neg}.outputX'

        # (condition) falloff_cond - Check if jnt falls inside falloff range
        # valid range: falloff_pos >= jntpos >= falloff_neg
        falloff_pos_cond = f'{sdk_name}_falloff_pos{_COND}'
        falloff_neg_cond = f'{sdk_name}_falloff_neg{_COND}'
        cmds.createNode('condition', n=falloff_pos_cond, s=True, ss=True)
        cmds.createNode('condition', n=falloff_neg_cond, s=True, ss=True)
        # (+) if (numerator_pos) >= 0:
        # (ctrl - jnt + falloff) >= 0
        # jnt <= ctrl + falloff
        # jnt <= falloff_pos
        cmds.setAttr(f'{falloff_pos_cond}.operation', 3) # greater or equal
        cmds.connectAttr(f'{numerator_pos}.output1D', f'{falloff_pos_cond}.firstTerm', f=1)
        cmds.setAttr(f'{falloff_pos_cond}.secondTerm', 0)
        cmds.setAttr(f'{falloff_pos_cond}.colorIfFalseR', 0)
        cmds.setAttr(f'{falloff_pos_cond}.colorIfTrueR', 1)
        # (-) if (numerator_neg) >= 0:
        # (jnt - ctrl + falloff) >= 0
        # jnt >= ctrl - falloff
        # jnt >= falloff_neg
        cmds.setAttr(f'{falloff_neg_cond}.operation', 3) # greater or equal
        cmds.connectAttr(f'{numerator_neg}.output1D', f'{falloff_neg_cond}.firstTerm', f=1)
        cmds.setAttr(f'{falloff_neg_cond}.secondTerm', 0)
        cmds.setAttr(f'{falloff_neg_cond}.colorIfFalseR', 0)
        cmds.setAttr(f'{falloff_neg_cond}.colorIfTrueR', 1)
        # Output f'{falloff_pos_cond}.outColorR' = (+) 0/1
        # Output f'{falloff_neg_cond}.outColorR' = (-) 0/1

        # (condition) cond - Condition for rotation multiplier
        cond = f'{sdk_name}_rotmult{_COND}'
        cmds.createNode('condition', n=cond, s=True, ss=True)
        # Compare ctrlpos and jntpos
        # (+) falloff_pos > jntpos > ctrlpos
        # if ctrl is after joint (less than), use rotmult_pos
        # (-) ctrlpos > jntpos > falloff_neg
        # if ctrl is before joint (greater than), use rotmult_neg
        cmds.setAttr(f'{cond}.operation', 2) # greater than
        cmds.connectAttr(f'{ctrlpos}.output', f'{cond}.firstTerm', f=1) # ctrlpos
        cmds.connectAttr(f'{jnt}.joint_pos', f'{cond}.secondTerm', f=1) # jntpos
        # Pass rotmult value to outColorR
        # (+) jntpos >= ctrlpos
        cmds.connectAttr(f'{rotmult_pos}.outputX', f'{cond}.colorIfFalseR', f=1)
        # (-) ctrlpos > jntpos
        cmds.connectAttr(f'{rotmult_neg}.outputX', f'{cond}.colorIfTrueR', f=1)
        # Pass falloff range condition to outColorG
        # (+) if jntpos > ctrlpos, check that falloff_pos >= jntpos
        # (-) if ctrlpos > jntpos, check that jntpos >= falloff_neg
        cmds.connectAttr(f'{falloff_pos_cond}.outColorR', f'{cond}.colorIfFalseG', f=1)
        cmds.connectAttr(f'{falloff_neg_cond}.outColorR', f'{cond}.colorIfTrueG', f=1)
        # Output f'{cond}.outColorR' = rotmult_pos,rotmult_neg
        # Output f'{cond}.outColorG' = (+/-) 0,1

        # rotmult = ctrl rotation * rotation multiplier
        rotmult = f'{sdk_name}_rotmult_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult, s=True, ss=True)
        cmds.setAttr(f'{rotmult}.operation', 1) # multiply
        cmds.connectAttr(f'{control}.rotate', f'{rotmult}.input1', f=1)
        # multiply rotmult to all axes X,Y,Z
        cmds.connectAttr(f'{cond}.outColorR', f'{rotmult}.input2X', f=1)
        cmds.connectAttr(f'{cond}.outColorR', f'{rotmult}.input2Y', f=1)
        cmds.connectAttr(f'{cond}.outColorR', f'{rotmult}.input2Z', f=1)
        # Output total rotation f'{rotmult}.output'

        # percentage = rotmult / (2 * joints_affected)
        percentage = f'{sdk_name}_percentage_multiplyDivide'
        cmds.createNode('multiplyDivide', n=percentage, s=True, ss=True)
        cmds.setAttr(f'{percentage}.operation', 2) # divide
        cmds.connectAttr(f'{rotmult}.output', f'{percentage}.input1', f=1)
        cmds.connectAttr(f'{control}.num_joints', f'{percentage}.input2X', f=1)
        cmds.connectAttr(f'{control}.num_joints', f'{percentage}.input2Y', f=1)
        cmds.connectAttr(f'{control}.num_joints', f'{percentage}.input2Z', f=1)
        # Output percentage rotation f'{percentage}.output'

        # (condition) threshold_cond - Condition for rotation threshold
        threshold_cond = f'{sdk_name}_threshold{_COND}'
        cmds.createNode('condition', n=threshold_cond, s=True, ss=True)
        # if cond.outColorG == 0, set rotation to 0
        # if cond.outColorG == 1, set rotation to percentage.output
        cmds.connectAttr(f'{cond}.outColorG', f'{threshold_cond}.firstTerm', f=1)
        cmds.setAttr(f'{threshold_cond}.secondTerm', 0)
        cmds.setAttr(f'{threshold_cond}.operation', 1) # not equal
        cmds.setAttr(f'{threshold_cond}.colorIfFalse', 0,0,0)
        cmds.connectAttr(f'{percentage}.output', f'{threshold_cond}.colorIfTrue', f=1)
        # Output final rotation f'{threshold_cond}.outColor'

        # Pipe in final result to sdk rotation
        cmds.connectAttr(f'{threshold_cond}.outColor', f'{sdk}.rotate', f=1)


# SDK GROUPS (FK) ======================================================

def create_sdk_groups(rigname, joints):
    '''
    Create n SDK groups above each joint.
    Return the top group that contains the FK joint chain,
    including all the SDK groups and joints.
    '''
    logger.info('Creating SDK groups above FK joints')
    basejnt = joints[0]
    basectrl = fstr(rigname, BASECTRL)
    fkjnt_grp = fstr(rigname, GRP, TYPE_FK)
    first_sdk_grp = None

    if cmds.objExists(fkjnt_grp): # If fkjnt_grp exists, match to basectrl
        logger.debug(f"fkjnt_grp exists:'{fkjnt_grp}' basectrl:'{basectrl}'")
        match_transform(fkjnt_grp, basectrl)
    else:
        # Check if first_sdk_grp has a parent that could be fkjnt_grp
        first_sdk_parent = cmds.listRelatives(first_sdk_grp, p=True, typ='transform')
        if first_sdk_parent:
            logger.debug(f"fkjnt_grp found:'{first_sdk_parent[0]}' basectrl:'{basectrl}'")
            fkjnt_grp = cmds.rename(first_sdk_parent[0], fkjnt_grp)
            match_transform(fkjnt_grp, basectrl)
        else: # Create new fkjnt_grp
            logger.debug(f"Creating new fkjnt_grp:'{fkjnt_grp}' basectrl:{basectrl}")
            create_group(fkjnt_grp)
            match_transform(fkjnt_grp, basectrl, moc=False)
    transf = cmds.listRelatives(fkjnt_grp, typ='transform') or []

    for jnt in reversed(joints):
        NN = get_index_from_name(jnt)
        jnt_name = fstr(rigname, JNT, TYPE_FK, NN)
        prev_sdk_grp = None # Previous sdk group
        first_sdk_grp = None # First sdk group
        last_sdk_grp = None # Last sdk group

        for idx in range(NUM_CTRL_FK+1): # Create sdk groups
            if idx < NUM_CTRL_FK:
                sdk_grp = fstr(rigname, SDK_GRP, TYPE_FK, NN, nn=idx+1)
            else:
                sdk_grp = fstr(rigname, SDK_CTRL, TYPE_FK, NN)
            create_group(sdk_grp)

            if idx > 0:
                # Set attribute sdk_grp.joint_pos
                v = cmds.getAttr(f'{jnt}.joint_pos') # Copy value from jnt
                if cmds.attributeQuery('joint_pos', n=sdk_grp, ex=1):
                    cmds.addAttr(f'{sdk_grp}.joint_pos', e=1, at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                else:
                    cmds.addAttr(sdk_grp, ln='joint_pos', nn='Joint Pos', at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                cmds.setAttr(f'{sdk_grp}.joint_pos', cb=1, l=1)

            if prev_sdk_grp: # Nest the sdk groups
                # Move current sdk_grp under prev
                parent_to(sdk_grp, prev_sdk_grp, r=True)
                # Match sdk_grp to prev_sdk_grp
                match_transform(sdk_grp, prev_sdk_grp)
            else:
                first_sdk_grp = sdk_grp # Store first sdk grp
            prev_sdk_grp = sdk_grp # Store sdk group to prev
        last_sdk_grp = prev_sdk_grp # Store last sdk grp

        put_jnt_under_sdk_groups(jnt, first_sdk_grp, last_sdk_grp)

    # Move first_sdk_grp under fkjnt_grp
    parent_to(first_sdk_grp, fkjnt_grp)
    opm(first_sdk_grp)
    return fkjnt_grp

def get_sdk_groups(joints):
    '''
    Get SDK groups. Return multidimensional list.
    '''
    logger.debug('Getting lists of SDK groups for all joints')
    sdk_list = [list() for n in range(NUM_CTRL_FK+1)]
    for jnt in joints:
        child = jnt
        for num in reversed(range(NUM_CTRL_FK+1)):
            parent = cmds.listRelatives(child, p=True, typ='transform')
            if parent:
                parent = parent[0]
            else:
                logger.error(f'{child} has no parent SDK group.')
            sdk_list[num].append(parent)
            child = parent
    return sdk_list

def put_jnt_under_sdk_groups(jnt, first_sdk_grp, last_sdk_grp):
    '''
    Nest joint under SDK groups.
    '''
    if is_parent(jnt, last_sdk_grp): # jnt already under sdk grp
        return

    jnt_parent = cmds.listRelatives(jnt, p=True) or []
    if jnt_parent:
        jnt_parent = jnt_parent[0]
        # Create temporary group for jnt
        tmp_grp = cmds.group(em=True, n=f'{jnt}_tmp')
        cmds.matchTransform(tmp_grp, jnt)
        parent_to(jnt, tmp_grp, a=1) # Unparent jnt
        logger.debug(f"jnt:'{jnt}' jnt_parent:'{jnt_parent}' first_sdk_grp:'{first_sdk_grp}' last_sdk_grp:'{last_sdk_grp}'")
        # Move first_sdk_grp under jnt_parent
        parent_to(first_sdk_grp, jnt_parent)
        cmds.matchTransform(first_sdk_grp, jnt_parent)
        reset_opm(first_sdk_grp)
        reset_transforms(first_sdk_grp)
        cmds.matchTransform(first_sdk_grp, jnt)
        # Move jnt under last_sdk_grp
        parent_to(jnt, last_sdk_grp, a=1)
        # Jnt transform created by re-parenting
        transf = cmds.listRelatives(jnt, p=True, typ='transform')[0]
        if transf != last_sdk_grp:
            cmds.ungroup(transf)
        cmds.delete(tmp_grp)
    else: # no jnt_parent
        match_transform(first_sdk_grp, jnt, moc=False)
        parent_to(jnt, last_sdk_grp, a=1)


# SPLINE SQUASH / STRETCH ==============================================

def build_squash_stretch(rigname, joints, typ=''):
    '''
    Build squash and stretch

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        typ (str): TYPE (FK,IK,BN)

    Return
        stretchy_nodes (str list): [curveinfo, stretch_mult, squash_mult,
            stretch_blend, squash_blend, stretch_mult_nodes, squash_mult_nodes]
    '''
    logger.info('Build squash and stretch')
    basectrl = fstr(rigname, BASECTRL)

    # stretchy_nodes (str list): [curveinfo, stretch_mult, squash_mult,
    #     stretch_blend, squash_blend, stretch_mult_nodes, squash_mult_nodes]
    stretchy_nodes = create_stretchy(rigname, joints, typ)

    # Create and connect attributes
    # stretchy_nodes[3] = stretch_blend
    # stretchy_nodes[4] = squash_blend
    add_attribute_squash_stretch(basectrl, stretchy_nodes[3], stretchy_nodes[4])

    # Connect control to world scale
    # stretchy_nodes[2] = squash_mult
    scale_grp, scale_mult_ik, scale_mult_fk = stretchy_world_scale_mod(
            rigname, basectrl, stretchy_nodes[2], typ)

    return stretchy_nodes

def create_stretchy(rigname, joints, typ=''):
    '''
    Create squash and stretch for spline ik.
    Blend nodes are needed later to assign attributes to the controls.
    Squash and stretch can be mixed/multiplied independently.

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints

    Return
        stretchy_nodes (str list): [curveinfo, stretch_mult, squash_mult,
            stretch_blend, squash_blend, stretch_mult_nodes, squash_mult_nodes]
    '''
    logger.info('Create Stretchy')
    if typ == TYPE_FK: # FK uses reference curve for scaling
        curve = fstr(rigname, CURVE_SCALE, typ) # Curve scale
    else: # IK uses original curve
        curve = fstr(rigname, CURVE, typ)
    curveinfo, curvelen = set_curveinfo_stretch(rigname, curve, typ, duplicate_ends=True)
    # init_crvlen in setup_joint_stretch

    # Setup stretch
    stretch_mult, stretch_blend, stretch_mult_nodes = setup_joint_stretch(
            rigname, joints, curvelen, typ)

    # Setup squash
    squash_mult, squash_blend, squash_mult_nodes = setup_joint_squash(
            rigname, joints, curvelen, stretch_mult, typ)

    return [curveinfo,
            stretch_mult, squash_mult,
            stretch_blend, squash_blend,
            stretch_mult_nodes,
            squash_mult_nodes]

def setup_joint_stretch(rigname, joints, curvelen, typ=''):
    '''
    Set up stretch for spline chain.
    Return created multiplyDivide and blendTwoAttr nodes

    # (multiplyDivide) spline_stretch_multiplyDivide
    crvlen -> stretch_mult.input1X
    crvlen -> stretch_mult.input1Y
    stretch_mult.input2X = len(joints)-1
    stretch_mult.input2Y = crvlen
    stretch_mult.input1Z = crvlen / len(joints)
    stretch_mult.outputX = crvlen / len(joints)-1
    stretch_mult.outputY = crvlen / init_crvlen

    # (blendTwoAttr) spline_stretch_blendTwoAttr
    stretch_mult.input1Z -> stretch_blend.input[0]
    stretch_mult.outputX (stretch length) -> stretch_blend.input[1]
    basectrl.stretch -> stretch_blend.attributesBlender

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        curvelen (str): curve length node
        typ (str): TYPE (FK,IK,BN)

    Return
        stretch_mult (str): multiplyDivide node for stretch
        stretch_blend (str): blendTwoAttr node for stretch
        stretch_mult_nodes (str list): multiplyDivide nodes to maintain scale
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        logger.info(f'CURVELEN is curveInfo')
        crvlen = f'{curvelen}.arcLength'
    elif cmds.nodeType(curvelen) == 'remapValue':
        logger.info(f'CURVELEN is remapValue')
        crvlen = f'{curvelen}.outValue'
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")

    # (multiplyDivide) stretch_mult
    stretch_mult = f'{typ}{rigname}_spline_stretch_multiplyDivide'
    cmds.createNode('multiplyDivide', n=stretch_mult, s=True, ss=True)
    cmds.setAttr(f'{stretch_mult}.operation', 2) # divide
    # Divide variable curve length by number of joints
    cmds.setAttr(f'{stretch_mult}.input2X', len(joints)-1)
    cmds.connectAttr(crvlen, f'{stretch_mult}.input1X', f=1)
    # Initial value for no stretch (curve length / num joints)
    init_crvlen = cmds.getAttr(crvlen) / (len(joints)-1)
    logger.info(f'DEBUG crvlen {cmds.getAttr(crvlen)} init_crvlen {init_crvlen}')
    if init_crvlen <= 0: # TODO
        init_crvlen = 38.866
    cmds.setAttr(f'{stretch_mult}.input1Z', init_crvlen)
    # Output f'{stretch_mult}.output'

    # (blendTwoAttr) stretch_blend
    stretch_blend = f'{typ}{rigname}_spline_stretch_blendTwoAttr'
    cmds.createNode('blendTwoAttr', n=stretch_blend, s=True, ss=True)
    cmds.connectAttr(f'{stretch_mult}.outputX', f'{stretch_blend}.input[1]', f=1)
    cmds.connectAttr(f'{stretch_mult}.input1Z', f'{stretch_blend}.input[0]', f=1)
    break_connection(f'{stretch_blend}.attributesBlender')
    cmds.setAttr(f'{stretch_blend}.attributesBlender', 1)
    # Output f'{stretch_blend}.output'

    # Check if joint lengths are same on x
    joint_lengths = list()
    for jnt in joints[1:]: # Skip first joint
        joint_lengths.append(cmds.getAttr(f'{jnt}.translateX'))
    samelen = all(x==joint_lengths[0] for x in joint_lengths)

    # Connect joints to blend. Create mult if different joint lengths
    stretch_mult_nodes = list()
    if samelen: # If joint lengths are same, use same node for all joints
        for i, jnt in enumerate(joints):
            if typ == TYPE_FK:
                ctrl_sdk = fstr(rigname, SDK_CTRL, TYPE_FK, i)
                cmds.connectAttr(f'{stretch_blend}.output', f'{ctrl_sdk}.translateX', f=1)
            else:
                cmds.connectAttr(f'{stretch_blend}.output', f'{jnt}.translateX', f=1)
    else: # Otherwise, create mult node for every joint
        for i, jnt in enumerate(joints):
            if i == 0: # Skip 00_jnt
                # cmds.connectAttr(f'{stretch_blend}.output', f'{jnt}.translateX', f=1)
                continue
            NN = get_index_from_name(jnt)
            mult = f'{typ}{rigname}_stretch_{NN:02}_multiplyDivide'
            cmds.createNode('multiplyDivide', n=mult, s=True, ss=True)
            jntlen = cmds.getAttr(f'{jnt}.translateX')
            # Set multiplier based on even joint length
            # evenlen = jntlen / (total length / (len(joints)-1))
            cmds.setAttr(f'{mult}.input1.input1X', jntlen / init_crvlen)
            cmds.connectAttr(f'{stretch_blend}.output', f'{mult}.input2.input2X', f=1)
            if typ == TYPE_FK:
                ctrl_sdk = fstr(rigname, SDK_CTRL, TYPE_FK, i)
                cmds.connectAttr(f'{mult}.output.outputX', f'{ctrl_sdk}.translateX', f=1)
            else:
                cmds.connectAttr(f'{mult}.output.outputX', f'{jnt}.translateX', f=1)
            stretch_mult_nodes.append(mult)

    return stretch_mult, stretch_blend, stretch_mult_nodes

def setup_joint_squash(rigname, joints, curvelen, stretch_mult, typ=''):
    '''
    Set up squash for spline ik chain.
    Return created multiplyDivide and blendTwoAttr nodes

    # (multiplyDivide) spline_squash_multiplyDivide
    scale_mult.outputX -> squash_mult.input1Y == scale_grp.scaleX^2
    stretch_mult.input2Y = crvlen
    stretch_mult.outputY -> squash_mult.input2Y == crvlen / init_crvlen
    squash_mult.input1Z = 1 (no scale)
    squash_mult.outputY = scale_grp.scaleX^2 * init_crvlen / crvlen

    # (blendTwoAttr) spline_squash_blendTwoAttr
    squash_mult.input1Z -> squash_blend.input[0] == 1
    squash_mult.outputY -> squash_blend.input[1]
    basectrl.squash -> squash_blend.attributesBlender

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        curvelen (str): curve length node
        stretch_mult: multiplyDivide node for stretch
        typ (str): TYPE (FK,IK,BN)

    Return
        squash_mult (str): multiplyDivide node for squash
        squash_blend (str): blendTwoAttr node for squash
        squash_mult_nodes (str list): multiplyDivide nodes to maintain scale
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        logger.info(f'CURVELEN is curveInfo')
        crvlen = f'{curvelen}.arcLength'
    elif cmds.nodeType(curvelen) == 'remapValue':
        logger.info(f'CURVELEN is remapValue')
        crvlen = f'{curvelen}.outValue'
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")

    # Maintain scale
    squash_mult_nodes = joint_squash_scale(rigname, joints, True, typ)

    cmds.connectAttr(crvlen, f'{stretch_mult}.input1Y', f=1)
    cmds.setAttr(f'{stretch_mult}.input2Y', cmds.getAttr(crvlen))

    # (multiplyDivide) squash_mult
    squash_mult = f'{typ}{rigname}_spline_squash_multiplyDivide'
    cmds.createNode('multiplyDivide', n=squash_mult, s=True, ss=True)
    cmds.connectAttr(f'{stretch_mult}.outputY', f'{squash_mult}.input2Y', f=1)
    cmds.setAttr(f'{squash_mult}.operation', 2) # divide
    break_connection(f'{squash_mult}.input1Y')
    cmds.setAttr(f'{squash_mult}.input1Y', 1)
    cmds.setAttr(f'{squash_mult}.input1Z', 1) # no scale value
    # Output f'{squash_mult}.output'

    # (blendTwoAttr) squash_blend
    squash_blend = f'{typ}{rigname}_spline_squash_blendTwoAttr'
    cmds.createNode('blendTwoAttr', n=squash_blend, s=True, ss=True)
    cmds.connectAttr(f'{squash_mult}.outputY', f'{squash_blend}.input[1]', f=1) # squash
    cmds.connectAttr(f'{squash_mult}.input1Z', f'{squash_blend}.input[0]', f=1) # no squash
    break_connection(f'{squash_blend}.attributesBlender')
    cmds.setAttr(f'{squash_blend}.attributesBlender', 1)
    # Output f'{squash_blend}.output'

    # Connect joints to blend
    if squash_mult_nodes: # If maintaining scale
        for mult in squash_mult_nodes:
            cmds.connectAttr(f'{squash_blend}.output', f'{mult}.input1.input1Y', f=1)
            cmds.connectAttr(f'{squash_blend}.output', f'{mult}.input1.input1Z', f=1)
    else: # Not maintaining scale, or joints are all scaled to 1
        for i, jnt in enumerate(joints):
            if typ == TYPE_FK:
                ctrl_sdk = fstr(rigname, SDK_CTRL, TYPE_FK, i)
                cmds.connectAttr(f'{squash_blend}.output', f'{ctrl_sdk}.scaleY', f=1)
                cmds.connectAttr(f'{squash_blend}.output', f'{ctrl_sdk}.scaleZ', f=1)
            else:
                cmds.connectAttr(f'{squash_blend}.output', f'{jnt}.scaleY', f=1)
                cmds.connectAttr(f'{squash_blend}.output', f'{jnt}.scaleZ', f=1)

    return squash_mult, squash_blend, squash_mult_nodes

def joint_squash_scale(rigname, joints, force=True, typ=''):
    '''
    Create multiplyDivide nodes to maintain joint scale on Y and Z.
    If scale is non-uniform, create extra multiplyDivide nodes for every joint.
    Skip if joint scale is uniform = 1.0

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        force (bool): force=True always build maintain nodes for extra scale control
        typ (str): TYPE (FK,IK,BN)
    Return
        squash_mult_nodes (str list): list of multiplyDivide nodes used for scaling,
                                         empty list if skipped.
    '''
    squash_mult_nodes = list()
    jnt_scales = [cmds.getAttr(f'{jnt}.scale')[0] for jnt in joints]
    if not force and all(scale==(1,1,1) for scale in jnt_scales):
        # No need to maintain, return empty list
        return list()

    # Create multiplyDivide nodes to maintain scale
    for i, scale in enumerate(jnt_scales):
        scale_mult = fstr(rigname, SCALE_MULT, NN=i)
        cmds.createNode('multiplyDivide', n=scale_mult, s=True, ss=True)
        cmds.setAttr(f'{scale_mult}.input2Y', jnt_scales[i][1])
        cmds.setAttr(f'{scale_mult}.input2Z', jnt_scales[i][2])
        if typ == TYPE_FK:
            ctrl_sdk = fstr(rigname, SDK_CTRL, TYPE_FK, i)
            cmds.connectAttr(f'{scale_mult}.outputY', f'{ctrl_sdk}.scaleY', f=1)
            cmds.connectAttr(f'{scale_mult}.outputZ', f'{ctrl_sdk}.scaleZ', f=1)
        else:
            cmds.connectAttr(f'{scale_mult}.outputY', f'{joints[i]}.scaleY', f=1)
            cmds.connectAttr(f'{scale_mult}.outputZ', f'{joints[i]}.scaleZ', f=1)
        squash_mult_nodes.append(scale_mult)

    return squash_mult_nodes

def add_attribute_squash_stretch(control, stretch_blend, squash_blend):
    '''
    Add squash and stretch attributes to a control
    '''
    if not cmds.attributeQuery('squash', n=control, ex=1):
        cmds.addAttr(control, ln='squash', at='float', k=1, dv=1)
    if not cmds.attributeQuery('stretch', n=control, ex=1):
        cmds.addAttr(control, ln='stretch', at='float', k=1, dv=1)
    cmds.connectAttr(f'{control}.squash', f'{squash_blend}.attributesBlender', f=1)
    cmds.connectAttr(f'{control}.stretch', f'{stretch_blend}.attributesBlender', f=1)

def stretchy_world_scale_mod(rigname, control, squash_mult, typ=''):
    '''
    Apply world scale to the squash and stretch setup.
    Create a scale group constrained to the control, which keeps track of world scale.
    The scale group feeds into a mult node to get the square value,
    which is then plugged into a squash_mult to maintain scale of the rig.
    '''
    scale_grp = fstr(rigname, SCALE_GRP)
    create_group(scale_grp)
    constr_scale_grp = get_constraint(scale_grp, typ='scaleConstraint')
    if not constr_scale_grp: # Constrain scale group
        constr_scale_grp = cmds.scaleConstraint(control, scale_grp)

    # (multiplyDivide) scale_mult
    scale_mult = f'{typ}{rigname}_world_scale_multiplyDivide'
    cmds.createNode('multiplyDivide', n=scale_mult, s=True, ss=True)
    # Connect scale_grp to scale_mult to double
    cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_mult}.input1.input1X')
    cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_mult}.input2.input2X')
    # Connect scale_mult to squash_mult node
    cmds.connectAttr(f'{scale_mult}.output.outputX',
                     f'{squash_mult}.input1.input1Y', f=1)

    return scale_grp, scale_mult, constr_scale_grp


# BUILD / RIG TAIL CORE ================================================

def build_rig_tail(rigname, fk=True, ik=True):
    # CleanUp existing basectrl
    basectrl_grp = fstr(rigname, BASECTRL_GRP)
    basectrl = fstr(rigname, BASECTRL)
    # Disconnect skeleton
    disconnect_skeleton(rigname, fk, ik)
    # Remove existing controls under basectrl
    remove(basectrl_grp)
    remove(basectrl)
    # Build new FK IK tail
    if fk:
        rig_tail_fk(rigname)
    if ik:
        rig_tail_ik(rigname)

def rig_tail_fk(rigname):
    '''
    Create FK tail using variable FK method.

    Arguments
        rigname (str): name of rig part

    Result
        Create FK tail from start_jnt to end_jnt.
        If end_jnt is not provided, this function will read
        the joint hierarchy until the end of the joint chain.
        Create NURBS curve and bind to given joints.
        Create N controls on FK tail.
        Create N sdk groups above FK joints.
    '''
    logger.info(f"START creating FK tail '{rigname}'")
    logger.debug(f'joints {JOINTS_FK[rigname]}')

    # Make sure fk joints match BN joints and rotations are zero
    #reset_joint_rotations(JOINTS_FK[rigname])
    #reset_fk_joints(rigname, JOINTS_FK[rigname])

    joints = JOINTS_FK[rigname]
    jnt_pos = get_joint_position_from_list(joints) # Joint position
    # Label joint positions
    set_joint_attributes(joints)

    # CleanUp NURBS Curve
    curve_old = '{rigname}{_CRV}_01'
    remove(curve_old) # REMOVE

    # Create new curve or get existing curve
    curve_fk = create_curve(rigname, jnt_pos, TYPE_FK) # Curve FK
    curve_scale = fstr(rigname, CURVE_SCALE, TYPE_FK) # Curve scale for squash stretch
    if cmds.objExists(curve_scale):
        logger.info(f"Curve scale '{curve_scale}' already exists")
    else:
        logger.info(f"Create reference curve for scaling '{curve_scale}'")
        cmds.duplicate(curve_fk, n=curve_scale)
        cmds.delete(curve_fk, ch=1) # Delete construction history
    # Move curves under rig systems
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)
    parent_to(curve_fk, rig_systems_grp)
    parent_to(curve_scale, rig_systems_grp)

    # Create controls and set attributes
    varfk_controls = create_controls_fk(rigname, joints, jnt_pos)
    set_control_attributes(varfk_controls, joints)
    # Create curveInfo and pointOnCurveInfo nodes to parameterize control position
    set_curveinfo_fk(rigname, curve_fk, varfk_controls)

    # Detect orientation of curve_fk
    #orient = axis_vector_colinearity(start_jnt, get_local_vec(start_jnt, end_jnt))
    # Create nurbs surface to match joint chain
    # nsurface = create_surface_from_curve(rigname, curve_fk, orient)

    # Create sdk groups on joint chain
    fkjnt_grp = create_sdk_groups(rigname, joints)
    sdk_groups = get_sdk_groups(joints)

    # Falloff Rotation
    for n in range(NUM_CTRL_FK):
       falloff_rotation(rigname, n, joints, sdk_groups[n])

    # Bind curve to selected bones/joints
    logger.info(f"Bind Curve '{curve_fk}' to FK joints")
    cmds.select(clear=True) # Deselect all
    # Create skinCluster - [
    #     normalizeWeights: interactive,
    #     bindMethod: closest distance between joint and point on geo,
    #     skinMethod: classic linear,
    #     maximumInfluences: 4,
    #     toSelectedBones: True]
    cmds.skinCluster(joints, curve_fk, n=f'{curve_fk}_skinCluster',
                     nw=1, bm=0, sm=0, mi=4, tsb=True)

    # Squash and Stretch
    build_squash_stretch(rigname, joints, TYPE_FK)

    logger.info(f"DONE creating FK tail '{rigname}'")

def rig_tail_ik(rigname):
    '''
    Create IK tail
    '''
    logger.info(f"START creating IK tail '{rigname}'")
    logger.debug(f'joints {JOINTS_IK[rigname]}')

    joints = JOINTS_IK[rigname]
    jnt_pos = get_joint_position_from_list(joints)
    # Get scale X value of joints
    jnt_scales = [cmds.getAttr(f'{jnt}.scale')[0] for jnt in joints]
    # Get initial start jnt and end jnt Z vectors
    start_vec = cmds.xform(joints[0], q=1, ws=1, m=1) [8:11]
    end_vec = cmds.xform(joints[-1], q=1, ws=1, m=1) [8:11]

    # Spline IK
    curve_ik = create_curve(rigname, jnt_pos, TYPE_IK) # Curve IK
    # spline_list (list): ikhandle object [ikhandle, effector, curve]
    spline_list = create_spline_handle(rigname, joints, curve_ik)
    clusters = create_clusters_on_curve(rigname, spline_list[2], relative=False)

    # Build controls and control groups
    # ik_controls (dict): control type (ik, float, spline, upvec) -> list of controls
    # ik_ctrlgrps (dict): ctrl grp typ (ik, float, spline, upvec) -> list of ctrl grps
    ik_controls, ik_ctrlgrps = create_controls_ik(rigname, joints, clusters)

    # Squash and Stretch
    build_squash_stretch(rigname, joints, TYPE_IK)
    build_advanced_twist(spline_list[0], clusters[0][1], clusters[-1][1],
                         start_vec, end_vec)

    logger.info(f"DONE creating IK tail '{rigname}'")


# CONNECTIONS ==========================================================

def connect_rig_tail(fk, ik):
    '''
    Connect FK tail rig.
    Original IKFK Switch attributes are created on Cog control.
    Proxy IKFK Switch attributes are created on Base,IK,FK controls.

    Arguments
        RIGPARTS (list): List of all rig components
        fk (bool): connect FK components
        ik (bool): connect IK components
    '''
    logger.info('START connecting rig components..')
    connect_root(fk, ik) # Root
    connect_cog(fk, ik) # Cog
    for rigname in RIGPARTS:
        connect_basectrl(rigname, fk, ik) # Basectrl
        set_control_visibility(fk, ik)
        add_attribute_proxy(rigname, fk, ik)
        connect_fk(rigname, fk, ik) # FK
        connect_ik(rigname, fk, ik) # IK
        constrain_skeleton(rigname, fk, ik)
    logger.info('DONE connecting rig components..')

def connect_root(fk, ik):
    '''
    Create attributes on root control.
    Warning: Uses hardcoded names, check naming.
    '''
    logger.info(f'Connect root')
    root_ctrl = fstr('', ROOT_CTRL)
    geometry_grp = fstr('', GEOMETRY_GRP)
    control_grp = fstr('', CONTROL_GRP)
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)
    skeleton_grp = fstr('', SKELETON_GRP)
    locators_grp = fstr('', LOCATORS_GRP)
    # Attribute Template: (longName, niceName, enumName, dv)
    if (fk and not ik) or (ik and not fk):
        rootctrl_attrs = [
            ('divider', 'visibilityDivider', 'VISIBILITY', 0),
            (geometry_grp, 'geo', 'Geometry', 1),
            (control_grp, 'controls', 'Controls', 1),
            (rig_systems_grp, 'rig_systems', 'Rig Systems', 1),
            (skeleton_grp, 'skeleton', 'Skeleton', 0),
            (locators_grp, 'locators', 'Locators', 0),
            ('divider', 'dispDivider', 'DISPLAY', 0)
        ]
    else:
        ik_skeleton_grp = fstr('', SKELETON_GRP, TYPE_IK)
        fk_skeleton_grp = fstr('', SKELETON_GRP, TYPE_FK)
        rootctrl_attrs = [
            ('divider', 'visibilityDivider', 'VISIBILITY', 0),
            (geometry_grp, 'geo', 'Geometry', 1),
            (control_grp, 'controls', 'Controls', 1),
            (rig_systems_grp, 'rig_systems', 'Rig Systems', 1),
            (skeleton_grp, 'skeleton', 'Skeleton', 0),
            (ik_skeleton_grp, 'ik_skeleton', 'IK Skeleton', 0),
            (fk_skeleton_grp, 'fk_skeleton', 'FK Skeleton', 1),
            (locators_grp, 'locators', 'Locators', 0),
            ('divider', 'dispDivider', 'DISPLAY', 0)
        ]

    # Add rootctrl attributes
    for group, ln_attr, nn_attr, dv in rootctrl_attrs:
        add_attribute_enum(root_ctrl, ln_attr, nn_attr, dv=dv)
        if group != 'divider':
            cmds.connectAttr(f'{root_ctrl}.{ln_attr}', f'{group}.visibility', f=1)
    # Add Export Geometry attribute
    add_attribute_enum(root_ctrl, ln='export_geo', nn='Export Geometry',
                       en='Unlocked:Wireframe:Locked', dv=0)
    cmds.setAttr(f'{geometry_grp}.overrideEnabled', 1)
    cmds.connectAttr(f'{root_ctrl}.export_geo',
                     f'{geometry_grp}.overrideDisplayType', f=1)
    cmds.setAttr(f'{root_ctrl}.export_geo', 2) # Locked geo

def connect_cog(fk, ik):
    '''
    Create attributes on cog control
    '''
    logger.info(f'Connect cog')
    cog_ctrl = fstr('', COG_CTRL)

    if ik:
        add_attribute_enum(cog_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        for rigname in RIGPARTS:
            ln_ikfk = fstr(rigname, IKFK)
            nn_ikfk = re.sub(r'[-_\s]+', ' ', ln_ikfk).title()
            # IKFK Switch attribute
            add_attribute_enum(cog_ctrl, ln_ikfk, nn_ikfk,
                               IKFK_SWITCH[2], IKFK_SWITCH[3])

def connect_basectrl(rigname, fk, ik):
    '''
    Connect attributes on base control
    '''
    logger.info(f'Connect basectrl')
    cog_ctrl = fstr('', COG_CTRL)
    basectrl_grp = fstr(rigname, BASECTRL_GRP)
    basectrl = fstr(rigname, BASECTRL)

    # Move basectrl under cog_ctrl
    parent_to(basectrl_grp, cog_ctrl)

    if ik: # IKFK Switch for basectrl
        # Get joint scales
        jnt_scales = [cmds.getAttr(f'{jnt}.scale')[0] for jnt in JOINTS_IK[rigname]]
        # Add twist, offset, roll, scale attributes
        add_attribute_basectrl(rigname, jnt_scales)

    # CleanUp visibility condition
    basectrl_name = basectrl.rsplit(_CTRL, 1)[0]
    remove(f'{basectrl_name}{_VIS}{_COND}') # REMOVE

def connect_fk(rigname, fk, ik):
    '''
    Connect FK components
    '''
    if not fk:
        return
    logger.info(f"Connect FK '{rigname}'")

    cog_ctrl = fstr('', COG_CTRL)
    basectrl = fstr(rigname, BASECTRL)
    fkroot_grp = fstr(rigname, CTRLROOT_GRP, TYPE_FK)
    fkjnt_grp = fstr(rigname, GRP, TYPE_FK)

    # FK joint group constraint
    cmds.parentConstraint(basectrl, fkjnt_grp, mo=1)

    # FK control constraint
    for num, jnt in enumerate(JOINTS_FK[rigname]):
        fk_ctrl = fstr(rigname, CTRL, TYPE_FK, num+1)
        fk_ctrl_grp = fstr(rigname, CTRL_GRP, TYPE_FK, num+1)
        last_sdk = fstr(rigname, SDK_GRP, TYPE_FK, num, NUM_CTRL_FK)
        ctrl_sdk = fstr(rigname, SDK_CTRL, TYPE_FK, num)
        sdk_constr = f'{fk_ctrl_grp}_parentConstraint1'
        jnt_constr = f'{jnt}_parentConstraint1'
        if not cmds.objExists(sdk_constr):
            cmds.parentConstraint(last_sdk, fk_ctrl_grp)
        cmds.connectAttr(f'{fk_ctrl}.rotate', f'{ctrl_sdk}.rotate', f=1)

    if fk and not ik:
        skeleton_grp = fstr('', SKELETON_GRP)
        parent_to(fkjnt_grp, skeleton_grp)
    else:
        fk_skeleton_grp = fstr('', SKELETON_GRP, TYPE_FK)
        parent_to(fkjnt_grp, fk_skeleton_grp)

        # IKFK Switch
        ikfk_switch = fstr(rigname, IKFK)
        for num in range(NUM_CTRL_FK):
            NN = num + 1
            fk_ctrl = fstr(rigname, CTRL, '', NN)
            # IKFK Divider
            add_attribute_enum(fk_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
            # Proxy IKFK Switch attribute from Cog
            add_attribute_enum(fk_ctrl, IKFK_SWITCH[0], IKFK_SWITCH[1],
                               pxy=f'{cog_ctrl}.{ikfk_switch}')

        # IKFK Condition
        ikfk_cond = fstr(rigname, IKFK_COND)
        create_condition(ikfk_cond, op=0, secondTerm=3)
        cmds.connectAttr(f'{cog_ctrl}.{ikfk_switch}', f'{ikfk_cond}.firstTerm', f=1)
        # FK Visibility
        cmds.connectAttr(f'{ikfk_cond}.outColorR', f'{fkroot_grp}.visibility', f=1)
        cmds.connectAttr(f'{ikfk_cond}.outColorR', f'{fkjnt_grp}.visibility', f=1)

        logger.info(f"DONE connecting FK '{rigname}'")

def connect_ik(rigname, fk, ik):
    '''
    Connect IK components
    Warning: Uses hardcoded names, check naming.
    '''
    if not ik:
        return
    logger.info(f"Connect IK '{rigname}'")

    cog_ctrl = fstr('', COG_CTRL)
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)
    ik_skeleton_grp = fstr('', SKELETON_GRP, TYPE_IK)
    ikjnt_grp = fstr(rigname, GRP, TYPE_IK)

    # Organize groups
    # Move IK joint group under IK skeleton group
    if not cmds.objExists(ikjnt_grp):
        ikjnt_grp = cmds.group(em=True, n=ikjnt_grp)
        parent_to(ikjnt_grp, ik_skeleton_grp)
    # Move IK joints under ikjnt_grp
    parent_to(JOINTS_IK[rigname][0], ikjnt_grp)
    # Move ikjnt_grp under ik_skeleton_grp
    parent_to(ikjnt_grp, ik_skeleton_grp)

    # Get IK controls
    # ik_controls = {'ik':[], 'float':[], 'spline': [], 'upvec':[]}
    ik_controls, ik_ctrlgrps = get_controls_ik(rigname)

    # Constrain spline controls
    driveattr, switch_cond_pts, switch_cond_vis, spline_constraints =\
        constrain_spline_controls(rigname, switch=IKFK_SWITCH)
    connect_spline(rigname, ik_controls, ik_ctrlgrps)
    logger.info(f"driveattr:'{driveattr}'\n" +\
            f"switch cond:{switch_cond_pts}" +\
            f"switch vis:{switch_cond_vis}" +\
            f"spline_constraints {spline_constraints}")
    # Move spline and scale grps under rig systems
    spline_grp = fstr(rigname, SPLINE_GRP, TYPE_IK)
    cluster_grp = fstr(rigname, CLUSTER_GRP)
    scale_grp = fstr(rigname, SCALE_GRP)
    parent_to(spline_grp, rig_systems_grp)
    parent_to(cluster_grp, spline_grp)
    parent_to(scale_grp, spline_grp)
    # Group Visibility
    set_visibility(spline_grp, 1, k=0, cb=1, l=0) # Show and cb visibility
    set_visibility(cluster_grp, 1, k=0, cb=1, l=0)
    set_visibility(scale_grp, 1, k=0, cb=1, l=0)

    # IKFK Switch
    ikfk_switch = fstr(rigname, IKFK)
    # IK control types: ik, float, spline
    for num in range(NUM_CTRL_IK):
        # Proxy IKFK Switch attribute from Cog
        ik_ctrl = ik_controls['ik'][num]
        add_attribute_enum(ik_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(ik_ctrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f'{cog_ctrl}.{ikfk_switch}')
        float_ctrl = ik_controls['float'][num]
        add_attribute_enum(float_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(float_ctrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f'{cog_ctrl}.{ikfk_switch}')
    for spline_ctrl in ik_controls['spline']:
        add_attribute_enum(spline_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(spline_ctrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f'{cog_ctrl}.{ikfk_switch}')

    # IKFK Condition
    ikfk_switch = fstr(rigname, IKFK)
    # IK condition and visibility nodes
    ikspline_cond = f'{rigname}_switch_ikspline{_COND}'
    ik_cond = f'{rigname}_switch_ik{_COND}'
    float_cond = f'{rigname}_switch_float{_COND}'
    create_condition(ikspline_cond, op=0, secondTerm=0) # Switch Enum 0
    create_condition(ik_cond, op=0, secondTerm=1) # Switch Enum 1
    create_condition(float_cond, op=0, secondTerm=2) # Switch Enum 2
    cmds.connectAttr(f'{cog_ctrl}.{ikfk_switch}', f'{ikspline_cond}.firstTerm', f=1)
    cmds.connectAttr(f'{cog_ctrl}.{ikfk_switch}', f'{ik_cond}.firstTerm', f=1)
    cmds.connectAttr(f'{cog_ctrl}.{ikfk_switch}', f'{float_cond}.firstTerm', f=1)
    # CleanUp Visibility condition not needed
    ikspline_vis = f'{rigname}_switch_ikspline{_VIS}{_COND}'
    ik_vis = f'{rigname}_switch_ik{_VIS}{_COND}'
    float_vis = f'{rigname}_switch_float{_VIS}{_COND}'
    for vis_cond in [ikspline_vis, ik_vis, float_vis]:
        remove(vis_cond) # REMOVE

    # IK Visibility
    for ikspline_ctrlgrp in ik_ctrlgrps['spline']:
        cmds.connectAttr(f'{ikspline_cond}.outColorR', f'{ikspline_ctrlgrp}.visibility', f=1)
    for idx in range(1, NUM_CTRL_IK+1):
        ik_ctrlgrp = fstr(rigname, SPLINE_IK_CTRL+'{_GRP}', TYPE_IK, idx)
        cmds.connectAttr(f'{ik_cond}.outColorR', f'{ik_ctrlgrp}.visibility', f=1)
        float_ctrlgrp = fstr(rigname, SPLINE_FLOAT_CTRL+'{_GRP}', TYPE_IK, idx)
        cmds.connectAttr(f'{float_cond}.outColorR', f'{float_ctrlgrp}.visibility', f=1)

    logger.info(f"DONE connecting IK '{rigname}'")

def connect_spline(rigname, ik_controls, ik_ctrlgrps):
    '''
    Clean up spline structure. Attribute visibility and organization.
    '''
    logger.info(f'ik_controls {ik_controls} ik_ctrlgrps {ik_ctrlgrps}')

    # Group clusters and ik handle objects under spline group
    # spline_list (str list): [ikhandle, effector, curve]
    spline_list = get_spline_handle(rigname, existing=False)
    curve = fstr(rigname, CURVE, TYPE_IK)
    # Create spline group
    spline_grp = fstr(rigname, SPLINE_GRP, TYPE_IK)
    create_group(spline_grp)
    # Parent ikhandle and curve under spline group
    parent_to(spline_list[0], spline_grp)
    parent_to(spline_list[2], spline_grp)

    # Move controls under basectrl
    basectrl = fstr(rigname, BASECTRL)
    contents = list()
    contents.append(ik_ctrlgrps['ik'][0])
    contents.extend(ik_ctrlgrps['float'])
    contents.extend([ik_ctrlgrps['spline'][0], ik_ctrlgrps['spline'][4],
                    ik_ctrlgrps['spline'][-1]]) # top, bot, mid_rot
    for obj in contents:
        parent_to(obj, basectrl)

    cmds.select(clear=True) # Deselect all


# CONSTRAINTS ==========================================================

def constrain_skeleton(rigname, fk, ik):
    '''
    Constrain FK IK skeleton to BN skeleton
    '''
    logger.info('Setting up Skeleton Constraints..')
    # CleanUp old constraints
    for bn_jnt in JOINTS_BN[rigname]:
        constraints = cmds.listConnections(bn_jnt, type='constraint') or []
        for constr in constraints:
            cmds.delete(constr) # Delete constraint

    if fk and not ik:
        for n in range(len(JOINTS_FK[rigname])):
            fk_jnt = JOINTS_FK[rigname][n]
            bn_jnt = JOINTS_BN[rigname][n]
            # Constrain skeleton - FK skeleton, BN skeleton
            cmds.parentConstraint(fk_jnt, bn_jnt, mo=True)
            cmds.scaleConstraint(fk_jnt, bn_jnt)

    elif ik and not fk:
        for n in range(len(JOINTS_IK[rigname])):
            ik_jnt = JOINTS_IK[rigname][n]
            bn_jnt = JOINTS_BN[rigname][n]
            # Constrain skeleton - IK skeleton, BN skeleton
            cmds.parentConstraint(ik_jnt, bn_jnt, mo=True)
            cmds.scaleConstraint(ik_jnt, bn_jnt)

    else:
        for n in range(len(JOINTS_FK[rigname])):
            fk_jnt = JOINTS_FK[rigname][n]
            ik_jnt = JOINTS_IK[rigname][n]
            bn_jnt = JOINTS_BN[rigname][n]
            # Constrain skeleton - FK, IK, BN skeleton
            bn_constr = cmds.parentConstraint(fk_jnt, ik_jnt, bn_jnt, mo=True)[0]
            sc_constr = cmds.scaleConstraint(fk_jnt, ik_jnt, bn_jnt)[0]
            # Set influence on BN constraint
            ikfk_cond = fstr(rigname, IKFK_COND) # IKFK Condition
            cmds.connectAttr(f'{ikfk_cond}.outColorR', f'{bn_constr}.{fk_jnt}W0', f=1)
            cmds.connectAttr(f'{ikfk_cond}.outColorG', f'{bn_constr}.{ik_jnt}W1', f=1)
            cmds.connectAttr(f'{ikfk_cond}.outColorR', f'{sc_constr}.{fk_jnt}W0', f=1)
            cmds.connectAttr(f'{ikfk_cond}.outColorG', f'{sc_constr}.{ik_jnt}W1', f=1)

def disconnect_skeleton(rigname, fk, ik):
    '''
    Disconnect skeleton and joint scale
    '''
    if fk:
        fk_jnts = JOINTS_FK[rigname]
        for fk_jnt in fk_jnts:
            disconnect_all(fk_jnt, source=True)
    if ik:
        ik_jnts = JOINTS_IK[rigname]
        for ik_jnt in ik_jnts:
            disconnect_all(ik_jnt, source=True)

def constrain_spline_controls(rigname, switch=IKFK_SWITCH):
    '''
    Create constraints for spline controls, condition nodes for switch modes.

    Arguments
        rigname (str): name of rig part
        switch (tuple): switch type (longName, niceName, enumName, dv)

    Return
        driveattr (str): switch attribute plug on cog ctrl
        switch_cond_pts (str list): condition nodes for switching parents
        switch_cond_vis (str list): condition nodes for switching visibility
        spline_constraints (str list): constraints created on spline clusters
    '''
    CTRLTYP = ['spline', 'ik', 'float']

    cog_ctrl = fstr('', COG_CTRL)
    driveattr = '' # Switch attribute plug
    switch_cond_pts = list() # Condition nodes for switching parents
    switch_cond_vis = list() # Condition nodes for switching visibility
    spline_constraints = list() # Constraints on spline clusters

    # Constrain IK Spline for start, end, mid
    # Build 2 constraints, the first between top, bot, mid controls,
    # and the second between mid_rot and top controls.
    spline_controls = [fstr(rigname, template, TYPE_IK) for template in SPLINE_CONTROLS]
    spline_ctrlgrps = [f'{ctrl}{_GRP}' for ctrl in spline_controls]
    # Constrain ikspline - bot_ctrl, top_ctrl, mid_grp
    cmds.parentConstraint(spline_controls[0], spline_controls[4], spline_ctrlgrps[2], mo=1)[0]
    # Constrain ikspline - mid_rot, top_grp
    cmds.parentConstraint(spline_controls[5], spline_ctrlgrps[4], mo=1)[0]

    # (_constrainToControls)
    # Get cluster handles
    cluster_handles = list()
    for NN in range(1, NUM_CTRL_IK+1):
        cluster_handle = fstr(rigname, CLUSTER_HANDLE, '', NN)
        cluster_handles.append(cluster_handle)

    # (constrainToControls) Create IKFK Switch attributes on cog ctrl
    # e.g. ----------| TAIL IKFK
    #      L Fintail | SplineIK / IK / Float / Fk
    #      R Fintail | SplineIK / IK / Float / Fk
    if not cmds.attributeQuery(IKFK_DIVIDER[0], n=cog_ctrl, ex=1):
        add_attribute_enum(cog_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
    # IKFK Switch attribute
    cog_ln_ikfk = fstr(rigname, IKFK) # e.g. L_fintail_ikfk
    cog_nn_ikfk = re.sub(r'[-_\s]+', ' ', rigname).title() # e.g. L Fintail
    driveattr = f'{cog_ctrl}.{cog_ln_ikfk}' # e.g. cog_ctrl.L_fintail_ikfk
    # Create Switch attribute as Enum list
    if not cmds.attributeQuery(cog_ln_ikfk, n=cog_ctrl, ex=1):
        add_attribute_enum(cog_ctrl, ln_ikfk, nn_ikfk, switch[2])
    logger.info(f'driveattr: {driveattr}')

    ik_controls, ik_ctrlgrps = get_controls_ik(rigname)

    # (constrainControls) Constrain spline to the controls. Set up switching later
    logger.info(f'cluster handles {cluster_handles}')
    for i, clstr in enumerate(cluster_handles):
        constrain_objs = list() # List of 5 objects, 4 controls and last cluster
        for ctrltyp in CTRLTYP:
            constrain_objs.append(ik_controls[ctrltyp][i]) # Get existing controls
        constrain_objs.append(clstr) # Append target object, cluster handle
        # Constrain cluster to controls
        cluster_constr = cmds.parentConstraint(constrain_objs, mo=1)[0]
        spline_constraints.append(cluster_constr)
    logger.info(f'spline_constraints {spline_constraints}')

    # (setupConditionNodesConstraints) Set up switching condition for constraints and vis
    for i, ctrltyp in enumerate(CTRLTYP):
        constr_list = list()
        vis_attr_list = list()
        for j in range(NUM_CTRL_IK): # Iterate through IK controls
            ik_control = ik_controls[ctrltyp][j]
            ik_ctrlgrp = f'{ik_control}{_GRP}'
            constr_list.append(f'{spline_constraints[j]}.{ik_control}W{i}')
            vis_attr_list.append(f'{ik_ctrlgrp}.visibility')
        if ctrltyp == 'spline':
            mid_rot_ctrl = fstr(rigname, SPLINE_MID_ROT, TYPE_IK)
            vis_attr_list.append(f'{mid_rot_ctrl}.visibility')
        # Create condition node connecting driveattr to drivenattrs
        logger.debug(f'driveattr {driveattr} \nconstr_list {constr_list} \nvis_attr_list {vis_attr_list}')
        name_cond_pts = fstr(rigname, f'{{TYPE}}{{rigname}}_switch_{ctrltyp}{_CST}{_COND}', TYPE_IK)
        name_cond_vis = fstr(rigname, f'{{TYPE}}{{rigname}}_switch_{ctrltyp}{_VIS}{_COND}', TYPE_IK)
        switch_cond_pts.append(create_condition_multi(
            driveattr, constr_list, i, name_cond_pts))
        switch_cond_vis.append(create_condition_multi(
            driveattr, vis_attr_list, i, name_cond_vis))

    # Constrain upvec
    upvec_bsegrp = ik_ctrlgrps['upvec'][0]
    upvec_endgrp = ik_ctrlgrps['upvec'][1]
    upvec_bsectrl = ik_controls['upvec'][0]
    upvec_endctrl = ik_controls['upvec'][1]
    cluster_bse_handle = fstr(rigname, CLUSTER_UPV_HANDLE, TAG='_base')
    cluster_end_handle = fstr(rigname, CLUSTER_UPV_HANDLE, TAG='_end')
    # Match upvec to first and last spline controls
    cmds.matchTransform(upvec_bsegrp, cluster_bse_handle, pos=1, rot=1, scl=0, piv=0)
    cmds.matchTransform(upvec_endgrp, cluster_end_handle, pos=1, rot=1, scl=0, piv=0)
    # Constrain upvec to cluster
    cmds.parentConstraint(upvec_bsectrl, cluster_bse_handle, mo=1)
    cmds.parentConstraint(upvec_endctrl, cluster_end_handle, mo=1)

    return driveattr, switch_cond_pts, switch_cond_vis, spline_constraints


# RENAME / REPLACE COMPONENTS ==========================================

def rename(source, target):
    if cmds.objExists(source):
        cmds.rename(source, target)
    else:
        logger.warning(f"{source} -> {target}. source '{source}' does not exist.")

def rename_components():
    '''
    Rename IK related controls and attributes.
    Warning: Uses hardcoded names, check naming convention.
    '''
    for rigname in RIGPARTS:
        # Rename: {rigname}_spineRig_srt -> ROOT_GRP
        rename(f'{rigname}_spineRig_srt', fstr('', ROOT_GRP))
        # Rename: {rigname}_splineIk_srt -> CLUSTER_GRP
        rename(f'{rigname}_splineIk_srt', fstr(rigname, CLUSTER_GRP))
        # Rename: {rigname}_cog_ctrl -> BASECTRL
        rename(f'{rigname}_cog_ctrl', fstr(rigname, BASECTRL))
        # Rename: {rigname}_cog_grp -> BASECTRL_GRP
        rename(f'{rigname}_cog_grp', fstr(rigname, BASECTRL_GRP))

        # Rename control groups
        basectrl = fstr(rigname, BASECTRL)
        controls = cmds.listRelatives(basectrl, ad=True, typ='transform') or []
        for ctrl in controls: # Rename control group
            if f'{_GRP}' in ctrl:
                if not f'{_CTRL}{_GRP}' in ctrl:
                    name = ctrl.replace(f'{_GRP}', f'{_CTRL}{_GRP}')
                    cmds.rename(ctrl, name)

        # CleanUp
        revik_ctrl_grp = f'{rigname}_revik_{NUM_CTRL_IK:02d}{_CTRL}{_GRP}'
        # Remove Rev IK control group
        remove(revik_ctrl_grp) # REMOVE

    replace_names = {
        'srt': 'grp',
        'spineRig1_': '',
        'spineRig': '',
        'splineIk': 'ik',
        'Handle': 'handle',
        'Effector': 'effector',
        'ikSplineCurve': 'ikspline_crv',
        'fkSpline': 'ik',
        'spineSpline': 'spline',
        'floatSpline': 'float',
        'cntrlBase': 'bot',
        'cntrlMid': 'mid',
        'rotMid': 'mid_rot',
        'cntrlTop': 'top',
        'Sml': '_sml',
        '__' : '_',
        HIER_SWITCH[0]: 'switch'
        }

    # Rename switch attributes
    transforms = cmds.ls(tr=True)
    for obj in transforms:
        # CleanUp old IKFK Switch attribute on controls
        if cmds.attributeQuery(HIER_SWITCH[0], n=obj, ex=1):
            cmds.deleteAttr(obj, at=HIER_SWITCH[0]) # REMOVE
            add_attribute_enum(obj, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
            add_attribute_enum(obj, IKFK_SWITCH[0], IKFK_SWITCH[1],
                               IKFK_SWITCH[2], IKFK_SWITCH[3])
        name = obj
        for tag, newtag in replace_names.items():
            name = name.replace(tag, newtag)
        if obj != name: # Replace name
            logger.debug(f"Rename component '{obj}' -> '{name}'")
            cmds.rename(obj, name)

    # Rename conditions
    conditions = cmds.ls(typ='condition')
    for cond in conditions:
        if HIER_SWITCH[0] in cond:
            name = cond
            for tag, newtag in replace_names.items():
                name = name.replace(tag, newtag)
            if cond != name:
                logger.debug(f"Rename condition '{cond}' -> '{name}'")
                cmds.rename(cond, name)


# RUN: RIG TAIL ========================================================

def rig_tail_test(rigname, start_jnt=None, end_jnt=None,
                  root=None, connect=True, fk=True, ik=True):
    '''
    Test rig_tail_fk on single FK joint chain.
    Example
        rt.rig_tail_test('tail', root='tail_spline_grp', connect=False, fk=True, ik=True)
        rt.rig_tail_test('tail', root='tail_spline_grp', connect=True)
        rt.rig_tail_test('tail', root='tail_spline_grp', connect=True, fk=False, ik=True)
    '''
    global RIGPARTS, FK_START_JNT, FK_END_JNT
    RIGPARTS = [rigname]
    set_joints(rigname, start_jnt, end_jnt)

    set_root(root)
    setup_rig_components(fk, ik)
    build_rig_tail(rigname, fk, ik)
    if connect:
        connect_rig_tail(fk, ik)

def rig_tail_selected(root=None, connect=True, fk=True, ik=True):
    '''
    Run rig_tail() on user selected joints.
    Select all top joints on FK joint chains.
    The selected joints will be the start_jnt.
    Rigname and end_jnt are auto detected.
    '''
    global RIGPARTS
    RIGPARTS = list()
    selected = cmds.ls(sl=True)
    if not selected:
        logger.error('Please select joint to create tail rig.')

    set_root(root)
    for jnt in selected:
        if cmds.objectType(jnt, i='joint'):
            rigname = get_rigname(jnt, JNT, underscore=True)
            RIGPARTS.append(rigname)
            set_joints(rigname, start_jnt=jnt, end_jnt=None)
        else:
            logger.warning(f"Selected object '{jnt}' is not a joint.")

    setup_rig_components(fk, ik)
    for rigname in RIGPARTS:
        build_rig_tail(rigname, fk, ik)
    if connect:
        connect_rig_tail(fk, ik)

def main(root=None, connect=True, fk=True, ik=True):
    '''
    Run rig_tail() on all components in RIGPARTS.
    Arguments
        root (str): Name of rig; name of main group
        connect (bool): Connect IKFK attributes and constraints
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    for rigname in RIGPARTS:
        set_joints(rigname)
    set_root(root)
    setup_rig_components(fk, ik)
    for rigname in RIGPARTS:
        build_rig_tail(rigname, fk, ik)
    if connect:
        connect_rig_tail(fk, ik)
