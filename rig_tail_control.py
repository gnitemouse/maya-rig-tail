'''
# rig_tail_control.py
author: Daisy Jane @dayzl

Control methods for Rig Tail
'''

import maya.cmds as cmds
from logger_config import logger_setup
from rig_tail_constants import *
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_math as rt_mat
import rig_tail_util as rt_utl  # Backward compatibility

logger = logger_setup(__name__)


# CREATE CONTROLS ======================================================

def create_root_cog():
    '''
    Create root and cog controls.

    Return
        root_grp (str): Root group
        root_ctrl (str): Root control
        cog_ctrl (str): Cog control
    '''
    root_grp = rt_nam.fstr('', ROOT_GRP)
    root_ctrl = rt_nam.fstr('', ROOT_CTRL)
    cog_ctrl = rt_nam.fstr('', COG_CTRL)
    rt_mya.create_group(root_grp) # Create root group
    if not cmds.objExists(root_ctrl): # Create root control
        create_circle_control(root_ctrl, ROOT_CTRL_SZ, nr=(0,1,0), color='lightgreen')
    if not cmds.objExists(cog_ctrl): # Create cog control
        create_circle_control(cog_ctrl, COG_CTRL_SZ, nr=(0,1,0), color='cyan')
    return root_grp, root_ctrl, cog_ctrl

def create_basectrl(rigname, up_axis=None):
    '''
    Create base control for the tail rig.

    Arguments
        rigname (str): Name of rig component
        up_axis (str): Axis orientation (e.g. '+y', '-z')

    Return
        basectrl (str): Base control
        basectrl_grp (str): Base control group
    '''
    joints = rt_cst.JOINTS_BN[rigname]
    cog_ctrl = rt_nam.fstr('', COG_CTRL)
    basectrl_grp = rt_nam.fstr(rigname, BASECTRL_GRP)
    basectrl = rt_nam.fstr(rigname, BASECTRL)
    basectrl, basectrl_grp = create_control(basectrl, group=basectrl_grp,
                                            match_to=joints[0], parent=cog_ctrl,
                                            size=BASE_CTRL_SZ, nr=(1,0,0),
                                            color='magenta', shape='circle')
    rt_utl.opm(basectrl_grp)
    if not up_axis:
        up_axis = rt_mat.get_axis_orientation(joints)
    rot_offset = ROT_AXIS_DICT[up_axis]
    logger.debug(f"up_axis '{up_axis}' rot_offset '{rot_offset}'")
    cmds.setAttr(f'{basectrl_grp}.rotate', rot_offset[0], rot_offset[1], rot_offset[2])
    rt_utl.opm(basectrl_grp)
    return basectrl, basectrl_grp

def create_circle_control(name, size, nr=(1,0,0), color='darkgrey'):
    '''
    Create circle curve control.

    Arguments
        name (str): Control name
        size (float): Control radius
        nr (tuple): Normal direction for circle plane
        color (str): Control color from COLOR_OVERRIDE dict

    Return
        circle_ctrl (str): Circle control name
    '''
    circle_ctrl = cmds.circle(n=name, nr=nr, r=size)[0]
    rt_nam.rename_shapes(circle_ctrl, typ='ctrl')
    set_control_color(circle_ctrl, color=color)
    cmds.delete(circle_ctrl, ch=1)
    return circle_ctrl

def create_sphere_control(name, size=1, color='darkgrey'):
    '''
    Create sphere curve control from three circles.

    Arguments
        name (str): Control name
        size (float): Control radius
        color (str): Control color from COLOR_OVERRIDE dict

    Return
        sphere_ctrl (str): Sphere control name
    '''
    sphere_ctrl = cmds.circle(n=name, nr=(1,0,0), r=size)[0]
    circle_01 = cmds.circle(n=name, nr=(0,1,0), r=size)[0]
    circle_02 = cmds.circle(n=name, nr=(0,0,1), r=size)[0]
    shape_01 = cmds.listRelatives(circle_01, s=1)[0]
    shape_02 = cmds.listRelatives(circle_02, s=1)[0]
    cmds.parent(shape_01, sphere_ctrl, r=1, s=1)
    cmds.parent(shape_02, sphere_ctrl, r=1, s=1)
    cmds.delete(circle_01)
    cmds.delete(circle_02)
    rt_nam.rename_shapes(sphere_ctrl, typ='ctrl')
    set_control_color(sphere_ctrl, color=color)
    cmds.delete(sphere_ctrl, ch=1)
    return sphere_ctrl

