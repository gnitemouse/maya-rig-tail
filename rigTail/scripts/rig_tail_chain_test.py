"""
rig_tail_chain_test.py
author: Daisy Jane @gnitemouse

Tests for Joint Chain Builder (rig_tail_chain_spacing and
rig_tail_chain_build). Two kinds:

  MATH tests - deterministic, no scene needed. Exercise the pure-math
    spacing functions in isolation under plain Python.
    run_math()

  SCENE tests - MUTATING. Create, rebuild and re-space real joint chains
    through the same entry points the UI uses, so RELOAD THE SCENE
    afterwards. Each test restores the chain's names and positions, but a
    shrink deletes joints and undo does not cover the whole suite.
    run_scene()

Usage:
    import rig_tail_chain_test as rt_chain_test
    rt_chain_test.run_math()               # safe: pure-math unit tests
    rt_chain_test.run_scene()              # MUTATING: every feature on the scene

    # individual (scene tests mutate; reload after):
    rt_chain_test.test_rebuild_count('C_tail')
    rt_chain_test.test_guards()            # MUTATING, but on its own scratch chain

The scene tests default to DEFAULT_CHAIN on the sample squid scene; pass
a rig part name (or any joint in the chain) on another rig. run_math()
needs no Maya, so the Maya I/O layer is imported behind a guard.

Functions:
  Runners
    run_math: every math test (safe), with a PASS/FAIL summary
    run_scene: every scene test (MUTATING), with a PASS/FAIL summary
    run_all: run_math plus a pointer to the mutating scene tests
  Math tests (safe, no scene)
    test_containment: no core module imports rig_tail_chain
    test_distribution_endpoints: every mode/param: f(0)=0, f(1)=1
    test_uniform_equivalence: power k=1 == ratio r=1 == uniform
    test_invert_symmetry: invert(invert(f)) == f
    test_taper_direction: Power and Ratio taper base -> tip; Invert flips
        every mode, Keep included
    test_pchip_monotone: random monotone data resamples monotonically
    test_pchip_knot_exact: evaluation at knots returns knot values
    test_catmullrom_interpolates: curve at knot params == input points
    test_arclength: monotone; total chord sum for a straight chain
    test_keep_idempotent: N->N returns s_hat to < 1e-9
    test_resample_noop: re-applying a mode to its own result never drifts
    test_respace_same_count: same-count re-spacing does redistribute
    test_param_defaults: param=None uses K_DEFAULT/R_DEFAULT; clamping holds
    test_roundtrip_drift: 20->30->20 max deviation < tol
    test_snap_exact: 21->11 uniform snaps to every other original
    test_degenerate: N=2, coincident points, r=1.0, k=1.0, n=2
  Scene tests (MUTATING)
    test_rebuild_count: count, order, parenting, _ee_ position
    test_names_preserved: n==N leaves every name untouched
    test_partial_rebuild: 'from selected joint' leaves the run above alone
    test_radius_consistent: one joint radius per chain, fitted to the spacing
    test_guards: skinned/rig-driven/locked/branching chains are refused
    test_cache_no_compounding: drift same as single pass, not 10x
    test_undo: one Ctrl+Z restores the pre-click state
"""

import math
import os
import re

import rig_tail_chain_spacing as rt_chain_spacing

# The scene half, behind a guard so run_math() stays runnable under plain
# Python. _require_maya() turns the absence into a skip.
try:
    import maya.cmds as cmds
    import rig_tail_constants as rt_constants
    import rig_tail_naming as rt_naming
    import rig_tail_joint as rt_joint
    import rig_tail_chain_build as rt_chain_build
    from logger_config import RigTailBuildError
except ImportError:
    cmds = rt_constants = rt_naming = rt_joint = rt_chain_build = None
    RigTailBuildError = None


POS_TOL = 1e-6
POS_TOL_SCENE = 1e-3
LEN_TOL = 1e-9

# Sample squid scene: a long center chain with an _ee_ end joint.
DEFAULT_CHAIN = 'C_tail'

# Prefix for the nodes test_guards builds and deletes.
SCRATCH = 'tailChainGuard'


def _verdict(name, ok, msg=""):
    tag = "PASS" if ok else "FAIL"
    line = f"  [{tag}] {name}"
    if msg:
        line += f"  --  {msg}"
    print(line)
    return ok


def _summary(title, results):
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)
    ok = True
    for name, outcome in results:
        if isinstance(outcome, Exception):
            status, detail = "ERROR", str(outcome)
            ok = False
        elif outcome is True:
            status, detail = "PASS", ""
        elif outcome is False:
            status, detail = "FAIL", ""
            ok = False
        else:
            status, detail = "RAN ", ""
        line = f"  {status}  {name}"
        if detail:
            line += f"  --  {detail}"
        print(line)
    print("=" * 64)
    print(f'  RESULT: {"ALL CLEAR" if ok else "ISSUES FOUND"}')
    print("=" * 64 + "\n")
    return ok


def _linspace(start, stop, n):
    h = (stop - start) / (n - 1) if n > 1 else 0
    return [start + h * i for i in range(n)]


def _max_move(before, after):
    """Largest distance between two lists of positions, pairwise."""
    return max((math.dist(a, b) for a, b in zip(before, after)), default=0.0)


def _chain_length(positions):
    """Total chord length along a list of positions."""
    return sum(math.dist(positions[i], positions[i + 1])
               for i in range(len(positions) - 1))


def _smooth_chain(n, bunch=2.0):
    """A tail-like chain: a smooth curve, unevenly spaced along itself.

    Random noise turns ~90-180 degrees between segments, which no real
    skeleton does. Shape-stability claims are about a chain like this:
    curved, with a path well-defined enough to survive resampling.
    """
    pts = []
    for i in range(n):
        t = (i / (n - 1)) ** bunch      # bunched toward the base
        a = t * 1.6                     # sweep, in radians
        pts.append([math.cos(a) * 5.0, math.sin(a) * 5.0, t * 3.0])
    return pts


def _random_chain(n, seed=0):
    r = seed
    pts = []
    for i in range(n):
        r = (r * 1103515245 + 12345) & 0x7fffffff
        x = (r % 100) / 100.0
        r = (r * 1103515245 + 12345) & 0x7fffffff
        y = (r % 100) / 100.0
        r = (r * 1103515245 + 12345) & 0x7fffffff
        z = (r % 100) / 100.0
        pts.append([x, y, z])
    return pts


# MATH TESTS ============================================================

