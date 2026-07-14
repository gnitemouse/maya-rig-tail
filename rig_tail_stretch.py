'''
# rig_tail_stretch.py
author: Daisy Jane @gnitemouse

Squash and stretch for the tail. The module controls two things:

    Length    - joints spread apart or bunch up along the tail
    Thickness - BN joints fatten or thin through scaleY/scaleZ

How the values are computed:

    1. Measure the tail curve.
       A curveInfo node reads the curve's current arcLength. The rest
       length is stored once as an 'initial_length' attribute on the
       curveInfo, so rebuilding the rig while the tail is posed does not
       adopt the stretched length as the new rest length.

    2. Stretch ratio (length).
       IK: current length / rest length, so joints follow the curve
       when the IK controls pull it longer or shorter; the
       preserveVolume slider fades this reactive part in and out.
       FK: slider-driven only, the curve is not consulted. Either way
       the 'stretch' slider (-10..10 on the base control, remapped to
       +-0.5) is added on top, and the result is clamped to 0.1..2.0.
       The ratio multiplies each joint's rest translateX - IK joints
       directly, FK through the SDK offset group above each FK control.

    3. Volume preservation (thickness).
       Thickness = ratio ^ -0.5, like pulling taffy: stretching thins
       the tail, compressing fattens it. The 'preserveVolume' slider
       blends this on or off, and the 'squash' slider (-10..10,
       remapped to +-0.5) is added on top. The result is divided by the
       rig's global scale squared, so scaling the whole character does
       not fatten the tail, then multiplied by the per-joint
       'jntScaleYZ' sliders before driving each BN joint's scaleY/Z.

Build/connect split: build_stretch() runs early in the build and only
creates nodes - the sliders do not exist on the base control yet.
rig_tail_connect later creates the attributes and calls
connect_stretch_to_joints() to wire everything together. Until then
the remap nodes sit with their inputs unconnected; that is expected.
All nodes are looked up by name and reused, so re-runs refresh
connections instead of duplicating the network.

Related: BN children are positioned by offsetParentMatrix, so a
parent's squash scale would shear them. rig_tail_matrix appends a
squashInv term to each child's OPM to cancel it.

Functions:
    Build phase (called from rig_tail):
        build_stretch: Create the full stretch/squash node network
        set_curveinfo_stretch: curveInfo with stored rest length
        fallback_curve_length: Joint-distance fallback when curve fails
        remap_stretch_attr / create_stretch / create_squash /
        create_world_scale / create_joint_mult: network pieces
    Connect phase (called from rig_tail_connect):
        add_stretch_attributes_to_basectrl: stretch/squash/preserveVolume
        add_jntscale_attributes_to_basectrl: per-joint jntScaleYZ sliders
        connect_stretch_to_joints: Wire sliders and outputs to joints
    Spline IK:
        build_advanced_twist: Spline IK advanced twist setup
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya

logger = logger_setup(__name__)


# BUILD STRETCH NODES (Called from rig_tail) ===========================

def build_stretch(rigname, curve, joints, typ):
    '''
    Build stretch/squash node network WITHOUT creating attributes.
    Attributes are created later in rig_tail_connect.add_basectrl_attributes().

    This function only creates:
    - curveInfo nodes for measurement
    - Remap nodes (stretch_remap, squash_remap) waiting for connections
    - Stretch calculation nodes (ratio, pma, clamp)
    - Squash calculation nodes (volume, blend)
    - World scale nodes (scale_world, squash_world)
    - Joint multiply nodes (stretch_mult, squash_mult)

    Arguments
        rigname (str): Name of rig component
        curve (str): Name of curve driving joint chain
        joints (str list): List of joint names
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)
    '''
    logger.info(f"{typ}_{rigname}: Build Stretch")

    # Create curveInfo for measurement
    curvelen = set_curveinfo_stretch(rigname, curve, typ)
    if not curvelen:
        logger.error('Failed to create scale curveInfo')
        return

    # Create remap nodes (will be connected in connect phase)
    stretch_remap, squash_remap = remap_stretch_attr(rigname)

    # Create stretch calculation nodes
    stretch_ratio = create_stretch(rigname, joints, curvelen, stretch_remap, typ)

    # Create squash calculation nodes
    squash_blend, squash_pma = create_squash(rigname, curvelen, squash_remap, stretch_ratio, typ)

    # Create world scale nodes
    scale_world, squash_world = create_world_scale(rigname, squash_pma)

    # Create joint multiply nodes (prepare for connection)
    stretch_jnt_mult, squash_jnt_mult = create_joint_mult(rigname, joints, typ)


# CONNECT STRETCH (Called from rig_tail_connect) =======================

def connect_stretch_to_joints(rigname, basectrl, fk, ik):
    '''
    Connect stretch system to joints.
    Called from rig_tail_connect.connect_stretch() after attributes are created.

    This connects:
    - Basectrl preserveVolume -> squash blend
    - World scale nodes -> scale_grp
    - Squash nodes -> joint multiply nodes
    - Joint multiply nodes -> joint scaleY/Z

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Base control with squash/stretch attr
        fk (bool): Connect FK stretch
        ik (bool): Connect IK stretch
    '''
    logger.debug(f'{rigname}: Connect stretch to joints')

    # Connect preserveVolume attribute to squash blend
    squash_blend = f'{rigname}_squash_volume_blendTwoAttr'
    connect_preserve_volume(rigname, basectrl, squash_blend)

    # Connect world scale
    scale_world = f'{rigname}_scale_world_multiplyDivide'
    connect_world_scale(rigname, basectrl, scale_world)

    # Connect joint squash
    squash_world = f'{rigname}_squash_world_multiplyDivide'
    connect_joint_squash(rigname, basectrl, squash_world)

    # Apply stretch to joints
    if ik:
        stretch_ratio = f'{rt_cst.TYPE_IK}_{rigname}_stretch_ratio'
        connect_ik_stretch_to_joints(rigname, rt_cst.JOINTS_IK[rigname], stretch_ratio, rt_cst.TYPE_IK)
    if fk:
        stretch_ratio = f'{rt_cst.TYPE_FK}_{rigname}_stretch_ratio'
        connect_fk_stretch_to_joints(rigname, rt_cst.JOINTS_FK[rigname], stretch_ratio, rt_cst.TYPE_FK)

def add_stretch_attributes_to_basectrl(rigname, basectrl):
    if not rt_cst.EFFECTS['stretchy']:
        return
    rt_mya.add_attribute_enum(basectrl, rt_cst.STRETCH_DIVIDER[0], rt_cst.STRETCH_DIVIDER[1], rt_cst.STRETCH_DIVIDER[2])

    if not cmds.attributeQuery('stretch', n=basectrl, ex=1):
        cmds.addAttr(basectrl, ln='stretch', at='float', k=1, dv=0, min=-10, max=10)
    if not cmds.attributeQuery('squash', n=basectrl, ex=1):
        cmds.addAttr(basectrl, ln='squash', at='float', k=1, dv=0, min=-10, max=10)
    if not cmds.attributeQuery('preserveVolume', n=basectrl, ex=1):
        cmds.addAttr(basectrl, ln='preserveVolume', at='float', k=1, dv=1, min=0, max=1)

def add_jntscale_attributes_to_basectrl(rigname, basectrl):
    '''
    Add per-joint scale tweak attributes under the JNT SCALE divider.
    Kept separate from add_stretch_attributes_to_basectrl so the channel
    box orders: STRETCH, TWIST, ANIMATION, then the long JNT SCALE list.
    '''
    if not rt_cst.EFFECTS['stretchy']:
        return
    rt_mya.add_attribute_enum(basectrl, rt_cst.SCALE_DIVIDER[0], rt_cst.SCALE_DIVIDER[1], rt_cst.SCALE_DIVIDER[2])

    for i, bn_jnt in enumerate(rt_cst.JOINTS_BN[rigname]):
        if not cmds.attributeQuery(f'jntScaleYZ{i:02}', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln=f'jntScaleYZ{i:02}', at='float', k=1, dv=1, min=0.5, max=10)


# CREATE NODES =========================================================

def remap_stretch_attr(rigname):
    '''
    Create remap nodes for stretch and squash.
    These will be connected to basectrl attributes in connect phase.

    Arguments
        rigname (str): Name of rig component

    Return
        stretch_remap (str): Stretch remap node (-0.5 to 0.5)
        squash_remap (str): Squash remap node (-0.5 to 0.5)
    '''
    # Stretch remap: -10 to 10 -> -0.5 to 0.5
    stretch_remap = f'{rigname}_stretch_remap_multiplyDivide'
    if not cmds.objExists(stretch_remap):
        cmds.createNode('multiplyDivide', n=stretch_remap, s=1, ss=1)
        cmds.setAttr(f'{stretch_remap}.operation', 1)  # multiply
        cmds.setAttr(f'{stretch_remap}.input2X', 0.05)

    # Squash remap: -10 to 10 -> -0.5 to 0.5
    squash_remap = f'{rigname}_squash_remap_multiplyDivide'
    if not cmds.objExists(squash_remap):
        cmds.createNode('multiplyDivide', n=squash_remap, s=1, ss=1)
        cmds.setAttr(f'{squash_remap}.operation', 1)  # multiply
        cmds.setAttr(f'{squash_remap}.input2X', 0.05)

    logger.debug(f"Created remap nodes '{stretch_remap}' '{squash_remap}'")
    return stretch_remap, squash_remap

def create_stretch(rigname, joints, curvelen, stretch_remap, typ):
    '''
    Create stretch calculation node network.

    IK: Reactive stretch (curve length / initial length) + user stretch
    FK: User stretch only (0 to 2 range)

    Arguments
        rigname (str): Name of rig component
        joints (str list): list of joints
        curvelen (str): Curve length node
        typ (str): Rig type identifier (TYPE_IK, TYPE_FK)

    Return
        stretch_ratio (str): Stretch ratio multiplier
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        crvlen = f'{curvelen}.arcLength'
        initial_len = cmds.getAttr(f'{curvelen}.initial_length')
    elif cmds.nodeType(curvelen) == 'remapValue':
        crvlen = f'{curvelen}.outValue'
        initial_len = cmds.getAttr(f'{curvelen}.outputMax')
    else:
        logger.error(f'Unrecognized curvelen {curvelen}')
        return
    logger.debug(f'curvelen {curvelen} initial_len {initial_len:.3f}')

    if initial_len <= 0:
        # Degenerate rest length would make the reactive ratio divide
        # by zero; fall back to a neutral rest length
        logger.warning(f'{typ}_{rigname}: initial_len {initial_len} '
                       'invalid, using 1.0')
        initial_len = 1.0

    if typ == rt_cst.TYPE_IK:
        # IK: Reactive stretch based on current curve length
        # (nodes are reused on re-runs; only connections are refreshed)

        # (multiplyDivide) reactive stretch ratio (current_len / initial_len)
        stretch_reactive = f'{typ}_{rigname}_stretch_reactive_multiplyDivide'
        if not cmds.objExists(stretch_reactive):
            cmds.createNode('multiplyDivide', n=stretch_reactive, s=1, ss=1)
        cmds.setAttr(f'{stretch_reactive}.operation', 2)  # divide
        cmds.connectAttr(crvlen, f'{stretch_reactive}.input1X', f=1)
        cmds.setAttr(f'{stretch_reactive}.input2X', initial_len)

        # (blendTwoAttr) Blend reactive stretch on/off with preserveVolume
        stretch_preservevol = f'{typ}_{rigname}_stretch_preservevol_blendTwoAttr'
        if not cmds.objExists(stretch_preservevol):
            cmds.createNode('blendTwoAttr', n=stretch_preservevol, s=1, ss=1)
        cmds.setAttr(f'{stretch_preservevol}.input[0]', 1.0)  # No reactive
        cmds.connectAttr(f'{stretch_reactive}.outputX', f'{stretch_preservevol}.input[1]', f=1)
        # preserveVolume connection made later in connect phase

        # (plusMinusAverage) user stretch + reactive stretch
        stretch_pma = f'{typ}_{rigname}_stretch_user_plusMinusAverage'
        if not cmds.objExists(stretch_pma):
            cmds.createNode('plusMinusAverage', n=stretch_pma, s=1, ss=1)
        cmds.setAttr(f'{stretch_pma}.operation', 1)  # sum
        cmds.connectAttr(f'{stretch_preservevol}.output', f'{stretch_pma}.input1D[0]', f=1)
        cmds.connectAttr(f'{stretch_remap}.outputX', f'{stretch_pma}.input1D[1]', f=1)

        # (clamp) stretch_ratio - 0.1 to 2.0
        stretch_ratio = f'{typ}_{rigname}_stretch_ratio'
        if not cmds.objExists(stretch_ratio):
            cmds.createNode('clamp', n=stretch_ratio, s=1, ss=1)
        cmds.connectAttr(f'{stretch_pma}.output1D', f'{stretch_ratio}.inputR', f=1)
        cmds.setAttr(f'{stretch_ratio}.minR', 0.1)
        cmds.setAttr(f'{stretch_ratio}.maxR', 2.0)

    elif typ == rt_cst.TYPE_FK:
        # FK: Direct multiplier from stretch attribute

        # (multiplyDivide) double user stretch
        stretch_mult = f'{typ}_{rigname}_stretch_double_multiplyDivide'
        if not cmds.objExists(stretch_mult):
            cmds.createNode('multiplyDivide', n=stretch_mult, s=1, ss=1)
        cmds.setAttr(f'{stretch_mult}.operation', 1)  # multiply
        cmds.setAttr(f'{stretch_mult}.input1X', 2)
        cmds.connectAttr(f'{stretch_remap}.outputX', f'{stretch_mult}.input2X', f=1)

        # (plusMinusAverage) user stretch range - 0 to 2
        stretch_pma = f'{typ}_{rigname}_stretch_user_plusMinusAverage'
        if not cmds.objExists(stretch_pma):
            cmds.createNode('plusMinusAverage', n=stretch_pma, s=1, ss=1)
        cmds.setAttr(f'{stretch_pma}.operation', 1)  # sum
        cmds.setAttr(f'{stretch_pma}.input1D[0]', 1)  # Base ratio = 1
        cmds.connectAttr(f'{stretch_mult}.outputX', f'{stretch_pma}.input1D[1]', f=1)

        # (clamp) stretch_ratio
        stretch_ratio = f'{typ}_{rigname}_stretch_ratio'
        if not cmds.objExists(stretch_ratio):
            cmds.createNode('clamp', n=stretch_ratio, s=1, ss=1)
        cmds.connectAttr(f'{stretch_pma}.output1D', f'{stretch_ratio}.inputR', f=1)
        cmds.setAttr(f'{stretch_ratio}.minR', 0.1)
        cmds.setAttr(f'{stretch_ratio}.maxR', 2.0)

    else:
        logger.error(f'Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')

    return stretch_ratio

