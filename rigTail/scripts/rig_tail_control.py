'''
# rig_tail_control.py
author: Daisy Jane @gnitemouse

Control methods for Rig Tail
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_math as rt_mat

logger = logger_setup(__name__)

# Number of SplineIK controls (bot, bot_sml, mid, top_sml, top).
# Spline controls are positioned at fixed tail fractions:
# 0, 1/4, 1/2, 3/4, 1 (mid_rot shares the mid position).
# SplineIK controls stay the same in number and position
# regardless of change in NUM_CTRL_IK.
# spline_control_index maps each of the NUM_CTRL_IK clusters
# back onto the SplineIK control
NUM_SPLINEIK = 5
# Fixed position fractions of the 5 SplineIK controls:
# bot, bot_sml, mid, top_sml, top. Used to place them independently of the cluster count.
SPLINE_MAIN_FRACS = [0.0, 0.25, 0.5, 0.75, 1.0]

def spline_control_index(cluster_j, num_clusters):
    '''
    Main spline control index that drives cluster j in SplineIK mode.

    Arguments
        cluster_j (int): Cluster index (0 .. num_clusters-1)
        num_clusters (int): Number of control clusters (NUM_CTRL_IK)

    Return
        int: Main spline control index (0=bot .. 4=top)
    '''
    if num_clusters <= 1:
        return 0
    return round(cluster_j * (NUM_SPLINEIK - 1) / (num_clusters - 1))


# CREATE CONTROLS ======================================================

def create_root_cog():
    '''
    Create root and cog controls.

    Root and cog sit above the rig parts and are usually built once for
    the whole character, so PRESERVE_CTRL defaults them to True: an
    existing root or cog keeps its shapes. When either does not exist yet
    it is built from the constants, since there is nothing to preserve.

    Return
        root_grp (str): Root group
        root_ctrl (str): Root control
        cog_ctrl (str): Cog control
    '''
    root_grp = rt_nam.fstr('', rt_cst.ROOT_GRP)
    root_ctrl = rt_nam.fstr('', rt_cst.ROOT_CTRL)
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    rt_mya.create_group(root_grp) # Create root group
    build_control_shapes(root_ctrl, rt_cst.ROOT_CTRL_SZ, nr=(0,1,0),
                         color='lightgreen', shape='circle',
                         preserve=rt_cst.PRESERVE_CTRL.get('root', True))
    build_control_shapes(cog_ctrl, rt_cst.COG_CTRL_SZ, nr=(0,1,0),
                         color='cyan', shape='circle',
                         preserve=rt_cst.PRESERVE_CTRL.get('cog', True))
    return root_grp, root_ctrl, cog_ctrl

def create_basectrl(rigname, aim_axis=None):
    '''
    Create base control for the tail rig.

    The control circle is built with normal -Y and the control group is
    rotated so its local +Y runs down the joint chain, which leaves the
    circle perpendicular to the tail for any chain direction.

    The aim axis is read from the joints' own orientation
    (get_local_orientation), not from where the chain sits in world
    space: a tail pointing sideways is oriented the same way as one
    hanging straight down.

    Arguments
        rigname (str): Name of rig component
        aim_axis (str): Local axis of the first joint that points down
            the chain (e.g. '+x', '-z'). Measured from the joints if None

    Return
        basectrl (str): Base control
        basectrl_grp (str): Base control group
    '''
    joints = rt_cst.JOINTS_BN[rigname]
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    basectrl_grp = rt_nam.fstr(rigname, rt_cst.BASECTRL_GRP)
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    basectrl, basectrl_grp = create_control(basectrl, group=basectrl_grp,
                                            match_to=joints[0], parent=cog_ctrl,
                                            size=rt_cst.BASE_CTRL_SZ, nr=(0,-1,0),
                                            color='magenta', shape='circle',
                                            preserve=rt_cst.PRESERVE_CTRL.get('base', False))
    rt_mya.opm(basectrl_grp)
    if not aim_axis:
        aim_axis = rt_mat.get_local_orientation(joints)
    if aim_axis not in rt_cst.ROT_AXIS_DICT:
        # No usable chain direction (coincident joints, or an axis name
        # missing from ROT_AXIS_DICT). Orienting off a default would leave
        # every IK control rolled off a made-up up-vector, so stop here
        # with the rig component named rather than build something wrong.
        raise ValueError(
            f"{rigname}: cannot orient base control, chain direction is "
            f"'{aim_axis}'. Check that '{joints[0]}' and the joints after "
            f'it are not all at the same position.')
    rot_offset = rt_cst.ROT_AXIS_DICT[aim_axis]
    logger.trace(f"{rigname}: aim_axis '{aim_axis}' rot_offset '{rot_offset}'")
    cmds.setAttr(f'{basectrl_grp}.rotate', rot_offset[0], rot_offset[1], rot_offset[2])
    rt_mya.opm(basectrl_grp)
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
    cube_ctrl = cmds.curve(d=1, p=rt_cst.CUBE_CTRL_PTS, n=name)
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
    cmds.setAttr(f'{control}.overrideColor', rt_cst.COLOR_OVERRIDE[color])

def create_control_shape(name, size=1, nr=(1,0,0), color='darkcyan', shape=None):
    '''
    Create a standalone control curve transform of the given shape.

    Arguments
        name (str): Transform name
        size (float): Control size
        nr (tuple): Normal direction for circle controls
        color (str): Control color from COLOR_OVERRIDE dict
        shape (str): Control shape ('circle', 'sphere', 'cube')

    Return
        control (str): Control name, or None if shape is not a curve shape
    '''
    if shape == 'circle':
        return create_circle_control(name, size, nr=nr, color=color)
    if shape == 'sphere':
        return create_sphere_control(name, size, color=color)
    if shape == 'cube':
        return create_cube_control(name, size, color=color)
    return None

def build_control_shapes(control, size=1, nr=(1,0,0), color='darkcyan',
                         shape=None, preserve=False):
    '''
    Give a control the wanted shape, creating the transform if needed.

    An existing control keeps its transform node: the new curves are built
    on a temporary transform and swapped in underneath it. Nothing
    parented under the control is ever detached, so no child has to be
    found again by name afterwards.

    preserve leaves existing shapes untouched - size, CV edits and colour
    all survive. It is ignored when the control does not exist yet, since
    there is nothing to preserve and the constants are the only source of
    a shape.

    Arguments
        control (str): Control name
        size (float): Control size
        nr (tuple): Normal direction for circle controls
        color (str): Control color from COLOR_OVERRIDE dict
        shape (str): Control shape ('circle', 'sphere', 'cube', None=empty group)
        preserve (bool): Keep an existing control's shapes as they are

    Return
        control (str): Control name
    '''
    if not cmds.objExists(control):
        if create_control_shape(control, size, nr=nr, color=color,
                                shape=shape) is None:
            rt_mya.create_group(control)
        return control

    if preserve:
        logger.trace(f"Control exists '{control}', preserving shapes")
        return control
    if not shape:
        return control  # Shapeless control, nothing to rebuild

    logger.trace(f"Control exists '{control}', rebuilding shapes in place")
    tmp = create_control_shape(f'{control}_shapeswap_tmp', size, nr=nr,
                               color=color, shape=shape)
    rt_mya.swap_shapes(control, tmp)
    cmds.delete(tmp)
    # The swapped-in shapes are named after the temporary transform, and
    # colour lives on the transform so it does not travel with them
    rt_nam.rename_shapes(control, typ='ctrl')
    set_control_color(control, color=color)
    return control

def create_control(control, group=None, match_to=None, parent=None,
             size=1, nr=(1,0,0), color='darkcyan', shape=None, preserve=False):
    '''
    Create a control with group hierarchy.

    An existing control is reshaped in place rather than deleted and
    rebuilt, so its children stay parented throughout (see
    build_control_shapes). Its transform values are still reset, matching
    what deleting the control used to do, so the control sits on its group
    wherever the group has been matched to.

    Arguments
        control (str): Control name
        group (str): Group name (auto-generated if None)
        match_to (str): Object to match transforms to
        parent (str): Parent object for group
        size (float): Control size
        nr (tuple): Normal direction for circle controls
        color (str): Control color from COLOR_OVERRIDE dict
        shape (str): Control shape ('circle', 'sphere', 'cube', None=empty group)
        preserve (bool): Keep an existing control's shapes as they are

    Return
        control (str): Control name
        group (str): Group name
    '''
    logger.trace(f'{control}, {match_to}, {parent}, {size}, {nr}, {color}, {shape}, {preserve}')
    # Create control group
    groupname = group if group else f'{control}_{rt_cst.GRP}'
    group = rt_mya.create_group(groupname)
    if parent:
        rt_mya.parent_to(group, parent)
    if match_to: # Match transforms position and rotation
        rt_mya.reset_opm(group)
        rt_mya.reset_transforms(group)
        cmds.matchTransform(group, match_to)

    existed = cmds.objExists(control)
    build_control_shapes(control, size=size, nr=nr, color=color,
                         shape=shape, preserve=preserve)

    # Parent control to group
    rt_mya.parent_to(control, group, r=1)
    if existed:
        # The control is placed by its group, so clear any pose left on it
        # by a previous build or by an animator
        rt_mya.reset_opm(control)
        rt_mya.reset_transforms(control)
    cmds.delete(control, ch=1)

    return control, group

def create_control_match_list(rigname, matchlist, template_ctrl, template_grp=None,
                              typ='', parent=None, nest_controls=False,
                              size=1, color='darkcyan', shape=None, preserve=False):
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
        preserve (bool): Keep existing controls' shapes as they are

    Return
        controls (list): List of control names
        groups (list): List of control group names
    '''
    logger.trace(f'{rigname},\n{matchlist},\n{template_ctrl}, {template_grp}, {typ},\n{parent}, {nest_controls}, {size}, {color}, {shape}')
    controls = list()
    groups = list()
    for i, obj in enumerate(matchlist):
        nn = i if typ == rt_cst.TYPE_FK else i+1
        ctrl = rt_nam.fstr(rigname, template_ctrl, typ, nn)
        grp = rt_nam.fstr(rigname, template_grp, typ, nn) if template_grp else None
        control, group = create_control(ctrl, grp, match_to=obj, parent=parent,
                                        size=size, color=color, shape=shape,
                                        preserve=preserve)
        if nest_controls:
            parent = control
        controls.append(control)
        groups.append(group)
    return controls, groups

