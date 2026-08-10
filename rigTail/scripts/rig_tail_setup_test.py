'''
rig_tail_setup_test.py
author: Daisy Jane @gnitemouse

Tests for the Setup phase (rig_tail_setup): the skeleton-prep step that
orients, mirrors and rolls the BN skeleton before a build.

Two kinds of test:

  MATH tests - deterministic, no scene needed. They exercise the geometry
    helpers directly (reflect, roll, aim/mirror frame assembly, pairing).
    This is where "is the mirror math correct?" is answered in isolation.
        run_math()

  SCENE tests - run on the loaded skeleton and are MUTATING: they re-orient
    / mirror / roll real joints through the same entry points the UI uses,
    then verify the result. Like a real Setup run they detach the build's
    offsetParentMatrix drivers, re-baseline (or unbind) geometry and clear
    the rest pose, so RELOAD THE SCENE afterwards. Each scene test saves and
    restores RIGPARTS and the Setup flags so your config is left untouched.
        run_scene()

Designed for the sample scene squid_rig.0013.autorigger.ma (a full squid
build). Its L/R pairs (fintail, sidetail, tail1..3) and center chains
(C_tail, C_fintail) are the defaults below; pass your own names on another
rig.

Usage:
    import rig_tail_setup_test as rt_setup_test
    rt_setup_test.run_math()                 # safe: geometry-helper unit tests
    rt_setup_test.check_mirror('fintail')    # mirror math + one real L/R pair
    rt_setup_test.run_scene()                # MUTATING: every feature on the scene
    rt_setup_test.run_all()                  # run_math() + guidance for run_scene()

    # individual (scene tests mutate; reload after):
    rt_setup_test.test_mirror_orient('fintail')
    rt_setup_test.test_mirror_joints('fintail')
    rt_setup_test.test_orient('C_tail')
    rt_setup_test.test_roll('C_tail', 90)
    rt_setup_test.test_rigname_from_selection('C_tail')

    rt_setup_test.check_skin('C_tail')       # safe: what is bound, and drift

Functions:
  Runners
    run_math: every math test (safe), with a PASS/FAIL summary
    run_scene: every scene test (MUTATING), with a PASS/FAIL summary
    check_mirror: focused mirror check, math plus one real L/R pair
    run_all: run_math plus a pointer to the mutating scene tests
  Math tests (safe, no scene)
    test_reflect: _reflect negates only the plane-normal component
    test_assign_rows: frame assembly is orthonormal and right-handed
    test_roll_about: roll rotates about the aim by the given angle
    test_aim_frames: orient frames aim down-chain and are twist-free
    test_up_mode: cascade keeps the chain's roll, best-fit rebuilds it,
        and both still remove twist (ORIENT_UP_MODE)
    test_mirror_frames: mirror reflects the aim to the far side, and the
        symmetric/parallel behaviors are a 180 deg roll apart
    test_find_mirror_pairs: L/R pairing honours the source side
    test_implied_mirror_pairs: a lone source side names its own target
    test_mirror_index: a created joint carries the source's index, or its
        absence, so the two sides differ only by the side token
    test_swap_side: side-token swap picks the mirrored parent's name
  Scene tests (MUTATING)
    test_orient: ORIENT_JOINTS leaves valid frames aimed down the chain
    test_end_joint: the '_ee_' joint keeps its position, stays down-chain
    test_mirror_orient: MIRROR_ORIENT makes the sides mirror orientations,
        for either MIRROR_BEHAVIOR
    test_mirror_joints: MIRROR_JOINTS makes the sides mirror positions
    test_roll: roll_chain keeps positions and aim, rotates up by the angle
    test_skin_rebaseline: KEEP_WEIGHTS keeps a bound mesh and its weights
        exactly where they were through a re-orient
    test_rigname_from_selection: a selected joint resolves to its RIGPART
  Reports (read-only)
    check_skin: what is bound to a rig part, and its rest-pose drift
'''
import math

import maya.cmds as cmds

import rig_tail_constants as rt_constants
import rig_tail_cleanup as rt_cleanup
import rig_tail_setup as rt_setup


# Defaults for the sample squid scene.
DEFAULT_PAIR_BASE = 'fintail'      # -> L_fintail / R_fintail
DEFAULT_CHAIN = 'C_tail'           # a long center chain (no pair)

# Tolerances: setup bakes orientation into jointOrient via xform, so expect
# float noise, not exact zeros.
ANG_TOL = 0.5      # degrees, orientation comparisons
POS_TOL = 1e-3     # scene units, position comparisons

_AX = {'x': 0, 'y': 1, 'z': 2}


# VECTOR / MATRIX HELPERS ==============================================

def _rows(m):
    ''' The three world axis rows (local X/Y/Z) of a 16-float matrix. '''
    return ([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]])


def _pos(m):
    ''' The translation of a 16-float world matrix. '''
    return [m[12], m[13], m[14]]


def _ang(a, b):
    ''' Angle in degrees between two vectors (0 if either is degenerate). '''
    a, b = rt_setup._norm(a), rt_setup._norm(b)
    if rt_setup._length(a) < 1e-9 or rt_setup._length(b) < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, rt_setup._dot(a, b)))))


def _orthonormal(rows):
    ''' True when the three rows are unit length and mutually perpendicular. '''
    for r in rows:
        if abs(rt_setup._length(r) - 1.0) > 1e-4:
            return False
    return (abs(rt_setup._dot(rows[0], rows[1])) < 1e-4 and
            abs(rt_setup._dot(rows[1], rows[2])) < 1e-4 and
            abs(rt_setup._dot(rows[0], rows[2])) < 1e-4)


def _right_handed(rows):
    ''' True when X cross Y points along +Z (a proper rotation, det +1). '''
    return rt_setup._dot(rt_setup._cross(rows[0], rows[1]), rows[2]) > 0.999


def _verdict(name, ok, msg=''):
    ''' Print one PASS/FAIL line and return the bool for aggregation. '''
    tag = 'PASS' if ok else 'FAIL'
    line = f'  [{tag}] {name}'
    if msg:
        line += f'  --  {msg}'
    print(line)
    return ok