def create_squash(rigname, curvelen, squash_remap, stretch_ratio, typ):
    '''
    Create squash calculation node network.
    Volume-preserving squash based on stretch ratio.

    Arguments
        rigname (str): Name of rig component
        curvelen (str): Curve length node
        squash_remap (str): Squash remap node (-0.5 to 0.5)
        stretch_ratio (str): Stretch ratio multiplier
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return
        squash_blend (str): Volume preservation blend (reactive squash)
        squash_pma (str): Reactive squash + user squash
    '''
    if cmds.nodeType(curvelen) == 'curveInfo':
        crvlen = f'{curvelen}.arcLength'
    elif cmds.nodeType(curvelen) == 'remapValue':
        crvlen = f'{curvelen}.outValue'
    else:
        logger.error(f'Unrecognized curvelen {curvelen}')

    # (multiplyDivide) squash_vol - Inverse sqrt for volume preservation
    # Shared between FK and IK builds; the IK stretch ratio wins when both
    # are built (it reacts to curve length, FK's is user-driven only)
    squash_vol = f'{rigname}_squash_volume_multiplyDivide'
    if not cmds.objExists(squash_vol):
        cmds.createNode('multiplyDivide', n=squash_vol, s=1, ss=1)
        cmds.setAttr(f'{squash_vol}.operation', 3)  # power
        cmds.setAttr(f'{squash_vol}.input2X', -0.5)
    if typ == rt_cst.TYPE_IK or \
            not cmds.listConnections(f'{squash_vol}.input1X', s=1, d=0):
        cmds.connectAttr(f'{stretch_ratio}.outputR', f'{squash_vol}.input1X', f=1)

    # (blendTwoAttr) squash_blend - Blend volume preservation on/off
    squash_blend = f'{rigname}_squash_volume_blendTwoAttr'
    if not cmds.objExists(squash_blend):
        cmds.createNode('blendTwoAttr', n=squash_blend, s=1, ss=1)
        cmds.setAttr(f'{squash_blend}.input[0]', 1.0)
        cmds.connectAttr(f'{squash_vol}.outputX', f'{squash_blend}.input[1]', f=1)
        # preserveVolume connection made later in connect phase

    # (plusMinusAverage) squash_pma - Add user squash control
    squash_pma = f'{rigname}_squash_user_plusMinusAverage'
    if not cmds.objExists(squash_pma):
        cmds.createNode('plusMinusAverage', n=squash_pma, s=1, ss=1)
        cmds.setAttr(f'{squash_pma}.operation', 1)  # sum
        cmds.connectAttr(f'{squash_blend}.output', f'{squash_pma}.input1D[0]', f=1)
        cmds.connectAttr(f'{squash_remap}.outputX', f'{squash_pma}.input1D[1]', f=1)

    logger.debug(f"Created squash nodes '{squash_vol}' '{squash_blend}' '{squash_pma}'")
    return squash_blend, squash_pma

