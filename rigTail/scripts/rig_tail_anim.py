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
    build_anim_effects: entry point; build the enabled FX for one part
    add_anim_attributes_to_basectrl: the animatable FX attrs (gated
        per enabled effect; mirrored by rt_ctrlall.routed_attr_specs)
    build_loop: modulo-time driver the other FX read
    build_wave, build_curl, build_noise: one network per effect
'''

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
# Degrees of TOTAL bend, base to tip, that one unit of a curl attribute adds.
# The curl attributes run -10..10, so 108 puts THREE full turns at each end
# of the slider. Curl names the whole chain's wrap, not a per-joint angle
# (see build_curl), which is what makes a 12-joint and an 80-joint tail curl
# by the same amount.
#
# Three turns is what a 50-joint chain - the squid tails - can just carry at
# the default falloff without any joint reaching the guard below (its worst
# is 85 of 90 degrees). Past this the guard starts eating the increase on
# those chains rather than tightening them, so more here would want a
# higher guard, or a lower curl_falloff to spread the wrap further down the
# chain, or both.
CURL_DEGREES_PER_UNIT = 108.0
# Ceiling on the bend any SINGLE joint takes, whatever total the curl value
# and the falloff ask for. This is what keeps the tip inside the coil rather
# than folding out of it: the total above is shared out by the falloff
# profile, which peaks at the tip, and a chain with few joints cannot carry
# two turns without giving that last joint an absurd angle. The guard caps
# it, so a sparse chain simply stops tightening near the top of the slider
# while a dense one reaches the full wrap. Raising the total therefore only
# spends where the joint count can carry it. See build_curl.
CURL_MAX_JOINT_DEGREES = 90.0


# Maya inserts one of these between an expression and the plug it writes,
# and they have to come down with it - see sync_expressions.
CONVERSION_TYPES = ['unitConversion', 'unitToTimeConversion',
                    'timeToUnitConversion']


def _expression_is_current(expr, code, plug):
    '''
    Whether an expression holds this code AND is the thing driving this plug.

    Matching code alone proves nothing about the wiring, and an expression
    connected to nothing is silent rather than visibly broken.
    skipConversionNodes, so the unitConversion Maya inserts on an angle plug
    does not hide the expression behind it.

    Arguments
        expr (str): Expression node name
        code (str): Code it should hold
        plug (str): Plug it should drive

    Return
        bool: True if the expression can be left alone
    '''
    if not cmds.ls(expr, type='expression'):
        return False
    if cmds.expression(expr, q=True, s=True) != code:
        return False
    return expr in (cmds.listConnections(plug, s=True, d=False,
                                         scn=True) or [])


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
        specs (list): (expression name, code, driven plug) triples

    Return
        int: expressions rewritten
    '''
    stale = [spec for spec in specs if not _expression_is_current(*spec)]
    if not stale:
        return 0

    remove_expressions([name for name, _, _ in stale])
    for name, code, plug in stale:
        rt_maya.break_connection(plug)
        cmds.expression(n=name, s=code, o='', ae=1, uc='all')

    logger.debug(f'{len(stale)} of {len(specs)} expressions rewritten')
    return len(stale)


ensure_connect = rt_maya.ensure_connect


# BUILD ANIM EFFECTS ===================================================

