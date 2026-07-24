'''
# rig_tail_connect.py
author: Daisy Jane @gnitemouse

Final wiring phase of the build. rig_tail creates the pieces (joints,
curves, clusters, controls, node networks); connect_rig_tail() then
assembles them into one working rig. For each rig part it:

    - Parents the FK/IK systems into the rig hierarchy and constrains
      them to the base control.
    - Creates the switch and channel-box attributes: the per-tail IKFK
      switch lives on the cog control, and stretch/twist/animation
      attributes live on the base control, mirrored onto every control
      as proxy attributes. With the Main Controller dashboard active
      (rig_tail_mainctrl) the cog also carries ALL values and per-tail
      override flags, and consumers read rt_mc.resolved_plug() /
      rt_mc.ikfk_driver() instead of the basectrl/cog plug directly.
    - Wires the IKFK mode switching. Each curve cluster is parent-
      constrained to one control per mode (spline/ik/float), and set
      driven keys on the switch attribute fade constraint weights and
      control/joint visibility so only the active mode has influence.
      The spline set is fixed (bot/mid/top); spline_control_index maps
      its 5 main controls onto however many clusters NUM_CTRL_IK made.
      The FK mode index comes from rt_cst.ikfk_fk_mode_index().
    - Hands off to rig_tail_matrix (BN offsetParentMatrix network),
      rig_tail_anim (FX) and rig_tail_stretch (squash/stretch wiring),
      then binds the geometry to the BN joints.

The control cache avoids repeated get_controls_ik() scene scans within
one build; it is cleared at the start of every build and cleanup.
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import importlib as il

import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
from rig_tail_control import get_controls_ik, spline_control_index
from rig_tail_curve import get_spline_handle
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_cache as rt_cache
import rig_tail_joint as rt_jnt
from rig_tail_matrix import build_matrix_offset_network
import rig_tail_mainctrl as rt_mc
import rig_tail_stretch as rt_str
import rig_tail_anim as rt_ani
import rig_tail_test as rt_test
import re

il.reload(rt_test)
logger = logger_setup(__name__)


# CONTROL CACHE ========================================================

_CONTROL_CACHE = {}

def cache_controls_ik(rigname):
    if rigname not in _CONTROL_CACHE:
        _CONTROL_CACHE[rigname] = get_controls_ik(rigname)
    return _CONTROL_CACHE[rigname]

def clear_control_cache():
    _CONTROL_CACHE.clear()

def get_cached_controls_ik(rigname):
    return cache_controls_ik(rigname)


# CONNECTIONS ==========================================================

def connect_rig_tail(fk, ik):
    logger.info('-----------------------------------------------------')
    logger.info('Connect Rig Components')

    clear_control_cache()

    for rigname in rt_cst.RIGPARTS:
        # Parts without joints were skipped during setup and build
        if rigname not in rt_cst.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping connect")
            continue
        connect_fk(rigname, fk, ik)
        connect_ik(rigname, fk, ik)
        # rt_test.dump_chain()
        build_matrix_offset_network(rigname, fk, ik)
        rt_ani.build_anim_effects(rigname, fk, ik)
        connect_effects(rigname, fk, ik)
        rt_mya.bind_geometry(rigname)
        # rt_test.dump_chain()

    # After everything is connected the IK spline has reached its final
    # (low-CV driver) shape, so the IK joints now read their true rest -- match
    # FK onto it so the two modes agree and the tail does not pop on a switch.
    match_fk_to_ik_rest(fk, ik)

    # The mode SDKs are wired now; tuck the internal resolved IKFK
    # drivers out of the cog channel box (no-op when the dashboard is off)
    rt_mc.hide_resolved_attrs()

    # Rig joints are driven by the rig, never keyed directly: make every
    # joint channel non-keyable (shown but not settable) so animators cannot
    # accidentally key them. Not locked -- the OPM/constraint/SDK
    # connections that drive the joints must stay intact.
    rt_mya.finalize_joint_channels(keyable=False)

    logger.debug('DONE Connected Rig Components')
    logger.debug('-----------------------------------------------------')

def match_fk_to_ik_rest(fk, ik):
    '''
    Snap each FK joint onto its IK-solved counterpart so FK and IK share one
    rest pose and the tail does not pop when the ikfk switch moves between them.

    Runs at the END of the build (called from connect_rig_tail). The IK spline
    only reaches its final low-CV shape once the IK system is fully connected,
    so this is the first point the IK joints reliably read their true rest -- an
    earlier read, even after a forced eval, catches the sharper raw-solver pose
    (~more bend) that the joints briefly sit on.

    The FK joint sits at the bottom of the variable-FK SDK stack, and the
    controls drive the groups ABOVE it, so moving the joint itself shifts only
    the rest -- the controls still animate from there. Matched base-to-tip.

    Only runs when both systems are built. FK-only / IK-only builds keep their
    own rest (there is no other mode to pop against).

    Arguments
        fk (bool): FK was built
        ik (bool): IK was built
    '''
    if not (fk and ik):
        return
    # The IK system is fully wired now, so a forced evaluation settles the
    # spline onto its final shape before we read it (unlike mid-build, where the
    # driver->solver network was not yet complete and no eval could settle it).
    cmds.dgdirty(allPlugs=True)
    try:
        cmds.refresh(force=True)
    except Exception as e:
        logger.trace(f'refresh before FK match skipped: {e}')
    for rigname in rt_cst.RIGPARTS:
        fk_joints = rt_cst.JOINTS_FK.get(rigname, [])
        ik_joints = rt_cst.JOINTS_IK.get(rigname, [])
        if not fk_joints or not ik_joints or len(fk_joints) != len(ik_joints):
            continue
        logger.debug(f'{rigname}: Match FK rest to the settled IK rest')
        for fk_jnt, ik_jnt in zip(fk_joints, ik_joints):
            if cmds.objExists(fk_jnt) and cmds.objExists(ik_jnt):
                cmds.matchTransform(fk_jnt, ik_jnt, pos=True, rot=True)

def connect_root(fk, ik):
    root_ctrl = rt_nam.fstr('', rt_cst.ROOT_CTRL)
    geometry_grp = rt_nam.fstr('', rt_cst.GEOMETRY_GRP)
    control_grp = rt_nam.fstr('', rt_cst.CONTROL_GRP)
    rig_systems_grp = rt_nam.fstr('', rt_cst.RIG_SYSTEMS_GRP)
    skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP)
    clusters_grp = rt_nam.fstr('', rt_cst.CLUSTERS_GRP)
    logger.debug(f'Connect root \'{root_ctrl}\'')
    rt_mya.parent_to(root_ctrl, control_grp)

    if fk and ik:
        ik_skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_IK)
        fk_skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_FK)
        rootctrl_attrs = [
            ('divider', 'visibilityDivider', 'VISIBILITY', 0),
            (geometry_grp, 'geo', 'Geometry', 1),
            (control_grp, 'controls', 'Controls', 1),
            (rig_systems_grp, 'rig_systems', 'Rig Systems', 0),
            (skeleton_grp, 'skeleton', 'Skeleton', 1),
            (ik_skeleton_grp, 'ik_skeleton', 'IK Skeleton', 1),
            (fk_skeleton_grp, 'fk_skeleton', 'FK Skeleton', 1),
            (clusters_grp, 'clusters', 'Clusters', 0),
            ('divider', 'dispDivider', 'DISPLAY', 0)
        ]
    else:
        rootctrl_attrs = [
            ('divider', 'visibilityDivider', 'VISIBILITY', 0),
            (geometry_grp, 'geo', 'Geometry', 1),
            (control_grp, 'controls', 'Controls', 1),
            (rig_systems_grp, 'rig_systems', 'Rig Systems', 0),
            (skeleton_grp, 'skeleton', 'Skeleton', 0),
            (clusters_grp, 'clusters', 'Clusters', 0),
            ('divider', 'dispDivider', 'DISPLAY', 0)
        ]

    for group, ln_attr, nn_attr, dv in rootctrl_attrs:
        rt_mya.add_attribute_enum(root_ctrl, ln_attr, nn_attr, dv=dv)
        if group != 'divider':
            if cmds.objExists(group):
                cmds.connectAttr(f'{root_ctrl}.{ln_attr}', f'{group}.visibility', f=1)
            else:
                logger.warning(f'Group \'{group}\' does not exist, skipping visibility connection.')

    if cmds.objExists(geometry_grp):
        rt_mya.add_attribute_enum(root_ctrl, ln='export_geo', nn='Export Geometry',
                           en='Unlocked:Wireframe:Locked', dv=0)
        # Some incoming scenes (modeling/autorigger) drop the geometry group
        # into a display layer (e.g. 'disp_geo'). That layer drives
        # geometry.drawOverride, which both (a) forces overrideEnabled on -- so
        # the export_geo block below gets skipped -- and (b) ANDs the layer's
        # own visibility into the mesh, so root_ctrl.geo -> geometry.v can't
        # actually show it. Evict the group back to the default layer so the
        # rig owns its own visibility + display-type overrides. (We don't
        # create this layer, so there's nothing to preserve on our side.)
        member_layers = cmds.listConnections(
            geometry_grp, type='displayLayer', s=True, d=False) or []
        for layer in set(member_layers):
            if layer != 'defaultLayer':
                cmds.editDisplayLayerMembers('defaultLayer', geometry_grp,
                                             noRecurse=True)
                logger.info(
                    f"Removed '{geometry_grp}' from display layer '{layer}' so "
                    f"the root ctrl can drive geometry visibility/display.")
                break
        # Display overrides are cosmetic: if overrideEnabled is still locked or
        # driven after the eviction above (unexpected), leave the existing
        # setup in place instead of failing the build.
        if cmds.getAttr(f'{geometry_grp}.overrideEnabled', settable=True):
            cmds.setAttr(f'{geometry_grp}.overrideEnabled', 1)
            # Enabling the override activates whatever overrideVisibility the
            # group came in with. Modeling/autorigger scenes sometimes leave
            # it OFF, which then hides all geometry once we switch the
            # override on. Force it visible (the Show/Hide toggle already
            # lives on the separate .visibility -> geometry.v channel).
            if cmds.getAttr(f'{geometry_grp}.overrideVisibility', settable=True):
                cmds.setAttr(f'{geometry_grp}.overrideVisibility', 1)
            cmds.connectAttr(f'{root_ctrl}.export_geo',
                             f'{geometry_grp}.overrideDisplayType', f=1)
            cmds.setAttr(f'{root_ctrl}.export_geo', 2)
        else:
            logger.warning(
                f"'{geometry_grp}.overrideEnabled' is locked or driven "
                f"(display layer?); skipping export_geo display override.")

def connect_cog(fk, ik):
    root_ctrl = rt_nam.fstr('', rt_cst.ROOT_CTRL)
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    logger.debug(f'Connect cog \'{cog_ctrl}\'')
    rt_mya.parent_to(cog_ctrl, root_ctrl)

    if ik:
        add_attributes_ikfk_switch(cog_ctrl, fk, ik)

    # Main controller dashboard: ALL values + per-tail override flags
    if rt_mc.active():
        rt_mc.add_dashboard_to_cog(cog_ctrl, fk, ik)

def connect_basectrl(rigname, fk, ik):
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    basectrl_grp = rt_nam.fstr(rigname, rt_cst.BASECTRL_GRP)
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    logger.debug(f'{rigname}: Connect basectrl \'{basectrl}\'')

    rt_mya.parent_to(basectrl_grp, cog_ctrl)

    # Channel box order: OVERRIDE ALL (dashboard only), IKFK, STRETCH,
    # TWIST, ANIMATION, JNT SCALE
    # (STRETCH attributes always come before TWIST attributes)
    if rt_mc.active():
        rt_mc.add_override_to_basectrl(rigname, basectrl)
    if ik:
        add_ikfk_attributes_to_basectrl(rigname, basectrl)
    rt_str.add_stretch_attributes_to_basectrl(rigname, basectrl)
    if ik:
        add_twist_attributes_to_basectrl(rigname, basectrl)
    rt_ani.add_anim_attributes_to_basectrl(rigname, basectrl)
    rt_str.add_jntscale_attributes_to_basectrl(rigname, basectrl)

    # Route every dashboard attribute through its override condition
    # (local basectrl value vs cog ALL value). Runs here, in the setup
    # phase, so the connect phase (stretch remaps, spline handle, FX
    # expressions, mode SDKs) can read the resolved plugs.
    if rt_mc.active():
        rt_mc.build_override_conditions(rigname, fk, ik)


# CONNECT FK ===========================================================

def connect_fk(rigname, fk, ik):
    if not fk:
        return
    logger.debug(f'{rigname}: Connect FK')

    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    fkroot_grp = rt_nam.fstr(rigname, rt_cst.CTRLROOT_GRP, rt_cst.TYPE_FK)
    fkjnt_grp = rt_nam.fstr(rigname, rt_cst.GROUP, rt_cst.TYPE_FK)

    cmds.parentConstraint(basectrl, fkjnt_grp, mo=1)
    connect_spline_fk(rigname)

    # Wire the individual (per-joint) FK controls to the joint SDK layer.
    # Only built when INDIV_FK is enabled; otherwise the SDK_JNT layer is
    # left as an identity group and joints follow the variable-FK controls.
    if rt_cst.INDIV_FK:
        for i, jnt in enumerate(rt_cst.JOINTS_FK[rigname]):
            fk_ctrl = rt_nam.fstr(rigname, rt_cst.CONTROL, rt_cst.TYPE_FK, i)
            fk_ctrl_grp = rt_nam.fstr(rigname, rt_cst.CTRL_GRP, rt_cst.TYPE_FK, i)
            last_sdk = rt_nam.fstr(rigname, rt_cst.SDK_GRP, rt_cst.TYPE_FK, i, rt_cst.NUM_CTRL_FK)
            sdk_grp = rt_nam.fstr(rigname, rt_cst.SDK_JNT, rt_cst.TYPE_FK, i)

            if not cmds.objExists(f'{fk_ctrl_grp}_parentConstraint1'):
                cmds.parentConstraint(last_sdk, fk_ctrl_grp)

            cmds.connectAttr(f'{fk_ctrl}.rotate', f'{sdk_grp}.rotate', f=1)

    if ik:
        fk_skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_FK)
        rt_mya.parent_to(fkjnt_grp, fk_skeleton_grp)

        for i in range(rt_cst.NUM_CTRL_FK):
            fk_ctrl = rt_nam.fstr(rigname, rt_cst.CONTROL, '', i+1)
            add_proxy_attributes_to_controls(rigname, fk_ctrl, rt_cst.TYPE_FK)

        setup_switch_fk(rigname, fkroot_grp, fkjnt_grp)
    else:
        skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP)
        rt_mya.parent_to(fkjnt_grp, skeleton_grp)

def connect_spline_fk(rigname):
    curve_fk = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_FK)
    spline_grp_fk = rt_nam.fstr(rigname, rt_cst.SPLINE_GRP, rt_cst.TYPE_FK)
    rt_mya.parent_to(curve_fk, spline_grp_fk)


# CONNECT IK ===========================================================

def connect_ik(rigname, fk, ik):
    if not ik:
        return
    logger.debug(f'{rigname}: Connect IK')

    ik_skeleton_grp = rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_IK)
    ikjnt_grp = rt_nam.fstr(rigname, rt_cst.GROUP, rt_cst.TYPE_IK)

    if not cmds.objExists(ikjnt_grp):
        ikjnt_grp = cmds.group(em=True, n=ikjnt_grp)
        rt_mya.parent_to(ikjnt_grp, ik_skeleton_grp)
    rt_mya.parent_to(rt_cst.JOINTS_IK[rigname][0], ikjnt_grp)
    rt_mya.parent_to(ikjnt_grp, ik_skeleton_grp)

    spline_constraints = constrain_spline_controls(rigname)
    setup_switch_ik(rigname, ikjnt_grp, spline_constraints)
    setup_switch_upvec(rigname)
    connect_spline_ik(rigname)

    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    for i in range(rt_cst.NUM_CTRL_IK):
        add_proxy_attributes_to_controls(rigname, ik_controls['ik'][i], rt_cst.TYPE_IK)
        add_proxy_attributes_to_controls(rigname, ik_controls['float'][i], rt_cst.TYPE_IK)
    for spline_ctrl in ik_controls['spline']:
        add_proxy_attributes_to_controls(rigname, spline_ctrl, rt_cst.TYPE_IK)

def connect_spline_ik(rigname):
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    logger.trace(f'ik_controls {ik_controls} ik_ctrlgrps {ik_ctrlgrps}')

    spline_grp_ik = rt_nam.fstr(rigname, rt_cst.SPLINE_GRP, rt_cst.TYPE_IK)
    driver_curve = rt_nam.fstr(rigname, rt_cst.CURVE, rt_cst.TYPE_IK)
    # get_spline_handle returns [] when the handle is missing or orphaned
    # (no joint list). Abort this part's IK connect with a clear message
    # rather than crashing on a 3-way unpack of an empty list.
    spline_info = get_spline_handle(rigname)
    if not spline_info:
        logger.warning(f'{rigname}: No valid spline handle to connect; '
                       f'skipping IK spline connect')
        return
    ikhandle, effector, solver_curve = spline_info
    rt_mya.parent_to(ikhandle, spline_grp_ik)
    rt_mya.parent_to(solver_curve, spline_grp_ik)
    rt_mya.parent_to(driver_curve, spline_grp_ik)

    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    contents = list()
    contents.append(ik_ctrlgrps['ik'][0])
    contents.extend(ik_ctrlgrps['float'])
    contents.extend([ik_ctrlgrps['spline'][0], ik_ctrlgrps['spline'][-1]])
    for obj in contents:
        rt_mya.parent_to(obj, basectrl)

    spline_handle = rt_nam.fstr(rigname, rt_cst.SPLINE_HANDLE, rt_cst.TYPE_IK)
    if cmds.objExists(spline_handle):
        for attr in ['twist', 'roll', 'offset']:
            cmds.connectAttr(rt_mc.resolved_plug(rigname, attr),
                             f'{spline_handle}.{attr}', f=1)


# CONNECT EFFECTS =====================================================

def connect_effects(rigname, fk, ik):
    logger.debug(f'{rigname}: Connect animation effects')

    if rt_cst.EFFECTS['stretchy']:
        connect_stretch(rigname, fk, ik)

def connect_stretch(rigname, fk, ik):
    logger.debug(f'{rigname}: Connect stretch')
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)

    stretch_remap = f'{rigname}_stretch_remap_multiplyDivide'
    squash_remap = f'{rigname}_squash_remap_multiplyDivide'

    if not cmds.objExists(stretch_remap):
        logger.warning(f'Stretch remap node not found: {stretch_remap}')
        return
    if not cmds.objExists(squash_remap):
        logger.warning(f'Squash remap node not found: {squash_remap}')
        return

    # resolved_plug: the override condition output when the dashboard is
    # active, the basectrl attribute otherwise
    if cmds.attributeQuery('stretch', n=basectrl, ex=1):
        cmds.connectAttr(rt_mc.resolved_plug(rigname, 'stretch'),
                         f'{stretch_remap}.input1X', f=1)
    if cmds.attributeQuery('squash', n=basectrl, ex=1):
        cmds.connectAttr(rt_mc.resolved_plug(rigname, 'squash'),
                         f'{squash_remap}.input1X', f=1)

    rt_str.connect_stretch_to_joints(rigname, basectrl, fk, ik)


# ATTRIBUTES ===========================================================

def add_ikfk_attributes_to_basectrl(rigname, basectrl):
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    ikfk_switch = rt_nam.fstr(rigname, rt_cst.IKFK)
    rt_mya.add_attribute_enum(basectrl, rt_cst.IKFK_DIVIDER[0], rt_cst.IKFK_DIVIDER[1], rt_cst.IKFK_DIVIDER[2])
    rt_mya.add_attribute_enum(basectrl, rt_cst.IKFK_SWITCH[0], rt_cst.IKFK_SWITCH[1],
                       pxy=f'{cog_ctrl}.{ikfk_switch}')

def add_twist_attributes_to_basectrl(rigname, basectrl):
    rt_mya.add_attribute_enum(basectrl, rt_cst.TWIST_DIVIDER[0], rt_cst.TWIST_DIVIDER[1], rt_cst.TWIST_DIVIDER[2])

    for attr in ['twist', 'roll', 'offset']:
        if not cmds.attributeQuery(attr, n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln=attr, at='float', k=1, dv=0)

def add_attributes_ikfk_switch(control, fk, ik):
    # The autorigger leaves its own empty switch divider on the cog
    # (ln 'ikfkDivider', labeled TAIL IKFK); ours takes over that label,
    # so drop the stale one rather than showing two dividers
    if cmds.attributeQuery('ikfkDivider', n=control, ex=1):
        rt_mya.remove_attribute(control, 'ikfkDivider')

    rt_mya.add_attribute_enum(control, rt_cst.TAIL_IKFK_DIVIDER[0], rt_cst.TAIL_IKFK_DIVIDER[1], rt_cst.TAIL_IKFK_DIVIDER[2])

    for rigname in rt_cst.RIGPARTS:
        ln_ikfk = rt_nam.fstr(rigname, rt_cst.IKFK)
        nn_ikfk = re.sub(r'[-_\s]+', ' ', ln_ikfk).title()
        rt_mya.add_attribute_enum(control, ln_ikfk, nn_ikfk, rt_cst.IKFK_SWITCH[2], rt_cst.IKFK_SWITCH[3])

def add_proxy_attributes_to_controls(rigname, control, typ):
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)

    ikfk_switch = rt_nam.fstr(rigname, rt_cst.IKFK)
    rt_mya.add_attribute_enum(control, rt_cst.IKFK_DIVIDER[0], rt_cst.IKFK_DIVIDER[1], rt_cst.IKFK_DIVIDER[2])
    rt_mya.add_attribute_enum(control, rt_cst.IKFK_SWITCH[0], rt_cst.IKFK_SWITCH[1],
                       pxy=f'{cog_ctrl}.{ikfk_switch}')

    # STRETCH proxies always come before TWIST proxies
    if rt_cst.EFFECTS['stretchy']:
        rt_mya.add_attribute_enum(control, rt_cst.STRETCH_DIVIDER[0], rt_cst.STRETCH_DIVIDER[1], rt_cst.STRETCH_DIVIDER[2])
        for atr, nice in [('stretch', 'Stretch'), ('squash', 'Squash'),
                          ('preserveVolume', 'Preserve Volume')]:
            rt_mya.add_attribute_enum(control, ln=atr, nn=nice, pxy=f'{basectrl}.{atr}')

    # Twist attributes exist on the basectrl whenever IK is built,
    # independent of stretchy
    if typ == rt_cst.TYPE_IK:
        rt_mya.add_attribute_enum(control, rt_cst.TWIST_DIVIDER[0], rt_cst.TWIST_DIVIDER[1], rt_cst.TWIST_DIVIDER[2])
        for atr in ['twist', 'roll', 'offset']:
            rt_mya.add_attribute_enum(control, ln=atr, nn=rt_nam.titlecase(atr), pxy=f'{basectrl}.{atr}')


# CONSTRAINTS ==========================================================

def constrain_spline_controls(rigname, typ=rt_cst.TYPE_IK):
    logger.trace(f"{rigname}: Constrain clusters to spline controls")
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)

    cluster_handles = list()
    for NN in range(1, rt_cst.NUM_CTRL_IK+1):
        cluster_handle = rt_nam.fstr(rigname, rt_cst.CLUSTER_HANDLE, typ, NN)
        cluster_handles.append(cluster_handle)
    logger.trace(f'Get cluster handles for spline constraint:\n{cluster_handles}')

    # Constraint targets per cluster: W0=spline, W1=ik, W2=float.
    # IK and Float controls map 1:1 to clusters; the spline target
    # comes from the fixed bot/mid/top set via spline_control_index.
    num_clusters = len(cluster_handles)
    spline_constraints = list()
    for i, clstr in enumerate(cluster_handles):
        spline_ctrl = ik_controls['spline'][spline_control_index(i, num_clusters)]
        constrain_objs = [spline_ctrl,
                          ik_controls['ik'][i],
                          ik_controls['float'][i],
                          clstr]
        cluster_constr = cmds.parentConstraint(constrain_objs)[0]
        spline_constraints.append(cluster_constr)
    logger.trace(f'constraints {spline_constraints}')

    cmds.parentConstraint(ik_controls['spline'][0], ik_controls['spline'][4], ik_ctrlgrps['spline'][2], mo=1)

    return spline_constraints


# IKFK MODE SWITCH =====================================================

def setup_switch_fk(rigname, fkroot_grp, fkjnt_grp):
    # ikfk_driver: the hidden resolved attr (ALL vs local, picked by the
    # override flag) when the dashboard is active, the per-tail cog
    # switch otherwise
    ikfk_attr = rt_mc.ikfk_driver(rigname)
    fk_mode = rt_cst.ikfk_fk_mode_index() # Get index of FK mode
    if fk_mode is None:
        logger.warning(f"{rigname}: No 'FK' mode in IKFK_MODES, skip FK switch")
        return

    for mode in range(len(rt_cst.IKFK_MODES)):
        v = 1 if mode == fk_mode else 0
        rt_mya.sdk(ikfk_attr, f'{fkroot_grp}.visibility', dv=mode, v=v)
        rt_mya.sdk(ikfk_attr, f'{fkjnt_grp}.visibility', dv=mode, v=v)

def setup_switch_ik(rigname, ikjnt_grp, spline_constraints):
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    ikfk_attr = rt_mc.ikfk_driver(rigname)
    fk_mode = rt_cst.ikfk_fk_mode_index() # Get index of FK mode
    num_clusters = len(spline_constraints)

    # Constraint weights: each cluster constraint has targets
    # W0=spline, W1=ik, W2=float; only the active mode's target weighs in.
    # The spline target per cluster comes from the fixed bot/mid/top set.
    for j in range(num_clusters):
        constraint = spline_constraints[j]
        targets = [ik_controls['spline'][spline_control_index(j, num_clusters)],
                   ik_controls['ik'][j],
                   ik_controls['float'][j]]
        for i, ctrl in enumerate(targets):
            for mode in range(len(rt_cst.IKFK_MODES)):
                v = 1 if mode == i else 0
                rt_mya.sdk(ikfk_attr, f'{constraint}.{ctrl}W{i}', dv=mode, v=v)

    # Control visibility: each control set is only shown in its own mode
    # (ik_ctrlgrps['spline'] includes the mid_rot group)
    for i, ctrltyp in enumerate(['spline', 'ik', 'float']):
        for ctrl_grp in ik_ctrlgrps[ctrltyp]:
            for mode in range(len(rt_cst.IKFK_MODES)):
                v = 1 if (mode == i and mode != fk_mode) else 0
                rt_mya.sdk(ikfk_attr, f'{ctrl_grp}.visibility', dv=mode, v=v)

    for mode in range(len(rt_cst.IKFK_MODES)):
        v = 0 if mode == fk_mode else 1
        rt_mya.sdk(ikfk_attr, f'{ikjnt_grp}.visibility', dv=mode, v=v)

def setup_switch_upvec(rigname, typ=rt_cst.TYPE_IK):
    logger.trace(f"{rigname}: Space switching for upvec")
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    ikfk_attr = rt_mc.ikfk_driver(rigname)

    upvec_bsectrl = ik_controls['upvec'][0]
    upvec_endctrl = ik_controls['upvec'][1]
    upvec_bsegrp = ik_ctrlgrps['upvec'][0]
    upvec_endgrp = ik_ctrlgrps['upvec'][1]

    # Base follows the first control of each set, end follows the last.
    # spline[4] is the fixed 'top' control; ik/float vary with NUM_CTRL_IK.
    base_parents = [
        ik_controls['spline'][0],
        ik_controls['ik'][0],
        ik_controls['float'][0]
    ]
    end_parents = [
        ik_controls['spline'][4],
        ik_controls['ik'][-1],
        ik_controls['float'][-1]
    ]

    base_constr = cmds.parentConstraint(base_parents, upvec_bsegrp, mo=1)[0]
    end_constr = cmds.parentConstraint(end_parents, upvec_endgrp, mo=1)[0]
    upvec_SDKs = {
        base_constr: base_parents,
        end_constr: end_parents
    }

    fk_mode = rt_cst.ikfk_fk_mode_index()
    for constr, parents in upvec_SDKs.items():
        for i, parent in enumerate(parents):
            for mode in range(len(rt_cst.IKFK_MODES)):
                v = 1 if (mode == i and mode != fk_mode) else 0
                rt_mya.sdk(ikfk_attr, f'{constr}.{parent}W{i}', dv=mode, v=v)

    for grp in (upvec_bsegrp, upvec_endgrp):
        for mode in range(len(rt_cst.IKFK_MODES)):
            v = 0 if mode == fk_mode else 1
            rt_mya.sdk(ikfk_attr, f'{grp}.visibility', dv=mode, v=v)

    cluster_handle_bse = rt_nam.fstr(rigname, rt_cst.CLUSTER_UPV_HANDLE, typ, TAG='base')
    cluster_handle_end = rt_nam.fstr(rigname, rt_cst.CLUSTER_UPV_HANDLE, typ, TAG='end')

    cmds.parentConstraint(upvec_bsectrl, cluster_handle_bse)
    cmds.parentConstraint(upvec_endctrl, cluster_handle_end)
