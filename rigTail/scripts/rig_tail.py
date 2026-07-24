'''
# rig_tail.py
author: Daisy Jane @gnitemouse

Rig a stretchy tail with IK FK modes.
Switch modes include SplineIK, IK, Float, and FK.
IK mode combines ikHandle with clusters on spline curve.
FK mode provides variable FK sliding controls for joint rotations with falloff.
Option to build squash/stretch.

Run in Maya Script Editor (Python):
import importlib as il
import rig_tail
il.reload(rig_tail)
rig_tail.rig_tail_single('tail', root='tail_spline_grp', fk=True, ik=True)
rig_tail.main()

Rig Hierarchy:

ROOT                                        (ROOT_GRP)

- geometry                                  (GEOMETRY_GRP)

- controls                                  (CONTROL_GRP)
    └─ ROOT_ctrl                            (ROOT_CTRL)
       └─ cog_ctrl                          (COG_CTRL)
          └─ {rigname}_base_ctrl_grp        (BASECTRL_GRP)
             └─ {rigname}_base_ctrl         (BASECTRL)
                └─ FK_{rigname}_root_grp    (CTRLROOT_GRP)
                   └─ {rigname}_NN_ctrl_grp (CTRL_GRP)
                      └─ {rigname}_NN_ctrl  (CTRL)

- skeleton                                  (SKELETON_GRP)
    └─ BN_{rigname}_NN_jnt                  (JNT)

- FK_skeleton                               (FK_SKELETON_GRP)
    └─ FK_{rigname}_grp                     (FK_GRP)
       └─ FK_{rigname}_NN_01_sdk            (SDK_GRP)
          └─ FK_{rigname}_NN_02_sdk
             └─ FK_{rigname}_NN_03_sdk
                └─ FK_{rigname}_NN_jnt_sdk  (SDK_JNT)
                   └─ FK_{rigname}_NN_jnt   (JNT)

- IK_skeleton                               (IK_SKELETON_GRP)
    └─ IK_{rigname}_grp                     (IK_GRP)
       └─ IK_{rigname}_NN_jnt               (IK Joints)


Naming template can be changed in rig_tail_constants.py
Rig component {rigname}s can be changed under RIGPARTS in rig_tail_constants.py
'''

import maya.cmds as cmds
import importlib as il

# logger_config is reloaded before it is imported from, so editing it does
# not need a Maya restart. Every other module does 'from logger_config
# import ...' at its own module level and reads whatever is cached, so a
# stale copy here would make all of them import names that no longer
# match the file on disk.
import logger_config
il.reload(logger_config)
from logger_config import logger_setup, abort_build

import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
import rig_tail_joint as rt_jnt
import rig_tail_math as rt_mat
import rig_tail_matrix as rt_mtx
import rig_tail_naming as rt_nam
import rig_tail_cache as rt_che
import rig_tail_maya as rt_mya
import rig_tail_mainctrl as rt_mc
import rig_tail_orient as rt_orient
import rig_tail_setup as rt_set
import rig_tail_control as rt_ctl
import rig_tail_curve as rt_crv
import rig_tail_fk as rt_fk
import rig_tail_stretch as rt_str
import rig_tail_anim as rt_ani
import rig_tail_connect as rt_con
import rig_tail_restpose as rt_rest
import rig_tail_ui as rt_ui
import rig_tail_test as rt_test

# rig_tail_constants is deliberately NOT reloaded. It is the only module
# holding session state: the settings edited in the UI, the loaded config
# path, and the joint/rest caches all live there as module globals.
# Reloading it re-executes the module and re-runs load_config(), which
# silently discards everything the user set this session. Reload it by
# hand (il.reload(rt_cst)) after editing naming templates or defaults.
il.reload(rt_jnt)
il.reload(rt_mat)
il.reload(rt_mtx)
il.reload(rt_nam)
il.reload(rt_che)
il.reload(rt_mya)
il.reload(rt_mc)
il.reload(rt_orient)
il.reload(rt_set)
il.reload(rt_ctl)
il.reload(rt_crv)
il.reload(rt_fk)
il.reload(rt_str)
il.reload(rt_ani)
il.reload(rt_con)
il.reload(rt_rest)
il.reload(rt_ui)
il.reload(rt_test)

