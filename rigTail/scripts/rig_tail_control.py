'''
# rig_tail_control.py
author: Daisy Jane @gnitemouse

Control-curve creation for Rig Tail: the root/cog/base hierarchy, the
sliding variable-FK controls, and the IK sets (ik, float, spline,
up-vector), plus their colours, shapes and channel-box attributes.

Every control is a named curve under a same-named offset group, built
through create_control so a rebuild finds and reuses the existing pair
instead of stacking a new one. Colours are written on the SHAPE nodes
(a rebuilt transform keeps its override otherwise), and
PRESERVE_CTRL_SHAPES keeps a hand-edited shape across rebuilds per
control type.

The SplineIK set is fixed at 5 controls (bot, bot_sml, mid, top_sml,
top) at fixed tail fractions no matter what NUM_CTRL_IK is;
spline_control_index maps however many clusters were built back onto
those 5. IK/Float counts follow NUM_CTRL_IK.

Functions:
    spline_control_index: map a cluster index onto the fixed spline set
    create_root_cog: root and cog controls at the top of the hierarchy
    create_basectrl: one per-tail base control, aimed down its chain
    create_circle_control, create_sphere_control, create_cube_control,
        create_control_shape, build_control_shapes: shape primitives
    set_control_color: write an override colour on a control's shapes
    create_control: one control + offset group, reused on rebuild
    create_control_match_list: a control per entry of a match list
    create_controls_fk: the variable-FK sliding control set
    create_controls_ik / create_spline_controls_ik / _float / _spline /
        create_spline_up_vectors: the IK-mode control sets
    get_controls_ik: collect the IK sets back from the scene
    get_control_hierarchy: controls under a control, in DAG order
    add_fk_attributes_to_controls: position/falloff attrs on FK controls
    set_attributes_visibility_fk / _ik: lock and hide unused channels
    get_control_position: a control's position along the chain, as a
        fraction of TAIL LENGTH from the base (the animator-facing dial;
        see rig_tail_fk for how it converts into joint_pos units)
    orient_control_aims / orient_aim_controls_nulls: aim controls at
        each other via throwaway nulls
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_math as rt_math
import rig_tail_mirror as rt_mirror

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
    root_grp = rt_naming.fstr('', rt_constants.ROOT_GRP)
    root_ctrl = rt_naming.fstr('', rt_constants.ROOT_CTRL)
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    rt_maya.create_group(root_grp) # Create root group
    build_control_shapes(root_ctrl, rt_constants.ROOT_CTRL_SZ, nr=(0,1,0),
                         color='lightgreen', shape='circle',
                         preserve=rt_constants.PRESERVE_CTRL.get('root', True))
    build_control_shapes(cog_ctrl, rt_constants.COG_CTRL_SZ, nr=(0,1,0),
                         color='cyan', shape='circle',
                         preserve=rt_constants.PRESERVE_CTRL.get('cog', True))
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
    joints = rt_constants.JOINTS_BN[rigname]
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    basectrl_grp = rt_naming.fstr(rigname, rt_constants.BASECTRL_GRP)
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    basectrl, basectrl_grp = create_control(basectrl, group=basectrl_grp,
                                            match_to=joints[0], parent=cog_ctrl,
                                            size=rt_constants.BASE_CTRL_SZ, nr=(0,-1,0),
                                            color='magenta', shape='circle',
                                            preserve=rt_constants.PRESERVE_CTRL.get('base', False))
    rt_maya.opm(basectrl_grp)
    if not aim_axis:
        aim_axis = rt_math.get_local_orientation(joints)
    if aim_axis not in rt_constants.ROT_AXIS_DICT:
        # No usable chain direction (coincident joints, or an axis name
        # missing from ROT_AXIS_DICT). Orienting off a default would leave
        # every IK control rolled off a made-up up-vector, so stop here
        # with the rig component named rather than build something wrong.
        #
        # The usual cause is not a bad skeleton but a PREVIOUS build that
        # aborted: build_matrix_offset_network zeroes every BN joint's
        # local TRS and jointOrient before re-driving the pose through
        # offsetParentMatrix, so a run that died in between leaves the
        # whole chain stacked on one point. Rebuilding cannot recover it -
        # the pose is gone from the scene - so say so instead of letting
        # the next run fail somewhere less obvious.
        raise ValueError(
            f"{rigname}: cannot orient base control, chain direction is "
            f"'{aim_axis}': every joint from '{joints[0]}' is at the same "
            f'position. If this followed a build that errored, the BN '
            f'skeleton was left collapsed - reopen the scene (or undo back '
            f'past that build) rather than building again over it.')
    rot_offset = rt_constants.ROT_AXIS_DICT[aim_axis]
    logger.trace(f"{rigname}: aim_axis '{aim_axis}' rot_offset '{rot_offset}'")
    cmds.setAttr(f'{basectrl_grp}.rotate', rot_offset[0], rot_offset[1], rot_offset[2])
    rt_maya.opm(basectrl_grp)
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
    rt_naming.rename_shapes(circle_ctrl, typ='ctrl')
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
    rt_naming.rename_shapes(sphere_ctrl, typ='ctrl')
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
    cube_ctrl = cmds.curve(d=1, p=rt_constants.CUBE_CTRL_PTS, n=name)
    rt_naming.rename_shapes(cube_ctrl, typ='ctrl')
    set_control_color(cube_ctrl, color=color)
    cmds.xform(cube_ctrl, s=(size,size,size))
    cmds.makeIdentity(cube_ctrl, apply=1, t=1, r=1, s=1, jo=1)
    cmds.delete(cube_ctrl, ch=1)
    return cube_ctrl

def set_control_color(control, color='neonblue'):
    '''
    Set control color using Maya's override system.

    The override is written onto the control's SHAPE nodes -- the shape is
    what the viewport draws, and shape colour survives a rebuild's shape
    swap. Writing it only on the transform is fragile on re-runs: a
    swapped-in shape that carries its own overrideEnabled masks the
    transform's override, so the colour silently stops updating. Locked or
    connected override plugs are skipped rather than erroring.

    Arguments
        control (str): Control name
        color (str): Color name from COLOR_OVERRIDE dict
    '''
    index = rt_constants.COLOR_OVERRIDE[color]
    shapes = cmds.listRelatives(control, shapes=True, fullPath=True) or [control]
    # The drawing-override plugs exist on every DAG node, so the settable
    # check alone is enough (see rig_tail_maya.set_joint_color)
    for node in shapes:
        for plug, value in (('overrideEnabled', 1),
                            ('overrideRGBColors', 0),
                            ('overrideColor', index)):
            attr = f'{node}.{plug}'
            if cmds.getAttr(attr, settable=True):
                cmds.setAttr(attr, value)

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
            rt_maya.create_group(control)
        return control

    if preserve:
        logger.trace(f"Control exists '{control}', preserving shapes")
        return control
    if not shape:
        return control  # Shapeless control, nothing to rebuild

    logger.trace(f"Control exists '{control}', rebuilding shapes in place")
    tmp = create_control_shape(f'{control}_shapeswap_tmp', size, nr=nr,
                               color=color, shape=shape)
    rt_maya.swap_shapes(control, tmp)
    cmds.delete(tmp)
    # The swapped-in shapes are named after the temporary transform, and
    # colour lives on the transform so it does not travel with them
    rt_naming.rename_shapes(control, typ='ctrl')
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
    groupname = group if group else f'{control}_{rt_constants.GRP}'
    group = rt_maya.create_group(groupname)
    if parent:
        rt_maya.parent_to(group, parent)
    if match_to: # Match transforms position and rotation
        rt_maya.reset_opm(group)
        rt_maya.reset_transforms(group)
        cmds.matchTransform(group, match_to)

    existed = cmds.objExists(control)
    build_control_shapes(control, size=size, nr=nr, color=color,
                         shape=shape, preserve=preserve)

    # Parent control to group
    rt_maya.parent_to(control, group, r=1)
    if existed:
        # The control is placed by its group, so clear any pose left on it
        # by a previous build or by an animator
        rt_maya.reset_opm(control)
        rt_maya.reset_transforms(control)
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
        nn = i if typ == rt_constants.TYPE_FK else i+1
        ctrl = rt_naming.fstr(rigname, template_ctrl, typ, nn)
        grp = rt_naming.fstr(rigname, template_grp, typ, nn) if template_grp else None
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
    Create NUM_CTRL_FK Variable FK controls and, when rt_constants.INDIV_FK is
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
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    fkroot_grp = rt_naming.fstr(rigname, rt_constants.CTRLROOT_GRP, rt_constants.TYPE_FK)
    fkjnt_grp = rt_naming.fstr(rigname, rt_constants.GROUP, rt_constants.TYPE_FK)

    if cmds.objExists(fkjnt_grp):
        # Delete constraint on fkjnt_grp
        fkjnt_constraint = cmds.listRelatives(fkjnt_grp, typ='constraint') or []
        for constraint in fkjnt_constraint:
            cmds.delete(constraint)

    # Create FK root group
    rt_maya.create_group(fkroot_grp)
    rt_maya.parent_to(fkroot_grp, basectrl) # Move fkroot_grp under basectrl
    rt_maya.match_transform(fkroot_grp, basectrl)

    # Spread the controls evenly by LENGTH along the chain, rounded to the
    # nearest joint. Evenly spaced INDICES look equivalent and are not: the
    # squid fintails taper 8:1 from first bone to last, so indices put the
    # three controls at 44%, 66% and 85% of the tail instead of 25/50/75 -
    # all of them past halfway, bunched at the tip. By length they land on
    # joints 5, 15 and 30, i.e. 26/50/75%.
    fractions = list(rt_math.linspace(0, 1, rt_constants.NUM_CTRL_FK+2))
    lengths = rt_math.length_fractions(jnt_pos)
    positions = [joints[rt_math.nearest_index(lengths, f)]
                 for f in fractions[1:rt_constants.NUM_CTRL_FK+1]]
    # Create variable FK controls
    varfk_ctrls, varfk_ctrl_grps = create_control_match_list(rigname,
                                                             positions,
                                                             template_ctrl=rt_constants.CONTROL,
                                                             template_grp=rt_constants.CTRL_GRP,
                                                             typ='',
                                                             parent=fkroot_grp,
                                                             nest_controls=False,
                                                             size=rt_constants.VARFK_CTRL_SZ,
                                                             color='lightpink',
                                                             shape='cube',
                                                             preserve=rt_constants.PRESERVE_CTRL.get('varfk', False))
    # Stand them in the behavior-mirrored frame on the mirrored side of an
    # L/R pair, so the same values pose both sides as mirror images. No-op
    # for a center, unpaired or source-side part. The matching sign on the
    # rotation the controls send out is rig_tail_fk.falloff_rotation's.
    mirror_control_frames(rigname, varfk_ctrl_grps, positions)

    for varfk_ctrl in varfk_ctrls:
        for attr in ['tx', 'ty', 'tz']: # Hide translate
            cmds.setAttr(f'{varfk_ctrl}.{attr}', k=0, cb=0, l=1)

    # Create individual FK joint controls (optional, one per joint)
    if rt_constants.INDIV_FK:
        fk_ctrls, fk_ctrl_grps = create_control_match_list(rigname,
                                                           joints,
                                                           template_ctrl=rt_constants.CONTROL,
                                                           template_grp=rt_constants.CTRL_GRP,
                                                           typ=rt_constants.TYPE_FK,
                                                           parent=fkroot_grp,
                                                           nest_controls=True,
                                                           size=rt_constants.FK_CTRL_SZ,
                                                           color='pink',
                                                           shape='circle',
                                                           preserve=rt_constants.PRESERVE_CTRL.get('fk', False))
        # Same frame treatment as the variable-FK controls above. Their
        # groups are parent-constrained to the SDK stack in
        # rig_tail_connect.connect_fk, which captures this frame as its
        # offset - so the flip has to be on before that runs.
        mirror_control_frames(rigname, fk_ctrl_grps, joints)
        # Set FK control visibility (hide translate, scale)
        set_attributes_visibility_fk(fk_ctrls)

    # Set variable FK control visibility (hide translate, scale)
    set_attributes_visibility_fk(varfk_ctrls)
    return varfk_ctrls

def mirror_control_frames(rigname, groups, matches):
    '''
    Re-stand control groups in the behavior-mirrored frame of the joints
    they were matched to, on the mirrored side of an L/R pair.

    A no-op wherever control_signs answers None - a center part, an
    unpaired part, the source side, or a behavior with no right-handed
    control frame - which is what leaves an unmirrored rig untouched.

    The frame is written ABSOLUTELY, off the joint's own world matrix,
    rather than applied as a relative roll: a rebuild then lands on the
    same frame instead of flipping the flip, and a nested control stack
    cannot compound its parents' flips into its own.

    A constraint already driving a group is deleted first, since it would
    both block this write and then hold the group in the unmirrored frame;
    rig_tail_connect.connect_fk rebuilds it with maintain-offset on.

    Arguments
        rigname (str): Name of rig component
        groups (list): Control groups to re-stand
        matches (list): The joint each group was matched to, in step

    Return
        dict or None: the signs used, or None when nothing was changed
    '''
    signs = rt_mirror.control_signs(rigname)
    if not signs:
        return None
    moved = 0
    for group, match in zip(groups, matches):
        if not (cmds.objExists(group) and cmds.objExists(match)):
            continue
        constraints = cmds.listRelatives(group, type='constraint') or []
        if constraints:
            cmds.delete(constraints)
        cmds.xform(group, ws=True,
                   matrix=rt_mirror.mirrored_matrix(match, signs))
        moved += 1
    flipped = ''.join(a for a in 'XYZ' if signs[a] < 0)
    logger.debug(f'{rigname}: Mirror: {moved} control group(s) re-stood, '
                 f'{flipped} negated')
    return signs


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
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)

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
                                                 template_ctrl=rt_constants.SPLINE_IK_CTRL,
                                                 typ=rt_constants.TYPE_IK,
                                                 parent=orient_world,
                                                 nest_controls=True,
                                                 size=rt_constants.IK_CTRL_SZ*scale,
                                                 color='neonyellow',
                                                 shape='cube',
                                                 preserve=rt_constants.PRESERVE_CTRL.get('ik', False))
    orient_control_aims(groups, orient_world,
                        flip_aim=rt_mirror.flip_control_aim(rigname))
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
                                                 template_ctrl=rt_constants.SPLINE_FLOAT_CTRL,
                                                 typ=rt_constants.TYPE_IK,
                                                 parent=orient_world,
                                                 nest_controls=False,
                                                 size=rt_constants.IK_CTRL_SZ*scale,
                                                 color='cyan',
                                                 shape='cube',
                                                 preserve=rt_constants.PRESERVE_CTRL.get('float', False))
    orient_control_aims(groups, orient_world,
                        flip_aim=rt_mirror.flip_control_aim(rigname))
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

    preserve = rt_constants.PRESERVE_CTRL.get('spline', False)
    controls = list()
    groups = list()
    # Create spline controls at fixed tail fractions
    for i, template in enumerate(rt_constants.SPLINE_CONTROLS):
        ctrlname = rt_naming.fstr(rigname, template, rt_constants.TYPE_IK)
        if 'mid_rot' in ctrlname: # spline_mid_rot. Same position as spline_mid
            match_to = match_target(0.5)
            control, group = create_control(ctrlname, match_to=match_to,
                                            parent=orient_world,
                                            size=rt_constants.SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neongreen', shape='sphere',
                                            preserve=preserve)
        else: # spline_bot, spline_bot_sml, spline_mid, spline_top_sml, spline_top
            match_to = match_target(SPLINE_MAIN_FRACS[i])
            control, group = create_control(ctrlname, match_to=match_to,
                                            parent=orient_world,
                                            size=rt_constants.SPLINE_CONTROLS_SZ[i]*scale,
                                            color='neonred', shape='cube',
                                            preserve=preserve)
        controls.append(control)
        groups.append(group)
    orient_control_aims(groups, orient_world,
                        flip_aim=rt_mirror.flip_control_aim(rigname))

    rt_maya.parent_to(groups[1], controls[0]) # Parent bot_sml to bot
    rt_maya.parent_to(groups[3], controls[4]) # Parent top_sml to top
    rt_maya.parent_to(groups[4], controls[5]) # Parent top to mid_rot

    return controls, groups

def create_spline_up_vectors(rigname, cluster_handles, scale=1):
    '''
    Build up-vector controls for twist on first and last controls.

    The two BRACKET the chain: base sits before the first joint, end past
    the last, so the tail runs between them.

    Position comes from the cluster handle, which is where the CV is;
    orientation from orient_control_aims, the same frame the other IK rows
    get and the one setup_switch_upvec then constrains these to. A cluster
    handle carries no rotation, so matching it for both leaves these
    world-aligned - and the bracket offset along the row then only brackets
    a tail that happens to run along world Y.

    Reorienting them is safe because rig_tail_connect constrains the
    CLUSTER with maintainOffset: the handle keeps the world-aligned rest
    that build_advanced_twist's up vectors were measured in, so the twist
    at rest is unchanged and only the animator's frame moves.

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
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    preserve = rt_constants.PRESERVE_CTRL.get('upvec', False)
    upvec_bsectrl = rt_naming.fstr(rigname, rt_constants.UPV_CTRL, TAG='base')
    upvec_endctrl = rt_naming.fstr(rigname, rt_constants.UPV_CTRL, TAG='end')
    upvec_bsegrp = rt_naming.fstr(rigname, rt_constants.UPV_CTRLGRP, TAG='base')
    upvec_endgrp = rt_naming.fstr(rigname, rt_constants.UPV_CTRLGRP, TAG='end')

    upvec_bsectrl, upvec_bsegrp = create_control(upvec_bsectrl,
                                                 group=upvec_bsegrp,
                                                 match_to=cluster_handles[0],
                                                 parent=basectrl,
                                                 size=rt_constants.SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere',
                                                 preserve=preserve)
    upvec_endctrl, upvec_endgrp = create_control(upvec_endctrl,
                                                 group=upvec_endgrp,
                                                 match_to=cluster_handles[1],
                                                 parent=basectrl,
                                                 size=rt_constants.SPLINE_UPV_SZ,
                                                 nr=(0,1,0),
                                                 color='purple',
                                                 shape='sphere',
                                                 preserve=preserve)

    flip_aim = rt_mirror.flip_control_aim(rigname)
    orient_control_aims([upvec_bsegrp, upvec_endgrp], basectrl,
                        flip_aim=flip_aim)

    if not preserve:
        # Full paths: short shape names are ambiguous when the scene
        # contains duplicate node names
        upvec_bsectrl_shapes = cmds.listRelatives(upvec_bsectrl, s=True, f=True) or []
        upvec_endctrl_shapes = cmds.listRelatives(upvec_endctrl, s=True, f=True) or []
        # Bracketing is an offset ALONG the row, which is local Y once
        # orient_control_aims has run. orient_aim_controls_nulls negates
        # the aim on a mirrored side, so +Y runs back UP the row there;
        # carrying the same factor keeps both controls pointing away from
        # the tail on both sides rather than folding into it on one.
        aim_sign = -1 if flip_aim else 1
        for shape in upvec_bsectrl_shapes:
            tr = (rt_constants.SPLINE_BOT_SZ + 0.5) * scale
            cmds.move(0, -aim_sign*tr, 0, f'{shape}.cv[*]',
                      r=True, objectSpace=True)
        for shape in upvec_endctrl_shapes:
            tr = (rt_constants.SPLINE_TOP_SZ + 0.5) * scale
            cmds.move(0, aim_sign*tr, 0, f'{shape}.cv[*]',
                      r=True, objectSpace=True)
    return [upvec_bsectrl, upvec_endctrl], [upvec_bsegrp, upvec_endgrp]


