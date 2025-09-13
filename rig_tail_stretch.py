'''
# rig_tail_stretchy.py
author: Daisy Jane Lee @dayzl

Squash and Stretch for Rig Tail
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import rig_tail_constants as cst
from rig_tail_constants import *
from rig_tail_util import *

logger = logger_setup(__name__)


# SQUASH AND STRETCH ===================================================

def build_squash_stretch(rigname, curve, joints, typ):
    '''
    Squash and stretch setup.
    Creates independent stretch (length) and squash (thickness) controls.
    Volume preservation will enable squash during stretch.

    Arguments
        rigname (str): Name of rig component
        curve (str): Name of curve driving joint chain
        joints (str list): List of joint names
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return
        scale_crv (str): Name of scale curve info node for length, None if failed
    '''
    logger.info(f"Build squash and stretch on '{typ}{rigname}'")
    basectrl = fstr(rigname, BASECTRL)

    if typ == TYPE_FK:
        scale_crv, orig_crvinfo, scale_crvinfo = set_curveinfo_stretch(
                rigname, curve, typ)
    elif typ == TYPE_IK:
        scale_crv, orig_crvinfo, scale_crvinfo = set_curveinfo_stretch(
                rigname, curve, typ)
    else:
        logger.error('Invalid typ {typ}. Choose TYPE_FK or TYPE_IK.')
        return None

    if not scale_crvinfo:
        logger.error('Failed to create scale curveInfo')
        return None

    # Setup stretch (translateX)
    stretch_mult, stretch_blend, stretch_mult_nodes = setup_joint_stretch(
            rigname, joints, scale_crvinfo, typ)

    # Setup squash (scaleY,scaleZ)
    squash_pma, squash_vol, squash_blend, squash_mult_nodes = setup_joint_squash(
            rigname, joints, scale_crvinfo, stretch_mult, typ)

    # Create and connect attributes
    add_attribute_squash_stretch(basectrl, stretch_blend, squash_pma, squash_vol)

    # Connect control to world scale
    scale_grp, scale_mult_ik, scale_mult_fk = stretchy_world_scale_mod(
            rigname, basectrl, squash_pma, typ)

    return scale_crv

def setup_joint_stretch(rigname, joints, curvelen, typ):
    '''
    Set up stretch for spline joint chain.
    Apply proportional scaling to maintain joint spacing during stretch.

    Calculate stretch ratio and apply transform to translateX.
    Stretch ratio (stretch_mult) = current_len / initial_len
    Blend between original joint length and stretched length based on stretch attribute.

    Arguments
        rigname (str): Name of rig component
        joints (str list): List of joint names to apply stretch to
        curvelen (str): CurveInfo or remapValue node providing curve length
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return (tuple)
        stretch_mult (str): multiplyDivide node for stretch ratio calculation
        stretch_blend (str): blendTwoAttr node for stretch
        stretch_mult_nodes (str list): multiplyDivide nodes for individual joints (if needed)
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        crvlen = f"{curvelen}.arcLength"
        # Get initial length from custom attribute
        init_length = cmds.getAttr(f"{curvelen}.initial_length")
    elif cmds.nodeType(curvelen) == 'remapValue':
        crvlen = f"{curvelen}.outValue"
        init_length = cmds.getAttr(f"{curvelen}.outputMax")
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")

    # (multiplyDivide) stretch_mult - Stretch ratio
    # stretch_mult = current_len / initial_len
    stretch_mult = f"{typ}{rigname}_stretch_multiplyDivide"
    cmds.createNode('multiplyDivide', n=stretch_mult, s=1, ss=1)
    cmds.setAttr(f"{stretch_mult}.operation", 2) # divide
    cmds.connectAttr(crvlen, f"{stretch_mult}.input1X", f=1) # current_len
    cmds.setAttr(f"{stretch_mult}.input2X", init_length) # initial_len
    # Output f"{stretch_mult}.outputX

    # (blendTwoAttr) stretch_blend - Blend between no stretch and full stretch
    stretch_blend = f"{typ}{rigname}_stretch_blendTwoAttr"
    cmds.createNode('blendTwoAttr', n=stretch_blend, s=1, ss=1)
    cmds.setAttr(f"{stretch_blend}.input[0]", 1.0) # no stretch
    cmds.connectAttr(f"{stretch_mult}.outputX", f"{stretch_blend}.input[1]", f=1)
    break_connection(f"{stretch_blend}.attributesBlender")
    cmds.setAttr(f"{stretch_blend}.attributesBlender", 1) # Default On
    # Output f"{stretch_blend}.output"

    # Get individual joint lengths for scaling
    joint_lengths = list()
    for i in range(1, len(joints)):
        try:
            pos1 = cmds.xform(joints[i-1], q=1, ws=1, t=1)
            pos2 = cmds.xform(joints[i], q=1, ws=1, t=1)
            distance = sum((pos2[j] - pos1[j]) ** 2 for j in range(3)) ** 0.5
            joint_lengths.append(distance)
        except:
            logger.error(f'Could not calculate distance for joint {joints[i]}')
            joint_lengths.append(1.0)

    # Apply stretch to joints with proportional scaling
    stretch_mult_nodes = list()
    for i, jnt in enumerate(joints[1:], 1): # Skip first joint
        # Create individual joint stretch multiplier
        jnt_mult = f'{typ}{rigname}_stretch_{i:02d}_multiplyDivide'
        cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
        cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply

        # Set original joint length as base value
        orig_len = joint_lengths[i-1] if i-1 < len(joint_lengths) else 1.0
        cmds.setAttr(f'{jnt_mult}.input1X', orig_len)

        # Connect stretch ratio
        cmds.connectAttr(f'{stretch_blend}.output', f'{jnt_mult}.input2X', f=1)

        # Apply transform to translateX (SDK control for FK, joint for IK)
        if typ == TYPE_FK:
            ctrl_sdk = fstr(rigname, SDK_CTRL, typ, i-1)
            if cmds.objExists(ctrl_sdk):
                cmds.connectAttr(f'{jnt_mult}.outputX', f'{ctrl_sdk}.translateX', f=1)
            else:
                logger.warning(f"SDK '{ctrl_sdk}' not found")
        else: # TYPE_IK
            cmds.connectAttr(f'{jnt_mult}.outputX', f'{jnt}.translateX', f=1)

        stretch_mult_nodes.append(jnt_mult)

    return stretch_mult, stretch_blend, stretch_mult_nodes

