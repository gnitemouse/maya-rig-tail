'''
rig_tail_restpose.py
author: Daisy Jane @gnitemouse

Rest-pose store for the IK rebuild-degradation fix (Method D, the
curve-input fix).

The problem: rig_tail_ik builds the IK driver curve from the joints'
current world positions, and the spline solves the joints back onto that
low-CV curve. Every rebuild then traces the previous rebuild's smoothed
output, so a curved chain flattens a little more each time (measured on
the squid fintails: total bend 22.5, then 14.9, then 9.8 deg over
successive rebuilds).

Method D breaks the loop on its input. It captures each BN joint's rest
world matrix once, on the first build while BN is still at true rest,
stores it on the joint, and builds the IK curve from that stored rest on
every rebuild. The curve still smooths the shape, but by the same amount
each time, so rebuilds reproduce the same setup instead of compounding.

Scope is deliberately minimal: it fixes only the curve input. It does not
restore BN and does not stamp FK, so FK re-duplicated from an
already-smoothed BN settles to the smoothed value. An accepted trade-off
for keeping this small.

Swap point: the build touches this module in two places. build_rig_tail
calls capture_rest_pose once at build start, and rig_tail_ik calls
curve_source_positions to decide what the IK curve is built from. To
change the strategy, change the functions here; the call sites do not
move, and curve_source_positions always falls back to live positions so a
no-op version reproduces the original behavior.

Functions:
    capture_rest_pose: store each BN joint's rest world matrix once
    curve_source_positions: rest positions for the IK curve, else live
    rest_positions: stored rest positions aligned to a target chain
    clear_rest_pose: remove the stored rest pose (for re-capture/testing)
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_joint as rt_jnt

logger = logger_setup(__name__)

# Locked world-matrix attribute holding a BN joint's captured rest pose. A
# full matrix (not just the position Method D uses) is stored so a future
# method can read the rest orientation too without a re-capture. Capturing
# again after the first build would store the already-smoothed pose and
# defeat the whole fix.
REST_ATTR = 'restMatrix'


def capture_rest_pose(rignames=None):
    '''
    Store each BN joint's rest world matrix, once.

    Call while BN is at true rest, at the start of the build, before the IK
    curve and spline smooth anything. Guarded per joint on the attribute's
    absence, so it records the rest pose on the first build only and never
    re-captures from a later, smoothed BN.

    Arguments
        rignames (list): Parts to capture, or None for all of rt_cst.RIGPARTS.

    Return
        None.
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
    Stored rest world positions for a rig part, aligned to a target chain.

    Rest is captured on the BN chain, and FK/IK are duplicated from BN one
    to one, so the BN rest positions line up with whichever chain is passed
    in. Returns None when no complete rest pose is stored (a joint missing
    its attribute, or a length mismatch), so callers fall back to live
    positions.

    Arguments
        rigname (str): Rig part name.
        joints (list): Target chain the positions must align with.

    Return
        list or None: [(x, y, z), ...] rest positions, or None.
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
    Positions the IK curve should be built from (Method D's swap point).

    Returns the stored rest pose when available, so every rebuild traces the
    same source and cannot compound. Falls back to the joints' live world
    positions otherwise (nothing captured yet, or an incomplete store),
    which reproduces the original behavior.

    Arguments
        rigname (str): Rig part name.
        joints (list): IK joint chain the curve is built for.

    Return
        list: [(x, y, z), ...] positions to feed create_curve.
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
