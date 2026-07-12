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

import maya.cmds as cmds
from logger_config import logger_setup
from rig_tail_constants import *
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_joint as rt_jnt
import rig_tail_cache as rt_cache
import rig_tail_control as rt_ctl
import rig_tail_connect as rt_con

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
    logger.info(f'-----------------------------------------------------')
    logger.info(f"Cleanup Rig")

    # Clear control cache
    rt_con.clear_control_cache()
    # Validate cache
    rt_cache.validate_cache()

    # Delete SDK animCurves
    logger.debug(f"Cleaning up SDK curves")
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
        if rt_cst.FORCE_REBUILD or joints_changed:
            cleanup_rigname(rigname, fk, ik)
        else:
            cleanup_connections(rigname, fk, ik)

def cleanup_rigname(rigname, fk, ik):
    '''
    Cleanup components for a single RIGPART (rigname).
    Can be called independently for targeted cleanup.
    '''
    logger.info(f"{rigname}: Cleanup rig part")
    types = ['', TYPE_BN, TYPE_IK, TYPE_FK, TYPE_FX]
    basectrl_grp = rt_nam.fstr(rigname, BASECTRL_GRP)
    basectrl = rt_nam.fstr(rigname, BASECTRL)

    # 1. Disconnect skeleton, delete joint constraints
    logger.debug(f"{rigname}: Cleaning up skeleton constraints")
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
            if f'{CTRL}' in node:
                rt_mya.reset_opm(node, unlock=True)
                rt_mya.reset_transforms(node, unlock=True)

    # 3. Delete skinClusters from curves
    logger.debug(f"{rigname}: Cleaning up skinClusters")
    if fk:
        curve_fk = rt_nam.fstr(rigname, CURVE, TYPE_FK)
        rt_mya.unbind_skincluster(curve_fk)
    if ik:
        curve_ik = rt_nam.fstr(rigname, CURVE, TYPE_IK)
        curve_ik_spline = rt_nam.fstr(rigname, CURVE, TYPE_IK, TAG='_spline')
        rt_mya.unbind_skincluster(curve_ik)
        rt_mya.unbind_skincluster(curve_ik_spline)

    # 4. Delete existing controls and control groups
    logger.debug(f"{rigname} Cleaning up controls and groups")
    rt_mya.remove(basectrl_grp)
    rt_mya.remove(basectrl)

    if fk:
        fkroot_grp = rt_nam.fstr(rigname, CTRLROOT_GRP, TYPE_FK)
        rt_mya.remove(fkroot_grp)

    # Remove SDK groups for FK
    if fk and rigname in rt_cst.JOINTS_FK:
        logger.debug(f'{rigname}: Cleaning up FK SDK groups')
        # First, restore FK joint hierarchy by removing SDK groups
        joints = rt_cst.JOINTS_FK[rigname]
        fkjnt_grp = rt_nam.fstr(rigname, GROUP, TYPE_FK)

        # Unparent all FK joints to world temporarily
        for jnt in joints:
            if cmds.objExists(jnt):
                jnt_parent = cmds.listRelatives(jnt, p=True, typ='transform') or []
                if jnt_parent and jnt_parent[0] != fkjnt_grp:
                    cmds.parent(jnt, world=True)

        # Delete all SDK groups
        for i, jnt in enumerate(joints):
            NN = rt_nam.get_index_from_name(jnt)
            for idx in range(NUM_CTRL_FK+1):
                if idx < NUM_CTRL_FK:
                    sdk_grp = rt_nam.fstr(rigname, SDK_GRP, TYPE_FK, NN, nn=idx+1)
                else:
                    sdk_grp = rt_nam.fstr(rigname, SDK_JNT, TYPE_FK, NN)
                if cmds.objExists(sdk_grp):
                    rt_mya.remove(sdk_grp)

        # Re-parent FK joints in proper hierarchy
        for i in range(len(joints)-1, 0, -1):  # Reverse order
            if cmds.objExists(joints[i]) and cmds.objExists(joints[i-1]):
                rt_mya.parent_to(joints[i], joints[i-1])

    # 5. Delete utility nodes (conditions, multiply, math nodes)
    logger.debug(f"{rigname}: Cleaning up utility nodes")
    for typ in types:
        node_patterns = [
            f'{typ}_{rigname}*_condition',
            f'{typ}_{rigname}*_multiplyDivide',
            f'{typ}_{rigname}*_plusMinusAverage',
            f'{typ}_{rigname}*_multDoubleLinear',
            f'{typ}_{rigname}*_pointMatrixMult',
            f'{typ}_{rigname}*_blendTwoAttr',
            f'{typ}_{rigname}*_clamp',
            f'{typ}_{rigname}*_setRange',
            f'{typ}_{rigname}*_choice',
            f'{typ}_{rigname}*_curveInfo',
            f'{typ}_{rigname}*_pointOnCurveInfo'
        ]
        for pattern in node_patterns:
            nodes = cmds.ls(pattern) or []
            for node in nodes:
                rt_mya.remove(node)

    # 7. Delete curves, clusters, ikHandles
    logger.debug(f"{rigname}: Cleaning up curves and clusters")
    if fk:
        curve_fk = rt_nam.fstr(rigname, CURVE, TYPE_FK)
        spline_grp_fk = rt_nam.fstr(rigname, SPLINE_GRP, TYPE_FK)
        cluster_grp_fk = rt_nam.fstr(rigname, CLUSTER_GRP, TYPE_FK)
        rt_mya.remove(curve_fk)
        rt_mya.remove(spline_grp_fk)
        rt_mya.remove(cluster_grp_fk)

    if ik:
        curve_ik = rt_nam.fstr(rigname, CURVE, TYPE_IK)
        curve_ik_spline = rt_nam.fstr(rigname, CURVE, TYPE_IK, TAG='_spline')
        spline_grp_ik = rt_nam.fstr(rigname, SPLINE_GRP, TYPE_IK)
        cluster_grp_ik = rt_nam.fstr(rigname, CLUSTER_GRP, TYPE_IK)
        spline_handle = rt_nam.fstr(rigname, SPLINE_HANDLE, TYPE_IK)
        spline_effector = rt_nam.fstr(rigname, SPLINE_EFFECTOR, TYPE_IK)
        rt_mya.remove(spline_handle)
        rt_mya.remove(spline_effector)
        rt_mya.remove(curve_ik)
        rt_mya.remove(curve_ik_spline)
        rt_mya.remove(spline_grp_ik)
        rt_mya.remove(cluster_grp_ik)

    # Clean up animation effects
    cleanup_anim_effects(rigname, fk, ik)

    # Delete scale group
    scale_grp = rt_nam.fstr(rigname, SCALE_GRP)
    rt_mya.remove(scale_grp)

    # Clean up old visibility conditions
    basectrl_name = basectrl.rsplit(CTRL, 1)[0]
    rt_mya.remove(f'{basectrl_name}{VIS}{COND}')

    # Clean up old items
    patterns = list()
    for typ in types:
        patterns.extend([
            f'{typ}_{rigname}_revik_{NUM_CTRL_IK:02d}{CTRL}{GRP}',
            f'{typ}_{rigname}_switch_*{VIS}{COND}',
            f'{typ}_{rigname}_measure_scale{GRP}'
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
    '''
    logger.info(f'{rigname}: Cleanup connections')

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
            for idx in range(NUM_CTRL_FK + 1):
                if idx < NUM_CTRL_FK:
                    sdk_grp = rt_nam.fstr(rigname, SDK_GRP, TYPE_FK, NN, nn=idx+1)
                else:
                    sdk_grp = rt_nam.fstr(rigname, SDK_JNT, TYPE_FK, NN)
                if cmds.objExists(sdk_grp):
                    rt_mya.disconnect_all(sdk_grp, source=True)

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
    logger.debug(f'{rigname}: Cleanup animation effects')
    # Delete animation node patterns (expressions first)
    typ = TYPE_FX
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
    Sweep unitConversion nodes orphaned by deleting SDK animCurves or
    expressions, otherwise they accumulate with every rebuild.
    '''
    for uc in cmds.ls(type='unitConversion') or []:
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
    logger.info('-----------------------------------------------------')
    logger.info('Setup rig components')
    root_grp = rt_nam.fstr('', ROOT_GRP)
    root_ctrl = rt_nam.fstr('', ROOT_CTRL)
    cog_ctrl = rt_nam.fstr('', COG_CTRL)
    geometry_grp = rt_nam.fstr('', GEOMETRY_GRP)
    control_grp = rt_nam.fstr('', CONTROL_GRP)
    skeleton_grp = rt_nam.fstr('', SKELETON_GRP)
    rig_systems_grp = rt_nam.fstr('', RIG_SYSTEMS_GRP)
    clusters_grp = rt_nam.fstr('', CLUSTERS_GRP)

    if fk and not ik:
        groups = [geometry_grp, control_grp, skeleton_grp, rig_systems_grp, clusters_grp]
    else:
        fk_skeleton_grp = rt_nam.fstr('', SKELETON_GRP, TYPE_FK)
        ik_skeleton_grp = rt_nam.fstr('', SKELETON_GRP, TYPE_IK)
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
            logger.debug(f"Group exists '{group}'")

    if ik: # Replace names
        rename_components()

    rt_con.connect_root(fk, ik)
    rt_con.connect_cog(fk, ik)
    for rigname in rt_cst.RIGPARTS:
        rt_ctl.create_basectrl(rigname)
        rt_con.connect_basectrl(rigname, fk, ik)

def set_root(root):
    '''
    Set the root name for the rig.
    Renames existing root group if necessary.

    Arguments
        root (str): New root name (with or without _grp suffix)
    '''
    if root:
        rt_cst.ROOT = root
        root_grp = rt_nam.fstr('', ROOT_GRP)
        logger.info(f"Set ROOT '{rt_cst.ROOT}'")

        if cmds.objExists(root) and root != root_grp:
            cmds.rename(root, root_grp)
        if cmds.objExists(root_grp):
            if cmds.nodeType(root_grp) != 'transform':
                rt_mya.remove(root_grp)
    else:
        logger.error(f"Invalid argument '{root}'.")


# JOINTS ===============================================================

def set_joints_auto():
    '''
    Auto-detect joints for all RIGPARTS.
    Search scene for joints matching naming convention.
    '''
    logger.info('Auto-detect joints for all RIGPARTS')

    for rigname in rt_cst.RIGPARTS:
        # Try to find start joint using naming convention
        start_jnt = rt_nam.fstr(rigname, JOINT, TYPE_BN, NN=0)

        if not cmds.objExists(start_jnt):
            # Fallback: search for any joint with rigname
            all_joints = cmds.ls(type='joint')
            matching = [j for j in all_joints if rigname in j and TYPE_BN in j]
            if matching:
                start_jnt = matching[0]
                logger.info(f"{rigname}: Found start joint '{start_jnt}'")
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
    types = [TYPE_BN, TYPE_FK, TYPE_IK]

    # Check if cached joints are still valid
    cache_valid = True
    for i, joints in enumerate(joints_list):
        if rigname in joints:
            # Verify all cached joints still exist
            if not all(cmds.objExists(j) for j in joints[rigname]):
                logger.debug(f'{rigname}: Cached {types[i]} joints invalid, rebuilding')
                cache_valid = False
                del joints[rigname]
        else:
            cache_valid = False

    # If all caches valid, skip rebuild
    if cache_valid:
        logger.info(f"{rigname}: Using cached joints (all valid)")
        return

    # Detect or validate start joint
    if not start_jnt:
        start_jnt = rt_nam.fstr(rigname, JOINT, TYPE_BN, 0)
        logger.info(f"{rigname}: Auto-detect start_jnt: {start_jnt}")

    if not cmds.objExists(start_jnt):
        logger.error(f"{rigname}: start_jnt '{start_jnt}' does not exist")
        return
    if end_jnt and not cmds.objExists(end_jnt):
        logger.error(f"{rigname}: end_jnt '{end_jnt}' does not exist")
        return

    logger.info(f"{rigname}: Setting joints - start:{start_jnt} end:{end_jnt}")

    joint_chain = rt_jnt.get_joint_chain(start_jnt, end_jnt)
    if not joint_chain:
        logger.error(f"{rigname}: No joints found")
        return
    logger.debug(f'Joint Chain: {joint_chain}')

    # Create/rename joints - always create BN
    rt_cst.JOINTS_BN[rigname] = create_rename_joints(rigname, joint_chain, TYPE_BN)
    logger.info(f'{rigname}: Processed {TYPE_BN} joints: {len(rt_cst.JOINTS_BN[rigname])} joints')
    # Create IK/FK joints here since setup runs before build
    rt_cst.JOINTS_FK[rigname] = create_rename_joints(rigname, rt_cst.JOINTS_BN[rigname], TYPE_FK)
    rt_cst.JOINTS_IK[rigname] = create_rename_joints(rigname, rt_cst.JOINTS_BN[rigname], TYPE_IK)


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
    logger.debug(f"rigname:'{rigname}' joints:'{typ}'")

    # BN: rename in place
    if typ == TYPE_BN:
        out = []
        for jnt in joints:
            NN = rt_nam.get_index_from_name(jnt)
            new_name = rt_nam.fstr(rigname, JOINT, TYPE_BN, NN)
            if jnt != new_name:
                jnt = cmds.rename(jnt, new_name)
            if NN == 'ee':
                break
            out.append(jnt)
        return out

    # FK/IK: duplicate BN hierarchy
    # BN root must already exist
    bn_root = joints[0]
    target_root = rt_nam.fstr(rigname, JOINT, typ, 0)

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
        new_name = rt_nam.fstr(rigname, JOINT, typ, NN)
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
        logger.info(f"Renamed '{source}' -> '{target}'")
    else:
        logger.debug(f"Cancel rename '{source}' -> '{target}'. Source '{source}' does not exist.")

def rename_components():
    '''
    Rename IK related controls and attributes from old naming convention.
    Handles legacy rig component names for compatibility.

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

    dag_nodes = cmds.ls(dag=True)
    transforms = cmds.ls(dag_nodes, type='transform')

    # Remove old IKFK Switch attributes (change as necessary)
    old_switches = ['hierarchySwitch', 'IKFK Switch']
    for node in transforms:
        for old_switch in old_switches:
            if cmds.attributeQuery(old_switch, n=node, ex=1):
                cmds.deleteAttr(node, at=old_switch)
                rt_mya.add_attribute_enum(node, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
                rt_mya.add_attribute_enum(node, IKFK_SWITCH[0], IKFK_SWITCH[1], IKFK_SWITCH[2], IKFK_SWITCH[3])

    # Rename DAG nodes
    for node in dag_nodes:
        for old_name, new_name in replace_names.items():
            node_name = node.replace(old_name, new_name)
        if node != node_name: # Replace name
            logger.debug(f"Rename DAG node '{node}' -> '{node_name}'")
            cmds.rename(node, node_name)

    # Rename utility nodes
    non_dag_nodes = cmds.ls(dag=False)
    util_nodes = ['condition', 'multiplyDivide', 'plusMinusAverage',
                  'curveInfo', 'pointOnCurveInfo', 'blendTwoAttr',
                  'multDoubleLinear', 'pointMatrixMult', 'setRange', 'clamp']
    for util_typ in util_nodes:
        util_node = cmds.ls(non_dag_nodes, type=util_typ)
        for node in util_node:
            for old_name, new_name in replace_names.items():
                node_name = node.replace(old_name, new_name)
            if node != node_name:
                logger.debug(f"Rename non-DAG node '{node}' -> '{node_name}'")
                cmds.rename(node, node_name)
