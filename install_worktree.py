'''
# install_worktree.py
author: Daisy Jane @gnitemouse

Add Maya shelf launchers that run Rig Tail from THIS git worktree.

Drag this file into Maya from a worktree checkout. The buttons it makes
run the worktree's scripts directly, so a branch can be tested in a real
Maya session without disturbing an existing install: nothing is copied,
no module registration is written, and the ordinary Rig Tail buttons keep
pointing where they always did.

Isolation rests on two things. Each button pins its own scripts directory
at the front of sys.path on every click, and purges every rig_tail module
first, so whichever button was pressed last is the tree that answers the
next import. The main install's own buttons do the same for themselves,
with one exception: they keep rig_tail_constants to preserve the session's
roster, so use the main Tail Reload button when switching back to it.

Buttons are labelled with the worktree name, which is what keeps them
distinct from the main install's and from other worktrees'. uninstall.py
leaves them alone; uninstall_worktree.py is what removes them.

Requires Maya 2020+ (Python 3).
'''

import os
import sys

import maya.cmds as cmds
import maya.mel as mel

MODULE_NAME = 'rigTail'

LABEL_PREFIX = 'WT'
LABEL_SEP = ':'

SHELF_ICON_CHAIN = 'octopus_white.png'
SHELF_ICON_SETUP = 'octopus_light_grey.png'
SHELF_ICON_BUILD = 'octopus_dark_grey.png'
SHELF_ICON_RELOAD = 'octopus_black.png'

CHAIN = 'chain'
SETUP = 'setup'
BUILD = 'build'
RELOAD = 'reload'
COMPONENTS = (CHAIN, SETUP, BUILD, RELOAD)

TOOL_NAMES = {
    CHAIN: 'ChainBuild',
    SETUP: 'TailSetup',
    BUILD: 'TailBuild',
    RELOAD: 'TailReload',
}

OVERLAY_LABELS = {
    CHAIN: 'Chain',
    SETUP: 'Setup',
    BUILD: 'Build',
    RELOAD: 'Reload',
}

COMPONENT_ICONS = {
    CHAIN: SHELF_ICON_CHAIN,
    SETUP: SHELF_ICON_SETUP,
    BUILD: SHELF_ICON_BUILD,
    RELOAD: SHELF_ICON_RELOAD,
}

# An amber badge behind the overlay text, so a worktree button is
# recognisable at a glance against the main install's identical icons.
OVERLAY_BACK_COLOR = (0.75, 0.45, 0.05, 1.0)
OVERLAY_TEXT_COLOR = (1.0, 1.0, 1.0)

# Unlike the main install's preamble, this one re-pins on every click
# rather than inserting only when absent. Two trees can both be on
# sys.path at once, and the button pressed most recently has to be the one
# that wins.
TOOL_DIR_PREAMBLE = '''import sys
import importlib as il

# Worktree this button runs from.
TOOL_DIR = r'{0}'
sys.path = [p for p in sys.path if p != TOOL_DIR]
sys.path.insert(0, TOOL_DIR)
'''

# The main install keeps rig_tail_constants so an unsaved roster survives a
# relaunch. A worktree button cannot: constants carries naming templates
# and build options that a branch may have changed, and one left over from
# the other tree would be read as this tree's own.
PURGE_MODULES = '''# Every rig_tail module, so nothing survives from another checkout
for mod in list(sys.modules):
    if mod.startswith('rig_tail') or mod == 'logger_config':
        del sys.modules[mod]
'''

PREAMBLE_TOKEN = '#@TOOL_DIR@'
PURGE_TOKEN = '#@PURGE@'

LAUNCH_BUILD_COMMAND = '''# Launch Tail Builder (worktree)
#@TOOL_DIR@

#@PURGE@
import rig_tail_build_ui as rt_build_ui
import rig_tail_build_test as rt_build_test
rt_build_ui.show_ui()
'''

LAUNCH_CHAIN_COMMAND = '''# Launch Joint Chain Builder (worktree)
#@TOOL_DIR@

#@PURGE@
import rig_tail_chain_build_ui as rt_chain_ui
import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing
import rig_tail_chain_test as rt_chain_test
rt_chain_ui.show_ui()
'''

LAUNCH_SETUP_COMMAND = '''# Launch Tail Setup (worktree)
#@TOOL_DIR@

#@PURGE@
import rig_tail_setup as rt_setup
import rig_tail_setup_ui as rt_setup_ui
import rig_tail_setup_test as rt_setup_test
rt_setup_ui.show_ui()
'''

