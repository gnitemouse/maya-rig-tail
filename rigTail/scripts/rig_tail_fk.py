'''
# rig_tail_fk.py
author: Daisy Jane @gnitemouse

Rig Tail FK: variable FK for tails, tentacles and trunks.

A few controls slide along the chain and each bends the joints near them,
instead of one control per joint. Rotating a control bends the tail under
it, fading to nothing at the edge of its reach; sliding the control moves
that bend along the tail.

OVERVIEW
    NUM_CTRL_FK controls (3 by default), each with three dials:
        position (0-10)   where it sits: 0 at the base, 10 at the tip
        falloff (0.1-10)  how far its bend reaches along the chain
        rotate            the bend itself
    A control also reports num_joints, the number of joints currently in
    its range, for the animator to read.

ARCHITECTURE
    Controls slide
        Each control's position drives a pointOnCurveInfo on the FK curve,
        which places the control's parent group. See set_curveinfo_fk.
    Joints receive
        Every joint sits under a stack of SDK groups - one per control,
        plus one for its own FK control. Each control writes into its own
        layer, so influences accumulate through the hierarchy instead of
        fighting over a channel. See create_sdk_groups.
    Weighting
        weight = max(0, 1 - |joint_pos - ctrl_pos| / falloff)
        A symmetric linear tent, normalised by the number of joints in
        range, so widening the falloff spreads the same total bend further
        rather than adding more of it. See falloff_rotation.
    Layering
        A control also carries the rotation of every control before it, so
        the chain reads as FK: bending control 1 carries 2 and 3 with it.
    Twist / roll / offset
        A separate network on the same SDK stack, so the basectrl dials
        that drive the IK spline handle work in FK mode too. The BN chain
        blends between the FK and IK drivers (rig_tail_matrix), so there
        is no switching network here. See connect_twist_roll.

TWO METRICS, easily confused
    position   what the animator reads - a fraction of TAIL LENGTH, so 5
               is halfway down the tail.
    joint_pos  what the network compares against - a fraction of the
               curve's PARAMETER range (a normalised Greville abscissa),
               which is what a pointOnCurveInfo needs to land on a joint.
    The two diverge wherever joints are unevenly spaced. A remapValue per
    control converts the first into the second (set_curveinfo_fk); always
    read it through control_position_plug, never off the raw dial.

KEY DECISIONS
    Stock nodes only, wired with maya.cmds. The module installs by copying
    scripts: no compiled plugin, no per-Maya-version or per-platform
    builds, and a rig opens on any machine that can open Maya, render
    nodes included. Every intermediate value stays an inspectable plug, so
    the network can be debugged in the Node Editor and fixed in a scene
    without a rebuild.

    The cost is node count, which scales with controls x joints, so the
    weighting is kept lean at four nodes per joint per control. For the
    compiled alternative see Serguei Kalentchouk's write-up in the credits
    below: the same job as one C++ node, at the price of a build matrix
    and scenes that will not open without the plugin.

    Rotation only. Controls transmit rotation; position along the chain,
    stretch and twist come from their own networks, which keeps each on a
    separate channel of the SDK stack.

    Falloff stays in joint_pos units while position reads in tail length.
    A width cannot pass through a point-wise remap, and joint units are
    what make num_joints a meaningful joint count.

FUNCTIONS
    control_position_plug   Plug carrying a control's position in joint_pos units
    set_curveinfo_fk        Wire controls to slide along the curve
    falloff_rotation        Distribute one control's rotation across joints in range
    create_sdk_groups       Build the per-joint SDK stack, one layer per control
    get_sdk_groups          Collect existing SDK groups, grouped by layer
    put_jnt_under_sdk_groups  Nest a joint into its stack, keeping its rest pose
    connect_twist_roll      Give the FK chain twist, roll and offset
    offset_unit_scale       Scene units per unit of offset, so FK matches IK

Credits:
- Variable FK based on elephant trunk rig by Jeff Brodsky (vimeo.com/72424469)
- Serguei Kalentchouk, 'Variable FK Revisited' - the same method built as a
  compiled C++ node, worth reading if you want to go that way:
  https://medium.com/@k_serguei/variable-fk-revisited-9e8435c0c337
'''

import maya.cmds as cmds
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_math as rt_math
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_mirror as rt_mirror

logger = logger_setup(__name__)


# ADD CURVEINFO (FK) ===================================================

def control_position_plug(control):
    '''
    Output plug carrying a control's position in joint_pos units.

    Anything comparing a control against jnt.joint_pos must read this, not
    the raw dial: the dial is in tail length, joint_pos is a curve
    parameter fraction, and set_curveinfo_fk's remapValue is what converts
    between them.

    Falls back to the scaled dial on an FK-only build that never ran
    set_curveinfo_fk, so there is no remap to read.

    Arguments
        control (str): Control node-name stem, e.g. 'FK_tail_01'

    Return
        str: plug name to connect from
    '''
    remap = f'{control}_position_remapValue'
    if cmds.objExists(remap):
        return f'{remap}.outValue'
    return f'{control}_control_position_multDoubleLinear.output'


