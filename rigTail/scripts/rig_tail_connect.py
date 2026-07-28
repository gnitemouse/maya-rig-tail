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
      (rig_tail_ctrlall) the cog also carries ALL values and per-tail
      override flags, and consumers read rt_ca.resolved_plug() /
      rt_ca.ikfk_driver() instead of the basectrl/cog plug directly.
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
from rig_tail_control import get_controls_ik, spline_control_index
from rig_tail_curve import get_spline_handle
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import rig_tail_cache as rt_cache
import rig_tail_joint as rt_jnt
from rig_tail_matrix import build_matrix_offset_network
import rig_tail_ctrlall as rt_ca
import rig_tail_stretch as rt_str
import rig_tail_anim as rt_ani
import rig_tail_fk as rt_fk
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

    parts = rt_cache.active_parts()
    for rigname in parts:
        # Parts without joints were skipped during setup and build
        if rigname not in rt_cst.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping connect")
            continue
        # Step timings (rt_mya.timed) report on the build_timer's second
        # line, so a slow connect phase says which step it was slow in
        with rt_mya.timed('connect.fk'):
            connect_fk(rigname, fk, ik)
        with rt_mya.timed('connect.ik'):
            connect_ik(rigname, fk, ik)
        # rt_test.dump_chain()
        with rt_mya.timed('connect.matrix'):
            build_matrix_offset_network(rigname, fk, ik)
        with rt_mya.timed('connect.fx'):
            rt_ani.build_anim_effects(rigname, fk, ik)
            connect_effects(rigname, fk, ik)
        with rt_mya.timed('connect.bind'):
            rt_mya.bind_geometry(rigname)
        # rt_test.dump_chain()

    # Consolidated warning for parts whose mesh name did not match, so the
    # geometry that never bound is easy to spot and rename.
    rt_mya.report_missing_geometry(parts)

    # After everything is connected the IK spline has reached its final
    # (low-CV driver) shape, so the IK joints now read their true rest -- match
    # FK onto it so the two modes agree and the tail does not pop on a switch.
    # Timed separately: it forces a full-scene dirty and evaluation, which is
    # the one step here whose cost is set by the whole scene rather than by
    # this rig.
    with rt_mya.timed('connect.match_rest'):
        match_fk_to_ik_rest(fk, ik)

    # The mode SDKs are wired now; tuck the internal resolved IKFK
    # drivers out of the cog channel box (no-op when the dashboard is off)
    rt_ca.hide_resolved_attrs()

    # Rig joints are driven by the rig, never keyed directly: make every
    # joint channel non-keyable (shown but not settable) so animators cannot
    # accidentally key them. Not locked -- the OPM/constraint/SDK
    # connections that drive the joints must stay intact.
    with rt_mya.timed('connect.joint_channels'):
        rt_mya.finalize_joint_channels(keyable=False)

    # Colour the three skeletons by type (BN blue, IK orange, FK purple)
    # so they read apart in the viewport.
    if getattr(rt_cst, 'COLOR_SKELETON', True):
        with rt_mya.timed('connect.color'):
            rt_mya.color_skeletons()

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
    # force_refresh punches through build_performance_scope's suspended
    # viewport refresh (temporarily resumes it), so the settle still
    # happens when the build runs inside the fast scope
    rt_mya.force_refresh()
    for rigname in rt_cache.active_parts():
        fk_joints = rt_cst.JOINTS_FK.get(rigname, [])
        ik_joints = rt_cst.JOINTS_IK.get(rigname, [])
        if not fk_joints or not ik_joints or len(fk_joints) != len(ik_joints):
            continue
        logger.debug(f'{rigname}: Match FK rest to the settled IK rest')
        for fk_jnt, ik_jnt in zip(fk_joints, ik_joints):
            if cmds.objExists(fk_jnt) and cmds.objExists(ik_jnt):
                cmds.matchTransform(fk_jnt, ik_jnt, pos=True, rot=True)

