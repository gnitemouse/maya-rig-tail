"""
rig_tail_chain_spacing.py
author: Daisy Jane @gnitemouse

Pure-math joint spacing for Tail Chain Builder.
No Maya imports — testable in plain Python.

Shape and distribution are separate:
    Shape — the Catmull–Rom curve through the chain's positions.
    Distribution — where along that arclength each joint sits.
    Distribution modes are analytic functions of j/(n-1), which
    makes them count-independent and idempotent (zero drift).

Functions:
    catmull_rom_knots: knot parameters for centripetal Catmull–Rom
    catmull_rom_eval: evaluate Catmull–Rom at a knot parameter
    arclength_table: build cumulative arclength table
    eval_at_arclength: evaluate the curve at a normalised arclength
    distribute: produce normalised positions along [0, 1]
    pchip_tangents: Fritsch–Carlson monotone tangents
    pchip_eval: evaluate monotone cubic Hermite at query points
    snap_to_source: snap target u values to existing source indices
    resample: full pipeline — knots, arclength, distribute, snap, eval
"""

import math


# MODULE CONSTANTS ======================================================

ARC_SAMPLES = 16      # sub-samples per span when building the arclength table
SNAP_TOL = 1e-4       # normalised-arclength window for snap-to-existing
EPS = 1e-9
ALPHA = 0.5           # centripetal Catmull–Rom
K_RANGE = (0.2, 5.0)  # power exponent clamping
R_RANGE = (0.5, 1.5)  # ratio clamping


# VECTOR HELPERS (inline, no numpy) ====================================

def _add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _scale(v, s):
    return [v[i] * s for i in range(3)]


def _length(v):
    return math.sqrt(sum(x * x for x in v))


def _norm(v):
    d = _length(v)
    return [x / d for x in v] if d > EPS else [0.0, 0.0, 0.0]


def _lerp(a, b, t):
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def _midpoint(a, b):
    return [(a[i] + b[i]) * 0.5 for i in range(3)]


# CENTRIPETAL CATMULL–ROM ==============================================

def catmull_rom_knots(points):
    """
    Knot parameters for a centripetal Catmull–Rom curve.

    Arguments:
        points (list of [x, y, z]): N input positions, N >= 2

    Return:
        list: knot parameters t_0 .. t_{N-1}, t_0 = 0
    """
    n = len(points)
    knots = [0.0]
    for i in range(1, n):
        d = _length(_sub(points[i], points[i - 1]))
        knots.append(knots[-1] + d ** ALPHA)
    return knots


def catmull_rom_eval(points, knots, t):
    """
    Evaluate a centripetal Catmull–Rom curve at parameter t.

    Uses Barry–Goldman recursive form (three lerp levels).
    N == 2 falls back to straight-line lerp.

    Arguments:
        points (list of [x, y, z]): N input positions
        knots (list): knot parameters from catmull_rom_knots
        t (float): parameter to evaluate at

    Return:
        list: [x, y, z] position on the curve
    """
    n = len(points)
    if n == 2:
        u = (t - knots[0]) / (knots[1] - knots[0]) if knots[1] > knots[0] else 0.0
        return _lerp(points[0], points[1], u)

    # Find the span containing t
    i = 1
    while i < n - 1 and knots[i] < t:
        i += 1
    i -= 1  # span index: points[i] .. points[i+1]
    if i < 0:
        i = 0
    if i >= n - 1:
        i = n - 2

    p0 = points[i - 1] if i > 0 else _sub(_scale(points[0], 2), points[1])
    p1 = points[i]
    p2 = points[i + 1]
    p3 = points[i + 2] if i + 2 < n else _sub(_scale(points[-1], 2), points[-2])

    t0 = knots[i - 1] if i > 0 else (2 * knots[0] - knots[1])
    t1 = knots[i]
    t2 = knots[i + 1]
    t3 = knots[i + 2] if i + 2 < n else (2 * knots[-1] - knots[-2])

    # Barry–Goldman: three lerp levels
    def _span_lerp(a, b, ta, tb):
        if abs(tb - ta) < EPS:
            return _midpoint(a, b)
        s = (t - ta) / (tb - ta)
        return _lerp(a, b, s)

    a01 = _span_lerp(p0, p1, t0, t1)
    a11 = _span_lerp(p1, p2, t1, t2)
    a21 = _span_lerp(p2, p3, t2, t3)

    a02 = _span_lerp(a01, a11, t0, t2)
    a12 = _span_lerp(a11, a21, t1, t3)

    return _span_lerp(a02, a12, t1, t2)