# GET CONTROLS =========================================================

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
    ctrl_template = [rt_constants.SPLINE_IK_CTRL, rt_constants.SPLINE_FLOAT_CTRL]
    for i, ctrltyp in enumerate(['ik', 'float']):
        controls = list()
        ctrlgrps = list()
        for num in range(rt_constants.NUM_CTRL_IK):
            NN = num + 1
            ctrl = rt_naming.fstr(rigname, ctrl_template[i], rt_constants.TYPE_IK, NN)
            ctrlgrp = f'{ctrl}_{rt_constants.GRP}'
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
    for ctrl_template in rt_constants.SPLINE_CONTROLS:
        ctrl = rt_naming.fstr(rigname, ctrl_template, rt_constants.TYPE_IK)
        ctrlgrp = f'{ctrl}_{rt_constants.GRP}'
        if cmds.objExists(ctrl):
            ik_controls['spline'].append(ctrl)
        else:
            logger.warning(f"ctrl '{ctrl}' does not exist")
        if cmds.objExists(ctrlgrp):
            ik_ctrlgrps['spline'].append(ctrlgrp)
        else:
            logger.warning(f"ctrlgrp '{ctrlgrp}' does not exist")

    # Type upvec
    upvec_bsectrl = rt_naming.fstr(rigname, rt_constants.UPV_CTRL, TAG='base')
    upvec_endctrl = rt_naming.fstr(rigname, rt_constants.UPV_CTRL, TAG='end')
    upvec_bsegrp = rt_naming.fstr(rigname, rt_constants.UPV_CTRLGRP, TAG='base')
    upvec_endgrp = rt_naming.fstr(rigname, rt_constants.UPV_CTRLGRP, TAG='end')
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
    elif cmds.objectType(control, i='transform') and rt_constants.CTRL in control:
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
    # translate/rotate/scale exist on every transform-derived node, so skip
    # the per-axis attributeQuery (see rig_tail_maya.set_joint_channels)
    for ctrl in fk_controls:
        logger.trace(f"Set FK control attribute visibility for {ctrl}")
        # Hide translate
        for axis in 'XYZ':
            cmds.setAttr(f'{ctrl}.translate{axis}', k=0, cb=0, l=1)
        # Hide scale
        for axis in 'XYZ':
            cmds.setAttr(f'{ctrl}.scale{axis}', k=0, cb=0, l=1)
        # Show rotate
        for axis in 'XYZ':
            cmds.setAttr(f'{ctrl}.rotate{axis}', k=1, cb=0, l=0)
        # Show visibility
        rt_maya.set_visibility(ctrl, 1, k=0, cb=1, l=0) # Unlock and show cb

