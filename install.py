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
    Drag install.py from a file browser into the Maya viewport. It asks
    where to install, then adds three shelf buttons -- "TailSetup"
    (skeleton orient / mirror, black icon), "TailRig" (the builder, white
    icon) and "TailManual" (developer import/build/test workflow, grey
    icon). Works immediately -- no restart. Keep install.py next to the
    rigTail/ folder and rigTail.mod when you drag it.

    Three answers to "where?":

    "Default (maya/modules)" (recommended for users)
        Copies rigTail/ into ~/Documents/maya/modules/. Self-contained:
        this folder can then be moved or deleted.

    "Current (this folder)" (for a git clone)
        Copies nothing -- runs from where it already is, so a git pull is
        live on the next button click. Move the folder and it breaks.

    "Other..." (pick a folder)
        Copies rigTail/ into a folder chosen in a file browser, for a
        shared network location or a per-project tools folder.

    Whichever is chosen, one resolved path drives everything:

        rigTail.mod   always written to ~/Documents/maya/modules/ (the
                      only place Maya scans), holding a relative path
                      when the tree sits alongside it and an absolute
                      path otherwise -- so a clone or a picked folder is
                      registered on every Maya start, and "import
                      rig_tail" works in a bare Script Editor.
        TOOL_DIR      baked into all three shelf buttons, which put it at
                      the front of sys.path. A button therefore always
                      runs the install it was made from, even with
                      another copy of Rig Tail registered as a module.
        manifest      rigTail.install.json beside the .mod, recording
                      what went where so uninstall.py knows exactly what
                      to remove -- and what it merely pointed at and must
                      leave alone.

INSTALL (manual, no drag-and-drop)
    Copy rigTail/ and rigTail.mod into ~/Documents/maya/modules/
    then restart Maya. Launch from the Script Editor with:
        import rig_tail; rig_tail.main()        # builder
        import rig_tail; rig_tail.main_setup()  # setup (orient / mirror)
    (or make your own shelf buttons running those lines).

UNINSTALL
    Drag uninstall.py in, or delete rigTail.mod and the rigTail folder
    from ~/Documents/maya/modules/ and remove the shelf buttons.
--------------------------------------------------------------------------

Module layout:
    <userAppDir>/modules/
        rigTail.mod            -> points at the chosen location
        rigTail.install.json   -> what the last install did
    <chosen location>/
        rigTail/
            scripts/   rig_tail*.py + logger_config.py
            icons/     octopus{,_black,_grey}.png (+ _200 variants)

Compatible with Maya 2020+ (Python 3). UI tested in Maya 2024/2025.
'''

import json
import os
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

# Name of the prebuilt module folder and .mod shipped alongside this file.
MODULE_NAME = 'rigTail'
MOD_FILE = MODULE_NAME + '.mod'
MODULE_VERSION = '1.0'

# Record of what the last install did, written next to the .mod so
# uninstall.py can find the install wherever it went. JSON keys:
# mode, module_dir, tool_dir, copied.
MANIFEST_FILE = MODULE_NAME + '.install.json'

# The .mod always lands in <userAppDir>/modules/ (the only place Maya
# scans by default), but the path inside it points at wherever the module
# tree actually lives -- 'rigTail' when it sits alongside, or an absolute
# path when it lives in a git clone or a folder the user picked. That is
# what makes 'import rig_tail' work in a fresh Script Editor after
# restart, not just from the shelf buttons.
MOD_TEMPLATE = '''+ {name} {version} {path}
scripts: scripts
icons: icons
'''

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

# Every shelf button opens with this, with the chosen scripts folder
# baked in. Pinning TOOL_DIR to the front of sys.path (rather than
# relying on the .mod alone) means a button always runs the install it
# was made from: the "run from this folder" mode has no .mod at all, and
# even in module mode it keeps another Rig Tail copy earlier on sys.path
# from shadowing this one.
TOOL_DIR_PREAMBLE = '''import sys
import importlib