# Binds every module for interactive use and lays the workflow out as
# commented calls. A worktree has the whole repo on disk, so unlike the
# main installer's Reload there is no selective install to work around.
LAUNCH_RELOAD_COMMAND = '''# Rig Tail -- Reload ({branch})
#@TOOL_DIR@

#@PURGE@
import rig_tail as rt
import rig_tail_anim as rt_anim
import rig_tail_cache as rt_cache
import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing
import rig_tail_chain_test as rt_chain_test
import rig_tail_cleanup as rt_cleanup
import rig_tail_connect as rt_connect
import rig_tail_constants as rt_constants
import rig_tail_control as rt_control
import rig_tail_ctrlall as rt_ctrlall
import rig_tail_curve as rt_curve
import rig_tail_fk as rt_fk
import rig_tail_joint as rt_joint
import rig_tail_math as rt_math
import rig_tail_matrix as rt_matrix
import rig_tail_maya as rt_maya
import rig_tail_mirror as rt_mirror
import rig_tail_naming as rt_naming
import rig_tail_restpose as rt_rest
import rig_tail_setup as rt_setup
import rig_tail_setup_test as rt_setup_test
import rig_tail_stretch as rt_stretch
import rig_tail_build_test as rt_build_test

print('# Rig Tail worktree: {branch}')
print('# Rig Tail worktree: ' + rt_maya.__file__)

# --- BUILD ---
#rt.rig_tail_single('tail', fk=True, ik=True)
#rt.rig_tail_multiple('squid', fk=True, ik=True)
#rt.main()

# --- TEST / INSPECT ---
#rt_build_test.run_all('squid')
#rt_build_test.test_stretch('C_fintail')
#rt_build_test.test_twist_roll_offset('C_fintail')
#rt_build_test.report_bend(rt_constants.RIGPARTS)
'''


def _source_dir():
    '''Return this installer's folder.'''
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.abspath(os.getcwd())


def _branch_name(repo_dir):
    '''Return the checked-out branch, falling back to the folder name.

    A worktree's .git is a file pointing at the real git directory, so the
    branch is read from there rather than by shelling out to git, which is
    not reliably on PATH inside Maya.

    '''
    fallback = os.path.basename(os.path.normpath(repo_dir))
    git_path = os.path.join(repo_dir, '.git')
    try:
        if os.path.isfile(git_path):
            with open(git_path) as handle:
                gitdir = handle.read().strip()
            if not gitdir.startswith('gitdir:'):
                return fallback
            gitdir = gitdir.split(':', 1)[1].strip()
        elif os.path.isdir(git_path):
            gitdir = git_path
        else:
            return fallback

        with open(os.path.join(gitdir, 'HEAD')) as handle:
            head = handle.read().strip()
    except (IOError, OSError):
        return fallback

    if head.startswith('ref:'):
        return head.split('/')[-1] or fallback
    return head[:7] or fallback


def _shelf_label(branch, key):
    '''Label identifying one tool in one worktree.'''
    return LABEL_SEP.join((LABEL_PREFIX, branch, TOOL_NAMES[key]))


def _shelf_command(template, tool_dir, branch):
    '''Embed the worktree path and purge into a shelf command.'''
    preamble = TOOL_DIR_PREAMBLE.format(tool_dir.replace('\\', '/'))
    return (template.replace('{branch}', branch)
                    .replace(PREAMBLE_TOKEN, preamble)
                    .replace(PURGE_TOKEN, PURGE_MODULES))


def _current_shelf():
    '''Return the name of the currently visible shelf.'''
    top = mel.eval('$_tmp = $gShelfTopLevel')
    return cmds.tabLayout(top, query=True, selectTab=True)


def _remove_existing_button(shelf, label):
    '''Delete a prior button with this label so re-running does not stack them.'''
    children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
    for child in children:
        if cmds.control(child, exists=True) and cmds.shelfButton(
                child, query=True, exists=True):
            if cmds.shelfButton(child, query=True, label=True) == label:
                cmds.deleteUI(child)


def _icon_path(icons_dir, name):
    '''Return the worktree's icon, or its Maya-resolved filename.'''
    path = os.path.join(icons_dir, name)
    return path if os.path.isfile(path) else name


def _choose_components(branch, module_dir):
    '''Ask which worktree buttons to add.

    Returns a component -> bool mapping, or None if the user cancelled.

    '''
    cancelled = 'Cancel|' + '|'.join('0' for _ in COMPONENTS)

    def _dialog_ui():
        cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
        cmds.text(label='Add shelf buttons running Rig Tail from:', align='left')
        cmds.text(label='    branch  {0}'.format(branch), align='left')
        cmds.text(label='    folder  {0}'.format(module_dir), align='left')

        cmds.separator(style='in', height=10)
        cmds.text(
            label='Nothing is copied and no module is registered. Your\n'
                  'existing Rig Tail install is left exactly as it is.',
            align='left')
        cmds.separator(style='in', height=10)

        boxes = {}
        boxes[CHAIN] = cmds.checkBox(
            label='Chain Builder', value=False)
        boxes[SETUP] = cmds.checkBox(
            label='Tail Setup', value=False)
        boxes[BUILD] = cmds.checkBox(
            label='Tail Builder', value=True, enable=False)
        boxes[RELOAD] = cmds.checkBox(
            label='Tail Reload', value=True)
        cmds.separator(style='in', height=10)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                       columnAttach=(1, 'both', 0))

        def _dismiss(choice):
            flags = '|'.join(
                '1' if cmds.checkBox(boxes[key], query=True, value=True) else '0'
                for key in COMPONENTS)
            cmds.layoutDialog(dismiss='{0}|{1}'.format(choice, flags))

        cmds.button(label='Add buttons', command=lambda *_: _dismiss('Add'))
        cmds.button(label='Cancel',
                    command=lambda *_: cmds.layoutDialog(dismiss=cancelled))

    response = cmds.layoutDialog(
        ui=_dialog_ui, title='Rig Tail worktree: {0}'.format(branch))
    parts = (response or '').split('|')
    if parts[0] != 'Add':
        return None

    flags = parts[1:]
    selection = {key: (flags[i] == '1' if i < len(flags) else False)
                 for i, key in enumerate(COMPONENTS)}
    selection[BUILD] = True
    return selection


