'''
rig_tail_mirror.py
author: Daisy Jane @gnitemouse

Makes an L/R pair's DIALS agree with MIRROR_BEHAVIOR on all three axes.

    'mirror', 'symmetric' - the same value moves the pair as mirror images
    'parallel'            - the same value moves the pair the same way
                            round the world

THREE OF SIX. A frame has six things it could mirror: a rotation about
each of its three axes, and a translation along each. It is always
exactly three, and the behavior only picks which:

    'mirror'     -aim -roll -up   rotations: all three  translations: none
    'symmetric'  +aim +roll -up   rotations: up         translations: aim, roll
    'parallel'   +aim -roll +up   rotations: roll       translations: aim, up

Read each axis as '+' when it points the SAME way as the mirror image of
its partner's matching axis and '-' when it points the OPPOSITE way. aim
runs down the chain (ORIENT_AIM_AXIS), up is ORIENT_UP_AXIS, roll is the
remaining one. Rotations mirror on the '-' axes, translations on the '+'
ones, and the count of '-' must be odd, which is where the three comes
from. 'mirror' reverses the aim, so it alone runs BACKWARDS down the
chain - aim_reversed reports it.

So the choice follows what a thing is POSED BY. Everything with a gizmo in
this rig is posed by rotation - the FK controls turn their joints, the
spline mid_rot control swings the top of the chain - and curl, wave,
noise, twist and roll are rotations too. 'mirror' is the default because
it spends its three there. The one dial it costs is offset, a slide along
the aim, and a sign covers that.

WHY THIS EXISTS. A mirrored skeleton cannot deliver either one on its own.
The dials rotate joints about their own local axes, and 'the same value
moves the two sides as mirror images' holds for a local axis only when the
target's copy of it points OPPOSITE the reflection of the source's. Write
the target frame as T = R * S * D, with R the reflection, S the source
frame and D a diagonal of +/-1 marking which axes were negated:

    det(T) = det(R) * det(S) * det(D) = -det(D)

T has to stay right-handed, so det(D) = -1, so an ODD number of axes are
negated. One axis, or all three. NEVER TWO - in any rig, in any software.

All three negated is 'mirror', Maya's mirrorJoint -mirrorBehavior, and is
why a mirrored arm's local X runs backwards. One negated leaves the aim
running down the chain, and MIRROR_BEHAVIOR picks which of the other two
takes it: 'symmetric' the up, 'parallel' the roll.

Nothing here is free either way, which is the point of the table above.
So the sign does not live in the joint orientation. It lives on the way
IN, on the dial value, where the parity argument has no hold at all: a
scalar can be negated freely. That is all this module does - it measures
how a pair's chains actually stand and reports which axes need negating to
land on the convention MIRROR_BEHAVIOR names. Under 'mirror' the rotations
need nothing and offset needs a sign; under 'symmetric' it is the other
way about.

ROTATIONS AND TRANSLATIONS TAKE OPPOSITE SIGNS. A rotation about a local
axis mirrors when that axis points AGAINST the reflection; a translation
along one mirrors when it points ALONG it. So a dial that slides a joint
(FK offset, the IK spline handle's offset) needs translation_signs, not
rotation_signs.

Consumers: rig_tail_anim (curl, wave, noise), rig_tail_fk (twist, roll,
offset), rig_tail_connect (the IK spline handle's twist/roll/offset) and
rig_tail_stretch, which needs aim_reversed to tell the advanced twist
which way down the chain the mirrored side runs.

CONTROLS ARE A SECOND CASE. A dial is a number, so a sign on it is free.
A control is a gizmo, so its axes must point where its motion goes - which
pins the control frame to the joint frame scaled by these same signs, and
that frame has to stay right-handed. See control_signs. Under 'mirror' it
needs nothing: the joints are already the full behavior mirror, so a
control matched to its joint mirrors on all three rotations by itself.

Functions:
    rotation_signs: per-axis sign for a rotation about each local axis
    translation_signs: the same for a translation along each local axis
    control_signs: signs for a behavior-mirrored control, or None
    spline_up_vector: in-plane world up the spline control rows roll to
    flip_control_aim: whether a part's spline rows aim back up the row
    mirrored_matrix: a node's world frame with each axis scaled by its sign
    aim_axis: the chain's aim axis as a signs key ('X'/'Y'/'Z')
    aim_reversed: whether a part's aim runs back up its own chain
    behavior: the validated MIRROR_BEHAVIOR
'''

