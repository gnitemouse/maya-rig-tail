'''
rig_tail_cleanup.py
author: Daisy Jane @gnitemouse

Teardown of a previous rig and preparation of the scene structure, run at
the start of every build, before rig_tail builds the components. Not to
be confused with rig_tail_setup, which owns the optional Setup phase
(joint orient and mirror) and does not run during a build.

Cleanup picks one of two paths per rig part, chosen from the joint cache:
    full teardown (cleanup_rigname): joints changed or a forced rebuild;
        deletes controls, curves, clusters, FX and utility nodes.
    light path (cleanup_connections): joints unchanged; only breaks
        connections and keeps the nodes for reuse.
After cleanup, setup_rig creates the rig root, cog and hierarchy groups,
and the joint helpers detect or (re)build the BN/FK/IK chains for RIGPARTS.

Functions:
    cleanup_rig: entry point; per part choose full vs light teardown
    remove_rig: strip the rig back to bare skeleton + geometry
    unique_path: resolve a name to one unambiguous full DAG path
    _rescue_from_root: move skeleton/geometry out before deleting the root
    _sweep_rig_leftovers: delete orphaned utility/anim nodes of a rig part
    rig_leftovers: report rig nodes still in the scene after a removal
    cleanup_rigname: full teardown of one rig part
    cleanup_connections: light teardown, break connections only
    cleanup_anim_effects: remove one part's FX expression/node network
    cleanup_disabled_effects: remove the networks of the effects now off
    excluded_sdk_curves: SDK curves the scene-wide sweep must spare
    cleanup_dangling_unit_conversions: sweep orphaned conversion nodes
    cleanup_dangling_curveinfo: sweep curveInfo nodes with no input curve
    restore_fk_joint_chain: unwrap FK SDK stack back to a flat chain
    fk_sdk_structure_is_current: is the FK SDK layout the current one
    setup_rig: create root/cog and the rig hierarchy groups
    set_root: set ROOT and reconcile the scene root group
    find_existing_root_grp: locate the current rig root group
    set_joints_auto: detect and (re)build BN/FK/IK chains for RIGPARTS
    set_joints: detect/build the chains for one rig part
    _bn_start_finder: BN start-joint lookup sharing one scene scan
    bn_start_candidates: every BN chain start per rig part, for duplicates
    duplicate_rigparts: rig parts matched by more than one chain
    detect_joints_bn: fill JOINTS_BN by chain detection only (Setup phase)
    create_rename_joints: rename BN in place, duplicate FK/IK from it
    rigpart_has_joints: does the scene hold BN joints for a rig part
    rename_rigpart: rename a rig part in place across scene and caches
    rename_components: apply legacy node-name migrations
'''

import fnmatch
import math
import re
import maya.cmds as cmds
import maya.api.OpenMaya as om
from logger_config import logger_setup, abort_build
import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_joint as rt_joint
import rig_tail_cache as rt_cache
import rig_tail_control as rt_control
import rig_tail_connect as rt_connect
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_mirror as rt_mirror

logger = logger_setup(__name__)

# The conversion sweep is scene-wide, so running it per rig part would
# re-walk every conversion node in the scene to find the same orphans each
# time. While cleanup_rig holds it deferred, callers only record that one is
# due and it runs once at the end; a standalone caller still sweeps
# immediately, so the helper is safe to call on its own.
_DEFER_CONVERSION_SWEEP = False
_CONVERSION_SWEEP_PENDING = False

# The utility node types the build creates and a teardown deletes. Serves
# two purposes at once: the TYPE filter for cmds.ls, and the set of name
# suffixes these nodes carry - the build names each one for its own type, so
# one typed scan plus a name test replaces a wildcard pattern per type per
# rig part (see cleanup_rigname).
UTILITY_NODE_TYPES = ['condition', 'multiplyDivide', 'plusMinusAverage',
                      'multDoubleLinear', 'pointMatrixMult', 'blendTwoAttr',
                      'clamp', 'setRange', 'choice', 'curveInfo',
                      'pointOnCurveInfo', 'remapValue', 'aimMatrix']

# Node types Maya has RENAMED, in the build's spelling -> the current one.
# createNode still accepts the old spelling (it warns and substitutes), but
# cmds.ls(type=<old spelling>) matches nothing - so on Maya 2026+ the build
# goes on making these nodes while every typed scan here is blind to them.
NODE_TYPE_ALIASES = {
    'multDoubleLinear': 'multDL',
    'addDoubleLinear': 'addDL',
    'pointMatrixMult': 'pointMatrixMultDL',
}


def resolve_node_types(types):
    '''
    A node type list the RUNNING Maya's cmds.ls will actually answer for.

    Each name is kept if this Maya knows it and swapped for its alias if not
    (NODE_TYPE_ALIASES), so one list serves every Maya version. Only ever
    one spelling at a time: cmds.ls warns rather than raises on a type it
    does not know, which would be a warning per scan per rig part.

    A name neither spelling covers is dropped - that is a type from a plugin
    which never loaded, matrixNodes or quatNodes - so it never reaches
    _ls_types' fallback.

    allNodeTypes is asked per call rather than cached, because a plugin can
    load part way through a session and a cached answer would go stale.

    Arguments
        types (list): Node type names, in the build's own spelling

    Return
        list: the names to hand cmds.ls, deduped, order preserved
    '''
    known = set(cmds.allNodeTypes() or [])
    resolved = []
    for typ in types:
        if typ in known:
            resolved.append(typ)
        alias = NODE_TYPE_ALIASES.get(typ)
        if alias and alias in known:
            resolved.append(alias)
    return list(dict.fromkeys(resolved))


def fx_expression_patterns(rigname):
    '''
    The FX expression nodes, which no teardown can usefully keep: build_loop,
    build_wave and build_noise each rebuild their expression from scratch
    whatever they find.

    Shared by both teardown paths. The light one takes ONLY these and leaves
    the rest of the FX network standing, since build_curl reuses its nodes in
    place.

    The wave and noise patterns are loose enough to catch every naming
    spelling a scene may be carrying, not only the one in use.

    Arguments
        rigname (str): Name of rig component

    Return
        list: name patterns, matched against a node's leaf name
    '''
    return [
        f'{rigname}_*wave*_expression',
        f'{rigname}_*noise*_expression',
        f'{rigname}_loop_time_expression',
    ]


def fx_effect_patterns(rigname, effect):
    '''
    The nodes ONE animation effect owns, matched against a leaf name.

    Only the four rig_tail_anim builds. rt_constants.EFFECTS also carries
    'stretchy', whose squash network belongs to rig_tail_stretch and comes
    down with the rest of the stretch rig - sweeping half of it from here
    would hand the build a network nobody had finished removing.

    Arguments
        rigname (str): Name of rig component
        effect (str): Key from rt_constants.EFFECTS

    Return
        list: name patterns, empty for an effect owning no network here
    '''
    typ = rt_constants.TYPE_FX
    return {
        'wave': [f'{rigname}_*wave*_expression',
                 f'{rigname}_*_wave_composeMatrix',
                 f'{rigname}_wave_*',
                 f'{typ}_{rigname}_wave_*'],
        'noise': [f'{rigname}_*noise*_expression',
                  f'{rigname}_*_noise_composeMatrix'],
        'curl': [f'{rigname}_curl*_multiplyDivide',
                 f'{rigname}_curl*_plusMinusAverage',
                 f'{rigname}_curl*_clamp',
                 f'{rigname}_*_curl_composeMatrix',
                 f'{typ}_{rigname}_curl_*'],
        'loop': [f'{rigname}_loop_time_expression',
                 f'{rigname}_loop_time',
                 f'{typ}_{rigname}_loop_*'],
    }.get(effect, [])


def cleanup_disabled_effects(rigname):
    '''
    Delete the networks of the animation effects that are switched OFF.

    Keeping the FX network is right for an effect still enabled, which the
    build meets and reuses, and wrong for one switched off: nothing
    rebuilds it and nothing else deletes it, so it sits in the scene
    evaluating and unchecking the effect buys no playback back. An
    expression is the expensive case, being built to always evaluate - it
    costs a frame's work whether or not anything reads its output.

    Read off the CURRENT settings rather than a remembered previous build.
    The scene outlives the session: LAST_BUILD is empty after a restart
    while the nodes are still there, so a comparison would find nothing to
    do in exactly the case that needs doing.

    Arguments
        rigname (str): Name of rig component

    Return
        int: nodes deleted
    '''
    patterns = []
    for effect in ('wave', 'curl', 'noise', 'loop'):
        if not rt_constants.EFFECTS.get(effect):
            patterns += fx_effect_patterns(rigname, effect)
    if not patterns:
        return 0

    # One scene scan however many patterns, then match in Python where a
    # name test is free (see cleanup_anim_effects)
    typ = rt_constants.TYPE_FX
    candidates = cmds.ls(f'{rigname}_*', f'{typ}_{rigname}_*') or []
    nodes = [n for n in dict.fromkeys(candidates)
             if any(fnmatch.fnmatchcase(n.split('|')[-1], p)
                    for p in patterns)]
    if not nodes:
        return 0

    # Expressions first, and as one batch: a delete cascades through a
    # connected expression's web (see cleanup_anim_effects)
    expressions = [n for n in nodes if cmds.nodeType(n) == 'expression']
    removed = rt_maya.remove_nodes(expressions)
    removed += rt_maya.remove_nodes([n for n in nodes
                                     if n not in set(expressions)])
    cleanup_dangling_unit_conversions()

    logger.debug(f'{rigname}: {removed} node(s) removed for disabled effects')
    return removed


# CLEANUP ==============================================================

