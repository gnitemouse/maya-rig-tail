'''
# rig_tail_test.py
author: Daisy Jane @gnitemouse

Debug test for matrix-based FX offset architecture.
Updated for per-FX composeMatrix approach with parentInverseMatrix.

Usage:
import rig_tail_test as rt_test
rt_test.test_matrix()           # Quick diagnostic
rt_test.show_data_flow()        # Data flow diagram
rt_test.test_alignment()        # Position alignment check
rt_test.test_matrix_opm()       # OPM check
rt_test.test_wave()             # Wave test
rt_test.test_curl()             # Curl test
rt_test.test_local_trs()        # Check BN joints are zeroed
rt_test.test_time_evaluation()  # Test time
'''
import maya.cmds as cmds
import maya.api.OpenMaya as om
from rig_tail_constants import *
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_anim as rt_ani
import math

# FX MATRIX DIAGNOSTICS ======================================

def test_matrix(rigname='tail'):
    '''
    Diagnostic for pure matrix OPM architecture.

    Expected node names per joint (matching rig_tail_matrix.py):
        {rigname}_{NN:02d}_ikfk_blendMatrix
        {rigname}_{NN:02d}_baseLocal_multMatrix
        {rigname}_{NN:02d}_final_multMatrix      (only when fx_list is non-empty)
        {rigname}_{NN:02d}_{fx}_composeMatrix    (one per FX, all joints including 00)

    BN.offsetParentMatrix source:
        With FX:    final_multMatrix.matrixSum
        Without FX: baseLocal_multMatrix.matrixSum

    Joint 00 differs from 01+ only in baseLocal_multMatrix wiring:
        Joint 00:  blendMatrix.outputMatrix → matrixIn[0]  (no parent inverse)
        Joint 01+: bn_parent.worldInverseMatrix → matrixIn[0]
                   blendMatrix.outputMatrix     → matrixIn[1]
    '''
    header = '''
================================================================================
                QUICK DIAGNOSTIC (Pure Matrix OPM Architecture)
================================================================================
'''
    print(header)

    if rigname not in rt_cst.JOINTS_BN:
        print(f'x No BN joints defined for {rigname}')
        return

    joints = rt_cst.JOINTS_BN[rigname]
    print(f'Found {len(joints)} BN joints\n')

    fx_list = []
    if rt_cst.EFFECTS.get('curl'):
        fx_list.append('curl')
    if rt_cst.EFFECTS.get('wave'):
        fx_list.append('wave')
    if rt_cst.EFFECTS.get('noise'):
        fx_list.append('noise')

    issues = []
    warnings = []

    # === JOINT ORIENT CHECK ===
    # JO must NOT be zeroed - zeroing it changes BN worldMatrix and corrupts
    # all child baseLocal computations via bn_parent.worldInverseMatrix
    section = '''JOINT ORIENT CHECK (JO must be preserved, not zeroed):
--------------------------------------------------------------------------------'''
    print(section)

    for i, jnt in enumerate(joints):
        NN = rt_nam.get_index_from_name(jnt)
        if not cmds.attributeQuery('jointOrient', node=jnt, exists=True):
            continue
        jo = cmds.getAttr(f'{jnt}.jointOrient')[0]
        if i == 0:
            # Root joint - check IK JO matches BN JO (they should be identical)
            ik_joints = rt_cst.JOINTS_IK.get(rigname, [])
            if ik_joints:
                ik_jo = cmds.getAttr(f'{ik_joints[0]}.jointOrient')[0]
                jo_match = all(abs(jo[k] - ik_jo[k]) < 0.001 for k in range(3))
                if not jo_match:
                    issues.append(
                        f'Joint 00: BN JO {[round(v,3) for v in jo]} != '
                        f'IK JO {[round(v,3) for v in ik_jo]} - axes will mismatch'
                    )
                else:
                    print(f'  Jnt 00: JO = {[round(v,3) for v in jo]} (matches IK) OK')
            # Warn if JO was zeroed on root - this is the primary spiral cause
            if all(abs(jo[k]) < 0.001 for k in range(3)):
                ik_joints = rt_cst.JOINTS_IK.get(rigname, [])
                if ik_joints:
                    ik_jo = cmds.getAttr(f'{ik_joints[0]}.jointOrient')[0]
                    if any(abs(ik_jo[k]) > 0.1 for k in range(3)):
                        issues.append(
                            f'Joint 00: BN JO is (0,0,0) but IK JO is '
                            f'{[round(v,3) for v in ik_jo]} - '
                            f'JO was zeroed, this causes spiral in all children'
                        )
    print()

    # === MATRIX NETWORK CHECK ===
    section = '''MATRIX NETWORK CHECK:
--------------------------------------------------------------------------------'''
    print(section)

    for i, jnt in enumerate(joints):
        NN = rt_nam.get_index_from_name(jnt)

        # --- blendMatrix ---
        blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
        if not cmds.objExists(blend_mtx):
            issues.append(f'Joint {NN:02d}: Missing ikfk_blendMatrix')
        else:
            input_conn = cmds.listConnections(f'{blend_mtx}.inputMatrix', s=1, d=0, p=1) or []
            if not input_conn:
                issues.append(f'Joint {NN:02d}: blendMatrix.inputMatrix not connected')
            else:
                print(f'  Jnt {NN:02d}: blendMatrix <- {input_conn[0]}')

        # --- baseLocal_multMatrix ---
        baselocal = f'{rigname}_{NN:02d}_baseLocal_multMatrix'
        if not cmds.objExists(baselocal):
            issues.append(f'Joint {NN:02d}: Missing baseLocal_multMatrix')
        else:
            m0 = cmds.listConnections(f'{baselocal}.matrixIn[0]', s=1, d=0, p=1) or []
            m1 = cmds.listConnections(f'{baselocal}.matrixIn[1]', s=1, d=0, p=1) or []

            if i == 0:
                # Root: matrixIn[0] = blendMatrix.outputMatrix, matrixIn[1] unused
                if not m0:
                    issues.append(f'Joint 00: baseLocal_multMatrix.matrixIn[0] not connected')
                elif 'blendMatrix' not in m0[0]:
                    issues.append(
                        f'Joint 00: baseLocal_multMatrix.matrixIn[0] should be '
                        f'blendMatrix, got {m0[0]}'
                    )
                else:
                    print(f'  Jnt 00: baseLocal = blendMatrix only (root, no parent inverse) OK')
            else:
                # Non-root: matrixIn[0] = blendMatrix (driver), matrixIn[1] = parent worldInverseMatrix
                if not m0:
                    issues.append(f'Joint {NN:02d}: baseLocal_multMatrix.matrixIn[0] not connected')
                elif 'blendMatrix' not in m0[0]: # [0] is driver now
                    issues.append(
                        f'Joint {NN:02d}: baseLocal.matrixIn[0] should be '
                        f'blendMatrix, got {m0[0]}'
                    )
                if not m1:
                    issues.append(f'Joint {NN:02d}: baseLocal_multMatrix.matrixIn[1] not connected')
                elif 'worldInverseMatrix' not in m1[0]: # [1] is parent inverse now
                    issues.append(
                        f'Joint {NN:02d}: baseLocal.matrixIn[1] should be '
                        f'worldInverseMatrix, got {m1[0]}'
                    )
                if m0 and m1 and 'blendMatrix' in m0[0] and 'worldInverseMatrix' in m1[0]:
                    print(f'  Jnt {NN:02d}: baseLocal = blendMatrix * parentInv OK')

        # --- FX composeMatrix nodes ---
        for fx_name in fx_list:
            fx_compose = f'{rigname}_{NN:02d}_{fx_name}_composeMatrix'
            if not cmds.objExists(fx_compose):
                issues.append(f'Joint {NN:02d}: Missing {fx_name}_composeMatrix')
            else:
                # Translation must be zero
                trans = cmds.getAttr(f'{fx_compose}.inputTranslate')[0]
                if any(abs(t) > 0.001 for t in trans):
                    issues.append(
                        f'Joint {NN:02d}: {fx_name}_composeMatrix has '
                        f'non-zero translation {[round(v,4) for v in trans]}'
                    )
                # Check rotation input is connected
                has_rot_conn = any(
                    cmds.listConnections(f'{fx_compose}.inputRotate{ax}', s=1, d=0)
                    for ax in ('X', 'Y', 'Z')
                )
                if not has_rot_conn:
                    warnings.append(
                        f'Joint {NN:02d}: {fx_name}_composeMatrix.inputRotate not connected'
                    )

        # --- final_multMatrix (only exists when fx_list non-empty) ---
        if fx_list:
            final_mult = f'{rigname}_{NN:02d}_final_multMatrix'
            if not cmds.objExists(final_mult):
                issues.append(f'Joint {NN:02d}: Missing final_multMatrix')
            else:
                m0 = cmds.listConnections(f'{final_mult}.matrixIn[0]', s=1, d=0, p=1) or []
                if not m0 or 'baseLocal_multMatrix' not in m0[0]:
                    issues.append(
                        f'Joint {NN:02d}: final_multMatrix.matrixIn[0] should be '
                        f'baseLocal_multMatrix.matrixSum, got {m0[0] if m0 else "nothing"}'
                    )
                # Check each FX compose is wired in
                for idx, fx_name in enumerate(fx_list, start=1):
                    mx = cmds.listConnections(
                        f'{final_mult}.matrixIn[{idx}]', s=1, d=0, p=1
                    ) or []
                    if not mx or fx_name not in mx[0]:
                        issues.append(
                            f'Joint {NN:02d}: final_multMatrix.matrixIn[{idx}] '
                            f'should be {fx_name}_composeMatrix, got '
                            f'{mx[0] if mx else "nothing"}'
                        )

        # --- OPM source ---
        opm_conn = cmds.listConnections(f'{jnt}.offsetParentMatrix', s=1, d=0, p=1) or []
        if not opm_conn:
            issues.append(f'Joint {NN:02d}: offsetParentMatrix not connected')
        else:
            expected_src = (
                f'{rigname}_{NN:02d}_final_multMatrix.matrixSum'
                if fx_list
                else f'{rigname}_{NN:02d}_baseLocal_multMatrix.matrixSum'
            )
            if expected_src not in opm_conn[0]:
                issues.append(
                    f'Joint {NN:02d}: offsetParentMatrix connected to wrong source. '
                    f'Expected {expected_src.split(".")[0]}, got {opm_conn[0]}'
                )
            else:
                print(f'  Jnt {NN:02d}: OPM <- {opm_conn[0]} OK')

    print()

    # === LOCAL TRS CHECK ===
    section = '''LOCAL TRS CHECK (should be identity):
--------------------------------------------------------------------------------'''
    print(section)

    for i, jnt in enumerate(joints):
        NN = rt_nam.get_index_from_name(jnt)
        trans = cmds.getAttr(f'{jnt}.translate')[0]
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        scale = cmds.getAttr(f'{jnt}.scale')[0]
        if any(abs(t) > 0.001 for t in trans):
            issues.append(f'Joint {NN:02d}: translate = {[round(v,4) for v in trans]}')
        if any(abs(r) > 0.001 for r in rot):
            issues.append(f'Joint {NN:02d}: rotate = {[round(v,4) for v in rot]}')
        if any(abs(s - 1.0) > 0.001 for s in scale):
            issues.append(f'Joint {NN:02d}: scale = {[round(v,4) for v in scale]}')

    if not any('translate' in x or 'rotate' in x or 'scale' in x for x in issues):
        print('  OK All BN joints have identity local TRS')
    print()

    # === OPM NUMERIC CHECK ===
    # First-row X value should be ~1.0 for all joints after root.
    # Growing deviation indicates accumulation from a mismatched BN worldMatrix
    # (most commonly caused by zeroed JO on root joint).
    section = '''OPM NUMERIC CHECK (first-row X should be ~1.0, off-diag ~0.0):
--------------------------------------------------------------------------------'''
    print(section)

    opm_drift_warned = False
    for i, jnt in enumerate(joints):
        NN = rt_nam.get_index_from_name(jnt)
        opm = cmds.getAttr(f'{jnt}.offsetParentMatrix')
        if opm:
            diag_x = opm[0]   # should be ~1.0
            off_y = opm[1]    # should be ~0.0
            off_z = opm[2]    # should be ~0.0
            drift = abs(diag_x - 1.0) + abs(off_y) + abs(off_z)
            if drift > 0.01 and not opm_drift_warned:
                issues.append(
                    f'Joint {NN:02d}: OPM first row [{round(diag_x,6)}, '
                    f'{round(off_y,6)}, {round(off_z,6)}] - '
                    f'significant deviation, likely JO zeroed on root'
                )
                opm_drift_warned = True
                print(f'  Jnt {NN:02d}: OPM first row [{round(diag_x,6)}, {round(off_y,8)}, {round(off_z,8)}] DRIFT DETECTED')
            elif drift > 1e-10:
                print(f'  Jnt {NN:02d}: OPM first row [{round(diag_x,6)}, {round(off_y,2e-8):.2e}, {round(off_z,2e-8):.2e}] (fp noise, ok)')
            else:
                print(f'  Jnt {NN:02d}: OPM first row [{round(diag_x,6)}, {off_y:.2e}, {off_z:.2e}] OK')
    print()

    # === ALIGNMENT CHECK ===
    if rigname in rt_cst.JOINTS_IK:
        section = '''POSITION ALIGNMENT CHECK (BN vs IK world positions):
--------------------------------------------------------------------------------'''
        print(section)
        max_misalign = 0
        worst_joint = 0

        for i, (bn_jnt, ik_jnt) in enumerate(
            zip(joints, rt_cst.JOINTS_IK[rigname])
        ):
            bn_pos = cmds.xform(bn_jnt, q=1, ws=1, t=1)
            ik_pos = cmds.xform(ik_jnt, q=1, ws=1, t=1)
            dist = sum((bn_pos[j] - ik_pos[j])**2 for j in range(3)) ** 0.5

            if dist > max_misalign:
                max_misalign = dist
                worst_joint = i

            if dist > 0.5:
                warnings.append(f'Joint {i:02d}: BN misaligned from IK by {dist:.3f} units')

        print(f'  Max misalignment: {max_misalign:.4f} units at joint {worst_joint:02d}')
        if max_misalign < 0.01:
            print('  OK Alignment EXCELLENT')
        elif max_misalign < 0.1:
            print('  WARNING Alignment ACCEPTABLE')
        else:
            print('  ERROR Alignment BAD - check JO and matrix wiring')
        print()

    # === RESULTS ===
    if issues:
        print('CRITICAL ISSUES:')
        for issue in issues:
            print(f'  {issue}')
        print()

    if warnings:
        print('WARNINGS:')
        for warning in warnings:
            print(f'  {warning}')
        print()

    if not issues and not warnings:
        print('OK Pure matrix OPM architecture looks correct.')
        print('   If deformation is still wrong, check that IK/FK joints')
        print('   are at correct positions and run test_alignment().')


