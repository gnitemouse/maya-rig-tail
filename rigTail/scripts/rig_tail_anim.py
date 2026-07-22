'''
# rig_tail_anim.py
author: Daisy Jane @gnitemouse

Animation Effects for Rig Tail
Matrix-based offset architecture outputs each FX to its own composeMatrix.

FX
- Curl: Progressive static rotation (animator-driven, adjustable falloff)
- Wave: Sinusoidal traveling wave (time-based, adjustable falloff)
- Noise: Procedural noise variation (time-based)
- Loop: Seamless timeline looping (modulo time)
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_maya as rt_mya

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


def delete_expression(expr):
    '''
    Delete an expression node safely.
    cmds.delete on a connected expression cascades through its whole
    connection web (loop network node, sibling FX expressions, composeMatrix
    nodes), so all connections must be broken before deleting.
    Unit conversion nodes are removed with the expression: a dangling
    conversion left connected to the written attribute blocks the rebuilt
    expression from connecting to it.
    '''
    if not cmds.objExists(expr):
        return
    conv_types = ('unitConversion', 'unitToTimeConversion', 'timeToUnitConversion')
    convs = {c for c in (cmds.listConnections(expr) or [])
             if cmds.nodeType(c) in conv_types}
    rt_mya.remove(expr)
    for conv in convs:
        if cmds.objExists(conv):
            rt_mya.remove(conv)

ensure_connect = rt_mya.ensure_connect


# BUILD ANIM EFFECTS ===================================================

def build_anim_effects(rigname, fk, ik):
    '''
    Build all animation effects.
    Effects connect to per-FX composeMatrix nodes created
    by build_matrix_offset_network() in rig_tail_matrix.py.
    Each FX writes to its own composeMatrix.

    Arguments
        rigname (str): Name of rig component
        fk (bool): Connect FK anim effects
        ik (bool): Connect IK anim effects
    '''
    logger.debug(f'{rigname}: Build Animation Effects (Matrix-Per-FX)')

    if rigname not in rt_cst.JOINTS_BN:
        logger.warning(f'{rigname}: No BN joints found')
        return

    basectrl = rt_nam.fstr(rigname, rt_cst.BASECTRL)
    joints = rt_cst.JOINTS_BN[rigname]

    loop_time = None
    if rt_cst.EFFECTS['loop']:
        loop_time = build_loop(rigname, basectrl)

    if rt_cst.EFFECTS['wave']:
        build_wave(rigname, basectrl, joints, loop_time)
    if rt_cst.EFFECTS['curl']:
        build_curl(rigname, basectrl, joints)
    if rt_cst.EFFECTS['noise']:
        build_noise(rigname, basectrl, joints, loop_time)

def add_anim_attributes_to_basectrl(rigname, basectrl):
    logger.debug(f'{rigname}: Add animation effect attributes to basectrl')

    if rt_cst.effects_enabled():
        rt_mya.add_attribute_enum(basectrl, rt_cst.ANIM_DIVIDER[0], rt_cst.ANIM_DIVIDER[1], rt_cst.ANIM_DIVIDER[2])

    wave_axes = [('X', 'waveX'), ('Y', 'waveY'), ('Z', 'waveZ')]
    curl_axes = [('X', 'curlX'), ('Y', 'curlY'), ('Z', 'curlZ')]

    if rt_cst.EFFECTS['wave']:
        for axis, attr in wave_axes:
            if not cmds.attributeQuery(attr, n=basectrl, ex=1):
                cmds.addAttr(basectrl, ln=attr, nn=f'Wave {axis}', at='float', k=1, dv=0, min=-10, max=10)
        if not cmds.attributeQuery('wave_frequency', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='wave_frequency', nn='Wave Frequency', at='float', k=1, dv=2, min=0.1, max=5)
        if not cmds.attributeQuery('wave_speed', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='wave_speed', nn='Wave Speed', at='float', k=1, dv=3, min=0, max=10)
        if not cmds.attributeQuery('wave_falloff', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='wave_falloff', nn='Wave Falloff', at='float', k=1, dv=1, min=0.1, max=10)

    if rt_cst.EFFECTS['curl']:
        for axis, attr in curl_axes:
            if not cmds.attributeQuery(attr, n=basectrl, ex=1):
                cmds.addAttr(basectrl, ln=attr, nn=f'Curl {axis}', at='float', k=1, dv=0, min=-10, max=10)
        if not cmds.attributeQuery('curl_falloff', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='curl_falloff', nn='Curl Falloff', at='float', k=1, dv=3.0, min=0.1, max=10)

    if rt_cst.EFFECTS['noise']:
        if not cmds.attributeQuery('noise', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='noise', nn='Noise', at='float', k=1, dv=0, min=-10, max=10)
        if not cmds.attributeQuery('noise_frequency', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='noise_frequency', nn='Noise Frequency', at='float', k=1, dv=1, min=0.1, max=5)
        if not cmds.attributeQuery('noise_speed', n=basectrl, ex=1):
            cmds.addAttr(basectrl, ln='noise_speed', nn='Noise Speed', at='float', k=1, dv=3, min=0, max=10)

    if rt_cst.EFFECTS['loop']:
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

    expr_code = f'''// Loop modulo expression - normalized time output
