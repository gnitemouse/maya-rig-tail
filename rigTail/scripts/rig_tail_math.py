"""
rig_tail_math.py
author: Daisy Jane @gnitemouse

Math helpers for Rig Tail.
Vector operations, position calculations, and orientation utilities.

Functions:
    linspace: Generate evenly spaced values
    cumulative_lengths / length_fractions: distance along a point chain
    nearest_index: index of the entry closest to a target value
    transform_vector: transform a displacement by a matrix (3x3 only)
    invert_matrix: inverse of a 16-float matrix
    greville_fractions: normalised Greville abscissae of a clamped curve
    clamp_degree / clamped_uniform_knots: the curve create_curve builds
    bspline_point: evaluate a clamped uniform B-spline off-scene
    bspline_arclength_table / bspline_at_arclength: arclength along one
    get_axis_orientation: Guess axis from position bounding box
    get_local_orientation: Determine chain direction from joint orientations
    get_local_pos: Get local position
    get_world_pos: Get world position
    get_local_vec_to_worldspace: Transform local vector to worldspace
    get_local_vec: Get normalized vector between two nodes
    get_vec_length: Get distance between two nodes
    axis_vector_colinearity: Find which local axis aligns with vector
"""

import bisect
import math
import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup, abort_build

logger = logger_setup(__name__)


def linspace(start, stop, n):
    """
    Generate n evenly spaced values from start to stop (inclusive).

    Arguments:
        start (float): Start value
        stop (float): End value
        n (int): Number of values to generate

    Yields:
        float: Each value in the sequence
    """
    if n == 1:
        yield stop
        return
    h = (stop - start) / (n - 1)
    for i in range(n):
        yield start + h * i


def cumulative_lengths(points):
    """
    Running distance along a chain of points, starting at 0.

    Arguments:
        points (list): List of [x, y, z] positions

    Return:
        list: Same length as points; entry i is the distance from points[0]
            to points[i] measured along the chain
    """
    out = [0.0]
    for i in range(1, len(points)):
        a, b = points[i-1], points[i]
        out.append(out[-1] + math.sqrt(sum((b[k]-a[k])**2 for k in range(3))))
    return out


def length_fractions(points):
    """
    cumulative_lengths normalised to 0..1, base to tip.

    This is the metric an animator reads as 'how far along the tail': it is
    evenly spaced in space, unlike joint INDEX, which on a tapered chain
    (8:1 bone ratio on the squid fintails) bunches badly toward the tip.

    Arguments:
        points (list): List of [x, y, z] positions

    Return:
        list: Fractions 0..1, or all zeros for a zero-length chain
    """
    lengths = cumulative_lengths(points)
    total = lengths[-1] if lengths else 0.0
    if total <= 0:
        return [0.0] * len(points)
    return [v / total for v in lengths]


def nearest_index(values, target):
    """
    Index of the entry in values closest to target.

    Arguments:
        values (list): Sorted or unsorted numbers
        target (float): Value to match

    Return:
        int: Index of the closest entry, or 0 for an empty list
    """
    if not values:
        return 0
    return min(range(len(values)), key=lambda i: abs(values[i] - target))


def greville_fractions(num, degree=3):
    """
    Normalised Greville abscissae for a clamped uniform NURBS curve.

    A CV does not sit at one parameter - it influences a stretch of curve.
    The Greville abscissa is the one parameter that is most a given CV's
    own: where its basis function peaks. For CV j of a degree-d curve it is
    the mean of the d knots following knot j.

    This is the metric that makes a control's DRAWN position agree with the
    joints it actually rotates. The FK curve carries one CV per joint, so
    feeding a joint's Greville fraction to a pointOnCurveInfo (with
    turnOnPercentage on, i.e. a fraction of the PARAMETER range) lands
    exactly on that joint - no lookup table needed. Measured on the squid
    C_fintail the error is under 0.05% of tail length from joint 3 down;
    joints 1-2 sit up to 0.55 units off because the first bone is 8x the
    last against a clamped start knot, which no control is placed near.

    Computed analytically rather than by closest-point query: it is exact
    for the curve create_curve builds (clamped, uniform, one CV per joint)
    and strictly increasing by construction - and joint_pos MUST be
    monotonic or falloff_rotation's ramp hands one control's rotation to
    two separate stretches of tail. A closest-point query is more accurate
    at joints 1-2 but carries no such guarantee.

    Arguments:
        num (int): Number of CVs
        degree (int): Curve degree; clamped to num-1 the way create_curve
            clamps it, so the two cannot disagree on a short chain

    Return:
        list: num fractions from 0.0 at the base to 1.0 at the tip
    """
    if num < 1:
        return []
    if num == 1:
        return [0.0]
    d = clamp_degree(num, degree)
    knots = clamped_uniform_knots(num, d)
    grev = [sum(knots[j+1:j+1+d]) / d for j in range(num)]
    if grev[-1] <= 0:
        return [0.0] * num
    return [g / grev[-1] for g in grev]