def create_cube_control(name, size, color='darkgrey'):
    '''
    Create cube curve control.

    Arguments
        name (str): Control name
        size (float): Control size
        color (str): Control color from COLOR_OVERRIDE dict

    Return
        cube_ctrl (str): Cube control name
    '''
    cube_ctrl = cmds.curve(d=1, p=CUBE_CTRL_PTS, n=name)
    rt_nam.rename_shapes(cube_ctrl, typ='ctrl')
    set_control_color(cube_ctrl, color=color)
    cmds.xform(cube_ctrl, s=(size,size,size))
    cmds.makeIdentity(cube_ctrl, apply=1, t=1, r=1, s=1, jo=1)
    cmds.delete(cube_ctrl, ch=1)
    return cube_ctrl

def set_control_color(control, color='neonblue'):
    '''
    Set control color using Maya's override system.

    Arguments
        control (str): Control name
        color (str): Color name from COLOR_OVERRIDE dict
    '''
    cmds.setAttr(f'{control}.overrideEnabled', 1)
    cmds.setAttr(f'{control}.overrideRGBColors', 0)
    cmds.setAttr(f'{control}.overrideColor', COLOR_OVERRIDE[color])

def create_control(control, group=None, match_to=None, parent=None,
             size=1, nr=(1,0,0), color='darkcyan', shape=None):
    '''
    Create a control with group hierarchy.

    Arguments
        control (str): Control name
        group (str): Group name (auto-generated if None)
        match_to (str): Object to match transforms to
        parent (str): Parent object for group
        size (float): Control size
        nr (tuple): Normal direction for circle controls
        color (str): Control color from COLOR_OVERRIDE dict
        shape (str): Control shape ('circle', 'sphere', 'cube', None=empty group)

    Return
        control (str): Control name
        group (str): Group name
    '''
    logger.debug(f'{control}, {match_to}, {parent}, {size}, {nr}, {color}, {shape}')
    # Create control group
    groupname = group if group else f'{control}_{GRP}'
    group = rt_mya.create_group(groupname)
    if parent:
        rt_utl.parent_to(group, parent)
    if match_to: # Match transforms position and rotation
        rt_utl.reset_opm(group)
        rt_utl.reset_transforms(group)
        cmds.matchTransform(group, match_to)

    # Create control or handle existing control
    children_to_restore = []
    if cmds.objExists(control):
        logger.debug(f"Control exists '{control}', will recreate")
        # Store children (full paths to avoid name conflicts)
        children = cmds.listRelatives(control, typ='transform', fullPath=True) or []
        if children:
            # Unparent to world temporarily
            cmds.parent(children, world=True)
            children_to_restore = [c.split('|')[-1] for c in children]  # Short names

        # Delete the old control completely
        cmds.delete(control)

    # Create new control curve (always fresh)
    if shape == 'circle':
        create_circle_control(control, size, nr=nr, color=color)
    elif shape == 'sphere':
        create_sphere_control(control, size, color=color)
    elif shape == 'cube':
        create_cube_control(control, size, color=color)
    else:
        rt_mya.create_group(control)

    # Parent control to group
    rt_utl.parent_to(control, group, r=1)
    cmds.delete(control, ch=1)

    # Restore children
    for child_name in children_to_restore:
        if cmds.objExists(child_name):
            cmds.parent(child_name, control)

    return control, group

