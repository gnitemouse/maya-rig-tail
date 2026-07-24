'''
# rig_tail_setup.py
author: Daisy Jane @gnitemouse

Setup for Rig Tail
Cleanup previous rig and prepare for build.

cleanup_rig picks teardown path
True ->
    cleanup_rigname: full teardown, delete basectrl, curves, clusters, FX nodes
False ->
    cleanup_connections: only break connections, keep nodes

validate_cache() checks whether RIGPARTS / ROOT changed
'''

import re
import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_joint as rt_jnt
import rig_tail_cache as rt_cache
import rig_tail_control as rt_ctl
import rig_tail_connect as rt_con
import rig_tail_mainctrl as rt_mc
import rig_tail_orient as rt_orient

logger = logger_setup(__name__)


# CLEANUP ==============================================================

def cleanup_rig(fk, ik):
    '''
    Safely clean up rig components defined in RIGPARTS.
    Handles constraints, skinClusters, controls, node networks.
    Called from build_rig_tail() before building new components.

    Cleanup:
    1. Disconnect skeleton, delete joint constraints
    2. Delete control constraints
    3. Delete skinClusters from curves
    4. Delete existing controls and control groups
    5. Delete utility nodes (conditions, multiply, etc)
    7. Delete curves, clusters, ikHandles

    Arguments
        rigname (str): Name of rig component to clean up
        fk (bool): Clean up FK components
        ik (bool): Clean up IK components
    '''
    logger.debug(f'-----------------------------------------------------')
    logger.info(f"Cleanup Rig")

    # Clear control cache
    rt_con.clear_control_cache()
    # Validate cache
    rt_cache.validate_cache()
    # Control count changes invalidate the node layout for every part
    structure_changed = rt_cache.validate_cache_structure()

    # Delete SDK animCurves
    logger.trace(f"Cleaning up SDK curves")
    anim_curves = cmds.ls(type=['animCurveUU', 'animCurveUL', 'animCurveUA', 'animCurveTT'])
    for anim_curve in anim_curves:
        cmds.delete(anim_curve)
    cleanup_dangling_unit_conversions()

    for rigname in rt_cst.RIGPARTS:
        # Validate cache
        joints_changed = rt_cache.validate_cache_joints(rigname)
        # Unbind geometry before rebuild
        rt_mya.unbind_geometry(rigname)

        # Rebuild check
        if rt_cst.FORCE_REBUILD or joints_changed or structure_changed:
            cleanup_rigname(rigname, fk, ik)
        else:
            cleanup_connections(rigname, fk, ik)

    # Main controller dashboard: remove stale override conditions and,
    # when the dashboard is off, every dashboard attribute. Runs after
    # the per-part loop so expressions referencing the conditions are
    # already gone on a full teardown.
    rt_mc.cleanup_mainctrl(fk, ik)

def restore_fk_joint_chain(rigname):
    '''
    Tear down the FK SDK-group hierarchy and restore a flat FK joint chain.

    build_fk (create_sdk_groups / put_jnt_under_sdk_groups) assumes each FK
    joint enters the build as a plain link in a flat chain (its parent is the
    previous joint). A prior build leaves every joint wrapped in its own SDK
    stack instead; re-wrapping an already-wrapped joint parents the stack top
    under its own descendant, which Maya rejects as a cycle. Unparent the
    joints out, delete all SDK groups (pattern match also clears groups from a
    previous NUM_CTRL_FK value or an older layer layout), then re-chain flat.

    Arguments
        rigname (str): Name of rig component
    '''
    if rigname not in rt_cst.JOINTS_FK:
        return
    logger.trace(f'{rigname}: Restoring flat FK joint chain')
    joints = rt_cst.JOINTS_FK[rigname]
    fkjnt_grp = rt_nam.fstr(rigname, rt_cst.GROUP, rt_cst.TYPE_FK)

    # Unparent all FK joints to world temporarily
    for jnt in joints:
        if cmds.objExists(jnt):
            jnt_parent = cmds.listRelatives(jnt, p=True, typ='transform') or []
            if jnt_parent and jnt_parent[0] != fkjnt_grp:
                cmds.parent(jnt, world=True)

    # Delete all SDK groups by pattern: SDK_GRP and SDK_JNT both end with the
    # SDK label. Pattern matching (not exact counts) also removes groups left
    # over from a previous NUM_CTRL_FK value.
    sdk_pattern = f'{rt_cst.TYPE_FK}_{rigname}_*_{rt_cst.SDK}'
    for sdk_grp in cmds.ls(sdk_pattern, type='transform'):
        if cmds.objExists(sdk_grp):
            rt_mya.remove(sdk_grp)

    # Re-parent FK joints in proper hierarchy
    for i in range(len(joints)-1, 0, -1):  # Reverse order
        if cmds.objExists(joints[i]) and cmds.objExists(joints[i-1]):
            rt_mya.parent_to(joints[i], joints[i-1])


