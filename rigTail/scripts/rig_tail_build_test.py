'''
# rig_tail_build_test.py
author: Daisy Jane @gnitemouse

Diagnostics for the matrix-based FX rig (per-FX composeMatrix + OPM
architecture). Read-only test_*/check_* functions validate a built rig;
fix_* helpers mutate and are opt-in.

Ships with the rigTail module, alongside the rig_tail_* scripts it
diagnoses. Keeping it here means there is one copy on sys.path: a second
copy in ~/Documents/maya/scripts shadows this one, because Maya adds the
user script directories ahead of module script directories.

Usage:
    import rig_tail_build_test as rt_build_test
    rt_build_test.run_all()               # every read-only check + PASS/FAIL summary
    rt_build_test.test_matrix()           # FX matrix / OPM node wiring
    rt_build_test.test_local_trs()        # BN joints have identity local TRS
    rt_build_test.test_fx_order()         # FX multiplies before baseLocal
    rt_build_test.test_alignment()        # BN vs IK/FK world-position alignment
    rt_build_test.test_matrix_opm()       # offsetParentMatrix parent-space math
    rt_build_test.show_data_flow()        # per-joint data-flow diagram
    rt_build_test.test_wave() / rt_build_test.test_curl()
    rt_build_test.test_time_evaluation()  # time-varying FX across frames
    rt_build_test.test_twist_roll_offset('C_fintail')        # twist/roll/offset in FK and IK (MUTATES)
    rt_build_test.test_stretch('C_fintail')                  # Stretch lengthens, equally in FK and IK (MUTATES)
    rt_build_test.test_override_routing('C_fintail')         # Override All gates the IKFK mode (MUTATES)
    rt_build_test.test_solver_curve_shape('L_sidetail')      # bending does not S-curve the solver curve (MUTATES)
    rt_build_test.report_bend('C_fintail')                   # current bend, read-only (manual before/after)
    rt_build_test.measure_rebuild_degradation('C_fintail')   # curvature loss across rebuilds (MUTATES)
    rt_build_test.test_build_exclusion('C_tail')             # Excluded part survives a rebuild (MUTATES)
    rt_build_test.test_remove_rig()                          # Remove Rig leaves a clean scene (MUTATES)
    rt_build_test.profile_build()                            # which Maya command the build time goes to (MUTATES)
'''
import contextlib
import math
import re
import sys
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_anim as rt_anim
import rig_tail_cleanup as rt_cleanup
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_mirror as rt_mirror


# STAGE PROBE ================================================
#
# Find the build stage where a joint chain loses its shape.
#
# 'bend' is the total angle the chain turns through, summed over every
# pair of consecutive bones. A chain that follows a curve has a bend of
# tens of degrees; a chain that has been flattened into a straight line
# reads 0. Print it at each stage of a build and the stage where the
# number collapses is the stage that broke it.
#
# The per-joint lines then say WHERE the shape is currently held -
# jointOrient, rotate, or offsetParentMatrix - so a stage that zeroes one
# without baking it into another is visible directly.
#
# Read-only, and every call is wrapped so a probe can never break a build.

IDENTITY_MTX = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def chain_bend(joints):
    '''
    Total angle (degrees) a joint chain turns through.

    Arguments:
        joints (list): Joint chain, base to tip

    Return:
        tuple: (total bend, largest single bend, number of bones measured)
    '''
    pts = [cmds.xform(j, q=1, ws=1, t=1) for j in joints if cmds.objExists(j)]
    vecs = []
    for i in range(len(pts) - 1):
        v = [pts[i + 1][k] - pts[i][k] for k in range(3)]
        length = math.sqrt(sum(c * c for c in v))
        if length > 1e-9:                    # skip coincident joints
            vecs.append([c / length for c in v])
    angles = []
    for i in range(len(vecs) - 1):
        dot = sum(vecs[i][k] * vecs[i + 1][k] for k in range(3))
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
    return sum(angles), (max(angles) if angles else 0.0), len(vecs)


def probe(stage='', rigname=None, count=4):
    '''
    Print the shape of every rig part's joint chains at one build stage.

    Call at several points in a build and compare the bend column across
    stages. Example, with the FK chain collapsing during the build phase:

        [PROBE] after set_joints   C_fintail  BN bend=29.3  FK bend=29.3  IK bend=29.3
        [PROBE] after build        C_fintail  BN bend=29.3  FK bend= 0.0  IK bend=29.3

    Arguments:
        stage (str): Label for this call site, printed on every line
        rigname (str): Single rig part, or None for all of RIGPARTS
        count (int): How many joints to detail per chain (0 for none)
    '''
    try:
        parts = [rigname] if rigname else list(rt_constants.RIGPARTS)
        for part in parts:
            chains = [('BN', rt_constants.JOINTS_BN.get(part, [])),
                      ('FK', rt_constants.JOINTS_FK.get(part, [])),
                      ('IK', rt_constants.JOINTS_IK.get(part, []))]
            summary = []
            for label, joints in chains:
                if not joints:
                    summary.append(f'{label} -')
                    continue
                total, worst, bones = chain_bend(joints)
                summary.append(f'{label} bend={total:6.1f} (max {worst:5.1f}, {bones} bones)')
            print(f"[PROBE] {stage:<24} {part:<12} {'  '.join(summary)}")

            if not count:
                continue
            for label, joints in chains:
                if label == 'BN':
                    continue          # BN is the reference, detail the drivers
                for jnt in joints[:count]:
                    print(f'[PROBE]     {probe_joint(jnt)}')
    except Exception as exc:              # a probe must never break a build
        print(f'[PROBE] {stage}: probe failed, {exc!r}')


def probe_joint(jnt):
    '''
    One-line description of where a joint's transform is currently held.

    Arguments:
        jnt (str): Joint name

    Return:
        str: Joint name with jointOrient, rotate, translate, whether its
            offsetParentMatrix is identity, and its parent
    '''
    if not cmds.objExists(jnt):
        return f'{jnt}  MISSING'

    def fmt(values):
        return '(' + ', '.join(f'{v:7.2f}' for v in values) + ')'

    jo = (cmds.getAttr(f'{jnt}.jointOrient')[0]
          if cmds.attributeQuery('jointOrient', node=jnt, exists=True)
          else (0.0, 0.0, 0.0))
    rot = cmds.getAttr(f'{jnt}.rotate')[0]
    tr = cmds.getAttr(f'{jnt}.translate')[0]
    opm = cmds.getAttr(f'{jnt}.offsetParentMatrix')
    opm_state = ('identity'
                 if all(abs(a - b) < 1e-6 for a, b in zip(opm, IDENTITY_MTX))
                 else 'SET')
    parent = cmds.listRelatives(jnt, p=True) or ['(world)']
    return (f'{jnt:<26} jo={fmt(jo)} r={fmt(rot)} t={fmt(tr)} '
            f'opm={opm_state:<8} parent={parent[0]}')


# REBUILD DEGRADATION ========================================
#
# Quantify how much curvature a chain loses across repeated rebuilds.
# Curved chains (the L/R/C fintails on the squid) flatten a little on every
# rebuild; straight chains do not, so measure the fintails. See the memory
# note 'ikfk-rebuild-degradation' for the root cause.
#
# Unlike the read-only test_*/check_* helpers, this MUTATES the scene: it
# rebuilds the rig. Keep it out of run_all().


def _chain_from_scene(rigname, typ):
    '''
    Locate a rig part's joint chain in the scene by naming convention, base to
    tip, excluding the _ee_ tip.

    Reads the scene directly (cmds.ls) instead of the rt_constants.JOINTS_* session
    cache, so it works on a freshly loaded build: the cache is empty until a
    build runs in the current Python session, which is why the first version of
    this test reported "nothing measured" on a just-opened scene.

    Arguments:
        rigname (str): Rig part name
        typ (str): Joint type prefix (rt_constants.TYPE_BN / _FK / _IK)

    Return:
        list: Joint names, base to tip, ee excluded (empty if none found)
    '''
    pattern = f'{typ}_{rigname}_*_{rt_constants.JNT}'
    indexed = []
    for j in cmds.ls(pattern, type='joint') or []:
        leaf = j.split('|')[-1]
        # Exact rigname match so 'C_fintail' never grabs 'C_fintail2' joints
        if rt_naming.get_rigname(leaf, rt_constants.JOINT) != rigname:
            continue
        NN = rt_naming.get_index_from_name(leaf)
        if NN == 'ee' or NN is None:
            continue
        indexed.append((NN, j))
    indexed.sort(key=lambda pair: pair[0])
    return [j for _, j in indexed]


def _bend_snapshot(parts):
    '''Total bend (deg) of every part's BN/FK/IK chain, keyed [part][label].

    Resolves joints from the scene (see _chain_from_scene), independent of the
    session cache. A chain with fewer than two joints records None so it is
    skipped rather than counted as 0.
    '''
    types = {'BN': rt_constants.TYPE_BN, 'FK': rt_constants.TYPE_FK, 'IK': rt_constants.TYPE_IK}
    snap = {}
    for part in parts:
        snap[part] = {}
        for label, typ in types.items():
            joints = _chain_from_scene(part, typ)
            snap[part][label] = chain_bend(joints)[0] if len(joints) >= 2 else None
    return snap


def measure_rebuild_degradation(rignames, rebuilds=2, tol=1.0,
                                force_full=True, invalidate_cache=False,
                                scope=True, detail=True):
    '''
    Measure curvature loss across repeated rebuilds.

    Records chain_bend (total turn angle) of each part's BN, FK and IK chains
    BEFORE any rebuild, then AFTER each of `rebuilds` consecutive rebuilds, and
    prints the per-chain sequence with its drift from the baseline. A rig that
    reproduces its shape holds each number steady; a degrading rig shows the
    bend fall on every rebuild. BN is the one that matters most -- it is the
    source pose; if BN drops, the rig cannot be reproduced (see the memory note
    'ikfk-rebuild-degradation').

    Works on a freshly loaded build: chains are read from the scene, not the
    session cache. MUTATES the scene: rebuilds the rig via
    rig_tail.rig_tail_multiple, as the UI Build button does.

    Knobs:
      scope=True  -- rebuild ONLY the measured parts (RIGPARTS is narrowed to
          `rignames` for the duration, then restored). This both makes the
          rebuild actually cover the parts on a fresh scene and keeps it fast
          -- rebuilding one fintail instead of all twelve tails. The existing
          root group is detected and reused so no hierarchy is renamed.
      force_full=True  -- force the full-cleanup path (FORCE_REBUILD). The light
          path reuses the IK curve, so it does NOT resample and will not show
          curve-side degradation.
      invalidate_cache=True -- clear the in-memory joint caches for these parts
          before each rebuild, so set_joints re-duplicates FK/IK from the
          (already OPM-smoothed) BN, the way a fresh Maya session does -- the
          compounding path the memory note identifies.

    FORCE_REBUILD, RIGPARTS and ROOT are saved and restored on exit.

    Usage:
        import rig_tail_build_test as rt_build_test
        # scoped to one fintail, 2 rebuilds, full-cleanup path:
        rt_build_test.measure_rebuild_degradation('C_fintail')
        # fresh-session re-duplication path (re-duplicates FK/IK from BN):
        rt_build_test.measure_rebuild_degradation('C_fintail', invalidate_cache=True)
        # measure several parts at once:
        rt_build_test.measure_rebuild_degradation(['C_fintail', 'L_fintail'])

        REOPEN the scene between runs. The test degrades the part it measures,
        so a second run starts from the first run's degraded end-state, not the
        original rest pose -- baselines will not be comparable otherwise.

    Arguments:
        rignames (str or list): Part(s) to measure. Required.
        rebuilds (int): Rebuilds to run after the baseline capture. Needs >= 2
            to measure consistency (the spread among post-first-build states).
        tol (float): Max allowed spread (deg) of a chain's bend ACROSS rebuilds
            (after the first build) before it is reported FAIL. The one-time
            first-build smoothing of the raw pose is reported separately and
            does not count against this.
        force_full (bool): Force the full-rebuild cleanup path.
        invalidate_cache (bool): Clear joint caches before each rebuild.
        scope (bool): Narrow RIGPARTS to `rignames` while rebuilding.
        detail (bool): Print the per-rebuild sequence, not only PASS/FAIL.

    Return:
        bool: True if every measured chain stayed within tol across all
            rebuilds; False if any drifted past tol. None if nothing measured.
    '''
    import rig_tail            # lazy import: both modules are loaded by call time
    import rig_tail_cleanup as rt_cleanup

    parts = [rignames] if isinstance(rignames, str) else list(rignames or [])
    if not parts:
        print('[DEGRADE] No rig parts given to measure')
        return None

    history = [_bend_snapshot(parts)]   # index 0 = baseline (pre-rebuild)

    # Reuse the scene's existing root group so a scoped rebuild does not rename
    # the hierarchy (set_root would otherwise rename whatever root it finds to
    # the ROOT template name).
    existing_root = rt_cleanup.find_existing_root_grp()
    root_arg = existing_root if existing_root else rt_constants.ROOT

    saved_force = rt_constants.FORCE_REBUILD
    saved_rigparts = list(rt_constants.RIGPARTS)
    saved_root = rt_constants.ROOT
    try:
        if force_full:
            rt_constants.FORCE_REBUILD = True
        if scope:
            rt_constants.RIGPARTS = list(parts)
        for _ in range(rebuilds):
            if invalidate_cache:
                for jdict in (rt_constants.JOINTS_BN, rt_constants.JOINTS_FK,
                              rt_constants.JOINTS_IK, rt_constants.JOINTS_FX):
                    for part in parts:
                        jdict.pop(part, None)
            rig_tail.rig_tail_multiple(root=root_arg,
                                       fk=rt_constants.BUILD_FK,
                                       ik=rt_constants.BUILD_IK)
            history.append(_bend_snapshot(parts))
    finally:
        rt_constants.FORCE_REBUILD = saved_force
        rt_constants.RIGPARTS = saved_rigparts
        rt_constants.ROOT = saved_root

    # Report
    print('\n' + '=' * 72)
    print(f'  REBUILD DEGRADATION  ({rebuilds} rebuilds, tol {tol} deg, '
          f'full={force_full}, invalidate_cache={invalidate_cache}, '
          f'scope={scope})')
    print('=' * 72)
    print('  settle = drift ACROSS rebuilds (the degradation metric -- pass/fail)')
    print('  1st-build = one-time smoothing from the raw pose (accepted, informational)')

    ok = True
    measured_any = False
    for part in parts:
        printed_part = False
        for label in ('BN', 'FK', 'IK'):
            series = [h[part][label] for h in history]
            if all(v is None for v in series):
                continue
            measured_any = True
            baseline = series[0]
            # Consistency is measured AFTER the first rebuild: the first build
            # applies an accepted one-time smoothing of the raw pose, so drift
            # from the raw baseline is not degradation. Degradation is when
            # rebuilds keep changing -- the spread among the post-first-build
            # snapshots. That is the pass/fail metric.
            built = [v for v in series[1:] if v is not None]
            if built:
                ref = built[0]
                settle = max(abs(v - ref) for v in built)
            else:
                ref, settle = None, 0.0
            first_build = (ref - baseline
                           if ref is not None and baseline is not None else None)
            status = 'OK  ' if settle <= tol else 'FAIL'
            if settle > tol:
                ok = False
            if not printed_part:
                print(f'\n  {part}')
                printed_part = True
            cells = ' -> '.join('   -  ' if v is None else f'{v:6.1f}'
                                for v in series)
            fb = '' if first_build is None else f', 1st-build {first_build:+.1f}'
            print(f'    {label}  {status}  bend: {cells}   '
                  f'(settle {settle:.1f}{fb})')

    print('\n' + '=' * 72)
    if not measured_any:
        print(f'  RESULT: nothing measured -- no BN/FK/IK joints found in the '
              f'scene for {parts}.')
        print(f'          Check the part name(s) against the joint names, e.g. '
              f'BN_<name>_00_jnt.')
        print('=' * 72 + '\n')
        return None
    if ok:
        print(f'  RESULT: CONSISTENT across rebuilds (settle <= {tol} deg)')
    else:
        print(f'  RESULT: DEGRADING -- a chain keeps changing across rebuilds '
              f'(settle > {tol} deg)')
    print('=' * 72 + '\n')
    return ok


def report_bend(rignames):
    '''
    Print each part's current BN/FK/IK total bend, read from the scene.

    Read-only, no rebuild -- the cheapest way to measure degradation: call it,
    run your own Build (UI or rig_tail_multiple), then call it again and compare
    the BN row. That is one build instead of the N that measure_rebuild_
    degradation runs.

    Usage:
        import rig_tail_build_test as rt_build_test
        rt_build_test.report_bend('C_fintail')   # before
        # ... press Build once (UI), or rig_tail_multiple(...) ...
        rt_build_test.report_bend('C_fintail')   # after; compare the BN number

    Arguments:
        rignames (str or list): Part(s) to report.

    Return:
        dict: {part: {'BN'/'FK'/'IK': bend or None}}
    '''
    parts = [rignames] if isinstance(rignames, str) else list(rignames)
    snap = _bend_snapshot(parts)
    for part in parts:
        cells = '  '.join(
            f'{lbl}={"   -  " if snap[part][lbl] is None else f"{snap[part][lbl]:6.1f}"}'
            for lbl in ('BN', 'FK', 'IK'))
        print(f'[BEND] {part:<16} {cells}')
    return snap


