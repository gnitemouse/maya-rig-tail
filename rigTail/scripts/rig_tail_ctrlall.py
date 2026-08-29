'''
rig_tail_ctrlall.py
author: Daisy Jane @gnitemouse

Main Controller dashboard for rigs with multiple tails.

Built during the connect phase of the build when MAIN_CONTROLLER is on
and RIGPARTS has two or more parts. The cog control becomes a dashboard:

  ALL section: one 'all_*' copy on the cog of every routed attribute
    (IKFK mode, STRETCH, TWIST, ANIMATION). JNT SCALE stays per-tail.
  OVERRIDE section: one '{rigname}_override' flag per tail, naming the
    control that wins. Cog (default) makes the tail follow the ALL
    values; Basectrl makes it use its own base control values. Every
    control carrying the routed dials shows the flag as an 'Override
    All' proxy of the cog master, matching the convention that global
    truth lives on the cog (like the per-tail IKFK switches).

Routing is one condition node per tail per attribute
('{rigname}_{attr}_override_condition'): firstTerm reads the override
flag, colorIfTrueR the tail's own value, colorIfFalseR the ALL value, and
outColorR feeds whatever consumed the basectrl attribute before (remap
nodes, spline handle, FX expressions). The IKFK mode is special: its
local value already lives on the cog and its consumers are driven keys
that cannot be re-pointed, so the condition output lands on a hidden
'{rigname}_ikfk_resolved' cog attribute that the mode SDKs drive from.

Consumers read resolved_plug() and ikfk_driver(), which return the plain
basectrl or cog plug when the dashboard is off, so a single-tail build
wires exactly as before. cleanup_ctrlall (from cleanup_rig) removes
stale pieces every build and strips all dashboard attributes when the
option is off, while keeping ALL values and override choices across
rebuilds.

Functions:
    active: is the dashboard enabled for the current settings
    routed_attr_specs: the attributes routed through the dashboard
    add_dashboard_to_cog: add the ALL and OVERRIDE sections to the cog
    add_override_to_control: proxy a tail's override flag onto a control
    build_override_conditions: create/rewire a tail's override conditions
    resolved_plug: source plug a consumer reads for a routed attribute
    ikfk_driver: driver plug for a tail's IKFK mode SDKs
    hide_resolved_attrs: hide the internal resolved IKFK driver attrs
    cleanup_ctrlall: remove stale or all dashboard nodes and attributes
    all_attr, condition_node: attribute and node name helpers
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import re

logger = logger_setup(__name__)

# rig_tail_constants is deliberately never reloaded by rig_tail (it holds
# session state), so a Maya session started before this feature existed
# has a stale constants module without the dashboard templates. This
# module IS reloaded every run: install anything missing onto rt_constants so
# the build works without a Maya restart. Values must match
# rig_tail_constants; existing attributes are never overwritten, so a
# restarted session or a user-customized constants module wins.
_CST_DEFAULTS = {
    'TAIL_IKFK_DIVIDER': ('ikfk_divider', '----------', 'TAIL IKFK'),
    'ALL_DIVIDER': ('all_divider', '----------', 'ALL'),
    'OVERRIDE_ALL_DIVIDER': ('override_all_divider', '----------', 'OVERRIDE ALL'),
    'OVERRIDE_DIVIDER': ('override_divider', '----------', 'OVERRIDE'),
    'OVERRIDE': '{rigname}_override',
    'OVERRIDE_ENUM': 'Cog:Basectrl',
    'IKFK_RESOLVED': '{rigname}_ikfk_resolved',
    'ALL_PREFIX': 'all_',
}
for _name, _value in _CST_DEFAULTS.items():
    if not hasattr(rt_constants, _name):
        setattr(rt_constants, _name, _value)


# NAMES ================================================================

def active():
    '''
    Whether the dashboard should be built: the MAIN_CONTROLLER option is
    on and RIGPARTS has 2+ parts (a single tail has nothing to
    centralize; the UI disables the checkbox too). Computed here rather
    than in rig_tail_constants so a stale (never-reloaded) constants
    module cannot break the build.
    '''
    return rt_constants.MAIN_CONTROLLER and len(rt_constants.RIGPARTS) >= 2

def all_attr(attr):
    ''' Name of the ALL copy of a routed attribute on the cog. '''
    return f'{rt_constants.ALL_PREFIX}{attr}'

def condition_node(rigname, attr):
    ''' Name of the override condition for one tail's routed attribute. '''
    return f'{rigname}_{attr}_override_{rt_constants.COND}'


# ATTRIBUTE SPECS ======================================================

