'''
# install.py
author: Daisy Jane @gnitemouse

Install Rig Tail as a Maya module and add shelf launchers.

Drag this file into Maya, choose an install location, and tick the shelf
buttons you want. The Tail Builder is always installed; the Joint Chain
Builder, Tail Setup and Tail Reload launchers are optional. Installing
to a folder copies only the files the ticked buttons need -- running in place
copies nothing at all. The module registration and install manifest are
written under ~/Documents/maya/modules/. Reinstalling updates files in place.

Requires Maya 2020+ (Python 3).
'''

import json
import os
import shutil
import stat
import sys
import time

import maya.cmds as cmds
import maya.mel as mel

MODULE_NAME = 'rigTail'
MOD_FILE = MODULE_NAME + '.mod'
MODULE_VERSION = '1.0'

MANIFEST_FILE = MODULE_NAME + '.install.json'

MOD_TEMPLATE = '''+ {name} {version} {path}
scripts: scripts
icons: icons
'''

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

# One icon per button, and the only icons that install. The _200 variants
# in icons/ are documentation assets, so leaving them unlisted keeps them
# out of a copied install.
COMPONENT_ICONS = {
    CHAIN: SHELF_ICON_CHAIN,
    SETUP: SHELF_ICON_SETUP,
    BUILD: SHELF_ICON_BUILD,
    RELOAD: SHELF_ICON_RELOAD,
}

# A light matcha badge behind the overlay text, so the shelf reads as part
# of the tool rather than fading into the shelf background.
# install_worktree.py uses amber for the same icons, so the two installs
# stay distinguishable on a shelf carrying both. The two are held at the
# same lightness - white sits on this at 3.5:1 and on the amber at 3.7:1 -
# so neither badge shouts over the other.
OVERLAY_BACK_COLOR = (0.494, 0.569, 0.314, 1.0)
OVERLAY_TEXT_COLOR = (1.0, 1.0, 1.0)


# Scripts each optional button owns. Anything absent from this table is core
# -- the Builder's own modules and the shared library beneath them -- and
# always installs.
#
# rig_tail_setup.py appears under BOTH Setup and Chain. It is the Setup phase
# proper, but Chain's orient option calls rt_setup.aim_frames
# (rig_tail_chain_build._orient_chain), so Chain needs the file even when the
# Setup button is not installed. Its UI and tests stay Setup-only.
#
# The Builder deliberately owns nothing here: rig_tail.py defers its
# rig_tail_setup import into setup_tails() so the build never needs a Setup
# file on disk. Keep it that way -- a top-level import there would make every
# selection below install rig_tail_setup.py.
COMPONENT_SCRIPTS = {
    CHAIN: (
        'rig_tail_chain_build.py',
        'rig_tail_chain_build_ui.py',
        'rig_tail_chain_spacing.py',
        'rig_tail_chain_test.py',
        'rig_tail_setup.py',
    ),
    SETUP: (
        'rig_tail_setup.py',
        'rig_tail_setup_ui.py',
        'rig_tail_setup_test.py',
    ),
}

# Every script owned by at least one optional button. A script outside this
# set is core and ships regardless of what was ticked.
OPTIONAL_SCRIPTS = frozenset(
    name for names in COMPONENT_SCRIPTS.values() for name in names)

# Each launcher pins its selected scripts directory ahead of other installs.
TOOL_DIR_PREAMBLE = '''import sys
import os
import importlib as il

# Rig Tail install this button was made from. Re-pinned on every click, not
# inserted only when absent: being ON sys.path is not the same as being at
# the FRONT of it. A worktree launcher pins its own tree the same way, so
# once one has been clicked this directory is present but behind it, an
# insert-if-absent does nothing, and the purge below then reloads the
# worktree's copy from the front of the path. Whichever button was pressed
# last has to be the tree the next import answers from.
#
# Compared normalized: the same directory reaches sys.path in both slash
# spellings (this file writes '/', a worktree install writes '\\'), and a
# plain != leaves the other spelling sitting in front.
TOOL_DIR = r'{0}'
_rt_key = lambda p: os.path.normcase(os.path.normpath(p))
sys.path = [p for p in sys.path if _rt_key(p) != _rt_key(TOOL_DIR)]
sys.path.insert(0, TOOL_DIR)
'''

