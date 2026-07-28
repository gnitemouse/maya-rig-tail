'''
# rig_tail_constants.py
author: Daisy Jane @gnitemouse

User Variables and Constants for Rig Tail

All user-editable values live in this module: rig parts, build options,
naming templates (type labels, controls/joints, groups, curves/clusters,
spline, ikfk attributes) and control constants. The UI (rig_tail_ui.py)
edits these globals in place, and save_config()/load_config() round-trip
them through a JSON config file so setups can be exported and shared.

Some values are derived from others (e.g. IKFK_SWITCH embeds the enum
string joined from IKFK_MODES). After editing globals directly, call
rebuild_derived() to keep those in sync; load_config() does this
automatically.
'''

# CACHE ================================================================

# Joint dictionaries: rigname -> joints list
JOINTS_BN = dict()
JOINTS_IK = dict()
JOINTS_FK = dict()
JOINTS_FX = dict()

LAST_BUILD = {
    'rigparts': [],
    'root': '',
    'joints_pos': {},  # {rigname: [[x, y, z] per joint] from last build}
    'num_ctrl_fk': None,  # control counts of the last build; a change
    'num_ctrl_ik': None,  # forces the full teardown path on re-rig
    'indiv_fk': None,     # individual-FK toggle; a change forces rebuild
    'build_mode': None    # (fk, ik) of the last build; switching mode
                          # forces the full teardown, which strips BOTH
                          # modes so the old one leaves nothing behind
}


# USER VARIABLES =======================================================

# Rig Components: List all parts to be rigged, e.g.
# RIGPARTS = ['tail']
RIGPARTS = ['L_fintail', 'R_fintail', 'C_fintail',
    'L_sidetail', 'R_sidetail',
    'L_tail3', 'L_tail2', 'L_tail1', 'C_tail',
    'R_tail1', 'R_tail2', 'R_tail3']

# Rig parts held back from BOTH the Setup phase and the build. Names listed
# here stay in RIGPARTS - they are still part of the rig roster, still
# renameable, and still resolve for L/R pairing - they are just left alone:
# Setup skips their orient/mirror steps and leaves their geometry bound, and
# the build neither tears their rig down nor rebuilds it. Move parts between
# Include and Exclude in the 'Edit Rig Parts' editor.
#
# Roster-level work still covers every part, excluded or not: the cog keeps
# an IKFK switch and a dashboard override flag per tail, and cleanup only
# treats a dashboard node as stale when its part left RIGPARTS entirely.
#
# The entry points that name their parts outright (rig_tail_single,
# rig_tail_selected) lift the exclusion on what they were asked to build,
# so an explicit request is never a silent no-op.
RIGPARTS_EXCLUDE = []

# Root Name
ROOT = 'squid'
# Which systems to build. Held here rather than only in the UI so the
# choice survives closing and reopening the window, like every other
# setting.
BUILD_FK = True
BUILD_IK = True
# Build individual FK controls (one per joint) alongside variable-FK
# sliding controls. Requires FK. If False, only build varFK controls.
INDIV_FK = False
# Build centralized main controller dashboard (for multiple tails)
MAIN_CONTROLLER = True
# Tear down and rebuild even if joints are unchanged. Set per build by
# the UI's Force Rebuild button (or by hand before rig_tail_multiple);
# deliberately not part of the saved config.
FORCE_REBUILD = False

# SKIN =================================================================
# Keep existing skinClusters and their painted weights.
#
# The rig binds geometry named after each rig part to that part's BN
# joints, with a plain closest-distance bind. That is only ever right the
# FIRST time: once weights have been painted, a rebind throws the paint
# work away, and a mesh shared with the rest of the character (a body
# skinned to head and limb joints as well as to a tail) loses those
# influences entirely.
#
# With this on:
#   - Setup does NOT unbind before re-orienting. It re-baselines instead,
#     writing each moved joint's new world matrix into the skinCluster's
#     bindPreMatrix so the new pose becomes the rest pose (weights kept).
#   - A rebuild does NOT unbind, and binding a mesh that is already
#     skinned adds any missing rig joints as influences at weight 0
#     instead of deleting the cluster. New influences start weightless, so
#     paint them in - the mesh will not follow the tail until you do.
# With it off the old behaviour returns: unbind, re-orient, rebind from
# scratch, weights lost.
PRESERVE_SKIN = True