def fk_sdk_structure_is_current(rigname):
    '''
    Report whether the scene's FK SDK hierarchy matches what the current
    builder produces: every FK joint parented directly under its own SDK_JNT
    group. Returns False when the joints are unwrapped (no prior build) or
    wrapped in a stale layout (e.g. built by older code with a different SDK
    layer set), which the joint/control caches cannot detect on their own.

    Arguments
        rigname (str): Name of rig component

    Return
        bool: True if the existing SDK structure can be safely reused as-is
    '''
    if rigname not in rt_cst.JOINTS_FK:
        return True
    for jnt in rt_cst.JOINTS_FK[rigname]:
        if not cmds.objExists(jnt):
            return False
        NN = rt_nam.get_index_from_name(jnt)
        sdk_jnt = rt_nam.fstr(rigname, rt_cst.SDK_JNT, rt_cst.TYPE_FK, NN)
        jnt_parent = cmds.listRelatives(jnt, p=True, typ='transform') or []
        if not jnt_parent or jnt_parent[0] != sdk_jnt:
            return False
    return True


def cleanup_rigname(rigname, fk, ik):
    '''
    Cleanup components for a single RIGPART (rigname).
    Can be called independently for targeted cleanup.
    '''
    logger.debug(f"{rigname}: Cleanup rig part")
    types = ['', rt_cst.TYPE_BN, rt_cst.TYPE_IK, rt_cst.TYPE_FK, rt_cst.TYPE_FX]
    basectrl_grp = rt_nam.fstr(rigname, rt_cst.BASECTRL_GRP)
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)

    # 1. Disconnect skeleton, delete joint constraints
    logger.trace(f"{rigname}: Cleaning up skeleton constraints")
    for joints in [rt_cst.JOINTS_BN, rt_cst.JOINTS_FK, rt_cst.JOINTS_IK, rt_cst.JOINTS_FX]:
        if rigname in joints:
            for jnt in joints[rigname]:
                if cmds.objExists(jnt):
                    # Delete constraints
                    constraints = cmds.listRelatives(jnt, type='constraint') or []
                    for constr in constraints:
                        cmds.delete(constr)
                    # Disconnect incoming connections
                    rt_mya.disconnect_all(jnt, source=True)

    # 2. Delete control constraints
    if cmds.objExists(basectrl):
        all_descendants = cmds.listRelatives(basectrl, ad=True, type='transform') or []
        for node in all_descendants:
            constraints = cmds.listRelatives(node, type='constraint') or []
            for constr in constraints:
                cmds.delete(constr)
            # Reset OPM and transforms on controls
            if f'{rt_cst.CTRL}' in node:
                rt_mya.reset_opm(node, unlock=True)
                rt_mya.reset_transforms(node, unlock=True)

    # 3. Delete skinClusters from curves
    logger.trace(f"{rigname}: Cleaning up skinClusters")
    if fk:
        curve_fk = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_FK)
        rt_mya.unbind_skincluster(curve_fk)
    if ik:
        curve_ik = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_IK)
        curve_ik_spline = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_IK, TAG='_spline')
        rt_mya.unbind_skincluster(curve_ik)
        rt_mya.unbind_skincluster(curve_ik_spline)

    # 4. Delete existing controls and control groups
    logger.trace(f"{rigname} Cleaning up controls and groups")
    rt_mya.remove(basectrl_grp)
    rt_mya.remove(basectrl)

    if fk:
        fkroot_grp = rt_nam.fstr(rigname, rt_cst.CTRLROOT_GRP, rt_cst.TYPE_FK)
        rt_mya.remove(fkroot_grp)

    # Remove SDK groups for FK, restoring the flat FK joint chain
    if fk and rigname in rt_cst.JOINTS_FK:
        restore_fk_joint_chain(rigname)

    # 5. Delete utility nodes (conditions, multiply, math nodes)
    # Every utility node is named '{rigname}_<descriptor>_<nodetype>',
    # so anchor the underscore after rigname: '{rigname}_*' cannot
    # bleed into another part whose name merely extends this one
    # ('tail' cleanup must not delete 'tail2' nodes)
    logger.trace(f"{rigname}: Cleaning up utility nodes")
    for typ in types:
        node_patterns = [
            f'{typ}_{rigname}_*condition',
            f'{typ}_{rigname}_*multiplyDivide',
            f'{typ}_{rigname}_*plusMinusAverage',
            f'{typ}_{rigname}_*multDoubleLinear',
            f'{typ}_{rigname}_*pointMatrixMult',
            f'{typ}_{rigname}_*blendTwoAttr',
            f'{typ}_{rigname}_*clamp',
            f'{typ}_{rigname}_*setRange',
            f'{typ}_{rigname}_*choice',
            f'{typ}_{rigname}_*curveInfo',
            f'{typ}_{rigname}_*pointOnCurveInfo'
        ]
        for pattern in node_patterns:
            nodes = cmds.ls(pattern) or []
            for node in nodes:
                rt_mya.remove(node)

    # 7. Delete curves, clusters, ikHandles
    logger.trace(f"{rigname}: Cleaning up curves and clusters")
    if fk:
        curve_fk = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_FK)
        spline_grp_fk = rt_nam.fstr(rigname, rt_cst.SPLINE_GRP, rt_cst.TYPE_FK)
        cluster_grp_fk = rt_nam.fstr(rigname, rt_cst.CLUSTER_GRP, rt_cst.TYPE_FK)
        rt_mya.remove(curve_fk)
        rt_mya.remove(spline_grp_fk)
        rt_mya.remove(cluster_grp_fk)

    if ik:
        curve_ik = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_IK)
        curve_ik_spline = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_IK, TAG='_spline')
        spline_grp_ik = rt_nam.fstr(rigname, rt_cst.SPLINE_GRP, rt_cst.TYPE_IK)
        cluster_grp_ik = rt_nam.fstr(rigname, rt_cst.CLUSTER_GRP, rt_cst.TYPE_IK)
        spline_handle = rt_nam.fstr(rigname, rt_cst.SPLINE_HANDLE, rt_cst.TYPE_IK)
        spline_effector = rt_nam.fstr(rigname, rt_cst.SPLINE_EFFECTOR, rt_cst.TYPE_IK)
        rt_mya.remove(spline_handle)
        rt_mya.remove(spline_effector)
        rt_mya.remove(curve_ik)
        rt_mya.remove(curve_ik_spline)
        rt_mya.remove(spline_grp_ik)
        rt_mya.remove(cluster_grp_ik)

    # Clean up animation effects
    cleanup_anim_effects(rigname, fk, ik)

    # Delete scale group
    scale_grp = rt_nam.fstr(rigname, rt_cst.SCALE_GRP)
    rt_mya.remove(scale_grp)

    # Clean up old visibility conditions
    basectrl_name = basectrl.rsplit(rt_cst.CTRL, 1)[0]
    rt_mya.remove(f'{basectrl_name}{rt_cst.VIS}{rt_cst.COND}')

    # Clean up old items
    patterns = list()
    for typ in types:
        patterns.extend([
            f'{typ}_{rigname}_revik_{rt_cst.NUM_CTRL_IK:02d}{rt_cst.CTRL}{rt_cst.GRP}',
            f'{typ}_{rigname}_switch_*{rt_cst.VIS}{rt_cst.COND}',
            f'{typ}_{rigname}_measure_scale{rt_cst.GRP}'
        ])
    # for p in patterns:
    #     nodes = cmds.ls(p) or []
    #     for node in nodes:
    #         rt_mya.remove(node)
    for p in patterns:
        nodes = cmds.ls(p)
        if nodes:
            cmds.delete(nodes)

