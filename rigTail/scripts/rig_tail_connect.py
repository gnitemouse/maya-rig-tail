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
      override flags, and consumers read rt_ctrlall.resolved_plug() /
      rt_ctrlall.ikfk_driver() instead of the basectrl/cog plug directly.
    - Wires the IKFK mode switching. Each curve cluster is parent-
      constrained to one control per mode (spline/ik/float), and set
      driven keys on the switch attribute fade constraint weights and
      control/joint visibility so only the active mode has influence.
      The spline set is fixed (bot/mid/top); spline_control_index maps
      its 5 main controls onto however many clusters NUM_CTRL_IK made.
      The FK mode index comes from rt_constants.ikfk_fk_mode_index().
    - Hands off to rig_tail_matrix (BN offsetParentMatrix network),
      rig_tail_anim (FX) and rig_tail_stretch (squash/stretch wiring),
      then binds the geometry to the BN joints.

The control cache avoids repeated rt_control.get_controls_ik() scene scans within
one build; it is cleared at the start of every build and cleanup.
'''

import maya.cmds as cmds
from logger_config import logger_setup

import rig_tail_constants as rt_constants
import rig_tail_control as rt_control
import rig_tail_curve as rt_curve
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_cache as rt_cache
import rig_tail_joint as rt_joint
import rig_tail_matrix as rt_matrix
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_mirror as rt_mirror
import rig_tail_stretch as rt_stretch
import rig_tail_anim as rt_anim
import rig_tail_fk as rt_fk
import re

logger = logger_setup(__name__)


# CONTROL CACHE ========================================================

_CONTROL_CACHE = {}

def cache_controls_ik(rigname):
    if rigname not in _CONTROL_CACHE:
        _CONTROL_CACHE[rigname] = rt_control.get_controls_ik(rigname)
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
        if rigname not in rt_constants.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping connect")
            continue
        # Step timings (rt_maya.timed) report on the build_timer's second
        # line, so a slow connect phase says which step it was slow in
        with rt_maya.timed('connect.fk'):
            connect_fk(rigname, fk, ik)
        with rt_maya.timed('connect.ik'):
            connect_ik(rigname, fk, ik)
        with rt_maya.timed('connect.matrix'):
            rt_matrix.build_matrix_offset_network(rigname, fk, ik)
        with rt_maya.timed('connect.fx'):
            rt_anim.build_anim_effects(rigname, fk, ik)
            connect_effects(rigname, fk, ik)
        with rt_maya.timed('connect.bind'):
            rt_maya.bind_geometry(rigname)

    # Consolidated warning for parts whose mesh name did not match, so the
    # geometry that never bound is easy to spot and rename. Nothing bound
    # at all with BIND_GEOMETRY off, so a report on which meshes missed out
    # would be noise about a step that was never going to run.
    if rt_maya.bind_enabled():
        rt_maya.report_missing_geometry(parts)

    # After everything is connected the IK spline has settled onto its final
    # shape, so the IK joints now read their true rest -- match FK onto it so
    # the two modes agree and the tail does not pop on a switch.
    # Timed separately: it forces a full-scene dirty and evaluation, which is
    # the one step here whose cost is set by the whole scene rather than by
    # this rig.
    with rt_maya.timed('connect.match_rest'):
        match_fk_to_ik_rest(fk, ik)

    # The mode SDKs are wired now; tuck the internal resolved IKFK
    # drivers out of the cog channel box (no-op when the dashboard is off)
    rt_ctrlall.hide_resolved_attrs()

    # Rig joints are driven by the rig, never keyed directly: make every
    # joint channel non-keyable (shown but not settable) so animators cannot
    # accidentally key them. Not locked -- the OPM/constraint/SDK
    # connections that drive the joints must stay intact.
    with rt_maya.timed('connect.joint_channels'):
        rt_maya.finalize_joint_channels(keyable=False)

    # Colour the three skeletons by type (BN blue, IK orange, FK purple)
    # so they read apart in the viewport.
    if getattr(rt_constants, 'COLOR_SKELETON', True):
        with rt_maya.timed('connect.color'):
            rt_maya.color_skeletons()

    logger.debug('DONE Connected Rig Components')
    logger.debug('-----------------------------------------------------')

def match_fk_to_ik_rest(fk, ik):
    '''
    Snap each FK joint onto its IK-solved counterpart so FK and IK share one
    rest pose and the tail does not pop when the ikfk switch moves between them.

    Runs at the END of the build (called from connect_rig_tail). The IK spline
    only settles onto its final shape once the IK system is fully connected --
    in particular once connect_driver_to_solver_curve's rest correction is
    wired, without which the solver curve is still the low-CV driver's
    smoothed shape -- so this is the first point the IK joints reliably read
    their true rest. An earlier read, even after a forced eval, catches the
    raw-solver pose the joints briefly sit on.

    Still needed, and by a smaller margin than it used to be. The IK rest is
    now the joint chain rather than a badly smoothed version of it, but a
    curve with CVs AT the joints approximates rather than interpolates them,
    so the IK joints still settle about 1.7 degrees off at the base of the
    squid C_fintail (it was 21.2). FK sits on the joints exactly, so that
    difference is what would pop on a mode switch without this. It becomes a
    true no-op only if the solver curve is built to interpolate.

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
    rt_maya.force_refresh()
    for rigname in rt_cache.active_parts():
        fk_joints = rt_constants.JOINTS_FK.get(rigname, [])
        ik_joints = rt_constants.JOINTS_IK.get(rigname, [])
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
        ('geo', 'Geometry', rt_naming.fstr('', rt_constants.GEOMETRY_GRP), 1, None),
        ('controls', 'Controls', rt_naming.fstr('', rt_constants.CONTROL_GRP), 1, None),
        ('rig_systems', 'Rig Systems',
         rt_naming.fstr('', rt_constants.RIG_SYSTEMS_GRP), 0, None),
        ('skeleton', 'Skeleton', rt_naming.fstr('', rt_constants.SKELETON_GRP), 0, None),
    ]
    if fk and ik:
        specs += [
            ('ik_skeleton', 'IK Skeleton',
             rt_naming.fstr('', rt_constants.SKELETON_GRP, rt_constants.TYPE_IK), 0, None),
            ('fk_skeleton', 'FK Skeleton',
             rt_naming.fstr('', rt_constants.SKELETON_GRP, rt_constants.TYPE_FK), 0, None),
        ]
    if cmds.attributeQuery('locators', n=root_ctrl, ex=1):
        specs.append(('locators', 'Locators', None, 0, None))
    specs += [
        # Clusters is a visibility toggle, so it belongs above the DISPLAY
        # divider. It used to be created after it on some rigs; enforce_
        # attr_order below is what actually moves it back.
        ('clusters', 'Clusters', rt_naming.fstr('', rt_constants.CLUSTERS_GRP), 0, None),
        ('dispDivider', 'DISPLAY', None, None, None),
    ]
    # export_geo is only created when there is a geometry group to drive, so
    # only claim a slot for it then - a reorder must not delete an attribute
    # that the block below would not put back.
    if cmds.objExists(rt_naming.fstr('', rt_constants.GEOMETRY_GRP)):
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
        rt_maya.remove_attribute(node, attr)
    return True