def test_local_trs(rigname='tail'):
    '''
    Check that all BN joints have identity local TRS.
    '''
    print('\n=== LOCAL TRS CHECK ===\n')

    if rigname not in rt_cst.JOINTS_BN:
        print(f'× No BN joints for {rigname}')
        return

    joints = rt_cst.JOINTS_BN[rigname]
    all_good = True

    for i, jnt in enumerate(joints):
        NN = rt_nam.get_index_from_name(jnt)
        trans = cmds.getAttr(f'{jnt}.translate')[0]
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        scale = cmds.getAttr(f'{jnt}.scale')[0]

        trans_ok = all(abs(t) < 0.001 for t in trans)
        rot_ok = all(abs(r) < 0.001 for r in rot)
        scale_ok = all(abs(s - 1.0) < 0.001 for s in scale)

        if not (trans_ok and rot_ok and scale_ok):
            all_good = False
            print(f'Joint {NN:02d}: {jnt}')
            if not trans_ok:
                print(f'  ❌ translate = {[round(v, 4) for v in trans]} (should be [0,0,0])')
            if not rot_ok:
                print(f'  ❌ rotate = {[round(v, 4) for v in rot]} (should be [0,0,0])')
            if not scale_ok:
                print(f'  ❌ scale = {[round(v, 4) for v in scale]} (should be [1,1,1])')

    if all_good:
        print('✅ All BN joints have identity local TRS')
    else:
        print('\n❌ Some BN joints have non-zero local TRS')
        print('   Run: rt_test.fix_bn_local_trs(rigname) to fix')
    print()