def routed_attr_specs(fk, ik):
    '''
    Specs of the attributes routed through the dashboard, honoring the
    same gates that create them on the basectrl. Each entry is
    (attr, addAttr kwargs) and MUST mirror the definitions in
    rig_tail_stretch.add_stretch_attributes_to_basectrl,
    rig_tail_connect.add_twist_attributes_to_basectrl and
    rig_tail_anim.add_anim_attributes_to_basectrl. The IKFK mode is
    routed too but handled separately (enum + resolved driver), and the
    JNT SCALE attributes are deliberately excluded from the dashboard.

    Arguments
        fk (bool): FK is being built
        ik (bool): IK is being built

    Return
        list: [(attr, kwargs), ...] for the active build options
    '''
    specs = list()
    if rt_constants.EFFECTS['stretchy']:
        specs += [
            ('stretch', dict(at='float', dv=0, min=-10, max=10)),
            ('squash', dict(at='float', dv=0, min=-10, max=10)),
            ('preserveVolume', dict(at='float', dv=1, min=0, max=1)),
        ]
    # twist/roll/offset drive the IK spline handle AND the FK SDK network
    # (rig_tail_fk.connect_twist_roll), so they are routed whenever either
    # mode is built - gating them on ik alone left an FK-only rig with
    # basectrl attributes the ALL section could not reach, and left a
    # dual-mode rig's ALL values doing nothing while it sat in FK mode.
    if fk or ik:
        specs += [(atr, dict(at='float', dv=0))
                  for atr in ('twist', 'roll', 'offset')]
    if rt_constants.EFFECTS['wave']:
        specs += [(f'wave{axis}', dict(at='float', dv=0, min=-10, max=10))
                  for axis in 'XYZ']
        specs += [
            ('wave_frequency', dict(at='float', dv=2, min=0.1, max=5)),
            ('wave_speed', dict(at='float', dv=3, min=0, max=10)),
            ('wave_falloff', dict(at='float', dv=1, min=0.1, max=10)),
        ]
    if rt_constants.EFFECTS['curl']:
        specs += [(f'curl{axis}', dict(at='float', dv=0, min=-10, max=10))
                  for axis in 'XYZ']
        specs += [('curl_falloff', dict(at='float', dv=3.0, min=0.1, max=10))]
    if rt_constants.EFFECTS['noise']:
        specs += [
            ('noise', dict(at='float', dv=0, min=-10, max=10)),
            ('noise_frequency', dict(at='float', dv=1, min=0.1, max=5)),
            ('noise_speed', dict(at='float', dv=3, min=0, max=10)),
        ]
    if rt_constants.EFFECTS['loop']:
        # Deferred import: rig_tail_anim imports this module at its top
        import rig_tail_anim as rt_anim
        specs += [
            ('loop', dict(at='bool', dv=0)),
            ('loop_frame', dict(at='long', dv=rt_anim.LOOP_FRAME_DEFAULT, min=1)),
        ]
    return specs


# BUILD ================================================================

def add_all_ikfk_to_cog(cog_ctrl):
    '''
    Add the ALL IKFK mode attribute to the cog.

    Called from rig_tail_connect.add_attributes_ikfk_switch, so it lands
    directly under the TAIL IKFK divider and above the per-tail switches
    it overrides. The channel box orders dynamic attributes by creation,
    so where this runs is what decides where it shows.

    Arguments
        cog_ctrl (str): Cog control
    '''
    # The build-derived default the per-tail switches use: a tail whose
    # override is Off follows this value, so a stale mode here would put
    # that tail in it regardless of its own default.
    dv = rt_constants.IKFK_SWITCH[3]
    rt_maya.add_attribute_enum(cog_ctrl, all_attr('ikfk'), 'All IKFK',
                              rt_constants.IKFK_SWITCH[2], dv)
    rt_maya.set_attr_value(f'{cog_ctrl}.{all_attr("ikfk")}', dv)


