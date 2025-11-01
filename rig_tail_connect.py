'''
# rig_tail_connect.py
author: Daisy Jane Lee @dayzl

Connections and Constraints for Rig Tail
'''

import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup
import rig_tail_constants as cst
from rig_tail_constants import *
from rig_tail_util import *
from rig_tail_control import get_controls_ik
from rig_tail_curve import get_spline_handle
import re

logger = logger_setup(__name__)


# CONNECTIONS ==========================================================

def connect_rig_tail(fk, ik, stretchy):
    '''
    Connect FK tail rig.
    Original IKFK Switch attributes are created on Cog control.
    Proxy IKFK Switch attributes are created on Base,IK,FK controls.
    RIGPARTS (list): List of all rig components

    Arguments
        fk (bool): connect FK components
        ik (bool): connect IK components
    '''
    logger.info('-----------------------------------------------------')
    logger.info('START connecting rig components..')
    connect_root(fk, ik) # Root
    connect_cog(fk, ik) # Cog
    for rigname in cst.RIGPARTS:
        connect_basectrl(rigname, fk, ik, stretchy) # Basectrl
        set_control_visibility(fk, ik)
        connect_fk(rigname, fk, ik, stretchy) # FK
        connect_ik(rigname, fk, ik, stretchy) # IK
        constrain_skeleton(rigname, fk, ik)
        bind_geometry(rigname)
    logger.info('DONE connecting rig components..')

def connect_root(fk, ik):
    '''
    Create attributes on root control.
    Warning: Uses hardcoded names, check naming.
    '''
    logger.info(f"Connect root")
    root_ctrl = fstr('', ROOT_CTRL)
    geometry_grp = fstr('', GEOMETRY_GRP)
    control_grp = fstr('', CONTROL_GRP)
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)
    skeleton_grp = fstr('', SKELETON_GRP)
    locators_grp = fstr('', LOCATORS_GRP)
    # Attribute Template: (longName, niceName, enumName, dv)
    if (fk and not ik) or (ik and not fk):
        rootctrl_attrs = [
            ('divider', 'visibilityDivider', 'VISIBILITY', 0),
            (geometry_grp, 'geo', 'Geometry', 1),
            (control_grp, 'controls', 'Controls', 1),
            (rig_systems_grp, 'rig_systems', 'Rig Systems', 1),
            (skeleton_grp, 'skeleton', 'Skeleton', 0),
            (locators_grp, 'locators', 'Locators', 0),
            ('divider', 'dispDivider', 'DISPLAY', 0)
        ]
    else:
        ik_skeleton_grp = fstr('', SKELETON_GRP, TYPE_IK)
        fk_skeleton_grp = fstr('', SKELETON_GRP, TYPE_FK)
        rootctrl_attrs = [
            ('divider', 'visibilityDivider', 'VISIBILITY', 0),
            (geometry_grp, 'geo', 'Geometry', 1),
            (control_grp, 'controls', 'Controls', 1),
            (rig_systems_grp, 'rig_systems', 'Rig Systems', 1),
            (skeleton_grp, 'skeleton', 'Skeleton', 0),
            (ik_skeleton_grp, 'ik_skeleton', 'IK Skeleton', 1),
            (fk_skeleton_grp, 'fk_skeleton', 'FK Skeleton', 1),
            (locators_grp, 'locators', 'Locators', 0),
            ('divider', 'dispDivider', 'DISPLAY', 0)
        ]

    # Add rootctrl attributes
    for group, ln_attr, nn_attr, dv in rootctrl_attrs:
        add_attribute_enum(root_ctrl, ln_attr, nn_attr, dv=dv)
        if group != 'divider':
            cmds.connectAttr(f"{root_ctrl}.{ln_attr}", f"{group}.visibility", f=1)
    # Add Export Geometry attribute
    add_attribute_enum(root_ctrl, ln='export_geo', nn='Export Geometry',
                       en='Unlocked:Wireframe:Locked', dv=0)
    cmds.setAttr(f"{geometry_grp}.overrideEnabled", 1)
    cmds.connectAttr(f"{root_ctrl}.export_geo",
                     f"{geometry_grp}.overrideDisplayType", f=1)
    cmds.setAttr(f"{root_ctrl}.export_geo", 2) # Locked geo