def create_controls_fk(rigname, joints, jnt_pos):
    '''
    Create NUM_CTRL_FK Variable FK controls and, when rt_cst.INDIV_FK is
    enabled, individual FK joint controls.
    Variable FK controls are distributed evenly along the FK chain.
    Individual FK joint controls are created at each joint.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints
        jnt_pos (list): List of joint world positions

    Return
        varfk_ctrls (list): List of Variable FK control names
    '''
    logger.debug(f"{rigname}: Create FK controls")
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    fkroot_grp = rt_nam.fstr(rigname, rt_cst.CTRLROOT_GRP, rt_cst.TYPE_FK)
    fkjnt_grp = rt_nam.fstr(rigname, rt_cst.GROUP, rt_cst.TYPE_FK)

    if cmds.objExists(fkjnt_grp):
        # Delete constraint on fkjnt_grp
        fkjnt_constraint = cmds.listRelatives(fkjnt_grp, typ='constraint') or []
        for constraint in fkjnt_constraint:
            cmds.delete(constraint)

    # Create FK root group
    rt_mya.create_group(fkroot_grp)
    rt_mya.parent_to(fkroot_grp, basectrl) # Move fkroot_grp under basectrl
    rt_mya.match_transform(fkroot_grp, basectrl)

    # Calculate indices evenly distributed throughout FK chain
    indices = list(rt_mat.linspace(0, len(jnt_pos)-1, rt_cst.NUM_CTRL_FK+2))
    positions = [joints[round(indices[i])] for i in range(1, rt_cst.NUM_CTRL_FK+1)]
    # Create variable FK controls
    varfk_ctrls, varfk_ctrl_grps = create_control_match_list(rigname,
                                                             positions,
                                                             template_ctrl=rt_cst.CONTROL,
                                                             template_grp=rt_cst.CTRL_GRP,
                                                             typ='',
                                                             parent=fkroot_grp,
                                                             nest_controls=False,
                                                             size=rt_cst.VARFK_CTRL_SZ,
                                                             color='lightpink',
                                                             shape='cube',
                                                             preserve=rt_cst.PRESERVE_CTRL.get('varfk', False))
    for varfk_ctrl in varfk_ctrls:
        for attr in ['tx', 'ty', 'tz']: # Hide translate
            cmds.setAttr(f'{varfk_ctrl}.{attr}', k=0, cb=0, l=1)

    # Create individual FK joint controls (optional, one per joint)
    if rt_cst.INDIV_FK:
        fk_ctrls, fk_ctrl_grps = create_control_match_list(rigname,
                                                           joints,
                                                           template_ctrl=rt_cst.CONTROL,
                                                           template_grp=rt_cst.CTRL_GRP,
                                                           typ=rt_cst.TYPE_FK,
                                                           parent=fkroot_grp,
                                                           nest_controls=True,
                                                           size=rt_cst.FK_CTRL_SZ,
                                                           color='pink',
                                                           shape='circle',
                                                           preserve=rt_cst.PRESERVE_CTRL.get('fk', False))
        # Set FK control visibility (hide translate, scale)
        set_attributes_visibility_fk(fk_ctrls)

    # Set variable FK control visibility (hide translate, scale)
    set_attributes_visibility_fk(varfk_ctrls)
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
    logger.debug(f"{rigname}: Create IK controls")
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)

    all_cluster_handles = [x[1] for x in clusters]
    if duplicate_ends:
        cluster_handles = all_cluster_handles[2:]
        cluster_handles_upv = [all_cluster_handles[0], all_cluster_handles[1]]
    else:
        cluster_handles = all_cluster_handles
        cluster_handles_upv = [all_cluster_handles[0], all_cluster_handles[-1]]
    logger.trace(f'Cluster Handles: {len(cluster_handles)} {cluster_handles}')

    # Create spline controls
    controls_ik, groups_ik = create_spline_controls_ik(
        rigname, cluster_handles, basectrl, scale)
    controls_float, groups_float = create_spline_controls_float(
        rigname, cluster_handles, basectrl, scale)
    controls_spline, groups_spline = create_spline_controls_spline(
        rigname, cluster_handles, basectrl, scale, joints=joints)

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
    logger.debug(f"{rigname}: Create IK spline controls - IK")
    controls, groups = create_control_match_list(rigname,
                                                 cluster_handles,
                                                 template_ctrl=rt_cst.SPLINE_IK_CTRL,
                                                 typ=rt_cst.TYPE_IK,
                                                 parent=orient_world,
                                                 nest_controls=True,
                                                 size=rt_cst.IK_CTRL_SZ*scale,
                                                 color='neonyellow',
                                                 shape='cube',
                                                 preserve=rt_cst.PRESERVE_CTRL.get('ik', False))
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
    logger.debug(f"{rigname}: Create IK spline controls - Float")
    controls, groups = create_control_match_list(rigname,
                                                 cluster_handles,
                                                 template_ctrl=rt_cst.SPLINE_FLOAT_CTRL,
                                                 typ=rt_cst.TYPE_IK,
                                                 parent=orient_world,
                                                 nest_controls=False,
                                                 size=rt_cst.IK_CTRL_SZ*scale,
                                                 color='cyan',
                                                 shape='cube',
                                                 preserve=rt_cst.PRESERVE_CTRL.get('float', False))
    orient_control_aims(groups, orient_world)
    return controls, groups