def connect_root(fk, ik):
    root_ctrl = rt_naming.fstr('', rt_constants.ROOT_CTRL)
    geometry_grp = rt_naming.fstr('', rt_constants.GEOMETRY_GRP)
    control_grp = rt_naming.fstr('', rt_constants.CONTROL_GRP)
    logger.debug(f'Connect root \'{root_ctrl}\'')
    rt_maya.parent_to(root_ctrl, control_grp)

    rootctrl_attrs = rootctrl_attr_specs(fk, ik, root_ctrl)
    enforce_attr_order(root_ctrl, [s[0] for s in rootctrl_attrs])

    for ln_attr, nn_attr, group, value, en in rootctrl_attrs:
        # export_geo is created here but wired further down, next to the
        # display-override handling it belongs to.
        if ln_attr == 'export_geo':
            continue
        rt_maya.add_attribute_enum(root_ctrl, ln_attr, nn_attr, en=en,
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
            rt_maya.set_attr_value(f'{root_ctrl}.{ln_attr}', value)

    if cmds.objExists(geometry_grp):
        rt_maya.add_attribute_enum(root_ctrl, ln='export_geo', nn='Export Geometry',
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
            rt_maya.set_attr_value(f"{root_ctrl}.export_geo", 0)
        else:
            logger.warning(
                f"'{geometry_grp}.overrideEnabled' is locked or driven "
                f"(display layer?); skipping export_geo display override.")

def connect_cog(fk, ik):
    root_ctrl = rt_naming.fstr('', rt_constants.ROOT_CTRL)
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    logger.debug(f'Connect cog \'{cog_ctrl}\'')
    rt_maya.parent_to(cog_ctrl, root_ctrl)

    if ik:
        add_attributes_ikfk_switch(cog_ctrl, fk, ik)

    # Main controller dashboard: ALL values + per-tail override flags
    if rt_ctrlall.active():
        rt_ctrlall.add_dashboard_to_cog(cog_ctrl, fk, ik)

def connect_basectrl(rigname, fk, ik):
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    basectrl_grp = rt_naming.fstr(rigname, rt_constants.BASECTRL_GRP)
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    logger.debug(f'{rigname}: Connect basectrl \'{basectrl}\'')

    rt_maya.parent_to(basectrl_grp, cog_ctrl)

    # Channel box order: OVERRIDE ALL (dashboard only), IKFK, STRETCH,
    # TWIST, ANIMATION, JNT SCALE
    # (STRETCH attributes always come before TWIST attributes)
    if rt_ctrlall.active():
        rt_ctrlall.add_override_to_basectrl(rigname, basectrl)
    if ik:
        add_ikfk_attributes_to_basectrl(rigname, basectrl)
    rt_stretch.add_stretch_attributes_to_basectrl(rigname, basectrl)
    if ik or fk:
        add_twist_attributes_to_basectrl(rigname, basectrl, fk, ik)
    rt_anim.add_anim_attributes_to_basectrl(rigname, basectrl)
    rt_stretch.add_jntscale_attributes_to_basectrl(rigname, basectrl)

    # Route every dashboard attribute through its override condition
    # (local basectrl value vs cog ALL value). Runs here, in the setup
    # phase, so the connect phase (stretch remaps, spline handle, FX
    # expressions, mode SDKs) can read the resolved plugs.
    if rt_ctrlall.active():
        rt_ctrlall.build_override_conditions(rigname, fk, ik)


# CONNECT FK ===========================================================

def connect_fk(rigname, fk, ik):
    if not fk:
        return
    logger.debug(f'{rigname}: Connect FK')

    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    fkroot_grp = rt_naming.fstr(rigname, rt_constants.CTRLROOT_GRP, rt_constants.TYPE_FK)
    fkjnt_grp = rt_naming.fstr(rigname, rt_constants.GROUP, rt_constants.TYPE_FK)

    cmds.parentConstraint(basectrl, fkjnt_grp, mo=1)
    connect_spline_fk(rigname)

    # Wire the individual (per-joint) FK controls to the joint SDK layer.
    # Only built when INDIV_FK is enabled; otherwise the SDK_JNT layer is
    # left as an identity group and joints follow the variable-FK controls.
    if rt_constants.INDIV_FK:
        # Where these controls stand in a mirrored frame
        # (rt_control.mirror_control_frames), the constraint that makes them
        # follow the chain has to keep that offset and the rotation they
        # send on has to be negated to match. Both no-ops without one.
        signs = rt_mirror.control_signs(rigname)
        for i, jnt in enumerate(rt_constants.JOINTS_FK[rigname]):
            fk_ctrl = rt_naming.fstr(rigname, rt_constants.CONTROL, rt_constants.TYPE_FK, i)
            fk_ctrl_grp = rt_naming.fstr(rigname, rt_constants.CTRL_GRP, rt_constants.TYPE_FK, i)
            last_sdk = rt_naming.fstr(rigname, rt_constants.SDK_GRP, rt_constants.TYPE_FK, i, rt_constants.NUM_CTRL_FK)
            sdk_grp = rt_naming.fstr(rigname, rt_constants.SDK_JNT, rt_constants.TYPE_FK, i)

            if not cmds.objExists(f'{fk_ctrl_grp}_parentConstraint1'):
                cmds.parentConstraint(last_sdk, fk_ctrl_grp, mo=bool(signs))

            rotation_plug = f'{fk_ctrl}.rotate'
            mirror_node = f'{rt_constants.TYPE_FK}_{rigname}_{i:02d}_ctrlmirror_multiplyDivide'
            if signs:
                if not cmds.objExists(mirror_node):
                    cmds.createNode('multiplyDivide', n=mirror_node, s=1, ss=1)
                    cmds.setAttr(f'{mirror_node}.operation', 1)  # multiply
                cmds.connectAttr(rotation_plug, f'{mirror_node}.input1', f=1)
                cmds.setAttr(f'{mirror_node}.input2',
                             signs['X'], signs['Y'], signs['Z'], type='double3')
                rotation_plug = f'{mirror_node}.output'
            elif cmds.objExists(mirror_node):
                rt_maya.remove(mirror_node)

            cmds.connectAttr(rotation_plug, f'{sdk_grp}.rotate', f=1)

    # Twist/roll/offset for FK. Runs after the INDIV_FK block above so it
    # reroutes that block's SDK_JNT connection rather than being
    # overwritten by it. Reads rt_ctrlall.resolved_plug internally, so the cog's
    # ALL values reach FK mode the same way they reach IK.
    rt_fk.connect_twist_roll(rigname, rt_constants.JOINTS_FK[rigname])

    if ik:
        fk_skeleton_grp = rt_naming.fstr('', rt_constants.SKELETON_GRP, rt_constants.TYPE_FK)
        rt_maya.parent_to(fkjnt_grp, fk_skeleton_grp)

        for i in range(rt_constants.NUM_CTRL_FK):
            fk_ctrl = rt_naming.fstr(rigname, rt_constants.CONTROL, '', i+1)
            add_proxy_attributes_to_controls(rigname, fk_ctrl, rt_constants.TYPE_FK)

        setup_switch_fk(rigname, fkroot_grp, fkjnt_grp)
    else:
        skeleton_grp = rt_naming.fstr('', rt_constants.SKELETON_GRP)
        rt_maya.parent_to(fkjnt_grp, skeleton_grp)

def connect_spline_fk(rigname):
    curve_fk = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_FK)
    spline_grp_fk = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, rt_constants.TYPE_FK)
    rt_maya.parent_to(curve_fk, spline_grp_fk)