def cleanup_connections(rigname, fk, ik):
    '''
    Clean up connections. Only disconnect, don't delete nodes.

    Exception: the FK SDK hierarchy is only safe to reuse in place when it
    matches the current builder's layout. A stale layout (older build with a
    different SDK layer set) is not something the joint/control caches detect,
    and rebuilding over it re-wraps already-wrapped joints into a parenting
    cycle, so tear that part down to a flat chain first.
    '''
    logger.debug(f'{rigname}: Cleanup connections')

    if fk and not fk_sdk_structure_is_current(rigname):
        logger.debug(f'{rigname}: FK SDK layout is stale; rebuilding it from a flat chain')
        restore_fk_joint_chain(rigname)

    for joints in [rt_cst.JOINTS_BN, rt_cst.JOINTS_FK, rt_cst.JOINTS_IK]:
        if rigname in joints:
            for jnt in joints[rigname]:
                if cmds.objExists(jnt):
                    rt_mya.disconnect_all(jnt, source=True)
                    # Remove constraints
                    constraints = cmds.listRelatives(jnt, type='constraint') or []
                    for constr in constraints:
                        cmds.delete(constr)

    # Disconnect FK SDK groups
    if fk and rigname in rt_cst.JOINTS_FK:
        for i, jnt in enumerate(rt_cst.JOINTS_FK[rigname]):
            NN = rt_nam.get_index_from_name(jnt)
            for idx in range(rt_cst.NUM_CTRL_FK + 1):
                if idx < rt_cst.NUM_CTRL_FK:
                    sdk_grp = rt_nam.fstr(rigname, rt_cst.SDK_GRP, rt_cst.TYPE_FK, NN, nn=idx+1)
                else:
                    sdk_grp = rt_nam.fstr(rigname, rt_cst.SDK_JNT, rt_cst.TYPE_FK, NN)
                if cmds.objExists(sdk_grp):
                    rt_mya.disconnect_all(sdk_grp, source=True)

    # Delete FK utility node networks: the FK build (set_curveinfo_fk,
    # falloff_rotation) recreates them from scratch every run, so
    # keeping the old nodes would accumulate name-suffixed duplicates
    if fk:
        typ = rt_cst.TYPE_FK
        # Underscore anchored after rigname so 'tail' cannot delete
        # 'tail2' nodes (see cleanup_rigname)
        fk_patterns = [
            f'{typ}_{rigname}_*{rt_cst.COND}',
            f'{typ}_{rigname}_*multiplyDivide',
            f'{typ}_{rigname}_*plusMinusAverage',
            f'{typ}_{rigname}_*multDoubleLinear',
            f'{typ}_{rigname}_*pointMatrixMult',
            f'{typ}_{rigname}_*setRange',
            f'{typ}_{rigname}_*pointOnCurveInfo',
        ]
        for pattern in fk_patterns:
            for node in cmds.ls(pattern) or []:
                rt_mya.remove(node)

