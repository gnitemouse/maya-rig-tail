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
    '''
    logger.info(f"Build squash and stretch on '{typ}{rigname}'")
    basectrl = fstr(rigname, BASECTRL)

    curvelen = set_curveinfo_stretch(rigname, curve, typ)
    if not curvelen:
        logger.error('Failed to create scale curveInfo')

    # Create and connect attributes
    stretch_attr, squash_attr = add_attribute_squash_stretch(
            rigname, basectrl, curvelen, typ)

    # Setup stretch (scaleX)
    stretch_ratio, stretch_mult_nodes = setup_joint_stretch(
            rigname, joints, curvelen, stretch_attr, typ)

    # Setup squash (scaleY,scaleZ)
    squash_pma, squash_mult_nodes = setup_joint_squash(
            rigname, basectrl, joints, curvelen, squash_attr, stretch_ratio, typ)

    # Connect control to world scale
    scale_grp, scale_mult, scale_mult_fk = stretchy_world_scale_mod(
            rigname, basectrl, squash_pma, squash_mult_nodes, typ)

def setup_joint_stretch(rigname, joints, curvelen, stretch_attr, typ):
    '''
    Set up stretch for joints.
    Create stretch calculation and application network.
    Apply proportional scaling to maintain joint spacing during stretch.

    Blend between original joint length and stretched length based on stretch attribute.
    Calculate stretch ratio and apply transform to translateX.
    For IK, stretch_mult = stretch_attr / current_len
            stretch_ratio = stretch_mult * 2 (0-2 range)
    For FK, stretch_ratio = stretch_attr.attributesBlender * 2 (0-2 range)

    Arguments
        rigname (str): Name of rig component
        joints (str list): List of joint names to apply stretch to
        curvelen (str): CurveInfo or remapValue node providing curve length
        stretch_attr (str): Stretch length according to stretch attribute
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return (tuple)
        stretch_ratio (str): multiplyDivide node for stretch ratio calculation
        stretch_mult_nodes (str list): multiplyDivide nodes for individual joints (if needed)
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        crvlen = f"{curvelen}.arcLength"
        # Get initial length from custom attribute
        initial_len = cmds.getAttr(f"{curvelen}.initial_length")
    elif cmds.nodeType(curvelen) == 'remapValue':
        crvlen = f"{curvelen}.outValue"
        initial_len = cmds.getAttr(f"{curvelen}.outputMax")
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")
    logger.debug(f"curvelen '{curvelen}' initial_len {initial_len:.3f}")

    if typ == TYPE_IK: # IK: Reactive stretch based on current curve length
        # (multiplyDivide) stretch_mult = stretch_attr / current_len
        stretch_mult = f"{typ}{rigname}_stretch_mult_multiplyDivide"
        cmds.createNode('multiplyDivide', n=stretch_mult, s=1, ss=1)
        cmds.setAttr(f"{stretch_mult}.operation", 2) # divide
        cmds.connectAttr(f"{stretch_attr}.output", f"{stretch_mult}.input1X", f=1)
        cmds.connectAttr(crvlen, f"{stretch_mult}.input2X", f=1) # current_len

        # (multiplyDivide) stretch_ratio - Multiply by 2 to get 0-2 range
        stretch_ratio = f"{typ}{rigname}_stretch_ratio_multiplyDivide"
        cmds.createNode('multiplyDivide', n=stretch_ratio, s=1, ss=1)
        cmds.setAttr(f"{stretch_ratio}.operation", 1) # multiply
        cmds.setAttr(f"{stretch_ratio}.input2X", 2)
        cmds.connectAttr(f"{stretch_mult}.outputX", f"{stretch_ratio}.input1X", f=1)

    elif typ == TYPE_FK: # FK: Direct multiplier from stretch attribute
        # (multiplyDivide) stretch_ratio = stretch_attr.attributesBlender * 2
        stretch_ratio = f"{typ}{rigname}_stretch_ratio_multiplyDivide"
        cmds.createNode('multiplyDivide', n=stretch_ratio, s=1, ss=1)
        cmds.setAttr(f"{stretch_ratio}.operation", 1) # multiply
        cmds.connectAttr(f"{stretch_attr}.attributesBlender", f"{stretch_ratio}.input1X", f=1)
        cmds.setAttr(f"{stretch_ratio}.input2X", 2)

    else:
        logger.error('Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')

    # Apply stretch to joints
    stretch_mult_nodes = list()
    if typ == TYPE_FK:
        for i, jnt in enumerate(joints[1:], 1): # Skip first joint
            first_sdk = fstr(rigname, SDK_GRP, typ, i, 1)
            # Create individual joint stretch multiplier
            jnt_mult = f'{typ}{rigname}_stretch_{i:02d}_multiplyDivide'
            cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
            cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            cmds.setAttr(f'{jnt_mult}.input1X', cmds.getAttr(f'{first_sdk}.translateX'))
            cmds.connectAttr(f'{stretch_ratio}.outputX', f'{jnt_mult}.input2X', f=1)
            cmds.setAttr(f'{first_sdk}.translateX', 0)

            # Connect scaling to ctrl_sdk
            ctrl_sdk = fstr(rigname, SDK_CTRL, typ, i)
            if cmds.objExists(ctrl_sdk):
                cmds.connectAttr(f'{jnt_mult}.outputX', f'{ctrl_sdk}.translateX', f=1)
            else: # Fallback to joint if ctrl_sdk not found
                cmds.connectAttr(f'{jnt_mult}.outputX', f'{jnt}.translateX', f=1)
                logger.error(f"SDK control '{ctrl_sdk}' not found for FK stretch")
            stretch_mult_nodes.append(jnt_mult)

    elif typ == TYPE_IK:
        for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
            jnt_mult = f'{typ}{rigname}_stretch_{i:02d}_multiplyDivide'
            cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
            cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            cmds.setAttr(f'{jnt_mult}.input1X', 1)
            cmds.connectAttr(f'{stretch_ratio}.outputX', f'{jnt_mult}.input2X', f=1)
            # Connect scaling to jnt
            cmds.connectAttr(f'{jnt_mult}.outputX', f'{jnt}.scaleX', f=1)
            stretch_mult_nodes.append(jnt_mult)

    return stretch_ratio, stretch_mult_nodes