def set_curveinfo_fk(rigname, curve, controls, typ=rt_constants.TYPE_FK):
    '''
    Slide each variable-FK control along the curve from its position dial,
    by driving its parent group from a pointOnCurveInfo.

    The remapValue in that chain is what keeps a control drawn on the
    joints it actually rotates. poci.turnOnPercentage takes a fraction of
    the curve's PARAMETER range rather than of its length, and the two
    diverge wherever the bones are uneven, far enough on a tapered chain to
    draw a control well away from its own bend. So the ramp carries one
    point per joint, mapping that joint's length fraction to its Greville
    fraction. Points are sampled off the curve itself, so degree and CV
    count cannot drift out of step with create_curve.

    falloff_rotation reads the same remap output through
    control_position_plug, which is what holds the two together.

    The ramp is built from the REST curve, so a curve stretching under
    animation drifts the mapping slightly, the same class of static
    approximation as offset_unit_scale.

    Position, joint_pos and the curve parameter all run base to tip. Call
    after setting control attributes.

    Arguments
        rigname (str): Name of rig component
        curve (str): NURBS curve along joint chain
        controls (list): List of Variable FK control names
    '''
    logger.trace(f"{rigname}: Add control curveInfo")
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0]

    # Ramp points read off the curve, not off the joint list: the FK curve
    # carries one CV per joint at the joint positions, so its CVs give both
    # metrics, and its own degree keeps greville_fractions in step.
    num_cv, _spans, degree = rt_maya.get_num_cv(curve)
    cv_pos = [cmds.pointPosition(f'{curve}.cv[{i}]', w=True)
              for i in range(num_cv)]
    ramp_in = rt_math.length_fractions(cv_pos)
    ramp_out = rt_math.greville_fractions(num_cv, degree)

    for i, ctrl in enumerate(controls):
        NN = rt_naming.get_index_from_name(ctrl)
        ctrl_name = f'{typ}_{rigname}_{NN:02d}'

        # Create pointOnCurveInfo node
        poci = f'{ctrl_name}_pointOnCurveInfo'
        cmds.createNode('pointOnCurveInfo', n=poci, s=1, ss=1)
        cmds.setAttr(f'{poci}.turnOnPercentage', 1)
        cmds.connectAttr(f'{crvshape}.worldSpace[0]', f'{poci}.inputCurve', f=1)

        # Get parent group above control
        ctrlgrp = cmds.listRelatives(ctrl, p=True, typ='transform')
        if ctrlgrp:
            ctrlgrp = ctrlgrp[0]
        else:
            abort_build(logger, f'Could not get parent of control {ctrl}.')

        # multDoubleLinear: Scale control position to range(0,1)
        ctrlpos = f'{ctrl_name}_control_position_multDoubleLinear'
        cmds.createNode('multDoubleLinear', n=ctrlpos, s=1, ss=1)
        cmds.connectAttr(f'{ctrl}.position', f'{ctrlpos}.input1', f=1)
        cmds.setAttr(f'{ctrlpos}.input2', 0.1)
        # Output: f'{ctrlpos}.output' = scaled position (0-1)

        # remapValue: tail-length fraction -> curve parameter fraction.
        # One ramp point per joint, linear between them.
        remap = f'{ctrl_name}_position_remapValue'
        if not cmds.objExists(remap):
            cmds.createNode('remapValue', n=remap, s=1, ss=1)
        for idx, (in_v, out_v) in enumerate(zip(ramp_in, ramp_out)):
            cmds.setAttr(f'{remap}.value[{idx}].value_Position', in_v)
            cmds.setAttr(f'{remap}.value[{idx}].value_FloatValue', out_v)
            cmds.setAttr(f'{remap}.value[{idx}].value_Interp', 1) # linear
        # Drop any ramp points left over from a shorter chain: a stale entry
        # past the end of the new ramp would still be interpolated through
        for idx in cmds.getAttr(f'{remap}.value', mi=True) or []:
            if idx >= len(ramp_in):
                cmds.removeMultiInstance(f'{remap}.value[{idx}]', b=True)
        cmds.connectAttr(f'{ctrlpos}.output', f'{remap}.inputValue', f=1)
        cmds.connectAttr(f'{remap}.outValue', f'{poci}.parameter', f=1)

        # pointMatrixMult: Convert world position to local space
        pmm = f'{ctrl_name}_pointMatrixMult'
        cmds.createNode('pointMatrixMult', n=pmm, s=1, ss=1)
        cmds.connectAttr(f'{basectrl}.worldInverseMatrix[0]', f'{pmm}.inMatrix', f=1)
        cmds.connectAttr(f'{poci}.position', f'{pmm}.inPoint', f=1)
        cmds.connectAttr(f'{pmm}.output', f'{ctrlgrp}.translate', f=1)


