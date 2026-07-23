'''
# rig_tail_mainctrl.py
author: Daisy Jane @gnitemouse

Main Controller (dashboard) for rigs with multiple tails.

Built when the MAIN_CONTROLLER option is on and RIGPARTS has 2+ parts
(active()). The cog control becomes the dashboard:

- ALL section (real attributes on the cog): one 'all_*' copy of every
  routed attribute -- IKFK mode, STRETCH, TWIST and ANIMATION values.
  JNT SCALE attributes stay per-tail and are never routed.
- OVERRIDE ALL section: one '{rigname}_override' flag per tail.
  Override Off (default): the tail follows the ALL values directly and
  its basectrl values are ignored downstream (though they stay
  editable). Override On: the tail uses its own basectrl values. Each
  basectrl shows its flag through a proxy attribute, following the
  existing convention that global truth lives on the cog (like the
  per-tail IKFK switches).

Routing is one condition node per tail per routed attribute
('{rigname}_{attr}_override_condition'): firstTerm reads the override
flag, colorIfTrueR the tail's own value, colorIfFalseR the ALL value,
and outColorR feeds whatever consumed the basectrl attribute before
(remap nodes, the spline handle, FX expressions). The IKFK mode is
special twice over: its local truth already lives on the cog (the
basectrl only proxies it), and its consumers are driven keys that
cannot be re-pointed per consumer -- so the condition output lands on
a hidden '{rigname}_ikfk_resolved' attribute on the cog and
setup_switch_fk/ik/upvec drive from that instead.

Lifecycle: cleanup_mainctrl() (from cleanup_rig) removes stale pieces
and, when the dashboard is off, every dashboard attribute and node;
the setup phase (connect_cog / connect_basectrl) then rebuilds
attributes and conditions idempotently, so ALL values and per-tail
override choices survive a rebuild. resolved_plug() is self-gating:
it returns the plain basectrl plug whenever the dashboard did not
build a condition, so every consumer can call it unconditionally.
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya
import re

logger = logger_setup(__name__)

# rig_tail_constants is deliberately never reloaded by rig_tail (it holds
# session state), so a Maya session started before this feature existed
# has a stale constants module without the dashboard templates. This
# module IS reloaded every run: install anything missing onto rt_cst so
# the build works without a Maya restart. Values must match
# rig_tail_constants; existing attributes are never overwritten, so a
# restarted session or a user-customized constants module wins.
_CST_DEFAULTS = {
    'ALL_DIVIDER': ('all_divider', '----------', 'ALL'),
    'OVERRIDE_ALL_DIVIDER': ('override_all_divider', '----------', 'OVERRIDE ALL'),
    'OVERRIDE_DIVIDER': ('override_divider', '----------', 'OVERRIDE'),
    'OVERRIDE': '{rigname}_override',
    'OVERRIDE_ENUM': 'Off:On',
    'IKFK_RESOLVED': '{rigname}_ikfk_resolved',
    'ALL_PREFIX': 'all_',
}
for _name, _value in _CST_DEFAULTS.items():
    if not hasattr(rt_cst, _name):
        setattr(rt_cst, _name, _value)


# NAMES ================================================================

def active():
    '''
    Whether the dashboard should be built: the MAIN_CONTROLLER option is
    on and RIGPARTS has 2+ parts (a single tail has nothing to
    centralize; the UI disables the checkbox too). Computed here rather
    than in rig_tail_constants so a stale (never-reloaded) constants
    module cannot break the build.
    '''
    return rt_cst.MAIN_CONTROLLER and len(rt_cst.RIGPARTS) >= 2

def all_attr(attr):
    ''' Name of the ALL copy of a routed attribute on the cog. '''
    return f'{rt_cst.ALL_PREFIX}{attr}'

def condition_node(rigname, attr):
    ''' Name of the override condition for one tail's routed attribute. '''
    return f'{rigname}_{attr}_override_{rt_cst.COND}'


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
    if rt_cst.EFFECTS['stretchy']:
        specs += [
            ('stretch', dict(at='float', dv=0, min=-10, max=10)),
            ('squash', dict(at='float', dv=0, min=-10, max=10)),
            ('preserveVolume', dict(at='float', dv=1, min=0, max=1)),
        ]
    if ik:
        specs += [(atr, dict(at='float', dv=0))
                  for atr in ('twist', 'roll', 'offset')]
    if rt_cst.EFFECTS['wave']:
        specs += [(f'wave{axis}', dict(at='float', dv=0, min=-10, max=10))
                  for axis in 'XYZ']
        specs += [
            ('wave_frequency', dict(at='float', dv=2, min=0.1, max=5)),
            ('wave_speed', dict(at='float', dv=3, min=0, max=10)),
            ('wave_falloff', dict(at='float', dv=1, min=0.1, max=10)),
        ]
    if rt_cst.EFFECTS['curl']:
        specs += [(f'curl{axis}', dict(at='float', dv=0, min=-10, max=10))
                  for axis in 'XYZ']
        specs += [('curl_falloff', dict(at='float', dv=3.0, min=0.1, max=10))]
    if rt_cst.EFFECTS['noise']:
        specs += [
            ('noise', dict(at='float', dv=0, min=-10, max=10)),
            ('noise_frequency', dict(at='float', dv=1, min=0.1, max=5)),
            ('noise_speed', dict(at='float', dv=3, min=0, max=10)),
        ]
    if rt_cst.EFFECTS['loop']:
        # Deferred import: rig_tail_anim imports this module at its top
        import rig_tail_anim as rt_ani
        specs += [
            ('loop', dict(at='bool', dv=0)),
            ('loop_frame', dict(at='long', dv=rt_ani.LOOP_FRAME_DEFAULT, min=1)),
        ]
    return specs