def add_dashboard_to_cog(cog_ctrl, fk, ik):
    '''
    Add the ALL and OVERRIDE ALL sections to the cog control.
    Runs in the setup phase (connect_cog), after the per-tail IKFK
    switches, so the channel box reads: IKFK switches, ALL values,
    override flags. Existing attributes are kept (values survive a
    rebuild); missing ones are added.

    ALL IKFK is not added here - it heads the TAIL IKFK section instead
    (add_all_ikfk_to_cog).

    Arguments
        cog_ctrl (str): Cog control
        fk (bool): FK is being built
        ik (bool): IK is being built
    '''
    logger.debug(f"Add main controller dashboard to '{cog_ctrl}'")

    # ALL section: one real copy of every routed attribute. Nice names
    # are left to Maya ('all_wave_frequency' -> 'All Wave Frequency').
    rt_maya.add_attribute_enum(cog_ctrl, rt_constants.ALL_DIVIDER[0],
                              rt_constants.ALL_DIVIDER[1], rt_constants.ALL_DIVIDER[2])
    order_all_section(cog_ctrl, routed_attr_specs(fk, ik))

    # OVERRIDE section: per-tail flags. Off (default) follows the
    # ALL values; On uses the tail's own basectrl values.
    rt_maya.add_attribute_enum(cog_ctrl, rt_constants.OVERRIDE_DIVIDER[0],
                              rt_constants.OVERRIDE_DIVIDER[1],
                              rt_constants.OVERRIDE_DIVIDER[2])
    for rigname in rt_constants.RIGPARTS:
        ln = rt_naming.fstr(rigname, rt_constants.OVERRIDE)
        nn = re.sub(r'[-_\s]+', ' ', ln).title()
        rt_maya.add_attribute_enum(cog_ctrl, ln, nn, rt_constants.OVERRIDE_ENUM, 0)

def order_all_section(cog_ctrl, specs):
    '''
    Put the ALL values in routed_attr_specs order, whatever order an
    earlier build left them in.

    The channel box orders dynamic attributes by CREATION, and there is no
    command to move one. So an attribute a later build adds - Stretch on a
    rig first built with Stretchy off, say - lands under everything already
    there, and stays under it for good. Adding the missing ones is not
    enough; the section has to be laid down again in one pass.

    Only ALL values are rebuilt this way, and only when they are already
    out of order. They are read by the override conditions alone, which
    build_override_conditions rewires afterwards in the same phase. The
    per-tail switches and override flags look reorderable too and are NOT:
    every control proxies them, and deleting a proxy's master breaks the
    proxy rather than moving it.

    Arguments
        cog_ctrl (str): Cog control
        specs (list): [(attr, addAttr kwargs), ...] in the wanted order
    '''
    wanted = [(all_attr(attr), kwargs) for attr, kwargs in specs]
    names = [ln for ln, _ in wanted]
    present = [a for a in cmds.listAttr(cog_ctrl, ud=1) or [] if a in names]

    if present == [ln for ln in names if ln in present]:
        for ln, kwargs in wanted:
            if not cmds.attributeQuery(ln, n=cog_ctrl, ex=1):
                cmds.addAttr(cog_ctrl, ln=ln, k=1, **kwargs)
        return

    logger.debug(f"'{cog_ctrl}': ALL values are out of order, laying the "
                 f'section down again')
    # The animator's values are the point of keeping these across a
    # rebuild, so they come back on the far side of the delete
    saved = {ln: cmds.getAttr(f'{cog_ctrl}.{ln}') for ln in present}
    for ln in present:
        rt_maya.remove_attribute(cog_ctrl, ln)
    for ln, kwargs in wanted:
        cmds.addAttr(cog_ctrl, ln=ln, k=1, **kwargs)
        if ln in saved:
            rt_maya.set_attr_value(f'{cog_ctrl}.{ln}', saved[ln])


def add_override_to_control(rigname, control):
    '''
    Show the tail's override flag on one of its controls as a proxy of
    the real flag on the cog (same direction as the IKFK switch proxy).

    Added to every control that carries the routed dials, not just the
    basectrl: the flag decides whether those dials are live, and an
    animator holding an IK control could not see it from there.
    It leads the channel box, above the IKFK section, because it governs
    the mode switch below it as well.

    Arguments
        rigname (str): Name of rig component
        control (str): Control to show the flag on
    '''
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    override = rt_naming.fstr(rigname, rt_constants.OVERRIDE)
    rt_maya.add_attribute_enum(control, rt_constants.OVERRIDE_ALL_DIVIDER[0],
                              rt_constants.OVERRIDE_ALL_DIVIDER[1],
                              rt_constants.OVERRIDE_ALL_DIVIDER[2])
    rt_maya.add_attribute_enum(control, override, 'Override All',
                              pxy=f'{cog_ctrl}.{override}')