def test_containment():
    """No core module imports rig_tail_chain."""
    scripts_dir = os.path.dirname(__file__)
    # Only an import couples the core to the sub-tool. Comments and
    # docstrings cross-reference it freely, so match the statement rather
    # than the bare name.
    imported = re.compile(r'^[ \t]*(?:import|from)[ \t]+rig_tail_chain', re.M)
    ok = True
    checked = 0
    for fname in sorted(os.listdir(scripts_dir)):
        if not fname.endswith(".py") or fname == os.path.basename(__file__):
            continue
        if fname.startswith("rig_tail_chain"):
            continue
        try:
            with open(os.path.join(scripts_dir, fname),
                      encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (IOError, OSError):
            continue
        checked += 1
        if imported.search(content):
            ok &= _verdict(f"{fname} imports rig_tail_chain", False)
    if ok:
        _verdict("no core module imports rig_tail_chain", True,
                 f"{checked} module(s) checked")
    return ok


def test_distribution_endpoints():
    """Every mode/param: f(0)=0, f(1)=1, strictly increasing."""
    ok = True
    for n in (2, 3, 5, 10):
        for mode in ("uniform", "power", "ratio"):
            param = 1.7 if mode == "power" else (0.90 if mode == "ratio" else None)
            u = rt_chain_spacing.distribute(mode, n, param=param)
            ok &= _verdict(f"{mode} n={n} f(0)=0",
                           abs(u[0]) < LEN_TOL, f"got {u[0]}")
            ok &= _verdict(f"{mode} n={n} f(1)=1",
                           abs(u[-1] - 1.0) < LEN_TOL, f"got {u[-1]}")
            inc = all(u[i] < u[i + 1] for i in range(n - 1))
            ok &= _verdict(f"{mode} n={n} increasing", inc)
    return ok


def test_uniform_equivalence():
    """power k=1 == ratio r=1 == uniform."""
    ok = True
    for n in (3, 5, 10):
        u1 = rt_chain_spacing.distribute("uniform", n)
        u2 = rt_chain_spacing.distribute("power", n, param=1.0)
        u3 = rt_chain_spacing.distribute("ratio", n, param=1.0)
        ok &= _verdict(f"n={n} power(1)==uniform",
                       all(abs(a - b) < LEN_TOL for a, b in zip(u1, u2)))
        ok &= _verdict(f"n={n} ratio(1)==uniform",
                       all(abs(a - b) < LEN_TOL for a, b in zip(u1, u3)))
    return ok


def test_invert_symmetry():
    """invert(invert(f)) == f."""
    ok = True
    for n in (3, 5, 10):
        for mode in ("uniform", "power", "ratio"):
            param = 1.7 if mode == "power" else (0.90 if mode == "ratio" else None)
            u = rt_chain_spacing.distribute(mode, n, param=param)
            u_inv = rt_chain_spacing.distribute(mode, n, param=param, invert=True)
            u_back = [1.0 - v for v in reversed(u_inv)]
            ok &= _verdict(f"{mode} n={n} double-invert recovers",
                           all(abs(a - b) < LEN_TOL for a, b in zip(u, u_back)))
    return ok


def test_taper_direction():
    """Power and Ratio both taper base -> tip; Invert reverses every mode.

    The UI promises this in words, and it is one sign flip from being
    silently wrong: t**k tapers the opposite way to t**(1/k), and both
    look plausible in a screenshot.
    """
    ok = True
    for n in (5, 9, 20):
        for mode, param in (("power", 1.7), ("power", 3.0),
                            ("ratio", 0.90), ("ratio", 0.60)):
            u = rt_chain_spacing.distribute(mode, n, param=param)
            seg = [u[i + 1] - u[i] for i in range(n - 1)]
            ok &= _verdict(
                f"{mode} {param} n={n} segments shrink base -> tip",
                all(seg[i] > seg[i + 1] for i in range(len(seg) - 1)),
                f"first={seg[0]:.4f} last={seg[-1]:.4f}")
            u_inv = rt_chain_spacing.distribute(mode, n, param=param,
                                                invert=True)
            seg_inv = [u_inv[i + 1] - u_inv[i] for i in range(n - 1)]
            ok &= _verdict(
                f"{mode} {param} n={n} invert grows base -> tip",
                all(seg_inv[i] < seg_inv[i + 1] for i in range(len(seg_inv) - 1)),
                f"first={seg_inv[0]:.4f} last={seg_inv[-1]:.4f}")

    # Keep is the mode most likely to be left out of an 'all four modes'
    # claim, because inverting it means mirroring a sampled profile rather
    # than flipping a formula.
    source = [0.0, 0.05, 0.12, 0.30, 0.62, 1.0]
    u = rt_chain_spacing.distribute("keep", len(source), source=source)
    u_inv = rt_chain_spacing.distribute("keep", len(source), source=source,
                                        invert=True)
    ok &= _verdict("keep honours invert",
                   any(abs(a - b) > LEN_TOL for a, b in zip(u, u_inv)),
                   f"inverted={[round(v, 4) for v in u_inv]}")
    mirrored = [1.0 - v for v in reversed(u)]
    ok &= _verdict("keep invert is the mirrored profile",
                   all(abs(a - b) < LEN_TOL for a, b in zip(mirrored, u_inv)))
    return ok


def test_pchip_monotone():
    """Random monotone data resamples monotonically."""
    ok = True
    for n in (3, 5, 10):
        x = _linspace(0, 1, n)
        y = [float(i) / (n - 1) for i in range(n)]
        m = rt_chain_spacing.pchip_tangents(x, y)
        xq = _linspace(0, 1, 50)
        yq = rt_chain_spacing.pchip_eval(x, y, m, xq)
        inc = all(yq[i] <= yq[i + 1] for i in range(len(yq) - 1))
        ok &= _verdict(f"n={n} monotone", inc)
    return ok


def test_pchip_knot_exact():
    """Evaluation at knots returns knot values."""
    ok = True
    for n in (3, 5, 10):
        x = _linspace(0, 1, n)
        y = [math.sin(v * math.pi) for v in x]
        m = rt_chain_spacing.pchip_tangents(x, y)
        yq = rt_chain_spacing.pchip_eval(x, y, m, x)
        ok &= _verdict(f"n={n} exact at knots",
                       all(abs(a - b) < LEN_TOL for a, b in zip(y, yq)))
    return ok


def test_catmullrom_interpolates():
    """Curve at knot params == input points."""
    ok = True
    for n in (3, 5, 10):
        pts = _random_chain(n, seed=n)
        knots = rt_chain_spacing.catmull_rom_knots(pts)
        for i in range(n):
            p = rt_chain_spacing.catmull_rom_eval(pts, knots, knots[i])
            d = math.sqrt(sum((p[j] - pts[i][j]) ** 2 for j in range(3)))
            ok &= _verdict(f"n={n} pt={i} interpolates",
                           d < LEN_TOL, f"err={d}")
    return ok


def test_arclength():
    """Arclength table is monotone; total approx chord sum for straight chain."""
    pts = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0], [4, 0, 0]]
    table, total = rt_chain_spacing.arclength_table(pts)
    inc = all(table[i] <= table[i + 1] for i in range(len(table) - 1))
    ok = _verdict("arclength monotone", inc)
    ok &= _verdict("arclength straight chain", abs(total - 4.0) < 0.01,
                   f"total={total}")
    return ok


def test_keep_idempotent():
    """Keep mode N->N returns original arclengths to < 1e-9."""
    ok = True
    for n in (3, 5, 10):
        pts = _random_chain(n, seed=n + 100)
        positions, snapped = rt_chain_spacing.resample(pts, n, "keep")
        d = max(math.sqrt(sum((positions[i][j] - pts[i][j]) ** 2 for j in range(3)))
                for i in range(n))
        ok &= _verdict(f"n={n} keep idempotent", d < LEN_TOL, f"max err={d}")
    # Straight-line chain (arclength = chord = parameter positions)
    pts = [[float(i), 0.0, 0.0] for i in range(10)]
    positions, snapped = rt_chain_spacing.resample(pts, 10, "keep")
    d = max(math.sqrt(sum((positions[i][j] - pts[i][j]) ** 2 for j in range(3)))
            for i in range(10))
    ok &= _verdict("straight keep idempotent", d < LEN_TOL, f"max err={d}")
    return ok