def fix_bn_local_trs(rigname='tail'):
    '''
    Emergency fix: zero all BN joint local TRS.
    WARNING: This will break existing offsetParentMatrix connections.
    Only use if rebuilding the matrix network.
    '''
    print('\n=== FIXING BN LOCAL TRS ===\n')

    if rigname not in rt_cst.JOINTS_BN:
        print(f'× No BN joints for {rigname}')
        return

    joints = rt_cst.JOINTS_BN[rigname]

    for jnt in joints:
        for attr in ['translateX', 'translateY', 'translateZ', 'rotateX', 'rotateY', 'rotateZ']:
            cmds.setAttr(f'{jnt}.{attr}', 0, l=0)
        cmds.setAttr(f'{jnt}.scale', 1, 1, 1, type='double3')
        print(f'  Zeroed {jnt}')

    print('\n✅ All BN joints zeroed')
    print('   You must rebuild the matrix network for this to take effect!')
    print()


def test_fx_order(rigname='tail'):
    '''
    Verify FX matrices multiply BEFORE baseLocal in final_multMatrix.

    Correct order (rotate about own pivot):
        matrixIn[0..n-1] = fx composeMatrix nodes
        matrixIn[n]      = baseLocal_multMatrix.matrixSum
    Wrong order (zig-zag / lengthening):
        matrixIn[0]      = baseLocal, fx after
    '''
    print('\n=== FX MULTIPLY ORDER CHECK ===\n')

    if rigname not in rt_cst.JOINTS_BN:
        print(f'x No BN joints for {rigname}')
        return

    fx_list = []
    if rt_cst.EFFECTS.get('curl'):
        fx_list.append('curl')
    if rt_cst.EFFECTS.get('wave'):
        fx_list.append('wave')
    if rt_cst.EFFECTS.get('noise'):
        fx_list.append('noise')

    if not fx_list:
        print('  No FX enabled, nothing to check')
        return

    joints = rt_cst.JOINTS_BN[rigname]
    bad = 0

    for jnt in joints:
        NN = rt_nam.get_index_from_name(jnt)
        final_mult = f'{rigname}_{NN:02d}_final_multMatrix'
        if not cmds.objExists(final_mult):
            continue

        # What is wired into matrixIn[0]? Should be an fx composeMatrix, NOT baseLocal.
        first = cmds.listConnections(f'{final_mult}.matrixIn[0]', s=1, d=0, p=1) or []
        last_idx = len(fx_list)
        last = cmds.listConnections(f'{final_mult}.matrixIn[{last_idx}]', s=1, d=0, p=1) or []

        first_src = first[0] if first else 'nothing'
        last_src = last[0] if last else 'nothing'

        first_is_fx = any(fx in first_src for fx in fx_list)
        last_is_base = 'baseLocal_multMatrix' in last_src

        if first_is_fx and last_is_base:
            if NN < 3:
                print(f'  Jnt {NN:02d}: fx first, baseLocal last OK')
        else:
            bad += 1
            print(f'  Jnt {NN:02d}: WRONG ORDER')
            print(f'           matrixIn[0]        = {first_src}')
            print(f'           matrixIn[{last_idx}] = {last_src}')
            print(f'           expected fx at [0], baseLocal at [{last_idx}]')

    print()
    if bad == 0:
        print('  OK All FX chains multiply fx before baseLocal')
    else:
        print(f'  ERROR {bad} joints have baseLocal ahead of FX (causes zig-zag)')
    print()


def test_ikfk_drive(rigname='tail', joint_index=3):
    '''
    Diagnose FK control -> FK joint -> BN propagation at both switch extremes.

    Checks, for one sample joint:
      1. What drives FK_jnt.rotate (is the control actually connected?)
      2. blendMatrix target weight when switch is set low vs high
      3. Whether BN world position tracks IK vs FK as the switch moves
    '''
    print(f'\n=== IK/FK DRIVE CHECK (joint {joint_index:02d}) ===\n')

    from rig_tail_constants import COG_CTRL, IKFK
    cog_ctrl = rt_nam.fstr('', COG_CTRL)
    ikfk_attr = rt_nam.fstr(rigname, IKFK)

    ik_jnt = rt_cst.JOINTS_IK[rigname][joint_index]
    fk_jnt = rt_cst.JOINTS_FK[rigname][joint_index]
    bn_jnt = rt_cst.JOINTS_BN[rigname][joint_index]
    NN = rt_nam.get_index_from_name(bn_jnt)

    # 1. What drives the FK joint's rotation?
    print('FK JOINT DRIVE:')
    sdk_grp = rt_nam.fstr(rigname, SDK_JNT, TYPE_FK, NN)
    fk_rot_conn = cmds.listConnections(f'{sdk_grp}.rotate', s=1, d=0, p=1) or []
    fk_rx_conn = cmds.listConnections(f'{sdk_grp}.rotateX', s=1, d=0, p=1) or []
    if fk_rot_conn or fk_rx_conn:
        for c in (fk_rot_conn + fk_rx_conn):
            print(f'  {sdk_grp}.rotate <- {c}')
    else:
        print(f'  x NOTHING drives {sdk_grp}.rotate')
        print(f'    -> FK control to SDK group link is missing')

    # 2. Switch attribute range vs condition threshold
    print('SWITCH ATTRIBUTE:')
    if cmds.objExists(cog_ctrl) and cmds.attributeQuery(ikfk_attr, node=cog_ctrl, exists=True):
        cur = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}')
        has_min = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, minExists=True)
        has_max = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, maxExists=True)
        amin = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, min=True)[0] if has_min else None
        amax = cmds.attributeQuery(ikfk_attr, node=cog_ctrl, max=True)[0] if has_max else None
        print(f'  {cog_ctrl}.{ikfk_attr} = {cur} (range {amin} to {amax})')
        cond = f'{rigname}_{NN:02d}_ikfk_remap_condition'
        if cmds.objExists(cond):
            thresh = cmds.getAttr(f'{cond}.secondTerm')
            print(f'  condition threshold (secondTerm) = {thresh}')
            if amax is not None and thresh > amax:
                print(f'  ERROR threshold {thresh} exceeds max {amax}: FK weight can never turn on')
    else:
        print(f'  x switch attr {ikfk_attr} not found on {cog_ctrl}')
    print()

    # 3. Sweep the switch, watch blend weight and BN tracking
    print('SWEEP (BN should track FK at one end, IK at the other):')
    blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
    saved = cmds.getAttr(f'{cog_ctrl}.{ikfk_attr}')

    fk_ctrl0 = rt_nam.fstr(rigname, CONTROL, TYPE_FK, 0)
    saved_rot = cmds.getAttr(f'{fk_ctrl0}.rotate')[0]
    cmds.setAttr(f'{fk_ctrl0}.rotate', 0, 0, 30)

    for val in (0, 3):
        cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', val)
        # force eval
        t = cmds.currentTime(q=1)
        cmds.currentTime(t + 0.01, e=1)
        cmds.currentTime(t, e=1)

        weight = None
        if cmds.objExists(blend_mtx):
            wconn = cmds.listConnections(f'{blend_mtx}.target[0].weight', s=1, d=0) or []
            weight = cmds.getAttr(f'{blend_mtx}.target[0].weight')

        bn_pos = cmds.xform(bn_jnt, q=1, ws=1, t=1)
        ik_pos = cmds.xform(ik_jnt, q=1, ws=1, t=1)
        fk_pos = cmds.xform(fk_jnt, q=1, ws=1, t=1)
        d_ik = sum((bn_pos[j] - ik_pos[j])**2 for j in range(3)) ** 0.5
        d_fk = sum((bn_pos[j] - fk_pos[j])**2 for j in range(3)) ** 0.5
        tracking = 'IK' if d_ik < d_fk else 'FK'
        print(f'  switch={val}: blendWeight={weight}  BN tracks {tracking} '
              f'(dIK={d_ik:.3f} dFK={d_fk:.3f})')

    cmds.setAttr(f'{fk_ctrl0}.rotate', *saved_rot)
    cmds.setAttr(f'{cog_ctrl}.{ikfk_attr}', saved)
    print()