def set_attributes_visibility_ik(ik_controls):
    '''
    Hide scale on IK controls.
    Show translate, rotate, visibility as keyable.
    Float controls are translate-only: rotate is non-keyable and hidden.

    Arguments
        ik_controls (dict): Dict of control types -> control lists
    '''
    # translate/rotate/scale exist on every transform-derived node, so skip
    # the per-axis attributeQuery (see rig_tail_maya.set_joint_channels)
    for mode, controls in ik_controls.items():
        for ctrl in controls:
            logger.trace(f"Set IK control attribute visibility for {ctrl}")
            # Show translate
            for axis in 'XYZ':
                cmds.setAttr(f'{ctrl}.translate{axis}', k=1, cb=0, l=0)
            # Hide scale
            for axis in 'XYZ':
                cmds.setAttr(f'{ctrl}.scale{axis}', k=0, cb=0, l=1)
            # Rotate: hidden and locked on Float controls, shown elsewhere
            if mode == 'float':
                rk, rcb, rl = 0, 0, 1
            else:
                rk, rcb, rl = 1, 0, 0
            for axis in 'XYZ':
                cmds.setAttr(f'{ctrl}.rotate{axis}', k=rk, cb=rcb, l=rl)
            # Show visibility
            rt_maya.set_visibility(ctrl, 1, k=0, cb=1, l=0) # Unlock and show cb