# BUILD ================================================================

def add_dashboard_to_cog(cog_ctrl, fk, ik):
    '''
    Add the ALL and OVERRIDE ALL sections to the cog control.
    Runs in the setup phase (connect_cog), after the per-tail IKFK
    switches, so the channel box reads: IKFK switches, ALL values,
    override flags. Existing attributes are kept (values survive a
    rebuild); missing ones are added.

    Arguments
        cog_ctrl (str): Cog control
        fk (bool): FK is being built
        ik (bool): IK is being built
    '''
    logger.debug(f"Add main controller dashboard to '{cog_ctrl}'")

    # ALL section: one real copy of every routed attribute. Nice names
    # are left to Maya ('all_wave_frequency' -> 'All Wave Frequency').
    rt_mya.add_attribute_enum(cog_ctrl, rt_cst.ALL_DIVIDER[0],
                              rt_cst.ALL_DIVIDER[1], rt_cst.ALL_DIVIDER[2])
    if ik:
        rt_mya.add_attribute_enum(cog_ctrl, all_attr('ikfk'), 'All IKFK',
                                  rt_cst.IKFK_SWITCH[2], rt_cst.IKFK_SWITCH[3])
    for attr, kwargs in routed_attr_specs(fk, ik):
        ln = all_attr(attr)
        if not cmds.attributeQuery(ln, n=cog_ctrl, ex=1):
            cmds.addAttr(cog_ctrl, ln=ln, k=1, **kwargs)

    # OVERRIDE ALL section: per-tail flags. Off (default) follows the
    # ALL values; On uses the tail's own basectrl values.
    rt_mya.add_attribute_enum(cog_ctrl, rt_cst.OVERRIDE_ALL_DIVIDER[0],
                              rt_cst.OVERRIDE_ALL_DIVIDER[1],
                              rt_cst.OVERRIDE_ALL_DIVIDER[2])
    for rigname in rt_cst.RIGPARTS:
        ln = rt_nam.fstr(rigname, rt_cst.OVERRIDE)
        nn = re.sub(r'[-_\s]+', ' ', ln).title()
        rt_mya.add_attribute_enum(cog_ctrl, ln, nn, rt_cst.OVERRIDE_ENUM, 0)