# CONNECT IK ===========================================================

def connect_ik(rigname, fk, ik):
    if not ik:
        return
    logger.debug(f'{rigname}: Connect IK')

    ik_skeleton_grp = rt_naming.fstr('', rt_constants.SKELETON_GRP, rt_constants.TYPE_IK)
    ikjnt_grp = rt_naming.fstr(rigname, rt_constants.GROUP, rt_constants.TYPE_IK)

    if not cmds.objExists(ikjnt_grp):
        ikjnt_grp = cmds.group(em=True, n=ikjnt_grp)
        rt_maya.parent_to(ikjnt_grp, ik_skeleton_grp)
    rt_maya.parent_to(rt_constants.JOINTS_IK[rigname][0], ikjnt_grp)
    rt_maya.parent_to(ikjnt_grp, ik_skeleton_grp)

    spline_constraints = constrain_spline_controls(rigname)
    setup_switch_ik(rigname, ikjnt_grp, spline_constraints)
    setup_switch_upvec(rigname)
    connect_spline_ik(rigname)

    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    for i in range(rt_constants.NUM_CTRL_IK):
        add_proxy_attributes_to_controls(rigname, ik_controls['ik'][i], rt_constants.TYPE_IK)
        add_proxy_attributes_to_controls(rigname, ik_controls['float'][i], rt_constants.TYPE_IK)
    for spline_ctrl in ik_controls['spline']:
        add_proxy_attributes_to_controls(rigname, spline_ctrl, rt_constants.TYPE_IK)

