'''
# rig_tail.py
author: Daisy Jane Lee @dayzl

Rig a stretchy tail with FK/IK modes.
IK modes include IK, Float, and Spline IK controls.
FK mode provides a variable FK solution with sliding controls for tapered joint rotations.

Run in Maya Script Editor (Python):
import dayz_rig_tail as rt
import importlib as il
il.reload(rt)
rt.rig_tail_test('tail', start_jnt='FK_tail_00_jnt', end_jnt='FK_tail_36_jnt', root='tail_spline_grp')
rt.main()

By default,
Assumes the following structure or naming convention:
    controls                       (CONTROL_GRP)
      - {rigname}_ctrl_grp         (BASECTRL_GRP)
      -- {rigname}_base_ctrl       (BASECTRL)
      --- FK_{rigname}_root_grp    (CTRLROOT_GRP)
      ---- {rigname}_##_ctrl_grp   (CTRL_GRP)
      ----- {rigname}_##_ctrl      (CTRL)
      ---- etc.
    FK_skeleton                    (fk_SKELETON_GRP)
      - FK_{rigname}_grp           (fk_GRP)
      -- FK_{rigname}_##_01_sdk    (first_SDK_GRP)
      --- FK_{rigname}_##_02_sdk   ( ... SDK_GRP)
      ---- FK_{rigname}_##_02_sdk  (last_SDK_GRP)
      ----- FK_{rigname}_##_jnt    (JNT)
      ------ etc.
Naming convention can be changed under Naming Template in rig_tail_constants.
Rig components {rigname}s can be changed under RIGPARTS in rig_tail_constants.

'''
import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import rig_tail_constants as cst
from rig_tail_constants import *
import rig_tail_setup as rt_set
import rig_tail_util as rt_utl
import rig_tail_control as rt_ctl
import rig_tail_curve as rt_crv
import rig_tail_fk as rt_fk
import rig_tail_stretch as rt_str
import rig_tail_connect as rt_con
import re

logger = logger_setup(__name__)


# BUILD / RIG TAIL CORE ================================================

def build_rig_tail(rigname, fk=True, ik=True):
    # Cleanup existing basectrl
    basectrl_grp = rt_utl.fstr(rigname, BASECTRL_GRP)
    basectrl = rt_utl.fstr(rigname, BASECTRL)
    # Disconnect skeleton
    rt_con.disconnect_skeleton(rigname, fk, ik)
    # Remove existing controls under basectrl
    rt_utl.remove(basectrl_grp)
    rt_utl.remove(basectrl)
    # Build new FK IK tail
    if fk:
        rig_tail_fk(rigname)
    if ik:
        rig_tail_ik(rigname)

def rig_tail_fk(rigname, typ=TYPE_FK):
    '''
    Create FK tail using variable FK method.

    Arguments
        rigname (str): name of rig part

    Result
        Create FK tail from start_jnt to end_jnt.
        If end_jnt is not provided, this function will read
        the joint hierarchy until the end of the joint chain.
        Create NURBS curve and bind to given joints.
        Create N controls on FK tail.
        Create N sdk groups above FK joints.
    '''
    logger.info('-----------------------------------------------------')
    logger.info(f"START creating FK tail '{rigname}'")
    logger.debug(f"joints {cst.JOINTS_FK[rigname]}")

    joints = cst.JOINTS_FK[rigname]
    jnt_pos = rt_utl.get_joint_position_from_list(joints) # Joint position
    # Label joint positions
    rt_utl.set_joint_attributes(joints)

    # Create new curve or get existing curve
    curve_fk = rt_crv.create_curve(rigname, jnt_pos, typ) # Curve FK
    rt_utl.set_curve_visibility(curve_fk)

    # Create group for spline and clusters
    rig_systems_grp = rt_utl.fstr('', RIG_SYSTEMS_GRP)
    spline_grp_fk = rt_utl.fstr(rigname, SPLINE_GRP, typ)
    cluster_grp_fk = rt_utl.fstr(rigname, CLUSTER_GRP, typ)
    scale_grp = rt_utl.fstr(rigname, SCALE_GRP)
    rt_utl.create_group(spline_grp_fk)
    rt_utl.create_group(cluster_grp_fk)
    rt_utl.create_group(scale_grp)
    rt_utl.parent_to(spline_grp_fk, rig_systems_grp)
    rt_utl.parent_to(cluster_grp_fk, spline_grp_fk)
    rt_utl.parent_to(scale_grp, rig_systems_grp)
    # Create clusters on curve
    clusters = rt_crv.create_clusters_on_curve(rigname, curve_fk, typ)

    # Create controls and set attributes
    varfk_controls = rt_ctl.create_controls_fk(rigname, joints, jnt_pos)
    rt_ctl.set_control_attributes(varfk_controls, joints)
    # Create curveInfo and pointOnCurveInfo nodes to parameterize control position
    rt_fk.set_curveinfo_fk(rigname, curve_fk, varfk_controls)

    # Detect orientation of curve_fk
    #orient = axis_vector_colinearity(start_jnt, get_local_vec(start_jnt, end_jnt))
    # Create nurbs surface to match joint chain
    # nsurface = create_surface_from_curve(rigname, curve_fk, orient)

    # Create sdk groups on joint chain
    fkjnt_grp = rt_fk.create_sdk_groups(rigname, joints)
    sdk_groups = rt_fk.get_sdk_groups(joints)

    # Falloff Rotation
    for n in range(NUM_CTRL_FK):
       rt_fk.falloff_rotation(rigname, n, joints, sdk_groups[n])

    # Bind curve to joints
    logger.info(f"Bind Curve '{curve_fk}' to FK joints")
    cmds.select(clear=True) # Deselect all
    rt_utl.bind_skincluster(joints, curve_fk, f"{curve_fk}_skinCluster")

    # Spline FK. Squash and Stretch
    curve_scale_fk = rt_str.build_squash_stretch(rigname, curve_fk, joints, typ)

    logger.info(f"DONE creating FK tail '{rigname}'")

