'''
# rig_tail_control.py
author: Daisy Jane Lee @dayzl

Control methods for Rig Tail
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
from rig_tail_constants import *
from rig_tail_util import *

logger = logger_setup(__name__)


# CREATE CONTROLS ======================================================

def create_control(control, group=None, match_to=None, parent=None,
             size=1, nr=(1,0,0), color='darkcyan', shape=None, overwrite=True):
    logger.debug(f"{control}, {match_to}, {parent}, {size}, {nr}, {color}, {shape}")
    # Create control group
    groupname = group if group else f"{control}{GRP}"
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
            remove(control) # Remove control and make new control
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
    Create controls to match position of a list of objects.

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
    logger.debug(f"{rigname},\n{matchlist},\n{template_ctrl}, {template_grp}, {typ},\n{parent}, {nest_controls}, {size}, {color}, {shape}")
    controls = list()
    groups = list()
    if not template_ctrl:
        template_ctrl = '{TYPE}{rigname}{_NN}{CTRL}'
    for i, obj in enumerate(matchlist):
        nn = i if typ == TYPE_FK else i+1
        ctrl = fstr(rigname, template_ctrl, typ, nn)
        grp = fstr(rigname, template_grp, typ, nn) if template_grp else None
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
        cmds.setAttr(f"{basectrl_grp}.rotate", rot_offset[0], rot_offset[1], rot_offset[2])
    return basectrl, basectrl_grp

def create_controls_fk(rigname, joints, jnt_pos):
    '''
    Create NUM_CTRL_FK FK controls at equal distance along FK chain.
    Also create individual controls on each FK joint.

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        jnt_pos (float list) position of joints

    Return list of created controls
    '''
    logger.info('Creating FK controls..')
    # Auto orient axis
    up_axis = get_axis_orientation(joints)
    # Create base control
    basectrl, basectrl_grp = create_basectrl(rigname, joints[0], up_axis)

    fkroot_grp = fstr(rigname, CTRLROOT_GRP, TYPE_FK)
    fkjnt_grp = fstr(rigname, GROUP, TYPE_FK)

    # Cleanup
    old_fkroot_grp = fstr(rigname, '{TYPE}{rigname}_root{CTRL}{GRP}', TYPE_FK)
    if cmds.objExists(old_fkroot_grp):
        remove(old_fkroot_grp) # Remove
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
            cmds.setAttr(f"{varfk_ctrl}.{attr}", k=0, cb=0, l=1)

    # Create individual FK controls
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
    return varfk_ctrls

def create_controls_ik(rigname, joints, clusters, duplicate_ends=True, scale=1):
    '''
    Create NUM_CTRL_IK IK controls to drive the IK spline.

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        clusters (tuple list): list of clusters [cluster_name, cluster_handle_name]
        duplicate_ends (bool): Whether to skip the first and last CVs

    Return
        ik_controls (dict): ik control type (ik, float, spline, upvec) -> list of controls
        ik_ctrlgrps (dict): ik control group type (ik, float, spline, upvec) -> list of control groups
    '''
    logger.info('Creating IK controls..')
    all_cluster_handles = [x[1] for x in clusters]
    if duplicate_ends:
        cluster_handles = all_cluster_handles[2:]
        cluster_handles_upv = [all_cluster_handles[0], all_cluster_handles[1]]
    else:
        cluster_handles = all_cluster_handles
        cluster_handles_upv = [all_cluster_handles[0], all_cluster_handles[-1]]
    logger.info(f"Cluster Handles: {len(cluster_handles)} {cluster_handles}")
    # Auto orient axis
    up_axis = get_axis_orientation(cluster_handles)
    # Create base control
    basectrl, basectrl_grp = create_basectrl(rigname, joints[0], up_axis)
    logger.info(f"basectrl {basectrl}, basectrl_grp {basectrl_grp}")

    # Create spline controls
    controls_ik, groups_ik = create_spline_controls_ik(
        rigname, cluster_handles, basectrl, scale)
    controls_float, groups_float = create_spline_controls_float(
        rigname, cluster_handles, basectrl, scale)
    controls_spline, groups_spline = create_spline_controls_spline(
        rigname, cluster_handles, basectrl, scale)

    # Up vector controls
    controls_upv, groups_upv = create_spline_up_vectors(rigname, cluster_handles_upv, scale)

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
    logger.info('Create spline controls, IK')
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
    # parent_group_controls(controls, groups)
    return controls, groups

def create_spline_controls_float(rigname, cluster_handles, orient_world, scale=1):
    '''
    Build controls for float spline clusters
    5 clusters total, world up is -z of orient_world.
    '''
    logger.info('Create spline controls, Float')
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
    logger.info('Create spline controls, IK Spline')
    logger.info(f"Cluster Handles: {len(cluster_handles)} {cluster_handles}")
    if len(cluster_handles) != NUM_CTRL_IK:
        logger.error(f"Need {NUM_CTRL_IK} Cluster Handles")

    controls = list()
    groups = list()
    # Create spline controls matched to clusters
    for i, template in enumerate(SPLINE_CONTROLS):
        ctrlname = fstr(rigname, template, TYPE_IK)
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
        cmds.move(0,tr,0, f"{shape}.cv[*]", r=True, objectSpace=True)
    for shape in upvec_endctrl_shapes:
        tr = (SPLINE_TOP_SZ + 0.5) * scale
        cmds.move(0,-tr,0, f"{shape}.cv[*]", r=True, objectSpace=True)
    return [upvec_bsectrl, upvec_endctrl], [upvec_bsegrp, upvec_endgrp]


# CONTROL UTILITY ======================================================

def get_control_hierarchy(control, end_control=None):
    '''
    Get controls in hierarchy. Return list of controls.
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

    logger.debug(f"{control} V: {v}")
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
            ctrlgrp = f"{ctrl}{GRP}"
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
        ctrlgrp = f"{ctrl}{GRP}"
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
        # Cleanup
        if cmds.attributeQuery('number_joints', n=ctrl, ex=1):
            cmds.deleteAttr(ctrl, at='number_joints')

        # Attribute: Original Position
        ctrlpos = get_control_position(ctrl, joints)
        if cmds.attributeQuery('orig_position', n=ctrl, ex=1):
            cmds.addAttr(f"{ctrl}.orig_position", e=1, nn='Orig Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        else:
            cmds.addAttr(ctrl, ln='orig_position', nn='Orig Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        cmds.setAttr(f"{ctrl}.orig_position", cb=True, l=True)

        # Attribute: Position
        ctrlpos = get_control_position(ctrl, joints)
        if cmds.attributeQuery('position', n=ctrl, ex=1):
            cmds.addAttr(f"{ctrl}.position", e=1, nn='Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        else:
            cmds.addAttr(ctrl, ln='position', nn='Position',
                         at='float', min=0, max=10, dv=ctrlpos*10)
        cmds.setAttr(f"{ctrl}.position", k=True, l=False)

        # Attribute: Falloff
        if cmds.attributeQuery('falloff', n=ctrl, ex=1):
            cmds.addAttr(f"{ctrl}.falloff", e=1, nn='Falloff',
                         at='float', min=0.1, max=10, dv=2)
        else:
            cmds.addAttr(ctrl, ln='falloff', nn='Falloff',
                         at='float', min=0.1, max=10, dv=2)
        cmds.setAttr(f"{ctrl}.falloff", k=True, l=False)

        # Attribute: Num Joints
        if cmds.attributeQuery('num_joints', n=ctrl, ex=1):
            cmds.addAttr(f"{ctrl}.num_joints", e=1, nn='Num Joints',
                         at='float', min=0, max=len(joints)-1)
        else:
            cmds.addAttr(ctrl, ln='num_joints', nn='Num Joints',
                         at='float', min=0, max=len(joints)-1)
        cmds.setAttr(f"{ctrl}.num_joints", cb=True, l=False)

        logger.debug(f"Added attributes to ctrl {ctrl}.")

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
        tmp_grp = cmds.group(em=True, n=f"null_{i:02}_tmp", w=1)
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

def set_control_color(control, color='neonblue'):
    cmds.setAttr(f"{control}.overrideEnabled", 1)
    cmds.setAttr(f"{control}.overrideRGBColors", 0)
    cmds.setAttr(f"{control}.overrideColor", COLOR_OVERRIDE[color])