def _summary(title, results):
    '''
    Print a PASS/FAIL/ERROR block for a list of (name, outcome) and return
    True only when nothing failed or errored. outcome is bool, None (RAN)
    or an Exception.
    '''
    print('\n' + '=' * 64)
    print(f'  {title}')
    print('=' * 64)
    ok = True
    for name, outcome in results:
        if isinstance(outcome, Exception):
            status, detail = 'ERROR', str(outcome)
            ok = False
        elif outcome is True:
            status, detail = 'PASS', ''
        elif outcome is False:
            status, detail = 'FAIL', ''
            ok = False
        else:
            status, detail = 'RAN ', ''
        line = f'  {status}  {name}'
        if detail:
            line += f'  --  {detail}'
        print(line)
    print('=' * 64)
    print(f'  RESULT: {"ALL CLEAR" if ok else "ISSUES FOUND"}')
    print('=' * 64 + '\n')
    return ok


def _max_twist(frames, points):
    '''
    The largest RELATIVE twist between consecutive frames, in degrees.

    Twist is the roll of the up axis about the segment the two joints share,
    so both ups are projected perpendicular to that segment first (the
    measure rig_tail_setup._report_twist uses). The raw angle between two
    ups is not it: on a chain that bends, ups must tilt with their own aim
    even when nothing is twisted.
    '''
    worst = 0.0
    for i in range(len(frames) - 1):
        aim = rt_setup._norm(rt_setup._sub(points[i + 1], points[i]))
        ups = []
        for f in (frames[i], frames[i + 1]):
            ups.append(rt_setup._sub(
                f[2], rt_setup._scale(aim, rt_setup._dot(f[2], aim))))
        if all(rt_setup._length(u) > 1e-9 for u in ups):
            worst = max(worst, _ang(ups[0], ups[1]))
    return worst


# MATH TESTS (safe, no scene) ==========================================

def test_reflect():
    '''_reflect negates only the plane-normal component.'''
    cases = [
        (0, [1, 2, 3], [-1, 2, 3]),
        (1, [1, 2, 3], [1, -2, 3]),
        (2, [1, 2, 3], [1, 2, -3]),
    ]
    ok = True
    for keep, vec, want in cases:
        got = rt_setup._reflect(vec, keep)
        good = got == want
        ok &= _verdict(f'reflect keep={keep}', good, f'{vec} -> {got}')
    return ok


def test_assign_rows():
    '''_assign_rows places aim/up and builds a right-handed orthonormal frame.'''
    aim = rt_setup._norm([1, 0, 0])
    up = rt_setup._norm([0, 0, 1])
    rows = rt_setup._assign_rows(aim, up, 'x', 'z')
    ok = True
    ok &= _verdict('assign_rows orthonormal', _orthonormal(rows))
    ok &= _verdict('assign_rows right-handed', _right_handed(rows))
    ok &= _verdict('assign_rows aim on X', _ang(rows[0], aim) < ANG_TOL,
                   f'X={[round(v, 3) for v in rows[0]]}')
    ok &= _verdict('assign_rows up on Z', _ang(rows[2], up) < ANG_TOL,
                   f'Z={[round(v, 3) for v in rows[2]]}')
    return ok


def test_roll_about():
    '''_roll_about rotates about the aim axis by the given angle, staying unit.'''
    aim = [1, 0, 0]
    up = [0, 0, 1]
    ok = True
    # +90 about +X sends +Z to -Y; 180 to -Z; -90 to +Y (verified geometry).
    for deg, want in ((90, [0, -1, 0]), (180, [0, 0, -1]), (-90, [0, 1, 0])):
        got = rt_setup._roll_about(up, aim, deg)
        good = _ang(got, want) < ANG_TOL and abs(rt_setup._length(got) - 1) < 1e-4
        ok &= _verdict(f'roll_about {deg:>4}', good,
                       f'-> {[round(v, 3) for v in got]}')
    # A roll must not move the aim axis (rolling the up around it).
    same = _ang(rt_setup._roll_about(aim, aim, 37.0), aim) < ANG_TOL
    ok &= _verdict('roll_about keeps aim fixed', same)
    return ok


def test_aim_frames():
    '''aim_frames: orthonormal, right-handed, aim down-chain, twist-free.'''
    # A chain bending in the Z=0 plane (so the plane normal is world Z).
    pts = [[0, 0, 0], [1, 0.2, 0], [2, 0.5, 0], [3, 0.9, 0], [4, 1.4, 0]]
    frames = rt_setup.aim_frames(pts, 'x', 'z')
    segs = [rt_setup._norm(rt_setup._sub(pts[i + 1], pts[i]))
            for i in range(len(pts) - 1)]
    ok = True
    orth = all(_orthonormal(f) for f in frames)
    rh = all(_right_handed(f) for f in frames)
    ok &= _verdict('aim_frames orthonormal', orth)
    ok &= _verdict('aim_frames right-handed', rh)
    # Aim row (index 0) follows the chain segments.
    aim_err = max(_ang(frames[i][0], segs[min(i, len(segs) - 1)])
                  for i in range(len(frames)))
    ok &= _verdict('aim_frames aim follows chain', aim_err < ANG_TOL,
                   f'max aim err={aim_err:.3f} deg')
    # Twist-free: consecutive up rows (index 2) stay aligned.
    twist = max(_ang(frames[i][2], frames[i + 1][2])
                for i in range(len(frames) - 1))
    ok &= _verdict('aim_frames twist-free', twist < ANG_TOL,
                   f'max twist={twist:.3f} deg')
    return ok