def rig_tail_ik(rigname, typ=TYPE_IK):
    '''
    Create IK tail

    Driver curve (curve_ik):
    - Contains all CVs needed for cluster control (NUM_CTRL_IK + 2 upvec CVs)
    - Deformed by clusters attached to spline controls
    - Never used directly by ikHandle

    Solver curve (curve_ik_solver):
    - Minimal CVs for efficient IK calculation (typically 3-4 CVs)
    - Driven by pointOnCurveInfo nodes sampling from driver curve
    - Used by ikHandle for actual joint deformation
    '''
    logger.info('-----------------------------------------------------')
    logger.info(f"START creating IK tail '{rigname}'")
    logger.debug(f"joints {cst.JOINTS_IK[rigname]}")

    joints = cst.JOINTS_IK[rigname]
    jnt_pos = rt_utl.get_joint_position_from_list(joints)

    # Create group for spline and clusters
    rig_systems_grp = rt_utl.fstr('', RIG_SYSTEMS_GRP)
    spline_grp_ik = rt_utl.fstr(rigname, SPLINE_GRP, typ)
    cluster_grp_ik = rt_utl.fstr(rigname, CLUSTER_GRP, typ)
    scale_grp = rt_utl.fstr(rigname, SCALE_GRP)
    rt_utl.create_group(spline_grp_ik)
    rt_utl.create_group(cluster_grp_ik)
    rt_utl.create_group(scale_grp)
    rt_utl.parent_to(spline_grp_ik, rig_systems_grp)
    rt_utl.parent_to(cluster_grp_ik, spline_grp_ik)
    rt_utl.parent_to(scale_grp, rig_systems_grp)

    # Create driver curve (curve_ik) with CVs for cluster control
    curve_ik = rt_crv.create_curve(rigname, jnt_pos, typ) # Curve IK
    logger.info(f"Created driver curve '{curve_ik}'")

    # Create solver curve (curve_ik_solver) used by ikHandle
    curve_ik_solver = rt_utl.fstr(rigname, CURVE, typ, TAG='_spline')
    curve_ik_solver = cmds.duplicate(curve_ik, n=curve_ik_solver, rc=1)[0]
    logger.info(f"Created solver curve '{curve_ik_solver}'")

    # Create clusters on driver curve
    clusters = rt_crv.create_clusters_on_curve(rigname, curve_ik, typ)

    # Set up driver-to-solver connection using pointOnCurveInfo nodes
    # Allowing the cluster-deformed driver curve to control the solver curve
    rt_crv.connect_driver_to_solver_curve(rigname, curve_ik, curve_ik_solver, typ)

    # Create spline IK handle using the solver curve
    # spline_list (list): ikhandle object [ikhandle, effector, curve]
    spline_list = rt_crv.create_spline_handle(rigname, joints, curve_ik_solver, typ)

    # Build controls and control groups
    # ik_controls (dict): control type (ik, float, spline, upvec) -> list of controls
    # ik_ctrlgrps (dict): ctrl grp typ (ik, float, spline, upvec) -> list of ctrl grps
    ik_controls, ik_ctrlgrps = rt_ctl.create_controls_ik(rigname, joints, clusters)

    # Spline IK. Squash and Stretch
    curve_scale_ik = rt_str.build_squash_stretch(rigname, curve_ik, joints, typ)

    # Get initial start and end vectors for advanced twist
    srt_vec = cmds.xform(joints[0], q=1, ws=1, m=1) [8:11]
    end_vec = cmds.xform(joints[-1], q=1, ws=1, m=1) [8:11]
    rt_str.build_advanced_twist(spline_list[0], clusters[0][1], clusters[-1][1],
                         srt_vec, end_vec)

    logger.info(f"DONE creating IK tail '{rigname}'")