def connect_spline_ik(rigname):
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    logger.trace(f'ik_controls {ik_controls} ik_ctrlgrps {ik_ctrlgrps}')

    spline_grp_ik = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, rt_constants.TYPE_IK)
    driver_curve = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_IK)
    # get_spline_handle returns [] when the handle is missing or orphaned
    # (no joint list). Abort this part's IK connect with a clear message
    # rather than crashing on a 3-way unpack of an empty list.
    spline_info = rt_curve.get_spline_handle(rigname)
    if not spline_info:
        logger.warning(f'{rigname}: No valid spline handle to connect; '
                       f'skipping IK spline connect')
        return
    ikhandle, effector, solver_curve = spline_info
    rt_maya.parent_to(ikhandle, spline_grp_ik)
    rt_maya.parent_to(solver_curve, spline_grp_ik)
    rt_maya.parent_to(driver_curve, spline_grp_ik)

    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    contents = list()
    contents.append(ik_ctrlgrps['ik'][0])
    contents.extend(ik_ctrlgrps['float'])
    contents.extend([ik_ctrlgrps['spline'][0], ik_ctrlgrps['spline'][-1]])
    for obj in contents:
        rt_maya.parent_to(obj, basectrl)

    spline_handle = rt_naming.fstr(rigname, rt_constants.SPLINE_HANDLE, rt_constants.TYPE_IK)
    if cmds.objExists(spline_handle):
        connect_twist_roll_ik(rigname, spline_handle)