def setup_joint_squash(rigname, control, joints, curvelen, squash_attr, stretch_ratio, typ):
    '''
    Set up squash for joints.
    Create squash (thickness) control with volume preservation.
    Includes volume-preserving squash (automatic thinning during stretch) and
    user-controlled squash (manual adjustment). Only affects scaleY/scaleZ.

    Create two separate squash calculations:
    1. Volume-preserving squash (based on stretch)
       Gets thinner as the joint chain stretches longer.
       scale_Y_Z = sqrt(1/stretch_ratio)^0.5
    2. User-controlled squash (based on squash attribute)

    Arguments
        rigname (str): Name of rig component
        control (str): Name of basectrl
        joints (list): List of joint names to apply squash to
        curvelen (str): CurveInfo or remapValue node providing curve length data
        squash_attr (str): Remapped squash attribute
        stretch_ratio (str): Stretch multiplier node for volume preservation
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return (tuple)
        squash_pma (str): user squash control node, plusMinusAverage
        squash_mult_nodes (str list): List of scale multiplyDivide nodes
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        crvlen = f"{curvelen}.arcLength"
    elif cmds.nodeType(curvelen) == 'remapValue':
        crvlen = f"{curvelen}.outValue"
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")

    # (multiplyDivide) squash_mult - Inverse sqrt for volume preservation
    # Automatically thin when stretching
    squash_mult = f'{typ}{rigname}_squash_volume_multiplyDivide'
    cmds.createNode('multiplyDivide', n=squash_mult, s=1, ss=1)
    cmds.setAttr(f'{squash_mult}.operation', 3)  # power
    cmds.connectAttr(f'{stretch_ratio}.outputX', f'{squash_mult}.input1X', f=1)
    cmds.setAttr(f'{squash_mult}.input2X', -0.5) # sqrt(1/stretch_ratio)

    # (blendTwoAttr) squash_vol - Blend volume preservation on/off
    # Allows disabling automatic volume preservation
    squash_vol = f'{typ}{rigname}_squash_volume_blendTwoAttr'
    cmds.createNode('blendTwoAttr', n=squash_vol, s=1, ss=1)
    cmds.setAttr(f'{squash_vol}.input[0]', 1.0)  # no volume preservation
    cmds.connectAttr(f'{squash_mult}.outputX', f'{squash_vol}.input[1]', f=1)
    cmds.setAttr(f'{squash_vol}.attributesBlender', 1) # Default On
    # Connect volume preservation toggle
    cmds.connectAttr(f'{control}.preserveVolume', f'{squash_vol}.attributesBlender', f=1)

    # (plusMinusAverage) squash_pma - Add user squash control
    # Combines volume preservation with manual squash control
    squash_pma = f'{typ}{rigname}_squash_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=squash_pma, s=1, ss=1)
    cmds.setAttr(f'{squash_pma}.operation', 1)  # sum
    cmds.connectAttr(f'{squash_vol}.output', f'{squash_pma}.input1D[0]', f=1)
    # Connect user squash attribute
    cmds.connectAttr(f'{squash_attr}.outputX', f'{squash_pma}.input1D[1]', f=1)

    # Apply squash to joint scale, Only scaleY scaleZ
    squash_mult_nodes = list()
    if typ == TYPE_FK:
        for i, jnt in enumerate(joints):
            jnt_mult = f'{typ}{rigname}_squash_{i:02d}_multiplyDivide'
            cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
            cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            cmds.setAttr(f'{jnt_mult}.input1Y', 1)
            cmds.setAttr(f'{jnt_mult}.input1Z', 1)
            # cmds.connectAttr(f'{squash_blend}.output', f'{jnt_mult}.input1Y', f=1)
            # cmds.connectAttr(f'{squash_blend}.output', f'{jnt_mult}.input1Z', f=1)
            # Input1Y/Z will be connected from squash_world in stretchy_world_scale_mod
            cmds.setAttr(f'{jnt_mult}.input2Y', 1)
            cmds.setAttr(f'{jnt_mult}.input2Z', 1)

            if i > 0:
                jnt_div = f'{typ}{rigname}_squash_div_{i:02d}_multiplyDivide'
                cmds.createNode('multiplyDivide', n=jnt_div, s=1, ss=1)
                cmds.setAttr(f'{jnt_div}.operation', 2)  # divide
                cmds.connectAttr(f'{jnt_mult}.outputY', f'{jnt_div}.input1Y', f=1)
                cmds.connectAttr(f'{jnt_mult}.outputZ', f'{jnt_div}.input1Z', f=1)
                cmds.connectAttr(f'{prev_mult}.outputY', f'{jnt_div}.input2Y', f=1)
                cmds.connectAttr(f'{prev_mult}.outputZ', f'{jnt_div}.input2Z', f=1)
                squash_out = jnt_div
            else:
                squash_out = jnt_mult
            squash_mult_nodes.append(jnt_mult)

            # Connect scaling to ctrl_sdk
            ctrl_sdk = fstr(rigname, SDK_CTRL, typ, i)
            if cmds.objExists(ctrl_sdk): # Connect to SDK
                cmds.connectAttr(f'{squash_out}.outputY', f'{ctrl_sdk}.scaleY', f=1)
                cmds.connectAttr(f'{squash_out}.outputZ', f'{ctrl_sdk}.scaleZ', f=1)
            else: # Fallback to joint if SDK not found
                logger.warning(f"SDK '{ctrl_sdk}' not found for FK squash")
                cmds.connectAttr(f'{squash_out}.outputY', f'{jnt}.scaleY', f=1)
                cmds.connectAttr(f'{squash_out}.outputZ', f'{jnt}.scaleZ', f=1)
            prev_mult = jnt_mult

    elif typ == TYPE_IK:
        for i, jnt in enumerate(joints):
            jnt_mult = f'{typ}{rigname}_squash_{i:02d}_multiplyDivide'
            cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
            cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            cmds.setAttr(f'{jnt_mult}.input1Y', 1)
            cmds.setAttr(f'{jnt_mult}.input1Z', 1)
            # Input1Y/Z will be connected from squash_world in stretchy_world_scale_mod
            cmds.setAttr(f'{jnt_mult}.input2Y', 1)
            cmds.setAttr(f'{jnt_mult}.input2Z', 1)
            # cmds.connectAttr(f'{squash_blend}.output', f'{jnt_mult}.input1Y', f=1)
            # cmds.connectAttr(f'{squash_blend}.output', f'{jnt_mult}.input1Z', f=1)
            # Connect scaling to joint
            cmds.connectAttr(f'{jnt_mult}.outputY', f'{jnt}.scaleY', f=1)
            cmds.connectAttr(f'{jnt_mult}.outputZ', f'{jnt}.scaleZ', f=1)
            squash_mult_nodes.append(jnt_mult)

    return squash_pma, squash_mult_nodes

def add_attribute_squash_stretch(rigname, control, curvelen, typ):
    '''
    Create user-facing attributes and remap their values.
    Add attributes to basectrl:
        - stretch (-10 to 10, default 0)
        - squash (-10 to 10, default 0)
        - preserveVolume (0 to 1, default 1)
    Maps user-friendly ranges to 0-1 blend values for intuitive control.

    Return
    stretch_blend (str): Stretch blend node
    squash_remap (str): Squash remap node
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        crvlen = f"{curvelen}.arcLength"
        # Get initial length from custom attribute
        initial_len = cmds.getAttr(f"{curvelen}.initial_length")
    elif cmds.nodeType(curvelen) == 'remapValue':
        crvlen = f"{curvelen}.outValue"
        initial_len = cmds.getAttr(f"{curvelen}.outputMax")
    else:
        logger.error(f"Unrecognized curvelen '{curvelen}'")
    logger.debug(f"curvelen '{curvelen}' initial_len {initial_len:.3f}")

    add_attribute_enum(control, STRETCH_DIVIDER[0], STRETCH_DIVIDER[1], STRETCH_DIVIDER[2])
    # Add stretch attribute (0 = no stretch, 10 = full stretch)
    if not cmds.attributeQuery('stretch', n=control, ex=1):
        cmds.addAttr(control, ln='stretch', at='float', k=1, dv=0, min=-10, max=10)
    # Add squash attribute (-10 to 10, 0 = no change, negative = contract, positive = expand)
    if not cmds.attributeQuery('squash', n=control, ex=1):
        cmds.addAttr(control, ln='squash', at='float', k=1, dv=0, min=-10, max=10)
    # Add volume preservation attribute (0 = disabled, 1 = squash during stretch)
    if not cmds.attributeQuery('preserveVolume', n=control, ex=1):
            cmds.addAttr(control, ln='preserveVolume', at='float', k=1, dv=1, min=0, max=1)

    # (multiplyDivide) Stretch remapping -10 to 10 -> -0.5 to 0.5
    stretch_remap = f'{typ}{rigname}_stretch_remap_multiplyDivide'
    cmds.createNode('multiplyDivide', n=stretch_remap, s=1, ss=1)
    cmds.setAttr(f'{stretch_remap}.operation', 1) # multiply
    cmds.setAttr(f'{stretch_remap}.input2X', 0.05)
    cmds.connectAttr(f'{control}.stretch', f'{stretch_remap}.input1X', f=1)

    # (plusMinusAverage) Add 0.5 offset -> 0 to 1 range (0 stretch = 0.5 blend)
    stretch_offset = f'{typ}{rigname}_stretch_offset_plusMinusAverage'
    cmds.createNode('plusMinusAverage', n=stretch_offset, s=1, ss=1)
    cmds.setAttr(f'{stretch_offset}.operation', 1) # sum
    cmds.connectAttr(f'{stretch_remap}.outputX', f'{stretch_offset}.input1D[0]', f=1)
    cmds.setAttr(f'{stretch_offset}.input1D[1]', 0.5)

    # (blendTwoAttr) stretch_blend - Blend between no stretch and full stretch
    stretch_blend = f"{typ}{rigname}_stretch_blendTwoAttr"
    cmds.createNode('blendTwoAttr', n=stretch_blend, s=1, ss=1)
    cmds.setAttr(f"{stretch_blend}.input[0]", 0) # no stretch
    cmds.setAttr(f"{stretch_blend}.input[1]", initial_len) # full stretch
    cmds.connectAttr(f'{stretch_offset}.output1D', f'{stretch_blend}.attributesBlender', f=1)

    # (multiplyDivide) Squash remapping -10 to 10 -> -0.5 to 0.5
    squash_remap = f'{typ}{rigname}_squash_remap_multiplyDivide'
    cmds.createNode('multiplyDivide', n=squash_remap, s=1, ss=1)
    cmds.setAttr(f'{squash_remap}.operation', 1) # multiply
    cmds.setAttr(f'{squash_remap}.input2X', 0.05) # squash factor
    cmds.connectAttr(f'{control}.squash', f'{squash_remap}.input1X', f=1)

    return stretch_blend, squash_remap