def test_keep_preserves_distribution():
    """Keep evaluates source arclength at uniform joint-index coordinates."""
    source = [0.0, 0.05, 0.45, 1.0]
    u = rt_chain_spacing.distribute('keep', len(source), source=source)
    return _verdict('keep preserves source distribution',
                    all(abs(a - b) < LEN_TOL for a, b in zip(u, source)),
                    f'got {u}')


def test_resample_noop():
    """Re-spacing at unchanged count does not drift.

    The exactness claim the anti-drift argument rests on - and NOT 'n == N
    returns the input': Uniform on a non-uniform chain must genuinely
    redistribute it. Three promises:

      Keep at n == N is EXACT at any count, since PCHIP and Catmull-Rom
        both interpolate at their knots. It is also the default mode, so
        the first click on a freshly selected chain never touches it.
      The analytic modes are idempotent to within a hair of the chain
        length once the count samples the shape. At tiny counts the
        reconstruction itself is the loss, and that error falls with count.
      Everything contracts (below), so repeated clicks settle.
    """
    ok = True
    for n in (3, 5, 10, 21):
        pts = _smooth_chain(n)
        length = sum(math.sqrt(sum((pts[i + 1][j] - pts[i][j]) ** 2
                                   for j in range(3))) for i in range(n - 1))
        for mode in ("uniform", "power", "ratio", "keep"):
            param = 1.7 if mode == "power" else (0.90 if mode == "ratio" else None)
            once, _ = rt_chain_spacing.resample(pts, n, mode, param=param)
            twice, _ = rt_chain_spacing.resample(once, n, mode, param=param)
            d = max(math.sqrt(sum((twice[i][j] - once[i][j]) ** 2 for j in range(3)))
                    for i in range(n))
            if mode == "keep":
                ok &= _verdict(f"n={n} keep exact at unchanged count",
                               d < POS_TOL, f"max err={d}")
            else:
                # Bounds the shape-reconstruction error, not floating
                # point: N samples reconstruct a curve to O(1/N^2), so the
                # limit tightens with count rather than being one flat
                # number that suits neither n=3 nor n=21.
                limit = length * 0.5 / n ** 2
                ok &= _verdict(f"n={n} {mode} idempotent to {limit:.4f}",
                               d < limit, f"max err={d}")

    # An already-uniform straight chain is a fixed point of uniform, exactly.
    pts = [[float(i), 0.0, 0.0] for i in range(10)]
    positions, _ = rt_chain_spacing.resample(pts, 10, "uniform")
    d = max(math.sqrt(sum((positions[i][j] - pts[i][j]) ** 2 for j in range(3)))
            for i in range(10))
    ok &= _verdict("straight uniform no-op", d < LEN_TOL, f"max err={d}")

    # A chaotic zigzag is NOT a one-step fixed point: redistributing its
    # points defines a visibly different curve, so the next pass lands
    # somewhere new. What must hold even there is contraction (successive
    # passes move less, never more), so a stuck artist clicking Build
    # repeatedly settles instead of wandering off.
    pts = _random_chain(10, seed=210)
    steps = [pts]
    for _ in range(4):
        nxt, _ = rt_chain_spacing.resample(steps[-1], 10, "uniform")
        steps.append(nxt)
    deltas = [max(math.sqrt(sum((b[i][j] - a[i][j]) ** 2 for j in range(3)))
                  for i in range(10))
              for a, b in zip(steps[1:], steps[2:])]
    ok &= _verdict("zigzag chain contracts under re-spacing",
                   all(deltas[i + 1] < deltas[i] for i in range(len(deltas) - 1)),
                   f"deltas={[round(v, 5) for v in deltas]}")
    return ok


def test_respace_same_count():
    """Same-count re-spacing actually moves joints (no n == N short-circuit).

    A bunched chain asked for Uniform at its own count must come out evenly
    spaced. Returning the input unchanged here would leave three of the four
    modes doing nothing at the count the UI defaults to.
    """
    # 9 joints crowded into the first fifth of a straight 8-unit span.
    pts = [[float(i) * 0.2, 0.0, 0.0] for i in range(8)] + [[8.0, 0.0, 0.0]]
    n = len(pts)

    def _spread(chain):
        segs = [math.sqrt(sum((chain[i + 1][j] - chain[i][j]) ** 2
                              for j in range(3))) for i in range(len(chain) - 1)]
        return max(segs) - min(segs)

    positions, _ = rt_chain_spacing.resample(pts, n, "uniform")
    before, after = _spread(pts), _spread(positions)
    # Bounded by the arclength table's inversion error, O(1/ARC_SAMPLES^2)
    # ~= 0.4% of the chain, not by floating point: the table samples in
    # parameter and interpolates linearly between samples.
    total = 8.0
    ok = _verdict("uniform at n == N evens the spacing",
                  after < total / rt_chain_spacing.ARC_SAMPLES ** 2,
                  f"spread {before:.4f} -> {after:.6f}")
    ok &= _verdict("re-spacing changed the chain", before - after > 1.0,
                   f"spread {before:.4f} -> {after:.6f}")
    # Endpoints are fixed by definition, whatever the redistribution.
    for idx, tag in ((0, "first"), (-1, "last")):
        d = math.sqrt(sum((positions[idx][j] - pts[idx][j]) ** 2
                          for j in range(3)))
        ok &= _verdict(f"{tag} joint pinned", d < LEN_TOL, f"err={d}")
    return ok


def test_param_defaults():
    """param=None uses the documented defaults, not the range floor."""
    ok = True
    for mode, default in (("power", rt_chain_spacing.K_DEFAULT),
                          ("ratio", rt_chain_spacing.R_DEFAULT)):
        implicit = rt_chain_spacing.distribute(mode, 8)
        explicit = rt_chain_spacing.distribute(mode, 8, param=default)
        ok &= _verdict(f"{mode} default == {default}",
                       all(abs(a - b) < LEN_TOL
                           for a, b in zip(implicit, explicit)),
                       f"implicit={[round(v, 4) for v in implicit]}")
    ok &= _verdict("K_DEFAULT within K_RANGE",
                   rt_chain_spacing.K_RANGE[0] <= rt_chain_spacing.K_DEFAULT <= rt_chain_spacing.K_RANGE[1])
    ok &= _verdict("R_DEFAULT within R_RANGE",
                   rt_chain_spacing.R_RANGE[0] <= rt_chain_spacing.R_DEFAULT <= rt_chain_spacing.R_RANGE[1])
    # Out-of-range values clamp rather than collapsing a segment to zero.
    for mode, bad in (("power", 99.0), ("power", -1.0),
                      ("ratio", 5.0), ("ratio", 0.0)):
        u = rt_chain_spacing.distribute(mode, 12, param=bad)
        inc = all(u[i] < u[i + 1] for i in range(len(u) - 1))
        ok &= _verdict(f"{mode} param={bad} clamps and stays increasing", inc)
    return ok


