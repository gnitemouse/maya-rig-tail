'''
# rig_tail_anim.py
author: Daisy Jane @gnitemouse

Animation FX for Rig Tail: procedural motion layered on top of whatever
the controls do, driven by animatable basectrl attributes.

- Curl: progressive static rotation (animator-driven, adjustable falloff)
- Wave: sinusoidal traveling wave (time-based, adjustable falloff)
- Noise: procedural jitter (time-based)
- Loop: seamless timeline looping (modulo time)

Each FX writes per-joint rotations into its own composeMatrix, which
rig_tail_matrix multiplies into the BN joint's offsetParentMatrix in
front of the driver term - so FX rotate each joint about its own pivot
and never touch the joints' channels. Curl is a pure node network; wave
and noise are expressions, which is what buys them time as an input.
Attribute sources go through rt_ctrlall.resolved_plug so the Main
Controller dashboard can route them.

Wave and noise are ONE expression per part, driving every joint and axis.
An expression costs the DG a fixed price per node per evaluation that
dwarfs the arithmetic inside it: curl's ~3500-node graph is free at
playback while the same count of expressions was 94% of frame time. So
the count is what matters, and everything a part's joints share - the
frequencies, the amplitudes, the clock - is computed once per frame
rather than once per driven plug.

Every effect is built to be met again: nodes are created only when absent
and connected only when unconnected, and expressions pass through
sync_expressions, which compares each against the code it should hold. An
effect's shape is fixed by the rig it describes, so rebuilding one over an
unchanged rig settles to a pass of queries.

An L/R pair's FX obey MIRROR_BEHAVIOR on all three axes, via the signs
rig_tail_mirror measures for the pair - see that module for why a
mirrored skeleton alone can only ever manage one of them.

Functions:
    remove_expressions: delete expressions without the delete cascading
    sync_expressions: write only the expressions whose code has moved
    drop_per_joint_expressions: clear a rig's superseded FX expressions
    build_anim_effects: entry point; build the enabled FX for one part
    add_anim_attributes_to_basectrl: the animatable FX attrs (gated
        per enabled effect; mirrored by rt_ctrlall.routed_attr_specs)
    build_loop: modulo-time driver the other FX read
    build_wave, build_curl, build_noise: one network per effect
'''

import math

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_mirror as rt_mirror

logger = logger_setup(__name__)

# 2*pi, matching the literal used inside the loop expression.
TWO_PI = 6.28318530718
# Default value of the basectrl 'loop_frame' attribute. When the Loop
# effect is NOT built there is no loop node normalizing time, so wave and
# noise scale raw time by TWO_PI / LOOP_FRAME_DEFAULT to run at the same
# speed as a loop-built rig with loop toggled off (whose loop node outputs
# time1.outTime * TWO_PI / loop_frame at the default loop_frame).
LOOP_FRAME_DEFAULT = 60
# Time-source expression for wave/noise when the Loop effect is not built.
UNLOOPED_TIME_SRC = f'(time1.outTime * {TWO_PI / LOOP_FRAME_DEFAULT})'
# Degrees of TOTAL bend, base to tip, per unit of a curl attribute. The
# attributes run -10..10, so 108 is three full turns at each end. Curl
# names the whole chain's wrap rather than a per-joint angle (see
# build_curl), which is what makes a 12-joint and an 80-joint tail curl by
# the same amount.
CURL_DEGREES_PER_UNIT = 108.0

# Ceiling on the bend any SINGLE joint takes. The falloff profile peaks at
# the tip, and a chain with few joints cannot carry the total above without
# giving that last joint an angle that folds it out of the coil. Capping
# the ANGLE rather than the total is what lets the total go high: a dense
# chain spreads the wrap thinly enough never to reach the guard, a sparse
# one saturates its last joints and stops tightening. So the tightness on
# offer is limited by the joint count, which is the truth of the thing.
CURL_MAX_JOINT_DEGREES = 90.0