def cleanup_rig(fk, ik):
    '''
    Clear the way for a rebuild, across every rig part being built
    (rig_tail_cache.active_parts: RIGPARTS minus RIGPARTS_EXCLUDE). Runs
    from build_rig_tail before anything is created.

    Each part goes down one of two paths, chosen from the caches:

        cleanup_rigname     - full: delete and let the build recreate.
                              Taken when the skeleton or the node layout
                              changed, or a rebuild was forced.
        cleanup_connections - light: keep the nodes, cut their drivers.
                              Taken when both caches say nothing moved.

    A full teardown always strips BOTH modes, not only the ones being
    rebuilt: a previous build may have left the other mode's curves,
    clusters and spline handles behind, and the build recreates only what
    was asked for.

    Two things are hoisted out of the per-part loop because they are
    scene-wide and would otherwise repeat identical work per part: the typed
    scan of utility nodes, which every part filters for its own names, and
    the conversion sweep, held deferred until the end.

    An EXCLUDED part is not torn down at all, so nothing scene-wide here may
    take its nodes either - see excluded_sdk_curves, which is why the
    scene-wide animCurve delete is filtered.

    Arguments
        fk (bool): Clean up FK components
        ik (bool): Clean up IK components
    '''
    logger.debug(f'-----------------------------------------------------')
    logger.info(f"Cleanup Rig")

    # Clear control cache
    rt_connect.clear_control_cache()
    # Same reason: the mirror measurement is memoized for one build, and
    # Setup can re-orient a chain between two builds of a session
    rt_mirror.clear_sign_cache()
    # Validate cache
    rt_cache.validate_cache()
    # Control count and build-mode changes invalidate the node layout for
    # every part
    structure_changed = rt_cache.validate_cache_structure(fk, ik)

    # Every SDK animCurve in one delete - a rebuild has hundreds - except
    # those belonging to excluded parts, which are not rebuilt and would lose
    # their variable-FK falloff and mode switching for good.
    logger.trace(f"Cleaning up SDK curves")
    with rt_maya.timed('cleanup.sdk_curves'):
        anim_curves = cmds.ls(type=['animCurveUU', 'animCurveUL', 'animCurveUA', 'animCurveTT'])
        keep = excluded_sdk_curves(anim_curves)
        anim_curves = [c for c in anim_curves if c not in keep]
        if anim_curves:
            cmds.delete(anim_curves)

    global _DEFER_CONVERSION_SWEEP
    _DEFER_CONVERSION_SWEEP = True
    try:
        cleanup_dangling_unit_conversions()

        # Shared by every part. Nodes an earlier part already deleted are
        # filtered downstream by rt_maya.remove_nodes, and cleanup creates
        # nothing, so one scan stays accurate for the whole loop.
        utility_nodes = cmds.ls(type=resolve_node_types(UTILITY_NODE_TYPES)) or []

        for rigname in rt_cache.active_parts():
            joints_changed = rt_cache.validate_cache_joints(rigname)
            with rt_maya.timed('cleanup.unbind'):
                rt_maya.unbind_geometry(rigname)

            if rt_constants.FORCE_REBUILD or joints_changed or structure_changed:
                with rt_maya.timed('cleanup.teardown_full'):
                    cleanup_rigname(rigname, fk=True, ik=True,
                                    utility_nodes=utility_nodes)
            else:
                with rt_maya.timed('cleanup.teardown_light'):
                    cleanup_connections(rigname, fk, ik)
    finally:
        _DEFER_CONVERSION_SWEEP = False
    if _CONVERSION_SWEEP_PENDING:
        with rt_maya.timed('cleanup.conversions'):
            cleanup_dangling_unit_conversions()
            # The same idea for curveInfo nodes, which cmds.ikHandle leaves
            # on the temporary curve it makes for every spline build
            cleanup_dangling_curveinfo()

    # The dashboard last, so that on a full teardown the expressions
    # referencing its override conditions are already gone.
    with rt_maya.timed('cleanup.ctrlall'):
        rt_ctrlall.cleanup_ctrlall(fk, ik)

def remove_rig():
    '''
    Strip the rig back to bare skeleton + geometry: the reverse of a
    build, for handing a scene on or starting over. Destructive and NOT
    an undo - animation on the controls goes with the controls, and the
    pre-build scene is not otherwise restored.

    Kept: BN joints (posed as they are now, as plain joints) and all
    geometry, still bound to them. Removed: controls, curves, clusters,
    ikHandles, FX and utility networks, the duplicated FK/IK chains, and
    the whole rig group hierarchy including root and cog.

    Scoped to the INCLUDED rig parts (rig_tail_cache.active_parts), the
    same roster the builder works on. An excluded part is one the build
    leaves alone, so a removal takes nothing of it either. When anything is
    excluded this is a PARTIAL removal and the rig hierarchy STAYS - the
    excluded parts' controls and joints live inside it.

    Order matters, and each step exists for a reason:

    1. Capture every BN joint's world matrix FIRST, while the build's
       drivers are still live. The build zeroes BN local TRS and drives
       the pose through offsetParentMatrix (rig_tail_matrix), so deleting
       that network without capturing first collapses the skeleton onto
       its parents.
    2. Full per-part teardown (cleanup_rigname), then delete the FK/IK
       duplicate chains, which teardown unwraps but does not remove.
    3. Turn each BN joint back into a plain joint at its captured matrix -
       incoming drivers detached, opm identity, orientation moved into
       jointOrient. Only INCOMING connections are cut, so the outgoing
       worldMatrix -> skinCluster geometry bind survives and the mesh
       keeps deforming (same rule as cleanup_rigname and
       rig_tail_setup._apply_frames).
    4. Move skeleton and geometry out of the rig hierarchy BEFORE deleting
       the root group, or Maya deletes them along with it. A node that
       could NOT be moved out aborts the deletion rather than being taken
       down with the group: leaving a root group standing costs less than
       losing the geometry inside it.
    5. Sweep the DG leftovers the per-part teardown does not reach:
       orphaned set-driven-key curves (cleanup_rig deletes those in one
       scene-wide call, which this is not) and any utility/anim node still
       carrying a rig part's name. Without this the scene keeps hundreds of
       disconnected animCurve and condition nodes.
    6. Drop the FK/IK/FX caches and the last-build record, so a later
       build re-detects from the skeleton instead of trusting names that
       no longer exist.

    Every scene node is addressed by its full DAG path. Short names are
    ambiguous the moment a scene holds two nodes with the same name under
    different parents (a duplicated 'rivets' group), and Maya answers an
    ambiguous name with 'More than one object matches name', which would
    abort the whole removal.

    Return
        bool: True if a rig was found and removed
    '''
    logger.debug('-----------------------------------------------------')
    logger.info('Remove Rig')

    rt_connect.clear_control_cache()
    root_grp = unique_path(find_existing_root_grp()
                           or rt_naming.fstr('', rt_constants.ROOT_GRP))
    # Only the INCLUDED parts, the same roster the build works on
    # (rig_tail_cache.active_parts): an excluded part is one the builder
    # leaves alone, and removing what it never built is not this action's
    # job. With anything excluded this becomes a PARTIAL removal - see
    # step 4, which then has to leave the hierarchy standing.
    parts = rt_cache.active_parts()
    kept = [p for p in rt_constants.RIGPARTS if p not in parts]
    if kept:
        logger.info(f'Removing {len(parts)} included part(s); leaving '
                    f'{len(kept)} excluded part(s) built: {", ".join(kept)}')
    if not parts:
        logger.warning('Every rig part is excluded; nothing to remove')
        return False

    # 1. Capture the skeleton while the rig still drives it (see
    # capture_bn_poses: rest where it is stored, live otherwise), keyed by
    # full path so step 3 cannot re-resolve to a different node
    poses = capture_bn_poses(parts)
    bn_paths = {rigname: [p for p in
                          (unique_path(j)
                           for j in rt_constants.JOINTS_BN.get(rigname, []))
                          if p]
                for rigname in parts}
    logger.debug(f'Captured {len(poses)} BN joint poses')

    # 2. Tear down each part, then the duplicated chains. One conversion
    # sweep for the whole teardown instead of one per part (see cleanup_rig)
    global _DEFER_CONVERSION_SWEEP
    _DEFER_CONVERSION_SWEEP = True
    try:
        for rigname in parts:
            # cleanup_rigname ends with cleanup_anim_effects, so the FX
            # network is already gone by the time it returns
            cleanup_rigname(rigname, fk=True, ik=True)
        for rigname in parts:
            for typ in (rt_constants.TYPE_FK, rt_constants.TYPE_IK):
                chain_root = rt_naming.fstr(rigname, rt_constants.JOINT, typ, 0)
                if cmds.objExists(chain_root):
                    rt_maya.remove(chain_root)
                jnt_grp = rt_naming.fstr(rigname, rt_constants.GROUP, typ)
                if cmds.objExists(jnt_grp):
                    rt_maya.remove(jnt_grp)
    finally:
        _DEFER_CONVERSION_SWEEP = False

    # 3. Restore the skeleton as plain joints, root to tip
    restore_bn_skeleton(parts, poses=poses, bn_paths=bn_paths)
    # Leave the skeleton keyable and visible, as the Setup phase does.
    # Outside the part loop: it walks the whole BN cache on every call, so
    # calling it per part re-did the same work once per rig part. Scoped to
    # the parts being removed - an excluded part's joints are still driven
    # by its rig and must stay non-keyable.
    rt_maya.finalize_joint_channels(
        True, visibility=1,
        joint_dicts=[{p: rt_constants.JOINTS_BN[p] for p in parts
                      if p in rt_constants.JOINTS_BN}])

    # 4. Rescue skeleton and geometry, then drop the hierarchy - but only
    # when the WHOLE roster went. With parts excluded, their rig is still
    # live and lives in this hierarchy: their controls hang under the root
    # group, their joints under the skeleton group, so deleting it would
    # take the rig this action was told to leave alone.
    removed = False
    if kept:
        logger.debug(f"Kept '{root_grp}': excluded parts are still built")
        removed = True
    elif root_grp and cmds.objExists(root_grp):
        rescued, stuck = _rescue_from_root(root_grp)
        logger.debug(f"Moved {rescued} node(s) out of '{root_grp}'")
        if stuck:
            abort_build(logger,
                        f"Remove Rig stopped: {len(stuck)} node(s) could not "
                        f"be moved out of '{root_grp}', and deleting the "
                        f"group would delete them too: "
                        f"{', '.join(stuck[:5])}. Reparent them by hand, "
                        f"then run Remove Rig again.")
        rt_maya.remove(root_grp)
        removed = True
    else:
        logger.warning('No rig root group found; nothing to remove')

    # 5. Sweep the DG leftovers
    swept = _sweep_rig_leftovers(parts)
    cleanup_dangling_unit_conversions()
    swept += cleanup_dangling_curveinfo()
    if swept:
        logger.debug(f'Swept {swept} leftover rig node(s)')

    # 6. Forget the build, for the parts that went. LAST_BUILD keeps its
    # documented key set - rig_tail_cache indexes 'rigparts'/'root'/...
    # directly, so replacing it with an empty dict would KeyError on the
    # next build.
    for rigname in parts:
        for jdict in (rt_constants.JOINTS_FK, rt_constants.JOINTS_IK, rt_constants.JOINTS_FX):
            jdict.pop(rigname, None)
        (rt_constants.LAST_BUILD.get('joints_pos') or {}).pop(rigname, None)
    if kept:
        # A partial removal: the excluded parts are still built, so their
        # build record has to survive or the next build would treat them as
        # new and rebuild what it was told to leave alone
        rt_constants.LAST_BUILD['rigparts'] = [
            p for p in rt_constants.LAST_BUILD.get('rigparts') or []
            if p not in parts]
    else:
        rt_constants.LAST_BUILD.update({
            'rigparts': [],
            'root': '',
            'joints_pos': {},
            'num_ctrl_fk': None,
            'num_ctrl_ik': None,
            'indiv_fk': None,
            'build_mode': None,
        })

    logger.info(f'Remove Rig complete ({len(poses)} skeleton joints kept)')
    return removed


