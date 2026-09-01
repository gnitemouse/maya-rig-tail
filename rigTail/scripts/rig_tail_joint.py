"""
rig_tail_joint.py
author: Daisy Jane @gnitemouse

Joint traversal and joint-specific helpers for Rig Tail.

Walks read the scene and never write to it, except set_joint_attributes,
which stamps the joint_pos label. Joints come back as full DAG paths: a
scene may legitimately hold two chains with the same joint names (a
replacement tail built alongside the one it replaces, an imported second
skeleton), and a short name is only usable while it is unique. Paths also
let a caller tell those two chains apart, which is the point of allowing
both. A walk stops where the chain stops being this rig part's, so it
never crosses a branch point into another part.

Functions:
    get_joint_chain: Get joint chain from start to optional end
    get_joint_hierarchy: Get joints in DAG order
    get_joint_position_from_list: Get world positions from joint list
    is_equal_joint: Compare two joints by transform
    set_joint_attributes: Label joint positions with custom attribute
"""

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_math as rt_math
import rig_tail_maya as rt_maya
import rig_tail_naming as rt_naming

logger = logger_setup(__name__)


def get_joint_chain(start, end=None):
    """
    Get joint chain from start joint to optional end joint.

    Follows the first child at each level, stopping at an end joint or
    where the chain stops being this rig part's (see _same_rigpart).

    Arguments:
        start (str): Starting joint name or DAG path
        end (str): Optional ending joint (name or path; stops when reached)

    Return:
        list: List of joint DAG paths in chain order, empty when start does
        not resolve to exactly one node
    """
    start = rt_maya.unique_path(start)
    if not start:
        return []
    end = rt_maya.unique_path(end) if end else None

    chain = [start]
    j = start
    while True:
        children = cmds.listRelatives(j, typ='joint', c=True,
                                      fullPath=True) or []
        if not children:
            break
        j = children[0]
        # The marker is the joint's OWN name: testing the path would make
        # every descendant of an '_ee_' joint read as an end joint too.
        if rt_maya.is_end_joint(j):
            return chain
        # A branch point hands its children to whichever rig part names
        # them, and children[0] is an arbitrary pick between them. Walking
        # into one regardless is how 'BN_L_leg_jnt' - a pivot with a rear
        # wing and a rear eye hanging off it - would report the wing's
        # joints as part of the leg, and mirror a leg chain with a stray
        # wing joint welded to its end.
        if not _same_rigpart(chain[-1], j):
            return chain
        chain.append(j)
        if j == end:
            return chain
    return chain


def _same_rigpart(parent, child):
    '''
    Whether a child joint belongs to the same rig part as its parent.

    An unreadable name on either side answers True, so a hand-named or
    half-named chain still walks end to end - only two joints that BOTH
    parse, to different rig parts, part company.

    Arguments:
        parent (str): the joint already in the chain
        child (str): the candidate next joint

    Return:
        bool: True to keep walking, False to end the chain at parent
    '''
    here = rt_naming.get_rigname(parent, rt_constants.JOINT)
    below = rt_naming.get_rigname(child, rt_constants.JOINT)
    return not (here and below and here != below)


def get_joint_hierarchy(start_jnt, end_jnt=None):
    """
    Return joints in deterministic DAG order (parent before child).

    Arguments:
        start_jnt (str): Starting joint name or DAG path
        end_jnt (str): Optional ending joint (name or path)

    Return:
        list: List of joint DAG paths in DAG order
    """
    start_jnt = rt_maya.unique_path(start_jnt)
    if not start_jnt:
        return []
    end_jnt = rt_maya.unique_path(end_jnt) if end_jnt else None

    dag = cmds.ls(start_jnt, dag=True, type='joint', long=True)
    if not dag:
        return []

    joints = []
    for j in dag:
        if rt_maya.is_end_joint(j):
            break
        joints.append(j)
        if end_jnt and j == end_jnt:
            break
    return joints


def get_joint_position_from_list(joints):
    """
    Get world positions from list of joints.

    Arguments:
        joints (list): List of joint names

    Return:
        list: List of [x, y, z] world positions
    """
    jnt_pos = []
    for jnt in joints:
        jnt_pos.append(rt_math.get_world_pos(jnt))
    return jnt_pos


def is_equal_joint(joint1, joint2, tolerance=0.1):
    """
    Check if two joints are equal in translation and rotation.

    Arguments:
        joint1 (str): First joint name
        joint2 (str): Second joint name
        tolerance (float): Maximum allowed difference

    Return:
        bool: True if joints are equal within tolerance
    """
    if not cmds.objExists(joint1):
        logger.error(f"joint '{joint1}' does not exist")
        return False
    if not cmds.objExists(joint2):
        logger.error(f"joint '{joint2}' does not exist")
        return False

    # Get translation
    jnt1_tr = cmds.xform(joint1, q=1, ws=1, t=1)
    jnt2_tr = cmds.xform(joint2, q=1, ws=1, t=1)
    # Get rotation
    jnt1_ro = cmds.xform(joint1, q=1, ws=1, ro=1)
    jnt2_ro = cmds.xform(joint2, q=1, ws=1, ro=1)
    logger.trace(f"{joint1} tr{jnt1_tr} ro{jnt1_ro}")
    logger.trace(f"{joint2} tr{jnt2_tr} ro{jnt2_ro}")

    if not all(abs(t1 - t2) < tolerance for t1, t2 in zip(jnt1_tr, jnt2_tr)):
        return False
    if not all(abs(r1 - r2) < tolerance for r1, r2 in zip(jnt1_ro, jnt2_ro)):
        return False
    return True


def set_joint_attributes(joints):
    """
    Label each joint with a locked 'joint_pos' attribute running 0 at the
    base to 1 at the tip.

    The value is the joint's normalised Greville abscissa on the FK curve,
    not a distance. The FK curve carries one CV per joint, so joint j's
    Greville fraction is the parameter fraction that lands on joint j, and
    the variable-FK controls are placed by feeding this same number to a
    pointOnCurveInfo (rig_tail_fk.set_curveinfo_fk). Control and joints
    therefore share one metric. The degree is clamped as
    rig_tail_curve.create_curve clamps it, so a short chain cannot make the
    two disagree.

    Two consequences:
    - joint_pos is a fraction of JOINT COUNT, not of tail length, which is
      what makes falloff_rotation's num_joints setRange self-consistent.
    - the animator-facing `position` dial stays in tail-length units; a
      remapValue converts it into this space. See set_curveinfo_fk.

    Arguments:
        joints (list): List of joint names
    """
    logger.trace('Label joint positions')
    if not joints:
        logger.error('No joints provided')
        return

    fractions = rt_math.greville_fractions(len(joints), min(3, len(joints)-1))
    for jnt, v in zip(joints, fractions):
        logger.trace(f"joint '{jnt}' joint_pos {v}")

        if cmds.attributeQuery('joint_pos', n=jnt, ex=1):
            cmds.setAttr(f"{jnt}.joint_pos", l=0)  # Unlock
            cmds.addAttr(f"{jnt}.joint_pos", e=1, dv=v)
            cmds.setAttr(f"{jnt}.joint_pos", cb=1, l=1)  # Channel box, lock
        else:
            cmds.addAttr(jnt, ln='joint_pos', nn='Joint Pos', at='float',
                         min=0, max=1, h=False, dv=v)
            cmds.setAttr(f"{jnt}.joint_pos", cb=1, l=1)