def create_world_scale(rigname, squash_pma):
    '''
    Create world scale compensation nodes.
    Prevents squash from being affected by overall rig scaling.

    Arguments
        rigname (str): Name of rig component
        squash_pma (str): Reactive squash + user squash

    Return
        scale_world (str): World scale volume based on scale_grp
        squash_world (str): World scale compensation node
    '''
    scale_grp = rt_nam.fstr(rigname, rt_cst.SCALE_GRP)

    # Set scale visibility for scale group
    for axis in 'XYZ':
        if cmds.attributeQuery(f'scale{axis}', n=scale_grp, ex=1):
            cmds.setAttr(f'{scale_grp}.scale{axis}', k=0, cb=1, l=0)

    # (multiplyDivide) scale_world - Square scale value for volume compensation
    scale_world = f'{rigname}_scale_world_multiplyDivide'
    if not cmds.objExists(scale_world):
        cmds.createNode('multiplyDivide', n=scale_world, s=1, ss=1)
        cmds.setAttr(f'{scale_world}.operation', 1)  # multiply
        # Neutral output until the connect phase wires scale_grp in:
        # squash_world divides by this, so it must not start at zero
        cmds.setAttr(f'{scale_world}.input1X', 1)
        # Connections made later in connect phase

    # (multiplyDivide) squash_world - Divide squash by scale world squared
    squash_world = f'{rigname}_squash_world_multiplyDivide'
    if not cmds.objExists(squash_world):
        cmds.createNode('multiplyDivide', n=squash_world, s=1, ss=1)
        cmds.setAttr(f'{squash_world}.operation', 2)  # divide
        cmds.connectAttr(f'{squash_pma}.output1D', f'{squash_world}.input1X', f=1)
        cmds.connectAttr(f'{scale_world}.outputX', f'{squash_world}.input2X', f=1)

    logger.debug(f"Created world scale nodes '{scale_world}' '{squash_world}'")
    return scale_world, squash_world