def add_override_to_basectrl(rigname, basectrl):
    '''
    Show the tail's override flag on its basectrl as a proxy of the
    real flag on the cog (same direction as the IKFK switch proxy).

    Arguments
        rigname (str): Name of rig component
        basectrl (str): Base control
    '''
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    override = rt_nam.fstr(rigname, rt_cst.OVERRIDE)
    rt_mya.add_attribute_enum(basectrl, rt_cst.OVERRIDE_DIVIDER[0],
                              rt_cst.OVERRIDE_DIVIDER[1],
                              rt_cst.OVERRIDE_DIVIDER[2])
    rt_mya.add_attribute_enum(basectrl, override, 'Override',
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
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    override_plug = f'{cog_ctrl}.{rt_nam.fstr(rigname, rt_cst.OVERRIDE)}'

    routed = [(attr, f'{basectrl}.{attr}', f'{cog_ctrl}.{all_attr(attr)}')
              for attr, _ in routed_attr_specs(fk, ik)]
    if ik:
        # IKFK local truth already lives on the cog (basectrl proxies it)
        local_ikfk = f'{cog_ctrl}.{rt_nam.fstr(rigname, rt_cst.IKFK)}'
        routed.append(('ikfk', local_ikfk, f'{cog_ctrl}.{all_attr("ikfk")}'))

    for attr, local_plug, all_plug in routed:
        cond = condition_node(rigname, attr)
        if not cmds.objExists(cond):
            cmds.createNode('condition', n=cond, s=1, ss=1)
        cmds.setAttr(f'{cond}.operation', 0)  # equal
        cmds.setAttr(f'{cond}.secondTerm', 1)
        rt_mya.ensure_connect(override_plug, f'{cond}.firstTerm')
        rt_mya.ensure_connect(local_plug, f'{cond}.colorIfTrueR')
        rt_mya.ensure_connect(all_plug, f'{cond}.colorIfFalseR')

    if ik:
        resolved = rt_nam.fstr(rigname, rt_cst.IKFK_RESOLVED)
        if not cmds.attributeQuery(resolved, n=cog_ctrl, ex=1):
            cmds.addAttr(cog_ctrl, ln=resolved, at='float', k=0)
        rt_mya.ensure_connect(f'{condition_node(rigname, "ikfk")}.outColorR',
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
    return f'{rt_nam.fstr(rigname, rt_cst.BASECTRL)}.{attr}'

def ikfk_driver(rigname):
    '''
    Driver plug for the tail's IKFK mode SDKs: the hidden resolved attr
    when the dashboard is active, else the per-tail switch on the cog.

    Arguments
        rigname (str): Name of rig component

    Return
        str: Driver plug for setup_switch_fk/ik/upvec
    '''
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    resolved = rt_nam.fstr(rigname, rt_cst.IKFK_RESOLVED)
    if active() and cmds.objExists(cog_ctrl) \
            and cmds.attributeQuery(resolved, n=cog_ctrl, ex=1):
        return f'{cog_ctrl}.{resolved}'
    return f'{cog_ctrl}.{rt_nam.fstr(rigname, rt_cst.IKFK)}'

def hide_resolved_attrs():
    '''
    Hide the resolved IKFK driver attrs after the mode SDKs are wired:
    sdk() flips its driver keyable, which would leave an internal,
    connection-driven attribute in the cog channel box.
    Called at the end of connect_rig_tail.
    '''
    if not active():
        return
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)
    if not cmds.objExists(cog_ctrl):
        return
    for rigname in rt_cst.RIGPARTS:
        resolved = rt_nam.fstr(rigname, rt_cst.IKFK_RESOLVED)
        if cmds.attributeQuery(resolved, n=cog_ctrl, ex=1):
            cmds.setAttr(f'{cog_ctrl}.{resolved}', k=0)
            cmds.setAttr(f'{cog_ctrl}.{resolved}', cb=0)


# CLEANUP ==============================================================

def cleanup_mainctrl(fk, ik):
    '''
    Remove dashboard leftovers before a rebuild; called from
    cleanup_rig every build. When the dashboard is off (option
    unchecked, or RIGPARTS dropped below 2) every dashboard attribute
    and condition node goes. When it is on, only stale pieces go:
    conditions/attrs of parts no longer in RIGPARTS, or of attributes
    whose effect/build gates turned off. Live pieces are kept so ALL
    values and per-tail override choices survive the rebuild -- the
    setup phase re-adds and re-wires them idempotently.

    Nodes are removed with rt_mya.remove (disconnect first), so a
    condition still referenced by an FX expression cannot cascade the
    delete through the expression web.

    Arguments
        fk (bool): FK is being built
        ik (bool): IK is being built
    '''
    act = active()
    logger.debug(f'Cleanup main controller dashboard (active={act})')
    cog_ctrl = rt_nam.fstr('', rt_cst.COG_CTRL)

    # Condition nodes: keep only the set the current build will rewire
    expected_nodes = set()
    if act:
        attrs = [attr for attr, _ in routed_attr_specs(fk, ik)]
        if ik:
            attrs.append('ikfk')
        expected_nodes = {condition_node(rigname, attr)
                          for rigname in rt_cst.RIGPARTS for attr in attrs}
    for node in cmds.ls(f'*_override_{rt_cst.COND}', type='condition') or []:
        if node not in expected_nodes:
            rt_mya.remove(node)

    # Basectrl override proxies (before their cog masters, so the proxy
    # is not left pointing at a deleted master)
    if not act:
        for rigname in rt_cst.RIGPARTS:
            basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
            if not cmds.objExists(basectrl):
                continue
            for attr in (rt_nam.fstr(rigname, rt_cst.OVERRIDE),
                         rt_cst.OVERRIDE_DIVIDER[0]):
                if cmds.attributeQuery(attr, n=basectrl, ex=1):
                    rt_mya.remove_attribute(basectrl, attr)

    if not cmds.objExists(cog_ctrl):
        return

    # Cog attributes: ALL values, dividers, override flags, resolved
    # drivers. Sweep everything dashboard-shaped that the current build
    # does not expect.
    expected_attrs = set()
    if act:
        expected_attrs = {all_attr(attr)
                          for attr, _ in routed_attr_specs(fk, ik)}
        expected_attrs |= {rt_cst.ALL_DIVIDER[0],
                           rt_cst.OVERRIDE_ALL_DIVIDER[0]}
        if ik:
            expected_attrs.add(all_attr('ikfk'))
        for rigname in rt_cst.RIGPARTS:
            expected_attrs.add(rt_nam.fstr(rigname, rt_cst.OVERRIDE))
            if ik:
                expected_attrs.add(rt_nam.fstr(rigname, rt_cst.IKFK_RESOLVED))
    for attr in cmds.listAttr(cog_ctrl, ud=1) or []:
        dashboard = (attr.startswith(rt_cst.ALL_PREFIX)
                     or attr.endswith('_override')
                     or attr.endswith('_ikfk_resolved')
                     or attr in (rt_cst.ALL_DIVIDER[0],
                                 rt_cst.OVERRIDE_ALL_DIVIDER[0]))
        if dashboard and attr not in expected_attrs:
            rt_mya.remove_attribute(cog_ctrl, attr)
