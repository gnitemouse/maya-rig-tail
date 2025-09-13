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
            curve = cmds.curve(p=jnt_pos, d=degree, n=curve)

        elif typ == TYPE_IK:
            # IK: Create curve with upvec CVs at ends + NUM_CTRL_IK control CVs

            # Sample positions evenly along joint chain
            indices = list(linspace(0, len(jnt_pos)-1, NUM_CTRL_IK))
            sampled_pos = [jnt_pos[round(i)] for i in indices]

            # Create upvec positions slightly extended beyond joint chain
            # This provides clean advanced twist calculation at extremes
            first_pos = jnt_pos[0]
            last_pos = jnt_pos[-1]

            # Calculate extension vector (2% of total chain length)
            chain_vec = [last_pos[i] - first_pos[i] for i in range(3)]
            chain_len = sum(v**2 for v in chain_vec) ** 0.5
            if chain_len > 0:
                extension_factor = 0.02
                norm_vec = [v / chain_len for v in chain_vec]
                extension = [v * chain_len * extension_factor for v in norm_vec]
                upvec_srt = [first_pos[i] - extension[i] for i in range(3)]
                upvec_end = [last_pos[i] + extension[i] for i in range(3)]
            else:
                upvec_srt = first_pos
                upvec_end = last_pos
            all_pos = [upvec_srt] + sampled_pos + [upvec_end]

            # Create curve with all control points
            degree = min(3, len(all_pos) - 1)
            curve = cmds.curve(n=curve, d=degree, p=all_pos)
            # cmds.rebuildCurve(curve,
            #                   ch=0,   # No construction history
            #                   rpo=1,  # Replace original
            #                   rt=0,   # Uniform parameterization
            #                   end=1,  # Keep ends
            #                   kr=0,   # Keep original range
            #                   kcp=1,  # Keep control points
            #                   kep=1,  # Keep end points
            #                   kt=0,   # Don't keep tangents
            #                   d=degree)

        else: # Default simple curve
            curve = cmds.curve(p=jnt_pos, d=degree, n=curve)

        rename_shapes(curve, typ='crv')
        set_curve_visibility(curve)
        cmds.delete(curve, ch=1)
        num_cv = cmds.getAttr(f'{curve}.controlPoints', size=True)
        logger.info(f"Created curve '{curve}' with {num_cv} CVs, degree {degree} from {len(jnt_pos)} joints")
        return curve

    except Exception as e:
        logger.error(f'Failed to create curve {curve}: {e}')
        return None


def connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ):
    '''
    Create pointOnCurveInfo nodes to drive solver curve from driver curve.
    Driver curve has clusters at CVs, while solver curve is minimal for ikHandle.

    Arguments
        rigname (str): Rig component name
        driver_curve (str): Curve controlled by clusters
        solver_curve (str): Curve used by ikHandle
        typ (str): Rig type identifier (TYPE_FK, TYPE_IK)
    '''
    logger.info(f"Setting up driver-solver connection: {driver_curve} -> {solver_curve}")

    # Get curve information
    driver_shape = cmds.listRelatives(driver_curve, s=1, ni=1)[0]
    solver_shape = cmds.listRelatives(solver_curve, s=1, ni=1)[0]

    # Get number of CVs on solver curve
    solver_num_cv = cmds.getAttr(f'{solver_curve}.controlPoints', size=True)

    # Create group for pointOnCurveInfo nodes organization
    spline_grp = fstr(rigname, SPLINE_GRP, typ)
    poci_grp = fstr(rigname, '{rigname}_poci{GRP}', typ)
    if not cmds.objExists(poci_grp):
        cmds.group(em=True, n=poci_grp)
        if cmds.objExists(spline_grp):
            parent_to(poci_grp, spline_grp)

    # Get driver curve spans for parameter calculation
    driver_spans = cmds.getAttr(f'{driver_curve}.spans')

    # Create pointOnCurveInfo nodes for each solver CV
    poci_nodes = []
    for i in range(solver_num_cv):
        poci_name = f'{typ}{rigname}_poci_{i:02d}'
        if cmds.objExists(poci_name):
            cmds.delete(poci_name)

        poci = cmds.createNode('pointOnCurveInfo', n=poci_name)
        poci_nodes.append(poci)

        # Connect driver curve to pointOnCurveInfo
        cmds.connectAttr(f'{driver_shape}.worldSpace[0]', f'{poci}.inputCurve')

        # Calculate parameter position along driver curve
        if solver_num_cv == 1:
            param = 0.5 * driver_spans  # middle
        else:
            # Evenly distribute parameters from 0 to spans
            param = (float(i) / (solver_num_cv - 1)) * driver_spans

        cmds.setAttr(f'{poci}.parameter', param)

        # Connect position to solver curve CV
        # We need to use a decomposeMatrix to extract position
        decomp = f'{typ}{rigname}_decomp_{i:02d}_decomposeMatrix'
        cmds.createNode('decomposeMatrix', n=decomp, s=1, ss=1)
        # Create matrix setup to get world position
        fourfour = f'{typ}{rigname}_4x4_{i:02d}_fourByFourMatrix'
        cmds.createNode('fourByFourMatrix', n=fourfour, s=1, ss=1)

        # Connect position components to matrix
        cmds.connectAttr(f'{poci}.positionX', f'{fourfour}.in30')
        cmds.connectAttr(f'{poci}.positionY', f'{fourfour}.in31')
        cmds.connectAttr(f'{poci}.positionZ', f'{fourfour}.in32')
        cmds.setAttr(f'{fourfour}.in33', 1.0) # homogeneous coordinate

        # Decompose matrix to get translation
        cmds.connectAttr(f'{fourfour}.output', f'{decomp}.inputMatrix')

        # Connect to solver curve CV
        cmds.connectAttr(f'{decomp}.outputTranslateX', f'{solver_shape}.controlPoints[{i}].xValue')
        cmds.connectAttr(f'{decomp}.outputTranslateY', f'{solver_shape}.controlPoints[{i}].yValue')
        cmds.connectAttr(f'{decomp}.outputTranslateZ', f'{solver_shape}.controlPoints[{i}].zValue')

    logger.info(f"Created {len(poci_nodes)} pointOnCurveInfo connections")

    # Store reference to driver curve on solver curve for cleanup
    if not cmds.attributeQuery('driver_curve', n=solver_curve, ex=1):
        cmds.addAttr(solver_curve, ln='driver_curve', dt='string')
        cmds.setAttr(f'{solver_curve}.driver_curve', driver_curve, type='string')

