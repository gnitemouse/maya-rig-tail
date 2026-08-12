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

# Shelf icon colours: white=chain, light-grey=setup, dark-grey=build, black=reload.
SHELF_ICON_CHAIN = 'octopus_white.png'
SHELF_ICON_SETUP = 'octopus_light_grey.png'
SHELF_ICON_BUILD = 'octopus_dark_grey.png'
SHELF_ICON_RELOAD = 'octopus_black.png'

SHELF_ICON_LABEL_CHAIN_BUILD = 'Chain'
SHELF_ICON_LABEL_SETUP = 'Setup'
SHELF_ICON_LABEL_BUILD = 'Build'
SHELF_ICON_LABEL_RELOAD = 'Reload'

SHELF_BUILD_LABEL = 'TailBuild'
SHELF_SETUP_LABEL = 'TailSetup'
SHELF_RELOAD_LABEL = 'TailReload'
SHELF_CHAIN_BUILD_LABEL = 'ChainBuild'

# The four shelf buttons, in the order they appear in the install dialog and
# on the shelf. BUILD is the tool proper and is never optional.
CHAIN = 'chain'
SETUP = 'setup'
BUILD = 'build'
RELOAD = 'reload'
COMPONENTS = (CHAIN, SETUP, BUILD, RELOAD)

SHELF_LABELS = {
    CHAIN: SHELF_CHAIN_BUILD_LABEL,
    SETUP: SHELF_SETUP_LABEL,
    BUILD: SHELF_BUILD_LABEL,
    RELOAD: SHELF_RELOAD_LABEL,
}

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

# One icon per button. The _200 variants in icons/ are documentation assets
# and never install, so unlisted icons do not ship.
COMPONENT_ICONS = {
    CHAIN: (SHELF_ICON_CHAIN,),
    SETUP: (SHELF_ICON_SETUP,),
    BUILD: (SHELF_ICON_BUILD,),
    RELOAD: (SHELF_ICON_RELOAD,),
}

# Each launcher pins its selected scripts directory ahead of other installs.
TOOL_DIR_PREAMBLE = '''import sys
import importlib as il

# Rig Tail install this button was made from.
TOOL_DIR = r'{0}'
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)
'''

PREAMBLE_TOKEN = '#@TOOL_DIR@'
PURGE_TOKEN = '#@PURGE@'

# Every launcher purges the whole rig_tail package, not just its own tool's
# modules.
#
# Each tool sits on top of the shared core -- rig_tail_maya, _joint,
# _naming, _cleanup, _math -- and purging only 'rig_tail_chain' left that
# core loaded from whenever it was first imported. A core module that gained
# a function then looked, from the tool, like it had never had one:
# "module 'rig_tail_maya' has no attribute 'unique_path'", with nothing to
# suggest a stale import was the cause. Clicking the tool's own button is
# what an artist does after an update, so that is where the reload has to be
# complete.
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
        icons.update(COMPONENT_ICONS.get(key, ()))

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
    button left unticked this time, so re-installing over an older install
    with fewer buttons clears what was dropped instead of orphaning it.

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

        cmds.text(label='Which shelf buttons should be installed?', align='left')
        boxes = {}
        boxes[CHAIN] = cmds.checkBox(
            label='Add a "Joint Chain Builder" shelf button', value=True)
        cmds.text(
            label='Opens the Chain Build UI to create and re-space joint chains.',
            align='left')
        boxes[SETUP] = cmds.checkBox(
            label='Add a "Tail Setup" shelf button', value=True)
        cmds.text(
            label='Opens the Setup UI to orient and mirror the raw skeleton.',
            align='left')
        # Ticked and greyed out: the Builder is the tool itself, so the user
        # can see it is going in rather than wonder where its option went.
        boxes[BUILD] = cmds.checkBox(
            label='Add a "Tail Builder" shelf button', value=True,
            enable=False)
        cmds.text(label='The main tool -- always installed.', align='left')
        boxes[RELOAD] = cmds.checkBox(
            label='Add a "Tail Reload" shelf button', value=True)
        cmds.text(
            label='Reloads every module and lays the workflow out in the '
                  'Script Editor.',
            align='left')
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
    '''Refresh the selected chain, setup, build, and reload launchers.

    Every Rig Tail button is removed first, so a component left unticked
    loses the button an earlier install made for it.

    '''
    shelf = _current_shelf()
    for label in SHELF_LABELS.values():
        _remove_existing_button(shelf, label)

    if selection.get(CHAIN):
        icon = _icon_path(icons_dir, SHELF_ICON_CHAIN)
        cmds.shelfButton(
            parent=shelf,
            label=SHELF_CHAIN_BUILD_LABEL,
            annotation='Open the Joint Chain Builder (Chain Build UI) to create and re-space joint chains.',
            image=icon,
            image1=icon,
            imageOverlayLabel=SHELF_ICON_LABEL_CHAIN_BUILD,
            sourceType='python',
            command=_shelf_command(LAUNCH_CHAIN_COMMAND, tool_dir),
        )

    if selection.get(SETUP):
        icon = _icon_path(icons_dir, SHELF_ICON_SETUP)
        cmds.shelfButton(
            parent=shelf,
            label=SHELF_SETUP_LABEL,
            annotation='Launch the Tail Setup UI (skeleton orient / mirror)',
            image=icon,
            image1=icon,
            imageOverlayLabel=SHELF_ICON_LABEL_SETUP,
            sourceType='python',
            command=_shelf_command(LAUNCH_SETUP_COMMAND, tool_dir),
        )

    icon = _icon_path(icons_dir, SHELF_ICON_BUILD)
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_BUILD_LABEL,
        annotation='Launch the Tail Builder UI',
        image=icon,
        image1=icon,
        imageOverlayLabel=SHELF_ICON_LABEL_BUILD,
        sourceType='python',
        command=_shelf_command(LAUNCH_BUILD_COMMAND, tool_dir),
    )

    if selection.get(RELOAD):
        icon = _icon_path(icons_dir, SHELF_ICON_RELOAD)
        cmds.shelfButton(
            parent=shelf,
            label=SHELF_RELOAD_LABEL,
            annotation='Rig Tail Reload: load/reload every module fresh, with '
                       'build / setup / test commands ready in the Script Editor',
            image=icon,
            image1=icon,
            imageOverlayLabel=SHELF_ICON_LABEL_RELOAD,
            sourceType='python',
            command=_shelf_command(_reload_command(selection), tool_dir),
        )
    return shelf


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
        shelf = _add_shelf_buttons(os.path.join(module_dir, 'icons'), tool_dir,
                                   selection)
    except Exception as exc:  # surface a readable error to the user
        cmds.confirmDialog(
            title='Rig Tail install failed',
            message=str(exc),
            button=['OK'],
            icon='critical')
        raise

    labels = [SHELF_LABELS[key] for key in COMPONENTS if selection.get(key)]
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