def create_joint_mult(rigname, joints, typ):
    '''
    Create multiply nodes for each joint.
    These will be connected in connect phase.

    For stretch: multiply joint translateX by stretch_ratio
    For squash: multiply base squash by per-joint scale attribute

    Arguments
        rigname (str): Name of rig component
        joints (str list): List of joint names to apply stretch to
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return
        stretch_jnt_mult (str list): Stretch joint multiplier nodes
        squash_jnt_mult (str list): Squash joint multiplier nodes
    '''
    logger.debug(f"{rigname}: Create joint multiply nodes")
    # Stretch multiply nodes
    stretch_jnt_mult = list()
    if typ == rt_cst.TYPE_IK:
        for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
            jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
            if not cmds.objExists(jnt_mult):
                cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
                cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            cmds.setAttr(f'{jnt_mult}.input1X', cmds.getAttr(f'{jnt}.translateX'))
            stretch_jnt_mult.append(jnt_mult)

    elif typ == rt_cst.TYPE_FK:
        for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
            jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
            first_sdk = rt_nam.fstr(rigname, rt_cst.SDK_GRP, typ, i, 1)
            if not cmds.objExists(jnt_mult):
                cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
                cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            if cmds.objExists(first_sdk):
                cmds.setAttr(f'{jnt_mult}.input1X', cmds.getAttr(f'{first_sdk}.translateX'))
            else:
                logger.warning(f'SDK group {first_sdk} does not exist')
            stretch_jnt_mult.append(jnt_mult)

    # Squash multiply nodes
    squash_jnt_mult = list()
    for i, jnt in enumerate(rt_cst.JOINTS_BN[rigname]):
        jnt_mult = f'{rigname}_squash_{i:02d}_multiplyDivide'
        if not cmds.objExists(jnt_mult):
            cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
            cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
        squash_jnt_mult.append(jnt_mult)

    return stretch_jnt_mult, squash_jnt_mult


