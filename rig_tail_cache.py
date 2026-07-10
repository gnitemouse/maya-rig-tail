"""
rig_tail_cache.py
author: Daisy Jane @gnitemouse

Cache operations for Rig Tail.
Centralized cache management using rig_tail_constants for persistent state.

Functions:
    validate_cache: Clear cache if RIGPARTS or ROOT changed
    validate_cache_joints: Check if cached joints still exist
    cache_controls_ik: Cache IK controls for a rigname
    clear_control_cache: Clear all cached controls
    get_cached_controls_ik: Get cached IK controls
"""

import maya.cmds as cmds
from logger_config import logger_setup
from rig_tail_constants import RIGPARTS, ROOT
import rig_tail_constants as rt_cst

logger = logger_setup(__name__)

# Module-level control cache
_CONTROL_CACHE = {}


def validate_cache():
    """
    Clear cache if RIGPARTS or ROOT changed since last build.
    Compares current values against LAST_BUILD state.
    """
    # Check if RIGPARTS changed
    if set(RIGPARTS) != set(rt_cst.LAST_BUILD['rigparts']):
        removed = set(rt_cst.LAST_BUILD['rigparts']) - set(RIGPARTS)
        added = set(RIGPARTS) - set(rt_cst.LAST_BUILD['rigparts'])

        # Clear data for removed rigparts
        for rigname in removed:
            rt_cst.JOINTS_FK.pop(rigname, None)
            rt_cst.JOINTS_IK.pop(rigname, None)
            rt_cst.JOINTS_BN.pop(rigname, None)

        logger.info(f'RIGPARTS changed. Removed: {removed}, Added: {added}')
        rt_cst.LAST_BUILD['rigparts'] = RIGPARTS.copy()

    # Check if ROOT changed
    if ROOT != rt_cst.LAST_BUILD['root']:
        logger.info(f"ROOT changed: '{rt_cst.LAST_BUILD['root']}' -> '{ROOT}'")
        rt_cst.LAST_BUILD['root'] = ROOT


def validate_cache_joints(rigname):
    """
    Check if cached joints still exist and match scene.

    Arguments:
        rigname (str): Name of rig component

    Return:
        bool: True if joints changed (full rebuild needed)
    """
    if rigname not in rt_cst.JOINTS_BN:
        return True  # No cache, need rebuild

    # Check if cached joints exist in scene
    for joint_dict in [rt_cst.JOINTS_BN, rt_cst.JOINTS_FK, rt_cst.JOINTS_IK]:
        if rigname in joint_dict:
            for jnt in joint_dict[rigname]:
                if not cmds.objExists(jnt):
                    logger.warning(f"Cached joint '{jnt}' no longer exists")
                    return True  # Joints changed

    # Compute hash of current joint positions
    stored_hash = rt_cst.LAST_BUILD.get('joints_hash', {}).get(rigname)
    current_hash = hash(tuple(
        tuple(cmds.xform(j, q=1, ws=1, t=1))
        for j in rt_cst.JOINTS_BN[rigname]
    ))

    if current_hash != stored_hash:
        logger.info(f'{rigname}: Joint positions changed')
        rt_cst.LAST_BUILD.setdefault('joints_hash', {})[rigname] = current_hash
        return True

    return False  # Joints unchanged


def cache_controls_ik(rigname):
    """
    Cache IK controls for a rigname.
    Imports get_controls_ik dynamically to avoid circular imports.

    Arguments:
        rigname (str): Name of rig component

    Return:
        tuple: (ik_controls, ik_ctrlgrps) dicts
    """
    global _CONTROL_CACHE
    if rigname not in _CONTROL_CACHE:
        from rig_tail_control import get_controls_ik
        _CONTROL_CACHE[rigname] = get_controls_ik(rigname)
    return _CONTROL_CACHE[rigname]


def clear_control_cache():
    """
    Clear all cached controls.
    Call this before rig rebuild.
    """
    global _CONTROL_CACHE
    _CONTROL_CACHE.clear()


def get_cached_controls_ik(rigname):
    """
    Get cached IK controls.
    Wrapper for cache_controls_ik for clarity.

    Arguments:
        rigname (str): Name of rig component

    Return:
        tuple: (ik_controls, ik_ctrlgrps) dicts
    """
    return cache_controls_ik(rigname)
