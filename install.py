'''
# install.py -- drag-and-drop installer for Rig Tail
author: Daisy Jane @gnitemouse

Installs Rig Tail as a self-contained Maya module and adds a launcher button
to the active shelf. This is the non-invasive, standard way to ship a Maya
tool: everything lives in one folder plus one .mod file under
~/Documents/maya/modules/. Maya adds the module's scripts/ to sys.path and
icons/ to the icon path at startup, so nothing global (userSetup.py,
Maya.env) is ever touched.

--------------------------------------------------------------------------
INSTALL (drag-and-drop)
    Drag install.py from a file browser into the Maya viewport. It copies
    the module in and adds three shelf buttons -- "TailSetup" (skeleton
    orient / mirror, black icon), "TailRig" (the builder, white icon) and
    "TailManual" (developer import/build/test workflow, grey icon). Works
    immediately -- no restart. Keep install.py next to the rigTail/ folder
    and rigTail.mod when you drag it, since it copies them.

INSTALL (manual, no drag-and-drop)
    Copy rigTail/ and rigTail.mod into ~/Documents/maya/modules/
    then restart Maya. Launch from the Script Editor with:
        import rig_tail; rig_tail.main()        # builder
        import rig_tail; rig_tail.main_setup()  # setup (orient / mirror)
    (or make your own shelf buttons running those lines).

UNINSTALL
    Delete rigTail.mod and the rigTail folder from
    ~/Documents/maya/modules/, and remove the shelf button.
--------------------------------------------------------------------------

Installed layout:
    <userAppDir>/modules/
        rigTail.mod
        rigTail/
            scripts/   rig_tail*.py + logger_config.py
            icons/     octopus{,_black,_grey}.png (+ _200 variants)

Compatible with Maya 2020+ (Python 3). UI tested in Maya 2024/2025.
'''

import os
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

# Name of the prebuilt module folder and .mod shipped alongside this file.
MODULE_NAME = 'rigTail'
MOD_FILE = MODULE_NAME + '.mod'

# Shelf icons: "Octopus" icon by Icons8 (https://icons8.com/icons/set/octopus).
# Free use requires attribution -- see the Credits section of README.md.
# Resolved by bare name via the module's icon path once registered. Three
# recolours of the same octopus distinguish the three buttons at a glance:
#   white      -> Builder
#   black      -> Setup
#   light grey -> Manual Run (developer console workflow)
SHELF_ICON = 'octopus.png'              # white  -- Builder
SHELF_ICON_SETUP = 'octopus_black.png'  # black  -- Setup
SHELF_ICON_MANUAL = 'octopus_grey.png'  # grey   -- Manual Run

SHELF_BUTTON_LABEL = 'TailRig'
# Second button for the Setup phase (skeleton orient / mirror).
SHELF_SETUP_LABEL = 'TailSetup'
# Third button: developer console workflow (import / build / setup / test
# from the source tree).
SHELF_MANUAL_LABEL = 'TailManual'

# Command the shelf button runs. No absolute path is baked in: once the
# module is registered, scripts/ is on sys.path automatically.
LAUNCH_COMMAND = '''# Launch Rig Tail Builder
import importlib
import rig_tail
importlib.reload(rig_tail)
rig_tail.main()
'''

# Setup-phase launcher: orient / mirror the skeleton before building.
LAUNCH_SETUP_COMMAND = '''# Launch Rig Tail Setup
import importlib
import rig_tail
importlib.reload(rig_tail)
rig_tail.main_setup()
'''

# Manual Run: a developer console workflow rather than a UI launcher. It
# loads the modules fresh from the SOURCE tree (edit a .py, click the
# button, it re-imports and runs) and keeps module handles bound for
# interactive testing in the Script Editor. Commented lines lay out the
# full workflow -- setup, build, and test -- so the user can uncomment the
# call they want. Raw string so the Windows TOOL_DIR backslashes survive.
LAUNCH_MANUAL_COMMAND = r'''# Rig Tail -- Manual Run (import / build / setup / test from source)
import sys
import os
import importlib

# Source scripts dir (edit this if your repo lives elsewhere).
TOOL_DIR = os.path.expanduser(r'~\Documents\maya-rig-tail\rigTail\scripts')

# Put this version's directory at the FRONT of sys.path
# so that internal imports resolve to files in THIS directory.
if TOOL_DIR in sys.path:
    sys.path.remove(TOOL_DIR)
sys.path.insert(0, TOOL_DIR)

# Purge already loaded modules
for mod_name in list(sys.modules):
    if mod_name.startswith('rig_tail'):
        del sys.modules[mod_name]

# Fresh import.
# rig_tail pulls in the rest via its own imports;
# explicit list keeps handles around for console testing
rt = importlib.import_module('rig_tail')
rt_con = importlib.import_module('rig_tail_connect')
rt_cst = importlib.import_module('rig_tail_constants')
rt_ctl = importlib.import_module('rig_tail_control')
rt_crv = importlib.import_module('rig_tail_curve')
rt_fk = importlib.import_module('rig_tail_fk')
rt_set = importlib.import_module('rig_tail_setup')
rt_str = importlib.import_module('rig_tail_stretch')
rt_test = importlib.import_module('rig_tail_test')

# --- SETUP phase (optional; run BEFORE the build, on the raw BN skeleton) ---
#rt.setup_tails('squid', dry_run=True)   # preview only (orient/mirror), no changes
#rt.setup_tails('squid')                 # apply orient/mirror, then build
#rt.main_setup()                         # or open the Setup UI

# --- BUILD ---
#rt.rig_tail_single('tail', fk=True, ik=True)
rt.rig_tail_multiple('squid', fk=True, ik=True)
#rt.main()                               # or open the Builder UI

# --- TEST / INSPECT (after a build) ---
#rt_test.run_all('squid')                    # full test sweep
#rt_test.report_bend(rt_cst.RIGPARTS)        # per-chain bend angles
#rt_test.probe('after build', 'C_fintail')   # quick joint probe
#rt_test.measure_rebuild_degradation(rt_cst.RIGPARTS, rebuilds=2)
'''


