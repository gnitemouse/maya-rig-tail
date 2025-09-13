'''
# rig_tail_constants.py
author: Daisy Jane Lee @dayzl

Constants for Rig Tail
'''
# USER VARIABLES =======================================================

# Rig Components
# List all parts that will be rigged with a tail
# RIGPARTS = ['L_fintail', 'R_fintail', 'C_fintail',
#     'L_sidetail', 'R_sidetail',
#     'L_tail3', 'L_tail2', 'L_tail1', 'C_tail',
#     'R_tail1', 'R_tail2', 'R_tail3']
RIGPARTS = ['R_tail3']

# Joints Dict: rigname -> joint list
JOINTS_FK = dict()
JOINTS_IK = dict()
JOINTS_BN = dict()

ROOT = ''

# NAMING TEMPLATE ======================================================
# Change name templates as necessary.
# If you change formatting of indices(_NN),
# make sure to update DFORMAT and get_index_from_name()

# Decimal formatting for indices
DFORMAT = '{:02d}'

# Naming Template: Type Labels
TYPE_BN = 'BN_'
TYPE_IK = 'IK_'
TYPE_FK = 'FK_'
GRP = '_grp'
CTRL = '_ctrl'
JNT = '_jnt'
SDK = '_sdk'
CRV = '_crv'
CSR = '_cluster'
HDL = 'Handle'
EFF = '_effector'
VIS = '_visibility'
CST = '_constraint'
CRVI = '_curveInfo'
POCI = '_pointOnCurveInfo'
COND = '_condition'

# Naming Template: tail
BASECTRL_GRP = '{TYPE}{rigname}_base{TAG}{CTRL}{GRP}'
BASECTRL = '{TYPE}{rigname}_base{TAG}{CTRL}'
CTRLROOT_GRP = '{TYPE}{rigname}_root{TAG}{GRP}'
CTRL_GRP = '{TYPE}{rigname}{TAG}{_NN}{CTRL}{GRP}'
CONTROL = '{TYPE}{rigname}{TAG}{_NN}{CTRL}'
GROUP = '{TYPE}{rigname}{TAG}{_NN}{GRP}'
JOINT = '{TYPE}{rigname}{TAG}{_NN}{JNT}'
SDK_GRP = '{TYPE}{rigname}{TAG}{_NN}{_nn}{SDK}'
SDK_CTRL = '{TYPE}{rigname}{TAG}{_NN}{CTRL}{SDK}'

# Naming Template: curve, clusters
CURVE = '{TYPE}{rigname}{TAG}{CRV}'
CURVE_SCALE = '{TYPE}{rigname}_scale{TAG}{CRV}'
CURVEINFO = '{TYPE}{rigname}{TAG}{CRVI}'
CLUSTER_GRP = '{TYPE}{rigname}{TAG}{_NN}{CSR}{GRP}'
CLUSTER = '{TYPE}{rigname}{TAG}{_NN}{CSR}'
CLUSTER_HANDLE = '{TYPE}{rigname}{TAG}{_NN}{CSR}{HDL}'
# Upvec
UPV_CTRL = '{TYPE}{rigname}_upvec{TAG}{CTRL}'
UPV_CTRLGRP = '{TYPE}{rigname}_upvec{TAG}{CTRL}{GRP}'
CLUSTER_UPV = '{TYPE}{rigname}_upvec{TAG}{_NN}{CSR}'
CLUSTER_UPV_HANDLE = '{TYPE}{rigname}_upvec{TAG}{_NN}{CSR}{HDL}'

# Naming Template: spline
SPLINE_GRP = '{TYPE}{rigname}_spline{TAG}{GRP}'
SPLINE_HANDLE = '{TYPE}{rigname}_spline{TAG}{HDL}'
SPLINE_EFFECTOR = '{TYPE}{rigname}_spline{TAG}{EFF}'
SCALE_GRP = '{TYPE}{rigname}_scale{TAG}{GRP}'
# Spline Controls
SPLINE_IK_CTRL = '{TYPE}{rigname}_ik{TAG}{_NN}{CTRL}'
SPLINE_FLOAT_CTRL = '{TYPE}{rigname}_float{TAG}{_NN}{CTRL}'
SPLINE_BOT = '{TYPE}{rigname}_spline_bot{CTRL}'
SPLINE_BOT_SML = '{TYPE}{rigname}_spline_bot_sml{CTRL}'
SPLINE_MID_ROT = '{TYPE}{rigname}_spline_mid_rot{CTRL}'
SPLINE_MID = '{TYPE}{rigname}_spline_mid{CTRL}'
SPLINE_TOP_SML = '{TYPE}{rigname}_spline_top_sml{CTRL}'
SPLINE_TOP = '{TYPE}{rigname}_spline_top{CTRL}'
SPLINE_CONTROLS = [SPLINE_BOT, SPLINE_BOT_SML, SPLINE_MID,
                   SPLINE_TOP_SML, SPLINE_TOP, SPLINE_MID_ROT]

# Naming Template: groups
ROOT_GRP = '{ROOT}{TAG}{GRP}'
ROOT_CTRL = '{ROOT}{TAG}{CTRL}'
COG_CTRL = 'cog{TAG}{CTRL}'
GEOMETRY_GRP = 'geometry{TAG}'
CONTROL_GRP = '{TYPE}controls{TAG}'
SKELETON_GRP = '{TYPE}skeleton{TAG}'
RIG_SYSTEMS_GRP = 'rig_systems{TAG}'
LOCATORS_GRP = 'locators{TAG}'
IKFK = '{rigname}{TAG}_ikfk'
IKFK_COND = '{rigname}_ikfk{TAG}_condition'
SCALE_MULT = '{rigname}_scale{TAG}{_NN}_multiplyDivide'
SCALE_COND = '{rigname}_scale{TAG}{_NN}_condition'

# Attribute Template: (longName, niceName, enumName, dv)
# Switch
HIER_SWITCH = ('hierarchySwitch', 'Hierarchy Switch', 'spine:fk:float:revFk', 0)
IKFK_SWITCH = ('ikfk_switch', 'IKFK Switch', 'SplineIK:IK:Float:FK', 0)
# Divider
IKFK_DIVIDER = ('ikfk_divider', '----------', 'IKFK')
STRETCH_DIVIDER = ('stretch_divider', '----------', 'STRETCH')
TWIST_DIVIDER = ('twist_divider', '----------', 'TWIST')
SCALE_DIVIDER = ('scale_divider', '----------', 'JNT SCALE')

# CONSTANTS ============================================================

# Number of controls
NUM_CTRL_FK = 3
NUM_CTRL_IK = 5

# Control Size
ROOT_CTRL_SZ = 30
COG_CTRL_SZ = 20
BASE_CTRL_SZ = 2
VARFK_CTRL_SZ = 1.2
FK_CTRL_SZ = 0.4
IK_CTRL_SZ = 1.0
SPLINE_UPV_SZ = 0.4
SPLINE_BOT_SZ = 1.4
SPLINE_BOT_SML_SZ = 0.8
SPLINE_MID_ROT_SZ = 1
SPLINE_MID_SZ = 1
SPLINE_TOP_SML_SZ = 0.6
SPLINE_TOP_SZ = 1
SPLINE_CONTROLS_SZ = [SPLINE_BOT_SZ, SPLINE_BOT_SML_SZ, SPLINE_MID_SZ,
                      SPLINE_TOP_SML_SZ, SPLINE_TOP_SZ, SPLINE_MID_ROT_SZ]

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