def test_build_exclusion(rigname, root=None):
    '''
    An Excluded rig part survives a rebuild untouched (RIGPARTS_EXCLUDE).

    MUTATING: runs one full rebuild with `rigname` excluded, so run it on a
    scene you can reload. Everything else in RIGPARTS is rebuilt as usual;
    this only checks that the excluded part was left alone.

    Snapshots the part's nodes and its SDK animation curves, rebuilds, then
    verifies nothing of its own went missing. The SDK curves are the point
    of the check: cleanup_rig sweeps them in one scene-wide call, and
    cleanup.excluded_sdk_curves is what holds this part's back.

    RIGPARTS_EXCLUDE is restored afterwards whatever happens.

    Usage:
        import rig_tail_build_test as rt_build_test
        rt_build_test.test_build_exclusion('C_tail')

    Arguments:
        rigname (str): Part to exclude from the rebuild.
        root (str): Rig root; defaults to the scene's existing root group.

    Return:
        bool: True if the part came through the rebuild intact.
    '''
    import rig_tail as rig_tail
    import rig_tail_cleanup as rt_cleanup
    import rig_tail_cache as rt_cache

    if rigname not in rt_constants.RIGPARTS:
        print(f"[EXCLUDE] '{rigname}' is not in RIGPARTS")
        return False

    # Same whole-token rule cleanup uses to decide who owns a node
    token = re.compile(rf'(?<![A-Za-z0-9]){re.escape(rigname)}(?![A-Za-z0-9])')

    def _owned_nodes():
        return {n for n in cmds.ls() if token.search(n.split('|')[-1])}

    def _owned_curves():
        curves = cmds.ls(type=['animCurveUU', 'animCurveUL',
                               'animCurveUA', 'animCurveTT']) or []
        return set(rt_cleanup.excluded_sdk_curves(curves))

    saved_exclude = list(getattr(rt_constants, 'RIGPARTS_EXCLUDE', None) or [])
    existing_root = rt_cleanup.find_existing_root_grp()
    root_arg = root or existing_root or rt_constants.ROOT
    try:
        rt_constants.RIGPARTS_EXCLUDE = sorted(set(saved_exclude) | {rigname})
        before_nodes = _owned_nodes()
        before_curves = _owned_curves()
        built = rt_cache.active_parts()
        print(f'\n--- BUILD EXCLUSION ({rigname}) ---')
        print(f'  {len(before_nodes)} node(s), {len(before_curves)} SDK '
              f'curve(s) before; rebuilding {len(built)} other part(s)')
        if rigname in built:
            print('  FAIL: active_parts() still lists the excluded part')
            return False

        rig_tail.rig_tail_multiple(root=root_arg, fk=rt_constants.BUILD_FK,
                                   ik=rt_constants.BUILD_IK)

        lost_nodes = sorted(before_nodes - _owned_nodes())
        survived = set(cmds.ls(list(before_curves))) if before_curves else set()
        lost_curves = sorted(before_curves - survived)
    finally:
        rt_constants.RIGPARTS_EXCLUDE = saved_exclude

    ok = not lost_nodes and not lost_curves
    if lost_curves:
        print(f'  FAIL: {len(lost_curves)} SDK curve(s) deleted, e.g. '
              f'{", ".join(lost_curves[:5])}')
    if lost_nodes:
        print(f'  FAIL: {len(lost_nodes)} node(s) deleted, e.g. '
              f'{", ".join(lost_nodes[:5])}')
    if ok:
        print(f'  PASS: all {len(before_nodes)} node(s) and '
              f'{len(before_curves)} SDK curve(s) survived the rebuild')
    return ok


def test_remove_rig(tolerance=0.001):
    '''
    Remove Rig strips the rig and leaves the scene clean.

    MUTATING, and not undoable in any reliable way: it removes the rig in
    the current scene, so run it on a scene you can reload. It is the only
    way to check the teardown - the thing being verified is what the scene
    looks like afterwards.

    Checks, in the order they matter:
      1. the rig root group is gone
      2. every BN joint still exists, in the same world position and
         orientation it held while the rig drove it (this is the check that
         catches the skeleton collapsing: the build zeroes BN local TRS and
         poses through offsetParentMatrix, so a teardown that deletes the
         matrix network without baking the pose back flattens the chain)
      3. BN joints are plain joints again: identity offsetParentMatrix, no
         incoming connections, no constraints
      4. geometry survived and is still skinned, with the same influence
         count - the mesh must keep deforming
      5. nothing rig-shaped is left in the scene (rig_leftovers): no root,
         no FK/IK/FX-prefixed nodes, no orphaned SDK curves, condition
         nodes or FX expressions

    Usage:
        import rig_tail_build_test as rt_build_test
        rt_build_test.test_remove_rig()

    Arguments:
        tolerance (float): allowed world-position drift per joint, in
            scene units.

    Return:
        bool: True when every check passed.
    '''
    import rig_tail_cache as rt_cache
    import rig_tail_cleanup as rt_cleanup
    import rig_tail_maya as rt_maya

    # The included roster only: an excluded part is left built on purpose,
    # so it must NOT be checked for removal (see rt_cleanup.remove_rig)
    parts = rt_cache.active_parts()
    kept = [p for p in rt_constants.RIGPARTS if p not in parts]
    root_grp = rt_cleanup.find_existing_root_grp()
    print('\n--- REMOVE RIG ---')
    if not root_grp:
        print('  SKIP: no rig root group in this scene, nothing to remove')
        return False
    if not parts:
        print('  SKIP: every rig part is excluded, nothing to remove')
        return False

    # BEFORE: skeleton pose, and the skin on every mesh the parts own
    before_jnts = {}
    for part in parts:
        for jnt in rt_constants.JOINTS_BN.get(part, []):
            path = rt_cleanup.unique_path(jnt)
            if path:
                before_jnts[path.split('|')[-1]] = (
                    cmds.xform(path, q=1, ws=1, t=1),
                    cmds.xform(path, q=1, ws=1, ro=1))
    before_skin = {}
    for part in parts:
        for geo in rt_maya.find_geometry_for_rigname(part):
            skin = rt_maya.find_skincluster(geo)
            if skin:
                influences = cmds.skinCluster(skin, q=True, inf=True) or []
                before_skin[geo.split('|')[-1]] = len(influences)
    print(f'  before: {len(before_jnts)} BN joint(s), '
          f'{len(before_skin)} skinned mesh(es) under {len(parts)} '
          f'included part(s)'
          + (f'; {len(kept)} excluded part(s) must survive: '
             f'{", ".join(kept)}' if kept else ''))

    removed = rt_cleanup.remove_rig()

    fails = []
    if not removed:
        fails.append('remove_rig() returned False')

    # 1. Root group gone - unless parts were excluded, in which case their
    # rig is still built and the hierarchy has to stay standing for it
    if kept:
        if not cmds.objExists(root_grp):
            fails.append(f"rig root group '{root_grp}' was deleted, but "
                         f"{len(kept)} excluded part(s) are still built in "
                         f"it: {', '.join(kept)}")
        for part in kept:
            if not cmds.ls(f'*{part}*{rt_constants.CTRL}') :
                fails.append(f"excluded part '{part}' lost its controls")
    elif cmds.objExists(root_grp):
        fails.append(f"rig root group '{root_grp}' still exists")

    # 2 + 3. Skeleton kept, in place, and plain again
    moved, lost, driven = [], [], []
    for leaf, (pos, rot) in before_jnts.items():
        path = rt_cleanup.unique_path(leaf)
        if not path:
            lost.append(leaf)
            continue
        now_pos = cmds.xform(path, q=1, ws=1, t=1)
        now_rot = cmds.xform(path, q=1, ws=1, ro=1)
        drift = max(abs(a - b) for a, b in zip(pos, now_pos))
        # Orientation is compared as a direction, not raw euler values:
        # the same orientation has several euler representations, and the
        # teardown moves it from rotate into jointOrient
        spin = max(abs((a - b + 180) % 360 - 180) for a, b in zip(rot, now_rot))
        if drift > tolerance or spin > 0.1:
            moved.append(f'{leaf} (moved {drift:.3f}, turned {spin:.2f}deg)')
        incoming = cmds.listConnections(path, s=True, d=False) or []
        constraints = cmds.listRelatives(path, type='constraint') or []
        opm = cmds.getAttr(f'{path}.offsetParentMatrix')
        if incoming or constraints or \
                max(abs(a - b) for a, b in zip(opm, IDENTITY_MTX)) > 1e-6:
            driven.append(leaf)
    if lost:
        fails.append(f'{len(lost)} BN joint(s) deleted: '
                     f'{", ".join(lost[:5])}')
    if moved:
        fails.append(f'{len(moved)} BN joint(s) shifted: '
                     f'{", ".join(moved[:5])}')
    if driven:
        fails.append(f'{len(driven)} BN joint(s) still driven '
                     f'(connection, constraint or non-identity opm): '
                     f'{", ".join(driven[:5])}')

    # 4. Geometry survived, still skinned. The meshes were reparented out
    # of the deleted rig hierarchy, so they are found by leaf name now.
    unskinned = []
    for leaf, influences in before_skin.items():
        path = rt_cleanup.unique_path(leaf)
        if not path:
            unskinned.append(f'{leaf} (deleted)')
            continue
        skin = rt_maya.find_skincluster(path)
        if not skin:
            unskinned.append(f'{leaf} (skin gone)')
        else:
            now = len(cmds.skinCluster(skin, q=True, inf=True) or [])
            if now != influences:
                unskinned.append(f'{leaf} ({influences} -> {now} influences)')
    if unskinned:
        fails.append(f'{len(unskinned)} mesh(es) lost their bind: '
                     f'{", ".join(unskinned[:5])}')

    # 5. No strays
    leftovers = rt_cleanup.rig_leftovers(parts)
    for label, nodes in leftovers.items():
        if nodes:
            fails.append(f'{len(nodes)} {label} node(s) left behind: '
                         f'{", ".join(n.split("|")[-1] for n in nodes[:5])}')

    if fails:
        for line in fails:
            print(f'  FAIL: {line}')
    else:
        print(f'  PASS: rig removed; {len(before_jnts)} BN joint(s) kept in '
              f'place, {len(before_skin)} mesh(es) still skinned, no '
              f'leftover rig nodes')
    return not fails


# BUILD PROFILING ============================================
#
# Where the build's time actually goes, by Maya command.
#
# The phase timer (rt_maya.build_timer) says which phase is slow and the
# step timings say which step, but neither settles the question the numbers
# raise: is the build slow because it issues too many commands, or because
# a handful of commands are individually expensive? Those have opposite
# fixes - batching versus not calling the command at all - and the answer
# is different for cleanup, build and connect.
#
# So measure it. profile_cmds wraps maya.cmds for the duration of a call
# and reports, per command name: how many times it was called, how long
# those calls took in total, and the mean. The summary line compares the
# total time spent inside commands against wall-clock time, which is the
# actual test of the 'command overhead is the ceiling' hypothesis: if
# commands account for most of the wall time, batching is the only lever
# left; if they do not, the time is in the Python around them.


@contextlib.contextmanager
def profile_cmds(top=25, threshold=0.05, callers_for=()):
    '''
    Count and time every maya.cmds call made inside the block.

    Every callable in maya.cmds is temporarily replaced with a counting
    wrapper. Modules that did 'import maya.cmds as cmds' look the function
    up on the module object at call time, so they get the wrapper too
    without being reloaded. Everything is restored in a finally block.

    The wrapper itself costs roughly a microsecond per call - visible in the
    mean for trivial commands like objExists, negligible for the ones that
    matter. Do not read the totals as absolute build cost; read them
    against each other.

    A command's total says WHAT is expensive, never WHERE it is issued from,
    and the answer is rarely the obvious call site: 'ls' turned out to be
    one line inside break_connection, not the pattern scans in cleanup. Name
    commands in callers_for to get a per-call-site breakdown for them:

        rt_build_test.profile_build(callers_for=['ls', 'delete', 'setAttr'])

    Usage:
        with rt_build_test.profile_cmds():
            rig_tail.rig_tail_multiple(root='squid')

    Arguments:
        top (int): How many commands to list, slowest first.
        threshold (float): Also list any command whose mean call time
            exceeds this many milliseconds, however rarely it is called.
        callers_for (list): Commands to also break down by call site.
            Costs an extra frame lookup per call of those commands only.

    Yield:
        dict: name -> [calls, seconds], live during the block.
    '''
    stats = {}
    originals = {}
    callers = {}
    watched = set(callers_for)

    def wrap(name, func):
        watch = name in watched

        def profiled(*args, **kwargs):
            started = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                entry = stats.get(name)
                if entry is None:
                    stats[name] = [1, elapsed]
                else:
                    entry[0] += 1
                    entry[1] += elapsed
                if watch:
                    frame = sys._getframe(1)
                    path = frame.f_code.co_filename.replace('\\', '/')
                    site = (f'{path.rsplit("/", 1)[-1]}:{frame.f_lineno} '
                            f'{frame.f_code.co_name}')
                    seen = callers.setdefault(name, {})
                    hit = seen.get(site)
                    if hit is None:
                        seen[site] = [1, elapsed]
                    else:
                        hit[0] += 1
                        hit[1] += elapsed
        return profiled

    for name in dir(cmds):
        if name.startswith('_'):
            continue
        func = getattr(cmds, name)
        if callable(func):
            originals[name] = func
            setattr(cmds, name, wrap(name, func))

    wall = time.perf_counter()
    try:
        yield stats
    finally:
        for name, func in originals.items():
            setattr(cmds, name, func)
        wall = time.perf_counter() - wall
        report_cmds(stats, wall, top=top, threshold=threshold,
                    callers=callers)


def report_cmds(stats, wall, top=25, threshold=0.05, callers=None):
    '''
    Print a profile_cmds table: the slowest commands, then the ones with
    the worst mean call time, then the totals, then any per-call-site
    breakdown that was collected.

    Arguments:
        stats (dict): name -> [calls, seconds] from profile_cmds
        wall (float): Wall-clock seconds the profiled block took
        top (int): How many commands to list, slowest first
        threshold (float): Mean call time (ms) worth calling out
        callers (dict): name -> {call site: [calls, seconds]}
    '''
    calls = sum(c for c, _ in stats.values())
    in_cmds = sum(s for _, s in stats.values())
    print('\n' + '=' * 72)
    print(f'  MAYA COMMAND PROFILE  ({calls} calls, {in_cmds:.1f}s in '
          f'commands, {wall:.1f}s wall)')
    print('=' * 72)
    print(f'  {"command":<26}{"calls":>8}{"total":>10}{"mean":>11}{"share":>8}')
    ranked = sorted(stats.items(), key=lambda kv: -kv[1][1])
    for name, (count, secs) in ranked[:top]:
        print(f'  {name:<26}{count:>8}{secs:>9.2f}s{secs / count * 1000:>10.3f}ms'
              f'{secs / wall * 100:>7.1f}%')
    slow = [(n, c, s) for n, (c, s) in ranked[top:]
            if s / c * 1000 > threshold]
    if slow:
        print(f'  -- below the top {top}, but expensive per call:')
        for name, count, secs in slow[:10]:
            print(f'  {name:<26}{count:>8}{secs:>9.2f}s'
                  f'{secs / count * 1000:>10.3f}ms{secs / wall * 100:>7.1f}%')
    print('-' * 72)
    print(f'  {calls} commands, {in_cmds / max(calls, 1) * 1000000:.0f}us '
          f'mean, {in_cmds / wall * 100:.0f}% of wall time inside commands')
    print('=' * 72)
    for name, sites in (callers or {}).items():
        print(f'\n  WHERE {name} IS CALLED FROM')
        print('  ' + '-' * 68)
        for site, (count, secs) in sorted(sites.items(),
                                          key=lambda kv: -kv[1][1])[:10]:
            print(f'  {site:<48}{count:>8}{secs:>9.2f}s')
    print()


def profile_build(root=None, fk=None, ik=None, callers_for=()):
    '''
    Run a full rebuild under profile_cmds and print the command profile.

    MUTATING: this is a real build of the current scene, with the current
    settings. Run it when a timing report needs explaining - the phase and
    step lines say where, this says what.

    Usage:
        import rig_tail_build_test as rt_build_test
        rt_build_test.profile_build()
        rt_build_test.profile_build(callers_for=['ls', 'delete'])

    Arguments:
        root (str): Rig root; defaults to the scene's existing root group.
        fk (bool): Build FK; defaults to the BUILD_FK setting.
        ik (bool): Build IK; defaults to the BUILD_IK setting.
        callers_for (list): Commands to break down by call site, for when
            the totals say what is expensive but not which line issues it.

    Return:
        dict: name -> [calls, seconds] for every command the build used.
    '''
    import rig_tail as rig_tail
    import rig_tail_cleanup as rt_cleanup

    root = root or rt_cleanup.find_existing_root_grp() or rt_constants.ROOT
    fk = rt_constants.BUILD_FK if fk is None else fk
    ik = rt_constants.BUILD_IK if ik is None else ik
    with profile_cmds(callers_for=callers_for) as stats:
        rig_tail.rig_tail_multiple(root=root, fk=fk, ik=ik)
    return stats


