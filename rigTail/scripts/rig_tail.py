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

from logger_config import logger_setup, abort_build

import rig_tail_constants as rt_constants
import rig_tail_joint as rt_joint
import rig_tail_math as rt_math
import rig_tail_matrix as rt_matrix
import rig_tail_naming as rt_naming
import rig_tail_cache as rt_cache
import rig_tail_maya as rt_maya
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_control as rt_control
import rig_tail_curve as rt_curve
import rig_tail_fk as rt_fk
import rig_tail_stretch as rt_stretch
import rig_tail_anim as rt_anim
import rig_tail_connect as rt_connect
# rig_tail_cleanup: teardown + build-structure setup
import rig_tail_cleanup as rt_cleanup
# rig_tail_setup (the pre-build Setup phase, orient/mirror) is NOT imported
# here. The build never calls it -- only the setup_tails delegate below
# does -- so it is imported there instead. That keeps the Build tool
# importable when the installer was run without the Setup shelf button,
# which leaves rig_tail_setup off disk entirely.
import rig_tail_restpose as rt_rest

# Plain imports, no importlib.reload sweep. Picking up edited code is the
# launchers' job: each shelf button drops its modules from sys.modules and
# imports them again. That is both cheaper (a module runs once per launch
# rather than twice) and safer (no reload order to keep in step with the
# dependency graph).
#
# Which modules a launcher drops is the difference between them. Setup and
# Build drop only their own, so rig_tail_constants stays loaded and the
# settings edited in the UI, the loaded config path and the joint/rest
# caches -- all module globals there -- carry across relaunches. Reload
# drops everything for a clean slate, constants included, which re-runs
# load_config() and returns those settings to their configured values.
# To pick up an edited naming template without losing the rest:
#     import importlib; importlib.reload(rt_constants)

logger = logger_setup(__name__)


# BUILD ================================================================