import math
import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_restpose as rt_rest

logger = logger_setup(__name__)

# Sign every dial so an L/R pair obeys MIRROR_BEHAVIOR on all three axes.
# Off leaves every part driving its own axes raw, which is how builds before
# this behaved: mirrored on one axis, opposite on the other two.
MIRROR_SLIDERS = True
# Stand the mirrored side's FK CONTROLS in the behavior-mirrored frame, so
# the same channel values pose an L/R pair as mirror images on all three
# axes (and a mirror-pose tool is a straight value copy). Only buildable
# under 'symmetric' - see control_signs. Off leaves the controls facing
# their own joints, which is how builds before this behaved.
MIRROR_CONTROLS = True
# Below this |cos| between a target axis and the reflection of its partner's,
# the two chains are not mirror images on that axis and no sign can make them
# read as one motion; the axis is left alone and said so.
MIRROR_TOLERANCE = 0.5

# Every axis raw. Returned whole, so callers may not mutate it.
NO_MIRROR = {'X': 1.0, 'Y': 1.0, 'Z': 1.0}

# The MIRROR_BEHAVIOR values, and the one a build with nothing stored takes.
# Mirroring is a three-of-six proposition - see the module docstring - and
# 'mirror' spends its three on the rotations, which is what every gizmo in
# this rig is posed by.
BEHAVIORS = ('mirror', 'symmetric', 'parallel')
BEHAVIOR_DEFAULT = 'mirror'


def rotation_signs(rigname):
    '''
    Per-axis sign that makes a ROTATION about each of this part's local
    axes agree with MIRROR_BEHAVIOR against its L/R partner.

    Only the target side of a pair is signed - the side that is NOT
    rt_constants.MIRROR_SOURCE_SIDE - so exactly one of the two moves and
    the authored source keeps driving its own axes raw. A center part, an
    unpaired part and the source side all come back unsigned.

    What each axis does NOW is MEASURED, never assumed: the target's copy
    of it is compared against the reflection of the source's across the
    symmetry plane, over the whole chain. Anti-parallel means that axis
    already mirrors; parallel means it drives both sides the same way round
    the world. Either way it takes -1 when that disagrees with the behavior
    and +1 when it already agrees. Only the SIGN of a dot product is read,
    so the answer survives the small orientation differences of two
    hand-placed chains - and a pair that is not a mirror on some axis (|cos|
    under MIRROR_TOLERANCE) is left alone with a warning, since no sign
    would make those two read as one motion.

    Measuring the frames while taking the GOAL from MIRROR_BEHAVIOR is what
    makes this right for chains that were oriented by hand or rolled with
    the Setup UI's Roll Chain and never went through mirror_frames at all:
    however those two chains ended up standing, the dials land on the
    convention the behavior names.

    Deliberately uncached. It is a handful of xform reads per rig part and
    it is asked for two or three times per build; a cache would have to be
    invalidated whenever Setup re-orients a chain, which is a bug waiting
    for the one session that re-runs Setup and rebuilds without a restart.

    Arguments
        rigname (str): Name of rig component

    Return
        dict: {'X': sign, 'Y': sign, 'Z': sign}, each +1.0 or -1.0
    '''
    return _signs(rigname, rotation=True)