PREAMBLE_TOKEN = '#@TOOL_DIR@'
PURGE_TOKEN = '#@PURGE@'

# Every launcher purges the whole rig_tail package, not just its own tool's
# modules. Each tool sits on the shared core -- rig_tail_maya, _joint,
# _naming, _cleanup, _math -- so a partial purge leaves that core at
# whatever version it was first imported at, and the tool then fails on a
# core function it cannot see. Clicking a tool's own button is what an
# artist does after an update, so that is where the reload has to be whole.
#
# rig_tail_constants is deliberately kept: it holds the roster and settings
# for the session, and re-importing it would throw away RIGPARTS edits made
# in the Setup UI but not yet saved to a config. It is data and config
# helpers only, with no dependency on the modules being reloaded. Use the
# Reload button when it changes -- that one purges everything.
PURGE_MODULES = '''# Reload the package, keeping the session's settings
# (see PURGE_MODULES in install.py for why rig_tail_constants stays)
for mod in list(sys.modules):
    if mod.startswith('rig_tail') and mod != 'rig_tail_constants':
        del sys.modules[mod]
'''

LAUNCH_BUILD_COMMAND = '''# Launch Tail Builder
#@TOOL_DIR@

#@PURGE@
import rig_tail_build_ui as rt_build_ui
import rig_tail_build_test as rt_build_test
rt_build_ui.show_ui()
'''

LAUNCH_CHAIN_COMMAND = '''# Launch Joint Chain Builder
#@TOOL_DIR@

#@PURGE@
import rig_tail_chain_build_ui as rt_chain_ui
import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing
import rig_tail_chain_test as rt_chain_test
rt_chain_ui.show_ui()
'''

LAUNCH_SETUP_COMMAND = '''# Launch Tail Setup
#@TOOL_DIR@

#@PURGE@
import rig_tail_setup as rt_setup
import rig_tail_setup_ui as rt_setup_ui
import rig_tail_setup_test as rt_setup_test
rt_setup_ui.show_ui()
'''

# Reload: loads/reloads the modules and runs commands rather than opening
# a UI. It force-reimports every module for a clean slate and keeps
# handles bound for interactive use in the Script Editor. Commented lines
# lay out the full workflow -- setup, build and test -- so the user can
# uncomment the call they want.
#
# Assembled per install by _reload_command(), because a selective install
# can leave the Chain or Setup modules off disk: importing them
# unconditionally would make the button raise ImportError on click.
RELOAD_HEAD = '''# Rig Tail -- Reload (load/reload the modules and run commands)
#@TOOL_DIR@

# Purge Cache
PREFIXES = ('rig_tail', 'logger_config')
for mod in list(sys.modules):
    if any(mod.startswith(p) for p in PREFIXES):
        del sys.modules[mod]

# Import
import rig_tail as rt
import rig_tail_anim as rt_anim
import rig_tail_cache as rt_cache
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
import rig_tail_qt as rt_qt
import rig_tail_restpose as rt_rest
import rig_tail_stretch as rt_stretch
import rig_tail_build_test as rt_build_test
'''

# rig_tail_setup ships with Chain as well as Setup, so bind it whenever the
# file is on disk. Its UI and tests are Setup-only.
RELOAD_IMPORT_SETUP = 'import rig_tail_setup as rt_setup\n'
RELOAD_IMPORT_SETUP_TEST = 'import rig_tail_setup_test as rt_setup_test\n'
RELOAD_IMPORT_CHAIN = '''import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing
import rig_tail_chain_test as rt_chain_test
'''

RELOAD_PHASE_CHAIN = '''
# --- CHAIN phase (optional; run BEFORE Setup, on raw BN skeleton) ---
#rt_chain.rebuild_selected(21, 'keep')   # re-space the selected chain(s)
#rt_chain.rebuild_selected(30, 'power', param=1.7)   # pack joints toward the base
'''