def test_roundtrip_drift():
    """20->30->20 max deviation is bounded (not compounding)."""
    # Straight line: the drift comes solely from the down-res pass.
    pts = [[float(i) * 0.5, 0.0, 0.0] for i in range(20)]
    mid, _ = rt_chain_spacing.resample(pts, 30, "uniform")
    back, _ = rt_chain_spacing.resample(mid, 20, "keep")
    d = max(math.sqrt(sum((back[i][j] - pts[i][j]) ** 2 for j in range(3)))
            for i in range(20))
    ok = _verdict("roundtrip 20->30->20 straight chain",
                  d < POS_TOL, f"max deviation={d}")

    # Random chain: drift should still be bounded by one down-res pass.
    pts = _random_chain(20, seed=42)
    mid, _ = rt_chain_spacing.resample(pts, 30, "uniform")
    back, _ = rt_chain_spacing.resample(mid, 20, "keep")
    d = max(math.sqrt(sum((back[i][j] - pts[i][j]) ** 2 for j in range(3)))
            for i in range(20))
    ok &= _verdict("roundtrip 20->30->20 random chain",
                   d < 1.0, f"max deviation={d}")
    return ok


def test_snap_exact():
    """21->11 uniform snaps to every other original, exactly.

    Uses a straight-line chain (collinear points) so the arclength and
    parameter positions coincide, which is the condition for exact snapping.
    """
    pts = [[float(i), 0.0, 0.0] for i in range(21)]
    positions, snapped = rt_chain_spacing.resample(pts, 11, "uniform")
    ok = True
    target_idx = list(range(0, 21, 2))
    for i in range(11):
        d = math.sqrt(sum((positions[i][j] - pts[target_idx[i]][j]) ** 2 for j in range(3)))
        ok &= _verdict(f"idx {i} (src {target_idx[i]}) snap exact",
                       d < LEN_TOL, f"err={d}")
    return ok


def test_degenerate():
    """N=2, coincident points, r=1.0, k=1.0, n=2."""
    pts = [[1, 1, 1], [1, 1, 1]]
    try:
        for mode in ("uniform", "power", "ratio"):
            param = 1.0 if mode in ("power", "ratio") else None
            positions, snapped = rt_chain_spacing.resample(pts, 2, mode, param=param)
        ok = True
    except Exception as exc:
        ok = _verdict("degenerate chain", False, str(exc))
    return ok


# SCENE HELPERS ========================================================

def _require_maya(test):
    """False (with a printed skip) when there is no Maya to run against."""
    if cmds is None:
        print(f'  {test}: no maya.cmds in this interpreter, skipping. '
              'Scene tests need a running Maya with a skeleton loaded.')
        return False
    return True


def _resolve_root(name):
    """
    The chain root for a rig part name, or for the name of any joint in it.

    The same lookups the UI's Joint Chain(s) box does: the naming template
    turns a rig part into its root joint's name, otherwise the name is
    taken literally as a joint and walked up.
    """
    try:
        templated = rt_naming.fstr(name, rt_constants.JOINT,
                                   rt_constants.TYPE_BN, 0)
        if cmds.objExists(templated):
            return rt_chain_build.chain_root(templated)
        return rt_chain_build.chain_root(name) if cmds.objExists(name) else None
    except Exception:
        return None


def _chain_bn(root):
    """The chain's BN joints, root first. Detection stops before the _ee_.

    The filter tests the joint's own name, not its DAG path: a chain
    parented under an '_ee_' joint would otherwise filter itself away.
    """
    return [j for j in rt_joint.get_joint_chain(root)
            if '_ee_' not in j.split('|')[-1]]


def _positions(joints):
    """World positions of a list of joints."""
    return [cmds.xform(j, q=True, ws=True, t=True) for j in joints]


def _leaves(joints):
    """Leaf names, so long DAG paths and short names compare equal."""
    return [j.split('|')[-1] for j in joints]


def _dist(a, b):
    """Distance between two [x, y, z] positions."""
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _radii(joints):
    """Display radius of each joint."""
    return [cmds.getAttr(f'{j}.radius') for j in joints]


def _mean_segment(positions):
    """Mean distance between consecutive positions."""
    if len(positions) < 2:
        return 0.0
    return sum(_dist(positions[i], positions[i + 1])
               for i in range(len(positions) - 1)) / (len(positions) - 1)


def _long(node):
    """A node's long DAG path, for identity comparisons."""
    return (cmds.ls(node, long=True) or [node])[0]


def _name_diff(want, got):
    """
    How two name lists differ, in one line.

    A tail is 30-odd joints, and printing both lists on every verdict
    buries the run. The first difference is what tells you what happened.
    """
    if want == got:
        return f'{len(got)} name(s) unchanged'
    for i, (a, b) in enumerate(zip(want, got)):
        if a != b:
            return (f'{len(want)} vs {len(got)} name(s), first difference at '
                    f'index {i}: {a} -> {b}')
    return f'{len(want)} -> {len(got)} name(s)'


def _parent_joint(joint):
    """The joint's parent joint, or None."""
    parent = cmds.listRelatives(joint, parent=True, typ='joint')
    return parent[0] if parent else None


def _broken_parent(chain):
    """
    The first joint whose parent is not its predecessor, or None.

    A rebuild can return a plausible-looking list while leaving a joint
    parented to a node no longer above it, since a shrink re-parents
    before deleting and a grow parents each new joint onto the previous.
    """
    for i in range(1, len(chain)):
        parent = _parent_joint(chain[i])
        if not parent or _long(parent) != _long(chain[i - 1]):
            return chain[i]
    return None


def _snapshot(root):
    """
    Everything a scene test has to put back: names, world positions and
    the _ee_ end joint.
    """
    bn = _chain_bn(root)
    ee = rt_chain_build._end_joint(bn[-1]) if bn else None
    return {
        'root': root,
        'names': _leaves(bn),
        'positions': _positions(bn),
        'radii': _radii(bn),
        'ee': ee.split('|')[-1] if ee else None,
        'ee_pos': cmds.xform(ee, q=True, ws=True, t=True) if ee else None,
    }


def _restore(state, root=None):
    """
    Put the chain back to its snapshot count and positions and forget the
    session original cache.

    Best effort, NOT a substitute for reloading the scene: deleted joints
    come back as fresh nodes and orientations are not restored. It exists
    so one failing test does not leave the next measuring a wrong chain.
    """
    root = root or _resolve_root(state['names'][0]) or state['root']
    try:
        if not root or not cmds.objExists(root):
            print('  restore: the chain root is gone, nothing put back')
            return False
        n = len(state['positions'])
        bn = _chain_bn(root)
        if len(bn) != n:
            rt_chain_build.clear_cache(root)
            bn = rt_chain_build.rebuild(root, n, mode='keep', snap=False)
        for joint, pos in zip(bn, state['positions']):
            cmds.xform(joint, ws=True, t=pos)
        # A rebuild fits the display radius to the spacing, so putting the
        # positions back without the radii would leave the chain the right
        # shape and visibly the wrong size for the next test.
        for joint, radius in zip(bn, state.get('radii') or []):
            try:
                cmds.setAttr(f'{joint}.radius', radius)
            except (RuntimeError, ValueError):
                pass
        ee = rt_chain_build._end_joint(bn[-1]) if bn else None
        if ee and state['ee_pos']:
            cmds.xform(ee, ws=True, t=state['ee_pos'])
        rt_chain_build.clear_cache(bn[0])
        return True
    except Exception as exc:
        print(f'  restore: could not put {state["names"][0]} back: {exc}')
        return False


