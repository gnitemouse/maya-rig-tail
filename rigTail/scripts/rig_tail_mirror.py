'''
rig_tail_mirror.py
author: Daisy Jane @gnitemouse

Makes an L/R pair's DIALS agree with MIRROR_BEHAVIOR on all three axes.

    'symmetric' - the same value moves the pair as mirror images
    'parallel'  - the same value moves the pair the same way round the world

WHY THIS EXISTS. A mirrored skeleton cannot deliver either one on its own.
The dials rotate joints about their own local axes, and 'the same value
moves the two sides as mirror images' holds for a local axis only when the
target's copy of it points OPPOSITE the reflection of the source's. Write
the target frame as T = R * S * D, with R the reflection, S the source
frame and D a diagonal of +/-1 marking which axes were negated:

    det(T) = det(R) * det(S) * det(D) = -det(D)

T has to stay right-handed, so det(D) = -1, so an ODD number of axes are
negated. One axis, or all three. NEVER TWO - in any rig, in any software.

All three is available in general: it is what Maya's mirrorJoint
-mirrorBehavior does, and why a mirrored arm's local X runs backwards. It
is not available HERE, because negating the aim points it back up the
chain, and the spline IK, the advanced twist and the stretch all read the
aim as running down the chain - and Setup's own Orient step re-derives it
from the joint positions, so it would undo the mirror on every re-run.

With the aim pinned, exactly ONE axis is free to mirror, and
MIRROR_BEHAVIOR only picks which: 'symmetric' the up axis, 'parallel' the
third. The other two do the opposite of whatever was asked for.

So the fix does not live in the joint orientation. It lives on the way IN,
as a sign on the dial value, where the parity argument has no hold at all:
a scalar can be negated freely. That is all this module does - it measures
how a pair's chains actually stand and reports which axes need negating to
land on the convention MIRROR_BEHAVIOR names.

ROTATIONS AND TRANSLATIONS TAKE OPPOSITE SIGNS. A rotation about a local
axis mirrors when that axis points AGAINST the reflection; a translation
along one mirrors when it points ALONG it. So a dial that slides a joint
(FK offset, the IK spline handle's offset) needs translation_signs, not
rotation_signs - the two are exact negatives of each other.

Consumers: rig_tail_anim (curl, wave, noise), rig_tail_fk (twist, roll,
offset) and rig_tail_connect (the IK spline handle's twist/roll/offset).

CONTROLS ARE A SECOND CASE. A dial is a number, so a sign on it is free.
A control is a gizmo, so its axes must point where its motion goes - which
pins the control frame to the joint frame scaled by these same signs, and
that frame has to stay right-handed. See control_signs: it works out under
'symmetric' and is impossible under 'parallel'.

Functions:
    rotation_signs: per-axis sign for a rotation about each local axis
    translation_signs: the same for a translation along each local axis
    control_signs: signs for a behavior-mirrored control, or None
    mirrored_matrix: a node's world frame with each axis scaled by its sign
    aim_axis: the chain's aim axis as a signs key ('X'/'Y'/'Z')
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


def _signs(rigname, rotation):
    '''
    The measurement behind rotation_signs and translation_signs.

    One body for both so the 'nothing to mirror' answers cannot diverge:
    an unpaired part, a center part and the source side must come back
    UNSIGNED for a translation exactly as they do for a rotation. Deriving
    one from the other by negating the whole dict got that wrong - it
    turned every unmirrored part's translation signs to -1 and reversed FK
    and IK offset on the source side and on every center tail.

    Arguments
        rigname (str): Name of rig component
        rotation (bool): True for a rotation about each axis, False for a
            translation along it (the opposite mirror rule)

    Return
        dict: {'X': sign, 'Y': sign, 'Z': sign}, each +1.0 or -1.0
    '''
    if not MIRROR_SLIDERS:
        return dict(NO_MIRROR)

    partner = rt_naming.mirror_partner(rigname)
    if not partner:
        return dict(NO_MIRROR)

    src = rt_constants.JOINTS_BN.get(partner) or []
    tgt = rt_constants.JOINTS_BN.get(rigname) or []
    if not src or not tgt:
        # Only reachable when the partner was never detected this session
        # (excluded from every run since Maya started). Worth saying out
        # loud: this side builds unsigned while the partner keeps whatever
        # signs its own build gave it, so the pair stops matching.
        logger.warning(f'{rigname}: Mirror: no BN chain for {partner}, '
                       'leaving the dials unsigned - run Setup so both '
                       'sides of the pair are detected')
        return dict(NO_MIRROR)

    keep = {'x': 0, 'y': 1, 'z': 2}.get(
        str(getattr(rt_constants, 'MIRROR_AXIS', 'x')).lower(), 0)

    # Mean cos between each target axis and the reflection of the source's.
    # Averaged over the chain rather than read off one joint: a single joint
    # can sit oddly (a hand-tweaked tip, an un-oriented base) without that
    # being what the chain as a whole does.
    cosines = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    # Only the joints both chains actually have, and only those still in the
    # scene: a stale JOINTS_BN entry naming a deleted joint must not take
    # the build down over a sign
    pairs = [(s, t) for s, t in zip(src, tgt)
             if cmds.objExists(s) and cmds.objExists(t)]
    if not pairs:
        logger.warning(f'{rigname}: Mirror: no joints in common with '
                       f'{partner}, leaving the dials unsigned')
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

    want_mirror = behavior() == 'symmetric'
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

        'symmetric' negates two axes (product +1)   -> buildable
        'parallel'  negates one    (product -1)   -> LEFT-HANDED, refused

    So behavior-mirrored controls exist under 'symmetric' and cannot exist
    under 'parallel'. That is not an omission: 'parallel' asks all three
    axes to move the pair the same way round the world, and a right-handed
    frame can only ever do that on two of them. The dials still honour it -
    a sign on a scalar is bound by nothing - but a gizmo cannot.

    The 'symmetric' frame this yields is the full behavior mirror (every
    axis the negated reflection of its partner's), which is what Maya's
    mirrorJoint -mirrorBehavior produces and what production biped rigs
    ship: the same channel values give mirrored poses on every axis, and
    mirror-pose tools reduce to a straight value copy.

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
        str: 'symmetric' or 'parallel'
    '''
    value = str(getattr(rt_constants, 'MIRROR_BEHAVIOR', 'symmetric'))
    value = value.strip().lower()
    if value not in ('symmetric', 'parallel'):
        logger.warning(f"Mirror: unknown MIRROR_BEHAVIOR '{value}', "
                       "using 'symmetric'")
        return 'symmetric'
    return value


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