# TEST ORCHESTRATION =========================================

def run_all(rigname='tail'):
    '''
    Run every read-only check and print a PASS/FAIL/RAN/ERROR summary.

    Mutating helpers (fix_*) and verbose dumps (print_*/show_*/dump_*) are
    excluded. Each check's return value is interpreted as:
        True  -> PASS        False -> FAIL
        None  -> RAN         (informational; no explicit verdict)
        raise -> ERROR
    Returns True only when no check FAILs or ERRORs.
    '''
    checks = [
        test_local_trs,      # BN local TRS is identity
        test_fx_order,       # FX before baseLocal in final_multMatrix
        test_alignment,      # BN vs IK/FK positions
        test_matrix_opm,     # OPM parent-space math
        test_matrix,         # FX matrix node wiring
        test_joint_orient,   # jointOrient / axis diagnostic
        check_expression_flags,  # expression time dependency
    ]

    results = []
    for fn in checks:
        try:
            outcome = fn(rigname)
            status = 'PASS' if outcome is True else \
                     'FAIL' if outcome is False else 'RAN'
            detail = ''
        except Exception as exc:
            status, detail = 'ERROR', str(exc)
        results.append((fn.__name__, status, detail))

    print('\n' + '=' * 64)
    print('  RUN_ALL SUMMARY  (rig: {0})'.format(rigname))
    print('=' * 64)
    for name, status, detail in results:
        line = '  {0:<6} {1}'.format(status, name)
        if detail:
            line += '  -- {0}'.format(detail)
        print(line)
    ok = not any(s in ('FAIL', 'ERROR') for _, s, _ in results)
    print('=' * 64)
    print('  RESULT: {0}'.format('ALL CLEAR' if ok else 'ISSUES FOUND'))
    print('=' * 64 + '\n')
    return ok

# FX MATRIX DIAGNOSTICS ======================================

def test_matrix(rigname='tail'):
    '''
    Diagnostic for pure matrix OPM architecture.

    Expected node names per joint (matching rig_tail_matrix.py):
        {rigname}_{NN:02d}_ikfk_blendMatrix
        {rigname}_{NN:02d}_baseLocal_multMatrix
        {rigname}_{NN:02d}_final_multMatrix      (only when fx_list is non-empty)
        {rigname}_{NN:02d}_{fx}_composeMatrix    (one per FX, all joints including 00)

    BN.offsetParentMatrix source:
        With FX:    final_multMatrix.matrixSum
        Without FX: baseLocal_multMatrix.matrixSum

    Joint 00 differs from 01+ only in baseLocal_multMatrix wiring:
        Joint 00:  blendMatrix.outputMatrix → matrixIn[0]  (no parent inverse)
        Joint 01+: bn_parent.worldInverseMatrix → matrixIn[0]
                   blendMatrix.outputMatrix     → matrixIn[1]
    '''
    header = '''
================================================================================
                QUICK DIAGNOSTIC (Pure Matrix OPM Architecture)
================================================================================
'''
    print(header)

    if rigname not in rt_constants.JOINTS_BN:
        print(f'x No BN joints defined for {rigname}')
        return

    joints = rt_constants.JOINTS_BN[rigname]
    print(f'Found {len(joints)} BN joints\n')

    fx_list = []
    if rt_constants.EFFECTS.get('curl'):
        fx_list.append('curl')
    if rt_constants.EFFECTS.get('wave'):
        fx_list.append('wave')
    if rt_constants.EFFECTS.get('noise'):
        fx_list.append('noise')

    issues = []
    warnings = []

    # === JOINT ORIENT CHECK ===
    # JO must NOT be zeroed - zeroing it changes BN worldMatrix and corrupts
    # all child baseLocal computations via bn_parent.worldInverseMatrix
    section = '''JOINT ORIENT CHECK (JO must be preserved, not zeroed):
--------------------------------------------------------------------------------'''
    print(section)

    for i, jnt in enumerate(joints):
        NN = rt_naming.get_index_from_name(jnt)
        if not cmds.attributeQuery('jointOrient', node=jnt, exists=True):
            continue
        jo = cmds.getAttr(f'{jnt}.jointOrient')[0]
        if i == 0:
            # Root joint - check IK JO matches BN JO (they should be identical)
            ik_joints = rt_constants.JOINTS_IK.get(rigname, [])
            if ik_joints:
                ik_jo = cmds.getAttr(f'{ik_joints[0]}.jointOrient')[0]
                jo_match = all(abs(jo[k] - ik_jo[k]) < 0.001 for k in range(3))
                if not jo_match:
                    issues.append(
                        f'Joint 00: BN JO {[round(v,3) for v in jo]} != '
                        f'IK JO {[round(v,3) for v in ik_jo]} - axes will mismatch'
                    )
                else:
                    print(f'  Jnt 00: JO = {[round(v,3) for v in jo]} (matches IK) OK')
            # Warn if JO was zeroed on root - this is the primary spiral cause
            if all(abs(jo[k]) < 0.001 for k in range(3)):
                ik_joints = rt_constants.JOINTS_IK.get(rigname, [])
                if ik_joints:
                    ik_jo = cmds.getAttr(f'{ik_joints[0]}.jointOrient')[0]
                    if any(abs(ik_jo[k]) > 0.1 for k in range(3)):
                        issues.append(
                            f'Joint 00: BN JO is (0,0,0) but IK JO is '
                            f'{[round(v,3) for v in ik_jo]} - '
                            f'JO was zeroed, this causes spiral in all children'
                        )
    print()

    # === MATRIX NETWORK CHECK ===
    section = '''MATRIX NETWORK CHECK:
--------------------------------------------------------------------------------'''
    print(section)

    for i, jnt in enumerate(joints):
        NN = rt_naming.get_index_from_name(jnt)

        # --- blendMatrix ---
        blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
        if not cmds.objExists(blend_mtx):
            issues.append(f'Joint {NN:02d}: Missing ikfk_blendMatrix')
        else:
            input_conn = cmds.listConnections(f'{blend_mtx}.inputMatrix', s=1, d=0, p=1) or []
            if not input_conn:
                issues.append(f'Joint {NN:02d}: blendMatrix.inputMatrix not connected')
            else:
                print(f'  Jnt {NN:02d}: blendMatrix <- {input_conn[0]}')

        # --- baseLocal_multMatrix ---
        baselocal = f'{rigname}_{NN:02d}_baseLocal_multMatrix'
        if not cmds.objExists(baselocal):
            issues.append(f'Joint {NN:02d}: Missing baseLocal_multMatrix')
        else:
            m0 = cmds.listConnections(f'{baselocal}.matrixIn[0]', s=1, d=0, p=1) or []
            m1 = cmds.listConnections(f'{baselocal}.matrixIn[1]', s=1, d=0, p=1) or []

            if i == 0:
                # Root: matrixIn[0] = blendMatrix.outputMatrix, matrixIn[1] unused
                if not m0:
                    issues.append(f'Joint 00: baseLocal_multMatrix.matrixIn[0] not connected')
                elif 'blendMatrix' not in m0[0]:
                    issues.append(
                        f'Joint 00: baseLocal_multMatrix.matrixIn[0] should be '
                        f'blendMatrix, got {m0[0]}'
                    )
                else:
                    print(f'  Jnt 00: baseLocal = blendMatrix only (root, no parent inverse) OK')
            else:
                # Non-root: matrixIn[0] = blendMatrix (driver), matrixIn[1] = parent worldInverseMatrix
                if not m0:
                    issues.append(f'Joint {NN:02d}: baseLocal_multMatrix.matrixIn[0] not connected')
                elif 'blendMatrix' not in m0[0]: # [0] is driver now
                    issues.append(
                        f'Joint {NN:02d}: baseLocal.matrixIn[0] should be '
                        f'blendMatrix, got {m0[0]}'
                    )
                if not m1:
                    issues.append(f'Joint {NN:02d}: baseLocal_multMatrix.matrixIn[1] not connected')
                elif 'worldInverseMatrix' not in m1[0]: # [1] is parent inverse now
                    issues.append(
                        f'Joint {NN:02d}: baseLocal.matrixIn[1] should be '
                        f'worldInverseMatrix, got {m1[0]}'
                    )
                if m0 and m1 and 'blendMatrix' in m0[0] and 'worldInverseMatrix' in m1[0]:
                    print(f'  Jnt {NN:02d}: baseLocal = blendMatrix * parentInv OK')

        # --- FX composeMatrix nodes ---
        for fx_name in fx_list:
            fx_compose = f'{rigname}_{NN:02d}_{fx_name}_composeMatrix'
            if not cmds.objExists(fx_compose):
                issues.append(f'Joint {NN:02d}: Missing {fx_name}_composeMatrix')
            else:
                # Translation must be zero
                trans = cmds.getAttr(f'{fx_compose}.inputTranslate')[0]
                if any(abs(t) > 0.001 for t in trans):
                    issues.append(
                        f'Joint {NN:02d}: {fx_name}_composeMatrix has '
                        f'non-zero translation {[round(v,4) for v in trans]}'
                    )
                # Check rotation input is connected
                has_rot_conn = any(
                    cmds.listConnections(f'{fx_compose}.inputRotate{ax}', s=1, d=0)
                    for ax in ('X', 'Y', 'Z')
                )
                if not has_rot_conn:
                    warnings.append(
                        f'Joint {NN:02d}: {fx_name}_composeMatrix.inputRotate not connected'
                    )

        # --- final_multMatrix (only exists when fx_list non-empty) ---
        if fx_list:
            final_mult = f'{rigname}_{NN:02d}_final_multMatrix'
            if not cmds.objExists(final_mult):
                issues.append(f'Joint {NN:02d}: Missing final_multMatrix')
            else:
                m0 = cmds.listConnections(f'{final_mult}.matrixIn[0]', s=1, d=0, p=1) or []
                if not m0 or 'baseLocal_multMatrix' not in m0[0]:
                    issues.append(
                        f'Joint {NN:02d}: final_multMatrix.matrixIn[0] should be '
                        f'baseLocal_multMatrix.matrixSum, got {m0[0] if m0 else "nothing"}'
                    )
                # Check each FX compose is wired in
                for idx, fx_name in enumerate(fx_list, start=1):
                    mx = cmds.listConnections(
                        f'{final_mult}.matrixIn[{idx}]', s=1, d=0, p=1
                    ) or []
                    if not mx or fx_name not in mx[0]:
                        issues.append(
                            f'Joint {NN:02d}: final_multMatrix.matrixIn[{idx}] '
                            f'should be {fx_name}_composeMatrix, got '
                            f'{mx[0] if mx else "nothing"}'
                        )

        # --- OPM source ---
        opm_conn = cmds.listConnections(f'{jnt}.offsetParentMatrix', s=1, d=0, p=1) or []
        if not opm_conn:
            issues.append(f'Joint {NN:02d}: offsetParentMatrix not connected')
        else:
            expected_src = (
                f'{rigname}_{NN:02d}_final_multMatrix.matrixSum'
                if fx_list
                else f'{rigname}_{NN:02d}_baseLocal_multMatrix.matrixSum'
            )
            if expected_src not in opm_conn[0]:
                issues.append(
                    f'Joint {NN:02d}: offsetParentMatrix connected to wrong source. '
                    f'Expected {expected_src.split(".")[0]}, got {opm_conn[0]}'
                )
            else:
                print(f'  Jnt {NN:02d}: OPM <- {opm_conn[0]} OK')

    print()

    # === LOCAL TRS CHECK ===
    section = '''LOCAL TRS CHECK (should be identity):
--------------------------------------------------------------------------------'''
    print(section)

    for i, jnt in enumerate(joints):
        NN = rt_naming.get_index_from_name(jnt)
        trans = cmds.getAttr(f'{jnt}.translate')[0]
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        scale = cmds.getAttr(f'{jnt}.scale')[0]
        if any(abs(t) > 0.001 for t in trans):
            issues.append(f'Joint {NN:02d}: translate = {[round(v,4) for v in trans]}')
        if any(abs(r) > 0.001 for r in rot):
            issues.append(f'Joint {NN:02d}: rotate = {[round(v,4) for v in rot]}')
        if any(abs(s - 1.0) > 0.001 for s in scale):
            issues.append(f'Joint {NN:02d}: scale = {[round(v,4) for v in scale]}')

    if not any('translate' in x or 'rotate' in x or 'scale' in x for x in issues):
        print('  OK All BN joints have identity local TRS')
    print()

    # === OPM NUMERIC CHECK ===
    # First-row X value should be ~1.0 for all joints after root.
    # Growing deviation indicates accumulation from a mismatched BN worldMatrix
    # (most commonly caused by zeroed JO on root joint).
    section = '''OPM NUMERIC CHECK (first-row X should be ~1.0, off-diag ~0.0):
--------------------------------------------------------------------------------'''
    print(section)

    opm_drift_warned = False
    for i, jnt in enumerate(joints):
        NN = rt_naming.get_index_from_name(jnt)
        opm = cmds.getAttr(f'{jnt}.offsetParentMatrix')
        if opm:
            diag_x = opm[0]   # should be ~1.0
            off_y = opm[1]    # should be ~0.0
            off_z = opm[2]    # should be ~0.0
            drift = abs(diag_x - 1.0) + abs(off_y) + abs(off_z)
            if drift > 0.01 and not opm_drift_warned:
                issues.append(
                    f'Joint {NN:02d}: OPM first row [{round(diag_x,6)}, '
                    f'{round(off_y,6)}, {round(off_z,6)}] - '
                    f'significant deviation, likely JO zeroed on root'
                )
                opm_drift_warned = True
                print(f'  Jnt {NN:02d}: OPM first row [{round(diag_x,6)}, {round(off_y,8)}, {round(off_z,8)}] DRIFT DETECTED')
            elif drift > 1e-10:
                print(f'  Jnt {NN:02d}: OPM first row [{round(diag_x,6)}, {round(off_y,2e-8):.2e}, {round(off_z,2e-8):.2e}] (fp noise, ok)')
            else:
                print(f'  Jnt {NN:02d}: OPM first row [{round(diag_x,6)}, {off_y:.2e}, {off_z:.2e}] OK')
    print()

    # === ALIGNMENT CHECK ===
    if rigname in rt_constants.JOINTS_IK:
        section = '''POSITION ALIGNMENT CHECK (BN vs IK world positions):
--------------------------------------------------------------------------------'''
        print(section)
        max_misalign = 0
        worst_joint = 0

        for i, (bn_jnt, ik_jnt) in enumerate(
            zip(joints, rt_constants.JOINTS_IK[rigname])
        ):
            bn_pos = cmds.xform(bn_jnt, q=1, ws=1, t=1)
            ik_pos = cmds.xform(ik_jnt, q=1, ws=1, t=1)
            dist = sum((bn_pos[j] - ik_pos[j])**2 for j in range(3)) ** 0.5

            if dist > max_misalign:
                max_misalign = dist
                worst_joint = i

            if dist > 0.5:
                warnings.append(f'Joint {i:02d}: BN misaligned from IK by {dist:.3f} units')

        print(f'  Max misalignment: {max_misalign:.4f} units at joint {worst_joint:02d}')
        if max_misalign < 0.01:
            print('  OK Alignment EXCELLENT')
        elif max_misalign < 0.1:
            print('  WARNING Alignment ACCEPTABLE')
        else:
            print('  ERROR Alignment BAD - check JO and matrix wiring')
        print()

    # === RESULTS ===
    if issues:
        print('CRITICAL ISSUES:')
        for issue in issues:
            print(f'  {issue}')
        print()

    if warnings:
        print('WARNINGS:')
        for warning in warnings:
            print(f'  {warning}')
        print()

    if not issues and not warnings:
        print('OK Pure matrix OPM architecture looks correct.')
        print('   If deformation is still wrong, check that IK/FK joints')
        print('   are at correct positions and run test_alignment().')