logger = logger_setup(__name__)


# BUILD ================================================================

def build_rig_tail(fk, ik):
    '''
    Rebuild-safe build with caching.

    Arguments
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    # Method D (rebuild-degradation fix): record each BN joint's rest pose once,
    # now, while BN is still at true rest -- cleanup/setup have run but the IK
    # curve/spline (which smooths) and the OPM network (which drives BN off
    # rest) have not. rig_tail_ik then builds the curve from this stored rest
    # instead of live positions, so rebuilds stay consistent. Guarded to
    # capture on the first build only; see rig_tail_restpose.
    rt_rest.capture_rest_pose()

    for rigname in rt_cst.RIGPARTS:
        # Parts without joints were skipped during setup
        if rigname not in rt_cst.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping build")
            continue
        if fk:
            rig_tail_fk(rigname)
        if ik:
            rig_tail_ik(rigname)
        # FK/IK rest match happens after connect_rig_tail (see
        # rig_tail_connect.match_fk_to_ik_rest): the IK spline only reaches its
        # final low-CV shape once the IK system is fully connected, so the IK
        # joints cannot be read reliably here.

def rig_tail_fk(rigname, typ=rt_cst.TYPE_FK):
    '''
    Create FK tail using variable FK method.
    '''
    logger.debug('-----------------------------------------------------')
    logger.info(f"{rigname}: Build FK tail")
    logger.trace(f'joints {rt_cst.JOINTS_FK[rigname]}')

    joints = rt_cst.JOINTS_FK[rigname]
    jnt_pos = rt_jnt.get_joint_position_from_list(joints)
    rt_jnt.set_joint_attributes(joints)

    # Create groups
    rig_systems_grp = rt_nam.fstr('', rt_cst.RIG_SYSTEMS_GRP)
    clusters_grp = rt_nam.fstr('', rt_cst.CLUSTERS_GRP)
    spline_grp_fk = rt_nam.fstr(rigname, rt_cst.SPLINE_GRP, typ)
    cluster_grp_fk = rt_nam.fstr(rigname, rt_cst.CLUSTER_GRP, typ)
    scale_grp = rt_nam.fstr(rigname, rt_cst.SCALE_GRP)
    rt_mya.create_group(spline_grp_fk, parent=rig_systems_grp)
    rt_mya.create_group(cluster_grp_fk, parent=clusters_grp)
    rt_mya.create_group(scale_grp, parent=rig_systems_grp)

    # Create curve
    curve_fk = rt_crv.create_curve(rigname, jnt_pos, typ)

    # Create controls
    varfk_controls = rt_ctl.create_controls_fk(rigname, joints, jnt_pos)

    rt_ctl.add_fk_attributes_to_controls(varfk_controls, joints)
    rt_fk.set_curveinfo_fk(rigname, curve_fk, varfk_controls)

    # Create SDK groups
    fkjnt_grp = rt_fk.create_sdk_groups(rigname, joints)
    sdk_groups = rt_fk.get_sdk_groups(joints)

    # Falloff Rotation
    for n in range(rt_cst.NUM_CTRL_FK):
       rt_fk.falloff_rotation(rigname, n, joints, sdk_groups[n])

    # Bind curve
    logger.debug(f"Bind Curve '{curve_fk}' to FK joints")
    rt_mya.bind_skincluster(joints, curve_fk, f'{curve_fk}_skinCluster')

    # Build stretch
    if rt_cst.EFFECTS['stretchy']:
        rt_str.build_stretch(rigname, curve_fk, joints, typ)

def rig_tail_ik(rigname, typ=rt_cst.TYPE_IK):
    '''
    Create IK tail
    '''
    logger.debug('-----------------------------------------------------')
    logger.info(f"{rigname}: Build IK tail")
    logger.trace(f'joints {rt_cst.JOINTS_IK[rigname]}')

    joints = rt_cst.JOINTS_IK[rigname]
    # Method D swap point: build the IK curve from the captured rest pose so
    # rebuilds don't compound (falls back to live positions when no rest is
    # stored). See rig_tail_restpose.
    jnt_pos = rt_rest.curve_source_positions(rigname, joints)

    # Create groups
    rig_systems_grp = rt_nam.fstr('', rt_cst.RIG_SYSTEMS_GRP)
    clusters_grp = rt_nam.fstr('', rt_cst.CLUSTERS_GRP)
    spline_grp_ik = rt_nam.fstr(rigname, rt_cst.SPLINE_GRP, typ)
    cluster_grp_ik = rt_nam.fstr(rigname, rt_cst.CLUSTER_GRP, typ)
    scale_grp = rt_nam.fstr(rigname, rt_cst.SCALE_GRP)
    rt_mya.create_group(spline_grp_ik, parent=rig_systems_grp)
    rt_mya.create_group(cluster_grp_ik, parent=clusters_grp)
    rt_mya.create_group(scale_grp, parent=rig_systems_grp)

    # Create curves
    curve_ik = rt_crv.create_curve(rigname, jnt_pos, typ) # Driver curve
    curve_ik_spline = rt_crv.create_curve(rigname, jnt_pos, typ, tag='spline') # Solver curve

    # Create clusters
    clusters = rt_crv.create_clusters_on_curve(rigname, curve_ik, typ)

    # Create spline
    spline_list = rt_crv.create_spline_handle(rigname, joints, curve_ik_spline, typ)

    # Connect curves
    rt_crv.connect_driver_to_solver_curve(rigname, curve_ik, curve_ik_spline, typ)

    # Create controls
    ik_controls, ik_ctrlgrps = rt_ctl.create_controls_ik(rigname, joints, clusters)

    # Build stretch
    if rt_cst.EFFECTS['stretchy']:
        rt_str.build_stretch(rigname, curve_ik, joints, typ)

        # Advanced twist
        srt_vec = cmds.xform(joints[0], q=1, ws=1, m=1)[8:11]
        end_vec = cmds.xform(joints[-1], q=1, ws=1, m=1)[8:11]
        rt_str.build_advanced_twist(spline_list[0], clusters[0][1], clusters[1][1],
                             srt_vec, end_vec)


# RUN: RIG TAIL ========================================================

def rig_tail_single(root=None, fk=True, ik=True, start_jnt=None, end_jnt=None):
    '''
    Rig a single tail from a single joint chain.
    Example
        rt.rig_tail_test('tail', root='tail_spline_grp', fk=True, ik=True)
        rt.rig_tail_test('tail', root='tail_spline_grp', fk=False, ik=True)
    '''
    rt_cst.RIGPARTS = [root]
    rt_set.set_root(root)
    rt_set.set_joints(root, start_jnt, end_jnt)
    rt_set.cleanup_rig(fk, ik)
    rt_set.setup_rig(fk, ik)
    build_rig_tail(fk, ik)
    rt_con.connect_rig_tail(fk, ik)

def rig_tail_multiple(root=None, fk=True, ik=True):
    '''
    Rig multiple tails with each part defined in RIGPARTS (see rig_tail_constants).

    Arguments
        root (str): Name of rig; name of root group
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    rt_set.set_root(root)
    rt_set.set_joints_auto()
    rt_set.cleanup_rig(fk, ik)
    rt_set.setup_rig(fk, ik)
    build_rig_tail(fk, ik)
    rt_con.connect_rig_tail(fk, ik)

def rig_tail_selected(root=None, fk=True, ik=True):
    '''
    Rig tail on user selected joints.
    Select start(base) joint on each joint chain.
    Auto-detect rigname and end_jnt.
    '''
    selected = cmds.ls(sl=True)
    if not selected:
        abort_build(logger, 'Select joint to rig tail')

    rt_set.set_root(root)
    for jnt in selected:
        if cmds.objectType(jnt, i='joint'):
            rigname = rt_nam.get_rigname(jnt, rt_cst.JOINT)
            if rigname and rigname not in rt_cst.RIGPARTS:
                rt_cst.RIGPARTS.append(rigname)
            rt_set.set_joints(rigname, jnt)
    rt_set.cleanup_rig(fk, ik)
    rt_set.setup_rig(fk, ik)
    build_rig_tail(fk, ik)
    rt_con.connect_rig_tail(fk, ik)

def main():
    '''
    Launch Qt UI
    '''
    return rt_ui.show_ui()
