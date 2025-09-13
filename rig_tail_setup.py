'''
# rig_tail_setup.py
author: Daisy Jane Lee @dayzl

Setup for Rig Tail
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import rig_tail_constants as cst
from rig_tail_constants import *
import rig_tail_util as rt_util
from rig_tail_control import create_circle_control
import re

logger = logger_setup(__name__)


# SETUP ================================================================

def set_root(new_root):
    if new_root:
        cst.ROOT = new_root.split(GRP, 1)[0]
        root_grp = rt_util.fstr('', ROOT_GRP)
        logger.info(f"Set ROOT '{cst.ROOT}'")
        if new_root != root_grp:
            cmds.rename(new_root, root_grp)
    else:
        logger.error(f"root '{new_root}' does not exist.")

def set_joints(rigname, start_jnt=None, end_jnt=None):
    '''
    Set start and end joints. Store joint names in dict.
    Assume that joints follow Naming Template
    '''
    logger.info(f"{rigname} - start_jnt:'{start_jnt}' end_jnt:'{end_jnt}'")

    if not start_jnt: # Assume type is FK joint if start_jnt not provided
        start_jnt = rt_util.fstr(rigname, JOINT, TYPE_FK, NN=0)
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
                         "Make sure that Type Labels are set correctly.")

    if start_jnt and not cmds.objExists(start_jnt):
        logger.error(f"start_jnt '{start_jnt}' does not exist.\n"\
                "Please select start joint.")
    if end_jnt and not cmds.objExists(end_jnt):
        logger.error(f"end_jnt '{end_jnt}' does not exist.\n"\
                "Please select end joint.")

    joints = get_joint_hierarchy(start_jnt, end_jnt)
    cst.JOINTS_FK[rigname] = rename_joints(rigname, joints, TYPE_FK)
    cst.JOINTS_IK[rigname] = rename_joints(rigname, joints, TYPE_IK)
    cst.JOINTS_BN[rigname] = rename_joints(rigname, joints, TYPE_BN)
    logger.debug(f"JOINTS_FK[{rigname}] = {cst.JOINTS_FK[rigname]}")
    logger.debug(f"JOINTS_IK[{rigname}] = {cst.JOINTS_IK[rigname]}")
    logger.debug(f"JOINTS_BN[{rigname}] = {cst.JOINTS_BN[rigname]}")

def rename(source, target):
    if cmds.objExists(source):
        cmds.rename(source, target)
    else:
        logger.warning(f"{source} -> {target}. source '{source}' does not exist.")

def setup_rig_components(fk, ik):
    '''
    Create groups, root control, cog control.
    Connect root and cog.
    Rename components for IK if necessary.
    Make sure that RIGPARTS is set.
    '''
    logger.info('-----------------------------------------------------')
    logger.info('SETUP rig components..')
    root_grp = rt_util.fstr('', ROOT_GRP)
    root_ctrl = rt_util.fstr('', ROOT_CTRL)
    cog_ctrl = rt_util.fstr('', COG_CTRL)
    geometry_grp = rt_util.fstr('', GEOMETRY_GRP)
    control_grp = rt_util.fstr('', CONTROL_GRP)
    skeleton_grp = rt_util.fstr('', SKELETON_GRP)
    rig_systems_grp = rt_util.fstr('', RIG_SYSTEMS_GRP)
    locators_grp = rt_util.fstr('', LOCATORS_GRP)

    if fk and not ik:
        groups = [geometry_grp, control_grp, skeleton_grp, rig_systems_grp, locators_grp]
    else:
        fk_skeleton_grp = rt_util.fstr('', SKELETON_GRP, TYPE_FK)
        ik_skeleton_grp = rt_util.fstr('', SKELETON_GRP, TYPE_IK)
        groups = [geometry_grp, control_grp, skeleton_grp,
                  fk_skeleton_grp, ik_skeleton_grp,
                  rig_systems_grp, locators_grp]

    # Root group must exist
    rt_util.create_group(root_grp)
    # Create groups
    for group in groups:
        if not cmds.objExists(group):
            group = cmds.group(em=True, n=group)
            logger.debug(f"created group {group}")
            if group == geometry_grp:
                meshes = get_geometry_from_scene()
                for geo in meshes:
                    rt_util.unbind_skincluster(geo, typ='geo')
                    rt_util.parent_to(geo, geometry_grp)
            elif group == control_grp:
                controls = get_controls_from_scene()
                for ctrl in controls:
                    rt_util.parent_to(ctrl, control_grp)
            elif group == skeleton_grp:
                joints = get_joints_from_scene()
                for joint in joints:
                    rt_util.parent_to(joint, skeleton_grp)
            rt_util.parent_to(group, root_grp)

    if ik:
        rename_components() # Replace names
    if not cmds.objExists(root_ctrl): # Create root_ctrl
        create_circle_control(root_ctrl, ROOT_CTRL_SZ, nr=(0,1,0), color='lightgreen')
        rt_util.parent_to(root_ctrl, control_grp)
    if not cmds.objExists(cog_ctrl): # Create cog_ctrl
        create_circle_control(cog_ctrl, COG_CTRL_SZ, nr=(0,1,0), color='cyan')
        rt_util.parent_to(cog_ctrl, root_ctrl)


# GENERAL ==============================================================

def rename_components():
    '''
    Rename IK related controls and attributes.
    Warning: Uses hardcoded names, check naming convention.
    '''
    for rigname in cst.RIGPARTS:
        # Rename: {rigname}_spineRig_srt -> ROOT_GRP
        rename(f"{rigname}_spineRig_srt", rt_util.fstr('', ROOT_GRP))
        # Rename: {rigname}_cog_ctrl -> BASECTRL
        rename(f"{rigname}_cog_ctrl", rt_util.fstr(rigname, BASECTRL))
        # Rename: {rigname}_cog_grp -> BASECTRL_GRP
        rename(f"{rigname}_cog_grp", rt_util.fstr(rigname, BASECTRL_GRP))

        # Rename control groups
        basectrl = rt_util.fstr(rigname, BASECTRL)
        if cmds.objExists(basectrl):
            controls = cmds.listRelatives(basectrl, ad=True, typ='transform') or []
            for ctrl in controls: # Rename control group
                if f"{GRP}" in ctrl:
                    if not f"{CTRL}{GRP}" in ctrl:
                        name = ctrl.replace(f"{GRP}", f"{CTRL}{GRP}")
                        cmds.rename(ctrl, name)

        # Remove Rev IK control group
        rt_util.remove(f"{rigname}_revik_{NUM_CTRL_IK:02d}{CTRL}{GRP}")

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
            cmds.deleteAttr(obj, at=HIER_SWITCH[0])
            rt_util.add_attribute_enum(obj, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
            rt_util.add_attribute_enum(obj, IKFK_SWITCH[0], IKFK_SWITCH[1],
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


# JOINTS ===============================================================

def rename_joints(rigname, joints, jnt_type):
    logger.debug(f"rigname:'{rigname}' joints:'{jnt_type}'")
    jnts = list()
    for jnt in joints:
        NN = rt_util.get_index_from_name(jnt)
        jnt_name = rt_util.fstr(rigname, JOINT, jnt_type, NN)
        logger.debug(f"jnt:'{jnt}' jnt_name:'{jnt_name}' jnt_type:'{jnt_type}'")
        if jnt_type in jnt: # joint has same TYPE
            if jnt == jnt_name:
                jnt = cmds.rename(jnt, jnt_name)
        else: # different TYPE
            if not cmds.objExists(jnt_name):
                jnt = cmds.duplicate(jnt, n=jnt_name, po=True)[0]
        if jnts:
            rt_util.parent_to(jnt_name, jnts[-1])
        jnts.append(jnt_name)
    return jnts


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