def test_local_trs(rigname='tail'):
    '''
    Check every BN joint has identity local TRS ([0,0,0]/[0,0,0]/[1,1,1]).
    Returns True if all joints pass, False otherwise.
    '''
    print('\n=== LOCAL TRS CHECK ===\n')

    if rigname not in rt_constants.JOINTS_BN:
        print(f'× No BN joints for {rigname}')
        return None

    joints = rt_constants.JOINTS_BN[rigname]
    all_good = True

    for i, jnt in enumerate(joints):
        NN = rt_naming.get_index_from_name(jnt)
        trans = cmds.getAttr(f'{jnt}.translate')[0]
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        scale = cmds.getAttr(f'{jnt}.scale')[0]

        trans_ok = all(abs(t) < 0.001 for t in trans)
        rot_ok = all(abs(r) < 0.001 for r in rot)
        scale_ok = all(abs(s - 1.0) < 0.001 for s in scale)

        if not (trans_ok and rot_ok and scale_ok):
            all_good = False
            print(f'Joint {NN:02d}: {jnt}')
            if not trans_ok:
                print(f'  ❌ translate = {[round(v, 4) for v in trans]} (should be [0,0,0])')
            if not rot_ok:
                print(f'  ❌ rotate = {[round(v, 4) for v in rot]} (should be [0,0,0])')
            if not scale_ok:
                print(f'  ❌ scale = {[round(v, 4) for v in scale]} (should be [1,1,1])')

    if all_good:
        print('✅ All BN joints have identity local TRS')
    else:
        print('\n❌ Some BN joints have non-zero local TRS')
        print('   Run: rt_build_test.fix_bn_local_trs(rigname) to fix')
    print()
    return all_good


def fix_bn_local_trs(rigname='tail'):
    '''
    Emergency fix: zero all BN joint local TRS.
    WARNING: This will break existing offsetParentMatrix connections.
    Only use if rebuilding the matrix network.
    '''
    print('\n=== FIXING BN LOCAL TRS ===\n')

    if rigname not in rt_constants.JOINTS_BN:
        print(f'× No BN joints for {rigname}')
        return

    joints = rt_constants.JOINTS_BN[rigname]

    for jnt in joints:
        for attr in ['translateX', 'translateY', 'translateZ', 'rotateX', 'rotateY', 'rotateZ']:
            cmds.setAttr(f'{jnt}.{attr}', 0, l=0)
        cmds.setAttr(f'{jnt}.scale', 1, 1, 1, type='double3')
        print(f'  Zeroed {jnt}')

    print('\n✅ All BN joints zeroed')
    print('   You must rebuild the matrix network for this to take effect!')
    print()


def test_fx_order(rigname='tail'):
    '''
    Verify FX matrices multiply BEFORE baseLocal in final_multMatrix.

    Correct order (rotate about own pivot):
        matrixIn[0..n-1] = fx composeMatrix nodes
        matrixIn[n]      = baseLocal_multMatrix.matrixSum
    Wrong order (zig-zag / lengthening):
        matrixIn[0]      = baseLocal, fx after

    Returns True if every FX chain has the correct order, False if any is
    wrong, or None when there is nothing to check.
    '''
    print('\n=== FX MULTIPLY ORDER CHECK ===\n')

    if rigname not in rt_constants.JOINTS_BN:
        print(f'x No BN joints for {rigname}')
        return None

    fx_list = []
    if rt_constants.EFFECTS.get('curl'):
        fx_list.append('curl')
    if rt_constants.EFFECTS.get('wave'):
        fx_list.append('wave')
    if rt_constants.EFFECTS.get('noise'):
        fx_list.append('noise')

    if not fx_list:
        print('  No FX enabled, nothing to check')
        return None

    joints = rt_constants.JOINTS_BN[rigname]
    bad = 0

    for jnt in joints:
        NN = rt_naming.get_index_from_name(jnt)
        final_mult = f'{rigname}_{NN:02d}_final_multMatrix'
        if not cmds.objExists(final_mult):
            continue

        # What is wired into matrixIn[0]? Should be an fx composeMatrix, NOT baseLocal.
        first = cmds.listConnections(f'{final_mult}.matrixIn[0]', s=1, d=0, p=1) or []
        last_idx = len(fx_list)
        last = cmds.listConnections(f'{final_mult}.matrixIn[{last_idx}]', s=1, d=0, p=1) or []

        first_src = first[0] if first else 'nothing'
        last_src = last[0] if last else 'nothing'

        first_is_fx = any(fx in first_src for fx in fx_list)
        last_is_base = 'baseLocal_multMatrix' in last_src

        if first_is_fx and last_is_base:
            if NN < 3:
                print(f'  Jnt {NN:02d}: fx first, baseLocal last OK')
        else:
            bad += 1
            print(f'  Jnt {NN:02d}: WRONG ORDER')
            print(f'           matrixIn[0]        = {first_src}')
            print(f'           matrixIn[{last_idx}] = {last_src}')
            print(f'           expected fx at [0], baseLocal at [{last_idx}]')

    print()
    if bad == 0:
        print('  OK All FX chains multiply fx before baseLocal')
    else:
        print(f'  ERROR {bad} joints have baseLocal ahead of FX (causes zig-zag)')
    print()
    return bad == 0


def test_ikfk_drive(rigname='tail', joint_index=3):
    '''
    Diagnose FK control -> FK joint -> BN propagation at both switch extremes.

    Checks, for one sample joint:
      1. What drives FK_jnt.rotate (is the control actually connected?)
      2. blendMatrix target weight when switch is set low vs high
      3. Whether BN world position tracks IK vs FK as the switch moves
    '''
    print(f'\n=== IK/FK DRIVE CHECK (joint {joint_index:02d}) ===\n')

    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    ikfk_attr = rt_naming.fstr(rigname, rt_constants.IKFK)

    ik_jnt = rt_constants.JOINTS_IK[rigname][joint_index]
    fk_jnt = rt_constants.JOINTS_FK[rigname][joint_index]
    bn_jnt = rt_constants.JOINTS_BN[rigname][joint_index]
    NN = rt_naming.get_index_from_name(bn_jnt)

    # 1. What drives the FK joint's rotation?
    print('FK JOINT DRIVE:')
    sdk_grp = rt_naming.fstr(rigname, rt_constants.SDK_JNT, rt_constants.TYPE_FK, NN)
    fk_rot_conn = cmds.listConnections(f'{sdk_grp}.rotate', s=1, d=0, p=1) or []
    fk_rx_conn = cmds.listConnections(f'{sdk_grp}.rotateX', s=1, d=0, p=1) or []
    if fk_rot_conn or fk_rx_conn:
        for c in (fk_rot_conn + fk_rx_conn):
            print(f'  {sdk_grp}.rotate <- {c}')
    else:
        print(f'  x NOTHING drives {sdk_grp}.rotate')
        print(f'    -> FK control to SDK group link is missing')

    # 2. Switch attribute range vs condition threshold
    print('SWITCH ATTRIBUTE:')
    if cmds.objExists(cog_ctrl) and cmds.attributeQuery(ikfk_attr, node=cog_ctrl, exists=True):
        cur = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}')
        has_min = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, minExists=True)
        has_max = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, maxExists=True)
        amin = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, min=True)[0] if has_min else None
        amax = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, max=True)[0] if has_max else None
        print(f'  {cog_ctrl}.{ikfk_attr} = {cur} (range {amin} to {amax})')
        cond = f'{rigname}_{NN:02d}_ikfk_remap_condition'
        if cmds.objExists(cond):
            thresh = cmds.getAttr(f'{cond}.secondTerm')
            print(f'  condition threshold (secondTerm) = {thresh}')
            if amax is not None and thresh > amax:
                print(f'  ERROR threshold {thresh} exceeds max {amax}: FK weight can never turn on')
    else:
        print(f'  x switch attr {ikfk_attr} not found on {cog_ctrl}')
    print()

    # 3. Sweep the switch, watch blend weight and BN tracking
    print('SWEEP (BN should track FK at one end, IK at the other):')
    blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
    saved = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}')

    fk_ctrl0 = rt_naming.fstr(rigname, rt_constants.CONTROL, rt_constants.TYPE_FK, 0)
    saved_rot = cmds.getAttr(f'{fk_ctrl0}.rotate')[0]
    cmds.setAttr(f'{fk_ctrl0}.rotate', 0, 0, 30)

    for val in (0, 3):
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', val)
        # force eval
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

        weight = None
        if cmds.objExists(blend_mtx):
            wconn = cmds.listConnections(f'{blend_mtx}.target[0].weight', s=1, d=0) or []
            weight = cmds.getAttr(f'{blend_mtx}.target[0].weight')

        bn_pos = cmds.xform(bn_jnt, q=1, ws=1, t=1)
        ik_pos = cmds.xform(ik_jnt, q=1, ws=1, t=1)
        fk_pos = cmds.xform(fk_jnt, q=1, ws=1, t=1)
        d_ik = sum((bn_pos[j] - ik_pos[j])**2 for j in range(3)) ** 0.5
        d_fk = sum((bn_pos[j] - fk_pos[j])**2 for j in range(3)) ** 0.5
        tracking = 'IK' if d_ik < d_fk else 'FK'
        print(f'  switch={val}: blendWeight={weight}  BN tracks {tracking} '
              f'(dIK={d_ik:.3f} dFK={d_fk:.3f})')

    cmds.setAttr(f'{fk_ctrl0}.rotate', *saved_rot)
    cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', saved)
    print()


def test_override_routing(rigname='tail', tolerance=1e-3):
    '''
    Verify the Override All flag actually gates the tail's IKFK mode, and
    that every control shows the same two dials (MUTATES, restores).

    The joints are what 'the tail is in FK' means, and they reach their
    mode by a different route from everything else: the visibility and
    constraint SDKs are keyed on the resolved driver, while the BN blend
    weight comes off a remap condition wired in rig_tail_matrix. Point
    that condition at the tail's own switch and the rig LOOKS routed -
    the controls hide and show on the ALL value - while the joints follow
    the per-tail switch behind them. Nothing errors; the only thing that
    sees it is moving the switch and watching what does not move.

    So the sweep is run twice, and it is the FIRST half that catches it:

      Cog       the per-tail switch is swept and the chain must NOT move.
                Movement here means something downstream reads the raw
                switch instead of the resolved plug.
      Basectrl  the same sweep must move the chain, or the override flag
                is dead in the other direction and the tail can never be
                driven on its own.

    FK is posed first, since with both chains at rest the two modes put
    the joints in the same place and neither half would measure anything.

    The proxy audit is a pre-flight, not a pass condition: a control
    missing the switch is an inconvenience, a control whose switch is not
    the cog's is a second source of truth.

    Arguments
        rigname (str): Rig part to test
        tolerance (float): Movement below this counts as none

    Return
        bool: True if the flag gates the mode in both directions
    '''
    print(f'\n=== OVERRIDE ROUTING CHECK: {rigname} ===\n')

    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    if not rt_ctrlall.active() or not cmds.objExists(cog_ctrl):
        print('  - dashboard not active - nothing to route, skipping')
        return True

    ikfk_attr = rt_naming.fstr(rigname, rt_constants.IKFK)
    override_attr = rt_naming.fstr(rigname, rt_constants.OVERRIDE)
    for attr in (ikfk_attr, override_attr, rt_ctrlall.all_attr('ikfk')):
        if not cmds.attributeQuery(attr, n=cog_ctrl, ex=1):
            print(f'  x {cog_ctrl}.{attr} not found - build with IK and the '
                  f'Main Controller on')
            return False

    bn = bn_joints(rigname)
    if len(bn) < 3:
        print(f'  x need at least 3 BN joints, found {len(bn)}')
        return False

    fk_mode = rt_constants.ikfk_fk_mode_index()
    if fk_mode is None or fk_mode == 0:
        print('  x no distinct FK mode to switch to - build FK and IK')
        return False

    # Proxy audit. A control belongs to this tail when its name carries
    # the part between separators, which is what keeps 'L_tail1' from
    # claiming 'L_tail11'.
    print('PROXIES (should all read the cog):')
    missing, foreign = list(), list()
    for control in cmds.ls(f'*_{rt_constants.CTRL}', type='transform') or []:
        if not (control.startswith(f'{rigname}_')
                or f'_{rigname}_' in control):
            continue
        for attr, master in ((rt_constants.IKFK_SWITCH[0], ikfk_attr),
                             (override_attr, override_attr)):
            if not cmds.attributeQuery(attr, n=control, ex=1):
                missing.append(f'{control}.{attr}')
                continue
            src = cmds.listConnections(f'{control}.{attr}', s=1, d=0, p=1) or []
            if f'{cog_ctrl}.{master}' not in src:
                foreign.append(f'{control}.{attr} <- {src or "nothing"}')
    print(f'  {"missing":8s} {len(missing)}')
    for name in missing:
        print(f'    - {name}')
    print(f'  {"foreign":8s} {len(foreign)}')
    for name in foreign:
        print(f'    x {name}')

    def _eval():
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

    def _travel(a, b):
        return sum(sum((b[i][k] - a[i][k]) ** 2 for k in range(3)) ** 0.5
                   for i in range(len(a)))

    def _pose():
        return [cmds.xform(j, q=1, ws=1, t=1) for j in bn]

    override_plug = f'{cog_ctrl}.{override_attr}'
    ikfk_plug = f'{cog_ctrl}.{ikfk_attr}'
    all_plug = f'{cog_ctrl}.{rt_ctrlall.all_attr("ikfk")}'
    saved = {p: cmds.getAttr(p) for p in (override_plug, ikfk_plug, all_plug)}

    # Something has to differ between the two modes or neither half of the
    # sweep measures anything. The variable-FK controls the animator holds
    # carry no type prefix and count from 1 (see connect_fk) - naming them
    # 'FK_<part>_00_ctrl' finds nothing, and a sweep with no pose behind it
    # reports a dead switch rather than an unposed rig.
    fk_ctrl0 = rt_naming.fstr(rigname, rt_constants.CONTROL, '', 1)
    posed = cmds.objExists(fk_ctrl0)
    saved_rot = cmds.getAttr(f'{fk_ctrl0}.rotate')[0] if posed else None
    if posed:
        cmds.setAttr(f'{fk_ctrl0}.rotate', 0, 0, 30)

    print('\nSWEEP (per-tail switch 0 -> FK, ALL held at 0):')
    cmds.setAttr(all_plug, 0)
    travel = dict()
    for label, flag in (('Cog', 0), ('Basectrl', 1)):
        cmds.setAttr(override_plug, flag)
        cmds.setAttr(ikfk_plug, 0)
        _eval()
        before = _pose()
        cmds.setAttr(ikfk_plug, fk_mode)
        _eval()
        travel[label] = _travel(before, _pose())

    if posed:
        cmds.setAttr(f'{fk_ctrl0}.rotate', *saved_rot)
    for plug, value in saved.items():
        cmds.setAttr(plug, value)
    _eval()

    held = travel['Cog'] <= tolerance
    driven = travel['Basectrl'] > tolerance
    print(f'  {"Cog":10s} chain moved {travel["Cog"]:9.4f}  '
          f'{"OK - the flag holds it" if held else "x SWITCH LEAKS PAST THE FLAG"}')
    print(f'  {"Basectrl":10s} chain moved {travel["Basectrl"]:9.4f}  '
          f'{"OK - the switch drives it" if driven else "x SWITCH DOES NOTHING"}')
    if not held:
        print(f'\n  something downstream reads {ikfk_plug} directly rather '
              f'than rt_ctrlall.ikfk_driver({rigname!r}) - the BN blend '
              f"weight ('{rigname}_NN_ikfk_remap_condition.firstTerm') is "
              f'where this went wrong before')
    if not driven and not posed:
        print(f'\n  no FK control {fk_ctrl0} to pose, so the two modes may '
              f'simply agree - this half proves nothing on its own')

    ok = held and driven
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return ok