# Maya inserts one of these between an expression and the plug it writes,
# and they have to come down with it - see sync_expressions.
CONVERSION_TYPES = ['unitConversion', 'unitToTimeConversion',
                    'timeToUnitConversion']


def _mel_float(value):
    '''
    A Python float as a MEL literal that reads back as the same double.

    repr round-trips, but can spell a number '1e-17', and MEL wants a
    decimal point before the exponent.

    Arguments
        value (float): Value to spell

    Return
        str: MEL float literal
    '''
    text = repr(float(value))
    if 'e' in text and '.' not in text:
        text = text.replace('e', '.0e')
    return text


def _expression_is_current(expr, code, plugs):
    '''
    Whether an expression holds this code AND is the thing driving every
    plug it should.

    Matching code alone proves nothing about the wiring, and an expression
    connected to nothing is silent rather than visibly broken.
    skipConversionNodes, so the unitConversion Maya inserts on an angle plug
    does not hide the expression behind it.

    The plugs go to listConnections in one call. It returns one entry per
    CONNECTED destination, so a result that is this expression as many times
    over as there are plugs says all of them are driven and all by this - no
    plug names to match, and one command instead of a hundred and fifty.

    Arguments
        expr (str): Expression node name
        code (str): Code it should hold
        plugs (list): Plugs it should drive

    Return
        bool: True if the expression can be left alone
    '''
    if not cmds.ls(expr, type='expression'):
        return False
    if cmds.expression(expr, q=True, s=True) != code:
        return False
    drivers = cmds.listConnections(plugs, s=True, d=False, scn=True) or []
    return len(drivers) == len(plugs) and set(drivers) == {expr}


def remove_expressions(exprs):
    '''
    Delete expressions together with the conversion nodes Maya inserted
    alongside them.

    An expression cannot simply be deleted: a raw delete cascades through
    its connection web, taking the loop node, the sibling expressions and
    their composeMatrix nodes. rt_maya.remove_nodes disconnects the whole
    list before deleting any of it, so the cascade has nothing to travel
    along. The conversions go in the same batch, since one left holding a
    target plug shuts a replacement expression out of it.

    Arguments
        exprs (list): Expression node names

    Return
        int: nodes deleted
    '''
    live = rt_maya.existing(exprs)
    if not live:
        return 0
    conversions = cmds.ls(cmds.listConnections(live) or [],
                          type=CONVERSION_TYPES) or []
    return rt_maya.remove_nodes(live + conversions)


def sync_expressions(specs):
    '''
    Write only the expressions that are not already what they should be.

    An FX expression's code is fixed by the rig it describes - the joint's
    position along the chain, the mirror signs, and the plugs
    rt_ctrlall.resolved_plug routes the attributes through - so a rebuild
    over an unchanged rig wants exactly the expressions already in the
    scene, at a query each.

    The rest go down together through remove_expressions, then each target
    plug is cleared before it is written. Whatever holds a plug would
    otherwise refuse the new expression outright, and it is not always
    reachable from the expression being replaced - an orphaned
    unitConversion survives on the plug side alone.

    Arguments
        specs (list): (expression name, code, driven plugs) triples

    Return
        int: expressions rewritten
    '''
    stale = [spec for spec in specs if not _expression_is_current(*spec)]
    if not stale:
        return 0

    remove_expressions([name for name, _, _ in stale])
    for name, code, plugs in stale:
        for plug in plugs:
            rt_maya.break_connection(plug)
        cmds.expression(n=name, s=code, o='', ae=1, uc='all')

    logger.debug(f'{len(stale)} of {len(specs)} expressions rewritten')
    return len(stale)