# FALLOFF ROTATION (FK) ================================================

def falloff_rotation(rigname, n, joints, sdks, typ=rt_constants.TYPE_FK):
    '''
    Distribute one control's rotation across the joints within its falloff.

    Each joint in range receives a share of the control's rotation on this
    control's SDK layer:

        weight = max(0, 1 - |joint_pos - ctrl_pos| / falloff)
        joint_rotation = rotation * weight / num_joints

    A symmetric linear tent - full strength under the control, fading to
    zero at either edge of the falloff and staying there. Dividing by
    num_joints means widening the falloff spreads the same total bend over
    more joints rather than adding more of it.

    `rotation` is this control's rotation plus that of every control before
    it, so the chain reads as FK: bending control 1 carries 2 and 3 along.

    Control attributes:
        position (0-10)   where the control sits, in TAIL-LENGTH units
        falloff (0.1-10)  reach along the chain, in joint_pos units
                          (default 2)
        num_joints        joints currently in range, computed here from
                          falloff and reported back to the animator

    The weight falls out of a single remapValue per joint. Normalising an
    input between two bounds is what that node already does, and
    inputMin/inputMax are connectable, so driving them from ctrl_pos -/+
    falloff yields the tent's position directly with no arithmetic nodes in
    front. Those bounds do not vary along the chain, which is why they are
    built per control rather than per joint.

    A mirror multiply sits between the rotation sum and the weighting. On
    the mirrored side of an L/R pair the controls stand in a
    behaviour-mirrored frame (rt_control.mirror_control_frames) pointing two
    axes the other way from the joints', and negating those two puts gizmo
    and bend back in agreement, so one value poses the pair as mirror
    images.

    Comparisons against jnt.joint_pos go through control_position_plug, so
    a control rotates the joints it is drawn on. See set_curveinfo_fk.

    Arguments
        rigname (str): Name of rig component
        n (int): Index of Variable FK control (0 to NUM_CTRL_FK-1)
        joints (list): List of FK joints
        sdks (list): SDK groups corresponding to this control's layer
    '''
    ctrl = rt_naming.fstr(rigname, rt_constants.CONTROL, '', n+1)
    logger.trace(f"Setup Falloff Rotations for '{ctrl}'")
    control = f'{typ}_{rigname}_{n+1:02d}'

    if len(sdks) != len(joints):
        logger.error('Lists of sdk groups and joints should match in length.')
        return

    # Scale control position to range(0,1)
    ctrlpos = f'{control}_control_position_multDoubleLinear'
    if not cmds.objExists(ctrlpos):
        cmds.createNode('multDoubleLinear', n=ctrlpos, s=1, ss=1)
        cmds.connectAttr(f'{ctrl}.position', f'{ctrlpos}.input1', f=1)
        cmds.setAttr(f'{ctrlpos}.input2', 0.1)
    # The plug to COMPARE against joint_pos is the remap's output, not this
    # node's: set_curveinfo_fk runs first (see rig_tail.rig_tail_fk), so the
    # remap is already there on a full build
    ctrlpos_plug = control_position_plug(control)

    # Scale control falloff to range(0,1)
    falloff = f'{control}_control_falloff_multDoubleLinear'
    if not cmds.objExists(falloff):
        cmds.createNode('multDoubleLinear', n=falloff, s=1, ss=1)
        cmds.connectAttr(f'{ctrl}.falloff', f'{falloff}.input1', f=1)
        cmds.setAttr(f'{falloff}.input2', 0.1)
    # Output: f'{falloff}.output' = scaled falloff (0-1)

    # Remap falloff range(0,1) to num_joints range(1, len(joints))
    setrange = f'{control}_setRange'
    cmds.createNode('setRange', n=setrange, s=1, ss=1)
    cmds.setAttr(f'{setrange}.oldMinX', 0)
    cmds.setAttr(f'{setrange}.oldMaxX', 1)
    cmds.setAttr(f'{setrange}.minX', 1)
    cmds.setAttr(f'{setrange}.maxX', len(joints))
    cmds.connectAttr(f'{falloff}.output', f'{setrange}.valueX', f=1)
    cmds.connectAttr(f'{setrange}.outValueX', f'{ctrl}.num_joints', f=1)

    # Calculate rotation sum (this control + all parent controls)
    rotsum = f'{control}_rotsum_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=rotsum, s=1, ss=1)
    cmds.setAttr(f'{rotsum}.operation', 1) # Add
    cmds.connectAttr(f'{ctrl}.rotate', f'{rotsum}.input3D[0]', f=1)
    # Add rotations from all parent controls
    i = 1
    for parent_n in range(n):
        parent_ctrl = rt_naming.fstr(rigname, rt_constants.CONTROL, '', parent_n+1)
        cmds.connectAttr(f'{parent_ctrl}.rotate', f'{rotsum}.input3D[{i}]', f=1)
        i += 1
    # Output: f'{rotsum}.output3D' = accumulated rotation

    # One node per control - every control summed above is on this side, in
    # this frame. See the docstring for what the signs correct.
    rotation_plug = f'{rotsum}.output3D'
    signs = rt_mirror.control_signs(rigname)
    if signs:
        mirror_node = f'{control}_rotmirror_multiplyDivide'
        cmds.createNode('multiplyDivide', n=mirror_node, s=1, ss=1)
        cmds.setAttr(f'{mirror_node}.operation', 1)  # multiply
        cmds.connectAttr(rotation_plug, f'{mirror_node}.input1', f=1)
        cmds.setAttr(f'{mirror_node}.input2',
                     signs['X'], signs['Y'], signs['Z'], type='double3')
        rotation_plug = f'{mirror_node}.output'

    # 1 / num_joints, which rides on each weight ramp's outputMax below
    # rather than needing a divide per joint
    inv_numjnt = f'{control}_inv_num_joints_multiplyDivide'
    if not cmds.objExists(inv_numjnt):
        cmds.createNode('multiplyDivide', n=inv_numjnt, s=1, ss=1)
        cmds.setAttr(f'{inv_numjnt}.operation', 2) # divide
        cmds.setAttr(f'{inv_numjnt}.input1X', 1)
    # setRange holds num_joints at a minimum of 1, so this cannot divide by 0
    cmds.connectAttr(f'{ctrl}.num_joints', f'{inv_numjnt}.input2X', f=1)

    # The control's reach, as the pair of joint_pos values its ramp spans.
    # The window can never collapse: falloff's attribute minimum of 0.1,
    # scaled by 0.1 above, holds (max - min) at 0.02 or more, so the
    # normalisation inside every remapValue below has a non-zero divisor.
    falloff_min = f'{control}_falloff_min_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=falloff_min, s=1, ss=1)
    cmds.setAttr(f'{falloff_min}.operation', 2) # subtract
    cmds.connectAttr(ctrlpos_plug, f'{falloff_min}.input1D[0]', f=1)
    cmds.connectAttr(f'{falloff}.output', f'{falloff_min}.input1D[1]', f=1)

    falloff_max = f'{control}_falloff_max_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=falloff_max, s=1, ss=1)
    cmds.setAttr(f'{falloff_max}.operation', 1) # add
    cmds.connectAttr(ctrlpos_plug, f'{falloff_max}.input1D[0]', f=1)
    cmds.connectAttr(f'{falloff}.output', f'{falloff_max}.input1D[1]', f=1)

    # For each joint, calculate weighted rotation
    for idx, jnt in enumerate(joints):
        sdk_grp = sdks[idx]
        NN = rt_naming.get_index_from_name(jnt)
        sdk_name = f'{control}_{NN:02d}'

        # The tent, in one node. joint_pos is normalised against the window
        # and clamped there, then the ramp - three linear points running
        # 0 -> 1 -> 0 - is sampled, clamping again at its end points, so a
        # joint past the falloff reads 0 with no condition node. The ramp
        # being symmetric is what saves branching on which side of the
        # control the joint sits.
        #
        # outputMax carries the 1/num_joints scaling: the node returns
        # outputMin + (outputMax - outputMin) * ramp with outputMin at 0, so
        # the weight arrives already divided.
        weight = f'{sdk_name}_weight_remapValue'
        cmds.createNode('remapValue', n=weight, s=1, ss=1)
        # value is a compound of (position, floatValue, interp), so a ramp
        # point is one setAttr rather than three. Interp 1 is linear.
        for r_idx, (r_pos, r_val) in enumerate(((0, 0), (0.5, 1), (1, 0))):
            cmds.setAttr(f'{weight}.value[{r_idx}]', r_pos, r_val, 1,
                         type='double3')
        cmds.connectAttr(f'{jnt}.joint_pos', f'{weight}.inputValue', f=1)
        cmds.connectAttr(f'{falloff_min}.output1D', f'{weight}.inputMin', f=1)
        cmds.connectAttr(f'{falloff_max}.output1D', f'{weight}.inputMax', f=1)
        cmds.connectAttr(f'{inv_numjnt}.outputX', f'{weight}.outputMax', f=1)

        # The joint's share. Out of range the weight is already 0, which
        # zeroes the product, so no gate node is needed.
        rotmult = f'{sdk_name}_rotmult_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult, s=1, ss=1)
        cmds.setAttr(f'{rotmult}.operation', 1) # multiply
        cmds.connectAttr(rotation_plug, f'{rotmult}.input1', f=1)
        for axis in 'XYZ':
            cmds.connectAttr(f'{weight}.outValue', f'{rotmult}.input2{axis}', f=1)

        # Connect final rotation to SDK group
        cmds.connectAttr(f'{rotmult}.output', f'{sdk_grp}.rotate', f=1)