# CONNECT NODES ========================================================

def connect_preserve_volume(rigname, basectrl, squash_blend):
    '''
    Connect preserveVolume attribute to blend nodes.

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Name of basectrl
        squash_blend (str): Volume preservation blend (reactive squash)
    '''
    logger.debug(f"{rigname}: Connect preserveVolume to blend")
    if not cmds.attributeQuery('preserveVolume', n=basectrl, ex=1):
        logger.warning(f'preserveVolume attribute not found on {basectrl}')
        return

    # Check if IK nodes exist
    stretch_preservevol = f'{rt_cst.TYPE_IK}_{rigname}_stretch_preservevol_blendTwoAttr'
    if cmds.objExists(stretch_preservevol):
        cmds.connectAttr(f'{basectrl}.preserveVolume',
                         f'{stretch_preservevol}.attributesBlender', f=1)

    # Connect to squash blend
    if cmds.objExists(squash_blend):
        cmds.connectAttr(f'{basectrl}.preserveVolume',
                         f'{squash_blend}.attributesBlender', f=1)

def connect_world_scale(rigname, basectrl, scale_world):
    '''
    Connect world scale compensation.
    Constrains scale_grp to basectrl and connects scale nodes.

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Name of basectrl
        scale_world (str): World scale volume based on scale_grp
    '''
    logger.debug(f"{rigname}: Connect world scale")
    scale_grp = rt_nam.fstr(rigname, rt_cst.SCALE_GRP)
    if not cmds.objExists(scale_grp):
        logger.error(f'Scale group not found: {scale_grp}')

    # Constrain scale group to basectrl
    scale_constr = rt_mya.get_constraint(scale_grp, typ='scaleConstraint')
    if not scale_constr:
        scale_constr = cmds.scaleConstraint(basectrl, scale_grp, mo=1)[0]

    # Connect scale_grp to scale_world node
    if cmds.objExists(scale_world):
        cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_world}.input1X', f=1)
        cmds.connectAttr(f'{scale_grp}.scaleX', f'{scale_world}.input2X', f=1)

