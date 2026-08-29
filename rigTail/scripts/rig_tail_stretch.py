'''
# rig_tail_stretch.py
author: Daisy Jane @gnitemouse

Squash and stretch for the tail. A node network derives a single
stretch ratio and drives two outputs from it:

    Length    - joints spread apart or bunch up along the tail
    Thickness - BN joints fatten or thin via scaleY/scaleZ

The ratio is the tail curve's current length over its rest length. IK
derives it live from a curveInfo (the rest length is cached once so
rebuilding while posed doesn't adopt a stretched rest); FK is slider-
only, and deliberately so - nothing in variable FK pulls the tip, so
there is no goal length to react to.

The 'stretch' slider reaches the two modes by different routes. FK adds
it straight onto the ratio. IK cannot: a term there lengthens the bones
while the curve stays where it was, and the tail walks out past the very
controls meant to hold it. So IK spends the slider on the CONTROLS
instead - the row spreads, the curve grows with it, and the reactive
ratio picks the new length up by itself. One mechanism, and the controls
sit on the tail because they define it. Both modes are clamped.

Length scales each joint's rest bone offset by the ratio. IK writes
that absolutely onto the joint's own translate. FK cannot: its joints
hang from SDK groups whose offsetParentMatrix already carries the rest
offset, so it adds (ratio - 1) times that offset on the translate
channel instead, which sums to the same thing and leaves an untouched
slider at the rig's built rest state.

Thickness uses ratio^-0.5 (the taffy rule), blended by
'preserveVolume', trimmed by 'squash', divided out by global scale,
and scaled per joint by 'jntScaleYZ'.

Architecture notes:
  - Build/connect split: build_stretch() runs early and only creates
    nodes; the base-control sliders don't exist yet, so remap inputs
    sit unconnected until rig_tail_connect creates the attributes and
    calls connect_stretch_to_joints(). Nodes are looked up by name and
    reused, so re-runs refresh the network instead of duplicating it.
  - BN children are placed by offsetParentMatrix, so a parent's squash
    would shear them; rig_tail_matrix cancels it with a squashInv OPM
    term.
  - The IK controls sit UPSTREAM of everything they deform: control ->
    cluster -> driver curve -> solver curve -> ikHandle and curveInfo ->
    joints. Whatever drives a control may read the sliders, its own baked
    rest, or controls nearer the base; reading the curve, its length or
    the joints closes the loop and cycles.
  - preserveVolume is a thickness dial only: it fades the taffy rule in
    and out of scaleY/Z and has no say in how long the tail is. Gating
    the reactive ratio with it would make preserveVolume = 0 mean the IK
    length cannot react to its own curve.

Functions:
    Build phase (called from rig_tail):
        build_stretch: Create the full stretch/squash node network
        set_curveinfo_stretch: curveInfo with stored rest length
        fallback_curve_length: Joint-distance fallback when curve fails
        remap_stretch_attr / create_stretch / create_squash /
        create_world_scale / create_joint_mult: network pieces
        fk_stretch_delta: Name of the FK (ratio - 1) node
        sdk_rest_offset: Rest bone offset out of an SDK group's OPM
        driven_source: Whether a plug's driver is itself driven
    Connect phase (called from rig_tail_connect):
        add_stretch_attributes_to_basectrl: stretch/squash/preserveVolume
        add_jntscale_attributes_to_basectrl: per-joint jntScaleYZ sliders
        connect_stretch_to_joints: Wire sliders and outputs to joints
        connect_stretch_to_ik_controls: Spread the IK control row from
            the dial, so IK stretches by growing its own curve
        connect_stretch_to_ik_controls: spread all three IK control sets
        spread_nested / spread_flat: the spread, by set hierarchy
        spread_factor_node / spread_rest: the shared factor, the baked rest
        ik_ / float_ / spline_control_groups: the sets to spread
    Spline IK:
        build_advanced_twist: Spline IK advanced twist setup
'''

import maya.cmds as cmds
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_mirror as rt_mirror

logger = logger_setup(__name__)