# Rig Tail install this button was made from.
TOOL_DIR = r'{0}'

# Put this version's directory at the FRONT of sys.path so that internal
# imports resolve to the files in THIS directory.
if TOOL_DIR in sys.path:
    sys.path.remove(TOOL_DIR)
sys.path.insert(0, TOOL_DIR)'''

# Placeholder the three commands below share; _shelf_command() swaps in
# the preamble above.
PREAMBLE_TOKEN = '#@TOOL_DIR@'

LAUNCH_COMMAND = '''# Launch Rig Tail Builder
#@TOOL_DIR@

import rig_tail
importlib.reload(rig_tail)
rig_tail.main()
'''

# Setup-phase launcher: orient / mirror the skeleton before building.
LAUNCH_SETUP_COMMAND = '''# Launch Rig Tail Setup
#@TOOL_DIR@

import rig_tail
importlib.reload(rig_tail)
rig_tail.main_setup()
'''

# Manual Run: a developer console workflow rather than a UI launcher. It
# force-reimports every module for a clean slate and keeps handles bound
# for interactive testing in the Script Editor. Commented lines lay out
# the full workflow -- setup, build and test -- so the user can uncomment
# the call they want.
LAUNCH_MANUAL_COMMAND = '''# Rig Tail -- Manual Run (import / build / setup / test)
#@TOOL_DIR@

# Purge every loaded Rig Tail module so the import below is genuinely
# fresh -- no exceptions. This is the only thing that reloads
# rig_tail_constants (rig_tail's own reload sweep deliberately skips it),
# so new constants and defaults are picked up without restarting Maya;
# session state there resets and its auto-load restores the saved config.
for mod_name in list(sys.modules):
    if mod_name.startswith('rig_tail') or mod_name == 'logger_config':
        del sys.modules[mod_name]

# Fresh import. rig_tail pulls in the rest via its own imports; the
# explicit list keeps handles around for console testing.
rt = importlib.import_module('rig_tail')
rt_anim = importlib.import_module('rig_tail_anim')
rt_cache = importlib.import_module('rig_tail_cache')
rt_cleanup = importlib.import_module('rig_tail_cleanup')
rt_connect = importlib.import_module('rig_tail_connect')
rt_constants = importlib.import_module('rig_tail_constants')
rt_control = importlib.import_module('rig_tail_control')
rt_curve = importlib.import_module('rig_tail_curve')
rt_fk = importlib.import_module('rig_tail_fk')
rt_joint = importlib.import_module('rig_tail_joint')
rt_mainctrl = importlib.import_module('rig_tail_mainctrl')
rt_math = importlib.import_module('rig_tail_math')
rt_matrix = importlib.import_module('rig_tail_matrix')
rt_maya = importlib.import_module('rig_tail_maya')
rt_naming = importlib.import_module('rig_tail_naming')
rt_restpose = importlib.import_module('rig_tail_restpose')
rt_setup = importlib.import_module('rig_tail_setup')
rt_setup_ui = importlib.import_module('rig_tail_setup_ui')
rt_stretch = importlib.import_module('rig_tail_stretch')
rt_test = importlib.import_module('rig_tail_test')
rt_test_setup = importlib.import_module('rig_tail_test_setup')
rt_ui = importlib.import_module('rig_tail_ui')

# --- SETUP phase (optional; run BEFORE the build, on the raw BN skeleton) ---
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
    '''Folder this installer lives in (holds rigTail/ and the .mod).'''
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        # __file__ is undefined when run from the Script Editor; fall back
        # to the current working directory.
        return os.path.abspath(os.getcwd())


def _shelf_command(template, tool_dir):
    '''Bake the chosen scripts folder into a shelf button's command.'''
    # Forward slashes read cleanly in the shelf editor and are what Maya
    # hands back from its own path queries; Windows accepts them fine.
    preamble = TOOL_DIR_PREAMBLE.format(tool_dir.replace('\\', '/'))
    return template.replace(PREAMBLE_TOKEN, preamble)


