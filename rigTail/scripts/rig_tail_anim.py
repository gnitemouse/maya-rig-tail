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
and never touch the joints' channels. Wave and noise are expressions
(they need time); curl is a pure node network. Attribute sources go
through rt_ctrlall.resolved_plug so the Main Controller dashboard can route
them, and expressions are deleted via delete_expression - a raw delete
on a connected expression cascades through its connection web.

An L/R pair's FX obey MIRROR_BEHAVIOR on all three axes: fx_mirror_signs
measures how the two chains' joint frames actually relate and negates the
axes that do not already do what the behavior asked for. See MIRROR_FX
for why a mirrored skeleton alone can only ever manage one of them.

Functions:
    delete_expression: remove an expression without the delete cascading
    build_anim_effects: entry point; build the enabled FX for one part
    add_anim_attributes_to_basectrl: the animatable FX attrs (gated
        per enabled effect; mirrored by rt_ctrlall.routed_attr_specs)
    fx_mirror_signs: per-axis sign making a part's FX mirror its L/R partner
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
# The curl attributes run -10..10, so 72 puts TWO full turns at each end of
# the slider. Curl names the whole chain's wrap, not a per-joint angle
# (see build_curl), which is what makes a 12-joint and an 80-joint tail curl
# by the same amount.
CURL_DEGREES_PER_UNIT = 72.0
# Ceiling on the bend any SINGLE joint takes, whatever total the curl value
# and the falloff ask for. This is what keeps the tip inside the coil rather
# than folding out of it: the total above is shared out by the falloff
# profile, which peaks at the tip, and a chain with few joints cannot carry
# two turns without giving that last joint an absurd angle. The guard caps
# it, so a sparse chain simply stops tightening near the top of the slider
# while a dense one reaches the full wrap. Raising the total therefore only
# spends where the joint count can carry it. See build_curl.
CURL_MAX_JOINT_DEGREES = 90.0
# Make an L/R pair's FX obey MIRROR_BEHAVIOR on all three axes: 'symmetric'
# moves the pair as mirror images, 'parallel' moves it the same way round
# the world.
#
# WHY THIS IS NEEDED. A mirrored skeleton cannot do it on its own. The FX
# rotate each joint about its own local axes, and 'the same channel value
# moves the two sides as mirror images' holds for a local axis only when the
# target's copy of it points OPPOSITE the reflection of the source's. A
# reflection flips handedness, so a joint frame can satisfy that on an ODD
# number of its three axes - one or all three, never two. All three means
# negating the aim as well, which points it back UP the chain (this is what
# Maya's mirrorJoint -mirrorBehavior does, and why a mirrored arm's local X
# runs backwards); the spline IK, the advanced twist and the stretch all
# read the aim as running down the chain, and Setup's own Orient step
# re-derives it from the joint positions, so that is not available here.
# With the aim pinned, exactly ONE axis is left free to mirror, and
# MIRROR_BEHAVIOR only chooses which: 'symmetric' the up axis
# (ORIENT_UP_AXIS), 'parallel' the third one. Whichever is not chosen drives
# both sides the same way round the world, which reads as the pair moving
# oppositely - and no orientation scheme, in any software, escapes that.
#
# So the fix cannot live in the joint orientation; it lives here, as a sign
# on the value going in, where the parity argument has no hold. Sliders can
# do what the frames cannot. fx_mirror_signs measures the two chains rather
# than assuming a mirror was run, and negates only the axes that do not
# already do what MIRROR_BEHAVIOR asked for, on one side of the pair only
# (the side that is not MIRROR_SOURCE_SIDE).
#
# Off leaves every part's FX driving its own axes raw, which is how builds
# before this behaved: mirrored on one axis, same-way-round on the other two.
MIRROR_FX = True
# Below this |cos| between a target axis and the reflection of its partner's,
# the two chains are not mirror images on that axis and no sign can make them
# read as one; fx_mirror_signs leaves it alone and says so.
MIRROR_FX_TOLERANCE = 0.5


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
    rt_maya.remove(expr)
    for conv in convs:
        if cmds.objExists(conv):
            rt_maya.remove(conv)

ensure_connect = rt_maya.ensure_connect


# BUILD ANIM EFFECTS ===================================================