def test_matrix_opm(rigname='tail', count=0):
    print(f'\n=== MANUAL MATRIX CHECK: (COUNT: {count}) ===\n')

    for joint_idx in range(1,4):
        ik_jnt = f'IK_{rigname}_{joint_idx:02d}_jnt'
        bn_jnt = f'BN_{rigname}_{joint_idx:02d}_jnt'
        bn_parent = f'BN_{rigname}_{joint_idx-1:02d}_jnt'

        # World matrices
        ik_world = om.MMatrix(cmds.getAttr(f'{ik_jnt}.worldMatrix[0]'))
        parent_world = om.MMatrix(cmds.getAttr(f'{bn_parent}.worldMatrix[0]'))
        parent_inv = parent_world.inverse()

        # Expected local matrix (parent space)
        expected_local = parent_inv * ik_world
        # Actual offsetParentMatrix
        opm = om.MMatrix(cmds.getAttr(f'{bn_jnt}.offsetParentMatrix'))
        print_matrix(opm, f'{bn_jnt}.offsetParentMatrix')

        # Extract translations
        def t(m):
            return [m[12], m[13], m[14]]

        expected_trans = t(expected_local)
        actual_trans = t(opm)
        trans_error = sum((expected_trans[i] - actual_trans[i])**2 for i in range(3)) ** 0.5

        if trans_error > 0.001:
            print('❌ offsetParentMatrix mismatch')
            print('   Matrix wiring or bind offset is incorrect')
        else:
            print('✅ offsetParentMatrix correct')
            print('   Bind offset + parent-space math validated')
        print(f'Expected OPM translation: {[round(v, 4) for v in expected_trans]}')
        print(f'Actual   OPM translation: {[round(v, 4) for v in actual_trans]}')
        print(f'Translation error: {trans_error:.6f}\n')

def test_alignment(rigname='tail', count=0):
    '''
    Detailed position alignment check between BN and IK/FK.
    '''
    print(f'\n=== ALIGNMENT CHECK (COUNT: {count}) ===\n')

    if rigname not in rt_cst.JOINTS_BN:
        print(f'× No BN joints for {rigname}')
        return

    bn_joints = rt_cst.JOINTS_BN[rigname]
    ik_joints = rt_cst.JOINTS_IK[rigname]
    fk_joints = rt_cst.JOINTS_FK[rigname]
    opm_conn = list()

    if not ik_joints and not fk_joints:
        print('× No IK or FK joints to compare')
        return

    ref_joints = ik_joints if ik_joints else fk_joints
    ref_type = 'IK' if ik_joints else 'FK'

    print(f'Comparing BN vs {ref_type}:\n')
    max_dist = 0
    worst_idx = 0

    for i, (bn_jnt, ref_jnt) in enumerate(zip(bn_joints, ref_joints)):
        bn_pos = cmds.xform(bn_jnt, q=1, ws=1, t=1)
        ref_pos = cmds.xform(ref_jnt, q=1, ws=1, t=1)
        dist = sum((bn_pos[j] - ref_pos[j])**2 for j in range(3)) ** 0.5

        if dist > max_dist:
            max_dist = dist
            worst_idx = i

        status = '✓' if dist < 0.1 else '⚠️' if dist < 1.0 else '❌'
        print(f'  Joint {i:02d}: {status} distance = {dist:.4f}')

    print(f'\nMax misalignment: {max_dist:.4f} units at joint {worst_idx:02d}')

    if max_dist < 0.01:
        print('✅ Alignment EXCELLENT')
    elif max_dist < 0.1:
        print('⚠️  Alignment GOOD')
    else:
        print('❌ Alignment BAD')
        if count > 0:
            print(f'❌ Joints are misaligned (Count: {count})')
        else:
            print('❌ Joints are misaligned BEFORE matrix wiring!')
            print('   This means BN joints were not duplicated from IK/FK correctly.')
    print()

def show_data_flow(rigname='tail', joint_idx=0):
    '''
    Show complete data flow diagram for per-FX matrix architecture.
    '''
    header = f'''
================================================================================
          DATA FLOW DIAGRAM (Per-FX Matrix): {rigname} Joint {joint_idx:02d}
================================================================================
'''
    print(header)

    NN = joint_idx

    if rigname not in rt_cst.JOINTS_BN:
        print(f'❌ No BN joints for {rigname}')
        return

    bn_jnt = rt_cst.JOINTS_BN[rigname][joint_idx]
    ik_jnt = rt_cst.JOINTS_IK[rigname][joint_idx] if rigname in rt_cst.JOINTS_IK else None
    fk_jnt = rt_cst.JOINTS_FK[rigname][joint_idx] if rigname in rt_cst.JOINTS_FK else None

    section_header = '''NODE GRAPH:
--------------------------------------------------------------------------------
'''
    print(section_header)

    if joint_idx == 0:
        graph = f'''  {ik_jnt}.worldMatrix ────┬──> blendMatrix.inputMatrix
  {fk_jnt}.worldMatrix ────┴──> blendMatrix.target[0].targetMatrix
  cog_ctrl.ikfk_switch ───────────> blendMatrix.envelope (via condition)

                                     blendMatrix.outputMatrix
                                           │
                                           ▼
  parent.parentInverseMatrix ──────> multMatrix.matrixIn[0]
  blendMatrix.outputMatrix ────────> multMatrix.matrixIn[1]
                                           │
                                     multMatrix.matrixSum
                                           │
                                           ▼
                              {bn_jnt}.offsetParentMatrix

                   BN joint local translate/rotate = 0
                   (NO FX applied to joint 00)
'''
    else:
        graph = f'''  {ik_jnt}.worldMatrix ────┬──> blendMatrix.inputMatrix
  {fk_jnt}.worldMatrix ────┴──> blendMatrix.target[0].targetMatrix
  cog_ctrl.ikfk_switch ───────────> blendMatrix.envelope (via condition)

                                     blendMatrix.outputMatrix (baseWorld)
                                           │
                                           ▼
  curl_composeMatrix.outputMatrix ─────┬──> fxCombined_multMatrix.matrixIn[0]
  wave_composeMatrix.outputMatrix ─────┼──> fxCombined_multMatrix.matrixIn[1]
  dyn_composeMatrix.outputMatrix ──────┴──> fxCombined_multMatrix.matrixIn[2]
                                           │
                                     fxCombined_multMatrix.matrixSum (fxCombined)
                                           │
                                           ▼
  blendMatrix.outputMatrix (baseWorld) ──> baseFX_multMatrix.matrixIn[0]
  fxCombined_multMatrix.matrixSum ────────> baseFX_multMatrix.matrixIn[1]
                                           │
                                     baseFX_multMatrix.matrixSum (baseFX)
                                           │
                                           ▼
  parent.parentInverseMatrix ──────────> fxLocal_multMatrix.matrixIn[0]
  baseFX_multMatrix.matrixSum ─────────> fxLocal_multMatrix.matrixIn[1]
                                           │
                                     fxLocal_multMatrix.matrixSum (fxLocal)
                                           │
                                           ▼
                              {bn_jnt}.offsetParentMatrix

                   BN joint local translate/rotate = 0

KEY CHANGE: FX applied to baseWorld BEFORE converting to parent space
           This ensures FX rotates around joint's own pivot, not endpoint
'''

    print(graph)

    section_header = '''
VALUES:
--------------------------------------------------------------------------------'''
    print(section_header)

    blend_mtx = f'{rigname}_{NN:02d}_ikfk_blendMatrix'
    if cmds.objExists(blend_mtx):
        envelope = cmds.getAttr(f'{blend_mtx}.envelope')
        print(f'  blendMatrix.envelope = {envelope:.3f}')

    if joint_idx > 0:
        fx_list = ['curl', 'wave', 'dyn']
        for fx_name in fx_list:
            fx_compose = f'{rigname}_{NN:02d}_{fx_name}_composeMatrix'
            if cmds.objExists(fx_compose):
                print(f'\n  {fx_name}_composeMatrix:')
                for axis in ['X', 'Y', 'Z']:
                    val = cmds.getAttr(f'{fx_compose}.inputRotate{axis}')
                    if abs(val) > 0.001:
                        print(f'    inputRotate{axis} = {val:.3f}°')

    opm_conn = cmds.listConnections(f'{bn_jnt}.offsetParentMatrix', s=1, d=0, p=1)
    if opm_conn:
        print(f'\n  BN.offsetParentMatrix ← {opm_conn[0]}')
    else:
        print(f'\n  BN.offsetParentMatrix: NOT CONNECTED')

    bn_t = [round(v, 3) for v in cmds.getAttr(f'{bn_jnt}.translate')[0]]
    bn_r = [round(v, 3) for v in cmds.getAttr(f'{bn_jnt}.rotate')[0]]
    bn_w_t = [round(v, 3) for v in cmds.xform(bn_jnt, q=1, ws=1, t=1)]
    print(f'  {bn_jnt}.translate (local) = {bn_t} (should be [0,0,0])')
    print(f'  {bn_jnt}.rotate (local) = {bn_r} (should be [0,0,0])')
    print(f'  {bn_jnt}.worldTranslate = {bn_w_t}')
    print()


