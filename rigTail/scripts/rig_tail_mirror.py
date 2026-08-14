'''
rig_tail_mirror.py
author: Daisy Jane @gnitemouse

Makes an L/R pair agree with MIRROR_BEHAVIOR on all three axes.

    'mirror', 'symmetric' - the same value moves the pair as mirror images
    'parallel'            - the same value moves the pair the same way
                            round the world

THREE OF SIX. A frame has six things it could mirror: a rotation about
each of its three axes, and a translation along each. Write an axis '+'
when it points the SAME way as the mirror image of its partner's matching
axis and '-' when it points the OPPOSITE way; a rotation about an axis
mirrors when it is '-', a translation along it when it is '+'. A
reflection flips handedness, so the count of '-' must be ODD - one, or all
three, never two - and three of the six therefore mirror under every
value. The behavior only picks which three:

    'mirror'     -aim -roll -up   rotations: all three  translations: none
    'symmetric'  +aim +roll -up   rotations: up         translations: aim, roll
    'parallel'   +aim -roll +up   rotations: roll       translations: aim, up

aim runs down the chain (ORIENT_AIM_AXIS), up is ORIENT_UP_AXIS, roll is
the remaining one. 'mirror' is Maya's mirrorJoint -mirrorBehavior; its
'-aim' means the mirrored side runs BACKWARDS down its own chain, which
aim_reversed reports and the spline IK's advanced twist must be told.

The choice follows what a thing is POSED BY, and everything with a gizmo
here is posed by rotation - the FK controls turn their joints, the spline
mid_rot swings the top of the chain - as are curl, wave, noise, twist and
roll. 'mirror' is the default because it spends its three there. The one
dial it costs is offset, a slide along the aim.

WHAT THIS MODULE IS FOR. No orientation gives all six, so the remaining
three are corrected on the way IN, as a sign on the value: the parity
argument binds frames, not scalars. This module measures how a pair's
chains actually stand and reports the signs that land them on the
behavior's convention. Rotations and translations take OPPOSITE signs, so
a dial that slides a joint asks translation_signs and one that turns it
asks rotation_signs - neither is the other negated, since a part with no
partner is unsigned for both.

Everything here is measured off the skeleton rather than read from
MIRROR_BEHAVIOR. The setting says what the next Mirror Orient will do; the
joints say what the rig must be built against, and only the second is safe
when a config and a skeleton disagree.

CONTROLS ARE A SECOND CASE. A sign on a dial is free, but a control is a
gizmo: its axes have to point where its motion goes, which pins its frame
and puts it back under the parity rule. See control_signs, and
flip_control_aim for the spline rows, which carry their own frame rather
than the joints'.

Consumers: rig_tail_anim (curl, wave, noise), rig_tail_fk (twist, roll,
offset), rig_tail_connect (the spline handle's twist/roll/offset),
rig_tail_control (the FK and spline control frames) and rig_tail_stretch
(the advanced twist's forward axis).

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

# Sign the dials so an L/R pair obeys MIRROR_BEHAVIOR on all three axes.
# Off drives every part's own axes raw, which mirrors on one axis only.
MIRROR_SLIDERS = True

# Stand the mirrored side's controls in a frame that poses the pair as
# mirror images, rather than one facing its own joints. Off for a rig whose
# controls are keyed per side and must not change meaning.
MIRROR_CONTROLS = True

# Below this |cos| a target axis is not a mirror of its partner's at all,
# and no sign would make the two read as one motion, so it is left alone.
MIRROR_TOLERANCE = 0.5

# Every axis raw. Returned whole, so callers may not mutate it.
NO_MIRROR = {'X': 1.0, 'Y': 1.0, 'Z': 1.0}

# 'mirror' is the default because it mirrors all three ROTATIONS, and every
# gizmo and dial in this rig but offset is posed by rotation.
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

    The one measurement every answer here is built on: -1 is a '-' axis in
    the +aim/+roll/+up notation, +1 a '+' one. Averaged over the chain
    because a single joint can sit oddly - a hand-tweaked tip, an
    un-oriented base - without that being what the chain as a whole does.

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
        # Reachable only for a partner never detected this session. Worth
        # saying out loud: this side builds unsigned while the partner keeps
        # whatever its own build gave it, so the pair stops matching.
        logger.warning(f'{rigname}: Mirror: no BN chain for {partner}, '
                       'leaving the dials unsigned - run Setup so both '
                       'sides of the pair are detected')
        return None

    keep = {'x': 0, 'y': 1, 'z': 2}.get(
        str(getattr(rt_constants, 'MIRROR_AXIS', 'x')).lower(), 0)
    cosines = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    # Only joints both chains have and the scene still holds: a stale
    # JOINTS_BN entry must not take the build down over a sign
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

    One body for both so their 'nothing to mirror' answers cannot diverge:
    an unpaired part, a center part and the source side must come back
    UNSIGNED for a translation exactly as they do for a rotation, which is
    why neither may be derived by negating the other.

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

    # 'mirror' and 'symmetric' both ask for mirror images and differ only
    # in which axes manage it unaided; 'parallel' asks for the opposite
    want_mirror = behavior() != 'parallel'
    signs = dict(NO_MIRROR)
    for axis, cos in cosines.items():
        if abs(cos) < MIRROR_TOLERANCE:
            logger.warning(f'{rigname}: Mirror: the {axis} axis is not a '
                           f'mirror of {partner} (cos {cos:+.2f}), leaving '
                           'it unsigned - re-run Setup Mirror Orient on the '
                           'pair')
            continue
        # Negate whichever axis does not already do what was asked for
        mirrors_now = (cos < 0) if rotation else (cos > 0)
        signs[axis] = 1.0 if mirrors_now == want_mirror else -1.0

    flipped = ''.join(a for a in 'XYZ' if signs[a] < 0)
    kind = 'rotation' if rotation else 'translation'
    logger.debug(f'{rigname}: Mirror of {partner} ({behavior()}, {kind}): '
                 f'{"negating " + flipped if flipped else "nothing to negate"}')
    return signs


def translation_signs(rigname):
    '''
    Per-axis sign for a TRANSLATION along each of this part's local axes.

    A rotation about a local axis mirrors when that axis points AGAINST
    the reflection of its partner's; a translation along one mirrors when
    it points ALONG it. Opposite rules, so a dial that slides a joint asks
    for these and never for rotation_signs negated - the two agree on a
    mirrored part and differ on every part that has nothing to mirror.

    Arguments
        rigname (str): Name of rig component

    Return
        dict: {'X': sign, 'Y': sign, 'Z': sign}, each +1.0 or -1.0
    '''
    return _signs(rigname, rotation=False)


def control_signs(rigname):
    '''
    Signs for a behavior-mirrored CONTROL, or None when there is no such
    frame to build.

    A control is posed by dragging a gizmo, so its axes must point where
    its motion goes: the joints turn about sign_k * J_k, so the control's
    own axis k has to as well. That makes the control frame the joint frame
    with each axis scaled by its sign - a frame, so the three signs must
    multiply to +1 to stay right-handed:

        'mirror'    -aim -roll -up  none negated: the joints already are it
        'symmetric' +aim +roll -up  two negated (+1)  -> buildable
        'parallel'  +aim -roll +up  one negated (-1)  -> left-handed, None

    'parallel' asking all three axes to move the pair the same way round
    the world is simply not something a right-handed frame can do on more
    than two. The dials still honour it; a gizmo cannot.

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
    takes, so the caller writes it ABSOLUTELY. That is what makes a rebuild
    land on the same frame instead of flipping the flip, and what keeps a
    nested control stack from compounding its parents' flips into its own.

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
    World up vector the spline control rows roll toward, lying IN the
    symmetry plane.

    A stated convention rather than a derived one: taking it from the
    basectrl's Z reaches it from the JOINTS, which makes the control frames
    a by-product of MIRROR_BEHAVIOR.

    A world axis is invariant under the reflection up to SIGN either way,
    so both an in-plane axis and the plane's own normal relate the two
    sides cleanly. What the normal costs is WHICH axis carries the
    negation: the up resolves negated too, and with the aim reversed on top
    of that all three end up negated - the one frame with no mirrored
    translation at all, which is the worst answer for a control posed by
    dragging. So it is refused, and flip_control_aim stands down with it.

    Return
        list or None: unit world vector, or None when no axis is usable
            (the caller then keeps the basectrl-derived reference)
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

    Resolving the up against an in-plane vector leaves both sides' aim and
    up pointing along the reflection of their partner's, which puts the
    negation on the roll axis - so the two axes an animator drags ACROSS
    the tail come out unmirrored. Reversing the aim moves the negation onto
    the aim instead, mirroring BOTH axes that bend the curve and spending
    the slide along the tail's own length, which is the least likely of the
    three to be typed rather than dragged.

    Negating the constraint's aim vector rather than re-standing the frame
    afterwards is what keeps a rebuild from flipping the flip.

    Stands down without an in-plane up reference (spline_up_vector), where
    reversing the aim would negate all three axes and mirror nothing.

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

    Measured off the skeleton, not read from MIRROR_BEHAVIOR: the setting
    describes what the next Mirror Orient will do, and a config and a
    skeleton can disagree. Anything reading the aim as running from parent
    to child has to ask - the spline IK's advanced twist is told through
    its forward axis, and a slide along the aim reverses with it.

    Only the mirrored side of a pair can answer True; the source side is
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
    # A disagreement means a Mirror Orient re-run would change the
    # convention under the rig, so it is worth saying
    if reversed_aim != (behavior() == 'mirror'):
        logger.info(f'{rigname}: Mirror: the skeleton is'
                    f'{"" if reversed_aim else " not"} aim-reversed, but '
                    f"MIRROR_BEHAVIOR says '{behavior()}'. Building for the "
                    'skeleton; re-run Setup Mirror Orient to move it.')
    return reversed_aim


def _axis_rows(node):
    '''
    A node's three local axes (X/Y/Z) as unit world vectors, from its
    stored REST pose when it has one.

    Rest, not live, because this is asked DURING the build and the answer
    must not depend on where in the build it is asked: rig_tail_matrix
    zeroes every BN joint and rebuilds the network under it partway
    through, so a live world matrix can catch a chain mid-rebuild. The rest
    matrix is captured once at build start, before anything moves.

    Normalized so the cosines are comparable between joints: a joint
    carrying scale (the squash network drives BN scaleY/Z) would otherwise
    weigh more than its neighbours.
    '''
    m = None
    if cmds.attributeQuery(rt_rest.REST_ATTR, node=node, exists=True):
        m = cmds.getAttr(f'{node}.{rt_rest.REST_ATTR}')
        # Some callers read a matrix attribute back as [[16 floats]]
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