# Rest segment vector cached on each IK control group, so a rebuild reads
# the built rest rather than whatever the dial is currently spreading
REST_SEGMENT_ATTR = 'stretch_rest_segment'


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
    logger.debug(f"{typ}_{rigname}: Build Stretch")

    # Create curveInfo for measurement
    curvelen = set_curveinfo_stretch(rigname, curve, typ)
    if not curvelen:
        abort_build(logger, 'Failed to create scale curveInfo')
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
    - Stretch dial -> IK control row, which is how IK stretches at all

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Base control with squash/stretch attr
        fk (bool): Connect FK stretch
        ik (bool): Connect IK stretch
    '''
    logger.trace(f'{rigname}: Connect stretch to joints')

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
        stretch_ratio = f'{rt_constants.TYPE_IK}_{rigname}_stretch_ratio'
        connect_ik_stretch_to_joints(rigname, rt_constants.JOINTS_IK[rigname], stretch_ratio, rt_constants.TYPE_IK)
        # IK reads its ratio off the curve, so the dial reaches the joints
        # by moving the controls that shape it, not by a term of its own
        stretch_remap = f'{rigname}_stretch_remap_multiplyDivide'
        connect_stretch_to_ik_controls(rigname, stretch_remap, rt_constants.TYPE_IK)
    if fk:
        # The DELTA, not the ratio: FK adds to a rest offset already held
        # in the SDK group's offsetParentMatrix
        stretch_delta = fk_stretch_delta(rigname, rt_constants.TYPE_FK)
        connect_fk_stretch_to_joints(rigname, rt_constants.JOINTS_FK[rigname], stretch_delta, rt_constants.TYPE_FK)

def add_stretch_attributes_to_basectrl(rigname, basectrl):
    if not rt_constants.EFFECTS['stretchy']:
        return
    rt_maya.add_attribute_enum(basectrl, rt_constants.STRETCH_DIVIDER[0], rt_constants.STRETCH_DIVIDER[1], rt_constants.STRETCH_DIVIDER[2])

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
    if not rt_constants.EFFECTS['stretchy']:
        return
    rt_maya.add_attribute_enum(basectrl, rt_constants.SCALE_DIVIDER[0], rt_constants.SCALE_DIVIDER[1], rt_constants.SCALE_DIVIDER[2])

    for i, bn_jnt in enumerate(rt_constants.JOINTS_BN[rigname]):
        if not cmds.attributeQuery(f'jntScaleYZ{i:02}', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln=f'jntScaleYZ{i:02}', at='float', k=1, dv=1, min=0.5, max=10)


# CREATE NODES =========================================================

def fk_stretch_delta(rigname, typ=rt_constants.TYPE_FK):
    '''
    Name of the node carrying (stretch ratio - 1) for the FK chain.

    Both phases resolve the node through here so the build and connect
    sides cannot drift apart on a rename.

    Arguments
        rigname (str): Name of rig component
        typ (str): Rig type identifier (TYPE_FK)

    Return
        str: plusMinusAverage node name
    '''
    return f'{typ}_{rigname}_stretch_delta_plusMinusAverage'


def driven_source(plug):
    '''
    Whether plug is fed by a node that is itself fed.

    A node left behind by a rebuild that dropped its mode still answers
    objExists and still holds a connection, so plain existence cannot tell
    a live driver from a stale one. Reading one step further up does: the
    stretch ratios all sit downstream of something, and an orphan has
    nothing above it.

    Arguments
        plug (str): Destination plug to inspect

    Return
        bool: True when plug's source node has an input of its own
    '''
    src = cmds.listConnections(plug, s=True, d=False) or []
    if not src:
        return False
    return bool(cmds.listConnections(src[0], s=True, d=False, skipConversionNodes=False))


def sdk_rest_offset(sdk_grp):
    '''
    An SDK group's rest bone offset, expressed in its OWN local frame.

    create_sdk_groups bakes each FK joint's rest transform into the top
    SDK group's offsetParentMatrix and leaves the local channels at zero,
    so the bone length is in that matrix rather than in translateX. This
    reads it back in the units the translate channel is in.

    The frame change is what makes a curved rest pose stretch correctly.
    offsetParentMatrix carries the joint's ORIENTATION as well as its
    offset, and a local translate is applied before it, so a raw
    (length, 0, 0) would push the joint along the CHILD joint's aim axis
    instead of along the bone. Rotating the offset into the local frame
    cancels that. On a straight chain the rotation is identity and this
    returns (length, 0, 0).

    Dividing by each basis row's squared length also absorbs any scale
    baked into the matrix, rather than mis-scaling the result by it.

    Arguments
        sdk_grp (str): SDK group whose offsetParentMatrix holds the rest

    Return
        list or None: [x, y, z] in the group's local frame, or None when a
            basis row is degenerate (a zero-length bone, which no ratio
            can stretch)
    '''
    mtx = cmds.getAttr(f'{sdk_grp}.offsetParentMatrix')
    trans = mtx[12:15]
    rest = list()
    for row in (mtx[0:3], mtx[4:7], mtx[8:11]):
        sqlen = row[0]*row[0] + row[1]*row[1] + row[2]*row[2]
        if sqlen < 1e-12:
            return None
        rest.append((trans[0]*row[0] + trans[1]*row[1] + trans[2]*row[2]) / sqlen)
    return rest


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

    logger.trace(f"Created remap nodes '{stretch_remap}' '{squash_remap}'")
    return stretch_remap, squash_remap

def create_stretch(rigname, joints, curvelen, stretch_remap, typ):
    '''
    Create stretch calculation node network.

    IK: Reactive stretch only (curve length / initial length). The dial
        reaches IK through the controls (connect_stretch_to_ik_controls)
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
        abort_build(logger, f'Unrecognized curvelen {curvelen}')
        return
    logger.trace(f'curvelen {curvelen} initial_len {initial_len:.3f}')

    if initial_len <= 0:
        # Degenerate rest length would make the reactive ratio divide
        # by zero; fall back to a neutral rest length
        logger.warning(f'{typ}_{rigname}: initial_len {initial_len} '
                       'invalid, using 1.0')
        initial_len = 1.0

    if typ == rt_constants.TYPE_IK:
        # IK: Reactive stretch based on current curve length
        # (nodes are reused on re-runs; only connections are refreshed)

        # (multiplyDivide) reactive stretch ratio (current_len / initial_len)
        stretch_reactive = f'{typ}_{rigname}_stretch_reactive_multiplyDivide'
        if not cmds.objExists(stretch_reactive):
            cmds.createNode('multiplyDivide', n=stretch_reactive, s=1, ss=1)
        cmds.setAttr(f'{stretch_reactive}.operation', 2)  # divide
        cmds.connectAttr(crvlen, f'{stretch_reactive}.input1X', f=1)
        cmds.setAttr(f'{stretch_reactive}.input2X', initial_len)

        # The reactive ratio IS the whole IK ratio. It already tracks
        # whatever the controls do to the curve, so the dial spreads those
        # controls (connect_stretch_to_ik_controls) instead of adding a
        # second term here - a term that grows the bones without moving
        # anything, which is what walks the tail out past its own controls.
        #
        # Older rigs hold a preserveVolume blend and a user sum in this
        # slot. Neither belongs in the length path - the blend pins the
        # ratio to 1.0 wherever preserveVolume reaches 0, and the sum is
        # where the slider term went in.
        # remove_nodes, not cmds.delete: both sit BETWEEN the reactive node
        # and the clamp, so a delete that does not disconnect first can
        # cascade into the length path it was meant to clear.
        rt_maya.remove_nodes(
            [f'{typ}_{rigname}_stretch_preservevol_blendTwoAttr',
             f'{typ}_{rigname}_stretch_user_plusMinusAverage'])

        # (clamp) stretch_ratio - 0.1 to 2.0
        stretch_ratio = f'{typ}_{rigname}_stretch_ratio'
        if not cmds.objExists(stretch_ratio):
            cmds.createNode('clamp', n=stretch_ratio, s=1, ss=1)
        cmds.connectAttr(f'{stretch_reactive}.outputX', f'{stretch_ratio}.inputR', f=1)
        cmds.setAttr(f'{stretch_ratio}.minR', 0.1)
        cmds.setAttr(f'{stretch_ratio}.maxR', 2.0)

    elif typ == rt_constants.TYPE_FK:
        # Slider only. Nothing in variable FK pulls the tip, so there is no
        # goal length to react to, and the FK curveInfo cannot supply one:
        # that curve is skinned to the FK joints, so a length driving the
        # stretch would be measuring what the stretch moved.

        # A rebuild can meet a doubling node in this slot with nothing
        # reading it, which would sit in the graph as an orphan.
        stretch_double = f'{typ}_{rigname}_stretch_double_multiplyDivide'
        rt_maya.remove_nodes([stretch_double])

        # The remap scales the -10..10 dial to -0.5..0.5, the same term IK
        # adds to its reactive ratio, so one dial value means one amount of
        # stretch in either mode.
        stretch_pma = f'{typ}_{rigname}_stretch_user_plusMinusAverage'
        if not cmds.objExists(stretch_pma):
            cmds.createNode('plusMinusAverage', n=stretch_pma, s=1, ss=1)
        cmds.setAttr(f'{stretch_pma}.operation', 1)  # sum
        cmds.setAttr(f'{stretch_pma}.input1D[0]', 1)  # Base ratio = 1
        cmds.connectAttr(f'{stretch_remap}.outputX', f'{stretch_pma}.input1D[1]', f=1)

        # (clamp) stretch_ratio
        stretch_ratio = f'{typ}_{rigname}_stretch_ratio'
        if not cmds.objExists(stretch_ratio):
            cmds.createNode('clamp', n=stretch_ratio, s=1, ss=1)
        cmds.connectAttr(f'{stretch_pma}.output1D', f'{stretch_ratio}.inputR', f=1)
        cmds.setAttr(f'{stretch_ratio}.minR', 0.1)
        cmds.setAttr(f'{stretch_ratio}.maxR', 2.0)

        # The FK rest offset sits in an offsetParentMatrix, so the translate
        # channel carries only what stretch adds on top: the ratio less its
        # rest of 1 (see create_joint_mult).
        #
        # Read from the CLAMPED ratio and shared by every joint. The clamp
        # is what bounds how far the tail can stretch, and a delta taken
        # upstream of it would let length run past a bound that thickness
        # still obeys.
        stretch_delta = fk_stretch_delta(rigname, typ)
        if not cmds.objExists(stretch_delta):
            cmds.createNode('plusMinusAverage', n=stretch_delta, s=1, ss=1)
        cmds.setAttr(f'{stretch_delta}.operation', 2)  # subtract
        cmds.connectAttr(f'{stretch_ratio}.outputR',
                         f'{stretch_delta}.input1D[0]', f=1)
        cmds.setAttr(f'{stretch_delta}.input1D[1]', 1)

    else:
        abort_build(logger, f'Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')

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
        abort_build(logger, f'Unrecognized curvelen {curvelen}')

    # (multiplyDivide) squash_vol - Inverse sqrt for volume preservation
    # Shared between FK and IK builds; the IK stretch ratio wins when both
    # are built, since it reacts to curve length where FK's only follows a
    # slider. FK yields to a LIVE IK ratio only. A rebuild that drops IK
    # leaves its ratio node behind with nothing driving it, and a clamp
    # with no input holds its own minimum, which would peg thickness at a
    # constant and leave the squash dials dead.
    squash_vol = f'{rigname}_squash_volume_multiplyDivide'
    if not cmds.objExists(squash_vol):
        cmds.createNode('multiplyDivide', n=squash_vol, s=1, ss=1)
        cmds.setAttr(f'{squash_vol}.operation', 3)  # power
        cmds.setAttr(f'{squash_vol}.input2X', -0.5)
    if typ == rt_constants.TYPE_IK or not driven_source(f'{squash_vol}.input1X'):
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

    logger.trace(f"Created squash nodes '{squash_vol}' '{squash_blend}' '{squash_pma}'")
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
    scale_grp = rt_naming.fstr(rigname, rt_constants.SCALE_GRP)

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

    logger.trace(f"Created world scale nodes '{scale_world}' '{squash_world}'")
    return scale_world, squash_world