def _axis_cosines(rigname):
    """
    Mean cos between each of this part's local axes and the reflection of
    its L/R partner's matching axis, over the whole chain.

    The one measurement every answer in this module is built on: -1 means
    the axis points OPPOSITE the reflection ('-' in the +aim/+roll/+up
    notation), +1 means it points the same way ('+'). Averaged over the
    chain rather than read off one joint, since a single joint can sit
    oddly - a hand-tweaked tip, an un-oriented base - without that being
    what the chain as a whole does.

    Arguments
        rigname (str): Name of rig component

    Return
        dict or None: {'X','Y','Z'} mean cosines, or None when this part
            has no partner to be measured against
    """
    partner = rt_naming.mirror_partner(rigname)
    if not partner:
        return None

    src = rt_constants.JOINTS_BN.get(partner) or []
    tgt = rt_constants.JOINTS_BN.get(rigname) or []
    if not src or not tgt:
        # Only reachable when the partner was never detected this session
        # (excluded from every run since Maya started). Worth saying out
        # loud: this side builds unsigned while the partner keeps whatever
        # its own build gave it, so the pair stops matching.
        logger.warning(f'{rigname}: Mirror: no BN chain for {partner}, '
                       'leaving the dials unsigned - run Setup so both '
                       'sides of the pair are detected')
        return None

    keep = {'x': 0, 'y': 1, 'z': 2}.get(
        str(getattr(rt_constants, 'MIRROR_AXIS', 'x')).lower(), 0)
    cosines = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    # Only the joints both chains actually have, and only those still in the
    # scene: a stale JOINTS_BN entry naming a deleted joint must not take
    # the build down over a sign
    pairs = [(a, b) for a, b in zip(src, tgt)
             if cmds.objExists(a) and cmds.objExists(b)]
    if not pairs:
        logger.warning(f'{rigname}: Mirror: no joints in common with '
                       f'{partner}, leaving the dials unsigned')
        return None
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
    return cosines


def _signs(rigname, rotation):
    """
    The sign rule behind rotation_signs and translation_signs.

    One body for both so the 'nothing to mirror' answers cannot diverge:
    an unpaired part, a center part and the source side must come back
    UNSIGNED for a translation exactly as they do for a rotation. Deriving
    one from the other by negating the whole dict got that wrong - it
    turned every unmirrored part's translation signs to -1 and reversed FK
    and IK offset on the source side and on every center tail. Callers must
    ask for the motion they mean rather than negating what they got.

    Arguments
        rigname (str): Name of rig component
        rotation (bool): True for a rotation about each axis, False for a
            translation along it (the opposite mirror rule)

    Return
        dict: {'X': sign, 'Y': sign, 'Z': sign}, each +1.0 or -1.0
    """
    if not MIRROR_SLIDERS:
        return dict(NO_MIRROR)
    cosines = _axis_cosines(rigname)
    if cosines is None:
        return dict(NO_MIRROR)
    partner = rt_naming.mirror_partner(rigname)

    # 'mirror' and 'symmetric' both ask the pair to move as mirror images;
    # they differ in WHICH axes manage it unaided, which the measurement
    # below discovers. Only 'parallel' asks for the opposite.
    want_mirror = behavior() != 'parallel'
    signs = dict(NO_MIRROR)
    for axis, cos in cosines.items():
        if abs(cos) < MIRROR_TOLERANCE:
            logger.warning(f'{rigname}: Mirror: the {axis} axis is not a '
                           f'mirror of {partner} (cos {cos:+.2f}), leaving '
                           'it unsigned - re-run Setup Mirror Orient on the '
                           'pair')
            continue
        # A rotation about an axis anti-parallel to the reflection (cos < 0)
        # mirrors as it stands; a translation along it mirrors when the axis
        # points the OTHER way (cos > 0). Negate whichever does not already
        # do what the behavior asked for.
        mirrors_now = (cos < 0) if rotation else (cos > 0)
        signs[axis] = 1.0 if mirrors_now == want_mirror else -1.0

    flipped = ''.join(a for a in 'XYZ' if signs[a] < 0)
    kind = 'rotation' if rotation else 'translation'
    logger.debug(f'{rigname}: Mirror of {partner} ({behavior()}, {kind}): '
                 f'{"negating " + flipped if flipped else "nothing to negate"}')
    return signs