# SETUP PHASE ==========================================================
# Skeleton-prep options, run by the separate 'Tail Rig Setup' step
# (rig_tail_setup) BEFORE the build, never during it. Three batch
# operations, each independent:
#
# ORIENT_JOINTS - aim-orient each chain: re-aim every joint down its own
#   chain with a single up-axis (the chain's plane normal), removing the
#   intra-chain twist so a tail bends in a plane. No mirroring; both sides
#   are oriented from their own geometry.
# MIRROR_ORIENT - reflect matching 'L_'/'R_' pairs' ORIENTATION across the
#   symmetry plane, so the two sides face as mirror images. Positions kept.
# MIRROR_JOINTS - reflect matching 'L_'/'R_' pairs' POSITIONS across the
#   symmetry plane, so the target side's joints sit at the exact mirror of
#   the source side's.
#
# ORIENT_JOINTS runs first, then the mirrors, so a mirror copies a clean
# source. A typical run enables ORIENT_JOINTS + MIRROR_ORIENT (+ MIRROR_
# JOINTS when the sides are positionally off). Mirroring orientation alone
# does not remove twist; it copies the source's, twist and all. The mirrors
# only act when an L/R pair exists. To turn a single wrong-facing chain onto
# the right plane afterwards, use the interactive roll in the Setup UI
# (rig_tail_setup.roll_chain) - it has no constant.
ORIENT_JOINTS = True
# Reflect L/R orientation. Default OFF pending Maya verification: the mirror
# math (mirror_frames) was reworked to a true plane reflection (verified
# right-handed on a worked example); enable per-run to test L/R symmetry,
# then flip this default on once confirmed on a real rig.
MIRROR_ORIENT = False
# Reflect L/R positions across the symmetry plane (moves the target side's
# joints). Default OFF; enable only when the two sides are not already
# positional mirrors of each other.
MIRROR_JOINTS = False
# Only LOG the intended changes without modifying joints (safe preview);
# covers all batch operations above (orient and both mirrors).
MIRROR_DRYRUN = False
# Character symmetry-plane normal: 'x' = YZ plane (left/right along X). The
# plane is assumed to pass through the world origin.
MIRROR_AXIS = 'x'
# Authored side used as the mirror source; the other side is overwritten.
MIRROR_SOURCE_SIDE = 'R'
# How the mirrored side is rolled about its aim axis (MIRROR_ORIENT only).
# The aim axis must keep pointing down the chain (the spline IK and the
# advanced twist depend on it), so the only freedom left is the roll, and
# there are exactly two right-handed choices, 180 degrees apart:
#   'symmetric' - the same channel value moves the target as the exact
#       mirror of the source: both tails curl up together, both curl
#       outward together. Equivalent to Maya's mirrorJoint -mirrorBehavior.
#       The default, and what animators normally expect.
#   'parallel'  - the same channel value moves the target the opposite way,
#       so a splayed pair reads as one curling up while the other curls
#       down. (Formally: the mirror of the source driven by the NEGATED
#       angle, since this frame is the symmetric one rolled 180 degrees.)
# The two differ by a 180-degree roll about the aim axis, so the Setup UI's
# Roll Chain fix-up at 180 converts one into the other on a single chain.
MIRROR_BEHAVIOR = 'symmetric'
# Local axes for the aim-orient: ORIENT_AIM_AXIS runs down the chain,
# ORIENT_UP_AXIS aligns to the up reference. The interactive roll rolls
# about ORIENT_AIM_AXIS.
ORIENT_AIM_AXIS = 'x'
ORIENT_UP_AXIS = 'z'
# Where the aim-orient takes its up reference from. The aim is fixed by the
# joint positions, so this only decides the chain's ROLL about the aim:
#   'cascade'  - seed from the chain's OWN first joint as it stands now and
#       carry that down the chain. Twist still goes (one reference for every
#       joint), but the existing roll is kept, so a mirrored pair stays
#       mirrored and a Roll Chain fix-up survives a re-run. The default:
#       re-running Setup on a set-up skeleton is then non-destructive.
#   'best-fit' - derive the roll from the chain's best-fit plane normal,
#       ignoring how the joints are currently oriented. Lands a raw,
#       arbitrarily-oriented skeleton on its own bend plane in one pass, but
#       overwrites any mirrored or hand-rolled orientation.
ORIENT_UP_MODE = 'cascade'

# Max per-joint world position drift (scene units) still treated as
# "unchanged" on re-rig. Building the rig drives joints through the OPM
# network, which perturbs world positions by float noise; drift within
# this tolerance takes the light cleanup_connections path instead of a
# full teardown.
JOINT_POS_TOLERANCE = 0.001

# SKELETON DISPLAY =====================================================
# Colour each joint chain by type via the drawing override (the viewport
# wireframe / index colour, the same mechanism controls use -- NOT the
# outliner colour, which only tints outliner text). Applied at the end of
# the build. Colour names index COLOR_OVERRIDE below.
COLOR_SKELETON = True
BN_COLOR = 'blue'      # bind skeleton
IK_COLOR = 'orange'    # IK skeleton
FK_COLOR = 'purple'    # FK skeleton


# ANIMATION EFFECTS ====================================================
# Features: Build which features
EFFECTS = {
    'stretchy': True,
    'wave': True,
    'curl': True,
    'noise': True,
    'loop': True
    }

def active_rigparts():
    '''
    RIGPARTS minus RIGPARTS_EXCLUDE, in RIGPARTS order.

    The parts the batch Setup phase and the build should act on. Excluded
    names stay in RIGPARTS (see RIGPARTS_EXCLUDE) so pairing, renaming, the
    per-chain Roll Chain fix-up and the cog's per-tail attributes still see
    the full roster; the per-part work honours the exclusion.

    Read it through rig_tail_cache.active_parts(), not directly: that
    wrapper keeps working in a Maya session started before this function
    existed, since rig_tail_constants is never reloaded.

    Return
        list: included rig part names.
    '''
    excluded = set(RIGPARTS_EXCLUDE or [])
    return [p for p in RIGPARTS if p not in excluded]


def effects_enabled():
    ''' Return True if EFFECTS are enabled. '''
    global EFFECTS
    if EFFECTS['wave'] or EFFECTS['curl'] or EFFECTS['noise'] or EFFECTS['loop']:
        return True
    return False


# NAMING TEMPLATE ======================================================
# Change Naming Convention as necessary.
# If you change formatting of indices(NN),
# make sure to update DFORMAT and get_index_from_name()

# Decimal formatting for indices
DFORMAT = '{:02d}'

# Joint Types
TYPE_BN = 'BN'
TYPE_IK = 'IK'
TYPE_FK = 'FK'
TYPE_FX = 'FX'

# Naming Template: type labels
GRP = 'grp'
CTRL = 'ctrl'
JNT = 'jnt'
SDK = 'sdk'
CRV = 'crv'
CSR = 'cluster'
HDL = 'Handle'
EFF = 'effector'
VIS = 'visibility'
COND = 'condition'
CST = 'constraint'

# Naming Template: controls, joints
ROOT_CTRL = 'root_{CTRL}'
COG_CTRL = 'cog_{CTRL}'
BASECTRL_GRP = '{TYPE}_{rigname}_base_{CTRL}_{GRP}'
BASECTRL = '{TYPE}_{rigname}_base_{CTRL}'
CTRLROOT_GRP = '{TYPE}_{rigname}_root_{GRP}'
CTRL_GRP = '{TYPE}_{rigname}_{NN}_{CTRL}_{GRP}'
CONTROL = '{TYPE}_{rigname}_{NN}_{CTRL}'
JOINT = '{TYPE}_{rigname}_{NN}_{JNT}'

# Naming Template: curves, clusters
CURVE = '{TYPE}_{rigname}_{TAG}_{CRV}'
CURVE_SCALE = '{TYPE}_{rigname}_scale_{CRV}'
CURVEINFO = '{TYPE}_{rigname}_curveInfo'
CLUSTER_GRP = '{TYPE}_{rigname}_{NN}_{CSR}_{GRP}'
CLUSTER = '{TYPE}_{rigname}_{NN}_{CSR}'
CLUSTER_HANDLE = '{TYPE}_{rigname}_{NN}_{CSR}{HDL}'
UPV_CTRLGRP = '{TYPE}_{rigname}_upvec_{TAG}_{CTRL}_{GRP}'
UPV_CTRL = '{TYPE}_{rigname}_upvec_{TAG}_{CTRL}'
CLUSTER_UPV = '{TYPE}_{rigname}_upvec_{TAG}_{CSR}'
CLUSTER_UPV_HANDLE = '{TYPE}_{rigname}_upvec_{TAG}_{CSR}{HDL}'

