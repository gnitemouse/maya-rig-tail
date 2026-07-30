'''
# install.py
author: Daisy Jane @gnitemouse

Install Rig Tail as a Maya module and add shelf launchers.

Drag this file into Maya, choose an install location, and optionally add the
Joint Chain Builder launcher. The module registration and install manifest are
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

# Each launcher pins its selected scripts directory ahead of other installs.
TOOL_DIR_PREAMBLE = '''import sys
import importlib as il

# Rig Tail install this button was made from.
TOOL_DIR = r'{0}'
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)
'''

PREAMBLE_TOKEN = '#@TOOL_DIR@'

LAUNCH_BUILD_COMMAND = '''# Launch Rig Tail Builder
#@TOOL_DIR@

for mod in list(sys.modules):
    if mod.startswith('rig_tail_build'):
        del sys.modules[mod]

import rig_tail_build_ui as rt_build_ui
import rig_tail_build_test as rt_build_test
rt_build_ui.show_ui()
'''

LAUNCH_CHAIN_COMMAND = '''# Launch Joint Chain Builder
#@TOOL_DIR@

for mod in list(sys.modules):
    if mod.startswith('rig_tail_chain'):
        del sys.modules[mod]

import rig_tail_chain_build_ui as rt_chain_ui
import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing
import rig_tail_chain_test as rt_chain_test
rt_chain_ui.show_ui()
'''

LAUNCH_SETUP_COMMAND = '''# Launch Rig Tail Setup
#@TOOL_DIR@

for mod in list(sys.modules):
    if mod.startswith('rig_tail_setup'):
        del sys.modules[mod]

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
LAUNCH_RELOAD_COMMAND = '''# Rig Tail -- Reload (load/reload the modules and run commands)
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
import rig_tail_naming as rt_naming
import rig_tail_restpose as rt_rest
import rig_tail_stretch as rt_stretch
import rig_tail_setup as rt_setup

# --- SETUP phase (optional; run BEFORE the build, on raw BN skeleton) ---
#rt.setup_tails('squid', dry_run=True)   # preview only (orient/mirror), no changes
#rt.setup_tails('squid')                 # apply orient/mirror, then build
#rt.main_setup()                         # or open the Setup UI

# --- BUILD ---
#rt.rig_tail_single('tail', fk=True, ik=True)
#rt.rig_tail_multiple('squid', fk=True, ik=True)
#rt.main()                               # or open the Builder UI

# --- TEST / INSPECT (after a build) ---
#rt_test.run_all('squid')                          # full test sweep
#rt_test.report_bend(rt_constants.RIGPARTS)        # per-chain bend angles
#rt_test.probe('after build', 'C_fintail')         # quick joint probe
#rt_test.measure_rebuild_degradation(rt_constants.RIGPARTS, rebuilds=2)
'''


def _source_dir():
    '''Return this installer folder.'''
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.abspath(os.getcwd())


def _shelf_command(template, tool_dir):
    '''Embed tool_dir in a shelf command.'''
    preamble = TOOL_DIR_PREAMBLE.format(tool_dir.replace('\\', '/'))
    return template.replace(PREAMBLE_TOKEN, preamble)


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


def _copy_tree(src, dst):
    '''Copy src over dst and return files that could not be updated.'''
    blocked = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == os.curdir else os.path.join(dst, rel)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        for name in filenames:
            if name.endswith('.pyc'):
                continue
            try:
                _replace_file(os.path.join(dirpath, name),
                              os.path.join(target_dir, name))
            except (IOError, OSError) as exc:
                blocked.append((os.path.join(target_dir, name), exc))
    return blocked


def _prune_stale(src, dst):
    '''Remove stale destination files after an update.'''
    for dirpath, dirnames, filenames in os.walk(dst):
        if os.path.basename(dirpath) == '__pycache__':
            shutil.rmtree(dirpath, ignore_errors=True)
            dirnames[:] = []
            continue
        rel = os.path.relpath(dirpath, dst)
        source_dir = src if rel == os.curdir else os.path.join(src, rel)
        for name in filenames:
            if not os.path.isfile(os.path.join(source_dir, name)):
                try:
                    os.remove(os.path.join(dirpath, name))
                except (IOError, OSError):
                    pass


def _choose_destination(src_dir, modules_dir):
    '''Ask where to install and whether to add Joint Chain Builder.

    Returns (folder to hold rigTail/, install_chain_build), or
    (None, False) if the user cancelled.

    '''
    default_label = 'Default (maya/modules)'
    current_label = 'Current (this folder)'
    other_label = 'Other...'
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
        chain_checkbox = cmds.checkBox(
            label='Add a "Joint Chain Builder" shelf button', value=True)
        cmds.text(
            label='Opens the Chain Build UI to create and re-space joint chains.',
            align='left')
        cmds.separator(style='in', height=10)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                       columnAttach=(1, 'both', 0))

        def _dismiss(choice):
            val = cmds.checkBox(chain_checkbox, query=True, value=True)
            install_chain_build = 1 if val else 0
            cmds.layoutDialog(
                dismiss='{0}|{1}'.format(choice, install_chain_build))

        cmds.button(label='Install', command=lambda *_: _dismiss(
            default_label if cmds.radioButton(default_radio, query=True, select=True)
            else current_label if cmds.radioButton(current_radio, query=True, select=True)
            else other_label))
        cmds.button(label='Cancel', command=lambda *_: cmds.layoutDialog(
            dismiss='Cancel|0'))

    response = cmds.layoutDialog(ui=_dialog_ui, title='Install Rig Tail')
    choice, _, checked = response.partition('|')
    install_chain_build = checked == '1'

    if choice == default_label:
        return modules_dir, install_chain_build
    if choice == current_label:
        return src_dir, install_chain_build
    if choice == other_label:
        picked = cmds.fileDialog2(
            fileMode=3,                 # 3 = existing directory
            caption='Choose a folder to install Rig Tail into',
            okCaption='Install here',
            dialogStyle=2,
            startingDirectory=src_dir) or []
        # Cancelling the folder picker cancels the install rather than
        # silently falling back to a location the user did not choose.
        return (picked[0], install_chain_build) if picked else (None, False)
    return None, False


