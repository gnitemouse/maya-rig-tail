'''
rig_tail_setup.py
author: Daisy Jane @gnitemouse

Setup phase: prepare the tail skeleton before the build.

Runs on the BN skeleton only, before any rig components exist. The build
later duplicates the FK and IK chains from this skeleton, so orienting it
here is enough. This phase is optional and never runs during the build;
launch it from the Tail Setup UI (rig_tail_setup_ui) or call
setup_tails() directly, then build as usual.

Two families of operations. The batch ones run from setup_tails and each
have a toggle in rig_tail_constants:
    ORIENT_JOINTS  aim-orient each chain so its up-axis stops twisting from
                   joint to joint. No mirroring; both sides are oriented
                   from their own geometry. The aim is fixed by the joint
                   positions, so ORIENT_UP_MODE only decides the chain's
                   ROLL about it: 'cascade' (default) carries the chain's
                   own first joint's current up down the chain, keeping the
                   roll it already has - so a re-run does not undo a mirror
                   or a roll_chain fix-up - while 'best-fit' takes the roll
                   from the chain's bend plane, ignoring how the joints
                   stand now. See aim_frames.
    MIRROR_ORIENT  reflect matching 'L_'/'R_' pairs' ORIENTATION across the
                   symmetry plane, so the two sides face as mirror images.
                   Positions unchanged. MIRROR_BEHAVIOR picks how the
                   mirrored side is rolled about its aim: 'symmetric' (the
                   same channel value moves the sides as exact mirrors -
                   both curl up together) or 'parallel' (the same channel
                   value moves them opposite ways). See mirror_frames.
    MIRROR_JOINTS  reflect matching 'L_'/'R_' pairs' POSITIONS across the
                   symmetry plane, so the target side's joints sit at the
                   exact mirror of the source side's. This is also the only
                   operation that can CREATE a chain: a pair whose target
                   side has no BN chain at all gets one built from the
                   source (create_missing_chains), as does a source-side
                   part with no counterpart in RIGPARTS at all - listing
                   'L_leg' alone is enough to have 'R_leg' built and added.
                   Mirror Orient and Orient Joints only ever rewrite joints
                   that already exist, so they warn about a missing chain
                   instead. It owns the target side's HIERARCHY as well:
                   reconcile_chain_structure hangs each target chain under
                   the mirror of its source's parent, which a world matrix
                   cannot say and which a mirror of positions alone leaves
                   wrong while reporting success.
MIRROR_ORIENT and MIRROR_JOINTS are independent (either, both, or neither);
run ORIENT_JOINTS first so a mirror copies a clean source. A typical run
enables ORIENT_JOINTS + MIRROR_ORIENT (+ MIRROR_JOINTS if the sides are
positionally off).

The remaining operation is an interactive per-chain fix-up, not a batch
toggle:
    roll_chain     roll ONE chain about its aim axis by an angle, to turn a
                   correctly-oriented-but-wrong-facing chain onto the right
                   plane. Applied on demand from the UI after the batch run;
                   no constant. Positions never change.

Orientation changes are written into jointOrient with rotate left at zero;
MIRROR_JOINTS additionally moves world positions. MIRROR_DRYRUN logs the
intended batch changes without touching anything.

Every batch operation, every L/R pairing and every warning is scoped to
rig_tail_cache.active_parts() - RIGPARTS minus
RIGPARTS_EXCLUDE - so single tails can be held back from the 'Edit Rig
Parts' editor. An excluded part keeps its BN chain detected (the
interactive roll still reaches it) but is never oriented, never mirrored,
and never unbound; excluding one side of an L/R pair stops that pair
mirroring altogether. The same exclusion holds the part back from the
build, so a tail can be set up and rigged once and then left alone while
the rest of the roster is iterated on.

A part is held back for one other reason, which the roster does not state:
a rig part name that more than one BN chain answers to. Detection can only
pick one of them arbitrarily, so the whole run leaves that part alone and
puts every candidate in REVIEW_SET rather than orient, reparent or mirror
the wrong chain of a pair - the failure mode that leaves correct-looking
joints on a chain nothing is bound to. Setup deletes neither, since either
may be the one carrying the skin.

Where the competing chain is provably damage rather than a choice -
Maya's uniquifying digits, or a left chain hanging off the right side -
mark_stray_nodes renames it '<name>_delN' first, which takes it out of the
convention and lets the rig part resolve to one chain after all. That is
the whole of the cleanup Setup does on its own: it renames, reports and
leaves the node in the scene for you to delete once you are satisfied,
because a joint that looks like garbage may still carry skin or a
constraint, and a run is one undo chunk holding hundreds of operations.

Re-orienting or moving a bound joint would drag the mesh. With KEEP_WEIGHTS
on (the default) the skin stays bound and is RE-BASELINED afterwards - each
moved joint's new world matrix is written into the skinCluster's
bindPreMatrix, so the new pose becomes the rest pose and every painted
weight survives. With it off, the affected geometry is unbound first and
left for the build to rebind, losing its weights - unless BIND_GEOMETRY is
also off, in which case nothing would rebind it and Setup preserves and
re-baselines regardless.

Functions:
    setup_tails: entry point; detect joints, run the phase, re-baseline skin
    run_setup: run the enabled batch orient/mirror steps on rt_constants.JOINTS_BN
    orient_chains: aim-orient every BN chain to remove twist (ORIENT_JOINTS)
    mirror_chains: reflect each L/R pair's orientation and/or positions
        (MIRROR_ORIENT / MIRROR_JOINTS)
    create_missing_chains: build a pair's absent target chain, stated or
        implied by a lone source side (MIRROR_JOINTS)
    reconcile_chain_structure: hang each target chain under the mirror of
        its source's parent (MIRROR_JOINTS)
    mark_stray_nodes: rename joints no rig part can own to '<name>_delN'
    roll_chain: interactively roll one chain about its aim axis
    rignames_from_selection: the RIGPARTS of every selected joint (UI)
    rigname_from_selection: resolve the RIGPART of the selected joint (UI)
    show_joint_orients: toggle the joints' local-axis display (UI check)
    find_mirror_pairs: pair rig parts by 'L_'/'R_' prefix
    aim_frames: per-joint world frames aimed down a chain, twist-free
    mirror_frames: reflect source world orientations for the target
'''

import maya.cmds as cmds
from logger_config import logger_setup
import rig_tail_constants as rt_constants
import rig_tail_cleanup as rt_cleanup
import rig_tail_cache as rt_cache
import rig_tail_maya as rt_maya
import rig_tail_naming as rt_naming
import rig_tail_mirror as rt_mirror
import math
import re

logger = logger_setup(__name__)

# Rig part prefix that marks a mirrored side, e.g. 'L_fintail'. Defined
# with the pairing it serves, in rig_tail_naming.
_SIDE_RE = rt_naming.SIDE_RE
_EPS = 1e-9

# Nodes Setup could not resolve on its own, gathered into one Maya set so
# they can be selected and dealt with by hand. Setup deletes no joint, so
# whatever it had to refuse has to stay findable after the run.
REVIEW_SET = 'rig_tail_review_SET'

# Suffix marking a joint no rig part can own. Renaming rather than deleting
# keeps the node and everything wired to it recoverable, while taking the
# name out of the convention so detection stops competing with it. Held in
# rig_tail_constants because the chain walk has to recognize it too.
STRAY_SUFFIX = rt_constants.STRAY_SUFFIX


def _cst(name):
    ''' Read a Setup setting from the constants module. '''
    return getattr(rt_constants, name)


def _active():
    '''
    Rig parts the batch operations should act on (RIGPARTS minus excluded).

    The build asks the same question of the same helper, so Setup and
    build always agree on which parts are in play.
    '''
    return rt_cache.active_parts()


# ENTRY ================================================================

def setup_tails(root=None, dry_run=None):
    '''
    Run the Setup phase on the tail skeleton.

    Detects the BN chains for every RIGPART, builds any mirror target chain
    that is missing (MIRROR_JOINTS), then runs the enabled orientation steps
    (run_setup). Re-orienting a bound joint distorts the mesh, so skinned
    geometry is re-baselined onto the new pose afterwards (KEEP_WEIGHTS,
    weights kept) or, with that off, unbound first and left for the build to
    rebind. Run this once on the raw skeleton, verify, then build.

    Usage:
        import rig_tail_setup as rt_setup
        rt_setup.setup_tails('squid')          # apply
        rt_setup.setup_tails('squid', True)    # preview only

    Arguments
        root (str): Rig root name (sets rt_constants.ROOT); skipped when None.
        dry_run (bool): Override MIRROR_DRYRUN; None uses the setting.
            When true nothing is created, unbound or modified.

    Return
        dict: summary from run_setup, plus 'created' (chains built for a
        mirror target), 'missing_chains' (included parts still without a BN
        chain), 'missing_geo' (parts with no matching mesh) and 'excluded'
        (parts held back by RIGPARTS_EXCLUDE).
    '''
    if root:
        rt_cleanup.set_root(root)

    # Same performance scope the build entry points use (viewport refresh
    # suspended, evaluation manager in DG mode, one undo chunk). Setup
    # rewrites the world matrix of every joint in every chain, and each
    # write would otherwise trigger a redraw and an EM graph rebuild -
    # exactly the churn the build already avoids. See rig_tail_maya.
    with rt_maya.build_performance_scope('rig_tail setup'), \
            rt_maya.build_timer('setup_tails') as timer:
        # Detect over the FULL roster (cheap, non-destructive) so JOINTS_BN
        # is populated for excluded parts too - the interactive roll_chain
        # fix-up still needs them. Everything destructive below is filtered
        # to the active parts.
        with timer.phase('detect'):
            found = rt_cleanup.detect_joints_bn()
        if not found:
            logger.warning('Setup: no BN joints found for any RIGPART')
            return {'oriented': 0, 'mirrored': 0, 'dry_run': True,
                    'created': [], 'marked': [], 'superseded': [],
                    'incomplete': [], 'reparented': [], 'unresolved': [],
                    'duplicates': [], 'missing_chains': list(_active()),
                    'missing_geo': [], 'excluded': rt_cache.excluded_parts()}

        excluded = rt_cache.excluded_parts()
        if excluded:
            logger.info(f'Setup: skipping {len(excluded)} excluded rig '
                        f'part(s): {", ".join(excluded)}')

        preview = dry_run if dry_run is not None \
            else bool(_cst('MIRROR_DRYRUN'))
        # A preview reasons as though the roster had grown - adoption feeds
        # the pairing every later step reads - and hands it back untouched.
        roster = list(rt_constants.RIGPARTS)

        # Marked before the roster is resolved, because a stray competes for
        # a rig part's name: taking it out of the convention is what lets
        # the part below resolve to one chain instead of being held back.
        with timer.phase('strays'):
            marked = mark_stray_nodes(preview)
        if marked and not preview:
            found = rt_cleanup.detect_joints_bn()

        # Settled before anything acts on a chain: every step below resolves
        # one chain per rig part, and orienting, reparenting or mirroring
        # the wrong one of two is the failure that reads as success.
        with timer.phase('duplicates'):
            # A preview renames nothing, so the chains marking WOULD have
            # taken out are still competing for their names here; without
            # them the preview reports an ambiguity the real run never sees.
            ambiguous = _report_duplicate_chains(
                preview, {m[3] for m in marked} if preview else set())
        skip = set(ambiguous)

        # A target side whose SHAPE disagrees with its source is superseded
        # before creation rather than reconciled after it: reconciliation
        # moves chains the names have already paired up, and these names
        # pair the wrong joints. Marking clears the names so the rebuild
        # below can take them.
        with timer.phase('supersede'):
            shape = supersede_mismatched_subtrees(preview, skip) \
                if bool(_cst('MIRROR_JOINTS')) \
                else {'superseded': [], 'incomplete': [], 'marked': []}
        marked += shape['marked']
        if shape['marked'] and not preview:
            found = rt_cleanup.detect_joints_bn()

        # Build any mirror target that has no chain yet, BEFORE the geometry
        # check and the unbind: a chain created here is a full member of this
        # run, so its mesh must be matched and its orient/mirror must happen.
        roster_before = list(rt_constants.RIGPARTS)
        with timer.phase('create'):
            created = create_missing_chains(preview, set(found), skip) \
                if bool(_cst('MIRROR_JOINTS')) else []
        # A part the roster gained by ADOPTION has a chain in the scene that
        # the first detection never looked for, so JOINTS_BN holds nothing
        # for it and every step below would read it as missing and offer to
        # create the chain it already has. A created part is already stored.
        if rt_constants.RIGPARTS != roster_before:
            with timer.phase('redetect'):
                found = rt_cleanup.detect_joints_bn()

        have = set(found) | set(created)
        # Re-read the roster rather than reuse `included`: creating an
        # implied target adds it to RIGPARTS, and it is a full member of
        # this run - it must be geometry-checked and oriented like any other.
        active = [p for p in _active() if p in have and p not in skip]
        missing_chains = _report_chain_gaps(have, created, preview)

        # Structure before orientation: both passes read each joint's parent
        # frame, so a chain still owed a move would be oriented against the
        # hierarchy it is about to leave.
        with timer.phase('reconcile'):
            structure = reconcile_chain_structure(preview, skip) \
                if bool(_cst('MIRROR_JOINTS')) \
                else {'reparented': [], 'unresolved': []}
        # Chains are stored as full DAG paths, so a reparent invalidates the
        # path of the chain that moved and of every chain under it.
        if structure['reparented'] and not preview:
            with timer.phase('redetect'):
                rt_cleanup.detect_joints_bn()

        # Warn about parts whose mesh does not follow the naming convention:
        # they are not unbound before re-orienting (so their mesh distorts)
        # nor rebound by the build. Reported so the meshes can be renamed.
        # A part is only checked once it has a chain, so a just-created
        # L side is matched against its existing mesh rather than skipped.
        missing_geo = rt_maya.report_missing_geometry(active)

        skinned = []
        if not preview:
            # Excluded parts are deliberately left bound: nothing is going
            # to move their joints, so unbinding would only throw away
            # their skin. With KEEP_WEIGHTS on, already-skinned meshes are
            # left bound too and re-baselined below instead of losing their
            # painted weights.
            with timer.phase('unbind'):
                for rigname in active:
                    if rt_maya.unbind_geometry(rigname):
                        skinned.append(rigname)

        with timer.phase('orient/mirror'):
            result = run_setup(dry_run=dry_run, skip=skip)

        # Now that the joints have moved, tell each skinCluster they drive
        # that this is its rest pose. Until this runs the mesh is dragged
        # out of shape by the re-orient.
        #
        # Every part this run touched, not only those the name-based unbind
        # returned: a mesh that does not follow the naming convention is
        # never matched, so it was never unbound and never re-baselined
        # either - the joints moved out from under a skin nothing had
        # found, which is the one case that tears the model. Tracing the
        # skinCluster from the joints reaches it whatever it is called, and
        # a part that did not move writes nothing.
        with timer.phase('rebaseline'):
            # Nothing moved in a preview, so there is no new rest pose to
            # accept - and writing one would change the skin under a run
            # that reports changing nothing. The old code was safe only by
            # accident: it iterated the list the unbind filled, which a
            # preview left empty.
            for rigname in ([] if preview
                            else sorted(set(active) | set(skinned))):
                rt_maya.rebaseline_skin(rigname)

        result['created'] = created
        result['marked'] = marked
        result['superseded'] = shape['superseded']
        result['incomplete'] = shape['incomplete']
        result['reparented'] = structure['reparented']
        result['unresolved'] = structure['unresolved']
        result['duplicates'] = ambiguous
        result['missing_chains'] = missing_chains
        result['missing_geo'] = missing_geo
        result['excluded'] = excluded
        if preview:
            rt_constants.RIGPARTS[:] = roster
        return result