# IK SOLVER ============================================================

def create_ik_solver_curve(rigname, jnt_pos, typ):
    '''
    Create a minimal solver curve for IK efficiency.
    This curve will be used by the ikHandle and driven by pointOnCurveInfo nodes.

    The solver curve needs only enough CVs to span the joint chain effectively:
    - Typically 3-4 CVs are sufficient for most joint chains
    - Uses strategic positioning: start, middle point(s), and end
    - Maintains cubic degree for smooth deformation

    Arguments:
        rigname (str): name of rig component
        jnt_pos (list): list of joint world positions
        typ (str): type identifier (TYPE_IK)

    Returns:
        str: name of created solver curve
    '''
    solver_curve = rt_utl.fstr(rigname, CURVE, typ, TAG='_solver')

    # Remove existing solver curve
    if cmds.objExists(solver_curve):
        rt_utl.unbind_skincluster(solver_curve, typ='crv')
        rt_utl.remove(solver_curve)

    logger.info(f"Creating solver curve '{solver_curve}' from {len(jnt_pos)} joint positions")

    # Calculate strategic CV positions for optimal IK deformation
    # Use start, one or two middle points, and end for minimal spanning
    num_joints = len(jnt_pos)

    if num_joints <= 3:
        # For short chains, use all joint positions
        solver_positions = jnt_pos[:]
    elif num_joints <= 6:
        # For medium chains, use start, middle, end
        mid_idx = num_joints // 2
        solver_positions = [
            jnt_pos[0],      # start
            jnt_pos[mid_idx], # middle
            jnt_pos[-1]       # end
        ]
    else:
        # For long chains, use start, two middle points, end
        quarter_idx = num_joints // 4
        three_quarter_idx = 3 * num_joints // 4
        solver_positions = [
            jnt_pos[0],              # start
            jnt_pos[quarter_idx],    # first quarter
            jnt_pos[three_quarter_idx], # third quarter  
            jnt_pos[-1]              # end
        ]

    # Create solver curve with calculated positions
    degree = min(3, len(solver_positions) - 1)
    solver_curve = cmds.curve(p=solver_positions, d=degree, n=solver_curve)
    cmds.delete(solver_curve, ch=True)  # Remove construction history

    num_cv = cmds.getAttr(f'{solver_curve}.controlPoints', size=True)
    logger.info(f"Created solver curve '{solver_curve}' with {num_cv} CVs, degree {degree}")

    return solver_curve

def connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ):
    '''
    Connect driver curve to solver curve using pointOnCurveInfo sampling.

    This system allows the cluster-deformed driver curve to control the solver curve
    used by the ikHandle. The connection works by:

    1. Sampling positions along the driver curve at parameterized locations
    2. Using pointOnCurveInfo nodes to get world positions from the driver curve  
    3. Feeding these positions directly to the solver curve's CV positions
    4. Maintaining real-time deformation transfer from clusters to IK solver

    Key benefits:
    - Driver curve can have any number of CVs for detailed control
    - Solver curve maintains minimal CVs for IK efficiency  
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
    driver_spans = cmds.getAttr(f'{driver_curve}.spans')
    solver_num_cv = cmds.getAttr(f'{solver_curve}.controlPoints', size=True)

    logger.debug(f"Driver curve spans: {driver_spans}, Solver CVs: {solver_num_cv}")

    # Create organization group for pointOnCurveInfo nodes
    spline_grp = rt_utl.fstr(rigname, SPLINE_GRP, typ)
    poci_grp = rt_utl.fstr(rigname, '{rigname}_poci{GRP}', typ)
    rt_utl.create_group(poci_grp)
    if cmds.objExists(spline_grp):
        rt_utl.parent_to(poci_grp, spline_grp)

    # Create pointOnCurveInfo nodes for each solver curve CV
    for cv_index in range(solver_num_cv):
        # Create unique names for each CV connection
        poci_name = f'{typ}{rigname}_poci_{cv_index:02d}'
        matrix_name = f'{typ}{rigname}_matrix_{cv_index:02d}' 
        decomp_name = f'{typ}{rigname}_decomp_{cv_index:02d}'

        # Clean up existing nodes
        for node in [poci_name, matrix_name, decomp_name]:
            if cmds.objExists(node):
                cmds.delete(node)

        # Create pointOnCurveInfo node to sample driver curve
        poci = cmds.createNode('pointOnCurveInfo', n=poci_name)
        cmds.connectAttr(f'{driver_shape}.worldSpace[0]', f'{poci}.inputCurve')

        # Calculate parameter position along driver curve
        # Evenly distribute sample points from parameter 0 to spans
        if solver_num_cv == 1:
            param = 0.5 * driver_spans  # Single CV at middle
        else:
            # Multiple CVs distributed evenly across parameter range
            param = (float(cv_index) / (solver_num_cv - 1)) * driver_spans

        cmds.setAttr(f'{poci}.parameter', param)
        logger.debug(f"CV {cv_index}: parameter {param:.3f}")

        # Create matrix nodes to transfer position data
        # fourByFourMatrix is needed to properly format position data
        matrix = cmds.createNode('fourByFourMatrix', n=matrix_name)
        decomp = cmds.createNode('decomposeMatrix', n=decomp_name)

        # Connect pointOnCurveInfo position to matrix
        cmds.connectAttr(f'{poci}.positionX', f'{matrix}.in30')
        cmds.connectAttr(f'{poci}.positionY', f'{matrix}.in31') 
        cmds.connectAttr(f'{poci}.positionZ', f'{matrix}.in32')
        cmds.setAttr(f'{matrix}.in33', 1.0)  # homogeneous coordinate

        # Decompose matrix to extract translation
        cmds.connectAttr(f'{matrix}.output', f'{decomp}.inputMatrix')

        # Connect decomposed translation directly to solver curve CV
        cmds.connectAttr(f'{decomp}.outputTranslateX', 
                        f'{solver_shape}.controlPoints[{cv_index}].xValue')
        cmds.connectAttr(f'{decomp}.outputTranslateY', 
                        f'{solver_shape}.controlPoints[{cv_index}].yValue')
        cmds.connectAttr(f'{decomp}.outputTranslateZ', 
                        f'{solver_shape}.controlPoints[{cv_index}].zValue')

    logger.info(f"Created {solver_num_cv} pointOnCurveInfo connections")

    # Store driver curve reference on solver curve for cleanup and debugging
    if not cmds.attributeQuery('driver_curve', n=solver_curve, ex=1):
        cmds.addAttr(solver_curve, ln='driver_curve', dt='string')
        cmds.setAttr(f'{solver_curve}.driver_curve', driver_curve, type='string')


# RUN: RIG TAIL ========================================================

def rig_tail_test(rigname, start_jnt=None, end_jnt=None,
                  root=None, connect=True, fk=True, ik=True):
    '''
    Test rig_tail_fk on single FK joint chain.
    Example
        rt.rig_tail_test('tail', root='tail_spline_grp', connect=False, fk=True, ik=True)
        rt.rig_tail_test('tail', root='tail_spline_grp', connect=True)
        rt.rig_tail_test('tail', root='tail_spline_grp', connect=True, fk=False, ik=True)
    '''
    cst.RIGPARTS = [rigname]
    rt_set.set_joints(rigname, start_jnt, end_jnt)

    rt_set.set_root(root)
    rt_set.setup_rig_components(fk, ik)
    build_rig_tail(rigname, fk, ik)
    # if connect:
    #     rt_con.connect_rig_tail(fk, ik)

def rig_tail_selected(root=None, connect=True, fk=True, ik=True):
    '''
    Run rig_tail() on user selected joints.
    Select all top joints on FK joint chains.
    The selected joints will be the start_jnt.
    Rigname and end_jnt are auto detected.
    '''
    cst.RIGPARTS = list()
    selected = cmds.ls(sl=True)
    if not selected:
        logger.error('Please select joint to create tail rig.')

    rt_set.set_root(root)
    for jnt in selected:
        if cmds.objectType(jnt, i='joint'):
            rigname = get_rigname(jnt, JOINT, underscore=True)
            cst.RIGPARTS.append(rigname)
            rt_set.set_joints(rigname, start_jnt=jnt, end_jnt=None)
        else:
            logger.warning(f"Selected object '{jnt}' is not a joint.")

    rt_set.setup_rig_components(fk, ik)
    for rigname in cst.RIGPARTS:
        build_rig_tail(rigname, fk, ik)
    if connect:
        rt_con.connect_rig_tail(fk, ik)

def main(root=None, connect=True, fk=True, ik=True):
    '''
    Run rig_tail() on all components in RIGPARTS.

    Arguments
        root (str): Name of rig; name of main group
        connect (bool): Connect IKFK attributes and constraints
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    for rigname in cst.RIGPARTS:
        rt_set.set_joints(rigname)
    rt_set.set_root(root)
    rt_set.setup_rig_components(fk, ik)
    for rigname in cst.RIGPARTS:
        build_rig_tail(rigname, fk, ik)
    if connect:
        rt_con.connect_rig_tail(fk, ik)
