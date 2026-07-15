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
    'indiv_fk': None      # individual-FK toggle; a change forces rebuild
}


# USER VARIABLES =======================================================

# Rig Components: List all parts to be rigged, e.g.
# RIGPARTS = ['L_fintail', 'R_fintail', 'C_fintail',
#     'L_sidetail', 'R_sidetail',
#     'L_tail3', 'L_tail2', 'L_tail1', 'C_tail',
#     'R_tail1', 'R_tail2', 'R_tail3']
RIGPARTS = ['tail']

# Root Name
ROOT = 'tail'
# Build individual FK controls (one per joint) alongside variable-FK
# sliding controls. Requires FK. If False, only build varFK controls.
INDIV_FK = False
# Build centralized main controller dashboard (for multiple tails)
MAIN_CONTROLLER = False
# Force Rebuild (even if joints are unchanged)
FORCE_REBUILD = False
# Max per-joint world position drift (scene units) still treated as
# "unchanged" on re-rig. Building the rig drives joints through the OPM
# network, which perturbs world positions by float noise; drift within
# this tolerance takes the light cleanup_connections path instead of a
# full teardown.
JOINT_POS_TOLERANCE = 0.001


# ANIMATION EFFECTS ====================================================
# Features: Build which features
EFFECTS = {
    'stretchy': True,
    'wave': True,
    'curl': True,
    'noise': True,
    'loop': True
    }

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
ROOT_CTRL = '{ROOT}_{CTRL}'
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
STRETCH_DIVIDER = ('stretch_divider', '----------', 'STRETCH')
ANIM_DIVIDER = ('anim_divider', '----------', 'ANIMATION')
TWIST_DIVIDER = ('twist_divider', '----------', 'TWIST')
SCALE_DIVIDER = ('scale_divider', '----------', 'JNT SCALE')

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

def update_ikfk_modes(fk, ik):
    '''
    Derive the active IKFK_MODES from IKFK_MODES_ALL and build options:
    - FK and IK: all modes are offered.
    - IK only: the FK mode (position 3) is dropped.
    - FK only: there is no switch attribute, so the list is empty.

    IKFK_MODES_ALL is never modified here, so custom mode names survive
    any sequence of build-option changes.

    Runs before every build (setup_rig) and when UI checkboxes change.
    Rebuilds the derived IKFK_SWITCH enum when the list changes.

    Return:
        bool: True if IKFK_MODES changed
    '''
    global IKFK_MODES
    before = list(IKFK_MODES)
    if ik:
        IKFK_MODES = list(IKFK_MODES_ALL) if fk else list(IKFK_MODES_ALL[:3])
    else:
        IKFK_MODES = list()
    changed = IKFK_MODES != before
    if changed:
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


# OTHER ================================================================

ROT_AXIS_DICT = {
    '+x': (0, -90, 90),
    '-x': (0, 90, 90),
    '+y': (0, 0, 0),
    '-y': (0, 180, 0),
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
        # User Variables
        'ROOT': ROOT,
        'EFFECTS': EFFECTS,
        'INDIV_FK': INDIV_FK,
        'MAIN_CONTROLLER': MAIN_CONTROLLER,
        'FORCE_REBUILD': FORCE_REBUILD,
        'JOINT_POS_TOLERANCE': JOINT_POS_TOLERANCE,

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
    global RIGPARTS, ROOT, EFFECTS, INDIV_FK, MAIN_CONTROLLER, FORCE_REBUILD, JOINT_POS_TOLERANCE
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
    global NUM_CTRL_FK, NUM_CTRL_IK
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
        ROOT = config.get('ROOT', ROOT)
        EFFECTS = config.get('EFFECTS', EFFECTS)
        INDIV_FK = config.get('INDIV_FK', INDIV_FK)
        # 'MASTER_CONTROLLER' and 'GROUP_CONTROLS' are legacy keys
        MAIN_CONTROLLER = config.get(
            'MAIN_CONTROLLER', config.get(
                'MASTER_CONTROLLER', config.get('GROUP_CONTROLS', MAIN_CONTROLLER)))
        FORCE_REBUILD = config.get('FORCE_REBUILD', FORCE_REBUILD)
        JOINT_POS_TOLERANCE = config.get('JOINT_POS_TOLERANCE', JOINT_POS_TOLERANCE)

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