def cleanup_anim_effects(rigname, fk, ik):
    '''
    Clean up animation effect nodes.
    Expressions are removed first via rt_mya.remove(), which disconnects
    before deleting: cmds.delete on a connected expression cascades through
    its whole connection web (loop network node, sibling FX expressions,
    composeMatrix nodes).

    Arguments
        rigname (str): Name of rig component
        fk (bool): Clean FK effects
        ik (bool): Clean IK effects
    '''
    logger.trace(f'{rigname}: Cleanup animation effects')
    # Delete animation node patterns (expressions first)
    typ = rt_cst.TYPE_FX
    node_patterns = [
        f'{rigname}_*_wave*_expression',
        f'{rigname}_*_noise_*_expression',
        f'{rigname}_loop_time_expression',
        f'{rigname}_loop_time',
        f'{rigname}_curl*_multiplyDivide',
        f'{typ}_{rigname}_wave_*',
        f'{typ}_{rigname}_curl_*',
        f'{typ}_{rigname}_dynOffset_*',
        f'{typ}_{rigname}_loop_*',
        f'{typ}_{rigname}_*_blender_plusMinusAverage',
        f'{typ}_{rigname}_*_ikfk_blendColors',
        f'{typ}_{rigname}_*_ikfk_remap_condition'
    ]
    for pattern in node_patterns:
        nodes = cmds.ls(pattern) or []
        for node in nodes:
            rt_mya.remove(node)

    cleanup_dangling_unit_conversions()

def cleanup_dangling_unit_conversions():
    '''
    Sweep conversion nodes orphaned by deleting SDK animCurves or
    expressions, otherwise they accumulate with every rebuild.
    timeToUnitConversion / unitToTimeConversion are separate node types
    from unitConversion (created for time-attribute connections) and
    need sweeping too.
    '''
    conversions = cmds.ls(type=['unitConversion', 'timeToUnitConversion',
                                'unitToTimeConversion']) or []
    for uc in conversions:
        # Deleting one conversion node can cascade-delete others still in
        # this pre-captured list; skip any that Maya already removed so the
        # .input/.output query below can't raise 'No object matches name'.
        if not cmds.objExists(uc):
            continue
        if not cmds.listConnections(f'{uc}.input', s=True, d=False) \
                or not cmds.listConnections(f'{uc}.output', s=False, d=True):
            cmds.delete(uc)


# SETUP ================================================================