def build_anim_effects(rigname, fk, ik):
    '''
    Build all animation effects.
    Effects connect to per-FX composeMatrix nodes created
    by build_matrix_offset_network() in rig_tail_matrix.py.
    Each FX writes to its own composeMatrix.

    The mirror signs are measured once here and handed to every effect, so
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
    Create a single loop expression node that outputs loop_time in seconds.
    Uses time1.outTime as continuous time source.

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

    sync_expressions([(modulo_expr, expr_code, f'{loop_time}.loop_time')])
    return f'{loop_time}.loop_time'


# WAVE =================================================================

def build_wave(rigname, basectrl, joints, loop_time=None, signs=None):
    '''
    Build wave animation effect with adjustable falloff.
    Creates one expression per joint per axis that writes directly to composeMatrix.
    ComposeMatrix nodes are pre-created by build_matrix_offset_network().

    Wave = sin(u*frequency + time*speed) * amplitude * (u^falloff) * sign

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
    num_joints = len(joints)
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

    specs = []
    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_naming.get_index_from_name(jnt)
        u = idx / float(num_joints - 1) if num_joints > 1 else 0.0

        for rot_axis, wave_attr in wave_axes:
            expr = f'{rigname}_{NN:02d}_wave{rot_axis}_expression'
            compose_node = f'{rigname}_{NN:02d}_wave_composeMatrix'

            if not cmds.objExists(compose_node):
                logger.trace(f'{compose_node} does not exist, skipping wave expression')
                continue

            amp_src = amp_srcs[wave_attr]

            expr_code = f'''// Wave expression for joint {NN:02d} axis {rot_axis}
float $loop_enabled = {loop_enabled_src};
float $wave_freq = {freq_src};
float $wave_speed = {speed_src} * 0.5;
float $freq;
float $speed;
if ($loop_enabled > 0.5) {{
    $freq  = floor($wave_freq + 0.5);
    if ($freq < 1.0) $freq = 1.0;          // ensure at least 1
    $speed = floor($wave_speed + 0.5);
}} else {{
    $freq = $wave_freq;
    $speed = $wave_speed;
}}
float $amp = {amp_src} * {3.0 * signs[rot_axis]};
float $falloff = {falloff_src};
float $t = {time_source};
float $u = {u};

float $phase = (6.28318530718 * $u * $freq) + ($t * $speed);
float $val = sin($phase);
float $w = pow($u, $falloff);
float $out = $val * $amp * $w;

{compose_node}.inputRotate{rot_axis} = $out;
'''
            specs.append((expr, expr_code,
                          f'{compose_node}.inputRotate{rot_axis}'))

    sync_expressions(specs)
    logger.debug(f'{rigname}: Wave effect built')


# CURL =================================================================

def build_curl(rigname, basectrl, joints, signs=None):
    '''
    Build curl animation effect with adjustable falloff.
    Uses DG node graph per joint for clean connections.
    Connects to curl_composeMatrix nodes pre-created by build_matrix_offset_network().

    Math, for joint i of n (u = i / (n - 1), 0 at the base, 1 at the tip):

        share_i  = u_i ** curl_falloff / SUM_j(u_j ** curl_falloff)
        rotate_i = clamp(curl * CURL_DEGREES_PER_UNIT * share_i,
                         +/- CURL_MAX_JOINT_DEGREES)

    The shares SUM TO ONE, so a curl attribute names the total bend of the
    whole chain and the falloff only decides how that bend is distributed
    along it - tip-loaded at a high falloff, near-uniform at a low one.

    Normalizing is what makes the total mean something. The per-joint
    rotations compound down the BN hierarchy, so the chain's total wrap is
    their sum; an unnormalized u**falloff profile made that sum grow with
    both the curl value AND the joint count, so a denser chain curled
    further than a sparse one at the same slider value. Dividing by the live
    sum bounds the total at curl * CURL_DEGREES_PER_UNIT degrees whatever
    the joint count.

    The per-joint clamp is what keeps the tip in the coil. The share profile
    peaks at the tip, so the last joint always takes the largest single
    angle; left alone it passes a right angle and folds the final bone (and
    the '_ee_' riding on it) out of an otherwise tidy spiral. Clamping the
    ANGLE rather than lowering the total is what lets the total go to two
    full turns: a chain with enough joints spreads that wrap thinly enough
    to never reach the guard and coils twice, while a sparse chain saturates
    its last joints and simply stops tightening. Tightness is limited by the
    joint count, which is the truth of the thing - a 12-bone chain cannot
    draw two clean turns. A LOWER curl_falloff buys back most of it: it
    spreads the same total over the whole chain instead of piling it on the
    last few joints, so far less of it is lost to the guard.

    The profile (u**falloff and its sum) is built once per chain and shared
    by all three axes: it does not depend on the axis, and one copy means
    curlX/Y/Z stay consistent by construction. The tip joint always
    contributes u**falloff = 1, so the divisor can never reach zero. The
    clamp is likewise one node per joint carrying all three axes on its
    R/G/B channels.

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

        # One clamp per joint, all three axes on its R/G/B channels. Curl
        # runs both ways, so the guard is symmetric about zero. Bounds are
        # written per channel rather than as a compound: they are set on
        # every build, not just on creation, so a changed
        # CURL_MAX_JOINT_DEGREES reaches a rig that cleanup kept.
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
        # The mirror sign rides on the degrees-per-unit factor: it is the one
        # place the whole axis passes through, so nothing downstream has to
        # know which side of a pair this is
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
    Build procedural noise for waving/tentacle motion.
    Creates one expression per joint per axis that writes directly to composeMatrix.
    ComposeMatrix nodes are pre-created by build_matrix_offset_network().

    Behavior:
    - Dominant traveling low-frequency wave along the chain for kelp/tentacle motion
    - Small, higher-frequency jitter added on top for natural variation
    - Per-joint falloff so root stays near straight axis and tip is looser
    - Deterministic per-joint/axis seed for stable but different motion per joint/axis
    - When loop_time is provided, uses integer harmonic counts so the animation loops exactly
    - The seed is per joint index and axis, so an L/R pair already jitters
      to the same numbers; the mirror signs (rt_mirror) are what turn
      that into the two sides jittering as mirror images

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
    num_joints = len(joints)
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

    specs = []
    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_naming.get_index_from_name(jnt)
        u = idx / float(num_joints - 1) if num_joints > 1 else 0.0

        for axis in effect_axes:
            expr = f'{rigname}_{NN:02d}_noise_{axis}_expression'
            compose_node = f'{rigname}_{NN:02d}_noise_composeMatrix'
            axis_seed = axis_offsets[axis]

            if not cmds.objExists(compose_node):
                logger.trace(f'{compose_node} does not exist, skipping noise expression')
                continue

            expr_code = f'''