def test_twist_roll_offset(rigname='tail', amount=45.0, offset_amount=1.0):
    '''
    Verify twist / roll / offset move the BN chain in EVERY IKFK mode, by
    both routes: the tail's own basectrl values and the Main Controller's
    ALL values (MUTATES, restores everything it touches).

    Each attribute is swept 0 -> value and the chain measured three ways.
    An attribute counts as wired if EITHER moved or rot changed: spline-IK
    twist rolls the joints about the curve they are constrained to, so it
    rotates every joint while moving none, and a displacement-only test
    reports a working IK twist as NOT WIRED.

      moved     sum of per-joint world translation change
      rot       sum of per-joint world ORIENTATION change, in degrees
      relTwist  max change in RELATIVE orientation between consecutive
                joints - classifies the shape, does not detect wiring

    Expected signatures:
      twist   rot > 0, relTwist > 0   (ramp)
      roll    rot > 0, relTwist ~ 0   (rigid)
      offset  moved > 0, rot ~ 0      (slide)

    Both rows per mode matter, and they need OPPOSITE override settings:

      local   the tail's basectrl attribute, tested with its override flag
              On. With the dashboard active and the flag Off (the default)
              the override condition ignores the basectrl input by design,
              so driving it proves nothing - an earlier version of this
              test set it with the flag Off and reported a false NOT WIRED
              on a rig that was working.
      ALL     the cog's all_* attribute, tested with the flag Off. This is
              the route that really did break once, when a consumer read
              the basectrl directly instead of rt_ctrlall.resolved_plug.

    offset is driven SIGNED, by rt_mirror.translation_signs. The chain
    spans its whole curve, so the dial can only slide it back along that
    curve, and a mirrored side reaches that one direction through the
    opposite dial sign (connect_spline_ik's mirror node). One fixed sign
    therefore tests the blocked direction on one side of every mirrored
    pair, where a perfectly wired offset moves nothing at all.

    Arguments
        rigname (str): Rig part to test
        amount (float): Degrees to drive twist/roll to
        offset_amount (float): Scene units to drive offset to. Separate
            because offset is a distance, not an angle. Its sign comes
            from the part's mirroring, not from this value.

    Return
        bool: True if every mode responded to every attribute by both routes
    '''
    print(f'\n=== TWIST / ROLL / OFFSET CHECK: {rigname} ===\n')

    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    if not cmds.objExists(basectrl):
        print(f'  x basectrl {basectrl} not found - build the rig first')
        return False
    bn = bn_joints(rigname)
    if len(bn) < 3:
        print(f'  x need at least 3 BN joints, found {len(bn)} - is '
              f"'{rigname}' spelled as it is in RIGPARTS?")
        return False

    ai = {'x': 0, 'y': 1, 'z': 2}.get(
        getattr(rt_constants, 'ORIENT_AIM_AXIS', 'x'), 0)

    def _state():
        '''Every BN joint's full world matrix.'''
        return [cmds.xform(j, q=1, ws=1, m=1) for j in bn]

    def _ang(u, v):
        d = sum(u[k] * v[k] for k in range(3))
        return math.degrees(math.acos(max(-1.0, min(1.0, d))))

    def _row(m, r):
        return m[r * 4: r * 4 + 3]

    def _compare(a, b):
        '''
        Three independent measures. Position change alone is NOT enough to
        tell whether an attribute did anything: spline-IK twist rolls the
        joints about the curve they are constrained to, so it rotates every
        joint while moving none of them. Reporting only displacement made a
        working IK twist read as NOT WIRED.

          moved    sum of per-joint world translation change
          rotated  sum of per-joint world ORIENTATION change (max angle
                   over the three basis rows, so a rotation about any axis
                   is caught)
          rel      max change in RELATIVE orientation between consecutive
                   joints - a shape classifier, not a detector: it tells a
                   ramp (twist) from a rigid turn (roll), and is near zero
                   for both a rigid turn and a pure slide
        '''
        moved = sum(sum((b[i][12 + k] - a[i][12 + k]) ** 2
                        for k in range(3)) ** 0.5
                    for i in range(len(a)))
        rotated = sum(max(_ang(_row(a[i], r), _row(b[i], r)) for r in range(3))
                      for i in range(len(a)))
        rel = 0.0
        for i in range(len(a) - 1):
            up = (ai + 1) % 3
            rel = max(rel, abs(_ang(_row(a[i], up), _row(a[i + 1], up))
                               - _ang(_row(b[i], up), _row(b[i + 1], up))))
        return moved, rotated, rel

    def _eval():
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

    # Which modes exist on this rig
    ikfk_attr = rt_naming.fstr(rigname, rt_constants.IKFK)
    has_switch = (cmds.objExists(cog_ctrl)
                  and cmds.attributeQuery(ikfk_attr, n=cog_ctrl, ex=1))
    modes = []
    if has_switch:
        enum = cmds.attributeQuery(ikfk_attr, n=cog_ctrl, le=1)[0].split(':')
        for i, name in enumerate(enum):
            modes.append((name, i))
    else:
        modes.append(('(single mode)', None))

    saved_mode = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}') if has_switch else None
    attrs = [a for a in ('twist', 'roll', 'offset')
             if cmds.attributeQuery(a, n=basectrl, ex=1)]
    if not attrs:
        print(f'  x no twist/roll/offset attributes on {basectrl}')
        return False

    def _drive(plug, value):
        '''Sweep one plug 0 -> value, return (moved, relTwist). Restores.'''
        saved = cmds.getAttr(plug)
        cmds.setAttr(plug, 0)
        _eval()
        before = _state()
        cmds.setAttr(plug, value)
        _eval()
        result = _compare(before, _state())
        cmds.setAttr(plug, saved)
        _eval()
        return result

    # With the dashboard active the tail's override flag decides which
    # input of the override condition is live, so each path has to be
    # tested with the flag set the way that path requires: On for the
    # tail's own basectrl values, Off for the cog's ALL values. Driving a
    # basectrl attribute with the flag Off is SUPPOSED to do nothing.
    dash = rt_ctrlall.active()
    override = f'{cog_ctrl}.{rt_naming.fstr(rigname, rt_constants.OVERRIDE)}'
    has_override = dash and cmds.objExists(cog_ctrl) \
        and cmds.attributeQuery(rt_naming.fstr(rigname, rt_constants.OVERRIDE),
                                n=cog_ctrl, ex=1)
    saved_ovr = cmds.getAttr(override) if has_override else None

    # The chain spans its whole curve, so 'offset' can only slide it BACK
    # along that curve - there is nothing ahead to slide into. Which dial
    # sign that is flips per side, because connect_spline_ik puts a
    # negating mirror node on a signed side. Driving the same sign on both
    # tests the blocked direction on one of them, where a perfectly wired
    # offset moves nothing and reads as dead. Same source the build signs
    # from, so an inverted mirror still fails rather than being papered over.
    offset_sign = rt_mirror.translation_signs(rigname).get(
        rt_mirror.aim_axis(), 1.0)

    # (label, plug builder, override value the path needs)
    paths = [('local', lambda a: f'{basectrl}.{a}', 1)]
    if dash:
        paths.append(('ALL', lambda a: f'{cog_ctrl}.{rt_ctrlall.all_attr(a)}', 0))

    ok = True
    failures = []
    for mode_name, mode_val in modes:
        if mode_val is not None:
            cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', mode_val)
        print(f'MODE {mode_name}:')
        for label, plug_of, need_ovr in paths:
            if has_override:
                cmds.setAttr(override, need_ovr)
            for attr in attrs:
                plug = plug_of(attr)
                if not cmds.objExists(plug.split('.')[0]) \
                        or not cmds.attributeQuery(plug.split('.', 1)[1],
                                                   n=plug.split('.')[0], ex=1):
                    print(f'  {label:5s} {attr:7s} x attribute not found')
                    ok = False
                    continue
                if cmds.listConnections(plug, s=1, d=0, p=1):
                    print(f'  {label:5s} {attr:7s} (driven by a connection, '
                          f'skipped)')
                    continue
                # offset is a DISTANCE, not an angle: driving it to the
                # same number as twist/roll slides the chain by that many
                # scene units per joint, which says nothing extra about
                # whether it is wired. Signed per side - see offset_sign.
                value = (offset_amount * offset_sign if attr == 'offset'
                         else amount)
                moved, rotated, rel = _drive(plug, value)
                # Wired = the chain changed AT ALL, by translation or by
                # rotation. Requiring translation hides spline-IK twist,
                # which rotates joints in place along the curve.
                good = moved > 1e-4 or rotated > 1e-2
                ok &= good
                if rotated < 1e-2:
                    shape = 'slide'
                elif rel > 1.0:
                    shape = 'ramp'
                else:
                    shape = 'rigid'
                if not good:
                    failures.append(f'{mode_name}/{label}/{attr}')
                print(f'  {label:5s} {attr:7s} moved={moved:8.3f}  '
                      f'rot={rotated:7.2f}  relTwist={rel:6.2f} '
                      f'({shape:5s})  {"OK" if good else "x NOT WIRED"}')
        print()

    if has_override:
        cmds.setAttr(override, saved_ovr)

    if has_switch:
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', saved_mode)
    _eval()

    if failures:
        print(f'  not wired: {", ".join(failures)}')
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return ok


def test_spline_mid_rot(rigname='tail', amount=30.0, axis='Z', tolerance=0.02):
    '''
    Tell a constraint fault from curve geometry when rotating
    spline_mid_rot S-curves the tail's top section (MUTATES, restores).

    mid_rot rigidly swings top, top_sml and upvec_end - top's group is
    parented under it - so those CVs travel together. Whether the TIP goes
    with them is a separate question, and the two failures look identical
    in the viewport. This measures them apart:

      tracking  end cluster travel / top control travel. ~1.0 means the
                chain from mid_rot through top to the upvec end cluster
                carries the swing intact. Well under 1 means a constraint
                is dropping it, and the RIGGING is at fault.
      follow    tip joint travel / top control travel. Below 1 by the
                curve's own arithmetic, not by any fault: the driver curve
                is cubic over NUM_CTRL_IK+2 CVs, so the span nearest the
                tip is still weighted by the fixed mid CV and the tip
                cannot swing rigidly however sound the constraints are.

    So a low follow alongside tracking at ~1.0 is geometry. Raising
    NUM_CTRL_IK narrows each CV's influence and tightens it; hunting
    constraints will not. Only tracking decides the pass.

    Arguments
        rigname (str): Rig part to test
        amount (float): Degrees to rotate mid_rot
        axis (str): Local axis of mid_rot to rotate about
        tolerance (float): Allowed shortfall in tracking

    Return
        bool: True if the end cluster tracked the swing
    '''
    print(f'\n=== SPLINE MID_ROT CHECK: {rigname} ===\n')

    typ = rt_constants.TYPE_IK
    mid_rot = rt_naming.fstr(rigname, rt_constants.SPLINE_MID_ROT, typ)
    top = rt_naming.fstr(rigname, rt_constants.SPLINE_TOP, typ)
    end_cluster = rt_naming.fstr(rigname, rt_constants.CLUSTER_UPV_HANDLE,
                                 typ, TAG='end')
    for node in (mid_rot, top, end_cluster):
        if not cmds.objExists(node):
            print(f'  x {node} not found - build IK first')
            return False

    bn = bn_joints(rigname)
    if len(bn) < 3:
        print(f'  x need at least 3 BN joints, found {len(bn)}')
        return False
    tip = bn[-1]

    plug = f'{mid_rot}.rotate{axis.upper()}'
    if cmds.listConnections(plug, s=1, d=0, p=1):
        print(f'  x {plug} is driven by a connection')
        return False

    def _eval():
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

    def _pos(node):
        '''World rotate-pivot: a cluster handle's translate sits at the
        origin with its pivot on the CV, so translate says nothing.'''
        return cmds.xform(node, q=1, ws=1, rp=1)

    def _travel(a, b):
        return sum((b[i] - a[i]) ** 2 for i in range(3)) ** 0.5

    # mid_rot only has influence in SplineIK mode, which is position 0
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    ikfk_attr = rt_naming.fstr(rigname, rt_constants.IKFK)
    has_switch = (cmds.objExists(cog_ctrl)
                  and cmds.attributeQuery(ikfk_attr, n=cog_ctrl, ex=1))
    saved_mode = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}') if has_switch else None
    if has_switch:
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', 0)

    saved_rot = cmds.getAttr(plug)
    cmds.setAttr(plug, 0)
    _eval()
    before = {n: _pos(n) for n in (top, end_cluster, tip)}
    cmds.setAttr(plug, amount)
    _eval()
    after = {n: _pos(n) for n in (top, end_cluster, tip)}

    cmds.setAttr(plug, saved_rot)
    if has_switch:
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', saved_mode)
    _eval()

    top_travel = _travel(before[top], after[top])
    cluster_travel = _travel(before[end_cluster], after[end_cluster])
    tip_travel = _travel(before[tip], after[tip])

    print(f'  mid_rot.rotate{axis.upper()} 0 -> {amount}')
    print(f'  {"top control":16s} travelled {top_travel:8.3f}')
    print(f'  {"end cluster":16s} travelled {cluster_travel:8.3f}')
    print(f'  {"tip joint":16s} travelled {tip_travel:8.3f}')

    if top_travel < 1e-4:
        print(f'\n  x top control did not move - mid_rot is not swinging it, '
              f'so nothing downstream can be judged')
        return False

    tracking = cluster_travel / top_travel
    follow = tip_travel / top_travel
    ok = tracking >= 1.0 - tolerance
    print(f'\n  tracking {tracking:5.3f}  '
          f'{"OK - constraints carry the swing" if ok else "x cluster is not following top"}')
    print(f'  follow   {follow:5.3f}  '
          f'(tip vs top; below 1 is the cubic driver curve, not a fault)')
    if ok and follow < 0.9:
        print(f'\n  the S-curve is the curve, not the rigging: the tip span '
              f'is still weighted by the fixed mid CV.\n  NUM_CTRL_IK is '
              f'{rt_constants.NUM_CTRL_IK}; raising it narrows each CV\'s '
              f'influence and tightens the tip.')

    print('RESULT:', 'PASS' if ok else 'FAIL')
    return ok


def test_solver_curve_shape(rigname='tail', amount=-45.0, axis='Z',
                            tolerance=0.02):
    '''
    Measure whether bending the tail puts an S in the SOLVER curve
    (MUTATES, restores).

    test_spline_mid_rot measures how far the ENDS travel, which is what
    separates a dropped constraint from curve geometry - but it never
    looks between them, so it passes on an S-curved tail. This looks at
    the shape.

    Two metrics do NOT work here, both tried:

      chord     deviation from the curve's own chord. A bent tail is
                supposed to leave its chord, so it grows with the bend
                whether the shape is right or wrong.
      tangent   the angle between a CV's correction and the curve tangent
                there. That one cannot fail: the aimMatrix aligns its
                primary axis TO the tangent, so the bake fixes that angle
                and any frame built this way preserves it. It reported
                0.00 drift on every CV of a tail that still had the S.

    What is left unconstrained by the aim, and is therefore what this
    measures, is the correction's position ACROSS the curve: each solver
    CV's distance to the driver curve. The correction is a rigid offset,
    so if its frame really turns with the curve that distance is a
    property of the rest pose and holds through a bend. A frame that goes
    stale swings the offset relative to the curve and the distance moves
    with it - worst toward the tip, where the shape has turned furthest.
    The roll about the tangent is the part still taken from the base
    control, so this is aimed at the half that can still be wrong.

    Curvature flips are reported alongside as the visible symptom: with
    only mid_rot rotated the driver curve bends one way and flips zero
    times, so any flip the solver curve has and the driver curve does not
    IS the S. It is reported rather than failed on, since a pose that
    genuinely S-curves the driver curve would flip both.

    Arguments
        rigname (str): Rig part to test
        amount (float): Degrees to rotate mid_rot
        axis (str): Local axis of mid_rot to rotate about
        tolerance (float): Allowed drift, as a fraction of the tail's length

    Return
        bool: True if every correction held its distance to the curve
    '''
    print(f'\n=== SOLVER CURVE SHAPE: {rigname} ===\n')

    typ = rt_constants.TYPE_IK
    mid_rot = rt_naming.fstr(rigname, rt_constants.SPLINE_MID_ROT, typ)
    driver = rt_naming.fstr(rigname, rt_constants.CURVE, typ)
    solver = rt_naming.fstr(rigname, rt_constants.CURVE, typ, TAG='spline')
    for node in (mid_rot, driver, solver):
        if not cmds.objExists(node):
            print(f'  x {node} not found - build IK first')
            return False

    solver_shape = cmds.listRelatives(solver, s=1, ni=1)[0]
    driver_shape = cmds.listRelatives(driver, s=1, ni=1)[0]
    num_cv = cmds.getAttr(f'{solver}.controlPoints', size=True)

    plug = f'{mid_rot}.rotate{axis.upper()}'
    if cmds.listConnections(plug, s=1, d=0, p=1):
        print(f'  x {plug} is driven by a connection')
        return False

    def _eval():
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

    def _sub(a, b):
        return [a[k] - b[k] for k in range(3)]

    def _dot(a, b):
        return sum(a[k] * b[k] for k in range(3))

    def _norm(a):
        return _dot(a, a) ** 0.5

    def _across():
        '''
        Each solver CV's distance to the driver curve.

        Through MFnNurbsCurve rather than cmds: nearestPointOnCurve is a
        node type, not a command, and building one per sample would put
        the thing being measured into the graph that computes it.
        '''
        sel = om.MSelectionList()
        sel.add(driver_shape)
        fn = om.MFnNurbsCurve(sel.getDagPath(0))
        out = list()
        for i in range(num_cv):
            cv = cmds.xform(f'{solver_shape}.cv[{i}]', q=1, ws=1, t=1)
            point = om.MPoint(cv[0], cv[1], cv[2])
            near = fn.closestPoint(point, space=om.MSpace.kWorld)
            out.append(point.distanceTo(near))
        return out

    def _flips(curve):
        '''
        Curvature sign changes along a curve, in its own bend plane.

        Nearly straight stretches have a curvature vector that is mostly
        rounding, and its sign flips at random - so the threshold is a
        fraction of the biggest curvature on this curve rather than a
        fixed epsilon, which counted noise as flips.
        '''
        pts = [cmds.pointOnCurve(curve, pr=p / 40.0, top=1, p=1)
               for p in range(41)]
        kurv = [[pts[j-1][k] - 2 * pts[j][k] + pts[j+1][k] for k in range(3)]
                for j in range(1, len(pts) - 1)]
        ref = max(kurv, key=_norm)
        floor = _norm(ref) * 0.05
        signs = [_dot(k, ref) for k in kurv if _norm(k) > floor]
        return sum(1 for a, b in zip(signs, signs[1:]) if a * b < 0)

    # mid_rot only has influence in SplineIK mode, which is position 0
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    ikfk_attr = rt_naming.fstr(rigname, rt_constants.IKFK)
    has_switch = (cmds.objExists(cog_ctrl)
                  and cmds.attributeQuery(ikfk_attr, n=cog_ctrl, ex=1))
    saved_mode = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}') if has_switch else None
    if has_switch:
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', 0)

    saved_rot = cmds.getAttr(plug)
    cmds.setAttr(plug, 0)
    _eval()
    rest = _across()
    rest_flips = (_flips(solver), _flips(driver))
    cmds.setAttr(plug, amount)
    _eval()
    bent = _across()
    bent_flips = (_flips(solver), _flips(driver))

    cmds.setAttr(plug, saved_rot)
    if has_switch:
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', saved_mode)
    _eval()

    # Scale-relative: the drift that matters is how big it is against the
    # tail, and these run from a few units long to a few hundred
    span = max(rest) if max(rest) > 1e-6 else 1.0
    drift = [(i, abs(b - r) / span) for i, (r, b) in enumerate(zip(rest, bent))]

    print(f'  mid_rot.rotate{axis.upper()} 0 -> {amount}, {num_cv} solver '
          f'CVs, distances to {driver}\n')
    print(f'  {"cv":>4s} {"rest":>9s} {"bent":>9s} {"drift":>9s}')
    for i, d in drift:
        mark = '' if d <= tolerance else '  x'
        print(f'  {i:4d} {rest[i]:9.4f} {bent[i]:9.4f} {d * 100:8.2f}%{mark}')

    worst_cv, worst = max(drift, key=lambda pair: pair[1])
    ok = worst <= tolerance
    print(f'\n  worst drift {worst * 100:.2f}% at CV {worst_cv} of {num_cv}  '
          f'{"OK" if ok else "x CORRECTION IS NOT FOLLOWING THE CURVE"}')
    print(f'  curvature flips  rest: solver {rest_flips[0]}, driver '
          f'{rest_flips[1]}')
    print(f'                   bent: solver {bent_flips[0]}, driver '
          f'{bent_flips[1]}')
    if bent_flips[0] > bent_flips[1]:
        print(f'    the solver curve changes direction where the driver '
              f'curve does not - that is the S')
    if not ok:
        print(f'\n  the correction is turning with something other than the '
              f'curve. rig_tail_curve.wire_aim_frame is what aims it, and '
              f'its ROLL about the tangent still comes from the base '
              f'control (base_up_node) - which cannot see a local bend.')

    print('RESULT:', 'PASS' if ok else 'FAIL')
    return ok


