'''
# rig_tail.py
author: Daisy Jane @gnitemouse

Main entry point: rig a stretchy tail with IK FK modes.
Switch modes include SplineIK, IK, Float, and FK.
IK mode combines ikHandle with clusters on spline curve.
FK mode provides variable FK sliding controls for joint rotations with falloff.
Option to build squash/stretch.

Each build runs the same pipeline per rig part: cleanup (tear down or
reuse the previous rig, from the joint cache), joints (detect or rebuild
the BN/FK/IK chains), curves + clusters + controls, then connect (wire
switches, matrix network, stretch, FX, bind geometry). The optional
Setup phase (rig_tail_setup) runs separately, BEFORE a build.

Run in Maya Script Editor (Python):
# Build a single tail from a single joint chain
rig_tail.rig_tail_single(root='tail', fk=True, ik=True)
# Build multiple tails with each part defined in rig_tail_constants.RIGPARTS
rig_tail.rig_tail_multiple(root='tail', fk=True, ik=True)
rig_tail.main()         # or launch the Builder UI


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

import logger_config
from logger_config import logger_setup, abort_build

import rig_tail_constants as rt_cst
import rig_tail_joint as rt_jnt
import rig_tail_math as rt_mat
import rig_tail_matrix as rt_mtx
import rig_tail_naming as rt_nam
import rig_tail_cache as rt_che
import rig_tail_maya as rt_mya
import rig_tail_ctrlall as rt_ca
import rig_tail_control as rt_ctl
import rig_tail_curve as rt_crv
import rig_tail_fk as rt_fk
import rig_tail_stretch as rt_str
import rig_tail_anim as rt_ani
import rig_tail_connect as rt_con
# rig_tail_cleanup: teardown + build-structure setup (former rig_tail_setup)
import rig_tail_cleanup as rt_cln
# rig_tail_setup: pre-build Setup phase, orient/mirror (former rig_tail_orient)
import rig_tail_setup as rt_set
import rig_tail_restpose as rt_rest

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
il.reload(rt_ca)
il.reload(rt_ctl)
il.reload(rt_crv)
il.reload(rt_fk)
il.reload(rt_str)
il.reload(rt_ani)
il.reload(rt_con)
# rig_tail_cleanup before rig_tail_setup: the Setup module imports it
il.reload(rt_cln)
il.reload(rt_set)
il.reload(rt_rest)

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
    rt_rest.capture_rest_pose(rt_che.active_parts())

    for rigname in rt_che.active_parts():
        # Parts without joints were skipped during setup
        if rigname not in rt_cst.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping build")
            continue
        # Step timings (rt_mya.timed) report on the build_timer's second
        # line; the phase total alone never says which half was slow
        if fk:
            with rt_mya.timed('build.fk'):
                rig_tail_fk(rigname)
        if ik:
            with rt_mya.timed('build.ik'):
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
    with rt_mya.timed('build.fk.controls'):
        varfk_controls = rt_ctl.create_controls_fk(rigname, joints, jnt_pos)
        rt_ctl.add_fk_attributes_to_controls(varfk_controls, joints)
    rt_fk.set_curveinfo_fk(rigname, curve_fk, varfk_controls)

    # Create SDK groups
    with rt_mya.timed('build.fk.sdk_groups'):
        fkjnt_grp = rt_fk.create_sdk_groups(rigname, joints)
        sdk_groups = rt_fk.get_sdk_groups(joints)

    # Falloff Rotation
    with rt_mya.timed('build.fk.falloff'):
        for n in range(rt_cst.NUM_CTRL_FK):
            rt_fk.falloff_rotation(rigname, n, joints, sdk_groups[n])

    # Bind curve
    logger.debug(f"Bind Curve '{curve_fk}' to FK joints")
    with rt_mya.timed('build.fk.bind_curve'):
        rt_mya.bind_skincluster(joints, curve_fk, f'{curve_fk}_skinCluster')

    # Build stretch
    if rt_cst.EFFECTS['stretchy']:
        with rt_mya.timed('build.fk.stretch'):
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
    with rt_mya.timed('build.ik.clusters'):
        clusters = rt_crv.create_clusters_on_curve(rigname, curve_ik, typ)

    # Create spline
    with rt_mya.timed('build.ik.spline'):
        spline_list = rt_crv.create_spline_handle(rigname, joints, curve_ik_spline, typ)
        # Connect curves
        rt_crv.connect_driver_to_solver_curve(rigname, curve_ik, curve_ik_spline, typ)

    # Create controls
    with rt_mya.timed('build.ik.controls'):
        ik_controls, ik_ctrlgrps = rt_ctl.create_controls_ik(rigname, joints, clusters)

    # Build stretch
    if rt_cst.EFFECTS['stretchy']:
        with rt_mya.timed('build.ik.stretch'):
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
    # Named outright, so build it even if the roster had it excluded
    rt_che.include_parts([root])
    # build_performance_scope: viewport refresh suspended, evaluation
    # manager in DG mode, one undo chunk -- the build runs much faster
    # with no behaviour change (see rig_tail_maya)
    # build_timer: logs where the time actually went, per phase
    with rt_mya.build_performance_scope(), \
            rt_mya.build_timer('rig_tail_single') as timer:
        with timer.phase('cleanup'):
            rt_cln.set_root(root)
            rt_cln.set_joints(root, start_jnt, end_jnt)
            rt_cln.cleanup_rig(fk, ik)
        with timer.phase('setup'):
            rt_cln.setup_rig(fk, ik)
        with timer.phase('build'):
            build_rig_tail(fk, ik)
        with timer.phase('connect'):
            rt_con.connect_rig_tail(fk, ik)

def rig_tail_multiple(root=None, fk=True, ik=True):
    '''
    Rig multiple tails with each part defined in RIGPARTS (see rig_tail_constants).

    Arguments
        root (str): Name of rig; name of root group
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    # See rig_tail_single for the performance scope and timer rationale
    with rt_mya.build_performance_scope(), \
            rt_mya.build_timer('rig_tail_multiple') as timer:
        with timer.phase('cleanup'):
            rt_cln.set_root(root)
            rt_cln.set_joints_auto()
            rt_cln.cleanup_rig(fk, ik)
        with timer.phase('setup'):
            rt_cln.setup_rig(fk, ik)
        with timer.phase('build'):
            build_rig_tail(fk, ik)
        with timer.phase('connect'):
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

    # See rig_tail_single for the performance scope and timer rationale
    with rt_mya.build_performance_scope(), \
            rt_mya.build_timer('rig_tail_selected') as timer:
        with timer.phase('cleanup'):
            rt_cln.set_root(root)
            for jnt in selected:
                if cmds.objectType(jnt, i='joint'):
                    rigname = rt_nam.get_rigname(jnt, rt_cst.JOINT)
                    if rigname and rigname not in rt_cst.RIGPARTS:
                        rt_cst.RIGPARTS.append(rigname)
                    # Selected outright, so build it even if it was excluded
                    rt_che.include_parts([rigname])
                    rt_cln.set_joints(rigname, jnt)
            rt_cln.cleanup_rig(fk, ik)
        with timer.phase('setup'):
            rt_cln.setup_rig(fk, ik)
        with timer.phase('build'):
            build_rig_tail(fk, ik)
        with timer.phase('connect'):
            rt_con.connect_rig_tail(fk, ik)

# SETUP: TAIL SKELETON =================================================

def setup_tails(root=None, dry_run=None):
    '''
    Convenience delegate to rig_tail_setup.setup_tails (the Setup phase).

    Setup is optional and never runs during the build; it orients and
    mirrors the skeleton beforehand. See rig_tail_setup for details.

    Arguments
        root (str): Rig root name; sets rt_cst.ROOT when given.
        dry_run (bool): override MIRROR_DRYRUN; None uses the setting.
            When true, nothing is unbound or modified.

    Return
        dict: summary from rig_tail_setup.run_setup.
    '''
    return rt_set.setup_tails(root=root, dry_run=dry_run)

def main():
    '''
    Launch the Tail Rig Builder UI (the build phase).
    '''
    import rig_tail_build_ui as rt_build_ui
    il.reload(rt_build_ui)
    return rt_build_ui.show_ui()

def main_setup():
    '''
    Launch the Tail Rig Setup UI (skeleton orient / mirror, pre-build).
    '''
    import rig_tail_setup_ui as rt_setup_ui
    il.reload(rt_setup_ui)
    return rt_setup_ui.show_ui()