# ARCLENGTH TABLE ======================================================

def arclength_table(points):
    """
    Build a cumulative arclength table from a Catmull–Rom curve.

    Samples ARC_SAMPLES sub-points per span.

    Arguments:
        points (list of [x, y, z]): N input positions

    Return:
        (list, float): (cumulative_arclengths, total_length)
            cumulative_arclengths has (N-1) * ARC_SAMPLES + 1 entries,
            starting at 0 and ending at total_length.
    """
    knots = catmull_rom_knots(points)
    n = len(points)
    total = (n - 1) * ARC_SAMPLES
    table = [0.0]
    prev = list(points[0])
    # Sample every span independently.  A global, uniformly spaced knot
    # parameter misses the original knots on non-uniform chains, which in
    # turn makes s_hat point at the wrong entries in the table.
    for span in range(n - 1):
        for step in range(1, ARC_SAMPLES + 1):
            t = knots[span] + (knots[span + 1] - knots[span]) * step / ARC_SAMPLES
            pos = catmull_rom_eval(points, knots, t)
            table.append(table[-1] + _length(_sub(pos, prev)))
            prev = pos
    total_length = table[-1]
    return table, total_length


def eval_at_arclength(points, knots, table, total, u):
    """
    Evaluate the Catmull–Rom curve at a normalised arclength u in [0, 1].

    Inverts the arclength table by binary search plus linear interpolation.

    Arguments:
        points (list of [x, y, z]): N input positions
        knots (list): knot parameters from catmull_rom_knots
        table (list): cumulative arclengths from arclength_table
        total (float): total arclength from arclength_table
        u (float): normalised arclength in [0, 1]

    Return:
        list: [x, y, z] position on the curve
    """
    if total < EPS:
        return list(points[0])
    target = u * total

    # Binary search
    lo, hi = 0, len(table) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if table[mid] < target:
            lo = mid
        else:
            hi = mid

    # Linear interpolate between lo and hi
    def sample_t(index):
        if index >= len(table) - 1:
            return knots[-1]
        span, step = divmod(index, ARC_SAMPLES)
        return knots[span] + (knots[span + 1] - knots[span]) * step / ARC_SAMPLES

    t_lo = sample_t(lo)
    t_hi = sample_t(hi)

    if abs(table[hi] - table[lo]) < EPS:
        t = t_lo
    else:
        frac = (target - table[lo]) / (table[hi] - table[lo])
        t = t_lo + (t_hi - t_lo) * frac

    return catmull_rom_eval(points, knots, t)


# DISTRIBUTION ==========================================================