# SPLINE ===============================================================

def create_spline_handle(rigname, joints, curve, typ=TYPE_IK):
    '''
    Create spline handle with separated driver/solver curve system.
    Driver curve is controlled by clusters, solver curve is driven by pointOnCurveInfo nodes.

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

    # Clean up old components
    spline_list = get_spline_handle(rigname, joints, existing=True)
    for obj in spline_list:
        logger.info(f"Deleting '{obj}'")
        remove(obj) # Delete spline component
    if cmds.objExists('curveInfo1'):
        cmds.delete('curveInfo1')

    # TODO
    # num_cv, spans, degree = get_num_cv(curve)
    # logger.info(f"Curve '{curve}' has {num_cv} CVs, {spans} spans, degree {degree}")

    # IK handle object [ikhandle, effector, curve]
    spline_list = cmds.ikHandle(n=fstr(rigname, SPLINE_HANDLE),
                                c=curve, fj=1,
                                sj=joints[0], ee=joints[-1],
                                sol='ikSplineSolver')

    return rename_spline_handle(rigname, curve, spline_list, typ)

def rename_spline_handle(rigname, curve, spline_list, typ=TYPE_IK):
    spline_handle = fstr(rigname, SPLINE_HANDLE) # Handle
    spline_effector = fstr(rigname, SPLINE_EFFECTOR) # Effector
    # Rename Handle, Effector, Curve
    logger.debug(f"Original spline_list {spline_list}")
    cmds.rename(spline_list[0], spline_handle)
    cmds.rename(spline_list[1], spline_effector)
    cmds.rename(spline_list[2], curve)
    rename_shapes(curve, typ='crv')
    set_curve_visibility(curve)
    spline_list = [spline_handle, spline_effector, curve]
    logger.debug(f"Renamed spline_list {spline_list}")
    return spline_list

def get_spline_handle(rigname, joints=None, existing=False):
    '''
    Get spline handle components, return solver curve.
    Return
        spline_list (list): IK handle object [ikhandle, effector, curve]
    '''
    spline_handle = fstr(rigname, SPLINE_HANDLE) # Handle
    spline_effector = fstr(rigname, SPLINE_EFFECTOR) # Effector
    curve = fstr(rigname, CURVE, TYPE_IK) # Curve

    if existing:
        # Get existing IK handle if it already exists
        ex_ikhandles = cmds.ls(typ='ikHandle')
        for i, ikh in enumerate(ex_ikhandles):
            if i==0 and not joints:
                # Return first find if joints not provided
                return [ikh, cmds.ikHandle(ikh, q=1, ee=1),
                        cmds.ikHandle(ikh, q=1, c=1).split('|')[-2]]
            jl = cmds.ikHandle(ikh, q=1, jl=1)
            if is_equal_joint(jl[0], joints[0]) and is_equal_joint(jl[-1], joints[-2]):
                # Return IK handle with matching start end joints
                spline_list = [ikh, cmds.ikHandle(ikh, q=1, ee=1),
                               cmds.ikHandle(ikh, q=1, c=1).split('|')[-2]]
                logger.info(f"Found existing match {spline_list}")
                return spline_list
        logger.info(f"No existing spline handles match")
        return None
    else:
        if not cmds.objExists(spline_handle):
            spline_handle = None
        if not cmds.objExists(spline_effector):
            spline_effector = None
        if not cmds.objExists(curve):
            curve = None
        return [spline_handle, spline_effector, curve]


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
        for NN in [0, num_cv-1]:
            cluster_node = fstr(rigname, CLUSTER, typ, NN)
            cluster_handle = fstr(rigname, CLUSTER_HANDLE, typ, NN)
            cluster = create_cluster([cluster_node, cluster_handle], curve, NN)
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
        for NN in range(1, num_cv-1): # Control clusters for interior CVs
            cluster_node = fstr(rigname, CLUSTER, typ, NN)
            cluster_handle = fstr(rigname, CLUSTER_HANDLE, typ, NN)
            cluster = create_cluster([cluster_node, cluster_handle], curve, NN)
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