RELOAD_PHASE_SETUP = '''
# --- SETUP phase (optional; run BEFORE the build, on raw BN skeleton) ---
#rt.setup_tails('squid', dry_run=True)   # preview only (orient/mirror), no changes
#rt.setup_tails('squid')                 # apply orient/mirror, then build
#rt.main_setup()                         # or open the Setup UI
'''

RELOAD_PHASE_BUILD = '''
# --- BUILD ---
#rt.rig_tail_single('tail', fk=True, ik=True)
#rt.rig_tail_multiple('squid', fk=True, ik=True)
#rt.main()                               # or open the Builder UI

# --- TEST / INSPECT ---
'''

RELOAD_TEST_CHAIN = ('#rt_chain_test.run_math()'
                     '                                # chain spacing maths (safe)\n')

RELOAD_TEST_BUILD = '''#rt_build_test.run_all('squid')                          # full test sweep
#rt_build_test.report_bend(rt_constants.RIGPARTS)        # per-chain bend angles
#rt_build_test.probe('after build', 'C_fintail')         # quick joint probe
#rt_build_test.measure_rebuild_degradation(rt_constants.RIGPARTS, rebuilds=2)
'''


def _source_dir():
    '''Return this installer folder.'''
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.abspath(os.getcwd())


def _shelf_command(template, tool_dir):
    '''Embed tool_dir and the module purge in a shelf command.'''
    preamble = TOOL_DIR_PREAMBLE.format(tool_dir.replace('\\', '/'))
    return (template.replace(PREAMBLE_TOKEN, preamble)
                    .replace(PURGE_TOKEN, PURGE_MODULES))


def _reload_command(selection):
    '''Build the Reload button command for the installed components.

    Only the modules that were installed are imported, and only the
    workflow lines that can actually run are offered.

    '''
    chain = selection.get(CHAIN)
    setup = selection.get(SETUP)

    parts = [RELOAD_HEAD]
    if chain or setup:
        parts.append(RELOAD_IMPORT_SETUP)
    if setup:
        parts.append(RELOAD_IMPORT_SETUP_TEST)
    if chain:
        parts.append(RELOAD_IMPORT_CHAIN)

    if chain:
        parts.append(RELOAD_PHASE_CHAIN)
    if setup:
        parts.append(RELOAD_PHASE_SETUP)
    parts.append(RELOAD_PHASE_BUILD)
    if chain:
        parts.append(RELOAD_TEST_CHAIN)
    parts.append(RELOAD_TEST_BUILD)
    return ''.join(parts)


def _install_filter(selection):
    '''Return a predicate deciding which module files to install.

    It takes a path relative to the module folder ('scripts/rig_tail.py',
    'icons/octopus_white.png') and answers whether this selection wants it.

    '''
    scripts = set()
    icons = set()
    for key, wanted in selection.items():
        if not wanted:
            continue
        scripts.update(COMPONENT_SCRIPTS.get(key, ()))
        if key in COMPONENT_ICONS:
            icons.add(COMPONENT_ICONS[key])

    def keep(rel_path):
        folder, _, name = rel_path.replace('\\', '/').rpartition('/')
        if folder == 'icons':
            return name in icons
        if name in OPTIONAL_SCRIPTS:
            return name in scripts
        return True

    return keep


def _same_path(a, b):
    '''Compare normalized absolute paths.'''
    return (os.path.normcase(os.path.normpath(os.path.abspath(a))) ==
            os.path.normcase(os.path.normpath(os.path.abspath(b))))


def _is_inside(path, parent):
    '''True if path sits under parent.'''
    path = os.path.normcase(os.path.abspath(path))
    parent = os.path.normcase(os.path.abspath(parent))
    return path.startswith(parent + os.sep)


def _release_cwd(path):
    '''Leave path when it is the current working directory.'''
    try:
        cwd = os.getcwd()
    except OSError:
        return None
    if not (_same_path(cwd, path) or _is_inside(cwd, path)):
        return None
    home = os.path.expanduser('~')
    os.chdir(home)
    return home