def drop_per_joint_expressions(pattern):
    '''
    Delete the per-joint-per-axis expressions a rig built before wave and
    noise collapsed to one expression each.

    The light teardown leaves the FX network standing for the build to meet
    (rt_cleanup.cleanup_connections), so without this sweep the old nodes
    survive a rebuild holding the plugs the new expression wants, and go on
    evaluating for nothing. The patterns cannot match the one-per-part
    names, so a rig already converted pays a single ls.

    Arguments
        pattern (str): Name pattern for the superseded expressions

    Return
        int: nodes deleted
    '''
    return remove_expressions(cmds.ls(pattern, type='expression') or [])


ensure_connect = rt_maya.ensure_connect


# BUILD ANIM EFFECTS ===================================================

def build_anim_effects(rigname, fk, ik):
    '''
    Build every enabled animation effect for one rig part.

    Each effect writes into its own composeMatrix, pre-created by
    rig_tail_matrix.build_matrix_offset_network. The mirror signs are measured once here and handed to every effect, so
    all three read the pair the same way (see rt_mirror).

    Arguments
        rigname (str): Name of rig component
        fk (bool): Connect FK anim effects
        ik (bool): Connect IK anim effects
    '''
    logger.debug(f'{rigname}: Build Animation Effects (Matrix-Per-FX)')

    if rigname not in rt_constants.JOINTS_BN:
        logger.warning(f'{rigname}: No BN joints found')
        return

    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    joints = rt_constants.JOINTS_BN[rigname]
    signs = rt_mirror.rotation_signs(rigname)

    loop_time = None
    if rt_constants.EFFECTS['loop']:
        loop_time = build_loop(rigname, basectrl)

    if rt_constants.EFFECTS['wave']:
        build_wave(rigname, basectrl, joints, loop_time, signs)
    if rt_constants.EFFECTS['curl']:
        build_curl(rigname, basectrl, joints, signs)
    if rt_constants.EFFECTS['noise']:
        build_noise(rigname, basectrl, joints, loop_time, signs)

def add_anim_attributes_to_basectrl(rigname, basectrl):
    logger.debug(f'{rigname}: Add animation effect attributes to basectrl')

    if rt_constants.effects_enabled():
        rt_maya.add_attribute_enum(basectrl, rt_constants.ANIM_DIVIDER[0], rt_constants.ANIM_DIVIDER[1], rt_constants.ANIM_DIVIDER[2])

    wave_axes = [('X', 'waveX'), ('Y', 'waveY'), ('Z', 'waveZ')]
    curl_axes = [('X', 'curlX'), ('Y', 'curlY'), ('Z', 'curlZ')]

    if rt_constants.EFFECTS['wave']:
        for axis, attr in wave_axes:
            if not cmds.attributeQuery(attr, n=basectrl, ex=1):
                cmds.addAttr(basectrl, ln=attr, nn=f'Wave {axis}', at='float', k=1, dv=0, min=-10, max=10)
        if not cmds.attributeQuery('wave_frequency', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='wave_frequency', nn='Wave Frequency', at='float', k=1, dv=2, min=0.1, max=5)
        if not cmds.attributeQuery('wave_speed', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='wave_speed', nn='Wave Speed', at='float', k=1, dv=3, min=0, max=10)
        if not cmds.attributeQuery('wave_falloff', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='wave_falloff', nn='Wave Falloff', at='float', k=1, dv=1, min=0.1, max=10)

    if rt_constants.EFFECTS['curl']:
        for axis, attr in curl_axes:
            if not cmds.attributeQuery(attr, n=basectrl, ex=1):
                cmds.addAttr(basectrl, ln=attr, nn=f'Curl {axis}', at='float', k=1, dv=0, min=-10, max=10)
        if not cmds.attributeQuery('curl_falloff', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='curl_falloff', nn='Curl Falloff', at='float', k=1, dv=3.0, min=0.1, max=10)

    if rt_constants.EFFECTS['noise']:
        if not cmds.attributeQuery('noise', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='noise', nn='Noise', at='float', k=1, dv=0, min=-10, max=10)
        if not cmds.attributeQuery('noise_frequency', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='noise_frequency', nn='Noise Frequency', at='float', k=1, dv=1, min=0.1, max=5)
        if not cmds.attributeQuery('noise_speed', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='noise_speed', nn='Noise Speed', at='float', k=1, dv=3, min=0, max=10)

    if rt_constants.EFFECTS['loop']:
        if not cmds.attributeQuery('loop', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='loop', nn='Loop', at='bool', k=1, dv=0)
        if not cmds.attributeQuery('loop_frame', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='loop_frame', nn='Loop Frame', at='long', k=1,
                         dv=LOOP_FRAME_DEFAULT, min=1)