def create_joint_mult(rigname, joints, typ):
    '''
    Create multiply nodes for each joint.
    These will be connected in connect phase.

    For stretch: scale the joint's rest bone offset. IK multiplies its
        translateX by the ratio; FK multiplies its whole offset vector by
        (ratio - 1), because its rest already sits in an SDK group's
        offsetParentMatrix (see sdk_rest_offset).
    For squash: multiply base squash by per-joint scale attribute

    Arguments
        rigname (str): Name of rig component
        joints (str list): List of joint names to apply stretch to
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return
        stretch_jnt_mult (str list): Stretch joint multiplier nodes
        squash_jnt_mult (str list): Squash joint multiplier nodes
    '''
    logger.trace(f"{rigname}: Create joint multiply nodes")
    # Stretch multiply nodes
    stretch_jnt_mult = list()
    if typ == rt_constants.TYPE_IK:
        for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
            jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
            if not cmds.objExists(jnt_mult):
                cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
                cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            cmds.setAttr(f'{jnt_mult}.input1X', cmds.getAttr(f'{jnt}.translateX'))
            stretch_jnt_mult.append(jnt_mult)

    elif typ == rt_constants.TYPE_FK:
        # The whole rest offset rather than its aim component alone, so a
        # curved rest pose stretches along the bone (see sdk_rest_offset).
        # The multiply therefore runs on all three channels.
        for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
            jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
            # SDK groups are named for the joint's OWN number
            # (create_sdk_groups), which matches its position in the list
            # only on a chain numbered from zero.
            NN = rt_naming.get_index_from_name(jnt)
            first_sdk = rt_naming.fstr(rigname, rt_constants.SDK_GRP, typ, NN, 1)
            if not cmds.objExists(jnt_mult):
                cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
                cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
            if not cmds.objExists(first_sdk):
                logger.warning(f'SDK group {first_sdk} does not exist')
                stretch_jnt_mult.append(jnt_mult)
                continue
            rest = sdk_rest_offset(first_sdk)
            if rest is None:
                # A zero multiply holds the joint at its rest length rather
                # than scaling an offset with no direction to scale along
                logger.warning(f"'{first_sdk}' has a degenerate rest "
                               f"offset; '{jnt}' will not stretch")
                rest = (0, 0, 0)
            cmds.setAttr(f'{jnt_mult}.input1', *rest, type='double3')
            stretch_jnt_mult.append(jnt_mult)

    # Squash multiply nodes
    squash_jnt_mult = list()
    for i, jnt in enumerate(rt_constants.JOINTS_BN[rigname]):
        jnt_mult = f'{rigname}_squash_{i:02d}_multiplyDivide'
        if not cmds.objExists(jnt_mult):
            cmds.createNode('multiplyDivide', n=jnt_mult, s=1, ss=1)
            cmds.setAttr(f'{jnt_mult}.operation', 1)  # multiply
        squash_jnt_mult.append(jnt_mult)

    return stretch_jnt_mult, squash_jnt_mult


