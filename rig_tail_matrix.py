'''
rig_tail_matrix.py
author: Daisy Jane @gnitemouse

Pure matrix network builders for Rig Tail.
Drives offsetParentMatrix directly with baseLocal * FX chain.

Functions:
    build_matrix_offset_network: Build dynamic OPM network for BN chain
    create_matrix_nodes_for_joint: Create matrix node network for single joint
    _get_driver_joint: Get appropriate driver joint for index
    _get_compose_output_attr: Detect composeMatrix output attribute (Maya version)
    _get_parent_squash_inverse: Matrix plug cancelling parent BN squash scale
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_name

logger = logger_setup(__name__)


def build_matrix_offset_network(rigname, fk, ik):
    '''
    Build pure matrix offsetParentMatrix network for BN chain.

    Architecture:
        baseLocal = bn_parent.worldInverseMatrix * driver.worldMatrix
        finalLocal = baseLocal * fxCurl * fxWave * fxNoise
        finalLocal → bn.offsetParentMatrix

    No decomposition, no Euler conversions, exact matrix math.
    Driver's jointOrient is already in worldMatrix, no cancellation needed.

    Arguments:
        rigname (str): Name of rig part
        fk (bool): If True and ik is False, use FK joints as drivers
        ik (bool): If True, use IK joints as drivers (preferred when both True)
    '''

    logger.info(f'{rigname}: Building pure matrix OPM network')

    if rigname not in rt_cst.JOINTS_BN:
        logger.warning(f'{rigname}: No BN joints found')
        return

    joints = rt_cst.JOINTS_BN[rigname]
    cog_ctrl = rt_name.fstr('', rt_cst.COG_CTRL)
    ikfk_attr = rt_name.fstr(rigname, rt_cst.IKFK)

    # Build FX list from constants
    fx_list = []
    if rt_cst.EFFECTS.get('curl'):
        fx_list.append('curl')
    if rt_cst.EFFECTS.get('wave'):
        fx_list.append('wave')
    if rt_cst.EFFECTS.get('noise'):
        fx_list.append('noise')

    logger.info(f'{rigname}: Zeroing OPM and local TRS on {len(joints)} BN joints')

    # Zero everything before building network (no bind-time baking)
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    for bn_jnt in joints:
        cmds.setAttr(f'{bn_jnt}.offsetParentMatrix', *identity, type='matrix')
        cmds.setAttr(f'{bn_jnt}.translate', 0, 0, 0)
        cmds.setAttr(f'{bn_jnt}.rotate', 0, 0, 0)
        cmds.setAttr(f'{bn_jnt}.scale', 1, 1, 1)
        if cmds.attributeQuery('jointOrient', node=bn_jnt, exists=True):
            cmds.setAttr(f'{bn_jnt}.jointOrient', 0, 0, 0)

    logger.info(f'{rigname}: Building runtime matrix networks')

    for i, bn_jnt in enumerate(joints):
        driver_jnt = _get_driver_joint(rigname, i, fk, ik)
        if driver_jnt:
            create_matrix_nodes_for_joint(
                rigname, bn_jnt, driver_jnt, i, cog_ctrl, ikfk_attr, fx_list,
                fk, ik
            )
        else:
            logger.warning(f'{rigname}: No driver for {bn_jnt} at index {i}')

    logger.info(
        f'{rigname}: Matrix OPM network complete '
        f'({len(joints)} joints, {len(fx_list)} FX layers)'
    )


def _get_driver_joint(rigname, index, fk, ik):
    '''
    Get appropriate driver joint for a given index.

    Arguments:
        rigname (str): Rig component name
        index (int): Joint index
        fk (bool): Use FK joints
        ik (bool): Use IK joints (takes priority)

    Return:
        str or None: Driver joint name
    '''
    if ik and rigname in rt_cst.JOINTS_IK:
        return rt_cst.JOINTS_IK[rigname][index]
    elif fk and rigname in rt_cst.JOINTS_FK:
        return rt_cst.JOINTS_FK[rigname][index]
    return None


def _get_compose_output_attr(node):
    '''
    Detect composeMatrix output attribute name (Maya version-dependent).
    Maya 2020+: .outputMatrix
    Maya 2019-: .output

    Arguments:
        node (str): composeMatrix node name

    Return:
        str: Full attribute path (e.g. 'node.outputMatrix' or 'node.output')
    '''
    if cmds.attributeQuery('outputMatrix', node=node, exists=True):
        return f'{node}.outputMatrix'
    elif cmds.attributeQuery('output', node=node, exists=True):
        return f'{node}.output'
    else:
        logger.warning(
            f'Unknown composeMatrix output attr on {node}, '
            f'defaulting to .outputMatrix'
        )
        return f'{node}.outputMatrix'


def _get_parent_squash_inverse(rigname, parent_index):
    '''
    Get matrix plug that cancels the parent BN joint's squash scale.

    The squash network (rig_tail_stretch) drives BN scaleY/Z through
    {rigname}_squash_NN_multiplyDivide. Children are positioned by
    offsetParentMatrix, which is multiplied by the parent's world matrix,
    so the parent's squash scale would shear every child position.
    Appending inverse(parentSquash) at the end of the child's OPM chain
    cancels it exactly (diagonal scale matrices multiply elementwise).

    Arguments:
        rigname (str): Rig component name
        parent_index (int): Index (NN) of the parent BN joint

    Return:
        str or None: composeMatrix output plug, or None when no squash
            network exists (stretchy disabled)
    '''
    parent_mult = f'{rigname}_squash_{parent_index:02d}_multiplyDivide'
    if not cmds.objExists(parent_mult):
        return None

    inv = f'{rigname}_{parent_index:02d}_squashInv_multiplyDivide'
    if not cmds.objExists(inv):
        cmds.createNode('multiplyDivide', n=inv, s=1, ss=1)
        cmds.setAttr(f'{inv}.operation', 2)  # divide
        cmds.setAttr(f'{inv}.input1', 1, 1, 1, type='double3')
        cmds.setAttr(f'{inv}.input2', 1, 1, 1, type='double3')
        cmds.connectAttr(f'{parent_mult}.outputY', f'{inv}.input2Y', f=1)
        cmds.connectAttr(f'{parent_mult}.outputZ', f'{inv}.input2Z', f=1)

    compose = f'{rigname}_{parent_index:02d}_squashInv_composeMatrix'
    if not cmds.objExists(compose):
        cmds.createNode('composeMatrix', n=compose, s=1, ss=1)
        cmds.connectAttr(f'{inv}.outputY', f'{compose}.inputScaleY', f=1)
        cmds.connectAttr(f'{inv}.outputZ', f'{compose}.inputScaleZ', f=1)
    return _get_compose_output_attr(compose)


def create_matrix_nodes_for_joint(
    rigname, bn_jnt, driver_jnt, index, cog_ctrl, ikfk_attr, fx_list,
    fk=True, ik=True
):
    '''
    Create pure matrix network for a single BN joint.

    Network structure:
        1. IK/FK blend: fk_driver.world + ik_driver.world → blendMatrix
           (only when both chains are built; single-chain builds drive
           the blendMatrix input directly, no switch attribute needed)
        2. baseLocal: bn_parent.worldInverseMatrix * blended_driver
        3. FX layers: composeMatrix nodes (rotation-only, T=0, S=1)
        4. squashInv: cancels the parent BN joint's squash scale so the
           OPM translation is not sheared by volume preservation
        5. Final: fxCurl * fxWave * ... * baseLocal * squashInv
           → bn.offsetParentMatrix

    Arguments:
        rigname (str): Rig component name
        bn_jnt (str): BN joint name
        driver_jnt (str): Driver joint name (IK or FK based on priority)
        index (int): Joint index
        cog_ctrl (str): COG control name
        ikfk_attr (str): IK/FK switch attribute name
        fx_list (list): List of FX effect names (e.g. ['curl', 'wave', 'noise'])
        fk (bool): FK components are being built
        ik (bool): IK components are being built
    '''
    NN = rt_name.get_index_from_name(bn_jnt)

    # Create BlendMatrix for IK/FK switching
    blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
    if not cmds.objExists(blend_mtx):
        cmds.createNode('blendMatrix', n=blend_mtx, s=1, ss=1)

    # Connect IK or FK driver worldMatrix (based on _get_driver_joint logic)
    cmds.connectAttr(f'{driver_jnt}.worldMatrix[0]', f'{blend_mtx}.inputMatrix', f=1)

    # If current driver is IK, get FK for blend target; if FK, get IK
    fk_driver = _get_driver_joint(rigname, index, fk=True, ik=False)
    ik_driver = _get_driver_joint(rigname, index, fk=False, ik=True)

    # IK/FK blending only applies when both chains are built: the switch
    # attribute on the cog only exists when IK is built, and single-chain
    # builds have nothing to blend to (the other duplicate chain is unrigged)
    fk_mode = rt_cst.ikfk_mode_index('FK')
    blending = (fk and ik and fk_mode is not None
                and fk_driver and ik_driver and fk_driver != ik_driver)
    ikfk_remap = f'{rigname}_{NN:02d}_ikfk_remap_condition'

    if blending:
        # Condition remaps the switch enum to FK weight (1 in FK mode)
        if not cmds.objExists(ikfk_remap):
            cmds.createNode('condition', n=ikfk_remap, s=1, ss=1)
            cmds.setAttr(f'{ikfk_remap}.operation', 2)
            cmds.setAttr(f'{ikfk_remap}.colorIfTrueR', 1)
            cmds.setAttr(f'{ikfk_remap}.colorIfFalseR', 0)
            cmds.connectAttr(f'{cog_ctrl}.{ikfk_attr}', f'{ikfk_remap}.firstTerm', f=1)
        # FK is not necessarily the last mode; re-derive threshold each build
        cmds.setAttr(f'{ikfk_remap}.secondTerm', fk_mode - 0.5)

        # Connect alternate driver to target[0] for blending
        alt_driver = fk_driver if driver_jnt == ik_driver else ik_driver
        cmds.connectAttr(f'{alt_driver}.worldMatrix[0]', f'{blend_mtx}.target[0].targetMatrix', f=1)
        # Connect blend weight from remap condition
        cmds.connectAttr(f'{ikfk_remap}.outColorR', f'{blend_mtx}.target[0].weight', f=1)
    else:
        # Re-run safety: break stale blend target left by a previous
        # both-chains build so the unrigged chain cannot bleed in
        for attr in ('target[0].targetMatrix', 'target[0].weight'):
            plug = f'{blend_mtx}.{attr}'
            for src in cmds.listConnections(plug, s=True, d=False, p=True) or []:
                cmds.disconnectAttr(src, plug)

    # Find BN parent
    bn_parent = cmds.listRelatives(bn_jnt, p=True, f=True)
    bn_parent = bn_parent[0] if bn_parent else None

    # baseLocal = bn_parent.worldInverseMatrix * driver.worldMatrix
    # Driver's worldMatrix already includes its jointOrient, no cancellation needed
    baselocal_mult = f'{rigname}_{NN:02d}_baseLocal_multMatrix'
    if not cmds.objExists(baselocal_mult):
        cmds.createNode('multMatrix', n=baselocal_mult, s=1, ss=1)

    if index == 0:
        # Root: parent is skeleton. OPM = fx * driver.world * skeleton.worldInverse
        cmds.connectAttr(f'{blend_mtx}.outputMatrix', f'{baselocal_mult}.matrixIn[0]', f=1)
        if bn_parent:
            cmds.connectAttr(f'{bn_parent}.worldInverseMatrix[0]', f'{baselocal_mult}.matrixIn[1]', f=1)
    else:
        cmds.connectAttr(f'{blend_mtx}.outputMatrix', f'{baselocal_mult}.matrixIn[0]', f=1) # driver-child world
        # parent term is driver parent inverse, not bn_parent, so curl propagates
        parent_NN = rt_name.get_index_from_name(bn_parent)
        parent_blend = f'{rigname}_{parent_NN:02d}_ikfk_blendMatrix'
        parent_inv = f'{rigname}_{NN:02d}_driverParentInv_inverseMatrix'
        if not cmds.objExists(parent_inv):
            cmds.createNode('inverseMatrix', n=parent_inv, s=1, ss=1)
            cmds.connectAttr(f'{parent_blend}.outputMatrix', f'{parent_inv}.inputMatrix', f=1)
        cmds.connectAttr(f'{parent_inv}.outputMatrix', f'{baselocal_mult}.matrixIn[1]', f=1)

    # Assemble the OPM chain: FX layers go before baseLocal so each joint
    # rotates about its own pivot, squash compensation goes last so it
    # meets the parent's world matrix first.
    # OPM = fxCurl * fxWave * fxNoise * baseLocal * squashInvParent
    matrix_plugs = []
    for fx_name in fx_list:
        fx_compose = f'{rigname}_{NN:02d}_{fx_name}_composeMatrix'
        if not cmds.objExists(fx_compose):
            cmds.createNode('composeMatrix', n=fx_compose, s=1, ss=1)
            cmds.setAttr(f'{fx_compose}.inputTranslate', 0, 0, 0, type='double3')
            cmds.setAttr(f'{fx_compose}.inputScale', 1, 1, 1, type='double3')
        matrix_plugs.append(_get_compose_output_attr(fx_compose))

    matrix_plugs.append(f'{baselocal_mult}.matrixSum')

    # Volume-preserving squash scales the parent BN joint's Y/Z; the OPM
    # translation lives in parent space, so without compensation the
    # parent scale shears every child position and the error compounds
    # down the chain (tail end drifts off the end control on bent poses).
    if index > 0 and rt_cst.EFFECTS.get('stretchy'):
        squash_inv = _get_parent_squash_inverse(rigname, parent_NN)
        if squash_inv:
            matrix_plugs.append(squash_inv)

    if len(matrix_plugs) == 1:
        # No FX layers and no squash: drive OPM from baseLocal directly
        cmds.connectAttr(matrix_plugs[0], f'{bn_jnt}.offsetParentMatrix', f=1)
    else:
        final_mult = f'{rigname}_{NN:02d}_final_multMatrix'
        if not cmds.objExists(final_mult):
            cmds.createNode('multMatrix', n=final_mult, s=1, ss=1)
        for idx, plug in enumerate(matrix_plugs):
            cmds.connectAttr(plug, f'{final_mult}.matrixIn[{idx}]', f=1)
        # Re-run safety: drop stale inputs left over when FX/squash
        # options shrink between builds
        for idx in cmds.getAttr(f'{final_mult}.matrixIn', mi=True) or []:
            if idx >= len(matrix_plugs):
                plug = f'{final_mult}.matrixIn[{idx}]'
                for src in cmds.listConnections(plug, s=True, d=False, p=True) or []:
                    cmds.disconnectAttr(src, plug)
                cmds.removeMultiInstance(plug, b=True)
        cmds.connectAttr(f'{final_mult}.matrixSum', f'{bn_jnt}.offsetParentMatrix', f=1)

    # Ensure local TRS stays at zero (critical for pure matrix approach)
    for attr in ('translateX', 'translateY', 'translateZ',
                 'rotateX', 'rotateY', 'rotateZ'):
        cmds.setAttr(f'{bn_jnt}.{attr}', 0, l=0)
    cmds.setAttr(f'{bn_jnt}.scale', 1, 1, 1, type='double3')