# LOOP =================================================================

def build_loop(rigname, basectrl):
    '''
    Create the one expression node that outputs loop_time in seconds,
    from time1.outTime.

    Returns:
        str: Output attr plug for loop time (e.g.'node.loop_time')
    '''
    logger.trace(f'{rigname}: Loop System (Modulo Time)')
    modulo_expr = f'{rigname}_loop_time_expression'
    loop_time = f'{rigname}_loop_time'

    if not cmds.objExists(loop_time):
        cmds.createNode('network', n=loop_time)
        cmds.addAttr(loop_time, ln='loop_time', at='time', k=1)

    # resolved_plug: override condition output when the main controller
    # dashboard is active, the basectrl attribute otherwise
    loop_src = rt_ctrlall.resolved_plug(rigname, 'loop')
    frame_src = rt_ctrlall.resolved_plug(rigname, 'loop_frame')

    expr_code = f'''// Loop modulo expression - normalized time output
float $loop_enabled = {loop_src};
float $loop_len = {frame_src};
float $t = time1.outTime;
float $two_pi = {TWO_PI};

if ($loop_enabled > 0.5) {{
    float $mod = $t - floor($t / $loop_len) * $loop_len;
    {loop_time}.loop_time = $mod * $two_pi / $loop_len;
}} else {{
    {loop_time}.loop_time = $t * $two_pi / $loop_len;
}}
'''

    sync_expressions([(modulo_expr, expr_code, [f'{loop_time}.loop_time'])])
    return f'{loop_time}.loop_time'


# WAVE =================================================================