def test_up_mode():
    '''
    aim_frames' up reference decides the roll: cascade keeps it, best-fit
    recomputes it.

    The point of ORIENT_UP_MODE. A chain is oriented, then rolled 90 degrees
    (standing in for a mirror or a Roll Chain fix-up) and given a per-joint
    twist on top. Re-orienting must flatten that twist either way, but only
    cascade - seeded from the chain's own first joint - may keep the 90.
    best-fit derives the roll from the bend plane, so it must come back to
    the unrolled orientation.
    '''
    # A chain bending out of any world plane, so the roll is unambiguous.
    pts = [[0, 0, 0], [1, 0.2, 0.1], [2, 0.5, 0.35], [3, 0.9, 0.8]]
    base = rt_setup.aim_frames(pts, 'x', 'z')
    ok = True

    # Roll the whole chain 90 deg, then twist each joint a bit more.
    rolled = []
    for i, f in enumerate(base):
        up = rt_setup._roll_about(f[2], f[0], 90.0 + 17.0 * i)
        rolled.append(rt_setup._assign_rows(f[0], up, 'x', 'z'))
    before = _max_twist(rolled, pts)
    ok &= _verdict('test data is twisted to start with', before > 10.0,
                   f'{before:.1f} deg')

    cascade = rt_setup.aim_frames(pts, 'x', 'z', rolled[0][2])
    bestfit = rt_setup.aim_frames(pts, 'x', 'z')
    for name, frames in (('cascade', cascade), ('best-fit', bestfit)):
        ok &= _verdict(f'aim_frames {name} orthonormal',
                       all(_orthonormal(f) for f in frames))
        ok &= _verdict(f'aim_frames {name} right-handed',
                       all(_right_handed(f) for f in frames))
        # Both modes exist to remove twist; that must not depend on the mode
        # or on the roll the seed carries.
        twist = _max_twist(frames, pts)
        ok &= _verdict(f'aim_frames {name} removes twist', twist < ANG_TOL,
                       f'{before:.1f} -> {twist:.3f} deg')

    # The roll itself: cascade stays with the rolled chain, best-fit returns
    # to the plane-derived orientation. Measured at the seed joint, where
    # the chain's own non-planarity does not muddy the comparison.
    kept = _ang(cascade[0][2], rolled[0][2])
    ok &= _verdict('aim_frames cascade keeps the roll', kept < ANG_TOL,
                   f'{kept:.2f} deg from the rolled up')
    lost = _ang(bestfit[0][2], rolled[0][2])
    ok &= _verdict('aim_frames best-fit rebuilds the roll', lost > 45.0,
                   f'{lost:.2f} deg from the rolled up')

    # A missing or degenerate seed must fall back to best-fit, not blow up.
    for tag, seed in (('None', None), ('zero', [0.0, 0.0, 0.0])):
        fallback = rt_setup.aim_frames(pts, 'x', 'z', seed)
        same = max(_ang(a[2], b[2]) for a, b in zip(fallback, bestfit))
        ok &= _verdict(f'aim_frames {tag} seed falls back to best-fit',
                       same < ANG_TOL)

    # _up_mode validates and defaults.
    ok &= _verdict("_up_mode('Best-Fit') normalizes",
                   rt_setup._up_mode('Best-Fit') == 'best-fit')
    ok &= _verdict("_up_mode('nonsense') defaults to cascade",
                   rt_setup._up_mode('nonsense') == 'cascade')
    return ok


def test_mirror_frames():
    '''
    mirror_frames reflects a source orientation across the plane.

    The defining property: the mirrored aim is the reflection of the source
    aim (so reflecting the result back recovers the source), and the frame
    stays orthonormal and right-handed. The up is the reflected up under
    'parallel' and its negation under 'symmetric' - the two behaviors are a
    180-degree roll about the aim apart, which is checked here too. This is
    the core "is the mirror correct?" check, in isolation from the scene.
    '''
    keep = _AX['x']
    # A source frame aiming outward on +X, up +Z.
    aim = rt_setup._norm([0.8, 0.6, 0.0])
    up = rt_setup._norm([0.0, 0.0, 1.0])
    src_rows = rt_setup._assign_rows(aim, up, 'x', 'z')
    m = [src_rows[0][0], src_rows[0][1], src_rows[0][2], 0,
         src_rows[1][0], src_rows[1][1], src_rows[1][2], 0,
         src_rows[2][0], src_rows[2][1], src_rows[2][2], 0,
         0, 0, 0, 1]
    reflected_up = rt_setup._reflect(src_rows[2], keep)
    ok = True

    for behavior, sign in (('symmetric', -1.0), ('parallel', 1.0)):
        tgt = rt_setup.mirror_frames([m], 'x', 'x', 'z', behavior)[0]
        ok &= _verdict(f'mirror_frames {behavior} orthonormal', _orthonormal(tgt))
        ok &= _verdict(f'mirror_frames {behavior} right-handed', _right_handed(tgt))
        # Aim reflected regardless of behavior: it must follow the mirrored
        # chain, which is exactly what leaves the roll as the only freedom.
        aim_ok = _ang(tgt[0], rt_setup._reflect(src_rows[0], keep)) < ANG_TOL
        ok &= _verdict(f'mirror_frames {behavior} aim reflected', aim_ok,
                       f'target aim={[round(v, 3) for v in tgt[0]]}')
        want_up = rt_setup._scale(reflected_up, sign)
        up_ok = _ang(tgt[2], want_up) < ANG_TOL
        ok &= _verdict(f'mirror_frames {behavior} up '
                       f'{"negated" if sign < 0 else "reflected"}', up_ok,
                       f'target up={[round(v, 3) for v in tgt[2]]}')
        # The fix this reworked: the mirrored aim must point to the OPPOSITE
        # side (its X component flips sign), not the same way as the source.
        outward = (tgt[0][0] * src_rows[0][0]) < 0
        ok &= _verdict(f'mirror_frames {behavior} aim flips side', outward,
                       f'src X={src_rows[0][0]:.3f}, tgt X={tgt[0][0]:.3f}')

    # The two behaviors differ by exactly 180 degrees about the aim - the
    # property that lets Roll Chain at 180 convert one into the other.
    sym = rt_setup.mirror_frames([m], 'x', 'x', 'z', 'symmetric')[0]
    par = rt_setup.mirror_frames([m], 'x', 'x', 'z', 'parallel')[0]
    ok &= _verdict('mirror_frames behaviors are a 180 roll apart',
                   abs(_ang(sym[2], par[2]) - 180.0) < ANG_TOL,
                   f'up-to-up angle={_ang(sym[2], par[2]):.2f} deg')
    ok &= _verdict('mirror_frames behaviors share an aim',
                   _ang(sym[0], par[0]) < ANG_TOL)

    # An unrecognized behavior must not silently mirror some third way.
    bad = rt_setup.mirror_frames([m], 'x', 'x', 'z', 'sideways')[0]
    ok &= _verdict('mirror_frames unknown behavior falls back to symmetric',
                   _ang(bad[2], sym[2]) < ANG_TOL)
    return ok