def translation_signs(rigname):
    '''
    Per-axis sign for a TRANSLATION along each of this part's local axes -
    the exact negative of rotation_signs.

    A rotation about a local axis mirrors when that axis points AGAINST the
    reflection of its partner's; a translation along one mirrors when it
    points ALONG it. So the two rules are opposites, and a dial that slides
    a joint rather than turning it needs these signs.

    NOT the negation of rotation_signs, though it looks like one on a
    mirrored part. A part with nothing to mirror - unpaired, center, or the
    source side of a pair - must come back UNSIGNED for both, and negating
    the dict turned all of those to -1: it reversed FK and IK offset on the
    source side and on every center tail, which is the one place the sign
    had no business being.

    Arguments
        rigname (str): Name of rig component

    Return
        dict: {'X': sign, 'Y': sign, 'Z': sign}, each +1.0 or -1.0
    '''
    return _signs(rigname, rotation=False)


def control_signs(rigname):
    '''
    Signs for a behavior-mirrored CONTROL, or None when there is no such
    control frame to build.

    A control is posed by dragging a gizmo, so it has a requirement the
    dials do not: the gizmo and the motion must agree. The joints turn
    about sign_k * J_k (J the joint frame), so the control's own axis k has
    to point along sign_k * J_k too - which makes the control frame the
    joint frame with each axis scaled by its sign. Being a transform, that
    frame has to stay right-handed, so the three signs must multiply to +1:

        'mirror'    -aim -roll -up  negates none: already it  -> None
        'symmetric' +aim +roll -up  negates two (product +1)  -> buildable
        'parallel'  +aim -roll +up  negates one (product -1)  -> refused

    So behavior-mirrored controls are BUILT under 'symmetric', already
    correct under 'mirror', and impossible under 'parallel'. The last is
    not an omission: 'parallel' asks all three axes to move the pair the
    same way round the world, and a right-handed frame can only ever do
    that on two of them. The dials still honour it - a sign on a scalar is
    bound by nothing - but a gizmo cannot.

    The 'symmetric' frame this yields is the full behavior mirror (every
    axis the negated reflection of its partner's) - which is precisely the
    frame the JOINTS already stand in under 'mirror', hence nothing to do
    there. Either way the same channel values give mirrored poses on every
    axis, and mirror-pose tools reduce to a straight value copy.

    Arguments
        rigname (str): Name of rig component

    Return
        dict or None: {'X','Y','Z'} signs, or None when the part takes no
            control mirror (unpaired, source side, MIRROR_CONTROLS off, or
            a behavior with no right-handed control frame)
    '''
    if not MIRROR_CONTROLS:
        return None
    signs = rotation_signs(rigname)
    if signs['X'] * signs['Y'] * signs['Z'] < 0:
        logger.debug(f'{rigname}: Mirror: no right-handed control frame '
                     f"under '{behavior()}', leaving the controls as built")
        return None
    if all(sign > 0 for sign in signs.values()):
        return None      # nothing to mirror: source side, or unpaired
    return signs


def mirrored_matrix(node, signs):
    '''
    A node's world matrix with each axis scaled by its sign - the frame a
    behavior-mirrored control stands in, over the joint it belongs to.

    Position is untouched: the control still sits on its joint, it only
    faces the other way about. Returned as the flat 16 floats cmds.xform
    takes, so the caller writes it absolutely rather than rotating by a
    relative amount - which is what makes re-running the build idempotent,
    and what keeps a NESTED control stack (INDIV_FK) from compounding its
    parents' flips into itself.

    Arguments
        node (str): Node whose world frame is the starting point
        signs (dict): Per-axis signs from control_signs

    Return
        list: 16 floats, row-major, ready for cmds.xform(ws=True, m=...)
    '''
    m = list(cmds.xform(node, q=True, ws=True, matrix=True))
    for row, axis in enumerate('XYZ'):
        sign = signs[axis]
        for col in range(3):
            m[row * 4 + col] *= sign
    return m