def setup_rig(fk, ik):
    '''
    Create groups, root control, cog control.
    Connect root and cog.
    Rename components for IK if necessary.
    Make sure that RIGPARTS are set.

    Arguments
        fk (bool): Setup FK components
        ik (bool): Setup IK components
    '''
    logger.debug('-----------------------------------------------------')
    logger.info('Setup rig components')

    # The matrix OPM network needs matrixNodes; load it up front
    rt_mya.ensure_plugins()

    # Behavior-mirror L/R chains before anything reads their orientation.
    # This is the safe window: cleanup_rig has unbound geometry and the
    # FK/IK driver chains are still free duplicates (not yet wired to
    # controls or the spline), so re-orienting cannot drag skin or a
    # live rig. No-op when MIRROR_ORIENT is off or no L/R pair exists.
    rt_orient.mirror_orient_all(fk, ik)

    # Sync IKFK_MODES with the build options before the switch attribute
    # is created (connect_cog): IK-only builds must not offer 'FK'
    if rt_cst.update_ikfk_modes(fk, ik):
        logger.debug(f'IKFK_MODES updated for build options: {rt_cst.IKFK_MODES}')

    root_grp = rt_nam.fstr('', rt_cst.ROOT_GRP)
    root_ctrl = rt_nam.fstr('', rt_cst.ROOT_CTRL)
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    geometry_grp = rt_nam.fstr('', rt_cst.GEOMETRY_GRP)
    control_grp = rt_nam.fstr('', rt_cst.CONTROL_GRP)
    skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP)
    rig_systems_grp = rt_nam.fstr('', rt_cst.RIG_SYSTEMS_GRP)
    clusters_grp = rt_nam.fstr('', rt_cst.CLUSTERS_GRP)

    if fk and not ik:
        groups = [geometry_grp, control_grp, skeleton_grp, rig_systems_grp, clusters_grp]
    else:
        fk_skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_FK)
        ik_skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_IK)
        groups = [geometry_grp, control_grp, skeleton_grp,
                  fk_skeleton_grp, ik_skeleton_grp,
                  rig_systems_grp, clusters_grp]
    rt_ctl.create_root_cog()

    # Create structure groups
    for group in groups:
        rt_mya.create_group(group, parent=root_grp)
        if group == geometry_grp:
            meshes = rt_mya.get_geometry_from_scene()
            for geo in meshes:
                # Refuse to create duplicate sibling names: Maya would
                # auto-rename the incoming node, and the clashing shape
                # names ('rivetsShape') break later short-name lookups
                leaf = geo.split('|')[-1]
                if cmds.objExists(f'{geometry_grp}|{leaf}'):
                    logger.warning(
                        f"Skip parenting '{geo}' under '{geometry_grp}': "
                        f"a child named '{leaf}' already exists there. "
                        f"Rename or delete one of the duplicates.")
                    continue
                rt_mya.parent_to(geo, geometry_grp)
        elif group == control_grp:
            controls = rt_mya.get_controls_from_scene()
            for ctrl in controls:
                rt_mya.parent_to(ctrl, control_grp)
        elif group == skeleton_grp:
            joints = rt_mya.get_joints_from_scene()
            for joint in joints:
                rt_mya.parent_to(joint, skeleton_grp)
        else:
            logger.trace(f"Group exists '{group}'")

    if ik: # Replace names
        rename_components()

    rt_con.connect_root(fk, ik)
    rt_con.connect_cog(fk, ik)
    for rigname in rt_cst.RIGPARTS:
        # Parts without joints were skipped by set_joints/set_joints_auto
        if rigname not in rt_cst.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping setup")
            continue
        rt_ctl.create_basectrl(rigname)
        rt_con.connect_basectrl(rigname, fk, ik)

def set_root(root):
    '''
    Set the root name for the rig. A trailing group label is stripped
    (e.g. tail_root_grp -> tail_root). When ROOT changes between
    builds, the previous root group is renamed to the new name so the
    rig is not split across two hierarchies.

    Arguments
        root (str): New root name (with or without group suffix)
    '''
    if not root:
        logger.error(f"Invalid argument '{root}'.")
        return

    rt_cst.ROOT = rt_nam.strip_group_suffix(root)
    root_grp = rt_nam.fstr('', rt_cst.ROOT_GRP)
    logger.debug(f"Set ROOT '{rt_cst.ROOT}'")

    if cmds.objExists(root) and root != root_grp:
        # User passed an existing group name: rename to template name
        cmds.rename(root, root_grp)
    elif not cmds.objExists(root_grp):
        # ROOT changed since the rig was built: carry the existing
        # root group over to the new name
        prev_root_grp = find_existing_root_grp()
        if prev_root_grp and prev_root_grp != root_grp:
            logger.debug(
                f"ROOT changed: rename root group "
                f"'{prev_root_grp}' -> '{root_grp}'")
            cmds.rename(prev_root_grp, root_grp)
    if cmds.objExists(root_grp):
        if cmds.nodeType(root_grp) != 'transform':
            rt_mya.remove(root_grp)

def find_existing_root_grp():
    '''
    Locate the root group of a previous build regardless of its name:
    the parent of the structure groups (geometry/skeleton/controls),
    which only ever live directly under the root group.

    Return
        str or None: Existing root group, or None if no rig is built
    '''
    for template in (rt_cst.GEOMETRY_GRP, rt_cst.SKELETON_GRP,
                     rt_cst.CONTROL_GRP):
        grp = rt_nam.fstr('', template)
        if cmds.objExists(grp):
            parent = cmds.listRelatives(grp, p=True, typ='transform') or []
            if parent:
                return parent[0]
    return None