def _same_path(a, b):
    '''True if two paths point at the same place (case-insensitive on
    Windows, and blind to trailing slashes or ".." segments).'''
    return (os.path.normcase(os.path.normpath(os.path.abspath(a))) ==
            os.path.normcase(os.path.normpath(os.path.abspath(b))))


def _choose_destination(src_dir, modules_dir):
    '''Ask where to install. Returns the folder to hold rigTail/, or None
    if the user cancelled.

    Three answers, and the returned path is all that differs downstream:
    the Maya modules folder (the shipping install), this folder (a git
    clone, where a pull should be live on the next button click rather
    than needing a re-install), or somewhere the user picks.
    '''
    default_label = 'Default (maya/modules)'
    current_label = 'Current (this folder)'
    other_label = 'Other...'
    choice = cmds.confirmDialog(
        title='Install Rig Tail',
        message=(
            'Where should Rig Tail be installed?\n\n'
            '{0}\n    {1}\n'
            '    The usual install. Self-contained, so this folder can '
            'then be\n    moved or deleted.\n\n'
            '{2}\n    {3}\n'
            '    Nothing is copied -- runs from where it already is, so a '
            'git pull\n    takes effect on the next click. Moving this '
            'folder breaks it.\n\n'
            '{4}\n    Pick a folder; rigTail/ is copied into it.\n\n'
            'Either way a {5} pointing at the chosen location is written '
            'to\n{6}\nso the module is registered on every Maya '
            'start.'.format(
                default_label, os.path.join(modules_dir, MODULE_NAME),
                current_label, os.path.join(src_dir, MODULE_NAME),
                other_label, MOD_FILE, modules_dir)),
        button=[default_label, current_label, other_label, 'Cancel'],
        defaultButton=default_label,
        cancelButton='Cancel',
        dismissString='Cancel')

    if choice == default_label:
        return modules_dir
    if choice == current_label:
        return src_dir
    if choice == other_label:
        picked = cmds.fileDialog2(
            fileMode=3,                 # 3 = existing directory
            caption='Choose a folder to install Rig Tail into',
            okCaption='Install here',
            dialogStyle=2,
            startingDirectory=src_dir) or []
        # Cancelling the folder picker cancels the install rather than
        # silently falling back to a location the user did not choose.
        return picked[0] if picked else None
    return None