def distribute(mode, n, param=None, invert=False, source=None):
    """
    Generate n normalised positions along [0, 1] using a spacing profile.

    Modes:
        uniform  — u = t
        power    — u = t ** k          (k default 1.7, clamped to K_RANGE)
        ratio    — geometric ratio r    (r default 0.90, clamped to R_RANGE)
        keep     — PCHIP resample of source distribution (source required)

    Arguments:
        mode (str): 'uniform', 'power', 'ratio', or 'keep'
        n (int): Number of positions to generate (n >= 2)
        param (float): Exponent k (power) or ratio r (ratio)
        invert (bool): Mirror the distribution (base <-> tip)
        source (list of float): Normalised source positions for 'keep'

    Return:
        list: n normalised positions in [0, 1], strictly increasing
    """
    if n < 2:
        return [0.0, 1.0] if n == 1 else []

    t_vals = [i / (n - 1) for i in range(n)]

    if mode == 'uniform':
        u_vals = list(t_vals)

    elif mode == 'power':
        k = K_RANGE[0] if param is None else max(K_RANGE[0], min(K_RANGE[1], param))
        if abs(k - 1.0) < EPS:
            u_vals = list(t_vals)
        else:
            u_vals = [t ** k for t in t_vals]

    elif mode == 'ratio':
        r = R_RANGE[0] if param is None else max(R_RANGE[0], min(R_RANGE[1], param))
        if abs(r - 1.0) < EPS:
            u_vals = list(t_vals)
        else:
            u_vals = [(1.0 - r ** i) / (1.0 - r ** (n - 1)) for i in range(n)]

    elif mode == 'keep':
        if not source or len(source) < 2:
            u_vals = list(t_vals)
        else:
            # Preserve the source distribution: joint index is the input
            # coordinate and its curve arclength is the output coordinate.
            # Reversing those axes instead produces the inverse profile.
            x_norm = [i / (len(source) - 1) for i in range(len(source))]
            m = pchip_tangents(x_norm, source)
            u_vals = pchip_eval(x_norm, source, m, t_vals)
            # clamp to [0, 1] and enforce monotonicity
            u_vals = [max(0.0, min(1.0, v)) for v in u_vals]
            for i in range(1, len(u_vals)):
                if u_vals[i] < u_vals[i - 1]:
                    u_vals[i] = u_vals[i - 1]

    else:
        u_vals = list(t_vals)

    if invert:
        u_vals = [1.0 - v for v in reversed(u_vals)]

    return u_vals


# PCHIP (Fritsch–Carlson monotone cubic Hermite) ========================

def pchip_tangents(x, y):
    """
    Fritsch–Carlson monotone tangents for a piecewise cubic Hermite
    interpolant.

    Arguments:
        x (list): strictly increasing knot positions
        y (list): values at knots

    Return:
        list: slopes at each knot
    """
    n = len(x)
    if n < 2:
        return [0.0]
    if n == 2:
        h = x[1] - x[0]
        return [(y[1] - y[0]) / h] * 2 if abs(h) > EPS else [0.0, 0.0]

    deltas = [(y[i + 1] - y[i]) / (x[i + 1] - x[i])
              for i in range(n - 1)] if all(
        abs(x[i + 1] - x[i]) > EPS for i in range(n - 1)) else [0.0] * (n - 1)

    m = [0.0] * n
    # Interior
    for i in range(1, n - 1):
        if deltas[i - 1] * deltas[i] <= 0.0:
            m[i] = 0.0
        else:
            h0 = x[i] - x[i - 1]
            h1 = x[i + 1] - x[i]
            w1 = 2.0 * h1 + h0
            w2 = h1 + 2.0 * h0
            if abs(w1 / deltas[i - 1] + w2 / deltas[i]) < EPS:
                m[i] = 0.0
            else:
                m[i] = (w1 + w2) / (w1 / deltas[i - 1] + w2 / deltas[i])
    # Endpoints
    m[0] = deltas[0]
    m[-1] = deltas[-1]
    return m


