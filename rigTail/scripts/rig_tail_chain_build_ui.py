'''
rig_tail_chain_build_ui.py
author: Daisy Jane @gnitemouse

PySide2 UI for Joint Chain Builder.
Create and re-space BN joint chains before the Setup phase.

Own copies of create_group_box / style_button — never modifies shared
widgets in rig_tail_ui.py (see §2.2 of the Joint Chain Builder plan).

Deliberately stateless: every option is a widget read at click time, with
no config file and no preferences. The tool has six controls and one undo
step per click, so there is nothing worth persisting.

Classes and functions:
    JointChainBuilderUI: the Joint Chain Builder window
    show_ui: build and show the window, closing any previous instance
    get_maya_window: the Maya main window as a QWidget for parenting
'''

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from shiboken2 import wrapInstance
from PySide2 import QtWidgets, QtCore

import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_joint as rt_joint
import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing


class JointChainBuilderUI(QtWidgets.QDialog):
    '''Joint Chain Builder window: create and re-space joint chains.'''

    FIELD_W = 150       # dropdown / spin box width
    FIELD_H = 28        # height of every input widget
    LABEL_W = 150       # shared field start
    ROW_GAP = 6         # label -> field gap, identical on every row
    # Breathing room between a field and the grey hint beside it. Applied as
    # the hint's own left margin rather than the row's spacing, because
    # changing a row's spacing would also move that row's field and break
    # the shared left edge.
    HINT_STYLE = 'color: #999999; font-size: 10px; margin-left: 2px;'

    # Left padding is 8px everywhere EXCEPT the combo box, which gets 10.
    # QLineEdit and the spin boxes (which contain a QLineEdit) add Qt's own
    # 2px internal horizontal margin on top of the stylesheet padding; a
    # QComboBox paints its text straight into the padded rect and has no
    # such margin. Matching numbers therefore look mismatched: the combo
    # text sits 2px left of everything else. The extra 2px here is what
    # makes Joint Chain(s), Joint Count, Spacing and Spacing Value all
    # start their text on one line.
    FIELD_STYLE = '''
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            background-color: #3a3a3a; color: #cccccc;
            border: 1px solid #555555; border-radius: 4px; padding: 2px 8px;
        }
        QComboBox { padding-left: 10px; }
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
            border-color: #F5D041;
        }
        QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QLineEdit:disabled { color: #777777; border-color: #444444; }
    '''

    MODES = ['Keep', 'Uniform', 'Power', 'Ratio']

    # Spacing Value is only meaningful for the two parametric modes. Each
    # keeps its own value and range so switching Power <-> Ratio does not
    # carry a nonsensical number across (1.7 is a fine exponent and an
    # impossible ratio).
    PARAM_MODES = {
        'power': (rt_chain_spacing.K_DEFAULT, rt_chain_spacing.K_RANGE, 0.05),
        'ratio': (rt_chain_spacing.R_DEFAULT, rt_chain_spacing.R_RANGE, 0.01),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Joint Chain Builder')
        self.setMinimumWidth(520)
        # Last value the artist typed for each parametric mode, so a trip
        # through Uniform and back does not lose it.
        self._param_values = {mode: default
                              for mode, (default, _, _) in self.PARAM_MODES.items()}
        self._param_mode = None
        # Rig name -> chain root, filled by Select. The box shows rig names
        # because that is what reads well in a list; the tool works on roots.
        self._roots = {}
        self.setup_ui()
        self._sync_source_mode()    # also syncs the mode-dependent fields
        self.resize(520, self.sizeHint().height())

    # LAYOUT ============================================================

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 10, 15, 10)

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(2)
        title = QtWidgets.QLabel('JOINT CHAIN BUILDER')
        title.setStyleSheet('font-size: 18px; font-weight: bold; color: #FFFFFF;')
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(title)

        subtitle = QtWidgets.QLabel('Create and re-space joint chains before Setup')
        subtitle.setStyleSheet('font-size: 10px; color: #999999;')
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(subtitle)
        main_layout.addLayout(title_layout)

        author = QtWidgets.QLabel('author Daisy Jane @gnitemouse')
        author.setStyleSheet('font-size: 10px; font-weight: normal; color: #4A90E2;')
        author.setAlignment(QtCore.Qt.AlignRight)
        main_layout.addWidget(author)

        # Source section -------------------------------------------------
        src_group = self.create_group_box('Source')
        src_layout = QtWidgets.QVBoxLayout()
        src_layout.setSpacing(3)

        # A textbox, not a dropdown: several chains can be re-spaced in one
        # click by listing them, which is what Select fills in from a
        # multi-chain selection. Same convention as Setup's Roll Chain box.
        chain_row = QtWidgets.QHBoxLayout()
        chain_row.setSpacing(self.ROW_GAP)
        chain_label = QtWidgets.QLabel('Joint Chain(s):')
        chain_label.setMinimumWidth(self.LABEL_W)
        self.txt_chain = QtWidgets.QLineEdit()
        self.txt_chain.setPlaceholderText('rig part(s), comma separated')
        self.txt_chain.setToolTip(
            'The chain(s) to re-space, comma separated - one click rebuilds '
            'every one of them. Select joints in the viewport and click '
            'Select to fill this in: any joint of a chain names that chain, '
            'so each chain is listed once. Typing works too - a rig part '
            'name or any joint name of the chain.')
        self.txt_chain.setStyleSheet(self.FIELD_STYLE)
        self.txt_chain.setFixedHeight(self.FIELD_H)
        self.txt_chain.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.txt_chain.textChanged.connect(self._sync_detected)
        self.btn_select = QtWidgets.QPushButton('Select')
        self.btn_select.setToolTip(
            'Fill the box from the current viewport selection: reads the '
            'chain of every selected joint, listing each chain once however '
            'many of its joints are selected.')
        self.style_button(self.btn_select, 0)
        self.btn_select.setFixedSize(70, self.FIELD_H)
        self.btn_select.clicked.connect(self.select_from_viewport)
        chain_row.addWidget(chain_label)
        chain_row.addWidget(self.txt_chain, 1)
        chain_row.addWidget(self.btn_select)

        self.rad_rebuild = QtWidgets.QRadioButton('Rebuild selected chain')
        self.rad_new = QtWidgets.QRadioButton('New chain between two selected objects')
        self.rad_rebuild.setChecked(True)
        self.rad_rebuild.setToolTip(
            'Re-space the chains listed above, keeping their names where the '
            'count allows.')
        # 'objects', not 'joints': the two ends are usually locators placed
        # before any skeleton exists. Only their positions are read, so a
        # joint, a control or a group works just as well.
        self.rad_new.setToolTip(
            'Create a chain between two selected objects - locators, joints, '
            'anything with a position. The two mark the ends, are left '
            'untouched, and the new joints are named after the first of '
            'them; rename them in Maya afterwards if you want something '
            'else.')
        self.rad_rebuild.toggled.connect(self._sync_source_mode)
        for rad in (self.rad_rebuild, self.rad_new):
            rad.setStyleSheet('color: #cccccc; spacing: 4px;')

        src_layout.addLayout(chain_row)
        src_layout.addSpacing(6)
        src_layout.addWidget(self.rad_rebuild)
        src_layout.addWidget(self.rad_new)
        src_group.setLayout(src_layout)
        main_layout.addWidget(src_group)

        # Spacing section ------------------------------------------------
        spc_group = self.create_group_box('Spacing')
        spc_layout = QtWidgets.QVBoxLayout()
        spc_layout.setSpacing(2)

        # Joint Count
        count_row = QtWidgets.QHBoxLayout()
        self.lbl_count = QtWidgets.QLabel('Joint Count:')
        self.lbl_count.setMinimumWidth(self.LABEL_W)
        self.spn_count = QtWidgets.QSpinBox()
        self.spn_count.setRange(2, 200)
        self.spn_count.setValue(21)
        self.spn_count.setToolTip(
            'How many BN joints the chain ends up with. Set to the detected '
            'count by Select, so the first click re-spaces without changing '
            'the count.')
        self.spn_count.setStyleSheet(self.FIELD_STYLE)
        self.spn_count.setFixedSize(self.FIELD_W, self.FIELD_H)
        self.spn_count.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.lbl_detected = QtWidgets.QLabel('(detected: —)')
        self.lbl_detected.setStyleSheet(self.HINT_STYLE)
        count_row.setSpacing(self.ROW_GAP)
        count_row.addWidget(self.lbl_count)
        count_row.addWidget(self.spn_count)
        # Both hints land at the same x without anything being measured: each
        # row is a LABEL_W label, the same gap, a FIELD_W field, then the
        # hint's own margin.
        count_row.addWidget(self.lbl_detected)
        count_row.addStretch()
        spc_layout.addLayout(count_row)

        # Spacing
        spacing_row = QtWidgets.QHBoxLayout()
        spacing_row.setSpacing(self.ROW_GAP)
        spacing_label = QtWidgets.QLabel('Spacing:')
        spacing_label.setMinimumWidth(self.LABEL_W)
        self.cmb_mode = QtWidgets.QComboBox()
        self.cmb_mode.addItems(self.MODES)
        self.cmb_mode.setToolTip(
            'How joints are distributed along the chain:\n'
            '  Keep     - hold the current spacing pattern at the new count\n'
            '  Uniform  - even segments\n'
            '  Power    - u = t^k, packs joints toward the BASE as k rises\n'
            '  Ratio    - each segment r times the last, packs toward the TIP\n'
            'Keep and Uniform take no Spacing Value.')
        self.cmb_mode.setStyleSheet(self.FIELD_STYLE)
        self.cmb_mode.setFixedSize(self.FIELD_W, self.FIELD_H)
        self.cmb_mode.currentIndexChanged.connect(self._sync_mode)
        spacing_row.addWidget(spacing_label)
        spacing_row.addWidget(self.cmb_mode)
        spacing_row.addStretch()
        spc_layout.addLayout(spacing_row)

        # Spacing Value
        param_row = QtWidgets.QHBoxLayout()
        self.lbl_param = QtWidgets.QLabel('Spacing Value:')
        self.lbl_param.setMinimumWidth(self.LABEL_W)
        self.spn_param = QtWidgets.QDoubleSpinBox()
        self.spn_param.setDecimals(2)
        self.spn_param.setStyleSheet(self.FIELD_STYLE)
        self.spn_param.setFixedSize(self.FIELD_W, self.FIELD_H)
        self.spn_param.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.spn_param.valueChanged.connect(self._remember_param)
        self.lbl_param_hint = QtWidgets.QLabel('')
        self.lbl_param_hint.setStyleSheet(self.HINT_STYLE)
        param_row.setSpacing(self.ROW_GAP)
        param_row.addWidget(self.lbl_param)
        param_row.addWidget(self.spn_param)
        param_row.addWidget(self.lbl_param_hint)
        param_row.addStretch()
        spc_layout.addLayout(param_row)
        spc_layout.addSpacing(8)

        # Invert
        self.chk_invert = QtWidgets.QCheckBox('Invert spacing')
        self.chk_invert.setToolTip(
            'Mirror the spacing profile end for end, so whichever end the '
            'joints bunch toward swaps base <-> tip. Applies to all four '
            'modes, Keep included.')
        self.style_checkbox(self.chk_invert)
        spc_layout.addWidget(self.chk_invert)

        # Snap
        self.chk_snap = QtWidgets.QCheckBox('Keep exact original positions')
        self.chk_snap.setChecked(True)
        self.chk_snap.setToolTip(
            'Where a target joint lands on an existing one, reuse that '
            'joint\'s exact position instead of a curve evaluation. Makes '
            'clean count changes lossless - 21 joints down to 11 lands on '
            'every other original exactly. Never moves a joint anywhere it '
            'was not already going.')
        self.style_checkbox(self.chk_snap)
        spc_layout.addWidget(self.chk_snap)

        # Orient joints
        self.chk_orient = QtWidgets.QCheckBox('Orient joints')
        self.chk_orient.setToolTip(
            'Aim-orient the joints down the chain after placing them, so they '
            'point at their new neighbours instead of their old ones. Off by '
            'default: Tail Rig Setup owns orientation, and this saves a trip '
            'there for a chain that needs nothing else.\n\n'
            'A rebuild keeps the roll the chain already has (Setup\'s '
            'cascade), so a mirrored pair stays mirrored and a Roll Chain '
            'fix-up survives. A new chain takes its roll from its own bend '
            'plane.')
        self.style_checkbox(self.chk_orient)
        spc_layout.addWidget(self.chk_orient)

        spc_group.setLayout(spc_layout)
        main_layout.addWidget(spc_group)
        main_layout.setSpacing(20)

        # Buttons --------------------------------------------------------
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        self.btn_reset = QtWidgets.QPushButton('Bake Joint Chain')
        self.btn_reset.setToolTip('Bake joints to preserve the current shape,'
            'keeping this joint chain for the next Build.\n'
            'Build preserves this shape and the earlier one is forgotten.')
        self.btn_build = QtWidgets.QPushButton('Build Joints')
        self.btn_build.setToolTip(
            'Build or re-space the listed chain(s) with the settings above. '
            'One undo step.')

        self.btn_reset.clicked.connect(self.reset_original)
        self.btn_build.clicked.connect(self.run_build)

        self.style_button(self.btn_reset, 0)
        self.style_button(self.btn_build, 4)  # olive primary

        button_layout.addWidget(self.btn_reset)
        button_layout.addWidget(self.btn_build)

        main_layout.addLayout(button_layout)

    # STYLING ===========================================================

    def create_group_box(self, title):
        group = QtWidgets.QGroupBox(title)
        group.setStyleSheet('''
            QGroupBox {
                font-weight: bold; border: 2px solid #555555;
                border-radius: 6px; margin-top: 10px; padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px;
            }
        ''')
        return group

    def style_button(self, button, style):
        if style == 4:  # olive green (primary) — matches rig_tail_setup_ui
            button.setStyleSheet('''
                QPushButton {
                    background-color: #6B7A45; color: white; border: none;
                    border-radius: 4px; padding: 8px 16px; font-weight: bold;
                }
                QPushButton:hover { background-color: #7C8C52; }
                QPushButton:pressed { background-color: #525E33; }
            ''')
        else:  # grey — matches rig_tail_setup_ui
            button.setStyleSheet('''
                QPushButton {
                    background-color: #3a3a3a; color: #cccccc;
                    border: 1px solid #555555; border-radius: 4px; padding: 6px 12px;
                }
                QPushButton:hover { background-color: #4a4a4a; border-color: #666666; }
                QPushButton:pressed { background-color: #2a2a2a; }
            ''')
        button.setMinimumHeight(32)

    def style_checkbox(self, checkbox):
        checkbox.setStyleSheet('''
            QCheckBox { spacing: 6px; color: #cccccc; }
            QCheckBox::indicator { width: 18px; height: 18px; }
            QCheckBox:disabled { color: #777777; }
        ''')
        checkbox.setMinimumHeight(self.FIELD_H)

    # STATE =============================================================

    def _chain_names(self):
        '''The names listed in the Joint Chain(s) box, in order,
        de-duplicated.'''
        names = []
        for name in self.txt_chain.text().replace(';', ',').split(','):
            name = name.strip()
            if name and name not in names:
                names.append(name)
        return names

    def _sync_source_mode(self, *args):
        '''Enable the controls each source mode actually uses.

        *args soaks up the bool that QRadioButton.toggled sends.
        '''
        is_new = self.rad_new.isChecked()
        # Nothing to snap to and no distribution to keep on a chain that does
        # not exist yet. Orient applies to both.
        self.chk_snap.setEnabled(not is_new)
        self.txt_chain.setEnabled(not is_new)
        self.btn_select.setEnabled(not is_new)
        # Nothing is cached for a chain that does not exist yet.
        self.btn_reset.setEnabled(not is_new)
        if is_new and self.cmb_mode.currentText().lower() == 'keep':
            self.cmb_mode.setCurrentIndex(self.MODES.index('Uniform'))
        self._sync_mode()

    def _sync_mode(self, *args):
        '''Enable and range the Spacing Value field for the selected mode.

        Keep and Uniform take no value, so the field greys out rather than
        showing a number that does nothing. *args soaks up the index that
        QComboBox.currentIndexChanged sends.
        '''
        mode = self.cmb_mode.currentText().lower()
        # Keep needs a source distribution, which a new chain has not got.
        if mode == 'keep' and self.rad_new.isChecked():
            self.cmb_mode.setCurrentIndex(self.MODES.index('Uniform'))
            return

        if mode in self.PARAM_MODES:
            default, (low, high), step = self.PARAM_MODES[mode]
            value = self._param_values.get(mode, default)
            self._param_mode = None     # suppress _remember_param while setting
            self.spn_param.setRange(low, high)
            self.spn_param.setSingleStep(step)
            self.spn_param.setValue(value)
            self.spn_param.setEnabled(True)
            self._param_mode = mode
            self.lbl_param_hint.setText(f'({low:g} to {high:g})')
            self.spn_param.setToolTip(
                'Exponent k: 1.0 is uniform, above packs joints toward the '
                f'base, below toward the tip. {low:g} to {high:g}.'
                if mode == 'power' else
                'Ratio r: each segment is r times the one before, so joints '
                f'pack toward the tip. 1.0 is uniform. {low:g} to {high:g} - '
                'smaller would collapse the far segments to nothing.')
        else:
            self._param_mode = None
            self.spn_param.setEnabled(False)
            self.lbl_param_hint.setText(f'(not used by {mode.capitalize()})')
            self.spn_param.setToolTip(
                f'{mode.capitalize()} spacing takes no value.')

    def _remember_param(self, value):
        '''Keep each parametric mode's own last value.'''
        if self._param_mode:
            self._param_values[self._param_mode] = value

    def _sync_detected(self, *args):
        '''Show the joint count of the listed chain(s).'''
        counts = []
        for name in self._chain_names():
            root = self._resolve_root(name)
            if not root:
                counts.append('?')
                continue
            try:
                counts.append(str(len(rt_joint.get_joint_chain(root))))
            except Exception:
                counts.append('?')
        self.lbl_detected.setText(
            f'(detected: {", ".join(counts)})' if counts else '(detected: —)')

    def _resolve_root(self, name):
        '''
        The chain root for one entry in the Joint Chain(s) box, or None.

        Three cheap lookups, no scene-wide scans:
            1. a rig name Select put there, whose root is still in the scene;
            2. a rig name resolved through the naming template, which gives
               the root's expected name outright;
            3. the name of any joint in the chain, taken literally.
        '''
        try:
            root = self._roots.get(name)
            if root and cmds.objExists(root):
                return rt_chain.chain_root(root)
            templated = rt_naming.fstr(name, rt_constants.JOINT, rt_constants.TYPE_BN, 0)
            if cmds.objExists(templated):
                return rt_chain.chain_root(templated)
            return rt_chain.chain_root(name) if cmds.objExists(name) else None
        except Exception:
            return None

    # ACTIONS ===========================================================

    def select_from_viewport(self):
        '''List the chain of every selected joint, one entry per chain.'''
        try:
            specs = rt_chain.resolve_selection()
        except Exception as exc:
            cmds.warning(f'Could not resolve selection: {exc}')
            return
        if not specs:
            cmds.warning('No chain resolved from the selection.')
            return

        new_specs = [s for s in specs if s.is_new]
        if new_specs:
            # Two plain transforms: those mark the ends of a new chain.
            spec = new_specs[0]
            self.rad_new.setChecked(True)
            self.txt_chain.clear()
            cmds.warning(f'New chain mode: {spec.start} to {spec.end}. '
                         'Click Build Joints.')
            return

        # Show rig names, remember the roots they came from. A rig name reads
        # far better in a list than a root joint name, and it is what the
        # rest of the pipeline calls a chain.
        self._roots = {}
        names = []
        for spec in specs:
            name = spec.rigname or spec.root.split('|')[-1]
            if name in self._roots:
                cmds.warning(f'Two chains both resolve to "{name}"; listing '
                             'the first. Rebuild them one at a time.')
                continue
            self._roots[name] = spec.root
            names.append(name)

        self.rad_rebuild.setChecked(True)
        self.txt_chain.setText(', '.join(names))
        # Default the count to the first chain's own length, so the first
        # click re-spaces rather than resizing by surprise.
        try:
            self.spn_count.setValue(len(rt_joint.get_joint_chain(specs[0].root)))
        except Exception:
            pass
        self._sync_detected()

    def reset_original(self):
        '''Re-baseline the session original cache for the listed chain(s).

        Clears only those chains, not every cached chain: forgetting an
        unrelated tail's original would silently cost it a lossless rebuild.
        '''
        names = self._chain_names()
        if not names:
            cmds.warning('No chain listed. Select a joint of each chain and '
                         'click Select first.')
            return
        cleared, missing = 0, []
        for name in names:
            root = self._resolve_root(name)
            if not root:
                missing.append(name)
                continue
            cleared += rt_chain.clear_cache(root)
        if missing:
            cmds.warning(f'No chain found for: {", ".join(missing)}.')
        cmds.warning(f'Re-baselined {cleared} chain(s): their shape as it '
                     'stands now is what the next Build measures from.')

    def run_build(self):
        '''Build or re-space with the current settings. One undo step.'''
        n = self.spn_count.value()
        mode = self.cmb_mode.currentText().lower()
        param = self.spn_param.value() if self.spn_param.isEnabled() else None
        invert = self.chk_invert.isChecked()
        snap = self.chk_snap.isChecked()
        orient = self.chk_orient.isChecked()

        if self.rad_new.isChecked():
            self._build_new_chain(n, mode, param, invert, orient)
            return

        names = self._chain_names()
        if not names:
            cmds.warning('No chain listed. Select a joint of each chain and '
                         'click Select, or type the rig part names.')
            return

        # One chain per name, each on its own: a typo or a guarded chain in a
        # list of four still leaves the other three rebuilt.
        done, failed = [], []
        for name in names:
            root = self._resolve_root(name)
            if not root:
                cmds.warning(f'No chain found for "{name}" - not a rig part '
                             'or joint name in this scene.')
                failed.append(name)
                continue
            try:
                rt_chain.rebuild(root, n, mode, param, invert, snap, orient)
                done.append(name)
            except Exception as exc:
                cmds.warning(f'Rebuild failed on {name}: {exc}')
                failed.append(name)
        if done:
            cmds.warning(f'Re-spaced {len(done)} chain(s) to {n} joints '
                         f'({mode}): {", ".join(done)}.')
        if failed:
            cmds.warning(f'No rebuild for: {", ".join(failed)}. See the '
                         'Script Editor for why.')
        self._sync_detected()

    def _build_new_chain(self, n, mode, param, invert, orient):
        '''Create one chain between the two selected objects.

        No rig name is asked for: the joints are named after the first
        selected object and can be renamed in Maya like any others.
        '''
        sel = cmds.ls(selection=True, transforms=True)
        if len(sel) < 2:
            cmds.warning('Select two objects to mark the ends of the new '
                         'chain.')
            return
        try:
            joints = rt_chain.build_new(sel[0], sel[1], n, None, mode, param,
                                      invert, orient)
        except Exception as exc:
            cmds.warning(f'Chain build failed: {exc}')
            return
        cmds.warning(f'Built {n} joints ({mode}) from {sel[0]} to {sel[1]}: '
                     f'{joints[0]} onward.')


def get_maya_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def show_ui():
    '''Show the Joint Chain Builder window, closing any previous instance.'''
    global joint_chain_builder_window
    try:
        joint_chain_builder_window.close()
        joint_chain_builder_window.deleteLater()
    except Exception:
        pass
    parent = get_maya_window()
    joint_chain_builder_window = JointChainBuilderUI(parent=parent)
    joint_chain_builder_window.show()
    return joint_chain_builder_window