def clamp_degree(num, degree=3):
    """
    Curve degree for num CVs, clamped the way create_curve clamps it.

    Arguments:
        num (int): Number of CVs
        degree (int): Requested degree

    Return:
        int: Usable degree, at least 1
    """
    return max(1, min(degree, num - 1))


def clamped_uniform_knots(num, degree=3):
    """
    Full knot vector for the curve cmds.curve(p=...) builds: clamped ends,
    unit-spaced interior, num+degree+1 knots.

    Maya's own .knots data omits the outermost knot at each end; this
    returns the mathematical vector, which is what an evaluator needs.

    Arguments:
        num (int): Number of CVs
        degree (int): Curve degree (clamped against num)

    Return:
        list: num+degree+1 knot values, 0 .. num-degree
    """
    d = clamp_degree(num, degree)
    spans = num - d
    return ([0.0] * (d + 1)
            + [float(i) for i in range(1, spans)]
            + [float(spans)] * (d + 1))


def transform_vector(vec, matrix):
    """
    Transform a DISPLACEMENT by a matrix: the 3x3 part only, no translation.

    Maya matrices are row-major and vectors multiply on the left (v * M),
    which is the convention pointMatrixMult uses in vectorMultiply mode - so
    a value baked with this comes back out of that node unchanged when the
    matrix is the inverse of the one used here.

    Arguments:
        vec (list): [x, y, z] displacement
        matrix (list): 16 floats, row-major, as cmds.getAttr returns

    Return:
        list: [x, y, z] transformed displacement
    """
    return [sum(vec[k] * matrix[k*4 + c] for k in range(3)) for c in range(3)]


def invert_matrix(matrix):
    """
    Inverse of a 16-float row-major matrix, in the same layout.

    Goes through MMatrix rather than transposing the 3x3, which is only the
    inverse when the matrix is a pure rotation. The frames this is used on
    carry the rig's global scale as well.

    Arguments:
        matrix (list): 16 floats, row-major, as cmds.getAttr returns

    Return:
        list: 16 floats, row-major
    """
    inv = om.MMatrix(matrix).inverse()
    return [inv.getElement(r, c) for r in range(4) for c in range(4)]


def bspline_point(cvs, u, degree=3):
    """
    Evaluate a clamped uniform B-spline at parameter u, by de Boor.

    Used to work out, in Python, where the low-CV IK driver curve sits at a
    given parameter WITHOUT reading the scene. That matters because the
    figure is needed as a rest reference: reading it off the live curve
    would pick up whatever the animator has the controls doing on a
    rebuild, and bake a posed shape in as rest.

    Arguments:
        cvs (list): CV positions, each an [x, y, z]
        u (float): Parameter, clamped into the curve's range
        degree (int): Curve degree (clamped against the CV count)

    Return:
        list: [x, y, z] point on the curve
    """
    n = len(cvs)
    if n == 0:
        return [0.0, 0.0, 0.0]
    if n == 1:
        return list(cvs[0])
    d = clamp_degree(n, degree)
    knots = clamped_uniform_knots(n, d)
    lo, hi = knots[d], knots[n]
    u = min(max(u, lo), hi)
    # Span containing u. The last span is closed on the right so u == hi
    # lands on the final CV instead of falling off the end.
    k = d
    while k < n - 1 and u >= knots[k+1]:
        k += 1
    pts = [list(cvs[k-d+j]) for j in range(d+1)]
    for r in range(1, d+1):
        for j in range(d, r-1, -1):
            i = k - d + j
            den = knots[i+d-r+1] - knots[i]
            a = 0.0 if den == 0 else (u - knots[i]) / den
            pts[j] = [(1.0-a)*pts[j-1][c] + a*pts[j][c] for c in range(3)]
    return pts[d]