# JOINTS ===============================================================

def set_joints_auto():
    '''
    Auto-detect joints for all RIGPARTS.
    Search scene for joints matching naming convention.
    '''
    logger.debug('Auto-detect joints for all RIGPARTS')

    for rigname in rt_cst.RIGPARTS:
        # Try to find start joint using naming convention
        start_jnt = rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, NN=0)

        if not cmds.objExists(start_jnt):
            # Fallback: search for any BN joint whose name resolves to
            # exactly this rigname via the naming template, so 'tail'
            # never grabs 'BN_R_tail_00_jnt' (that belongs to 'R_tail')
            all_joints = cmds.ls(type='joint')
            matching = [j for j in all_joints
                        if rt_cst.TYPE_BN in j and
                        rt_nam.get_rigname(j.split('|')[-1], rt_cst.JOINT) == rigname]
            if matching:
                start_jnt = matching[0]
                logger.debug(f"{rigname}: Found start joint '{start_jnt}'")
            else:
                logger.warning(f"{rigname}: No joints found, skipping")
                continue

        # Set joints for this rigname (will auto-detect end)
        set_joints(rigname, start_jnt=start_jnt, end_jnt=None)

def set_joints(rigname, start_jnt=None, end_jnt=None):
    '''
    Create FK, IK, and BN joint chains.
    Set start and end joints. Store joint names in dict.
    Detect joints and decide whether to rename or duplicate.
    Assume that joints follow Naming Template.
    Rebuild-safe: reuses cached joints if they still exist and are valid.

    Arguments
        rigname (str): Name of rig component
        start_jnt (str): First joint in chain (auto-detected if None)
        end_jnt (str): Last joint in chain (auto-detected if None)
    '''
    joints_list = [rt_cst.JOINTS_BN, rt_cst.JOINTS_FK, rt_cst.JOINTS_IK]
    types = [rt_cst.TYPE_BN, rt_cst.TYPE_FK, rt_cst.TYPE_IK]

    # Check if cached joints are still valid
    cache_valid = True
    for i, joints in enumerate(joints_list):
        if rigname in joints:
            # Verify all cached joints still exist
            if not all(cmds.objExists(j) for j in joints[rigname]):
                logger.trace(f'{rigname}: Cached {types[i]} joints invalid, rebuilding')
                cache_valid = False
                del joints[rigname]
        else:
            cache_valid = False

    # If all caches valid, skip rebuild
    if cache_valid:
        logger.debug(f"{rigname}: Using cached joints (all valid)")
        return

    # Detect or validate start joint
    if not start_jnt:
        start_jnt = rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, 0)
        logger.debug(f"{rigname}: Auto-detect start_jnt: {start_jnt}")

    if not cmds.objExists(start_jnt):
        logger.error(f"{rigname}: start_jnt '{start_jnt}' does not exist")
        return
    if end_jnt and not cmds.objExists(end_jnt):
        logger.error(f"{rigname}: end_jnt '{end_jnt}' does not exist")
        return

    logger.debug(f"{rigname}: Setting joints - start:{start_jnt} end:{end_jnt}")

    joint_chain = rt_jnt.get_joint_chain(start_jnt, end_jnt)
    if not joint_chain:
        logger.error(f"{rigname}: No joints found")
        return
    logger.trace(f'Joint Chain: {joint_chain}')

    # Create/rename joints - always create BN
    rt_cst.JOINTS_BN[rigname] = create_rename_joints(rigname, joint_chain, rt_cst.TYPE_BN)
    logger.debug(f'{rigname}: Processed {rt_cst.TYPE_BN} joints: {len(rt_cst.JOINTS_BN[rigname])} joints')
    # Create IK/FK joints here since setup runs before build
    rt_cst.JOINTS_FK[rigname] = create_rename_joints(rigname, rt_cst.JOINTS_BN[rigname], rt_cst.TYPE_FK)
    rt_cst.JOINTS_IK[rigname] = create_rename_joints(rigname, rt_cst.JOINTS_BN[rigname], rt_cst.TYPE_IK)


