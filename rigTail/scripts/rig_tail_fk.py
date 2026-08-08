'''
# rig_tail_fk.py
author: Daisy Jane @gnitemouse

Rig Tail FK: Variable FK system with sliding controls

In FK mode, rotate varFK controls to control tail shape.
Use Position attribute to move varFK controls along curve (0 at the base,
10 at the tip, in tail-length units).
Use Falloff attribute to adjust the range of joints affected.

Two metrics meet here and must not be confused. `position` is what the
animator reads: a fraction of TAIL LENGTH, so 5 is halfway down the tail.
`joint_pos` is what the network compares against: a normalised Greville
abscissa, i.e. a fraction of the curve's PARAMETER range, which is what a
pointOnCurveInfo needs to land on a given joint. A remapValue per control
converts the first into the second (set_curveinfo_fk), and everything
downstream reads that output through control_position_plug.

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
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_math as rt_math
import rig_tail_ctrlall as rt_ctrlall

logger = logger_setup(__name__)


# ADD CURVEINFO (FK) ===================================================

def control_position_plug(control):
    '''
    Output plug carrying a control's position in joint_pos units.

    The `position` dial is in tail-length units and joint_pos is a Greville
    (parameter) fraction, so everything that compares the two has to read
    the remapValue's output, never the raw scaled dial. Falls back to the
    scaled dial when no remap exists (an FK build that never ran
    set_curveinfo_fk), which reproduces the old behaviour rather than
    erroring.

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
    Slide each variable-FK control along the curve from its position attr.

    Node network per control:
    1. curveInfo: Measures total curve length
    2. multDoubleLinear (ctrlpos): Scales position attribute (0-10) to range (0-1)
    3. remapValue (remap): tail-length fraction -> curve parameter fraction
    4. pointOnCurveInfo (poci): Gets world position on curve at parameter
    5. pointMatrixMult (pmm): Converts world position to local space
    6. Connection: pmm.output -> control_group.translate

    The remap is the fix for controls drawn away from the joints they drive.
    `poci.turnOnPercentage` takes a fraction of the curve's PARAMETER range,
    not of its length - those differ wherever the bones are uneven, and on
    the squid fintails (8:1 taper) feeding it a length fraction drew the
    first control at 60% of the tail while its rotation landed at 44%.
    turnOnPercentage was never wrong; what it was being fed was.

    So the ramp maps each joint's length fraction to that joint's Greville
    fraction, one point per joint, sampled off the curve itself (so degree
    and CV count cannot drift out of step with create_curve). Downstream,
    falloff_rotation reads the SAME remap output via control_position_plug,
    which is what keeps the drawn position and the rotated joints together.

    Two deliberate asymmetries:
    - `position` reads in tail length, `falloff` stays in joint_pos units. A
      width cannot go through a point-wise remap, and leaving it in joint
      units is what makes num_joints (a joint count) consistent.
    - the ramp is built from the REST curve. If the curve stretches under
      animation the mapping drifts slightly - the same class of static
      approximation as offset_unit_scale, and invisible next to what it
      replaces.

    The old `1 - ctrlpos` reversal is gone: position, joint_pos and the
    curve parameter now all run base to tip.

    Called after setting control attributes.

    Arguments
        rigname (str): Name of rig component
        curve (str): NURBS curve along joint chain
        controls (list): List of Variable FK control names
    '''
    logger.trace(f"{rigname}: Add control curveInfo")
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    curveinfo = rt_maya.create_curveinfo(rigname, curve, rt_constants.TYPE_FK)
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
    Remap control's rotation to joint rotations with falloff.

    Falloff Algorithm:
    - Each control affects joints within its falloff range
    - Rotation is distributed using a linear ramp within the falloff range
    - Multiple controls' rotations accumulate on each joint

    Control Attributes:
        position (0-10): Control position along curve (0=base, 10=tip),
            in TAIL-LENGTH units
        falloff (0.1-10): Range of influence (default 2), in joint_pos
            (Greville) units - a width cannot go through the position
            remap, and joint units are what make num_joints consistent
        num_joints (calculated): Number of joints affected by falloff

    The comparison against jnt.joint_pos reads control_position_plug(), i.e.
    the remapValue output rather than the raw scaled dial, so the joints a
    control rotates are the joints it is drawn on. See set_curveinfo_fk.

    Weight:
        weight = max(0, 1 - |joint_pos - ctrl_pos| / falloff)

        A symmetric linear tent: full strength where the joint sits under
        the control, falling to zero at either edge of the falloff and
        staying there.

        This was built as two mirrored halves - (ctrl - jnt + f)/f for
        joints one side, (jnt - ctrl + f)/f for the other, a condition to
        pick between them, two more to test each half's range and a fourth
        to zero the result outside it. Both halves expand to the same
        1 - |d|/f, and the conditions only clamp that at zero, so the whole
        arrangement collapses to one tent. Twelve nodes per joint per
        control became four.

        The algebra is exact; the wiring is not quite. remapValue samples
        its ramp in SINGLE precision where the old chain of divides stayed
        double, so the weight now carries ~1e-7 relative rounding. Measured
        against the old network in Maya 2024 across the falloff x position
        x joint_pos space, on the 36-joint squid fintail values: worst case
        2e-6 degrees on a control rotated 30, 6e-5 at 720 - it scales with
        the rotation rather than sitting at a fixed floor, and stays orders
        of magnitude under what the curve editor will even display. So this
        is a rewiring, not a retune: existing animation reads the same.

        At the exact falloff edge the old network could leak ~3e-7 of a
        degree through its gate condition. The tent returns a clean zero.

    Final Rotation:
        joint_rotation = control_rotation * weight

        The 1/num_joints normalisation rides on the remapValue's outputMax
        rather than a per-joint divide, so widening the falloff still
        spreads a fixed total rotation across more joints instead of adding
        more of it.

    Node network per joint:
    1. plusMinusAverage (delta): joint_pos - ctrl_pos, signed
    2. multiplyDivide (ratio): delta / falloff -> -1 .. +1 across the range
    3. remapValue (weight): tent over that range, scaled by 1/num_joints
    4. multiplyDivide (rotmult): rotsum * weight -> sdk_grp.rotate

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

    # 1 / num_joints, once per control instead of once per joint. The old
    # per-joint `percentage` divide put three connections into num_joints
    # for every joint (324 of them on a 36-joint tail with 3 controls), so
    # nudging falloff dirtied that many plugs directly. Now it is one.
    inv_numjnt = f'{control}_inv_num_joints_multiplyDivide'
    if not cmds.objExists(inv_numjnt):
        cmds.createNode('multiplyDivide', n=inv_numjnt, s=1, ss=1)
        cmds.setAttr(f'{inv_numjnt}.operation', 2) # divide
        cmds.setAttr(f'{inv_numjnt}.input1X', 1)
    # setRange holds num_joints at a minimum of 1, so this cannot divide by 0
    cmds.connectAttr(f'{ctrl}.num_joints', f'{inv_numjnt}.input2X', f=1)

    # For each joint, calculate weighted rotation
    for idx, jnt in enumerate(joints):
        sdk_grp = sdks[idx]
        NN = rt_naming.get_index_from_name(jnt)
        sdk_name = f'{control}_{NN:02d}'

        # (jnt - ctrl): signed distance along the chain, in joint_pos units.
        # Sign carries which side of the control the joint is on; the tent
        # below is symmetric, so nothing downstream has to branch on it.
        delta = f'{sdk_name}_delta_plusMinusAverage'
        cmds.createNode('plusMinusAverage', n=delta, s=1, ss=1)
        cmds.setAttr(f'{delta}.operation', 2) # subtract
        cmds.connectAttr(f'{jnt}.joint_pos', f'{delta}.input1D[0]', f=1)
        cmds.connectAttr(ctrlpos_plug, f'{delta}.input1D[1]', f=1)

        # (jnt - ctrl) / falloff: -1 at one edge of the range, 0 under the
        # control, +1 at the other. falloff bottoms out at 0.01 (attr min
        # 0.1, scaled by 0.1 above), so this never divides by zero either.
        ratio = f'{sdk_name}_ratio_multiplyDivide'
        cmds.createNode('multiplyDivide', n=ratio, s=1, ss=1)
        cmds.setAttr(f'{ratio}.operation', 2) # divide
        cmds.connectAttr(f'{delta}.output1D', f'{ratio}.input1X', f=1)
        cmds.connectAttr(f'{falloff}.output', f'{ratio}.input2X', f=1)

        # The tent. remapValue normalises inputValue from [inputMin,
        # inputMax] to [0,1] and clamps it there, then samples the ramp -
        # and a ramp clamps at its end points regardless, so a joint past
        # the falloff reads 0 by both mechanisms. Three linear points
        # running 0 -> 1 -> 0 give max(0, 1 - |jnt - ctrl|/falloff) with no
        # condition nodes at all - to single precision, the one thing the
        # ramp costs us over the old divides (see the docstring).
        #
        # outputMax carries the 1/num_joints normalisation: remapValue
        # returns outputMin + (outputMax - outputMin) * ramp, and outputMin
        # is 0, so the whole tent comes out pre-divided.
        weight = f'{sdk_name}_weight_remapValue'
        cmds.createNode('remapValue', n=weight, s=1, ss=1)
        cmds.setAttr(f'{weight}.inputMin', -1)
        cmds.setAttr(f'{weight}.inputMax', 1)
        for r_idx, (r_pos, r_val) in enumerate(((0, 0), (0.5, 1), (1, 0))):
            cmds.setAttr(f'{weight}.value[{r_idx}].value_Position', r_pos)
            cmds.setAttr(f'{weight}.value[{r_idx}].value_FloatValue', r_val)
            cmds.setAttr(f'{weight}.value[{r_idx}].value_Interp', 1) # linear
        cmds.connectAttr(f'{ratio}.outputX', f'{weight}.inputValue', f=1)
        cmds.connectAttr(f'{inv_numjnt}.outputX', f'{weight}.outputMax', f=1)

        # Apply the weight to the accumulated rotation, and that is the
        # joint's share. No threshold node: out of range the weight is
        # already 0, which zeroes the product on its own.
        rotmult = f'{sdk_name}_rotmult_multiplyDivide'
        cmds.createNode('multiplyDivide', n=rotmult, s=1, ss=1)
        cmds.setAttr(f'{rotmult}.operation', 1) # multiply
        cmds.connectAttr(f'{rotsum}.output3D', f'{rotmult}.input1', f=1)
        for axis in 'XYZ':
            cmds.connectAttr(f'{weight}.outValue', f'{rotmult}.input2{axis}', f=1)

        # Connect final rotation to SDK group
        cmds.connectAttr(f'{rotmult}.output', f'{sdk_grp}.rotate', f=1)


# SDK GROUPS (FK) ======================================================

def create_sdk_groups(rigname, joints, typ=rt_constants.TYPE_FK):
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

            if not cmds.objExists(sdk_grp):
                rt_maya.create_group(sdk_grp)

            if idx > 0:
                # Set joint_pos attribute on SDK group (copy from joint).
                # The channel-box and lock flags go through the API
                # (rt_maya.set_channel_flags): this runs NUM_CTRL_FK times
                # per joint per rig part, so it is the hottest loop in the
                # build, and a flag write there is a command spent on
                # display state.
                v = joint_pos
                if cmds.attributeQuery('joint_pos', n=sdk_grp, ex=1):
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
    Nest joint under SDK group hierarchy.
    Preserves joint transforms while reparenting.

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
        # Create temporary group to preserve joint transform. createNode
        # rather than cmds.group(em=True): identical result, a sixth of the
        # cost, and this runs once per FK joint per rig part
        tmp_grp = cmds.createNode('transform', n=f'{jnt}_tmp', ss=1)
        cmds.matchTransform(tmp_grp, jnt)
        rt_maya.parent_to(jnt, tmp_grp, a=1) # Unparent joint
        logger.trace(f"jnt:'{jnt}' jnt_parent:'{jnt_parent}' first_sdk_grp:'{first_sdk_grp}' last_sdk_grp:'{last_sdk_grp}'")

        # Move first_sdk_grp under joint's parent
        rt_maya.parent_to(first_sdk_grp, jnt_parent)
        cmds.matchTransform(first_sdk_grp, jnt_parent)
        rt_maya.reset_opm(first_sdk_grp)
        rt_maya.reset_transforms(first_sdk_grp)
        cmds.matchTransform(first_sdk_grp, jnt)
        # Bake the joint's rest transform into offsetParentMatrix, which
        # leaves local rotate at zero. falloff_rotation connects the
        # variable-FK output to this group's rotate, so a rest
        # orientation left there would be overwritten the moment that
        # connection is made - silently flattening any chain whose joints
        # are not already aligned with their parent.
        rt_maya.opm(first_sdk_grp)

        # Move joint under last_sdk_grp
        rt_maya.parent_to(jnt, last_sdk_grp, a=1)
        # Clean up any extra transform created by reparenting
        transf = cmds.listRelatives(jnt, p=True, typ='transform')[0]
        if transf != last_sdk_grp:
            cmds.ungroup(transf)
        cmds.delete(tmp_grp)
    else:
        # No parent - simple case
        rt_maya.match_transform(first_sdk_grp, jnt, moc=0)
        rt_maya.parent_to(jnt, last_sdk_grp, a=1)


# TWIST / ROLL (FK) ====================================================

def connect_twist_roll(rigname, joints):
    '''
    Give the FK chain its own twist/roll/offset network, so the same three
    basectrl attributes that drive the IK spline handle also work in FK
    mode. The BN chain follows a blendMatrix of the FK and IK drivers
    (rig_tail_matrix), so building both is what makes the dials switch
    with the IKFK mode - there is no separate switching network.

    Sources come from rt_ctrlall.resolved_plug, NOT the basectrl attribute
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

    # twist / N, shared by every joint (see docstring: the constant term
    # is what produces a linear ramp once it compounds down the hierarchy)
    twist_step = f'{rt_constants.TYPE_FK}_{rigname}_twist_step_multiplyDivide'
    if not cmds.objExists(twist_step):
        cmds.createNode('multiplyDivide', n=twist_step, s=1, ss=1)
        cmds.setAttr(f'{twist_step}.operation', 2)  # divide
    cmds.connectAttr(twist_src, f'{twist_step}.input1X', f=1)
    cmds.setAttr(f'{twist_step}.input2X', n)

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
            cmds.connectAttr(roll_src,
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