def run_setup(dry_run=None, skip=None):
    '''
    Run the enabled orient and mirror steps on the BN skeleton.

    orient_chains runs first when ORIENT_JOINTS is on, then mirror_chains
    when MIRROR_ORIENT and/or MIRROR_JOINTS is on, so each L/R pair mirrors
    a clean source. Expects rt_constants.JOINTS_BN to be populated (setup_tails
    does this) and the affected geometry unbound. Orientation-only steps
    keep positions; MIRROR_JOINTS moves the target side's joints.

    Arguments
        dry_run (bool): Override MIRROR_DRYRUN; None uses the setting.
        skip (set): rignames to leave alone, on top of RIGPARTS_EXCLUDE.
            setup_tails passes the parts whose chain it could not resolve to
            one candidate; acting on an arbitrary pick is the failure this
            avoids.

    Return
        dict: {'oriented': n, 'mirrored': n, 'dry_run': bool}.
    '''
    if dry_run is None:
        dry_run = bool(_cst('MIRROR_DRYRUN'))
    skip = skip or set()
    do_orient = bool(_cst('ORIENT_JOINTS'))
    mir_orient = bool(_cst('MIRROR_ORIENT'))
    mir_joints = bool(_cst('MIRROR_JOINTS'))
    mode = ' [dry-run]' if dry_run else ''
    behavior = f' ({_behavior()})' if mir_orient else ''
    up_mode = f' ({_up_mode()})' if do_orient else ''
    logger.info(f'Setup{mode}: orient_joints={do_orient}{up_mode}, '
                f'mirror_orient={mir_orient}{behavior}, '
                f'mirror_joints={mir_joints}')

    oriented = orient_chains(dry_run, skip) if do_orient else 0
    mirrored = mirror_chains(dry_run, mir_orient, mir_joints, skip) \
        if (mir_orient or mir_joints) else 0
    if not (do_orient or mir_orient or mir_joints):
        logger.info('Setup: nothing enabled (ORIENT_JOINTS, MIRROR_ORIENT '
                    'and MIRROR_JOINTS all off)')

    # The raw skeleton is the user's to inspect and pose during Setup, so
    # keep every joint keyable and visible (the build later makes the rig
    # joints non-keyable). Skipped on a dry run, which changes nothing.
    if not dry_run:
        rt_maya.finalize_joint_channels(
            keyable=True, visibility=1, joint_dicts=[rt_constants.JOINTS_BN])
        # Only the chains this run actually touched: clearing is destructive
        # (the anchor is captured once and never re-captured, so a cleared
        # part rebuilds its rest from whatever pose BN is in), and a run with
        # every option off, or with parts held back, moved nothing.
        if do_orient or mir_orient or mir_joints:
            _clear_rest_pose([p for p in rt_cache.active_parts()
                              if p not in skip])

    return {'oriented': oriented, 'mirrored': mirrored, 'dry_run': dry_run}


def _clear_rest_pose(rignames=None):
    '''
    Clear any rest pose a previous build stamped on the BN joints.

    Re-orienting or moving the skeleton invalidates the stored rest pose
    (the rest anchor, rig_tail_restpose): the stored restMatrix describes the OLD
    orientation, so the next build would drive the IK curve from a stale
    pose and the chain would jump. Clearing it makes the build recapture
    from the corrected skeleton. Best-effort; logs and continues on failure.

    Scoped to the parts the caller actually moved. An excluded part keeps
    its stored rest, the same way Setup leaves the rest of its skeleton
    alone - clearing it would silently re-anchor a rig this run was told
    not to touch, and the anchor cannot be recovered once dropped.

    Arguments
        rignames (list): Parts to clear, or None for every part in RIGPARTS.
    '''
    try:
        import rig_tail_restpose as rt_rest
        rt_rest.clear_rest_pose(rignames)
    except Exception as err:
        logger.warning(f'Setup: could not clear stored rest pose: {err}')


def show_joint_orients(show=True):
    '''
    Toggle the local-rotation-axis display on every BN chain joint.

    A quick visual check of the orient result: Maya draws each joint's
    local X/Y/Z as a coloured cross (the joint's displayLocalAxis). Detects
    the BN joints for the current RIGPARTS first, so it works before or
    after a run and never modifies orientation.

    Arguments
        show (bool): True to show the axes, False to hide them.

    Return
        int: number of joints toggled.
    '''
    rt_cleanup.detect_joints_bn()
    val = 1 if show else 0
    count = 0
    for joints in rt_constants.JOINTS_BN.values():
        for jnt in joints:
            # Inside the try, all of it: cmds.objExists answers True for a
            # name two joints share and attributeQuery then raises on it, so
            # the check that was meant to make this safe was itself the
            # throw. Chains carry full paths now, but one bad joint must
            # still not cost the other forty-nine their axes.
            try:
                if cmds.attributeQuery('displayLocalAxis', node=jnt,
                                       exists=True):
                    cmds.setAttr(f'{jnt}.displayLocalAxis', val)
                    count += 1
            except Exception as err:
                logger.debug(f'Setup: could not toggle axes on {jnt}: {err}')
    logger.info(f'Setup: joint local axes '
                f'{"shown" if show else "hidden"} on {count} BN joints')
    return count


# OPERATIONS ===========================================================
# Both operate on the BN skeleton only (rt_constants.JOINTS_BN).

def orient_chains(dry_run, skip=None):
    '''
    Aim-orient every BN chain to remove intra-chain twist.

    Re-aims each joint down its own chain with a single up reference, so the
    up-axis stops twisting from joint to joint. ORIENT_UP_MODE picks that
    reference (see aim_frames): 'cascade' carries the chain's own first
    joint's current up down the chain, keeping the roll it already has, and
    'best-fit' derives the roll from the chain's bend plane instead.
    Applies to every rig part, both sides. Skips chains with fewer than two
    joints. To turn a single chain onto a different plane afterwards, use
    roll_chain.

    Arguments
        dry_run (bool): only log the intended changes, do not modify.
        skip (set): rignames to leave alone this run.

    Return
        int: joints re-oriented (or that would be, in a dry run).
    '''
    skip = skip or set()
    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    up_mode = _up_mode()
    mode = ' [dry-run]' if dry_run else ''
    count = 0
    for rigname in _active():
        joints = rt_constants.JOINTS_BN.get(rigname)
        if rigname in skip or not joints or len(joints) < 2:
            continue
        try:
            positions = [cmds.xform(j, q=True, ws=True, translation=True)
                         for j in joints]
            # Capture the end joint BEFORE re-orienting its parent, which
            # would swing it (see _end_joint_position).
            ee_pos = _end_joint_position(joints[-1])
            # Cascade seeds from the chain as it stands NOW, so this must be
            # read before _apply_frames rewrites it.
            up_ref = _current_up(joints[0], up_axis) \
                if up_mode == 'cascade' else None
            frames = aim_frames(positions, aim_axis, up_axis, up_ref)
            logger.info(f'Orient{mode}: aim {rigname} ({len(joints)} jnts, '
                        f'{up_mode})')
            # Re-aiming a joint swings everything below it. This chain's
            # own joints keep their positions by construction; a rig part
            # hanging off one of them has to be held there by hand.
            held = [] if dry_run else _hold_world(_foreign_children(joints))
            count += _apply_frames(joints, frames, dry_run)
            # The end ('_ee_') joint is excluded from the chain, so align it
            # to the chain's final frame or it keeps the stale orientation.
            _orient_end_joint(joints[-1], frames[-1], dry_run, position=ee_pos)
            _restore_world(held)
            if not dry_run:
                _report_twist(rigname, joints, positions, aim_axis, up_axis)
        except Exception as err:
            logger.error(f'Orient: aim failed on {rigname}: {err}')
    return count