def test_find_mirror_pairs():
    '''find_mirror_pairs pairs L/R by prefix and honours the source side.'''
    saved_parts = list(rt_constants.RIGPARTS)
    saved_side = getattr(rt_constants, 'MIRROR_SOURCE_SIDE', 'R')
    try:
        rt_constants.RIGPARTS = ['R_fintail', 'L_fintail', 'C_tail',
                           'L_sidetail', 'R_sidetail', 'L_wing']
        rt_constants.MIRROR_SOURCE_SIDE = 'R'
        pairs, paired = rt_setup.find_mirror_pairs(rt_constants.RIGPARTS)
        pairset = set(pairs)
        want = {('R_fintail', 'L_fintail'), ('R_sidetail', 'L_sidetail')}
        ok = True
        ok &= _verdict('find_mirror_pairs correct pairs', pairset == want,
                       f'{sorted(pairset)}')
        # Source side R => first element of each pair is the R part.
        src_ok = all(s.startswith('R_') for s, _ in pairs)
        ok &= _verdict('find_mirror_pairs source=R', src_ok)
        # Unpaired parts excluded.
        ok &= _verdict('find_mirror_pairs unpaired excluded',
                       'C_tail' not in paired and 'L_wing' not in paired)
        return ok
    finally:
        rt_constants.RIGPARTS = saved_parts
        rt_constants.MIRROR_SOURCE_SIDE = saved_side


def test_mirror_index():
    '''A created joint carries the source's index, absence included.'''
    import rig_tail_naming as rt_naming
    ok = True
    # An unnumbered source stays unnumbered, so the two sides read as the
    # same name but for the side token - and _mirror_parent can find it.
    ok &= _verdict('unnumbered source gives no index',
                   rt_setup._mirror_index('BN_L_leg_jnt', 0) == '')
    ok &= _verdict('and so names the mirror BN_R_leg_jnt',
                   rt_naming.fstr('R_leg', rt_constants.JOINT,
                                  rt_constants.TYPE_BN,
                                  rt_setup._mirror_index('BN_L_leg_jnt', 0))
                   == 'BN_R_leg_jnt')
    # A numbered source numbers the mirror the same, as it always has.
    ok &= _verdict('numbered source keeps its number',
                   rt_setup._mirror_index('BN_L_fintail_03_jnt', 7) == 3)
    ok &= _verdict('and so names the mirror BN_R_fintail_03_jnt',
                   rt_naming.fstr('R_fintail', rt_constants.JOINT,
                                  rt_constants.TYPE_BN,
                                  rt_setup._mirror_index(
                                      'BN_L_fintail_03_jnt', 7))
                   == 'BN_R_fintail_03_jnt')
    # An '_ee_' in the chain BODY is a stray name, not an end joint:
    # get_joint_chain stops at end joints, so one here would mint a second.
    ok &= _verdict("a stray '_ee_' falls back to its position",
                   rt_setup._mirror_index('BN_L_odd_ee_jnt', 4) == 4)
    return ok


def test_implied_mirror_pairs():
    '''A lone source side names its own target, and only its own target.'''
    saved_parts = list(rt_constants.RIGPARTS)
    saved_side = getattr(rt_constants, 'MIRROR_SOURCE_SIDE', 'R')
    saved_excl = list(getattr(rt_constants, 'RIGPARTS_EXCLUDE', []))
    try:
        rt_constants.RIGPARTS = ['L_leg', 'L_rear_eye', 'R_fintail',
                                 'L_fintail', 'C_tail', 'L_nochain']
        rt_constants.RIGPARTS_EXCLUDE = []
        rt_constants.MIRROR_SOURCE_SIDE = 'L'
        # The parts a run detected chains for; L_nochain deliberately has none.
        detected = {'L_leg', 'L_rear_eye', 'L_fintail', 'R_fintail', 'C_tail'}
        implied = rt_setup._implied_mirror_pairs(detected)

        ok = True
        ok &= _verdict('implied pairs for the unpaired source sides',
                       set(implied) == {('L_leg', 'R_leg'),
                                        ('L_rear_eye', 'R_rear_eye')},
                       f'{sorted(implied)}')
        # L_fintail already has R_fintail listed: find_mirror_pairs' business.
        ok &= _verdict('a stated pair is not also implied',
                       all(s != 'L_fintail' for s, _ in implied))
        # Nothing to mirror FROM, so naming a target would only add a name.
        ok &= _verdict('a source with no chain implies nothing',
                       all(s != 'L_nochain' for s, _ in implied))
        ok &= _verdict('a centre part implies nothing',
                       all(s != 'C_tail' for s, _ in implied))

        # Source side R: the L parts are targets, and a target never implies
        # a source - that would overwrite the side the artist authored.
        rt_constants.MIRROR_SOURCE_SIDE = 'R'
        rt_constants.RIGPARTS = ['L_leg', 'L_rear_eye']
        ok &= _verdict('target-side-only roster implies nothing',
                       rt_setup._implied_mirror_pairs(detected) == [])
        return ok
    finally:
        rt_constants.RIGPARTS = saved_parts
        rt_constants.RIGPARTS_EXCLUDE = saved_excl
        rt_constants.MIRROR_SOURCE_SIDE = saved_side


