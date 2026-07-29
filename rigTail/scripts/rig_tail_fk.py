'''
# rig_tail_fk.py
author: Daisy Jane @gnitemouse

Rig Tail FK: Variable FK system with sliding controls

In FK mode, rotate varFK controls to control tail shape.
Use Position attribute to move varFK controls along curve.
Use Falloff attribute to adjust the range of joints affected.

System Overview:
    Creates Variable FK controls (N=3 by default) that slide along the curve
    and distribute their rotation to joints based on position and falloff.
    Each joint has SDK groups that receive weighted rotation from all controls.

Credits:
- Variable FK based on elephant trunk rig by Jeff Brodsky (vimeo.com/72424469)
- Test world colinearity by Chris Evans
  (http://www.chrisevans3d.com/pub_blog/maya-python-vector-math-primer/)
'''

import maya.cmds as cmds
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_math as rt_mat
import rig_tail_ctrlall as rt_ca

logger = logger_setup(__name__)


# ADD CURVEINFO (FK) ===================================================

def set_curveinfo_fk(rigname, curve, controls, typ=rt_cst.TYPE_FK):
    '''
    Parameterize control position to curve length using pointOnCurveInfo.
    Creates node network to slide controls along curve based on position attribute.

    Node network per control:
    1. curveInfo: Measures total curve length
    2. multDoubleLinear (ctrlpos): Scales position attribute (0-10) to range (0-1)
    3. plusMinusAverage (pma): Calculates parameter = 1 - ctrlpos (reverses direction)
    4. pointOnCurveInfo (poci): Gets world position on curve at parameter
    5. pointMatrixMult (pmm): Converts world position to local space
    6. Connection: pmm.output -> control_group.translate

    Called after setting control attributes.

    Arguments
        rigname (str): Name of rig component
        curve (str): NURBS curve along joint chain
        controls (list): List of Variable FK control names
    '''
    logger.trace(f"{rigname}: Add control curveInfo")
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    curveinfo = rt_mya.create_curveinfo(rigname, curve, rt_cst.TYPE_FK)
    crvshape = cmds.listRelatives(curve, s=True, ni=True)[0]

    for i, ctrl in enumerate(controls):
        NN = rt_nam.get_index_from_name(ctrl)
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

        # plusMinusAverage: Create parameter (1 - ctrlpos)
        pma = f'{ctrl_name}_parameter_plusMinusAverage'
        cmds.createNode('plusMinusAverage', n=pma, s=1, ss=1)
        cmds.setAttr(f'{pma}.operation', 2) # Subtract
        cmds.setAttr(f'{pma}.input1D[0]', 1) # 1 - position
        cmds.connectAttr(f'{ctrlpos}.output', f'{pma}.input1D[1]', f=1)
        cmds.connectAttr(f'{pma}.output1D', f'{poci}.parameter', f=1)

        # pointMatrixMult: Convert world position to local space
        pmm = f'{ctrl_name}_pointMatrixMult'
        cmds.createNode('pointMatrixMult', n=pmm, s=1, ss=1)
        cmds.connectAttr(f'{basectrl}.worldInverseMatrix[0]', f'{pmm}.inMatrix', f=1)
        cmds.connectAttr(f'{poci}.position', f'{pmm}.inPoint', f=1)
        cmds.connectAttr(f'{pmm}.output', f'{ctrlgrp}.translate', f=1)


# FALLOFF ROTATION (FK) ================================================