def bspline_arclength_table(cvs, degree=3, samples=0):
    """
    Sample a clamped uniform B-spline and accumulate arclength along it.

    The B-spline counterpart of rig_tail_chain_spacing.arclength_table (which
    is Catmull-Rom, an interpolating curve, and cannot describe this one).

    Arguments:
        cvs (list): CV positions, each an [x, y, z]
        degree (int): Curve degree (clamped against the CV count)
        samples (int): Sample count; 0 picks 8 per CV, floor 200, which
            measures length to well under a thousandth of a unit on a tail

    Return:
        (list, list): (points, cumulative) - cumulative[i] is the arclength
            from the start to points[i], so cumulative[-1] is the total
    """
    n = len(cvs)
    if n < 2:
        return ([list(cvs[0])] if n else [[0.0, 0.0, 0.0]]), [0.0]
    d = clamp_degree(n, degree)
    knots = clamped_uniform_knots(n, d)
    lo, hi = knots[d], knots[n]
    if samples <= 0:
        samples = max(200, 8 * n)
    points = [bspline_point(cvs, lo + (hi - lo) * i / samples, d)
              for i in range(samples + 1)]
    cumulative = [0.0]
    for i in range(1, len(points)):
        a, b = points[i-1], points[i]
        cumulative.append(cumulative[-1]
                          + math.sqrt(sum((b[k]-a[k])**2 for k in range(3))))
    return points, cumulative


def bspline_at_arclength(points, cumulative, target):
    """
    Point at a given arclength along a sampled curve.

    Interpolates INSIDE the sample interval rather than snapping to the
    nearest sample. Snapping quantises the result to the table spacing,
    which is enough to stop solver_curve_cvs' iteration converging - it
    ends up chasing the quantisation instead of the shape.

    Clamps to the curve's end when target runs past it.

    Arguments:
        points (list): Sampled positions from bspline_arclength_table
        cumulative (list): Matching cumulative arclengths
        target (float): Arclength to find

    Return:
        list: [x, y, z] position on the curve
    """
    if target <= 0 or len(points) < 2:
        return list(points[0])
    if target >= cumulative[-1]:
        return list(points[-1])
    k = max(1, min(bisect.bisect_left(cumulative, target), len(points) - 1))
    span = cumulative[k] - cumulative[k-1]
    t = 0.0 if span <= 0 else (target - cumulative[k-1]) / span
    return [points[k-1][c] + t * (points[k][c] - points[k-1][c])
            for c in range(3)]


def get_axis_orientation(nodes, secondary_axis=False):
    """
    Guess axis orientation for a list of objects based on pivot world positions.

    Arguments:
        nodes (list): List of objects
        secondary_axis (bool): If True, return secondary axis; else return up axis

    Return:
        str: '+x', '+y', or '+z' representing the axis
    """
    node_pos = []
    for node in nodes:
        node_pos.append(cmds.xform(node, q=1, ws=1, rp=1))

    # Get range of XYZ values
    range_xyz = []
    for values in zip(*node_pos):
        floats = sorted(values)
        min_val = round(floats[0], 5)
        max_val = round(floats[-1], 5)
        axis_range = max_val - min_val
        range_xyz.append(axis_range)

    # Compare edges of bounding box
    if range_xyz[0] > range_xyz[1] and range_xyz[0] > range_xyz[2]:
        if not secondary_axis:
            return '+x'
        else:
            return '+y'
    elif range_xyz[1] > range_xyz[0] and range_xyz[1] > range_xyz[2]:
        if not secondary_axis:
            return '+y'
        else:
            return '+z'
    else:
        if not secondary_axis:
            return '+z'
        else:
            return '+y'


