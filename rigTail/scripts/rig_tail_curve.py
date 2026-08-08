'''
# rig_tail_curve.py
author: Daisy Jane @gnitemouse

Curves, spline IK handles and clusters for Rig Tail.

The FK curve follows the joint positions exactly (the variable-FK
controls read their position from it). The IK side is a pair: a DRIVER
curve carrying one CV per cluster control plus two for the up-vector
ends, and a SOLVER curve with one CV per joint, which is what the
ikHandle reads. Construction history is deleted at creation so curve
length never re-evaluates through stale history. Handles and clusters are
found and renamed rather than duplicated on rebuild.

The driver curve is deliberately low-resolution - one CV per control is
what gives each control a single CV to move - so it cannot describe the
chain's real shape. It therefore drives the solver curve as an OFFSET
FROM REST, not as an absolute position: at rest the solver curve is the
joint chain exactly, and the clusters deform it from there. Driving
absolute positions is what used to flatten the base of a tail (8% of the
bend surviving on the squid fintails) and shorten the curve below the
chain length, kinking the last joints. See
connect_driver_to_solver_curve.

Functions:
    driver_curve_positions: the driver curve's rest CVs, from joint rest
    create_curve: NURBS curve from joint positions, FK or IK flavour
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

    Factored out so create_curve and connect_driver_to_solver_curve derive
    the same rest CVs from the same joint positions. The second needs them
    to know where the driver curve sits AT REST, and must not read that off
    the live curve: on a rebuild the clusters are already constrained to the
    controls, so a live read picks up the current pose and bakes it in as
    rest.

    Arguments
        jnt_pos (list): Joint world positions, base to tip

    Return
        list: NUM_CTRL_IK + 2 CV positions
    '''
    indices = rt_math.linspace(0, len(jnt_pos)-1, rt_constants.NUM_CTRL_IK)
    return [jnt_pos[0]] + [jnt_pos[round(i)] for i in indices] + [jnt_pos[-1]]