def connect_joint_squash(rigname, basectrl, squash_world):
    '''
    Connect squash to BN joints.
    Routes squash through multiply nodes to avoid nested scaling issues.

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Name of basectrl
        squash_world (str): World scale compensation node
    '''
    logger.debug(f"{rigname}: Connect joint squash")
    if not cmds.objExists(squash_world):
        logger.error(f'Missing squash world node: {squash_world}')

    for i, jnt in enumerate(rt_cst.JOINTS_BN[rigname]):
        jnt_mult = f'{rigname}_squash_{i:02d}_multiplyDivide'
        if not cmds.objExists(jnt_mult):
            logger.warning(f'Joint multiply node not found: {jnt_mult}')
            continue

        # Check if jntScaleYZ attribute exists
        jnt_scale_attr = f'jntScaleYZ{i:02}'
        if not cmds.attributeQuery(jnt_scale_attr, n=basectrl, ex=1):
            logger.warning(f'Attribute {jnt_scale_attr} not found on {basectrl}')
            continue

        # Connect squash network to joint
        cmds.connectAttr(f'{squash_world}.outputX', f'{jnt_mult}.input1Y', f=1)
        cmds.connectAttr(f'{squash_world}.outputX', f'{jnt_mult}.input1Z', f=1)
        cmds.connectAttr(f'{basectrl}.{jnt_scale_attr}', f'{jnt_mult}.input2Y', f=1)
        cmds.connectAttr(f'{basectrl}.{jnt_scale_attr}', f'{jnt_mult}.input2Z', f=1)

        # Apply to joint
        cmds.connectAttr(f'{jnt_mult}.outputY', f'{jnt}.scaleY', f=1)
        cmds.connectAttr(f'{jnt_mult}.outputZ', f'{jnt}.scaleZ', f=1)

