'''
# uninstall.py -- drag-and-drop uninstaller for Rig Tail
author: Daisy Jane @gnitemouse

Removes everything install.py added: the three shelf buttons (TailSetup,
TailRig, TailManual) from every shelf, the rigTail.mod that registers the
module, and the module folder itself. It also drops the module's scripts/
and icons/ paths from the running session so nothing lingers until
restart.

Because install.py can put the module in the Maya modules folder, leave
it in a git clone, or copy it to a folder of the user's choosing, this
does not assume a location. It finds the install three ways, in order:

    rigTail.install.json    the manifest install.py writes beside the
                            .mod, naming the module folder and whether
                            it was copied there
    rigTail.mod             the module path Maya is registering
    shelf buttons           the TOOL_DIR baked into their commands

A module folder is only deleted when the manifest says the installer
copied it there. A folder the installer merely pointed at -- a git clone
installed with "Current (this folder)" -- is left untouched: the .mod and
buttons go, the source stays. Deleting a copy that sits outside the Maya
modules folder asks first.

--------------------------------------------------------------------------
UNINSTALL (drag-and-drop)
    Drag uninstall.py from a file browser into the Maya viewport. Works
    immediately -- no restart.

UNINSTALL (manual)
    Delete rigTail.mod and rigTail.install.json from
    ~/Documents/maya/modules/, delete the rigTail folder if it was copied
    there, and remove the shelf buttons by hand.
--------------------------------------------------------------------------

Compatible with Maya 2020+ (Python 3).
'''

import json
import os
import re
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

# Must match install.py.
MODULE_NAME = 'rigTail'
MOD_FILE = MODULE_NAME + '.mod'
MANIFEST_FILE = MODULE_NAME + '.install.json'
SHELF_LABELS = ('TailSetup', 'TailRig', 'TailManual')

# TOOL_DIR as install.py bakes it into every shelf button command.
TOOL_DIR_RE = re.compile(r'^TOOL_DIR\s*=\s*r?[\'"](.+?)[\'"]\s*$', re.M)

# A module line: "+ [FLAG:value ...] rigTail 1.0 <path>". The path runs to
# the end of the line rather than being one token -- "C:/Users/Jane Doe/
# tools" is a perfectly ordinary place to install to.
MOD_LINE_RE = re.compile(
    r'^\+\s+(?:\S+\s+)*?' + MODULE_NAME + r'\s+\S+\s+(.+?)\s*$')


def _same_path(a, b):
    '''True if two paths point at the same place (case-insensitive on
    Windows, and blind to trailing slashes or ".." segments).'''
    return (os.path.normcase(os.path.normpath(os.path.abspath(a))) ==
            os.path.normcase(os.path.normpath(os.path.abspath(b))))


def _is_inside(path, parent):
    '''True if path sits under parent.'''
    path = os.path.normcase(os.path.abspath(path))
    parent = os.path.normcase(os.path.abspath(parent))
    return path.startswith(parent + os.sep)


def _rig_tail_buttons():
    '''Every Rig Tail shelf button, as (shelf, button) pairs.'''
    found = []
    top = mel.eval('$_tmp = $gShelfTopLevel')
    shelves = cmds.tabLayout(top, query=True, childArray=True) or []
    for shelf in shelves:
        children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
        for child in children:
            if not (cmds.control(child, exists=True) and
                    cmds.shelfButton(child, query=True, exists=True)):
                continue
            if cmds.shelfButton(child, query=True, label=True) in SHELF_LABELS:
                found.append((shelf, child))
    return found


def _tool_dirs_from_buttons(buttons):
    '''Read the TOOL_DIR baked into each button's command.

    The buttons are the one record that survives a hand-deleted manifest,
    and they name the install that was actually being launched.
    '''
    tool_dirs = []
    for _, button in buttons:
        command = cmds.shelfButton(button, query=True, command=True) or ''
        match = TOOL_DIR_RE.search(command)
        if match:
            path = os.path.normpath(match.group(1))
            if not any(_same_path(path, known) for known in tool_dirs):
                tool_dirs.append(path)
    return tool_dirs


def _remove_shelf_buttons(buttons):
    '''Delete the Rig Tail buttons, returning the count.'''
    for _, button in buttons:
        cmds.deleteUI(button)
    return len(buttons)