def connect_twist_roll_ik(rigname, spline_handle):
    '''
    Drive the spline handle's native twist/roll/offset from the basectrl
    dials, carrying the L/R mirror sign.

    The FK equivalent is rig_tail_fk.connect_twist_roll, and the two MUST
    sign the same way: the BN chain blends between the two drivers
    (rig_tail_matrix), so a dial that mirrors in one mode and not the other
    would make the tail jump on an IK/FK switch.

    twist and roll turn the chain about its own axis and take the ROTATION
    sign; offset re-samples the joints ALONG the curve, which is a slide,
    so it takes the translation sign (see rig_tail_mirror).

    A sign of +1 wires the dial straight to the handle, so an unmirrored
    rig gains no nodes; only a negated axis pays for one. A node left over
    from when this side WAS signed is removed, so flipping the behavior or
    the source side cannot leave one feeding the handle.

    Arguments
        rigname (str): Name of rig component
        spline_handle (str): The part's IK spline handle
    '''
    aim_key = rt_mirror.aim_axis()
    rot_sign = rt_mirror.rotation_signs(rigname).get(aim_key, 1.0)
    # offset asks for its own signs rather than negating the rotation ones:
    # the two differ on every part that has nothing to mirror
    trn_sign = rt_mirror.translation_signs(rigname).get(aim_key, 1.0)
    signs = {'twist': rot_sign, 'roll': rot_sign, 'offset': trn_sign}
    for attr, sign in signs.items():
        src = rt_ctrlall.resolved_plug(rigname, attr)
        mirror_node = f'{rt_constants.TYPE_IK}_{rigname}_{attr}_mirror_multiplyDivide'
        if sign < 0:
            if not cmds.objExists(mirror_node):
                cmds.createNode('multiplyDivide', n=mirror_node, s=1, ss=1)
                cmds.setAttr(f'{mirror_node}.operation', 1)  # multiply
            cmds.connectAttr(src, f'{mirror_node}.input1X', f=1)
            cmds.setAttr(f'{mirror_node}.input2X', sign)
            src = f'{mirror_node}.outputX'
        elif cmds.objExists(mirror_node):
            rt_maya.remove(mirror_node)
        cmds.connectAttr(src, f'{spline_handle}.{attr}', f=1)


# CONNECT EFFECTS =====================================================

def connect_effects(rigname, fk, ik):
    logger.debug(f'{rigname}: Connect animation effects')

    if rt_constants.EFFECTS['stretchy']:
        connect_stretch(rigname, fk, ik)

def connect_stretch(rigname, fk, ik):
    logger.debug(f'{rigname}: Connect stretch')
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)

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
        cmds.connectAttr(rt_ctrlall.resolved_plug(rigname, 'stretch'),
                         f'{stretch_remap}.input1X', f=1)
    if cmds.attributeQuery('squash', n=basectrl, ex=1):
        cmds.connectAttr(rt_ctrlall.resolved_plug(rigname, 'squash'),
                         f'{squash_remap}.input1X', f=1)

    rt_stretch.connect_stretch_to_joints(rigname, basectrl, fk, ik)


# ATTRIBUTES ===========================================================