def falloff_rotation(rigname, n, joints, sdks, typ=rt_cst.TYPE_FK):
    '''
    Remap control's rotation to joint rotations with falloff.

    Falloff Algorithm:
    - Each control affects joints within its falloff range
    - Rotation is distributed using a linear ramp within the falloff range
    - Multiple controls' rotations accumulate on each joint

    Control Attributes:
        position (0-10): Control position along curve (0=base, 10=tip)
        falloff (0.1-10): Range of influence (default 2)
        num_joints (calculated): Number of joints affected by falloff

    Range Calculation:
        Valid range: (ctrl - falloff) <= joint_pos <= (ctrl + falloff)
        (+) If joint is ahead of control: falloff_pos = ctrl + falloff
            rotmult_pos = (ctrl - jnt + falloff) / falloff
        (-) If joint is behind control: falloff_neg = ctrl - falloff
            rotmult_neg = (jnt - ctrl + falloff) / falloff

    Final Rotation:
        joint_rotation = control_rotation * rotmult * (1 / num_joints)

    This creates smooth falloff where joints closer to control receive
    more rotation, and rotation fades to zero at the falloff boundary.

    Arguments
        rigname (str): Name of rig component
        n (int): Index of Variable FK control (0 to NUM_CTRL_FK-1)
        joints (list): List of FK joints
        sdks (list): SDK groups corresponding to this control's layer
    '''
    ctrl = rt_nam.fstr(rigname, rt_cst.CONTROL, '', n+1)
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
    # Output: f'{ctrlpos}.output' = scaled position (0-1)

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
        parent_ctrl = rt_nam.fstr(rigname, rt_cst.CONTROL, '', parent_n+1)
        cmds.connectAttr(f'{parent_ctrl}.rotate', f'{rotsum}.input3D[{i}]', f=1)
        i += 1
    # Output: f'{rotsum}.output3D' = accumulated rotation

    # For each joint, calculate weighted rotation
    for idx, jnt in enumerate(joints):
        sdk_grp = sdks[idx]
        NN = rt_nam.get_index_from_name(jnt)
        sdk_name = f'{control}_{NN:02d}'

        # Calculate rotation multiplier for positive direction
        # rotmult_pos = (ctrl - jnt + falloff) / falloff
        ctrl_minus_jnt = f'{sdk_name}_ctrl_minus_jnt_plusMinusAverage'
        numerator_pos = f'{sdk_name}_numerator_pos_plusMinusAverage'

        # (ctrl - jnt)
        cmds.createNode('plusMinusAverage', n=ctrl_minus_jnt, s=1, ss=1)
        cmds.setAttr(f'{ctrl_minus_jnt}.operation', 2) # subtract
        cmds.connectAttr(f'{ctrlpos}.output', f'{ctrl_minus_jnt}.input1D[0]', f=1)
        cmds.connectAttr(f'{jnt}.joint_pos', f'{ctrl_minus_jnt}.input1D[1]', f=1)

        # (ctrl - jnt + falloff)
        cmds.createNode('plusMinusAverage', n=numerator_pos, s=1, ss=1)
        cmds.setAttr(f'{numerator_pos}.operation', 1) # add
        cmds.connectAttr(f'{ctrl_minus_jnt}.output1D', f'{numerator_pos}.input1D[0]', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{numerator_pos}.input1D[1]', f=1)

        # Calculate rotation multiplier for negative direction
        # rotmult_neg = (jnt - ctrl + falloff) / falloff
        jnt_minus_ctrl = f'{sdk_name}_jnt_minus_ctrl_plusMinusAverage'
        numerator_neg = f'{sdk_name}_numerator_neg_plusMinusAverage'

        # (jnt - ctrl)
        cmds.createNode('plusMinusAverage', n=jnt_minus_ctrl, s=1, ss=1)
        cmds.setAttr(f'{jnt_minus_ctrl}.operation', 2) # subtract
        cmds.connectAttr(f'{jnt}.joint_pos', f'{jnt_minus_ctrl}.input1D[0]', f=1)
        cmds.connectAttr(f'{ctrlpos}.output', f'{jnt_minus_ctrl}.input1D[1]', f=1)

        # (jnt - ctrl + falloff)
        cmds.createNode('plusMinusAverage', n=numerator_neg, s=1, ss=1)
        cmds.setAttr(f'{numerator_neg}.operation', 1) # add
        cmds.connectAttr(f'{jnt_minus_ctrl}.output1D', f'{numerator_neg}.input1D[0]', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{numerator_neg}.input1D[1]', f=1)

        # Divide by falloff to get multipliers
        rotmult_pos = f'{sdk_name}_rotmult_pos_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult_pos, s=1, ss=1)
        cmds.setAttr(f'{rotmult_pos}.operation', 2) # divide
        cmds.connectAttr(f'{numerator_pos}.output1D', f'{rotmult_pos}.input1X', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{rotmult_pos}.input2X', f=1)

        rotmult_neg = f'{sdk_name}_rotmult_neg_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult_neg, s=1, ss=1)
        cmds.setAttr(f'{rotmult_neg}.operation', 2) # divide
        cmds.connectAttr(f'{numerator_neg}.output1D', f'{rotmult_neg}.input1X', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{rotmult_neg}.input2X', f=1)

        # Check if joint falls inside falloff range
        falloff_pos_cond = f'{sdk_name}_falloff_pos_{rt_cst.COND}'
        falloff_neg_cond = f'{sdk_name}_falloff_neg_{rt_cst.COND}'
        cmds.createNode('condition', n=falloff_pos_cond, s=1, ss=1)
        cmds.createNode('condition', n=falloff_neg_cond, s=1, ss=1)

        # (+) Check if jnt <= ctrl + falloff
        cmds.setAttr(f'{falloff_pos_cond}.operation', 3) # greater or equal
        cmds.connectAttr(f'{numerator_pos}.output1D', f'{falloff_pos_cond}.firstTerm', f=1)
        cmds.setAttr(f'{falloff_pos_cond}.secondTerm', 0)
        cmds.setAttr(f'{falloff_pos_cond}.colorIfFalseR', 0)
        cmds.setAttr(f'{falloff_pos_cond}.colorIfTrueR', 1)

        # (-) Check if jnt >= ctrl - falloff
        cmds.setAttr(f'{falloff_neg_cond}.operation', 3) # greater or equal
        cmds.connectAttr(f'{numerator_neg}.output1D', f'{falloff_neg_cond}.firstTerm', f=1)
        cmds.setAttr(f'{falloff_neg_cond}.secondTerm', 0)
        cmds.setAttr(f'{falloff_neg_cond}.colorIfFalseR', 0)
        cmds.setAttr(f'{falloff_neg_cond}.colorIfTrueR', 1)

        # Choose appropriate rotation multiplier based on position
        cond = f'{sdk_name}_rotmult_{rt_cst.COND}'
        cmds.createNode('condition', n=cond, s=1, ss=1)
        cmds.setAttr(f'{cond}.operation', 2) # greater than
        cmds.connectAttr(f'{ctrlpos}.output', f'{cond}.firstTerm', f=1) # ctrlpos
        cmds.connectAttr(f'{jnt}.joint_pos', f'{cond}.secondTerm', f=1) # jntpos

        # If ctrl > jnt (control ahead): use rotmult_neg
        # If ctrl <= jnt (control behind): use rotmult_pos
        cmds.connectAttr(f'{rotmult_pos}.outputX', f'{cond}.colorIfFalseR', f=1)
        cmds.connectAttr(f'{rotmult_neg}.outputX', f'{cond}.colorIfTrueR', f=1)
        cmds.connectAttr(f'{falloff_pos_cond}.outColorR', f'{cond}.colorIfFalseG', f=1)
        cmds.connectAttr(f'{falloff_neg_cond}.outColorR', f'{cond}.colorIfTrueG', f=1)

        # Apply rotation multiplier to accumulated rotation
        rotmult = f'{sdk_name}_rotmult_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult, s=1, ss=1)
        cmds.setAttr(f'{rotmult}.operation', 1) # multiply
        cmds.connectAttr(f'{rotsum}.output3D', f'{rotmult}.input1', f=1)
        cmds.connectAttr(f'{cond}.outColorR', f'{rotmult}.input2X', f=1)
        cmds.connectAttr(f'{cond}.outColorR', f'{rotmult}.input2Y', f=1)
        cmds.connectAttr(f'{cond}.outColorR', f'{rotmult}.input2Z', f=1)

        # Divide by num_joints for percentage
        percentage = f'{sdk_name}_percentage_multiplyDivide'
        cmds.createNode('multiplyDivide', n=percentage, s=1, ss=1)
        cmds.setAttr(f'{percentage}.operation', 2) # divide
        cmds.connectAttr(f'{rotmult}.output', f'{percentage}.input1', f=1)
        cmds.connectAttr(f'{ctrl}.num_joints', f'{percentage}.input2X', f=1)
        cmds.connectAttr(f'{ctrl}.num_joints', f'{percentage}.input2Y', f=1)
        cmds.connectAttr(f'{ctrl}.num_joints', f'{percentage}.input2Z', f=1)

        # Apply threshold (zero out rotation if outside falloff range)
        threshold_cond = f'{sdk_name}_threshold_{rt_cst.COND}'
        cmds.createNode('condition', n=threshold_cond, s=1, ss=1)
        cmds.connectAttr(f'{cond}.outColorG', f'{threshold_cond}.firstTerm', f=1)
        cmds.setAttr(f'{threshold_cond}.secondTerm', 0)
        cmds.setAttr(f'{threshold_cond}.operation', 1) # not equal
        cmds.setAttr(f'{threshold_cond}.colorIfFalse', 0,0,0)
        cmds.connectAttr(f'{percentage}.output', f'{threshold_cond}.colorIfTrue', f=1)

        # Connect final rotation to SDK group
        cmds.connectAttr(f'{threshold_cond}.outColor', f'{sdk_grp}.rotate', f=1)


# SDK GROUPS (FK) ======================================================

def create_sdk_groups(rigname, joints, typ=rt_cst.TYPE_FK):
    '''
    Create NUM_CTRL_FK+1 SDK groups above each joint for rotation distribution.

    SDK Group Structure (per joint):
    - NUM_CTRL_FK SDK groups (one per Variable FK control)
    - 1 control SDK group (for FK joint control rotation)
    - Nested hierarchy: sdk_01 > sdk_02 > sdk_03 > ctrl_sdk > joint

    Each SDK layer accumulates rotation from one Variable FK control.
    The control SDK receives direct rotation from FK joint control.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of FK joints
        typ str): Type identifier (TYPE_FK)

    Return
        fkjnt_grp (str): Top group containing entire FK joint chain with SDK groups
    '''
    logger.trace(f'{rigname}: Create {rt_cst.NUM_CTRL_FK + 1} SDK groups above '
                 f'each of {len(joints)} {typ} joints '
                 f"('{joints[0]}' .. '{joints[-1]}')")
    basejnt = joints[0]
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    fkjnt_grp = rt_nam.fstr(rigname, rt_cst.GROUP, typ)
    first_sdk_grp = None

    if cmds.objExists(fkjnt_grp):
        logger.trace(f"fkjnt_grp exists:'{fkjnt_grp}' basectrl:'{basectrl}'")
        rt_mya.match_transform(fkjnt_grp, basectrl, moc=1)
    else:
        # Check if first_sdk_grp has a parent that could be fkjnt_grp
        first_sdk_parent = cmds.listRelatives(first_sdk_grp, p=True, typ='transform')
        if first_sdk_parent:
            logger.trace(f"fkjnt_grp found:'{first_sdk_parent[0]}' basectrl:'{basectrl}'")
            fkjnt_grp = cmds.rename(first_sdk_parent[0], fkjnt_grp)
            rt_mya.match_transform(fkjnt_grp, basectrl, moc=1)
        else:
            logger.trace(f"Create new fkjnt_grp:'{fkjnt_grp}' basectrl:'{basectrl}'")
            rt_mya.create_group(fkjnt_grp)
            rt_mya.match_transform(fkjnt_grp, basectrl, moc=0)


    # Create SDK groups for each joint (in reverse order for proper parenting)
    for jnt in reversed(joints):
        NN = rt_nam.get_index_from_name(jnt)
        jnt_name = rt_nam.fstr(rigname, rt_cst.JOINT, typ, NN, TAG='_sdk')
        prev_sdk_grp = None
        first_sdk_grp = None
        last_sdk_grp = None
        # Read the joint's position label once, not once per SDK layer
        joint_pos = cmds.getAttr(f'{jnt}.joint_pos')

        # Create NUM_CTRL_FK + 1 SDK groups
        for idx in range(rt_cst.NUM_CTRL_FK+1):
            if idx < rt_cst.NUM_CTRL_FK:
                sdk_grp = rt_nam.fstr(rigname, rt_cst.SDK_GRP, typ, NN, nn=idx+1)
            else:
                sdk_grp = rt_nam.fstr(rigname, rt_cst.SDK_JNT, typ, NN)

            if not cmds.objExists(sdk_grp):
                rt_mya.create_group(sdk_grp)

            if idx > 0:
                # Set joint_pos attribute on SDK group (copy from joint).
                # The channel-box and lock flags go through the API
                # (rt_mya.set_channel_flags): this runs NUM_CTRL_FK times
                # per joint per rig part, so it is the hottest loop in the
                # build, and a flag write there is a command spent on
                # display state.
                v = joint_pos
                if cmds.attributeQuery('joint_pos', n=sdk_grp, ex=1):
                    rt_mya.set_channel_flags(sdk_grp, ['joint_pos'], l=False)
                    cmds.addAttr(f'{sdk_grp}.joint_pos', e=1, at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                else:
                    cmds.addAttr(sdk_grp, ln='joint_pos', nn='Joint Pos', at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                cmds.setAttr(f'{sdk_grp}.joint_pos', v)
                rt_mya.set_channel_flags(sdk_grp, ['joint_pos'],
                                         cb=True, l=True)

            if prev_sdk_grp: # Nest current SDK group under previous
                rt_mya.parent_to(sdk_grp, prev_sdk_grp, r=True)
                rt_mya.match_transform(sdk_grp, prev_sdk_grp, moc=1)
            else:
                first_sdk_grp = sdk_grp
            prev_sdk_grp = sdk_grp
        last_sdk_grp = prev_sdk_grp

        put_jnt_under_sdk_groups(jnt, first_sdk_grp, last_sdk_grp)


    # Move first_sdk_grp under fkjnt_grp
    rt_mya.parent_to(first_sdk_grp, fkjnt_grp)
    rt_mya.opm(first_sdk_grp)

    return fkjnt_grp

def get_sdk_groups(joints):
    '''
    Get SDK groups for all joints.
    Returns multidimensional list organized by SDK layer.

    Arguments
        joints (list): List of FK joints

    Return
        sdk_list (list of lists): SDK groups organized by layer
            sdk_list[0] = [first SDK group for each joint]
            sdk_list[1] = [second SDK group for each joint]
            ...
            sdk_list[NUM_CTRL_FK] = [control SDK group for each joint]
    '''
    logger.trace('Get lists of SDK groups for all joints')
    sdk_list = [list() for n in range(rt_cst.NUM_CTRL_FK+1)]
    for jnt in joints:
        child = jnt
        for num in reversed(range(rt_cst.NUM_CTRL_FK+1)):
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
    Nest joint under SDK group hierarchy.
    Preserves joint transforms while reparenting.

    Arguments
        jnt (str): Joint to nest
        first_sdk_grp (str): Top SDK group in hierarchy
        last_sdk_grp (str): Bottom SDK group (direct parent of joint)
    '''
    if rt_mya.is_parent(jnt, last_sdk_grp):
        return # Joint already under SDK groups

    jnt_parent = cmds.listRelatives(jnt, p=True) or []
    if jnt_parent:
        jnt_parent = jnt_parent[0]
        # Create temporary group to preserve joint transform. createNode
        # rather than cmds.group(em=True): identical result, a sixth of the
        # cost, and this runs once per FK joint per rig part
        tmp_grp = cmds.createNode('transform', n=f'{jnt}_tmp', ss=1)
        cmds.matchTransform(tmp_grp, jnt)
        rt_mya.parent_to(jnt, tmp_grp, a=1) # Unparent joint
        logger.trace(f"jnt:'{jnt}' jnt_parent:'{jnt_parent}' first_sdk_grp:'{first_sdk_grp}' last_sdk_grp:'{last_sdk_grp}'")

        # Move first_sdk_grp under joint's parent
        rt_mya.parent_to(first_sdk_grp, jnt_parent)
        cmds.matchTransform(first_sdk_grp, jnt_parent)
        rt_mya.reset_opm(first_sdk_grp)
        rt_mya.reset_transforms(first_sdk_grp)
        cmds.matchTransform(first_sdk_grp, jnt)
        # Bake the joint's rest transform into offsetParentMatrix, which
        # leaves local rotate at zero. falloff_rotation connects the
        # variable-FK output to this group's rotate, so a rest
        # orientation left there would be overwritten the moment that
        # connection is made - silently flattening any chain whose joints
        # are not already aligned with their parent.
        rt_mya.opm(first_sdk_grp)

        # Move joint under last_sdk_grp
        rt_mya.parent_to(jnt, last_sdk_grp, a=1)
        # Clean up any extra transform created by reparenting
        transf = cmds.listRelatives(jnt, p=True, typ='transform')[0]
        if transf != last_sdk_grp:
            cmds.ungroup(transf)
        cmds.delete(tmp_grp)
    else:
        # No parent - simple case
        rt_mya.match_transform(first_sdk_grp, jnt, moc=0)
        rt_mya.parent_to(jnt, last_sdk_grp, a=1)


# TWIST / ROLL (FK) ====================================================

def connect_twist_roll(rigname, joints):
    '''
    Give the FK chain its own twist/roll/offset network, so the same three
    basectrl attributes that drive the IK spline handle also work in FK
    mode. The BN chain follows a blendMatrix of the FK and IK drivers
    (rig_tail_matrix), so building both is what makes the dials switch
    with the IKFK mode - there is no separate switching network.

    Sources come from rt_ca.resolved_plug, NOT the basectrl attribute
    directly: with the Main Controller dashboard active the tail's own
    value is only one input of its override condition, and reading the
    basectrl behind that condition's back is what makes the cog's
    'All Twist' appear to do nothing while the rig sits in FK mode.

    twist - linear world-space ramp, 0 at the base to full value at the
        tip. Every joint gets the SAME local increment (twist / N) about
        the aim axis, on its SDK_JNT layer. A serial FK chain's local
        rotations compound additively down the hierarchy about one
        consistent (Setup-guaranteed twist-free) aim axis, so a constant
        per-joint increment integrates into a linear world ramp on its
        own. Weighting by joint index would double-compound into a curved
        ramp - do not do that.
    roll - uniform rigid roll of the whole chain, no ramp. Only the FIRST
        joint receives the full value; everything below inherits it
        through the hierarchy. Adding it to every joint (as twist does)
        would stack into a staircase instead.
    offset - slides the chain along its own length, on the base joint's
        SDK_GRP layer-1 translate (aim axis). This is an APPROXIMATION of
        the IK meaning: on the spline handle, offset re-samples the joints
        along the curve, which has no exact analog in a chain whose shape
        comes from rotations rather than a curve. Here it is a rigid slide
        along the base joint's aim axis - visually close on a straight or
        gently curved tail, not on a tight curl.

    Channel choice is deliberate: layer-1 translateX of joints 1..N is
    already driven by FK stretch (connect_fk_stretch_to_joints), which
    skips joint 0 - so the base joint's layer-1 translate is the one free
    channel of its kind, and offset can use it without contending with
    stretch. SDK_JNT.rotate may already be driven by an individual FK
    control under INDIV_FK, so twist/roll reroute that through an add node
    rather than overwriting it.

    Arguments
        rigname (str): Name of rig component
        joints (list): FK joints, base to tip
    '''
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    if not cmds.objExists(basectrl) \
            or not cmds.attributeQuery('twist', n=basectrl, ex=1):
        return

    # plusMinusAverage's input3D[i] children are lowercase (.input3Dx),
    # unlike multiplyDivide's uppercase .outputX used elsewhere here.
    if rt_cst.ORIENT_AIM_AXIS not in ('x', 'y', 'z'):
        logger.warning(f"{rigname}: Unknown ORIENT_AIM_AXIS "
                       f"'{rt_cst.ORIENT_AIM_AXIS}', skipping twist/roll")
        return
    axis = rt_cst.ORIENT_AIM_AXIS
    n = len(joints)
    if not n:
        return

    twist_src = rt_ca.resolved_plug(rigname, 'twist')
    roll_src = rt_ca.resolved_plug(rigname, 'roll')
    offset_src = rt_ca.resolved_plug(rigname, 'offset')

    # twist / N, shared by every joint (see docstring: the constant term
    # is what produces a linear ramp once it compounds down the hierarchy)
    twist_step = f'{rt_cst.TYPE_FK}_{rigname}_twist_step_multiplyDivide'
    if not cmds.objExists(twist_step):
        cmds.createNode('multiplyDivide', n=twist_step, s=1, ss=1)
        cmds.setAttr(f'{twist_step}.operation', 2)  # divide
    cmds.connectAttr(twist_src, f'{twist_step}.input1X', f=1)
    cmds.setAttr(f'{twist_step}.input2X', n)

    for i, jnt in enumerate(joints):
        NN = rt_nam.get_index_from_name(jnt)
        sdk_jnt = rt_nam.fstr(rigname, rt_cst.SDK_JNT, rt_cst.TYPE_FK, NN)
        if not cmds.objExists(sdk_jnt):
            continue

        sum_node = f'{rt_cst.TYPE_FK}_{rigname}_{NN}_twistroll_plusMinusAverage'
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
            cmds.connectAttr(roll_src,
                             f'{sum_node}.input3D[2].input3D{axis}', f=1)

        cmds.connectAttr(f'{sum_node}.output3D', f'{sdk_jnt}.rotate', f=1)

    # offset: rigid slide along the base joint's aim axis. Layer-1 of
    # joint 0 is free of FK stretch (which starts at joint 1), so this
    # needs no summing node. Scaled into the IK handle's units first.
    base_NN = rt_nam.get_index_from_name(joints[0])
    base_sdk = rt_nam.fstr(rigname, rt_cst.SDK_GRP, rt_cst.TYPE_FK, base_NN, 1)
    if not cmds.objExists(base_sdk):
        logger.warning(f'{rigname}: No base SDK group {base_sdk}, '
                       f'skipping FK offset')
        return
    scale_node = f'{rt_cst.TYPE_FK}_{rigname}_offset_scale_multiplyDivide'
    if not cmds.objExists(scale_node):
        cmds.createNode('multiplyDivide', n=scale_node, s=1, ss=1)
        cmds.setAttr(f'{scale_node}.operation', 1)  # multiply
    cmds.connectAttr(offset_src, f'{scale_node}.input1X', f=1)
    cmds.setAttr(f'{scale_node}.input2X', offset_unit_scale(rigname))
    cmds.connectAttr(f'{scale_node}.outputX',
                     f'{base_sdk}.translate{axis.upper()}', f=1)


def offset_unit_scale(rigname):
    '''
    Scene units the chain must slide per 1.0 of `offset`, so FK offset
    matches what IK offset does at the same dial value.

    The two are not natively in the same units. On the spline handle
    `.offset` is a CURVE PARAMETER shift: the joints re-sample along the
    curve, so one unit of offset moves them by one parameter's worth of
    arc length. The FK network instead writes scene units straight onto a
    translate. Measured on a 9-joint tail the same dial value gave 1.0
    unit per joint in FK against ~3.3 in IK, so switching mode with
    offset dialled in visibly jumped.

    Converting needs the curve the IK handle actually solves against
    (its parameter range is what offset indexes) - the FK curve is only
    the fallback for an FK-only build, where there is no IK to match and
    the scale merely has to stay sane. Returns 1.0 when no curve is
    available, leaving the raw behaviour rather than guessing.

    Arguments
        rigname (str): Name of rig component

    Return
        float: multiplier from offset units to scene units
    '''
    curves = [rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_IK, TAG='_spline'),
              rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_FK)]
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
