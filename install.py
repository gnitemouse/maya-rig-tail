'''
# install.py -- drag-and-drop installer for Rig Tail
author: Daisy Jane @gnitemouse

Installs Rig Tail as a Maya module and adds three launcher buttons to the
active shelf. This is the non-invasive, standard way to ship a Maya tool:
the code lives in one folder, and one .mod file under
~/Documents/maya/modules/ points Maya at it. Maya adds the module's
scripts/ to sys.path and icons/ to the icon path at startup, so nothing
global (userSetup.py, Maya.env) is ever touched.

The folder itself need not be under modules/ -- it can stay in a git
clone or sit anywhere the user picks, with the .mod naming that location.
That is what makes a working copy installable without copying it.

--------------------------------------------------------------------------
INSTALL (drag-and-drop)
    Drag install.py from a file browser into the Maya viewport. It asks
    where to install, then adds three shelf buttons -- "TailSetup"
    (skeleton orient / mirror, black icon), "TailRig" (the builder, white
    icon) and "TailReload" (load/reload the modules and run commands,
    grey icon). Works immediately -- no restart. Keep install.py next to the
    rigTail/ folder when you drag it; the .mod is written from scratch,
    so the one in the repo is only needed for a manual install.

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

RE-INSTALL
    Installing again over an existing install overwrites it file by file
    rather than deleting it first. Clearing the folder fails the moment
    Windows has anything in it locked -- and fails after the old files
    are gone, leaving no working install. Overwriting means a stubborn
    file costs that file, and the installer names it. Leftovers from an
    older version are cleared afterwards.

    Code already imported into the running session is not affected by
    new files on disk: click "TailReload" (which re-imports everything)
    or restart Maya.

INSTALL (manual, no drag-and-drop)
    Copy rigTail/ and rigTail.mod into ~/Documents/maya/modules/
    then restart Maya. Launch from the Script Editor with:
        import rig_tail; rig_tail.main()        # builder
        import rig_tail; rig_tail.main_setup()  # setup (orient / mirror)
    (or make your own shelf buttons running those lines).

UNINSTALL
    Drag uninstall.py in: it reads the manifest to find the install
    wherever it went, and leaves a folder it only pointed at alone. By
    hand, delete rigTail.mod and rigTail.install.json from
    ~/Documents/maya/modules/, delete the rigTail folder if it was copied
    there, and remove the shelf buttons.
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
import stat
import sys
import time

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
#   light grey -> Reload (load/reload the modules, run commands)
SHELF_ICON = 'octopus.png'              # white  -- Builder
SHELF_ICON_SETUP = 'octopus_black.png'  # black  -- Setup
SHELF_ICON_RELOAD = 'octopus_grey.png'  # grey   -- Reload

SHELF_BUTTON_LABEL = 'TailRig'
# Second button for the Setup phase (skeleton orient / mirror).
SHELF_SETUP_LABEL = 'TailSetup'
# Third button: purge and re-import every module, with the build / setup /
# test commands laid out for the Script Editor.
SHELF_RELOAD_LABEL = 'TailReload'
# Labels earlier versions used for the third button; removed on
# re-install and uninstall so an upgrade does not leave a stale button.
SHELF_LEGACY_LABELS = ('TailManual',)

# Every shelf button opens with this, with the chosen scripts folder
# baked in. Pinning TOOL_DIR to the front of sys.path, rather than
# relying on the .mod alone, means a button always runs the install it
# was made from: only one .mod can be registered at a time, so a second
# install (a clone alongside a modules-folder copy, say) would otherwise
# have its buttons quietly load the other one's code.
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

# Reload: loads/reloads the modules and runs commands rather than opening
# a UI. It force-reimports every module for a clean slate and keeps
# handles bound for interactive use in the Script Editor. Commented lines
# lay out the full workflow -- setup, build and test -- so the user can
# uncomment the call they want.
LAUNCH_RELOAD_COMMAND = '''# Rig Tail -- Reload (load/reload the modules and run commands)
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
rt_ctrlall = importlib.import_module('rig_tail_ctrlall')
rt_curve = importlib.import_module('rig_tail_curve')
rt_fk = importlib.import_module('rig_tail_fk')
rt_joint = importlib.import_module('rig_tail_joint')
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


def _is_inside(path, parent):
    '''True if path sits under parent.'''
    path = os.path.normcase(os.path.abspath(path))
    parent = os.path.normcase(os.path.abspath(parent))
    return path.startswith(parent + os.sep)


