'''
# uninstall.py -- drag-and-drop uninstaller for Rig Tail
author: Daisy Jane @gnitemouse

Removes everything install.py added: the three shelf buttons (TailSetup,
TailRig, TailManual) from every shelf, and the installed module folder and
.mod file under ~/Documents/maya/modules/. It also drops the module's
scripts/ and icons/ paths from the running session so nothing lingers
until restart.

If Rig Tail was installed with the "Run from this folder" option, there
is nothing under modules/ to delete -- this removes the shelf buttons and
reports that. The source folder itself is never touched.

--------------------------------------------------------------------------
UNINSTALL (drag-and-drop)
    Drag uninstall.py from a file browser into the Maya viewport. It
    deletes the shelf buttons and the installed module. Works immediately
    -- no restart.

UNINSTALL (manual)
    Delete rigTail.mod and the rigTail folder from
    ~/Documents/maya/modules/, and remove the shelf buttons by hand.
--------------------------------------------------------------------------

Compatible with Maya 2020+ (Python 3).
'''

import os
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

# Must match install.py.
MODULE_NAME = 'rigTail'
MOD_FILE = MODULE_NAME + '.mod'
SHELF_LABELS = ('TailSetup', 'TailRig', 'TailManual')


def _remove_shelf_buttons():
    '''Delete the Rig Tail buttons from every shelf, returning the count.'''
    removed = 0
    top = mel.eval('$_tmp = $gShelfTopLevel')
    shelves = cmds.tabLayout(top, query=True, childArray=True) or []
    for shelf in shelves:
        children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
        for child in children:
            if not (cmds.control(child, exists=True) and
                    cmds.shelfButton(child, query=True, exists=True)):
                continue
            if cmds.shelfButton(child, query=True, label=True) in SHELF_LABELS:
                cmds.deleteUI(child)
                removed += 1
    return removed


def _remove_module(modules_dir):
    '''Delete the installed module folder and .mod file, if present.'''
    removed = []
    module_dir = os.path.join(modules_dir, MODULE_NAME)
    mod_path = os.path.join(modules_dir, MOD_FILE)
    if os.path.isdir(module_dir):
        shutil.rmtree(module_dir, ignore_errors=True)
        removed.append(module_dir)
    if os.path.isfile(mod_path):
        os.remove(mod_path)
        removed.append(mod_path)
    return module_dir, removed


def _deactivate_session(module_dir):
    '''Drop the module's scripts/ and icons/ from the running session so a
    restart is not needed to fully unhook it.'''
    scripts_dir = os.path.join(module_dir, 'scripts')
    icons_dir = os.path.join(module_dir, 'icons')

    while scripts_dir in sys.path:
        sys.path.remove(scripts_dir)

    xbm = os.environ.get('XBMLANGPATH', '')
    if xbm:
        parts = [p for p in xbm.split(os.pathsep) if p and p != icons_dir]
        os.environ['XBMLANGPATH'] = os.pathsep.join(parts)

    # Purge imported modules so a later re-install imports cleanly.
    for name in list(sys.modules):
        if name.startswith('rig_tail'):
            del sys.modules[name]


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    modules_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')

    try:
        buttons = _remove_shelf_buttons()
        module_dir, removed = _remove_module(modules_dir)
        _deactivate_session(module_dir)
    except Exception as exc:  # surface a readable error to the user
        cmds.confirmDialog(
            title='Rig Tail uninstall failed',
            message=str(exc),
            button=['OK'],
            icon='critical')
        raise

    cmds.inViewMessage(
        amg='<hl>Rig Tail uninstalled</hl> - removed {0} shelf button(s) '
            'and the installed module'.format(buttons),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail: removed {0} shelf button(s)'.format(buttons))
    for path in removed:
        print('# Rig Tail: removed {0}'.format(path))
    if not removed:
        print('# Rig Tail: no installed module found under {0}'.format(
            modules_dir))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
