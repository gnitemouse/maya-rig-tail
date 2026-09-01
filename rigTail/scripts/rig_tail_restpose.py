'''
rig_tail_restpose.py
author: Daisy Jane @gnitemouse

The REST ANCHOR: the canonical rest pose the IK curve is built from.

Owns the record of rest, not the writing of it. Each BN joint's rest world
matrix is captured once, while BN is still at true rest, stored on the
joint and read back on every rebuild. Restoring BN and stamping FK belong
elsewhere; rig_tail_cleanup.capture_bn_poses is the record's second
reader, which is what Remove Rig and every rebuild restore BN to.

Not a cache. A cache may be discarded and recomputed at will, and
recomputing this is the failure mode it exists to prevent. rig_tail_ik
builds the IK driver curve from joint world positions and the spline
solves the joints back onto that curve, so a second capture would record
the previous build's smoothed output and a curved chain would flatten
further on every rebuild. Building from the stored rest makes rebuilds
reproduce one setup rather than compound.

The anchor makes that smoothing reproducible; it does not remove it.
rig_tail_curve.connect_driver_to_solver_curve is what makes the rest shape
exact, by driving the solver curve as an offset from rest. Even so the
loop is only slowed, not closed: a curve whose CVs sit at the joints does
not pass through them, so the spline settles them slightly off what it was
built from, and reading that back would compound without bound toward a
straight line.

The stored positions also define what rest MEANS, since the correction is
measured against them. Clearing the store is therefore heavier than it
looks: the fallback is the CURRENT pose, so clearing while a rig is posed
and then rebuilding anchors the setup to that pose. Callers scope a clear
to what they moved themselves, never the whole store.

Swap point: build_rig_tail calls capture_rest_pose once at build start,
and rig_tail_ik calls curve_source_positions to decide what the IK curve
is built from. Change the strategy by changing these two functions;
curve_source_positions always falls back to live positions, so a no-op
version builds from the live chain.

Functions:
    capture_rest_pose: store each BN joint's rest world matrix once
    curve_source_positions: rest positions for the IK curve, else live
    rest_positions: stored rest positions aligned to a target chain
    clear_rest_pose: remove the stored rest pose (for re-capture/testing)
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_joint as rt_joint

logger = logger_setup(__name__)

# Locked world-matrix attribute holding a BN joint's captured rest pose. A
# full matrix (not just the position the anchor uses) is stored so a future
# method can read the rest orientation too without a re-capture. Capturing
# again after the first build would store the already-smoothed pose and
# defeat the whole fix.
REST_ATTR = 'restMatrix'


def capture_rest_pose(rignames=None):
    '''
    Store each BN joint's rest world matrix, once.

    Call while BN is at true rest, at the start of the build, before the IK
    curve and spline smooth anything. Guarded per joint on the attribute's
    absence, so a joint that already carries one is left alone rather than
    re-captured from a smoothed BN.

    Arguments
        rignames (list): Parts to capture, or None for all of
            rt_constants.RIGPARTS.
    '''
    for rigname in (rignames if rignames is not None else rt_constants.RIGPARTS):
        for jnt in rt_constants.JOINTS_BN.get(rigname, []):
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
    bn = rt_constants.JOINTS_BN.get(rigname, [])
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
    Positions the IK curve should be built from (the anchor's swap point).

    Returns the stored rest pose when one is complete, so every rebuild
    traces the same source. Falls back to the joints' live world positions
    when nothing is captured yet or the store is incomplete.

    Arguments
        rigname (str): Rig part name.
        joints (list): IK joint chain the curve is built for.

    Return
        list: [(x, y, z), ...] positions to feed create_curve.
    '''
    rest = rest_positions(rigname, joints)
    if rest is not None:
        return rest
    return rt_joint.get_joint_position_from_list(joints)


def clear_rest_pose(rignames=None):
    '''
    Remove the stored rest pose from a part's BN joints.

    For re-capturing on a corrected/clean scene, or resetting during testing.
    The next build recaptures from whatever pose BN is in, so only clear when
    BN is (or will be rebuilt to) the pose you want recorded.

    Arguments
        rignames (list): Parts to clear, or None for all of rt_constants.RIGPARTS.
    '''
    for rigname in (rignames if rignames is not None else rt_constants.RIGPARTS):
        for jnt in rt_constants.JOINTS_BN.get(rigname, []):
            if cmds.objExists(jnt) and cmds.attributeQuery(REST_ATTR, node=jnt, exists=True):
                cmds.setAttr(f'{jnt}.{REST_ATTR}', lock=False)
                cmds.deleteAttr(f'{jnt}.{REST_ATTR}')