def build_wave(rigname, basectrl, joints, loop_time=None, signs=None):
    '''
    Build the wave effect: a sinusoidal traveling wave with adjustable
    falloff, as one expression per part driving every joint and axis.

    Wave = sin(u*frequency + time*speed) * amplitude * (u^falloff) * sign

    Only u varies down the chain, and the three axes differ only in
    amplitude - so the frequencies and the clock are read once per frame,
    and sin and pow are called once per joint rather than once per plug.

    Arguments:
        rigname (str): Name of rig component
        basectrl (str): Base control with wave attributes
        joints (list): List of BN joints (joint 00 will be skipped in matrix network)
        loop_time (str): Optional loop time output plug
        signs (dict): Per-axis mirror signs from
            rt_mirror.rotation_signs; None
            leaves every axis driving its own direction raw
    '''
    logger.trace(f'{rigname}: Wave Animation Effect')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for wave')
        return

    signs = signs or rt_mirror.NO_MIRROR
    wave_axes = [('X', 'waveX'), ('Y', 'waveY'), ('Z', 'waveZ')]
    span = float(len(joints) - 1)
    # With no loop node, normalize raw time the same way the loop node does
    # at its default frame (loop off) so wave speed matches a loop-built rig.
    time_source = loop_time if loop_time else UNLOOPED_TIME_SRC
    # The loop attribute only exists when the Loop effect is built; when it
    # is not, reference a literal 0 so the expression still compiles and
    # takes the non-looping branch. resolved_plug: override condition
    # output when the main controller dashboard is active, the basectrl
    # attribute otherwise.
    loop_enabled_src = rt_ctrlall.resolved_plug(rigname, 'loop') if loop_time else '0'
    freq_src = rt_ctrlall.resolved_plug(rigname, 'wave_frequency')
    speed_src = rt_ctrlall.resolved_plug(rigname, 'wave_speed')
    falloff_src = rt_ctrlall.resolved_plug(rigname, 'wave_falloff')

    amp_srcs = {attr: rt_ctrlall.resolved_plug(rigname, attr)
                for _, attr in wave_axes}

    lines = [f'// Wave for {rigname} - every joint and axis',
             f'float $loop_enabled = {loop_enabled_src};',
             f'float $wave_freq = {freq_src};',
             f'float $wave_speed = {speed_src} * 0.5;',
             'float $freq;',
             'float $speed;',
             'if ($loop_enabled > 0.5) {',
             '    $freq  = floor($wave_freq + 0.5);',
             '    if ($freq < 1.0) $freq = 1.0;          // ensure at least 1',
             '    $speed = floor($wave_speed + 0.5);',
             '} else {',
             '    $freq = $wave_freq;',
             '    $speed = $wave_speed;',
             '}']
    lines += [f'float $amp{rot_axis} = {amp_srcs[wave_attr]} '
              f'* {_mel_float(3.0 * signs[rot_axis])};'
              for rot_axis, wave_attr in wave_axes]
    lines += [f'float $falloff = {falloff_src};',
              f'float $t = {time_source};',
              'float $u;',
              'float $val;',
              'float $w;']

    plugs = []
    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_naming.get_index_from_name(jnt)
        compose_node = f'{rigname}_{NN:02d}_wave_composeMatrix'

        if not cmds.objExists(compose_node):
            logger.trace(f'{compose_node} does not exist, skipping wave joint')
            continue

        lines += ['',
                  f'// joint {NN:02d}',
                  f'$u = {_mel_float(idx / span)};',
                  '$val = sin((6.28318530718 * $u * $freq) + ($t * $speed));',
                  '$w = pow($u, $falloff);']
        for rot_axis, _ in wave_axes:
            lines.append(f'{compose_node}.inputRotate{rot_axis} = '
                         f'$val * $amp{rot_axis} * $w;')
            plugs.append(f'{compose_node}.inputRotate{rot_axis}')

    drop_per_joint_expressions(f'{rigname}_*_wave?_expression')
    if not plugs:
        logger.warning(f'{rigname}: No wave composeMatrix nodes to drive')
        return

    sync_expressions([(f'{rigname}_wave_expression',
                       '\n'.join(lines) + '\n', plugs)])
    logger.debug(f'{rigname}: Wave effect built ({len(plugs)} plugs)')


# CURL =================================================================