def _rigid(matrix):
    '''
    The rigid part of a 4x4 row-major world matrix.

    A BN joint's world matrix is not rigid. Volume preservation drives .sy
    and .sz (rig_tail_stretch), so a chain at rest still reads a scale near
    1.0004, and composing the OPM chain leaves shear in the low digits. A
    joint has no shear attribute, so writing such a matrix straight back
    bakes scale into the skeleton and hands Maya a transform it cannot
    store on a joint.

    Gram-Schmidt off X, which is the axis the chain aims down: X keeps its
    direction exactly, Z is made perpendicular to X and the incoming Y, and
    Y is rebuilt from those. Translation is copied untouched, so world
    positions come back exact; only the scale and the skew go.

    Arguments
        matrix (list): 16 floats, row-major

    Return
        list: 16 floats, orthonormal basis and the original translation
    '''
    x = om.MVector(matrix[0], matrix[1], matrix[2])
    y = om.MVector(matrix[4], matrix[5], matrix[6])
    z = x ^ y
    # A degenerate basis has no rigid version to find - leave it to the
    # caller's matrix rather than writing an invalid frame
    if x.length() < 1e-9 or z.length() < 1e-9:
        return list(matrix)
    x.normalize()
    z.normalize()
    y = z ^ x
    return [x.x, x.y, x.z, 0.0,
            y.x, y.y, y.z, 0.0,
            z.x, z.y, z.z, 0.0,
            matrix[12], matrix[13], matrix[14], 1.0]


def capture_bn_poses(parts, rest=True):
    '''
    World matrix per BN joint, for restoring the skeleton later.

    REST, NOT LIVE, wherever a rest pose is stored. The rest anchor
    (rig_tail_restpose) records each joint's rest world matrix on the first
    build and never re-captures, so it is the one description of the
    skeleton that a posed rig cannot corrupt. Reading live instead means
    Remove Rig clicked on a posed rig hands back a skeleton frozen in that
    pose - correct-looking and wrong - and a rebuild re-anchors the whole
    setup to it. Falls back to the live matrix per joint when nothing is
    stored: a chain that has never been built, or one Joint Chain Builder
    just re-spaced and cleared.

    Every matrix is made rigid on the way out (see _rigid).

    Arguments
        parts (list): rig parts to capture
        rest (bool): False to force live capture and ignore any stored rest

    Return
        dict: {joint full path: 16-float world matrix}
    '''
    try:
        import rig_tail_restpose as rt_rest
        rest_attr = rt_rest.REST_ATTR
    except Exception as err:
        logger.warning(f'Could not load the rest pose module, capturing live: {err}')
        rest_attr = None

    poses = {}
    stored = 0
    for rigname in parts:
        for jnt in rt_constants.JOINTS_BN.get(rigname, []):
            path = unique_path(jnt)
            if not path:
                continue
            matrix = None
            if rest and rest_attr and cmds.attributeQuery(rest_attr, node=path,
                                                          exists=True):
                try:
                    matrix = cmds.getAttr(f'{path}.{rest_attr}')
                    stored += 1
                except RuntimeError as err:
                    logger.warning(f"Could not read the rest pose on '{path}': {err}")
                    matrix = None
            if matrix is None:
                matrix = cmds.xform(path, q=True, ws=True, matrix=True)
            poses[path] = _rigid(matrix)
    logger.debug(f'Captured {len(poses)} BN poses ({stored} from the stored rest)')
    return poses


def restore_bn_skeleton(parts, poses=None, bn_paths=None):
    '''
    Turn the BN chains back into plain joints at their captured pose.

    The build zeroes every BN joint's local TRS and jointOrient and drives
    the pose through offsetParentMatrix (rig_tail_matrix), so a built
    skeleton carries its shape ONLY in a live node network. Anything that
    disturbs that network - deleting the FK/IK chains the blendMatrix reads,
    tearing the rig down, disconnecting the joints - collapses the chain
    onto its parent, and there is nothing left to recover it from.

    This puts the shape back where a joint can hold it on its own: pose in
    jointOrient, offsetParentMatrix at identity, no incoming connections.
    After it runs the chain is what a first-ever build sees, so the order of
    everything downstream stops mattering.

    Only INCOMING connections are cut, so the outgoing worldMatrix ->
    skinCluster geometry bind survives and the mesh keeps deforming (the
    same rule as cleanup_rigname and remove_rig).

    Arguments
        parts (list): rig parts to restore
        poses (dict): captured matrices from capture_bn_poses. Captured here
            when not given - callers that tear the rig down first must
            capture BEFORE the teardown and pass the result in, since by
            then the live matrices are gone.
        bn_paths (dict): rigname -> joint paths, when the caller already
            resolved them

    Return
        int: joints restored
    '''
    if poses is None:
        poses = capture_bn_poses(parts)
    if bn_paths is None:
        bn_paths = {rigname: [p for p in
                              (unique_path(j)
                               for j in rt_constants.JOINTS_BN.get(rigname, []))
                              if p]
                    for rigname in parts}

    restored = 0
    for rigname in parts:
        # Root to tip: each joint is written in world space, so a parent has
        # to be sitting at its own restored pose before its child is placed
        for jnt in bn_paths.get(rigname, []):
            if not cmds.objExists(jnt) or jnt not in poses:
                continue
            rt_maya.disconnect_all(jnt, source=True, destination=False)
            rt_maya.reset_opm(jnt)
            cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)
            cmds.setAttr(f'{jnt}.jointOrient', 0, 0, 0)
            cmds.setAttr(f'{jnt}.scale', 1, 1, 1)
            cmds.xform(jnt, ws=True, matrix=poses[jnt])
            # The matrix is rigid, so this leaves scale at 1 and shear at 0
            # rather than re-applying the squash scale the old capture carried
            rot = cmds.getAttr(f'{jnt}.rotate')[0]
            cmds.setAttr(f'{jnt}.jointOrient', rot[0], rot[1], rot[2])
            cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)
            restored += 1
    logger.debug(f'Restored {restored} BN joints as plain joints')
    return restored


def unique_path(node):
    '''
    Resolve a node name to its one full DAG path.

    Alias of rt_maya.unique_path, which is the canonical implementation -
    the joint traversal in rig_tail_joint needs it too and cannot import
    this module (cleanup imports joint, not the other way round). Kept here
    under its original name for the teardown code that already calls it.

    Arguments
        node (str): Node name or DAG path (None/'' is accepted)

    Return
        str or None: full path, or None when the name is missing or
        matches more than one node (both are logged)
    '''
    return rt_maya.unique_path(node)


def _rescue_from_root(root_grp):
    '''
    Move the skeleton and geometry out of the rig hierarchy to the scene
    root, so deleting the rig root group cannot take them with it.

    Everything directly under the geometry and skeleton groups is moved,
    rather than only the cached BN chains: a scene usually holds meshes
    and joints the rig never touched (other parts of the character), and
    they were parented in by setup_rig just the same.

    Children are addressed by full path (see unique_path): a mesh named the
    same as a node elsewhere in the scene cannot be reparented by its short
    name, and that failure is exactly the case where losing the node
    matters, so it is reported to the caller rather than merely logged.

    Arguments
        root_grp (str): The rig root group about to be deleted

    Return
        tuple: (nodes moved out, list of nodes that could not be moved)
    '''
    moved = 0
    stuck = []
    for template in (rt_constants.GEOMETRY_GRP, rt_constants.SKELETON_GRP):
        grp = unique_path(rt_naming.fstr('', template))
        if not grp:
            continue
        for child in cmds.listRelatives(grp, c=True, typ='transform',
                                        f=True) or []:
            try:
                cmds.parent(child, world=True)
                moved += 1
            except Exception as e:
                # Every exception type: Maya reports an ambiguous name as
                # ValueError from some commands and RuntimeError from others
                logger.warning(f"Could not move '{child}' out of "
                               f"'{grp}': {e}")
                stuck.append(child)
    return moved, stuck


# Node types the leftover sweep is allowed to delete. Utility, matrix and
# animation nodes only: no joint, transform, mesh, nurbsCurve, skinCluster
# or dagPose type appears here, so a sweep can never take the skeleton, the
# geometry or the skin the removal is meant to keep.
_SWEEP_TYPES = [
    'condition', 'multiplyDivide', 'plusMinusAverage', 'multDoubleLinear',
    'addDoubleLinear', 'pointMatrixMult', 'blendTwoAttr', 'blendColors',
    'clamp', 'setRange', 'choice', 'curveInfo', 'pointOnCurveInfo',
    'remapValue', 'reverse', 'expression', 'composeMatrix',
    'decomposeMatrix', 'multMatrix', 'inverseMatrix', 'addMatrix',
    'wtAddMatrix', 'pickMatrix', 'aimMatrix', 'quatToEuler', 'eulerToQuat',
    'angleBetween', 'distanceBetween', 'cluster', 'ikHandle', 'ikEffector',
    'animCurveUU', 'animCurveUL', 'animCurveUA', 'animCurveTT',
]


def _ls_types(types):
    '''
    cmds.ls(type=...) that tolerates a node type this Maya does not know.

    The matrix nodes (multMatrix, composeMatrix, pickMatrix...) come from
    the matrixNodes plugin, and cmds.ls raises on the whole query if one
    type in the list is unknown - which would take the sweep down in a
    session where the plugin never loaded. One query normally; on failure,
    fall back to querying type by type and skip the ones Maya rejects.

    Renamed types are resolved first (see resolve_node_types), or the sweep
    is blind to every multDoubleLinear/addDoubleLinear/pointMatrixMult node
    on Maya 2026+ and leaves them behind.

    Arguments
        types (list): Node type names

    Return
        list: matching nodes
    '''
    types = resolve_node_types(types)
    try:
        return cmds.ls(type=types) or []
    except RuntimeError:
        found = []
        for typ in types:
            try:
                found.extend(cmds.ls(type=typ) or [])
            except RuntimeError:
                logger.trace(f"Unknown node type '{typ}', skipped")
        return found


def _sweep_rig_leftovers(parts, delete=True):
    '''
    Delete the DG nodes a per-part teardown leaves behind.

    cleanup_rigname deletes by name pattern, and every pattern is anchored
    on a type prefix ('FK_tail_*'). The rig also builds nodes that carry no
    prefix - the dashboard's '{rigname}_stretch_override_condition', the FX
    expressions, and the hundreds of set-driven-key animCurves that
    cleanup_rig only removes in its own scene-wide call - so a removal that
    relied on the patterns alone left them in the scene as orphans.

    A node is swept when its name carries a rig part's name as a whole
    token ('tail' matches 'FK_tail_02_condition', never 'detail') AND its
    type is in _SWEEP_TYPES. Type is the safety net: the skeleton, the
    meshes and their skinClusters share those names and none of their types
    are sweepable.

    Arguments
        parts (list): Rig part names being removed
        delete (bool): False to only report (used by rig_leftovers)

    Return
        int or list: nodes deleted, or the node list when delete is False
    '''
    if not parts:
        return 0 if delete else []
    tokens = [re.compile(rf'(?<![A-Za-z0-9]){re.escape(p)}(?![A-Za-z0-9])')
              for p in parts]
    found = []
    for node in _ls_types(_SWEEP_TYPES) or []:
        leaf = node.split('|')[-1]
        if any(t.search(leaf) for t in tokens):
            found.append(node)
    if not delete:
        return found
    # Expressions first, then everything else, in two batched passes (see
    # cleanup_anim_effects)
    expressions = [n for n in found if cmds.nodeType(n) == 'expression']
    count = rt_maya.remove_nodes(expressions)
    count += rt_maya.remove_nodes([n for n in found
                                  if n not in set(expressions)])
    return count