def test_swap_side():
    '''_swap_side flips L/R side tokens and leaves everything else alone.'''
    cases = [
        ('BN_R_fintail_00_jnt', 'BN_L_fintail_00_jnt'),
        ('BN_L_fintail_00_jnt', 'BN_R_fintail_00_jnt'),
        ('BN_C_fintail_00_jnt', 'BN_C_fintail_00_jnt'),
        # Multi-letter tokens starting with l/r must not be touched.
        ('BN_root_left_jnt', 'BN_root_left_jnt'),
    ]
    ok = True
    for name, want in cases:
        got = rt_setup._swap_side(name)
        ok &= _verdict(f'_swap_side {name}', got == want, f'got {got}')
    return ok


# SCENE HELPERS ========================================================

_FLAGS = ('ORIENT_JOINTS', 'MIRROR_ORIENT', 'MIRROR_JOINTS', 'MIRROR_DRYRUN',
          'MIRROR_BEHAVIOR')


def _run_setup_on(parts, orient=False, mir_orient=False, mir_joints=False,
                  behavior=None):
    '''
    Run the real Setup pipeline (setup_tails) on just `parts`, then restore
    RIGPARTS and the Setup flags. Mutates the skeleton like a real run.

    behavior overrides MIRROR_BEHAVIOR for the run ('symmetric'/'parallel');
    None keeps the current setting.
    '''
    saved_parts = list(rt_constants.RIGPARTS)
    saved_flags = {n: getattr(rt_constants, n, None) for n in _FLAGS}
    try:
        rt_constants.RIGPARTS = list(parts)
        rt_constants.ORIENT_JOINTS = orient
        rt_constants.MIRROR_ORIENT = mir_orient
        rt_constants.MIRROR_JOINTS = mir_joints
        rt_constants.MIRROR_DRYRUN = False
        if behavior is not None:
            rt_constants.MIRROR_BEHAVIOR = behavior
        return rt_setup.setup_tails(root=None, dry_run=False)
    finally:
        rt_constants.RIGPARTS = saved_parts
        for n, v in saved_flags.items():
            if v is not None:
                setattr(rt_constants, n, v)


def _pair_joints(base):
    '''
    (source_rigname, target_rigname, source_joints, target_joints) for an
    L/R base, or None when the pair or its BN joints are missing. Detects
    BN joints for the pair first.
    '''
    saved_parts = list(rt_constants.RIGPARTS)
    saved_side = getattr(rt_constants, 'MIRROR_SOURCE_SIDE', 'R')
    try:
        parts = [f'{saved_side}_{base}',
                 f'{"L" if saved_side == "R" else "R"}_{base}']
        rt_constants.RIGPARTS = parts
        pairs, _ = rt_setup.find_mirror_pairs(parts)
        if not pairs:
            return None
        source, target = pairs[0]
        rt_cleanup.detect_joints_bn()
        src = rt_constants.JOINTS_BN.get(source)
        tgt = rt_constants.JOINTS_BN.get(target)
        if not src or not tgt:
            return None
        return source, target, list(src), list(tgt)
    finally:
        rt_constants.RIGPARTS = saved_parts
        rt_constants.MIRROR_SOURCE_SIDE = saved_side


def _chain_joints(rigname):
    ''' BN joints for a single chain, or None. Detects first. '''
    saved_parts = list(rt_constants.RIGPARTS)
    try:
        rt_constants.RIGPARTS = [rigname]
        rt_cleanup.detect_joints_bn()
        joints = rt_constants.JOINTS_BN.get(rigname)
        return list(joints) if joints else None
    finally:
        rt_constants.RIGPARTS = saved_parts


# SCENE TESTS (mutating) ===============================================

def test_orient(rigname=DEFAULT_CHAIN):
    '''
    Orient one chain (ORIENT_JOINTS) and verify the result.

    Correct orient: every joint's frame is orthonormal and right-handed and
    its aim axis points down the chain. Residual twist and planarity are
    reported (a non-planar chain keeps some twist by nature - not a bug), so
    the verdict rests on orthonormality and aim alignment, not twist.
    '''
    joints = _chain_joints(rigname)
    if not joints or len(joints) < 2:
        print(f'  test_orient: no usable chain for {rigname}, skipping')
        return None

    aim_axis = rt_setup._cst('ORIENT_AIM_AXIS')
    ai = _AX.get(aim_axis, 0)
    _run_setup_on([rigname], orient=True)

    positions = [cmds.xform(j, q=True, ws=True, translation=True) for j in joints]
    segs = [rt_setup._norm(rt_setup._sub(positions[i + 1], positions[i]))
            for i in range(len(positions) - 1)]
    orth = rh = True
    aim_err = 0.0
    for i, j in enumerate(joints):
        rows = _rows(cmds.xform(j, q=True, ws=True, matrix=True))
        orth &= _orthonormal(rows)
        rh &= _right_handed(rows)
        seg = segs[min(i, len(segs) - 1)]
        aim_err = max(aim_err, _ang(rows[ai], seg))

    ok = True
    ok &= _verdict(f'orient {rigname} orthonormal', orth)
    ok &= _verdict(f'orient {rigname} right-handed', rh)
    ok &= _verdict(f'orient {rigname} aim follows chain', aim_err < ANG_TOL,
                   f'max aim err={aim_err:.3f} deg')
    return ok


def test_end_joint(rigname=DEFAULT_CHAIN):
    '''
    Orient one chain and verify its end ('_ee_') joint.

    The end joint is a child excluded from the chain, so it is not re-placed
    by the orient: it swings with its parent. Its world position must be
    preserved, and it must end up on the FAR side of the last joint - down
    the chain, not back up it. A chain whose last bone ran along the negative
    aim axis used to swing to the wrong side and stay there.
    '''
    joints = _chain_joints(rigname)
    if not joints or len(joints) < 2:
        print(f'  test_end_joint: no usable chain for {rigname}, skipping')
        return None
    ee = rt_setup._find_end_joint(joints[-1])
    if not ee:
        print(f'  test_end_joint: {rigname} has no end joint, skipping')
        return None

    ai = _AX.get(rt_setup._cst('ORIENT_AIM_AXIS'), 0)
    before = cmds.xform(ee, q=True, ws=True, translation=True)
    last_before = cmds.xform(joints[-1], q=True, ws=True, translation=True)
    _run_setup_on([rigname], orient=True)
    after = cmds.xform(ee, q=True, ws=True, translation=True)
    last_after = cmds.xform(joints[-1], q=True, ws=True, translation=True)

    ok = True
    moved = math.dist(before, after)
    ok &= _verdict(f'end joint {rigname} position kept', moved < POS_TOL,
                   f'moved {moved:.5f} '
                   f'({[round(v, 3) for v in before]} -> '
                   f'{[round(v, 3) for v in after]})')

    # The end joint must lie down-chain of the last joint: the direction to
    # it agrees with the last joint's aim axis, not the reverse.
    to_ee = rt_setup._sub(after, last_after)
    aim = _rows(cmds.xform(joints[-1], q=True, ws=True, matrix=True))[ai]
    align = _ang(to_ee, aim)
    ok &= _verdict(f'end joint {rigname} on the aim side', align < 90.0,
                   f'angle to aim={align:.1f} deg (>90 means it flipped '
                   'back up the chain)')

    # Sanity: the last joint itself did not move either.
    ok &= _verdict(f'end joint {rigname} parent position kept',
                   math.dist(last_before, last_after) < POS_TOL)
    return ok