def _scene_chain(test, rigname, minimum=4):
    """
    (root, BN joints) for a scene test, or None with a printed skip.

    Scene tests need a chain long enough to grow AND shrink and still be a
    chain, so the count is checked once here instead of in each of them.
    """
    root = _resolve_root(rigname)
    if not root:
        print(f'  {test}: no chain for "{rigname}", skipping')
        return None
    bn = _chain_bn(root)
    if len(bn) < minimum:
        print(f'  {test}: {rigname} has {len(bn)} joint(s), needs {minimum}, '
              'skipping')
        return None
    return root, bn


def _make_scratch_chain(junk, n=6, length=10.0):
    """
    Build a throwaway straight chain, returning (root, joints).

    test_guards arms its hazards - a skinCluster, a driver connection, a
    branch child - on a chain the test owns, since doing that to real
    joints would leave debris the moment something raised.

    Every node joins the caller's junk list as it is made, so a raise
    halfway through still leaves something to delete.
    """
    start = cmds.spaceLocator(name=f'{SCRATCH}_start')[0]
    junk.append(start)
    end = cmds.spaceLocator(name=f'{SCRATCH}_end')[0]
    junk.append(end)
    cmds.xform(start, ws=True, t=[0.0, 0.0, 0.0])
    cmds.xform(end, ws=True, t=[length, 0.0, 0.0])
    joints = rt_chain_build.build_new(start, end, n, rigname=SCRATCH,
                                      mode='uniform')
    junk.append(joints[0])
    return joints[0], joints


def _delete_junk(nodes):
    """Delete scratch nodes, tolerating ones already gone."""
    for node in nodes:
        try:
            if cmds.objExists(node):
                cmds.delete(node)
        except Exception as exc:
            print(f'  could not delete {node}: {exc}')


# SCENE TESTS (MUTATING) ===============================================

def test_rebuild_count(rigname=DEFAULT_CHAIN):
    """
    rebuild at a new count: how many joints, in what order, parented to
    what, and where the _ee_ ends up.

    Grow, shrink and same-count in one pass, all resampled from the same
    cached original. What has to hold for every one:

      The chain IN THE SCENE - walked down from the root, not read off the
        returned list - is exactly n joints in one parent-to-child line.
        Checking only the return value would miss an orphaned joint.
      Both ends of the TAIL sit where they always did: the root joint, and
        the _ee_ when there is one, otherwise the last BN joint.
      No two joints land on top of each other.
      The _ee_ hangs off the NEW tip. Left at the old one it would hand
        Setup a bogus final aim direction.
      The last BN joint stops ONE SEGMENT short of the _ee_, and that gap
        narrows as the count rises - the _ee_ is the end of the tail's
        length, not a fixed-length stub behind the last joint.
    """
    if not _require_maya('test_rebuild_count'):
        return None
    found = _scene_chain('test_rebuild_count', rigname)
    if not found:
        return None
    root, bn = found

    state = _snapshot(root)
    start_n = len(bn)
    has_ee = bool(state['ee_pos'])
    # Where the tail ends: the _ee_ if there is one, the last BN joint if not.
    tail_end = state['ee_pos'] if has_ee else state['positions'][-1]
    gaps = {}
    rt_chain_build.clear_cache(root)
    ok = True
    try:
        for n in (start_n + 3, start_n - 2, start_n):
            label = f'{rigname} {start_n}->{n}'
            result = rt_chain_build.rebuild(root, n, mode='uniform', snap=False)
            root = result[0]
            ok &= _verdict(f'{label} returns {n} joints', len(result) == n,
                           f'got {len(result)}')

            chain = _chain_bn(root)
            ok &= _verdict(f'{label} scene chain is the returned chain',
                           _leaves(chain) == _leaves(result),
                           _name_diff(_leaves(result), _leaves(chain)))
            broken = _broken_parent(chain)
            ok &= _verdict(f'{label} one parent-to-child line', not broken,
                           f'{broken} is not parented to its predecessor'
                           if broken else '')

            positions = _positions(chain)
            moved = math.dist(positions[0], state['positions'][0])
            ok &= _verdict(f'{label} base pinned', moved < POS_TOL_SCENE,
                           f'base moved {moved:.5f}')
            segments = [math.dist(positions[i], positions[i + 1])
                        for i in range(len(positions) - 1)]
            ok &= _verdict(f'{label} no coincident joints',
                           min(segments) > rt_chain_spacing.EPS,
                           f'shortest segment={min(segments):.6f}')

            if not has_ee:
                # No _ee_: the last BN joint IS the end of the tail, so it is
                # the thing that may not move.
                moved = math.dist(positions[-1], tail_end)
                ok &= _verdict(f'{label} tip pinned', moved < POS_TOL_SCENE,
                               f'tip moved {moved:.5f}')
                continue

            ee = rt_chain_build._end_joint(chain[-1])
            ok &= _verdict(f'{label} _ee_ still on the tip', bool(ee),
                           f'{ee.split("|")[-1]} under {chain[-1]}' if ee
                           else f'nothing under {chain[-1]}')
            if not ee:
                continue
            ee_pos = cmds.xform(ee, q=True, ws=True, t=True)
            moved = math.dist(ee_pos, tail_end)
            ok &= _verdict(f'{label} _ee_ pinned', moved < POS_TOL_SCENE,
                           f'_ee_ moved {moved:.5f}')
            gap = math.dist(ee_pos, positions[-1])
            gaps[n] = gap
            # Uniform spacing runs base to _ee_, so the last gap is just
            # another segment. The margin is for chord vs arclength on a
            # curved tail, not for a gap of the wrong order.
            mean = _mean_segment(positions + [ee_pos])
            ok &= _verdict(f'{label} last joint one segment short of the _ee_',
                           abs(gap - mean) < 0.25 * mean,
                           f'gap={gap:.5f}, mean segment={mean:.5f}')

        if len(gaps) > 1:
            counts = sorted(gaps)
            ok &= _verdict('the _ee_ gap narrows as the count rises',
                           all(gaps[counts[i]] > gaps[counts[i + 1]]
                               for i in range(len(counts) - 1)),
                           ', '.join(f'{c} joints: {gaps[c]:.4f}'
                                     for c in counts))
        return ok
    finally:
        _restore(state, root)


def test_names_preserved(rigname=DEFAULT_CHAIN):
    """
    A same-count rebuild leaves every name untouched.

    n == N is what the UI opens on, so this is the common case: the joints
    move and nothing else changes - no renumbering, no new nodes, no
    renamed _ee_.

    Power at k=3 rather than Uniform, deliberately. Uniform would move
    nothing on an already-even chain, and 'the names survived' would then
    be true of doing nothing at all.
    """
    if not _require_maya('test_names_preserved'):
        return None
    found = _scene_chain('test_names_preserved', rigname, minimum=3)
    if not found:
        return None
    root, bn = found

    state = _snapshot(root)
    n = len(bn)
    rt_chain_build.clear_cache(root)
    ok = True
    try:
        result = rt_chain_build.rebuild(root, n, mode='power', param=3.0,
                                        snap=False)
        root = result[0]
        chain = _chain_bn(root)
        ok &= _verdict(f'{rigname} count unchanged', len(chain) == n,
                       f'{n} -> {len(chain)}')
        ok &= _verdict(f'{rigname} every name untouched',
                       _leaves(chain) == state['names'],
                       _name_diff(state['names'], _leaves(chain)))

        # Vacuous unless the rebuild actually did something.
        moved = _max_move(state['positions'], _positions(chain))
        ok &= _verdict(f'{rigname} joints actually moved',
                       moved > POS_TOL_SCENE, f'max move={moved:.5f}')

        if state['ee']:
            ee = rt_chain_build._end_joint(chain[-1]) if chain else None
            ok &= _verdict(f'{rigname} _ee_ name untouched',
                           bool(ee) and ee.split('|')[-1] == state['ee'],
                           f'{state["ee"]} -> {ee}')
        return ok
    finally:
        _restore(state, root)