# CONTROL UTILITY ======================================================

def get_control_position(control, joints):
    '''
    Get control position along the chain, measured from the BASE (0 to 1).

    This is the animator-facing number: it feeds the `position` attribute
    (x10, so 0-10), and it is a fraction of TAIL LENGTH, so position 5 is
    genuinely halfway down the tail whatever the bones do. It is NOT the
    same metric as joint_pos, which is a Greville (parameter) fraction - a
    remapValue converts between them, see rig_tail_fk.set_curveinfo_fk.

    Measured along the chain and snapped to the nearest joint, rather than
    as a straight line: on a curled tail a chord shortens as the tail bends
    (so the dial would drift under animation) and is not even monotonic -
    two joints can sit the same distance from the tip.

    Arguments
        control (str): Control to measure
        joints (list): List of joints defining chain length

    Return
        v (float): Normalized position (0=base, 1=tip)
    '''
    if not joints:
        logger.error(f"ctrl '{control}' - no joints to measure against")
        return 0
    jnt_pos = [rt_math.get_world_pos(jnt) for jnt in joints]
    fractions = rt_math.length_fractions(jnt_pos)
    ctrl_pos = rt_math.get_world_pos(control)
    idx = min(range(len(jnt_pos)),
              key=lambda i: sum((jnt_pos[i][k] - ctrl_pos[k])**2
                                for k in range(3)))
    v = fractions[idx]
    logger.trace(f"ctrl '{control}' - nearest '{joints[idx]}' V: {v}")
    return v