def build_curl(rigname, basectrl, joints, signs=None):
    '''
    Build the curl effect: a progressive static bend with adjustable
    falloff, as a node graph per joint rather than an expression.

    Math, for joint i of n (u = i / (n - 1), 0 at the base, 1 at the tip):

        share_i  = u_i ** curl_falloff / SUM_j(u_j ** curl_falloff)
        rotate_i = clamp(curl * CURL_DEGREES_PER_UNIT * share_i,
                         +/- CURL_MAX_JOINT_DEGREES)

    The shares SUM TO ONE, so a curl attribute names the total bend of the
    whole chain and the falloff only decides how that bend is distributed
    along it - tip-loaded at a high falloff, near-uniform at a low one.
    Normalising is what makes the total mean anything: the per-joint
    rotations compound down the BN hierarchy, so an unnormalised profile
    would grow the chain's wrap with the joint count as well as the dial.

    The clamp is what keeps the tip in the coil, and CURL_MAX_JOINT_DEGREES
    explains why it caps the angle rather than the total. A lower
    curl_falloff buys back most of what the guard takes, by spreading the
    same total over the whole chain instead of the last few joints.

    The profile (u**falloff and its sum) is built once per chain and shared
    by all three axes, so curlX/Y/Z stay consistent by construction. The
    tip always contributes u**falloff = 1, so the divisor cannot reach
    zero. The clamp is likewise one node per joint, carrying all three axes
    on its R/G/B channels.

    Arguments:
        rigname (str): Name of rig component
        basectrl (str): Base control with curl attributes
        joints (list): List of BN joints (joint 00 will be skipped)
        signs (dict): Per-axis mirror signs from
            rt_mirror.rotation_signs; None
            leaves every axis curling its own direction raw
    '''
    logger.trace(f'{rigname}: Curl Animation Effect')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for curl')
        return

    signs = signs or rt_mirror.NO_MIRROR
    curl_axes = [('X', 'curlX'), ('Y', 'curlY'), ('Z', 'curlZ')]
    # Rotation axis -> the clamp channel carrying it (one clamp per joint)
    clamp_channel = {'X': 'R', 'Y': 'G', 'Z': 'B'}
    curl_joints = list(enumerate(joints[1:], 1))
    span = float(len(joints) - 1)

    # Shared falloff profile: u ** curl_falloff per joint, and their live sum.
    # Live, because curl_falloff is animatable - a constant divisor baked at
    # build time would only be right at the falloff it was built for.
    total = f'{rigname}_curl_falloffSum_plusMinusAverage'
    if not cmds.objExists(total):
        cmds.createNode('plusMinusAverage', n=total)
    cmds.setAttr(f'{total}.operation', 1)  # sum

    weights = {}
    clamps = {}
    for slot, (i, jnt) in enumerate(curl_joints):
        NN = rt_naming.get_index_from_name(jnt)

        falloff_node = f'{rigname}_curl_{NN:02d}_falloff_multiplyDivide'
        if not cmds.objExists(falloff_node):
            cmds.createNode('multiplyDivide', n=falloff_node)
        cmds.setAttr(f'{falloff_node}.operation', 3)  # power
        cmds.setAttr(f'{falloff_node}.input1X', i / span)
        # resolved_plug: override condition output when the dashboard is
        # active, the basectrl attribute otherwise
        ensure_connect(rt_ctrlall.resolved_plug(rigname, 'curl_falloff'),
                       f'{falloff_node}.input2X')
        ensure_connect(f'{falloff_node}.outputX', f'{total}.input1D[{slot}]')

        weight = f'{rigname}_curl_{NN:02d}_weight_multiplyDivide'
        if not cmds.objExists(weight):
            cmds.createNode('multiplyDivide', n=weight)
        cmds.setAttr(f'{weight}.operation', 2)  # divide
        ensure_connect(f'{falloff_node}.outputX', f'{weight}.input1X')
        ensure_connect(f'{total}.output1D', f'{weight}.input2X')
        weights[NN] = weight

        # Curl runs both ways, so the guard is symmetric about zero. Set on
        # every build, not just on creation, so a changed
        # CURL_MAX_JOINT_DEGREES reaches a rig cleanup kept in place.
        clamp = f'{rigname}_curl_{NN:02d}_clamp'
        if not cmds.objExists(clamp):
            cmds.createNode('clamp', n=clamp)
        for channel in 'RGB':
            cmds.setAttr(f'{clamp}.min{channel}', -CURL_MAX_JOINT_DEGREES)
            cmds.setAttr(f'{clamp}.max{channel}', CURL_MAX_JOINT_DEGREES)
        clamps[NN] = clamp

    for rot_axis, curl_attr in curl_axes:
        remap = f'{rigname}_curl{rot_axis}_remap_multiplyDivide'
        if not cmds.objExists(remap):
            cmds.createNode('multiplyDivide', n=remap)
        cmds.setAttr(f'{remap}.operation', 1)
        # The mirror sign rides on the degrees-per-unit factor, the one
        # place a whole axis passes through, so nothing downstream needs to
        # know which side of a pair it is on
        cmds.setAttr(f'{remap}.input2X',
                     CURL_DEGREES_PER_UNIT * signs[rot_axis])
        ensure_connect(rt_ctrlall.resolved_plug(rigname, curl_attr), f'{remap}.input1X')

        for _, jnt in curl_joints:
            NN = rt_naming.get_index_from_name(jnt)
            compose_node = f'{rigname}_{NN:02d}_curl_composeMatrix'
            if not cmds.objExists(compose_node):
                continue

            curl_mult = f'{rigname}_curl{rot_axis}_{NN:02d}_multiplyDivide'
            if not cmds.objExists(curl_mult):
                cmds.createNode('multiplyDivide', n=curl_mult)
            cmds.setAttr(f'{curl_mult}.operation', 1)
            ensure_connect(f'{remap}.outputX', f'{curl_mult}.input1X')
            ensure_connect(f'{weights[NN]}.outputX', f'{curl_mult}.input2X')
            channel = clamp_channel[rot_axis]
            ensure_connect(f'{curl_mult}.outputX',
                           f'{clamps[NN]}.input{channel}')
            ensure_connect(f'{clamps[NN]}.output{channel}',
                           f'{compose_node}.inputRotate{rot_axis}')
            logger.trace(f'Connected curl{rot_axis} to {compose_node}')

    logger.debug(f'{rigname}: Curl effect built '
                 f'({len(curl_joints)} joints, '
                 f'{CURL_DEGREES_PER_UNIT:g} deg total per unit, '
                 f'{CURL_MAX_JOINT_DEGREES:g} deg max per joint)')