def rootctrl_attr_specs(fk, ik, root_ctrl):
    '''
    The root control's VISIBILITY / DISPLAY attributes, in channel-box order.

    One ordered list drives creation, ordering and the post-build values, so
    the three cannot drift apart. Every build lands on this exact state:
    geometry and controls shown, everything structural hidden, and the
    export-geometry display override unlocked.

    'Locators' is only included when the control already carries it. It
    belongs to the incoming autorigger rig - this rig builds no locators
    group for it to drive - so it is positioned and defaulted where it
    exists, rather than invented as a dead attribute in a scene that never
    had one.

    Arguments
        fk (bool): FK is being built
        ik (bool): IK is being built
        root_ctrl (str): the root control

    Return
        list: (ln, nn, group, value, en) per attribute, in order. group is
        None for attributes that drive nothing; value is None for dividers
        (locked, nothing to set); en is None for the 'Hide:Show' default.
    '''
    specs = [
        ('visibilityDivider', 'VISIBILITY', None, None, None),
        ('geo', 'Geometry', rt_nam.fstr('', rt_cst.GEOMETRY_GRP), 1, None),
        ('controls', 'Controls', rt_nam.fstr('', rt_cst.CONTROL_GRP), 1, None),
        ('rig_systems', 'Rig Systems',
         rt_nam.fstr('', rt_cst.RIG_SYSTEMS_GRP), 0, None),
        ('skeleton', 'Skeleton', rt_nam.fstr('', rt_cst.SKELETON_GRP), 0, None),
    ]
    if fk and ik:
        specs += [
            ('ik_skeleton', 'IK Skeleton',
             rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_IK), 0, None),
            ('fk_skeleton', 'FK Skeleton',
             rt_nam.fstr('', rt_cst.SKELETON_GRP, rt_cst.TYPE_FK), 0, None),
        ]
    if cmds.attributeQuery('locators', n=root_ctrl, ex=1):
        specs.append(('locators', 'Locators', None, 0, None))
    specs += [
        # Clusters is a visibility toggle, so it belongs above the DISPLAY
        # divider. It used to be created after it on some rigs; enforce_
        # attr_order below is what actually moves it back.
        ('clusters', 'Clusters', rt_nam.fstr('', rt_cst.CLUSTERS_GRP), 0, None),
        ('dispDivider', 'DISPLAY', None, None, None),
    ]
    # export_geo is only created when there is a geometry group to drive, so
    # only claim a slot for it then - a reorder must not delete an attribute
    # that the block below would not put back.
    if cmds.objExists(rt_nam.fstr('', rt_cst.GEOMETRY_GRP)):
        specs.append(
            ('export_geo', 'Export Geometry', None, 0, 'Unlocked:Wireframe:Locked'))
    return specs


def enforce_attr_order(node, wanted):
    '''
    Make a node's dynamic attributes appear in the wanted order.

    Maya appends each new attribute to the end of the channel box and offers
    no reorder command, so an attribute introduced by a later version of the
    build keeps whatever position it was first created at - which is how
    'Clusters' ended up under DISPLAY on rigs built before it existed.
    Deleting and re-adding is the only way to move one.

    Only acts when the order is actually wrong, so a rebuild of an
    up-to-date rig neither churns attributes nor drops their values. When it
    does act, the caller must re-add every attribute (and remake its
    connections) straight afterwards.

    The test is that what is already there forms an exact PREFIX of the
    wanted order, not merely that it is in the right relative order: any
    attribute still missing gets appended at the end, so it can only land in
    the right place if every attribute already present precedes it. A scene
    holding just 'geo' and 'controls' therefore does need the rebuild -
    'visibilityDivider' belongs in front of both, and adding it would
    otherwise strand the VISIBILITY heading below them.

    Arguments
        node (str): Node holding the attributes
        wanted (list): attribute long names, in the wanted order

    Return
        bool: True if attributes were deleted and need re-adding
    '''
    wanted_set = set(wanted)
    existing = [a for a in (cmds.listAttr(node, ud=True) or [])
                if a in wanted_set]
    if existing == wanted[:len(existing)]:
        return False
    logger.info(f"'{node}': rebuilding {len(existing)} attribute(s) to fix "
                f'channel box order')
    for attr in existing:
        rt_mya.remove_attribute(node, attr)
    return True


def connect_root(fk, ik):
    root_ctrl = rt_nam.fstr('', rt_cst.ROOT_CTRL)
    geometry_grp = rt_nam.fstr('', rt_cst.GEOMETRY_GRP)
    control_grp = rt_nam.fstr('', rt_cst.CONTROL_GRP)
    logger.debug(f'Connect root \'{root_ctrl}\'')
    rt_mya.parent_to(root_ctrl, control_grp)

    rootctrl_attrs = rootctrl_attr_specs(fk, ik, root_ctrl)
    enforce_attr_order(root_ctrl, [s[0] for s in rootctrl_attrs])

    for ln_attr, nn_attr, group, value, en in rootctrl_attrs:
        # export_geo is created here but wired further down, next to the
        # display-override handling it belongs to.
        if ln_attr == 'export_geo':
            continue
        rt_mya.add_attribute_enum(root_ctrl, ln_attr, nn_attr, en=en,
                                  dv=(0 if value is None else value))
        if group:
            if cmds.objExists(group):
                cmds.connectAttr(f'{root_ctrl}.{ln_attr}', f'{group}.visibility', f=1)
            else:
                logger.warning(f'Group \'{group}\' does not exist, skipping visibility connection.')
        # Force the value, not just the default: a rebuild reuses the
        # existing attribute, which would otherwise keep whatever the
        # animator last set it to.
        if value is not None:
            rt_mya.set_attr_value(f'{root_ctrl}.{ln_attr}', value)

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
            # Unlocked (0). This used to force Locked (2), which left the
            # geometry unselectable in the viewport after every build.
            rt_mya.set_attr_value(f"{root_ctrl}.export_geo", 0)
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
    if rt_ca.active():
        rt_ca.add_dashboard_to_cog(cog_ctrl, fk, ik)

