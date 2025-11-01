'''
# rig_tail_curve.py
author: Daisy Jane Lee @dayzl

Curve and Cluster methods for Rig Tail
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
from rig_tail_constants import *
from rig_tail_util import *

logger = logger_setup(__name__)


# CURVE ================================================================

def create_curve(rigname, jnt_pos, typ=''):
    '''
    Create NURBS curve from joint positions.
    Remove construction history to prevent length evaluation issues.

    For FK: Create curve with CV at each joint position
    For IK: Create curve with proper CV count for clusters

    The curve creation ensures that:
    - FK curves match joint positions exactly for accurate binding
    - IK curves have proper parameterization for spline IK deformation
    - Both maintain consistent CV indexing for cluster creation

    Arguments
        rigname (str): Name of rig component for curve naming
        jnt_pos (float tuple list): List of joint world positions
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return NURBS curve
        curve (str): Name of curve, or None if creation failed
    '''
    curve = fstr(rigname, CURVE, typ) # Curve
    if cmds.objExists(curve): # Delete if curve exists
        unbind_skincluster(curve, typ='crv')
        remove(curve)

    logger.info(f"Create curve '{curve}'")
    logger.debug(f"jnt_pos len{len(jnt_pos)} {jnt_pos}")

    # Create curve from joint positions
    if len(jnt_pos) < 2:
        logger.error(f'Need at least 2 joint positions, got {len(jnt_pos)}')

    # Determine curve degree based on number of points
    degree = min(3, len(jnt_pos) - 1)

    try: # Create curve
        if typ == TYPE_FK:
            # FK: Create curve matching joint positions exactly
            curve = cmds.curve(n=curve, d=degree, p=jnt_pos)

        elif typ == TYPE_IK:
            # IK: Create curve with upvec CVs at ends + NUM_CTRL_IK control CVs

            # Add upvec positions at start/end
            all_pos = [jnt_pos[0]] + jnt_pos + [jnt_pos[-1]]

            # Create curve with all control points
            degree = min(3, len(all_pos) - 1)
            curve = cmds.curve(n=curve, d=degree, p=all_pos)

        else: # Default simple curve
            curve = cmds.curve(p=jnt_pos, d=degree, n=curve)

        rename_shapes(curve, typ='crv')
        set_curve_visibility(curve)
        cmds.delete(curve, ch=1)

        spline_grp = fstr(rigname, SPLINE_GRP, typ)
        parent_to(curve, spline_grp)

        num_cv = cmds.getAttr(f'{curve}.controlPoints', size=True)
        logger.info(f"Created curve '{curve}' with {num_cv} CVs, degree {degree} from {len(jnt_pos)} joints")
        return curve

    except Exception as e:
        logger.error(f'Failed to create curve {curve}: {e}')
        return None

def connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ):
    '''
    Connect driver curve to solver curve using pointOnCurveInfo sampling.
    Allows the cluster-deformed driver curve to control the solver curve used by the ikHandle.

    The connection works by:
    - Sampling positions along the driver curve at parameterized locations
    - Using pointOnCurveInfo nodes to get world positions from the driver curve
    - Feeding these positions directly to the solver curve's CV positions
    - Maintaining real-time deformation transfer from clusters to IK solver

    - Driver curve can have any number of CVs
    - Solver curve has minimal CVs after rebuilding from create_spline_handle
    - No dependency on ikHandle's automatic curve rebuilding
    - Preserves user control over deformation resolution

    Arguments:
        rigname (str): name of rig component
        driver_curve (str): curve with clusters (full CV set)
        solver_curve (str): curve used by ikHandle (minimal CV set)
        typ (str): type identifier (TYPE_IK)
    '''
    logger.info(f"Connecting driver curve '{driver_curve}' to solver curve '{solver_curve}'")

    # Get curve shape nodes for connections
    driver_shape = cmds.listRelatives(driver_curve, s=1, ni=1)[0]
    solver_shape = cmds.listRelatives(solver_curve, s=1, ni=1)[0]

    # Get curve information for parameterization
    solver_num_cv = cmds.getAttr(f'{solver_curve}.controlPoints', size=True)
    driver_num_cv = cmds.getAttr(f'{driver_curve}.controlPoints', size=True)

    # Use solver curve's actual parameter range (after rebuilding)
    solver_min_param = cmds.getAttr(f'{solver_curve}.minValue')
    solver_max_param = cmds.getAttr(f'{solver_curve}.maxValue')
    solver_param_range = solver_max_param - solver_min_param

    # Driver curve parameter range for sampling
    driver_min_param = cmds.getAttr(f'{driver_curve}.minValue') 
    driver_max_param = cmds.getAttr(f'{driver_curve}.maxValue')
    driver_param_range = driver_max_param - driver_min_param

    logger.info(f"Solver CVs: {solver_num_cv}, Driver CVs: {driver_num_cv}")
    logger.info(f"Solver param range: {solver_min_param:.3f} to {solver_max_param:.3f}")
    logger.info(f"Driver param range: {driver_min_param:.3f} to {driver_max_param:.3f}")

    # Create pointOnCurveInfo nodes for each solver curve CV
    for cv_i in range(solver_num_cv):
        # Create pointOnCurveInfo node to sample driver curve
        poci = f'{typ}{rigname}_poci_{cv_i:02d}_pointOnCurveInfo'
        cmds.createNode('pointOnCurveInfo', n=poci, s=1, ss=1)
        cmds.connectAttr(f'{driver_shape}.worldSpace[0]', f'{poci}.inputCurve')

        # Calculate parameter based on solver curve's position along its length
        # Map solver CV position to driver curve parameter space
        if solver_num_cv == 1:
            # If single CV, use middle of driver curve
            driver_param = driver_min_param + (driver_param_range * 0.5)
        else: # If multiple CVs, distribute evenly across driver curve
            t = float(cv_i) / (solver_num_cv - 1) # 0 to 1
            driver_param = driver_min_param + (driver_param_range * t)

        cmds.setAttr(f'{poci}.parameter', driver_param)
        logger.debug(f"CV {cv_i}: parameter {driver_param:.3f}")

        # Connect position to solver curve CV
        cmds.connectAttr(f'{poci}.positionX', 
                         f'{solver_shape}.controlPoints[{cv_i}].xValue')
        cmds.connectAttr(f'{poci}.positionY', 
                         f'{solver_shape}.controlPoints[{cv_i}].yValue')
        cmds.connectAttr(f'{poci}.positionZ', 
                         f'{solver_shape}.controlPoints[{cv_i}].zValue')

    logger.info(f"Created {solver_num_cv} pointOnCurveInfo connections")

    # Store driver curve reference on solver curve for cleanup and debugging
    if not cmds.attributeQuery('driver_curve', n=solver_curve, ex=1):
        cmds.addAttr(solver_curve, ln='driver_curve', dt='string')
        cmds.setAttr(f'{solver_curve}.driver_curve', driver_curve, type='string')


# SPLINE ===============================================================

def create_spline_handle(rigname, joints, curve, typ=TYPE_IK):
    '''
    Create spline handle with separated driver/solver curve system.
    Driver curve is controlled by clusters, solver curve is driven by pointOnCurveInfo nodes.

    Note: Maya's ikHandle rebuilds the curve, which changes curve parameters.

    Arguments
        rigname (str): Name of rig component
        joints (list): List of joints
        curve (str): IK driver curve
        typ (str): Rig type identifier (TYPE_FK or TYPE_IK)

    Return
        spline_list (list): ikhandle object [ikhandle, effector, curve]
    '''

    if len(joints) < 3:
        logger.error(f"Must have more than 3 joints '{joints}'")
    if not cmds.objExists(curve): # Ensure curve exists for IK handle creation
        logger.error(f"Curve '{curve}' does not exist")

    # Clean up old spline list
    old_spline_list = get_spline_handle(rigname, joints) or []
    if cmds.objExists('curveInfo1'):
        cmds.delete('curveInfo1')
    for obj in old_spline_list:
        remove(obj)

    # IK handle object [ikhandle, effector, curve]
    spline_list = cmds.ikHandle(n=fstr(rigname, SPLINE_HANDLE, typ),
                                c=curve, fj=1,
                                sj=joints[0], ee=joints[-1],
                                sol='ikSplineSolver')

    spline_grp = fstr(rigname, SPLINE_GRP, typ)
    parent_to(spline_list[0], spline_grp)
    parent_to(spline_list[2], spline_grp)

    return rename_spline_handle(rigname, spline_list, typ)

def rename_spline_handle(rigname, spline_list, typ):
    logger.debug(f"spline_list {spline_list}")
    spline_handle = fstr(rigname, SPLINE_HANDLE, typ) # Handle
    spline_effector = fstr(rigname, SPLINE_EFFECTOR, typ) # Effector
    spline_curve = fstr(rigname, CURVE, typ, TAG='_spline') # Curve
    # Rename Handle, Effector, Curve
    cmds.rename(spline_list[0], spline_handle)
    cmds.rename(spline_list[1], spline_effector)
    cmds.rename(spline_list[2], spline_curve)
    rename_shapes(spline_curve, typ='crv')
    set_curve_visibility(spline_curve)
    return [spline_handle, spline_effector, spline_curve]

def get_spline_handle(rigname, joints=None, typ=TYPE_IK):
    '''
    Get spline handle components, return solver curve.
    Return
        spline_list (list): IK handle object [ikhandle, effector, curve]
    '''
    if joints: # Find existing IK handle from joints
        get_ikhandles = cmds.ls(typ='ikHandle')
        for ikhandle in get_ikhandles:
            # Check if it uses ikSplineSolver
            solver = cmds.ikHandle(ikhandle, q=1, sol=1)
            if solver != 'ikSplineSolver':
                continue
            # Check joint list that the handle manipulates
            jl = cmds.ikHandle(ikhandle, q=1, jl=1)
            if is_equal_joint(jl[0], joints[0]) and is_equal_joint(jl[-1], joints[-2]):
                # Return IK handle with matching start end joints
                spline_list = [ikhandle, cmds.ikHandle(ikhandle, q=1, ee=1),
                               cmds.ikHandle(ikhandle, q=1, c=1).split('|')[-2]]
                logger.info(f"Found IK Handle. spline_list {spline_list}")
                return spline_list
        return None

    else:
        handle = fstr(rigname, SPLINE_HANDLE, typ) # Handle
        effector = fstr(rigname, SPLINE_EFFECTOR, typ) # Effector
        curve = fstr(rigname, CURVE, typ, TAG='_spline') # Curve

        if cmds.objExists(handle) and cmds.objExists(effector) and cmds.objExists(curve):
            return [handle, effector, curve]

        if cmds.objExists(handle):
            effector = cmds.listConnections(f'{handle}.effector', s=1, d=0)[0]
            curve = cmds.listConnections(f'{handle}.curve', s=1, d=0)[0]
            return [handle, effector, curve]

        if cmds.objExists(effector):
            handles = cmds.listConnections(effector, type='ikHandle')
            for handle in handles:
                solver = cmds.ikHandle(ikhandle, q=1, sol=1)
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
    Create a cluster on a specific CV of a curve.

    Arguments
        cluster_names (list): [cluster_node_name, cluster_handle_name]
        curve (str): Target curve name
        cv_i (int): CV index to cluster

    Return
        cluster_result (list): [cluster_node, cluster_handle] from Maya command
    '''
    cluster_node, cluster_handle = cluster_names

    if cmds.objExists(cluster_node):
        logger.debug(f"Cluster exists: {cluster_node}")
        return [cluster_node, cluster_handle]
    else:
        logger.debug(f"Creating cluster: {cluster_node} on {curve}.cv[{cv_i}]")
        cluster = cmds.cluster(f"{curve}.cv[{cv_i}]", n=cluster_node, rel=False)
        return cluster