def _replace_file(src, dst, attempts=3):
    '''Copy one file, handling read-only files and transient locks.'''
    for attempt in range(attempts):
        try:
            if os.path.isfile(dst):
                os.chmod(dst, stat.S_IWRITE)
            shutil.copy2(src, dst)
            return
        except (IOError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


def _copy_tree(src, dst, keep):
    '''Copy the files keep() wants and return those that could not update.'''
    blocked = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == os.curdir else os.path.join(dst, rel)

        wanted = [name for name in filenames
                  if not name.endswith('.pyc')
                  and keep(name if rel == os.curdir
                           else os.path.join(rel, name))]
        # A folder nothing was selected from is not created at all.
        if not wanted:
            continue
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)

        for name in wanted:
            try:
                _replace_file(os.path.join(dirpath, name),
                              os.path.join(target_dir, name))
            except (IOError, OSError) as exc:
                blocked.append((os.path.join(target_dir, name), exc))
    return blocked


def _prune_stale(src, dst, keep):
    '''Remove destination files this install no longer covers.

    That means files dropped from the source AND files belonging to a shelf
    button left unticked this time, so re-installing with fewer buttons
    clears what they owned instead of orphaning it.
    '''
    for dirpath, dirnames, filenames in os.walk(dst):
        if os.path.basename(dirpath) == '__pycache__':
            shutil.rmtree(dirpath, ignore_errors=True)
            dirnames[:] = []
            continue
        rel = os.path.relpath(dirpath, dst)
        source_dir = src if rel == os.curdir else os.path.join(src, rel)
        for name in filenames:
            rel_path = name if rel == os.curdir else os.path.join(rel, name)
            if os.path.isfile(os.path.join(source_dir, name)) and keep(rel_path):
                continue
            try:
                os.remove(os.path.join(dirpath, name))
            except (IOError, OSError):
                pass


def _choose_destination(src_dir, modules_dir):
    '''Ask where to install and which shelf buttons to add.

    Returns (folder to hold rigTail/, selection), where selection maps each
    component to a bool. Returns (None, None) if the user cancelled.

    '''
    default_label = 'Default (maya/modules)'
    current_label = 'Current (this folder)'
    other_label = 'Other...'
    cancelled = 'Cancel|' + '|'.join('0' for _ in COMPONENTS)

    def _dialog_ui():
        cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
        cmds.text(label='Where should Rig Tail be installed?', align='left')
        cmds.radioCollection()
        default_radio = cmds.radioButton(
            label='{0}  ({1})'.format(default_label,
                                       os.path.join(modules_dir, MODULE_NAME)),
            select=True)
        current_radio = cmds.radioButton(
            label='{0}  ({1})'.format(current_label,
                                       os.path.join(src_dir, MODULE_NAME)))
        cmds.radioButton(label=other_label)

        cmds.separator(style='in', height=10)
        cmds.text(
            label='Only the files the ticked buttons need are copied. '
                  'Installing in place copies nothing at all.',
            align='left')
        cmds.text(
            label="- 'Chain Builder' creates and re-spaces joint chains.",
            align='left')
        cmds.text(
            label="- 'Tail Setup' orients and mirrors the skeleton.",
            align='left')
        cmds.text(
            label="- 'Tail Builder' builds the rig, and is always installed.",
            align='left')
        cmds.text(
            label="- 'Tail Reload' reloads all modules and binds them in the "
                  'Script Editor.',
            align='left')
        cmds.separator(style='in', height=10)

        cmds.text(label='Which shelf buttons should be installed?', align='left')
        boxes = {}
        boxes[CHAIN] = cmds.checkBox(label='Chain Builder', value=True)
        boxes[SETUP] = cmds.checkBox(label='Tail Setup', value=True)
        # Ticked and disabled, so the Builder reads as going in rather than
        # as an option that went missing
        boxes[BUILD] = cmds.checkBox(
            label='Tail Builder', value=True, enable=False)
        boxes[RELOAD] = cmds.checkBox(label='Tail Reload', value=True)
        cmds.separator(style='in', height=10)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                       columnAttach=(1, 'both', 0))

        def _dismiss(choice):
            flags = '|'.join(
                '1' if cmds.checkBox(boxes[key], query=True, value=True) else '0'
                for key in COMPONENTS)
            cmds.layoutDialog(dismiss='{0}|{1}'.format(choice, flags))

        cmds.button(label='Install', command=lambda *_: _dismiss(
            default_label if cmds.radioButton(default_radio, query=True, select=True)
            else current_label if cmds.radioButton(current_radio, query=True, select=True)
            else other_label))
        cmds.button(label='Cancel',
                    command=lambda *_: cmds.layoutDialog(dismiss=cancelled))

    response = cmds.layoutDialog(ui=_dialog_ui, title='Install Rig Tail')
    # Closing the window rather than pressing a button yields a bare
    # 'dismiss', so read the flags defensively.
    parts = (response or '').split('|')
    choice = parts[0]
    flags = parts[1:]
    selection = {key: (flags[i] == '1' if i < len(flags) else False)
                 for i, key in enumerate(COMPONENTS)}
    selection[BUILD] = True     # the Builder is never optional

    if choice == default_label:
        return modules_dir, selection
    if choice == current_label:
        return src_dir, selection
    if choice == other_label:
        picked = cmds.fileDialog2(
            fileMode=3,                 # 3 = existing directory
            caption='Choose a folder to install Rig Tail into',
            okCaption='Install here',
            dialogStyle=2,
            startingDirectory=src_dir) or []
        # Cancelling the folder picker cancels the install rather than
        # silently falling back to a location the user did not choose.
        return (picked[0], selection) if picked else (None, None)
    return None, None


