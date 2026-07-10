'''
# rig_tail_curve.py
author: Daisy Jane @gnitemouse

Curve and Cluster methods for Rig Tail
'''

import maya.cmds as cmds
from logger_config import logger_setup
from rig_tail_constants import *
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_math as rt_mat
import rig_tail_joint as rt_jnt
import rig_tail_util as rt_utl  # Backward compatibility

logger = logger_setup(__name__)


# CURVE ================================================================

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
    curve = rt_nam.fstr(rigname, CURVE, typ, TAG=tag)
    logger.info(f"{rigname}: Create curve '{curve}'")
    logger.debug(f'jnt_pos len{len(jnt_pos)} {jnt_pos}')

    # Validate input
    if len(jnt_pos) < 2:
        logger.error(f'Need at least 2 joint positions, got {len(jnt_pos)}')
        return None

    if typ == TYPE_FK:
        # FK: Create curve matching joint positions exactly for skinCluster binding
        degree = min(3, len(jnt_pos)-1)
        curve = cmds.curve(n=curve, d=degree, p=jnt_pos)

    elif typ == TYPE_IK:
        if tag:
            # IK Spline solver curve: Duplicate end CVs for upvec clusters
            all_pos = [jnt_pos[0]] + jnt_pos + [jnt_pos[-1]]
            degree = min(3, len(all_pos)-1)
            curve = cmds.curve(n=curve, d=degree, p=all_pos)
        else:
            # IK driver curve: Evenly spaced control CVs + duplicate ends for upvec
            indices = rt_mat.linspace(0, len(jnt_pos)-1, NUM_CTRL_IK)
            all_pos = [jnt_pos[0]] + [jnt_pos[round(i)] for i in indices] + [jnt_pos[-1]]
            degree = min(3, len(all_pos)-1)
            curve = cmds.curve(n=curve, d=degree, p=all_pos)
    else:
        # Default: Simple curve
        degree = min(3, len(jnt_pos)-1)
        curve = cmds.curve(p=jnt_pos, d=degree, n=curve)

    # Clean up and organize
    rt_nam.rename_shapes(curve, typ='crv')
    rt_utl.set_curve_visibility(curve)
    cmds.delete(curve, ch=1) # Delete construction history

    spline_grp = rt_nam.fstr(rigname, SPLINE_GRP, typ)
    rt_utl.parent_to(curve, spline_grp)

    num_cv = cmds.getAttr(f'{curve}.controlPoints', size=True)
    logger.debug(f"Created curve '{curve}' with {num_cv} CVs, degree {degree}")
    return curve

def connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ):
    '''
    Connect driver curve to solver curve using pointOnCurveInfo sampling.

    This connection system:
    - Allows cluster-deformed driver curve to control the IK solver curve
    - Samples positions along driver curve at parameterized locations
    - Uses pointOnCurveInfo nodes to get world positions from driver curve
    - Feeds these positions directly to solver curve CV positions
    - Maintains real-time deformation transfer from clusters to IK solver

    Design rationale:
    - Driver curve can have any number of CVs (flexible for user control)
    - Solver curve has minimal CVs (optimized for IK solver)
    - No dependency on ikHandle's automatic curve rebuilding
    - Preserves user control over deformation resolution

    Arguments
        rigname (str): Name of rig component
        driver_curve (str): Curve with clusters (full CV set)
        solver_curve (str): Curve used by ikHandle (minimal CV set)
        typ (str): Type identifier (TYPE_IK)
    '''
    logger.debug(f"Connect driver curve '{driver_curve}' to solver curve '{solver_curve}'")

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

    logger.debug(f'Solver CVs: {solver_num_cv}, Driver CVs: {driver_num_cv}')
    logger.debug(f'Solver param range: {solver_min_param:.3f} to {solver_max_param:.3f}')
    logger.debug(f'Driver param range: {driver_min_param:.3f} to {driver_max_param:.3f}')

    # Create pointOnCurveInfo nodes for each solver curve CV
    for cv_i in range(solver_num_cv):
        # Create pointOnCurveInfo node to sample driver curve
        poci = f'{typ}_{rigname}_poci_{cv_i:02d}_pointOnCurveInfo'
        cmds.createNode('pointOnCurveInfo', n=poci, s=1, ss=1)
        cmds.connectAttr(f'{driver_shape}.worldSpace[0]', f'{poci}.inputCurve')

        # Calculate parameter: Map solver CV position to driver curve parameter space
        if solver_num_cv == 1:
            # Single CV: Use middle of driver curve
            driver_param = driver_min_param + (driver_param_range * 0.5)
        else:
            # Multiple CVs: Distribute evenly across driver curve
            t = float(cv_i) / (solver_num_cv - 1) # 0 to 1
            driver_param = driver_min_param + (driver_param_range * t)

        cmds.setAttr(f'{poci}.parameter', driver_param)
        logger.debug(f'CV {cv_i}: parameter {driver_param:.3f}')

        # Connect position to solver curve CV
        cmds.connectAttr(f'{poci}.positionX',
                         f'{solver_shape}.controlPoints[{cv_i}].xValue')
        cmds.connectAttr(f'{poci}.positionY',
                         f'{solver_shape}.controlPoints[{cv_i}].yValue')
        cmds.connectAttr(f'{poci}.positionZ',
                         f'{solver_shape}.controlPoints[{cv_i}].zValue')

    logger.debug(f'Created {solver_num_cv} pointOnCurveInfo connections')

    # Store driver curve reference on solver curve for cleanup and debugging
    if not cmds.attributeQuery('driver_curve', n=solver_curve, ex=1):
        cmds.addAttr(solver_curve, ln='driver_curve', dt='string')
        cmds.setAttr(f'{solver_curve}.driver_curve', driver_curve, type='string')


