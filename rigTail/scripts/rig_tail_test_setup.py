'''
rig_tail_test_setup.py
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
    offsetParentMatrix drivers, unbind geometry and clear the rest pose, so
    RELOAD THE SCENE afterwards before building. Each scene test saves and
    restores RIGPARTS and the Setup flags so your config is left untouched.
        run_scene()

Designed for the sample scene squid_rig.0013.autorigger.ma (a full squid
build). Its L/R pairs (fintail, sidetail, tail1..3) and center chains
(C_tail, C_fintail) are the defaults below; pass your own names on another
rig.

Usage:
    import rig_tail_test_setup as rt_ts
    rt_ts.run_math()                 # safe: geometry-helper unit tests
    rt_ts.check_mirror('fintail')    # mirror math + one real L/R pair
    rt_ts.run_scene()                # MUTATING: every feature on the scene
    rt_ts.run_all()                  # run_math() + guidance for run_scene()

    # individual (scene tests mutate; reload after):
    rt_ts.test_mirror_orient('fintail')
    rt_ts.test_mirror_joints('fintail')
    rt_ts.test_orient('C_tail')
    rt_ts.test_roll('C_tail', 90)
    rt_ts.test_rigname_from_selection('C_tail')

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
    test_mirror_frames: mirror reflects aim/up and flips to the far side
    test_find_mirror_pairs: L/R pairing honours the source side
  Scene tests (MUTATING)
    test_orient: ORIENT_JOINTS leaves valid frames aimed down the chain
    test_mirror_orient: MIRROR_ORIENT makes the sides mirror orientations
    test_mirror_joints: MIRROR_JOINTS makes the sides mirror positions
    test_roll: roll_chain keeps positions and aim, rotates up by the angle
    test_rigname_from_selection: a selected joint resolves to its RIGPART
'''
import math

import maya.cmds as cmds

import rig_tail_constants as rt_cst
import rig_tail_cleanup as rt_cln
import rig_tail_setup as rt_set


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
    a, b = rt_set._norm(a), rt_set._norm(b)
    if rt_set._length(a) < 1e-9 or rt_set._length(b) < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, rt_set._dot(a, b)))))


def _orthonormal(rows):
    ''' True when the three rows are unit length and mutually perpendicular. '''
    for r in rows:
        if abs(rt_set._length(r) - 1.0) > 1e-4:
            return False
    return (abs(rt_set._dot(rows[0], rows[1])) < 1e-4 and
            abs(rt_set._dot(rows[1], rows[2])) < 1e-4 and
            abs(rt_set._dot(rows[0], rows[2])) < 1e-4)


def _right_handed(rows):
    ''' True when X cross Y points along +Z (a proper rotation, det +1). '''
    return rt_set._dot(rt_set._cross(rows[0], rows[1]), rows[2]) > 0.999


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
        got = rt_set._reflect(vec, keep)
        good = got == want
        ok &= _verdict(f'reflect keep={keep}', good, f'{vec} -> {got}')
    return ok


def test_assign_rows():
    '''_assign_rows places aim/up and builds a right-handed orthonormal frame.'''
    aim = rt_set._norm([1, 0, 0])
    up = rt_set._norm([0, 0, 1])
    rows = rt_set._assign_rows(aim, up, 'x', 'z')
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
        got = rt_set._roll_about(up, aim, deg)
        good = _ang(got, want) < ANG_TOL and abs(rt_set._length(got) - 1) < 1e-4
        ok &= _verdict(f'roll_about {deg:>4}', good,
                       f'-> {[round(v, 3) for v in got]}')
    # A roll must not move the aim axis (rolling the up around it).
    same = _ang(rt_set._roll_about(aim, aim, 37.0), aim) < ANG_TOL
    ok &= _verdict('roll_about keeps aim fixed', same)
    return ok