float $loop_enabled = {basectrl}.loop;
float $loop_len = {basectrl}.loop_frame;
float $t = time1.outTime;
float $two_pi = {TWO_PI};

if ($loop_enabled > 0.5) {{
    float $mod = $t - floor($t / $loop_len) * $loop_len;
    {loop_time}.loop_time = $mod * $two_pi / $loop_len;
}} else {{
    {loop_time}.loop_time = $t * $two_pi / $loop_len;
}}
'''

    delete_expression(modulo_expr)
    cmds.expression(n=modulo_expr, s=expr_code, o='', ae=1, uc='all')
    logger.debug(f'{rigname}: Loop expression built: {modulo_expr}')
    return f'{loop_time}.loop_time'


# WAVE =================================================================

def build_wave(rigname, basectrl, joints, loop_time=None):
    '''
    Build wave animation effect with adjustable falloff.
    Creates one expression per joint per axis that writes directly to composeMatrix.
    ComposeMatrix nodes are pre-created by build_matrix_offset_network().

    Wave = sin(u*frequency + time*speed) * amplitude * (u^falloff)

    Arguments:
        rigname (str): Name of rig component
        basectrl (str): Base control with wave attributes
        joints (list): List of BN joints (joint 00 will be skipped in matrix network)
        loop_time (str): Optional loop time output plug
    '''
    logger.trace(f'{rigname}: Wave Animation Effect')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for wave')
        return

    wave_axes = [('X', 'waveX'), ('Y', 'waveY'), ('Z', 'waveZ')]
    num_joints = len(joints)
    # With no loop node, normalize raw time the same way the loop node does
    # at its default frame (loop off) so wave speed matches a loop-built rig.
    time_source = loop_time if loop_time else UNLOOPED_TIME_SRC
    # The loop attribute only exists when the Loop effect is built; when it
    # is not, reference a literal 0 so the expression still compiles and
    # takes the non-looping branch.
    loop_enabled_src = f'{basectrl}.loop' if loop_time else '0'

    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_nam.get_index_from_name(jnt)
        u = idx / float(num_joints - 1) if num_joints > 1 else 0.0

        for rot_axis, wave_attr in wave_axes:
            expr = f'{rigname}_{NN:02d}_wave{rot_axis}_expression'
            compose_node = f'{rigname}_{NN:02d}_wave_composeMatrix'

            if not cmds.objExists(compose_node):
                logger.trace(f'{compose_node} does not exist, skipping wave expression')
                continue


            expr_code = f'''// Wave expression for joint {NN:02d} axis {rot_axis}
float $loop_enabled = {loop_enabled_src};
float $wave_freq = {basectrl}.wave_frequency;
float $wave_speed = {basectrl}.wave_speed * 0.5;
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
float $amp = {basectrl}.{wave_attr} * 3.0;
float $falloff = {basectrl}.wave_falloff;
float $t = {time_source};
float $u = {u};

float $phase = (6.28318530718 * $u * $freq) + ($t * $speed);
float $val = sin($phase);
float $w = pow($u, $falloff);
float $out = $val * $amp * $w;