# SPLINE ===============================================================

def create_spline_handle(rigname, joints, curve, typ=TYPE_IK):
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

    # Clean up old spline components
    old_spline_list = get_spline_handle(rigname, joints) or []
    rt_mya.remove('curveInfo1')
    for obj in old_spline_list:
        rt_mya.remove(obj)

    # Create IK handle [ikhandle, effector, temp_curve]
    spline_list = cmds.ikHandle(n=rt_nam.fstr(rigname, SPLINE_HANDLE, typ),
                                sj=joints[0],
                                ee=joints[-1],
                                sol='ikSplineSolver')

    # Get curve CV information
    num_cv, _spans, _degree = rt_mya.get_num_cv(spline_list[2])
    logger.debug(f"ikHandle curve '{spline_list[2]}' has {num_cv} CVs, {_spans} spans, degree {_degree}")

    # Replace ikHandle's curve with our solver curve
    # Get shape nodes
    ikhandle_crvshape = cmds.listRelatives(spline_list[2], s=1, ni=1)[0]
    ikspline_crvshape = cmds.listRelatives(curve, s=1, ni=1)[0]
    # Disconnect temp curve from ikHandle
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
    spline_handle = rt_nam.fstr(rigname, SPLINE_HANDLE, typ)
    spline_effector = rt_nam.fstr(rigname, SPLINE_EFFECTOR, typ)

    # Rename components
    cmds.rename(spline_list[0], spline_handle)
    cmds.rename(spline_list[1], spline_effector)
    rt_mya.remove(spline_list[2]) # Delete temp curve

    # Organize
    rt_utl.parent_to(spline_list[0], rt_nam.fstr(rigname, SPLINE_GRP, typ))
    rt_nam.rename_shapes(curve, typ='crv')
    rt_utl.set_curve_visibility(curve)

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
    spline_handle = rt_nam.fstr(rigname, SPLINE_HANDLE, TYPE_IK)

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
        if not (rt_jnt.is_equal_joint(jl[0], joints[0]) and rt_jnt.is_equal_joint(jl[-1], joints[-2])):
            logger.warning(f'IK handle joint mismatch')
            return []

    logger.debug(f'({spline_handle}, {effector}, {curve})')
    return (spline_handle, effector, curve)