def create_control_match_list(rigname, matchlist, template_ctrl, template_grp=None,
                              typ='', parent=None, nest_controls=False,
                              size=1, color='darkcyan', shape=None):
    '''
    Create controls to match position of a list of objects.

    Arguments
        rigname (str): Name of rig component
        matchlist (list): Objects to match transforms (e.g. joints)
        template_ctrl (str): Naming template for controls
        template_grp (str): Naming template for control groups
        typ (str): Type option (TYPE_FK, TYPE_IK, TYPE_BN, '')
        parent (str): Name of parent object
        nest_controls (bool): Whether to nest controls hierarchically
        size (float): Size of controls created
        color (str): Color of controls created
        shape (str): Shape of controls ('circle', 'sphere', 'cube')

    Return
        controls (list): List of control names
        groups (list): List of control group names
    '''
    logger.debug(f'{rigname},\n{matchlist},\n{template_ctrl}, {template_grp}, {typ},\n{parent}, {nest_controls}, {size}, {color}, {shape}')
    controls = list()
    groups = list()
    for i, obj in enumerate(matchlist):
        nn = i if typ == TYPE_FK else i+1
        ctrl = rt_nam.fstr(rigname, template_ctrl, typ, nn)
        grp = rt_nam.fstr(rigname, template_grp, typ, nn) if template_grp else None
        control, group = create_control(ctrl, grp, match_to=obj, parent=parent,
                                        size=size, color=color, shape=shape)
        if nest_controls:
            parent = control
        controls.append(control)
        groups.append(group)
    return controls, groups