def rig_leftovers(parts=None):
    '''
    Rig nodes still in the scene, for checking a removal was complete.

    Reports what Remove Rig is supposed to have deleted: the rig root
    group, anything named with a rig type prefix, and any sweepable
    utility/anim node carrying a part's name. Read-only.

    Arguments
        parts (list): Rig parts to check, defaulting to RIGPARTS

    Return
        dict: {'root': [...], 'prefixed': [...], 'utility': [...]}
    '''
    parts = list(parts if parts is not None else rt_constants.RIGPARTS)
    root_grp = rt_naming.fstr('', rt_constants.ROOT_GRP)
    prefixes = (rt_constants.TYPE_FK, rt_constants.TYPE_IK, rt_constants.TYPE_FX)
    prefixed = []
    for typ in prefixes:
        for part in parts:
            prefixed.extend(cmds.ls(f'{typ}_{part}_*', long=True) or [])
    return {
        'root': cmds.ls(root_grp, long=True) or [],
        'prefixed': sorted(dict.fromkeys(prefixed)),
        'utility': _sweep_rig_leftovers(parts, delete=False),
    }


def restore_fk_joint_chain(rigname):
    '''
    Unwrap the FK SDK hierarchy, leaving a plain FK joint chain.

    create_sdk_groups needs each FK joint to arrive as a plain link whose
    parent is the previous joint. A prior build leaves every joint buried in
    its own SDK stack, and re-wrapping one of those would parent the new
    stack under its own descendant, which Maya rejects as a cycle.

    So: lift the joints out to the world, delete the SDK groups, re-chain the
    joints. The groups go by name pattern rather than by counting layers,
    which also clears a stack built under a different NUM_CTRL_FK.

    Arguments
        rigname (str): Name of rig component
    '''
    if rigname not in rt_constants.JOINTS_FK:
        return
    logger.trace(f'{rigname}: Restoring flat FK joint chain')
    joints = rt_constants.JOINTS_FK[rigname]
    fkjnt_grp = rt_naming.fstr(rigname, rt_constants.GROUP, rt_constants.TYPE_FK)

    # One cmds.parent for the whole chain. A reparent is among the most
    # expensive commands there is, being a DAG restructure plus undo state.
    loose = []
    for jnt in joints:
        if cmds.objExists(jnt):
            jnt_parent = cmds.listRelatives(jnt, p=True, typ='transform') or []
            if jnt_parent and jnt_parent[0] != fkjnt_grp:
                loose.append(jnt)
    if loose:
        cmds.parent(*loose, world=True)

    # SDK_GRP and SDK_JNT both end with the SDK label, so one pattern finds
    # the whole stack. Disconnected first, then deleted in ONE call: there
    # are NUM_CTRL_FK+1 of these per joint.
    sdk_pattern = f'{rt_constants.TYPE_FK}_{rigname}_*_{rt_constants.SDK}'
    sdk_groups = cmds.ls(sdk_pattern, type='transform') or []
    rt_maya.disconnect_nodes(sdk_groups)
    sdk_groups = [g for g in sdk_groups if cmds.objExists(g)]
    if sdk_groups:
        cmds.delete(sdk_groups)

    # Re-parent FK joints in proper hierarchy
    for i in range(len(joints)-1, 0, -1):  # Reverse order
        if cmds.objExists(joints[i]) and cmds.objExists(joints[i-1]):
            rt_maya.parent_to(joints[i], joints[i-1])


def fk_sdk_structure_is_current(rigname):
    '''
    Report whether the scene's FK SDK hierarchy matches what the current
    builder produces: every FK joint parented directly under its own SDK_JNT
    group. Returns False when the joints are unwrapped (no prior build) or
    wrapped in a stale layout (e.g. built by older code with a different SDK
    layer set), which the joint/control caches cannot detect on their own.

    Arguments
        rigname (str): Name of rig component

    Return
        bool: True if the existing SDK structure can be safely reused as-is
    '''
    if rigname not in rt_constants.JOINTS_FK:
        return True
    for jnt in rt_constants.JOINTS_FK[rigname]:
        if not cmds.objExists(jnt):
            return False
        NN = rt_naming.get_index_from_name(jnt)
        sdk_jnt = rt_naming.fstr(rigname, rt_constants.SDK_JNT, rt_constants.TYPE_FK, NN)
        jnt_parent = cmds.listRelatives(jnt, p=True, typ='transform') or []
        if not jnt_parent or jnt_parent[0] != sdk_jnt:
            return False
    return True


def cleanup_rigname(rigname, fk, ik, utility_nodes=None):
    '''
    The full teardown for one rig part: delete everything the build makes,
    leaving the skeleton and the bound geometry. Also usable on its own, and
    used by remove_rig.

    Numbered below in the order it runs, and the order matters in one place:
    constraints and skinClusters come off before the nodes carrying them are
    deleted, so nothing cascades.

    What SURVIVES is the point. BN joints keep their outgoing
    worldMatrix -> skinCluster, which is the geometry bind; only their
    incoming drivers belong to the rig. FK/IK/FX joints hold no skin, so both
    directions go. The joints themselves stay in every case - the FK/IK
    duplicates are unwrapped back to plain chains, not removed.

    Work is batched per CHAIN and per SUBTREE rather than per node
    throughout, since listRelatives and listConnections both answer for a
    whole list in one command.

    Arguments
        rigname (str): Name of rig component
        fk (bool): Clean up FK components
        ik (bool): Clean up IK components
        utility_nodes (list): Every utility node in the scene, when the
            caller has already listed them (cleanup_rig does, once for the
            whole teardown). Listed here when not given.
    '''
    logger.debug(f"{rigname}: Cleanup rig part")
    types = ['', rt_constants.TYPE_BN, rt_constants.TYPE_IK, rt_constants.TYPE_FK, rt_constants.TYPE_FX]
    basectrl_grp = rt_naming.fstr(rigname, rt_constants.BASECTRL_GRP)
    basectrl = rt_naming.fstr(rigname, rt_constants.BASECTRL)

    # 1. Disconnect the skeleton and drop its constraints, a chain at a time
    logger.trace(f"{rigname}: Cleaning up skeleton constraints")
    for joints in [rt_constants.JOINTS_BN, rt_constants.JOINTS_FK, rt_constants.JOINTS_IK, rt_constants.JOINTS_FX]:
        if rigname in joints:
            chain = [j for j in joints[rigname] if cmds.objExists(j)]
            if not chain:
                continue
            constraints = cmds.listRelatives(chain, type='constraint') or []
            if constraints:
                cmds.delete(constraints)
            # BN's outgoing side is the geometry bind - see the docstring
            keep_skin = joints is rt_constants.JOINTS_BN
            rt_maya.disconnect_nodes(chain, source=True,
                                    destination=not keep_skin)

    # 2. Control constraints, in one query and one delete: a constraint is a
    # child of what it constrains, so the whole control subtree answers at
    # once. Nothing else is reset on these controls - step 4 deletes them.
    if cmds.objExists(basectrl):
        constraints = cmds.listRelatives(basectrl, ad=True,
                                         type='constraint', f=True) or []
        if constraints:
            cmds.delete(constraints)

    # 3. Delete skinClusters from curves
    logger.trace(f"{rigname}: Cleaning up skinClusters")
    if fk:
        curve_fk = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_FK)
        rt_maya.unbind_skincluster(curve_fk)
    if ik:
        curve_ik = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_IK)
        curve_ik_spline = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_IK, TAG='_spline')
        rt_maya.unbind_skincluster(curve_ik)
        rt_maya.unbind_skincluster(curve_ik_spline)

    # 4. Delete existing controls and control groups
    logger.trace(f"{rigname} Cleaning up controls and groups")
    rt_maya.remove(basectrl_grp)
    rt_maya.remove(basectrl)

    if fk:
        fkroot_grp = rt_naming.fstr(rigname, rt_constants.CTRLROOT_GRP, rt_constants.TYPE_FK)
        rt_maya.remove(fkroot_grp)

    # The FK joints come out of their SDK stack here, not deleted with it
    if fk and rigname in rt_constants.JOINTS_FK:
        restore_fk_joint_chain(rigname)

    # 5. The utility networks - conditions, math nodes, the FK falloff and
    # curve-info webs, hundreds of nodes per part.
    #
    # Found by TYPE and narrowed on name in Python, which is the difference
    # between a DG lookup and a scene walk: every name pattern handed to
    # cmds.ls walks the whole scene, and this needs one per type per rig-part
    # prefix. cleanup_rig hands the same scan to every part.
    #
    # The name test rebuilds '{typ}_{rigname}_*{nodetype}'. Anchoring the
    # underscore after rigname is what stops a 'tail' teardown reaching into
    # 'tail2'. The empty type is dropped - it only ever produced a leading
    # underscore, which matches nothing.
    logger.trace(f"{rigname}: Cleaning up utility nodes")
    if utility_nodes is None:
        utility_nodes = cmds.ls(type=resolve_node_types(UTILITY_NODE_TYPES)) or []
    prefixes = tuple(f'{typ}_{rigname}_' for typ in types if typ)
    suffixes = tuple(UTILITY_NODE_TYPES)
    found = [n for n in utility_nodes
             if n.split('|')[-1].startswith(prefixes)
             and n.endswith(suffixes)]
    rt_maya.remove_nodes(found)

    # 7. Delete curves, clusters, ikHandles
    logger.trace(f"{rigname}: Cleaning up curves and clusters")
    if fk:
        curve_fk = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_FK)
        spline_grp_fk = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, rt_constants.TYPE_FK)
        cluster_grp_fk = rt_naming.fstr(rigname, rt_constants.CLUSTER_GRP, rt_constants.TYPE_FK)
        rt_maya.remove(curve_fk)
        rt_maya.remove(spline_grp_fk)
        rt_maya.remove(cluster_grp_fk)

    if ik:
        curve_ik = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_IK)
        curve_ik_spline = rt_naming.fstr(rigname, rt_constants.CURVE, rt_constants.TYPE_IK, TAG='_spline')
        spline_grp_ik = rt_naming.fstr(rigname, rt_constants.SPLINE_GRP, rt_constants.TYPE_IK)
        cluster_grp_ik = rt_naming.fstr(rigname, rt_constants.CLUSTER_GRP, rt_constants.TYPE_IK)
        spline_handle = rt_naming.fstr(rigname, rt_constants.SPLINE_HANDLE, rt_constants.TYPE_IK)
        spline_effector = rt_naming.fstr(rigname, rt_constants.SPLINE_EFFECTOR, rt_constants.TYPE_IK)
        rt_maya.remove(spline_handle)
        rt_maya.remove(spline_effector)
        rt_maya.remove(curve_ik)
        rt_maya.remove(curve_ik_spline)
        rt_maya.remove(spline_grp_ik)
        rt_maya.remove(cluster_grp_ik)

    # Clean up animation effects
    cleanup_anim_effects(rigname, fk, ik)

    # Delete scale group
    scale_grp = rt_naming.fstr(rigname, rt_constants.SCALE_GRP)
    rt_maya.remove(scale_grp)

    # Clean up old visibility conditions
    basectrl_name = basectrl.rsplit(rt_constants.CTRL, 1)[0]
    rt_maya.remove(f'{basectrl_name}{rt_constants.VIS}{rt_constants.COND}')

    # Clean up old items. Only exact names go to cmds.ls - a name it can
    # look up costs nothing, a wildcard makes it walk the scene - so the
    # one wildcard pattern is matched against the typed scan instead
    # (switch conditions are conditions, so they are already in it).
    patterns = list()
    for typ in types:
        patterns.extend([
            f'{typ}_{rigname}_revik_{rt_constants.NUM_CTRL_IK:02d}{rt_constants.CTRL}{rt_constants.GRP}',
            f'{typ}_{rigname}_measure_scale{rt_constants.GRP}'
        ])
    switch_cond = tuple(f'{typ}_{rigname}_switch_' for typ in types if typ)
    switch_end = f'{rt_constants.VIS}{rt_constants.COND}'
    patterns.extend(n for n in utility_nodes
                    if n.split('|')[-1].startswith(switch_cond)
                    and n.endswith(switch_end))
    # Guard the unpack: cmds.ls() with no pattern returns the WHOLE scene,
    # and this one feeds straight into cmds.delete
    nodes = cmds.ls(*patterns) if patterns else []
    if nodes:
        cmds.delete(nodes)