def test_partial_rebuild(rigname=DEFAULT_CHAIN):
    """
    'Build from selected joint' re-spaces the span and NOTHING above it.

    The value of the option is what it leaves alone, so that is what is
    measured: the run above the picked joint keeps its names, positions
    and numbering, and the picked joint itself does not move - being an
    endpoint of the resample, it lets the joint above go on aiming at it.

    Grows the span rather than matching its count, so the renumbering path
    runs: it has to continue the numbers above it, not restart at 0 and
    collide with the root.
    """
    if not _require_maya('test_partial_rebuild'):
        return None
    found = _scene_chain('test_partial_rebuild', rigname, minimum=6)
    if not found:
        return None
    root, bn = found

    state = _snapshot(root)
    n = len(bn)
    mid = n // 2
    start = bn[mid]
    above_names = _leaves(bn[:mid])
    above_pos = _positions(bn[:mid])
    start_pos = cmds.xform(start, q=True, ws=True, t=True)
    target = (n - mid) + 3
    rt_chain_build.clear_cache(root)
    ok = True
    try:
        span = rt_chain_build.rebuild(root, target, mode='uniform',
                                      snap=False, start_joint=start)
        ok &= _verdict(f'{rigname} span rebuilt to the asked count',
                       len(span) == target, f'got {len(span)}')

        chain = _chain_bn(_resolve_root(state['names'][0]) or root)
        ok &= _verdict(f'{rigname} full chain is the span plus the run above',
                       len(chain) == mid + target,
                       f'{mid} + {target} expected, got {len(chain)}')
        ok &= _verdict(f'{rigname} joints above the start keep their names',
                       _leaves(chain[:mid]) == above_names,
                       _name_diff(above_names, _leaves(chain[:mid])))
        moved = _max_move(above_pos, _positions(chain[:mid])) if mid else 0.0
        ok &= _verdict(f'{rigname} joints above the start do not move',
                       moved < POS_TOL_SCENE, f'max move={moved:.5f}')

        d = _dist(start_pos, cmds.xform(span[0], q=True, ws=True, t=True))
        ok &= _verdict(f'{rigname} the picked joint itself does not move',
                       d < POS_TOL_SCENE, f'moved {d:.5f}')
        ok &= _verdict(f'{rigname} the span starts at the picked joint',
                       _long(span[0]) == _long(chain[mid]),
                       f'{_leaves([span[0]])[0]} vs {_leaves([chain[mid]])[0]}')
        ok &= _verdict(f'{rigname} names are unique after renumbering',
                       len(set(_leaves(chain))) == len(chain),
                       _name_diff(above_names, _leaves(chain)))
        broken = _broken_parent(chain)
        ok &= _verdict(f'{rigname} parenting intact', broken is None,
                       f'{broken} is not parented to its predecessor')

        # Vacuous unless the span was actually redistributed.
        tail_moved = _max_move(state['positions'][mid:],
                               _positions(chain[mid:mid + (n - mid)]))
        ok &= _verdict(f'{rigname} the span actually moved',
                       tail_moved > POS_TOL_SCENE, f'max move={tail_moved:.5f}')
        return ok
    finally:
        rt_chain_build.clear_cache(root)
        _restore(state, root)