def connect_ik_stretch_to_joints(rigname, joints, stretch_ratio, typ):
    '''
    Connect IK stretch to joints.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joint names to apply squash to
        stretch_ratio (str): Stretch ratio multiplier
        typ (str): Rig type identifier (TYPE_IK, TYPE_FK)
    '''
    logger.debug(f"{rigname}: Connect IK stretch to joints")
    if not cmds.objExists(stretch_ratio):
        logger.error(f'Stretch ratio node not found: {stretch_ratio}')

    for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
        jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
        if not cmds.objExists(jnt_mult):
            logger.warning(f'Joint multiply node not found: {jnt_mult}')
            continue

        # Connect stretch ratio to multiply node
        cmds.connectAttr(f'{stretch_ratio}.outputR', f'{jnt_mult}.input2X', f=1)
        # Connect to joint translateX
        cmds.connectAttr(f'{jnt_mult}.outputX', f'{jnt}.translateX', f=1)

def connect_fk_stretch_to_joints(rigname, joints, stretch_ratio, typ):
    '''
    Connect FK stretch to SDK groups.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joint names to apply squash to
        stretch_ratio (str): Stretch ratio multiplier
        typ (str): Rig type identifier (TYPE_IK, TYPE_FK)
    '''
    logger.debug(f"{rigname}: Connect FK stretch to SDK groups")

    if not cmds.objExists(stretch_ratio):
        logger.error(f'Stretch ratio node not found: {stretch_ratio}')

    for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
        jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
        first_sdk = rt_nam.fstr(rigname, rt_cst.SDK_GRP, typ, i, 1)
        if not cmds.objExists(jnt_mult):
            logger.warning(f'Joint multiply node not found: {jnt_mult}')
            continue
        if not cmds.objExists(first_sdk):
            logger.warning(f'SDK group not found: {first_sdk}')
            continue

        # Connect stretch ratio to multiply node
        cmds.connectAttr(f'{stretch_ratio}.outputR', f'{jnt_mult}.input2X', f=1)
        # Connect to SDK group translateX
        cmds.connectAttr(f'{jnt_mult}.outputX', f'{first_sdk}.translateX', f=1)


# MEASURE CURVE LENGTH =================================================

