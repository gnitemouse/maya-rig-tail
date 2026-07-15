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
    the module in and adds a "TailRig" shelf button. Works immediately --
    no restart. Keep install.py next to the rigTail/ folder and
    rigTail.mod when you drag it, since it copies them.

INSTALL (manual, no drag-and-drop)
    Copy rigTail/ and rigTail.mod into ~/Documents/maya/modules/
    then restart Maya. Launch from the Script Editor with:
        import rig_tail; rig_tail.main()
    (or make your own shelf button running the same two lines).

UNINSTALL
    Delete rigTail.mod and the rigTail folder from
    ~/Documents/maya/modules/, and remove the shelf button.
--------------------------------------------------------------------------

Installed layout:
    <userAppDir>/modules/
        rigTail.mod
        rigTail/
            scripts/   rig_tail*.py + logger_config.py
            icons/     octopus.png, octopus_200.png

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

# Shelf icon: "Octopus" icon by Icons8 (https://icons8.com/icons/set/octopus).
# Free use requires attribution -- see the Credits section of README.md.
# Resolved by bare name via the module's icon path once registered.
SHELF_ICON = 'octopus.png'

SHELF_BUTTON_LABEL = 'TailRig'

# Command the shelf button runs. No absolute path is baked in: once the
# module is registered, scripts/ is on sys.path automatically.
LAUNCH_COMMAND = '''# Launch Rig Tail
import importlib
import rig_tail
importlib.reload(rig_tail)
rig_tail.main()
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
    '''Add (or refresh) the TailRig launcher on the active shelf.'''
    shelf = _current_shelf()
    _remove_existing_button(shelf, SHELF_BUTTON_LABEL)

    cmds.shelfButton(
        parent=shelf,
        label=SHELF_BUTTON_LABEL,
        annotation='Launch the Rig Tail builder UI',
        image=SHELF_ICON,
        image1=SHELF_ICON,
        sourceType='python',
        command=LAUNCH_COMMAND,
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
        amg='<hl>Rig Tail installed</hl> - see the "{0}" button on the '
            '"{1}" shelf'.format(SHELF_BUTTON_LABEL, shelf),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail: installed module to {0}'.format(module_dir))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
