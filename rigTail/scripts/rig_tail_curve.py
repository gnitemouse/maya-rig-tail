'''
# rig_tail_curve.py
author: Daisy Jane @gnitemouse

Curves, spline IK handles and clusters for Rig Tail.

Takes joint rest positions, base to tip. Every rest SHAPE is derived from
those positions in Python and never sampled off a curve in the scene, so
a rebuild over a posed rig cannot bake the pose in as rest; only
parameter ranges and CV counts are read live. Curves are left with no
construction history, so curve length never re-evaluates through stale
history, and the solver curve reproduces the joint chain at rest.
Existing handles and clusters are found and renamed rather than
duplicated, so a rebuild keeps its connections. Clusters are IK only.

Constraining clusters to controls belongs to rig_tail_connect and the
spline's twist to build_advanced_twist; neither is wired here.

The IK side is a pair. The DRIVER curve carries one CV per cluster
control plus two for the up-vector ends, few enough that each control
owns exactly one CV and too few to describe the chain's real shape. It
therefore drives the SOLVER curve, one CV per joint, as an OFFSET FROM
REST rather than as an absolute position: at rest the solver curve is the
joint chain and the clusters deform it from there. See
connect_driver_to_solver_curve. The FK curve follows the joint positions
exactly and is only read from, never deformed.

Functions:
    driver_curve_positions: the driver curve's rest CVs, from joint rest
    solver_curve_cvs: the solver CVs that land the joints on their rest
        positions
    create_curve: NURBS curve from joint positions, FK or IK flavour
    wire_aim_frame / base_up_node / rest_aim_frames: the frame the rest
        correction is measured in, aimed down the driver curve
    connect_driver_to_solver_curve: wire the driver curve into the solver
        as an offset from rest
    create_spline_handle: spline ikHandle on the chain, reusing existing
    rename_spline_handle: bring a found handle/effector/curve onto the
        naming template
    get_spline_handle: existing handle by name or joint chain
    create_cluster: one cluster on given CVs
    create_clusters_on_curve: the full cluster row for a curve
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_math as rt_math
import rig_tail_joint as rt_joint

logger = logger_setup(__name__)


# CURVE ================================================================

def driver_curve_positions(jnt_pos):
    '''
    CV positions of the IK driver curve: NUM_CTRL_IK joints sampled evenly
    by index, with the first and last duplicated for the upvec clusters.

    create_curve and connect_driver_to_solver_curve both call this, so both
    derive the same rest CVs from the same joint positions rather than one
    of them reading the live curve.

    Arguments
        jnt_pos (list): Joint world positions, base to tip

    Return
        list: NUM_CTRL_IK + 2 CV positions
    '''
    indices = rt_math.linspace(0, len(jnt_pos)-1, rt_constants.NUM_CTRL_IK)
    return [jnt_pos[0]] + [jnt_pos[round(i)] for i in indices] + [jnt_pos[-1]]


SOLVER_CV_PASSES = 4


def solver_curve_cvs(jnt_pos, degree=3, passes=SOLVER_CV_PASSES):
    '''
    CV positions whose curve puts the joints back where they belong.

    A degree-3 curve does not pass through its own CVs, so CVs placed at the
    joints leave the spline settling them somewhere else, short enough
    overall to drop the last joint off the end. It also leaves the rebuild
    loop open: each build reads joints that are slightly off the last
    build's input, and that compounds.

    The condition to solve for is not 'the curve passes through the joints'.
    The ikSpline places joints by ARCLENGTH rather than at a parameter, so
    it is 'the joints, placed by their own bone lengths, land on the
    joints'. Fixed-point iteration on exactly that::

        P = joints
        repeat:  P += joints - place_by_arclength(curve(P))

    The smoothing is a contraction, so this converges, and each pass
    strictly reduces the residual it corrects. It runs at build time and
    adds no nodes: the offset network in connect_driver_to_solver_curve
    already writes an arbitrary constant per CV, so this only changes what
    that constant aims at.

    Arguments
        jnt_pos (list): Joint rest positions, base to tip
        degree (int): Curve degree (clamped against the CV count)
        passes (int): Correction passes; 0 returns jnt_pos unchanged

    Return
        list: CV positions, one per joint
    '''
    if len(jnt_pos) < 3 or passes <= 0:
        return [list(p) for p in jnt_pos]

    bones = [rt_math.cumulative_lengths(jnt_pos[i-1:i+1])[-1]
             for i in range(1, len(jnt_pos))]
    cvs = [list(p) for p in jnt_pos]
    for _ in range(passes):
        points, table = rt_math.bspline_arclength_table(cvs, degree)
        acc = 0.0
        landed = [points[0]]
        for bone in bones:
            acc += bone
            landed.append(rt_math.bspline_at_arclength(points, table, acc))
        cvs = [[cvs[i][k] + (jnt_pos[i][k] - landed[i][k]) for k in range(3)]
               for i in range(len(cvs))]
    return cvs


def create_curve(rigname, jnt_pos, typ, tag=''):
    '''
    Create a NURBS curve from joint positions.

    Three flavours, by typ and tag:
    - FK (no tag): one CV per joint, for the skinCluster and for the varFK
      controls to read their position from
    - IK driver (no tag): NUM_CTRL_IK CVs plus two duplicated ends for the
      upvec clusters, so each control owns exactly one CV
    - IK solver (tag='spline'): one CV per joint, ends NOT duplicated, read
      by the ikHandle

    An existing curve of the same name is reused untouched.

    Arguments
        rigname (str): Name of rig component for curve naming
        jnt_pos (list): List of joint world positions (tuples)
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)
        tag (str): Additional tag for curve name ('spline' for the IK solver)

    Return
        curve (str): Name of the curve, or None if given fewer than 2
            positions
    '''
    curve = rt_naming.fstr(rigname, rt_constants.CURVE, typ, TAG=tag)
    logger.debug(f"{rigname}: Create curve '{curve}'")
    logger.trace(f'jnt_pos len{len(jnt_pos)} {jnt_pos}')

    # Rebuild-safe: reuse the existing curve untouched. This path is only
    # reached when joints are unchanged (cleanup_connections); recreating
    # would name-clash and orphan skinCluster/cluster/pointOnCurveInfo
    # connections. A real joint change goes through cleanup_rigname, which
    # deletes the curve first.
    if cmds.objExists(curve):
        logger.trace(f"Curve '{curve}' exists, reusing")
        return curve

    # Validate input
    if len(jnt_pos) < 2:
        logger.error(f'Need at least 2 joint positions, got {len(jnt_pos)}')
        return None

    if typ == rt_constants.TYPE_FK:
        # FK: Create curve matching joint positions exactly for skinCluster binding
        degree = min(3, len(jnt_pos)-1)
        curve = cmds.curve(n=curve, d=degree, p=jnt_pos)

    elif typ == rt_constants.TYPE_IK:
        if tag:
            # IK Spline solver curve: one CV per joint, ends NOT duplicated.
            # The duplicated ends belong on the driver curve, where the upvec
            # clusters live (see driver_curve_positions). Nothing is attached
            # to this curve - every CV is driven by a pointOnCurveInfo - so
            # doubling its ends bought nothing and cost the tip: coincident
            # end CVs give a degenerate end tangent, and the solver places
            # joints by DISTANCE along the curve, so in that parked zone the
            # joints crowd up and their aim direction comes from a tangent
            # that is going to zero. Measured on the squid C_fintail, CV
            # spacing over the last few CVs collapsed 0.34 -> 0.0002, and the
            # last joints came out kinked by 30 and 16 degrees.
            degree = min(3, len(jnt_pos)-1)
            curve = cmds.curve(n=curve, d=degree, p=jnt_pos)
        else:
            # IK driver curve: control CVs + duplicated ends for the upvec
            # clusters. Low CV count on purpose - one cluster per CV is what
            # gives each IK control a single CV to move.
            all_pos = driver_curve_positions(jnt_pos)
            degree = min(3, len(all_pos)-1)
            curve = cmds.curve(n=curve, d=degree, p=all_pos)
    else:
        # Default: Simple curve
        degree = min(3, len(jnt_pos)-1)
        curve = cmds.curve(p=jnt_pos, d=degree, n=curve)

    # Clean up and organize
    rt_naming.rename_shapes(curve, typ='crv')
    rt_maya.set_curve_visibility(curve)
    cmds.delete(curve, ch=1) # Delete construction history

    spline_grp = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, typ)
    rt_maya.parent_to(curve, spline_grp)

    num_cv = cmds.getAttr(f'{curve}.controlPoints', size=True)
    logger.trace(f"Created curve '{curve}' with {num_cv} CVs, degree {degree}")
    return curve

# The rest correction's frame: +X follows the driver curve, +Z rolls toward
# the base control. Which axes these are does not matter to the result - the
# correction is baked against whatever frame the node produces (see
# rest_aim_frames) - but they must be the same on both sides of that bake.
AIM_PRIMARY_AXIS = (1, 0, 0)
AIM_SECONDARY_AXIS = (0, 0, 1)


def wire_aim_frame(node, tangent_plug, up_plug, base_plug):
    '''
    Wire one aimMatrix into the frame the rest correction rides.

    Called for the runtime nodes and for the throwaway one that reads the
    rest frames, so the two cannot drift apart: the bake is only correct
    while it inverts the same frame the rig evaluates. inputMatrix is the
    base control, which keeps the rig's global scale and a stable roll; only
    the aim axis is overridden, by the curve's direction at this parameter.

    Arguments
        node (str): aimMatrix node name
        tangent_plug (str): Plug supplying the curve tangent (double3)
        up_plug (str): Plug supplying the roll reference (double3)
        base_plug (str): Base control worldMatrix plug

    Return
        str: the node name
    '''
    cmds.setAttr(f'{node}.primaryMode', 2) # align to a vector, not a point
    cmds.setAttr(f'{node}.secondaryMode', 2)
    cmds.setAttr(f'{node}.primaryInputAxis', *AIM_PRIMARY_AXIS, type='double3')
    cmds.setAttr(f'{node}.secondaryInputAxis', *AIM_SECONDARY_AXIS, type='double3')
    rt_maya.ensure_connect(base_plug, f'{node}.inputMatrix')
    rt_maya.ensure_connect(tangent_plug, f'{node}.primaryTargetVector')
    rt_maya.ensure_connect(up_plug, f'{node}.secondaryTargetVector')
    return node


def base_up_node(rigname, typ, basectrl):
    '''
    The roll reference for every rest frame: the base control's +Z in world.

    One per rig part, shared by all its CVs. A tangent alone leaves the roll
    about it undetermined, and the curve's own normal cannot supply it: that
    flips where the curve runs straight, which is most of a tail.

    Arguments
        rigname (str): Name of rig component
        typ (str): Type identifier (TYPE_IK)
        basectrl (str): Base control

    Return
        str: pointMatrixMult node name
    '''
    node = f'{typ}_{rigname}_restup_pointMatrixMult'
    if not cmds.objExists(node):
        cmds.createNode('pointMatrixMult', n=node, s=1, ss=1)
    cmds.setAttr(f'{node}.vectorMultiply', 1)
    cmds.setAttr(f'{node}.inPoint', *AIM_SECONDARY_AXIS, type='double3')
    rt_maya.ensure_connect(f'{basectrl}.worldMatrix[0]', f'{node}.inMatrix')
    return node


def rest_aim_frames(rest_cvs, params, basectrl, up_plug):
    '''
    The frame each CV's correction is measured in, at REST.

    Read off a throwaway curve holding the rest CVs rather than computed in
    Python. The bake divides the correction by this frame and the rig
    multiplies it back by the live one, so the two must be the same
    construction; sharing wire_aim_frame makes that true by construction
    rather than by a reading of what aimMatrix does with primaryMode.

    The base control is read live. That needs no rest assumption: whatever
    pose it is in is inverted out here and multiplied back in at evaluation.

    Arguments
        rest_cvs (list): Driver curve CV positions at rest, world space
        params (list): Driver curve parameter per solver CV
        basectrl (str): Base control
        up_plug (str): Roll reference plug (base_up_node)

    Return
        list: One 16-float matrix per parameter
    '''
    degree = min(3, len(rest_cvs)-1)
    tmp_curve = cmds.curve(d=degree, p=rest_cvs)
    tmp_shape = cmds.listRelatives(tmp_curve, s=1, ni=1)[0]
    tmp_poci = cmds.createNode('pointOnCurveInfo', ss=1)
    tmp_aim = cmds.createNode('aimMatrix', ss=1)
    cmds.connectAttr(f'{tmp_shape}.worldSpace[0]', f'{tmp_poci}.inputCurve')
    wire_aim_frame(tmp_aim, f'{tmp_poci}.normalizedTangent', up_plug,
                   f'{basectrl}.worldMatrix[0]')

    frames = list()
    for param in params:
        cmds.setAttr(f'{tmp_poci}.parameter', param)
        frames.append(cmds.getAttr(f'{tmp_aim}.outputMatrix'))

    # remove_nodes, not cmds.delete: the up node is upstream of tmp_aim and
    # this is the only thing reading it yet, so a bare delete cascades back
    # through that connection and takes it with them - leaving the runtime
    # frames below with no roll reference to wire.
    rt_maya.remove_nodes([tmp_aim, tmp_poci, tmp_curve])
    return frames


def connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ,
                                   jnt_pos=None):
    '''
    Wire the cluster-deformed driver curve into the solver curve, as an
    OFFSET FROM REST rather than an absolute position.

    Each solver CV gets a pointOnCurveInfo sampling the driver curve at a
    fixed parameter, and is driven as::

        solver_cv[i] = driver_sample(t_i) + (rest_cv[i] - driver_rest(t_i))

    The bracketed term is a build-time constant: how far the low-CV driver
    curve falls short of the real shape at that CV. Driving absolute
    positions instead cuts the corner on a base bend and leaves the curve
    shorter than the joint chain, pushing the last joints off the end of it.

    The constant is stored as a local displacement, never a fixed world
    vector. Every cluster handle is parentConstrained to controls under the
    cog, so turning the character rotates the whole set of driver CVs in
    world space, and an offset that stayed put while they rotated would
    deform the tail by up to its own length. A pointMatrixMult in
    vectorMultiply mode multiplies it back out through a live frame each
    evaluation (3x3 only: this is a displacement, not a position).

    That frame is the DRIVER CURVE's, not the base control's. basectrl sits
    upstream of every IK control, so a correction riding it answers only to
    the whole rig moving: bend the tail and the driver sample swings to its
    new position while the correction still points where it pointed at rest,
    bowing an S into the curve. An aimMatrix per CV takes its aim from the
    tangent the same pointOnCurveInfo computes, putting the frame downstream
    of the controls so it turns with a local bend too. inputMatrix stays
    basectrl, which keeps the rig's global scale in the correction.

    A weighted deformer would not sharpen any of this: a cluster owns a
    single CV with the handle's rotate pivot on it, so rotating an IK
    control is a no-op and the deformation is translation-only. That same
    property is why the control-to-cluster constraints in rig_tail_connect
    are safe without maintainOffset.

    Arguments
        rigname (str): Name of rig component
        driver_curve (str): Curve with clusters (low CV set)
        solver_curve (str): Curve used by ikHandle (one CV per joint)
        typ (str): Type identifier (TYPE_IK)
        jnt_pos (list): Joint rest positions the curves were built from.
            The rest correction needs them, and is skipped without them,
            leaving the CVs driven by absolute position.
    '''
    logger.trace(f"Connect driver curve '{driver_curve}' to solver curve '{solver_curve}'")

    # Get curve shape nodes for connections
    driver_shape = cmds.listRelatives(driver_curve, s=1, ni=1)[0]
    solver_shape = cmds.listRelatives(solver_curve, s=1, ni=1)[0]

    # Get curve information for parameterization
    solver_num_cv = cmds.getAttr(f'{solver_curve}.controlPoints', size=True)
    driver_num_cv = cmds.getAttr(f'{driver_curve}.controlPoints', size=True)

    # Use solver curve's actual parameter range (after ikHandle rebuilding)
    solver_min_param = cmds.getAttr(f'{solver_curve}.minValue')
    solver_max_param = cmds.getAttr(f'{solver_curve}.maxValue')
    solver_param_range = solver_max_param - solver_min_param

    # Driver curve parameter range for sampling
    driver_min_param = cmds.getAttr(f'{driver_curve}.minValue')
    driver_max_param = cmds.getAttr(f'{driver_curve}.maxValue')
    driver_param_range = driver_max_param - driver_min_param

    logger.trace(f'Solver CVs: {solver_num_cv}, Driver CVs: {driver_num_cv}')
    logger.trace(f'Solver param range: {solver_min_param:.3f} to {solver_max_param:.3f}')
    logger.trace(f'Driver param range: {driver_min_param:.3f} to {driver_max_param:.3f}')

    # Rest reference for the offset. Both sides are derived from jnt_pos, not
    # read from the scene, so this is identical on every rebuild. Lengths
    # must line up: the solver curve is one CV per joint (see create_curve),
    # so anything else means the two were built from different joint sets and
    # a correction would be guesswork.
    # The correction rides a frame aimed down the driver curve (see the
    # docstring), and that frame still takes its roll and the rig's global
    # scale from the base control. Without a basectrl there is nothing to
    # anchor either to, so skip the correction rather than bake a
    # world-locked offset.
    # Parameters first: the rest frames are read in one sweep of a throwaway
    # curve rather than one per CV inside the loop.
    if solver_num_cv == 1:
        driver_params = [driver_min_param + (driver_param_range * 0.5)]
    else:
        driver_params = [driver_min_param + driver_param_range
                         * (float(i) / (solver_num_cv - 1))
                         for i in range(solver_num_cv)]

    rest_cv = None
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    if jnt_pos and len(jnt_pos) == solver_num_cv and cmds.objExists(basectrl):
        # NOT jnt_pos itself: a degree-3 curve does not pass through its own
        # CVs, so aiming the correction at the joints leaves the spline
        # settling them off it. solver_curve_cvs solves for the CVs that put
        # the joints where they belong (see there).
        rest_cv = solver_curve_cvs(jnt_pos, min(3, len(jnt_pos)-1))
        driver_rest_cv = driver_curve_positions(jnt_pos)
        up_node = base_up_node(rigname, typ, basectrl)
        rest_frames = rest_aim_frames(driver_rest_cv, driver_params, basectrl,
                                      f'{up_node}.output')
    elif jnt_pos and not cmds.objExists(basectrl):
        logger.warning(
            f"{rigname}: No base control '{basectrl}' to anchor the IK rest "
            f'correction to - skipping it, so the IK rest shape will be the '
            f'low-CV driver curve as before')
    elif jnt_pos:
        logger.warning(
            f'{rigname}: {len(jnt_pos)} joint positions against '
            f'{solver_num_cv} solver CVs - skipping the rest correction, so '
            f'the IK rest shape will be the low-CV driver curve as before')

    # Create pointOnCurveInfo nodes for each solver curve CV (reuse existing)
    for cv_i in range(solver_num_cv):
        # Create pointOnCurveInfo node to sample driver curve
        poci = f'{typ}_{rigname}_poci_{cv_i:02d}_pointOnCurveInfo'
        if not cmds.objExists(poci):
            cmds.createNode('pointOnCurveInfo', n=poci, s=1, ss=1)
        rt_maya.ensure_connect(f'{driver_shape}.worldSpace[0]', f'{poci}.inputCurve')

        # Solver CV position mapped evenly onto the driver curve's parameters
        driver_param = driver_params[cv_i]
        cmds.setAttr(f'{poci}.parameter', driver_param)
        logger.trace(f'CV {cv_i}: parameter {driver_param:.3f}')

        src = poci
        src_attr = ('positionX', 'positionY', 'positionZ')
        if rest_cv is not None:
            # rest correction: what the low-CV driver curve cannot express at
            # this CV, held in the curve's own frame here so it turns with a
            # local bend and not only with the rig
            sample = rt_math.bspline_point(driver_rest_cv, driver_param)
            offset = [rest_cv[cv_i][k] - sample[k] for k in range(3)]
            local = rt_math.transform_vector(
                offset, rt_math.invert_matrix(rest_frames[cv_i]))

            # aimMatrix: the driver curve's frame at this parameter
            restaim = f'{typ}_{rigname}_restaim_{cv_i:02d}_aimMatrix'
            if not cmds.objExists(restaim):
                cmds.createNode('aimMatrix', n=restaim, s=1, ss=1)
            wire_aim_frame(restaim, f'{poci}.normalizedTangent',
                           f'{up_node}.output', f'{basectrl}.worldMatrix[0]')

            # pointMatrixMult, vectorMultiply: local offset back out to world
            restvec = f'{typ}_{rigname}_restfix_{cv_i:02d}_pointMatrixMult'
            if not cmds.objExists(restvec):
                cmds.createNode('pointMatrixMult', n=restvec, s=1, ss=1)
            cmds.setAttr(f'{restvec}.vectorMultiply', 1)
            cmds.setAttr(f'{restvec}.inPoint', *local, type='double3')
            rt_maya.ensure_connect(f'{restaim}.outputMatrix',
                                   f'{restvec}.inMatrix')

            restfix = f'{typ}_{rigname}_restfix_{cv_i:02d}_plusMinusAverage'
            if not cmds.objExists(restfix):
                cmds.createNode('plusMinusAverage', n=restfix, s=1, ss=1)
            cmds.setAttr(f'{restfix}.operation', 1) # add
            rt_maya.ensure_connect(f'{poci}.position', f'{restfix}.input3D[0]')
            rt_maya.ensure_connect(f'{restvec}.output', f'{restfix}.input3D[1]')
            src = restfix
            src_attr = ('output3Dx', 'output3Dy', 'output3Dz')
            if cv_i == 0 or cv_i == solver_num_cv-1:
                logger.trace(f'CV {cv_i}: rest offset {offset} local {local}')

        # Connect position to solver curve CV
        for attr, axis in zip(src_attr, 'xyz'):
            rt_maya.ensure_connect(
                f'{src}.{attr}',
                f'{solver_shape}.controlPoints[{cv_i}].{axis}Value')

    logger.trace(f'Created {solver_num_cv} pointOnCurveInfo connections')

    # Store driver curve reference on solver curve for cleanup and debugging
    if not cmds.attributeQuery('driver_curve', n=solver_curve, ex=1):
        cmds.addAttr(solver_curve, ln='driver_curve', dt='string')
        cmds.setAttr(f'{solver_curve}.driver_curve', driver_curve, type='string')


# SPLINE ===============================================================

def create_spline_handle(rigname, joints, curve, typ=rt_constants.TYPE_IK):
    '''
    Create a spline IK handle reading the given solver curve.

    Left to itself, cmds.ikHandle builds its own curve and simplifies it to
    a handful of CVs. That simplification is the curve-creation step rather
    than the solver, so it is sidestepped: the handle is created with its
    temporary curve, that curve is disconnected and the solver curve
    connected to .inCurve in its place, and the temporary one is deleted.
    The solver then works against one CV per joint, and any smoothing left
    in the result comes from the low-CV driver curve upstream.

    Any handle and effector already carrying these names are removed first,
    so a rebuild that keeps them cannot leave a same-named duplicate for a
    later short-name lookup to trip on.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints for IK chain (minimum 3)
        curve (str): IK solver curve name
        typ (str): Rig type identifier (TYPE_IK)

    Return
        spline_list (list): [ikhandle, effector, curve] after renaming
    '''
    if len(joints) < 3:
        logger.error(f"Must have more than 3 joints '{joints}'")
        return None

    # Clean up old spline components. Remove the previous handle and effector
    # BY NAME, so a rebuild over an existing rig (the light-cleanup path, which
    # keeps these nodes) cannot leave a same-named duplicate for a later
    # short-name lookup to trip on ("Too many objects or values"). This must
    # NOT go through get_spline_handle: it returns [] whenever the handle's
    # joint-list query is empty -- which is exactly its state on a rebuilt
    # chain, i.e. exactly when the stale handle needs removing. It also returns
    # the solver curve in its tuple, and that curve is the one passed in here
    # (create_curve reuses it) and connected to the fresh handle below, so
    # removing via that list would delete the curve we are about to use. The
    # handle and effector are recreated unconditionally by cmds.ikHandle just
    # below, so removing them by name cannot break a working part -- it only
    # makes the removal reliable. This mirrors cleanup_rigname's full-teardown
    # removal, moved into the build so the light path gets it too.
    rt_maya.remove('curveInfo1')
    rt_maya.remove(rt_naming.fstr(rigname, rt_constants.SPLINE_HANDLE, typ))
    rt_maya.remove(rt_naming.fstr(rigname, rt_constants.SPLINE_EFFECTOR, typ))

    # Create IK handle [ikhandle, effector, temp_curve]
    spline_list = cmds.ikHandle(n=rt_naming.fstr(rigname, rt_constants.SPLINE_HANDLE, typ),
                                sj=joints[0],
                                ee=joints[-1],
                                sol='ikSplineSolver')

    # Get curve CV information
    num_cv, _spans, _degree = rt_maya.get_num_cv(spline_list[2])
    logger.trace(f"ikHandle curve '{spline_list[2]}' has {num_cv} CVs, {_spans} spans, degree {_degree}")

    # Replace ikHandle's curve with our solver curve
    # Get shape nodes
    ikhandle_crvshape = cmds.listRelatives(spline_list[2], s=1, ni=1)[0]
    ikspline_crvshape = cmds.listRelatives(curve, s=1, ni=1)[0]
    # Disconnect temp curve from ikHandle. On a rebuild the connection may
    # already be absent; the force-connect below sets inCurve regardless, so
    # only disconnect when the connection actually exists.
    if cmds.isConnected(f'{ikhandle_crvshape}.worldSpace[0]', f'{spline_list[0]}.inCurve'):
        cmds.disconnectAttr(f'{ikhandle_crvshape}.worldSpace[0]', f'{spline_list[0]}.inCurve')
    # Connect solver curve to ikHandle
    cmds.connectAttr(f'{ikspline_crvshape}.worldSpace[0]', f'{spline_list[0]}.inCurve', f=1)

    return rename_spline_handle(rigname, spline_list, curve, typ)

def rename_spline_handle(rigname, spline_list, curve, typ):
    '''
    Bring a handle and effector onto the naming template, parent them under
    the spline group and delete the temporary curve.

    Arguments
        rigname (str): Name of rig component
        spline_list (list): [ikhandle, effector, temp_curve] from cmds.ikHandle
        curve (str): Solver curve name
        typ (str): Rig type identifier (TYPE_IK)

    Return
        list: [renamed_handle, renamed_effector, curve]
    '''
    spline_handle = rt_naming.fstr(rigname, rt_constants.SPLINE_HANDLE, typ)
    spline_effector = rt_naming.fstr(rigname, rt_constants.SPLINE_EFFECTOR, typ)

    # Rename components
    cmds.rename(spline_list[0], spline_handle)
    cmds.rename(spline_list[1], spline_effector)
    rt_maya.remove(spline_list[2]) # Delete temp curve

    # Organize
    rt_maya.parent_to(spline_list[0], rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, typ))
    rt_naming.rename_shapes(curve, typ='crv')
    rt_maya.set_curve_visibility(curve)

    return [spline_handle, spline_effector, curve]

def get_spline_handle(rigname, joints=None):
    '''
    Find the existing spline handle by name, and its effector and curve.

    Arguments
        rigname (str): Name of rig component
        joints (list): Joints the handle must span; checked when given

    Return
        tuple: (ikhandle, effector, curve), or [] when the handle is
            missing, is not an ikHandle, has no joint list, or spans
            joints other than the ones given
    '''
    spline_handle = rt_naming.fstr(rigname, rt_constants.SPLINE_HANDLE, rt_constants.TYPE_IK)

    if not cmds.objExists(spline_handle):
        return []

    # Check if it's a valid ikHandle (not just a leftover name)
    if cmds.nodeType(spline_handle) != 'ikHandle':
        logger.warning(f'{spline_handle} exists but is not an ikHandle')
        return []

    # Get spline handle info
    try:
        effector = cmds.ikHandle(spline_handle, q=True, ee=True)
        curve = cmds.ikHandle(spline_handle, q=True, c=True).split('|')[-2]
        jl = cmds.ikHandle(spline_handle, q=True, jl=True)
    except (RuntimeError, TypeError) as e:
        logger.warning(f'Failed to query ikHandle {spline_handle}: {e}')
        return []

    # Validate joint list
    if jl is None:
        logger.warning(f'IK handle {spline_handle} has no joint list')
        return []

    # If joints provided, validate against them
    if joints:
        if not (rt_joint.is_equal_joint(jl[0], joints[0]) and rt_joint.is_equal_joint(jl[-1], joints[-2])):
            logger.warning(f'IK handle joint mismatch')
            return []

    logger.trace(f'({spline_handle}, {effector}, {curve})')
    return (spline_handle, effector, curve)


# CLUSTERS =============================================================

def create_cluster(cluster_names, curve, cv_i):
    '''
    Create a cluster on given CVs of a curve, reusing one that exists.

    Arguments
        cluster_names (list): [cluster_node_name, cluster_handle_name]
        curve (str): Target curve name
        cv_i (int or str): CV index, or a CV range string ('curve.cv[1:5]')

    Return
        cluster (list): [cluster_node, cluster_handle]
    '''
    cluster_node, cluster_handle = cluster_names

    if cmds.objExists(cluster_node):
        logger.trace(f'Cluster exists: {cluster_node}')
        return [cluster_node, cluster_handle]
    else:
        if isinstance(cv_i, int):
            cv_target = f'{curve}.cv[{cv_i}]'
        else:
            cv_target = cv_i
        logger.trace(f'Creating cluster: {cluster_node} on {cv_target}')
        cluster = cmds.cluster(cv_target, n=cluster_node, rel=False)
        return cluster

def create_clusters_on_curve(rigname, curve, typ, show_handle=False):
    '''
    Create the full cluster row on a curve: the upvec pair on the first and
    last CV, then one control cluster per interior CV.

    IK only, and errors on any other typ. Nothing deforms the FK curve: the
    varFK controls only read positions off it (set_curveinfo_fk), and FK
    twist and roll come from their own SDK-layer network
    (connect_twist_roll). The upvec pair is IK machinery specifically,
    consumed by build_advanced_twist as the spline handle's world-up
    objects, which needs an ikHandle to exist at all.

    Clusters already on the curve are deleted first, handles included, since
    an orphaned handle name-clashes with the recreated cluster's.

    Arguments
        rigname (str): Name of rig component
        curve (str): NURBS curve name
        typ (str): Must be TYPE_IK
        show_handle (bool): Show Maya clusterHandles in viewport

    Return
        clusters (list): (cluster_node, cluster_handle) pairs, upvec base
            and end first, then the control clusters in CV order.
            create_controls_ik relies on that split.
    '''
    if typ != rt_constants.TYPE_IK:
        logger.error(f'Invalid TYPE {typ}. Clusters are IK only.')
        return []

    logger.debug(f"Create clusters on curve '{curve}'")
    clusters = list()

    # Clean up old clusters, including their handle transforms:
    # an orphaned handle name-clashes with the recreated cluster's handle
    crvshape = cmds.listRelatives(curve, shapes=True, noIntermediate=True) or []
    for shape in crvshape:
        deformers = cmds.listHistory(shape) or []
        for node in deformers:
            if cmds.objExists(node) and cmds.nodeType(node) == 'cluster':
                handles = cmds.listConnections(f'{node}.matrix', s=True, d=False) or []
                cmds.delete(node)
                for handle in handles:
                    if cmds.objExists(handle):
                        rt_maya.remove(handle)

    # Get curve CV information
    num_cv, _spans, _degree = rt_maya.get_num_cv(curve)
    logger.trace(f"Curve '{curve}' has {num_cv} CVs, {_spans} spans, degree {_degree}")

    # IK: Create upvec clusters at first and last CVs
    cluster_upv_bse = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV, typ, TAG='base')
    cluster_handle_bse = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE, typ, TAG='base')
    cluster_upv_end = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV, typ, TAG='end')
    cluster_handle_end = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE, typ, TAG='end')

    clusters.append(create_cluster([cluster_upv_bse, cluster_handle_bse], curve, 0))
    clusters.append(create_cluster([cluster_upv_end, cluster_handle_end], curve, num_cv-1))

    # Control clusters for interior CVs (1 to num_cv-2). The order matters:
    # create_controls_ik takes handles[0:2] as the upvec pair and handles[2:]
    # as the control set (its duplicate_ends flag).
    for i in range(1, num_cv-1):
        cluster_node = rt_naming.fstr(rigname, rt_constants.CLUSTER, typ, i)
        cluster_handle = rt_naming.fstr(rigname, rt_constants.CLUSTER_HANDLE, typ, i)
        clusters.append(create_cluster([cluster_node, cluster_handle], curve, i))

    # Organize under cluster group. Created here if missing rather than
    # assumed: the callers make it, but a build that switched modes (or
    # aborted partway through a previous run) can reach this point
    # without it, and parenting to a name that is not there aborts the
    # whole build on 'No object matches name'.
    cluster_grp = rt_naming.fstr(rigname, rt_constants.CLUSTER_GRP, typ)
    if not cmds.objExists(cluster_grp):
        logger.warning(f"Cluster group '{cluster_grp}' missing, creating it")
        rt_maya.create_group(cluster_grp,
                            parent=rt_naming.fstr('', rt_constants.CLUSTERS_GRP))
    for cluster_node, cluster_handle in clusters:
        rt_maya.parent_to(cluster_handle, cluster_grp)
        cmds.setAttr(f'{cluster_handle}.displayHandle', show_handle)
        logger.trace(f'[{cluster_node}, {cluster_handle}],')

    logger.trace(f'Created {len(clusters)} clusters for {typ}')
    cmds.select(clear=True)
    return clusters