def add_ikfk_attributes_to_basectrl(rigname, basectrl):
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    ikfk_switch = rt_naming.fstr(rigname, rt_constants.IKFK)
    rt_maya.add_attribute_enum(basectrl, rt_constants.IKFK_DIVIDER[0], rt_constants.IKFK_DIVIDER[1], rt_constants.IKFK_DIVIDER[2])
    rt_maya.add_attribute_enum(basectrl, rt_constants.IKFK_SWITCH[0], rt_constants.IKFK_SWITCH[1],
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
    rt_maya.add_attribute_enum(basectrl, rt_constants.TWIST_DIVIDER[0], rt_constants.TWIST_DIVIDER[1], rt_constants.TWIST_DIVIDER[2])

    for attr in ['twist', 'roll', 'offset']:
        if not cmds.attributeQuery(attr, n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln=attr, at='float', k=1, dv=0)

def add_attributes_ikfk_switch(control, fk, ik):
    # The autorigger leaves its own empty switch divider on the cog
    # (ln 'ikfkDivider', labeled TAIL IKFK); ours takes over that label,
    # so drop the stale one rather than showing two dividers
    if cmds.attributeQuery('ikfkDivider', n=control, ex=1):
        rt_maya.remove_attribute(control, 'ikfkDivider')

    rt_maya.add_attribute_enum(control, rt_constants.TAIL_IKFK_DIVIDER[0], rt_constants.TAIL_IKFK_DIVIDER[1], rt_constants.TAIL_IKFK_DIVIDER[2])

    # Start every build in the build-derived default mode: FK when FK was
    # built, otherwise SplineIK (see rt_constants.ikfk_default_index, applied to
    # IKFK_SWITCH by update_ikfk_modes). Set explicitly as well as
    # defaulted, since a rebuild reuses the existing attribute and would
    # otherwise keep whatever mode the switch was left in.
    dv = rt_constants.IKFK_SWITCH[3]
    # The switch is created for the whole roster - an excluded part is not
    # rebuilt but its rig still needs its switch in the channel box. Only
    # the parts actually being rebuilt have their mode reset; an excluded
    # part keeps the mode it was left in, like the rest of its state.
    rebuilt = set(rt_cache.active_parts())
    for rigname in rt_constants.RIGPARTS:
        ln_ikfk = rt_naming.fstr(rigname, rt_constants.IKFK)
        nn_ikfk = re.sub(r'[-_\s]+', ' ', ln_ikfk).title()
        rt_maya.add_attribute_enum(control, ln_ikfk, nn_ikfk, rt_constants.IKFK_SWITCH[2], dv)
        if rigname in rebuilt:
            rt_maya.set_attr_value(f'{control}.{ln_ikfk}', dv)

def add_proxy_attributes_to_controls(rigname, control, typ):
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)

    ikfk_switch = rt_naming.fstr(rigname, rt_constants.IKFK)
    rt_maya.add_attribute_enum(control, rt_constants.IKFK_DIVIDER[0], rt_constants.IKFK_DIVIDER[1], rt_constants.IKFK_DIVIDER[2])
    rt_maya.add_attribute_enum(control, rt_constants.IKFK_SWITCH[0], rt_constants.IKFK_SWITCH[1],
                       pxy=f'{cog_ctrl}.{ikfk_switch}')

    # STRETCH proxies always come before TWIST proxies
    if rt_constants.EFFECTS['stretchy']:
        rt_maya.add_attribute_enum(control, rt_constants.STRETCH_DIVIDER[0], rt_constants.STRETCH_DIVIDER[1], rt_constants.STRETCH_DIVIDER[2])
        for atr, nice in [('stretch', 'Stretch'), ('squash', 'Squash'),
                          ('preserveVolume', 'Preserve Volume')]:
            rt_maya.add_attribute_enum(control, ln=atr, nn=nice, pxy=f'{basectrl}.{atr}')

    # Twist attributes exist on the basectrl whenever IK is built,
    # independent of stretchy
    if typ == rt_constants.TYPE_IK:
        rt_maya.add_attribute_enum(control, rt_constants.TWIST_DIVIDER[0], rt_constants.TWIST_DIVIDER[1], rt_constants.TWIST_DIVIDER[2])
        for atr in ['twist', 'roll', 'offset']:
            rt_maya.add_attribute_enum(control, ln=atr, nn=rt_naming.titlecase(atr), pxy=f'{basectrl}.{atr}')


# CONSTRAINTS ==========================================================