def _source_dir():
    '''Folder this installer lives in (holds rigTail/ and the .mod).'''
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        # __file__ is undefined when run from the Script Editor; fall back
        # to the current working directory.
        return os.path.abspath(os.getcwd())


def _copy_module(src_dir, modules_dir):
    '''Copy the prebuilt module tree and .mod into the Maya modules dir.'''
    src_module = os.path.join(src_dir, MODULE_NAME)
    src_mod = os.path.join(src_dir, MOD_FILE)
    if not os.path.isdir(src_module) or not os.path.isfile(src_mod):
        raise RuntimeError(
            'Could not find {0}/ and {1} next to install.py.\n'
            'Keep install.py beside them when dragging it in.'.format(
                MODULE_NAME, MOD_FILE))

    if not os.path.isdir(modules_dir):
        os.makedirs(modules_dir)

    dst_module = os.path.join(modules_dir, MODULE_NAME)
    # Replace any prior install cleanly (copytree needs a fresh dest on the
    # Python 3.7 that ships with Maya 2020/2022).
    if os.path.isdir(dst_module):
        shutil.rmtree(dst_module)
    shutil.copytree(src_module, dst_module,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(src_mod, os.path.join(modules_dir, MOD_FILE))

    return dst_module


def _activate_for_session(module_dir):
    '''Mirror the module paths into the running session so no restart is
    needed: scripts/ on sys.path for imports, icons/ on XBMLANGPATH so the
    shelf icon resolves by bare name immediately.'''
    scripts_dir = os.path.join(module_dir, 'scripts')
    icons_dir = os.path.join(module_dir, 'icons')

    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    xbm = os.environ.get('XBMLANGPATH', '')
    parts = xbm.split(os.pathsep) if xbm else []
    if icons_dir not in parts:
        os.environ['XBMLANGPATH'] = (
            icons_dir + (os.pathsep + xbm if xbm else ''))


def _current_shelf():
    '''Return the name of the currently visible shelf.'''
    top = mel.eval('$_tmp = $gShelfTopLevel')
    return cmds.tabLayout(top, query=True, selectTab=True)


def _remove_existing_button(shelf, label):
    '''Delete any prior TailRig button so re-installing does not stack them.'''
    children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
    for child in children:
        if cmds.control(child, exists=True) and cmds.shelfButton(
                child, query=True, exists=True):
            if cmds.shelfButton(child, query=True, label=True) == label:
                cmds.deleteUI(child)


def _add_shelf_button():
    '''Add (or refresh) the TailSetup + TailRig + TailManual launchers.

    Three buttons, added left-to-right in the order they are used: Setup
    (orient/mirror the skeleton, black icon), Build (the builder, white
    icon), and Manual Run (developer console workflow, grey icon).
    '''
    shelf = _current_shelf()
    _remove_existing_button(shelf, SHELF_SETUP_LABEL)
    _remove_existing_button(shelf, SHELF_BUTTON_LABEL)
    _remove_existing_button(shelf, SHELF_MANUAL_LABEL)

    cmds.shelfButton(
        parent=shelf,
        label=SHELF_SETUP_LABEL,
        annotation='Launch the Rig Tail Setup UI (skeleton orient / mirror)',
        image=SHELF_ICON_SETUP,
        image1=SHELF_ICON_SETUP,
        sourceType='python',
        command=LAUNCH_SETUP_COMMAND,
    )
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_BUTTON_LABEL,
        annotation='Launch the Rig Tail Builder UI',
        image=SHELF_ICON,
        image1=SHELF_ICON,
        sourceType='python',
        command=LAUNCH_COMMAND,
    )
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_MANUAL_LABEL,
        annotation='Rig Tail Manual Run: import / build / setup / test '
                   'the modules from source (developer console workflow)',
        image=SHELF_ICON_MANUAL,
        image1=SHELF_ICON_MANUAL,
        sourceType='python',
        command=LAUNCH_MANUAL_COMMAND,
    )
    return shelf


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    src_dir = _source_dir()
    modules_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')

    try:
        module_dir = _copy_module(src_dir, modules_dir)
        _activate_for_session(module_dir)
        shelf = _add_shelf_button()
    except Exception as exc:  # surface a readable error to the user
        cmds.confirmDialog(
            title='Rig Tail install failed',
            message=str(exc),
            button=['OK'],
            icon='critical')
        raise

    cmds.inViewMessage(
        amg='<hl>Rig Tail installed</hl> - see the "{0}", "{1}" and "{2}" '
            'buttons on the "{3}" shelf'.format(
                SHELF_SETUP_LABEL, SHELF_BUTTON_LABEL, SHELF_MANUAL_LABEL,
                shelf),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail: installed module to {0}'.format(module_dir))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