def build_override_conditions(rigname, fk, ik):
    '''
    Create/rewire the tail's override condition nodes: one per routed
    attribute, hard 2-way switch (no partial blends).

        firstTerm    = cog.{rigname}_override   (== 1 -> override on)
        colorIfTrueR = the tail's own value     (basectrl attr;
                                                 cog switch for ikfk)
        colorIfFalseR= cog.all_{attr}
        outColorR    -> the attribute's downstream consumer

    Idempotent: nodes and connections that already exist are reused, so
    a light rebuild only rewires what cleanup disconnected. For IKFK
    the output additionally lands on the hidden resolved driver attr on
    the cog so the mode SDKs have a stable plug to key against.

    Runs in the setup phase (connect_basectrl), after the basectrl and
    cog attributes exist and before the connect phase reads
    resolved_plug()/ikfk_driver().

    Arguments
        rigname (str): Name of rig component
        fk (bool): FK is being built
        ik (bool): IK is being built
    '''
    logger.debug(f'{rigname}: Build override conditions')
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    override_plug = f'{cog_ctrl}.{rt_naming.fstr(rigname, rt_constants.OVERRIDE)}'

    routed = [(attr, f'{basectrl}.{attr}', f'{cog_ctrl}.{all_attr(attr)}')
              for attr, _ in routed_attr_specs(fk, ik)]
    if ik:
        # IKFK local truth already lives on the cog (basectrl proxies it)
        local_ikfk = f'{cog_ctrl}.{rt_naming.fstr(rigname, rt_constants.IKFK)}'
        routed.append(('ikfk', local_ikfk, f'{cog_ctrl}.{all_attr("ikfk")}'))

    for attr, local_plug, all_plug in routed:
        cond = condition_node(rigname, attr)
        if not cmds.objExists(cond):
            cmds.createNode('condition', n=cond, s=1, ss=1)
        cmds.setAttr(f'{cond}.operation', 0)  # equal
        cmds.setAttr(f'{cond}.secondTerm', 1)
        rt_maya.ensure_connect(override_plug, f'{cond}.firstTerm')
        rt_maya.ensure_connect(local_plug, f'{cond}.colorIfTrueR')
        rt_maya.ensure_connect(all_plug, f'{cond}.colorIfFalseR')

    if ik:
        resolved = rt_naming.fstr(rigname, rt_constants.IKFK_RESOLVED)
        if not cmds.attributeQuery(resolved, n=cog_ctrl, ex=1):
            cmds.addAttr(cog_ctrl, ln=resolved, at='float', k=0)
        rt_maya.ensure_connect(f'{condition_node(rigname, "ikfk")}.outColorR',
                              f'{cog_ctrl}.{resolved}')


# RESOLVED PLUGS =======================================================

def resolved_plug(rigname, attr):
    '''
    Plug consumers should read for a routed attribute: the override
    condition's output when the dashboard built one, otherwise the
    plain basectrl attribute. Self-gating, so every consumer (stretch
    remaps, spline handle, FX expressions) calls it unconditionally and
    a dashboard-off build wires exactly as before.

    Arguments
        rigname (str): Name of rig component
        attr (str): Routed attribute name (e.g. 'stretch', 'waveX')

    Return
        str: Source plug for the attribute's consumers
    '''
    cond = condition_node(rigname, attr)
    if active() and cmds.objExists(cond):
        return f'{cond}.outColorR'
    return f'{rt_naming.fstr(rigname, rt_constants.BASECTRL)}.{attr}'

def ikfk_driver(rigname):
    '''
    Driver plug for the tail's IKFK mode SDKs: the hidden resolved attr
    when the dashboard is active, else the per-tail switch on the cog.

    Arguments
        rigname (str): Name of rig component

    Return
        str: Driver plug for setup_switch_fk/ik/upvec
    '''
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    resolved = rt_naming.fstr(rigname, rt_constants.IKFK_RESOLVED)
    if active() and cmds.objExists(cog_ctrl) \
            and cmds.attributeQuery(resolved, n=cog_ctrl, ex=1):
        return f'{cog_ctrl}.{resolved}'
    return f'{cog_ctrl}.{rt_naming.fstr(rigname, rt_constants.IKFK)}'

def hide_resolved_attrs():
    '''
    Hide the resolved IKFK driver attrs after the mode SDKs are wired:
    sdk() flips its driver keyable, which would leave an internal,
    connection-driven attribute in the cog channel box.
    Called at the end of connect_rig_tail.
    '''
    if not active():
        return
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    if not cmds.objExists(cog_ctrl):
        return
    for rigname in rt_constants.RIGPARTS:
        resolved = rt_naming.fstr(rigname, rt_constants.IKFK_RESOLVED)
        if cmds.attributeQuery(resolved, n=cog_ctrl, ex=1):
            cmds.setAttr(f'{cog_ctrl}.{resolved}', k=0)
            cmds.setAttr(f'{cog_ctrl}.{resolved}', cb=0)