# CONNECT NODES ========================================================

def connect_preserve_volume(rigname, basectrl, squash_blend):
    '''
    Connect preserveVolume attribute to the squash blend.

    preserveVolume is a THICKNESS dial only: it fades the taffy rule in and
    out of scaleY/Z, and has no say in how long the tail is. Blending the
    IK length path with it too would make 'preserveVolume = 0' pin the
    ratio to 1.0, leaving the joints unable to follow their own curve.

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Name of basectrl
        squash_blend (str): Volume preservation blend (reactive squash)
    '''
    logger.trace(f"{rigname}: Connect preserveVolume to blend")
    if not cmds.attributeQuery('preserveVolume', n=basectrl, ex=1):
        logger.warning(f'preserveVolume attribute not found on {basectrl}')
        return

    # resolved_plug: override condition output when the main controller
    # dashboard is active, the basectrl attribute otherwise
    preservevol_src = rt_ctrlall.resolved_plug(rigname, 'preserveVolume')

    # Connect to squash blend
    if cmds.objExists(squash_blend):
        cmds.connectAttr(preservevol_src,
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
    logger.trace(f"{rigname}: Connect world scale")
    scale_grp = rt_naming.fstr(rigname, rt_constants.SCALE_GRP)
    if not cmds.objExists(scale_grp):
        abort_build(logger, f'Scale group not found: {scale_grp}')

    # Constrain scale group to basectrl
    scale_constr = rt_maya.get_constraint(scale_grp, typ='scaleConstraint')
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
    logger.trace(f"{rigname}: Connect joint squash")
    if not cmds.objExists(squash_world):
        abort_build(logger, f'Missing squash world node: {squash_world}')

    for i, jnt in enumerate(rt_constants.JOINTS_BN[rigname]):
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
    logger.trace(f"{rigname}: Connect IK stretch to joints")
    if not cmds.objExists(stretch_ratio):
        abort_build(logger, f'Stretch ratio node not found: {stretch_ratio}')

    for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
        jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
        if not cmds.objExists(jnt_mult):
            logger.warning(f'Joint multiply node not found: {jnt_mult}')
            continue

        # Connect stretch ratio to multiply node
        cmds.connectAttr(f'{stretch_ratio}.outputR', f'{jnt_mult}.input2X', f=1)
        # Connect to joint translateX
        cmds.connectAttr(f'{jnt_mult}.outputX', f'{jnt}.translateX', f=1)

# The SplineIK set, in rt_constants.SPLINE_CONTROLS order, and the subset
# the spread drives. 'bot' is the anchor at the base of the tail, and 'mid'
# is parentConstrained to bot and top (constrain_spline_controls) - it
# follows them already, and its translate is not ours to drive.
SPLINE_ROLES = ('bot', 'bot_sml', 'mid', 'top_sml', 'top', 'mid_rot')
# Which rule a role spreads by is decided by its PARENT, not by where it
# sits in the row. bot_sml hangs off bot, top off mid_rot and top_sml off
# top, so each of those translates is a segment the DAG accumulates.
# mid_rot hangs off the BASE CONTROL, like bot and mid do, so its translate
# is a whole offset from the base and scaling it moves it about the base's
# origin instead of about bot.
SPLINE_NESTED_ROLES = ('bot_sml', 'top', 'top_sml')
SPLINE_ANCHOR_ROLE = 'bot'
SPLINE_FLAT_ROLES = ('mid_rot',)


def connect_stretch_to_ik_controls(rigname, stretch_remap, typ=rt_constants.TYPE_IK):
    '''
    Spread all three IK control sets along the tail from the stretch dial.

    The IK ratio is reactive only, so the dial cannot lengthen the tail on
    its own: it has to lengthen the CURVE, and the controls are what the
    curve is made of. SplineIK, IK and Float share one ratio because they
    share one spline, and they share one spread factor here for the same
    reason. What differs between them is only how each set's hierarchy
    carries that factor - see spread_nested and spread_flat.

    All three spread at once, with no mode gating. Only the active mode's
    controls drive the clusters (setup_switch_ik weights the constraints)
    and the other two sets are hidden, so the inactive ones simply sit
    where they would have been had they been active. That is what keeps a
    switch mid-dial from popping.

    Every set spreads about its own FIRST control, which is what lets the
    modes agree. SplineIK needs both rules to manage it: mid_rot hangs off
    the base control rather than off another spline control, so the nested
    rule would scale it about the base's origin while the rest of the set
    scales about bot. The two centres differ by bot's own offset from the
    base, and mid - which tracks the average of bot and top - lands on the
    bot-centred answer, so mid and mid_rot pulled apart as the dial went up.

    Nothing here reads the curve, its length or the joints: all three sit
    downstream of the controls, so a control reading them would feed its
    own input. The factor is the dial and a rest vector baked at build.

    Arguments
        rigname (str): Name of rig component
        stretch_remap (str): Stretch dial scaled to -0.5..0.5
        typ (str): Rig type identifier (TYPE_IK)
    '''
    logger.trace(f'{rigname}: Spread the IK control sets from the stretch dial')
    factor = spread_factor_node(rigname, stretch_remap, typ)

    built = set()
    built |= spread_nested(rigname, 'ik', ik_control_groups(rigname, typ)[1:],
                           factor, typ)

    spline = spline_control_groups(rigname, typ)
    if SPLINE_ANCHOR_ROLE in spline:
        built |= spread_flat(
            rigname, 'spline_base',
            [spline[SPLINE_ANCHOR_ROLE]]
            + [spline[role] for role in SPLINE_FLAT_ROLES if role in spline],
            factor, typ)
    built |= spread_nested(rigname, 'spline',
                           [spline[role] for role in SPLINE_NESTED_ROLES
                            if role in spline], factor, typ)

    built |= spread_flat(rigname, 'float',
                         float_control_groups(rigname, typ), factor, typ)

    # Which node drives which group is decided by the set's name and the
    # group's place in it, so a set that gains or loses a role - as SplineIK
    # just did - renames its whole row. Anything the pass above did not
    # write is from an older layout and would sit driving nothing.
    stale = [node for node in cmds.ls(f'{typ}_{rigname}_spread_*') or []
             if node not in built and node != factor]
    if stale:
        rt_maya.remove_nodes(stale)

def spread_factor_node(rigname, stretch_remap, typ=rt_constants.TYPE_IK):
    '''
    The one number every control set scales its rest offset by: 1 + dial.

    Shared across the three sets deliberately. They drive the same clusters
    on the same curve, so a set that spread by a different amount would
    move the tail on a mode switch rather than on the dial.

    Arguments
        rigname (str): Name of rig component
        stretch_remap (str): Stretch dial scaled to -0.5..0.5
        typ (str): Rig type identifier (TYPE_IK)

    Return
        str: plusMinusAverage node name
    '''
    node = f'{typ}_{rigname}_spread_factor_plusMinusAverage'
    if not cmds.objExists(node):
        cmds.createNode('plusMinusAverage', n=node, s=1, ss=1)
    cmds.setAttr(f'{node}.operation', 1)  # sum
    cmds.setAttr(f'{node}.input1D[0]', 1)
    cmds.connectAttr(f'{stretch_remap}.outputX', f'{node}.input1D[1]', f=1)
    return node

def spread_nested(rigname, setname, ctrlgrps, factor, typ=rt_constants.TYPE_IK):
    '''
    Scale each group's rest SEGMENT by the factor.

    For the sets whose controls nest - IK's single row, SplineIK's two
    chains (bot -> bot_sml, and mid_rot -> top -> top_sml). create_control
    puts a group under every control, so each group's translate is the
    offset from the control above it. Scaling every segment scales the
    chain about its base and the DAG accumulates it, so no control has to
    read another: acyclic by construction rather than by careful wiring.

    The caller drops the anchor group. It holds where the chain STARTS
    rather than a segment, so scaling it would slide the tail off its base.

    Arguments
        rigname (str): Name of rig component
        setname (str): Control set tag for node names ('ik', 'spline')
        ctrlgrps (list): Control groups to drive, anchor already dropped
        factor (str): Spread factor node
        typ (str): Rig type identifier (TYPE_IK)

    Return
        set: Nodes this wrote, for the caller's stale sweep
    '''
    built = set()
    for i, ctrlgrp in enumerate(ctrlgrps, 1):
        mult = f'{typ}_{rigname}_spread_{setname}_{i:02d}_multiplyDivide'
        if not cmds.objExists(mult):
            cmds.createNode('multiplyDivide', n=mult, s=1, ss=1)
            cmds.setAttr(f'{mult}.operation', 1)  # multiply
        cmds.setAttr(f'{mult}.input1', *spread_rest(ctrlgrp), type='double3')
        for axis in 'XYZ':
            cmds.connectAttr(f'{factor}.output1D', f'{mult}.input2{axis}', f=1)
        drive_group_translate(ctrlgrp, f'{mult}.output')
        built.add(mult)
    return built

def spread_flat(rigname, setname, ctrlgrps, factor, typ=rt_constants.TYPE_IK):
    '''
    Scale each group's rest offset FROM THE FIRST CONTROL by the factor.

    Float is the only set whose controls do not nest: connect_spline_ik
    parents every one of its groups straight to the base control. So each
    translate is already measured from the base and is therefore CUMULATIVE
    - there is no hierarchy to accumulate through, and no segment to scale.

    Scaling that offset whole would move the first control too, which the
    nested sets leave pinned. Subtracting the anchor out and adding it back
    pins the same control, so the two agree on a mode switch::

        translate = rest_1 + (rest_i - rest_1) * factor

    Still no live reads: the nested sets get their acyclicity from the DAG,
    this one from having nothing to read in the first place.

    Arguments
        rigname (str): Name of rig component
        setname (str): Control set tag for node names ('float')
        ctrlgrps (list): Control groups, base first, anchor included
        factor (str): Spread factor node
        typ (str): Rig type identifier (TYPE_IK)

    Return
        set: Nodes this wrote, for the caller's stale sweep
    '''
    built = set()
    if len(ctrlgrps) < 2:
        logger.warning(f'{rigname}: fewer than two {setname} control groups, '
                       'so the stretch dial has no row to spread')
        return built

    anchor = spread_rest(ctrlgrps[0])
    for i, ctrlgrp in enumerate(ctrlgrps[1:], 1):
        rest = spread_rest(ctrlgrp)
        span = [rest[k] - anchor[k] for k in range(3)]

        mult = f'{typ}_{rigname}_spread_{setname}_{i:02d}_multiplyDivide'
        if not cmds.objExists(mult):
            cmds.createNode('multiplyDivide', n=mult, s=1, ss=1)
            cmds.setAttr(f'{mult}.operation', 1)  # multiply
        cmds.setAttr(f'{mult}.input1', *span, type='double3')
        for axis in 'XYZ':
            cmds.connectAttr(f'{factor}.output1D', f'{mult}.input2{axis}', f=1)

        pma = f'{typ}_{rigname}_spread_{setname}_{i:02d}_plusMinusAverage'
        if not cmds.objExists(pma):
            cmds.createNode('plusMinusAverage', n=pma, s=1, ss=1)
        cmds.setAttr(f'{pma}.operation', 1)  # sum
        cmds.connectAttr(f'{mult}.output', f'{pma}.input3D[0]', f=1)
        cmds.setAttr(f'{pma}.input3D[1]', *anchor, type='double3')

        drive_group_translate(ctrlgrp, f'{pma}.output3D')
        built |= {mult, pma}
    return built

def drive_group_translate(ctrlgrp, plug):
    '''
    Connect a double3 plug onto a control group's translate.

    Maya refuses a compound connection while a child plug is driven, and
    force does not cover it (see connect_fk_stretch_to_joints), so the
    channels come apart first.

    Arguments
        ctrlgrp (str): Control group
        plug (str): double3 output plug to drive it with
    '''
    for axis in 'XYZ':
        rt_maya.break_connection(f'{ctrlgrp}.translate{axis}')
    cmds.connectAttr(plug, f'{ctrlgrp}.translate', f=1)

def numbered_control_groups(rigname, template, typ=rt_constants.TYPE_IK):
    '''
    The groups of a NUM_CTRL_IK-long control set, base first.

    Named here rather than read back through rig_tail_control, which would
    import the control module into this one for two f-strings.

    Arguments
        rigname (str): Name of rig component
        template (str): Control naming template
        typ (str): Rig type identifier (TYPE_IK)

    Return
        ctrlgrps (list): Control group names, in row order
    '''
    ctrlgrps = list()
    for NN in range(1, rt_constants.NUM_CTRL_IK+1):
        ctrl = rt_naming.fstr(rigname, template, typ, NN)
        ctrlgrp = f'{ctrl}_{rt_constants.GRP}'
        if cmds.objExists(ctrlgrp):
            ctrlgrps.append(ctrlgrp)
        else:
            logger.warning(f"Control group '{ctrlgrp}' does not exist")
    return ctrlgrps

def ik_control_groups(rigname, typ=rt_constants.TYPE_IK):
    return numbered_control_groups(rigname, rt_constants.SPLINE_IK_CTRL, typ)

def float_control_groups(rigname, typ=rt_constants.TYPE_IK):
    return numbered_control_groups(rigname, rt_constants.SPLINE_FLOAT_CTRL, typ)

def spline_control_groups(rigname, typ=rt_constants.TYPE_IK):
    '''
    The SplineIK control groups by role, skipping any that are missing.

    Keyed rather than indexed: this set is a fixed six with its own
    hierarchy, so which one a group is matters more than where it sits in
    the list (see SPLINE_SPREAD_ROLES).

    Arguments
        rigname (str): Name of rig component
        typ (str): Rig type identifier (TYPE_IK)

    Return
        groups (dict): role -> control group name
    '''
    groups = dict()
    for role, template in zip(SPLINE_ROLES, rt_constants.SPLINE_CONTROLS):
        ctrl = rt_naming.fstr(rigname, template, typ)
        ctrlgrp = f'{ctrl}_{rt_constants.GRP}'
        if cmds.objExists(ctrlgrp):
            groups[role] = ctrlgrp
        else:
            logger.warning(f"Spline control group '{ctrlgrp}' does not exist")
    return groups

def spread_rest(ctrlgrp):
    '''
    A control group's rest translate, cached on the group itself.

    Once the spread network drives the group its translate reads the
    CURRENT spread, so a rebuild that re-baked it would fold the dial into
    the rest and compound it every time. Cached once on first build, the
    same guard set_curveinfo_stretch puts on initial_length.

    What the value MEANS depends on the set: a segment from the control
    above for the nested sets, an offset from the base control for Float.
    Both are just the group's own translate at rest.

    Arguments
        ctrlgrp (str): Control group

    Return
        rest (tuple): Rest translate in the group's parent frame
    '''
    if not cmds.attributeQuery(REST_SEGMENT_ATTR, node=ctrlgrp, exists=True):
        cmds.addAttr(ctrlgrp, ln=REST_SEGMENT_ATTR, at='double3')
        for axis in 'XYZ':
            cmds.addAttr(ctrlgrp, ln=f'{REST_SEGMENT_ATTR}{axis}', at='double',
                         parent=REST_SEGMENT_ATTR)
        cmds.setAttr(f'{ctrlgrp}.{REST_SEGMENT_ATTR}',
                     *cmds.getAttr(f'{ctrlgrp}.translate')[0], type='double3')
    return cmds.getAttr(f'{ctrlgrp}.{REST_SEGMENT_ATTR}')[0]

def connect_fk_stretch_to_joints(rigname, joints, stretch_delta, typ):
    '''
    Connect FK stretch to SDK groups.

    Each joint's multiply holds its rest bone offset in the SDK group's
    local frame (create_joint_mult). Multiplying by (ratio - 1) and writing
    to translate ADDS to the rest the group's offsetParentMatrix already
    contributes, landing the joint at rest * ratio - where IK puts it, by a
    different route because FK has no joint translate of its own to
    overwrite.

    A delta rather than an absolute is what keeps an untouched slider free:
    at ratio 1 the channel sits at 0, its built rest, so the network stays
    inert and a rebuild over a stretched rig cannot read the pose as rest.

    Joint 0 is skipped, as in IK, which also leaves its translate to the
    'offset' dial driving the same channel one layer up (see
    rig_tail_fk.connect_twist_roll).

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joint names to apply stretch to
        stretch_delta (str): Stretch delta node, (ratio - 1)
        typ (str): Rig type identifier (TYPE_FK)
    '''
    logger.trace(f"{rigname}: Connect FK stretch to SDK groups")

    if not cmds.objExists(stretch_delta):
        abort_build(logger, f'Stretch delta node not found: {stretch_delta}')

    for i, jnt in enumerate(joints[1:], 1):  # Skip first joint
        jnt_mult = f'{typ}_{rigname}_stretch_{i:02d}_multiplyDivide'
        NN = rt_naming.get_index_from_name(jnt)
        first_sdk = rt_naming.fstr(rigname, rt_constants.SDK_GRP, typ, NN, 1)
        if not cmds.objExists(jnt_mult):
            logger.warning(f'Joint multiply node not found: {jnt_mult}')
            continue
        if not cmds.objExists(first_sdk):
            logger.warning(f'SDK group not found: {first_sdk}')
            continue

        for axis in 'XYZ':
            cmds.connectAttr(f'{stretch_delta}.output1D',
                             f'{jnt_mult}.input2{axis}', f=1)

        # Maya refuses a compound connection while a child plug is driven,
        # and force does not cover it, so a rig holding translateX alone
        # needs its children cleared. break_connection also unlocks, which
        # is the state create_group leaves them in.
        for axis in 'XYZ':
            rt_maya.break_connection(f'{first_sdk}.translate{axis}')

        # The rest offset is a vector in this group's frame, not a length
        # on the aim axis, so all three channels carry it
        cmds.connectAttr(f'{jnt_mult}.output', f'{first_sdk}.translate', f=1)


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
        abort_build(logger, f'Curve {curve} does not exist')

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
        logger.trace(f"Curve '{curve}' - initial_len {initial_len:.3f}")

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

    if typ == rt_constants.TYPE_FK:
        joints = rt_constants.JOINTS_FK[rigname]
    elif typ == rt_constants.TYPE_IK:
        joints = rt_constants.JOINTS_IK[rigname]
    else:
        abort_build(logger, f'Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')
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

# Spline IK advanced-twist axis enums, keyed (axis, positive).
FORWARD_AXIS_ENUM = {('x', True): 0, ('x', False): 1,
                     ('y', True): 2, ('y', False): 3,
                     ('z', True): 4, ('z', False): 5}
UP_AXIS_ENUM = {('y', True): 0, ('y', False): 1,
                ('z', True): 3, ('z', False): 4,
                ('x', True): 6, ('x', False): 7}


def build_advanced_twist(ikhandle, start_obj, end_obj, start_vec, end_vec,
                         rigname):
    '''
    Build Spline IK advanced twist.

    The solver names the JOINT's own local axes: which one runs down the
    chain and which is its up. Both come from ORIENT_AIM_AXIS /
    ORIENT_UP_AXIS - the Setup UI's Aim Axis and Up Axis - so it is told
    the convention the skeleton was actually oriented to rather than an
    assumed one.

    The FORWARD axis goes negative on a mirrored side whose aim runs
    child-to-parent (rt_mirror.aim_reversed). A solver told otherwise rolls
    the chain progressively along its whole length, and nothing else in the
    scene says why. The UP axis is the same on both sides: every behavior
    negates the up, and each side supplies its own mirrored up objects, so
    one enum still yields a mirrored roll.

    Arguments
        ikhandle (str): spline ik handle
        start_obj (str): First obj (cluster transform) for twist
        end_obj (str): Last obj (cluster transform) for twist
        start_vec (tuple): Start up vector
        end_vec (tuple): End up vector
        rigname (str): Rig part, for the mirrored-aim test. Required rather
            than defaulted: a caller that forgets it should raise, not
            silently build a chain that twists along its length.
    '''
    aim = str(getattr(rt_constants, 'ORIENT_AIM_AXIS', 'x')).strip().lower()
    up = str(getattr(rt_constants, 'ORIENT_UP_AXIS', 'z')).strip().lower()
    forward_positive = not rt_mirror.aim_reversed(rigname)
    fwd_enum = FORWARD_AXIS_ENUM.get((aim, forward_positive))
    up_enum = UP_AXIS_ENUM.get((up, True))
    if fwd_enum is None or up_enum is None:
        logger.warning(f"{rigname}: Advanced twist: unusable aim '{aim}' or "
                       f"up '{up}' axis, leaving the solver's own defaults")
        fwd_enum = FORWARD_AXIS_ENUM[('x', forward_positive)]
        up_enum = UP_AXIS_ENUM[('z', True)]

    # advancedSplineIkTwist
    cmds.setAttr(f'{ikhandle}.dTwistControlEnable', 1)
    cmds.setAttr(f'{ikhandle}.dWorldUpType', 4)  # Rot up start/end
    cmds.setAttr(f'{ikhandle}.dForwardAxis', fwd_enum)
    cmds.setAttr(f'{ikhandle}.dWorldUpAxis', up_enum)
    # Worth logging - a wrong axis here twists a whole chain and leaves
    # nothing else to see - but at debug, since it is per rig part and a
    # roster of tails would fill the build log with it. The warning above,
    # for an axis that is not the one asked for, stays at info.
    logger.debug(f'{rigname}: Advanced twist: forward axis '
                 f'{"+" if forward_positive else "-"}{aim.upper()}, '
                 f'up +{up.upper()}')

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