def bn_joints(rigname):
    '''
    The tail's BN chain, read from the scene when the cache is empty.

    JOINTS_BN is build-time state, not something the scene carries, so any
    module reload leaves it empty - and the worktree Reload button purges
    rig_tail_constants outright, by design, so a branch's own templates are
    the ones that answer. A test that trusted the cache would then report a
    rig with no joints, which reads as a broken rig rather than an empty
    dict. detect_joints_bn is the same non-destructive scan Setup and the
    build both start from: no renaming, no FK/IK duplication.

    Arguments
        rigname (str): Rig part

    Return
        list: BN joint names, empty when the scene holds no chain for it
    '''
    joints = rt_constants.JOINTS_BN.get(rigname) or []
    if not joints:
        rt_cleanup.detect_joints_bn()
        joints = rt_constants.JOINTS_BN.get(rigname) or []
    return joints


def test_stretch(rigname='tail', amount=10.0, tolerance=0.02):
    '''
    Verify the Stretch slider lengthens the BN chain in the modes that own
    a stretch mechanism, and by the SAME amount in each (MUTATES, restores
    what it touches).

    Three failures this is placed to catch:

      inert     a per-joint multiply seeded from a channel that holds no
                rest length evaluates to zero times the ratio and moves
                nothing, with no error and no warning. Measuring the chain
                is the only thing that sees it.
      mismatch  a mode scaling the dial differently from the others makes
                a switch pop whenever stretch is dialled in.
      not rest  FK writes a delta onto a channel whose rest is 0, so an
                absolute value there shows up as a chain already wrong
                before anything is dialled.

    Measured as the summed distance between consecutive BN joints, which is
    what 'the tail got longer' means whichever mode's network produced it.

    The dial is proved to reach the stretch remap before anything is
    measured. A tail whose override flag reads Cog takes the cog's ALL
    value, so sweeping its basectrl moves nothing at all - which looks
    exactly like a rig that cannot stretch, and sends you hunting the
    wrong end.

    Every mode is held to it. FK adds the dial onto its ratio; each IK set
    spends it on spreading its own control row
    (rt_stretch.connect_stretch_to_ik_controls), and all of them should
    land in the same place. Mode positions are the contract here, not
    names: [0]=SplineIK, [1]=IK, [2]=Float, [3]=FK.

    Arguments
        rigname (str): Rig part to test
        amount (float): Stretch dial value to sweep to
        tolerance (float): Allowed relative difference between modes

    Return
        bool: True if every mode stretched, and all modes agreed
    '''
    print(f'\n=== STRETCH CHECK: {rigname} ===\n')

    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    if not cmds.objExists(basectrl):
        print(f'  x basectrl {basectrl} not found - build the rig first')
        return False
    if not cmds.attributeQuery('stretch', n=basectrl, ex=1):
        print(f'  x no stretch attribute on {basectrl} - built without '
              f'Stretchy?')
        return False
    bn = bn_joints(rigname)
    if len(bn) < 3:
        print(f'  x need at least 3 BN joints, found {len(bn)} - is '
              f"'{rigname}' spelled as it is in RIGPARTS?")
        return False

    def _eval():
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

    def _length():
        '''Summed distance along the BN chain.'''
        pos = [cmds.xform(j, q=1, ws=1, t=1) for j in bn]
        return sum(
            sum((pos[i + 1][k] - pos[i][k]) ** 2 for k in range(3)) ** 0.5
            for i in range(len(pos) - 1))

    ikfk_attr = rt_naming.fstr(rigname, rt_constants.IKFK)
    has_switch = (cmds.objExists(cog_ctrl)
                  and cmds.attributeQuery(ikfk_attr, n=cog_ctrl, ex=1))
    modes = list()
    if has_switch:
        enum = cmds.attributeQuery(ikfk_attr, n=cog_ctrl, le=1)[0].split(':')
        modes = [(name, i) for i, name in enumerate(enum)]
    else:
        modes = [('(single mode)', None)]

    saved_mode = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}') if has_switch else None
    plug = f'{basectrl}.stretch'
    saved_stretch = cmds.getAttr(plug)
    if cmds.listConnections(plug, s=1, d=0, p=1):
        print(f'  x {plug} is driven by a connection - drive the dashboard '
              f'ALL value instead')
        return False

    # With the dashboard active the tail follows the cog's ALL value until
    # its own override flag reads Basectrl, so the dial this sweeps is read
    # by nothing. Flip the flag for the duration and put it back.
    override_plug = None
    saved_override = None
    if rt_ctrlall.active() and cmds.objExists(cog_ctrl):
        override_attr = rt_naming.fstr(rigname, rt_constants.OVERRIDE)
        if cmds.attributeQuery(override_attr, n=cog_ctrl, ex=1):
            override_plug = f'{cog_ctrl}.{override_attr}'
            saved_override = cmds.getAttr(override_plug)
            cmds.setAttr(override_plug, 1)

    # Nothing downstream can stretch if the dial does not reach the remap,
    # and a dead dial reads exactly like a rig that cannot stretch. Prove
    # the signal arrives before blaming what it drives.
    stretch_remap = f'{rigname}_stretch_remap_multiplyDivide'
    if cmds.objExists(stretch_remap):
        cmds.setAttr(plug, amount)
        _eval()
        reached = cmds.getAttr(f'{stretch_remap}.outputX')
        cmds.setAttr(plug, saved_stretch)
        if abs(reached) < 1e-6:
            print(f'  x {plug} does not reach {stretch_remap} - the dial is '
                  f'routed elsewhere, so this measures nothing')
            if override_plug is not None:
                cmds.setAttr(override_plug, saved_override)
            return False

    # Every mode owns a stretch mechanism: each IK set spreads its own
    # controls, FK adds the dial onto its ratio. So none is exempt, and a
    # mode that does not grow is a failure rather than a known gap.
    ok = True
    grew = dict()
    for mode_name, mode_val in modes:
        if mode_val is not None:
            cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', mode_val)
        cmds.setAttr(plug, 0)
        _eval()
        rest = _length()
        cmds.setAttr(plug, amount)
        _eval()
        stretched = _length()

        delta = stretched - rest
        ratio = stretched / rest if rest > 1e-6 else 0.0
        good = delta > 1e-4
        if not good:
            ok = False
        grew[mode_name] = delta
        note = 'OK' if good else 'x DID NOT STRETCH'
        print(f'  {mode_name:12s} rest={rest:8.3f}  stretched={stretched:8.3f}'
              f'  x{ratio:5.3f}  {note}')

    # Each mode reaches its length by a different route, so a disagreement
    # here is what pops the tail on a switch mid-dial
    if len(grew) > 1 and ok:
        biggest = max(grew.values())
        smallest = min(grew.values())
        spread = (biggest - smallest) / biggest if biggest > 1e-6 else 0.0
        agree = spread <= tolerance
        if not agree:
            ok = False
            worst = max(grew, key=grew.get)
            least = min(grew, key=grew.get)
            print(f'\n  x modes disagree by {spread * 100:.1f}%: '
                  f"'{worst}' grew {biggest:.3f}, '{least}' grew "
                  f'{smallest:.3f}')
        else:
            print(f'\n  modes agree within {spread * 100:.1f}%')

    cmds.setAttr(plug, saved_stretch)
    if override_plug is not None:
        cmds.setAttr(override_plug, saved_override)
    if has_switch:
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', saved_mode)
    _eval()

    print('RESULT:', 'PASS' if ok else 'FAIL')
    return ok


def test_matrix_opm(rigname='tail', count=0):
    '''
    Validate each BN joint's offsetParentMatrix against the expected
    parent-space matrix (parent_world.inverse() * ik_world) for joints 1-3.
    Returns True if every translation error is within tolerance, else False.
    '''
    print(f'\n=== MANUAL MATRIX CHECK: (COUNT: {count}) ===\n')

    max_error = 0.0
    for joint_idx in range(1,4):
        ik_jnt = f'IK_{rigname}_{joint_idx:02d}_jnt'
        bn_jnt = f'BN_{rigname}_{joint_idx:02d}_jnt'
        bn_parent = f'BN_{rigname}_{joint_idx-1:02d}_jnt'

        # World matrices
        ik_world = om.MMatrix(cmds.getAttr(f'{ik_jnt}.worldMatrix[0]'))
        parent_world = om.MMatrix(cmds.getAttr(f'{bn_parent}.worldMatrix[0]'))
        parent_inv = parent_world.inverse()

        # Expected local matrix (parent space)
        expected_local = parent_inv * ik_world
        # Actual offsetParentMatrix
        opm = om.MMatrix(cmds.getAttr(f'{bn_jnt}.offsetParentMatrix'))
        print_matrix(opm, f'{bn_jnt}.offsetParentMatrix')

        # Extract translations
        def t(m):
            return [m[12], m[13], m[14]]

        expected_trans = t(expected_local)
        actual_trans = t(opm)
        trans_error = sum((expected_trans[i] - actual_trans[i])**2 for i in range(3)) ** 0.5
        max_error = max(max_error, trans_error)

        if trans_error > 0.001:
            print('❌ offsetParentMatrix mismatch')
            print('   Matrix wiring or bind offset is incorrect')
        else:
            print('✅ offsetParentMatrix correct')
            print('   Bind offset + parent-space math validated')
        print(f'Expected OPM translation: {[round(v, 4) for v in expected_trans]}')
        print(f'Actual   OPM translation: {[round(v, 4) for v in actual_trans]}')
        print(f'Translation error: {trans_error:.6f}\n')

    return max_error <= 0.001

def test_alignment(rigname='tail', count=0):
    '''
    Compare BN world positions against their IK (or FK) reference joints.
    Returns True when the worst misalignment is under 0.1 units, False when
    it exceeds that, or None when there is nothing to compare.
    '''
    print(f'\n=== ALIGNMENT CHECK (COUNT: {count}) ===\n')

    if rigname not in rt_constants.JOINTS_BN:
        print(f'× No BN joints for {rigname}')
        return None

    bn_joints = rt_constants.JOINTS_BN[rigname]
    ik_joints = rt_constants.JOINTS_IK[rigname]
    fk_joints = rt_constants.JOINTS_FK[rigname]
    opm_conn = list()

    if not ik_joints and not fk_joints:
        print('× No IK or FK joints to compare')
        return None

    ref_joints = ik_joints if ik_joints else fk_joints
    ref_type = 'IK' if ik_joints else 'FK'

    print(f'Comparing BN vs {ref_type}:\n')
    max_dist = 0
    worst_idx = 0

    for i, (bn_jnt, ref_jnt) in enumerate(zip(bn_joints, ref_joints)):
        bn_pos = cmds.xform(bn_jnt, q=1, ws=1, t=1)
        ref_pos = cmds.xform(ref_jnt, q=1, ws=1, t=1)
        dist = sum((bn_pos[j] - ref_pos[j])**2 for j in range(3)) ** 0.5

        if dist > max_dist:
            max_dist = dist
            worst_idx = i

        status = '✓' if dist < 0.1 else '⚠️' if dist < 1.0 else '❌'
        print(f'  Joint {i:02d}: {status} distance = {dist:.4f}')

    print(f'\nMax misalignment: {max_dist:.4f} units at joint {worst_idx:02d}')

    if max_dist < 0.01:
        print('✅ Alignment EXCELLENT')
    elif max_dist < 0.1:
        print('⚠️  Alignment GOOD')
    else:
        print('❌ Alignment BAD')
        if count > 0:
            print(f'❌ Joints are misaligned (Count: {count})')
        else:
            print('❌ Joints are misaligned BEFORE matrix wiring!')
            print('   This means BN joints were not duplicated from IK/FK correctly.')
    print()
    return max_dist < 0.1

def show_data_flow(rigname='tail', joint_idx=0):
    '''
    Show complete data flow diagram for per-FX matrix architecture.
    '''
    header = f'''
================================================================================
          DATA FLOW DIAGRAM (Per-FX Matrix): {rigname} Joint {joint_idx:02d}
================================================================================
'''
    print(header)

    NN = joint_idx

    if rigname not in rt_constants.JOINTS_BN:
        print(f'❌ No BN joints for {rigname}')
        return

    bn_jnt = rt_constants.JOINTS_BN[rigname][joint_idx]
    ik_jnt = rt_constants.JOINTS_IK[rigname][joint_idx] if rigname in rt_constants.JOINTS_IK else None
    fk_jnt = rt_constants.JOINTS_FK[rigname][joint_idx] if rigname in rt_constants.JOINTS_FK else None

    section_header = '''NODE GRAPH:
--------------------------------------------------------------------------------
'''
    print(section_header)

    if joint_idx == 0:
        graph = f'''  {ik_jnt}.worldMatrix ────┬──> blendMatrix.inputMatrix
  {fk_jnt}.worldMatrix ────┴──> blendMatrix.target[0].targetMatrix
  cog_ctrl.ikfk_switch ───────────> blendMatrix.envelope (via condition)

                                     blendMatrix.outputMatrix
                                           │
                                           ▼
  parent.parentInverseMatrix ──────> multMatrix.matrixIn[0]
  blendMatrix.outputMatrix ────────> multMatrix.matrixIn[1]
                                           │
                                     multMatrix.matrixSum
                                           │
                                           ▼
                              {bn_jnt}.offsetParentMatrix

                   BN joint local translate/rotate = 0
                   (NO FX applied to joint 00)
'''
    else:
        graph = f'''  {ik_jnt}.worldMatrix ────┬──> blendMatrix.inputMatrix
  {fk_jnt}.worldMatrix ────┴──> blendMatrix.target[0].targetMatrix
  cog_ctrl.ikfk_switch ───────────> blendMatrix.envelope (via condition)

                                     blendMatrix.outputMatrix (baseWorld)
                                           │
                                           ▼
  curl_composeMatrix.outputMatrix ─────┬──> fxCombined_multMatrix.matrixIn[0]
  wave_composeMatrix.outputMatrix ─────┼──> fxCombined_multMatrix.matrixIn[1]
  dyn_composeMatrix.outputMatrix ──────┴──> fxCombined_multMatrix.matrixIn[2]
                                           │
                                     fxCombined_multMatrix.matrixSum (fxCombined)
                                           │
                                           ▼
  blendMatrix.outputMatrix (baseWorld) ──> baseFX_multMatrix.matrixIn[0]
  fxCombined_multMatrix.matrixSum ────────> baseFX_multMatrix.matrixIn[1]
                                           │
                                     baseFX_multMatrix.matrixSum (baseFX)
                                           │
                                           ▼
  parent.parentInverseMatrix ──────────> fxLocal_multMatrix.matrixIn[0]
  baseFX_multMatrix.matrixSum ─────────> fxLocal_multMatrix.matrixIn[1]
                                           │
                                     fxLocal_multMatrix.matrixSum (fxLocal)
                                           │
                                           ▼
                              {bn_jnt}.offsetParentMatrix

                   BN joint local translate/rotate = 0

KEY CHANGE: FX applied to baseWorld BEFORE converting to parent space
           This ensures FX rotates around joint's own pivot, not endpoint
'''

    print(graph)

    section_header = '''
VALUES:
--------------------------------------------------------------------------------'''
    print(section_header)

    blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
    if cmds.objExists(blend_mtx):
        envelope = cmds.getAttr(f'{blend_mtx}.envelope')
        print(f'  blendMatrix.envelope = {envelope:.3f}')

    if joint_idx > 0:
        fx_list = ['curl', 'wave', 'dyn']
        for fx_name in fx_list:
            fx_compose = f'{rigname}_{NN:02d}_{fx_name}_composeMatrix'
            if cmds.objExists(fx_compose):
                print(f'\n  {fx_name}_composeMatrix:')
                for axis in ['X', 'Y', 'Z']:
                    val = cmds.getAttr(f'{fx_compose}.inputRotate{axis}')
                    if abs(val) > 0.001:
                        print(f'    inputRotate{axis} = {val:.3f}°')

    opm_conn = cmds.listConnections(f'{bn_jnt}.offsetParentMatrix', s=1, d=0, p=1)
    if opm_conn:
        print(f'\n  BN.offsetParentMatrix ← {opm_conn[0]}')
    else:
        print(f'\n  BN.offsetParentMatrix: NOT CONNECTED')

    bn_t = [round(v, 3) for v in cmds.getAttr(f'{bn_jnt}.translate')[0]]
    bn_r = [round(v, 3) for v in cmds.getAttr(f'{bn_jnt}.rotate')[0]]
    bn_w_t = [round(v, 3) for v in cmds.xform(bn_jnt, q=1, ws=1, t=1)]
    print(f'  {bn_jnt}.translate (local) = {bn_t} (should be [0,0,0])')
    print(f'  {bn_jnt}.rotate (local) = {bn_r} (should be [0,0,0])')
    print(f'  {bn_jnt}.worldTranslate = {bn_w_t}')
    print()