def test_mirror_orient(base=DEFAULT_PAIR_BASE, behavior=None):
    '''
    Mirror one L/R pair's ORIENTATION (MIRROR_ORIENT) and verify symmetry.

    Invariant after the mirror: reflecting the target side's aim across the
    symmetry plane recovers the source side's, so the aim follows the
    mirrored chain either way. The up does the same under 'parallel'; under
    'symmetric' the reflected up is the source's NEGATED up, since the two
    behaviors are a 180-degree roll about the aim apart. Also checks the
    target stays a valid right-handed frame and its positions did not move.

    Arguments
        base (str): L/R pair base name, e.g. 'fintail'.
        behavior (str): 'symmetric'/'parallel'; None uses MIRROR_BEHAVIOR.
    '''
    info = _pair_joints(base)
    if not info:
        print(f'  test_mirror_orient: no L/R pair for "{base}", skipping')
        return None
    source, target, src, tgt = info
    keep = _AX.get(rt_setup._cst('MIRROR_AXIS'), 0)
    ai = _AX.get(rt_setup._cst('ORIENT_AIM_AXIS'), 0)
    ui = _AX.get(rt_setup._cst('ORIENT_UP_AXIS'), 2)

    tgt_pos_before = [cmds.xform(j, q=True, ws=True, translation=True) for j in tgt]
    _run_setup_on([source, target], mir_orient=True, behavior=behavior)
    # Read back what the run actually used, so the expected up sign matches
    # the setting even when the caller passed None.
    used = rt_setup._behavior(behavior)
    up_sign = -1.0 if used == 'symmetric' else 1.0

    n = min(len(src), len(tgt))
    aim_err = up_err = 0.0
    rh = True
    for i in range(n):
        sm = _rows(cmds.xform(src[i], q=True, ws=True, matrix=True))
        tm = _rows(cmds.xform(tgt[i], q=True, ws=True, matrix=True))
        rh &= _right_handed(tm)
        # reflect(target axis) should equal source axis (up negated when the
        # behavior is 'symmetric')
        aim_err = max(aim_err, _ang(rt_setup._reflect(tm[ai], keep), sm[ai]))
        want_up = rt_setup._scale(sm[ui], up_sign)
        up_err = max(up_err, _ang(rt_setup._reflect(tm[ui], keep), want_up))

    tgt_pos_after = [cmds.xform(j, q=True, ws=True, translation=True) for j in tgt]
    moved = max(math.dist(a, b)
                for a, b in zip(tgt_pos_before, tgt_pos_after))

    ok = True
    ok &= _verdict(f'mirror_orient {source}->{target} aim symmetric',
                   aim_err < ANG_TOL, f'max aim err={aim_err:.3f} deg')
    ok &= _verdict(f'mirror_orient {source}->{target} up symmetric '
                   f'({used})', up_err < ANG_TOL,
                   f'max up err={up_err:.3f} deg')
    ok &= _verdict(f'mirror_orient {target} right-handed', rh)
    ok &= _verdict(f'mirror_orient {target} positions kept', moved < POS_TOL,
                   f'max move={moved:.5f}')
    return ok


def test_mirror_joints(base=DEFAULT_PAIR_BASE):
    '''
    Mirror one L/R pair's POSITIONS (MIRROR_JOINTS) and verify symmetry.

    Invariant after the mirror: reflecting each target joint's world
    position across the symmetry plane recovers the source joint's position,
    so the two sides are positional mirrors regardless of how they started.
    '''
    info = _pair_joints(base)
    if not info:
        print(f'  test_mirror_joints: no L/R pair for "{base}", skipping')
        return None
    source, target, src, tgt = info
    keep = _AX.get(rt_setup._cst('MIRROR_AXIS'), 0)

    _run_setup_on([source, target], mir_joints=True)

    n = min(len(src), len(tgt))
    pos_err = 0.0
    for i in range(n):
        sp = cmds.xform(src[i], q=True, ws=True, translation=True)
        tp = cmds.xform(tgt[i], q=True, ws=True, translation=True)
        pos_err = max(pos_err, math.dist(rt_setup._reflect(tp, keep), sp))

    ok = _verdict(f'mirror_joints {source}->{target} positions symmetric',
                  pos_err < POS_TOL, f'max pos err={pos_err:.5f}')
    return ok