# Naming Template: spline
SPLINE_GRP = '{TYPE}_{rigname}_spline_{GRP}'
SPLINE_HANDLE = '{TYPE}_{rigname}_spline{HDL}'
SPLINE_EFFECTOR = '{TYPE}_{rigname}_spline_{EFF}'
SPLINE_IK_CTRL = '{TYPE}_{rigname}_ik_{NN}_{CTRL}'
SPLINE_FLOAT_CTRL = '{TYPE}_{rigname}_float_{NN}_{CTRL}'
SPLINE_BOT = '{TYPE}_{rigname}_spline_bot_{CTRL}'
SPLINE_BOT_SML = '{TYPE}_{rigname}_spline_bot_sml_{CTRL}'
SPLINE_MID_ROT = '{TYPE}_{rigname}_spline_mid_rot_{CTRL}'
SPLINE_MID = '{TYPE}_{rigname}_spline_mid_{CTRL}'
SPLINE_TOP_SML = '{TYPE}_{rigname}_spline_top_sml_{CTRL}'
SPLINE_TOP = '{TYPE}_{rigname}_spline_top_{CTRL}'
SPLINE_CONTROLS = [SPLINE_BOT, SPLINE_BOT_SML, SPLINE_MID,
                   SPLINE_TOP_SML, SPLINE_TOP, SPLINE_MID_ROT]

# Naming Template: groups
ROOT_GRP = '{ROOT}'
GEOMETRY_GRP = 'geometry'
CONTROL_GRP = '{TYPE}_controls'
SKELETON_GRP = '{TYPE}_skeleton'
RIG_SYSTEMS_GRP = 'rig_systems'
CLUSTERS_GRP = 'clusters'
SCALE_GRP = '{TYPE}_{rigname}_scale_{GRP}'
SDK_GRP = '{TYPE}_{rigname}_{NN}_{nn}_{SDK}'
SDK_JNT = '{TYPE}_{rigname}_{NN}_{JNT}_{SDK}'
GROUP = '{TYPE}_{rigname}_{NN}_{GRP}'

# Naming Template: ikfk, switch, divider
IKFK = '{rigname}_ikfk'
# Mode names are positional: [0]=SplineIK, [1]=IK, [2]=Float, [3]=FK.
# Rename them freely (e.g. ['spline', 'ik', 'float', 'fk']); the build
# maps behavior to a mode by its position, not its label.
# IKFK_MODES_ALL is the full user-configured list; IKFK_MODES is the
# subset active for the current build options, derived by
# update_ikfk_modes() (IK-only drops the FK mode, FK-only has no switch).
IKFK_MODES_ALL = ['SplineIK', 'IK', 'Float', 'FK']
IKFK_MODES = list(IKFK_MODES_ALL)
# Attribute Template: (longName, niceName, enumName, dv)
# IKFK_SWITCH's enumName is derived from IKFK_MODES;
# if IKFK_MODES changes, call rebuild_derived() to regenerate it.
IKFK_SWITCH = ('ikfk_switch', 'IKFK Switch', ':'.join(IKFK_MODES), 0)
# What each IKFK switch mode does; used for UI tooltips.
IKFK_MODE_DESCRIPTIONS = {
    'SplineIK': 'Spline bot/mid/top controls shape the whole curve '
                'like a flexible spline.',
    'IK': 'Chain IK controls follow the previous one down the tail; '
          'cluster controls are nested in a chain.',
    'Float': 'Floating IK controls move freely without following; '
             'independent cluster controls.',
    'FK': 'Variable FK controls slide along the tail '
          'and rotate adjacent joints within falloff range.'
}
IKFK_DIVIDER = ('ikfk_divider', '----------', 'IKFK')
# The cog's switch section label. Shares the ikfk_divider long name with
# IKFK_DIVIDER so a rebuild relabels the existing divider in place
# (keeping its channel-box position); per-tail controls keep the plain
# IKFK label above their own switch proxy.
TAIL_IKFK_DIVIDER = (IKFK_DIVIDER[0], IKFK_DIVIDER[1], 'TAIL IKFK')
STRETCH_DIVIDER = ('stretch_divider', '----------', 'STRETCH')
ANIM_DIVIDER = ('anim_divider', '----------', 'ANIMATION')
TWIST_DIVIDER = ('twist_divider', '----------', 'TWIST')
SCALE_DIVIDER = ('scale_divider', '----------', 'JNT SCALE')

# Naming Template: main controller dashboard (see rig_tail_ctrlall).
# Real ALL values and per-tail override flags live on the cog control
# (like the per-tail IKFK switches); each basectrl carries a proxy of
# its own override flag.
ALL_DIVIDER = ('all_divider', '----------', 'ALL')
# OVERRIDE labels the per-tail flag section on the cog; OVERRIDE ALL
# labels the flag's proxy on each basectrl ('Override All' = override
# the ALL section with this tail's own values)
OVERRIDE_DIVIDER = ('override_divider', '----------', 'OVERRIDE')
OVERRIDE_ALL_DIVIDER = ('override_all_divider', '----------', 'OVERRIDE ALL')
# Per-tail override flag on the cog: Off (0, default) = the tail follows
# the ALL section directly; On (1) = the tail uses its own basectrl values
OVERRIDE = '{rigname}_override'
OVERRIDE_ENUM = 'Off:On'
# Hidden resolved IKFK driver on the cog (all_ikfk vs the tail's own
# switch, picked by its override condition); the mode SDKs are driven
# from this when the dashboard is active
IKFK_RESOLVED = '{rigname}_ikfk_resolved'
# Prefix of the ALL attributes on the cog ('all_ikfk', 'all_stretch', ...)
ALL_PREFIX = 'all_'
# NOTE: whether the dashboard is active is computed by
# rig_tail_ctrlall.active(), not here: this module is deliberately
# never reloaded (see rig_tail.py), so build logic must not depend on
# functions added here after a session started. For the same reason
# rig_tail_ctrlall installs any of the above templates that are
# missing from a stale session's copy of this module.

