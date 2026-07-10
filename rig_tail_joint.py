"""
rig_tail_joint.py
author: Daisy Jane @gnitemouse

Joint traversal and joint-specific helpers for Rig Tail.
Functions for navigating joint hierarchies, comparing joints, and setting attributes.

Functions:
    get_joint_chain: Get joint chain from start to optional end
    get_joint_hierarchy: Get joints in DAG order
    get_joint_position_from_list: Get world positions from joint list
    is_equal_joint: Compare two joints by transform
    set_joint_attributes: Label joint positions with custom attribute
"""

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_math as rt_math

logger = logger_setup(__name__)


def get_joint_chain(start, end=None):
    """
    Get joint chain from start joint to optional end joint.
    Follows first child at each level.

    Arguments:
        start (str): Starting joint name
        end (str): Optional ending joint name (stops when reached)

    Return:
        list: List of joint names in chain order
    """
    chain = [start]
    j = start
    while True:
        children = cmds.listRelatives(j, typ='joint', c=True) or []
        if not children:
            break
        j = children[0]
        if '_ee_' in j:
            return chain
        chain.append(j)
        if j == end:
            return chain
    return chain


def get_joint_hierarchy(start_jnt, end_jnt=None):
    """
    Return joints in deterministic DAG order (parent before child).

    Arguments:
        start_jnt (str): Starting joint name
        end_jnt (str): Optional ending joint name

    Return:
        list: List of joint names in DAG order
    """
    dag = cmds.ls(start_jnt, dag=True, type='joint')
    if not dag:
        return []

    joints = []
    for j in dag:
        if '_ee_' in j:
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
    logger.debug(f"{joint1} tr{jnt1_tr} ro{jnt1_ro}")
    logger.debug(f"{joint2} tr{jnt2_tr} ro{jnt2_ro}")

    if not all(abs(t1 - t2) < tolerance for t1, t2 in zip(jnt1_tr, jnt2_tr)):
        return False
    if not all(abs(r1 - r2) < tolerance for r1, r2 in zip(jnt1_ro, jnt2_ro)):
        return False
    return True


def set_joint_attributes(joints):
    """
    Label joint positions with custom 'joint_pos' attribute.
    Adds attribute to each joint with V value (0 to 1) along chain.

    Arguments:
        joints (list): List of joint names
    """
    logger.debug('Label joint positions')
    if not joints:
        logger.error('No joints provided')
        return

    end = joints[-1]
    fullv = rt_math.get_vec_length(joints[0], end)
    for jnt in joints:
        length = rt_math.get_vec_length(jnt, end)
        logger.debug(f"joint '{jnt}' length {length} fullv {fullv}")
        if length == 0:
            v = 0
        elif fullv == 0:
            logger.error(f"joint '{jnt}' - length {length} fullv {fullv}")
            v = 0
        else:
            v = length / fullv

        if cmds.attributeQuery('joint_pos', n=jnt, ex=1):
            cmds.setAttr(f"{jnt}.joint_pos", l=0)  # Unlock
            cmds.addAttr(f"{jnt}.joint_pos", e=1, dv=v)
            cmds.setAttr(f"{jnt}.joint_pos", cb=1, l=1)  # Channel box, lock
        else:
            cmds.addAttr(jnt, ln='joint_pos', nn='Joint Pos', at='float',
                         min=0, max=1, h=False, dv=v)
            cmds.setAttr(f"{jnt}.joint_pos", cb=1, l=1)