# SDK GROUPS (FK) ======================================================

def create_sdk_groups(rigname, joints, typ=rt_constants.TYPE_FK):
    '''
    Build the stack of SDK groups that every FK joint hangs from.

    One group per variable-FK control, plus one for the joint's own FK
    control, nested and topped by the joint:

        sdk_01 > sdk_02 > sdk_03 > ctrl_sdk > joint

    A layer each is what lets the controls' influences accumulate through
    the hierarchy instead of contending for one channel: falloff_rotation
    writes a single control's weighted rotation into a single layer, while
    twist, roll and stretch each own a different channel of the same stack.
    Every layer also carries a copy of the joint's joint_pos, so the
    weighting network can read it locally.

    Built from the tip down, with each layer's transform baked into
    offsetParentMatrix as it is placed, leaving local rotate at zero for
    falloff_rotation to drive.

    Existing groups are reused where they stand, so a rebuild after a light
    teardown leaves almost nothing to do here.

    One scene query up front picks between two inner loops. With no SDK
    group for this part in the scene every group below is created here, so
    the reuse tests can only answer the same way for all of them and the
    fresh loop skips them. It also skips the match/bake pair, which on two
    groups created at identity matches what already matches and bakes an
    identity, and creates each group nested and unlocked outright rather
    than placing and unlocking it afterwards.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of FK joints
        typ (str): Type identifier (TYPE_FK)

    Return
        fkjnt_grp (str): Top group containing entire FK joint chain with SDK groups
    '''
    logger.trace(f'{rigname}: Create {rt_constants.NUM_CTRL_FK + 1} SDK groups above '
                 f'each of {len(joints)} {typ} joints '
                 f"('{joints[0]}' .. '{joints[-1]}')")
    basejnt = joints[0]
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    fkjnt_grp = rt_naming.fstr(rigname, rt_constants.GROUP, typ)
    first_sdk_grp = None

    if cmds.objExists(fkjnt_grp):
        logger.trace(f"fkjnt_grp exists:'{fkjnt_grp}' basectrl:'{basectrl}'")
        rt_maya.match_transform(fkjnt_grp, basectrl, moc=1)
    else:
        # Check if first_sdk_grp has a parent that could be fkjnt_grp
        first_sdk_parent = cmds.listRelatives(first_sdk_grp, p=True, typ='transform')
        if first_sdk_parent:
            logger.trace(f"fkjnt_grp found:'{first_sdk_parent[0]}' basectrl:'{basectrl}'")
            fkjnt_grp = cmds.rename(first_sdk_parent[0], fkjnt_grp)
            rt_maya.match_transform(fkjnt_grp, basectrl, moc=1)
        else:
            logger.trace(f"Create new fkjnt_grp:'{fkjnt_grp}' basectrl:'{basectrl}'")
            rt_maya.create_group(fkjnt_grp)
            rt_maya.match_transform(fkjnt_grp, basectrl, moc=0)

    # SDK_GRP and SDK_JNT both end with the SDK label, so one pattern covers
    # the whole stack - the same pattern restore_fk_joint_chain deletes by.
    sdk_pattern = f'{typ}_{rigname}_*_{rt_constants.SDK}'
    fresh = not cmds.ls(sdk_pattern, type='transform')

    # Create SDK groups for each joint (in reverse order for proper parenting)
    for jnt in reversed(joints):
        NN = rt_naming.get_index_from_name(jnt)
        jnt_name = rt_naming.fstr(rigname, rt_constants.JOINT, typ, NN, TAG='_sdk')
        prev_sdk_grp = None
        first_sdk_grp = None
        last_sdk_grp = None
        # Read the joint's position label once, not once per SDK layer
        joint_pos = cmds.getAttr(f'{jnt}.joint_pos')

        # Create NUM_CTRL_FK + 1 SDK groups
        for idx in range(rt_constants.NUM_CTRL_FK+1):
            if idx < rt_constants.NUM_CTRL_FK:
                sdk_grp = rt_naming.fstr(rigname, rt_constants.SDK_GRP, typ, NN, nn=idx+1)
            else:
                sdk_grp = rt_naming.fstr(rigname, rt_constants.SDK_JNT, typ, NN)

            if fresh:
                # Nested at creation and left unlocked. A new group sits at
                # identity, so creating it under the layer above places it
                # there outright; and falloff_rotation connects onto rotate
                # further down, which a locked plug refuses.
                kwargs = {'p': prev_sdk_grp} if prev_sdk_grp else {}
                sdk_grp = cmds.createNode('transform', n=sdk_grp, s=1, ss=1,
                                          **kwargs)
                rt_maya.set_channel_flags(
                    sdk_grp, ['translate', 'rotate', 'scale'],
                    k=False, cb=False)
                # Visibility is 1 on a new node, so only its flags are owed
                rt_maya.set_channel_flags(sdk_grp, ['visibility'],
                                          k=False, cb=True, l=False)
            elif not cmds.objExists(sdk_grp):
                rt_maya.create_group(sdk_grp)

            if idx > 0:
                # The layer's own copy of joint_pos. Flags go through the API
                # rather than setAttr: this is the build's hottest loop, and a
                # flag write here is a command spent on display state.
                v = joint_pos
                if not fresh and cmds.attributeQuery('joint_pos', n=sdk_grp, ex=1):
                    rt_maya.set_channel_flags(sdk_grp, ['joint_pos'], l=False)
                    cmds.addAttr(f'{sdk_grp}.joint_pos', e=1, at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                else:
                    cmds.addAttr(sdk_grp, ln='joint_pos', nn='Joint Pos', at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                cmds.setAttr(f'{sdk_grp}.joint_pos', v)
                rt_maya.set_channel_flags(sdk_grp, ['joint_pos'],
                                         cb=True, l=True)

            if prev_sdk_grp: # Nest current SDK group under previous
                # Only a reused group moves; a fresh one is already nested
                if not fresh:
                    rt_maya.parent_to(sdk_grp, prev_sdk_grp, r=True)
                    rt_maya.match_transform(sdk_grp, prev_sdk_grp, moc=1)
            else:
                first_sdk_grp = sdk_grp
            prev_sdk_grp = sdk_grp
        last_sdk_grp = prev_sdk_grp

        put_jnt_under_sdk_groups(jnt, first_sdk_grp, last_sdk_grp)


    # Move first_sdk_grp under fkjnt_grp
    rt_maya.parent_to(first_sdk_grp, fkjnt_grp)
    rt_maya.opm(first_sdk_grp)

    return fkjnt_grp

def get_sdk_groups(joints):
    '''
    Collect the SDK groups above each joint, grouped by layer.

    Walks up from each joint through the stack create_sdk_groups built.
    falloff_rotation takes one row of this: all the joints' groups for a
    single control.

    Arguments
        joints (list): List of FK joints

    Return
        sdk_list (list of lists): SDK groups by layer, outermost first
            sdk_list[0]             = [first SDK group for each joint]
            sdk_list[NUM_CTRL_FK]   = [control SDK group for each joint]
    '''
    logger.trace('Get lists of SDK groups for all joints')
    sdk_list = [list() for n in range(rt_constants.NUM_CTRL_FK+1)]
    for jnt in joints:
        child = jnt
        for num in reversed(range(rt_constants.NUM_CTRL_FK+1)):
            parent = cmds.listRelatives(child, p=True, typ='transform')
            if parent:
                parent = parent[0]
            else:
                abort_build(logger, f'{child} has no parent SDK group.')
            sdk_list[num].append(parent)
            child = parent
    return sdk_list

def put_jnt_under_sdk_groups(jnt, first_sdk_grp, last_sdk_grp):
    '''
    Nest a joint into its SDK stack without moving it.

    The stack takes the joint's place in the hierarchy: it is parented
    alongside the joint under the joint's own parent, cleared, placed on the
    joint and baked, and the joint then moves underneath it. Its rest
    transform ends up baked into the top group's offsetParentMatrix, which
    leaves local rotate at zero for falloff_rotation to drive - a rest
    orientation left on that channel would be overwritten the instant the
    connection is made.

    The joint moves ONCE, which is what the ordering buys. Joints are
    wrapped tip-first, so a joint already carries the whole wrapped chain
    below it and every reparent drags that subtree along - the most
    expensive command in the build on a long part.

    Returns immediately if the joint is already nested, which is the usual
    case on a rebuild.

    Arguments
        jnt (str): Joint to nest
        first_sdk_grp (str): Top SDK group in hierarchy
        last_sdk_grp (str): Bottom SDK group (direct parent of joint)
    '''
    if rt_maya.is_parent(jnt, last_sdk_grp):
        return # Joint already under SDK groups

    jnt_parent = cmds.listRelatives(jnt, p=True) or []
    if jnt_parent:
        jnt_parent = jnt_parent[0]
        logger.trace(f"jnt:'{jnt}' jnt_parent:'{jnt_parent}' first_sdk_grp:'{first_sdk_grp}' last_sdk_grp:'{last_sdk_grp}'")

        # The joint stays where it is while the stack is built beside it:
        # they are siblings until the single move below, so the joint's
        # world transform is what the placement here reads.
        rt_maya.parent_to(first_sdk_grp, jnt_parent)
        cmds.matchTransform(first_sdk_grp, jnt_parent)
        rt_maya.reset_opm(first_sdk_grp)
        rt_maya.reset_transforms(first_sdk_grp)
        cmds.matchTransform(first_sdk_grp, jnt)
        rt_maya.opm(first_sdk_grp)

        # Absolute, so the rest transform stays on the stack's
        # offsetParentMatrix and the joint's own channels come out clear.
        # Any extra transform the reparent inserts on the way is dropped.
        rt_maya.parent_to(jnt, last_sdk_grp, a=1)
        transf = cmds.listRelatives(jnt, p=True, typ='transform')[0]
        if transf != last_sdk_grp:
            cmds.ungroup(transf)
    else:
        # No parent - simple case
        rt_maya.match_transform(first_sdk_grp, jnt, moc=0)
        rt_maya.parent_to(jnt, last_sdk_grp, a=1)


# TWIST / ROLL (FK) ====================================================

def connect_twist_roll(rigname, joints):
    '''
    Give the FK chain twist, roll and offset from the basectrl dials.

    Builds the FK-side equivalent of what those three dials do to the IK
    spline handle, so they work in either mode. The BN chain blends between
    the FK and IK drivers (rig_tail_matrix), so there is no switching
    network here - both sides simply exist.

    twist   linear world-space ramp, zero at the base to full at the tip.
        Every joint gets the SAME local increment (twist / N) about the aim
        axis, on its SDK_JNT layer: local rotations compound down a serial
        chain, so a constant increment integrates into a linear ramp by
        itself. Weighting by joint index would double-compound it into a
        curve - do not do that.
    roll    rigid roll of the whole chain, no ramp. Only the FIRST joint
        takes the value; the rest inherit it through the hierarchy. Adding
        it per joint, as twist does, would stack into a staircase.
    offset  slides the chain along its own length, on the base joint's
        layer-1 translate. An APPROXIMATION of the IK meaning: on the
        spline handle offset re-samples joints along the curve, which has
        no exact analog in a chain shaped by rotations. Close on a straight
        or gently curved tail, not on a tight curl.

    Sources come from rt_ctrlall.resolved_plug, never the basectrl
    attribute directly - with the Main Controller dashboard active the
    tail's own value is only one input of its override condition, and
    reading behind that makes the cog's 'All Twist' look dead in FK mode.

    All three carry an L/R mirror sign (rig_tail_mirror), so a pair obeys
    MIRROR_BEHAVIOR on the aim axis the way curl and wave do on theirs.
    twist and roll turn the chain and take the ROTATION sign; offset slides
    it and takes the TRANSLATION one, which mirrors under the opposite rule.

    Channels are chosen to avoid contention: FK stretch drives layer-1
    translate on joints 1..N but skips joint 0, leaving the base joint's
    free for offset. SDK_JNT.rotate may already carry an individual FK
    control under INDIV_FK, so twist and roll route through an add node
    rather than overwriting it.

    Arguments
        rigname (str): Name of rig component
        joints (list): FK joints, base to tip
    '''
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    if not cmds.objExists(basectrl) \
            or not cmds.attributeQuery('twist', n=basectrl, ex=1):
        return

    # plusMinusAverage's input3D[i] children are lowercase (.input3Dx),
    # unlike multiplyDivide's uppercase .outputX used elsewhere here.
    if rt_constants.ORIENT_AIM_AXIS not in ('x', 'y', 'z'):
        logger.warning(f"{rigname}: Unknown ORIENT_AIM_AXIS "
                       f"'{rt_constants.ORIENT_AIM_AXIS}', skipping twist/roll")
        return
    axis = rt_constants.ORIENT_AIM_AXIS
    n = len(joints)
    if not n:
        return

    twist_src = rt_ctrlall.resolved_plug(rigname, 'twist')
    roll_src = rt_ctrlall.resolved_plug(rigname, 'roll')
    offset_src = rt_ctrlall.resolved_plug(rigname, 'offset')

    # L/R mirror signs for the aim axis - the one all three dials act on
    aim_key = rt_mirror.aim_axis()
    rot_sign = rt_mirror.rotation_signs(rigname).get(aim_key, 1.0)
    trn_sign = rt_mirror.translation_signs(rigname).get(aim_key, 1.0)

    # twist / N, shared by every joint (see docstring: the constant term
    # is what produces a linear ramp once it compounds down the hierarchy).
    # The mirror sign rides on the divisor rather than a node of its own.
    twist_step = f'{rt_constants.TYPE_FK}_{rigname}_twist_step_multiplyDivide'
    if not cmds.objExists(twist_step):
        cmds.createNode('multiplyDivide', n=twist_step, s=1, ss=1)
        cmds.setAttr(f'{twist_step}.operation', 2)  # divide
    cmds.connectAttr(twist_src, f'{twist_step}.input1X', f=1)
    cmds.setAttr(f'{twist_step}.input2X', n * rot_sign)

    # An unsigned roll goes straight onto the base joint's layer; only a
    # negated one needs a node to carry the sign
    roll_plug = roll_src
    roll_mult = f'{rt_constants.TYPE_FK}_{rigname}_roll_mirror_multiplyDivide'
    if rot_sign < 0:
        if not cmds.objExists(roll_mult):
            cmds.createNode('multiplyDivide', n=roll_mult, s=1, ss=1)
            cmds.setAttr(f'{roll_mult}.operation', 1)  # multiply
        cmds.connectAttr(roll_src, f'{roll_mult}.input1X', f=1)
        cmds.setAttr(f'{roll_mult}.input2X', rot_sign)
        roll_plug = f'{roll_mult}.outputX'
    elif cmds.objExists(roll_mult):
        rt_maya.remove(roll_mult)

    for i, jnt in enumerate(joints):
        NN = rt_naming.get_index_from_name(jnt)
        sdk_jnt = rt_naming.fstr(rigname, rt_constants.SDK_JNT, rt_constants.TYPE_FK, NN)
        if not cmds.objExists(sdk_jnt):
            continue

        sum_node = f'{rt_constants.TYPE_FK}_{rigname}_{NN}_twistroll_plusMinusAverage'
        if not cmds.objExists(sum_node):
            cmds.createNode('plusMinusAverage', n=sum_node, s=1, ss=1)
            cmds.setAttr(f'{sum_node}.operation', 1)  # add

        # Reroute whatever currently drives this SDK_JNT layer through the
        # sum node instead of overwriting it. Skip if it is already this
        # sum node (rebuild-safe: re-running must not feed the node's own
        # output back into itself).
        existing = cmds.listConnections(f'{sdk_jnt}.rotate', s=True,
                                        d=False, p=True) or []
        if existing and existing[0].split('.')[0] != sum_node:
            cmds.connectAttr(existing[0], f'{sum_node}.input3D[0]', f=1)

        cmds.connectAttr(f'{twist_step}.outputX',
                         f'{sum_node}.input3D[1].input3D{axis}', f=1)
        if i == 0:
            cmds.connectAttr(roll_plug,
                             f'{sum_node}.input3D[2].input3D{axis}', f=1)

        cmds.connectAttr(f'{sum_node}.output3D', f'{sdk_jnt}.rotate', f=1)

    # offset: rigid slide along the base joint's aim axis. Layer-1 of
    # joint 0 is free of FK stretch (which starts at joint 1), so this
    # needs no summing node. Scaled into the IK handle's units first.
    base_NN = rt_naming.get_index_from_name(joints[0])
    base_sdk = rt_naming.fstr(rigname, rt_constants.SDK_GRP, rt_constants.TYPE_FK, base_NN, 1)
    if not cmds.objExists(base_sdk):
        logger.warning(f'{rigname}: No base SDK group {base_sdk}, '
                       f'skipping FK offset')
        return
    scale_node = f'{rt_constants.TYPE_FK}_{rigname}_offset_scale_multiplyDivide'
    if not cmds.objExists(scale_node):
        cmds.createNode('multiplyDivide', n=scale_node, s=1, ss=1)
        cmds.setAttr(f'{scale_node}.operation', 1)  # multiply
    cmds.connectAttr(offset_src, f'{scale_node}.input1X', f=1)
    # Translation sign, not the rotation one - see the docstring
    cmds.setAttr(f'{scale_node}.input2X',
                 offset_unit_scale(rigname) * trn_sign)
    cmds.connectAttr(f'{scale_node}.outputX',
                     f'{base_sdk}.translate{axis.upper()}', f=1)


def offset_unit_scale(rigname):
    '''
    Scene units the chain slides per 1.0 of `offset`, so FK and IK offset
    read the same at the same dial value.

    The two are not natively in the same units. On the spline handle
    `.offset` is a CURVE PARAMETER shift - one unit moves the joints by one
    parameter's worth of arc length - while the FK network writes scene
    units straight onto a translate. Left unconverted, switching mode with
    offset dialled in jumps.

    Measured against the curve the IK handle solves against, since its
    parameter range is what offset indexes. The FK curve is the fallback
    for an FK-only build, where there is no IK to match and the scale only
    has to stay sane. Returns 1.0 if neither curve is available, leaving
    the raw behaviour rather than guessing.

    Arguments
        rigname (str): Name of rig component

    Return
        float: multiplier from offset units to scene units
    '''
    curves = [rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_IK, TAG='_spline'),
              rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_FK)]
    for curve in curves:
        if not cmds.objExists(curve):
            continue
        shapes = cmds.listRelatives(curve, s=True, ni=True) or []
        if not shapes:
            continue
        span = (cmds.getAttr(f'{shapes[0]}.maxValue')
                - cmds.getAttr(f'{shapes[0]}.minValue'))
        if span <= 1e-6:
            continue
        scale = cmds.arclen(curve) / span
        logger.trace(f'{rigname}: FK offset unit scale {scale:.4f} '
                     f"from '{curve}'")
        return scale
    logger.warning(f'{rigname}: No curve to derive the FK offset unit '
                   f'scale from; offset stays in raw scene units')
    return 1.0