def connect_cog(fk, ik):
    '''
    Create attributes on cog control
    '''
    logger.info(f"Connect cog")
    cog_ctrl = fstr('', COG_CTRL)

    if ik:
        add_attribute_enum(cog_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        for rigname in cst.RIGPARTS:
            ln_ikfk = fstr(rigname, IKFK)
            nn_ikfk = re.sub(r'[-_\s]+', ' ', ln_ikfk).title()
            # IKFK Switch attribute
            add_attribute_enum(cog_ctrl, ln_ikfk, nn_ikfk, IKFK_SWITCH[2], IKFK_SWITCH[3])

def connect_basectrl(rigname, fk, ik, stretchy):
    '''
    Connect attributes on base control
    '''
    logger.info(f"Connect basectrl")
    cog_ctrl = fstr('', COG_CTRL)
    basectrl_grp = fstr(rigname, BASECTRL_GRP)
    basectrl = fstr(rigname, BASECTRL)

    # Move basectrl under cog_ctrl
    parent_to(basectrl_grp, cog_ctrl)

    basectrl = fstr(rigname, BASECTRL)
    if ik: # Add Twist, Offset, Roll attributes
        add_attribute_basectrl_ik(rigname, basectrl)
    if stretchy: # Add Scale attributes
        if fk:
            add_attribute_basectrl_scale(rigname, basectrl, TYPE_FK)
        if ik:
            add_attribute_basectrl_scale(rigname, basectrl, TYPE_IK)

    # Cleanup visibility condition
    basectrl_name = basectrl.rsplit(CTRL, 1)[0]
    remove(f"{basectrl_name}{VIS}{COND}") # REMOVE


# CONNECT FK ===========================================================

def connect_fk(rigname, fk, ik, stretchy):
    '''
    Connect FK components
    '''
    if not fk:
        return
    logger.info(f"Connect FK '{rigname}'")

    cog_ctrl = fstr('', COG_CTRL)
    basectrl = fstr(rigname, BASECTRL)
    fkroot_grp = fstr(rigname, CTRLROOT_GRP, TYPE_FK)
    fkjnt_grp = fstr(rigname, GROUP, TYPE_FK)
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)

    # FK joint group constraint
    cmds.parentConstraint(basectrl, fkjnt_grp, mo=1)
    # Organize FK spline group
    connect_spline_fk(rigname)

    # FK control constraint
    for num, jnt in enumerate(cst.JOINTS_FK[rigname]):
        fk_ctrl = fstr(rigname, CONTROL, TYPE_FK, num)
        fk_ctrl_grp = fstr(rigname, CTRL_GRP, TYPE_FK, num)
        last_sdk = fstr(rigname, SDK_GRP, TYPE_FK, num, NUM_CTRL_FK)
        ctrl_sdk = fstr(rigname, SDK_CTRL, TYPE_FK, num)
        sdk_constr = f"{fk_ctrl_grp}_parentConstraint1"
        jnt_constr = f"{jnt}_parentConstraint1"
        if not cmds.objExists(sdk_constr):
            cmds.parentConstraint(last_sdk, fk_ctrl_grp)
        cmds.connectAttr(f"{fk_ctrl}.rotate", f"{ctrl_sdk}.rotate", f=1)

    if fk and not ik:
        skeleton_grp = fstr('', SKELETON_GRP)
        parent_to(fkjnt_grp, skeleton_grp)
    else:
        fk_skeleton_grp = fstr('', SKELETON_GRP, TYPE_FK)
        parent_to(fkjnt_grp, fk_skeleton_grp)

        # IKFK Switch
        ikfk_switch = fstr(rigname, IKFK)
        for num in range(NUM_CTRL_FK):
            NN = num + 1
            fk_ctrl = fstr(rigname, CONTROL, '', NN)
            # IKFK Divider
            add_attribute_enum(fk_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
            # Proxy IKFK Switch attribute from Cog
            add_attribute_enum(fk_ctrl, IKFK_SWITCH[0], IKFK_SWITCH[1],
                               pxy=f"{cog_ctrl}.{ikfk_switch}")

        # IKFK Condition
        ikfk_cond = fstr(rigname, IKFK_COND)
        if not cmds.objExists(ikfk_cond):
            create_condition(ikfk_cond, secondTerm=3)
            cmds.connectAttr(f"{cog_ctrl}.{ikfk_switch}", f"{ikfk_cond}.firstTerm", f=1)
        # FK Group Visibility
        cmds.connectAttr(f"{ikfk_cond}.outColorR", f"{fkroot_grp}.visibility", f=1)
        cmds.connectAttr(f"{ikfk_cond}.outColorR", f"{fkjnt_grp}.visibility", f=1)

    if ik and stretchy: # Add Squash, Stretch, Twist, Offset, Roll attributes
        add_attribute_control_proxy(rigname, TYPE_FK)

    logger.info(f"DONE connecting FK '{rigname}'")

def connect_spline_fk(rigname):
    '''
    Clean up FK Spline structure.
    '''
    curve_fk = fstr(rigname, CURVE, TYPE_FK) # Curve FK
    spline_grp_fk = fstr(rigname, SPLINE_GRP, TYPE_FK)
    parent_to(curve_fk, spline_grp_fk)


# CONNECT IK ===========================================================

def connect_ik(rigname, fk, ik, stretchy):
    '''
    Connect IK components
    Warning: Uses hardcoded names, check naming.
    '''
    if not ik:
        return
    logger.info(f"Connect IK '{rigname}'")

    cog_ctrl = fstr('', COG_CTRL)
    rig_systems_grp = fstr('', RIG_SYSTEMS_GRP)
    ik_skeleton_grp = fstr('', SKELETON_GRP, TYPE_IK)
    ikjnt_grp = fstr(rigname, GROUP, TYPE_IK)

    # Organize groups
    # Move IK joint group under IK skeleton group
    if not cmds.objExists(ikjnt_grp):
        ikjnt_grp = cmds.group(em=True, n=ikjnt_grp)
        parent_to(ikjnt_grp, ik_skeleton_grp)
    parent_to(cst.JOINTS_IK[rigname][0], ikjnt_grp)
    parent_to(ikjnt_grp, ik_skeleton_grp)

    # Get IK controls
    # ik_controls = {'ik':[], 'float':[], 'spline': [], 'upvec':[]}
    ik_controls, ik_ctrlgrps = get_controls_ik(rigname)

    # Constrain spline controls
    driveattr, switch_cond, spline_constraints =\
        constrain_spline_controls(rigname, switch=IKFK_SWITCH)
    # Organize IK spline group
    connect_spline_ik(rigname, ik_controls, ik_ctrlgrps)
    logger.debug(f"driveattr:'{driveattr}'\n" +\
            f"switch cond:{switch_cond}\n" +\
            f"spline_constraints {spline_constraints}")

    if stretchy: # Add Squash, Stretch, Twist, Offset, Roll attributes
        add_attribute_control_proxy(rigname, TYPE_IK)

    # IKFK Switch
    ikfk_switch = fstr(rigname, IKFK)
    # IK control types: ik, float, spline
    for num in range(NUM_CTRL_IK):
        # Proxy IKFK Switch attribute from Cog
        ik_ctrl = ik_controls['ik'][num]
        add_attribute_enum(ik_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(ik_ctrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f"{cog_ctrl}.{ikfk_switch}")
        float_ctrl = ik_controls['float'][num]
        add_attribute_enum(float_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(float_ctrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f"{cog_ctrl}.{ikfk_switch}")
    for spline_ctrl in ik_controls['spline']:
        add_attribute_enum(spline_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
        add_attribute_enum(spline_ctrl, ln=IKFK_SWITCH[0], nn=IKFK_SWITCH[1],
                           pxy=f"{cog_ctrl}.{ikfk_switch}")

    # IKFK Condition
    ikfk_cond = fstr(rigname, IKFK_COND)
    if not cmds.objExists(ikfk_cond):
        create_condition(ikfk_cond, secondTerm=3)
        cmds.connectAttr(f"{cog_ctrl}.{ikfk_switch}", f"{ikfk_cond}.firstTerm", f=1)
    # IK Group Visibility
    cmds.connectAttr(f"{ikfk_cond}.outColorG", f"{ikjnt_grp}.visibility", f=1)

    logger.info(f"DONE connecting IK '{rigname}'")

def connect_spline_ik(rigname, ik_controls, ik_ctrlgrps):
    '''
    Clean up IK Spline structure with proper curve organization.
    '''
    logger.debug(f"ik_controls {ik_controls} ik_ctrlgrps {ik_ctrlgrps}")

    # Make sure ik handle and curves are under spline group
    spline_grp_ik = fstr(rigname, SPLINE_GRP, TYPE_IK) # Spline group IK
    driver_curve = fstr(rigname, CURVE, TYPE_IK)
    ikhandle, effector, solver_curve = get_spline_handle(rigname)
    parent_to(ikhandle, spline_grp_ik)
    parent_to(solver_curve, spline_grp_ik)
    parent_to(driver_curve, spline_grp_ik)

    # Move controls under basectrl
    basectrl = fstr(rigname, BASECTRL)
    contents = list()
    contents.append(ik_ctrlgrps['ik'][0])
    contents.extend(ik_ctrlgrps['float'])
    contents.extend([ik_ctrlgrps['spline'][0], ik_ctrlgrps['spline'][4],
                    ik_ctrlgrps['spline'][-1]]) # top, bot, mid_rot
    for obj in contents:
        parent_to(obj, basectrl)

    cmds.select(clear=True) # Deselect all


# ADD ATTRIBUTES =======================================================

def add_attribute_basectrl_ik(rigname, control):
    '''
    Add twist, offset, roll, IKFK attributes
    '''
    spline_handle = fstr(rigname, SPLINE_HANDLE, TYPE_IK)
    if not cmds.objExists(spline_handle):
        logger.error(f"Could not find spline handle {spline_handle}")
    add_attribute_enum(control, TWIST_DIVIDER[0], TWIST_DIVIDER[1], TWIST_DIVIDER[2])
    if not cmds.attributeQuery('twist', n=control, ex=1):
        cmds.addAttr(control, ln='twist', at='float', k=1, dv=0)
    if not cmds.attributeQuery('roll', n=control, ex=1):
        cmds.addAttr(control, ln='roll', at='float', k=1, dv=0)
    if not cmds.attributeQuery('offset', n=control, ex=1):
        cmds.addAttr(control, ln='offset', at='float', k=1, dv=0)
    cmds.connectAttr(f"{control}.twist", f"{spline_handle}.twist", f=1)
    cmds.connectAttr(f"{control}.roll", f"{spline_handle}.roll", f=1)
    cmds.connectAttr(f"{control}.offset", f"{spline_handle}.offset", f=1)

    # IKFK Switch
    cog_ctrl = fstr(rigname, COG_CTRL)
    ikfk_switch = fstr(rigname, IKFK)
    add_attribute_enum(control, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
    # Proxy IKFK Switch attribute from Cog
    add_attribute_enum(control, IKFK_SWITCH[0], IKFK_SWITCH[1],
                       pxy=f"{cog_ctrl}.{ikfk_switch}")

def add_attribute_basectrl_scale(rigname, control, typ):
    '''
    Add jntScaleY and jntScaleZ attributes
    Connect to squash scale nodes created by setup_joint_squash()
    '''
    # Add Scale attributes
    add_attribute_enum(control, SCALE_DIVIDER[0], SCALE_DIVIDER[1], SCALE_DIVIDER[2])
    if typ == TYPE_FK:
        joints = cst.JOINTS_FK[rigname]
    elif typ == TYPE_IK:
        joints = cst.JOINTS_IK[rigname]
    else:
        logger.error(f"Invalid type '{typ}'. Choose TYPE_FK or TYPE_IK.")

    for i in range(len(joints)):
        # Joint scale node from setup_joint_squash()
        jnt_mult = f'{typ}{rigname}_squash_{i:02d}_multiplyDivide'
        if not cmds.objExists(jnt_mult):
            logger.warning(f"'{jnt_mult}' does not exist, skipping joint {i}")
            continue

        # Add scale attributes to control
        if not cmds.attributeQuery(f'jntScaleY{i:02}', n=control, ex=1):
            cmds.addAttr(control, ln=f'jntScaleY{i:02}', at='float', k=1, dv=1, min=0.1, max=10)
        if not cmds.attributeQuery(f'jntScaleZ{i:02}', n=control, ex=1):
            cmds.addAttr(control, ln=f'jntScaleZ{i:02}', at='float', k=1, dv=1, min=0.1, max=10)

        # Replace static input2Y/Z values with scale value
        # The jnt_mult node structure is:
        # input1Y/Z <- squash_blend.output (dynamic squash effect)
        # input2Y/Z <- original scale values (static) <- Replace with custom scale
        # outputY/Z -> joint/SDK scale
        cmds.connectAttr(f'{control}.jntScaleY{i:02}', f'{jnt_mult}.input2Y', f=1)
        cmds.connectAttr(f'{control}.jntScaleZ{i:02}', f'{jnt_mult}.input2Z', f=1)

def add_attribute_control_proxy(rigname, typ):
    '''
    Add proxy attributes from basectrl to controls.
    '''
    basectrl = fstr(rigname, BASECTRL)
    if typ == TYPE_FK:
        controls = get_controls_all(fk=True, ik=False, bn=False, include_cog=False)
    elif typ == TYPE_IK:
        controls = get_controls_all(fk=False, ik=True, bn=False, include_cog=False)
    else:
        logger.error(f"Invalid type '{typ}'. Choose TYPE_FK or TYPE_IK.")
    # Add proxy attributes to IK controls
    for ctrl in controls:
        add_attribute_enum(ctrl, STRETCH_DIVIDER[0], STRETCH_DIVIDER[1], STRETCH_DIVIDER[2])
        for atr in ['squash', 'stretch']: # Squash and Stretch
            add_attribute_enum(ctrl, ln=atr, nn=titlecase(atr), pxy=f"{basectrl}.{atr}")
        add_attribute_enum(ctrl, TWIST_DIVIDER[0], TWIST_DIVIDER[1], TWIST_DIVIDER[2])
        for atr in ['twist', 'roll', 'offset']: # Twist, Roll, Offset
            add_attribute_enum(ctrl, ln=atr, nn=titlecase(atr), pxy=f"{basectrl}.{atr}")


# CONSTRAINTS ==========================================================

def constrain_skeleton(rigname, fk, ik):
    '''
    Constrain FK IK skeleton to BN skeleton
    '''
    logger.info('Setting up Skeleton Constraints..')
    # Cleanup old constraints
    for bn_jnt in cst.JOINTS_BN[rigname]:
        constraints = cmds.listConnections(bn_jnt, type='constraint') or []
        for constr in constraints:
            cmds.delete(constr) # Delete constraint

    if fk and not ik:
        for n in range(len(cst.JOINTS_FK[rigname])):
            fk_jnt = cst.JOINTS_FK[rigname][n]
            bn_jnt = cst.JOINTS_BN[rigname][n]
            # Constrain skeleton - FK skeleton, BN skeleton
            cmds.parentConstraint(fk_jnt, bn_jnt, mo=True)
            cmds.scaleConstraint(fk_jnt, bn_jnt)

    elif ik and not fk:
        for n in range(len(cst.JOINTS_IK[rigname])):
            ik_jnt = cst.JOINTS_IK[rigname][n]
            bn_jnt = cst.JOINTS_BN[rigname][n]
            # Constrain skeleton - IK skeleton, BN skeleton
            cmds.parentConstraint(ik_jnt, bn_jnt, mo=True)
            cmds.scaleConstraint(ik_jnt, bn_jnt)

    else:
        for n in range(len(cst.JOINTS_FK[rigname])):
            fk_jnt = cst.JOINTS_FK[rigname][n]
            ik_jnt = cst.JOINTS_IK[rigname][n]
            bn_jnt = cst.JOINTS_BN[rigname][n]
            # Constrain skeleton - FK, IK, BN skeleton
            bn_constr = cmds.parentConstraint(fk_jnt, ik_jnt, bn_jnt, mo=True)[0]
            sc_constr = cmds.scaleConstraint(fk_jnt, ik_jnt, bn_jnt)[0]
            # Set influence on BN constraint
            ikfk_cond = fstr(rigname, IKFK_COND) # IKFK Condition
            cmds.connectAttr(f"{ikfk_cond}.outColorR", f"{bn_constr}.{fk_jnt}W0", f=1)
            cmds.connectAttr(f"{ikfk_cond}.outColorG", f"{bn_constr}.{ik_jnt}W1", f=1)
            cmds.connectAttr(f"{ikfk_cond}.outColorR", f"{sc_constr}.{fk_jnt}W0", f=1)
            cmds.connectAttr(f"{ikfk_cond}.outColorG", f"{sc_constr}.{ik_jnt}W1", f=1)

def disconnect_skeleton(rigname, fk, ik):
    '''
    Disconnect skeleton and joint scale
    '''
    if fk:
        fk_jnts = cst.JOINTS_FK[rigname]
        for fk_jnt in fk_jnts:
            disconnect_all(fk_jnt, source=True)
    if ik:
        ik_jnts = cst.JOINTS_IK[rigname]
        for ik_jnt in ik_jnts:
            disconnect_all(ik_jnt, source=True)

def constrain_spline_controls(rigname, typ=TYPE_IK, switch=IKFK_SWITCH):
    '''
    Create constraints for spline controls.
    Create condition nodes for switching modes between Spline, IK, Float modes.
    Handle constraint of cluster handles, which are used to deform the spline curve
driving the joint chain.

    Arguments
        rigname (str): name of rig part
        switch (tuple): switch type (longName, niceName, enumName, dv)

    Return
        driveattr (str): switch attribute plug on cog ctrl
        switch_cond (str list): condition nodes for switching
        spline_constraints (str list): constraints created on spline clusters
    '''
    # Control Types
    CTRLTYP = ['spline', 'ik', 'float']

    cog_ctrl = fstr('', COG_CTRL)
    driveattr = '' # Switch attribute plug
    switch_cond = list() # Condition nodes for switching
    spline_constraints = list() # Constraints on spline clusters

    # Get relevant controls
    ik_controls, ik_ctrlgrps = get_controls_ik(rigname)
    upvec_bsectrl = ik_controls['upvec'][0] # upvec_base_ctrl
    upvec_endctrl = ik_controls['upvec'][1] # upvec_end_ctrl
    upvec_bsegrp = ik_ctrlgrps['upvec'][0] # upvec_base_ctrl_grp
    upvec_endgrp = ik_ctrlgrps['upvec'][1] # upvec_end_ctrl_grp

    # Constrain IK Spline mid-controls and upvecs
    # Build 2 constraints, the first between top, bot, mid controls,
    # and the second between mid_rot and top controls.
    spline_controls = [fstr(rigname, template, typ) for template in SPLINE_CONTROLS]
    spline_ctrlgrps = [f"{ctrl}{GRP}" for ctrl in spline_controls]
    # Constrain ikspline - bot_ctrl, top_ctrl, mid_grp
    cmds.parentConstraint(spline_controls[0], spline_controls[4], spline_ctrlgrps[2], mo=1)[0]
    # Constrain ikspline - mid_rot, top_grp
    cmds.parentConstraint(spline_controls[5], upvec_endgrp, mo=1)[0]
    # cmds.parentConstraint(spline_controls[5], spline_ctrlgrps[4], mo=1)[0]

    # Get cluster handles
    cluster_handles = list()
    for NN in range(1, NUM_CTRL_IK+1):
        cluster_handle = fstr(rigname, CLUSTER_HANDLE, typ, NN)
        cluster_handles.append(cluster_handle)
    logger.info(f"Get Cluster Handles for Spline Constraint:\n{cluster_handles}")

    # (constrainToControls) Add IKFK Switch attribute to Cog ctrl
    # e.g. ----------| TAIL IKFK
    #      L Fintail | SplineIK / IK / Float / Fk
    #      R Fintail | SplineIK / IK / Float / Fk
    if not cmds.attributeQuery(IKFK_DIVIDER[0], n=cog_ctrl, ex=1):
        add_attribute_enum(cog_ctrl, IKFK_DIVIDER[0], IKFK_DIVIDER[1], IKFK_DIVIDER[2])
    # IKFK Switch attribute
    cog_ln_ikfk = fstr(rigname, IKFK) # e.g. L_fintail_ikfk
    cog_nn_ikfk = re.sub(r'[-_\s]+', ' ', rigname).title() # e.g. L Fintail
    driveattr = f"{cog_ctrl}.{cog_ln_ikfk}" # e.g. cog_ctrl.L_fintail_ikfk
    # Create Switch attribute as Enum list
    if not cmds.attributeQuery(cog_ln_ikfk, n=cog_ctrl, ex=1):
        add_attribute_enum(cog_ctrl, ln_ikfk, nn_ikfk, switch[2])
    logger.info(f"driveattr: {driveattr}")

    # (constrainControls) Constrain clusters to controls. Set up switching later
    # Each cluster handle is constrained to 3 different controls (spline, ik, float).
    # This allows for control blending based on the selected Switch mode.
    # Resulting constraints are stored for use in condition node setup.
    for i, clstr in enumerate(cluster_handles):
        constrain_objs = list() # List of 5 objects, 4 controls and last cluster
        for ctrltyp in CTRLTYP:
            constrain_objs.append(ik_controls[ctrltyp][i]) # Get existing controls
        constrain_objs.append(clstr) # Append target object, cluster handle
        # Constrain cluster to controls
        cluster_constr = cmds.parentConstraint(constrain_objs, mo=1)[0]
        spline_constraints.append(cluster_constr)
    logger.info(f"constraints {spline_constraints}")

    # (setupConditionNodesConstraints) Set up switching condition for constraints and vis
    # Condition nodes drive constraint weights and visiblity of controls.
    # Active control type (based on Switch enum) gets a weight of 1 and visiblity ON
    for i, ctrltyp in enumerate(CTRLTYP):
        drivenattrs = list()
        for j in range(NUM_CTRL_IK): # Iterate through IK controls
            ik_control = ik_controls[ctrltyp][j]
            ik_ctrlgrp = f"{ik_control}{GRP}"
            drivenattrs.append(f"{spline_constraints[j]}.{ik_control}W{i}")
            drivenattrs.append(f"{ik_ctrlgrp}.visibility")
        if ctrltyp == 'spline':
            mid_rot_ctrl = fstr(rigname, SPLINE_MID_ROT, typ)
            drivenattrs.append(f"{mid_rot_ctrl}.visibility")
        # IK Switch Condition
        ik_switch_cond = f"{typ}{rigname}_switch_{ctrltyp}{COND}"
        logger.info(f"ik_switch_cond {ik_switch_cond}\ndrivenattrs {drivenattrs}")
        # Create condition node connecting driveattr to drivenattrs
        switch_cond.append(create_condition_multi(
            driveattr, drivenattrs, ik_switch_cond, i))
        # Cleanup unnecessary Visibility Conditions
        remove(f"{rigname}_switch_{ctrltyp}{VIS}{COND}")
        remove(f"{TYPE_IK}{rigname}_switch_{ctrltyp}{VIS}{COND}")

    cluster_handle_bse = fstr(rigname, CLUSTER_UPV_HANDLE, typ, TAG='_base')
    cluster_handle_end = fstr(rigname, CLUSTER_UPV_HANDLE, typ, TAG='_end')
    # Match upvec control groups to base/end cluster handles
    match_transform(upvec_bsegrp, cluster_handle_bse, pos=1, rot=1, scl=0, moc=0)
    match_transform(upvec_endgrp, cluster_handle_end, pos=1, rot=1, scl=0, moc=0)
    # base/end cluster handles are constrained to upvec controls
    # These help drive twist and orientation for the spline IK
    cmds.parentConstraint(upvec_bsectrl, cluster_handle_bse, mo=1)
    cmds.parentConstraint(upvec_endctrl, cluster_handle_end, mo=1)
    cmds.parentConstraint(upvec_bsectrl, spline_ctrlgrps[0], mo=1) # upv_base, bot_ctrl
    cmds.parentConstraint(upvec_endctrl, spline_ctrlgrps[4], mo=1) # upv_end, top_ctrl

    return driveattr, switch_cond, spline_constraints