def test_radius_consistent(rigname=DEFAULT_CHAIN):
    """
    Every joint in a rebuilt chain draws at ONE radius, sized to the spacing.

    A grown chain must not mix the artist's radius with Maya's default 1.0
    on the joints it just created, which on a tail is the difference
    between a chain and a string of beads. The second claim is the one that
    stops a dense chain reading as a single blob: the radius is capped at
    half the new mean segment, so joints that end up closer together get
    smaller with the spacing rather than swallowing it.

    The lower count is rebuilt afterwards on purpose: the cap comes from
    the session original cache, so it has to lift again when the joints
    spread back out rather than ratcheting the chain permanently small.
    """
    if not _require_maya('test_radius_consistent'):
        return None
    found = _scene_chain('test_radius_consistent', rigname, minimum=4)
    if not found:
        return None
    root, bn = found

    state = _snapshot(root)
    n = len(bn)
    rt_chain_build.clear_cache(root)
    ok = True
    try:
        for label, target in (('grown', n + 8), ('shrunk', max(2, n // 2))):
            result = rt_chain_build.rebuild(root, target, mode='uniform',
                                            snap=False)
            root = result[0]
            chain = _chain_bn(root)
            ee = rt_chain_build._end_joint(chain[-1]) if chain else None
            radii = _radii(chain + ([ee] if ee else []))
            spread = max(radii) - min(radii)
            ok &= _verdict(f'{rigname} {label}: one radius across the chain',
                           spread < POS_TOL,
                           f'{min(radii):.5f} to {max(radii):.5f}')
            cap = _mean_segment(_positions(chain)) * \
                rt_chain_build.RADIUS_SEGMENT_FRACTION
            ok &= _verdict(f'{rigname} {label}: radius fits the spacing',
                           radii[0] <= cap + POS_TOL,
                           f'radius {radii[0]:.5f} > cap {cap:.5f}')
            ok &= _verdict(f'{rigname} {label}: radius is never enlarged',
                           radii[0] <= max(state['radii']) + POS_TOL,
                           f'radius {radii[0]:.5f} was {state["radii"][0]:.5f}')
        return ok
    finally:
        _restore(state, root)


def test_guards():
    """
    A chain that is skinned, rig-driven, locked or branching is REFUSED,
    and refused without moving anything.

    Runs on a scratch chain the test builds and deletes, so nothing here
    touches the loaded skeleton. Every hazard is armed and then disarmed
    with a clean rebuild either side: a refusal only means something if
    the same chain rebuilds once the hazard is gone.

    Two details do the real work. The guard must raise RigTailBuildError
    SPECIFICALLY - 'it raised something' would pass a tool whose skin
    lookup itself throws a TypeError on every chain, guarded or not, which
    is the bug this test exists for. And the chain must be untouched
    afterwards: the guards run before the write, so a refusal is a no-op.
    """
    if not _require_maya('test_guards'):
        return None
    ok = True
    root = None
    junk = []
    try:
        root, joints = _make_scratch_chain(junk)
        n = len(joints)
        mid = joints[n // 2]
        tip = joints[-1]

        def rebuilds(label):
            """The control: the scratch chain rebuilds cleanly."""
            nonlocal root
            try:
                result = rt_chain_build.rebuild(root, n, mode='uniform',
                                                snap=False)
            except Exception as exc:
                return _verdict(label, False, f'{type(exc).__name__}: {exc}')
            root = result[0]
            return _verdict(label, True)

        def refuses(label):
            """rebuild must abort with RigTailBuildError and move nothing."""
            before = _positions(_chain_bn(root))
            try:
                rt_chain_build.rebuild(root, n + 2, mode='uniform', snap=False)
            except Exception as exc:
                good = _verdict(f'{label} refused',
                                isinstance(exc, RigTailBuildError),
                                f'{type(exc).__name__}: {exc}')
                after = _positions(_chain_bn(root))
                untouched = (len(after) == len(before) and
                             _max_move(before, after) < POS_TOL_SCENE)
                return good & _verdict(f'{label} left the chain untouched',
                                       untouched,
                                       f'{len(before)} -> {len(after)} joints, '
                                       f'max move='
                                       f'{_max_move(before, after):.5f}')
            return _verdict(f'{label} refused', False,
                            'the rebuild completed - the guard never fired')

        def influence_skin(joint):
            """
            _find_influence_skin, reporting a raise instead of propagating.

            The lookup itself is what broke last time, so a version that
            throws must come back as a failed verdict here and leave the
            other hazards to be checked - not take the whole test with it.
            """
            try:
                return rt_chain_build._find_influence_skin(joint)
            except Exception as exc:
                return f'<raised {type(exc).__name__}: {exc}>'

        ok &= rebuilds('clean scratch chain rebuilds')

        # 1. skinCluster influence. The regression: a skinCluster sits
        # DOWNSTREAM of its influences (joint.worldMatrix feeds
        # skinCluster.matrix), so the lookup has to follow the joint's
        # future, and is checked directly here as well as through rebuild.
        geo = cmds.polyCube(name=f'{SCRATCH}_geo')[0]
        junk.append(geo)
        skin = cmds.skinCluster(joints, geo, toSelectedBones=True)[0]
        junk.append(skin)
        found = influence_skin(mid)
        ok &= _verdict('_find_influence_skin finds the bound cluster',
                       found == skin, f'got {found!r}, expected {skin!r}')
        ok &= refuses('skinned chain')
        cmds.skinCluster(geo, edit=True, unbind=True)
        unbound = influence_skin(mid)
        ok &= _verdict('_find_influence_skin empty once unbound',
                       unbound is None, f'got {unbound!r}')
        ok &= rebuilds('unbound chain rebuilds again')

        # 2. Driven by a built rig, on translate and on offsetParentMatrix.
        driver = cmds.spaceLocator(name=f'{SCRATCH}_driver')[0]
        junk.append(driver)
        cmds.connectAttr(f'{driver}.translate', f'{mid}.translate')
        ok &= refuses('rig-driven translate')
        cmds.disconnectAttr(f'{driver}.translate', f'{mid}.translate')
        cmds.connectAttr(f'{driver}.matrix', f'{mid}.offsetParentMatrix')
        ok &= refuses('rig-driven offsetParentMatrix')
        cmds.disconnectAttr(f'{driver}.matrix', f'{mid}.offsetParentMatrix')
        ok &= rebuilds('undriven chain rebuilds again')

        # 3. Locked translate: writable in the API sense, not the artist's.
        cmds.setAttr(f'{mid}.translateX', lock=True)
        ok &= refuses('locked translateX')
        cmds.setAttr(f'{mid}.translateX', lock=False)
        ok &= rebuilds('unlocked chain rebuilds again')

        # 4. A branch child would be orphaned by a shrink - and the branch
        # can be anywhere, not just at the tip.
        branch = cmds.createNode('joint', name=f'{SCRATCH}_branch')
        cmds.parent(branch, mid)
        ok &= refuses('branch child')
        cmds.delete(branch)
        ok &= rebuilds('unbranched chain rebuilds again')

        # 5. The exemption: an _ee_ is the one child that is not a branch.
        # Setup needs it and the rebuild re-places it, so the guard has to
        # let it through instead of refusing every finished chain.
        ee = cmds.createNode('joint', name=f'{SCRATCH}_ee_jnt')
        cmds.parent(ee, tip)
        # Out past the tip, where a real _ee_ lives. Left at the origin it
        # would sit on the base and hand the resample a chain that doubles
        # back on itself, since the _ee_ is now the end of the length being
        # re-spaced rather than a stub dragged behind the tip.
        tip_pos = cmds.xform(tip, q=True, ws=True, t=True)
        cmds.xform(ee, ws=True, t=[tip_pos[0] + 2.0, tip_pos[1], tip_pos[2]])
        ok &= rebuilds('_ee_ child is not treated as a branch')

        # 6. A lone joint is not a chain (_guard_min_length).
        lone = cmds.createNode('joint', name=f'{SCRATCH}_lone')
        junk.append(lone)
        try:
            rt_chain_build.rebuild(lone, 5, mode='uniform', snap=False)
            ok &= _verdict('single joint refused', False,
                           'the rebuild completed')
        except Exception as exc:
            ok &= _verdict('single joint refused',
                           isinstance(exc, RigTailBuildError),
                           f'{type(exc).__name__}: {exc}')
        return ok
    finally:
        if root:
            junk.append(root)
            try:
                rt_chain_build.clear_cache(root)
            except Exception:
                pass
        _delete_junk(junk)


def test_cache_no_compounding(rigname=DEFAULT_CHAIN):
    """
    Ten count changes and back to N land exactly where one N->n->N pass
    does. The empirical proof of the session original cache.

    Every rebuild resamples the chain's FIRST-SEEN shape, not what the last
    rebuild left behind, so distortion cannot accumulate however many times
    the count spinner is dragged. The claim is not the weak 'less than ten
    times the drift' - the two paths agree to within float noise, being the
    same single resampling of the same source.

    Power at k=3, not Uniform, so the redistribution is substantial on any
    chain; two paths that both moved nothing would prove nothing.

    Also checks the escape hatch: a chain edited by hand past
    JOINT_POS_TOLERANCE re-baselines, so the cache is not a cage.
    """
    if not _require_maya('test_cache_no_compounding'):
        return None
    found = _scene_chain('test_cache_no_compounding', rigname)
    if not found:
        return None
    root, bn = found

    state = _snapshot(root)
    n = len(bn)
    original = state['positions']
    ok = True
    try:
        # One pass out and back.
        rt_chain_build.clear_cache(root)
        root = rt_chain_build.rebuild(root, n + 5, mode='power', param=3.0,
                                      snap=False)[0]
        root = rt_chain_build.rebuild(root, n, mode='power', param=3.0,
                                      snap=False)[0]
        once = _positions(_chain_bn(root))
        drift_once = _max_move(original, once)

        # Back to the start, cache and all, then ten passes out and back.
        for joint, pos in zip(_chain_bn(root), original):
            cmds.xform(joint, ws=True, t=pos)
        # The _ee_ hangs off the tip, so the loop above drags it. It is the
        # baseline's final point: left where it lands, the ten passes
        # resample a longer tail than the one pass did.
        ee_start = rt_chain_build._end_joint(_chain_bn(root)[-1])
        if ee_start and state['ee_pos']:
            cmds.xform(ee_start, ws=True, t=state['ee_pos'])
        rt_chain_build.clear_cache(root)
        counts = [n + 5, n - 2, n + 9, n - 3, n + 12, n + 1,
                  n - 1, n + 7, n - 4, n + 3]
        for count in counts:
            root = rt_chain_build.rebuild(root, max(2, count), mode='power',
                                          param=3.0, snap=False)[0]
        root = rt_chain_build.rebuild(root, n, mode='power', param=3.0,
                                      snap=False)[0]
        many = _positions(_chain_bn(root))
        drift_many = _max_move(original, many)

        ok &= _verdict('re-spacing moved the chain at all',
                       drift_once > POS_TOL_SCENE,
                       f'one pass moved it {drift_once:.5f}')
        ok &= _verdict(f'{len(counts)} passes land where one pass does',
                       _max_move(once, many) < POS_TOL_SCENE,
                       f'max difference={_max_move(once, many):.6f}')
        ok &= _verdict('drift did not compound',
                       drift_many < drift_once * 1.5 + POS_TOL_SCENE,
                       f'one pass={drift_once:.5f}, '
                       f'{len(counts)} passes={drift_many:.5f}')

        # White box: the cached original is still the chain as it was
        # found. Every one of those rebuilds read it and none rewrote it.
        entry = rt_chain_build._ORIGINALS.get(_long(root), {})
        cached = entry.get('positions') or []
        # The baseline runs the tail's full length, so it carries one more
        # point than the chain has BN joints whenever there is an _ee_.
        want = n + (1 if state['ee_pos'] else 0)
        ok &= _verdict('cached original is the chain as it was found',
                       len(cached) == want and
                       _max_move(cached, original) < POS_TOL_SCENE,
                       f'{len(cached)} cached position(s), wanted {want}, '
                       f'max difference={_max_move(cached, original):.6f}')

        # Hand-edited chains re-baseline instead of snapping back.
        chain = _chain_bn(root)
        before_edit = _positions(chain)
        step = max(10.0 * rt_constants.JOINT_POS_TOLERANCE,
                   0.05 * _chain_length(before_edit))
        target = list(before_edit[1])
        target[1] += step
        cmds.xform(chain[1], ws=True, t=target)
        # Dragging one joint drags everything under it, so the hand-edited
        # shape is the chain as it now stands - not one moved point.
        edited = _positions(chain)
        ok &= _verdict('the hand edit is past JOINT_POS_TOLERANCE',
                       _max_move(before_edit, edited) >
                       rt_constants.JOINT_POS_TOLERANCE,
                       f'nudged by {step:.5f}, tolerance is '
                       f'{rt_constants.JOINT_POS_TOLERANCE}')
        root = rt_chain_build.rebuild(root, n, mode='keep', snap=False)[0]
        kept = _max_move(edited, _positions(_chain_bn(root)))
        ok &= _verdict('a hand edit re-baselines the cache',
                       kept < POS_TOL_SCENE,
                       f'moved {kept:.5f} from the hand-edited shape')
        return ok
    finally:
        _restore(state, root)


def test_undo(rigname=DEFAULT_CHAIN):
    """
    One undo restores the pre-click state, for a grow and for a shrink.

    The rebuild runs in a single undo chunk, so everything one click does -
    new nodes, deleted nodes, renumbering, the _ee_ re-parented and
    re-placed - has to come back in ONE step. The shrink is the harder
    half: undoing it must bring deleted joints back, named and placed as
    they were.

    The session original cache is a module global and is NOT undone, so the
    next rebuild re-baselines. That is intended, and checked elsewhere.
    """
    if not _require_maya('test_undo'):
        return None
    if not cmds.undoInfo(q=True, state=True):
        print('  test_undo: the undo queue is off, skipping')
        return None
    found = _scene_chain('test_undo', rigname)
    if not found:
        return None
    root, bn = found

    state = _snapshot(root)
    n = len(bn)
    ok = True
    try:
        for label, target in (('grow', n + 4), ('shrink', max(2, n - 2))):
            rt_chain_build.clear_cache(root)
            rt_chain_build.rebuild(root, target, mode='uniform', snap=False)
            cmds.undo()

            root = _resolve_root(state['names'][0])
            ok &= _verdict(f'undo after {label} restores the root joint',
                           bool(root), root or
                           f'{state["names"][0]} is not in the scene')
            if not root:
                break
            chain = _chain_bn(root)
            ok &= _verdict(f'undo after {label} restores the joint count',
                           len(chain) == n, f'{len(chain)} joints, wanted {n}')
            ok &= _verdict(f'undo after {label} restores every name',
                           _leaves(chain) == state['names'],
                           _name_diff(state['names'], _leaves(chain)))
            if len(chain) == n:
                moved = _max_move(state['positions'], _positions(chain))
                ok &= _verdict(f'undo after {label} restores the positions',
                               moved < POS_TOL_SCENE, f'max move={moved:.5f}')
            if not state['ee']:
                continue
            ee = rt_chain_build._end_joint(chain[-1]) if chain else None
            ok &= _verdict(f'undo after {label} restores the _ee_',
                           bool(ee) and ee.split('|')[-1] == state['ee'],
                           f'{state["ee"]} -> {ee}')
            if ee:
                d = math.dist(cmds.xform(ee, q=True, ws=True, t=True),
                              state['ee_pos'])
                ok &= _verdict(f'undo after {label} restores the _ee_ position',
                               d < POS_TOL_SCENE, f'moved {d:.5f}')
        return ok
    finally:
        _restore(state, root)


# RUNNERS ===============================================================

def run_math():
    """Run every safe math test and print a summary."""
    tests = [
        test_containment,
        test_distribution_endpoints,
        test_uniform_equivalence,
        test_invert_symmetry,
        test_taper_direction,
        test_pchip_monotone,
        test_pchip_knot_exact,
        test_catmullrom_interpolates,
        test_arclength,
        test_keep_idempotent,
        test_keep_preserves_distribution,
        test_resample_noop,
        test_respace_same_count,
        test_param_defaults,
        test_roundtrip_drift,
        test_snap_exact,
        test_degenerate,
    ]
    results = []
    for fn in tests:
        print(f"\n--- {fn.__name__} ---")
        try:
            results.append((fn.__name__, fn()))
        except Exception as exc:
            results.append((fn.__name__, exc))
    return _summary("CHAIN MATH TESTS", results)


def run_scene(chain=DEFAULT_CHAIN):
    """
    Run every MUTATING scene test on the loaded skeleton and print a
    summary. RELOAD THE SCENE afterwards.

    Each test puts the chain's count, names and positions back before the
    next one runs, but joints a shrink deleted return as new nodes and
    orientations are not restored - reload before building for real.

    Arguments:
        chain (str): rig part name, or the name of any joint in the chain.
    """
    print("\n*** run_scene MUTATES the skeleton. Reload before building. ***")
    tests = [
        (f"test_rebuild_count({chain})", lambda: test_rebuild_count(chain)),
        (f"test_names_preserved({chain})", lambda: test_names_preserved(chain)),
        (f"test_partial_rebuild({chain})", lambda: test_partial_rebuild(chain)),
        (f"test_radius_consistent({chain})",
         lambda: test_radius_consistent(chain)),
        ("test_guards", test_guards),
        (f"test_cache_no_compounding({chain})",
         lambda: test_cache_no_compounding(chain)),
        (f"test_undo({chain})", lambda: test_undo(chain)),
    ]
    results = []
    for name, fn in tests:
        print(f"\n--- {name} ---")
        try:
            results.append((name, fn()))
        except Exception as exc:
            results.append((name, exc))
    return _summary("CHAIN SCENE TESTS (mutating)", results)


def run_all():
    """Run the safe math tests and point to the mutating scene tests."""
    ok = run_math()
    print("Scene tests are MUTATING and not run by run_all().\n"
          "On the sample scene call:\n"
          "    rig_tail_chain_test.run_scene()\n")
    return ok