def create_spline_controls_spline(rigname, cluster_handles, orient_world,
                                  scale=1, joints=None):
    '''
    Build Spline IK mode controls for spline clusters.
    Creates 5 main controls plus a rotation offset mid control.
    SPLINE_CONTROLS = [bot, bot_sml, mid, top_sml, top, mid_rot]

    The spline set is fixed regardless of NUM_CTRL_IK: each main control
    is placed at a fixed fraction of the tail (bot=0, bot_sml=0.25,
    mid=0.5, top_sml=0.75, top=1.0; mid_rot shares mid). Fractions are
    resolved to the nearest joint (joints are far denser than clusters),
    so bot_sml/top_sml no longer drift onto a different cluster when
    NUM_CTRL_IK changes. When no joints are supplied the nearest cluster
    handle is used as a fallback. Positioning stays consistent with the
    SplineIK influence mapping (spline_control_index): control k drives the
    clusters nearest fraction k/4, which is where control k sits.

    Control hierarchy:
    - bot_sml parents under bot
    - top_sml parents under top
    - top parents under mid_rot
    - mid_rot controls middle rotation offset

    Arguments
        rigname (str): Name of rig component
        cluster_handles (list): Control cluster handle names
        orient_world (str): Object for aim constraint world up (-z axis)
        scale (float): Scale multiplier for controls
        joints (list): Joint chain (base->tip) used to resolve fixed
            fractions to positions; falls back to cluster_handles if None

    Return
        controls (list): List of Spline control names (length 6)
        groups (list): List of Spline control group names (length 6)
    '''
    logger.debug(f"{rigname}: Create IK Spline controls - SplineIK")
    logger.trace(f'Cluster Handles: {len(cluster_handles)} {cluster_handles}')
    num_clusters = len(cluster_handles)

    def match_target(frac):
        # Object at a fixed fraction of the tail: nearest joint when
        # available (dense, so position is stable across cluster counts),
        # otherwise nearest control cluster.
        if joints:
            return joints[round(frac * (len(joints) - 1))]
        return cluster_handles[round(frac * (num_clusters - 1))]

    preserve = rt_cst.PRESERVE_CTRL.get('spline', False)
    controls = list()
    groups = list()
    # Create spline controls at fixed tail fractions
    for i, template in enumerate(rt_cst.SPLINE_CONTROLS):
        ctrlname = rt_nam.fstr(rigname, template, rt_cst.TYPE_IK)
        if 'mid_rot' in ctrlname: # spline_mid_rot. Same position as spline_mid
            match_to = match_target(0.5)
            control, group = create_control(ctrlname, match_to=match_to,
                                            parent=orient_world,
                                            size=rt_cst.SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neongreen', shape='sphere',
                                            preserve=preserve)
        else: # spline_bot, spline_bot_sml, spline_mid, spline_top_sml, spline_top
            match_to = match_target(SPLINE_MAIN_FRACS[i])
            control, group = create_control(ctrlname, match_to=match_to,
                                            parent=orient_world,
                                            size=rt_cst.SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neonred', shape='cube',
                                            preserve=preserve)
        controls.append(control)
        groups.append(group)
    orient_control_aims(groups, orient_world)

    rt_mya.parent_to(groups[1], controls[0]) # Parent bot_sml to bot
    rt_mya.parent_to(groups[3], controls[4]) # Parent top_sml to top
    rt_mya.parent_to(groups[4], controls[5]) # Parent top to mid_rot

    return controls, groups