def test_roll(rigname=DEFAULT_CHAIN, angle=90.0):
    '''
    Roll one chain (roll_chain) and verify, then roll back.

    A roll must keep every joint's world position and aim axis fixed, and
    rotate the up axis by the roll angle about that aim. The test rolls by
    +angle, checks, then rolls by -angle to restore the orientation (roll
    does not touch the aim, so this returns the chain to its start).
    '''
    joints = _chain_joints(rigname)
    if not joints or len(joints) < 2:
        print(f'  test_roll: no usable chain for {rigname}, skipping')
        return None
    ai = _AX.get(rt_setup._cst('ORIENT_AIM_AXIS'), 0)
    ui = _AX.get(rt_setup._cst('ORIENT_UP_AXIS'), 2)

    before = [cmds.xform(j, q=True, ws=True, matrix=True) for j in joints]
    rt_setup.roll_chain(rigname, angle)
    after = [cmds.xform(j, q=True, ws=True, matrix=True) for j in joints]

    pos_moved = aim_moved = 0.0
    up_err = 0.0
    for b, a in zip(before, after):
        pos_moved = max(pos_moved, math.dist(_pos(b), _pos(a)))
        rb, ra = _rows(b), _rows(a)
        aim_moved = max(aim_moved, _ang(rb[ai], ra[ai]))
        up_err = max(up_err, abs(_ang(rb[ui], ra[ui]) - abs(angle)))

    ok = True
    ok &= _verdict(f'roll {rigname} positions kept', pos_moved < POS_TOL,
                   f'max move={pos_moved:.5f}')
    ok &= _verdict(f'roll {rigname} aim unchanged', aim_moved < ANG_TOL,
                   f'max aim move={aim_moved:.3f} deg')
    ok &= _verdict(f'roll {rigname} up rotated by {angle:g}', up_err < ANG_TOL,
                   f'up-angle error={up_err:.3f} deg')

    # Roll back so the chain's orientation is where it started.
    rt_setup.roll_chain(rigname, -angle)
    return ok


def _mesh_points(geo):
    ''' World-space vertex positions of a mesh, as a flat list. '''
    return cmds.xform(f'{geo}.vtx[*]', q=True, ws=True, t=True)