def build_rig_tail(fk, ik):
    '''
    Rebuild-safe build with caching.

    Arguments
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    # Rest anchor (see rig_tail_restpose): record each BN joint's rest pose once,
    # now, while BN is still at true rest -- cleanup/setup have run but the IK
    # curve/spline (which smooths) and the OPM network (which drives BN off
    # rest) have not. rig_tail_ik then builds the curve from this stored rest
    # instead of live positions, so rebuilds stay consistent. Guarded to
    # capture on the first build only; see rig_tail_restpose.
    rt_rest.capture_rest_pose(rt_cache.active_parts())

    for rigname in rt_cache.active_parts():
        # Parts without joints were skipped during setup
        if rigname not in rt_constants.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping build")
            continue
        # Step timings (rt_maya.timed) report on the build_timer's second
        # line; the phase total alone never says which half was slow
        if fk:
            with rt_maya.timed('build.fk'):
                rig_tail_fk(rigname)
        if ik:
            with rt_maya.timed('build.ik'):
                rig_tail_ik(rigname)
        # FK/IK rest match happens after connect_rig_tail (see
        # rig_tail_connect.match_fk_to_ik_rest): the IK spline only reaches its
        # final low-CV shape once the IK system is fully connected, so the IK
        # joints cannot be read reliably here.

def rig_tail_fk(rigname, typ=rt_constants.TYPE_FK):
    '''
    Create FK tail using variable FK method.
    '''
    logger.debug('-----------------------------------------------------')
    logger.info(f"{rigname}: Build FK tail")
    logger.trace(f'joints {rt_constants.JOINTS_FK[rigname]}')

    joints = rt_constants.JOINTS_FK[rigname]
    jnt_pos = rt_joint.get_joint_position_from_list(joints)
    rt_joint.set_joint_attributes(joints)

    # Create groups
    rig_systems_grp = rt_naming.fstr('', rt_constants.RIG_SYSTEMS_GRP)
    spline_grp_fk = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, typ)
    scale_grp = rt_naming.fstr(rigname, rt_constants.SCALE_GRP)
    rt_maya.create_group(spline_grp_fk, parent=rig_systems_grp)
    rt_maya.create_group(scale_grp, parent=rig_systems_grp)
    # No FK cluster group: nothing deforms the FK curve. Clusters are IK
    # only, up-vectors included - see rig_tail_curve.create_clusters_on_curve.
    # cleanup still removes the group, so older scenes lose their empty one.

    # Create curve
    curve_fk = rt_curve.create_curve(rigname, jnt_pos, typ)

    # Create controls
    with rt_maya.timed('build.fk.controls'):
        varfk_controls = rt_control.create_controls_fk(rigname, joints, jnt_pos)
        rt_control.add_fk_attributes_to_controls(varfk_controls, joints)
    rt_fk.set_curveinfo_fk(rigname, curve_fk, varfk_controls)

    # Create SDK groups
    with rt_maya.timed('build.fk.sdk_groups'):
        fkjnt_grp = rt_fk.create_sdk_groups(rigname, joints)
        sdk_groups = rt_fk.get_sdk_groups(joints)

    # Falloff Rotation
    with rt_maya.timed('build.fk.falloff'):
        for n in range(rt_constants.NUM_CTRL_FK):
            rt_fk.falloff_rotation(rigname, n, joints, sdk_groups[n])

    # Bind curve
    logger.debug(f"Bind Curve '{curve_fk}' to FK joints")
    with rt_maya.timed('build.fk.bind_curve'):
        rt_maya.bind_skincluster(joints, curve_fk, f'{curve_fk}_skinCluster')

    # Build stretch
    if rt_constants.EFFECTS['stretchy']:
        with rt_maya.timed('build.fk.stretch'):
            rt_stretch.build_stretch(rigname, curve_fk, joints, typ)

def rig_tail_ik(rigname, typ=rt_constants.TYPE_IK):
    '''
    Create IK tail
    '''
    logger.debug('-----------------------------------------------------')
    logger.info(f"{rigname}: Build IK tail")
    logger.trace(f'joints {rt_constants.JOINTS_IK[rigname]}')

    joints = rt_constants.JOINTS_IK[rigname]
    # Rest anchor: build the IK curve from the captured rest pose so
    # rebuilds don't compound (falls back to live positions when no rest is
    # stored). See rig_tail_restpose.
    jnt_pos = rt_rest.curve_source_positions(rigname, joints)

    # Create groups
    rig_systems_grp = rt_naming.fstr('', rt_constants.RIG_SYSTEMS_GRP)
    clusters_grp = rt_naming.fstr('', rt_constants.CLUSTERS_GRP)
    spline_grp_ik = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, typ)
    cluster_grp_ik = rt_naming.fstr(rigname, rt_constants.CLUSTER_GRP, typ)
    scale_grp = rt_naming.fstr(rigname, rt_constants.SCALE_GRP)
    rt_maya.create_group(spline_grp_ik, parent=rig_systems_grp)
    rt_maya.create_group(cluster_grp_ik, parent=clusters_grp)
    rt_maya.create_group(scale_grp, parent=rig_systems_grp)

    # Create curves
    curve_ik = rt_curve.create_curve(rigname, jnt_pos, typ) # Driver curve
    curve_ik_spline = rt_curve.create_curve(rigname, jnt_pos, typ, tag='spline') # Solver curve

    # Create clusters
    with rt_maya.timed('build.ik.clusters'):
        clusters = rt_curve.create_clusters_on_curve(rigname, curve_ik, typ)

    # Create spline
    with rt_maya.timed('build.ik.spline'):
        spline_list = rt_curve.create_spline_handle(rigname, joints, curve_ik_spline, typ)
        # Connect curves
        # jnt_pos is what makes the solver curve rest on the JOINTS rather
        # than on the low-CV driver curve's smoothed version of them
        rt_curve.connect_driver_to_solver_curve(rigname, curve_ik,
                                                curve_ik_spline, typ,
                                                jnt_pos=jnt_pos)

    # Create controls
    with rt_maya.timed('build.ik.controls'):
        ik_controls, ik_ctrlgrps = rt_control.create_controls_ik(rigname, joints, clusters)

    # Build stretch
    if rt_constants.EFFECTS['stretchy']:
        with rt_maya.timed('build.ik.stretch'):
            rt_stretch.build_stretch(rigname, curve_ik, joints, typ)

            # Advanced twist
            srt_vec = cmds.xform(joints[0], q=1, ws=1, m=1)[8:11]
            end_vec = cmds.xform(joints[-1], q=1, ws=1, m=1)[8:11]
            rt_stretch.build_advanced_twist(spline_list[0], clusters[0][1], clusters[1][1],
                                 srt_vec, end_vec, rigname)


# RUN: RIG TAIL ========================================================

def restore_bn_for_build(rignames=None):
    '''
    Put the BN skeleton back to plain joints before the build touches it.

    THE PRECONDITION EVERY BUILD ASSUMES. A first-ever build gets a plain
    posed skeleton and works from it; a rebuild used to get a skeleton whose
    shape lived only in the previous build's offsetParentMatrix network, and
    the first thing the pipeline does is delete the FK/IK chains that
    network reads (rig_tail_cleanup.create_rename_joints), which collapsed
    the whole chain onto its parent before cleanup had even run.

    Establishing the precondition up front is deliberately not the same fix
    as reordering the pipeline so chains are rebuilt after teardown. The
    reorder only holds if disconnecting a joint leaves its
    offsetParentMatrix on its last value; this holds no matter what
    disconnect does, because afterwards nothing drives BN at all.

    Restores to the stored REST pose where there is one, so a rebuild fired
    on a posed rig re-anchors to rest rather than freezing the pose into the
    skeleton. See rig_tail_cleanup.capture_bn_poses.

    Runs before set_joints/set_joints_auto, and only on the parts being
    built - an excluded part's rig is still live and still driving its
    joints.

    Arguments
        rignames (list): parts to restore, or None for the active roster
    '''
    # Chain detection only: fills JOINTS_BN without duplicating FK/IK, which
    # is the very step that must not run first. Timed separately from the
    # restore because it is a second full scene scan on top of the one
    # set_joints_auto does straight after, and the two are worth telling
    # apart before deciding whether that is worth sharing.
    with rt_maya.timed('cleanup.detect_bn'):
        rt_cleanup.detect_joints_bn()
    parts = rignames if rignames is not None else rt_cache.active_parts()
    parts = [p for p in parts if p in rt_constants.JOINTS_BN]
    if not parts:
        return
    with rt_maya.timed('cleanup.restore_bn'):
        rt_cleanup.restore_bn_skeleton(parts)


def rig_tail_single(root=None, fk=True, ik=True, start_jnt=None, end_jnt=None):
    '''
    Rig a single tail from a single joint chain.
    Example
        rt.rig_tail_test('tail', root='tail_spline_grp', fk=True, ik=True)
        rt.rig_tail_test('tail', root='tail_spline_grp', fk=False, ik=True)
    '''
    rt_constants.RIGPARTS = [root]
    # Named outright, so build it even if the roster had it excluded
    rt_cache.include_parts([root])
    guard_unique_rigparts([root])
    # build_performance_scope: viewport refresh suspended, evaluation
    # manager in DG mode, one undo chunk -- the build runs much faster
    # with no behaviour change (see rig_tail_maya)
    # build_timer: logs where the time actually went, per phase
    with rt_maya.build_performance_scope(), \
            rt_maya.build_timer('rig_tail_single') as timer:
        with timer.phase('cleanup'):
            rt_cleanup.set_root(root)
            restore_bn_for_build([root])
            rt_cleanup.set_joints(root, start_jnt, end_jnt)
            rt_cleanup.cleanup_rig(fk, ik)
        with timer.phase('setup'):
            rt_cleanup.setup_rig(fk, ik)
        with timer.phase('build'):
            build_rig_tail(fk, ik)
        with timer.phase('connect'):
            rt_connect.connect_rig_tail(fk, ik)

def rig_tail_multiple(root=None, fk=True, ik=True):
    '''
    Rig multiple tails with each part defined in RIGPARTS (see rig_tail_constants).

    Arguments
        root (str): Name of rig; name of root group
        fk (bool): Build FK components
        ik (bool): Build IK components
    '''
    guard_unique_rigparts()
    # See rig_tail_single for the performance scope and timer rationale
    with rt_maya.build_performance_scope(), \
            rt_maya.build_timer('rig_tail_multiple') as timer:
        with timer.phase('cleanup'):
            rt_cleanup.set_root(root)
            restore_bn_for_build()
            # Untimed until now, and the cleanup phase has been reporting
            # far more time than its steps account for. This is where the
            # FK/IK chains are duplicated - two chains per rig part, every
            # joint - so it is the prime suspect for the gap.
            with rt_maya.timed('cleanup.set_joints'):
                rt_cleanup.set_joints_auto()
            rt_cleanup.cleanup_rig(fk, ik)
        with timer.phase('setup'):
            rt_cleanup.setup_rig(fk, ik)
        with timer.phase('build'):
            build_rig_tail(fk, ik)
        with timer.phase('connect'):
            rt_connect.connect_rig_tail(fk, ik)

def rig_tail_selected(root=None, fk=True, ik=True):
    '''
    Rig tail on user selected joints.
    Select start(base) joint on each joint chain.
    Auto-detect rigname and end_jnt.
    '''
    selected = cmds.ls(sl=True)
    if not selected:
        abort_build(logger, 'Select joint to rig tail')

    # The roster is discovered from the selection below, so the check is on
    # the rig parts those joints name.
    guard_unique_rigparts(
        [r for r in (rt_naming.get_rigname(j, rt_constants.JOINT)
                     for j in selected if cmds.objectType(j, i='joint')) if r])

    # See rig_tail_single for the performance scope and timer rationale
    with rt_maya.build_performance_scope(), \
            rt_maya.build_timer('rig_tail_selected') as timer:
        with timer.phase('cleanup'):
            rt_cleanup.set_root(root)
            # Roster first, then the BN restore, then the chains. set_joints
            # deletes the FK/IK chains the live rig is driving BN through, so
            # it must not run until BN can hold its own pose - see
            # restore_bn_for_build.
            picked = []
            for jnt in selected:
                if cmds.objectType(jnt, i='joint'):
                    rigname = rt_naming.get_rigname(jnt, rt_constants.JOINT)
                    if rigname and rigname not in rt_constants.RIGPARTS:
                        rt_constants.RIGPARTS.append(rigname)
                    # Selected outright, so build it even if it was excluded
                    rt_cache.include_parts([rigname])
                    picked.append((rigname, jnt))
            restore_bn_for_build([r for r, _ in picked])
            for rigname, jnt in picked:
                rt_cleanup.set_joints(rigname, jnt)
            rt_cleanup.cleanup_rig(fk, ik)
        with timer.phase('setup'):
            rt_cleanup.setup_rig(fk, ik)
        with timer.phase('build'):
            build_rig_tail(fk, ik)
        with timer.phase('connect'):
            rt_connect.connect_rig_tail(fk, ik)

def guard_unique_rigparts(rignames=None):
    '''
    Abort the build when a rig part name is carried by more than one joint
    chain in the scene.

    Every node a build creates is named after its rig part - controls,
    curves, groups, the FK and IK skeletons - so two chains answering to one
    rig part have the build wiring a single rig out of both, and there is no
    correct guess to make. Joint Chain Builder and Setup can carry on (the
    first works off the viewport selection, the second picks the chain under
    ROOT and says which), which is what makes it safe to keep a replacement
    tail in the scene alongside the one it replaces - right up to the build.

    Runs BEFORE cleanup, which tears the existing rig down: aborting after
    that would leave the scene stripped and unbuilt.

    Arguments
        rignames (list): rig parts to check, or None for RIGPARTS
    '''
    duplicates = rt_cleanup.duplicate_rigparts(rignames)
    if not duplicates:
        return
    detail = '; '.join(
        f"{rigname}: {', '.join(paths)}" for rigname, paths in
        sorted(duplicates.items()))
    abort_build(logger,
        f'{len(duplicates)} rig part name(s) are carried by more than one '
        f'joint chain - {detail}. Give each chain its own rig part name '
        '(Joint Chain Builder can rename one), or remove the extra chain, '
        'then build again.')


# SETUP: TAIL SKELETON =================================================

def setup_tails(root=None, dry_run=None):
    '''
    Convenience delegate to rig_tail_setup.setup_tails (the Setup phase).

    Setup is optional and never runs during the build; it orients and
    mirrors the skeleton beforehand. See rig_tail_setup for details.

    Arguments
        root (str): Rig root name; sets rt_constants.ROOT when given.
        dry_run (bool): override MIRROR_DRYRUN; None uses the setting.
            When true, nothing is unbound or modified.

    Return
        dict: summary from rig_tail_setup.run_setup.
    '''
    # Imported here rather than at module scope so the Build tool stays
    # usable if Setup is unavailable. See the import block at the top.
    import rig_tail_setup as rt_setup

    return rt_setup.setup_tails(root=root, dry_run=dry_run)

def main():
    '''
    Launch the Tail Builder UI (the build phase).
    '''
    # Reloaded here, not left to the import: the shelf button already
    # purges the UI module, but a hand-typed rt.main() in the Script
    # Editor would otherwise reopen the window from stale code.
    import importlib
    import rig_tail_build_ui as rt_build_ui
    importlib.reload(rt_build_ui)
    return rt_build_ui.show_ui()

def main_setup():
    '''
    Launch the Tail Setup UI (skeleton orient / mirror, pre-build).
    '''
    import importlib
    import rig_tail_setup_ui as rt_setup_ui
    importlib.reload(rt_setup_ui)
    return rt_setup_ui.show_ui()