def test_wave(rigname='tail'):
    '''
    Quick test: Set wave attributes and check if values propagate.
    '''
    basectrl = rt_nam.fstr(rigname, BASECTRL)

    header = '''
=== QUICK WAVE TEST ==='''
    print(header)

    if not cmds.objExists(basectrl):
        print(f'✗ Base control missing: {basectrl}')
        return

    setup_msg = '''Setting wave attributes:
  waveZ = 5
  wave_frequency = 2
  wave_speed = 1'''
    print(setup_msg)

    cmds.setAttr(f'{basectrl}.noise', 0)
    cmds.setAttr(f'{basectrl}.curlX', 0)
    cmds.setAttr(f'{basectrl}.curlY', 0)
    cmds.setAttr(f'{basectrl}.curlZ', 0)
    cmds.setAttr(f'{basectrl}.waveX', 0)
    cmds.setAttr(f'{basectrl}.waveY', 0)
    cmds.setAttr(f'{basectrl}.waveZ', 5)
    cmds.setAttr(f'{basectrl}.wave_frequency', 2)
    cmds.setAttr(f'{basectrl}.wave_speed', 1)

    # Force evaluation
    cur = cmds.currentTime(q=1)
    cmds.currentTime(cur + 0.01, edit=True)
    cmds.currentTime(cur, edit=True)

    print('\nChecking wave_composeMatrix outputs:')
    for joint_idx in [2, 5, 10]:
        for axis in ['X', 'Y', 'Z']:
            compose_node = f'{rigname}_{joint_idx:02d}_wave_composeMatrix'
            if cmds.objExists(compose_node):
                val = cmds.getAttr(f'{compose_node}.inputRotate{axis}')
                if abs(val) > 0.001:
                    print(f'  Joint {joint_idx:02d} wave rotate{axis}: {val:.4f}° ✓')

    compose_node = f'{rigname}_02_wave_composeMatrix'
    if cmds.objExists(compose_node):
        val = cmds.getAttr(f'{compose_node}.inputRotateZ')
        if abs(val) > 0.001:
            print('\n✓ Wave appears to be producing output')
        else:
            error_msg = '''
✗ Wave output is zero - check expressions and time sources'''
            print(error_msg)
    else:
        print(f'\n✗ Wave composeMatrix not found: {compose_node}')
    print()


def test_curl(rigname='tail'):
    '''
    Quick test: Set curl attributes and check if values propagate.
    '''
    basectrl = rt_nam.fstr(rigname, BASECTRL)

    print('\n=== QUICK CURL TEST ===\n')

    if not cmds.objExists(basectrl):
        print(f'✗ Base control missing: {basectrl}')
        return

    print('Setting curl attributes:')
    print('  curlZ = 5')
    print('  curl_falloff = 2')

    cmds.setAttr(f'{basectrl}.noise', 0)
    cmds.setAttr(f'{basectrl}.waveX', 0)
    cmds.setAttr(f'{basectrl}.waveY', 0)
    cmds.setAttr(f'{basectrl}.waveZ', 0)
    cmds.setAttr(f'{basectrl}.curlX', 0)
    cmds.setAttr(f'{basectrl}.curlY', 0)
    cmds.setAttr(f'{basectrl}.curlZ', 5)
    cmds.setAttr(f'{basectrl}.curl_falloff', 2)

    # Force evaluation
    cmds.dgeval(basectrl)

    print('\nChecking curl_composeMatrix outputs:')
    has_output = False
    for joint_idx in [2, 5, 10]:
        compose_node = f'{rigname}_{joint_idx:02d}_curl_composeMatrix'
        if cmds.objExists(compose_node):
            val = cmds.getAttr(f'{compose_node}.inputRotateZ')
            if abs(val) > 0.001:
                print(f'  Joint {joint_idx:02d} curlZ: {val:.4f}° ✓')
                has_output = True

    if has_output:
        print('\n✓ Curl appears to be working')
    else:
        print('\n✗ Curl output is zero - check DG connections')
    print()

def decompose_mtx(m):
    if not isinstance(m, om.MMatrix):
        m = om.MMatrix(m)

    tm = om.MTransformationMatrix(m)
    t = tm.translation(om.MSpace.kWorld)
    euler = tm.rotation()
    rot = [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]
    scale = tm.scale(om.MSpace.kWorld)
    shear = tm.shear(om.MSpace.kWorld) # Edited: Added shear retrieval
    return [
        [round(v, 4) for v in (t.x, t.y, t.z)],
        [round(v, 4) for v in rot],
        [round(v, 4) for v in scale],
        [round(v, 4) for v in shear]
    ]

def print_matrix(data, name=''):
    print(f"-- {name} --")
    rows = []

    # Check if input is a 4x4 MMatrix or a flat list of 16
    if isinstance(data, om.MMatrix) or (isinstance(data, list) and len(data) == 16):
        m = om.MMatrix(data)
        for r in range(4):
            # Edited: Extract rows from 4x4 matrix
            rows.append([round(m[r*4 + c], 4) for c in range(4)])

    # Check if input is decomposed result (list of 3 or 4 components)
    elif isinstance(data, list) and len(data) in (3, 4):
        rows = data # Edited: Use the sub-lists (T, R, S, Sh) directly

    for row in rows:
        # Edited: Alignment for 4 decimal places
        formatted_row = '  '.join([f"{v:10.4f}" for v in row])
        print(f"[ {formatted_row} ]")

def print_chain(rigname='tail', joint_idx=6, count=0):
    '''
    Deep matrix-level diagnostic for a contiguous section of the BN / IK chain.
    Prints detailed information for joints 0 through joint_idx:
      - offsetParentMatrix (fully decomposed)
      - parent.worldMatrix
      - BN.worldMatrix and IK.worldMatrix
      - Incoming matrix connections
      - Local TRS, jointOrient, and inheritsTransform

    Intended use:
      - Debugging incorrect offsetParentMatrix math
      - Verifying parent-space conversion and matrix order
      - Tracing where rotation, scale, or shear is introduced
      - Validating matrix architecture during rig development

    Use dump_chain or print_chain_ends for quick checks.
    '''
    bn_joints = rt_cst.JOINTS_BN[rigname]
    ik_joints = rt_cst.JOINTS_IK[rigname] if rigname in rt_cst.JOINTS_IK else []
    N = min(joint_idx, len(bn_joints) - 1, len(ik_joints) - 1)

    print(f'--- PRINT CHAIN (CONDENSED) for first {N+1} joints (COUNT: {count}) ---')

    # helpers for gating
    def is_identity_trs(trs):
        t, r, s, sh = trs
        return (
            all(abs(v) < 0.001 for v in t) and
            all(abs(v) < 0.001 for v in r) and
            all(abs(v - 1.0) < 0.001 for v in s) and
            all(abs(v) < 0.001 for v in sh)
        )

    def fmt(label, trs, show_scale=False):
        t, r, s, sh = trs
        parts = [f'T={t}', f'R={r}']
        if show_scale and not all(abs(v - 1.0) < 0.001 for v in s):
            parts.append(f'S={s}')
        if not all(abs(v) < 0.001 for v in sh):
            parts.append(f'Sh={sh}')
        return f'  {label:<10}: ' + '  '.join(parts)

    for i in range(0, N + 1):
        bn = bn_joints[i]
        ik = ik_joints[i] if i < len(ik_joints) else None
        parent = cmds.listRelatives(bn, p=True, f=True)
        parent = parent[0] if parent else None

        print(f'\nJoint index {i:02d}: BN="{bn}" IK="{ik}" parent="{parent}"')
        print('-- MATRICES --')

        # BN offsetParentMatrix
        bn_opm = om.MMatrix(cmds.getAttr(f'{bn}.offsetParentMatrix'))
        bn_opm_trs = decompose_mtx(bn_opm)
        print(fmt('BN.OPM', bn_opm_trs, show_scale=True))

        # parent worldMatrix (skip for root)
        if parent:
            parent_world = om.MMatrix(cmds.getAttr(f'{parent}.worldMatrix[0]'))
            pw_trs = decompose_mtx(parent_world)
            if not is_identity_trs(pw_trs):
                print(fmt('PARENT.WLD', pw_trs))

        # BN worldMatrix
        bn_world = om.MMatrix(cmds.getAttr(f'{bn}.worldMatrix[0]'))
        bw_trs = decompose_mtx(bn_world)
        print(fmt('BN.WORLD', bw_trs))

        # IK worldMatrix
        if ik:
            ik_world = om.MMatrix(cmds.getAttr(f'{ik}.worldMatrix[0]'))
            iw_trs = decompose_mtx(ik_world)
            print(fmt('IK.WORLD', iw_trs))

        # connections
        print('-- Connections --')
        print('offsetParentMatrix inputs:',
              cmds.listConnections(f'{bn}.offsetParentMatrix', s=1, d=0, p=1))
        print('worldMatrix inputs:',
              cmds.listConnections(f'{bn}.worldMatrix[0]', s=1, d=0, p=1))

        # local TRS and jointOrient
        tx = cmds.getAttr(f'{bn}.translate')[0]
        rt = cmds.getAttr(f'{bn}.rotate')[0]
        sc = cmds.getAttr(f'{bn}.scale')[0]
        jo = cmds.getAttr(f'{bn}.jointOrient')[0]
        inh = cmds.getAttr(f'{bn}.inheritsTransform')

        print('-- Local TRS/jointOrient/inheritsTransform --')
        print('translate', tx, 'rotate', rt, 'scale', sc,
              'jointOrient', jo, 'inherits', inh)

    print(f'\n--- end print (COUNT: {count}) ---\n')