def _install_module(src_dir, dest_parent, selection):
    '''Install the selected module files and return (module_dir, copied).'''
    src_module = os.path.join(src_dir, MODULE_NAME)
    if not os.path.isdir(os.path.join(src_module, 'scripts')):
        raise RuntimeError(
            'Could not find {0}/scripts next to install.py in\n{1}\n'
            'Keep install.py at the top of the repo when dragging it '
            'in.'.format(MODULE_NAME, src_dir))

    dst_module = os.path.join(dest_parent, MODULE_NAME)
    # Running in place: the source folder IS the install, so return before
    # any copy or prune. This is what keeps a git checkout intact when the
    # user installs fewer buttons than the repo holds -- selection then
    # only decides which shelf buttons get made, never which files survive.
    # Do not move filtering above this check.
    if _same_path(src_module, dst_module):
        return src_module, False

    if not os.path.isdir(dest_parent):
        os.makedirs(dest_parent)

    keep = _install_filter(selection)
    _release_cwd(dst_module)
    blocked = _copy_tree(src_module, dst_module, keep)
    if blocked:
        raise RuntimeError(
            'Could not overwrite {0} file(s) in\n{1}\n\n{2}\n\n'
            'Something still has them open. Close any editor or Explorer '
            'window on that folder and try again; if it persists, restart '
            'Maya and re-run the installer before using the tool.'.format(
                len(blocked), dst_module,
                '\n'.join('{0}\n    {1}'.format(path, exc)
                          for path, exc in blocked[:5])))
    _prune_stale(src_module, dst_module, keep)
    return dst_module, True


def _write_mod(modules_dir, module_dir):
    '''Write the module registration file.'''
    if not os.path.isdir(modules_dir):
        os.makedirs(modules_dir)

    if _same_path(os.path.dirname(module_dir), modules_dir):
        path = MODULE_NAME
    else:
        path = os.path.abspath(module_dir).replace('\\', '/')

    mod_path = os.path.join(modules_dir, MOD_FILE)
    with open(mod_path, 'w') as handle:
        handle.write(MOD_TEMPLATE.format(
            name=MODULE_NAME, version=MODULE_VERSION, path=path))
    return mod_path


def _write_manifest(modules_dir, module_dir, tool_dir, copied, selection):
    '''Write the install record used by uninstall.py.

    'buttons' records what was ticked. The uninstaller does not need it --
    it sweeps every Rig Tail label off the shelves by name -- but it makes
    one install diffable against the next.

    '''
    manifest_path = os.path.join(modules_dir, MANIFEST_FILE)
    with open(manifest_path, 'w') as handle:
        json.dump({
            'module_dir': os.path.abspath(module_dir),
            'tool_dir': os.path.abspath(tool_dir),
            'copied': bool(copied),
            'buttons': [key for key in COMPONENTS if selection.get(key)],
        }, handle, indent=4)
    return manifest_path


