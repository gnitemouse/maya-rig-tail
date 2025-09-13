'''
# rig_tail_fk.py
author: Daisy Jane Lee @dayzl

Rig Tail FK

In FK mode, rotate FK controls to control the tail.
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
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
from rig_tail_constants import *
from rig_tail_util import *

logger = logger_setup(__name__)


# ADD CURVEINFO (FK) ===================================================

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
        ctrlname = ctrl.rsplit(CTRL, 1)[0]

        # (pointOnCurveInfo) poci FK
        poci = f"{ctrlname}{POCI}"
        cmds.createNode('pointOnCurveInfo', n=poci, s=1, ss=1)
        cmds.setAttr(f"{ctrlname}{POCI}.turnOnPercentage", 1)
        cmds.connectAttr(f"{crvshape}.worldSpace[0]", f"{poci}.inputCurve", f=1)
        # Get parent grp above ctrl
        ctrlgrp = cmds.listRelatives(ctrl, p=True, typ='transform')
        if ctrlgrp:
            ctrlgrp = ctrlgrp[0]
        else:
            logger.error(f"Could not get parent of control {ctrl}.")

        # (multDoubleLinear) ctrlpos - Scale control position to range(0,1)
        ctrlpos = f"{ctrlname}_control_position_multDoubleLinear"
        cmds.createNode('multDoubleLinear', n=ctrlpos, s=1, ss=1)
        cmds.connectAttr(f"{ctrl}.position", f"{ctrlpos}.input1", f=1)
        cmds.setAttr(f"{ctrlpos}.input2", 0.1)
        # Use f"{ctrlpos}.output" for ctrl position

        # (plusMinusAverage) pma - Create parameter plusMinusAverage node
        pma = f"{ctrlname}_parameter_plusMinusAverage"
        cmds.createNode('plusMinusAverage', n=pma, s=1, ss=1)
        cmds.setAttr(f"{pma}.operation", 2) # Subtract
        cmds.setAttr(f"{pma}.input1D[0]", 1) # Subtract from 1
        cmds.connectAttr(f"{ctrlpos}.output", f"{pma}.input1D[1]", f=1)
        cmds.connectAttr(f"{pma}.output1D", f"{poci}.parameter", f=1)

        # (pointMatrixMult) pmm - Create pointMatrixMult node
        pmm = f"{ctrlname}_pointMatrixMult"
        cmds.createNode('pointMatrixMult', n=pmm, s=1, ss=1)
        cmds.connectAttr(f"{basectrl}.worldInverseMatrix[0]", f"{pmm}.inMatrix", f=1)
        cmds.connectAttr(f"{poci}.position", f"{pmm}.inPoint", f=1)
        cmds.connectAttr(f"{pmm}.output", f"{ctrlgrp}.translate", f=1)


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
    control = fstr(rigname, CONTROL, '', n+1) # varfk_ctrl
    control_grp = fstr(rigname, CTRL_GRP, '', n+1) # varfk_ctrlgrp
    logger.info(f"Setup Falloff Rotations for control '{control}'")
    if len(sdks) != len(joints):
        logger.error('Lists of sdk groups and joints should match in length.')
    ctrlname = control.rsplit(CTRL, 1)[0]

    # Cleanup
    mult1 = f"{ctrlname}_multiplyDivide"
    remove(mult1)
    falloff_pos = f"{ctrlname}_falloff_pos_plusMinusAverage"
    falloff_neg = f"{ctrlname}_falloff_neg_plusMinusAverage"
    remove(falloff_pos)
    remove(falloff_neg)

    # (multiplyDivide) minusrot - Remove double rotation from control
    minusrot = f"{ctrlname}_minusrot_multiplyDivide"
    cmds.createNode('multiplyDivide', n=minusrot, s=1, ss=1)
    cmds.setAttr(f"{minusrot}.operation", 1) # multiply
    cmds.setAttr(f"{minusrot}.input2", -0.5,-0.5,-0.5)
    cmds.connectAttr(f"{control}.rotate", f"{minusrot}.input1", f=1)
    # Output f"{minusrot}.output"
    cmds.connectAttr(f"{minusrot}.output", f"{control_grp}.rotate", f=1)

    # (plusMinusAverage) plusrot - Add rotation from controls above
    if n > 0:
        plusrot = f"{ctrlname}_plusrot_plusMinusAverage"
        cmds.createNode('plusMinusAverage', n=plusrot, s=1, ss=1)
        cmds.setAttr(f"{plusrot}.operation", 1) # Add
        cmds.connectAttr(f"{minusrot}.output", f"{plusrot}.input3D[0]", f=1)
        prev_ctrl = fstr(rigname, CONTROL, '', n)
        if n > 1:
            prev_ctrlname = prev_ctrl.rsplit(CTRL, 1)[0]
            prev_plusrot = f"{prev_ctrlname}_plusrot_plusMinusAverage"
            cmds.connectAttr(f"{prev_plusrot}.output3D", f"{plusrot}.input3D[1]", f=1)
        else:
            cmds.connectAttr(f"{prev_ctrl}.rotate", f"{plusrot}.input3D[1]", f=1)
        cmds.connectAttr(f"{plusrot}.output3D", f"{control_grp}.rotate", f=1)

    # (multDoubleLinear) ctrlpos - Scale control position to range(0,1)
    ctrlpos = f"{ctrlname}_control_position_multDoubleLinear"
    if not cmds.objExists(ctrlpos):
        cmds.createNode('multDoubleLinear', n=ctrlpos, s=1, ss=1)
        cmds.connectAttr(f"{control}.position", f"{ctrlpos}.input1", f=1)
        cmds.setAttr(f"{ctrlpos}.input2", 0.1)
    # Output f"{ctrlpos}.output" for ctrl position

    # (multDoubleLinear) falloff - Scale control falloff to range(0,1)
    falloff = f"{ctrlname}_control_falloff_multDoubleLinear"
    if not cmds.objExists(falloff):
        cmds.createNode('multDoubleLinear', n=falloff, s=1, ss=1)
        cmds.connectAttr(f"{control}.falloff", f"{falloff}.input1", f=1)
        cmds.setAttr(f"{falloff}.input2", 0.1)
    # Output f"{falloff}.output" for ctrl falloff

    # Remap range(0,1) to range(0,num_jnts)
    # (setRange) old_min:0 old_max:1 -> new_min:1 new_max:num_jnts
    setrange = f"{ctrlname}_setRange"
    cmds.createNode('setRange', n=setrange, s=1, ss=1)
    cmds.setAttr(f"{setrange}.oldMinX", 0)
    cmds.setAttr(f"{setrange}.oldMaxX", 1)
    cmds.setAttr(f"{setrange}.minX", 1)
    cmds.setAttr(f"{setrange}.maxX", len(joints))
    # ctrl.falloff -> setrange.valueX
    cmds.connectAttr(f"{falloff}.output", f"{setrange}.valueX", f=1)
    # setrange.outValueX -> control
    cmds.connectAttr(f"{setrange}.outValueX", f"{control}.num_joints", f=1)

    # Iterate through joints, connect nodes
    for idx in range(len(joints)):
        sdk = sdks[idx] # sdk group above joint
        jnt = joints[idx] # each joint
        sdk_name = sdk.lstrip(TYPE_FK).rsplit(SDK, 1)[0]

        # Cleanup
        jntfalloff_pos = f"{sdk_name}_jntfalloff_pos_plusMinusAverage"
        jntfalloff_neg = f"{sdk_name}_jntfalloff_neg_plusMinusAverage"
        remove(jntfalloff_pos)
        remove(jntfalloff_neg)
        ctrlfalloff_pos = f"{sdk_name}_ctrlfalloff_pos_plusMinusAverage"
        ctrlfalloff_neg = f"{sdk_name}_ctrlfalloff_neg_plusMinusAverage"
        remove(ctrlfalloff_pos)
        remove(ctrlfalloff_neg)
        rotmult = f"{sdk_name}_rotation_multiplyDivide"
        remove(rotmult)
        mult2 = f"{sdk_name}_mult2_multiplyDivide"
        remove(mult2)
        finalcond = f"{sdk_name}_final{COND}"
        remove(finalcond)

        # rotmult_pos = (ctrl - jnt) / falloff
        ctrl_minus_jnt = f"{sdk_name}_ctrl_minus_jnt_plusMinusAverage"
        numerator_pos = f"{sdk_name}_numerator_pos_plusMinusAverage"
        # (+) ctrl_minus_jnt
        # (ctrl - jnt)
        cmds.createNode('plusMinusAverage', n=ctrl_minus_jnt, s=1, ss=1)
        cmds.setAttr(f"{ctrl_minus_jnt}.operation", 2) # subtract
        cmds.connectAttr(f"{ctrlpos}.output", f"{ctrl_minus_jnt}.input1D[0]", f=1)
        cmds.connectAttr(f"{jnt}.joint_pos", f"{ctrl_minus_jnt}.input1D[1]", f=1)
        # (+) numerator_pos
        # (ctrl - jnt + falloff)
        cmds.createNode('plusMinusAverage', n=numerator_pos, s=1, ss=1)
        cmds.setAttr(f"{numerator_pos}.operation", 1) # add
        cmds.connectAttr(f"{ctrl_minus_jnt}.output1D", f"{numerator_pos}.input1D[0]", f=1)
        cmds.connectAttr(f"{falloff}.output", f"{numerator_pos}.input1D[1]", f=1)
        # Output f"{numerator_pos}.output1D"

        # rotmult_neg = (jnt - ctrl) / falloff
        jnt_minus_ctrl = f"{sdk_name}_jnt_minus_ctrl_plusMinusAverage"
        numerator_neg = f"{sdk_name}_numerator_neg_plusMinusAverage"
        # (-) jnt_minus_ctrl
        # (jnt - ctrl)
        cmds.createNode('plusMinusAverage', n=jnt_minus_ctrl, s=1, ss=1)
        cmds.setAttr(f"{jnt_minus_ctrl}.operation", 2) # subtract
        cmds.connectAttr(f"{jnt}.joint_pos", f"{jnt_minus_ctrl}.input1D[0]", f=1)
        cmds.connectAttr(f"{ctrlpos}.output", f"{jnt_minus_ctrl}.input1D[1]", f=1)
        # (-) numerator_neg
        # (jnt - ctrl + falloff)
        cmds.createNode('plusMinusAverage', n=numerator_neg, s=1, ss=1)
        cmds.setAttr(f"{numerator_neg}.operation", 1) # add
        cmds.connectAttr(f"{jnt_minus_ctrl}.output1D", f"{numerator_neg}.input1D[0]", f=1)
        cmds.connectAttr(f"{falloff}.output", f"{numerator_neg}.input1D[1]", f=1)
        # Output f"{numerator_neg}.output1D"

        # rotmult_pos = (ctrl - jnt + falloff) / falloff
        rotmult_pos= f"{sdk_name}_rotmult_pos_multiplyDivide"
        cmds.createNode('multiplyDivide', n=rotmult_pos, s=1, ss=1)
        cmds.setAttr(f"{rotmult_pos}.operation", 2) # divide
        cmds.connectAttr(f"{numerator_pos}.output1D", f"{rotmult_pos}.input1X", f=1)
        cmds.connectAttr(f"{falloff}.output", f"{rotmult_pos}.input2X", f=1)
        # Output f"{rotmult_pos}.outputX"

        # rotmult_neg = (jnt - ctrl + falloff) / falloff
        rotmult_neg= f"{sdk_name}_rotmult_neg_multiplyDivide"
        cmds.createNode('multiplyDivide', n=rotmult_neg, s=1, ss=1)
        cmds.setAttr(f"{rotmult_neg}.operation",2) # divide
        cmds.connectAttr(f"{numerator_neg}.output1D", f"{rotmult_neg}.input1X", f=1)
        cmds.connectAttr(f"{falloff}.output", f"{rotmult_neg}.input2X", f=1)
        # Output f"{rotmult_neg}.outputX"

        # (condition) falloff_cond - Check if jnt falls inside falloff range
        # valid range: falloff_pos >= jntpos >= falloff_neg
        falloff_pos_cond = f"{sdk_name}_falloff_pos{COND}"
        falloff_neg_cond = f"{sdk_name}_falloff_neg{COND}"
        cmds.createNode('condition', n=falloff_pos_cond, s=1, ss=1)
        cmds.createNode('condition', n=falloff_neg_cond, s=1, ss=1)
        # (+) if (numerator_pos) >= 0:
        # (ctrl - jnt + falloff) >= 0
        # jnt <= ctrl + falloff
        # jnt <= falloff_pos
        cmds.setAttr(f"{falloff_pos_cond}.operation", 3) # greater or equal
        cmds.connectAttr(f"{numerator_pos}.output1D", f"{falloff_pos_cond}.firstTerm", f=1)
        cmds.setAttr(f"{falloff_pos_cond}.secondTerm", 0)
        cmds.setAttr(f"{falloff_pos_cond}.colorIfFalseR", 0)
        cmds.setAttr(f"{falloff_pos_cond}.colorIfTrueR", 1)
        # (-) if (numerator_neg) >= 0:
        # (jnt - ctrl + falloff) >= 0
        # jnt >= ctrl - falloff
        # jnt >= falloff_neg
        cmds.setAttr(f"{falloff_neg_cond}.operation", 3) # greater or equal
        cmds.connectAttr(f"{numerator_neg}.output1D", f"{falloff_neg_cond}.firstTerm", f=1)
        cmds.setAttr(f"{falloff_neg_cond}.secondTerm", 0)
        cmds.setAttr(f"{falloff_neg_cond}.colorIfFalseR", 0)
        cmds.setAttr(f"{falloff_neg_cond}.colorIfTrueR", 1)
        # Output f"{falloff_pos_cond}.outColorR" = (+) 0/1
        # Output f"{falloff_neg_cond}.outColorR" = (-) 0/1

        # (condition) cond - Condition for rotation multiplier
        cond = f"{sdk_name}_rotmult{COND}"
        cmds.createNode('condition', n=cond, s=1, ss=1)
        # Compare ctrlpos and jntpos
        # (+) falloff_pos > jntpos > ctrlpos
        # if ctrl is after joint (less than), use rotmult_pos
        # (-) ctrlpos > jntpos > falloff_neg
        # if ctrl is before joint (greater than), use rotmult_neg
        cmds.setAttr(f"{cond}.operation", 2) # greater than
        cmds.connectAttr(f"{ctrlpos}.output", f"{cond}.firstTerm", f=1) # ctrlpos
        cmds.connectAttr(f"{jnt}.joint_pos", f"{cond}.secondTerm", f=1) # jntpos
        # Pass rotmult value to outColorR
        # (+) jntpos >= ctrlpos
        cmds.connectAttr(f"{rotmult_pos}.outputX", f"{cond}.colorIfFalseR", f=1)
        # (-) ctrlpos > jntpos
        cmds.connectAttr(f"{rotmult_neg}.outputX", f"{cond}.colorIfTrueR", f=1)
        # Pass falloff range condition to outColorG
        # (+) if jntpos > ctrlpos, check that falloff_pos >= jntpos
        # (-) if ctrlpos > jntpos, check that jntpos >= falloff_neg
        cmds.connectAttr(f"{falloff_pos_cond}.outColorR", f"{cond}.colorIfFalseG", f=1)
        cmds.connectAttr(f"{falloff_neg_cond}.outColorR", f"{cond}.colorIfTrueG", f=1)
        # Output f"{cond}.outColorR" = rotmult_pos,rotmult_neg
        # Output f"{cond}.outColorG" = (+/-) 0,1

        # rotmult = ctrl rotation * rotation multiplier
        rotmult = f"{sdk_name}_rotmult_multiplyDivide"
        cmds.createNode('multiplyDivide', n=rotmult, s=1, ss=1)
        cmds.setAttr(f"{rotmult}.operation", 1) # multiply
        cmds.connectAttr(f"{control}.rotate", f"{rotmult}.input1", f=1)
        # multiply rotmult to all axes X,Y,Z
        cmds.connectAttr(f"{cond}.outColorR", f"{rotmult}.input2X", f=1)
        cmds.connectAttr(f"{cond}.outColorR", f"{rotmult}.input2Y", f=1)
        cmds.connectAttr(f"{cond}.outColorR", f"{rotmult}.input2Z", f=1)
        # Output total rotation f"{rotmult}.output"

        # percentage = rotmult / (2 * joints_affected)
        percentage = f"{sdk_name}_percentage_multiplyDivide"
        cmds.createNode('multiplyDivide', n=percentage, s=1, ss=1)
        cmds.setAttr(f"{percentage}.operation", 2) # divide
        cmds.connectAttr(f"{rotmult}.output", f"{percentage}.input1", f=1)
        cmds.connectAttr(f"{control}.num_joints", f"{percentage}.input2X", f=1)
        cmds.connectAttr(f"{control}.num_joints", f"{percentage}.input2Y", f=1)
        cmds.connectAttr(f"{control}.num_joints", f"{percentage}.input2Z", f=1)
        # Output percentage rotation f"{percentage}.output"

        # (condition) threshold_cond - Condition for rotation threshold
        threshold_cond = f"{sdk_name}_threshold{COND}"
        cmds.createNode('condition', n=threshold_cond, s=1, ss=1)
        # if cond.outColorG == 0, set rotation to 0
        # if cond.outColorG == 1, set rotation to percentage.output
        cmds.connectAttr(f"{cond}.outColorG", f"{threshold_cond}.firstTerm", f=1)
        cmds.setAttr(f"{threshold_cond}.secondTerm", 0)
        cmds.setAttr(f"{threshold_cond}.operation", 1) # not equal
        cmds.setAttr(f"{threshold_cond}.colorIfFalse", 0,0,0)
        cmds.connectAttr(f"{percentage}.output", f"{threshold_cond}.colorIfTrue", f=1)
        # Output final rotation f"{threshold_cond}.outColor"

        # Pipe in final result to sdk rotation
        cmds.connectAttr(f"{threshold_cond}.outColor", f"{sdk}.rotate", f=1)


# SDK GROUPS (FK) ======================================================

def create_sdk_groups(rigname, joints, typ=TYPE_FK):
    '''
    Create n SDK groups above each joint.
    Return the top group that contains the FK joint chain,
    including all the SDK groups and joints.
    '''
    logger.info('Creating SDK groups above FK joints')
    basejnt = joints[0]
    basectrl = fstr(rigname, BASECTRL)
    fkjnt_grp = fstr(rigname, GROUP, typ)
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
        jnt_name = fstr(rigname, JNT, typ, NN)
        prev_sdk_grp = None # Previous sdk group
        first_sdk_grp = None # First sdk group
        last_sdk_grp = None # Last sdk group

        for idx in range(NUM_CTRL_FK+1): # Create sdk groups
            if idx < NUM_CTRL_FK:
                sdk_grp = fstr(rigname, SDK_GRP, typ, NN, nn=idx+1)
            else:
                sdk_grp = fstr(rigname, SDK_CTRL, typ, NN)
            create_group(sdk_grp)

            if idx > 0:
                # Set attribute sdk_grp.joint_pos
                v = cmds.getAttr(f"{jnt}.joint_pos") # Copy value from jnt
                if cmds.attributeQuery('joint_pos', n=sdk_grp, ex=1):
                    cmds.addAttr(f"{sdk_grp}.joint_pos", e=1, at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                else:
                    cmds.addAttr(sdk_grp, ln='joint_pos', nn='Joint Pos', at='float',
                        min=0, max=1, k=False, h=False, dv=v)
                cmds.setAttr(f"{sdk_grp}.joint_pos", cb=1, l=1)

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
                logger.error(f"{child} has no parent SDK group.")
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
        tmp_grp = cmds.group(em=True, n=f"{jnt}_tmp")
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