def print_chain_ends(rigname='tail', count=0):
    '''
    Targeted matrix diagnostic for the structural ends of the chain.

    Prints the same detailed information as print_chain, but only for:
      - The first two joints (root stability, bind correctness)
      - The last five joints (FX accumulation, falloff, tip behavior)

    Intended use:
      - Debugging issues that only appear at the root or tip
      - Checking FX buildup without mid-chain noise
      - Faster iteration during animation FX tuning

    This is a focused alternative to print_chain,
    not a replacement for full-chain inspection.
    '''
    bn_joints = rt_cst.JOINTS_BN[rigname]
    ik_joints = rt_cst.JOINTS_IK[rigname] if rigname in rt_cst.JOINTS_IK else []

    total = len(bn_joints)

    # first two + last five, clamped and de-duplicated
    indices = list(range(0, min(2, total))) + list(range(max(0, total - 5), total))
    indices = sorted(set(indices))

    print(f'--- PRINT CHAIN ENDS (COUNT: {count}) ---')
    print(f'Printing joints: {indices}\n')

    for i in indices:
        bn = bn_joints[i]
        ik = ik_joints[i] if i < len(ik_joints) else None
        parent = cmds.listRelatives(bn, p=True, f=True)
        parent = parent[0] if parent else None

        print(f'\nJoint index {i:02d}: BN="{bn}" IK="{ik}" parent="{parent}"')

        # BN offsetParentMatrix
        try:
            bn_opm_list = cmds.getAttr(f'{bn}.offsetParentMatrix')
            bn_opm = om.MMatrix(bn_opm_list)
            print_matrix(decompose_mtx(bn_opm), 'BN.offsetParentMatrix')
        except Exception as e:
            print('  could not read BN.offsetParentMatrix:', e)

        # parent worldMatrix
        try:
            if parent:
                parent_world = om.MMatrix(cmds.getAttr(f'{parent}.worldMatrix[0]'))
                print_matrix(decompose_mtx(parent_world), 'parent.worldMatrix')
        except Exception as e:
            print('  could not read parent.worldMatrix:', e)

        # BN worldMatrix
        try:
            bn_world = om.MMatrix(cmds.getAttr(f'{bn}.worldMatrix[0]'))
            print_matrix(decompose_mtx(bn_world), 'BN.worldMatrix')
        except Exception as e:
            print('  could not read BN.worldMatrix:', e)

        # IK worldMatrix
        if ik:
            try:
                ik_world = om.MMatrix(cmds.getAttr(f'{ik}.worldMatrix[0]'))
                print_matrix(decompose_mtx(ik_world), 'IK.worldMatrix')
            except Exception as e:
                print('  could not read IK.worldMatrix:', e)

        # connections
        print('-- Connections --')
        try:
            opl = cmds.listConnections(f'{bn}.offsetParentMatrix', s=1, d=0, p=1)
            print('offsetParentMatrix inputs:', opl)
        except:
            print('offsetParentMatrix inputs: <error>')

        try:
            wml = cmds.listConnections(f'{bn}.worldMatrix[0]', s=1, d=0, p=1)
            print('worldMatrix inputs:', wml)
        except:
            print('worldMatrix inputs: <error>')

        # local TRS + jointOrient
        try:
            tx = cmds.getAttr(f'{bn}.translate')[0]
            rt = cmds.getAttr(f'{bn}.rotate')[0]
            sc = cmds.getAttr(f'{bn}.scale')[0]
            jo = cmds.getAttr(f'{bn}.jointOrient')[0]
            inh = cmds.getAttr(f'{bn}.inheritsTransform')
            print('-- Local TRS/jointOrient/inheritsTransform --')
            print('translate', tx, 'rotate', rt, 'scale', sc, 'jointOrient', jo, 'inherits', inh)
        except Exception as e:
            print('  could not read local TRS/jointOrient:', e)

    print(f'\n--- end print (COUNT: {count}) ---\n')

def dump_chain(rigname='tail'):
    '''
    Lightweight joint-orientation and bind-pose sanity dump.

    Prints per-joint:
      - BN and IK jointOrient
      - BN and IK local rotation (object space)
      - A small raw slice of offsetParentMatrix

    Intended use:
      - Verifying BN joints were duplicated correctly from IK/FK
      - Spotting unexpected local rotations or jointOrient mismatches
      - Quick validation before deeper matrix debugging

    This function does NOT inspect:
      - Full world matrices
      - Parent-space math
      - Matrix wiring or connections
      - Local translate, scale, or inheritsTransform

    Use print_chain when matrix math or OPM wiring is suspect.
    '''
    bn_joints = rt_cst.JOINTS_BN[rigname]
    ik_joints = rt_cst.JOINTS_IK[rigname]
    for i, bn in enumerate(bn_joints):
        ik = ik_joints[i]
        bjo = cmds.getAttr(f'{bn}.jointOrient')[0] if cmds.attributeQuery('jointOrient', node=bn, exists=True) else (0,0,0)
        kjo = cmds.getAttr(f'{ik}.jointOrient')[0] if cmds.attributeQuery('jointOrient', node=ik, exists=True) else (0,0,0)
        b_local = cmds.xform(bn, q=1, ro=1, ws=0)
        i_local = cmds.xform(ik, q=1, ro=1, ws=0)
        opm = cmds.getAttr(f'{bn}.offsetParentMatrix')
        print(i, bn, 'JO', bjo, 'IK.JO', kjo, 'BN_local_rot', b_local, 'IK_local_rot', i_local, 'opm', opm[0:4])

