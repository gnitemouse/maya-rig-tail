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

def build_rig_tail(rigname, fk=True, ik=True, stretchy=True):
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
        rig_tail_fk(rigname, stretchy)
    if ik:
        rig_tail_ik(rigname, stretchy)

def rig_tail_fk(rigname, stretchy=True, typ=TYPE_FK):
    '''
    Create FK tail using variable FK method.

    Arguments
        rigname (str): Name of rig component
        stretchy (bool): Whether to build stretchy
        typ (str): Rig type identifier (TYPE_FK, TYPE_IK)

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

    # Create new curve or get existing curve
    curve_fk = rt_crv.create_curve(rigname, jnt_pos, typ) # Curve FK
    rt_utl.set_curve_visibility(curve_fk)

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

    if stretchy:
        # Spline FK. Squash and Stretch
        rt_str.build_squash_stretch(rigname, curve_fk, joints, typ)

    logger.info(f"DONE creating FK tail '{rigname}'")

def rig_tail_ik(rigname, stretchy=True, typ=TYPE_IK):
    '''
    Create IK tail

    Driver curve (driver_curve):
    - Contains all CVs needed for cluster control (NUM_CTRL_IK + 2 upvec CVs)
    - Deformed by clusters attached to spline controls
    - Never used directly by ikHandle

    Solver curve (solver_curve):
    - Minimal CVs for efficient IK calculation (typically 3-4 CVs)
    - Driven by pointOnCurveInfo nodes sampling from driver curve
    - Used by ikHandle for actual joint deformation

    Arguments
        rigname (str): Name of rig component
        stretchy (bool): Whether to build stretchy
        typ (str): Rig type identifier (TYPE_FK, TYPE_IK)
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

    # Create driver curve with CVs for cluster control
    driver_curve = rt_crv.create_curve(rigname, jnt_pos, typ) # Curve IK

    # Create solver curve used by ikHandle
    solver_curve = rt_utl.fstr(rigname, CURVE, typ, TAG='_spline')
    solver_curve = cmds.duplicate(driver_curve, n=solver_curve, rc=1)[0]

    # Create clusters on driver curve
    clusters = rt_crv.create_clusters_on_curve(rigname, driver_curve, typ)

    # Create spline IK handle, rebuilding the solver curve
    # spline_list (list): ikhandle object [ikhandle, effector, curve]
    spline_list = rt_crv.create_spline_handle(rigname, joints, solver_curve, typ)

    # Set up driver-to-solver connection using pointOnCurveInfo nodes
    # Allowing the cluster-deformed driver curve to control the solver curve
    rt_crv.connect_driver_to_solver_curve(rigname, driver_curve, solver_curve, typ)

    # Build controls and control groups
    # ik_controls (dict): control type (ik, float, spline, upvec) -> list of controls
    # ik_ctrlgrps (dict): ctrl grp typ (ik, float, spline, upvec) -> list of ctrl grps
    ik_controls, ik_ctrlgrps = rt_ctl.create_controls_ik(rigname, joints, clusters)

    if stretchy:
        # Spline IK. Squash and Stretch
        rt_str.build_squash_stretch(rigname, driver_curve, joints, typ)

        # Get initial start and end vectors for advanced twist
        srt_vec = cmds.xform(joints[0], q=1, ws=1, m=1) [8:11]
        end_vec = cmds.xform(joints[-1], q=1, ws=1, m=1) [8:11]
        rt_str.build_advanced_twist(spline_list[0], clusters[0][1], clusters[1][1],
                             srt_vec, end_vec)

    logger.info(f"DONE creating IK tail '{rigname}'")


# RUN: RIG TAIL ========================================================

def rig_tail_test(rigname, start_jnt=None, end_jnt=None,
                  root=None, fk=True, ik=True, stretchy=True):
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
    build_rig_tail(rigname, fk, ik, stretchy)
    rt_con.connect_rig_tail(fk, ik, stretchy)

def rig_tail_selected(root=None, fk=True, ik=True, stretchy=True):
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
        build_rig_tail(rigname, fk, ik, stretchy)
    rt_con.connect_rig_tail(fk, ik, stretchy)

def main(root=None, fk=True, ik=True, stretchy=True):
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
        build_rig_tail(rigname, fk, ik, stretchy)
    rt_con.connect_rig_tail(fk, ik, stretchy)