def create_spline_up_vectors(rigname, cluster_handles, scale=1):
    '''
    Build up-vector controls for twist on first and last controls.
    Creates two sphere controls offset from the base and end positions.

    The offset is baked into the CVs, so it is applied only to shapes
    built by this call. Preserved shapes already carry it from the build
    that created them, and moving their CVs again would push the controls
    further out on every rebuild.

    Arguments
        rigname (str): Name of rig component
        cluster_handles (list): [base_cluster_handle, end_cluster_handle]
        scale (float): Scale multiplier for controls

    Return
        controls_upv (list): [upvec_base_ctrl, upvec_end_ctrl]
        groups_upv (list): [upvec_base_grp, upvec_end_grp]
    '''
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    preserve = rt_cst.PRESERVE_CTRL.get('upvec', False)
    upvec_bsectrl = rt_nam.fstr(rigname, rt_cst.UPV_CTRL, TAG='base')
    upvec_endctrl = rt_nam.fstr(rigname, rt_cst.UPV_CTRL, TAG='end')
    upvec_bsegrp = rt_nam.fstr(rigname, rt_cst.UPV_CTRLGRP, TAG='base')
    upvec_endgrp = rt_nam.fstr(rigname, rt_cst.UPV_CTRLGRP, TAG='end')

    upvec_bsectrl, upvec_bsegrp = create_control(upvec_bsectrl,
                                                 group=upvec_bsegrp,
                                                 match_to=cluster_handles[0],
                                                 parent=basectrl,
                                                 size=rt_cst.SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere',
                                                 preserve=preserve)
    upvec_endctrl, upvec_endgrp = create_control(upvec_endctrl,
                                                 group=upvec_endgrp,
                                                 match_to=cluster_handles[1],
                                                 parent=basectrl,
                                                 size=rt_cst.SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere',
                                                 preserve=preserve)

    if not preserve:
        # Full paths: short shape names are ambiguous when the scene
        # contains duplicate node names
        upvec_bsectrl_shapes = cmds.listRelatives(upvec_bsectrl, s=True, f=True) or []
        upvec_endctrl_shapes = cmds.listRelatives(upvec_endctrl, s=True, f=True) or []
        # Offset shape CVs
        for shape in upvec_bsectrl_shapes:
            tr = (rt_cst.SPLINE_BOT_SZ + 0.5) * scale
            cmds.move(0,tr,0, f'{shape}.cv[*]', r=True, objectSpace=True)
        for shape in upvec_endctrl_shapes:
            tr = (rt_cst.SPLINE_TOP_SZ + 0.5) * scale
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
    root_ctrl = rt_nam.fstr('', rt_cst.ROOT_CTRL)
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    controls = list()

    all_controls = get_control_hierarchy(root_ctrl)
    for ctrl in all_controls:
        if include_root and ctrl == root_ctrl:
            controls.append(ctrl)
        elif include_cog and ctrl == cog_ctrl:
            controls.append(ctrl)
        elif include_basectrl and ctrl == basectrl:
            controls.append(ctrl)
        elif fk and rt_cst.TYPE_FK in ctrl:
            controls.append(ctrl)
        elif ik and rt_cst.TYPE_IK in ctrl:
            controls.append(ctrl)
        elif bn and rt_cst.TYPE_BN in ctrl:
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
    ctrl_template = [rt_cst.SPLINE_IK_CTRL, rt_cst.SPLINE_FLOAT_CTRL]
    for i, ctrltyp in enumerate(['ik', 'float']):
        controls = list()
        ctrlgrps = list()
        for num in range(rt_cst.NUM_CTRL_IK):
            NN = num + 1
            ctrl = rt_nam.fstr(rigname, ctrl_template[i], rt_cst.TYPE_IK, NN)
            ctrlgrp = f'{ctrl}_{rt_cst.GRP}'
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
    for ctrl_template in rt_cst.SPLINE_CONTROLS:
        ctrl = rt_nam.fstr(rigname, ctrl_template, rt_cst.TYPE_IK)
        ctrlgrp = f'{ctrl}_{rt_cst.GRP}'
        if cmds.objExists(ctrl):
            ik_controls['spline'].append(ctrl)
        else:
            logger.warning(f"ctrl '{ctrl}' does not exist")
        if cmds.objExists(ctrlgrp):
            ik_ctrlgrps['spline'].append(ctrlgrp)
        else:
            logger.warning(f"ctrlgrp '{ctrlgrp}' does not exist")

    # Type upvec
    upvec_bsectrl = rt_nam.fstr(rigname, rt_cst.UPV_CTRL, TAG='base')
    upvec_endctrl = rt_nam.fstr(rigname, rt_cst.UPV_CTRL, TAG='end')
    upvec_bsegrp = rt_nam.fstr(rigname, rt_cst.UPV_CTRLGRP, TAG='base')
    upvec_endgrp = rt_nam.fstr(rigname, rt_cst.UPV_CTRLGRP, TAG='end')
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
    elif cmds.objectType(control, i='transform') and rt_cst.CTRL in control:
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
    logger.trace('Add FK control attributes')
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

        logger.trace(f"Added attributes to FK control '{ctrl}'")

