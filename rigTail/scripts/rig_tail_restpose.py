'''
# rig_tail_restpose.py
author: Daisy Jane @gnitemouse

Rest-pose store for the IK rebuild-degradation fix -- METHOD D (curve-input fix).

THE PROBLEM
    rig_tail_ik builds the IK driver curve from the joints' *current* world
    positions, and the spline then solves the joints back onto that low-CV
    curve. So every rebuild traces the previous rebuild's smoothed output, and
    a curved chain flattens a little more each time (measured on the squid
    fintails: total bend 22.5 -> 14.9 -> 9.8 ... deg over successive rebuilds).

METHOD D (this module)
    Break the loop on its INPUT. Capture each BN joint's rest world matrix
    ONCE -- on the first build, while BN is still at true rest -- store it
    durably on the joint, and build the IK curve from that stored rest on
    every rebuild. The curve still smooths the rest shape, but by the SAME
    amount every time, so rebuilds reproduce the same setup instead of
    compounding. Consistency, not a perfect curve.

SCOPE (deliberately minimal)
    Fixes only the curve input. It does NOT restore BN and does NOT stamp FK.
    So FK re-duplicated from an already-smoothed BN (e.g. a fresh session that
    re-duplicates the chains) settles to the smoothed value rather than the
    true rest. That is an accepted trade-off for keeping this small.

SWAP POINT (how to replace this later)
    The build touches this module in exactly two places:
      1. build_rig_tail() calls capture_rest_pose() once, at build start.
      2. rig_tail_ik() calls curve_source_positions() to decide what the IK
         curve is built from.
    To change the degradation strategy (Method A / an FK-stamp / a higher-
    fidelity interpolating curve), change the functions here; the two call
    sites in rig_tail.py do not need to move. curve_source_positions() always
    falls back to live positions, so a no-op version of this module reproduces
    the original pre-fix behaviour exactly.
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_joint as rt_jnt

logger = logger_setup(__name__)

# Locked world-matrix attribute holding a BN joint's captured rest pose. A
# full matrix (not just the position Method D uses) is stored so a future
# method can read the rest orientation too without a re-capture -- capturing
# again after the first build would store the already-smoothed pose and defeat
# the whole fix.
REST_ATTR = 'restMatrix'


def capture_rest_pose(rignames=None):
    '''
    Capture each BN joint's rest world matrix ONCE.

    Call while BN is at true rest -- at the start of the build, before the IK
    curve/spline smooths anything. Guarded per joint on the attribute's
    absence, so it records the rest pose on the first build only and never
    re-captures from a later, smoothed BN.

    Arguments
        rignames (list): Parts to capture, or None for all of rt_cst.RIGPARTS.
    '''
    for rigname in (rignames if rignames is not None else rt_cst.RIGPARTS):
        for jnt in rt_cst.JOINTS_BN.get(rigname, []):
            if not cmds.objExists(jnt):
                continue
            if cmds.attributeQuery(REST_ATTR, node=jnt, exists=True):
                continue  # already captured: never overwrite (would store the smoothed pose)
            try:
                mtx = cmds.xform(jnt, q=True, ws=True, m=True)
                cmds.addAttr(jnt, ln=REST_ATTR, dt='matrix')
                cmds.setAttr(f'{jnt}.{REST_ATTR}', mtx, type='matrix')
                cmds.setAttr(f'{jnt}.{REST_ATTR}', lock=True)
                logger.trace(f'Captured rest pose on {jnt}')
            except RuntimeError as e:
                logger.warning(f'Could not capture rest pose on {jnt}: {e}')


def rest_positions(rigname, joints):
    '''
    Stored rest world positions for `rigname`, aligned one-to-one with `joints`.

    Rest is captured on the BN chain; FK/IK are duplicated from BN 1:1, so the
    BN rest positions line up with whichever chain is passed in. Returns None
    when no complete rest pose is stored (any joint missing its attribute, or a
    length mismatch), so callers fall back to live positions.

    Arguments
        rigname (str): Rig part name
        joints (list): Target chain the positions must align with

    Return
        list or None: [(x, y, z), ...] rest positions, or None
    '''
    bn = rt_cst.JOINTS_BN.get(rigname, [])
    if len(bn) != len(joints):
        return None
    out = []
    for jnt in bn:
        if not cmds.objExists(jnt) or not cmds.attributeQuery(REST_ATTR, node=jnt, exists=True):
            return None
        m = cmds.getAttr(f'{jnt}.{REST_ATTR}')
        out.append((m[12], m[13], m[14]))
    return out


def curve_source_positions(rigname, joints):
    '''
    The positions the IK curve should be built from -- Method D's swap point.

    Returns the stored rest pose when available, so every rebuild traces the
    same source and cannot compound; falls back to the joints' live world
    positions otherwise (nothing captured yet, or an incomplete store), which
    reproduces the original pre-fix behaviour.

    Arguments
        rigname (str): Rig part name
        joints (list): IK joint chain the curve is built for

    Return
        list: [(x, y, z), ...] positions to feed create_curve
    '''
    rest = rest_positions(rigname, joints)
    if rest is not None:
        return rest
    return rt_jnt.get_joint_position_from_list(joints)


def clear_rest_pose(rignames=None):
    '''
    Remove the stored rest pose from a part's BN joints.

    For re-capturing on a corrected/clean scene, or resetting during testing.
    The next build recaptures from whatever pose BN is in, so only clear when
    BN is (or will be rebuilt to) the pose you want recorded.

    Arguments
        rignames (list): Parts to clear, or None for all of rt_cst.RIGPARTS.
    '''
    for rigname in (rignames if rignames is not None else rt_cst.RIGPARTS):
        for jnt in rt_cst.JOINTS_BN.get(rigname, []):
            if cmds.objExists(jnt) and cmds.attributeQuery(REST_ATTR, node=jnt, exists=True):
                cmds.setAttr(f'{jnt}.{REST_ATTR}', lock=False)
                cmds.deleteAttr(f'{jnt}.{REST_ATTR}')