{compose_node}.inputRotate{rot_axis} = $out;
'''

            delete_expression(expr)
            cmds.expression(n=expr, s=expr_code, o='', ae=1, uc='all')

    logger.debug(f'{rigname}: Wave effect built')


# CURL =================================================================

def build_curl(rigname, basectrl, joints):
    '''
    Build curl animation effect with adjustable falloff.
    Uses DG node graph per joint for clean connections.
    Connects to curl_composeMatrix nodes pre-created by build_matrix_offset_network().

    Math:
    - u = normalized joint position (0 at base, 1 at tip)
    - weight = u ^ curlFalloff (artist-controllable)
    - output = curl * weight * multiplier

    Arguments:
        rigname (str): Name of rig component
        basectrl (str): Base control with curl attributes
        joints (list): List of BN joints (joint 00 will be skipped)
    '''
    logger.trace(f'{rigname}: Curl Animation Effect')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for curl')
        return

    curl_axes = [('X', 'curlX'), ('Y', 'curlY'), ('Z', 'curlZ')]
    num_joints = len(joints)

    for rot_axis, curl_attr in curl_axes:
        remap = f'{rigname}_curl{rot_axis}_remap_multiplyDivide'
        if not cmds.objExists(remap):
            cmds.createNode('multiplyDivide', n=remap)
            cmds.setAttr(f'{remap}.operation', 1)
            cmds.setAttr(f'{remap}.input2X', 20.0)
        ensure_connect(f'{basectrl}.{curl_attr}', f'{remap}.input1X')

        for i, jnt in enumerate(joints[1:], 1):
            NN = rt_nam.get_index_from_name(jnt)
            compose_node = f'{rigname}_{NN:02d}_curl_composeMatrix'

            u = i / float(num_joints - 1) if num_joints > 1 else 0.0

            falloff_node = f'{rigname}_curl{rot_axis}_{NN:02d}_falloff_multiplyDivide'
            if not cmds.objExists(falloff_node):
                cmds.createNode('multiplyDivide', n=falloff_node)
                cmds.setAttr(f'{falloff_node}.operation', 3)
                cmds.setAttr(f'{falloff_node}.input1X', u)
            ensure_connect(f'{basectrl}.curl_falloff', f'{falloff_node}.input2X')

            weight_scale = f'{rigname}_curl{rot_axis}_{NN:02d}_weight_multiplyDivide'
            if not cmds.objExists(weight_scale):
                cmds.createNode('multiplyDivide', n=weight_scale)
                cmds.setAttr(f'{weight_scale}.operation', 1)
                cmds.connectAttr(f'{falloff_node}.outputX', f'{weight_scale}.input1X', f=1)
                cmds.setAttr(f'{weight_scale}.input2X', 2.0)

            curl_mult = f'{rigname}_curl{rot_axis}_{NN:02d}_multiplyDivide'
            if not cmds.objExists(curl_mult):
                cmds.createNode('multiplyDivide', n=curl_mult)
                cmds.setAttr(f'{curl_mult}.operation', 1)
                cmds.connectAttr(f'{remap}.outputX', f'{curl_mult}.input1X', f=1)
                cmds.connectAttr(f'{weight_scale}.outputX', f'{curl_mult}.input2X', f=1)

            if cmds.objExists(compose_node):
                ensure_connect(f'{curl_mult}.outputX', f'{compose_node}.inputRotate{rot_axis}')
                logger.trace(f'Connected curl{rot_axis} to {compose_node}')

    logger.debug(f'{rigname}: Curl effect built')


# NOISE ================================================================

def build_noise(rigname, basectrl, joints, loop_time=None):
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
    '''
    logger.trace(f'{rigname}: Noise Effect (wavy, loopable)')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for noise')
        return

    effect_axes = ['X', 'Y', 'Z']
    axis_offsets = {'X': 0.0, 'Y': 100.0, 'Z': 200.0}
    num_joints = len(joints)
    # With no loop node, normalize raw time the same way the loop node does
    # at its default frame (loop off) so noise speed matches a loop-built rig.
    time_source = loop_time if loop_time else UNLOOPED_TIME_SRC
    # The loop attribute only exists when the Loop effect is built; when it
    # is not, reference a literal 0 so the expression still compiles and
    # takes the non-looping branch.
    loop_enabled_src = f'{basectrl}.loop' if loop_time else '0'

    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_nam.get_index_from_name(jnt)
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
float $amp = {basectrl}.noise;
float $noise_freq = {basectrl}.noise_frequency;
float $noise_speed = {basectrl}.noise_speed * 0.2;
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
            delete_expression(expr)
            cmds.expression(n=expr, s=expr_code, o='', ae=1, uc='all')

    logger.debug(f'{rigname}: Noise effect built')