def test_aim_frames():
    '''aim_frames: orthonormal, right-handed, aim down-chain, twist-free.'''
    # A chain bending in the Z=0 plane (so the plane normal is world Z).
    pts = [[0, 0, 0], [1, 0.2, 0], [2, 0.5, 0], [3, 0.9, 0], [4, 1.4, 0]]
    frames = rt_set.aim_frames(pts, 'x', 'z')
    segs = [rt_set._norm(rt_set._sub(pts[i + 1], pts[i]))
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


def test_mirror_frames():
    '''
    mirror_frames reflects a source orientation across the plane.

    The defining property: the mirrored aim and up are the reflections of
    the source aim and up (so reflecting the result back recovers the
    source), and the frame stays orthonormal and right-handed. This is the
    core "is the mirror correct?" check, in isolation from the scene.
    '''
    keep = _AX['x']
    # A source frame aiming outward on +X, up +Z.
    aim = rt_set._norm([0.8, 0.6, 0.0])
    up = rt_set._norm([0.0, 0.0, 1.0])
    src_rows = rt_set._assign_rows(aim, up, 'x', 'z')
    m = [src_rows[0][0], src_rows[0][1], src_rows[0][2], 0,
         src_rows[1][0], src_rows[1][1], src_rows[1][2], 0,
         src_rows[2][0], src_rows[2][1], src_rows[2][2], 0,
         0, 0, 0, 1]
    frames = rt_set.mirror_frames([m], 'x', 'x', 'z')
    tgt = frames[0]
    ok = True
    ok &= _verdict('mirror_frames orthonormal', _orthonormal(tgt))
    ok &= _verdict('mirror_frames right-handed', _right_handed(tgt))
    # Aim reflected: target aim == reflect(source aim); reflecting back = source.
    aim_ok = _ang(tgt[0], rt_set._reflect(src_rows[0], keep)) < ANG_TOL
    ok &= _verdict('mirror_frames aim reflected', aim_ok,
                   f'target aim={[round(v, 3) for v in tgt[0]]}')
    up_ok = _ang(tgt[2], rt_set._reflect(src_rows[2], keep)) < ANG_TOL
    ok &= _verdict('mirror_frames up reflected', up_ok,
                   f'target up={[round(v, 3) for v in tgt[2]]}')
    # The fix this reworked: the mirrored aim must point to the OPPOSITE
    # side (its X component flips sign), not the same way as the source.
    outward = (tgt[0][0] * src_rows[0][0]) < 0
    ok &= _verdict('mirror_frames aim flips side', outward,
                   f'src X={src_rows[0][0]:.3f}, tgt X={tgt[0][0]:.3f}')
    return ok


def test_find_mirror_pairs():
    '''find_mirror_pairs pairs L/R by prefix and honours the source side.'''
    saved_parts = list(rt_cst.RIGPARTS)
    saved_side = getattr(rt_cst, 'MIRROR_SOURCE_SIDE', 'R')
    try:
        rt_cst.RIGPARTS = ['R_fintail', 'L_fintail', 'C_tail',
                           'L_sidetail', 'R_sidetail', 'L_wing']
        rt_cst.MIRROR_SOURCE_SIDE = 'R'
        pairs, paired = rt_set.find_mirror_pairs(rt_cst.RIGPARTS)
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
        rt_cst.RIGPARTS = saved_parts
        rt_cst.MIRROR_SOURCE_SIDE = saved_side


# SCENE HELPERS ========================================================

_FLAGS = ('ORIENT_JOINTS', 'MIRROR_ORIENT', 'MIRROR_JOINTS', 'MIRROR_DRYRUN')


def _run_setup_on(parts, orient=False, mir_orient=False, mir_joints=False):
    '''
    Run the real Setup pipeline (setup_tails) on just `parts`, then restore
    RIGPARTS and the Setup flags. Mutates the skeleton like a real run.
    '''
    saved_parts = list(rt_cst.RIGPARTS)
    saved_flags = {n: getattr(rt_cst, n, None) for n in _FLAGS}
    try:
        rt_cst.RIGPARTS = list(parts)
        rt_cst.ORIENT_JOINTS = orient
        rt_cst.MIRROR_ORIENT = mir_orient
        rt_cst.MIRROR_JOINTS = mir_joints
        rt_cst.MIRROR_DRYRUN = False
        return rt_set.setup_tails(root=None, dry_run=False)
    finally:
        rt_cst.RIGPARTS = saved_parts
        for n, v in saved_flags.items():
            if v is not None:
                setattr(rt_cst, n, v)


def _pair_joints(base):
    '''
    (source_rigname, target_rigname, source_joints, target_joints) for an
    L/R base, or None when the pair or its BN joints are missing. Detects
    BN joints for the pair first.
    '''
    saved_parts = list(rt_cst.RIGPARTS)
    saved_side = getattr(rt_cst, 'MIRROR_SOURCE_SIDE', 'R')
    try:
        parts = [f'{saved_side}_{base}',
                 f'{"L" if saved_side == "R" else "R"}_{base}']
        rt_cst.RIGPARTS = parts
        pairs, _ = rt_set.find_mirror_pairs(parts)
        if not pairs:
            return None
        source, target = pairs[0]
        rt_cln.detect_joints_bn()
        src = rt_cst.JOINTS_BN.get(source)
        tgt = rt_cst.JOINTS_BN.get(target)
        if not src or not tgt:
            return None
        return source, target, list(src), list(tgt)
    finally:
        rt_cst.RIGPARTS = saved_parts
        rt_cst.MIRROR_SOURCE_SIDE = saved_side


def _chain_joints(rigname):
    ''' BN joints for a single chain, or None. Detects first. '''
    saved_parts = list(rt_cst.RIGPARTS)
    try:
        rt_cst.RIGPARTS = [rigname]
        rt_cln.detect_joints_bn()
        joints = rt_cst.JOINTS_BN.get(rigname)
        return list(joints) if joints else None
    finally:
        rt_cst.RIGPARTS = saved_parts


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

    aim_axis = rt_set._cst('ORIENT_AIM_AXIS')
    ai = _AX.get(aim_axis, 0)
    _run_setup_on([rigname], orient=True)

    positions = [cmds.xform(j, q=True, ws=True, translation=True) for j in joints]
    segs = [rt_set._norm(rt_set._sub(positions[i + 1], positions[i]))
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


def test_mirror_orient(base=DEFAULT_PAIR_BASE):
    '''
    Mirror one L/R pair's ORIENTATION (MIRROR_ORIENT) and verify symmetry.

    Invariant after the mirror: reflecting the target side's aim and up axes
    across the symmetry plane recovers the source side's - i.e. the two
    sides are true mirror orientations. Also checks the target stays a valid
    right-handed frame and its positions did not move.
    '''
    info = _pair_joints(base)
    if not info:
        print(f'  test_mirror_orient: no L/R pair for "{base}", skipping')
        return None
    source, target, src, tgt = info
    keep = _AX.get(rt_set._cst('MIRROR_AXIS'), 0)
    ai = _AX.get(rt_set._cst('ORIENT_AIM_AXIS'), 0)
    ui = _AX.get(rt_set._cst('ORIENT_UP_AXIS'), 2)

    tgt_pos_before = [cmds.xform(j, q=True, ws=True, translation=True) for j in tgt]
    _run_setup_on([source, target], mir_orient=True)

    n = min(len(src), len(tgt))
    aim_err = up_err = 0.0
    rh = True
    for i in range(n):
        sm = _rows(cmds.xform(src[i], q=True, ws=True, matrix=True))
        tm = _rows(cmds.xform(tgt[i], q=True, ws=True, matrix=True))
        rh &= _right_handed(tm)
        # reflect(target axis) should equal source axis
        aim_err = max(aim_err, _ang(rt_set._reflect(tm[ai], keep), sm[ai]))
        up_err = max(up_err, _ang(rt_set._reflect(tm[ui], keep), sm[ui]))

    tgt_pos_after = [cmds.xform(j, q=True, ws=True, translation=True) for j in tgt]
    moved = max(math.dist(a, b)
                for a, b in zip(tgt_pos_before, tgt_pos_after))

    ok = True
    ok &= _verdict(f'mirror_orient {source}->{target} aim symmetric',
                   aim_err < ANG_TOL, f'max aim err={aim_err:.3f} deg')
    ok &= _verdict(f'mirror_orient {source}->{target} up symmetric',
                   up_err < ANG_TOL, f'max up err={up_err:.3f} deg')
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
    keep = _AX.get(rt_set._cst('MIRROR_AXIS'), 0)

    _run_setup_on([source, target], mir_joints=True)

    n = min(len(src), len(tgt))
    pos_err = 0.0
    for i in range(n):
        sp = cmds.xform(src[i], q=True, ws=True, translation=True)
        tp = cmds.xform(tgt[i], q=True, ws=True, translation=True)
        pos_err = max(pos_err, math.dist(rt_set._reflect(tp, keep), sp))

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
    ai = _AX.get(rt_set._cst('ORIENT_AIM_AXIS'), 0)
    ui = _AX.get(rt_set._cst('ORIENT_UP_AXIS'), 2)

    before = [cmds.xform(j, q=True, ws=True, matrix=True) for j in joints]
    rt_set.roll_chain(rigname, angle)
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
    rt_set.roll_chain(rigname, -angle)
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
    saved_parts = list(rt_cst.RIGPARTS)
    try:
        rt_cst.RIGPARTS = [rigname]
        ok = True

        mid = joints[len(joints) // 2]
        cmds.select(mid, replace=True)
        got = rt_set.rigname_from_selection()
        ok &= _verdict('rigname_from_selection mid joint', got == rigname,
                       f'{mid} -> {got}')

        ee = rt_set._find_end_joint(joints[-1])
        if ee:
            cmds.select(ee, replace=True)
            got_ee = rt_set.rigname_from_selection()
            ok &= _verdict('rigname_from_selection end joint', got_ee == rigname,
                           f'{ee} -> {got_ee}')

        cmds.select(clear=True)
        ok &= _verdict('rigname_from_selection empty',
                       rt_set.rigname_from_selection() is None)
        return ok
    finally:
        rt_cst.RIGPARTS = saved_parts
        if saved_sel:
            cmds.select(saved_sel, replace=True)
        else:
            cmds.select(clear=True)


# RUNNERS ==============================================================

def run_math():
    '''Run every safe geometry-helper test and print a summary.'''
    tests = [test_reflect, test_assign_rows, test_roll_about,
             test_aim_frames, test_mirror_frames, test_find_mirror_pairs]
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
    detaches offsetParentMatrix drivers, unbinds geometry and clears the
    rest pose. RELOAD THE SCENE afterwards before building.
    '''
    print('\n*** run_scene MUTATES the skeleton (unbinds geometry, detaches '
          'OPM). Reload the scene before building. ***')
    tests = [
        (f'test_orient({chain})', lambda: test_orient(chain)),
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
        (f'test_mirror_orient({base})', _safe(lambda: test_mirror_orient(base))),
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
          'scene call:\n    rig_tail_test_setup.run_scene()      # all features'
          '\n    rig_tail_test_setup.check_mirror()    # mirror only\n')
    return ok


def _safe(fn):
    ''' Call fn, returning its bool/None or the caught Exception. '''
    try:
        return fn()
    except Exception as exc:
        return exc