def orient_control_aims(controls, orient_world=None, flip_aim=False):
    '''
    Orient controls so that they aim toward each other.

    Controls aim on +Y down the row and roll so their +Z meets a world up
    reference: rt_mirror.spline_up_vector(), a stated direction lying in
    the symmetry plane. Falls back to orient_world's +Z only when no usable
    axis is configured - taking it from the basectrl makes these frames a
    by-product of MIRROR_BEHAVIOR, since the basectrl is oriented from the
    joints.

    Arguments
        controls (list): List of control group names to orient
        orient_world (str): Fallback object defining the world up (+Z),
            used only when no in-plane axis is available. If None, uses
            the first control
        flip_aim (bool): Aim the row BACKWARDS, putting the mirror's
            negation on the aim instead of the third axis
            (rt_mirror.flip_control_aim)
    '''
    if not orient_world: # If None, use first control
        orient_world = controls[0]
    up_vector = rt_mirror.spline_up_vector()
    if up_vector is None:
        up_vector = cmds.xform(orient_world, q=1, ws=1, m=1)[8:11]
    orient_nulls = orient_aim_controls_nulls(controls, up_vector, flip_aim)
    # Match controls
    for ctrl, ctrl_null in zip(controls, orient_nulls):
        cmds.matchTransform(ctrl, ctrl_null, pos=1, rot=1, scl=0, piv=0)
    cmds.delete(orient_nulls)