def build_anim_effects(rigname, fk, ik):
    '''
    Build all animation effects.
    Effects connect to per-FX composeMatrix nodes created
    by build_matrix_offset_network() in rig_tail_matrix.py.
    Each FX writes to its own composeMatrix.

    The mirror signs are measured once here and handed to every effect, so
    all three read the pair the same way (see fx_mirror_signs).

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
    signs = fx_mirror_signs(rigname)

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


# MIRROR ===============================================================

NO_MIRROR = {'X': 1.0, 'Y': 1.0, 'Z': 1.0}


def fx_mirror_signs(rigname):
    '''
    Per-axis sign that makes this part's FX agree with MIRROR_BEHAVIOR on
    ALL THREE axes. See MIRROR_FX for why the joint orientation cannot get
    past one of them on its own.

        'symmetric' - every axis moves the pair as mirror images
        'parallel'  - every axis moves the pair the same way round the world

    The behavior is the animator's stated convention, so the FX honour it
    outright rather than always mirroring: choosing 'parallel' on a splayed
    pair - one tail curling up while the other curls down - is a choice, and
    forcing the sliders to mirror would quietly overrule it.

    Only the target side of a pair is signed - the side that is NOT
    rt_constants.MIRROR_SOURCE_SIDE - so exactly one of the two moves and
    the source keeps driving its own axes raw. A center part, an unpaired
    part, and the source side all come back unsigned.

    What each axis does NOW is MEASURED, never assumed: the target's copy of
    it is compared against the reflection of the source's across the
    symmetry plane, over the whole chain. Anti-parallel means that axis
    already mirrors; parallel means it drives both sides the same way round
    the world. Whichever it is, it takes -1 when that disagrees with the
    behavior and +1 when it already agrees. The test only reads the SIGN of
    a dot product, so it survives the small orientation differences of two
    hand-placed chains - and a pair that is not a mirror on some axis (|cos|
    under MIRROR_FX_TOLERANCE) is left alone with a warning, since no sign
    would make those two read as one motion.

    Measuring the frames while taking the GOAL from MIRROR_BEHAVIOR is what
    makes this right for chains that were oriented by hand or rolled with
    the Setup UI's Roll Chain and never went through mirror_frames at all:
    however those two chains ended up standing, the sliders land on the
    convention the behavior names.

    Rotations only. A translation along a local axis mirrors under the
    OPPOSITE rule (its axis must point along the reflection, not against
    it), so anything sliding a joint - FK offset - needs the inverse of
    these signs, not these.

    Arguments
        rigname (str): Name of rig component

    Return
        dict: {'X': sign, 'Y': sign, 'Z': sign}, each +1.0 or -1.0
    '''
    if not MIRROR_FX:
        return dict(NO_MIRROR)

    partner = _mirror_partner(rigname)
    if not partner:
        return dict(NO_MIRROR)

    src = rt_constants.JOINTS_BN.get(partner) or []
    tgt = rt_constants.JOINTS_BN.get(rigname) or []
    if not src or not tgt:
        # Only reachable when the partner was never detected this session
        # (excluded from every run since Maya started). Worth saying out
        # loud: this side builds unsigned while the partner keeps whatever
        # signs its own build gave it, so the pair stops matching.
        logger.warning(f'{rigname}: FX mirror: no BN chain for {partner}, '
                       'building the FX unsigned - run Setup so both sides '
                       'of the pair are detected')
        return dict(NO_MIRROR)

    keep = {'x': 0, 'y': 1, 'z': 2}.get(
        str(getattr(rt_constants, 'MIRROR_AXIS', 'x')).lower(), 0)

    # Mean cos between each target axis and the reflection of the source's.
    # Averaged over the chain rather than read off one joint: a single joint
    # can sit oddly (a hand-tweaked tip, an un-oriented base) without that
    # being what the chain as a whole does.
    cosines = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    # Only the joints both chains actually have, and only those still in the
    # scene: a stale JOINTS_BN entry naming a deleted joint must not take the
    # build down over a sign
    pairs = [(s, t) for s, t in zip(src, tgt)
             if cmds.objExists(s) and cmds.objExists(t)]
    if not pairs:
        logger.warning(f'{rigname}: FX mirror: no joints in common with '
                       f'{partner}, building the FX unsigned')
        return dict(NO_MIRROR)
    for src_jnt, tgt_jnt in pairs:
        src_rows = _axis_rows(src_jnt)
        tgt_rows = _axis_rows(tgt_jnt)
        for row, axis in enumerate('XYZ'):
            reflected = [(-v if i == keep else v)
                         for i, v in enumerate(src_rows[row])]
            cosines[axis] += sum(reflected[i] * tgt_rows[row][i]
                                 for i in range(3))
    for axis in cosines:
        cosines[axis] /= len(pairs)

    want_mirror = _mirror_behavior() == 'symmetric'
    signs = dict(NO_MIRROR)
    for axis, cos in cosines.items():
        if abs(cos) < MIRROR_FX_TOLERANCE:
            logger.warning(f'{rigname}: FX mirror: the {axis} axis is not a '
                           f'mirror of {partner} (cos {cos:+.2f}), leaving it '
                           'unsigned - re-run Setup Mirror Orient on the pair')
            continue
        # An axis anti-parallel to the reflection (cos < 0) mirrors as it
        # stands; negate the ones that do not already do what was asked for
        signs[axis] = 1.0 if (cos < 0) == want_mirror else -1.0

    flipped = ''.join(a for a in 'XYZ' if signs[a] < 0)
    logger.debug(f'{rigname}: FX mirror of {partner} '
                 f'({_mirror_behavior()}): '
                 f'{"negating " + flipped if flipped else "nothing to negate"}')
    return signs


def _mirror_behavior():
    '''
    The mirror convention the FX should land on, 'symmetric' or 'parallel'.

    Same validation and fallback as rig_tail_setup's own reader, kept here
    rather than imported: that one is private to the Setup phase, and the
    FX need the answer on every build whether Setup ran this session or not.

    Return
        str: 'symmetric' or 'parallel'
    '''
    value = str(getattr(rt_constants, 'MIRROR_BEHAVIOR', 'symmetric'))
    value = value.strip().lower()
    if value not in ('symmetric', 'parallel'):
        logger.warning(f"FX mirror: unknown MIRROR_BEHAVIOR '{value}', "
                       "using 'symmetric'")
        return 'symmetric'
    return value


def _mirror_partner(rigname):
    '''
    The rig part this one should mirror, or None when it should not.

    Only the target side of an L/R pair gets a partner, so exactly one side
    of the pair is ever signed (see fx_mirror_signs). Pairing is
    rig_tail_setup's find_mirror_pairs, so the FX agree with the Setup
    phase's idea of what pairs with what.

    Arguments
        rigname (str): Name of rig component

    Return
        str or None: the source-side partner, or None
    '''
    # Deferred: rig_tail_setup is the Setup phase's module and pulls in the
    # cleanup and cache modules with it; the FX only need one function of it
    import rig_tail_setup as rt_setup
    try:
        pairs, _ = rt_setup.find_mirror_pairs(rt_constants.RIGPARTS)
    except Exception as err:
        logger.warning(f'{rigname}: FX mirror: cannot pair rig parts ({err})')
        return None
    for source, target in pairs:
        if target == rigname:
            return source
    return None


def _axis_rows(node):
    '''
    A node's three local axes (X/Y/Z) as unit world vectors.

    Normalized so the cosines fx_mirror_signs sums are comparable between
    joints: a joint carrying scale (the squash network drives BN scaleY/Z)
    would otherwise weigh more than its neighbours.
    '''
    m = cmds.xform(node, q=True, ws=True, matrix=True)
    rows = ([m[0], m[1], m[2]],
            [m[4], m[5], m[6]],
            [m[8], m[9], m[10]])
    unit = []
    for row in rows:
        length = math.sqrt(sum(v * v for v in row))
        unit.append([v / length for v in row] if length > 1e-9 else row)
    return unit


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

    delete_expression(modulo_expr)
    cmds.expression(n=modulo_expr, s=expr_code, o='', ae=1, uc='all')
    logger.debug(f'{rigname}: Loop expression built: {modulo_expr}')
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
        signs (dict): Per-axis mirror signs from fx_mirror_signs; None
            leaves every axis driving its own direction raw
    '''
    logger.trace(f'{rigname}: Wave Animation Effect')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for wave')
        return

    signs = signs or NO_MIRROR
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

    for idx, jnt in enumerate(joints[1:], 1):
        NN = rt_naming.get_index_from_name(jnt)
        u = idx / float(num_joints - 1) if num_joints > 1 else 0.0

        for rot_axis, wave_attr in wave_axes:
            expr = f'{rigname}_{NN:02d}_wave{rot_axis}_expression'
            compose_node = f'{rigname}_{NN:02d}_wave_composeMatrix'

            if not cmds.objExists(compose_node):
                logger.trace(f'{compose_node} does not exist, skipping wave expression')
                continue

            amp_src = rt_ctrlall.resolved_plug(rigname, wave_attr)

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

            delete_expression(expr)
            cmds.expression(n=expr, s=expr_code, o='', ae=1, uc='all')

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
        signs (dict): Per-axis mirror signs from fx_mirror_signs; None
            leaves every axis curling its own direction raw
    '''
    logger.trace(f'{rigname}: Curl Animation Effect')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for curl')
        return

    signs = signs or NO_MIRROR
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
      to the same numbers; the mirror signs (fx_mirror_signs) are what turn
      that into the two sides jittering as mirror images

    Arguments:
        rigname (str): Name of rig component
        basectrl (str): Base control with noise attributes
        joints (list): List of BN joints (joint 00 will be skipped)
        loop_time (str): Optional loop time output plug
        signs (dict): Per-axis mirror signs from fx_mirror_signs; None
            leaves every axis jittering its own direction raw
    '''
    logger.trace(f'{rigname}: Noise Effect (wavy, loopable)')
    if not joints or len(joints) < 2:
        logger.warning('Not enough joints for noise')
        return

    signs = signs or NO_MIRROR
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
            delete_expression(expr)
            cmds.expression(n=expr, s=expr_code, o='', ae=1, uc='all')

    logger.debug(f'{rigname}: Noise effect built')