def _sample_weights(skincluster, geo, count=20):
    '''
    Painted weights for a spread of vertices: {vertex index: [weights]}.
    A sample, not the whole mesh - enough to catch a rebind, cheap on a
    dense body mesh.
    '''
    total = cmds.polyEvaluate(geo, v=True)
    if not isinstance(total, int) or total < 1:
        return {}
    step = max(1, total // count)
    return {i: cmds.skinPercent(skincluster, f'{geo}.vtx[{i}]', q=True, v=True)
            for i in range(0, total, step)}


def test_skin_rebaseline(rigname=DEFAULT_CHAIN):
    '''
    KEEP_WEIGHTS: re-orienting a BOUND chain must not move the mesh or
    touch its weights.

    The point of the re-baseline. Snapshots the deformed mesh and a sample
    of its painted weights, runs a real orient over the bound skeleton, and
    checks the mesh landed back on its modelled shape with the same
    skinCluster and the same weights. Without the re-baseline the mesh is
    dragged by however far the joints turned; with the old behaviour the
    cluster is deleted and the weights are gone.

    Skips (returns None) when the part has no skinned geometry - bind the
    mesh first, or run this on a part that is bound.
    '''
    import rig_tail_maya as rt_maya
    if not rt_maya.keep_weights():
        print('  test_skin_rebaseline: Keep Weights is off, skipping')
        return None
    geos = [g for g in rt_maya.find_geometry_for_rigname(rigname)
            if rt_maya.find_skincluster(g)]
    if not geos:
        print(f'  test_skin_rebaseline: no skinned geometry for {rigname}, '
              f'skipping')
        return None

    before = {}
    for geo in geos:
        skincluster = rt_maya.find_skincluster(geo)
        before[geo] = (skincluster, _mesh_points(geo),
                       _sample_weights(skincluster, geo))

    _run_setup_on([rigname], orient=True)

    ok = True
    for geo, (skincluster, points, weights) in before.items():
        leaf = geo.split('|')[-1]
        now = rt_maya.find_skincluster(geo)
        ok &= _verdict(f'skin {leaf} cluster kept', now == skincluster,
                       f"was '{skincluster}', now '{now}'")
        if now != skincluster:
            continue

        after = _mesh_points(geo)
        drift = 0.0
        if len(after) == len(points):
            for i in range(0, len(points), 3):
                drift = max(drift, math.dist(points[i:i + 3], after[i:i + 3]))
            ok &= _verdict(f'skin {leaf} mesh did not move', drift < POS_TOL,
                           f'max vertex move={drift:.5f}')
        else:
            ok &= _verdict(f'skin {leaf} vertex count kept', False,
                           f'{len(points) // 3} -> {len(after) // 3}')

        after_w = _sample_weights(skincluster, geo)
        same = all(len(after_w.get(v, [])) == len(w)
                   and all(abs(x - y) < 1e-5 for x, y in zip(w, after_w[v]))
                   for v, w in weights.items())
        ok &= _verdict(f'skin {leaf} weights unchanged', same,
                       f'{len(weights)} vertices sampled')
    return ok


def check_skin(rigname=DEFAULT_CHAIN):
    '''
    Read-only report: what is bound to a rig part and how far its rest pose
    has drifted. Run before test_skin_rebaseline to see the starting state,
    or after a build to explain a mesh that sits in the wrong place.

    Non-mutating - queries only.
    '''
    import rig_tail_maya as rt_maya
    joints = rt_maya._chain_influence_joints(rigname) \
        if _chain_joints(rigname) else []
    geos = rt_maya.find_geometry_for_rigname(rigname)
    print(f'\n--- SKIN CHECK ({rigname}) ---')
    print(f'BIND_GEOMETRY={rt_maya.bind_enabled()}, '
          f'KEEP_WEIGHTS={rt_maya.keep_weights()}, '
          f'{len(joints)} chain joint(s), {len(geos)} matching mesh(es)')
    if not geos:
        print('  no geometry matches this rig part '
              "('<rigname>_geo', '<rigname>', '<rigname>_NN')")
        return False

    ok = True
    for geo in geos:
        leaf = geo.split('|')[-1]
        skincluster = rt_maya.find_skincluster(geo)
        if not skincluster:
            print(f'  {leaf}: NOT SKINNED - the build will bind it fresh'
                  if rt_maya.bind_enabled() else
                  f'  {leaf}: NOT SKINNED - Bind Geometry is off, so the '
                  f'build will leave it unbound')
            continue
        indices = rt_maya.skin_influence_indices(skincluster)
        total = len(cmds.skinCluster(skincluster, q=True, inf=True) or [])
        bound = [j for j in joints if j.split('|')[-1] in indices]
        missing = [j for j in joints if j.split('|')[-1] not in indices]
        drift = 0.0
        for jnt in bound:
            index = indices[jnt.split('|')[-1]]
            pos, _ = rt_maya._rest_drift(
                cmds.getAttr(f'{skincluster}.bindPreMatrix[{index}]'), jnt)
            drift = max(drift, pos)
        print(f"  {leaf}: '{skincluster}', {total} influence(s), "
              f'{len(bound)}/{len(joints)} of this chain, '
              f'max rest drift={drift:.5f}')
        if missing:
            print(f'    not influences (mesh will not follow them): '
                  f'{", ".join(j.split("|")[-1] for j in missing)}')
        if drift > POS_TOL:
            print(f'    rest pose is STALE by {drift:.5f} - the mesh is '
                  f'dragged; rebaseline_skin({rigname!r}) fixes it')
            ok = False
    return ok


def test_rigname_from_selection(rigname=DEFAULT_CHAIN):
    '''
    rigname_from_selection resolves the RIGPART from a selected joint.

    Read-only. Selects a mid-chain joint (and the end 'ee' joint) and checks
    both resolve to the rig part, and that an empty selection resolves to
    None. Restores the previous selection.
    '''
    joints = _chain_joints(rigname)
    if not joints:
        print(f'  test_rigname_from_selection: no chain for {rigname}, skipping')
        return None
    saved_sel = cmds.ls(selection=True) or []
    saved_parts = list(rt_constants.RIGPARTS)
    try:
        rt_constants.RIGPARTS = [rigname]
        ok = True

        mid = joints[len(joints) // 2]
        cmds.select(mid, replace=True)
        got = rt_setup.rigname_from_selection()
        ok &= _verdict('rigname_from_selection mid joint', got == rigname,
                       f'{mid} -> {got}')

        ee = rt_setup._find_end_joint(joints[-1])
        if ee:
            cmds.select(ee, replace=True)
            got_ee = rt_setup.rigname_from_selection()
            ok &= _verdict('rigname_from_selection end joint', got_ee == rigname,
                           f'{ee} -> {got_ee}')

        cmds.select(clear=True)
        ok &= _verdict('rigname_from_selection empty',
                       rt_setup.rigname_from_selection() is None)
        return ok
    finally:
        rt_constants.RIGPARTS = saved_parts
        if saved_sel:
            cmds.select(saved_sel, replace=True)
        else:
            cmds.select(clear=True)


# RUNNERS ==============================================================

def run_math():
    '''Run every safe geometry-helper test and print a summary.'''
    tests = [test_reflect, test_assign_rows, test_roll_about,
             test_aim_frames, test_up_mode, test_mirror_frames,
             test_find_mirror_pairs, test_implied_mirror_pairs,
             test_mirror_index, test_swap_side]
    results = []
    for fn in tests:
        print(f'\n--- {fn.__name__} ---')
        try:
            results.append((fn.__name__, fn()))
        except Exception as exc:
            results.append((fn.__name__, exc))
    return _summary('SETUP MATH TESTS', results)


def run_scene(base=DEFAULT_PAIR_BASE, chain=DEFAULT_CHAIN):
    '''
    Run every MUTATING scene test on the loaded skeleton and print a summary.

    Re-orients, mirrors and rolls real joints: like a real Setup run it
    detaches offsetParentMatrix drivers, re-baselines (or unbinds) geometry
    and clears the rest pose. RELOAD THE SCENE afterwards before building.
    '''
    print('\n*** run_scene MUTATES the skeleton (re-baselines or unbinds '
          'geometry, detaches OPM). Reload the scene before building. ***')
    tests = [
        (f'test_orient({chain})', lambda: test_orient(chain)),
        (f'test_end_joint({chain})', lambda: test_end_joint(chain)),
        (f'test_skin_rebaseline({chain})',
         lambda: test_skin_rebaseline(chain)),
        (f'test_mirror_orient({base})', lambda: test_mirror_orient(base)),
        (f'test_mirror_joints({base})', lambda: test_mirror_joints(base)),
        (f'test_roll({chain})', lambda: test_roll(chain)),
        (f'test_rigname_from_selection({chain})',
         lambda: test_rigname_from_selection(chain)),
    ]
    results = []
    for name, fn in tests:
        print(f'\n--- {name} ---')
        try:
            results.append((name, fn()))
        except Exception as exc:
            results.append((name, exc))
    return _summary('SETUP SCENE TESTS (mutating)', results)


def check_mirror(base=DEFAULT_PAIR_BASE):
    '''
    Focused answer to "is mirroring correct?": the mirror MATH test plus one
    real L/R pair (orientation and positions) on the loaded scene. Mutating
    (the scene part) - reload afterwards.
    '''
    results = [
        ('test_mirror_frames (math)', _safe(test_mirror_frames)),
        # Both behaviors on the real pair. Each run re-mirrors from the
        # untouched source side, so running them back to back is safe and
        # each verdict stands on its own.
        (f'test_mirror_orient({base}, symmetric)',
         _safe(lambda: test_mirror_orient(base, 'symmetric'))),
        (f'test_mirror_orient({base}, parallel)',
         _safe(lambda: test_mirror_orient(base, 'parallel'))),
        (f'test_mirror_joints({base})', _safe(lambda: test_mirror_joints(base))),
    ]
    return _summary(f'MIRROR CHECK ({base})', results)


def run_all():
    '''
    Run the safe math tests and point to the mutating scene tests.

    Scene tests are not run here because they mutate the skeleton; call
    run_scene() (or check_mirror()) on the sample scene when ready.
    '''
    ok = run_math()
    print('Scene tests are MUTATING and not run by run_all(). On the sample '
          'scene call:\n    rig_tail_setup_test.run_scene()      # all features'
          '\n    rig_tail_setup_test.check_mirror()    # mirror only\n')
    return ok


def _safe(fn):
    ''' Call fn, returning its bool/None or the caught Exception. '''
    try:
        return fn()
    except Exception as exc:
        return exc
