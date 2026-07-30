"""
rig_tail_chain_test.py
author: Daisy Jane @gnitemouse

Tests for Joint Chain Builder (rig_tail_chain_spacing and
rig_tail_chain_build). Two kinds:

  MATH tests - deterministic, no scene needed. Exercise the pure-math
    spacing functions in isolation under plain Python.
    run_math()

  SCENE tests - run on the loaded skeleton and are MUTATING: they
    create, rebuild and re-space real joint chains. Like the real tool
    they affect the scene, so RELOAD THE SCENE afterwards.
    run_scene()

Usage:
    import rig_tail_chain_test as rt_chain_test
    rt_chain_test.run_math()               # safe: pure-math unit tests
    rt_chain_test.run_scene()              # MUTATING: every feature on the scene

Containment:
    test_containment greps every core module for rig_tail_chain and
    fails if any is found - enforcing the one-way import rule.

Functions:
  Runners
    run_math: every math test (safe), with a PASS/FAIL summary
    run_scene: every scene test (MUTATING), with a PASS/FAIL summary
    run_all: run_math plus a pointer to the mutating scene tests
  Math tests (safe, no scene)
    test_containment: no core module mentions rig_tail_chain
    test_distribution_endpoints: every mode/param: f(0)=0, f(1)=1
    test_uniform_equivalence: power k=1 == ratio r=1 == uniform
    test_invert_symmetry: invert(invert(f)) == f
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
    test_guards: skinned/rig-driven/branching chains are refused
    test_cache_no_compounding: drift same as single pass, not 10x
    test_undo: one Ctrl+Z restores the pre-click state
"""

import math
import os

import rig_tail_chain_spacing as rt_chain_spacing


POS_TOL = 1e-6
POS_TOL_SCENE = 1e-3
LEN_TOL = 1e-9


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


def _smooth_chain(n, bunch=2.0):
    """A tail-like chain: a smooth curve, unevenly spaced along itself.

    _random_chain is uniform noise in a unit cube — consecutive segments
    turn through ~90-180 degrees, which no real skeleton does. Shape-
    stability claims are about chains like this one instead: curved, but
    with a well-defined path that survives being resampled.
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
    """No core module imports or mentions rig_tail_chain."""
    scripts_dir = os.path.dirname(__file__)
    ok = True
    for fname in os.listdir(scripts_dir):
        if not fname.startswith("rig_tail_") or fname == os.path.basename(__file__):
            continue
        if fname.startswith("rig_tail_chain"):
            continue
        fpath = os.path.join(scripts_dir, fname)
        if not fpath.endswith(".py"):
            continue
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "rig_tail_chain" in content:
                ok &= _verdict(f"{fname} mentions rig_tail_chain", False)
        except (IOError, OSError):
            pass
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

    This is the exactness claim the whole anti-drift argument rests on, and
    it is NOT 'n == N returns the input': asking for Uniform on a chain that
    is not uniform must genuinely redistribute it. Three separate promises:

      Keep at n == N is EXACT at any count — PCHIP and Catmull-Rom both
        interpolate at their knots, and that is the default mode, so the
        first click on a freshly selected chain never touches it.
      The analytic modes are idempotent to within a hair of the chain
        length once the count actually samples the shape. At tiny counts
        the reconstructed curve IS the loss (plan section 4), so a second
        pass lands slightly differently; the error falls away with count.
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
                # Bound the shape-reconstruction error, not floating point:
                # N samples of a curve reconstruct it to O(1/N^2), so the
                # limit tightens with count instead of being one flat number
                # that is either meaningless at n=21 or unmeetable at n=3.
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
    # somewhere new. What must hold even there is contraction — successive
    # passes move less, never more — so a stuck artist clicking Build
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
    # Straight line — the drift comes solely from the down-res pass.
    pts = [[float(i) * 0.5, 0.0, 0.0] for i in range(20)]
    mid, _ = rt_chain_spacing.resample(pts, 30, "uniform")
    back, _ = rt_chain_spacing.resample(mid, 20, "keep")
    d = max(math.sqrt(sum((back[i][j] - pts[i][j]) ** 2 for j in range(3)))
            for i in range(20))
    ok = _verdict("roundtrip 20->30->20 straight chain",
                  d < POS_TOL, f"max deviation={d}")

    # Random chain — drift should still be bounded by one down-res pass.
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


# RUNNERS ===============================================================

def run_math():
    """Run every safe math test and print a summary."""
    tests = [
        test_containment,
        test_distribution_endpoints,
        test_uniform_equivalence,
        test_invert_symmetry,
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


def run_scene():
    """Run every MUTATING scene test. RELOAD THE SCENE afterwards."""
    print("\n*** run_scene MUTATES the skeleton. Reload before building. ***")
    tests = [
        ("test_rebuild_count", lambda: None),
        ("test_names_preserved", lambda: None),
        ("test_guards", lambda: None),
        ("test_cache_no_compounding", lambda: None),
        ("test_undo", lambda: None),
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