def search_spline_handle(rigname, joints=None, typ=TYPE_IK):
    '''
    Get existing spline handle components.
    Search by joint chain or by naming convention.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints to find ikHandle for (optional)
        typ (str): Rig type identifier (TYPE_IK)

    Return
        spline_list (list): [ikhandle, effector, curve] or None if not found
    '''
    if joints: # Find existing ikHandle from joints
        get_ikhandles = cmds.ls(typ='ikHandle')
        for ikhandle in get_ikhandles:
            # Check if it uses ikSplineSolver
            solver = cmds.ikHandle(ikhandle, q=1, sol=1)
            if solver != 'ikSplineSolver':
                continue
            # Check joint list that the handle manipulates
            jl = cmds.ikHandle(ikhandle, q=1, jl=1)
            if jl is None:
                logger.warning(f'IK handle {spline_handle} has no joint list')
                return None

            if rt_jnt.is_equal_joint(jl[0], joints[0]) and rt_jnt.is_equal_joint(jl[-1], joints[-2]):
                # Return IK handle with matching start/end joints
                spline_list = [ikhandle,
                               cmds.ikHandle(ikhandle, q=1, ee=1),
                               cmds.ikHandle(ikhandle, q=1, c=1).split('|')[-2]]
                logger.debug(f'Found ikHandle {spline_list}')
                return spline_list
        return None
    else: # Find ikHandle by name
        handle = rt_nam.fstr(rigname, SPLINE_HANDLE, typ)
        effector = rt_nam.fstr(rigname, SPLINE_EFFECTOR, typ)
        curve = rt_nam.fstr(rigname, CURVE, typ, TAG='spline')

        if cmds.objExists(handle) and cmds.objExists(effector) and cmds.objExists(curve):
            return [handle, effector, curve]

        if cmds.objExists(handle):
            effector = cmds.listConnections(f'{handle}.effector', s=1, d=0)[0]
            curve = cmds.listConnections(f'{handle}.curve', s=1, d=0)[0]
            return [handle, effector, curve]

        if cmds.objExists(effector):
            handles = cmds.listConnections(effector, type='ikHandle')
            for handle in handles:
                solver = cmds.ikHandle(handle, q=1, sol=1)
                if solver != 'ikSplineSolver':
                    continue
                curve = cmds.listConnections(f'{handle}.curve', s=1, d=0)[0]
                return [handle, effector, curve]

        if cmds.objExists(curve):
            get_ikhandles = cmds.ls(typ='ikHandle')
            for handle in get_ikhandles:
                solver = cmds.ikHandle(handle, q=1, sol=1)
                if solver != 'ikSplineSolver':
                    continue
                conns = cmds.listConnections(f'{handle}.curve', s=1, d=0) or []
                if conns and curve in conns:
                    effector = cmds.listConnections(f'{handle}.effector', s=1, d=0)[0]
                    return [handle, effector, curve]
        return None


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
        logger.debug(f'Cluster exists: {cluster_node}')
        return [cluster_node, cluster_handle]
    else:
        if isinstance(cv_i, int):
            cv_target = f'{curve}.cv[{cv_i}]'
        else:
            cv_target = cv_i
        logger.debug(f'Creating cluster: {cluster_node} on {cv_target}')
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
    if typ not in [TYPE_FK, TYPE_IK]:
        logger.error(f'Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')
        return []

    logger.info(f"Create clusters on curve '{curve}'")
    clusters = list()

    # Clean up old clusters
    crvshape = cmds.listRelatives(curve, shapes=True, noIntermediate=True) or []
    for shape in crvshape:
        deformers = cmds.listHistory(shape) or []
        for node in deformers:
            if cmds.objExists(node) and cmds.nodeType(node) == 'cluster':
                cmds.delete(node)

    # Get curve CV information
    num_cv, _spans, _degree = rt_mya.get_num_cv(curve)
    logger.debug(f"Curve '{curve}' has {num_cv} CVs, {_spans} spans, degree {_degree}")

    # Create clusters based on type
    if typ == TYPE_FK:
        # FK: Clusters at first and last CVs only
        for i in [0, num_cv-1]:
            cluster_node = rt_nam.fstr(rigname, CLUSTER, typ, i)
            cluster_handle = rt_nam.fstr(rigname, CLUSTER_HANDLE, typ, i)
            cluster = create_cluster([cluster_node, cluster_handle], curve, i)
            clusters.append(cluster)

    elif typ == TYPE_IK:
        # IK: Create upvec clusters at first and last CVs
        cluster_upv_bse = rt_nam.fstr(rigname, CLUSTER_UPV, typ, TAG='base')
        cluster_handle_bse = rt_nam.fstr(rigname, CLUSTER_UPV_HANDLE, typ, TAG='base')
        cluster_upv_end = rt_nam.fstr(rigname, CLUSTER_UPV, typ, TAG='end')
        cluster_handle_end = rt_nam.fstr(rigname, CLUSTER_UPV_HANDLE, typ, TAG='end')

        upv_bse = create_cluster([cluster_upv_bse, cluster_handle_bse], curve, 0)
        upv_end = create_cluster([cluster_upv_end, cluster_handle_end], curve, num_cv-1)
        clusters.append(upv_bse)
        clusters.append(upv_end)

        # IK: Control clusters for interior CVs (1 to num_cv-2)
        for i in range(1, num_cv-1):
            cluster_node = rt_nam.fstr(rigname, CLUSTER, typ, i)
            cluster_handle = rt_nam.fstr(rigname, CLUSTER_HANDLE, typ, i)
            cluster = create_cluster([cluster_node, cluster_handle], curve, i)
            clusters.append(cluster)

    # Organize under cluster group
    cluster_grp = rt_nam.fstr(rigname, CLUSTER_GRP, typ)
    for cluster_node, cluster_handle in clusters:
        rt_utl.parent_to(cluster_handle, cluster_grp)
        cmds.setAttr(f'{cluster_handle}.displayHandle', show_handle)
        logger.debug(f'[{cluster_node}, {cluster_handle}],')

    logger.debug(f'Created {len(clusters)} clusters for {typ}')
    cmds.select(clear=True)
    return clusters