# CLEANUP ==============================================================

def cleanup_ctrlall(fk, ik):
    '''
    Remove dashboard leftovers before a rebuild; called from
    cleanup_rig every build. When the dashboard is off (option
    unchecked, or RIGPARTS dropped below 2) every dashboard attribute
    and condition node goes. When it is on, only stale pieces go:
    conditions/attrs of parts no longer in RIGPARTS, or of attributes
    whose effect/build gates turned off. Live pieces are kept so ALL
    values and per-tail override choices survive the rebuild; the connect
    phase re-adds and re-wires them idempotently.

    Nodes are removed with rt_maya.remove (disconnect first), so a
    condition still referenced by an FX expression cannot cascade the
    delete through the expression web.

    Arguments
        fk (bool): FK is being built
        ik (bool): IK is being built
    '''
    act = active()
    logger.debug(f'Cleanup main controller dashboard (active={act})')
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)

    # Condition nodes: keep only the set the current build will rewire
    expected_nodes = set()
    if act:
        attrs = [attr for attr, _ in routed_attr_specs(fk, ik)]
        if ik:
            attrs.append('ikfk')
        expected_nodes = {condition_node(rigname, attr)
                          for rigname in rt_constants.RIGPARTS for attr in attrs}
    # One disconnect pass and one delete for every stale condition
    # (rt_maya.remove_nodes keeps remove()'s disconnect-before-delete rule,
    # so a condition still referenced by an FX expression cannot cascade)
    rt_maya.remove_nodes([node for node
                         in cmds.ls(f'*_override_{rt_constants.COND}',
                                    type='condition') or []
                         if node not in expected_nodes])

    # Control override attrs (before their cog masters, so a proxy is
    # not left pointing at a deleted master). When the dashboard is off
    # everything goes; when it is on, only the stale divider from the
    # old naming goes ('override_divider' now labels the cog section,
    # controls carry 'override_all_divider').
    # Every control carries the flag, not just the basectrl, and an
    # excluded part is not torn down and rebuilt - so the controls are
    # found by asking which of them holds the attribute rather than by
    # rebuilding the name of each set.
    for control in cmds.ls(f'*_{rt_constants.CTRL}', type='transform') or []:
        held = set(cmds.listAttr(control, ud=1) or [])
        # Which tail's flag this control carries is what identifies it,
        # so a control named outside the templates is still swept
        flags = {rt_naming.fstr(rigname, rt_constants.OVERRIDE)
                 for rigname in rt_constants.RIGPARTS} & held
        if not flags:
            continue
        stale = {rt_constants.OVERRIDE_DIVIDER[0]}
        if not act:
            stale |= flags | {rt_constants.OVERRIDE_ALL_DIVIDER[0]}
        for attr in stale & held:
            rt_maya.remove_attribute(control, attr)

    if not cmds.objExists(cog_ctrl):
        return

    # Cog attributes: ALL values, dividers, override flags, resolved
    # drivers. Sweep everything dashboard-shaped that the current build
    # does not expect.
    expected_attrs = set()
    if act:
        expected_attrs = {all_attr(attr)
                          for attr, _ in routed_attr_specs(fk, ik)}
        expected_attrs |= {rt_constants.ALL_DIVIDER[0],
                           rt_constants.OVERRIDE_DIVIDER[0]}
        if ik:
            expected_attrs.add(all_attr('ikfk'))
        for rigname in rt_constants.RIGPARTS:
            expected_attrs.add(rt_naming.fstr(rigname, rt_constants.OVERRIDE))
            if ik:
                expected_attrs.add(rt_naming.fstr(rigname, rt_constants.IKFK_RESOLVED))
    # ALL IKFK is exempt. It heads the TAIL IKFK section, which survives
    # builds that do not rebuild IK, and the channel box has no way to
    # reorder: sweeping it on one build and re-adding it on the next would
    # move it to the bottom of the cog for good.
    for attr in cmds.listAttr(cog_ctrl, ud=1) or []:
        if attr == all_attr('ikfk'):
            continue
        dashboard = (attr.startswith(rt_constants.ALL_PREFIX)
                     or attr.endswith('_override')
                     or attr.endswith('_ikfk_resolved')
                     or attr in (rt_constants.ALL_DIVIDER[0],
                                 rt_constants.OVERRIDE_DIVIDER[0],
                                 rt_constants.OVERRIDE_ALL_DIVIDER[0]))
        if dashboard and attr not in expected_attrs:
            rt_maya.remove_attribute(cog_ctrl, attr)