def test_wave(rigname='tail'):
    '''
    Quick test: Set wave attributes and check if values propagate.
    '''
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)

    header = '''
=== QUICK WAVE TEST ==='''
    print(header)

    if not cmds.objExists(basectrl):
        print(f'✗ Base control missing: {basectrl}')
        return

    setup_msg = '''Setting wave attributes:
  waveZ = 5
  wave_frequency = 2
  wave_speed = 1'''
    print(setup_msg)

    cmds.setAttr(f'{basectrl}.noise', 0)
    cmds.setAttr(f'{basectrl}.curlX', 0)
    cmds.setAttr(f'{basectrl}.curlY', 0)
    cmds.setAttr(f'{basectrl}.curlZ', 0)
    cmds.setAttr(f'{basectrl}.waveX', 0)
    cmds.setAttr(f'{basectrl}.waveY', 0)
    cmds.setAttr(f'{basectrl}.waveZ', 5)
    cmds.setAttr(f'{basectrl}.wave_frequency', 2)
    cmds.setAttr(f'{basectrl}.wave_speed', 1)

    # Force evaluation
    cur = cmds.currentTime(q=1)
    cmds.currentTime(cur + 0.01, edit=True)
    cmds.currentTime(cur, edit=True)

    print('\nChecking wave_composeMatrix outputs:')
    for joint_idx in [2, 5, 10]:
        for axis in ['X', 'Y', 'Z']:
            compose_node = f'{rigname}_{joint_idx:02d}_wave_composeMatrix'
            if cmds.objExists(compose_node):
                val = cmds.getAttr(f'{compose_node}.inputRotate{axis}')
                if abs(val) > 0.001:
                    print(f'  Joint {joint_idx:02d} wave rotate{axis}: {val:.4f}° ✓')

    compose_node = f'{rigname}_02_wave_composeMatrix'
    if cmds.objExists(compose_node):
        val = cmds.getAttr(f'{compose_node}.inputRotateZ')
        if abs(val) > 0.001:
            print('\n✓ Wave appears to be producing output')
        else:
            error_msg = '''
✗ Wave output is zero - check expressions and time sources'''
            print(error_msg)
    else:
        print(f'\n✗ Wave composeMatrix not found: {compose_node}')
    print()


def test_curl(rigname='tail'):
    '''
    Quick test: Set curl attributes and check if values propagate.
    '''
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)

    print('\n=== QUICK CURL TEST ===\n')

    if not cmds.objExists(basectrl):
        print(f'✗ Base control missing: {basectrl}')
        return

    print('Setting curl attributes:')
    print('  curlZ = 5')
    print('  curl_falloff = 2')

    cmds.setAttr(f'{basectrl}.noise', 0)
    cmds.setAttr(f'{basectrl}.waveX', 0)
    cmds.setAttr(f'{basectrl}.waveY', 0)
    cmds.setAttr(f'{basectrl}.waveZ', 0)
    cmds.setAttr(f'{basectrl}.curlX', 0)
    cmds.setAttr(f'{basectrl}.curlY', 0)
    cmds.setAttr(f'{basectrl}.curlZ', 5)
    cmds.setAttr(f'{basectrl}.curl_falloff', 2)

    # Force evaluation
    cmds.dgeval(basectrl)

    print('\nChecking curl_composeMatrix outputs:')
    has_output = False
    for joint_idx in [2, 5, 10]:
        compose_node = f'{rigname}_{joint_idx:02d}_curl_composeMatrix'
        if cmds.objExists(compose_node):
            val = cmds.getAttr(f'{compose_node}.inputRotateZ')
            if abs(val) > 0.001:
                print(f'  Joint {joint_idx:02d} curlZ: {val:.4f}° ✓')
                has_output = True

    if has_output:
        print('\n✓ Curl appears to be working')
    else:
        print('\n✗ Curl output is zero - check DG connections')
    print()

def decompose_mtx(m):
    if not isinstance(m, om.MMatrix):
        m = om.MMatrix(m)

    tm = om.MTransformationMatrix(m)
    t = tm.translation(om.MSpace.kWorld)
    euler = tm.rotation()
    rot = [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]
    scale = tm.scale(om.MSpace.kWorld)
    shear = tm.shear(om.MSpace.kWorld) # Edited: Added shear retrieval
    return [
        [round(v, 4) for v in (t.x, t.y, t.z)],
        [round(v, 4) for v in rot],
        [round(v, 4) for v in scale],
        [round(v, 4) for v in shear]
    ]

def print_matrix(data, name=''):
    print(f"-- {name} --")
    rows = []

    # Check if input is a 4x4 MMatrix or a flat list of 16
    if isinstance(data, om.MMatrix) or (isinstance(data, list) and len(data) == 16):
        m = om.MMatrix(data)
        for r in range(4):
            # Edited: Extract rows from 4x4 matrix
            rows.append([round(m[r*4 + c], 4) for c in range(4)])

    # Check if input is decomposed result (list of 3 or 4 components)
    elif isinstance(data, list) and len(data) in (3, 4):
        rows = data # Edited: Use the sub-lists (T, R, S, Sh) directly

    for row in rows:
        # Edited: Alignment for 4 decimal places
        formatted_row = '  '.join([f"{v:10.4f}" for v in row])
        print(f"[ {formatted_row} ]")

def print_chain(rigname='tail', joint_idx=6, count=0):
    '''
    Deep matrix-level diagnostic for a contiguous section of the BN / IK chain.
    Prints detailed information for joints 0 through joint_idx:
      - offsetParentMatrix (fully decomposed)
      - parent.worldMatrix
      - BN.worldMatrix and IK.worldMatrix
      - Incoming matrix connections
      - Local TRS, jointOrient, and inheritsTransform

    Intended use:
      - Debugging incorrect offsetParentMatrix math
      - Verifying parent-space conversion and matrix order
      - Tracing where rotation, scale, or shear is introduced
      - Validating matrix architecture during rig development

    Use dump_chain or print_chain_ends for quick checks.
    '''
    bn_joints = rt_constants.JOINTS_BN[rigname]
    ik_joints = rt_constants.JOINTS_IK[rigname] if rigname in rt_constants.JOINTS_IK else []
    N = min(joint_idx, len(bn_joints) - 1, len(ik_joints) - 1)

    print(f'--- PRINT CHAIN (CONDENSED) for first {N+1} joints (COUNT: {count}) ---')

    # helpers for gating
    def is_identity_trs(trs):
        t, r, s, sh = trs
        return (
            all(abs(v) < 0.001 for v in t) and
            all(abs(v) < 0.001 for v in r) and
            all(abs(v - 1.0) < 0.001 for v in s) and
            all(abs(v) < 0.001 for v in sh)
        )

    def fmt(label, trs, show_scale=False):
        t, r, s, sh = trs
        parts = [f'T={t}', f'R={r}']
        if show_scale and not all(abs(v - 1.0) < 0.001 for v in s):
            parts.append(f'S={s}')
        if not all(abs(v) < 0.001 for v in sh):
            parts.append(f'Sh={sh}')
        return f'  {label:<10}: ' + '  '.join(parts)

    for i in range(0, N + 1):
        bn = bn_joints[i]
        ik = ik_joints[i] if i < len(ik_joints) else None
        parent = cmds.listRelatives(bn, p=True, f=True)
        parent = parent[0] if parent else None

        print(f'\nJoint index {i:02d}: BN="{bn}" IK="{ik}" parent="{parent}"')
        print('-- MATRICES --')

        # BN offsetParentMatrix
        bn_opm = om.MMatrix(cmds.getAttr(f'{bn}.offsetParentMatrix'))
        bn_opm_trs = decompose_mtx(bn_opm)
        print(fmt('BN.OPM', bn_opm_trs, show_scale=True))

        # parent worldMatrix (skip for root)
        if parent:
            parent_world = om.MMatrix(cmds.getAttr(f'{parent}.worldMatrix[0]'))
            pw_trs = decompose_mtx(parent_world)
            if not is_identity_trs(pw_trs):
                print(fmt('PARENT.WLD', pw_trs))

        # BN worldMatrix
        bn_world = om.MMatrix(cmds.getAttr(f'{bn}.worldMatrix[0]'))
        bw_trs = decompose_mtx(bn_world)
        print(fmt('BN.WORLD', bw_trs))

        # IK worldMatrix
        if ik:
            ik_world = om.MMatrix(cmds.getAttr(f'{ik}.worldMatrix[0]'))
            iw_trs = decompose_mtx(ik_world)
            print(fmt('IK.WORLD', iw_trs))

        # connections
        print('-- Connections --')
        print('offsetParentMatrix inputs:',
              cmds.listConnections(f'{bn}.offsetParentMatrix', s=1, d=0, p=1))
        print('worldMatrix inputs:',
              cmds.listConnections(f'{bn}.worldMatrix[0]', s=1, d=0, p=1))

        # local TRS and jointOrient
        tx = cmds.getAttr(f'{bn}.translate')[0]
        rt = cmds.getAttr(f'{bn}.rotate')[0]
        sc = cmds.getAttr(f'{bn}.scale')[0]
        jo = cmds.getAttr(f'{bn}.jointOrient')[0]
        inh = cmds.getAttr(f'{bn}.inheritsTransform')

        print('-- Local TRS/jointOrient/inheritsTransform --')
        print('translate', tx, 'rotate', rt, 'scale', sc,
              'jointOrient', jo, 'inherits', inh)

    print(f'\n--- end print (COUNT: {count}) ---\n')

def print_chain_ends(rigname='tail', count=0):
    '''
    Targeted matrix diagnostic for the structural ends of the chain.

    Prints the same detailed information as print_chain, but only for:
      - The first two joints (root stability, bind correctness)
      - The last five joints (FX accumulation, falloff, tip behavior)

    Intended use:
      - Debugging issues that only appear at the root or tip
      - Checking FX buildup without mid-chain noise
      - Faster iteration during animation FX tuning

    This is a focused alternative to print_chain,
    not a replacement for full-chain inspection.
    '''
    bn_joints = rt_constants.JOINTS_BN[rigname]
    ik_joints = rt_constants.JOINTS_IK[rigname] if rigname in rt_constants.JOINTS_IK else []

    total = len(bn_joints)

    # first two + last five, clamped and de-duplicated
    indices = list(range(0, min(2, total))) + list(range(max(0, total - 5), total))
    indices = sorted(set(indices))

    print(f'--- PRINT CHAIN ENDS (COUNT: {count}) ---')
    print(f'Printing joints: {indices}\n')

    for i in indices:
        bn = bn_joints[i]
        ik = ik_joints[i] if i < len(ik_joints) else None
        parent = cmds.listRelatives(bn, p=True, f=True)
        parent = parent[0] if parent else None

        print(f'\nJoint index {i:02d}: BN="{bn}" IK="{ik}" parent="{parent}"')

        # BN offsetParentMatrix
        try:
            bn_opm_list = cmds.getAttr(f'{bn}.offsetParentMatrix')
            bn_opm = om.MMatrix(bn_opm_list)
            print_matrix(decompose_mtx(bn_opm), 'BN.offsetParentMatrix')
        except Exception as e:
            print('  could not read BN.offsetParentMatrix:', e)

        # parent worldMatrix
        try:
            if parent:
                parent_world = om.MMatrix(cmds.getAttr(f'{parent}.worldMatrix[0]'))
                print_matrix(decompose_mtx(parent_world), 'parent.worldMatrix')
        except Exception as e:
            print('  could not read parent.worldMatrix:', e)

        # BN worldMatrix
        try:
            bn_world = om.MMatrix(cmds.getAttr(f'{bn}.worldMatrix[0]'))
            print_matrix(decompose_mtx(bn_world), 'BN.worldMatrix')
        except Exception as e:
            print('  could not read BN.worldMatrix:', e)

        # IK worldMatrix
        if ik:
            try:
                ik_world = om.MMatrix(cmds.getAttr(f'{ik}.worldMatrix[0]'))
                print_matrix(decompose_mtx(ik_world), 'IK.worldMatrix')
            except Exception as e:
                print('  could not read IK.worldMatrix:', e)

        # connections
        print('-- Connections --')
        try:
            opl = cmds.listConnections(f'{bn}.offsetParentMatrix', s=1, d=0, p=1)
            print('offsetParentMatrix inputs:', opl)
        except:
            print('offsetParentMatrix inputs: <error>')

        try:
            wml = cmds.listConnections(f'{bn}.worldMatrix[0]', s=1, d=0, p=1)
            print('worldMatrix inputs:', wml)
        except:
            print('worldMatrix inputs: <error>')

        # local TRS + jointOrient
        try:
            tx = cmds.getAttr(f'{bn}.translate')[0]
            rt = cmds.getAttr(f'{bn}.rotate')[0]
            sc = cmds.getAttr(f'{bn}.scale')[0]
            jo = cmds.getAttr(f'{bn}.jointOrient')[0]
            inh = cmds.getAttr(f'{bn}.inheritsTransform')
            print('-- Local TRS/jointOrient/inheritsTransform --')
            print('translate', tx, 'rotate', rt, 'scale', sc, 'jointOrient', jo, 'inherits', inh)
        except Exception as e:
            print('  could not read local TRS/jointOrient:', e)

    print(f'\n--- end print (COUNT: {count}) ---\n')

def dump_chain(rigname='tail'):
    '''
    Lightweight joint-orientation and bind-pose sanity dump.

    Prints per-joint:
      - BN and IK jointOrient
      - BN and IK local rotation (object space)
      - A small raw slice of offsetParentMatrix

    Intended use:
      - Verifying BN joints were duplicated correctly from IK/FK
      - Spotting unexpected local rotations or jointOrient mismatches
      - Quick validation before deeper matrix debugging

    This function does NOT inspect:
      - Full world matrices
      - Parent-space math
      - Matrix wiring or connections
      - Local translate, scale, or inheritsTransform

    Use print_chain when matrix math or OPM wiring is suspect.
    '''
    bn_joints = rt_constants.JOINTS_BN[rigname]
    ik_joints = rt_constants.JOINTS_IK[rigname]
    for i, bn in enumerate(bn_joints):
        ik = ik_joints[i]
        bjo = cmds.getAttr(f'{bn}.jointOrient')[0] if cmds.attributeQuery('jointOrient', node=bn, exists=True) else (0,0,0)
        kjo = cmds.getAttr(f'{ik}.jointOrient')[0] if cmds.attributeQuery('jointOrient', node=ik, exists=True) else (0,0,0)
        b_local = cmds.xform(bn, q=1, ro=1, ws=0)
        i_local = cmds.xform(ik, q=1, ro=1, ws=0)
        opm = cmds.getAttr(f'{bn}.offsetParentMatrix')
        print(i, bn, 'JO', bjo, 'IK.JO', kjo, 'BN_local_rot', b_local, 'IK_local_rot', i_local, 'opm', opm[0:4])