def cleanup_connections(rigname, fk, ik):
    '''
    The light teardown: leave the rig's nodes standing and cut their drivers,
    where cleanup_rigname deletes and the build rebuilds from nothing. Chosen
    when the caches report the skeleton and the node layout both unchanged.

    Keeping a node only pays when the build can reuse it, and the halves of
    the rig differ on that. The SDK hierarchy, the control shapes and the
    whole FX network including its expressions all stay, because the build
    meets them and does almost nothing. The FK utility networks are deleted:
    set_curveinfo_fk and falloff_rotation write every attribute and
    connection again regardless, at the same cost whether the node was there
    or not, so keeping them saves nothing - and the delete is what collects
    the ones the current settings no longer call for.

    The SDK hierarchy is the reason this path is cheap, and the one thing
    that can force a structural teardown anyway. Kept, create_sdk_groups
    meets every group already nested and already placed, and does almost
    nothing. But it is only reusable if it matches the layout the current
    builder produces - a stale layer set from older code is invisible to the
    caches, and rebuilding over it would wrap already-wrapped joints into a
    parenting cycle - so a mismatch is flattened back to a plain chain first.

    Arguments
        rigname (str): Name of rig component
        fk (bool): Clean FK components
        ik (bool): Clean IK components
    '''
    logger.debug(f'{rigname}: Cleanup connections')

    if fk and not fk_sdk_structure_is_current(rigname):
        logger.debug(f'{rigname}: FK SDK layout is stale; rebuilding it from a flat chain')
        restore_fk_joint_chain(rigname)

    # Per chain, not per joint (see cleanup_rigname step 1)
    for joints in [rt_constants.JOINTS_BN, rt_constants.JOINTS_FK, rt_constants.JOINTS_IK]:
        if rigname in joints:
            chain = [j for j in joints[rigname] if cmds.objExists(j)]
            if not chain:
                continue
            # Keep BN joints' outgoing worldMatrix -> skinCluster (the
            # geometry bind); only their incoming drivers are rebuilt.
            keep_skin = joints is rt_constants.JOINTS_BN
            rt_maya.disconnect_nodes(chain, source=True,
                                    destination=not keep_skin)
            # Remove constraints
            constraints = cmds.listRelatives(chain, type='constraint') or []
            if constraints:
                cmds.delete(constraints)

    # Disconnect FK SDK groups, the whole stack of every joint in two
    # commands (there are NUM_CTRL_FK + 1 of them per joint)
    if fk and rigname in rt_constants.JOINTS_FK:
        sdk_groups = []
        for jnt in rt_constants.JOINTS_FK[rigname]:
            NN = rt_naming.get_index_from_name(jnt)
            for idx in range(rt_constants.NUM_CTRL_FK + 1):
                if idx < rt_constants.NUM_CTRL_FK:
                    sdk_grp = rt_naming.fstr(rigname, rt_constants.SDK_GRP, rt_constants.TYPE_FK, NN, nn=idx+1)
                else:
                    sdk_grp = rt_naming.fstr(rigname, rt_constants.SDK_JNT, rt_constants.TYPE_FK, NN)
                sdk_groups.append(sdk_grp)
        rt_maya.disconnect_nodes(sdk_groups, source=True, destination=False)

    # The FK utility networks. set_curveinfo_fk and falloff_rotation write
    # every one of these again regardless, at the same cost whether the node
    # is there or not - so keeping them would buy nothing, while deleting
    # them is what collects the ones the current settings no longer call for.
    # A part that stops being mirrored, for instance, stops getting a
    # rotmirror node, and MIRROR_BEHAVIOR is not part of the structure
    # signature that would force a full teardown to sweep it.
    # Underscore anchored after rigname, so 'tail' cannot reach 'tail2'.
    if fk:
        typ = rt_constants.TYPE_FK
        fk_patterns = [
            f'{typ}_{rigname}_*{rt_constants.COND}',
            f'{typ}_{rigname}_*multiplyDivide',
            f'{typ}_{rigname}_*plusMinusAverage',
            f'{typ}_{rigname}_*multDoubleLinear',
            f'{typ}_{rigname}_*pointMatrixMult',
            f'{typ}_{rigname}_*setRange',
            f'{typ}_{rigname}_*pointOnCurveInfo',
            # set_curveinfo_fk's position remap (tail length -> parameter)
            f'{typ}_{rigname}_*remapValue',
            # Exact name, not '*curveInfo', which would also match stretch's
            # _scale_curveInfo - that one caches a rest length and must live
            f'{typ}_{rigname}_curveInfo',
        ]
        rt_maya.remove_nodes(dict.fromkeys(cmds.ls(*fk_patterns) or []))

    # The FX network of an ENABLED effect stays, both halves of it
    # rebuilding in place: build_curl reuses its nodes through objExists and
    # ensure_connect, and rig_tail_anim.sync_expressions compares each
    # expression against the code it should hold, rewriting only what
    # differs. An expression's code is fixed by the rig it describes, so on
    # an unchanged rig that is a query per expression against a teardown and
    # a rewrite of every one. A DISABLED effect has no such rebuild coming,
    # so its network goes now.
    cleanup_disabled_effects(rigname)

def cleanup_anim_effects(rigname, fk, ik):
    '''
    Delete the whole FX network for one part - expressions, the loop clock,
    and the curl and wave node webs - on the full teardown path.

    Expressions go in the first batch. cmds.delete on a connected expression
    cascades through its whole web, taking the loop network, its sibling
    expressions and their composeMatrix nodes, so they are disconnected
    before anything is deleted.

    Arguments
        rigname (str): Name of rig component
        fk (bool): Clean FK effects
        ik (bool): Clean IK effects
    '''
    logger.trace(f'{rigname}: Cleanup animation effects')
    typ = rt_constants.TYPE_FX
    node_patterns = fx_expression_patterns(rigname) + [
        f'{rigname}_loop_time',
        f'{rigname}_curl*_multiplyDivide',
        f'{rigname}_curl*_plusMinusAverage',
        f'{rigname}_curl*_clamp',
        f'{rigname}_wave_*',
        f'{typ}_{rigname}_wave_*',
        f'{typ}_{rigname}_curl_*',
        f'{typ}_{rigname}_dynOffset_*',
        f'{typ}_{rigname}_loop_*',
        f'{typ}_{rigname}_*_blender_plusMinusAverage',
        f'{typ}_{rigname}_*_ikfk_blendColors',
        f'{typ}_{rigname}_*_ikfk_remap_condition'
    ]
    # Every pattern above starts with '{rigname}_' or '{typ}_{rigname}_', so
    # those two are a superset: Maya walks the scene twice instead of twelve
    # times, and the twelve are then matched in Python where a name test is
    # free (see cleanup_rigname).
    candidates = cmds.ls(f'{rigname}_*', f'{typ}_{rigname}_*') or []
    nodes = [n for n in dict.fromkeys(candidates)
             if any(fnmatch.fnmatchcase(n.split('|')[-1], p)
                    for p in node_patterns)]
    # Split on node type rather than relying on pattern order, so the
    # expressions-first rule holds even for one matching a later pattern
    expressions = [n for n in nodes if cmds.nodeType(n) == 'expression']
    rt_maya.remove_nodes(expressions)
    rt_maya.remove_nodes([n for n in nodes if n not in set(expressions)])

    cleanup_dangling_unit_conversions()

def excluded_sdk_curves(anim_curves):
    '''
    The SDK animCurves that belong to excluded rig parts, so cleanup_rig's
    one-call sweep can spare them.

    A curve is claimed by the part its DRIVEN node belongs to: that node
    is always part-local (an FK SDK group, a spline group's visibility),
    whereas the driver side is often the shared cog. skipConversionNodes
    steps over the unitConversion that Maya inserts on angle-unit SDKs, so
    the real driven node is read, not 'unitConversion57'. Curves that drive
    nothing are left out - they are orphans the sweep should take.

    Matching is by whole name token, so excluding 'tail' does not also
    spare 'detail'. A node is matched against the WHOLE roster and claimed
    by the longest name that fits, so a roster holding both 'tail' and
    'C_tail' still reads 'FK_C_tail_00_01_sdk' as C_tail's - matching the
    excluded names alone would let the shorter name claim it.

    Arguments
        anim_curves (list): Candidate SDK animCurves from the scene

    Return
        set: Curves to keep.
    '''
    excluded = set(rt_cache.excluded_parts())
    if not excluded or not anim_curves:
        return set()

    # Longest first: the first pattern that hits is the owning part
    tokens = [(p, re.compile(rf'(?<![A-Za-z0-9]){re.escape(p)}(?![A-Za-z0-9])'))
              for p in sorted(rt_constants.RIGPARTS, key=len, reverse=True)]
    keep = set()
    for crv in anim_curves:
        driven = cmds.listConnections(crv, s=False, d=True, scn=True) or []
        for node in driven:
            short = node.split('|')[-1]
            owner = next((p for p, t in tokens if t.search(short)), None)
            if owner in excluded:
                keep.add(crv)
                break
    if keep:
        logger.debug(f'Keeping {len(keep)} SDK curves for excluded parts: '
                     f'{", ".join(sorted(excluded))}')
    return keep