def set_attributes_visibility_fk(fk_controls):
    '''
    Hide translate, scale on FK controls.
    Show rotate, visibility as keyable.

    Arguments
        controls (list): List of FK control names
    '''
    for ctrl in fk_controls:
        logger.trace(f"Set FK control attribute visibility for {ctrl}")
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
        rt_mya.set_visibility(ctrl, 1, k=0, cb=1, l=0) # Unlock and show cb

def set_attributes_visibility_ik(ik_controls):
    '''
    Hide scale on IK controls.
    Show translate, rotate, visibility as keyable.
    Float controls are translate-only: rotate is non-keyable and hidden.

    Arguments
        ik_controls (dict): Dict of control types -> control lists
    '''
    for mode, controls in ik_controls.items():
        for ctrl in controls:
            logger.trace(f"Set IK control attribute visibility for {ctrl}")
            # Show translate
            for axis in 'XYZ':
                if cmds.attributeQuery(f'translate{axis}', n=ctrl, ex=1):
                    cmds.setAttr(f'{ctrl}.translate{axis}', k=1, cb=0, l=0)
            # Hide scale
            for axis in 'XYZ':
                if cmds.attributeQuery(f'scale{axis}', n=ctrl, ex=1):
                    cmds.setAttr(f'{ctrl}.scale{axis}', k=0, cb=0, l=1)
            # Rotate: hidden and locked on Float controls, shown elsewhere
            if mode == 'float':
                rk, rcb, rl = 0, 0, 1
            else:
                rk, rcb, rl = 1, 0, 0
            for axis in 'XYZ':
                if cmds.attributeQuery(f'rotate{axis}', n=ctrl, ex=1):
                    cmds.setAttr(f'{ctrl}.rotate{axis}', k=rk, cb=rcb, l=rl)
            # Show visibility
            rt_mya.set_visibility(ctrl, 1, k=0, cb=1, l=0) # Unlock and show cb


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
    fullv = rt_mat.get_vec_length(joints[0], end)
    length = rt_mat.get_vec_length(control, end)
    logger.trace(f"ctrl '{control}' - length {length} fullv {fullv}")
    if length == 0:
        v = 0
    elif fullv == 0:
        logger.error(f"ctrl '{control}' - length {length} fullv {fullv}")
        v = 0
    else:
        v = length/fullv

    logger.trace(f'{control} V: {v}')
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