def test_time_evaluation(rigname='tail'):
    '''
    Comprehensive test for time-dependent expression evaluation.
    Tests wave, noise, and loop modulo system.
    '''
    header = '''
================================================================================
                        TIME EVALUATION DIAGNOSTIC
================================================================================
'''
    print(header)

    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)
    if not cmds.objExists(basectrl):
        print(f'✗ Base control missing: {basectrl}')
        return

    joints = rt_constants.JOINTS_BN.get(rigname, [])
    if len(joints) < 3:
        print('✗ Not enough joints for time test')
        return

    issues = []
    warnings = []

    # === EXPRESSION EXISTENCE CHECK ===
    section_header = '''EXPRESSION NODES:
--------------------------------------------------------------------------------'''
    print(section_header)

    test_joint = 2
    NN = rt_naming.get_index_from_name(joints[test_joint])

    wave_expressions = []
    noise_expressions = []
    loop_expression = f'{rigname}_loop_time_expression'

    for axis in ['X', 'Y', 'Z']:
        wave_expr = f'{rigname}_{NN:02d}_wave{axis}_expression'
        noise_expr = f'{rigname}_{NN:02d}_noise_{axis}_expression'

        if cmds.objExists(wave_expr):
            wave_expressions.append(wave_expr)
            print(f'  ✓ Found wave expression: {wave_expr}')
        else:
            issues.append(f'Missing wave expression: {wave_expr}')

        if cmds.objExists(noise_expr):
            noise_expressions.append(noise_expr)
            print(f'  ✓ Found noise expression: {noise_expr}')
        else:
            issues.append(f'Missing noise expression: {noise_expr}')

    if cmds.objExists(loop_expression):
        print(f'  ✓ Found loop expression: {loop_expression}')
    else:
        warnings.append(f'Loop expression not found: {loop_expression}')

    print()

    # === TIME DEPENDENCY CHECK ===
    section_header = '''TIME DEPENDENCY CHECK:
--------------------------------------------------------------------------------'''
    print(section_header)

    for expr in wave_expressions + noise_expressions:
        expr_code = cmds.expression(expr, q=1, s=1)

        # Check if expression references time
        has_time_ref = 'time1.outTime' in expr_code or '$t' in expr_code

        if has_time_ref:
            print(f'  ✓ {expr}: References time source')
        else:
            issues.append(f'{expr}: Does NOT reference time')

        # Check for stale conditionals (shouldn't have any)
        if 'if ($i ==' in expr_code:
            issues.append(f'{expr}: Contains conditional writes (OLD ARCHITECTURE)')

    print()

    # === SETUP TEST ATTRIBUTES ===
    section_header = '''SETTING UP TEST:
--------------------------------------------------------------------------------'''
    print(section_header)

    # Clear all effects
    cmds.setAttr(f'{basectrl}.waveX', 0)
    cmds.setAttr(f'{basectrl}.waveY', 0)
    cmds.setAttr(f'{basectrl}.waveZ', 0)
    cmds.setAttr(f'{basectrl}.curlX', 0)
    cmds.setAttr(f'{basectrl}.curlY', 0)
    cmds.setAttr(f'{basectrl}.curlZ', 0)
    cmds.setAttr(f'{basectrl}.noise', 0)

    # Set wave attributes
    cmds.setAttr(f'{basectrl}.waveZ', 5.0)
    cmds.setAttr(f'{basectrl}.wave_frequency', 2.0)
    cmds.setAttr(f'{basectrl}.wave_speed', 1.0)
    cmds.setAttr(f'{basectrl}.wave_falloff', 1.0)

    # Set noise attribute
    cmds.setAttr(f'{basectrl}.noise', 3.0)

    print('  Wave Z amplitude: 5.0')
    print('  Noise amplitude: 3.0')
    print('  Testing at frames: 1, 10, 20')
    print()

    # === TIME-BASED VALUE CHECK ===
    section_header = '''TIME-BASED VALUE CHANGES:
--------------------------------------------------------------------------------'''
    print(section_header)

    test_frames = [1, 10, 20]
    compose_node_wave = f'{rigname}_{NN:02d}_wave_composeMatrix'
    compose_node_noise = f'{rigname}_{NN:02d}_noise_composeMatrix'

    if not cmds.objExists(compose_node_wave):
        issues.append(f'Wave composeMatrix missing: {compose_node_wave}')
    if not cmds.objExists(compose_node_noise):
        issues.append(f'Noise composeMatrix missing: {compose_node_noise}')

    wave_values = []
    noise_values = []

    for frame in test_frames:
        cmds.currentTime(frame, edit=True)

        # Force DG evaluation
        cmds.dgeval(compose_node_wave, compose_node_noise)

        wave_z = cmds.getAttr(f'{compose_node_wave}.inputRotateZ')
        noise_x = cmds.getAttr(f'{compose_node_noise}.inputRotateX')

        wave_values.append(wave_z)
        noise_values.append(noise_x)

        print(f'  Frame {frame:3d}: Wave Z = {wave_z:8.4f}°  |  Noise X = {noise_x:8.4f}°')

    print()

    # Check if values are changing
    wave_range = max(wave_values) - min(wave_values)
    noise_range = max(noise_values) - min(noise_values)

    print(f'  Wave Z range: {wave_range:.4f}°')
    print(f'  Noise X range: {noise_range:.4f}°')
    print()

    if wave_range < 0.001:
        issues.append('Wave values NOT CHANGING over time (time evaluation BROKEN)')
    else:
        print('  ✓ Wave values changing with time')

    if noise_range < 0.001:
        issues.append('Noise values NOT CHANGING over time (time evaluation BROKEN)')
    else:
        print('  ✓ Noise values changing with time')

    print()

    # === LOOP MODULO TEST ===
    if cmds.objExists(loop_expression):
        section_header = '''LOOP MODULO TEST:
--------------------------------------------------------------------------------'''
        print(section_header)

        # Test with loop disabled
        cmds.setAttr(f'{basectrl}.loop', 0)
        cmds.setAttr(f'{basectrl}.loop_frame', 100)

        cmds.currentTime(1, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_1 = cmds.getAttr(f'{loop_expression}.output[0]')

        cmds.currentTime(150, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_150 = cmds.getAttr(f'{loop_expression}.output[0]')

        print(f'  Loop DISABLED:')
        print(f'    Frame 1   → loop time = {loop_val_1:.2f}')
        print(f'    Frame 150 → loop time = {loop_val_150:.2f}')

        if abs(loop_val_1 - 1) < 0.1 and abs(loop_val_150 - 150) < 0.1:
            print('    ✓ Pass-through working (time = frame)')
        else:
            warnings.append('Loop pass-through values unexpected')

        # Test with loop enabled
        cmds.setAttr(f'{basectrl}.loop', 1)

        cmds.currentTime(50, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_50 = cmds.getAttr(f'{loop_expression}.output[0]')

        cmds.currentTime(150, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_150_mod = cmds.getAttr(f'{loop_expression}.output[0]')

        print(f'\n  Loop ENABLED (loop_frame = 100):')
        print(f'    Frame 50  → loop time = {loop_val_50:.2f}')
        print(f'    Frame 150 → loop time = {loop_val_150_mod:.2f} (should be ~50)')

        if abs(loop_val_150_mod - 50) < 1.0:
            print('    ✓ Modulo working correctly')
        else:
            issues.append(f'Loop modulo BROKEN: expected ~50, got {loop_val_150_mod:.2f}')

        print()

    # === EXPRESSION CODE INSPECTION ===
    section_header = '''EXPRESSION CODE INSPECTION:
--------------------------------------------------------------------------------'''
    print(section_header)

    sample_expr = wave_expressions[0] if wave_expressions else None
    if sample_expr:
        expr_code = cmds.expression(sample_expr, q=1, s=1)
        print(f'Sample expression code ({sample_expr}):')
        print()
        for line in expr_code.split('\n')[:10]:
            print(f'  {line}')
        if expr_code.count('\n') > 10:
            print('  ...')
        print()

    # === FINAL REPORT ===
    section_header = '''DIAGNOSIS:
--------------------------------------------------------------------------------'''
    print(section_header)

    if issues:
        print('✗ CRITICAL ISSUES FOUND:')
        for issue in issues:
            print(f'  • {issue}')
        print()

    if warnings:
        print('⚠️  WARNINGS:')
        for warning in warnings:
            print(f'  • {warning}')
        print()

    if not issues and not warnings:
        print('✓ Time evaluation is working correctly!')
        print()
    elif issues:
        print('TROUBLESHOOTING:')
        print('  1. Delete and rebuild animation effects:')
        print('     → This recreates expressions with fresh time dependencies')
        print('  2. Check expression code for stale conditionals')
        print('  3. Verify expressions use "time1.outTime" not "frame"')
        print('  4. Try: cmds.delete(expression_node) before rebuilding')
        print()

    # Reset timeline
    cmds.currentTime(1, edit=True)


def fix_expression_time_dependency(rigname='tail'):
    '''
    Emergency fix: Delete and recreate all time-dependent expressions.
    This ensures clean time dependency flags.
    '''
    print('\n=== FIXING EXPRESSION TIME DEPENDENCIES ===\n')

    joints = rt_constants.JOINTS_BN.get(rigname, [])
    if len(joints) < 2:
        print('✗ No joints found')
        return

    # Every FX expression of the part, in one batch: rt_anim.remove_expressions
    # disconnects the lot before deleting any of it, which is what stops a
    # delete cascading through the connection web
    exprs = [f'{rigname}_loop_time_expression']
    for jnt in joints[1:]:
        NN = rt_naming.get_index_from_name(jnt)
        for axis in ['X', 'Y', 'Z']:
            exprs.append(f'{rigname}_{NN:02d}_wave{axis}_expression')
            exprs.append(f'{rigname}_{NN:02d}_noise_{axis}_expression')

    for expr in exprs:
        if cmds.objExists(expr):
            print(f'  Deleting: {expr}')
    deleted_count = rt_anim.remove_expressions(exprs)

    print(f'\n✓ Deleted {deleted_count} expression and conversion nodes')
    print('\nNow rebuild animation effects:')
    print('  import rig_tail_anim as rt_anim')
    print(f'  rt_anim.build_anim_effects("{rigname}", fk=True, ik=True)')
    print()


def check_expression_flags(rigname='tail'):
    '''
    Check if expressions have proper time dependency flags.
    '''
    print('\n=== EXPRESSION FLAGS CHECK ===\n')

    joints = rt_constants.JOINTS_BN.get(rigname, [])
    if len(joints) < 3:
        print('✗ Not enough joints')
        return

    # Check time1.outTime directly
    print('DIRECT TIME CHECK:')
    for frame in [1, 10, 20]:
        cmds.currentTime(frame, edit=True)
        time_val = cmds.getAttr('time1.outTime')
        print(f'  Frame {frame}: time1.outTime = {time_val}')
    print()

    test_joint = 2
    NN = rt_naming.get_index_from_name(joints[test_joint])

    expressions = []

    for axis in ['X', 'Y', 'Z']:
        expr = f'{rigname}_{NN:02d}_wave{axis}_expression'
        if cmds.objExists(expr):
            expressions.append(expr)

    loop_expr = f'{rigname}_loop_time_expression'
    if cmds.objExists(loop_expr):
        expressions.append(loop_expr)

    if not expressions:
        print('✗ No expressions found')
        return

    print('EXPRESSION NODES:')
    for expr in expressions:
        # Check expression string
        expr_code = cmds.expression(expr, q=1, s=1)

        # Check for time reference
        has_time = 'time1.outTime' in expr_code or '$t =' in expr_code

        # Check connections
        connections = cmds.listConnections(expr, s=1, d=0, p=1) or []
        has_time_conn = any('time1' in str(conn) for conn in connections)

        print(f'{expr}:')
        print(f'  References time in code: {"✓" if has_time else "✗"}')
        print(f'  Connected to time1: {"✓" if has_time_conn else "✗"}')

        # Try to get node type info
        try:
            node_type = cmds.nodeType(expr)
            print(f'  Node type: {node_type}')
        except:
            print(f'  Node type: <error>')

        # Check if it has output attributes
        if 'loop' in expr:
            try:
                output_val = cmds.getAttr(f'{expr}.output[0]')
                print(f'  Current output[0]: {output_val}')
            except:
                print(f'  No output[0] attribute')

        print()

def test_joint_orient(rigname='tail', joints_bn=None, joints_ik=None, count=10):
    if joints_bn is None:
        joints_bn = rt_constants.JOINTS_BN.get(rigname, [])
    if joints_ik is None:
        joints_ik = rt_constants.JOINTS_IK.get(rigname, [])

    print('--- Joint Orient Diagnostic for', rigname, '---')
    for i, bn in enumerate(joints_bn[:count]):
        ik = joints_ik[i] if i < len(joints_ik) else None
        parent = cmds.listRelatives(ik or bn, p=True, f=True)
        parent = parent[0] if parent else None

        bn_world = cmds.xform(bn, q=1, ws=1, m=1)
        bn_m = om.MMatrix(bn_world)

        if ik:
            ik_world = cmds.xform(ik, q=1, ws=1, m=1)
            ik_m = om.MMatrix(ik_world)
        else:
            ik_m = None

        if parent:
            parent_inv = cmds.getAttr(f'{parent}.worldInverseMatrix[0]')
            parent_inv_m = om.MMatrix(parent_inv)
            bn_local = parent_inv_m * bn_m
            ik_local = parent_inv_m * ik_m if ik_m else None
        else:
            bn_local = bn_m
            ik_local = ik_m

        bn_tr = om.MTransformationMatrix(bn_local)
        bn_rot_q = bn_tr.rotation(asQuaternion=True)
        bn_euler = bn_tr.rotation()  # use Euler for readable degrees

        if ik_local:
            ik_tr = om.MTransformationMatrix(ik_local)
            ik_rot_q = ik_tr.rotation(asQuaternion=True)
            ik_euler = ik_tr.rotation()
        else:
            ik_rot_q = None
            ik_euler = None

        # jointOrients:
        bn_jo = cmds.getAttr(f'{bn}.jointOrient')[0] if cmds.attributeQuery('jointOrient', node=bn, exists=True) else (0,0,0)
        ik_jo = cmds.getAttr(f'{ik}.jointOrient')[0] if ik and cmds.attributeQuery('jointOrient', node=ik, exists=True) else (0,0,0)

        bn_jo_m = om.MEulerRotation(
            om.MAngle(bn_jo[0], om.MAngle.kDegrees).asRadians(),
            om.MAngle(bn_jo[1], om.MAngle.kDegrees).asRadians(),
            om.MAngle(bn_jo[2], om.MAngle.kDegrees).asRadians()
        ).asQuaternion()

        ik_jo_m = om.MEulerRotation(
            om.MAngle(ik_jo[0], om.MAngle.kDegrees).asRadians(),
            om.MAngle(ik_jo[1], om.MAngle.kDegrees).asRadians(),
            om.MAngle(ik_jo[2], om.MAngle.kDegrees).asRadians()
        ).asQuaternion() if ik else None

        # compute candidate axis quats:
        if ik_jo_m:
            axis_old = bn_jo_m * ik_jo_m.inverse()   # OLD formula
            axis_new = bn_jo_m.inverse() * ik_jo_m   # NEW (bug) formula
        else:
            axis_old = None
            axis_new = None

        # offsetParentMatrix check
        opm = cmds.getAttr(f'{bn}.offsetParentMatrix') if cmds.attributeQuery('offsetParentMatrix', node=bn, exists=True) else None
        opm_incoming = cmds.listConnections(f'{bn}.offsetParentMatrix', s=True, d=False) or []

        print('index %02d :' % i, 'BN="%s" IK="%s"' % (bn, ik))
        print('  BN.jointOrient:', bn_jo)
        print('  IK.jointOrient:', ik_jo)
        print('  BN local euler (deg):', [om.MAngle(v).asDegrees() for v in bn_euler])
        if ik_euler:
            print('  IK local euler (deg):', [om.MAngle(v).asDegrees() for v in ik_euler])
        if axis_old:
            ao = axis_old.asEulerRotation()
            an = axis_new.asEulerRotation()
            print('  axis_old (euler deg):', [om.MAngle(v).asDegrees() for v in ao])
            print('  axis_new (euler deg):', [om.MAngle(v).asDegrees() for v in an])
        print('  offsetParentMatrix has incoming connections:', bool(opm_incoming), 'incoming nodes:', opm_incoming)
        print('  offsetParentMatrix raw first row:', opm[0:4] if opm else None)
        print('  worldMatrix inputs for BN:', cmds.listConnections(f'{bn}.worldMatrix', s=True, d=False) or [])
        print('  ---------------------------')
    print('--- end diagnostic ---')