def create_rename_joints(rigname, joints, typ):
    '''
    Rename or duplicate joint chains safely.

    TYPE_BN:
        - joints are the authoritative BN chain
        - rename in place only

    TYPE_FK / TYPE_IK:
        - duplicate BN root once
        - delete existing target chain
        - rename duplicated joints
    '''
    logger.trace(f"rigname:'{rigname}' joints:'{typ}'")

    # BN: rename in place
    if typ == rt_cst.TYPE_BN:
        out = []
        for jnt in joints:
            NN = rt_nam.get_index_from_name(jnt)
            new_name = rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, NN)
            if jnt != new_name:
                jnt = cmds.rename(jnt, new_name)
            if NN == 'ee':
                break
            out.append(jnt)
        return out

    # FK/IK: duplicate BN hierarchy
    # BN root must already exist
    bn_root = joints[0]
    target_root = rt_nam.fstr(rigname, rt_cst.JOINT, typ, 0)

    # Remove existing FK / IK chain cleanly
    if cmds.objExists(target_root):
        cmds.delete(target_root)

    # Duplicate entire hierarchy once
    dup_root = cmds.duplicate(bn_root, n=target_root, rc=True)[0]
    # Collect duplicated joints in DAG order
    dup_jnts = cmds.ls(dup_root, dag=True, type='joint')

    out = []
    for jnt in dup_jnts:
        NN = rt_nam.get_index_from_name(jnt)
        new_name = rt_nam.fstr(rigname, rt_cst.JOINT, typ, NN)
        if jnt != new_name:
            jnt = cmds.rename(jnt, new_name)
        if NN == 'ee':
            break
        out.append(jnt)
    return out


# RENAME ===============================================================

def rename(source, target):
    '''
    Safely rename Maya object.

    Arguments
        source (str): Current object name
        target (str): Desired object name
    '''
    if cmds.objExists(source):
        cmds.rename(source, target)
        logger.debug(f"Renamed '{source}' -> '{target}'")
    else:
        logger.trace(f"Cancel rename '{source}' -> '{target}'. Source '{source}' does not exist.")

def rigpart_has_joints(rigname):
    '''
    True if the scene contains BN joints for `rigname`, using the same
    detection as set_joints_auto (exact BN start joint, or any BN joint
    whose name resolves to exactly this rigname via the naming
    template). Used to validate/warn about RIGPARTS entries that would
    have nothing to build.

    Arguments
        rigname (str): Rig part name to check

    Return
        bool: True if BN joints exist for this rig part
    '''
    start_jnt = rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, NN=0)
    if cmds.objExists(start_jnt):
        return True
    all_joints = cmds.ls(type='joint') or []
    return any(rt_cst.TYPE_BN in j and
               rt_nam.get_rigname(j.split('|')[-1], rt_cst.JOINT) == rigname
               for j in all_joints)


def rename_rigpart(old, new):
    '''
    Rename a rig part in place: swap the rigname token `old` -> `new` in
    every node that carries it (joints and any already-built rig nodes),
    then update RIGPARTS and the joint caches so names stay consistent.

    The match is bounded to whole tokens, so renaming 'tail' does not
    touch 'detail' or 'tail2'. Renames are keyed by UUID (stable across
    renames) and rolled back if any single rename fails, so on failure the
    scene is left unchanged. If ROOT equals the old rigname (e.g. a single
    default tail whose root group shares the name), ROOT is updated too so
    it keeps matching the renamed root group.

    Arguments
        old (str): Current rig part name
        new (str): New rig part name

    Return
        (bool, str): (success, message). Nothing is changed on failure.
    '''
    new = (new or '').strip()
    if not new:
        return False, 'New name is empty.'
    if new == old:
        return False, 'New name is unchanged.'
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', new):
        return False, ('Invalid name. Use letters, digits and underscores; '
                       'do not start with a digit.')

    old_token = re.compile(rf'(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])')
    new_token = re.compile(rf'(?<![A-Za-z0-9]){re.escape(new)}(?![A-Za-z0-9])')

    all_nodes = cmds.ls(long=True)
    # Refuse if the new name is already used by any node (would collide)
    if any(new_token.search(n.split('|')[-1]) for n in all_nodes):
        return False, f"Name '{new}' is already used in the scene."

    targets = [n for n in all_nodes if old_token.search(n.split('|')[-1])]
    if not targets:
        # Nothing built for this part yet: just migrate list/cache state
        _migrate_rigpart_state(old, new, old_token)
        return True, f"Renamed '{old}' -> '{new}' (no scene nodes yet)."

    # UUIDs survive renames; resolve to the current path at each step so a
    # parent rename never invalidates a pending child.
    uuids = cmds.ls(targets, uuid=True)
    done = []  # (uuid, old_short) for rollback
    try:
        for uuid in uuids:
            cur = cmds.ls(uuid, long=True)
            if not cur:
                continue
            short = cur[0].split('|')[-1]
            new_short = old_token.sub(new, short)
            if new_short == short:
                continue
            cmds.rename(cur[0], new_short)
            done.append((uuid, short))
    except Exception as e:
        for uuid, old_short in reversed(done):
            cur = cmds.ls(uuid, long=True)
            if cur:
                try:
                    cmds.rename(cur[0], old_short)
                except Exception:
                    pass
        logger.warning(f"Rename '{old}' -> '{new}' failed, reverted: {e}")
        return False, f'Rename failed and was reverted: {e}'

    _migrate_rigpart_state(old, new, old_token)
    logger.debug(f"Renamed rig part '{old}' -> '{new}' ({len(done)} nodes)")
    return True, f"Renamed rig part '{old}' -> '{new}' ({len(done)} nodes)."