def _add_shelf_buttons(icons_dir, tool_dir, branch, selection):
    '''Refresh this worktree's launchers and return (shelf, labels).'''
    shelf = _current_shelf()
    commands = {
        CHAIN: LAUNCH_CHAIN_COMMAND,
        SETUP: LAUNCH_SETUP_COMMAND,
        BUILD: LAUNCH_BUILD_COMMAND,
        RELOAD: LAUNCH_RELOAD_COMMAND,
    }
    annotations = {
        CHAIN: 'Chain Builder UI (space joint chains)',
        SETUP: 'Tail Setup UI (orient/mirror skeleton)',
        BUILD: 'Tail Builder UI (build rig)',
        RELOAD: 'Tail Reload UI (reload modules)',
    }

    labels = []
    for key in COMPONENTS:
        label = _shelf_label(branch, key)
        _remove_existing_button(shelf, label)
        if not selection.get(key):
            continue
        icon = _icon_path(icons_dir, COMPONENT_ICONS[key])
        cmds.shelfButton(
            parent=shelf,
            label=label,
            annotation='[{0}] {1}\n{2}'.format(
                branch, annotations[key], tool_dir),
            image=icon,
            image1=icon,
            imageOverlayLabel=OVERLAY_LABELS[key],
            overlayLabelColor=OVERLAY_TEXT_COLOR,
            overlayLabelBackColor=OVERLAY_BACK_COLOR,
            sourceType='python',
            command=_shelf_command(commands[key], tool_dir, branch),
        )
        labels.append(label)
    return shelf, labels


def _activate_for_session(module_dir):
    '''Pin this worktree's scripts and icons for the current session.'''
    scripts_dir = os.path.join(module_dir, 'scripts')
    icons_dir = os.path.join(module_dir, 'icons')

    sys.path = [p for p in sys.path if p != scripts_dir]
    sys.path.insert(0, scripts_dir)

    xbm = os.environ.get('XBMLANGPATH', '')
    parts = xbm.split(os.pathsep) if xbm else []
    if icons_dir not in parts:
        os.environ['XBMLANGPATH'] = (
            icons_dir + (os.pathsep + xbm if xbm else ''))


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    src_dir = _source_dir()
    module_dir = os.path.join(src_dir, MODULE_NAME)
    scripts_dir = os.path.join(module_dir, 'scripts')

    if not os.path.isdir(scripts_dir):
        cmds.confirmDialog(
            title='Rig Tail worktree install failed',
            message='Could not find {0}/scripts next to this file in\n{1}\n\n'
                    'Keep install_worktree.py at the top of the '
                    'worktree.'.format(MODULE_NAME, src_dir),
            button=['OK'], icon='critical')
        return

    branch = _branch_name(src_dir)
    selection = _choose_components(branch, module_dir)
    if selection is None:
        print('# Rig Tail worktree: cancelled')
        return

    try:
        _activate_for_session(module_dir)
        shelf, labels = _add_shelf_buttons(
            os.path.join(module_dir, 'icons'), scripts_dir, branch, selection)
    except Exception as exc:
        cmds.confirmDialog(
            title='Rig Tail worktree install failed',
            message=str(exc), button=['OK'], icon='critical')
        raise

    cmds.inViewMessage(
        amg='<hl>Rig Tail worktree "{0}"</hl> - {1} button(s) added to the '
            '"{2}" shelf'.format(branch, len(labels), shelf),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail worktree: {0}'.format(branch))
    print('# Rig Tail worktree: TOOL_DIR -> {0}'.format(scripts_dir))
    print('# Rig Tail worktree: shelf buttons -> {0} on "{1}"'.format(
        ', '.join(labels), shelf))
    print('# Rig Tail worktree: nothing copied, no module registered')
    print('# Rig Tail worktree: press the main "TailReload" button to go '
          'back to the installed copy')


if __name__ == '__main__':
    onMayaDroppedPythonFile()