def ikfk_mode_index(name):
    '''
    Index of a mode in IKFK_MODES matched by name (case-insensitive),
    or None if the mode is not in the list.
    '''
    for i, mode in enumerate(IKFK_MODES):
        if mode.strip().upper() == name.strip().upper():
            return i
    return None

def ikfk_fk_mode_index():
    '''
    Index of the FK mode in the active IKFK_MODES, or None if the
    current build offers no FK mode. Matched by name first ('FK',
    case-insensitive) for backward compatibility, falling back to the
    canonical position (index 3) when the modes are custom-named.
    '''
    idx = ikfk_mode_index('FK')
    if idx is None and len(IKFK_MODES) > 3:
        idx = 3
    return idx

def ikfk_default_index():
    '''
    The mode a freshly built rig should start in: FK when the build offers
    it, otherwise SplineIK. Animators work in FK by default, and an IK-only
    build has no FK mode to fall back to.

    Applied to IKFK_SWITCH's default by update_ikfk_modes, so both the
    per-tail cog switches and the dashboard's All IKFK pick it up.

    Return:
        int: index into IKFK_MODES (0 when the list is empty).
    '''
    idx = ikfk_fk_mode_index()
    if idx is None:
        idx = ikfk_mode_index('SplineIK')
    return idx if idx is not None else 0

def update_ikfk_modes(fk, ik):
    '''
    Derive the active IKFK_MODES from IKFK_MODES_ALL and build options:
    - FK and IK: all modes are offered.
    - IK only: the FK mode (position 3) is dropped.
    - FK only: there is no switch attribute, so the list is empty.

    IKFK_MODES_ALL is never modified here, so custom mode names survive
    any sequence of build-option changes.

    Runs before every build (setup_rig) and when UI checkboxes change.
    Rebuilds the derived IKFK_SWITCH enum and its default mode.

    The default mode is build-derived (ikfk_default_index: FK when FK is
    built, else SplineIK), so it is set here rather than read from the
    config - a dv carried over from a config saved under different build
    options would name the wrong mode, or one that no longer exists.

    Return:
        bool: True if IKFK_MODES changed
    '''
    global IKFK_MODES, IKFK_SWITCH
    before = list(IKFK_MODES)
    if ik:
        IKFK_MODES = list(IKFK_MODES_ALL) if fk else list(IKFK_MODES_ALL[:3])
    else:
        IKFK_MODES = list()
    changed = IKFK_MODES != before
    # Always refresh the default, even when the mode list itself did not
    # change: rebuild_derived only runs on a change, and the dv may still be
    # stale from a loaded config.
    IKFK_SWITCH = IKFK_SWITCH[:3] + (ikfk_default_index(),)
    rebuild_derived()
    return changed


# CONSTANTS ============================================================

# Constants: number of controls
NUM_CTRL_FK = 3
NUM_CTRL_IK = 5

# Constants: control size
ROOT_CTRL_SZ = 35
COG_CTRL_SZ = 30
BASE_CTRL_SZ = 2.4
VARFK_CTRL_SZ = 1.4
FK_CTRL_SZ = 0.4
IK_CTRL_SZ = 1.4
SPLINE_UPV_SZ = 0.2
SPLINE_BOT_SZ = 1.8
SPLINE_BOT_SML_SZ = 1
SPLINE_MID_ROT_SZ = 1.6
SPLINE_MID_SZ = 1.8
SPLINE_TOP_SML_SZ = 1
SPLINE_TOP_SZ = 1.6
SPLINE_CONTROLS_SZ = [SPLINE_BOT_SZ, SPLINE_BOT_SML_SZ, SPLINE_MID_SZ,
                      SPLINE_TOP_SML_SZ, SPLINE_TOP_SZ, SPLINE_MID_ROT_SZ]

# Constants: preserve control shapes on rebuild, per control type.
# True keeps an existing control's curves exactly as they are - size, CV
# edits and colour - so hand-tuned shapes survive a re-rig. The matching
# _SZ constant then has no effect on that control type.
#
# Only shapes are inherited. The control's attributes and connections are
# rebuilt either way, so a preserved control cannot carry a previous rig's
# wiring into the new one.
#
# Ignored when the control does not exist yet (nothing to preserve, so it
# is built from the constants), and ignored on a full teardown, where the
# control layout itself changes and old shapes no longer correspond.
#
# root and cog default to True: they sit above the rig parts and are
# usually built once for the whole character, not per tail.
PRESERVE_CTRL = {
    'root': True,
    'cog': True,
    'base': False,
    'varfk': False,
    'fk': False,
    'ik': False,
    'float': False,
    'spline': False,
    'upvec': False
    }


# OTHER ================================================================

# Base control orientation offsets, keyed by the joint chain's LOCAL aim
# axis (from get_local_orientation) - the local axis of the first joint
# that points down the chain.
#
# Each value is the euler rotation that turns the base control group's
# local +Y onto that aim axis, so +Y always runs down the tail and the
# control circle (normal -Y) always sits perpendicular to it, whichever
# way the tail happens to point in world space.
#
# The x/y entries are pure Z rotations so the joint's own Z axis carries
# through untouched. That matters because orient_control_aims() reads the
# base control's Z as the world-up for every IK/Float/Spline control.
ROT_AXIS_DICT = {
    '+x': (0, 0, -90),
    '-x': (0, 0, 90),
    '+y': (0, 0, 0),
    '-y': (0, 0, 180),
    '+z': (90, 0, 0),
    '-z': (-90, 0, 0)
    }

# Coordinates used to create cube control
CUBE_CTRL_PTS = [\
    [-0.5, -0.5, -0.5], [-0.5, -0.5, 0.5], [-0.5, 0.5, 0.5], [-0.5, -0.5, 0.5],
    [0.5, -0.5, 0.5], [0.5, -0.5, -0.5], [-0.5, -0.5, -0.5], [-0.5, 0.5, -0.5],
    [-0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [0.5, -0.5, 0.5], [0.5, -0.5, -0.5],
    [0.5, 0.5, -0.5], [-0.5, 0.5, -0.5], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5]]