def constrain_spline_controls(rigname, typ=rt_constants.TYPE_IK):
    logger.trace(f"{rigname}: Constrain clusters to spline controls")
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)

    cluster_handles = list()
    for NN in range(1, rt_constants.NUM_CTRL_IK+1):
        cluster_handle = rt_naming.fstr(rigname, rt_constants.CLUSTER_HANDLE, typ, NN)
        cluster_handles.append(cluster_handle)
    logger.trace(f'Get cluster handles for spline constraint:\n{cluster_handles}')

    # Constraint targets per cluster: W0=spline, W1=ik, W2=float.
    # IK and Float controls map 1:1 to clusters; the spline target
    # comes from the fixed bot/mid/top set via spline_control_index.
    num_clusters = len(cluster_handles)
    spline_constraints = list()
    for i, clstr in enumerate(cluster_handles):
        spline_ctrl = ik_controls['spline'][rt_control.spline_control_index(i, num_clusters)]
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
    ikfk_attr = rt_ctrlall.ikfk_driver(rigname)
    fk_mode = rt_constants.ikfk_fk_mode_index() # Get index of FK mode
    if fk_mode is None:
        logger.warning(f"{rigname}: No 'FK' mode in IKFK_MODES, skip FK switch")
        return

    for mode in range(len(rt_constants.IKFK_MODES)):
        v = 1 if mode == fk_mode else 0
        rt_maya.sdk(ikfk_attr, f'{fkroot_grp}.visibility', dv=mode, v=v)
        rt_maya.sdk(ikfk_attr, f'{fkjnt_grp}.visibility', dv=mode, v=v)

def setup_switch_ik(rigname, ikjnt_grp, spline_constraints):
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    ikfk_attr = rt_ctrlall.ikfk_driver(rigname)
    fk_mode = rt_constants.ikfk_fk_mode_index() # Get index of FK mode
    num_clusters = len(spline_constraints)

    # Constraint weights: each cluster constraint has targets
    # W0=spline, W1=ik, W2=float; only the active mode's target weighs in.
    # The spline target per cluster comes from the fixed bot/mid/top set.
    for j in range(num_clusters):
        constraint = spline_constraints[j]
        targets = [ik_controls['spline'][rt_control.spline_control_index(j, num_clusters)],
                   ik_controls['ik'][j],
                   ik_controls['float'][j]]
        for i, ctrl in enumerate(targets):
            for mode in range(len(rt_constants.IKFK_MODES)):
                v = 1 if mode == i else 0
                rt_maya.sdk(ikfk_attr, f'{constraint}.{ctrl}W{i}', dv=mode, v=v)

    # Control visibility: each control set is only shown in its own mode
    # (ik_ctrlgrps['spline'] includes the mid_rot group)
    for i, ctrltyp in enumerate(['spline', 'ik', 'float']):
        for ctrl_grp in ik_ctrlgrps[ctrltyp]:
            for mode in range(len(rt_constants.IKFK_MODES)):
                v = 1 if (mode == i and mode != fk_mode) else 0
                rt_maya.sdk(ikfk_attr, f'{ctrl_grp}.visibility', dv=mode, v=v)

    for mode in range(len(rt_constants.IKFK_MODES)):
        v = 0 if mode == fk_mode else 1
        rt_maya.sdk(ikfk_attr, f'{ikjnt_grp}.visibility', dv=mode, v=v)

def setup_switch_upvec(rigname, typ=rt_constants.TYPE_IK):
    logger.trace(f"{rigname}: Space switching for upvec")
    ik_controls, ik_ctrlgrps = get_cached_controls_ik(rigname)
    ikfk_attr = rt_ctrlall.ikfk_driver(rigname)

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

    fk_mode = rt_constants.ikfk_fk_mode_index()
    for constr, parents in upvec_SDKs.items():
        for i, parent in enumerate(parents):
            for mode in range(len(rt_constants.IKFK_MODES)):
                v = 1 if (mode == i and mode != fk_mode) else 0
                rt_maya.sdk(ikfk_attr, f'{constr}.{parent}W{i}', dv=mode, v=v)

    for grp in (upvec_bsegrp, upvec_endgrp):
        for mode in range(len(rt_constants.IKFK_MODES)):
            v = 0 if mode == fk_mode else 1
            rt_maya.sdk(ikfk_attr, f'{grp}.visibility', dv=mode, v=v)

    cluster_handle_bse = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE, typ, TAG='base')
    cluster_handle_end = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE, typ, TAG='end')

    cmds.parentConstraint(upvec_bsectrl, cluster_handle_bse)
    cmds.parentConstraint(upvec_endctrl, cluster_handle_end)