def pchip_eval(x, y, m, xq):
    """
    Evaluate a PCHIP interpolant at query points.

    Arguments:
        x (list): knot positions (strictly increasing)
        y (list): values at knots
        m (list): slopes at knots (from pchip_tangents)
        xq (list): query positions

    Return:
        list: interpolated values at xq
    """
    n = len(x)
    result = []
    for q in xq:
        if q <= x[0]:
            result.append(y[0])
            continue
        if q >= x[-1]:
            result.append(y[-1])
            continue

        # Find span
        i = 0
        while i < n - 2 and x[i + 1] < q:
            i += 1

        h = x[i + 1] - x[i]
        if abs(h) < EPS:
            result.append(y[i])
            continue

        s = (q - x[i]) / h  # normalised in [0, 1]

        # Hermite basis functions
        h00 = 2.0 * s ** 3 - 3.0 * s ** 2 + 1.0
        h10 = s ** 3 - 2.0 * s ** 2 + s
        h01 = -2.0 * s ** 3 + 3.0 * s ** 2
        h11 = s ** 3 - s ** 2

        result.append(h00 * y[i] + h10 * h * m[i] + h01 * y[i + 1] + h11 * h * m[i + 1])

    return result


# SNAP TO EXISTING ======================================================

def snap_to_source(u_list, s_hat, tol=SNAP_TOL):
    """
    For each target u_j, if it lands within tol of an existing s_hat value,
    return the source index so the caller writes the original position.

    Rules:
        - j = 0 always snaps to index 0
        - j = n-1 always snaps to the last index
        - Indices must strictly increase; never let two targets claim the
          same source (that would create a zero-length segment).

    Arguments:
        u_list (list of float): target normalised positions in [0, 1]
        s_hat (list of float): source normalised positions in [0, 1]
        tol (float): snap window

    Return:
        list of int or None: source index for each target, or None for
            unsnapped positions
    """
    n = len(u_list)
    m = len(s_hat)
    if n == 0 or m == 0:
        return [None] * n

    snapped = [None] * n
    snapped[0] = 0
    snapped[-1] = m - 1

    last_idx = 0
    for j in range(1, n - 1):
        closest = None
        for i in range(last_idx + 1, m - 1):
            if abs(u_list[j] - s_hat[i]) < tol:
                closest = i
                break
        if closest is not None and closest > last_idx:
            snapped[j] = closest
            last_idx = closest

    return snapped


# TOP-LEVEL ENTRY POINT =================================================

def resample(source_points, n, mode, param=None, invert=False, snap=True):
    """
    Full resampling pipeline: shape reconstruction from source positions
    followed by redistribution.

    Pipeline: knots -> arclength table -> source s_hat -> distribute ->
    snap_to_source -> eval_at_arclength for unsnapped.

    Arguments:
        source_points (list of [x, y, z]): N source joint positions
        n (int): target joint count (n >= 2)
        mode (str): spacing mode ('uniform', 'power', 'ratio', 'keep')
        param (float): mode parameter (k for power, r for ratio)
        invert (bool): invert the distribution
        snap (bool): enable snap-to-existing

    Return:
        (list of [x, y, z], list of int or None): (target positions,
            snapped_indices)
    """
    N = len(source_points)
    if n == N:
        # Exact no-op at unchanged count — the Catmull–Rom interpolates
        # at its knots, so no resampling is needed.
        return [list(p) for p in source_points], [i for i in range(N)]

    knots = catmull_rom_knots(source_points)
    table, total = arclength_table(source_points)

    # Source normalised arclengths: the cumulative arclength fraction at
    # each original knot. On a uniform-straight chain these equal i/(N-1);
    # on a curved chain they differ, which is why snap uses the actual
    # arclength, not the parameter fraction.
    s_hat = []
    for i in range(N):
        if total > EPS:
            s_hat.append(table[i * ARC_SAMPLES] / total)
        else:
            s_hat.append(float(i) / (N - 1))

    # Target distribution
    u_vals = distribute(mode, n, param=param, invert=invert, source=s_hat if mode == 'keep' else None)

    snapped_indices = []
    if snap:
        snapped_indices = snap_to_source(u_vals, s_hat)
    else:
        snapped_indices = [None] * n

    positions = []
    for j in range(n):
        if snapped_indices[j] is not None:
            positions.append(list(source_points[snapped_indices[j]]))
        else:
            positions.append(eval_at_arclength(source_points, knots, table, total, u_vals[j]))

    return positions, snapped_indices