def test_time_evaluation(rigname='tail'):
    '''
    Comprehensive test for time-dependent expression evaluation.
    Tests wave, noise, and loop modulo system.
    '''
    header = '''
================================================================================
                        TIME EVALUATION DIAGNOSTIC
================================================================================
'''
    print(header)

    basectrl = rt_nam.fstr(rigname, BASECTRL)
    if not cmds.objExists(basectrl):
        print(f'✗ Base control missing: {basectrl}')
        return

    joints = rt_cst.JOINTS_BN.get(rigname, [])
    if len(joints) < 3:
        print('✗ Not enough joints for time test')
        return

    issues = []
    warnings = []

    # === EXPRESSION EXISTENCE CHECK ===
    section_header = '''EXPRESSION NODES:
--------------------------------------------------------------------------------'''
    print(section_header)

    test_joint = 2
    NN = rt_nam.get_index_from_name(joints[test_joint])

    wave_expressions = []
    noise_expressions = []
    loop_expression = f'{rigname}_loop_time_expression'

    for axis in ['X', 'Y', 'Z']:
        wave_expr = f'{rigname}_{NN:02d}_wave{axis}_expression'
        noise_expr = f'{rigname}_{NN:02d}_noise_{axis}_expression'

        if cmds.objExists(wave_expr):
            wave_expressions.append(wave_expr)
            print(f'  ✓ Found wave expression: {wave_expr}')
        else:
            issues.append(f'Missing wave expression: {wave_expr}')

        if cmds.objExists(noise_expr):
            noise_expressions.append(noise_expr)
            print(f'  ✓ Found noise expression: {noise_expr}')
        else:
            issues.append(f'Missing noise expression: {noise_expr}')

    if cmds.objExists(loop_expression):
        print(f'  ✓ Found loop expression: {loop_expression}')
    else:
        warnings.append(f'Loop expression not found: {loop_expression}')

    print()

    # === TIME DEPENDENCY CHECK ===
    section_header = '''TIME DEPENDENCY CHECK:
--------------------------------------------------------------------------------'''
    print(section_header)

    for expr in wave_expressions + noise_expressions:
        expr_code = cmds.expression(expr, q=1, s=1)

        # Check if expression references time
        has_time_ref = 'time1.outTime' in expr_code or '$t' in expr_code

        if has_time_ref:
            print(f'  ✓ {expr}: References time source')
        else:
            issues.append(f'{expr}: Does NOT reference time')

        # Check for stale conditionals (shouldn't have any)
        if 'if ($i ==' in expr_code:
            issues.append(f'{expr}: Contains conditional writes (OLD ARCHITECTURE)')

    print()

    # === SETUP TEST ATTRIBUTES ===
    section_header = '''SETTING UP TEST:
--------------------------------------------------------------------------------'''
    print(section_header)

    # Clear all effects
    cmds.setAttr(f'{basectrl}.waveX', 0)
    cmds.setAttr(f'{basectrl}.waveY', 0)
    cmds.setAttr(f'{basectrl}.waveZ', 0)
    cmds.setAttr(f'{basectrl}.curlX', 0)
    cmds.setAttr(f'{basectrl}.curlY', 0)
    cmds.setAttr(f'{basectrl}.curlZ', 0)
    cmds.setAttr(f'{basectrl}.noise', 0)

    # Set wave attributes
    cmds.setAttr(f'{basectrl}.waveZ', 5.0)
    cmds.setAttr(f'{basectrl}.wave_frequency', 2.0)
    cmds.setAttr(f'{basectrl}.wave_speed', 1.0)
    cmds.setAttr(f'{basectrl}.wave_falloff', 1.0)

    # Set noise attribute
    cmds.setAttr(f'{basectrl}.noise', 3.0)

    print('  Wave Z amplitude: 5.0')
    print('  Noise amplitude: 3.0')
    print('  Testing at frames: 1, 10, 20')
    print()

    # === TIME-BASED VALUE CHECK ===
    section_header = '''TIME-BASED VALUE CHANGES:
--------------------------------------------------------------------------------'''
    print(section_header)

    test_frames = [1, 10, 20]
    compose_node_wave = f'{rigname}_{NN:02d}_wave_composeMatrix'
    compose_node_noise = f'{rigname}_{NN:02d}_noise_composeMatrix'

    if not cmds.objExists(compose_node_wave):
        issues.append(f'Wave composeMatrix missing: {compose_node_wave}')
    if not cmds.objExists(compose_node_noise):
        issues.append(f'Noise composeMatrix missing: {compose_node_noise}')

    wave_values = []
    noise_values = []

    for frame in test_frames:
        cmds.currentTime(frame, edit=True)

        # Force DG evaluation
        cmds.dgeval(compose_node_wave, compose_node_noise)

        wave_z = cmds.getAttr(f'{compose_node_wave}.inputRotateZ')
        noise_x = cmds.getAttr(f'{compose_node_noise}.inputRotateX')

        wave_values.append(wave_z)
        noise_values.append(noise_x)

        print(f'  Frame {frame:3d}: Wave Z = {wave_z:8.4f}°  |  Noise X = {noise_x:8.4f}°')

    print()

    # Check if values are changing
    wave_range = max(wave_values) - min(wave_values)
    noise_range = max(noise_values) - min(noise_values)

    print(f'  Wave Z range: {wave_range:.4f}°')
    print(f'  Noise X range: {noise_range:.4f}°')
    print()

    if wave_range < 0.001:
        issues.append('Wave values NOT CHANGING over time (time evaluation BROKEN)')
    else:
        print('  ✓ Wave values changing with time')

    if noise_range < 0.001:
        issues.append('Noise values NOT CHANGING over time (time evaluation BROKEN)')
    else:
        print('  ✓ Noise values changing with time')

    print()

    # === LOOP MODULO TEST ===
    if cmds.objExists(loop_expression):
        section_header = '''LOOP MODULO TEST:
--------------------------------------------------------------------------------'''
        print(section_header)

        # Test with loop disabled
        cmds.setAttr(f'{basectrl}.loop', 0)
        cmds.setAttr(f'{basectrl}.loop_frame', 100)

        cmds.currentTime(1, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_1 = cmds.getAttr(f'{loop_expression}.output[0]')

        cmds.currentTime(150, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_150 = cmds.getAttr(f'{loop_expression}.output[0]')

        print(f'  Loop DISABLED:')
        print(f'    Frame 1   → loop time = {loop_val_1:.2f}')
        print(f'    Frame 150 → loop time = {loop_val_150:.2f}')

        if abs(loop_val_1 - 1) < 0.1 and abs(loop_val_150 - 150) < 0.1:
            print('    ✓ Pass-through working (time = frame)')
        else:
            warnings.append('Loop pass-through values unexpected')

        # Test with loop enabled
        cmds.setAttr(f'{basectrl}.loop', 1)

        cmds.currentTime(50, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_50 = cmds.getAttr(f'{loop_expression}.output[0]')

        cmds.currentTime(150, edit=True)
        cmds.dgeval(loop_expression)
        loop_val_150_mod = cmds.getAttr(f'{loop_expression}.output[0]')

        print(f'\n  Loop ENABLED (loop_frame = 100):')
        print(f'    Frame 50  → loop time = {loop_val_50:.2f}')
        print(f'    Frame 150 → loop time = {loop_val_150_mod:.2f} (should be ~50)')

        if abs(loop_val_150_mod - 50) < 1.0:
            print('    ✓ Modulo working correctly')
        else:
            issues.append(f'Loop modulo BROKEN: expected ~50, got {loop_val_150_mod:.2f}')

        print()

    # === EXPRESSION CODE INSPECTION ===
    section_header = '''EXPRESSION CODE INSPECTION:
--------------------------------------------------------------------------------'''
    print(section_header)

    sample_expr = wave_expressions[0] if wave_expressions else None
    if sample_expr:
        expr_code = cmds.expression(sample_expr, q=1, s=1)
        print(f'Sample expression code ({sample_expr}):')
        print()
        for line in expr_code.split('\n')[:10]:
            print(f'  {line}')
        if expr_code.count('\n') > 10:
            print('  ...')
        print()

    # === FINAL REPORT ===
    section_header = '''DIAGNOSIS:
--------------------------------------------------------------------------------'''
    print(section_header)

    if issues:
        print('✗ CRITICAL ISSUES FOUND:')
        for issue in issues:
            print(f'  • {issue}')
        print()

    if warnings:
        print('⚠️  WARNINGS:')
        for warning in warnings:
            print(f'  • {warning}')
        print()

    if not issues and not warnings:
        print('✓ Time evaluation is working correctly!')
        print()
    elif issues:
        print('TROUBLESHOOTING:')
        print('  1. Delete and rebuild animation effects:')
        print('     → This recreates expressions with fresh time dependencies')
        print('  2. Check expression code for stale conditionals')
        print('  3. Verify expressions use "time1.outTime" not "frame"')
        print('  4. Try: cmds.delete(expression_node) before rebuilding')
        print()

    # Reset timeline
    cmds.currentTime(1, edit=True)


def fix_expression_time_dependency(rigname='tail'):
    '''
    Emergency fix: Delete and recreate all time-dependent expressions.
    This ensures clean time dependency flags.
    '''
    print('\n=== FIXING EXPRESSION TIME DEPENDENCIES ===\n')

    joints = rt_cst.JOINTS_BN.get(rigname, [])
    if len(joints) < 2:
        print('✗ No joints found')
        return

    deleted_count = 0

    # Delete all wave expressions
    # Use delete_expression(): raw cmds.delete on a connected expression
    # cascades through its whole connection web
    for jnt in joints[1:]:
        NN = rt_nam.get_index_from_name(jnt)
        for axis in ['X', 'Y', 'Z']:
            expr = f'{rigname}_{NN:02d}_wave{axis}_expression'
            if cmds.objExists(expr):
                rt_ani.delete_expression(expr)
                deleted_count += 1
                print(f'  Deleted: {expr}')

    # Delete all noise expressions
    for jnt in joints[1:]:
        NN = rt_nam.get_index_from_name(jnt)
        for axis in ['X', 'Y', 'Z']:
            expr = f'{rigname}_{NN:02d}_noise_{axis}_expression'
            if cmds.objExists(expr):
                rt_ani.delete_expression(expr)
                deleted_count += 1
                print(f'  Deleted: {expr}')

    # Delete loop expression
    loop_expr = f'{rigname}_loop_time_expression'
    if cmds.objExists(loop_expr):
        rt_ani.delete_expression(loop_expr)
        deleted_count += 1
        print(f'  Deleted: {loop_expr}')

    print(f'\n✓ Deleted {deleted_count} expression nodes')
    print('\nNow rebuild animation effects:')
    print('  import rig_tail_anim as rt_ani')
    print(f'  rt_ani.build_anim_effects("{rigname}", fk=True, ik=True)')
    print()


def check_expression_flags(rigname='tail'):
    '''
    Check if expressions have proper time dependency flags.
    '''
    print('\n=== EXPRESSION FLAGS CHECK ===\n')

    joints = rt_cst.JOINTS_BN.get(rigname, [])
    if len(joints) < 3:
        print('✗ Not enough joints')
        return

    # Check time1.outTime directly
    print('DIRECT TIME CHECK:')
    for frame in [1, 10, 20]:
        cmds.currentTime(frame, edit=True)
        time_val = cmds.getAttr('time1.outTime')
        print(f'  Frame {frame}: time1.outTime = {time_val}')
    print()

    test_joint = 2
    NN = rt_nam.get_index_from_name(joints[test_joint])

    expressions = []

    for axis in ['X', 'Y', 'Z']:
        expr = f'{rigname}_{NN:02d}_wave{axis}_expression'
        if cmds.objExists(expr):
            expressions.append(expr)

    loop_expr = f'{rigname}_loop_time_expression'
    if cmds.objExists(loop_expr):
        expressions.append(loop_expr)

    if not expressions:
        print('✗ No expressions found')
        return

    print('EXPRESSION NODES:')
    for expr in expressions:
        # Check expression string
        expr_code = cmds.expression(expr, q=1, s=1)

        # Check for time reference
        has_time = 'time1.outTime' in expr_code or '$t =' in expr_code

        # Check connections
        connections = cmds.listConnections(expr, s=1, d=0, p=1) or []
        has_time_conn = any('time1' in str(conn) for conn in connections)

        print(f'{expr}:')
        print(f'  References time in code: {"✓" if has_time else "✗"}')
        print(f'  Connected to time1: {"✓" if has_time_conn else "✗"}')

        # Try to get node type info
        try:
            node_type = cmds.nodeType(expr)
            print(f'  Node type: {node_type}')
        except:
            print(f'  Node type: <error>')

        # Check if it has output attributes
        if 'loop' in expr:
            try:
                output_val = cmds.getAttr(f'{expr}.output[0]')
                print(f'  Current output[0]: {output_val}')
            except:
                print(f'  No output[0] attribute')

        print()

def test_joint_orient(rigname='tail', joints_bn=None, joints_ik=None, count=10):
    if joints_bn is None:
        joints_bn = rt_cst.JOINTS_BN.get(rigname, [])
    if joints_ik is None:
        joints_ik = rt_cst.JOINTS_IK.get(rigname, [])

    print('--- Joint Orient Diagnostic for', rigname, '---')
    for i, bn in enumerate(joints_bn[:count]):
        ik = joints_ik[i] if i < len(joints_ik) else None
        parent = cmds.listRelatives(ik or bn, p=True, f=True)
        parent = parent[0] if parent else None

        bn_world = cmds.xform(bn, q=1, ws=1, m=1)
        bn_m = om.MMatrix(bn_world)

        if ik:
            ik_world = cmds.xform(ik, q=1, ws=1, m=1)
            ik_m = om.MMatrix(ik_world)
        else:
            ik_m = None

        if parent:
            parent_inv = cmds.getAttr(f'{parent}.worldInverseMatrix[0]')
            parent_inv_m = om.MMatrix(parent_inv)
            bn_local = parent_inv_m * bn_m
            ik_local = parent_inv_m * ik_m if ik_m else None
        else:
            bn_local = bn_m
            ik_local = ik_m

        bn_tr = om.MTransformationMatrix(bn_local)
        bn_rot_q = bn_tr.rotation(asQuaternion=True)
        bn_euler = bn_tr.rotation()  # use Euler for readable degrees

        if ik_local:
            ik_tr = om.MTransformationMatrix(ik_local)
            ik_rot_q = ik_tr.rotation(asQuaternion=True)
            ik_euler = ik_tr.rotation()
        else:
            ik_rot_q = None
            ik_euler = None

        # jointOrients:
        bn_jo = cmds.getAttr(f'{bn}.jointOrient')[0] if cmds.attributeQuery('jointOrient', node=bn, exists=True) else (0,0,0)
        ik_jo = cmds.getAttr(f'{ik}.jointOrient')[0] if ik and cmds.attributeQuery('jointOrient', node=ik, exists=True) else (0,0,0)

        bn_jo_m = om.MEulerRotation(
            om.MAngle(bn_jo[0], om.MAngle.kDegrees).asRadians(),
            om.MAngle(bn_jo[1], om.MAngle.kDegrees).asRadians(),
            om.MAngle(bn_jo[2], om.MAngle.kDegrees).asRadians()
        ).asQuaternion()

        ik_jo_m = om.MEulerRotation(
            om.MAngle(ik_jo[0], om.MAngle.kDegrees).asRadians(),
            om.MAngle(ik_jo[1], om.MAngle.kDegrees).asRadians(),
            om.MAngle(ik_jo[2], om.MAngle.kDegrees).asRadians()
        ).asQuaternion() if ik else None

        # compute candidate axis quats:
        if ik_jo_m:
            axis_old = bn_jo_m * ik_jo_m.inverse()   # OLD formula
            axis_new = bn_jo_m.inverse() * ik_jo_m   # NEW (bug) formula
        else:
            axis_old = None
            axis_new = None

        # offsetParentMatrix check
        opm = cmds.getAttr(f'{bn}.offsetParentMatrix') if cmds.attributeQuery('offsetParentMatrix', node=bn, exists=True) else None
        opm_incoming = cmds.listConnections(f'{bn}.offsetParentMatrix', s=True, d=False) or []

        print('index %02d :' % i, 'BN="%s" IK="%s"' % (bn, ik))
        print('  BN.jointOrient:', bn_jo)
        print('  IK.jointOrient:', ik_jo)
        print('  BN local euler (deg):', [om.MAngle(v).asDegrees() for v in bn_euler])
        if ik_euler:
            print('  IK local euler (deg):', [om.MAngle(v).asDegrees() for v in ik_euler])
        if axis_old:
            ao = axis_old.asEulerRotation()
            an = axis_new.asEulerRotation()
            print('  axis_old (euler deg):', [om.MAngle(v).asDegrees() for v in ao])
            print('  axis_new (euler deg):', [om.MAngle(v).asDegrees() for v in an])
        print('  offsetParentMatrix has incoming connections:', bool(opm_incoming), 'incoming nodes:', opm_incoming)
        print('  offsetParentMatrix raw first row:', opm[0:4] if opm else None)
        print('  worldMatrix inputs for BN:', cmds.listConnections(f'{bn}.worldMatrix', s=True, d=False) or [])
        print('  ---------------------------')
    print('--- end diagnostic ---')