def spline_up_vector():
    '''
    World up vector for the spline controls' aim-orient, guaranteed to lie
    IN the symmetry plane.

    The IK, Float and Spline control rows are aimed at each other with a
    world up reference (rig_tail_control.orient_aim_controls_nulls). They
    used to take it from the basectrl's Z, which reaches them from the
    JOINTS through get_local_orientation - so the two sides' control frames
    were a by-product of MIRROR_BEHAVIOR rather than a convention, and a
    stated one is cheaper to reason about than a derived one. This is that
    convention: ORIENT_UP_AXIS read as a world direction.

    IN THE PLANE is the part that matters, though not for the reason it
    first looks. A world axis is always invariant under the reflection up
    to SIGN - unchanged when it lies in the plane, negated when it is the
    plane's own normal - so either way the two sides come out cleanly
    related; it is a reference lying at some general angle that would
    relate them by nothing at all, and an axis constant cannot be one.
    What the plane normal costs is WHICH axis carries the negation: the up
    resolves negated as well, and with the aim reversed on top of it
    (flip_control_aim) all three end up negated - the one frame with no
    mirrored translation at all, which is the worst possible answer for a
    control posed by dragging. So it is refused here, and flip_control_aim
    stands down with it.

    Return
        list or None: unit world vector, or None when no axis is usable
            (the caller then keeps the old basectrl-derived reference)
    '''
    axes = {'x': [1.0, 0.0, 0.0], 'y': [0.0, 1.0, 0.0], 'z': [0.0, 0.0, 1.0]}
    up = str(getattr(rt_constants, 'ORIENT_UP_AXIS', 'z')).strip().lower()
    plane_normal = str(getattr(rt_constants, 'MIRROR_AXIS', 'x')).strip().lower()
    if up not in axes:
        logger.warning(f"Mirror: unusable ORIENT_UP_AXIS '{up}' for the "
                       'spline control up reference')
        return None
    if up == plane_normal:
        logger.warning(f"Mirror: ORIENT_UP_AXIS '{up}' is the symmetry "
                       f"plane's own normal (MIRROR_AXIS '{plane_normal}'), "
                       'so it cannot be the spline control up reference - '
                       'the two sides would not resolve against the same '
                       'vector. Falling back to the base control.')
        return None
    return axes[up]


def flip_control_aim(rigname):
    '''
    Whether this part's spline controls should aim BACKWARDS up their row.

    The aim-oriented control sets (IK, Float, Spline) resolve their up
    against a vector lying in the symmetry plane, which leaves both sides'
    aim and up pointing along the reflection of their partner's - so the
    third axis carries the negation, and it is the two axes an animator
    drags ACROSS the tail that come out unmirrored.

    Reversing the aim on the mirrored side moves the negation onto the aim
    instead: rotations then mirror about the aim alone, and translations
    mirror along BOTH axes that bend the curve. It costs the slide along
    the tail's own length, which is the least likely of the three to be
    typed rather than dragged.

    Done by negating the constraint's aim vector rather than re-standing
    the frame afterwards, so the orientation is built right the first time
    and a rebuild cannot flip the flip.

    Stands down when there is no in-plane up reference to build on
    (spline_up_vector). Without one the up resolves negated too, and
    reversing the aim on top of that negates all three - the one frame
    where no translation mirrors, which is worse than leaving it alone.

    Arguments
        rigname (str): Name of rig component

    Return
        bool: True on the mirrored side of a pair, when MIRROR_CONTROLS is on
    '''
    return (MIRROR_CONTROLS
            and spline_up_vector() is not None
            and bool(rt_naming.mirror_partner(rigname)))


def aim_axis():
    '''
    The chain's aim axis as a key into the signs dicts ('X', 'Y' or 'Z').

    ORIENT_AIM_AXIS is stored lower case; the signs are keyed upper. None
    when the setting is not a recognized axis, which is the caller's cue to
    skip signing rather than guess.

    Return
        str or None: 'X' | 'Y' | 'Z', or None when the setting is unusable
    '''
    axis = str(getattr(rt_constants, 'ORIENT_AIM_AXIS', 'x')).strip().lower()
    return axis.upper() if axis in ('x', 'y', 'z') else None