# Color override index for controls
COLOR_OVERRIDE = {\
    'black':1, 'darkgrey':2, 'lightgrey':3, 'darkred':4, 'darkblue':5,
    'neonblue':6, 'darkgreen':7, 'blueblack':8, 'magenta':9, 'brown':10,
    'darkbrown':11, 'red':12, 'neonred':13, 'neongreen':14, 'blue':15,
    'white':16, 'lightyellow':17, 'lightblue':18, 'lightgreen':19, 'lightpink':20,
    'lightorange':21, 'neonyellow':22, 'green':23, 'orange':24, 'yellow':25,
    'yellowgreen':26, 'lightgreen':27, 'cyan':28, 'darkcyan':29, 'purple':30,
    'pink':31 }


# JSON CONFIG ==========================================================
import json
import os

# Default config path; the UI can pass any other path to
# save_config()/load_config() for per-user or per-show configs.
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'rig_tail_config.json')

# Path of the config file currently in effect; None while running on the
# module defaults. Set by load_config()/save_config(), shown in the UI.
LOADED_CONFIG = None

def rebuild_derived():
    '''
    Rebuild values that are derived from other user variables.

    IKFK_SWITCH embeds ':'.join(IKFK_MODES) as its enum string, and
    SPLINE_CONTROLS / SPLINE_CONTROLS_SZ are ordered collections of
    other templates and sizes. Call after editing globals in the UI or
    loading a config so the derived values stay in sync.
    '''
    global IKFK_SWITCH, SPLINE_CONTROLS, SPLINE_CONTROLS_SZ
    dv = IKFK_SWITCH[3]
    if not 0 <= dv < len(IKFK_MODES):
        dv = 0
    IKFK_SWITCH = (IKFK_SWITCH[0], IKFK_SWITCH[1], ':'.join(IKFK_MODES), dv)
    SPLINE_CONTROLS = [SPLINE_BOT, SPLINE_BOT_SML, SPLINE_MID,
                       SPLINE_TOP_SML, SPLINE_TOP, SPLINE_MID_ROT]
    SPLINE_CONTROLS_SZ = [SPLINE_BOT_SZ, SPLINE_BOT_SML_SZ, SPLINE_MID_SZ,
                          SPLINE_TOP_SML_SZ, SPLINE_TOP_SZ, SPLINE_MID_ROT_SZ]

def get_user_editable_config():
    '''
    Get dictionary of user-editable variables and constants.
    These are the values that can be modified in the UI.
    '''
    return {
        # Rig Components
        'RIGPARTS': RIGPARTS,
        'RIGPARTS_EXCLUDE': RIGPARTS_EXCLUDE,
        # User Variables
        'ROOT': ROOT,
        'EFFECTS': EFFECTS,
        'BUILD_FK': BUILD_FK,
        'BUILD_IK': BUILD_IK,
        'INDIV_FK': INDIV_FK,
        'MAIN_CONTROLLER': MAIN_CONTROLLER,
        'ORIENT_JOINTS': ORIENT_JOINTS,
        'MIRROR_ORIENT': MIRROR_ORIENT,
        'MIRROR_JOINTS': MIRROR_JOINTS,
        'MIRROR_DRYRUN': MIRROR_DRYRUN,
        'MIRROR_AXIS': MIRROR_AXIS,
        'MIRROR_SOURCE_SIDE': MIRROR_SOURCE_SIDE,
        'MIRROR_BEHAVIOR': MIRROR_BEHAVIOR,
        'ORIENT_AIM_AXIS': ORIENT_AIM_AXIS,
        'ORIENT_UP_AXIS': ORIENT_UP_AXIS,
        'ORIENT_UP_MODE': ORIENT_UP_MODE,
        'PRESERVE_SKIN': PRESERVE_SKIN,
        'JOINT_POS_TOLERANCE': JOINT_POS_TOLERANCE,
        'COLOR_SKELETON': COLOR_SKELETON,
        'BN_COLOR': BN_COLOR,
        'IK_COLOR': IK_COLOR,
        'FK_COLOR': FK_COLOR,

        # Naming Template: type labels
        'TYPE_BN': TYPE_BN,
        'TYPE_IK': TYPE_IK,
        'TYPE_FK': TYPE_FK,
        'TYPE_FX': TYPE_FX,
        'GRP': GRP,
        'CTRL': CTRL,
        'JNT': JNT,
        'SDK': SDK,
        'CRV': CRV,
        'CSR': CSR,
        'HDL': HDL,
        'EFF': EFF,
        'VIS': VIS,
        'COND': COND,
        'CST': CST,

        # Naming Template: groups, controls, joints
        'BASECTRL_GRP': BASECTRL_GRP,
        'BASECTRL': BASECTRL,
        'CTRLROOT_GRP': CTRLROOT_GRP,
        'CTRL_GRP': CTRL_GRP,
        'CONTROL': CONTROL,
        'GROUP': GROUP,
        'JOINT': JOINT,
        'SDK_GRP': SDK_GRP,
        'SDK_JNT': SDK_JNT,

        # Naming Template: curves, clusters
        'CURVE': CURVE,
        'CURVE_SCALE': CURVE_SCALE,
        'CURVEINFO': CURVEINFO,
        'CLUSTER_GRP': CLUSTER_GRP,
        'CLUSTER': CLUSTER,
        'CLUSTER_HANDLE': CLUSTER_HANDLE,
        'UPV_CTRLGRP': UPV_CTRLGRP,
        'UPV_CTRL': UPV_CTRL,
        'CLUSTER_UPV': CLUSTER_UPV,
        'CLUSTER_UPV_HANDLE': CLUSTER_UPV_HANDLE,

        # Naming Template: spline
        'SPLINE_GRP': SPLINE_GRP,
        'SPLINE_HANDLE': SPLINE_HANDLE,
        'SPLINE_EFFECTOR': SPLINE_EFFECTOR,
        'SPLINE_IK_CTRL': SPLINE_IK_CTRL,
        'SPLINE_FLOAT_CTRL': SPLINE_FLOAT_CTRL,
        'SPLINE_BOT': SPLINE_BOT,
        'SPLINE_BOT_SML': SPLINE_BOT_SML,
        'SPLINE_MID_ROT': SPLINE_MID_ROT,
        'SPLINE_MID': SPLINE_MID,
        'SPLINE_TOP_SML': SPLINE_TOP_SML,
        'SPLINE_TOP': SPLINE_TOP,

        # Naming Template: structure
        'ROOT_GRP': ROOT_GRP,
        'ROOT_CTRL': ROOT_CTRL,
        'COG_CTRL': COG_CTRL,
        'GEOMETRY_GRP': GEOMETRY_GRP,
        'CONTROL_GRP': CONTROL_GRP,
        'SKELETON_GRP': SKELETON_GRP,
        'RIG_SYSTEMS_GRP': RIG_SYSTEMS_GRP,
        'CLUSTERS_GRP': CLUSTERS_GRP,
        'SCALE_GRP': SCALE_GRP,

        # Naming Template: ikfk, switch, divider
        'IKFK': IKFK,
        'IKFK_MODES_ALL': IKFK_MODES_ALL,
        'IKFK_MODES': IKFK_MODES,
        'IKFK_SWITCH': IKFK_SWITCH,
        'IKFK_DIVIDER': IKFK_DIVIDER,
        'STRETCH_DIVIDER': STRETCH_DIVIDER,
        'ANIM_DIVIDER': ANIM_DIVIDER,
        'TWIST_DIVIDER': TWIST_DIVIDER,
        'SCALE_DIVIDER': SCALE_DIVIDER,

        # Constants: number of controls
        'NUM_CTRL_FK': NUM_CTRL_FK,
        'NUM_CTRL_IK': NUM_CTRL_IK,

        # Constants: preserve control shapes
        'PRESERVE_CTRL': PRESERVE_CTRL,

        # Constants: control size
        'ROOT_CTRL_SZ': ROOT_CTRL_SZ,
        'COG_CTRL_SZ': COG_CTRL_SZ,
        'BASE_CTRL_SZ': BASE_CTRL_SZ,
        'VARFK_CTRL_SZ': VARFK_CTRL_SZ,
        'FK_CTRL_SZ': FK_CTRL_SZ,
        'IK_CTRL_SZ': IK_CTRL_SZ,
        'SPLINE_UPV_SZ': SPLINE_UPV_SZ,
        'SPLINE_BOT_SZ': SPLINE_BOT_SZ,
        'SPLINE_BOT_SML_SZ': SPLINE_BOT_SML_SZ,
        'SPLINE_MID_ROT_SZ': SPLINE_MID_ROT_SZ,
        'SPLINE_MID_SZ': SPLINE_MID_SZ,
        'SPLINE_TOP_SML_SZ': SPLINE_TOP_SML_SZ,
        'SPLINE_TOP_SZ': SPLINE_TOP_SZ,
    }