float $loop_enabled = {loop_enabled_src};
float $amp = {amp_src} * {signs[axis]};
float $noise_freq = {freq_src};
float $noise_speed = {speed_src} * 0.2;
float $t = {time_source};
float $u = {u};

// deterministic per-joint/axis seed -> [0,1)
float $h = sin(({idx} * 12.9898) + ({axis_seed} * 78.233)) * 43758.5453;
float $seed = $h - floor($h);

float $f1;
float $f2;
float $f3;
float $speed;
if ($loop_enabled > 0.5) {{
    // quantize base to integer counts so sin(loop_time * N) loops exactly
    $f1 = floor($noise_freq + 0.5);
    if ($f1 < 1.0) $f1 = 1.0;          // ensure at least 1
    $f2 = $f1 * 2.0;                   // integer multiple
    $f3 = $f1 * 3.0;                   // integer multiple
    $speed = floor($noise_speed + 0.5);
}} else {{
    // freer fractional frequencies for organic motion
    $f1 = $noise_freq * 0.5;   // gentle base when free (tweakable)
    $f2 = $noise_freq * 1.7;
    $f3 = $noise_freq * 2.3;
    $speed = $noise_speed;
}}

// per-harmonic phases derived from seed
float $p1 = $seed * 6.28318530718;
float $p2 = $p1 + 1.234;
float $p3 = $p1 + 2.468;

// traveling subtle wave, spatial dependence
float $spatial = $u * 6.28318530718 * 0.75;

// build noise as small weighted sum of harmonics (zero-mean)
float $noise = 0.6*sin($t*$f1*$speed + $p1 + $spatial)
             + 0.35*sin($t*$f2*$speed + $p2 + $spatial*0.7)
             + 0.2*sin($t*$f3*$speed + $p3 + $spatial*0.4);

// small integer jitter frequency from seed
float $jf = floor($seed * 6.0 + 0.5);
if ($jf < 1.0) $jf = 1.0;
$noise += 0.12 * sin($t * $jf + $seed * 3.14159265);

// loose decay so base stays nearly straight
float $fall = pow($u, 1.1);

float $out = $noise * $amp * $fall;

{compose_node}.inputRotate{axis} = $out;
'''
            specs.append((expr, expr_code,
                          f'{compose_node}.inputRotate{axis}'))

    sync_expressions(specs)
    logger.debug(f'{rigname}: Noise effect built')