def orient_aim_controls_nulls(controls, up_vector, flip_aim=False):
    '''
    Creates temporary nulls/groups for orient matching via aim constraints.

    Each null aims at the NEXT one in the row on +Y, with its +Z rolled
    toward up_vector; the last aims at the one before it on -Y, which
    leaves its +Y pointing the same way down the row as the rest.

    flip_aim negates both, so +Y runs back UP the row: the whole of the
    mirrored side's frame difference. See rt_mirror.flip_control_aim for
    why the negation goes on the aim rather than another axis.

    Arguments
        controls (list): Controls for null positions
        up_vector (list): World up direction the +Z axis rolls toward
        flip_aim (bool): Reverse the aim direction

    Return
        nulls (list): List of created temporary null names
    '''
    nulls = list()
    for i, obj in enumerate(controls):
        tmp_grp = cmds.group(em=True, n=f'null_{i:02}_tmp', w=1)
        nulls.append(tmp_grp)
        cmds.matchTransform(nulls[i], obj, pos=1, rot=1, scl=0, piv=0)
    # The mirrored side's whole frame difference rides on this one factor
    sign = -1 if flip_aim else 1

    for i, null in enumerate(nulls):
        if i+1 == len(nulls):
            target = nulls[i-1]
            aim_vector = (0, -sign, 0)
        else:
            target = nulls[i+1]
            aim_vector = (0, sign, 0)
        cmds.aimConstraint(target, null,
                           worldUpVector=up_vector,
                           upVector=(0,0,1),
                           aimVector=aim_vector,
                           maintainOffset=False)
    return nulls