def setup_joint_squash(rigname, joints, curvelen, stretch_mult, typ):
    '''
    Set up squash for spline ik chain.
    Creates squash deformation system for tail thickness control.
    Sets up volume-preserving squash (automatic thinning during stretch) and
    user-controlled squash (manual adjustment). Only affects scaleY/scaleZ.

    Create two separate squash calculations:
    1. Volume-preserving squash (based on stretch)
       Gets thinner as the joint chain stretches longer.
       scale_Y_Z = sqrt(1/stretch_mult)^0.5
    2. User-controlled squash (based on squash attribute)

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joint names to apply squash to
        curvelen (str): CurveInfo or remapValue node providing curve length data
        stretch_mult (str): Stretch multiplier node for volume preservation
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return (tuple)
        squash_pma (str): user squash control node, plusMinusAverage
        squash_vol (str): volume preservation node, blendTwoAttr
        squash_blend (str): final squash blend node, blendTwoAttr
        squash_mult_nodes (str list): List of scale multiplyDivide nodes
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        logger.debug(f"CURVELEN is curveInfo")
        crvlen = f"{curvelen}.arcLength"
    elif cmds.nodeType(curvelen) == 'remapValue':
        logger.debug(f"CURVELEN is remapValue")
        crvlen = f"{curvelen}.outValue"
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")

    # (multiplyDivide) squash_mult - Inverse sqrt for volume preservation
    # Automatically thin when stretching
    squash_mult = f'{typ}{rigname}_squash_volume_multiplyDivide'
    cmds.createNode('multiplyDivide', n=squash_mult, s=1, ss=1)
    cmds.setAttr(f'{squash_mult}.operation', 3)  # power
    cmds.connectAttr(f'{stretch_mult}.outputX', f'{squash_mult}.input1X', f=1)
    cmds.setAttr(f'{squash_mult}.input2X', -0.5) # sqrt(1/stretch_ratio)

    # (blendTwoAttr) squash_vol - Blend volume preservation on/off
    # Allows disabling automatic volume preservation
    squash_vol = f'{typ}{rigname}_squash_volume_blendTwoAttr'
    cmds.createNode('blendTwoAttr', n=squash_vol, s=1, ss=1)
    cmds.setAttr(f'{squash_vol}.input[0]', 1.0)  # no volume preservation
    cmds.connectAttr(f'{squash_mult}.outputX', f'{squash_vol}.input[1]', f=1)
    cmds.setAttr(f'{squash_vol}.attributesBlender', 1) # Default On

    # (plusMinusAverage) squash_pma - Add user squash control
    # Combines volume preservation with manual squash control
    squash_pma = f'{typ}{rigname}_squash_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=squash_pma, s=1, ss=1)
    cmds.setAttr(f'{squash_pma}.operation', 1)  # sum
    cmds.connectAttr(f'{squash_vol}.output', f'{squash_pma}.input1D[0]', f=1)
    # User squash attribute will connect to input1D[1]

    # (blendTwoAttr) squash_blend - Final squash blend
    # Control overall squash effect intensity
    squash_blend = f'{typ}{rigname}_squash_blendTwoAttr'
    cmds.createNode('blendTwoAttr', n=squash_blend, s=1, ss=1)
    cmds.setAttr(f'{squash_blend}.input[0]', 1.0)  # no squash
    cmds.connectAttr(f'{squash_pma}.output1D', f'{squash_blend}.input[1]', f=1)
    break_connection(f'{squash_blend}.attributesBlender')
    cmds.setAttr(f'{squash_blend}.attributesBlender', 1) # Default On

    # Apply squash to joint scales, Only scaleY scaleZ
    squash_mult_nodes = list()
    for i, jnt in enumerate(joints):
        try: # Get original scale values
            break_connection(f'{jnt}.scaleY')
            break_connection(f'{jnt}.scaleZ')
            current_scale = cmds.getAttr(f'{jnt}.scale')[0]
        except:
            current_scale = (1.0, 1.0, 1.0)

        # Create multiplier to maintain original scale while applying squash
        scale_mult = f'{typ}{rigname}_squash_{i:02d}_multiplyDivide'
        cmds.createNode('multiplyDivide', n=scale_mult, s=1, ss=1)
        cmds.setAttr(f'{scale_mult}.operation', 1)  # multiply
        # Store original Y,Z scale values
        cmds.setAttr(f'{scale_mult}.input2Y', current_scale[1])
        cmds.setAttr(f'{scale_mult}.input2Z', current_scale[2])
        # Connect squash to Y,Z
        cmds.connectAttr(f'{squash_blend}.output', f'{scale_mult}.input1Y', f=1)
        cmds.connectAttr(f'{squash_blend}.output', f'{scale_mult}.input1Z', f=1)

        # Apply to appropriate transform (SDK control for FK, joint for IK)
        if typ == TYPE_FK:
            ctrl_sdk = fstr(rigname, SDK_CTRL, typ, i)
            if cmds.objExists(ctrl_sdk): # Connect to SDK
                cmds.connectAttr(f'{scale_mult}.outputY', f'{ctrl_sdk}.scaleY', f=1)
                cmds.connectAttr(f'{scale_mult}.outputZ', f'{ctrl_sdk}.scaleZ', f=1)
            else: # Fallback to joint if SDK not found
                cmds.connectAttr(f'{scale_mult}.outputY', f'{jnt}.scaleY', f=1)
                cmds.connectAttr(f'{scale_mult}.outputZ', f'{jnt}.scaleZ', f=1)
        else: # TYPE_IK
            cmds.connectAttr(f'{scale_mult}.outputY', f'{jnt}.scaleY', f=1)
            cmds.connectAttr(f'{scale_mult}.outputZ', f'{jnt}.scaleZ', f=1)

        squash_mult_nodes.append(scale_mult)

    return squash_pma, squash_vol, squash_blend, squash_mult_nodes

def joint_squash_scale(rigname, joints, typ, squash_blend=None, force=True):
    '''
    Create multiplyDivide nodes to maintain joint scale on Y and Z.
    If scale is non-uniform, create extra multiplyDivide nodes for every joint.
    Skip if joint scale is uniform = 1.0

    Arguments
        rigname (str): name of rig part
        joints (str list): list of joints
        typ (str): TYPE (FK,IK,BN)
        squash_blend (str): name of blendTwoAttr node for squash
        force (bool): force=True always build maintain nodes for extra scale control

    Return
        squash_mult_nodes (str list): list of multiplyDivide nodes used for scaling,
                                      empty list if skipped.
    '''
    jnt_scales = list()
    for jnt in joints:
        try:
            break_connection(f"{jnt}.scaleY")
            break_connection(f"{jnt}.scaleZ")
            # Get original scale values
            current_scale = cmds.getAttr(f"{jnt}.scale")[0]
            jnt_scales.append(current_scale)
        except Exception as e:
            logger.warning(f"Could not get scale for {jnt}: {e}, using (1,1,1)")
            jnt_scales.append((1.0, 1.0, 1.0))

        if not force and all(scale==(1,1,1) for scale in jnt_scales):
            # No need to maintain, return empty list but still connect directly
            if squash_blend:
                for i, jnt in enumerate(joints):
                    if typ == TYPE_FK:
                        ctrl_sdk = fstr(rigname, SDK_CTRL, typ, i)
                        if cmds.objExists(ctrl_sdk):
                            cmds.connectAttr(f"{squash_blend}.output", f"{ctrl_sdk}.scaleY", f=1)
                            cmds.connectAttr(f"{squash_blend}.output", f"{ctrl_sdk}.scaleZ", f=1)
                        else:
                            cmds.connectAttr(f"{squash_blend}.output", f"{jnt}.scaleY", f=1)
                            cmds.connectAttr(f"{squash_blend}.output", f"{jnt}.scaleZ", f=1)
            return list()

    # Create multiplyDivide nodes to maintain scale
    squash_mult_nodes = list()
    for i, scale in enumerate(jnt_scales):
        scale_mult = fstr(rigname, SCALE_MULT, NN=i)
        cmds.createNode('multiplyDivide', n=scale_mult, s=1, ss=1)
        cmds.setAttr(f"{scale_mult}.operation", 1)  # multiply
        cmds.setAttr(f"{scale_mult}.input2Y", jnt_scales[i][1])
        cmds.setAttr(f"{scale_mult}.input2Z", jnt_scales[i][2])

        if squash_blend:
            cmds.connectAttr(f"{squash_blend}.output", f"{scale_mult}.input1Y", f=1)
            cmds.connectAttr(f"{squash_blend}.output", f"{scale_mult}.input1Z", f=1)

        if typ == TYPE_FK:
            ctrl_sdk = fstr(rigname, SDK_CTRL, typ, i)
            cmds.connectAttr(f"{scale_mult}.outputY", f"{ctrl_sdk}.scaleY", f=1)
            cmds.connectAttr(f"{scale_mult}.outputZ", f"{ctrl_sdk}.scaleZ", f=1)
        else:
            cmds.connectAttr(f"{scale_mult}.outputY", f"{joints[i]}.scaleY", f=1)
            cmds.connectAttr(f"{scale_mult}.outputZ", f"{joints[i]}.scaleZ", f=1)
        squash_mult_nodes.append(scale_mult)

    return squash_mult_nodes

def add_attribute_squash_stretch(control, stretch_blend, squash_pma, squash_vol):
    '''
    Add squash/stretch control attributes with proper value mapping.
    Creates stretch (0 to 1), squash (-10 to 10), and volume preservation (0 to 1) attributes.
    Maps user-friendly ranges to internal multiplier values for intuitive control.
    '''
    add_attribute_enum(control, STRETCH_DIVIDER[0], STRETCH_DIVIDER[1], STRETCH_DIVIDER[2])
    # Add stretch attribute (0 = no stretch, 10 = full stretch)
    if not cmds.attributeQuery('stretch', n=control, ex=1):
        cmds.addAttr(control, ln='stretch', at='float', k=1, dv=10, min=0, max=10)
    # Add squash attribute (-10 to 10, 0 = no change, negative = contract, positive = expand)
    if not cmds.attributeQuery('squash', n=control, ex=1):
        cmds.addAttr(control, ln='squash', at='float', k=1, dv=0, min=-10, max=10)
    # Add volume preservation attribute (0 = disabled, 1 = squash during stretch)
    if not cmds.attributeQuery('preserveVolume', n=control, ex=1):
            cmds.addAttr(control, ln='preserveVolume', at='float', k=1, dv=1, min=0, max=1)

    # Stretch range remapping
    # Range: 0 to 10 -> multiplier 0.0 to 1.0
    stretch_remap = f'{control}_stretch_remap_multiplyDivide'
    cmds.createNode('multiplyDivide', n=stretch_remap, s=1, ss=1)
    cmds.setAttr(f'{stretch_remap}.operation', 1) # multiply
    cmds.setAttr(f'{stretch_remap}.input2X', 0.1) # scale factor
    cmds.connectAttr(f'{control}.stretch', f'{stretch_remap}.input1X', f=1)
    # Output f'{stretch_remap}.outputX'

    # Connect stretch
    cmds.connectAttr(f'{stretch_remap}.outputX', f'{stretch_blend}.attributesBlender', f=1)

    # Squash range remapping (positive = expand, negative = contract)
    # Formula: multiplier = 1.0 + (value * 0.1)
    # Range: -10 to 10 -> multiplier 0.0 to 2.0
    squash_remap = f'{control}_squash_remap_multiplyDivide'
    cmds.createNode('multiplyDivide', n=squash_remap, s=1, ss=1)
    cmds.setAttr(f'{squash_remap}.operation', 1) # multiply
    cmds.setAttr(f'{squash_remap}.input2X', 0.1) # scale factor
    cmds.connectAttr(f'{control}.squash', f'{squash_remap}.input1X', f=1)
    # Output f'{squash_remap}.outputX'

    # Add 1.0 offset to center multiplier around 1.0
    squash_offset = f'{control}_squash_offset_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=squash_offset, s=1, ss=1)
    cmds.setAttr(f'{squash_offset}.operation', 1)  # sum
    cmds.setAttr(f'{squash_offset}.input1D[0]', 1.0) # base multiplier
    cmds.connectAttr(f'{squash_remap}.outputX', f'{squash_offset}.input1D[1]', f=1)
    # Output f'{squash_offset}.output1D'

    # Connect to user squash node (created in setup_joint_squash)
    cmds.connectAttr(f'{squash_offset}.output1D', f'{squash_pma}.input1D[1]', f=1)

    # Connect volume preservation toggle
    cmds.connectAttr(f'{control}.preserveVolume', f'{squash_vol}.attributesBlender', f=1)

def stretchy_world_scale_mod(rigname, control, squash_pma, typ):
    '''
    Apply world scale compensation to squash and stretch setup.
    Create scale tracking group and compensate for rig scaling to maintain proportions.
    Divide squash multiplier by world_scale^2

    Arguments
        rigname (str): Name of rig component
        control (str): Name of control object to constrain scale group to
        squash_pma (str): User squash plusMinusAverage node
        typ (str): Rig type identifier (TYPE_FK, TYPE_IK)

    Return (tuple)
        scale_grp (str): scale group
        scale_mult (str): world scale multiplyDivide node
        scale_constraint (str): scaleConstraint on scale group
    '''
    scale_grp = fstr(rigname, SCALE_GRP)
    set_transform_visibility(scale_grp, k=0, cb=1, l=0) # Show channel box

    # Create scale constraint
    constr_scale_grp = get_constraint(scale_grp, typ='scaleConstraint')
    if not constr_scale_grp: # Constrain scale group
        constr_scale_grp = cmds.scaleConstraint(control, scale_grp, mo=1)[0]

    # (multiplyDivide) scale_mult - World scale compensation
    scale_mult = f'{typ}{rigname}_world_scale_multiplyDivide'
    cmds.createNode('multiplyDivide', n=scale_mult, s=1, ss=1)
    cmds.setAttr(f'{scale_mult}.operation', 1)  # multiply
    # Square scale value for volume compensation: scale^2 affects cross-sectional area
    cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_mult}.input1X', f=1)
    cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_mult}.input2X', f=1)

    # (multiplyDivide) squash_div - Divide squash multiplier
    squash_div = f'{typ}{rigname}_squash_world_multiplyDivide'
    cmds.createNode('multiplyDivide', n=squash_div, s=1, ss=1)
    cmds.setAttr(f'{squash_div}.operation', 2)  # divide
    cmds.connectAttr(f'{squash_pma}.output1D', f'{squash_div}.input1X', f=1)
    cmds.connectAttr(f'{scale_mult}.outputX', f'{squash_div}.input2X', f=1)

    # Connect outputs
    connections = cmds.listConnections(f'{squash_pma}.output1D', s=0, p=1) or []
    for dst in connections:
        cmds.disconnectAttr(f'{squash_pma}.output1D', dst)
        cmds.connectAttr(f'{squash_div}.outputX', dst, f=1)

    return scale_grp, scale_mult, constr_scale_grp


# MEASURE SCALE CURVE ==================================================

def set_curveinfo_stretch(rigname, curve, typ=''):
    '''
    Set up squash and stretch curve measurement system.

    Create measurement for stretch/squash:
    - Create curveInfo for original curve length reference
    - Create a scale curve that represents the deformable portion
    - Length measurement nodes for stretch calculation

    Arguments
        curve (str): Name of curve
        rigname (str): Name of rig part
        typ (str): TYPE (FK,IK,BN)

    Return (tuple)
        scale_curve (str): Scale curve
        curveinfo (str): Original curveInfo node
        scale_curveinfo (str): Scaling curveInfo node
    '''
    if not cmds.objExists(curve): # Ensure curve exists
        logger.error(f"Curve '{curve}' does not exist")
    # Create original curveInfo for total length reference
    orig_curveinfo = create_curveinfo(rigname, curve, typ)

    # Get curve information
    num_cv, spans, degree = get_num_cv(curve)
    srt_cv = 0
    end_cv = num_cv-1

    # Create scale curve to measure the deformed length
    scale_crv = create_scale_curve(rigname, curve, srt_cv, end_cv, typ, num_cv, spans)
    if not scale_crv:
        logger.warning('Scale curve creation failed, using fallback')
        return None, orig_curveinfo, fallback_curve_length(rigname, typ)

    # Create curveInfo for the segment to track deformed curve length
    scale_crvinfo = f"{typ}{rigname}_scale_curveInfo"
    cmds.createNode('curveInfo', n=scale_crvinfo, s=1, ss=1)
    # Connect original curve to measure its deformed length
    curve_shape = cmds.listRelatives(curve, s=1, ni=1)[0]
    cmds.connectAttr(f"{curve_shape}.worldSpace[0]", f"{scale_crvinfo}.inputCurve", f=1)

    # Store initial length from scale curve for reference
    scale_shape = cmds.listRelatives(scale_crv, s=1, ni=1)[0]
    tmp_crvinfo = cmds.createNode('curveInfo')
    cmds.connectAttr(f"{scale_shape}.worldSpace[0]", f"{tmp_crvinfo}.inputCurve")

    # Validate initial length measurement
    cmds.dgeval(f'{tmp_crvinfo}.arcLength')
    init_length = cmds.getAttr(f"{tmp_crvinfo}.arcLength")
    cmds.delete(tmp_crvinfo)
    if init_length <= 0:
        logger.warning(f"Initial length of scale curve is invalid {init_length}")

    # Store initial length as custom attribute on curveInfo node
    cmds.addAttr(scale_crvinfo, ln='initial_length', nn='Init Length', at='float', dv=init_length)

    # Validate current length measurement
    cmds.dgeval(f'{scale_crvinfo}.arcLength')
    current_length = cmds.getAttr(f'{scale_crvinfo}.arcLength')
    if current_length <= 0:
        logger.warning(f"Current length of scale curve is invalid {current_length}")

    return scale_crv, orig_curveinfo, scale_crvinfo

def create_scale_curve(rigname, curve, srt_cv, end_cv, typ, num_cv, spans):
    '''
    Create a scale curve by sampling points from the original curve.
    This curve represents the section we want to measure for squash and stretch.
    Uses sampling with pointOnCurveInfo nodes.

    Arguments
        rigname (str): Rig component name
        curve (str): Original curve to sample from
        start_cv (int): Starting CV index
        end_cv (int): Ending CV index
        typ (str): Type identifier
        num_cv (int): Total number of CVs
        spans (int): Number of curve spans

    Return
        scale_crv (str): Name of created scale curve, or None if failed
    '''
    scale_crv = fstr(rigname, CURVE_SCALE, typ)
    # Delete existing scale curve segment if it exists
    if cmds.objExists(scale_crv):
        unbind_skincluster(scale_crv, typ='crv')
        remove(scale_crv)

    curve_shape = cmds.listRelatives(curve, s=1, ni=1)[0]

    # Calculate parameter range for sampling
    if srt_cv == 0:
        srt = 0.0
    else:
        srt = float(srt_cv) / num_cv * spans
    if end_cv == num_cv - 1:
        end = float(spans)
    else:
        end = float(end_cv) / num_cv * spans

    # Sample points along the curve
    num_samples = max(4, abs(end_cv - srt_cv) + 1)
    sample_positions = list()

    for i in range(num_samples):
        # Calculate parameter along curve segment
        if num_samples == 1:
            param = srt
        else: # Linear interpolation between srt and end
            t = float(i) / (num_samples - 1)
            param = srt + t * (end - srt)

        # Sample position using pointOnCurveInfo
        temp_poci = cmds.createNode('pointOnCurveInfo')
        cmds.connectAttr(f'{curve_shape}.worldSpace[0]', f'{temp_poci}.inputCurve')
        cmds.setAttr(f'{temp_poci}.parameter', param)

        # Evaluate and get position
        cmds.dgeval(f'{temp_poci}.position')
        pos = cmds.getAttr(f'{temp_poci}.position')[0]
        sample_positions.append(pos)
        cmds.delete(temp_poci)

    try:
        # Create curve from sampled positions
        scale_crv = cmds.curve(p=sample_positions, d=min(3, len(sample_positions)-1), n=scale_crv)
        rename_shapes(scale_crv, typ='crv')

        # Rebuild for consistency
        if len(sample_positions) >= 4:
            cmds.rebuildCurve(scale_crv,
                              ch=0, rpo=1, rt=0, end=1, kr=0,
                              kcp=0, kep=1, kt=0, d=3, tol=0.01)
        rename_shapes(scale_crv, typ='crv')
        set_curve_visibility(scale_crv)
        cmds.delete(scale_crv, ch=1)

        # Parent to spline group
        spline_grp = fstr(rigname, SPLINE_GRP, typ)
        if cmds.objExists(spline_grp):
            parent_to(scale_crv, spline_grp)

        return scale_crv

    except Exception as e:
        logger.error(f'Failed to create scale curve: {e}')
        return None

def fallback_curve_length(rigname, typ):
    '''
    Fallback method using joint positions when curve method fails.
    '''
    logger.info(f'Using fallback joint distance calculation for {typ}{rigname}')

    # Get joints for calculation
    if typ == TYPE_FK:
        joints = cst.JOINTS_FK[rigname]
    else:
        joints = cst.JOINTS_IK[rigname]

    # Create a simple remapValue node that outputs total joint chain length
    fallback_crvlen = f'{typ}{rigname}_fallback_length'
    cmds.createNode('remapValue', n=fallback_crvlen, s=1, ss=1)

    # Calculate total distance between joints
    total_distance = 0.0
    for i in range(1, len(joints)):
        pos1 = cmds.xform(joints[i-1], q=1, ws=1, t=1)
        pos2 = cmds.xform(joints[i], q=1, ws=1, t=1)
        distance = sum((pos2[j] - pos1[j]) ** 2 for j in range(3)) ** 0.5
        total_distance += distance

    # Set up the remap node to output this distance
    cmds.setAttr(f'{fallback_crvlen}.inputMin', 0)
    cmds.setAttr(f'{fallback_crvlen}.inputMax', 1)
    cmds.setAttr(f'{fallback_crvlen}.outputMin', 0)
    cmds.setAttr(f'{fallback_crvlen}.outputMax', total_distance)
    cmds.setAttr(f'{fallback_crvlen}.inputValue', 1)  # Always output max

    return fallback_crvlen


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
    cmds.setAttr(f"{ikhandle}.dTwistControlEnable", 1) # Enable advanced twist
    cmds.setAttr(f"{ikhandle}.dWorldUpType", 4) # Rot up start/end
    cmds.setAttr(f"{ikhandle}.dWorldUpAxis", 3) # Up Axis to pos z
    # Start / end obj
    cmds.connectAttr(f"{start_obj}.xformMatrix", f"{ikhandle}.dWorldUpMatrix", f=1)
    cmds.connectAttr(f"{end_obj}.xformMatrix", f"{ikhandle}.dWorldUpMatrixEnd", f=1)
    # Start / end vec
    cmds.setAttr(f"{ikhandle}.dWorldUpVectorX", start_vec[0])
    cmds.setAttr(f"{ikhandle}.dWorldUpVectorY", start_vec[1])
    cmds.setAttr(f"{ikhandle}.dWorldUpVectorZ", start_vec[2])
    cmds.setAttr(f"{ikhandle}.dWorldUpVectorEndX", end_vec[0])
    cmds.setAttr(f"{ikhandle}.dWorldUpVectorEndY", end_vec[1])
    cmds.setAttr(f"{ikhandle}.dWorldUpVectorEndZ", end_vec[2])