def create_controls_fk(rigname, joints, jnt_pos):
    '''
    Create NUM_CTRL_FK Variable FK controls and individual FK joint controls.
    Variable FK controls are distributed evenly along the FK chain.
    Individual FK joint controls are created at each joint.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints
        jnt_pos (list): List of joint world positions

    Return
        varfk_ctrls (list): List of Variable FK control names
    '''
    logger.info(f"{rigname}: Create FK controls")
    basectrl = rt_nam.fstr(rigname, BASECTRL)
    fkroot_grp = rt_nam.fstr(rigname, CTRLROOT_GRP, TYPE_FK)
    fkjnt_grp = rt_nam.fstr(rigname, GROUP, TYPE_FK)

    if cmds.objExists(fkjnt_grp):
        # Delete constraint on fkjnt_grp
        fkjnt_constraint = cmds.listRelatives(fkjnt_grp, typ='constraint') or []
        for constraint in fkjnt_constraint:
            cmds.delete(constraint)

    # Create FK root group
    rt_mya.create_group(fkroot_grp)
    rt_utl.parent_to(fkroot_grp, basectrl) # Move fkroot_grp under basectrl
    rt_utl.match_transform(fkroot_grp, basectrl)

    # Calculate indices evenly distributed throughout FK chain
    indices = list(rt_mat.linspace(0, len(jnt_pos)-1, NUM_CTRL_FK+2))
    positions = [joints[round(indices[i])] for i in range(1, NUM_CTRL_FK+1)]
    # Create variable FK controls
    varfk_ctrls, varfk_ctrl_grps = create_control_match_list(rigname,
                                                             positions,
                                                             template_ctrl=CONTROL,
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

    # Create individual FK joint controls
    fk_ctrls, fk_ctrl_grps = create_control_match_list(rigname,
                                                       joints,
                                                       template_ctrl=CONTROL,
                                                       template_grp=CTRL_GRP,
                                                       typ=TYPE_FK,
                                                       parent=fkroot_grp,
                                                       nest_controls=True,
                                                       size=FK_CTRL_SZ,
                                                       color='pink',
                                                       shape='circle')

    # Set FK control visibility (hide translate, scale)
    set_attributes_visibility_fk(varfk_ctrls)
    set_attributes_visibility_fk(fk_ctrls)
    return varfk_ctrls

def create_controls_ik(rigname, joints, clusters, duplicate_ends=True, scale=1):
    '''
    Create NUM_CTRL_IK IK controls to drive the IK spline.
    Creates three sets of controls: IK, Float, and Spline.
    Also creates up-vector controls for twist.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints
        clusters (list): List of (cluster_node, cluster_handle) tuples
        duplicate_ends (bool): Whether first and last CVs are upvec clusters
        scale (float): Scale multiplier for control size

    Return
        ik_controls (dict): Dict of control types -> control lists
            Keys: 'ik', 'float', 'spline', 'upvec'
        ik_ctrlgrps (dict): Dict of control types -> control group lists
            Keys: 'ik', 'float', 'spline', 'upvec'
    '''
    logger.info(f"{rigname}: Create IK controls")
    basectrl = rt_utl.fstr(rigname, BASECTRL)

    all_cluster_handles = [x[1] for x in clusters]
    if duplicate_ends:
        cluster_handles = all_cluster_handles[2:]
        cluster_handles_upv = [all_cluster_handles[0], all_cluster_handles[1]]
    else:
        cluster_handles = all_cluster_handles
        cluster_handles_upv = [all_cluster_handles[0], all_cluster_handles[-1]]
    logger.debug(f'Cluster Handles: {len(cluster_handles)} {cluster_handles}')

    # Create spline controls
    controls_ik, groups_ik = create_spline_controls_ik(
        rigname, cluster_handles, basectrl, scale)
    controls_float, groups_float = create_spline_controls_float(
        rigname, cluster_handles, basectrl, scale)
    controls_spline, groups_spline = create_spline_controls_spline(
        rigname, cluster_handles, basectrl, scale)

    # Up vector controls
    controls_upv, groups_upv = create_spline_up_vectors(rigname, cluster_handles_upv, scale)

    ik_controls = {
        'ik': controls_ik,
        'float': controls_float,
        'spline': controls_spline,
        'upvec': controls_upv
        }
    ik_ctrlgrps = {
        'ik': groups_ik,
        'float': groups_float,
        'spline': groups_spline,
        'upvec': groups_upv
        }

    set_attributes_visibility_ik(ik_controls)
    return ik_controls, ik_ctrlgrps

def create_spline_controls_ik(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build IK mode controls for spline clusters.
    Creates NUM_CTRL_IK nested controls that aim toward each other.

    Arguments
        rigname (str): Name of rig component
        cluster_handles (list): Cluster handle names to match positions
        orient_world (str): Object for aim constraint world up (-z axis)
        scale (float): Scale multiplier for controls

    Return
        controls (list): List of IK control names
        groups (list): List of IK control group names
    '''
    logger.info(f"{rigname}: Create IK spline controls - IK")
    controls, groups = create_control_match_list(rigname,
                                                 cluster_handles,
                                                 template_ctrl=SPLINE_IK_CTRL,
                                                 typ=TYPE_IK,
                                                 parent=orient_world,
                                                 nest_controls=True,
                                                 size=IK_CTRL_SZ*scale,
                                                 color='neonyellow',
                                                 shape='cube')
    orient_control_aims(groups, orient_world)
    return controls, groups

def create_spline_controls_float(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build Float mode controls for spline clusters.
    Creates NUM_CTRL_IK independent (non-nested) controls.

    Arguments
        rigname (str): Name of rig component
        cluster_handles (list): Cluster handle names to match positions
        orient_world (str): Object for aim constraint world up (-z axis)
        scale (float): Scale multiplier for controls

    Return
        controls (list): List of Float control names
        groups (list): List of Float control group names
    '''
    logger.info(f"{rigname}: Create IK spline controls - Float")
    controls, groups = create_control_match_list(rigname,
                                                 cluster_handles,
                                                 template_ctrl=SPLINE_FLOAT_CTRL,
                                                 typ=TYPE_IK,
                                                 parent=orient_world,
                                                 nest_controls=False,
                                                 size=IK_CTRL_SZ*scale,
                                                 color='cyan',
                                                 shape='cube')
    orient_control_aims(groups, orient_world)
    return controls, groups

def create_spline_controls_spline(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build Spline IK mode controls for spline clusters.
    Creates 5 main controls plus a rotation offset mid control.
    SPLINE_CONTROLS = [bot, bot_sml, mid, top_sml, top, mid_rot]

    Control hierarchy:
    - bot_sml parents under bot
    - top_sml parents under top
    - top parents under mid_rot
    - mid_rot controls middle rotation offset

    Arguments
        rigname (str): Name of rig component
        cluster_handles (list): Cluster handle names (must be NUM_CTRL_IK length)
        orient_world (str): Object for aim constraint world up (-z axis)
        scale (float): Scale multiplier for controls

    Return
        controls (list): List of Spline control names (length 6)
        groups (list): List of Spline control group names (length 6)
    '''
    logger.info(f"{rigname}: Create IK Spline controls - SplineIK")
    logger.debug(f'Cluster Handles: {len(cluster_handles)} {cluster_handles}')
    if len(cluster_handles) != NUM_CTRL_IK:
        logger.error(f'Need {NUM_CTRL_IK} Cluster Handles')

    controls = list()
    groups = list()
    # Create spline controls matched to clusters
    for i, template in enumerate(SPLINE_CONTROLS):
        ctrlname = rt_utl.fstr(rigname, template, TYPE_IK)
        if 'mid_rot' in ctrlname: # spline_mid_rot. Same position as spline_mid
            control, group = create_control(ctrlname, match_to=cluster_handles[2],
                                            parent=orient_world,
                                            size=SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neongreen', shape='sphere')
        else: # spline_bot, spline_bot_sml, spline_mid, spline_top_sml, spline_top
            control, group = create_control(ctrlname, match_to=cluster_handles[i],
                                            parent=orient_world,
                                            size=SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neonred', shape='cube')
        controls.append(control)
        groups.append(group)
    orient_control_aims(groups, orient_world)

    rt_utl.parent_to(groups[1], controls[0]) # Parent bot_sml to bot
    rt_utl.parent_to(groups[3], controls[4]) # Parent top_sml to top
    rt_utl.parent_to(groups[4], controls[5]) # Parent top to mid_rot

    return controls, groups

def create_spline_up_vectors(rigname, cluster_handles, scale=1):
    '''
    Build up-vector controls for twist on first and last controls.
    Creates two sphere controls offset from the base and end positions.

    Arguments
        rigname (str): Name of rig component
        cluster_handles (list): [base_cluster_handle, end_cluster_handle]
        scale (float): Scale multiplier for controls

    Return
        controls_upv (list): [upvec_base_ctrl, upvec_end_ctrl]
        groups_upv (list): [upvec_base_grp, upvec_end_grp]
    '''
    basectrl = rt_utl.fstr(rigname, BASECTRL)
    upvec_bsectrl = rt_utl.fstr(rigname, UPV_CTRL, TAG='base')
    upvec_endctrl = rt_utl.fstr(rigname, UPV_CTRL, TAG='end')
    upvec_bsegrp = rt_utl.fstr(rigname, UPV_CTRLGRP, TAG='base')
    upvec_endgrp = rt_utl.fstr(rigname, UPV_CTRLGRP, TAG='end')

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
                                                 match_to=cluster_handles[1],
                                                 parent=basectrl,
                                                 size=SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere')

    upvec_bsectrl_shapes = cmds.listRelatives(upvec_bsectrl, s=True)
    upvec_endctrl_shapes = cmds.listRelatives(upvec_endctrl, s=True)
    # Offset shape CVs
    for shape in upvec_bsectrl_shapes:
        tr = (SPLINE_BOT_SZ + 0.5) * scale
        cmds.move(0,tr,0, f'{shape}.cv[*]', r=True, objectSpace=True)
    for shape in upvec_endctrl_shapes:
        tr = (SPLINE_TOP_SZ + 0.5) * scale
        cmds.move(0,-tr,0, f'{shape}.cv[*]', r=True, objectSpace=True)
    return [upvec_bsectrl, upvec_endctrl], [upvec_bsegrp, upvec_endgrp]


# GET CONTROLS =========================================================

def get_controls_all(rigname, fk=True, ik=True, bn=True,
                     include_cog=False, include_root=False, include_basectrl=False):
    '''
    Get all FK, IK, BN controls under root group.

    Arguments
        rigname (str): Name of rig component
        fk (bool): Include FK controls
        ik (bool): Include IK controls
        bn (bool): Include BN controls
        include_cog (bool): Include cog control
        include_root (bool): Include root control
        include_basectrl (bool): Include basectrl

    Return
        controls (list): List of control names
    '''
    root_ctrl = rt_utl.fstr('', ROOT_CTRL)
    cog_ctrl = rt_utl.fstr('', COG_CTRL)
    basectrl = rt_utl.fstr(rigname, BASECTRL)
    controls = list()

    all_controls = get_control_hierarchy(root_ctrl)
    for ctrl in all_controls:
        if include_root and ctrl == root_ctrl:
            controls.append(ctrl)
        elif include_cog and ctrl == cog_ctrl:
            controls.append(ctrl)
        elif include_basectrl and ctrl == basectrl:
            controls.append(ctrl)
        elif fk and TYPE_FK in ctrl:
            controls.append(ctrl)
        elif ik and TYPE_IK in ctrl:
            controls.append(ctrl)
        elif bn and TYPE_BN in ctrl:
            controls.append(ctrl)
    return controls

def get_controls_ik(rigname):
    '''
    Get IK controls and IK control groups for a rig component.

    Arguments
        rigname (str): Name of rig component

    Return
        ik_controls (dict): Dict of control types -> control lists
            Keys: 'ik', 'float', 'spline', 'upvec'
        ik_ctrlgrps (dict): Dict of control types -> control group lists
            Keys: 'ik', 'float', 'spline', 'upvec'
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
            ctrl = rt_utl.fstr(rigname, ctrl_template[i], TYPE_IK, NN)
            ctrlgrp = f'{ctrl}_{GRP}'
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
    for ctrl_template in SPLINE_CONTROLS:
        ctrl = rt_utl.fstr(rigname, ctrl_template, TYPE_IK)
        ctrlgrp = f'{ctrl}_{GRP}'
        if cmds.objExists(ctrl):
            ik_controls['spline'].append(ctrl)
        else:
            logger.warning(f"ctrl '{ctrl}' does not exist")
        if cmds.objExists(ctrlgrp):
            ik_ctrlgrps['spline'].append(ctrlgrp)
        else:
            logger.warning(f"ctrlgrp '{ctrlgrp}' does not exist")

    # Type upvec
    upvec_bsectrl = rt_utl.fstr(rigname, UPV_CTRL, TAG='base')
    upvec_endctrl = rt_utl.fstr(rigname, UPV_CTRL, TAG='end')
    upvec_bsegrp = rt_utl.fstr(rigname, UPV_CTRLGRP, TAG='base')
    upvec_endgrp = rt_utl.fstr(rigname, UPV_CTRLGRP, TAG='end')
    ik_controls['upvec'] = [upvec_bsectrl, upvec_endctrl]
    ik_ctrlgrps['upvec'] = [upvec_bsegrp, upvec_endgrp]

    return ik_controls, ik_ctrlgrps

def get_control_hierarchy(control, end_control=None):
    '''
    Get all controls in hierarchy recursively.
    Assume controls are labeled as CTRL.

    Arguments
        control (str): Starting control/group
        end_control (str): Stop recursion at this control

    Return
        controls (list): List of control names found
    '''
    controls = list()
    if control == end_control:
        return controls
    elif cmds.objectType(control, i='transform') and CTRL in control:
        controls = [control]

    children = cmds.listRelatives(control, typ='transform') or []
    for child in children:
        ct = get_control_hierarchy(child, end_control)
        controls.extend(ct)
    return controls


# CONTROL ATTRIBUTES ===================================================

def add_fk_attributes_to_controls(controls, joints):
    '''
    Add control attributes to Variable FK controls.
    Called after creating controls for the FK joint chain.

    Attributes added:
        orig_position (float): Original control position (0-10), locked for reference
        position (float): Current control position (0-10), keyable
        falloff (float): Falloff range (0.1-10), default 2, keyable
        num_joints (float): Number of joints affected (0 to len(joints)-1), channelBox

    Arguments
        controls (list): List of Variable FK control names
        joints (list): List of joints for calculating position
    '''
    logger.debug('Add FK control attributes')
    for ctrl in controls:
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

        logger.debug(f"Added attributes to FK control '{ctrl}'")

def set_attributes_visibility_fk(fk_controls):
    '''
    Hide translate, scale on FK controls.
    Show rotate, visibility as keyable.

    Arguments
        controls (list): List of FK control names
    '''
    for ctrl in fk_controls:
        logger.debug(f"Set FK control attribute visibility for {ctrl}")
        # Hide translate
        for axis in 'XYZ':
            if cmds.attributeQuery(f'translate{axis}', n=ctrl, ex=1):
                cmds.setAttr(f'{ctrl}.translate{axis}', k=0, cb=0, l=1)
        # Hide scale
        for axis in 'XYZ':
            if cmds.attributeQuery(f'scale{axis}', n=ctrl, ex=1):
                cmds.setAttr(f'{ctrl}.scale{axis}', k=0, cb=0, l=1)
        # Show rotate
        for axis in 'XYZ':
            if cmds.attributeQuery(f'rotate{axis}', n=ctrl, ex=1):
                cmds.setAttr(f'{ctrl}.rotate{axis}', k=1, cb=0, l=0)
        # Show visibility
        rt_utl.set_visibility(ctrl, 1, k=0, cb=1, l=0) # Unlock and show cb

def set_attributes_visibility_ik(ik_controls):
    '''
    Hide scale on IK controls.
    Show translate, rotate, visibility as keyable.

    Arguments
        controls (list): List of IK control names
    '''
    for mode, controls in ik_controls.items():
        for ctrl in controls:
            logger.debug(f"Set IK control attribute visibility for {ctrl}")
            # Show translate
            for axis in 'XYZ':
                if cmds.attributeQuery(f'translate{axis}', n=ctrl, ex=1):
                    cmds.setAttr(f'{ctrl}.translate{axis}', k=1, cb=0, l=0)
            # Hide scale
            for axis in 'XYZ':
                if cmds.attributeQuery(f'scale{axis}', n=ctrl, ex=1):
                    cmds.setAttr(f'{ctrl}.scale{axis}', k=0, cb=0, l=1)
            # Show rotate
            for axis in 'XYZ':
                if cmds.attributeQuery(f'rotate{axis}', n=ctrl, ex=1):
                    cmds.setAttr(f'{ctrl}.rotate{axis}', k=1, cb=0, l=0)
            # Show visibility
            rt_utl.set_visibility(ctrl, 1, k=0, cb=1, l=0) # Unlock and show cb


# CONTROL UTILITY ======================================================

def get_control_position(control, joints):
    '''
    Get control position relative to joint chain length (0 to 1).
    Calculates normalized distance from control to end joint.

    Arguments
        control (str): Control to measure
        joints (list): List of joints defining chain length

    Return
        v (float): Normalized position (0=start, 1=end)
    '''
    end = joints[-1]
    fullv = rt_utl.get_vec_length(joints[0], end)
    length = rt_utl.get_vec_length(control, end)
    logger.debug(f"ctrl '{control}' - length {length} fullv {fullv}")
    if length == 0:
        v = 0
    elif fullv == 0:
        logger.error(f"ctrl '{control}' - length {length} fullv {fullv}")
        v = 0
    else:
        v = length/fullv

    logger.debug(f'{control} V: {v}')
    return v

def parent_group_controls(controls, groups, reverse=False, long=False):
    '''
    Parent controls under groups in a simple hierarchy.
    Zero the controls, with option to reverse order.

    Arguments
        controls (list): List of control names
        groups (list): List of control group names
        reverse (bool): Reverse the hierarchy order
        long (bool): Return long names

    Return
        controls (list): Updated control names
        groups (list): Updated group names
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
    Controls aim on +Y axis, world up is -Z of orient_world.

    Arguments
        controls (list): List of control group names to orient
        orient_world (str): Object defining world up (-Z axis)
            If None, uses first control
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
    Creates temporary nulls/groups for orient matching via aim constraints.
    Determine global up vector from orient_world object.
    Aim controls on +Y axis.

    Arguments
        controls (list): Controls for null positions
        orient_world (str): Object that dictates world up vector

    Return
        nulls (list): List of created temporary null names
    '''
    nulls = list()
    for i, obj in enumerate(controls):
        tmp_grp = cmds.group(em=True, n=f'null_{i:02}_tmp', w=1)
        nulls.append(tmp_grp)
        cmds.matchTransform(nulls[i], obj, pos=1, rot=1, scl=0, piv=0)
    # Get vector of the global_orient_obj
    world_matrix = cmds.xform(orient_world, q=1, ws=1, m=1)
    world_z_vec = world_matrix[8:11]

    # Aim nulls at each other
    for i, null in enumerate(nulls):
        if i+1 == len(nulls):
            target = nulls[i-1]
            cmds.aimConstraint(target, null,
                               worldUpVector=world_z_vec,
                               upVector=(0,0,1),
                               aimVector=(0,-1,0),
                               maintainOffset=False)
        else:
            target = nulls[i+1]
            cmds.aimConstraint(target, null,
                               worldUpVector=world_z_vec,
                               upVector=(0,0,1),
                               aimVector=(0,1,0),
                               maintainOffset=False)
    return nulls