def cleanup_dangling_unit_conversions():
    '''
    Sweep conversion nodes orphaned by deleting SDK animCurves or
    expressions, otherwise they accumulate with every rebuild.
    timeToUnitConversion / unitToTimeConversion are separate node types
    from unitConversion (created for time-attribute connections) and
    need sweeping too.

    Deferred while cleanup_rig is running its per-part loop: the sweep is
    scene-wide, so repeating it per rig part re-walked every conversion
    node in the scene to find the same orphans each time. cleanup_rig runs
    the one deferred sweep at the end (see _DEFER_CONVERSION_SWEEP).
    '''
    global _CONVERSION_SWEEP_PENDING
    if _DEFER_CONVERSION_SWEEP:
        _CONVERSION_SWEEP_PENDING = True
        return
    _CONVERSION_SWEEP_PENDING = False

    conversions = cmds.ls(type=['unitConversion', 'timeToUnitConversion',
                                'unitToTimeConversion']) or []
    if not conversions:
        return

    # Two queries for the whole scene's conversion nodes, not two per node:
    # cmds.listConnections takes the list, and with connections=True each
    # answer names the plug it came from, so the owning node reads straight
    # off it. The per-node version was ~1.4s of a profiled build.
    def _wired(source, suffix):
        conns = cmds.listConnections(conversions, s=source, d=not source,
                                     p=True, c=True) or []
        return {conns[i].rsplit('.', 1)[0]
                for i in range(0, len(conns), 2)
                if conns[i].endswith(suffix)}

    has_input = _wired(True, '.input')
    has_output = _wired(False, '.output')
    dangling = [uc for uc in conversions
                if uc not in has_input or uc not in has_output]
    if dangling:
        cmds.delete(dangling)


def cleanup_dangling_curveinfo():
    '''
    Sweep curveInfo nodes with no input curve.

    A curveInfo whose curve has been deleted cannot do anything except
    print 'curveInfoNN (Curve Info): No valid NURBS curve' every time the
    graph evaluates, twice per evaluation, forever. They come from
    cmds.ikHandle, which creates a curveInfo on the temporary curve it
    makes for a spline solver - one per spline build, and the temporary
    curve is thrown away immediately afterwards.

    rt_maya.remove/remove_nodes now take a curve's curveInfo down with it
    (rt_maya.curveinfo_consumers), so new ones are not created. This clears
    the ones a previous build already left in the scene.

    A curveInfo with no input curve is dead by construction, whoever made
    it, which is what makes a scene-wide sweep safe here - the same rule
    cleanup_dangling_unit_conversions uses.

    Return
        int: nodes deleted
    '''
    curveinfos = cmds.ls(type='curveInfo') or []
    if not curveinfos:
        return 0
    conns = cmds.listConnections(curveinfos, s=True, d=False,
                                 p=True, c=True) or []
    fed = {conns[i].rsplit('.', 1)[0] for i in range(0, len(conns), 2)
           if conns[i].endswith('.inputCurve')}
    dead = [c for c in curveinfos if c not in fed]
    if dead:
        logger.debug(f'Deleting {len(dead)} curveInfo node(s) with no '
                     f'input curve: {", ".join(dead[:5])}')
        cmds.delete(dead)
    return len(dead)


# SETUP ================================================================

def setup_rig(fk, ik):
    '''
    Create the rig hierarchy groups and the root and cog controls, and
    connect them.

    Runs after cleanup and before any rig part is built, so every later
    phase can assume the hierarchy is there to parent into. Legacy IK
    component names are migrated here (rename_components), and RIGPARTS
    is checked before anything is created.

    Arguments
        fk (bool): Setup FK components
        ik (bool): Setup IK components
    '''
    logger.debug('-----------------------------------------------------')
    logger.info('Setup rig components')

    # The matrix OPM network needs matrixNodes; load it up front
    rt_maya.ensure_plugins()

    # NOTE: joint orientation and L/R mirroring are NOT done here. They are
    # a separate Setup phase (rig_tail_setup, run from the Tail Setup UI
    # or rig_tail_setup.setup_tails) that the user runs on the skeleton
    # before building. Keeping it out of the build means a rebuild never
    # silently re-orients joints.

    # Sync IKFK_MODES with the build options before the switch attribute
    # is created (connect_cog): IK-only builds must not offer 'FK'
    if rt_constants.update_ikfk_modes(fk, ik):
        logger.debug(f'IKFK_MODES updated for build options: {rt_constants.IKFK_MODES}')

    root_grp = rt_naming.fstr('', rt_constants.ROOT_GRP)
    root_ctrl = rt_naming.fstr('', rt_constants.ROOT_CTRL)
    cog_ctrl = rt_naming.fstr('', rt_constants.COG_CTRL)
    geometry_grp = rt_naming.fstr('', rt_constants.GEOMETRY_GRP)
    control_grp = rt_naming.fstr('', rt_constants.CONTROL_GRP)
    skeleton_grp = rt_naming.fstr('', rt_constants.SKELETON_GRP)
    rig_systems_grp = rt_naming.fstr('', rt_constants.RIG_SYSTEMS_GRP)
    clusters_grp = rt_naming.fstr('', rt_constants.CLUSTERS_GRP)

    if fk and not ik:
        groups = [geometry_grp, control_grp, skeleton_grp, rig_systems_grp, clusters_grp]
    else:
        fk_skeleton_grp = rt_naming.fstr('', rt_constants.SKELETON_GRP, rt_constants.TYPE_FK)
        ik_skeleton_grp = rt_naming.fstr('', rt_constants.SKELETON_GRP, rt_constants.TYPE_IK)
        groups = [geometry_grp, control_grp, skeleton_grp,
                  fk_skeleton_grp, ik_skeleton_grp,
                  rig_systems_grp, clusters_grp]
    rt_control.create_root_cog()

    # Create structure groups
    for group in groups:
        rt_maya.create_group(group, parent=root_grp)
        if group == geometry_grp:
            meshes = rt_maya.get_geometry_from_scene()
            for geo in meshes:
                # Refuse to create duplicate sibling names: Maya would
                # auto-rename the incoming node, and the clashing shape
                # names ('rivetsShape') break later short-name lookups
                leaf = geo.split('|')[-1]
                if cmds.objExists(f'{geometry_grp}|{leaf}'):
                    logger.warning(
                        f"Skip parenting '{geo}' under '{geometry_grp}': "
                        f"a child named '{leaf}' already exists there. "
                        f"Rename or delete one of the duplicates.")
                    continue
                rt_maya.parent_to(geo, geometry_grp)
        elif group == control_grp:
            controls = rt_maya.get_controls_from_scene()
            for ctrl in controls:
                rt_maya.parent_to(ctrl, control_grp)
        elif group == skeleton_grp:
            joints = rt_maya.get_joints_from_scene()
            for joint in joints:
                rt_maya.parent_to(joint, skeleton_grp)
        else:
            logger.trace(f"Group exists '{group}'")

    if ik: # Replace names
        rename_components()

    rt_connect.connect_root(fk, ik)
    rt_connect.connect_cog(fk, ik)
    for rigname in rt_cache.active_parts():
        # Parts without joints were skipped by set_joints/set_joints_auto
        if rigname not in rt_constants.JOINTS_BN:
            logger.warning(f"{rigname}: No joints set, skipping setup")
            continue
        rt_control.create_basectrl(rigname)
        rt_connect.connect_basectrl(rigname, fk, ik)

def set_root(root):
    '''
    Set the root name for the rig. A trailing group label is stripped
    (e.g. tail_root_grp -> tail_root). When ROOT changes between
    builds, the previous root group is renamed to the new name so the
    rig is not split across two hierarchies.

    Arguments
        root (str): New root name (with or without group suffix)
    '''
    if not root:
        logger.error(f"Invalid argument '{root}'.")
        return

    rt_constants.ROOT = rt_naming.strip_group_suffix(root)
    root_grp = rt_naming.fstr('', rt_constants.ROOT_GRP)
    logger.debug(f"Set ROOT '{rt_constants.ROOT}'")

    if cmds.objExists(root) and root != root_grp:
        # User passed an existing group name: rename to template name
        cmds.rename(root, root_grp)
    elif not cmds.objExists(root_grp):
        # ROOT changed since the rig was built: carry the existing
        # root group over to the new name
        prev_root_grp = find_existing_root_grp()
        if prev_root_grp and prev_root_grp != root_grp:
            logger.debug(
                f"ROOT changed: rename root group "
                f"'{prev_root_grp}' -> '{root_grp}'")
            cmds.rename(prev_root_grp, root_grp)
    if cmds.objExists(root_grp):
        if cmds.nodeType(root_grp) != 'transform':
            rt_maya.remove(root_grp)

def find_existing_root_grp():
    '''
    Locate the root group of a previous build regardless of its name:
    the parent of the structure groups (geometry/skeleton/controls),
    which only ever live directly under the root group.

    Return
        str or None: Existing root group, or None if no rig is built
    '''
    for template in (rt_constants.GEOMETRY_GRP, rt_constants.SKELETON_GRP,
                     rt_constants.CONTROL_GRP):
        grp = rt_naming.fstr('', template)
        if cmds.objExists(grp):
            parent = cmds.listRelatives(grp, p=True, typ='transform') or []
            if parent:
                return parent[0]
    return None


# JOINTS ===============================================================

def set_joints_auto():
    '''
    Detect or rebuild the BN/FK/IK chains for every active rig part, by
    matching scene joints against the naming template.

    One scene scan serves the whole roster (_bn_start_finder), so cost
    does not grow with the number of tails.
    '''
    logger.debug('Auto-detect joints for all RIGPARTS')

    # Excluded parts are skipped: set_joints re-duplicates the FK/IK chains
    # from BN, which would delete the joints their existing rig is built on.
    find_start = _bn_start_finder()
    for rigname in rt_cache.active_parts():
        start_jnt = find_start(rigname)
        if not start_jnt:
            logger.warning(f"{rigname}: No joints found, skipping")
            continue

        # Set joints for this rigname (will auto-detect end)
        set_joints(rigname, start_jnt=start_jnt, end_jnt=None)

def _bn_start_finder():
    '''
    Build a BN start-joint lookup that shares one scene scan across parts.

    One scene scan answers the whole roster. Resolving every joint's rigname
    through the naming template per rig part instead costs N joint listings
    and N x (joints) template matches for N tails.

    The scan is not optional. Building the start joint's name from the
    template and testing cmds.objExists is cheaper still, but it answers
    True for an ambiguous name and hands back a short one, and it cannot
    see a SECOND chain carrying the same rig part name, which is the thing
    the caller most needs told about.

    Start joints are returned as full DAG paths, and a rig part matched by
    more than one chain resolves to the one under rt_constants.ROOT (see
    bn_start_candidates). Detection has to answer with a path either way: a
    short name that two chains share is exactly what the traversal below it
    cannot use.

    Return
        callable: find(rigname) -> start joint DAG path, or None
    '''
    index = None

    def find(rigname):
        nonlocal index
        if index is None:
            index = bn_start_candidates()
        found = index.get(rigname) or []
        if not found:
            return None
        if len(found) > 1:
            chosen = _prefer_under_root(found)
            logger.warning(
                f"{rigname}: {len(found)} joint chains carry this rig part "
                f"name ({', '.join(found[:3])}). Using {chosen}. Rename the "
                f"others so each chain has its own rig part name - Tail Build "
                f"will not run until they do.")
            return chosen
        logger.debug(f"{rigname}: Found start joint '{found[0]}'")
        return found[0]

    return find