def _read_manifest(modules_dir):
    '''The manifest install.py wrote, or None if it is missing or
    unreadable (a hand-edited or half-deleted install).'''
    path = os.path.join(modules_dir, MANIFEST_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (ValueError, IOError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _module_dir_from_mod(modules_dir):
    '''The module folder the .mod registers, or None.

    A .mod line is "+ rigTail 1.0 <path>", where <path> is either
    absolute or relative to the folder holding the .mod.
    '''
    path = os.path.join(modules_dir, MOD_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as handle:
            lines = handle.readlines()
    except (IOError, OSError):
        return None

    for line in lines:
        match = MOD_LINE_RE.match(line)
        if match:
            module_path = match.group(1)
            if not os.path.isabs(module_path):
                module_path = os.path.join(modules_dir, module_path)
            return os.path.normpath(module_path)
    return None


def _resolve_module_dir(modules_dir, tool_dirs):
    '''Work out (module_dir, copied) for the install being removed.

    Manifest first -- it is the only source that knows whether the
    installer copied the tree or just pointed at it. Falling back to the
    .mod or the buttons means that answer is unknown, and unknown is
    treated as "do not delete": a wrong guess there deletes someone's
    working copy.
    '''
    manifest = _read_manifest(modules_dir)
    if manifest and manifest.get('module_dir'):
        return os.path.normpath(manifest['module_dir']), \
            bool(manifest.get('copied'))

    from_mod = _module_dir_from_mod(modules_dir)
    if from_mod:
        # No manifest, but a tree sitting in the modules folder can only
        # have been put there by an install.
        return from_mod, _same_path(os.path.dirname(from_mod), modules_dir)

    if tool_dirs:
        module_dir = os.path.dirname(tool_dirs[0])
        return module_dir, _same_path(os.path.dirname(module_dir), modules_dir)

    return None, False


def _confirm_outside_delete(module_dir):
    '''Ask before deleting a copy that lives outside the Maya modules
    folder -- an "Other..." install, where the surrounding folder is the
    user's, not ours.'''
    answer = cmds.confirmDialog(
        title='Remove Rig Tail files?',
        message=('Rig Tail was installed to a folder you chose:\n\n{0}\n\n'
                 'Delete that folder, or keep the files and just remove '
                 'the shelf buttons and module registration?'.format(
                     module_dir)),
        button=['Keep the files', 'Delete it', 'Cancel'],
        defaultButton='Keep the files',
        cancelButton='Cancel',
        dismissString='Cancel')
    return answer == 'Delete it'


def _remove_module(modules_dir, module_dir, copied):
    '''Delete the .mod, the manifest, and the module tree if it is ours.

    Returns (removed_paths, kept) -- kept is (path, reason) when a module
    folder was deliberately left on disk, else None.
    '''
    removed = []
    for name in (MOD_FILE, MANIFEST_FILE):
        path = os.path.join(modules_dir, name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)

    if not module_dir or not os.path.isdir(module_dir):
        return removed, None
    # Never delete a folder the installer only pointed at: with "Current
    # (this folder)" that is the user's git clone.
    if not copied:
        return removed, (module_dir, 'not created by the installer')
    if not _is_inside(module_dir, modules_dir) and \
            not _confirm_outside_delete(module_dir):
        return removed, (module_dir, 'kept at your request')

    shutil.rmtree(module_dir, ignore_errors=True)
    removed.append(module_dir)
    return removed, None


def _deactivate_session(module_dirs, tool_dirs):
    '''Drop every discovered scripts/ and icons/ path from the running
    session so a restart is not needed to fully unhook it.'''
    scripts_dirs = list(tool_dirs)
    icons_dirs = []
    for module_dir in module_dirs:
        if not module_dir:
            continue
        scripts_dirs.append(os.path.join(module_dir, 'scripts'))
        icons_dirs.append(os.path.join(module_dir, 'icons'))

    for scripts_dir in scripts_dirs:
        for entry in [p for p in sys.path if _same_path(p, scripts_dir)]:
            while entry in sys.path:
                sys.path.remove(entry)

    xbm = os.environ.get('XBMLANGPATH', '')
    if xbm:
        parts = [p for p in xbm.split(os.pathsep)
                 if p and not any(_same_path(p, i) for i in icons_dirs)]
        os.environ['XBMLANGPATH'] = os.pathsep.join(parts)

    # Purge imported modules so a later re-install imports cleanly.
    for name in list(sys.modules):
        if name.startswith('rig_tail') or name == 'logger_config':
            del sys.modules[name]


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    modules_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')

    try:
        # Read TOOL_DIR off the buttons before deleting them.
        found = _rig_tail_buttons()
        tool_dirs = _tool_dirs_from_buttons(found)
        module_dir, copied = _resolve_module_dir(modules_dir, tool_dirs)

        buttons = _remove_shelf_buttons(found)
        removed, kept = _remove_module(modules_dir, module_dir, copied)
        _deactivate_session([module_dir], tool_dirs)
    except Exception as exc:  # surface a readable error to the user
        cmds.confirmDialog(
            title='Rig Tail uninstall failed',
            message=str(exc),
            button=['OK'],
            icon='critical')
        raise

    cmds.inViewMessage(
        amg='<hl>Rig Tail uninstalled</hl> - removed {0} shelf button(s)'
            '{1}'.format(buttons, '' if kept else ' and the module'),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail: removed {0} shelf button(s)'.format(buttons))
    for tool_dir in tool_dirs:
        print('# Rig Tail: found install at TOOL_DIR {0}'.format(tool_dir))
    for path in removed:
        print('# Rig Tail: removed {0}'.format(path))
    if kept:
        print('# Rig Tail: left {0} in place ({1})'.format(*kept))
    if not removed:
        print('# Rig Tail: nothing to remove under {0}'.format(modules_dir))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