def _release_cwd(path):
    '''Step out of path if this process is sitting in it.

    Windows refuses to delete or rename a directory that any process has
    as its current directory -- with WinError 32, "being used by another
    process", even though the process is Maya itself. Maya's file
    browsers move the CWD around, so saving a Rig Tail config out of the
    installed scripts/ folder is enough to wedge the next install.
    '''
    try:
        cwd = os.getcwd()
    except OSError:
        return None                     # CWD already deleted; nothing held
    if not (_same_path(cwd, path) or _is_inside(cwd, path)):
        return None
    home = os.path.expanduser('~')
    os.chdir(home)
    return home


def _replace_file(src, dst, attempts=3):
    '''Copy one file over another, retrying briefly.

    The retry is for the transient case -- an antivirus scanner or the
    search indexer holding a handle for a moment after the file is
    touched. The chmod is for the persistent one: files unpacked from a
    zip or synced from version control can arrive read-only.
    '''
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
    '''Copy src over dst file by file. Returns the files that would not
    overwrite.

    Deliberately not "rmtree the destination, then copytree": clearing
    the old install first fails the moment anything holds a handle inside
    it, and it fails *after* the files are gone, leaving no working
    install behind. Overwriting in place means one stubborn file costs
    that file rather than the whole tool. (copytree's dirs_exist_ok would
    do this, but it needs Python 3.8 and Maya 2020/2022 ship 3.7.)
    '''
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
    '''Best-effort removal of files left over from an older install.

    A renamed module would otherwise stay importable, and stale
    __pycache__ can shadow the source it was built from. Failures are
    ignored: a leftover file is untidy, not broken, and is not worth
    aborting an otherwise good install for.
    '''
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
            '    The usual install. Copies the code, so the folder you '
            'dragged\n    install.py from can then be moved or deleted.'
            '\n\n'
            '{2}\n    {3}\n'
            '    Nothing is copied -- runs from where it already is, so a '
            'git pull\n    takes effect on the next click. Moving that '
            'folder breaks it.\n\n'
            '{4}\n    Pick a folder; rigTail/ is copied into it.\n\n'
            'Whichever you pick, a {5} pointing at that location is '
            'written to\n{6}\nso the module is registered on every Maya '
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
    '''Add (or refresh) the TailSetup + TailRig + TailReload launchers.

    Three buttons, added left-to-right in the order they are used: Setup
    (orient/mirror the skeleton, black icon), Build (the builder, white
    icon), and Reload (load/reload the modules and run commands, grey
    icon). Icons are passed as absolute paths so each button shows its
    own colour, and all three commands load from tool_dir (the chosen
    install's scripts folder) as TOOL_DIR.
    '''
    shelf = _current_shelf()
    for label in ((SHELF_SETUP_LABEL, SHELF_BUTTON_LABEL,
                   SHELF_RELOAD_LABEL) + SHELF_LEGACY_LABELS):
        _remove_existing_button(shelf, label)

    icon_setup = _icon_path(icons_dir, SHELF_ICON_SETUP)
    icon_build = _icon_path(icons_dir, SHELF_ICON)
    icon_reload = _icon_path(icons_dir, SHELF_ICON_RELOAD)

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
        label=SHELF_RELOAD_LABEL,
        annotation='Rig Tail Reload: load/reload every module fresh, with '
                   'build / setup / test commands ready in the Script Editor',
        image=icon_reload,
        image1=icon_reload,
        sourceType='python',
        command=_shelf_command(LAUNCH_RELOAD_COMMAND, tool_dir),
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
                SHELF_SETUP_LABEL, SHELF_BUTTON_LABEL, SHELF_RELOAD_LABEL,
                shelf),
        pos='midCenter', fade=True, fadeStayTime=3000)

    print('# Rig Tail: {0} {1}'.format(
        'installed module to' if copied else 'running in place from',
        module_dir))
    print('# Rig Tail: TOOL_DIR -> {0}'.format(tool_dir))
    print('# Rig Tail: module registered by {0}'.format(mod_path))
    print('# Rig Tail: shelf buttons -> {0}, {1}, {2} on "{3}"'.format(
        SHELF_SETUP_LABEL, SHELF_BUTTON_LABEL, SHELF_RELOAD_LABEL, shelf))


# Allow running from the Script Editor as well as drag-and-drop.
if __name__ == '__main__':
    onMayaDroppedPythonFile()