def stretchy_world_scale_mod(rigname, control, squash_pma, squash_mult_nodes, typ):
    '''
    Compensate for rig scaling to maintain proportions.
    Create scale_grp constrained to basectrl
    Divide squash by world_scale^2 and connect to all joint multiply nodes.
    This prevents squash from being affected by overall rig scaling.

    Arguments
        rigname (str): Name of rig component
        control (str): Name of control object to constrain scale group to
        squash_pma (str): User squash plusMinusAverage node
        squash_mult_nodes (list): List of joint squash multiplyDivide nodes
        typ (str): Rig type identifier (TYPE_FK, TYPE_IK)

    Return (tuple)
        scale_grp (str): scale group
        scale_mult (str): world scale multiplyDivide node
        scale_constraint (str): scaleConstraint on scale group
    '''
    # Cleanup old scale group
    remove(f'{rigname}_measure_scale{GRP}')

    scale_grp = fstr(rigname, SCALE_GRP)
    # Set scale visibilty for scale group
    for axis in 'XYZ':
        if cmds.attributeQuery(f"scale{axis}", n=scale_grp, ex=1):
            cmds.setAttr(f"{scale_grp}.scale{axis}", k=0, cb=1, l=0) # Show CB

    # Constrain scale group to basectrl
    constr_scale_grp = get_constraint(scale_grp, typ='scaleConstraint')
    if not constr_scale_grp:
        constr_scale_grp = cmds.scaleConstraint(control, scale_grp, mo=1)[0]

    # Use scale group for world scale calculation (avoids feedback)
    scale_mult = f'{typ}{rigname}_world_scale_multiplyDivide'
    if not cmds.objExists(scale_mult):
        cmds.createNode('multiplyDivide', n=scale_mult, s=1, ss=1)
        cmds.setAttr(f'{scale_mult}.operation', 1)  # multiply
        # Square scale value for volume compensation
        cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_mult}.input1X', f=1)
        cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_mult}.input2X', f=1)

    # Divide squash by world scale squared
    squash_world = f'{typ}{rigname}_squash_world_multiplyDivide'
    if not cmds.objExists(squash_world):
        cmds.createNode('multiplyDivide', n=squash_world, s=1, ss=1)
        cmds.setAttr(f'{squash_world}.operation', 2)  # divide
        cmds.connectAttr(f'{squash_pma}.output1D', f'{squash_world}.input1X', f=1)
        cmds.connectAttr(f'{scale_mult}.outputX', f'{squash_world}.input2X', f=1)

    for jnt_mult in squash_mult_nodes:
        cmds.connectAttr(f'{squash_world}.outputX', f'{jnt_mult}.input1Y', f=1)
        cmds.connectAttr(f'{squash_world}.outputX', f'{jnt_mult}.input1Z', f=1)

    return scale_grp, scale_mult, constr_scale_grp