def bn_start_candidates():
    '''
    Map each rig part to EVERY BN chain start joint in the scene whose name
    resolves to exactly that rigname via the naming template, so 'tail'
    never grabs 'BN_R_tail_00_jnt' (that belongs to 'R_tail').

    Every candidate is kept, not just the first. A scene legitimately holds
    two chains for one rig part while a replacement tail is being built
    alongside the one it will replace, and the difference between picking
    one and knowing there are two is the difference between Setup mirroring
    the chain the artist meant and silently overwriting the other side from
    the wrong source.

    A start joint is a BN joint whose parent is not a BN joint of the same
    rig part, which is what stops every joint of a chain being listed as a
    candidate for it. Parenthood is read off the DAG path rather than asked
    for per joint: one scene listing answers the whole roster, where a
    listRelatives per BN joint would be one Maya call per joint in the rig.

    Return
        dict: {rigname: [joint DAG path, ...]} in scene order
    '''
    found = {}
    for j in cmds.ls(type='joint', long=True) or []:
        parts = j.split('|')
        leaf = parts[-1]
        if rt_constants.TYPE_BN not in leaf:
            continue
        rigname = rt_naming.get_rigname(leaf, rt_constants.JOINT)
        if not rigname:
            continue
        # parts[0] is '' (paths are absolute), so a world-root node has no
        # parent component to read.
        parent_leaf = parts[-2] if len(parts) > 2 else ''
        if parent_leaf and rt_naming.get_rigname(
                parent_leaf, rt_constants.JOINT) == rigname:
            continue
        found.setdefault(rigname, []).append(j)
    return found


def duplicate_rigparts(rignames=None):
    '''
    Rig parts that more than one joint chain in the scene answers to.

    Two chains may share a rig part name while a replacement tail is being
    built alongside the one it will replace - Joint Chain Builder works off
    the viewport selection, so it can tell them apart, and Setup picks the
    one under ROOT and says so. A build cannot: every node it creates is
    named after the rig part, so two chains would have it wire one rig out
    of both. Tail Build calls this and refuses rather than guess.

    Arguments
        rignames (list): rig parts to check, or None for RIGPARTS

    Return
        dict: {rigname: [joint DAG path, ...]} for the parts with more than
        one chain, empty when every part resolves to one
    '''
    wanted = set(rignames if rignames is not None else rt_constants.RIGPARTS)
    candidates = bn_start_candidates()
    return {rigname: paths for rigname, paths in candidates.items()
            if rigname in wanted and len(paths) > 1}


def _prefer_under_root(candidates):
    '''
    Pick the chain under the rig root group when a rig part name matches
    more than one.

    The chain wired into the rig is the one the rig means; a loose chain at
    the scene root is work in progress. A guess either way, so the caller
    says out loud which it took - and Tail Build refuses to guess at all.

    Arguments
        candidates (list): joint DAG paths

    Return
        str: the chosen path (the first candidate when none is under ROOT,
        or when several are)
    '''
    root = unique_path(rt_constants.ROOT)
    if root:
        under = [c for c in candidates if c.startswith(f'{root}|')]
        if under:
            return under[0]
    return candidates[0]

def _find_bn_start(rigname):
    '''
    Locate the BN start joint for a rig part, using the same detection as
    set_joints_auto: the exact BN start-joint name first, then any BN
    joint whose name resolves to exactly this rigname. Returns None when
    the part has no joints.

    Use _bn_start_finder() directly when resolving several parts in a row -
    this wrapper cannot share its scene scan with the next call.
    '''
    return _bn_start_finder()(rigname)

def detect_joints_bn():
    '''
    Populate rt_constants.JOINTS_BN for every RIGPART by chain detection only,
    with no FK/IK duplication and no renaming. Used by the Setup phase
    (rig_tail_setup.setup_tails), which re-orients the raw BN skeleton
    before any rig components exist; the build's set_joints_auto later
    creates the FK/IK chains from the oriented BN.

    Return
        list: rignames whose BN chain was found and stored
    '''
    logger.debug('Detect BN joints for all RIGPARTS (Setup phase)')
    found = []
    find_start = _bn_start_finder()
    # Detection covers the full roster so roll_chain still reaches an
    # excluded part, but only an INCLUDED part missing its chain is worth
    # saying out loud - an excluded one was held back on purpose.
    included = set(rt_cache.active_parts())
    for rigname in rt_constants.RIGPARTS:
        start_jnt = find_start(rigname)
        if not start_jnt:
            if rigname in included:
                logger.warning(f'{rigname}: No BN joints found, skipping')
            else:
                logger.debug(f'{rigname}: No BN joints found (excluded)')
            continue
        chain = rt_joint.get_joint_chain(start_jnt)
        if not chain:
            logger.warning(f'{rigname}: Empty joint chain from {start_jnt}')
            continue
        rt_constants.JOINTS_BN[rigname] = chain
        found.append(rigname)
        logger.debug(f'{rigname}: {len(chain)} BN joints detected')
    return found

def fk_ik_match_bn(rigname, tol=None):
    '''
    Whether the cached FK and IK chains are still one-to-one with BN, and on it.

    FK and IK are duplicated from BN and, at rest, sit on it. So one test
    covers everything set_joints needs to know about whether its cached
    chains are reusable - count, membership and position - without keeping
    any history to compare against: ask whether the duplicates are still
    where BN is.

    The tolerance is rt_constants.JOINT_POS_TOLERANCE, the same one
    rt_cache.validate_cache_joints uses, and for the same reason: the OPM
    network perturbs world positions by float noise. It is safe to compare
    the IK chain this way only because the solver curve now rests on the
    joints exactly (rig_tail_curve.connect_driver_to_solver_curve) - before
    that the spline settled the IK joints visibly off BN and this would
    have reported a change on every build.

    Arguments
        rigname (str): rig part to check
        tol (float): max per-joint distance, defaults to
            rt_constants.JOINT_POS_TOLERANCE

    Return
        bool: True when both duplicates track BN, False when either does
            not, or when any chain is missing
    '''
    if tol is None:
        tol = rt_constants.JOINT_POS_TOLERANCE
    bn = rt_constants.JOINTS_BN.get(rigname)
    if not bn:
        return False
    try:
        bn_pos = [cmds.xform(j, q=True, ws=True, t=True) for j in bn]
    except (RuntimeError, ValueError):
        return False
    for typ, cache in ((rt_constants.TYPE_FK, rt_constants.JOINTS_FK),
                       (rt_constants.TYPE_IK, rt_constants.JOINTS_IK)):
        dup = cache.get(rigname)
        if not dup or len(dup) != len(bn):
            logger.debug(f'{rigname}: cached {typ} chain is '
                         f'{len(dup) if dup else 0} joints against '
                         f'{len(bn)} BN, regenerating')
            return False
        for bn_jnt, dup_jnt, pos in zip(bn, dup, bn_pos):
            try:
                dist = math.dist(pos, cmds.xform(dup_jnt, q=True, ws=True, t=True))
            except (RuntimeError, ValueError):
                return False
            if dist > tol:
                logger.debug(f"{rigname}: {typ} '{dup_jnt}' is {dist:.4f} off "
                             f"'{bn_jnt}' (tol {tol}), regenerating")
                return False
    return True


def set_joints(rigname, start_jnt=None, end_jnt=None):
    '''
    Detect one rig part's BN chain and duplicate the FK and IK chains
    from it, storing all three in the joint caches.

    The BN chain is renamed in place and FK/IK duplicated from it, so the
    modeller's joints stay the ones the geometry is bound to. Joints are
    expected to follow the naming template. Cached joints that still
    exist and still validate are reused rather than rebuilt.

    Arguments
        rigname (str): Name of rig component
        start_jnt (str): First joint in chain (auto-detected if None)
        end_jnt (str): Last joint in chain (auto-detected if None)
    '''
    joints_list = [rt_constants.JOINTS_BN, rt_constants.JOINTS_FK, rt_constants.JOINTS_IK]
    types = [rt_constants.TYPE_BN, rt_constants.TYPE_FK, rt_constants.TYPE_IK]

    # Check if cached joints are still valid
    cache_valid = True
    for i, joints in enumerate(joints_list):
        if rigname in joints:
            # Verify all cached joints still exist
            if not all(cmds.objExists(j) for j in joints[rigname]):
                logger.trace(f'{rigname}: Cached {types[i]} joints invalid, rebuilding')
                cache_valid = False
                del joints[rigname]
        else:
            cache_valid = False

    # Existence is not enough. Joint Chain Builder re-spaces a chain by
    # moving, adding and removing joints, so every cached name can still
    # exist while the chain is a different length or sitting somewhere else
    # entirely - and this returned early on that, leaving FK/IK duplicated
    # at the old count and the old positions against the new BN. That is
    # what put a 51-joint BN against 38-joint FK/IK chains, which
    # rig_tail_matrix indexes one to one.
    if cache_valid and not fk_ik_match_bn(rigname):
        cache_valid = False

    # FORCE_REBUILD means 'assume nothing is reusable'. Every other test
    # here and in rig_tail_cache samples ONE property of what might have
    # changed, and a sample can miss - a scene edited outside the tool, a
    # cache carried over from an earlier state. The flag has to bypass the
    # chain caches too, or it only covers the teardown half of the decision
    # and the checkbox means two different things in two places.
    if rt_constants.FORCE_REBUILD:
        cache_valid = False

    # If all caches valid, skip rebuild
    if cache_valid:
        logger.debug(f"{rigname}: Using cached joints (all valid)")
        return

    # Detect or validate start joint
    if not start_jnt:
        start_jnt = rt_naming.fstr(rigname, rt_constants.JOINT, rt_constants.TYPE_BN, 0)
        logger.debug(f"{rigname}: Auto-detect start_jnt: {start_jnt}")

    if not cmds.objExists(start_jnt):
        logger.error(f"{rigname}: start_jnt '{start_jnt}' does not exist")
        return
    if end_jnt and not cmds.objExists(end_jnt):
        logger.error(f"{rigname}: end_jnt '{end_jnt}' does not exist")
        return

    logger.debug(f"{rigname}: Setting joints - start:{start_jnt} end:{end_jnt}")

    joint_chain = rt_joint.get_joint_chain(start_jnt, end_jnt)
    if not joint_chain:
        logger.error(f"{rigname}: No joints found")
        return
    logger.trace(f'Joint Chain: {joint_chain}')

    # Create/rename joints - always create BN
    rt_constants.JOINTS_BN[rigname] = create_rename_joints(rigname, joint_chain, rt_constants.TYPE_BN)
    logger.debug(f'{rigname}: Processed {rt_constants.TYPE_BN} joints: {len(rt_constants.JOINTS_BN[rigname])} joints')
    # Create IK/FK joints here since setup runs before build
    rt_constants.JOINTS_FK[rigname] = create_rename_joints(rigname, rt_constants.JOINTS_BN[rigname], rt_constants.TYPE_FK)
    rt_constants.JOINTS_IK[rigname] = create_rename_joints(rigname, rt_constants.JOINTS_BN[rigname], rt_constants.TYPE_IK)