def create_clusters_on_curve(rigname, curve, typ, show_handle=False):
    '''
    Create clusters on NURBS curve, adding the clusters to CV positions.
    - FK: Create clusters only at first and last CVs (for end control)
    - IK: Create clusters for control CVs, skipping duplicate ends
    - Upvec clusters: Create at ends for twist control

    FK: Create clusters at first (0) and last (num_cv-1) CVs only
    - Provides end control while individual joints are controlled by FK hierarchy
    - For N num_cv, clusters at CV[0] and CV[N-1]

    IK: Create clusters for each control CV and upvec clusters at ends
    - Control clusters for main deformation
    - Upvec clusters at ends for twist control
    - For NUM_CTRL_IK controls, clusters at each CV

    Arguments
        rigname (str): Name of rig component
        curve (str): NURBS curve name
        typ (str): TYPE_FK or TYPE_IK
        show_handle (bool): Show Maya clusterHandles

    Return
        clusters (list): List of (cluster_node, cluster_handle) tuples
    '''
    if typ != TYPE_FK and typ != TYPE_IK:
        logger.error('Invalid TYPE {typ}. Choose TYPE_FK or TYPE_IK.')
    logger.info(f"Create clusters on curve '{curve}'")
    clusters = list()

    # Clean up old clusters
    crvshape = cmds.listRelatives(curve, shapes=True, noIntermediate=True) or []
    for shape in crvshape:
        deformers = cmds.listHistory(shape) or []
        for node in deformers: # Remove old clusters
            if cmds.objExists(node) and cmds.nodeType(node) == 'cluster':
                cmds.delete(node)

    # Get curve CV information
    num_cv, _spans, _degree = get_num_cv(curve)
    logger.info(f"Curve '{curve}' has {num_cv} CVs, {_spans} spans, degree {_degree}")

    # Create new clusters
    if typ == TYPE_FK: # Clusters at first and last CVs only
        for i in [0, num_cv-1]:
            cluster_node = fstr(rigname, CLUSTER, typ, i)
            cluster_handle = fstr(rigname, CLUSTER_HANDLE, typ, i)
            cluster = create_cluster([cluster_node, cluster_handle], curve, i)
            clusters.append(cluster)

    elif typ == TYPE_IK: # Upvec clusters at ends, control clusters in between
        cluster_upv_bse = fstr(rigname, CLUSTER_UPV, typ, TAG='_base')
        cluster_handle_bse = fstr(rigname, CLUSTER_UPV_HANDLE, typ, TAG='_base')
        cluster_upv_end = fstr(rigname, CLUSTER_UPV, typ, TAG='_end')
        cluster_handle_end = fstr(rigname, CLUSTER_UPV_HANDLE, typ, TAG='_end')
        upv_bse = create_cluster([cluster_upv_bse, cluster_handle_bse], curve, 0)
        upv_end = create_cluster([cluster_upv_end, cluster_handle_end], curve, num_cv-1)
        clusters.append(upv_bse)
        clusters.append(upv_end)
        # for NN in range(1, num_cv-1): # Control clusters for interior CVs
        #     cluster_node = fstr(rigname, CLUSTER, typ, NN)
        #     cluster_handle = fstr(rigname, CLUSTER_HANDLE, typ, NN)
        #     cluster = create_cluster([cluster_node, cluster_handle], curve, NN)
        #     clusters.append(cluster)
        # TODO
        # Count cluster indices. Sample positions evenly among joints
        indices = list(round(linspace(0, num_cv-2, NUM_CTRL_IK)))
        for i, count in enumerate(indices):
            cluster_node = fstr(rigname, CLUSTER, typ, i+1)
            cluster_handle = fstr(rigname, CLUSTER_HANDLE, typ, i+1)
            cluster = create_cluster([cluster_node, cluster_handle], curve, count+1)
            clusters.append(cluster)

    # Organize under cluster group
    cluster_grp = fstr(rigname, CLUSTER_GRP, typ)
    for cluster_node, cluster_handle in clusters:
        parent_to(cluster_handle, cluster_grp)
        cmds.setAttr(f"{cluster_handle}.displayHandle", show_handle)
        logger.info(f"[{cluster_node}, {cluster_handle}],")

    logger.info(f"Created {len(clusters)} clusters for {typ}")
    cmds.select(clear=True) # Deselect all
    return clusters