def _migrate_rigpart_state(old, new, old_token):
    '''Move RIGPARTS, ROOT (if it matched) and joint caches from old to new.'''
    rt_cst.RIGPARTS = [new if p == old else p for p in rt_cst.RIGPARTS]
    for jdict in (rt_cst.JOINTS_BN, rt_cst.JOINTS_FK,
                  rt_cst.JOINTS_IK, rt_cst.JOINTS_FX):
        if old in jdict:
            jdict[new] = [old_token.sub(new, j) for j in jdict.pop(old)]
    lb = rt_cst.LAST_BUILD
    lb['rigparts'] = [new if p == old else p for p in lb.get('rigparts', [])]
    jp = lb.get('joints_pos') or {}
    if old in jp:
        jp[new] = jp.pop(old)
    if rt_cst.ROOT == old:
        rt_cst.ROOT = new
        lb['root'] = new


def rename_components():
    '''
    Rename IK related controls and attributes from old naming convention.
    Handles legacy rig component names for compatibility.

    Only nodes whose names carry a legacy marker are touched: several
    replacements ('Handle' -> 'handle', 'Sml' -> '_sml', ...) would
    otherwise mangle names that already follow the current convention
    (e.g. clusterHandle, splineHandle).

    Warning: Uses hardcoded name replacements, check naming convention.
    '''
    replace_names = {
        'srt': 'grp',
        'spineRig1_': '',
        'spineRig': '',
        'splineIk': 'ik',
        'Handle': 'handle',
        'Effector': 'effector',
        'ikSplineCurve': 'ikspline_crv',
        'fkSpline': 'ik',
        'spineSpline': 'spline',
        'floatSpline': 'float',
        'cntrlBase': 'bot',
        'cntrlMid': 'mid',
        'rotMid': 'mid_rot',
        'cntrlTop': 'top',
        'Sml': '_sml',
        '__' : '_',
        'hierarchySwitch': 'switch'
        }
    # Substrings that only appear in legacy names; nodes without one
    # are already on the current convention and are left alone
    legacy_markers = ('spineRig', 'splineIk', 'ikSplineCurve', 'fkSpline',
                      'spineSpline', 'floatSpline', 'cntrlBase', 'cntrlMid',
                      'rotMid', 'cntrlTop', 'hierarchySwitch')

    def legacy_rename(node):
        if not any(marker in node for marker in legacy_markers):
            return
        node_name = node
        for old_name, new_name in replace_names.items():
            node_name = node_name.replace(old_name, new_name)
        # Renames can invalidate names listed earlier (e.g. shapes of a
        # renamed transform), so re-check existence
        if node != node_name and cmds.objExists(node):
            logger.trace(f"Rename legacy node '{node}' -> '{node_name}'")
            cmds.rename(node, node_name)

    dag_nodes = cmds.ls(dag=True)
    transforms = cmds.ls(dag_nodes, type='transform')

    # Remove old IKFK Switch attributes (change as necessary)
    old_switches = ['hierarchySwitch', 'IKFK Switch']
    for node in transforms:
        for old_switch in old_switches:
            if cmds.attributeQuery(old_switch, n=node, ex=1):
                cmds.deleteAttr(node, at=old_switch)
                rt_mya.add_attribute_enum(node, rt_cst.IKFK_DIVIDER[0], rt_cst.IKFK_DIVIDER[1], rt_cst.IKFK_DIVIDER[2])
                rt_mya.add_attribute_enum(node, rt_cst.IKFK_SWITCH[0], rt_cst.IKFK_SWITCH[1], rt_cst.IKFK_SWITCH[2], rt_cst.IKFK_SWITCH[3])

    # Rename legacy DAG nodes
    for node in dag_nodes:
        legacy_rename(node)

    # Rename legacy utility nodes
    non_dag_nodes = cmds.ls(dag=False)
    util_nodes = ['condition', 'multiplyDivide', 'plusMinusAverage',
                  'curveInfo', 'pointOnCurveInfo', 'blendTwoAttr',
                  'multDoubleLinear', 'pointMatrixMult', 'setRange', 'clamp']
    for util_typ in util_nodes:
        util_node = cmds.ls(non_dag_nodes, type=util_typ)
        for node in util_node:
            legacy_rename(node)