def create_rename_joints(rigname, joints, typ):
    '''
    Rename or duplicate joint chains safely.

    TYPE_BN:
        - joints are the authoritative BN chain
        - rename in place only

    TYPE_FK / TYPE_IK:
        - duplicate BN root once
        - delete existing target chain
        - rename duplicated joints

    Joints come in as full DAG paths (rt_joint.get_joint_chain) and go out
    as short names, which is what the rest of the build works in. That is
    safe here and only here: rig_tail.guard_unique_rigparts has already
    refused the build if any rig part is carried by more than one chain, so
    within a build every one of these names picks out exactly one node. The
    pre-build tools have no such guarantee - two chains may share a rig part
    name right up until the build - which is why they work in paths.
    '''
    logger.trace(f"rigname:'{rigname}' joints:'{typ}'")

    # BN: rename in place
    if typ == rt_constants.TYPE_BN:
        out = []
        for jnt in joints:
            NN = rt_naming.get_index_from_name(jnt)
            new_name = rt_naming.fstr(rigname, rt_constants.JOINT, rt_constants.TYPE_BN, NN)
            # Compare leaf to leaf: a path never equals a name, so comparing
            # them whole renames every joint in the chain to the name it
            # already has.
            if jnt.split('|')[-1] != new_name:
                cmds.rename(jnt, new_name)
            if NN == 'ee':
                break
            out.append(new_name)
        return out

    # FK/IK: duplicate BN hierarchy
    # BN root must already exist
    bn_root = joints[0]
    target_root = rt_naming.fstr(rigname, rt_constants.JOINT, typ, 0)

    # Remove existing FK / IK chain cleanly
    if cmds.objExists(target_root):
        cmds.delete(target_root)

    # Duplicate entire hierarchy once
    dup_root = cmds.duplicate(bn_root, n=target_root, rc=True)[0]
    # Collect duplicated joints in DAG order
    dup_jnts = cmds.ls(dup_root, dag=True, type='joint')

    out = []
    for jnt in dup_jnts:
        NN = rt_naming.get_index_from_name(jnt)
        new_name = rt_naming.fstr(rigname, rt_constants.JOINT, typ, NN)
        if jnt != new_name:
            jnt = cmds.rename(jnt, new_name)
        if NN == 'ee':
            break
        out.append(jnt)
    return out


# RENAME ===============================================================

def rename(source, target):
    '''
    Safely rename Maya object.

    Arguments
        source (str): Current object name
        target (str): Desired object name
    '''
    if cmds.objExists(source):
        cmds.rename(source, target)
        logger.debug(f"Renamed '{source}' -> '{target}'")
    else:
        logger.trace(f"Cancel rename '{source}' -> '{target}'. Source '{source}' does not exist.")

def rigpart_has_joints(rigname):
    '''
    True if the scene contains BN joints for `rigname`, by the same
    detection set_joints_auto uses: an exact BN start joint, or any BN
    joint whose name resolves to exactly this rigname through the naming
    template.

    Callers warn on a RIGPARTS entry that would have nothing to build.

    Arguments
        rigname (str): Rig part name to check

    Return
        bool: True if BN joints exist for this rig part
    '''
    return _find_bn_start(rigname) is not None


def rename_rigpart(old, new):
    '''
    Rename a rig part in place: swap the rigname token `old` -> `new` in
    every node that carries it (joints and any already-built rig nodes),
    then update RIGPARTS and the joint caches so names stay consistent.

    The match is bounded to whole tokens, so renaming 'tail' does not
    touch 'detail' or 'tail2'. Renames are keyed by UUID (stable across
    renames) and rolled back if any single rename fails, so on failure the
    scene is left unchanged. If ROOT equals the old rigname (e.g. a single
    default tail whose root group shares the name), ROOT is updated too so
    it keeps matching the renamed root group.

    Arguments
        old (str): Current rig part name
        new (str): New rig part name

    Return
        (bool, str): (success, message). Nothing is changed on failure.
    '''
    new = (new or '').strip()
    if not new:
        return False, 'New name is empty.'
    if new == old:
        return False, 'New name is unchanged.'
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', new):
        return False, ('Invalid name. Use letters, digits and underscores; '
                       'do not start with a digit.')

    old_token = re.compile(rf'(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])')
    new_token = re.compile(rf'(?<![A-Za-z0-9]){re.escape(new)}(?![A-Za-z0-9])')

    all_nodes = cmds.ls(long=True)
    # Refuse if the new name is already used by any node (would collide)
    if any(new_token.search(n.split('|')[-1]) for n in all_nodes):
        return False, f"Name '{new}' is already used in the scene."

    targets = [n for n in all_nodes if old_token.search(n.split('|')[-1])]
    if not targets:
        # Nothing built for this part yet: just migrate list/cache state
        _migrate_rigpart_state(old, new, old_token)
        return True, f"Renamed '{old}' -> '{new}' (no scene nodes yet)."

    # UUIDs survive renames; resolve to the current path at each step so a
    # parent rename never invalidates a pending child.
    uuids = cmds.ls(targets, uuid=True)
    done = []  # (uuid, old_short) for rollback
    try:
        for uuid in uuids:
            cur = cmds.ls(uuid, long=True)
            if not cur:
                continue
            short = cur[0].split('|')[-1]
            new_short = old_token.sub(new, short)
            if new_short == short:
                continue
            cmds.rename(cur[0], new_short)
            done.append((uuid, short))
    except Exception as e:
        for uuid, old_short in reversed(done):
            cur = cmds.ls(uuid, long=True)
            if cur:
                try:
                    cmds.rename(cur[0], old_short)
                except Exception:
                    pass
        logger.warning(f"Rename '{old}' -> '{new}' failed, reverted: {e}")
        return False, f'Rename failed and was reverted: {e}'

    _migrate_rigpart_state(old, new, old_token)
    logger.debug(f"Renamed rig part '{old}' -> '{new}' ({len(done)} nodes)")
    return True, f"Renamed rig part '{old}' -> '{new}' ({len(done)} nodes)."


def _migrate_rigpart_state(old, new, old_token):
    '''Move RIGPARTS, ROOT (if it matched) and joint caches from old to new.'''
    rt_constants.RIGPARTS = [new if p == old else p for p in rt_constants.RIGPARTS]
    for jdict in (rt_constants.JOINTS_BN, rt_constants.JOINTS_FK,
                  rt_constants.JOINTS_IK, rt_constants.JOINTS_FX):
        if old in jdict:
            jdict[new] = [old_token.sub(new, j) for j in jdict.pop(old)]
    lb = rt_constants.LAST_BUILD
    lb['rigparts'] = [new if p == old else p for p in lb.get('rigparts', [])]
    jp = lb.get('joints_pos') or {}
    if old in jp:
        jp[new] = jp.pop(old)
    if rt_constants.ROOT == old:
        rt_constants.ROOT = new
        lb['root'] = new


def rename_components():
    '''
    Migrate IK controls and attributes carrying legacy names onto the
    current convention, so a rig built by an earlier version rebuilds.

    Only nodes whose names carry a legacy marker are touched: several
    replacements ('Handle' -> 'handle', 'Sml' -> '_sml', ...) would
    otherwise mangle names that already follow the current convention
    (e.g. clusterHandle, splineHandle).

    Warning: Uses hardcoded name replacements, check naming convention.
    '''
    replace_names = {
        'srt': 'grp',
        'spineRig1_': '',
        'spineRig': '',
        'splineIk': 'ik',
        'Handle': 'handle',
        'Effector': 'effector',
        'ikSplineCurve': 'ikspline_crv',
        'fkSpline': 'ik',
        'spineSpline': 'spline',
        'floatSpline': 'float',
        'cntrlBase': 'bot',
        'cntrlMid': 'mid',
        'rotMid': 'mid_rot',
        'cntrlTop': 'top',
        'Sml': '_sml',
        '__' : '_',
        'hierarchySwitch': 'switch'
        }
    # Substrings that only appear in legacy names; nodes without one
    # are already on the current convention and are left alone
    legacy_markers = ('spineRig', 'splineIk', 'ikSplineCurve', 'fkSpline',
                      'spineSpline', 'floatSpline', 'cntrlBase', 'cntrlMid',
                      'rotMid', 'cntrlTop', 'hierarchySwitch')

    def legacy_rename(node):
        if not any(marker in node for marker in legacy_markers):
            return
        node_name = node
        for old_name, new_name in replace_names.items():
            node_name = node_name.replace(old_name, new_name)
        # Renames can invalidate names listed earlier (e.g. shapes of a
        # renamed transform), so re-check existence
        if node != node_name and cmds.objExists(node):
            logger.trace(f"Rename legacy node '{node}' -> '{node_name}'")
            cmds.rename(node, node_name)

    # Ask Maya for the nodes that carry a legacy marker instead of listing
    # every node in the scene and substring-testing each one in Python.
    # This runs on every IK build, and the old form walked the full DAG
    # (tens of thousands of nodes in a character scene) x 11 markers, plus
    # a typed ls per utility type, to find the handful that ever match.
    marker_patterns = [f'*{marker}*' for marker in legacy_markers]

    # Remove old IKFK Switch attributes (change as necessary).
    # One attribute-pattern ls per switch instead of an attributeQuery on
    # every transform in the scene. The former 'IKFK Switch' entry is
    # dropped: attribute long names cannot contain spaces, so the old
    # per-node query could never have matched it.
    old_switches = ['hierarchySwitch']
    for old_switch in old_switches:
        carriers = cmds.ls(f'*.{old_switch}', o=True, r=True) or []
        for node in cmds.ls(carriers, type='transform'):
            cmds.deleteAttr(node, at=old_switch)
            rt_maya.add_attribute_enum(node, rt_constants.IKFK_DIVIDER[0], rt_constants.IKFK_DIVIDER[1], rt_constants.IKFK_DIVIDER[2])
            rt_maya.add_attribute_enum(node, rt_constants.IKFK_SWITCH[0], rt_constants.IKFK_SWITCH[1], rt_constants.IKFK_SWITCH[2], rt_constants.IKFK_SWITCH[3])

    # Rename legacy DAG nodes, then legacy utility nodes. Same two sets as
    # before - DAG nodes, and non-DAG nodes of a utility type - just
    # narrowed to marker-matching names by Maya rather than in Python.
    util_nodes = ['condition', 'multiplyDivide', 'plusMinusAverage',
                  'curveInfo', 'pointOnCurveInfo', 'blendTwoAttr',
                  'multDoubleLinear', 'pointMatrixMult', 'setRange', 'clamp',
                  'remapValue']
    legacy_nodes = list(cmds.ls(*marker_patterns, dag=True) or [])
    legacy_nodes += cmds.ls(*marker_patterns,
                            type=resolve_node_types(util_nodes)) or []
    for node in dict.fromkeys(legacy_nodes):
        legacy_rename(node)
