"""
rig_tail_math.py
author: Daisy Jane @gnitemouse

Math helpers for Rig Tail.
Vector operations, position calculations, and orientation utilities.

Functions:
    linspace: Generate evenly spaced values
    get_axis_orientation: Guess axis from position bounding box
    get_local_orientation: Determine chain direction from joint orientations
    get_local_pos: Get local position
    get_world_pos: Get world position
    get_local_vec_to_worldspace: Transform local vector to worldspace
    get_local_vec: Get normalized vector between two nodes
    get_vec_length: Get distance between two nodes
    axis_vector_colinearity: Find which local axis aligns with vector
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import math

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
    logger.debug(f'Joint chain direction: {result}')
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
        logger.error('Failed to compute axis vector colinearity.')