def create_curve(rigname, jnt_pos, typ, tag=''):
    '''
    Create NURBS curve from joint positions with proper parameterization.

    The curve creation ensures:
    - FK curves match joint positions exactly for accurate skinCluster binding
    - IK curves have proper CV count for cluster control (NUM_CTRL_IK + 2 upvec CVs)
    - Both maintain consistent CV indexing for cluster creation
    - Construction history is deleted to prevent length evaluation issues

    Arguments
        rigname (str): Name of rig component for curve naming
        jnt_pos (list): List of joint world positions (tuples)
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)
        tag (str): Additional tag for curve name (e.g. 'spline' for IK solver curve)

    Return
        curve (str): Name of created curve, or None if creation failed
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

def connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ,
                                   jnt_pos=None):
    '''
    Wire the cluster-deformed driver curve into the solver curve, as an
    OFFSET FROM REST rather than an absolute position.

    Each solver CV gets a pointOnCurveInfo sampling the driver curve at a
    fixed parameter, exactly as before. What changed is what that sample
    means. It used to be written straight onto the CV, which made the tail's
    rest shape 'whatever a NUM_CTRL_IK+2 CV curve can express' - and it
    cannot express much. On the squid C_fintail, 50 joints collapsed to 7
    CVs, of which the first two and last two are coincident: the base bend
    (joints 1-5 carry 28.5 of the tail's 38.2 degrees) is spanned by a
    single CV interval, so the curve simply cut the corner. Only 8% of that
    bend survived, the base joint's aim was 22.3 degrees off, and cutting
    the corner made the curve 0.48 units SHORTER than the joint chain, which
    pushed the last joints clean off the end of it.

    Now each CV is driven as::

        solver_cv[i] = driver_sample(t_i) + (rest_cv[i] - driver_rest(t_i))

    The bracketed term is a constant worked out at build time: how far the
    low-CV driver curve falls short of the real shape at that CV. At rest
    the two cancel and the solver curve IS the joint chain, so there is no
    flattening and its length matches the chain. Move a cluster and the
    falloff is unchanged from before - sampling a B-spline at a parameter is
    a weighted sum of its CVs, so this is the same blend it always was, just
    measured from the right place.

    driver_rest is computed in Python (rt_math.bspline_point over
    driver_curve_positions) rather than read off the live curve, so a
    rebuild over a posed rig cannot bake the pose in as rest.

    The correction CANNOT be a fixed world vector. Every cluster handle is
    parentConstrained to controls that live under the cog, so turning the
    character rotates the whole set of driver CVs in world space - and an
    offset that stayed put while they rotated would deform the tail by up to
    its own length the moment the rig faced a different way. So it is stored
    in the base control's local space at build time and multiplied back out
    through basectrl.worldMatrix each evaluation, by a pointMatrixMult in
    vectorMultiply mode (3x3 only: this is a displacement, not a position).
    Rotate, scale or translate the rig and the correction goes with it.

Against a skinCluster on the same controls, this is not an approximation
    for anything the rig can currently do. Sampling a B-spline at a
    parameter is a weighted sum of its CVs whose weights total 1, so with
    the rest term restored the network computes::

        cv[i] = rest[i] + SUM_j w_ij * (control j's translation)

    which is exactly what linear blend skinning reduces to when influences
    translate. The two differ only when an influence ROTATES or SCALES -
    and here rotating an IK control moves nothing at all, because a cluster
    owns a single CV and the handle's rotate pivot sits on it (verified in
    the scene: IK_C_fintail_02_clusterHandle.rp is its own CV). Rotating a
    point about itself is a no-op, so the deformation is translation-only
    and the two agree everywhere.

    A weighted deformer (weighted clusters, or a skinCluster on
    NUM_CTRL_IK+2 influences) would therefore not correct anything here; it
    would ADD the ability for a control to twist the curve, which no
    control has today. It also needs maintainOffset on the
    control-to-deformer constraints in rig_tail_connect first - those are
    only safe because of that same one-CV-on-the-pivot property, and a
    weighted deformer loses it.

    Arguments
        rigname (str): Name of rig component
        driver_curve (str): Curve with clusters (low CV set)
        solver_curve (str): Curve used by ikHandle (one CV per joint)
        typ (str): Type identifier (TYPE_IK)
        jnt_pos (list): Joint rest positions the curves were built from. The
            rest correction needs them; without them this falls back to
            driving absolute positions, i.e. the old flattening behaviour.
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
    # The correction rides the base control, so it turns with the rig (see
    # the docstring). Without a basectrl there is nothing to ride and the
    # correction would break the moment the character turned, so skip it
    # rather than bake a world-locked offset.
    rest_cv = None
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    if jnt_pos and len(jnt_pos) == solver_num_cv and cmds.objExists(basectrl):
        rest_cv = jnt_pos
        driver_rest_cv = driver_curve_positions(jnt_pos)
        base_inv = cmds.getAttr(f'{basectrl}.worldInverseMatrix[0]')
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

        # Calculate parameter: Map solver CV position to driver curve parameter space
        if solver_num_cv == 1:
            # Single CV: Use middle of driver curve
            driver_param = driver_min_param + (driver_param_range * 0.5)
        else:
            # Multiple CVs: Distribute evenly across driver curve
            t = float(cv_i) / (solver_num_cv - 1) # 0 to 1
            driver_param = driver_min_param + (driver_param_range * t)

        cmds.setAttr(f'{poci}.parameter', driver_param)
        logger.trace(f'CV {cv_i}: parameter {driver_param:.3f}')

        src = poci
        src_attr = ('positionX', 'positionY', 'positionZ')
        if rest_cv is not None:
            # rest correction: what the low-CV driver curve cannot express
            # at this CV, held in the base control's local space so it turns
            # with the rig
            sample = rt_math.bspline_point(driver_rest_cv, driver_param)
            offset = [rest_cv[cv_i][k] - sample[k] for k in range(3)]
            local = rt_math.transform_vector(offset, base_inv)

            # pointMatrixMult, vectorMultiply: local offset back out to world
            restvec = f'{typ}_{rigname}_restfix_{cv_i:02d}_pointMatrixMult'
            if not cmds.objExists(restvec):
                cmds.createNode('pointMatrixMult', n=restvec, s=1, ss=1)
            cmds.setAttr(f'{restvec}.vectorMultiply', 1)
            cmds.setAttr(f'{restvec}.inPoint', *local, type='double3')
            rt_maya.ensure_connect(f'{basectrl}.worldMatrix[0]',
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
    Create spline IK handle with separated driver/solver curve system.

    Maya's ikSplineSolver automatically rebuilds the curve it's given,
    which changes curve parameters. This function:
    1. Creates ikHandle with temporary curve
    2. Replaces ikHandle's curve with our solver curve
    3. Returns components for further connection setup

    Note: Driver curve (with clusters) connects to solver curve (with ikHandle)
          via pointOnCurveInfo nodes in connect_driver_to_solver_curve()

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
    Rename spline IK handle components to match naming convention.

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
    Get existing spline handle information.
    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints to find ikHandle

    Return
        spline_list (list): [ikhandle, effector, curve]
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
    Create a cluster on specific CV(s) of a curve.

    Arguments
        cluster_names (list): [cluster_node_name, cluster_handle_name]
        curve (str): Target curve name
        cv_i (int or str): CV index (int) or CV range string (e.g. 'curve.cv[1:5]')

    Return
        cluster (list): [cluster_node, cluster_handle] from Maya command
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
    Create clusters on NURBS curve CVs for deformation control.

    Cluster placement strategy:
    - FK: Clusters only at first (CV 0) and last (CV N-1) CVs
      Provides end control while individual joints are controlled by FK hierarchy

    - IK: Clusters for each control CV + upvec clusters at ends
      - First two CVs (0, 1): Upvec clusters for twist control at base
      - Interior CVs: NUM_CTRL_IK control clusters for main deformation
      - Last CV (N-1): Already counted in upvec clusters

    Arguments
        rigname (str): Name of rig component
        curve (str): NURBS curve name
        typ (str): TYPE_FK or TYPE_IK
        show_handle (bool): Show Maya clusterHandles in viewport

    Return
        clusters (list): List of (cluster_node, cluster_handle) tuples
    '''
    if typ not in [rt_constants.TYPE_FK, rt_constants.TYPE_IK]:
        logger.error(f'Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')
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

    # Create clusters based on type
    if typ == rt_constants.TYPE_FK:
        # FK: Clusters at first and last CVs only
        for i in [0, num_cv-1]:
            cluster_node = rt_naming.fstr(rigname, rt_constants.CLUSTER, typ, i)
            cluster_handle = rt_naming.fstr(rigname, rt_constants.CLUSTER_HANDLE, typ, i)
            cluster = create_cluster([cluster_node, cluster_handle], curve, i)
            clusters.append(cluster)

    elif typ == rt_constants.TYPE_IK:
        # IK: Create upvec clusters at first and last CVs
        cluster_upv_bse = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV, typ, TAG='base')
        cluster_handle_bse = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE, typ, TAG='base')
        cluster_upv_end = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV, typ, TAG='end')
        cluster_handle_end = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE, typ, TAG='end')

        upv_bse = create_cluster([cluster_upv_bse, cluster_handle_bse], curve, 0)
        upv_end = create_cluster([cluster_upv_end, cluster_handle_end], curve, num_cv-1)
        clusters.append(upv_bse)
        clusters.append(upv_end)

        # IK: Control clusters for interior CVs (1 to num_cv-2)
        for i in range(1, num_cv-1):
            cluster_node = rt_naming.fstr(rigname, rt_constants.CLUSTER, typ, i)
            cluster_handle = rt_naming.fstr(rigname, rt_constants.CLUSTER_HANDLE, typ, i)
            cluster = create_cluster([cluster_node, cluster_handle], curve, i)
            clusters.append(cluster)

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