def _activate_for_session(module_dir):
    '''Activate scripts and icons for the current Maya session.'''
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
    '''Delete a prior launcher button so re-installing does not stack them.'''
    children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
    for child in children:
        if cmds.control(child, exists=True) and cmds.shelfButton(
                child, query=True, exists=True):
            if cmds.shelfButton(child, query=True, label=True) == label:
                cmds.deleteUI(child)


def _icon_path(icons_dir, name):
    '''Return a shipped icon path, or its Maya-resolved filename.'''
    path = os.path.join(icons_dir, name)
    return path if os.path.isfile(path) else name


def _add_shelf_buttons(icons_dir, tool_dir, selection):
    '''Refresh the selected launchers and return (shelf, labels).

    Every Rig Tail button is removed first, so a component left unticked
    loses the button an earlier install made for it.

    Reload's command is assembled per install rather than held as a
    constant, since a selective install can leave the Chain or Setup
    modules off disk and importing them would raise on click.

    '''
    shelf = _current_shelf()
    commands = {
        CHAIN: LAUNCH_CHAIN_COMMAND,
        SETUP: LAUNCH_SETUP_COMMAND,
        BUILD: LAUNCH_BUILD_COMMAND,
        RELOAD: _reload_command(selection),
    }
    annotations = {
        CHAIN: 'Chain Builder UI (create and re-space joint chains)',
        SETUP: 'Tail Setup UI (orient and mirror the skeleton)',
        BUILD: 'Tail Builder UI (build the rig)',
        RELOAD: 'Reload every module and bind them in the Script Editor',
    }

    labels = []
    for key in COMPONENTS:
        label = TOOL_NAMES[key]
        _remove_existing_button(shelf, label)
        if not selection.get(key):
            continue
        icon = _icon_path(icons_dir, COMPONENT_ICONS[key])
        cmds.shelfButton(
            parent=shelf,
            label=label,
            annotation='{0}\n{1}'.format(annotations[key], tool_dir),
            image=icon,
            image1=icon,
            imageOverlayLabel=OVERLAY_LABELS[key],
            overlayLabelColor=OVERLAY_TEXT_COLOR,
            overlayLabelBackColor=OVERLAY_BACK_COLOR,
            sourceType='python',
            command=_shelf_command(commands[key], tool_dir),
        )
        labels.append(label)
    return shelf, labels


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    src_dir = _source_dir()
    modules_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')

    dest_parent, selection = _choose_destination(src_dir, modules_dir)
    if dest_parent is None:
        print('# Rig Tail: install cancelled')
        return

    try:
        module_dir, copied = _install_module(src_dir, dest_parent, selection)
        tool_dir = os.path.join(module_dir, 'scripts')
        mod_path = _write_mod(modules_dir, module_dir)
        _write_manifest(modules_dir, module_dir, tool_dir, copied, selection)
        _activate_for_session(module_dir)
        shelf, labels = _add_shelf_buttons(
            os.path.join(module_dir, 'icons'), tool_dir, selection)
    except Exception as exc:
        cmds.confirmDialog(
            title='Rig Tail install failed',
            message=str(exc),
            button=['OK'],
            icon='critical')
        raise

    cmds.inViewMessage(
        amg='<hl>Rig Tail installed</hl> - see the {0} buttons on the "{1}" '
            'shelf'.format(', '.join('"{0}"'.format(label) for label in labels),
                            shelf),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail: {0} {1}'.format(
        'installed module to' if copied else 'running in place from',
        module_dir))
    print('# Rig Tail: TOOL_DIR -> {0}'.format(tool_dir))
    print('# Rig Tail: module registered by {0}'.format(mod_path))
    print('# Rig Tail: shelf buttons -> {0} on "{1}"'.format(
        ', '.join(labels), shelf))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