# MEASURE SCALE CURVE ==================================================

def set_curveinfo_stretch(rigname, curve, typ=''):
    '''
    Set up squash and stretch curve measurement system.
    Measure curve length for reactive stretching.

    - FK: Measure the skinned curve
    - IK: Measure the solver curve (used by ikHandle)

    Arguments
        curve (str): Name of curve
        rigname (str): Name of rig part
        typ (str): TYPE (FK,IK,BN)

    Return (tuple)
        scale_curveinfo (str): Scaling curveInfo node
    '''
    if not cmds.objExists(curve): # Ensure curve exists
        logger.error(f"Curve '{curve}' does not exist")

    # Create curveInfo on curve for stretchy measurement
    scale_crvinfo = f"{typ}{rigname}_scale_curveInfo"
    cmds.createNode('curveInfo', n=scale_crvinfo, s=1, ss=1)

    # Connect curve's worldSpace to curveInfo to measure its length
    curve_shape = cmds.listRelatives(curve, s=1, ni=1)[0]
    cmds.connectAttr(f"{curve_shape}.worldSpace[0]", f"{scale_crvinfo}.inputCurve", f=1)

    # Get initial_len and store as custom attribute
    cmds.dgeval(f'{scale_crvinfo}.arcLength')
    initial_len = cmds.getAttr(f'{scale_crvinfo}.arcLength')
    cmds.addAttr(scale_crvinfo, ln='initial_length', at='float', dv=initial_len)
    if initial_len <= 0:
        logger.warning(f"Initial length of curve '{curve}' is invalid {initial_len:.3f}")
    else:
        logger.info(f"Measuring curve '{curve}' with initial length {initial_len:.3f}")
    return scale_crvinfo

def fallback_curve_length(rigname, typ):
    '''
    Fallback method using joint positions when curve method fails.
    '''
    logger.info(f'Using fallback joint distance calculation for {typ}{rigname}')

    # Get joints for calculation
    if typ == TYPE_FK:
        joints = cst.JOINTS_FK[rigname]
    elif typ == TYPE_IK:
        joints = cst.JOINTS_IK[rigname]
    else:
        logger.error('Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')

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