def connect_basectrl(rigname, fk, ik):
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    basectrl_grp = rt_nam.fstr(rigname, rt_cst.BASECTRL_GRP)
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    logger.debug(f'{rigname}: Connect basectrl \'{basectrl}\'')

    rt_mya.parent_to(basectrl_grp, cog_ctrl)

    # Channel box order: OVERRIDE ALL (dashboard only), IKFK, STRETCH,
    # TWIST, ANIMATION, JNT SCALE
    # (STRETCH attributes always come before TWIST attributes)
    if rt_ca.active():
        rt_ca.add_override_to_basectrl(rigname, basectrl)
    if ik:
        add_ikfk_attributes_to_basectrl(rigname, basectrl)
    rt_str.add_stretch_attributes_to_basectrl(rigname, basectrl)
    if ik or fk:
        add_twist_attributes_to_basectrl(rigname, basectrl, fk, ik)
    rt_ani.add_anim_attributes_to_basectrl(rigname, basectrl)
    rt_str.add_jntscale_attributes_to_basectrl(rigname, basectrl)

    # Route every dashboard attribute through its override condition
    # (local basectrl value vs cog ALL value). Runs here, in the setup
    # phase, so the connect phase (stretch remaps, spline handle, FX
    # expressions, mode SDKs) can read the resolved plugs.
    if rt_ca.active():
        rt_ca.build_override_conditions(rigname, fk, ik)


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

    # Twist/roll/offset for FK. Runs after the INDIV_FK block above so it
    # reroutes that block's SDK_JNT connection rather than being
    # overwritten by it. Reads rt_ca.resolved_plug internally, so the cog's
    # ALL values reach FK mode the same way they reach IK.
    rt_fk.connect_twist_roll(rigname, rt_cst.JOINTS_FK[rigname])

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
            cmds.connectAttr(rt_ca.resolved_plug(rigname, attr),
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
        cmds.connectAttr(rt_ca.resolved_plug(rigname, 'stretch'),
                         f'{stretch_remap}.input1X', f=1)
    if cmds.attributeQuery('squash', n=basectrl, ex=1):
        cmds.connectAttr(rt_ca.resolved_plug(rigname, 'squash'),
                         f'{squash_remap}.input1X', f=1)

    rt_str.connect_stretch_to_joints(rigname, basectrl, fk, ik)


# ATTRIBUTES ===========================================================

def add_ikfk_attributes_to_basectrl(rigname, basectrl):
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    ikfk_switch = rt_nam.fstr(rigname, rt_cst.IKFK)
    rt_mya.add_attribute_enum(basectrl, rt_cst.IKFK_DIVIDER[0], rt_cst.IKFK_DIVIDER[1], rt_cst.IKFK_DIVIDER[2])
    rt_mya.add_attribute_enum(basectrl, rt_cst.IKFK_SWITCH[0], rt_cst.IKFK_SWITCH[1],
                       pxy=f'{cog_ctrl}.{ikfk_switch}')

def add_twist_attributes_to_basectrl(rigname, basectrl, fk, ik):
    '''
    twist/roll/offset are created once and drive BOTH modes' networks:
    the IK spline handle's native .twist/.roll/.offset (connect_spline_ik)
    and the FK SDK network (rig_tail_fk.connect_twist_roll). The BN chain
    follows a blendMatrix of the FK and IK drivers (rig_tail_matrix), so
    wiring both is what makes one dial work in whichever mode is active -
    there is no separate switching network to build.

    This list MUST stay in step with rig_tail_ctrlall.routed_attr_specs,
    or the dashboard's ALL values cannot reach the attributes.
    '''
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

    # Start every build in the build-derived default mode: FK when FK was
    # built, otherwise SplineIK (see rt_cst.ikfk_default_index, applied to
    # IKFK_SWITCH by update_ikfk_modes). Set explicitly as well as
    # defaulted, since a rebuild reuses the existing attribute and would
    # otherwise keep whatever mode the switch was left in.
    dv = rt_cst.IKFK_SWITCH[3]
    # The switch is created for the whole roster - an excluded part is not
    # rebuilt but its rig still needs its switch in the channel box. Only
    # the parts actually being rebuilt have their mode reset; an excluded
    # part keeps the mode it was left in, like the rest of its state.
    rebuilt = set(rt_cache.active_parts())
    for rigname in rt_cst.RIGPARTS:
        ln_ikfk = rt_nam.fstr(rigname, rt_cst.IKFK)
        nn_ikfk = re.sub(r'[-_\s]+', ' ', ln_ikfk).title()
        rt_mya.add_attribute_enum(control, ln_ikfk, nn_ikfk, rt_cst.IKFK_SWITCH[2], dv)
        if rigname in rebuilt:
            rt_mya.set_attr_value(f'{control}.{ln_ikfk}', dv)

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
    ikfk_attr = rt_ca.ikfk_driver(rigname)
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
    ikfk_attr = rt_ca.ikfk_driver(rigname)
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
    ikfk_attr = rt_ca.ikfk_driver(rigname)

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