def _report_twist(rigname, joints, positions, aim_axis, up_axis):
    '''
    Log a chain's planarity and its residual per-joint twist AFTER orienting.

    Reads back each joint's actual world matrix, so it reports what the
    skeleton really ended up as (not what was intended). Twist is the roll
    of the up axis about the aim axis between consecutive joints; a clean
    aim-orient keeps it near zero. Planarity is the summed segment-normal
    length over the summed segment length (0 = perfectly planar chain, near
    1 = highly non-planar) - a non-planar chain cannot be made fully
    twist-free about one plane normal, so a high value explains residual
    twist that is not a bug.
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    ai, ui = idx.get(aim_axis, 0), idx.get(up_axis, 2)

    # Planarity from positions.
    segs = [_sub(positions[i + 1], positions[i]) for i in range(len(positions) - 1)]
    seg_len = sum(_length(s) for s in segs) or 1.0
    normal = [0.0, 0.0, 0.0]
    for i in range(len(segs) - 1):
        normal = _add(normal, _cross(segs[i], segs[i + 1]))
    planarity = _length(normal) / (seg_len * seg_len)

    # Residual twist from the joints' actual world axes.
    rows = []
    for j in joints:
        m = cmds.xform(j, q=True, ws=True, matrix=True)
        rows.append(([m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]))
    aims = [_norm(r[ai]) for r in rows]
    ups = [_norm(r[ui]) for r in rows]
    rolls = []
    for i in range(len(joints) - 1):
        a = aims[i]
        u0 = _norm(_sub(ups[i], _scale(a, _dot(ups[i], a))))
        u1 = _norm(_sub(ups[i + 1], _scale(a, _dot(ups[i + 1], a))))
        if _length(u0) > _EPS and _length(u1) > _EPS:
            rolls.append(math.degrees(math.acos(max(-1.0, min(1.0, _dot(u0, u1))))))
    total = sum(rolls)
    mx = max(rolls) if rolls else 0.0
    logger.info(f'Orient: {rigname} residual twist total={total:.1f} '
                f'max/seg={mx:.1f} deg, planarity={planarity:.3f} '
                f'(aim={aim_axis}, up={up_axis}, {len(rolls)} segs)')


def mirror_chains(dry_run, do_orient, do_positions, skip=None):
    '''
    Reflect each L/R pair's BN chain across the symmetry plane.

    Overwrites the target side (rt_constants.MIRROR_SOURCE_SIDE picks the source)
    with the mirror of the source. Two independent effects, per the flags:
        do_orient    reflect the source ORIENTATION onto the target, so the
                     target's joints face as mirror images. Positions kept.
                     MIRROR_BEHAVIOR ('symmetric' or 'parallel') picks the
                     roll of the mirrored frames; see mirror_frames.
        do_positions reflect the source POSITIONS onto the target, so the
                     target's joints sit at the exact mirror of the source.

    The symmetry plane is assumed to pass through the world origin, with
    MIRROR_AXIS as its normal (the standard rig convention: 'x' = the YZ
    plane at x=0). Orientation is copied as-is, so it does not remove twist;
    run orient_chains first for a clean source. No-op when RIGPARTS has no
    L/R pair.

    Arguments
        dry_run (bool): only log the intended changes, do not modify.
        do_orient (bool): reflect orientation (MIRROR_ORIENT).
        do_positions (bool): reflect positions (MIRROR_JOINTS).
        skip (set): rignames to leave alone this run.

    Return
        int: joints changed (or that would be, in a dry run).
    '''
    skip = skip or set()
    axis = _cst('MIRROR_AXIS')
    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    behavior = _behavior()
    keep = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower(), 0)
    mode = ' [dry-run]' if dry_run else ''
    what = '+'.join(w for w, on in (('orient', do_orient),
                                    ('positions', do_positions)) if on)
    # Behavior only shapes a reflected ORIENTATION, so name it only then -
    # a positions-only mirror ignores it entirely.
    if do_orient:
        what = f'{what} ({behavior})'
    # Pair over the ACTIVE parts only: excluding one side of a pair means
    # the pair no longer mirrors at all, which is the right reading of
    # "leave this tail alone" - mirroring onto it would move it.
    pairs, _ = find_mirror_pairs(_active())
    if not pairs:
        logger.info(f'Mirror{mode}: no L/R pairs in RIGPARTS, skipping')
        return 0
    count = 0
    done = []
    # Parents first: mirroring positions moves joints, and a joint carries
    # its children with it, so a child placed on its mirror and then dragged
    # by its parent needs another whole run to settle.
    for source, target in _hierarchy_order(pairs):
        if source in skip or target in skip:
            continue
        src = rt_constants.JOINTS_BN.get(source)
        tgt = rt_constants.JOINTS_BN.get(target)
        # Which side is missing decides what can be done about it, so say
        # which one rather than naming the pair. The guidance itself is
        # _report_chain_gaps' job; this only records the skip.
        if not src:
            logger.warning(f'Mirror: source {source} has no BN chain, so '
                           f'{target} was not mirrored')
            continue
        if not tgt:
            if dry_run and do_positions:
                logger.info(f'Mirror [dry-run]: {target} would be created '
                            f'from {source} and mirrored')
            else:
                logger.warning(f'Mirror: {target} has no BN chain to mirror '
                               f'{source} onto')
            continue
        if len(src) != len(tgt):
            logger.warning(f'Mirror: {source} ({len(src)}) and {target} '
                           f'({len(tgt)}) differ in joint count; mirroring '
                           f'the first {min(len(src), len(tgt))}')
        try:
            src_mats = [cmds.xform(j, q=True, ws=True, matrix=True)
                        for j in src]
            before = [cmds.xform(j, q=True, ws=True, matrix=True) for j in tgt]
            # Capture the target's end joint BEFORE re-orienting its parent,
            # which would swing it (see _end_joint_position).
            tgt_ee_pos = _end_joint_position(tgt[-1])

            # Target orientation: the mirror of the source, or (orient off)
            # the target's own current orientation, kept unchanged.
            if do_orient:
                frames = mirror_frames(src_mats, axis, aim_axis, up_axis,
                                       behavior)
            else:
                frames = [_matrix_rows(m) for m in before]

            # Target positions: the reflected source positions, or (positions
            # off) None so _apply_frames keeps each joint where it is.
            positions = [_reflect([m[12], m[13], m[14]], keep)
                         for m in src_mats] if do_positions else None

            logger.debug(f'Mirror{mode}: {source} to {target} '
                         f'(axis={axis}, {what})')
            # Everything hanging off this chain that it does not own stays
            # where it is: the mirror describes THIS part's joints, not the
            # rig parts that happen to hang below them.
            held = [] if dry_run else _hold_world(_foreign_children(tgt))
            count += _apply_frames(tgt, frames, dry_run, positions=positions)

            # End joint: orient to the chain's final frame. Move it to the
            # mirrored source-end position when positions are mirrored,
            # otherwise pin it to where it started (never the swung position).
            ee_pos = tgt_ee_pos
            src_ee = _find_end_joint(src[-1])
            if do_positions and src_ee:
                sp = cmds.xform(src_ee, q=True, ws=True, translation=True)
                ee_pos = _reflect(sp, keep)
            # A source tip the target never had: _orient_end_joint only
            # aligns an end joint that exists, and only a chain built from
            # scratch gets one made for it, so a target that grew children
            # where the source keeps a tip would never acquire one.
            if src_ee and not _find_end_joint(tgt[-1]):
                _mirror_end_joint(target, tgt[-1], src_ee, ee_pos, dry_run)
            _orient_end_joint(tgt[-1], frames[-1], dry_run, position=ee_pos)
            _restore_world(held)

            done.append(target)
            if not dry_run:
                _report_mirror_delta(target, tgt, before)
        except Exception as err:
            logger.error(f'Mirror: failed on {source} to {target}: {err}')
    if done:
        logger.info(f'Mirror{mode}: {len(done)} pair(s) {what} - '
                    f'{", ".join(done)}')
    return count


def _report_mirror_delta(rigname, joints, before_mats):
    '''
    Log how much a mirror actually changed the target chain.

    Reports BOTH effects separately, because each mirror flag moves only one
    of them: the per-joint angular change (MIRROR_ORIENT) and the per-joint
    world distance moved (MIRROR_JOINTS). Reporting only one made a
    positions-only mirror read '0 joints changed' and look like the chain had
    been skipped.

    A near-zero delta on an enabled effect is still normal and does NOT mean
    the chain was skipped: it means the target already matched the source's
    mirror (common on an already-symmetric skeleton, or after orient). The
    'Mirror: <source> to <target>' line above it is what confirms the chain
    was processed.
    '''
    rot_deltas = []
    pos_deltas = []
    for jnt, m0 in zip(joints, before_mats):
        m1 = cmds.xform(jnt, q=True, ws=True, matrix=True)
        # relative rotation angle from the trace of R0^T * R1 (rows are axes)
        r0 = ([m0[0], m0[1], m0[2]], [m0[4], m0[5], m0[6]], [m0[8], m0[9], m0[10]])
        r1 = ([m1[0], m1[1], m1[2]], [m1[4], m1[5], m1[6]], [m1[8], m1[9], m1[10]])
        trace = sum(_dot(r0[k], r1[k]) for k in range(3))
        rot_deltas.append(
            math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))))
        pos_deltas.append(_length(_sub([m1[12], m1[13], m1[14]],
                                       [m0[12], m0[13], m0[14]])))
    n = len(rot_deltas)
    rot_moved = sum(1 for d in rot_deltas if d > 0.5)
    pos_moved = sum(1 for d in pos_deltas if d > 1e-4)
    rot_max = max(rot_deltas) if rot_deltas else 0.0
    pos_max = max(pos_deltas) if pos_deltas else 0.0
    line = (f'Mirror: {rigname} re-oriented {rot_moved}/{n} joints '
            f'(max {rot_max:.1f} deg), moved {pos_moved}/{n} joints '
            f'(max {pos_max:.4f} units)')
    # A pair that already matched its mirror is the ordinary case on a
    # re-run, and saying so once per part buries the ones that moved.
    if rot_moved or pos_moved:
        logger.info(line)
    else:
        logger.debug(line)


# CREATION =============================================================

def create_missing_chains(dry_run, detected=None, skip=None):
    '''
    Build the BN chain for any mirror target that has none (MIRROR_JOINTS).

    Mirror Joints defines the target side's positions entirely from the
    source, so it is the one operation that can honestly create a chain: a
    RIGPART like 'L_fintail' that is listed but has no joints gets a full
    chain mirrored from 'R_fintail', end joint included. Mirror Orient
    cannot (it has no positions to place joints at) and Orient Joints
    cannot, so both only warn - see _report_chain_gaps.

    A source-side part whose counterpart is not in RIGPARTS at all is
    treated as naming its own target (_implied_mirror_pairs): picking the
    left side's joints in 'Edit Rig Parts' is a complete statement of what
    to mirror, and requiring an 'R_leg' to be typed in beside 'L_leg'
    before Mirror Joints would build it made the roster carry a name for a
    chain that does not exist yet. The implied name is appended to
    RIGPARTS once the chain is actually built, so every later step -
    the orient/mirror pass below, the geometry check, the build - sees an
    ordinary listed rig part.

    The new joints are created with the mirrored ORIENTATION as well as the
    mirrored positions, so the chain is usable even when Mirror Orient is
    off; the enabled batch steps then run over it like any other chain.
    Pairs are taken from the included parts only, and a pair whose SOURCE
    side is the missing one is left alone - creating from nothing is not
    possible, and flipping the direction would overwrite the side that was
    actually authored.

    Arguments
        dry_run (bool): only log what would be created, create nothing.
        detected (set): rignames whose chain this run actually detected.
            JOINTS_BN is never cleared, so an entry left by an earlier run
            on a chain since deleted would otherwise read as present and
            suppress the creation. None trusts JOINTS_BN.
        skip (set): rignames to leave alone this run. A part whose own name
            resolves to two chains cannot be a creation source either - it
            is unknown which of them would be copied.

    Return
        list: target rignames whose chain was created (empty on a dry run).
        rt_constants.JOINTS_BN is updated for each, and an implied target is
        appended to rt_constants.RIGPARTS.
    '''
    if detected is None:
        detected = set(rt_constants.JOINTS_BN)
    skip = skip or set()
    pairs, _ = find_mirror_pairs(_active())
    implied = _implied_mirror_pairs(detected)
    # Detection only looks for the rig parts the roster NAMES, and an implied
    # target is added to the roster by this function - so it is never among
    # the detected and would read as missing on every run, each one leaving
    # another uniquified copy behind. The scene is the authority on what
    # already exists.
    in_scene = set(rt_cleanup.bn_start_candidates())
    created, adopted = [], []
    for source, target in _hierarchy_order(pairs + implied):
        if source not in detected or source in skip or target in skip:
            continue
        if target in detected or target in in_scene:
            # Nothing to create, but an implied target only ever reached the
            # roster by BEING created - and a part the roster does not list
            # is never paired, so it was never mirrored either. The chain
            # existing already is not a reason to leave it out of its pair.
            if _adopt_existing_target(target, source, dry_run):
                adopted.append(target)
            continue
        src = rt_constants.JOINTS_BN.get(source)
        if not src:
            continue
        # Resolved before anything is built: a chain that cannot be hung on
        # its own side is not built at all. Landing it under the SOURCE's
        # parent puts left joints on the right side of the rig, which is
        # worse than the missing chain it was standing in for.
        parent, note = _counterpart_parent(src[0])
        if note:
            logger.warning(f'Mirror: {target} was not created - it belongs '
                           f'under the mirror of {rt_maya.leaf(src[0])}\'s '
                           f'parent, but {note}. Create or mirror that '
                           f'parent first, then run Setup again.')
            continue
        if dry_run:
            listed = '' if (source, target) not in implied \
                else f', and listed as a rig part alongside {source}'
            logger.info(f'Mirror [dry-run]: would create {target} '
                        f'({len(src)} joints) mirrored from {source}{listed}')
            continue
        try:
            chain = _build_mirror_chain(src, target, parent)
        except Exception as err:
            logger.error(f'Mirror: could not create {target} from '
                         f'{source}: {err}')
            continue
        rt_constants.JOINTS_BN[target] = chain
        created.append(target)
        # Only now, with joints actually in the scene to answer to it: a
        # name added ahead of a failed build would leave the roster naming
        # a chain that is not there.
        if target not in rt_constants.RIGPARTS:
            rt_constants.RIGPARTS.append(target)
            logger.info(f'Mirror: added {target} to RIGPARTS (implied by '
                        f'{source} with Mirror Joints on)')
        logger.info(f'Mirror: created {target} ({len(chain)} joints) '
                    f'mirrored from {source}')
    if adopted:
        mode = ' [dry-run]' if dry_run else ''
        logger.info(f'Mirror{mode}: paired {len(adopted)} rig part(s) whose '
                    f'chain the scene already holds - {", ".join(adopted)}')
    return created


def _adopt_existing_target(target, source, dry_run):
    '''
    List a mirror target whose chain is already in the scene.

    Pairing reads the roster, and an implied target only reached the roster
    by being created - so a target that already existed was left unlisted,
    never paired, and never mirrored, which reads as mirroring being off for
    that part rather than as a roster gap.

    The roster is state that outlives the run and is written back to the
    config, so a preview must not touch it either.

    Return
        bool: True when the roster gained the name.
    '''
    if target in rt_constants.RIGPARTS:
        return False
    # Adopted even in a preview, because pairing reads the roster: without
    # the name there are no L/R pairs and the dry run reports nothing about
    # the mirror at all, which is the part most worth previewing. setup_tails
    # puts the roster back afterwards.
    rt_constants.RIGPARTS.append(target)
    mode = ' [dry-run]' if dry_run else ''
    would = 'would be added to' if dry_run else 'added to'
    logger.debug(f'Mirror{mode}: {target} {would} RIGPARTS (implied by '
                 f'{source}; its chain is already in the scene, so it is '
                 'paired rather than created)')
    return True


def supersede_mismatched_subtrees(dry_run, skip=None):
    '''
    Mark a target side that does not have the source side's SHAPE, so the
    mirror is built fresh instead of reconciled joint by joint.

    Reconciliation moves a chain to where its mirror belongs, which repairs
    a target whose joints correspond to the source's. It cannot repair one
    whose NAMES correspond to different joints - a side renamed on the
    source only, where 'L_finridge' is the mirror of 'R_fin' and every
    pairing is off by one. Names are the only correspondence this tool has,
    so nothing can be inferred from them once they are wrong; the shape of
    the two subtrees is what gives it away.

    Compared as the set of (rig part, its parent's rig part) with the side
    token dropped, so the two sides are describable in the same terms. Any
    difference supersedes: the whole target subtree is marked '<name>_delN'
    and create_missing_chains rebuilds it from the source, where the names
    come from the source and are right by construction.

    ONLY when every rig part of the source subtree is on the roster. A part
    that is not listed is never created, so superseding on a partial roster
    would mark a side and rebuild half of it - worse than the mismatch it
    was fixing. The missing names are reported instead.

    Nothing is deleted, and the old joints keep whatever is wired to them:
    a rebuilt side is a NEW set of joints, so skin, constraints and
    animation stay on the marked ones for you to transfer or discard.

    Arguments
        dry_run (bool): only log what would be superseded, mark nothing.
        skip (set): rignames to leave alone this run.

    Return
        dict: {'superseded': [rigname, ...], 'incomplete': [rigname, ...]}
    '''
    skip = skip or set()
    held_back = set(rt_cache.excluded_parts()) | skip
    active = set(_active())
    mode = ' [dry-run]' if dry_run else ''
    joints = cmds.ls(type='joint', long=True) or []
    candidates = rt_cleanup.bn_start_candidates()
    superseded, incomplete, marked = [], [], []

    for source_root, target_root in _mirror_subtree_roots(candidates):
        src_parts = _subtree_parts(source_root, joints)
        tgt_parts = _subtree_parts(target_root, joints)
        if held_back & (src_parts | tgt_parts):
            continue
        if _subtree_signature(source_root, joints) == \
                _subtree_signature(target_root, joints):
            continue
        # A side whose parts are all there, one joint for one joint, is not
        # a rebuild: reconciliation puts each chain under the parent the
        # mirror names and Mirror Joints moves it onto the mirrored
        # position, which between them repair a shape that is only mis-hung.
        # Rebuilding is for a target that has no joint to correspond.
        if not _joint_counts_differ(source_root, target_root, joints):
            logger.debug(
                f'Mirror: {rt_maya.leaf(target_root)} is shaped differently '
                f'from {rt_maya.leaf(source_root)} but has a joint for each '
                'of its own, so it is reconciled rather than rebuilt')
            continue

        unlisted = sorted(p for p in src_parts if p not in active)
        if unlisted:
            logger.warning(
                f'Mirror: {rt_maya.leaf(target_root)} does not have '
                f'{rt_maya.leaf(source_root)}\'s shape and cannot be rebuilt '
                f'from it, because {", ".join(unlisted)} '
                f'{"is" if len(unlisted) == 1 else "are"} not a rig part. '
                "Add them in 'Edit Rig Parts' and run Setup again; nothing "
                'was changed.')
            incomplete.extend(unlisted)
            continue

        doomed = _chain_from(target_root, None, joints)
        reason = f'superseded by a fresh mirror of {rt_maya.leaf(source_root)}'
        logger.warning(
            f'Mirror{mode}: {rt_maya.leaf(target_root)} does not have '
            f'{rt_maya.leaf(source_root)}\'s shape, so its {len(doomed)} '
            f'joint(s) are superseded and the side is rebuilt from '
            f'{", ".join(sorted(src_parts))}. Skin, constraints and '
            'animation stay on the marked joints.')
        for node in sorted(doomed, key=lambda p: -p.count('|')):
            if dry_run:
                marked.append((rt_maya.leaf(node),
                               _stray_name(rt_maya.leaf(node)), reason, node))
                continue
            done = _mark_one(node, reason)
            if done:
                marked.append(done)
        superseded.extend(sorted(tgt_parts))
    return {'superseded': superseded, 'incomplete': incomplete,
            'marked': marked}


def _joint_counts_differ(source_root, target_root, joints):
    '''
    Whether the two sides disagree about how many joints a rig part has,
    counting each side's parts by their sideless name.

    The one disagreement no amount of reparenting or mirroring can settle:
    a chain with nothing on the other side to correspond to, joint for
    joint, has to be built rather than moved.
    '''
    def census(root):
        counts = {}
        for node in joints:
            if node != root and not node.startswith(f'{root}|'):
                continue
            # An '_ee_' is not a chain member - get_joint_chain stops before
            # it, _build_mirror_chain creates it and _orient_end_joint aligns
            # it. Counting one would read a side that is merely missing its
            # tip marker as a side with no joint to correspond, and send a
            # chain the mirror could finish off to be rebuilt instead.
            if rt_maya.is_end_joint(node):
                continue
            rigname = rt_naming.get_rigname(rt_maya.leaf(node),
                                            rt_constants.JOINT)
            if rigname:
                key = _sideless(rigname)
                counts[key] = counts.get(key, 0) + 1
        return counts
    return census(source_root) != census(target_root)


def _mirror_subtree_roots(candidates):
    '''
    Source-side chains that top a mirrored subtree, paired with the target
    chain they are mirrored onto.

    A subtree root is a source-side chain whose parent is not itself part of
    the same side's subtree, so one root answers for a whole wing rather
    than each joint of it claiming to be its own.

    Return
        list: [(source root path, target root path), ...]
    '''
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    roots = []
    for rigname, starts in candidates.items():
        if len(starts) != 1 or _side_of(rigname) != source_side:
            continue
        start = starts[0]
        parent = _parent_of(start)
        parent_rig = rt_naming.get_rigname(rt_maya.leaf(parent),
                                           rt_constants.JOINT) if parent else None
        if parent_rig and _side_of(parent_rig) == source_side:
            continue
        target = _swap_side(rt_maya.leaf(start))
        found = cmds.ls(target, long=True) or []
        if len(found) == 1:
            roots.append((start, found[0]))
    return roots


def _subtree_parts(root, joints):
    ''' Every rig part named by a joint at or under root. '''
    parts = set()
    for node in joints:
        if node != root and not node.startswith(f'{root}|'):
            continue
        rigname = rt_naming.get_rigname(rt_maya.leaf(node),
                                        rt_constants.JOINT)
        if rigname:
            parts.add(rigname)
    return parts


def _subtree_signature(root, joints):
    '''
    A subtree's shape as (rig part, parent rig part) pairs with the side
    token dropped, so the two sides are described in the same terms.

    Joints of one rig part collapse to a single entry, so a chain's LENGTH
    is deliberately not part of the shape - the mirror derives that from the
    source, and a target one joint short is a reconcile, not a rebuild.
    '''
    signature = set()
    for node in joints:
        if node != root and not node.startswith(f'{root}|'):
            continue
        rigname = rt_naming.get_rigname(rt_maya.leaf(node),
                                        rt_constants.JOINT)
        if not rigname:
            continue
        parent = _parent_of(node)
        parent_rig = rt_naming.get_rigname(rt_maya.leaf(parent),
                                           rt_constants.JOINT) if parent else None
        if parent_rig == rigname:
            continue
        signature.add((_sideless(rigname), _sideless(parent_rig)))
    return signature


def _sideless(rigname):
    ''' A rig part name without its side token, so 'L_fin' and 'R_fin' are
    the same thing said twice. '''
    match = _SIDE_RE.match(rigname) if rigname else None
    return match.group(2) if match else rigname


def _hierarchy_order(pairs):
    '''
    Order pairs so a chain is dealt with before anything that hangs off it.

    Creation needs it because a created chain hangs under the MIRROR of the
    source's parent, which only works if that mirror already exists. Three
    chains off one leg - 'L_leg' the pivot, 'L_rear_wing' and 'L_rear_eye'
    below it - have to be built root first, or the two lower ones have
    nowhere on their own side to hang and are refused.

    Mirroring needs it for the opposite reason: a world matrix is written
    per rig part, but moving a joint carries its children with it. A child
    placed on its mirrored position and THEN dragged by its parent's move
    ends up off it, and the run converges only when a later pass finds the
    parent already in place - so the pair reads as still moving on a second
    run of a Setup that changed nothing.

    Roster order happens to get this right when the names sort that way and
    silently wrong when they do not, so it is not left to the roster: the
    order comes from the SOURCE side's own shape, shallower roots first and
    siblings in the order they sit under their parent.

    Arguments
        pairs (list): [(source_rigname, target_rigname), ...].

    Return
        list: the same pairs, parents before children.
    '''
    def where(pair):
        joints = rt_constants.JOINTS_BN.get(pair[0]) or []
        # No chain is a pair that gets skipped anyway; sort it last rather
        # than let it claim depth 0 and jump the queue.
        if not joints:
            return (1 << 30, 0)
        return (joints[0].count('|'), _sibling_index(joints[0]))
    return sorted(pairs, key=where)


def _sibling_index(joint):
    '''
    Where a joint sits among its parent's joint children, for ordering the
    created chains the way the source side is ordered.

    Arguments
        joint (str): joint DAG path.

    Return
        int: position under its parent, 0 when it has no joint parent.
    '''
    parents = cmds.listRelatives(joint, parent=True, typ='joint',
                                 fullPath=True) or []
    if not parents:
        return 0
    kids = cmds.listRelatives(parents[0], children=True, typ='joint',
                              fullPath=True) or []
    return kids.index(joint) if joint in kids else 0


def _implied_mirror_pairs(detected):
    '''
    Pairs for source-side parts whose counterpart is not in RIGPARTS.

    find_mirror_pairs answers with the pairs the roster STATES; this adds
    the ones it implies. A part on the mirror source side that has a chain,
    and whose opposite-side name no rig part carries, names its own target:
    'L_leg' with MIRROR_SOURCE_SIDE 'L' implies 'R_leg'. Only ever adds the
    target side, so the side the artist authored is never the one written
    onto.

    The side letter is swapped in place, so a lowercase 'l_leg' implies
    'r_leg' rather than switching the roster to a second convention.

    Arguments
        detected (set): rignames whose chain this run detected.

    Return
        list: [(source_rigname, target_rigname), ...] in RIGPARTS order.
    '''
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    known = {rp.upper() for rp in rt_constants.RIGPARTS}
    pairs = []
    for rp in _active():
        m = _SIDE_RE.match(rp)
        if not m or m.group(1).upper() != source_side or rp not in detected:
            continue
        letter = 'R' if source_side == 'L' else 'L'
        # Keep the source's own case, so 'l_leg' implies 'r_leg'.
        if m.group(1).islower():
            letter = letter.lower()
        target = f'{letter}_{m.group(2)}'
        if target.upper() in known:
            continue
        pairs.append((rp, target))
    return pairs


def _build_mirror_chain(src_joints, target, parent):
    '''
    Create one chain as the mirror of another and return its joints.

    Joints are named from the naming template under the target rigname,
    carrying the source's own index per joint so the two sides number
    alike, INCLUDING when the source has no index. See _mirror_index for
    what numbering an unnumbered source would cost.

    Positions and orientations are the reflected source's, written by the
    same _apply_frames the batch mirror uses.

    Every name is claimed before the first joint is made. Maya answers a
    name that is already taken by quietly uniquifying it, and a
    'BN_L_wing_jnt1' matches no naming template, so nothing downstream can
    see it: detection reads the part as still missing and the next run
    leaves another copy behind it. Refusing leaves the scene as it was.

    Arguments
        src_joints (list): the source chain's joints, root first.
        target (str): rig part name for the new chain.
        parent (str): node to hang the chain under, or None for the world
            root. Resolved by the caller, which refuses the whole chain
            rather than let it land on the source side.

    Return
        list: the new chain's joints as full DAG paths, root first.

    Raises
        RuntimeError: a name the chain needs is already taken.
    '''
    axis = _cst('MIRROR_AXIS')
    keep = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower(), 0)
    src_mats = [cmds.xform(j, q=True, ws=True, matrix=True) for j in src_joints]
    positions = [_reflect([m[12], m[13], m[14]], keep) for m in src_mats]
    frames = mirror_frames(src_mats, axis, _cst('ORIENT_AIM_AXIS'),
                           _cst('ORIENT_UP_AXIS'))

    names = [rt_naming.fstr(target, rt_constants.JOINT, rt_constants.TYPE_BN,
                            _mirror_index(src_jnt, i))
             for i, src_jnt in enumerate(src_joints)]
    src_ee = _find_end_joint(src_joints[-1])
    if src_ee:
        names.append(rt_naming.fstr(target, rt_constants.JOINT,
                                    rt_constants.TYPE_BN, 'ee'))
    taken = [n for n in names if cmds.objExists(n)]
    if taken:
        raise RuntimeError(
            f'{len(taken)} of its joint names already exist in the scene '
            f'({", ".join(taken[:3])}). Rename or delete them, or exclude '
            f'{target} - creating it now would leave uniquified copies '
            'nothing can see.')

    chain = []
    for src_jnt, name in zip(src_joints, names):
        jnt = _create_joint(name, parent)
        _copy_joint_attrs(src_jnt, jnt)
        chain.append(jnt)
        parent = jnt
    _apply_frames(chain, frames, dry_run=False, positions=positions)

    # The '_ee_' joint is excluded from the chain, so it is created and
    # placed here rather than by _apply_frames.
    if src_ee:
        ee = _create_joint(names[-1], chain[-1])
        _copy_joint_attrs(src_ee, ee)
        ee_pos = _reflect(cmds.xform(src_ee, q=True, ws=True,
                                     translation=True), keep)
        _orient_end_joint(chain[-1], frames[-1], False, position=ee_pos)
    return chain


def _mirror_index(src_jnt, position):
    '''
    The index a created joint should carry, from the source joint's own.

    The two sides must read as the same name but for the side token, so
    the source's index is carried across AS IT IS, its absence included. A
    one-joint chain is commonly authored unnumbered, and numbering the
    mirror of 'BN_L_leg_jnt' as 'BN_R_leg_00_jnt' breaks the pair twice
    over: the names stop matching, and the counterpart lookup for
    'BN_R_leg_jnt' misses the joint just created, so everything hanging off
    that pivot is refused for want of a parent on its own side.

    Arguments
        src_jnt (str): the source joint being mirrored.
        position (int): its position in the chain, the fallback numbering.

    Return
        int or str: index for rt_naming.fstr - '' for an unnumbered joint,
        which fstr collapses out of the name entirely.
    '''
    index = rt_naming.get_index_from_name(src_jnt)
    if index is None:
        return ''
    if index == 'ee':
        # get_joint_chain stops AT an '_ee_', so one inside the chain body
        # is a stray name rather than an end joint: number it by position
        # rather than mint a second '_ee_' for the target.
        return position
    return index


def _counterpart_parent(src_root):
    '''
    The opposite-side node a mirrored chain belongs under, when one can be
    named unambiguously.

    A parent whose name carries a side token ('L'/'R') resolves to its
    opposite-side counterpart, so a chain parented under an arm joint lands
    under the other arm. A center or unsided parent is shared by both sides
    and comes back as itself, which covers the usual case of both tails
    hanging off the same body joint.

    The source's own parent is never offered as a substitute, by any caller.
    It is the wrong side, and a left chain hanging off a right one reads as
    success in every view except the outliner while binding the two sides
    together - so a chain that cannot be placed on its own side is not
    created and not moved, only reported.

    Arguments
        src_root (str): the source chain's root joint.

    Return
        tuple: (parent DAG path or None, note). None means the world root
        when the note is empty and an unresolvable counterpart when it is
        not, so the two cases stay distinguishable.
    '''
    parent = _parent_of(src_root)
    if not parent:
        return None, ''
    counterpart = _swap_side(rt_maya.leaf(parent))
    if counterpart == rt_maya.leaf(parent):
        return parent, ''
    found = cmds.ls(counterpart, long=True) or []
    if len(found) == 1:
        return found[0], ''
    if len(found) > 1:
        return None, f"{len(found)} nodes are named '{counterpart}'"
    return None, f"there is no '{counterpart}'"


def _parent_of(node):
    ''' A node's parent as a full DAG path, or None at the world root. '''
    return (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]


def _swap_side(name):
    ''' Swap the single-letter L/R side tokens in an underscore-split name. '''
    swap = {'L': 'R', 'R': 'L', 'l': 'r', 'r': 'l'}
    return '_'.join(swap.get(p, p) if len(p) == 1 else p
                    for p in name.split('_'))


def _create_joint(name, parent=None):
    '''
    Create a joint under parent and return its full DAG path.

    Created already parented, and the path rebuilt from the name Maya
    actually used: cmds.createNode answers with a short name, which is not
    usable while the scene holds another joint called the same thing, and a
    clash under one parent gets uniquified anyway.
    '''
    made = cmds.createNode('joint', name=name, parent=parent) if parent \
        else cmds.createNode('joint', name=name)
    leaf = rt_maya.leaf(made)
    if not parent:
        return f'|{leaf}'
    return f'{(cmds.ls(parent, long=True) or [parent])[0]}|{leaf}'


def _copy_joint_attrs(src, dst):
    ''' Carry the channels the rest of the pipeline reads onto a new joint,
    so a created chain matches the one it mirrors. radius is visual only,
    but a fresh joint draws at Maya's default 1.0 and swamps the chain. '''
    for attr in ('rotateOrder', 'preferredAngleX', 'preferredAngleY',
                 'preferredAngleZ', 'radius'):
        try:
            cmds.setAttr(f'{dst}.{attr}', cmds.getAttr(f'{src}.{attr}'))
        except (RuntimeError, ValueError) as err:
            logger.debug(f'Setup: could not copy {attr} {src} to {dst}: {err}')


def reconcile_chain_structure(dry_run, skip=None):
    '''
    Hang each mirror target's chain where the mirror of its source says it
    belongs (MIRROR_JOINTS).

    Mirroring writes world matrices, so a target chain under the wrong
    parent still lands every joint in the right place and still reports a
    successful mirror - while deforming through the wrong parent and
    carrying a hierarchy the source side does not have. This is the half of
    'mirror the positions' no world matrix can express: the target's ROOT
    moves under the counterpart of the source root's parent, so a pair
    agrees on structure as well as on where its joints sit.

    Only that root is ever moved. Whatever hangs below it - another rig
    part's chain, an end joint, a stray locator - rides along, which is what
    keeps a structural fix aimed at one part from tearing a nested part off
    the rig. A nested part reconciles on its own turn against its own
    source, so a chain that rode along under the wrong parent is moved to
    the right one by its own pass rather than left where its parent landed.

    Nothing is deleted and nothing outside the pair is touched. A target
    whose mirrored parent cannot be named unambiguously is left exactly
    where it is and reported: the source side's own parent is a worse home
    than the wrong one the chain already has.

    Arguments
        dry_run (bool): only log the intended moves, reparent nothing.
        skip (set): rignames to leave alone, ambiguous ones among them.

    Return
        dict: {'reparented': [rigname, ...], 'unresolved': [rigname, ...]}
    '''
    skip = skip or set()
    mode = ' [dry-run]' if dry_run else ''
    reparented, unresolved = [], []
    for source, target, src_start, tgt_start in _scene_mirror_pairs(skip):
        # Re-resolved per pair: an earlier move in this same pass rewrites
        # the stored path of everything that rode along under it.
        src_root = _live_path(src_start)
        tgt_root = _live_path(tgt_start)
        if not src_root or not tgt_root:
            continue

        want, note = _counterpart_parent(src_root)
        have = _parent_of(tgt_root)
        if note:
            logger.warning(f'Mirror: {target} belongs under the mirror of '
                           f"{rt_maya.leaf(src_root)}'s parent, but {note}. "
                           f'Leaving it under {have}. Mirror or create that '
                           'parent first, then run Setup again.')
            unresolved.append(target)
            continue
        if want == have:
            continue
        if want and (want == tgt_root or want.startswith(f'{tgt_root}|')):
            logger.warning(f'Mirror: {target} cannot hang under {want}, '
                           f'which is inside its own chain. Leaving it '
                           f'under {have}.')
            unresolved.append(target)
            continue

        logger.info(f'Mirror{mode}: reparent {target} from {have} to {want}')
        if dry_run:
            reparented.append(target)
            continue
        try:
            _reparent(tgt_root, want)
        except Exception as err:
            logger.error(f'Mirror: could not reparent {target} under '
                         f'{want}: {err}')
            unresolved.append(target)
            continue
        reparented.append(target)
    return {'reparented': reparented, 'unresolved': unresolved}


def _scene_mirror_pairs(skip=None):
    '''
    L/R pairs read off the SCENE rather than the roster.

    The roster names the parts Setup may rewrite. It does not name every
    part whose place in the hierarchy the mirror describes: a chain hanging
    off one that moves has to travel with it, listed or not. An unlisted
    L_fintail under a fin being reparented would otherwise be left behind
    on a node nothing owns, and listing it only to move it would put an
    already-rigged tail back in reach of the orient and mirror passes,
    which is the opposite of what leaving it off the roster asked for.

    An EXCLUDED part is still excluded. Only a pair where each side
    resolves to exactly one chain is offered, so nothing is derived from
    an ambiguity.

    Arguments
        skip (set): rignames to leave alone this run.

    Return
        list: [(source, target, source root path, target root path), ...]
    '''
    held_back = set(rt_cache.excluded_parts()) | (skip or set())
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    candidates = rt_cleanup.bn_start_candidates()
    pairs = []
    for rigname, starts in candidates.items():
        match = _SIDE_RE.match(rigname or '')
        if not match or len(starts) != 1:
            continue
        if match.group(1).upper() != source_side or rigname in held_back:
            continue
        letter = 'L' if source_side == 'R' else 'R'
        if match.group(1).islower():
            letter = letter.lower()
        target = f'{letter}_{match.group(2)}'
        target_starts = candidates.get(target) or []
        if target in held_back or len(target_starts) != 1:
            continue
        pairs.append((rigname, target, starts[0], target_starts[0]))
    return pairs


def _foreign_children(chain):
    '''
    Joint children hanging off a chain that are not part of it.

    Moving or re-aiming a joint carries everything below it, so a chain put
    right on its own account drags whatever else happens to hang there. A
    part Setup owns is put back on its own turn, parents being dealt with
    first; one it does not own - an already-rigged tail under a fin - would
    simply stay dragged, and it had no reason to move because the fin was
    corrected.

    The '_ee_' is left out: _orient_end_joint places it deliberately.

    Arguments
        chain (list): the chain being rewritten, as full DAG paths.

    Return
        list: joint DAG paths hanging off it that belong to something else.
    '''
    own = set(chain)
    foreign = []
    for jnt in chain:
        for child in cmds.listRelatives(jnt, typ='joint', children=True,
                                        fullPath=True) or []:
            if child not in own and not rt_maya.is_end_joint(child):
                foreign.append(child)
    return foreign


def _hold_world(nodes):
    ''' The world matrices to put back after a move that should not have
    carried these nodes with it. '''
    return [(n, cmds.xform(n, q=True, ws=True, matrix=True)) for n in nodes]


def _restore_world(held):
    '''
    Put each node back on the world matrix it was holding.

    Best-effort per node: one child that cannot be placed must not cost the
    rest of them their positions.
    '''
    for node, matrix in held:
        try:
            cmds.xform(node, ws=True, matrix=matrix)
        except (RuntimeError, ValueError) as err:
            logger.warning(f'Setup: could not hold {rt_maya.leaf(node)} '
                           f'where it was: {err}')


def _reparent(node, parent):
    '''
    Move a joint under a new parent without moving it in world.

    Maya compensates the change of parent frame only when it can WRITE the
    joint's transform. A BN joint of a built rig cannot be written: its
    translate and rotate are driven through the offsetParentMatrix network,
    so the compensation is dropped and the joint snaps onto its new parent -
    which is how an already-rigged tail ended up at its fin's origin having
    been asked only to hang somewhere else.

    So the drivers come off first, exactly as _apply_frames takes them off
    before writing a frame, and the world matrix is put back afterwards
    rather than trusted to survive. The build rebuilds the network; the
    outgoing worldMatrix that feeds a skinCluster is left alone.
    '''
    world = cmds.xform(node, q=True, ws=True, matrix=True)
    rt_maya.disconnect_all(node, source=True, destination=False)
    rt_maya.reset_opm(node)
    if parent:
        cmds.parent(node, parent)
    else:
        cmds.parent(node, world=True)
    live = _live_path(node) or node
    cmds.xform(live, ws=True, matrix=world)


def _live_path(node):
    '''
    Re-resolve a DAG path an earlier reparent in the same pass may have
    invalidated.

    Chains are stored as full paths, so moving one chain rewrites the path
    of every chain under it. Ambiguous rig parts are held back before this
    runs, which is what makes a chain root's leaf name enough to find it
    again.

    Return
        str or None: the current full path, or None when the name no longer
        resolves to exactly one node.
    '''
    found = cmds.ls(node, long=True) or []
    if len(found) == 1:
        return found[0]
    found = cmds.ls(rt_maya.leaf(node), long=True) or []
    return found[0] if len(found) == 1 else None


def mark_stray_nodes(dry_run, skip=None):
    '''
    Rename joints no rig part can own to '<name>_delN'.

    Two kinds, both left behind by a mirror that could not finish:

        UNIQUIFIED  Maya answers a name already in use by appending digits,
            so a second attempt at 'BN_L_wing_base_jnt' becomes
            'BN_L_wing_base_jnt1'. The trailing digits put it outside the
            naming template, which is the only lens this tool has, so
            detection cannot see it - and a part it cannot see reads as
            missing, which is what had every run leave one more copy behind.
        CROSS-SIDE  a chain whose rig part names one side while an ancestor
            names the other, 'BN_L_finridge_jnt' under 'BN_R_fin_jnt'. The
            two sides are separate by construction, so this cannot be
            anything but damage.

    Renamed, not deleted. A joint that looks like garbage may still be
    carrying skin, a constraint or an artist's unfinished work, and a run
    is one undo chunk holding hundreds of other operations - the cost of
    being wrong is far worse than the clutter. The rename is reversible,
    reports what it touched, and is enough on its own: it takes the name
    out of the convention, so a rig part two chains answered to resolves to
    one and the run carries on instead of holding that part back.

    Scoped to the active parts, so an excluded part's joints are left alone
    like the rest of its skeleton.

    Arguments
        dry_run (bool): only log the intended renames, rename nothing.
        skip (set): rignames to leave alone this run.

    Return
        list: [(old leaf, new leaf, reason), ...] for what was renamed.
    '''
    mode = ' [dry-run]' if dry_run else ''
    strays = _stray_joints(skip)
    marked = []
    # Deepest first: renaming a parent rewrites its descendants' paths, and
    # the shallower entries of this same scan would go stale.
    for node in sorted(strays, key=lambda p: -p.count('|')):
        reason = strays[node]
        old = rt_maya.leaf(node)
        # Why each one was picked is the detail; the summary below is what a
        # run needs to say out loud.
        logger.debug(f'Setup{mode}: {old} is {reason}, marking it '
                     f'{_stray_name(old)}')
        if dry_run:
            marked.append((old, _stray_name(old), reason, node))
            continue
        done = _mark_one(node, reason)
        if done:
            marked.append(done)
    if marked:
        names = ', '.join(new for _, new, _, _ in marked[:6])
        if len(marked) > 6:
            names += f', and {len(marked) - 6} more'
        did = 'would mark' if dry_run else 'marked'
        logger.warning(
            f'Setup{mode}: {did} {len(marked)} joint(s) no rig part can own '
            f"- {names}. They are in '{REVIEW_SET}'; nothing was deleted, so "
            'check them and delete them yourself. Raise the log level for '
            'why each was picked.')
    return marked


def _stray_joints(skip=None):
    '''
    Joints that carry a rig part's name but that no rig part can own.

    One scene scan answers for the whole roster, and each node comes back
    with the reason it was picked so the caller can report it without
    measuring twice.

    Arguments
        skip (set): rignames to leave alone this run.

    Return
        dict: {joint DAG path: reason}.
    '''
    # Held back by NAME, not by the roster. A joint competing for a rig
    # part's name blocks that name whether or not the roster lists it -
    # an unlisted 'BN_L_fin_jnt' that matches two nodes stops L_finridge
    # reconciling just as surely as a listed one. Only an EXCLUDED part is
    # deliberately off limits; an unlisted one was never spoken for.
    held_back = set(rt_cache.excluded_parts()) | (skip or set())
    reasons = {}
    joints = cmds.ls(type='joint', long=True) or []
    # Resolving a name is a regex match, and the ancestor walk below asks
    # about the SAME names over and over - every joint of a fifty-joint
    # chain shares its whole ancestry, so a scene of a few thousand joints
    # was matching hundreds of thousands of times. One answer per name.
    resolved = {}

    def rigname_of(name):
        if name not in resolved:
            resolved[name] = rt_naming.get_rigname(name, rt_constants.JOINT)
        return resolved[name]

    for node in joints:
        leaf = rt_maya.leaf(node)
        rigname = rigname_of(leaf)
        if not rigname:
            # Only a name the convention would otherwise have accepted:
            # anything else in the scene is somebody else's node.
            base = leaf.rstrip('0123456789')
            owner = rigname_of(base) if base != leaf else None
            if owner and owner not in held_back:
                reasons[node] = (f"'{base}' with Maya's uniquifying suffix, "
                                 'which no naming template matches')
            continue
        if rigname in held_back:
            continue
        side = _side_of(rigname)
        if not side:
            continue
        # A tip the source side does not keep. The mirror gives a target the
        # '_ee_' its source has; the other direction is this - a leftover
        # from when the chain was a leaf, still answering to the rig part
        # whose real tip is now a child chain.
        if rt_maya.is_end_joint(node) and _source_lacks_end_joint(rigname):
            reasons[node] = (f'an end joint for {rigname}, where its mirror '
                             'source keeps none')
            continue
        for ancestor in node.split('|')[1:-1]:
            other = _side_of(rigname_of(ancestor))
            if other and other != side:
                reasons[node] = (f'a {side} joint hanging under {ancestor} '
                                 f'on the {other} side')
                break
    reasons.update(_misplaced_duplicates(held_back, reasons, joints))
    return reasons


def _misplaced_duplicates(held_back, already, joints):
    '''
    Of several chains answering to one rig part name, the ones that are not
    where that part's mirror says it belongs.

    Two validly-named chains are normally a choice Setup refuses to make.
    They stop being a choice when the pair itself settles it: the source
    side says which parent the target belongs under, and if exactly ONE
    candidate is there, the others are in a place the mirror does not
    describe. That is the same rule reconcile_chain_structure applies, so
    resolving it here rather than holding the part back only spares a run
    that would have made the same judgement.

    Left alone when the answer is not unarguable - no source side to
    compare against, an unresolvable mirrored parent, or several candidates
    equally well placed. Marking the whole chain, not just its root: a
    renamed root would leave the joint below it reading as a fresh start
    and the ambiguity would simply move down one.

    Arguments
        held_back (set): rignames to leave alone.
        already (dict): nodes another rule has claimed.
        joints (list): every joint in the scene, as full paths.

    Return
        dict: {joint DAG path: reason}.
    '''
    reasons = {}
    candidates = rt_cleanup.bn_start_candidates()
    for rigname, starts in candidates.items():
        if len(starts) < 2 or rigname in held_back:
            continue
        source = _mirror_source_root(rigname, candidates)
        if not source:
            continue
        want, note = _counterpart_parent(source)
        if note or not want:
            continue
        placed = [s for s in starts if _parent_of(s) == want]
        if len(placed) != 1:
            continue
        for start in starts:
            if start in placed:
                continue
            for node in _chain_from(start, rigname, joints):
                if node not in already:
                    reasons[node] = (
                        f'a second {rigname} chain, not under '
                        f'{rt_maya.leaf(want)} where the mirror of '
                        f'{rt_maya.leaf(source)} puts it')
    return reasons


def _mirror_source_root(rigname, candidates):
    '''
    The root of the chain a rig part mirrors FROM, taken from the scene
    rather than the roster.

    The roster is what find_mirror_pairs reads, and the part being asked
    about here may not be on it - the scene is what has to answer. Only the
    target side gets an answer, and only when the source resolves to one
    chain, so nothing is derived from an ambiguity.

    Arguments
        rigname (str): the rig part to find a source for.
        candidates (dict): {rigname: [chain start path, ...]} for the scene.

    Return
        str or None: the source chain's root joint.
    '''
    side = _side_of(rigname)
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    if not side or side == source_side:
        return None
    match = _SIDE_RE.match(rigname)
    letter = source_side.lower() if match.group(1).islower() else source_side
    starts = candidates.get(f'{letter}_{match.group(2)}') or []
    return starts[0] if len(starts) == 1 else None


def _chain_from(start, rigname, joints):
    ''' Every joint at or under start, or only those naming one rig part
    when rigname is given, so a chain is marked whole rather than losing
    only its root. '''
    under = [j for j in joints if j == start or j.startswith(f'{start}|')]
    if rigname is None:
        return under
    return [j for j in under
            if rt_naming.get_rigname(rt_maya.leaf(j),
                                     rt_constants.JOINT) == rigname]


def _mark_one(node, reason):
    '''
    Rename one joint to '<name>_delN' and put it in the review set.

    Return
        tuple or None: (old leaf, new leaf, reason), None when the rename
        failed.
    '''
    old = rt_maya.leaf(node)
    new = _stray_name(old)
    try:
        renamed = cmds.rename(node, new)
    except (RuntimeError, ValueError) as err:
        logger.error(f'Setup: could not mark {old} as {new}: {err}')
        return None
    _flag_for_review([renamed])
    return (old, new, reason, node)


def _source_lacks_end_joint(rigname):
    '''
    Whether this rig part mirrors a source that has no end joint of its own.

    Asked by NAME rather than by walking the source chain: the '_ee_' a rig
    part would own is named by the same template on either side, so its
    absence answers the question without resolving a chain that may itself
    be ambiguous.

    Only the target side of a pair can answer True - a source part has
    nothing to be measured against.

    Arguments
        rigname (str): the rig part the end joint belongs to.

    Return
        bool: True when the mirrored side keeps no end joint here.
    '''
    side = _side_of(rigname)
    source_side = str(_cst('MIRROR_SOURCE_SIDE')).upper()
    match = _SIDE_RE.match(rigname or '')
    if not match or not side or side == source_side:
        return False
    letter = source_side.lower() if match.group(1).islower() else source_side
    source = f'{letter}_{match.group(2)}'
    # The source part has to be there at all; a name with no chain behind it
    # says nothing about what its tip should be.
    if not (cmds.ls(rt_naming.fstr(source, rt_constants.JOINT,
                                   rt_constants.TYPE_BN, ''), long=True)
            or rt_constants.JOINTS_BN.get(source)):
        return False
    return not cmds.objExists(rt_naming.fstr(source, rt_constants.JOINT,
                                             rt_constants.TYPE_BN, 'ee'))


def _side_of(rigname):
    ''' The 'L'/'R' side token of a rig part name, or None for a center or
    unsided one. '''
    match = _SIDE_RE.match(rigname) if rigname else None
    return match.group(1).upper() if match else None


def _stray_name(leaf):
    '''
    A free '<name>_delN' for a stray, with Maya's own digits stripped first
    so the mark reads against the name the joint was trying to take.
    '''
    base = leaf.rstrip('0123456789') or leaf
    index = 1
    while cmds.objExists(f'{base}{STRAY_SUFFIX}{index}'):
        index += 1
    return f'{base}{STRAY_SUFFIX}{index}'


def _report_duplicate_chains(dry_run, ignore=None):
    '''
    Hold back and flag every rig part that more than one BN chain answers to.

    Detection picks one of the candidates and says which, enough to keep a
    run moving but not enough to trust what it did: the pick is arbitrary,
    and orienting or mirroring the wrong chain of a pair leaves
    correct-looking joints on a chain nothing is bound to, which is the
    failure that reads as success. So an ambiguous part is skipped by the
    whole run rather than guessed at.

    Every candidate goes into REVIEW_SET, not just the ones detection passed
    over. Either may be the chain carrying the skin, so which to delete is
    not Setup's call to make - and Setup deletes no joint.

    Reported for every rig part name in the SCENE, not just the ones the
    roster lists. An unlisted name that matches two joints blocks the
    listed part whose mirrored parent is that name, so leaving it unsaid
    reports the symptom - a chain that would not reconcile - and never the
    cause. Only a listed part is held back by it, because only a listed
    part was going to be touched.

    Arguments
        dry_run (bool): only log, flag nothing.
        ignore (set): chain start paths marking has already taken out of the
            convention. A preview renames nothing, so without this it would
            report an ambiguity the real run resolves a step earlier.

    Return
        list: LISTED rignames with more than one chain, in RIGPARTS order.
    '''
    ignore = ignore or set()
    excluded = set(rt_cache.excluded_parts())
    duplicates = {}
    for rigname, paths in rt_cleanup.bn_start_candidates().items():
        if rigname in excluded:
            continue
        left = [p for p in paths if p not in ignore]
        if len(left) > 1:
            duplicates[rigname] = left
    if not duplicates:
        return []
    listed = set(_active())
    for rigname, paths in duplicates.items():
        blocked = f'{rigname} was left untouched this run' if rigname in listed \
            else (f'{rigname} is not a rig part here, but anything whose '
                  f'mirrored parent resolves to one of these is blocked by it')
        logger.warning(
            f'Setup: {len(paths)} BN chains carry the rig part name '
            f'{rigname} ({", ".join(paths)}). Setup cannot tell which one '
            f'the rig means, so {blocked}. Delete or rename the chain you '
            'do not want, then run Setup again.')
        if not dry_run:
            _flag_for_review(paths)
    if not dry_run:
        logger.warning(f"Setup: the ambiguous chains are in '{REVIEW_SET}' - "
                       'select it to find them.')
    return [p for p in _active() if p in duplicates]


def _flag_for_review(nodes):
    '''
    Add nodes to REVIEW_SET, creating it on first use.

    Return
        list: the nodes actually added.
    '''
    nodes = [n for n in nodes if cmds.objExists(n)]
    if not nodes:
        return []
    try:
        if cmds.objExists(REVIEW_SET):
            cmds.sets(nodes, addElement=REVIEW_SET)
        else:
            cmds.sets(nodes, name=REVIEW_SET)
    except (RuntimeError, ValueError) as err:
        logger.warning(f'Setup: could not flag {len(nodes)} node(s) for '
                       f'review: {err}')
        return []
    return nodes


def _report_chain_gaps(have, created, dry_run):
    '''
    Log which included rig parts still have no BN chain, and what that means
    for each enabled operation.

    An operation can only rewrite joints that exist, so a listed part with no
    chain is silently skipped by all three - which is exactly what makes it
    hard to spot. This says so once, scoped to the included parts and to the
    L/R pairs among them, naming what to do about each case.

    Arguments
        have (set): rignames that now have a BN chain.
        created (list): rignames whose chain was just created.
        dry_run (bool): a preview, so nothing was actually created.

    Return
        list: included rignames with no BN chain, in RIGPARTS order.
    '''
    included = _active()
    missing = [p for p in included if p not in have]
    if created:
        logger.info(f'Setup: created {len(created)} missing chain(s): '
                    f'{", ".join(created)}')
    if not missing:
        return []

    do_orient = bool(_cst('ORIENT_JOINTS'))
    mir_orient = bool(_cst('MIRROR_ORIENT'))
    mir_joints = bool(_cst('MIRROR_JOINTS'))
    gone = set(missing)
    pairs, paired = find_mirror_pairs(included)

    # Only when a mirror is actually enabled, and only one way round per
    # pair: no source means nothing to mirror FROM, no target means nothing
    # to mirror ONTO.
    for source, target in (pairs if (mir_orient or mir_joints) else ()):
        if source in gone:
            logger.warning(
                f'Mirror: {source} is the mirror source side and has no BN '
                f'chain, so {target} was not mirrored. Build {source} in '
                f'Joint Chain Builder, or set Mirror Source Side to the '
                f'{target.split("_")[0]} side.')
        elif target in gone and not mir_joints:
            logger.warning(
                f'Mirror: {target} has no BN chain and Mirror Orient cannot '
                f'create one. Enable Mirror Joints to build it from '
                f'{source}, or build it in Joint Chain Builder.')
        elif target in gone and not dry_run:
            logger.warning(f'Mirror: {target} has no BN chain and could not '
                           f'be created from {source}.')

    unpaired = [p for p in missing if p not in paired]
    if unpaired:
        logger.warning(
            f'Setup: {len(unpaired)} included rig part(s) have no BN chain '
            f'and no L/R counterpart to build one from: '
            f'{", ".join(unpaired)}. Build them in Joint Chain Builder, or '
            "move them to Exclude in 'Edit Rig Parts'.")
    if do_orient:
        logger.warning(f'Orient: skipping {len(missing)} included rig part(s) '
                       f'with no BN chain: {", ".join(missing)}')
    if not (do_orient or mir_orient or mir_joints):
        logger.warning(f'Setup: {len(missing)} included rig part(s) have no '
                       f'BN chain: {", ".join(missing)}')
    return missing


# PAIRING ==============================================================

# Pairing is a question about rig part NAMES, so it lives in
# rig_tail_naming, where the build can reach it too: rig_tail_setup.py is
# an optional install a Builder-only setup leaves off disk. Re-exported so
# rt_setup.find_mirror_pairs stays a valid call for the UI and the tests.
find_mirror_pairs = rt_naming.find_mirror_pairs


# FRAMES ===============================================================

def aim_frames(positions, aim_axis, up_axis, up_ref=None):
    '''
    Per-joint world frames that aim down the chain with a twist-free up.

    The aim comes from the positions, so the only freedom is the roll about
    it, which the up reference fixes:
        up_ref given (ORIENT_UP_MODE 'cascade'): that world vector seeds the
            first joint - normally the chain's own first joint's current up -
            and is carried down the chain by parallel transport
            (_cascade_frames). Twist goes, while the roll the chain already
            had - a mirror, or a roll_chain fix-up - is kept.
        up_ref None ('best-fit'): the chain's best-fit plane normal (the
            summed cross product of consecutive segments), stable for a
            near-planar chain, with a straight or degenerate chain falling
            back to the world axis most perpendicular to the first segment.
            Ignores how the joints stand now, so it overwrites any mirrored
            or hand-rolled roll.
    Either way each joint's up is the reference made perpendicular to its
    own aim, so the up-axis stays consistent down the chain. To turn the
    whole chain onto a different plane afterwards, roll_chain rolls it about
    the aim axis.

    Arguments
        positions (list): [[x, y, z], ...] joint world positions.
        aim_axis (str): local axis aimed down the chain, 'x'|'y'|'z'.
        up_axis (str): local axis aligned to the up reference, 'x'|'y'|'z'.
        up_ref (list): [x, y, z] world up reference, or None to derive it
            from the chain's best-fit plane. A zero-length vector is
            ignored, so a caller can pass a failed lookup straight through.

    Return
        list: one [X_row, Y_row, Z_row] world frame per joint.
    '''
    n = len(positions)
    segs = [_sub(positions[i + 1], positions[i]) for i in range(n - 1)]
    aims = [_norm(segs[i] if i < n - 1 else segs[-1]) for i in range(n)]
    if up_ref is not None and _length(up_ref) > _EPS:
        return _cascade_frames(aims, up_ref, aim_axis, up_axis)

    normal = [0.0, 0.0, 0.0]
    for i in range(len(segs) - 1):
        normal = _add(normal, _cross(segs[i], segs[i + 1]))
    if _length(normal) < _EPS:
        normal = _world_axis_perp(_norm(segs[0])) if segs else [0, 0, 1]
    normal = _norm(normal)

    frames = []
    prev_up = None
    for aim in aims:
        up = _sub(normal, _scale(aim, _dot(normal, aim)))
        if _length(up) <= _EPS:
            # aim nearly parallel to the plane normal (a joint that bends out
            # of plane, often the tip): reuse the previous joint's up so the
            # frame stays continuous. Snapping to a world axis instead would
            # flip that one joint AND break L/R mirror symmetry (a world axis
            # is not mirrored between sides).
            ref = prev_up if prev_up is not None else _world_axis_perp(aim)
            up = _sub(ref, _scale(aim, _dot(ref, aim)))
            if _length(up) <= _EPS:
                up = _world_axis_perp(aim)
        up = _norm(up)
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
        prev_up = up
    return frames


def _cascade_frames(aims, seed, aim_axis, up_axis):
    '''
    Carry one up reference down the chain, adding no twist ('cascade').

    Each joint's up is the previous joint's up rotated by the minimal
    rotation that takes the previous aim onto this one (parallel transport),
    so consecutive ups differ only by that unavoidable re-aiming and the
    relative twist about the aim is zero by construction - whatever roll the
    seed carries.

    Projecting a single fixed reference onto each aim (what the best-fit
    branch does) is only twist-free while that reference stays near the
    chain's plane normal. A seed taken from the chain's own orientation
    generally does not: after a 90 degree roll it lies IN the bend plane,
    where the projection swings with every change of aim. Hence transport.

    Arguments
        aims (list): per-joint unit aim direction, down the chain.
        seed (list): [x, y, z] world up reference for the first joint.
        aim_axis (str): local axis aimed down the chain, 'x'|'y'|'z'.
        up_axis (str): local axis aligned to the up reference, 'x'|'y'|'z'.

    Return
        list: one [X_row, Y_row, Z_row] world frame per joint.
    '''
    frames = []
    up = _norm(seed)
    prev_aim = None
    for aim in aims:
        if prev_aim is not None:
            up = _transport(up, prev_aim, aim)
        # Re-orthogonalize against this aim: the seed is rarely exactly
        # perpendicular to the first one, and transport leaves rounding.
        perp = _sub(up, _scale(aim, _dot(up, aim)))
        if _length(perp) <= _EPS:
            # Seed collapsed onto the aim (a joint whose up points down its
            # own chain). Nothing of the original roll survives, so fall
            # back to a perpendicular world axis rather than a zero vector.
            perp = _world_axis_perp(aim)
        up = _norm(perp)
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
        prev_aim = aim
    return frames


def _transport(vec, from_aim, to_aim):
    '''
    Rotate a vector by the minimal rotation taking one aim onto another.

    Arguments
        vec (list): [x, y, z] vector to carry.
        from_aim (list): unit direction the rotation starts at.
        to_aim (list): unit direction it ends at.

    Return
        list: the rotated vector (unchanged when the aims are parallel).
    '''
    axis = _cross(from_aim, to_aim)
    if _length(axis) <= _EPS:
        return vec
    angle = math.degrees(math.acos(
        max(-1.0, min(1.0, _dot(from_aim, to_aim)))))
    return _roll_about(vec, _norm(axis), angle)


def roll_chain(rigname, degrees):
    '''
    Roll one chain about its aim axis by an angle (interactive fix-up).

    Turns a chain that is correctly oriented but facing the wrong way onto
    the right plane, without moving any joint. Reads each joint's CURRENT
    world orientation and rolls it in place about the aim axis, so it
    preserves whatever the batch orient/mirror produced and just adds the
    roll (unlike orient_chains, which re-derives the frame from positions).
    A uniform roll adds no relative twist between joints.

    Meant to be run on demand from the UI after the batch orient/mirror.
    A bound joint would drag the mesh, so the chain's geometry is either
    re-baselined onto the rolled pose (KEEP_WEIGHTS, weights kept) or
    unbound and left for the build to rebind. Clears the stored rest pose
    either way.

    Arguments
        rigname (str): the RIGPART whose chain to roll.
        degrees (float): roll angle about the aim axis.

    Return
        int: joints rolled (0 if none, or on a no-op angle).
    '''
    rt_cleanup.detect_joints_bn()
    joints = rt_constants.JOINTS_BN.get(rigname)
    if not joints or len(joints) < 2:
        logger.warning(f'Roll: {rigname} has no BN chain to roll')
        return 0
    if not degrees:
        logger.info(f'Roll: {rigname} angle is 0, nothing to do')
        return 0

    aim_axis = _cst('ORIENT_AIM_AXIS')
    up_axis = _cst('ORIENT_UP_AXIS')
    idx = {'x': 0, 'y': 1, 'z': 2}
    ai, ui = idx.get(aim_axis, 0), idx.get(up_axis, 2)

    # Rewrites every joint's world matrix, same as the batch orient, so it
    # gets the same performance scope (see setup_tails)
    with rt_maya.build_performance_scope('rig_tail roll'):
        skinned = rt_maya.unbind_geometry(rigname)

        # Capture the end joint BEFORE re-orienting its parent, which would
        # swing it (see _end_joint_position).
        ee_pos = _end_joint_position(joints[-1])

        # Roll each joint's current frame about its own aim axis.
        frames = []
        for j in joints:
            rows = _matrix_rows(cmds.xform(j, q=True, ws=True, matrix=True))
            aim = _norm(rows[ai])
            up = _roll_about(_norm(rows[ui]), aim, degrees)
            frames.append(_assign_rows(aim, up, aim_axis, up_axis))
        count = _apply_frames(joints, frames, dry_run=False)
        _orient_end_joint(joints[-1], frames[-1], dry_run=False,
                          position=ee_pos)
        if skinned:
            rt_maya.rebaseline_skin(rigname)
        _clear_rest_pose()
    logger.info(f'Roll: {rigname} rolled {degrees:g} deg about {aim_axis} '
                f'({count} joints)')
    return count


def rignames_from_selection():
    '''
    Resolve the RIGPARTS of every selected node (for the UI Select button).

    Maps each node selected in Maya back to a RIGPART via the naming
    convention, so chains can be picked by clicking joints instead of typing
    their names. Any joint of a chain works - BN/FK/IK/FX or the end 'ee'
    joint - since the name resolves to the same rigname, which is why several
    joints of one chain collapse to a single entry: selecting whole chains
    across several tails yields one name per tail.

    Return
        list[str]: the matching RIGPARTS in selection order, without
        duplicates. Empty when nothing is selected or no selected node is a
        recognized rig part.
    '''
    import rig_tail_naming as rt_naming
    rignames = []
    for node in cmds.ls(selection=True) or []:
        rigname = rt_naming.get_rigname(node.split('|')[-1], rt_constants.JOINT)
        if rigname and rigname in rt_constants.RIGPARTS and rigname not in rignames:
            rignames.append(rigname)
    return rignames


def rigname_from_selection():
    '''
    Resolve the RIGPART of the first selected node (for the UI Select button).

    Single-chain form of rignames_from_selection, kept for callers that only
    ever act on one chain.

    Return
        str or None: the matching RIGPART, or None when nothing is selected
        or the selection is not a recognized rig part.
    '''
    rignames = rignames_from_selection()
    return rignames[0] if rignames else None


def mirror_frames(src_matrices, axis, aim_axis, up_axis, behavior=None):
    '''
    Mirror source world orientations across the symmetry plane.

    Reflects each axis vector ACROSS the symmetry plane (negates its
    component along the plane normal). A reflection flips handedness, so
    only the aim and up are reflected and the frame is then reassembled with
    _assign_rows - the same right-handed assembly aim_frames uses - which
    rebuilds the third axis. The mirrored side therefore aims outward
    symmetrically and stays consistent with an oriented source.

    Note this is a reflection, not a 180-degree rotation about the normal:
    rotating would leave the aim pointing the same way as the source (into
    the body) instead of to the opposite side.

    BEHAVIOR. Write each of the mirrored side's three local axes '+' when
    it points the SAME way as the mirror image of its partner's matching
    axis and '-' when it points the OPPOSITE way. aim runs down the chain
    (aim_axis), up is up_axis, roll is the remaining one. A rotation about
    an axis mirrors when that axis is '-', a translation along it when it
    is '+', and a reflection flips handedness so the count of '-' must be
    ODD - one, or all three, never two. Three of the six therefore mirror
    under every value, and the choice only moves which three:

        'mirror'     -aim -roll -up   rotations: all three  translations: none
        'symmetric'  +aim +roll -up   rotations: up         translations: aim, roll
        'parallel'   +aim -roll +up   rotations: roll       translations: aim, up

    'mirror' is Maya's mirrorJoint -mirrorBehavior and the default. Its
    '-aim' is the aim running BACK UP the chain, which the advanced twist
    has to be told (rig_tail_stretch reads rt_mirror.aim_reversed) and
    which reverses a slide along the aim (rt_mirror.translation_signs
    reports it).

    Worked example, a tail splayed along +X with up +Z: 'symmetric' and
    'parallel' both give the target aim -X (down its own chain), 'mirror'
    gives +X (back up it). 'parallel' leaves the target up at +Z, so
    +rotate about up spins both about world +Z - the +X tip rises and the
    -X tip drops. 'symmetric' gives the target up -Z, so the same +rotate
    spins the target about world -Z instead and both tips rise.

    Since 'symmetric' and 'parallel' differ only by a 180-degree roll about
    the aim, running roll_chain(target, 180) converts one into the other on
    a single chain. 'mirror' is not reachable that way - it reverses the
    aim, which no roll about the aim can do.

    Arguments
        src_matrices (list): per-joint source world matrices (16 floats).
        axis (str): symmetry-plane normal, 'x'|'y'|'z'.
        aim_axis (str): local axis aimed down the chain, 'x'|'y'|'z'.
        up_axis (str): local axis aligned to the plane normal, 'x'|'y'|'z'.
        behavior (str): 'symmetric' or 'parallel'; None reads
            rt_constants.MIRROR_BEHAVIOR. An unrecognized value falls back to
            'symmetric' with a warning.

    Return
        list: one [X_row, Y_row, Z_row] world frame per joint.
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    keep = idx.get(str(axis).lower(), 0)
    ai, ui = idx[aim_axis], idx[up_axis]
    mode = _behavior(behavior)
    # 'parallel' keeps the reflected up; the other two negate it. 'mirror'
    # negates the aim as well, and _assign_rows then lands the roll axis on
    # its own negated reflection to stay right-handed, so all three come
    # out opposite without being asked for individually.
    up_sign = 1.0 if mode == 'parallel' else -1.0
    aim_sign = -1.0 if mode == 'mirror' else 1.0
    frames = []
    for m in src_matrices:
        rows = _matrix_rows(m)
        aim = _scale(_norm(_reflect(rows[ai], keep)), aim_sign)
        up = _scale(_norm(_reflect(rows[ui], keep)), up_sign)
        frames.append(_assign_rows(aim, up, aim_axis, up_axis))
    return frames


def _behavior(behavior=None):
    '''
    Resolve and validate the mirror behavior.

    The value set and the default live in rig_tail_mirror, which the build
    reads too, so Setup and the build cannot disagree about what is valid.

    Arguments
        behavior (str): explicit value, or None to read MIRROR_BEHAVIOR.

    Return
        str: 'mirror', 'symmetric' or 'parallel'.
    '''
    value = str(behavior if behavior is not None
                else _cst('MIRROR_BEHAVIOR')).strip().lower()
    if value not in rt_mirror.BEHAVIORS:
        logger.warning(f"Mirror: unknown behavior '{value}', "
                       f"using '{rt_mirror.BEHAVIOR_DEFAULT}'")
        return rt_mirror.BEHAVIOR_DEFAULT
    return value


def _up_mode(mode=None):
    '''
    Resolve and validate the orient up-reference mode, default 'cascade'.

    Arguments
        mode (str): explicit value, or None to read ORIENT_UP_MODE.

    Return
        str: 'cascade' or 'best-fit'.
    '''
    value = str(mode if mode is not None
                else _cst('ORIENT_UP_MODE')).strip().lower()
    if value not in ('cascade', 'best-fit'):
        logger.warning(f"Orient: unknown up mode '{value}', using 'cascade'")
        return 'cascade'
    return value


def _matrix_rows(matrix):
    ''' The three axis rows (local X/Y/Z in world) of a 16-float matrix. '''
    return ([matrix[0], matrix[1], matrix[2]],
            [matrix[4], matrix[5], matrix[6]],
            [matrix[8], matrix[9], matrix[10]])


def _current_up(joint, up_axis):
    '''
    A joint's current world up-axis direction (the cascade seed).

    Read before anything re-orients the chain, so 'cascade' can keep the
    roll the joint already has. Returns None when the joint cannot be read,
    which aim_frames treats as 'derive the reference from the plane'.

    Arguments
        joint (str): the joint to read.
        up_axis (str): which local axis is the up, 'x'|'y'|'z'.

    Return
        list or None: [x, y, z] unit world vector, or None.
    '''
    ui = {'x': 0, 'y': 1, 'z': 2}.get(up_axis, 2)
    try:
        rows = _matrix_rows(cmds.xform(joint, q=True, ws=True, matrix=True))
    except Exception as err:
        logger.warning(f'Orient: could not read up axis of {joint}: {err}')
        return None
    up = rows[ui]
    return _norm(up) if _length(up) > _EPS else None


def _reflect(vec, keep):
    ''' Reflect a vector across the plane whose normal is axis index keep
    (negate that one component). '''
    return [(-v if i == keep else v) for i, v in enumerate(vec)]


def _assign_rows(aim, up, aim_axis, up_axis):
    '''
    Build a right-handed world rotation from an aim and up direction.

    Returns three axis rows (Maya order: rows 0/1/2 are the local X/Y/Z
    axes in world), placing aim on aim_axis, up on up_axis, and the cross
    product on the remaining axis, flipped if needed to stay right-handed.

    Arguments
        aim (list): unit aim direction (down the chain).
        up (list): unit up direction (perpendicular to aim).
        aim_axis (str): local axis for aim, 'x'|'y'|'z'.
        up_axis (str): local axis for up, 'x'|'y'|'z'.

    Return
        list: [X_row, Y_row, Z_row].
    '''
    idx = {'x': 0, 'y': 1, 'z': 2}
    a, u = idx[aim_axis], idx[up_axis]
    t = 3 - a - u  # remaining axis index
    third = _cross(aim, up)
    rows = [None, None, None]
    rows[a] = aim
    rows[u] = up
    rows[t] = third
    # Keep right-handed (X cross Y == Z); flip the third axis if not.
    if _dot(_cross(rows[0], rows[1]), rows[2]) < 0:
        rows[t] = _scale(third, -1.0)
    return rows


# APPLY ================================================================

def _find_end_joint(parent):
    ''' The '_ee_' child of a joint (the end/tip marker), or None.

    Full paths, and the marker tested on the child's own name: the BN chains
    carry full DAG paths (rt_joint.get_joint_chain), because a scene may hold
    two chains with the same joint names and a short one would not say which
    end joint this is.
    '''
    for c in cmds.listRelatives(parent, typ='joint', children=True,
                                fullPath=True) or []:
        if rt_maya.is_end_joint(c):
            return c
    return None


def _end_joint_position(parent):
    '''
    World position of a joint's '_ee_' child, or None when it has none.

    MUST be read BEFORE the parent is re-oriented. The end joint is a child
    excluded from the chain, so it is not re-placed by _apply_frames: it
    simply swings with its parent, because its local translate is a fixed
    offset in the parent's space. Re-aiming the parent therefore moves the
    end joint in world, and on a chain whose last bone ran along the
    NEGATIVE aim axis it swings to the far side - the end joint ends up
    pointing back up the chain. Capturing the position first and passing it
    to _orient_end_joint pins the end joint where it belongs.

    Arguments
        parent (str): last real joint of the chain.

    Return
        list or None: [x, y, z] world position.
    '''
    ee = _find_end_joint(parent)
    if not ee:
        return None
    return cmds.xform(ee, q=True, ws=True, translation=True)


def _mirror_end_joint(rigname, parent, src_ee, position, dry_run):
    '''
    Give a mirrored chain the end joint its source has and it lacks.

    An '_ee_' is excluded from the chain, so nothing in the orient or mirror
    pass creates one: _build_mirror_chain makes it for a chain built from
    scratch, and _orient_end_joint only aligns one already there. A target
    that acquired children where the source kept a tip therefore stays
    without a tip forever, and the two sides never agree.

    Arguments
        rigname (str): rig part the end joint belongs to.
        parent (str): last real joint of the target chain.
        src_ee (str): the source chain's end joint, for its channels.
        position (list): world position for the new joint.
        dry_run (bool): only log, create nothing.

    Return
        str or None: the new end joint, None on a dry run or a failure.
    '''
    name = rt_naming.fstr(rigname, rt_constants.JOINT,
                          rt_constants.TYPE_BN, 'ee')
    if dry_run:
        logger.info(f'  [dry-run] {name}: end joint created under '
                    f'{rt_maya.leaf(parent)} to match {rt_maya.leaf(src_ee)}')
        return None
    if cmds.objExists(name):
        logger.warning(f'Mirror: {rigname} has no end joint and {name} is '
                       'already taken, so none was created. Rename or '
                       'delete that node.')
        return None
    try:
        ee = _create_joint(name, parent)
    except (RuntimeError, ValueError) as err:
        logger.error(f'Mirror: could not create {name}: {err}')
        return None
    _copy_joint_attrs(src_ee, ee)
    cmds.xform(ee, ws=True, translation=position)
    logger.info(f'Mirror: created {name} to match {rt_maya.leaf(src_ee)}')
    return ee


def _orient_end_joint(parent, frame, dry_run, position=None):
    '''
    Orient the end ('_ee_') joint to continue the chain.

    get_joint_chain stops before the '_ee_' joint, so orient_chains and
    mirror_chains never touch it and it keeps the autorigger's stale
    orientation - pointing a different way from the re-oriented chain. This
    gives the end joint the last real joint's frame (the chain's final aim
    and up), so it lines up with the chain. Same driver-detach as
    _apply_frames, since a built end joint is opm-driven.

    Callers must pass position, captured with _end_joint_position BEFORE
    re-orienting the chain. The end joint swings with its parent, so by the
    time this runs its current position is already wrong; reading it here
    would bake in that swing.

    Arguments
        parent (str): last real joint of the chain.
        frame (list): [X_row, Y_row, Z_row] to apply (the last joint's).
        dry_run (bool): only log, do not modify.
        position (list): world position to place the end joint at, captured
            before the chain was re-oriented. None falls back to its current
            position, which is only correct when the parent has not moved.

    Return
        int: 1 if an end joint was oriented (or would be), else 0.
    '''
    ee = _find_end_joint(parent)
    if not ee:
        return 0
    if dry_run:
        logger.info(f'  [dry-run] {ee}: end joint aligned to chain')
        return 1
    pos = position if position is not None \
        else cmds.xform(ee, q=True, ws=True, translation=True)
    rt_maya.disconnect_all(ee, source=True, destination=False)
    rt_maya.reset_opm(ee)
    cmds.setAttr(f'{ee}.rotate', 0, 0, 0)
    cmds.setAttr(f'{ee}.jointOrient', 0, 0, 0)
    cmds.xform(ee, ws=True, matrix=_world_matrix(frame, pos))
    rot = cmds.getAttr(f'{ee}.rotate')[0]
    cmds.setAttr(f'{ee}.jointOrient', rot[0], rot[1], rot[2])
    cmds.setAttr(f'{ee}.rotate', 0, 0, 0)
    logger.debug(f'  aligned end joint {ee} to chain')
    return 1


def _apply_frames(joints, frames, dry_run, positions=None):
    '''
    Write world orientations onto joints, at kept or given world positions.

    Processes root to tip using each joint's own position, so re-orienting a
    parent cannot move a child, and there is no re-parenting to drift. The
    full world matrix is set directly (unambiguous, no euler-order
    dependence); with jointOrient zeroed the orientation lands in rotate,
    which is then moved into jointOrient with rotate cleared.

    By default each joint keeps its current world position (orientation-only
    change). Pass positions to move the joints instead - used when a mirror
    also reflects positions - one world position per joint, root first.

    If the chain is already built, its BN joints are driven by the build's
    offsetParentMatrix network. Setting a world matrix while opm is live
    would bake the opm rotation into jointOrient (flipping the result) and
    leave the build in a confused state. So each joint's INCOMING drivers
    are detached and its opm reset to identity first, turning it back into a
    plain joint - the geometry bind on the OUTGOING side is preserved, and
    the build rebuilds the opm network afterwards. Current world positions
    are captured up front, while the drivers are still live, so they are
    exact.

    Arguments
        joints (list): chain joints, root first.
        frames (list): matching [X_row, Y_row, Z_row] world frames.
        dry_run (bool): only log, do not modify.
        positions (list): world position per joint to move to; None keeps
            each joint's current position.

    Return
        int: joints re-oriented (or that would be, in a dry run).
    '''
    n = min(len(joints), len(frames))
    if positions is not None:
        n = min(n, len(positions))
    else:
        positions = [cmds.xform(joints[i], q=True, ws=True, translation=True)
                     for i in range(n)]
    if dry_run:
        for i in range(n):
            before = cmds.xform(joints[i], q=True, ws=True, ro=True)
            logger.info(f'  [dry-run] {joints[i]}: world rot '
                        f'{[round(v, 2) for v in before]} to aligned')
        return n

    for i in range(n):
        jnt = joints[i]
        # Detach build drivers (incoming only: keep the outgoing
        # worldMatrix -> skinCluster geometry bind) and clear opm so the
        # joint re-orients as a plain joint, root to tip.
        rt_maya.disconnect_all(jnt, source=True, destination=False)
        rt_maya.reset_opm(jnt)
        cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)
        cmds.setAttr(f'{jnt}.jointOrient', 0, 0, 0)
        cmds.xform(jnt, ws=True, matrix=_world_matrix(frames[i], positions[i]))
        rot = cmds.getAttr(f'{jnt}.rotate')[0]
        cmds.setAttr(f'{jnt}.jointOrient', rot[0], rot[1], rot[2])
        cmds.setAttr(f'{jnt}.rotate', 0, 0, 0)
    logger.debug(f'  re-oriented {n} joints ({joints[0]} ...)')
    return n


def _world_matrix(rows, pos):
    ''' 16-float row-major world matrix from axis rows and a position. '''
    return [rows[0][0], rows[0][1], rows[0][2], 0.0,
            rows[1][0], rows[1][1], rows[1][2], 0.0,
            rows[2][0], rows[2][1], rows[2][2], 0.0,
            pos[0], pos[1], pos[2], 1.0]


# VECTORS ==============================================================

def _sub(a, b): return [a[i] - b[i] for i in range(3)]
def _add(a, b): return [a[i] + b[i] for i in range(3)]
def _scale(a, s): return [x * s for x in a]
def _dot(a, b): return sum(a[i] * b[i] for i in range(3))
def _cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def _length(a): return math.sqrt(_dot(a, a))


def _norm(a):
    ''' Normalize a vector; returns a zero vector when its length is ~0. '''
    l = _length(a)
    return [x / l for x in a] if l > _EPS else [0.0, 0.0, 0.0]


def _roll_about(vec, axis, degrees):
    '''
    Rotate vec about a unit axis by an angle (Rodrigues' rotation).

    Full Rodrigues (does not assume vec is perpendicular to axis), so it is
    safe for any input. axis is expected to be unit length. Returns a
    normalized vector.
    '''
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    cr = _cross(axis, vec)
    d = _dot(axis, vec)
    rotated = [vec[k] * c + cr[k] * s + axis[k] * d * (1.0 - c)
               for k in range(3)]
    return _norm(rotated)


def _world_axis_perp(aim):
    ''' Unit world axis least aligned with aim, made perpendicular to it. '''
    best = min(([1, 0, 0], [0, 1, 0], [0, 0, 1]), key=lambda ax: abs(_dot(ax, aim)))
    return _norm(_sub(best, _scale(aim, _dot(best, aim))))