def save_config(filepath=None):
    '''
    Save user variables and constants to a JSON config file.

    filepath: path to write to; defaults to CONFIG_FILE.
    Returns True on success, False on failure.
    '''
    global LOADED_CONFIG
    filepath = filepath or CONFIG_FILE
    config = get_user_editable_config()
    try:
        with open(filepath, 'w') as f:
            json.dump(config, f, indent=2)
        LOADED_CONFIG = filepath
        return True
    except Exception as e:
        print(f'Failed to save config: {e}')
        return False

def load_config(filepath=None):
    '''
    Load user variables and constants from a JSON config file.

    filepath: path to read from; defaults to CONFIG_FILE.
    Missing keys keep their current values. Attribute templates saved as
    JSON lists are restored to tuples, and derived values (IKFK_SWITCH
    enum, SPLINE_CONTROLS) are rebuilt afterwards.
    Returns True on success, False if the file is missing or unreadable.
    '''
    global LOADED_CONFIG
    global RIGPARTS, RIGPARTS_EXCLUDE, ROOT, EFFECTS, INDIV_FK
    global MAIN_CONTROLLER, PRESERVE_SKIN
    global ORIENT_JOINTS, MIRROR_ORIENT, MIRROR_JOINTS, MIRROR_DRYRUN, MIRROR_AXIS
    global MIRROR_SOURCE_SIDE, MIRROR_BEHAVIOR, ORIENT_AIM_AXIS, ORIENT_UP_AXIS
    global ORIENT_UP_MODE
    global BUILD_FK, BUILD_IK, JOINT_POS_TOLERANCE
    global COLOR_SKELETON, BN_COLOR, IK_COLOR, FK_COLOR
    global TYPE_BN, TYPE_IK, TYPE_FK, TYPE_FX
    global GRP, CTRL, JNT, SDK, CRV, CSR, HDL, EFF, VIS, COND, CST
    global BASECTRL_GRP, BASECTRL, CTRLROOT_GRP, CTRL_GRP, CONTROL, GROUP, JOINT, SDK_GRP, SDK_JNT
    global CURVE, CURVE_SCALE, CURVEINFO, CLUSTER_GRP, CLUSTER, CLUSTER_HANDLE
    global UPV_CTRLGRP, UPV_CTRL, CLUSTER_UPV, CLUSTER_UPV_HANDLE
    global SPLINE_GRP, SPLINE_HANDLE, SPLINE_EFFECTOR, SPLINE_IK_CTRL, SPLINE_FLOAT_CTRL
    global SPLINE_BOT, SPLINE_BOT_SML, SPLINE_MID_ROT, SPLINE_MID, SPLINE_TOP_SML, SPLINE_TOP
    global ROOT_GRP, ROOT_CTRL, COG_CTRL, GEOMETRY_GRP, CONTROL_GRP, SKELETON_GRP
    global RIG_SYSTEMS_GRP, CLUSTERS_GRP, SCALE_GRP
    global IKFK, IKFK_MODES_ALL, IKFK_MODES, IKFK_SWITCH, IKFK_DIVIDER
    global STRETCH_DIVIDER, ANIM_DIVIDER, TWIST_DIVIDER, SCALE_DIVIDER
    global NUM_CTRL_FK, NUM_CTRL_IK, PRESERVE_CTRL
    global ROOT_CTRL_SZ, COG_CTRL_SZ, BASE_CTRL_SZ, VARFK_CTRL_SZ, FK_CTRL_SZ, IK_CTRL_SZ
    global SPLINE_UPV_SZ, SPLINE_BOT_SZ, SPLINE_BOT_SML_SZ, SPLINE_MID_ROT_SZ
    global SPLINE_MID_SZ, SPLINE_TOP_SML_SZ, SPLINE_TOP_SZ

    filepath = filepath or CONFIG_FILE
    if not os.path.exists(filepath):
        return False

    try:
        with open(filepath, 'r') as f:
            config = json.load(f)

        # Update globals from config
        RIGPARTS = config.get('RIGPARTS', RIGPARTS)
        # Drop any excluded name the loaded RIGPARTS no longer contains, so
        # a stale exclusion cannot linger invisibly (the editor only ever
        # shows names that are in RIGPARTS).
        RIGPARTS_EXCLUDE = [p for p in config.get('RIGPARTS_EXCLUDE',
                                                  RIGPARTS_EXCLUDE)
                            if p in RIGPARTS]
        ROOT = config.get('ROOT', ROOT)
        EFFECTS = config.get('EFFECTS', EFFECTS)
        BUILD_FK = config.get('BUILD_FK', BUILD_FK)
        BUILD_IK = config.get('BUILD_IK', BUILD_IK)
        INDIV_FK = config.get('INDIV_FK', INDIV_FK)
        # 'MASTER_CONTROLLER' and 'GROUP_CONTROLS' are legacy keys
        MAIN_CONTROLLER = config.get(
            'MAIN_CONTROLLER', config.get(
                'MASTER_CONTROLLER', config.get('GROUP_CONTROLS', MAIN_CONTROLLER)))
        # Setup-phase keys were renamed. A new-format config has
        # 'ORIENT_JOINTS'; read it straight. A legacy config predates the
        # rename, where 'MIRROR_ORIENT' meant aim-orient and 'MIRROR_JOINTS'
        # meant orient-mirror - migrate those to the new names (positions-
        # mirror did not exist then, so new MIRROR_JOINTS stays default).
        if 'ORIENT_JOINTS' in config:
            ORIENT_JOINTS = config.get('ORIENT_JOINTS', ORIENT_JOINTS)
            MIRROR_ORIENT = config.get('MIRROR_ORIENT', MIRROR_ORIENT)
            MIRROR_JOINTS = config.get('MIRROR_JOINTS', MIRROR_JOINTS)
        else:
            ORIENT_JOINTS = config.get('MIRROR_ORIENT', ORIENT_JOINTS)
            MIRROR_ORIENT = config.get('MIRROR_JOINTS', MIRROR_ORIENT)
        # 'MIRROR_ORIENT_DRYRUN' is the legacy key name for MIRROR_DRYRUN
        MIRROR_DRYRUN = config.get(
            'MIRROR_DRYRUN', config.get('MIRROR_ORIENT_DRYRUN', MIRROR_DRYRUN))
        MIRROR_AXIS = config.get('MIRROR_AXIS', MIRROR_AXIS)
        MIRROR_SOURCE_SIDE = config.get('MIRROR_SOURCE_SIDE', MIRROR_SOURCE_SIDE)
        # Deliberately NOT migrated: a config saved before MIRROR_BEHAVIOR
        # existed was written by code that always produced 'parallel' frames,
        # but MIRROR_ORIENT defaulted off then, so such a config almost never
        # carries a mirrored result worth preserving. A missing key therefore
        # takes the module default ('symmetric') rather than the old maths.
        MIRROR_BEHAVIOR = config.get('MIRROR_BEHAVIOR', MIRROR_BEHAVIOR)
        ORIENT_AIM_AXIS = config.get('ORIENT_AIM_AXIS', ORIENT_AIM_AXIS)
        ORIENT_UP_AXIS = config.get('ORIENT_UP_AXIS', ORIENT_UP_AXIS)
        # A config saved before ORIENT_UP_MODE existed was written by code
        # that always did 'best-fit', but it takes the module default
        # ('cascade') anyway: the old maths is the destructive one, and a
        # skeleton set up under it is exactly what cascade protects.
        ORIENT_UP_MODE = config.get('ORIENT_UP_MODE', ORIENT_UP_MODE)
        # FORCE_REBUILD is deliberately not loaded: forcing is a per-click
        # action of the Build UI's button, and a config saved by an older
        # version with it stuck on must not make every build a teardown.
        PRESERVE_SKIN = config.get('PRESERVE_SKIN', PRESERVE_SKIN)
        JOINT_POS_TOLERANCE = config.get('JOINT_POS_TOLERANCE', JOINT_POS_TOLERANCE)
        COLOR_SKELETON = config.get('COLOR_SKELETON', COLOR_SKELETON)
        BN_COLOR = config.get('BN_COLOR', BN_COLOR)
        IK_COLOR = config.get('IK_COLOR', IK_COLOR)
        FK_COLOR = config.get('FK_COLOR', FK_COLOR)

        # Type labels
        TYPE_BN = config.get('TYPE_BN', TYPE_BN)
        TYPE_IK = config.get('TYPE_IK', TYPE_IK)
        TYPE_FK = config.get('TYPE_FK', TYPE_FK)
        TYPE_FX = config.get('TYPE_FX', TYPE_FX)
        GRP = config.get('GRP', GRP)
        CTRL = config.get('CTRL', CTRL)
        JNT = config.get('JNT', JNT)
        SDK = config.get('SDK', SDK)
        CRV = config.get('CRV', CRV)
        CSR = config.get('CSR', CSR)
        HDL = config.get('HDL', HDL)
        EFF = config.get('EFF', EFF)
        VIS = config.get('VIS', VIS)
        COND = config.get('COND', COND)
        CST = config.get('CST', CST)

        # Groups, controls, joints
        BASECTRL_GRP = config.get('BASECTRL_GRP', BASECTRL_GRP)
        BASECTRL = config.get('BASECTRL', BASECTRL)
        CTRLROOT_GRP = config.get('CTRLROOT_GRP', CTRLROOT_GRP)
        CTRL_GRP = config.get('CTRL_GRP', CTRL_GRP)
        CONTROL = config.get('CONTROL', CONTROL)
        GROUP = config.get('GROUP', GROUP)
        JOINT = config.get('JOINT', JOINT)
        SDK_GRP = config.get('SDK_GRP', SDK_GRP)
        SDK_JNT = config.get('SDK_JNT', SDK_JNT)

        # Curves, clusters
        CURVE = config.get('CURVE', CURVE)
        CURVE_SCALE = config.get('CURVE_SCALE', CURVE_SCALE)
        CURVEINFO = config.get('CURVEINFO', CURVEINFO)
        CLUSTER_GRP = config.get('CLUSTER_GRP', CLUSTER_GRP)
        CLUSTER = config.get('CLUSTER', CLUSTER)
        CLUSTER_HANDLE = config.get('CLUSTER_HANDLE', CLUSTER_HANDLE)
        UPV_CTRLGRP = config.get('UPV_CTRLGRP', UPV_CTRLGRP)
        UPV_CTRL = config.get('UPV_CTRL', UPV_CTRL)
        CLUSTER_UPV = config.get('CLUSTER_UPV', CLUSTER_UPV)
        CLUSTER_UPV_HANDLE = config.get('CLUSTER_UPV_HANDLE', CLUSTER_UPV_HANDLE)

        # Spline
        SPLINE_GRP = config.get('SPLINE_GRP', SPLINE_GRP)
        SPLINE_HANDLE = config.get('SPLINE_HANDLE', SPLINE_HANDLE)
        SPLINE_EFFECTOR = config.get('SPLINE_EFFECTOR', SPLINE_EFFECTOR)
        SPLINE_IK_CTRL = config.get('SPLINE_IK_CTRL', SPLINE_IK_CTRL)
        SPLINE_FLOAT_CTRL = config.get('SPLINE_FLOAT_CTRL', SPLINE_FLOAT_CTRL)
        SPLINE_BOT = config.get('SPLINE_BOT', SPLINE_BOT)
        SPLINE_BOT_SML = config.get('SPLINE_BOT_SML', SPLINE_BOT_SML)
        SPLINE_MID_ROT = config.get('SPLINE_MID_ROT', SPLINE_MID_ROT)
        SPLINE_MID = config.get('SPLINE_MID', SPLINE_MID)
        SPLINE_TOP_SML = config.get('SPLINE_TOP_SML', SPLINE_TOP_SML)
        SPLINE_TOP = config.get('SPLINE_TOP', SPLINE_TOP)

        # Structure
        ROOT_GRP = config.get('ROOT_GRP', ROOT_GRP)
        ROOT_CTRL = config.get('ROOT_CTRL', ROOT_CTRL)
        COG_CTRL = config.get('COG_CTRL', COG_CTRL)
        GEOMETRY_GRP = config.get('GEOMETRY_GRP', GEOMETRY_GRP)
        CONTROL_GRP = config.get('CONTROL_GRP', CONTROL_GRP)
        SKELETON_GRP = config.get('SKELETON_GRP', SKELETON_GRP)
        RIG_SYSTEMS_GRP = config.get('RIG_SYSTEMS_GRP', RIG_SYSTEMS_GRP)
        CLUSTERS_GRP = config.get('CLUSTERS_GRP', CLUSTERS_GRP)
        SCALE_GRP = config.get('SCALE_GRP', SCALE_GRP)

        # IKFK, switch, divider (JSON stores tuples as lists)
        IKFK = config.get('IKFK', IKFK)
        # Older configs only saved IKFK_MODES; use it as the full list
        IKFK_MODES_ALL = list(config.get(
            'IKFK_MODES_ALL', config.get('IKFK_MODES', IKFK_MODES_ALL)))
        IKFK_MODES = list(config.get('IKFK_MODES', IKFK_MODES_ALL))
        IKFK_SWITCH = tuple(config.get('IKFK_SWITCH', IKFK_SWITCH))
        IKFK_DIVIDER = tuple(config.get('IKFK_DIVIDER', IKFK_DIVIDER))
        STRETCH_DIVIDER = tuple(config.get('STRETCH_DIVIDER', STRETCH_DIVIDER))
        ANIM_DIVIDER = tuple(config.get('ANIM_DIVIDER', ANIM_DIVIDER))
        TWIST_DIVIDER = tuple(config.get('TWIST_DIVIDER', TWIST_DIVIDER))
        SCALE_DIVIDER = tuple(config.get('SCALE_DIVIDER', SCALE_DIVIDER))

        # Constants
        NUM_CTRL_FK = config.get('NUM_CTRL_FK', NUM_CTRL_FK)
        NUM_CTRL_IK = config.get('NUM_CTRL_IK', NUM_CTRL_IK)

        # Merged, not replaced: a config written before a control type was
        # added still leaves that type at its default rather than dropping
        # the key and failing the lookup at build time
        PRESERVE_CTRL = {**PRESERVE_CTRL, **config.get('PRESERVE_CTRL', {})}

        ROOT_CTRL_SZ = config.get('ROOT_CTRL_SZ', ROOT_CTRL_SZ)
        COG_CTRL_SZ = config.get('COG_CTRL_SZ', COG_CTRL_SZ)
        BASE_CTRL_SZ = config.get('BASE_CTRL_SZ', BASE_CTRL_SZ)
        VARFK_CTRL_SZ = config.get('VARFK_CTRL_SZ', VARFK_CTRL_SZ)
        FK_CTRL_SZ = config.get('FK_CTRL_SZ', FK_CTRL_SZ)
        IK_CTRL_SZ = config.get('IK_CTRL_SZ', IK_CTRL_SZ)
        SPLINE_UPV_SZ = config.get('SPLINE_UPV_SZ', SPLINE_UPV_SZ)
        SPLINE_BOT_SZ = config.get('SPLINE_BOT_SZ', SPLINE_BOT_SZ)
        SPLINE_BOT_SML_SZ = config.get('SPLINE_BOT_SML_SZ', SPLINE_BOT_SML_SZ)
        SPLINE_MID_ROT_SZ = config.get('SPLINE_MID_ROT_SZ', SPLINE_MID_ROT_SZ)
        SPLINE_MID_SZ = config.get('SPLINE_MID_SZ', SPLINE_MID_SZ)
        SPLINE_TOP_SML_SZ = config.get('SPLINE_TOP_SML_SZ', SPLINE_TOP_SML_SZ)
        SPLINE_TOP_SZ = config.get('SPLINE_TOP_SZ', SPLINE_TOP_SZ)

        rebuild_derived()
        LOADED_CONFIG = filepath
        return True
    except Exception as e:
        print(f'Failed to load config: {e}')
        return False

# Auto-load config on module import
load_config()