def behavior():
    '''
    The mirror convention the dials should land on.

    Same validation and fallback as rig_tail_setup's own reader, kept here
    rather than imported: that one is private to the Setup phase, and
    rig_tail_setup.py is an optional install the build cannot depend on.

    Return
        str: 'mirror', 'symmetric' or 'parallel'
    '''
    value = str(getattr(rt_constants, 'MIRROR_BEHAVIOR', BEHAVIOR_DEFAULT))
    value = value.strip().lower()
    if value not in BEHAVIORS:
        logger.warning(f"Mirror: unknown MIRROR_BEHAVIOR '{value}', "
                       f"using '{BEHAVIOR_DEFAULT}'")
        return BEHAVIOR_DEFAULT
    return value


def aim_reversed(rigname):
    '''
    Whether this part's joints aim back UP their own chain.

    MEASURED off the skeleton, not read from MIRROR_BEHAVIOR. The setting
    says what the next Mirror Orient WILL do; this asks what the joints
    standing in the scene actually are, and only the second is safe to
    build against. Reading the setting shipped the bug this replaces: Setup
    was run with 'mirror' from the UI, the Build reloaded a config that
    still held 'symmetric', and the spline solver was told the aim ran
    forwards on a chain where it ran backwards - which twists the whole
    chain along its length. Every other answer in this module is measured;
    this one had no business being the exception.

    Anything reading the aim as running from parent to child has to ask:
    the spline IK's advanced twist is told through its forward axis
    (rig_tail_stretch), and a translation along the aim reverses with it
    (which translation_signs already reports).

    Only the mirrored side of a pair can answer True - the source side is
    the authored one and is never reversed.

    Arguments
        rigname (str): Name of rig component

    Return
        bool: True when the aim runs from child to parent on this part
    '''
    cosines = _axis_cosines(rigname)
    axis = aim_axis()
    if cosines is None or axis is None:
        return False
    reversed_aim = cosines[axis] < -MIRROR_TOLERANCE
    # Worth saying when the skeleton and the setting disagree: it means a
    # Mirror Orient re-run would change the convention under the rig
    if reversed_aim != (behavior() == 'mirror'):
        logger.info(f'{rigname}: Mirror: the skeleton is'
                    f'{"" if reversed_aim else " not"} aim-reversed, but '
                    f"MIRROR_BEHAVIOR says '{behavior()}'. Building for the "
                    'skeleton; re-run Setup Mirror Orient to move it.')
    return reversed_aim


def _axis_rows(node):
    '''
    A node's three local axes (X/Y/Z) as unit world vectors, taken from its
    stored REST pose when it has one.

    Rest, not live, because this is asked DURING the build and the answer
    must not depend on where in the build it is asked. rig_tail_matrix
    zeroes every BN joint - offsetParentMatrix to identity, translate,
    rotate and jointOrient to zero - and rebuilds the network under it, and
    the FX are wired immediately afterwards. Reading a live world matrix
    there caught the chains mid-rebuild and mid-evaluation: the twist, roll
    and control signs, measured earlier in the same build, came out right
    while curl, wave and noise measured two chains that no longer looked
    like mirrors and were left unsigned. The stored rest matrix is captured
    once at build start, before anything moves, so every consumer now sees
    the same frames whenever it asks.

    Live is still the fallback, for a chain with nothing captured yet.

    Normalized so the cosines rotation_signs sums are comparable between
    joints: a joint carrying scale (the squash network drives BN scaleY/Z)
    would otherwise weigh more than its neighbours.
    '''
    m = None
    if cmds.attributeQuery(rt_rest.REST_ATTR, node=node, exists=True):
        m = cmds.getAttr(f'{node}.{rt_rest.REST_ATTR}')
        # A matrix attribute reads back as [[16 floats]] from some callers
        if m and isinstance(m[0], (list, tuple)):
            m = list(m[0])
    if not m or len(m) != 16:
        m = cmds.xform(node, q=True, ws=True, matrix=True)
    rows = ([m[0], m[1], m[2]],
            [m[4], m[5], m[6]],
            [m[8], m[9], m[10]])
    unit = []
    for row in rows:
        length = math.sqrt(sum(v * v for v in row))
        unit.append([v / length for v in row] if length > 1e-9 else row)
    return unit
