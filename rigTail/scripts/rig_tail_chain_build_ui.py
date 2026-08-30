'''
rig_tail_chain_build_ui.py
author: Daisy Jane @gnitemouse

Joint Chain Builder window.
Create and re-space BN joint chains before the Setup phase.

Keeps its own create_group_box / style_button rather than touching the
shared widgets the other tool windows use.

Stateless by design: every option is a widget read at click time, with no
config file and no preferences. What the window remembers is scene state,
not settings - which chains Select found, and which joint of each was
picked.

Classes and functions:
    JointChainBuilderUI: the Joint Chain Builder window
    show_ui: build and show the window, closing any previous instance
    get_maya_window: the Maya main window as a QWidget for parenting
'''

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from rig_tail_qt import QtWidgets, QtCore, wrapInstance

import rig_tail_constants as rt_constants
import rig_tail_naming as rt_naming
import rig_tail_maya as rt_maya
import rig_tail_joint as rt_joint
import rig_tail_chain_build as rt_chain
import rig_tail_chain_spacing as rt_chain_spacing


class JointChainBuilderUI(QtWidgets.QDialog):
    '''Joint Chain Builder window: create and re-space joint chains.'''

    FIELD_W = 180       # dropdown / spin box width
    FIELD_H = 28        # height of every input widget
    LABEL_W = 150       # shared field start
    ROW_GAP = 6         # label -> field gap, identical on every row
    # Gap between a field and the grey hint beside it. The hint's own left
    # margin, not the row's spacing - that would move the field too and
    # break the shared left edge.
    HINT_STYLE = 'color: #999999; font-size: 10px; margin-left: 2px;'

    # Left padding is 8px everywhere EXCEPT the combo box, which gets 10.
    # QLineEdit and the spin boxes add Qt's own 2px internal margin on top
    # of the stylesheet padding; a QComboBox does not. Matching numbers
    # would leave the combo text 2px left of every other field.
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

    # Spacing Value only means anything in the two parametric modes, and
    # each keeps its own value and range: 1.7 is a fine exponent and an
    # impossible ratio, so switching must not carry it across.
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
        # Rig name -> chain root, as a full DAG path, filled by Select. The
        # box lists rig names because they read well; a path is stored
        # because two chains may answer to one name and the pick says which.
        self._roots = {}
        # Typed names that turned out to match more than one joint, so an
        # action can say 'select the chain' rather than 'no chain found'.
        self._ambiguous = set()
        # Rig name -> the joint picked in the viewport, for 'Build from
        # selected joint'. Only Select can fill this in: a typed name says
        # which chain, never which joint of it.
        self._starts = {}
        # Last count Select or a Build-from switch put in the spin box, so
        # the field follows the detected count until the artist overrides it.
        self._detected_first = None
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
        # Explicit groups: all four radios are children of the Source group
        # box, and Qt would otherwise make one exclusive set of the lot.
        self._source_group = QtWidgets.QButtonGroup(self)
        for rad in (self.rad_rebuild, self.rad_new):
            rad.setStyleSheet('color: #cccccc; spacing: 4px;')
            self._source_group.addButton(rad)

        # Build from -----------------------------------------------------
        # Which end of a rebuild's chain the count and spacing apply from.
        # Last in Source: it qualifies the pair above rather than being a
        # third choice alongside them.
        self.rad_from_base = QtWidgets.QRadioButton('Build from base joint')
        self.rad_from_selected = QtWidgets.QRadioButton('Build from selected joint')
        self.rad_from_base.setChecked(True)
        self.rad_from_base.setToolTip(
            'Re-space the whole chain, base joint to tip, whichever joint of '
            'it was selected. Joint Count is the count of the entire chain.')
        self.rad_from_selected.setToolTip(
            'Re-space only the run from the selected joint down to the tip. '
            'Everything above it is left exactly as it is, and the selected '
            'joint itself does not move - it is the fixed end of the span. '
            'Joint Count is the count of that span, the selected joint '
            'included.\n\n'
            'Needs Select to have been used: a typed rig part name says '
            'which chain, never which joint of it, so a typed entry falls '
            'back to the base joint.')
        self._from_group = QtWidgets.QButtonGroup(self)
        for rad in (self.rad_from_base, self.rad_from_selected):
            rad.setStyleSheet('color: #cccccc; spacing: 4px;')
            self._from_group.addButton(rad)
        self.rad_from_base.toggled.connect(self._sync_detected)

        src_layout.addLayout(chain_row)
        src_layout.addSpacing(6)
        src_layout.addWidget(self.rad_rebuild)
        src_layout.addWidget(self.rad_new)
        src_layout.addSpacing(6)
        src_layout.addWidget(self.rad_from_base)
        src_layout.addWidget(self.rad_from_selected)
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
            '  Power    - u = t^(1/k), long segments at the base tapering\n'
            '             to short ones at the tip as k rises\n'
            '  Ratio    - each segment r times the last, so segments shrink\n'
            '             from base to tip as r drops below 1\n'
            'Power and Ratio taper the same way; Invert swaps the direction '
            'of any of the four.\n'
            'Keep and Uniform take no Spacing Value.')
        self.cmb_mode.setStyleSheet(self.FIELD_STYLE)
        self.cmb_mode.setFixedSize(self.FIELD_W, self.FIELD_H)
        self.cmb_mode.currentIndexChanged.connect(self._sync_mode)
        # Power, not Keep: Keep only means anything when there is an existing
        # spacing pattern to hold, and it is the one mode that silently does
        # nothing useful on a fresh chain (_sync_mode has to switch away from
        # it). Set after the signal is connected so the Spacing Value field
        # picks up K_DEFAULT on the way.
        self.cmb_mode.setCurrentIndex(self.MODES.index('Power'))
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
            'default: Tail Setup owns orientation, and this saves a trip '
            'there for a chain that needs nothing else.\n\n'
            'A rebuild keeps the roll the chain already has (Setup\'s '
            'cascade), so a mirrored pair stays mirrored and a Roll Chain '
            'fix-up survives. A new chain takes its roll from its own bend '
            'plane.')
        self.style_checkbox(self.chk_orient)
        spc_layout.addWidget(self.chk_orient)

        # End joint
        self.chk_ee = QtWidgets.QCheckBox('Add end joint (_ee_)')
        self.chk_ee.setToolTip(
            'Finish the chain with an end joint, one segment out past the '
            'tip along the chain\'s final direction. The tail gets longer by '
            'that segment; the joints already placed do not move.\n\n'
            'Tail Setup aims the last real joint at the end joint rather '
            'than guessing a final direction, and from the next rebuild on '
            'the end joint is treated as the end of the tail\'s length - so '
            'the chain re-spaces up to it rather than past it.\n\n'
            'Does nothing to a chain that already has one: that joint is the '
            'tail\'s end and is never moved out here, nor removed by '
            'unticking this.')
        self.style_checkbox(self.chk_ee)
        spc_layout.addWidget(self.chk_ee)

        spc_group.setLayout(spc_layout)
        main_layout.addWidget(spc_group)
        main_layout.addSpacing(8)

        # Status ---------------------------------------------------------
        status_layout = QtWidgets.QHBoxLayout()
        lbl_status = QtWidgets.QLabel('Status:')
        lbl_status.setMinimumWidth(24)
        self.txt_status = QtWidgets.QLineEdit()
        self.txt_status.setReadOnly(True)
        self.txt_status.setPlaceholderText('Ready')
        self.txt_status.setStyleSheet('''
            QLineEdit {
                background-color: #2b2b2b;
                color: #4A90E2;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 10px;
                padding: 2px 14px;
            }
        ''')
        self.txt_status.setToolTip('Report last action')
        status_layout.addWidget(lbl_status)
        status_layout.addWidget(self.txt_status)
        main_layout.addLayout(status_layout)

        # Buttons --------------------------------------------------------
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        self.btn_reset = QtWidgets.QPushButton('Bake Joint Chain')
        self.btn_reset.setToolTip('Bake joints to preserve the current shape,'
            'keeping this joint chain for the next Build.\n'
            'Build preserves this shape and the earlier one is forgotten.')
        # Renames ONE chain, not every node carrying the rig part name -
        # which is the way out of two chains sharing one: park the old chain
        # under a name RIGPARTS does not list and nothing else touches it.
        self.btn_rename = QtWidgets.QPushButton('Rename Chain')
        self.btn_rename.setToolTip(
            'Rename the listed chain onto a different rig part name, and '
            'renumber it from 00.\nRenames this chain only, so a replacement '
            'tail can take over the name while the old chain stays in the '
            'scene.\nA rig part name RIGPARTS does not list is ignored by '
            'Setup and by Tail Build.')
        self.btn_build = QtWidgets.QPushButton('Build Joints')
        self.btn_build.setToolTip(
            'Build or re-space the listed chain(s) with the settings above. '
            'One undo step.')

        self.btn_reset.clicked.connect(self.reset_original)
        self.btn_rename.clicked.connect(self.rename_chain)
        self.btn_build.clicked.connect(self.run_build)

        self.style_button(self.btn_reset, 0)
        self.style_button(self.btn_rename, 0)
        self.style_button(self.btn_build, 4)  # olive primary

        button_layout.addWidget(self.btn_reset)
        button_layout.addWidget(self.btn_rename)
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
        if style == 4:  # olive green (primary), matches rig_tail_setup_ui
            button.setStyleSheet('''
                QPushButton {
                    background-color: #6B7A45; color: white; border: none;
                    border-radius: 4px; padding: 8px 16px; font-weight: bold;
                }
                QPushButton:hover { background-color: #7C8C52; }
                QPushButton:pressed { background-color: #525E33; }
            ''')
        else:  # grey, matches rig_tail_setup_ui
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
        # A new chain has no base and no selected joint to build from, so both
        # of its ends are the objects that were picked.
        self.rad_from_base.setEnabled(not is_new)
        self.rad_from_selected.setEnabled(not is_new)
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
                'Exponent k: 1.0 is uniform, above 1 makes the base segments '
                'longer and the tip ones shorter, below 1 reverses that '
                f'(same as Invert). {low:g} to {high:g}.'
                if mode == 'power' else
                'Ratio r: each segment is r times the one before, so the '
                'segments start long at the base and shorten toward the tip. '
                f'1.0 is uniform. {low:g} to {high:g} - smaller would '
                'collapse the far segments to nothing.')
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
        '''Show the joint count of the listed chain(s), and follow it.

        The count reported is the count of what Build Joints would actually
        re-space, so it drops to the span length under 'Build from selected
        joint'. *args soaks up the bool QRadioButton.toggled sends.
        '''
        counts, labels = [], []
        for name in self._chain_names():
            start = self._resolve_start(name)
            try:
                count = len(rt_joint.get_joint_chain(start)) if start else None
            except Exception:
                count = None
            counts.append(count)
            labels.append(str(count) if count else '?')

        # The spin box follows the detected count only while it still holds
        # what was last detected, so Select lands on a count that re-spaces
        # what is there and a typed count is left alone.
        first = counts[0] if counts else None
        if first and (self._detected_first is None or
                      self.spn_count.value() == self._detected_first):
            self.spn_count.setValue(first)
        self._detected_first = first

        self.lbl_detected.setText(
            f'(detected: {", ".join(labels)})' if labels else '(detected: —)')

    def _resolve_start(self, name):
        '''The joint a rebuild of this entry starts from.

        The chain's base joint, or the joint Select recorded when 'Build from
        selected joint' is on. A typed name has no recorded joint: it names
        a chain, not a joint of it, so it falls back to the base.
        '''
        root = self._resolve_root(name)
        if not root or not self.rad_from_selected.isChecked():
            return root
        selected = self._starts.get(name)
        return selected if selected and cmds.objExists(selected) else root

    def _resolve_root(self, name):
        '''
        The chain root for one entry in the Joint Chain(s) box, or None.

        Three cheap lookups, no scene-wide scans:
            1. a rig name Select put there, whose root is still in the scene;
            2. a rig name resolved through the naming template, which gives
               the root's expected name outright;
            3. the name of any joint in the chain, taken literally.

        Select comes first as the only lookup that can tell two chains of
        one rig part apart: it records a DAG path, where a name lookup can
        only say 'a joint called this'. That is what lets a replacement tail
        be rebuilt while the chain it replaces is still in the scene.
        '''
        try:
            root = self._roots.get(name)
            if root and cmds.objExists(root):
                return rt_chain.chain_root(root)
            templated = rt_naming.fstr(name, rt_constants.JOINT,
                                       rt_constants.TYPE_BN, 0)
            return (self._root_from_name(templated) or
                    self._root_from_name(name))
        except Exception:
            return None

    def _root_from_name(self, name):
        '''The chain root for a joint NAME, or None when the name does not
        pick out exactly one joint.

        cmds.objExists cannot be the test: it answers True for a name two
        joints share, which is the case that must not resolve silently.
        '''
        matches = cmds.ls(name, long=True, type='joint') or []
        if len(matches) == 1:
            return rt_chain.chain_root(matches[0])
        if len(matches) > 1:
            self._ambiguous.add(name)
        return None

    # STATUS ============================================================

    def _set_status(self, text):
        '''Put one line in the status bar, and its full form in its tooltip
        for when the line is longer than the field.'''
        self.txt_status.setText(text)
        self.txt_status.setToolTip(text)
        self.txt_status.setCursorPosition(0)

    def _options_summary(self):
        '''The spacing options a Build Joints click is about to use, as one
        comma-separated phrase.'''
        mode = self.cmb_mode.currentText()
        parts = [f'{mode} {self.spn_param.value():g}'
                 if self.spn_param.isEnabled() else mode]
        if self.chk_invert.isChecked():
            parts.append('invert')
        if self.chk_snap.isChecked() and self.chk_snap.isEnabled():
            parts.append('snap')
        if self.chk_orient.isChecked():
            parts.append('orient')
        if self.chk_ee.isChecked():
            parts.append('end joint')
        if self.rad_rebuild.isChecked():
            parts.append('from selected joint'
                         if self.rad_from_selected.isChecked() else
                         'from base joint')
        return ', '.join(parts)

    # ACTIONS ===========================================================

    def select_from_viewport(self):
        '''List the chain of every selected joint, one entry per chain.'''
        try:
            specs = rt_chain.resolve_selection()
        except Exception as exc:
            cmds.warning(f'Could not resolve selection: {exc}')
            self._set_status(f'Select - could not resolve selection: {exc}')
            return
        if not specs:
            cmds.warning('No chain resolved from the selection.')
            self._set_status('Select - no chain resolved from the selection.')
            return

        new_specs = [s for s in specs if s.is_new]
        if new_specs:
            # Two plain transforms: those mark the ends of a new chain.
            spec = new_specs[0]
            self.rad_new.setChecked(True)
            self.txt_chain.clear()
            # Leaf names: the spec holds full paths, which read as noise in
            # a one-line report.
            start, end = rt_maya.leaf(spec.start), rt_maya.leaf(spec.end)
            cmds.warning(f'New chain mode: {start} to {end}. '
                         'Click Build Joints.')
            self._set_status(f'Select - new chain mode, {start} to '
                             f'{end}. Click Build Joints.')
            return

        # Show rig names, remember the roots they came from. A rig name reads
        # far better in a list than a root joint name, and it is what the
        # rest of the pipeline calls a chain.
        self._roots, self._starts = {}, {}
        names, skipped = [], 0
        for spec in specs:
            name = spec.rigname or spec.root.split('|')[-1]
            if name in self._roots:
                cmds.warning(f'Two chains both resolve to "{name}"; listing '
                             'the first. Rebuild them one at a time.')
                skipped += 1
                continue
            self._roots[name] = spec.root
            self._starts[name] = spec.selected
            names.append(name)

        self.rad_rebuild.setChecked(True)
        # Let the count follow whatever this selection detects, overriding
        # any earlier one: a fresh Select is a fresh chain to re-space.
        self._detected_first = None
        self.txt_chain.setText(', '.join(names))
        self._sync_detected()
        self._set_status(
            f'Select - listed {len(names)} chain(s): {", ".join(names)}' +
            (f' ({skipped} skipped, duplicate name)' if skipped else '') + '.')

    def reset_original(self):
        '''Re-baseline the session original cache for the listed chain(s).

        Clears only those chains, not every cached chain: forgetting an
        unrelated tail's original would silently cost it a lossless rebuild.
        '''
        names = self._chain_names()
        if not names:
            cmds.warning('No chain listed. Select a joint of each chain and '
                         'click Select first.')
            self._set_status('Bake Joint Chain - nothing baked: no chain '
                             'listed.')
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
        self._set_status(
            f'Bake Joint Chain - re-baselined {cleared} chain(s); their shape '
            'and joint size as they stand now are what the next Build '
            'measures from' +
            (f'. No chain found for: {", ".join(missing)}' if missing else '') +
            '.')

    def rename_chain(self):
        '''Move one listed chain onto a different rig part name.

        One at a time on purpose: the new name is typed, and a list of
        chains has no second name to give them. Takes the first entry in the
        box, so Select then Rename does what it looks like.
        '''
        names = self._chain_names()
        if not names:
            cmds.warning('No chain listed. Select a joint of the chain and '
                         'click Select first.')
            self._set_status('Rename Chain - nothing renamed: no chain '
                             'listed.')
            return
        if len(names) > 1:
            cmds.warning(f'{len(names)} chains listed. Rename takes one at a '
                         f'time; renaming "{names[0]}".')

        old = names[0]
        self._ambiguous = set()
        root = self._resolve_root(old)
        if not root:
            message = (f'"{old}" names more than one joint chain. Select a '
                       'joint of the one you mean and click Select.'
                       if self._ambiguous else
                       f'No chain found for "{old}".')
            cmds.warning(message)
            self._set_status(f'Rename Chain - {message}')
            return

        new, ok = QtWidgets.QInputDialog.getText(
            self, 'Rename Chain',
            f'New rig part name for "{old}":\n\n'
            'The chain is renamed and renumbered from 00. Nothing else in '
            'the scene is touched.\nA name RIGPARTS does not list is ignored '
            'by Setup and by Tail Build.',
            text=f'{old}Old')
        if not ok or not new.strip():
            self._set_status('Rename Chain - cancelled.')
            return

        try:
            joints = rt_chain.rename_chain(root, new.strip())
        except Exception as exc:
            cmds.warning(f'Rename failed: {exc}')
            self._set_status(f'Rename Chain - failed: {exc}')
            return

        # The box still lists the old rig name, which no longer exists. Point
        # both it and the recorded root at the renamed chain.
        new = new.strip()
        self._roots.pop(old, None)
        self._starts.pop(old, None)
        self._roots[new] = joints[0] if joints else None
        self.txt_chain.setText(
            ', '.join(new if n == old else n for n in names))
        self._sync_detected()
        cmds.warning(f'Renamed "{old}" to "{new}" ({len(joints)} joints, '
                     'renumbered from 00).')
        self._set_status(f'Rename Chain - renamed "{old}" to "{new}": '
                         f'{len(joints)} joints, renumbered from 00.')

    def run_build(self):
        '''Build or re-space with the current settings. One undo step.'''
        n = self.spn_count.value()
        mode = self.cmb_mode.currentText().lower()
        param = self.spn_param.value() if self.spn_param.isEnabled() else None
        invert = self.chk_invert.isChecked()
        snap = self.chk_snap.isChecked()
        orient = self.chk_orient.isChecked()
        add_ee = self.chk_ee.isChecked()

        if self.rad_new.isChecked():
            self._build_new_chain(n, mode, param, invert, orient, add_ee)
            return

        names = self._chain_names()
        if not names:
            cmds.warning('No chain listed. Select a joint of each chain and '
                         'click Select, or type the rig part names.')
            self._set_status('Build Joints - nothing built: no chain listed.')
            return

        from_selected = self.rad_from_selected.isChecked()
        options = self._options_summary()

        # One chain per name, each on its own: a typo or a guarded chain in a
        # list of four still leaves the other three rebuilt.
        done, failed, fellback = [], [], []
        self._ambiguous = set()
        for name in names:
            root = self._resolve_root(name)
            if not root:
                if self._ambiguous:
                    cmds.warning(
                        f'"{name}" names more than one joint chain in this '
                        'scene. Select a joint of the chain you mean and '
                        'click Select - a typed name cannot say which.')
                else:
                    cmds.warning(f'No chain found for "{name}" - not a rig '
                                 'part or joint name in this scene.')
                failed.append(name)
                continue
            # None means the whole chain. A from-selected rebuild with no
            # recorded pick is a typed entry: say so rather than quietly
            # doing something other than the option shown.
            start = self._starts.get(name) if from_selected else None
            if start and not cmds.objExists(start):
                start = None
            if from_selected and not start:
                fellback.append(name)
            try:
                rt_chain.rebuild(root, n, mode, param, invert, snap, orient,
                                 start, add_ee)
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
        if fellback:
            cmds.warning(f'No selected joint recorded for: '
                         f'{", ".join(fellback)}. Rebuilt from the base '
                         'joint. Click Select to record one.')

        status = []
        if done:
            status.append(f're-spaced {len(done)} chain(s) to {n} joints '
                          f'[{options}]: {", ".join(done)}')
        if fellback:
            status.append(f'no selected joint recorded for '
                          f'{", ".join(fellback)}, built from base')
        if failed:
            status.append(f'failed on {", ".join(failed)} (see Script Editor)')
        if self._ambiguous:
            status.append(f'{", ".join(sorted(self._ambiguous))} names more '
                          'than one chain - select the one you mean')
        self._set_status('Build Joints - ' + '; '.join(status) + '.')
        self._sync_detected()

    def _build_new_chain(self, n, mode, param, invert, orient, add_ee=False):
        '''Create one chain between the two selected objects.

        No rig name is asked for: the joints are named after the first
        selected object and can be renamed in Maya like any others.
        '''
        sel = cmds.ls(selection=True, transforms=True, long=True)
        if len(sel) < 2:
            cmds.warning('Select two objects to mark the ends of the new '
                         'chain.')
            self._set_status('Build Joints - nothing built: select two '
                             'objects to mark the ends of the new chain.')
            return
        options = self._options_summary()
        try:
            joints = rt_chain.build_new(sel[0], sel[1], n, None, mode, param,
                                      invert, orient, add_ee)
        except Exception as exc:
            cmds.warning(f'Chain build failed: {exc}')
            self._set_status(f'Build Joints - new chain failed: {exc}')
            return
        # Leaf names in the report: the ends and the new root are picked by
        # full path so the right nodes are used, but a path reads as noise
        # in a one-line status field.
        start, end = rt_maya.leaf(sel[0]), rt_maya.leaf(sel[1])
        root = rt_maya.leaf(joints[0])
        cmds.warning(f'Built {n} joints ({mode}) from {start} to {end}: '
                     f'{root} onward.')
        self._set_status(f'Build Joints - built a new chain of {n} joints '
                         f'[{options}] from {start} to {end}: {root} onward.')


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