def get_local_orientation(nodes, tol=1e-5):
    """
    Determine which local axis of the first node the chain extends along.

    Compares the first node against the nearest following node that is
    actually offset from it, rather than against nodes[1] unconditionally:
    joint chains often stack several joints on the same point (a driven
    chain evaluates to its rest pose, an unbuilt chain may sit entirely at
    its root), and a zero-length vector cannot name an axis.

    Returns None rather than guessing when no node in the list is offset
    from the first. Callers must treat that as "this chain has no
    direction" - falling back to a default axis there silently orients the
    whole rig component off a made-up value.

    Arguments:
        nodes (list): List of objects (minimum 2)
        tol (float): Minimum distance for a node to count as offset

    Return:
        str: '+x', '+y', '+z', '-x', '-y', or '-z', or None if the chain
            direction cannot be determined
    """
    if len(nodes) < 2:
        logger.warning('Need at least 2 nodes to determine chain direction')
        return None

    # First node that is actually offset from the root of the chain
    pos1 = cmds.xform(nodes[0], q=1, ws=1, t=1)
    world_vec = None
    for node in nodes[1:]:
        pos2 = cmds.xform(node, q=1, ws=1, t=1)
        vec = [pos2[i] - pos1[i] for i in range(3)]
        mag = math.sqrt(sum(v * v for v in vec))
        if mag > tol:
            world_vec = [v / mag for v in vec]
            break

    if world_vec is None:
        logger.error(f"'{nodes[0]}': every node within {tol} of the first, "
                     f'cannot determine chain direction')
        return None

    # Get joint's world matrix
    matrix = cmds.xform(nodes[0], q=1, ws=1, m=1)

    # Extract local axes from matrix (each axis is 3 values)
    local_x = [matrix[0], matrix[1], matrix[2]]
    local_y = [matrix[4], matrix[5], matrix[6]]
    local_z = [matrix[8], matrix[9], matrix[10]]

    # Dot product to find which local axis aligns with world direction
    def dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    # Find max alignment
    dots = {'x': dot(world_vec, local_x),
            'y': dot(world_vec, local_y),
            'z': dot(world_vec, local_z)}
    max_axis = max(dots, key=lambda k: abs(dots[k]))
    sign = '+' if dots[max_axis] > 0 else '-'

    result = f'{sign}{max_axis}'
    logger.trace(f'Joint chain direction: {result}')
    return result


def get_local_pos(node):
    """
    Get node's local (object-space) position.

    Arguments:
        node (str): Node name

    Return:
        list: [x, y, z] local position
    """
    return cmds.xform(node, q=1, os=1, t=1)


def get_world_pos(node):
    """
    Get node's world position.

    Arguments:
        node (str): Node name

    Return:
        list: [x, y, z] world position
    """
    return cmds.xform(node, q=1, ws=1, t=1)


def get_local_vec_to_worldspace(node, vec=om.MVector.kXaxisVector):
    """
    Transform a local axis vector to worldspace.

    Arguments:
        node (str): Node name
        vec (MVector): Local vector to transform (default X-axis)

    Return:
        MVector: Normalized worldspace vector
    """
    matrix = om.MGlobal.getSelectionListByName(node).getDagPath(0).inclusiveMatrix()
    vec = (vec * matrix).normal()
    return vec


def get_local_vec(start_node, end_node):
    """
    Get normalized direction vector from start to end node.

    Arguments:
        start_node (str): Start node name
        end_node (str): End node name

    Return:
        MVector: Normalized direction vector
    """
    start = om.MVector(get_world_pos(start_node))
    end = om.MVector(get_world_pos(end_node))
    vec = om.MVector(end - start).normal()
    return vec


def get_vec_length(start_node, end_node):
    """
    Get distance between two nodes.

    Arguments:
        start_node (str): Start node name
        end_node (str): End node name

    Return:
        float: Distance between nodes
    """
    start = om.MVector(get_world_pos(start_node))
    end = om.MVector(get_world_pos(end_node))
    length = om.MVector(end - start).length()
    return length


def axis_vector_colinearity(node, vec):
    """
    Find which local axis of a node is most aligned with a world vector.

    Arguments:
        node (str): Node name
        vec (tuple/list): World vector to compare

    Return:
        str: 'x', 'y', or 'z'
    """
    vec = om.MVector(vec)
    x = vec * get_local_vec_to_worldspace(node, vec=om.MVector.kXaxisVector)
    y = vec * get_local_vec_to_worldspace(node, vec=om.MVector.kYaxisVector)
    z = vec * get_local_vec_to_worldspace(node, vec=om.MVector.kZaxisVector)

    maxi = max(x, y, z)
    if maxi == x:
        return 'x'
    elif maxi == y:
        return 'y'
    elif maxi == z:
        return 'z'
    else:
        abort_build(logger, 'Failed to compute axis vector colinearity.')
