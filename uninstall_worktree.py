'''
# uninstall_worktree.py
author: Daisy Jane @gnitemouse

Remove the Maya shelf launchers that install_worktree.py added.

Drag this file into Maya. Buttons are matched by the worktree label
install_worktree.py writes, so only worktree launchers are touched; an
ordinary Rig Tail install has none and is left alone. Dropped from inside
a worktree it offers that worktree first, since a session may carry
buttons for several branches at once.

Nothing was copied on install, so nothing is deleted from disk. What
remains is session state: the worktree's scripts directory sits on
sys.path and its modules are in sys.modules, both of which would keep
answering imports after the buttons are gone. Clearing them is what hands
the session back to the installed copy.

Requires Maya 2020+ (Python 3).
'''

import os
import re
import sys

import maya.cmds as cmds
import maya.mel as mel

MODULE_NAME = 'rigTail'

LABEL_PREFIX = 'WT'
LABEL_SEP = ':'

# Matches the label install_worktree.py builds: WT:<branch>:<tool>
LABEL_RE = re.compile(
    r'^' + re.escape(LABEL_PREFIX + LABEL_SEP) + r'(.+)' +
    re.escape(LABEL_SEP) + r'[^' + re.escape(LABEL_SEP) + r']+$')

TOOL_DIR_RE = re.compile(r'^TOOL_DIR\s*=\s*r?[\'"](.+?)[\'"]\s*$', re.M)

ALL_BRANCHES = '<all worktrees>'


def _source_dir():
    '''Return this uninstaller's folder.'''
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.abspath(os.getcwd())


def _same_path(a, b):
    '''Compare normalized absolute paths.'''
    return (os.path.normcase(os.path.normpath(os.path.abspath(a))) ==
            os.path.normcase(os.path.normpath(os.path.abspath(b))))


def _worktree_buttons():
    '''Every worktree launcher, as (shelf, button, branch) triples.'''
    found = []
    top = mel.eval('$_tmp = $gShelfTopLevel')
    shelves = cmds.tabLayout(top, query=True, childArray=True) or []
    for shelf in shelves:
        children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
        for child in children:
            if not (cmds.control(child, exists=True) and
                    cmds.shelfButton(child, query=True, exists=True)):
                continue
            label = cmds.shelfButton(child, query=True, label=True) or ''
            match = LABEL_RE.match(label)
            if match:
                found.append((shelf, child, match.group(1)))
    return found


def _tool_dirs(buttons):
    '''Unique TOOL_DIR values held by these buttons.'''
    dirs = []
    for _, button, _branch in buttons:
        command = cmds.shelfButton(button, query=True, command=True) or ''
        match = TOOL_DIR_RE.search(command)
        if match:
            path = os.path.normpath(match.group(1))
            if not any(_same_path(path, known) for known in dirs):
                dirs.append(path)
    return dirs


def _choose_branch(branches, here):
    '''Ask which worktree to remove.

    Returns a branch name, ALL_BRANCHES, or None if the user cancelled.
    The worktree this file was dropped from is preselected, since that is
    the one the user is most likely finished with.

    '''
    options = list(branches)
    if len(options) > 1:
        options.append(ALL_BRANCHES)
    default = here if here in options else options[0]

    def _dialog_ui():
        cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
        cmds.text(label='Remove which worktree shelf buttons?', align='left')
        cmds.separator(style='in', height=10)
        cmds.radioCollection()
        radios = {}
        for name in options:
            radios[name] = cmds.radioButton(
                label=name, select=(name == default))
        cmds.separator(style='in', height=10)
        cmds.text(
            label='No files are deleted. The modules are unloaded and the\n'
                  'path removed, handing the session back to your install.',
            align='left')
        cmds.separator(style='in', height=10)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                       columnAttach=(1, 'both', 0))

        def _dismiss():
            for name, radio in radios.items():
                if cmds.radioButton(radio, query=True, select=True):
                    cmds.layoutDialog(dismiss=name)
                    return
            cmds.layoutDialog(dismiss='Cancel')

        cmds.button(label='Remove', command=lambda *_: _dismiss())
        cmds.button(label='Cancel',
                    command=lambda *_: cmds.layoutDialog(dismiss='Cancel'))

    response = cmds.layoutDialog(ui=_dialog_ui, title='Rig Tail worktree')
    if not response or response in ('Cancel', 'dismiss'):
        return None
    return response


def _purge_modules():
    '''Unload every rig_tail module, returning the count.

    Modules already imported outrank sys.path, so removing the directory
    alone would leave the worktree's code answering for the rest of the
    session.

    '''
    names = [mod for mod in sys.modules
             if mod.startswith('rig_tail') or mod == 'logger_config']
    for name in names:
        del sys.modules[name]
    return len(names)


def _remove_from_path(tool_dirs):
    '''Drop these directories from sys.path, returning those removed.'''
    removed = []
    for tool_dir in tool_dirs:
        matches = [p for p in sys.path if _same_path(p, tool_dir)]
        if matches:
            sys.path = [p for p in sys.path
                        if not _same_path(p, tool_dir)]
            removed.append(tool_dir)
    return removed


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    buttons = _worktree_buttons()
    if not buttons:
        cmds.confirmDialog(
            title='Rig Tail worktree',
            message='No worktree shelf buttons found.\n\n'
                    'Your ordinary Rig Tail install is untouched by this '
                    'file; use uninstall.py for that.',
            button=['OK'])
        print('# Rig Tail worktree: nothing to remove')
        return

    branches = sorted({branch for _, _, branch in buttons})
    here = os.path.basename(os.path.normpath(_source_dir()))
    choice = _choose_branch(branches, here)
    if choice is None:
        print('# Rig Tail worktree: cancelled')
        return

    targets = (buttons if choice == ALL_BRANCHES
               else [b for b in buttons if b[2] == choice])
    tool_dirs = _tool_dirs(targets)

    labels = [cmds.shelfButton(button, query=True, label=True)
              for _, button, _b in targets]
    for _shelf, button, _branch in targets:
        cmds.deleteUI(button)

    removed_paths = _remove_from_path(tool_dirs)
    purged = _purge_modules()

    cmds.inViewMessage(
        amg='<hl>Rig Tail worktree removed</hl> - {0} button(s), '
            '{1} module(s) unloaded'.format(len(labels), purged),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail worktree: removed {0}'.format(', '.join(labels)))
    for path in removed_paths:
        print('# Rig Tail worktree: sys.path -= {0}'.format(path))
    print('# Rig Tail worktree: {0} module(s) unloaded'.format(purged))
    print('# Rig Tail worktree: no files deleted')


if __name__ == '__main__':
    onMayaDroppedPythonFile()