def set_curveinfo_stretch(rigname, curve, typ=''):
    '''
    Set up curve measurement system for stretch.

    - FK: Measure the skinned curve
    - IK: Measure the solver curve (used by ikHandle)

    Arguments
        curve (str): Name of curve
        rigname (str): Name of rig part
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return
        scale_curveinfo (str): Scaling curveInfo node
    '''
    if not cmds.objExists(curve):
        logger.error(f'Curve {curve} does not exist')

    # Create curveInfo on curve (reused on re-run)
    scale_crvinfo = f'{typ}_{rigname}_scale_curveInfo'
    if not cmds.objExists(scale_crvinfo):
        cmds.createNode('curveInfo', n=scale_crvinfo, s=1, ss=1)

    # Connect curve's worldSpace to curveInfo
    curve_shape = cmds.listRelatives(curve, s=1, ni=1)[0]
    cmds.connectAttr(f'{curve_shape}.worldSpace[0]', f'{scale_crvinfo}.inputCurve', f=1)

    # Get initial_len and store as custom attribute; on re-run keep the
    # stored rest length instead of re-baking a possibly stretched curve
    cmds.dgeval(f'{scale_crvinfo}.arcLength')
    initial_len = cmds.getAttr(f'{scale_crvinfo}.arcLength')
    if not cmds.attributeQuery('initial_length', node=scale_crvinfo, exists=True):
        cmds.addAttr(scale_crvinfo, ln='initial_length', at='float', dv=initial_len)
    elif cmds.getAttr(f'{scale_crvinfo}.initial_length') <= 0 and initial_len > 0:
        # A zero rest length (from a failed earlier build) would divide
        # by zero downstream; refresh it from the current curve
        cmds.setAttr(f'{scale_crvinfo}.initial_length', initial_len)

    if initial_len <= 0:
        logger.warning(f"Curve '{curve}' - initial_len is invalid {initial_len:.3f}")
    else:
        logger.debug(f"Curve '{curve}' - initial_len {initial_len:.3f}")

    return scale_crvinfo

def fallback_curve_length(rigname, typ):
    '''
    Fallback method using joint positions when curve method fails.

    Arguments
        rigname (str): Name of rig component
        typ (str): Rig type identifier (TYPE_IK, TYPE_FK)

    Return
        fallback_crvlen (str): fallback curve length remapValue node
    '''
    logger.warning(f"{typ}_{rigname}: Using fallback joint distance calculation")

    if typ == rt_cst.TYPE_FK:
        joints = rt_cst.JOINTS_FK[rigname]
    elif typ == rt_cst.TYPE_IK:
        joints = rt_cst.JOINTS_IK[rigname]
    else:
        logger.error(f'Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')
        return None

    # Create remapValue node that outputs total joint chain length
    fallback_crvlen = f'{typ}_{rigname}_fallback_length'
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
    cmds.setAttr(f'{fallback_crvlen}.inputValue', 1)

    return fallback_crvlen


# SPLINE TWIST (IK) ====================================================

def build_advanced_twist(ikhandle, start_obj, end_obj, start_vec, end_vec):
    '''
    Build Spline IK advanced twist.

    Arguments
        ikhandle (str): spline ik handle
        start_obj (str): First obj (cluster transform) for twist
        end_obj (str): Last obj (cluster transform) for twist
        start_vec (tuple): Start up vector
        end_vec (tuple): End up vector
    '''
    # advancedSplineIkTwist
    cmds.setAttr(f'{ikhandle}.dTwistControlEnable', 1)
    cmds.setAttr(f'{ikhandle}.dWorldUpType', 4)  # Rot up start/end
    cmds.setAttr(f'{ikhandle}.dWorldUpAxis', 3)  # Up Axis to pos z

    # Start / end obj
    cmds.connectAttr(f'{start_obj}.xformMatrix', f'{ikhandle}.dWorldUpMatrix', f=1)
    cmds.connectAttr(f'{end_obj}.xformMatrix', f'{ikhandle}.dWorldUpMatrixEnd', f=1)

    # Start / end vec
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorX', start_vec[0])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorY', start_vec[1])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorZ', start_vec[2])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorEndX', end_vec[0])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorEndY', end_vec[1])
    cmds.setAttr(f'{ikhandle}.dWorldUpVectorEndZ', end_vec[2])