def _install_module(src_dir, dest_parent):
    '''Install the module tree and return (module_dir, copied).'''
    src_module = os.path.join(src_dir, MODULE_NAME)
    if not os.path.isdir(os.path.join(src_module, 'scripts')):
        raise RuntimeError(
            'Could not find {0}/scripts next to install.py in\n{1}\n'
            'Keep install.py at the top of the repo when dragging it '
            'in.'.format(MODULE_NAME, src_dir))

    dst_module = os.path.join(dest_parent, MODULE_NAME)
    if _same_path(src_module, dst_module):
        return src_module, False

    if not os.path.isdir(dest_parent):
        os.makedirs(dest_parent)

    _release_cwd(dst_module)
    blocked = _copy_tree(src_module, dst_module)
    if blocked:
        raise RuntimeError(
            'Could not overwrite {0} file(s) in\n{1}\n\n{2}\n\n'
            'Something still has them open. Close any editor or Explorer '
            'window on that folder and try again; if it persists, restart '
            'Maya and re-run the installer before using the tool.'.format(
                len(blocked), dst_module,
                '\n'.join('{0}\n    {1}'.format(path, exc)
                          for path, exc in blocked[:5])))
    _prune_stale(src_module, dst_module)
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


def _write_manifest(modules_dir, module_dir, tool_dir, copied):
    '''Write the install record used by uninstall.py.'''
    manifest_path = os.path.join(modules_dir, MANIFEST_FILE)
    with open(manifest_path, 'w') as handle:
        json.dump({
            'module_dir': os.path.abspath(module_dir),
            'tool_dir': os.path.abspath(tool_dir),
            'copied': bool(copied),
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


def _add_shelf_buttons(icons_dir, tool_dir, install_chain_build):
    '''Refresh the optional chain, setup, build, and reload launchers.'''
    shelf = _current_shelf()
    for label in ((SHELF_CHAIN_BUILD_LABEL, SHELF_SETUP_LABEL, SHELF_BUILD_LABEL,
                   SHELF_RELOAD_LABEL)):
        _remove_existing_button(shelf, label)

    icon_chain_build = _icon_path(icons_dir, SHELF_ICON_CHAIN)
    icon_setup = _icon_path(icons_dir, SHELF_ICON_SETUP)
    icon_build = _icon_path(icons_dir, SHELF_ICON_BUILD)
    icon_reload = _icon_path(icons_dir, SHELF_ICON_RELOAD)

    if install_chain_build:
        cmds.shelfButton(
            parent=shelf,
            label=SHELF_CHAIN_BUILD_LABEL,
            annotation='Open the Joint Chain Builder (Chain Build UI) to create and re-space joint chains.',
            image=icon_chain_build,
            image1=icon_chain_build,
            imageOverlayLabel=SHELF_ICON_LABEL_CHAIN_BUILD,
            sourceType='python',
            command=_shelf_command(LAUNCH_CHAIN_COMMAND, tool_dir),
        )

    cmds.shelfButton(
        parent=shelf,
        label=SHELF_SETUP_LABEL,
        annotation='Launch the Rig Tail Setup UI (skeleton orient / mirror)',
        image=icon_setup,
        image1=icon_setup,
        imageOverlayLabel=SHELF_ICON_LABEL_SETUP,
        sourceType='python',
        command=_shelf_command(LAUNCH_SETUP_COMMAND, tool_dir),
    )
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_BUILD_LABEL,
        annotation='Launch the Rig Tail Builder UI',
        image=icon_build,
        image1=icon_build,
        imageOverlayLabel=SHELF_ICON_LABEL_BUILD,
        sourceType='python',
        command=_shelf_command(LAUNCH_BUILD_COMMAND, tool_dir),
    )
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_RELOAD_LABEL,
        annotation='Rig Tail Reload: load/reload every module fresh, with '
                   'build / setup / test commands ready in the Script Editor',
        image=icon_reload,
        image1=icon_reload,
        imageOverlayLabel=SHELF_ICON_LABEL_RELOAD,
        sourceType='python',
        command=_shelf_command(LAUNCH_RELOAD_COMMAND, tool_dir),
    )
    return shelf


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    src_dir = _source_dir()
    modules_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')

    dest_parent, install_chain_build = _choose_destination(src_dir, modules_dir)
    if dest_parent is None:
        print('# Rig Tail: install cancelled')
        return

    try:
        module_dir, copied = _install_module(src_dir, dest_parent)
        tool_dir = os.path.join(module_dir, 'scripts')
        mod_path = _write_mod(modules_dir, module_dir)
        _write_manifest(modules_dir, module_dir, tool_dir, copied)
        _activate_for_session(module_dir)
        shelf = _add_shelf_buttons(os.path.join(module_dir, 'icons'), tool_dir,
                                   install_chain_build)
    except Exception as exc:  # surface a readable error to the user
        cmds.confirmDialog(
            title='Rig Tail install failed',
            message=str(exc),
            button=['OK'],
            icon='critical')
        raise

    labels = [SHELF_SETUP_LABEL, SHELF_BUILD_LABEL, SHELF_RELOAD_LABEL]
    if install_chain_build:
        labels.insert(0, SHELF_CHAIN_BUILD_LABEL)
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