# NOISE ================================================================

def build_noise(rigname, basectrl, joints, loop_time=None, signs=None):
    '''
    Build the noise effect: procedural jitter for kelp and tentacle
    motion, as one expression per part driving every joint and axis.

    A dominant low-frequency wave travels along the chain, with a smaller
    higher-frequency jitter on top, under a per-joint falloff that keeps
    the root near its axis and leaves the tip loose. Given loop_time the
    harmonic counts are integers, so the animation loops exactly.

    The seed is per joint index and axis, so an L/R pair already jitters
    to the same numbers; the mirror signs (rt_mirror) are what turn that
    into the two sides jittering as mirror images.

    Everything downstream of the seed - the phases, the spatial offset,
    the jitter frequency, the falloff - is fixed at build time, so it is
    computed here and spelled into the code as a literal instead of being
    recomputed every frame. The three harmonic rates depend only on time,
    so they are hoisted to the top; what is left per plug is the four sin
    calls that actually vary.

    Arguments:
        rigname (str): Name of rig component
        basectrl (str): Base control with noise attributes
        joints (list): List of BN joints (joint 00 will be skipped)
        loop_time (str): Optional loop time output plug
        signs (dict): Per-axis mirror signs from
            rt_mirror.rotation_signs; None
            leaves every axis jittering its own direction raw
    '''
    logger.trace(f'{rigname}: Noise Effect (wavy, loopable)')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for noise')
        return

    signs = signs or rt_mirror.NO_MIRROR
    effect_axes = ['X', 'Y', 'Z']
    axis_offsets = {'X': 0.0, 'Y': 100.0, 'Z': 200.0}
    span = float(len(joints) - 1)
    # With no loop node, normalize raw time the same way the loop node does
    # at its default frame (loop off) so noise speed matches a loop-built rig.
    time_source = loop_time if loop_time else UNLOOPED_TIME_SRC
    # The loop attribute only exists when the Loop effect is built; when it
    # is not, reference a literal 0 so the expression still compiles and
    # takes the non-looping branch. resolved_plug: override condition
    # output when the main controller dashboard is active, the basectrl
    # attribute otherwise.
    loop_enabled_src = rt_ctrlall.resolved_plug(rigname, 'loop') if loop_time else '0'
    amp_src = rt_ctrlall.resolved_plug(rigname, 'noise')
    freq_src = rt_ctrlall.resolved_plug(rigname, 'noise_frequency')
    speed_src = rt_ctrlall.resolved_plug(rigname, 'noise_speed')

    lines = [f'// Noise for {rigname} - every joint and axis',
             f'float $loop_enabled = {loop_enabled_src};',
             f'float $noise_freq = {freq_src};',
             f'float $noise_speed = {speed_src} * 0.2;',
             f'float $t = {time_source};']
    lines += [f'float $amp{axis} = {amp_src} * {_mel_float(signs[axis])};'
              for axis in effect_axes]
    lines += ['float $f1;',
              'float $f2;',
              'float $f3;',
              'float $speed;',
              'if ($loop_enabled > 0.5) {',
              '    // quantize base to integer counts so sin(loop_time * N) loops exactly',
              '    $f1 = floor($noise_freq + 0.5);',
              '    if ($f1 < 1.0) $f1 = 1.0;          // ensure at least 1',
              '    $f2 = $f1 * 2.0;                   // integer multiple',
              '    $f3 = $f1 * 3.0;                   // integer multiple',
              '    $speed = floor($noise_speed + 0.5);',
              '} else {',
              '    // freer fractional frequencies for organic motion',
              '    $f1 = $noise_freq * 0.5;   // gentle base when free (tweakable)',
              '    $f2 = $noise_freq * 1.7;',
              '    $f3 = $noise_freq * 2.3;',
              '    $speed = $noise_speed;',
              '}',
              '',
              '// the three harmonic clocks, shared by every joint and axis',
              'float $w1 = $t * $f1 * $speed;',
              'float $w2 = $t * $f2 * $speed;',
              'float $w3 = $t * $f3 * $speed;']

    plugs = []
    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_naming.get_index_from_name(jnt)
        compose_node = f'{rigname}_{NN:02d}_noise_composeMatrix'

        if not cmds.objExists(compose_node):
            logger.trace(f'{compose_node} does not exist, skipping noise joint')
            continue

        u = idx / span
        # traveling subtle wave, spatial dependence
        spatial = u * 6.28318530718 * 0.75
        # loose decay so base stays nearly straight
        fall = pow(u, 1.1)
        lines += ['', f'// joint {NN:02d}']

        for axis in effect_axes:
            # deterministic per-joint/axis seed -> [0,1), and the phases and
            # jitter frequency it fixes
            h = math.sin((idx * 12.9898) + (axis_offsets[axis] * 78.233)) \
                * 43758.5453
            seed = h - math.floor(h)
            p1 = seed * 6.28318530718
            jf = max(math.floor(seed * 6.0 + 0.5), 1.0)

            # weighted sum of harmonics (zero-mean), then the jitter
            noise = (f'0.6 * sin($w1 + {_mel_float(p1 + spatial)})'
                     f' + 0.35 * sin($w2 + {_mel_float(p1 + 1.234 + spatial * 0.7)})'
                     f' + 0.2 * sin($w3 + {_mel_float(p1 + 2.468 + spatial * 0.4)})'
                     f' + 0.12 * sin($t * {_mel_float(jf)}'
                     f' + {_mel_float(seed * 3.14159265)})')
            lines.append(f'{compose_node}.inputRotate{axis} = ({noise})'
                         f' * $amp{axis} * {_mel_float(fall)};')
            plugs.append(f'{compose_node}.inputRotate{axis}')

    drop_per_joint_expressions(f'{rigname}_*_noise_?_expression')
    if not plugs:
        logger.warning(f'{rigname}: No noise composeMatrix nodes to drive')
        return

    sync_expressions([(f'{rigname}_noise_expression',
                       '\n'.join(lines) + '\n', plugs)])
    logger.debug(f'{rigname}: Noise effect built ({len(plugs)} plugs)')