def _install_module(src_dir, dest_parent):
    '''Put the module tree under dest_parent and return
    (module_dir, copied).

    Copies rigTail/ into dest_parent, except when that is where it
    already lives -- the "current folder" answer, and also what happens
    if the user browses to this same folder under "Other...". Copying a
    tree onto itself would destroy it, so that case is detected and the
    existing folder used as-is.
    '''
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

    # Replace any prior install cleanly (copytree needs a fresh dest on the
    # Python 3.7 that ships with Maya 2020/2022).
    if os.path.isdir(dst_module):
        shutil.rmtree(dst_module)
    shutil.copytree(src_module, dst_module,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return dst_module, True


def _write_mod(modules_dir, module_dir):
    '''Write <modules_dir>/rigTail.mod pointing at module_dir.

    Relative when the tree sits right there (keeps the modules folder
    portable), absolute otherwise -- that absolute form is what registers
    a git clone or a user-picked folder with Maya at startup.
    '''
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
    '''Record where this install went, for uninstall.py to read back.

    Without it an uninstall can only guess whether the module tree is a
    copy it may delete or the user's own git clone it must leave alone.
    '''
    manifest_path = os.path.join(modules_dir, MANIFEST_FILE)
    with open(manifest_path, 'w') as handle:
        json.dump({
            'module_dir': os.path.abspath(module_dir),
            'tool_dir': os.path.abspath(tool_dir),
            'copied': bool(copied),
        }, handle, indent=4)
    return manifest_path


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


def _icon_path(icons_dir, name):
    '''Absolute path to a shipped icon, or the bare name as a fallback.

    An absolute path is baked into the shelf button so the correct icon
    always resolves, independent of the XBMLANGPATH icon-path cache (which
    can otherwise leave a new icon falling back to a default). Falls back
    to the bare name (resolved via the module icon path) if the file is
    missing.'''
    path = os.path.join(icons_dir, name)
    return path if os.path.isfile(path) else name


def _add_shelf_button(icons_dir, tool_dir):
    '''Add (or refresh) the TailSetup + TailRig + TailManual launchers.

    Three buttons, added left-to-right in the order they are used: Setup
    (orient/mirror the skeleton, black icon), Build (the builder, white
    icon), and Manual Run (developer console workflow, grey icon). Icons
    are passed as absolute paths so each button shows its own colour, and
    all three commands load from tool_dir (the chosen install's scripts
    folder) as TOOL_DIR.
    '''
    shelf = _current_shelf()
    _remove_existing_button(shelf, SHELF_SETUP_LABEL)
    _remove_existing_button(shelf, SHELF_BUTTON_LABEL)
    _remove_existing_button(shelf, SHELF_MANUAL_LABEL)

    icon_setup = _icon_path(icons_dir, SHELF_ICON_SETUP)
    icon_build = _icon_path(icons_dir, SHELF_ICON)
    icon_manual = _icon_path(icons_dir, SHELF_ICON_MANUAL)

    cmds.shelfButton(
        parent=shelf,
        label=SHELF_SETUP_LABEL,
        annotation='Launch the Rig Tail Setup UI (skeleton orient / mirror)',
        image=icon_setup,
        image1=icon_setup,
        sourceType='python',
        command=_shelf_command(LAUNCH_SETUP_COMMAND, tool_dir),
    )
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_BUTTON_LABEL,
        annotation='Launch the Rig Tail Builder UI',
        image=icon_build,
        image1=icon_build,
        sourceType='python',
        command=_shelf_command(LAUNCH_COMMAND, tool_dir),
    )
    cmds.shelfButton(
        parent=shelf,
        label=SHELF_MANUAL_LABEL,
        annotation='Rig Tail Manual Run: import / build / setup / test '
                   'the modules (developer console workflow)',
        image=icon_manual,
        image1=icon_manual,
        sourceType='python',
        command=_shelf_command(LAUNCH_MANUAL_COMMAND, tool_dir),
    )
    return shelf


def onMayaDroppedPythonFile(*args):
    '''Entry point Maya calls when this file is dropped into the viewport.'''
    src_dir = _source_dir()
    modules_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')

    dest_parent = _choose_destination(src_dir, modules_dir)
    if dest_parent is None:
        print('# Rig Tail: install cancelled')
        return

    try:
        module_dir, copied = _install_module(src_dir, dest_parent)
        # One resolved path drives everything from here: the three shelf
        # buttons, the .mod and the manifest all point at this install.
        tool_dir = os.path.join(module_dir, 'scripts')
        mod_path = _write_mod(modules_dir, module_dir)
        _write_manifest(modules_dir, module_dir, tool_dir, copied)
        _activate_for_session(module_dir)
        shelf = _add_shelf_button(os.path.join(module_dir, 'icons'), tool_dir)
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

    print('# Rig Tail: {0} {1}'.format(
        'installed module to' if copied else 'running in place from',
        module_dir))
    print('# Rig Tail: TOOL_DIR -> {0}'.format(tool_dir))
    print('# Rig Tail: module registered by {0}'.format(mod_path))
    print('# Rig Tail: shelf buttons -> {0}, {1}, {2} on "{3}"'.format(
        SHELF_SETUP_LABEL, SHELF_BUTTON_LABEL, SHELF_MANUAL_LABEL, shelf))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
