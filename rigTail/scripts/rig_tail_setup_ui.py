'''
rig_tail_setup_ui.py
author: Daisy Jane @gnitemouse

UI for the Tail Setup phase, the optional skeleton-prep step
that runs before the Tail Builder. Nothing here affects the build; it
only re-orients the raw BN skeleton so tails move coherently.

Three batch operations, exposed as checkboxes:
    Orient Joints (ORIENT_JOINTS): aim-orient each chain so its up-axis
        stops twisting from joint to joint. The dropdown in the same row
        picks where the up reference comes from (ORIENT_UP_MODE), which is
        what decides the chain's roll: 'Cascade' seeds from the chain's own
        first joint as it stands now, keeping the roll it already has (so a
        mirror or a Roll Chain fix-up survives a re-run), 'Best-fit' takes
        the roll from the chain's bend plane and overwrites it.
        Disabled unless Orient Joints is ticked.
    Mirror Orient (MIRROR_ORIENT): reflect matching 'L_'/'R_' pairs'
        orientation so the two sides face as mirror images. The dropdown
        in the same row picks the behavior (MIRROR_BEHAVIOR). Write each
        of the mirrored side's axes '+' when it points the same way as the
        mirror image of its partner's and '-' when it points the opposite
        way; a rotation about an axis mirrors on '-' and a translation
        along it on '+', so three of the six always mirror:
            'Mirror' (default)  -aim -roll -up  all rotations, no translation
            'Symmetric'         +aim +roll -up  up; aim and roll translate
            'Parallel'          +aim -roll +up  roll; aim and up translate
        Disabled unless Mirror Orient is ticked.
    Mirror Joints (MIRROR_JOINTS): reflect matching 'L_'/'R_' pairs'
        positions so the target side's joints sit at the exact mirror.
        Also the only operation that CREATES a chain: a listed rig part
        whose target-side joints do not exist yet is built from its source
        side. The other two can only rewrite joints that already exist, so
        they warn about a missing chain instead.
Below those, its own section, is the interactive Roll Chain fix-up: list one
or more chains in the Chain box (type them, or Select to read them from the
selected joints), then the left/right arrows roll every one of them about
its aim axis by the step angle - left subtracts, right adds - to turn
wrong-facing chains onto the right plane. It applies immediately with no
confirmation dialog. Dropdowns set the source side and the aim, up and
mirror axes. Dry Run only logs the intended batch changes. Orientation
steps keep positions; Mirror Joints and Roll move joints. Affected geometry
is unbound for the build to rebind.

Run Setup calls rig_tail_setup.setup_tails. Values live in
rig_tail_constants and round-trip through the same JSON config as the
Builder. The window is modeled on rig_tail_build_ui.RigTailUI and reuses its
RIGPARTS editor. The Qt binding comes from rig_tail_qt.

Classes and functions:
    RigTailSetupUI: the Setup window
    show_ui: build and show the window, closing any previous instance
    get_maya_window: the Maya main window as a QWidget for parenting
'''

import os

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from rig_tail_qt import QtWidgets, QtCore, wrapInstance

import rig_tail_constants as rt_constants
import rig_tail_mirror as rt_mirror
import rig_tail_build_ui as rt_build_ui  # reuse RigPartsEditor


class RigTailSetupUI(QtWidgets.QDialog):
    '''Tail Setup window: orient / mirror the skeleton before build.'''

    AXES = ['x', 'y', 'z']
    SIDES = ['R', 'L']
    # Display labels for MIRROR_BEHAVIOR; stored lower-case in constants.
    # Order matches rig_tail_mirror.BEHAVIORS, default first.
    BEHAVIORS = ['Mirror', 'Symmetric', 'Parallel']
    # Display labels for ORIENT_UP_MODE; stored lower-case in constants.
    UP_MODES = ['Cascade', 'Best-fit']

    # Label column of the configuration summary's list settings, sized to
    # its longest label so their values line up without pushing the text far
    # off the left edge. The toggles and axes below are grouped under their
    # own headings instead (see update_display).
    SUMMARY_W = len('RIGPARTS')

    # One size for every field-level widget, so the dropdowns, the spin box,
    # the arrows and the Select button all line up. The settings dropdowns
    # (behavior, source side, mirror/aim/up axis) are laid out flush right at
    # FIELD_W, which is what puts them in a single column despite sitting in
    # rows with different labels.
    FIELD_W = 150       # dropdown / spin box width
    FIELD_H = 28        # height of every dropdown, spin box, arrow, button
    ARROW_W = 28        # roll arrows (square at FIELD_H)
    LABEL_W = 170       # leading label column

    FIELD_STYLE = '''
        QComboBox, QSpinBox {
            background-color: #3a3a3a; color: #cccccc;
            border: 1px solid #555555; border-radius: 4px; padding: 2px 8px;
        }
        QComboBox:focus, QSpinBox:focus { border-color: #F5D041; }
        QComboBox:disabled { color: #777777; border-color: #444444; }
    '''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Tail Setup')
        self.setMinimumWidth(500)
        self.setup_ui()
        self.load_current_values()
        self.resize(500, self.sizeHint().height())

    # LAYOUT ===========================================================

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 10, 15, 10)

        # Title and its one-line descriptor read as one heading, so they sit
        # in their own tight layout instead of taking the main 10px spacing.
        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(2)
        title = QtWidgets.QLabel('TAIL SETUP')
        title.setStyleSheet('font-size: 18px; font-weight: bold; color: #FFFFFF;')
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(title)

        subtitle = QtWidgets.QLabel('Orient / mirror the skeleton before building')
        subtitle.setStyleSheet('font-size: 10px; color: #999999;')
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(subtitle)
        main_layout.addLayout(title_layout)

        author = QtWidgets.QLabel('author Daisy Jane @gnitemouse')
        author.setStyleSheet('font-size: 10px; font-weight: normal; color: #4A90E2;')
        author.setAlignment(QtCore.Qt.AlignRight)
        main_layout.addWidget(author)

        # Current configuration + config file row (mirrors the Builder)
        display_group = self.create_group_box('Current Configuration')
        display_layout = QtWidgets.QVBoxLayout()

        self.txt_display = QtWidgets.QTextEdit()
        self.txt_display.setReadOnly(True)
        # Taller than the Builder's: the summary is one aligned line per
        # setting, so eight lines would clip the axes off the bottom.
        self.txt_display.setMaximumHeight(170)
        self.txt_display.setStyleSheet('''
            QTextEdit {
                background-color: #2b2b2b;
                color: #cccccc;
                font-family: Consolas, monospace;
                font-size: 11px;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 2px 20px;
            }
        ''')
        self.txt_display.document().setDocumentMargin(4)
        self.txt_display.setToolTip('Summary of setup configuration')
        display_layout.setContentsMargins(8, 2, 8, 2)
        display_layout.setSpacing(4)
        display_layout.addWidget(self.txt_display)

        config_file_layout = QtWidgets.QHBoxLayout()
        lbl_config = QtWidgets.QLabel('File:')
        lbl_config.setMinimumWidth(24)
        self.txt_config = QtWidgets.QLineEdit()
        self.txt_config.setPlaceholderText('Default')
        self.txt_config.setStyleSheet('''
            QLineEdit {
                background-color: #2b2b2b;
                color: #4A90E2;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 10px;
                padding: 2px 14px;
            }
            QLineEdit:focus {
                border-color: #F5D041;
            }
        ''')
        self.txt_config.setToolTip(
            'Config file currently in effect (Default = built-in '
            'defaults). Type a path and press Enter to load it '
            'directly; Load/Save Config update it too.')
        self.txt_config.returnPressed.connect(self.load_config_from_text)
        config_file_layout.addWidget(lbl_config)
        config_file_layout.addWidget(self.txt_config)
        display_layout.addLayout(config_file_layout)

        config_btn_layout = QtWidgets.QHBoxLayout()
        self.btn_load_config = QtWidgets.QPushButton('Load Config')
        self.btn_save_config = QtWidgets.QPushButton('Save Config')
        self.btn_load_config.setToolTip('Import all settings from a JSON config file.')
        self.btn_save_config.setToolTip('Export all settings to a JSON config file.')
        self.btn_load_config.clicked.connect(self.load_config)
        self.btn_save_config.clicked.connect(self.save_config)
        self.style_button(self.btn_load_config, 0)
        self.style_button(self.btn_save_config, 0)
        config_btn_layout.addWidget(self.btn_load_config)
        config_btn_layout.addWidget(self.btn_save_config)
        display_layout.addLayout(config_btn_layout)
        display_layout.addSpacing(8)

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        # Setup options
        options_group = self.create_group_box('Setup Options')
        options_layout = QtWidgets.QVBoxLayout()
        options_layout.setSpacing(8)

        btn_parts = QtWidgets.QPushButton('Edit Rig Parts')
        btn_parts.setToolTip('Edit RIGPARTS: the tails to set up (shared with the Builder).')
        btn_parts.clicked.connect(self.open_rigparts_editor)
        self.style_button(btn_parts, 0)
        options_layout.addWidget(btn_parts)
        options_layout.addSpacing(6)

        # Operation toggles
        self.chk_orient = QtWidgets.QCheckBox('Orient Joints (fix twist)')
        self.chk_orient.setToolTip(
            'Aim-orient every chain: re-aim each joint down its own chain '
            'with one up-axis (the chain plane normal), so the tail bends '
            'in a plane. Removes intra-chain twist. No mirroring. '
            '(ORIENT_JOINTS)')
        self.chk_mirror_orient = QtWidgets.QCheckBox('Mirror Orient (L/R orientation)')
        self.chk_mirror_orient.setToolTip(
            'Reflect each matching L_/R_ pair\'s ORIENTATION across the '
            'symmetry plane, so the two sides face as mirror images. '
            'Positions unchanged. Does not remove twist by itself, so enable '
            'Orient Joints too. (MIRROR_ORIENT)')
        self.chk_mirror_joints = QtWidgets.QCheckBox('Mirror Joints (L/R positions)')
        self.chk_mirror_joints.setToolTip(
            'Reflect each matching L_/R_ pair\'s POSITIONS across the '
            'symmetry plane, so the target side\'s joints sit at the exact '
            'mirror of the source side\'s. Moves joints. Enable only when the '
            'sides are not already positional mirrors.\n'
            'Also BUILDS a target side that has no joints at all: an '
            "included rig part like 'L_fintail' with no chain gets one "
            "mirrored from 'R_fintail', end joint included. Mirror Orient "
            'and Orient Joints cannot create joints, so they only warn about '
            'a missing chain. (MIRROR_JOINTS)')
        self.chk_dryrun = QtWidgets.QCheckBox('Dry Run (preview only)')
        self.chk_dryrun.setToolTip(
            'Only log the intended changes for the batch operations (orient '
            'and both mirrors); do not modify any joints or unbind geometry. '
            'Use this first to verify. Does not apply to Roll Chain. '
            '(MIRROR_DRYRUN)')
        for chk in (self.chk_orient, self.chk_mirror_orient,
                    self.chk_mirror_joints, self.chk_dryrun):
            self.style_checkbox(chk)

        # Up Mode sits in the Orient Joints row, and is disabled alongside
        # it: it only picks where that operation takes its up reference.
        self.cmb_up_mode = self._combo(self.UP_MODES,
            'Where the aim-orient takes its up reference, which is what '
            "decides the chain's ROLL about the aim (the aim itself is "
            'fixed by the joint positions).\n'
            "Cascade: seed from the chain's OWN first joint as it stands "
            'now and carry that down the chain. Twist still goes, but the '
            'roll the chain already has is kept - so a mirrored pair stays '
            'mirrored and a Roll Chain fix-up survives a re-run. Use this '
            'on a skeleton that is already set up.\n'
            "Best-fit: take the roll from the chain's best-fit bend plane, "
            'ignoring how the joints stand now. Lands a raw, arbitrarily '
            'oriented skeleton on its own plane in one pass, but OVERWRITES '
            'any mirrored or hand-rolled orientation. (ORIENT_UP_MODE)')
        self.chk_orient.toggled.connect(self._sync_up_mode_enabled)

        # Mirror Behavior sits in the Mirror Orient row: it only shapes a
        # reflected orientation, so it is meaningless unless that box is
        # ticked - and is disabled alongside it to say so.
        self.cmb_behavior = self._combo(self.BEHAVIORS,
            'How the mirrored side is oriented.\n'
            'Each axis is written + when it points the SAME way as the '
            'mirror image of its partner\'s matching axis, - when it points '
            'the OPPOSITE way. aim runs down the chain, up is the Up Axis, '
            'roll is the remaining one.\n'
            'A ROTATION about an axis mirrors when it is -, a TRANSLATION '
            'along it when it is +, and the count of - is always odd (that '
            'is just the frame staying right-handed). So three of the six '
            'always mirror, and this only picks which three:\n'
            '\n'
            '  Mirror      -aim -roll -up   rotations: all three\n'
            '                               translations: none\n'
            '  Symmetric   +aim +roll -up   rotations: up\n'
            '                               translations: aim, roll\n'
            '  Parallel    +aim -roll +up   rotations: roll\n'
            '                               translations: aim, up\n'
            '\n'
            'Mirror is the default, and is Maya mirrorJoint '
            '-mirrorBehavior. Everything this rig is posed by is a rotation '
            '- curl, wave, twist, roll, and every control gizmo - so Mirror '
            'spends its three there. Its -aim means the aim runs back UP the '
            'chain.\n'
            'Symmetric and Parallel keep the aim running down the chain and '
            'differ by a 180 deg roll about it, so Roll Chain at 180 converts '
            'one into the other on a single chain. No roll reaches Mirror. '
            '(MIRROR_BEHAVIOR)')
        self.chk_mirror_orient.toggled.connect(self._sync_behavior_enabled)

        # Stretch before the combo so it lands flush right at FIELD_W, in the
        # same column as the settings dropdowns below (see _labeled_row).
        orient_row = QtWidgets.QHBoxLayout()
        orient_row.addWidget(self.chk_orient)
        orient_row.addStretch(1)
        orient_row.addWidget(self.cmb_up_mode)

        mirror_orient_row = QtWidgets.QHBoxLayout()
        mirror_orient_row.addWidget(self.chk_mirror_orient)
        mirror_orient_row.addStretch(1)
        mirror_orient_row.addWidget(self.cmb_behavior)

        # The four toggles are one block, so they get their own tight layout
        # rather than the 8px that separates the sections of Setup Options.
        # Every row is FIELD_H tall (see style_checkbox), so one spacing
        # value keeps the gaps even, even though two rows carry a dropdown.
        checks_layout = QtWidgets.QVBoxLayout()
        checks_layout.setSpacing(2)
        checks_layout.addLayout(orient_row)
        checks_layout.addLayout(mirror_orient_row)
        checks_layout.addWidget(self.chk_mirror_joints)
        checks_layout.addWidget(self.chk_dryrun)
        options_layout.addLayout(checks_layout)
        options_layout.addSpacing(6)

        # Dropdowns: source side, mirror axis, aim axis, up axis
        self.cmb_source = self._combo(self.SIDES,
            'Authored side used as the mirror source; the other is '
            'overwritten. (MIRROR_SOURCE_SIDE)')
        self.cmb_axis = self._combo(self.AXES,
            "Character symmetry-plane normal: 'x' = YZ plane "
            '(left/right along X). (MIRROR_AXIS)')
        self.cmb_aim = self._combo(self.AXES,
            'Local axis aimed down each chain. (ORIENT_AIM_AXIS)')
        self.cmb_up = self._combo(self.AXES,
            'Local axis aligned to the chain plane normal. (ORIENT_UP_AXIS)')
        # Keep the dropdown rows close together in their own tight layout.
        combos_layout = QtWidgets.QVBoxLayout()
        combos_layout.setSpacing(2)
        combos_layout.addLayout(self._labeled_row('Mirror Source Side:', self.cmb_source))
        combos_layout.addLayout(self._labeled_row('Mirror Axis (plane normal):', self.cmb_axis))
        combos_layout.addLayout(self._labeled_row('Aim Axis (down chain):', self.cmb_aim))
        combos_layout.addLayout(self._labeled_row('Up Axis (plane normal):', self.cmb_up))
        options_layout.addLayout(combos_layout)

        # Visualize joint local axes (Maya displayLocalAxis) to check the
        # orient result. Acts immediately on toggle; not a saved setting.
        options_layout.addSpacing(6)
        self.chk_show_axes = QtWidgets.QCheckBox('Show Joint Local Axes')
        self.chk_show_axes.setToolTip(
            "Draw each BN joint's local X/Y/Z axes in the viewport "
            '(Maya displayLocalAxis) so the orient result is visible. '
            'Toggles immediately; does not change any orientation.')
        self.style_checkbox(self.chk_show_axes)
        self.chk_show_axes.toggled.connect(self.toggle_joint_axes)
        options_layout.addWidget(self.chk_show_axes)

        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)

        # Interactive roll fix-up, its own section below Setup Options: it is
        # not a saved setting and Run Setup never touches it. Type one or more
        # chains (or Select them from the viewport), set a step angle, and the
        # left/right arrows roll every listed chain about its aim axis
        # immediately, positions kept - left subtracts the step, right adds
        # it. Use it after the batch orient/mirror to turn wrong-facing chains
        # onto their plane.
        roll_group = self.create_group_box('Roll Chain (per-tail fix-up)')
        roll_layout = QtWidgets.QVBoxLayout()
        # Tight: the Chain and Roll rows read as one control, not two
        # sections.
        roll_layout.setSpacing(2)
        # The group-box style adds 'padding-top: 10px', which pads the top
        # only, so the bottom margin carries that 10 to keep the space above
        # the Chain row and below the Roll row equal.
        roll_layout.setContentsMargins(8, 4, 8, 14)
        # A textbox, not a dropdown: several tails can be rolled in one click
        # by listing them, which is what Select fills in from a multi-joint
        # selection.
        self.txt_roll_chain = QtWidgets.QLineEdit()
        self.txt_roll_chain.setPlaceholderText('rig part(s), comma separated')
        self.txt_roll_chain.setToolTip(
            'The tail chain(s) (RIGPARTS) to roll, comma separated - the '
            'arrows roll every one of them. Type the names, or select joints '
            'in the viewport and click Select to fill this in.')
        self.txt_roll_chain.setStyleSheet('''
            QLineEdit {
                background-color: #3a3a3a; color: #cccccc;
                border: 1px solid #555555; border-radius: 4px;
                padding: 2px 8px;
            }
            QLineEdit:focus { border-color: #F5D041; }
        ''')
        self.txt_roll_chain.setFixedHeight(self.FIELD_H)
        self.btn_roll_select = QtWidgets.QPushButton('Select')
        self.btn_roll_select.setToolTip(
            'Fill the Chain box from the current viewport selection: reads '
            'the rig part of every selected joint (any joint of a chain '
            'works, so selecting whole chains across several tails lists '
            'each tail once).')
        self.style_button(self.btn_roll_select, 0)
        self.btn_roll_select.setFixedSize(60, self.FIELD_H)
        self.btn_roll_select.clicked.connect(self.select_roll_chain)

        # Step angle: a whole-number degree amount the arrows add/subtract.
        # No up/down spin arrows - the left/right buttons drive it instead.
        self.spn_roll = QtWidgets.QSpinBox()
        self.spn_roll.setRange(0, 360)
        self.spn_roll.setValue(90)
        self.spn_roll.setSuffix(' deg')
        self.spn_roll.setAlignment(QtCore.Qt.AlignCenter)
        self.spn_roll.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spn_roll.setToolTip(
            'Step angle (whole degrees) the arrows roll by, e.g. 90. The '
            'left arrow rolls the listed chains by minus this, the right '
            'arrow by plus this, about the aim axis. Positions never change.')
        self.spn_roll.setStyleSheet(self.FIELD_STYLE)
        self.spn_roll.setFixedHeight(self.FIELD_H)
        self.btn_roll_minus = QtWidgets.QToolButton()
        self.btn_roll_minus.setArrowType(QtCore.Qt.LeftArrow)
        self.btn_roll_minus.setToolTip(
            'Roll every listed chain by MINUS the step, now (immediate; '
            'ignores Dry Run). Unbinds those chains\' geometry and clears the '
            'stored rest pose for the build to redo.')
        self.btn_roll_plus = QtWidgets.QToolButton()
        self.btn_roll_plus.setArrowType(QtCore.Qt.RightArrow)
        self.btn_roll_plus.setToolTip(
            'Roll every listed chain by PLUS the step, now (immediate; '
            'ignores Dry Run). Unbinds those chains\' geometry and clears the '
            'stored rest pose for the build to redo.')
        for btn in (self.btn_roll_minus, self.btn_roll_plus):
            btn.setStyleSheet('''
                QToolButton {
                    background-color: #3a3a3a; color: #cccccc;
                    border: 1px solid #555555; border-radius: 4px;
                }
                QToolButton:hover { background-color: #4a4a4a; border-color: #666666; }
                QToolButton:pressed { background-color: #2a2a2a; }
            ''')
            btn.setFixedSize(self.ARROW_W, self.FIELD_H)
        self.btn_roll_minus.clicked.connect(lambda: self.apply_roll(-1))
        self.btn_roll_plus.clicked.connect(lambda: self.apply_roll(1))

        # Both rows use the same leading label width and then fill the rest,
        # so the Chain and Roll blocks span an identical width and their
        # outer edges line up even though their contents differ.
        chain_row = QtWidgets.QHBoxLayout()
        chain_label = QtWidgets.QLabel('Chain:')
        chain_label.setMinimumWidth(self.LABEL_W)
        chain_row.addWidget(chain_label)
        chain_row.addWidget(self.txt_roll_chain, 1)
        chain_row.addWidget(self.btn_roll_select)
        roll_layout.addLayout(chain_row)
        roll_row = QtWidgets.QHBoxLayout()
        roll_label = QtWidgets.QLabel('Roll (deg):')
        roll_label.setMinimumWidth(self.LABEL_W)
        roll_row.addWidget(roll_label)
        roll_row.addWidget(self.btn_roll_minus)
        roll_row.addWidget(self.spn_roll, 1)
        roll_row.addWidget(self.btn_roll_plus)
        roll_layout.addLayout(roll_row)
        roll_group.setLayout(roll_layout)
        main_layout.addWidget(roll_group)
        main_layout.addSpacing(8)

        # Buttons --------------------------------------------------------
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        self.btn_cancel = QtWidgets.QPushButton('Cancel')
        self.btn_run = QtWidgets.QPushButton('Run Setup')
        self.btn_cancel.setToolTip('Close without running setup.')
        self.btn_run.setToolTip(
            'Detect the BN skeleton and run the enabled orientation steps. '
            'Dry Run only logs; otherwise joints are re-oriented (positions '
            'kept) and affected geometry is unbound for the build to rebind.')
        self.btn_cancel.clicked.connect(self.close)
        self.btn_run.clicked.connect(self.run_setup)
        self.style_button(self.btn_cancel, 0)
        self.style_button(self.btn_run, 1)
        button_layout.addWidget(self.btn_cancel)
        button_layout.addWidget(self.btn_run)
        main_layout.addLayout(button_layout)

    def _combo(self, items, tip):
        '''A settings dropdown, at the shared field size.'''
        combo = QtWidgets.QComboBox()
        combo.addItems(items)
        combo.setToolTip(tip)
        combo.setStyleSheet(self.FIELD_STYLE)
        combo.setFixedSize(self.FIELD_W, self.FIELD_H)
        combo.currentIndexChanged.connect(self.update_display)
        return combo

    def _labeled_row(self, label_text, widget):
        '''
        Label on the left, widget flush right at its fixed width.

        The stretch between them is what aligns every settings dropdown into
        one column: the rows carry labels of different lengths, so anchoring
        the widgets to the right edge lines them up where anchoring them
        after the label would not.
        '''
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(label_text)
        label.setMinimumWidth(self.LABEL_W)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(widget)
        return row

    # STYLING ==========================================================

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
        if style == 1:  # olive green (primary) - matches Build Rig in the
                        # Build UI, and complements its burnt orange
            button.setStyleSheet('''
                QPushButton {
                    background-color: #6B7A45; color: white; border: none;
                    border-radius: 4px; padding: 8px 16px; font-weight: bold;
                }
                QPushButton:hover { background-color: #7C8C52; }
                QPushButton:pressed { background-color: #525E33; }
            ''')
        elif style == 2:  # yellow (used by the shared RIGPARTS editor)
            button.setStyleSheet('''
                QPushButton {
                    background-color: #F5D041; color: #555555; border: none;
                    border-radius: 4px; padding: 8px 16px;
                }
                QPushButton:hover { background-color: #ECBE0C; }
                QPushButton:pressed { background-color: #A58509; }
            ''')
        else:  # grey
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
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
        ''')
        # Every checkbox row is FIELD_H tall, the same as the rows that
        # carry a dropdown (Mirror Orient). A bare checkbox is shorter than
        # its combo, so without this the layout spacing lands on rows of
        # different heights and the gaps read as uneven.
        checkbox.setMinimumHeight(self.FIELD_H)

    # STATE ============================================================

    def _combo_set(self, combo, value, default_index=0):
        idx = combo.findText(str(value))
        combo.setCurrentIndex(idx if idx >= 0 else default_index)

    def load_current_values(self):
        '''Refresh every field from rig_tail_constants.'''
        self.chk_orient.setChecked(bool(rt_constants.ORIENT_JOINTS))
        self.chk_mirror_orient.setChecked(bool(rt_constants.MIRROR_ORIENT))
        self.chk_mirror_joints.setChecked(bool(rt_constants.MIRROR_JOINTS))
        self.chk_dryrun.setChecked(bool(rt_constants.MIRROR_DRYRUN))
        self._combo_set(self.cmb_source, rt_constants.MIRROR_SOURCE_SIDE)
        self._combo_set(self.cmb_up_mode,
            str(rt_constants.ORIENT_UP_MODE).capitalize())
        self._sync_up_mode_enabled(self.chk_orient.isChecked())
        self._combo_set(self.cmb_behavior,
            str(rt_constants.MIRROR_BEHAVIOR).capitalize())
        self._sync_behavior_enabled(self.chk_mirror_orient.isChecked())
        self._combo_set(self.cmb_axis, rt_constants.MIRROR_AXIS)
        self._combo_set(self.cmb_aim, rt_constants.ORIENT_AIM_AXIS)
        self._combo_set(self.cmb_up, rt_constants.ORIENT_UP_AXIS)
        self.update_display()

    def save_current_values(self):
        '''Write the UI state into rig_tail_constants.'''
        rt_constants.ORIENT_JOINTS = self.chk_orient.isChecked()
        rt_constants.MIRROR_ORIENT = self.chk_mirror_orient.isChecked()
        rt_constants.MIRROR_JOINTS = self.chk_mirror_joints.isChecked()
        rt_constants.MIRROR_DRYRUN = self.chk_dryrun.isChecked()
        rt_constants.MIRROR_SOURCE_SIDE = self.cmb_source.currentText()
        rt_constants.ORIENT_UP_MODE = self.cmb_up_mode.currentText().lower()
        rt_constants.MIRROR_BEHAVIOR = self.cmb_behavior.currentText().lower()
        rt_constants.MIRROR_AXIS = self.cmb_axis.currentText()
        rt_constants.ORIENT_AIM_AXIS = self.cmb_aim.currentText()
        rt_constants.ORIENT_UP_AXIS = self.cmb_up.currentText()

    def _sync_behavior_enabled(self, checked):
        '''Grey the Mirror Behavior combo out unless Mirror Orient is on.'''
        self.cmb_behavior.setEnabled(bool(checked))
        self.update_display()

    def _sync_up_mode_enabled(self, checked):
        '''Grey the Up Mode combo out unless Orient Joints is on.'''
        self.cmb_up_mode.setEnabled(bool(checked))
        self.update_display()

    def _roll_chains(self):
        '''The rig parts typed into the Chain box, in order, de-duplicated.'''
        names = []
        for name in self.txt_roll_chain.text().replace(';', ',').split(','):
            name = name.strip()
            if name and name not in names:
                names.append(name)
        return names

    def _mirror_pairs(self):
        '''(pairs, unpaired-sided) preview using the selected source side.'''
        prev = rt_constants.MIRROR_SOURCE_SIDE
        rt_constants.MIRROR_SOURCE_SIDE = self.cmb_source.currentText()
        try:
            import rig_tail_setup as rt_setup
            # Active parts only, so the preview matches what will run: an
            # excluded side breaks its pair.
            pairs, _ = rt_setup.find_mirror_pairs(rt_setup._active())
        except Exception:
            pairs = []
        finally:
            rt_constants.MIRROR_SOURCE_SIDE = prev
        return pairs

    def _summary_line(self, label, value):
        '''One 'label = value' summary line, padded into the value column.'''
        return f'{label:<{self.SUMMARY_W}} = {value}'

    @staticmethod
    def _tick(checked, text):
        '''A toggle as a ticked box plus its name, e.g. '[x] Dry Run'.'''
        return f'[{"x" if checked else " "}] {text}'

    def update_display(self):
        self.txt_config.setText(rt_constants.LOADED_CONFIG or '')
        parts = list(rt_constants.RIGPARTS)
        excluded = [p for p in parts
                    if p in set(rt_constants.RIGPARTS_EXCLUDE or [])]

        # Same list form as the Builder's summary, so a part reads the same
        # in both windows. Labelled EXCLUDE, not RIGPARTS_EXCLUDE, to keep
        # the value column near the left edge.
        lines = [
            self._summary_line('ROOT', f"'{rt_constants.ROOT}'"),
            self._summary_line('RIGPARTS', parts),
        ]
        if excluded:
            lines.append(self._summary_line(
                'EXCLUDE',
                f'{excluded}   ({len(parts) - len(excluded)} of '
                f'{len(parts)} active)'))

        # The four toggles as ticked boxes in two columns, so the whole set
        # reads at a glance in two lines. Up Mode and Mirror Behavior only
        # shape their own operation, so each rides on that box and
        # disappears when it is off.
        up_mode = (f' ({self.cmb_up_mode.currentText().lower()})'
                   if self.chk_orient.isChecked() else '')
        behavior = (f' ({self.cmb_behavior.currentText().lower()})'
                    if self.chk_mirror_orient.isChecked() else '')
        left = [self._tick(self.chk_orient.isChecked(),
                           f'Orient Joints{up_mode}'),
                self._tick(self.chk_mirror_joints.isChecked(), 'Mirror Joints')]
        right = [self._tick(self.chk_mirror_orient.isChecked(),
                            f'Mirror Orient{behavior}'),
                 self._tick(self.chk_dryrun.isChecked(), 'Dry Run')]
        # Second column starts past the longest first-column cell, which
        # grows and shrinks with the '(cascade)' suffix.
        col = max(len(cell) for cell in left) + 2
        lines.append('Setup:')
        lines += [f'  {cell:<{col}}{other}' for cell, other in zip(left, right)]
        lines += [
            'Axis:',
            f'  AIM = {self.cmb_aim.currentText()}  '
            f'UP = {self.cmb_up.currentText()}  '
            f'MIRROR = {self.cmb_axis.currentText()}',
        ]

        # One pair per line under its own heading, sources padded to a
        # common width so the arrows line up under each other.
        pairs = self._mirror_pairs()
        if pairs:
            width = max(len(s) for s, _ in pairs)
            lines.append('L/R pairs:')
            lines += [f'  {src:<{width}} -> {tgt}' for src, tgt in pairs]
        else:
            lines.append('L/R pairs: (none)')
        self.txt_display.setText('\n'.join(lines))

    # ACTIONS ==========================================================

    def open_rigparts_editor(self):
        '''Reuse the Builder's RIGPARTS editor.'''
        dialog = rt_build_ui.RigPartsEditor(self, phase='Setup')
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def config_start_path(self):
        '''Config path to preselect in file dialogs: the textbox path if
        one is typed/displayed, otherwise the default CONFIG_FILE.'''
        return self.txt_config.text().strip() or rt_constants.CONFIG_FILE

    def load_config_path(self, filepath):
        '''Load the given config file and refresh the UI.'''
        if rt_constants.load_config(filepath):
            self.load_current_values()
            QtWidgets.QMessageBox.information(
                self, 'Success', f'Configuration loaded from:\n{filepath}')
        else:
            QtWidgets.QMessageBox.warning(
                self, 'Warning', f'Failed to load configuration from:\n{filepath}')

    def load_config(self):
        '''Import configuration from a user-chosen JSON config file.'''
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Load Config', self.config_start_path(),
            'JSON Files (*.json);;All Files (*)')
        if filepath:
            self.load_config_path(filepath)

    def load_config_from_text(self):
        '''Load the config path typed into the config textbox (Enter).'''
        filepath = self.txt_config.text().strip()
        if not filepath:
            return
        if not os.path.isfile(filepath):
            QtWidgets.QMessageBox.warning(
                self, 'Warning', f'Config file not found:\n{filepath}')
            return
        self.load_config_path(filepath)

    def save_config(self):
        '''Export configuration to a user-chosen JSON config file.'''
        self.save_current_values()
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save Config', self.config_start_path(),
            'JSON Files (*.json);;All Files (*)')
        if not filepath:
            return
        if rt_constants.save_config(filepath):
            self.update_display()
            QtWidgets.QMessageBox.information(
                self, 'Success', f'Configuration saved to:\n{filepath}')
        else:
            QtWidgets.QMessageBox.critical(
                self, 'Error', f'Failed to save configuration to:\n{filepath}')

    def toggle_joint_axes(self, checked):
        '''Show/hide the BN joints' local rotation axes in the viewport.'''
        import rig_tail_setup as rt_setup
        try:
            n = rt_setup.show_joint_orients(bool(checked))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, 'Error',
                f'Could not toggle joint axes:\n{str(e)}')
            return
        if checked and not n:
            QtWidgets.QMessageBox.information(self, 'No joints',
                'No BN joints found to display. Check RIGPARTS and that the '
                'skeleton is in the scene.')

    def select_roll_chain(self):
        '''Fill the Chain box from the current viewport selection.

        Every selected joint contributes its rig part, so selecting joints
        across several tails lists them all - each tail once, however many of
        its joints are selected.'''
        import rig_tail_setup as rt_setup
        try:
            rignames = rt_setup.rignames_from_selection()
        except Exception as e:
            cmds.warning(f'Roll: could not read selection: {e}')
            return
        if not rignames:
            cmds.warning('Roll: no rig part in the selection. Select a joint '
                         'of each tail chain, then click Select.')
            return
        self.txt_roll_chain.setText(', '.join(rignames))

    def apply_roll(self, sign):
        '''
        Roll every listed chain by the step angle, immediately.

        sign is +1 (right arrow, add) or -1 (left arrow, subtract). No
        confirmation dialog - the roll just applies; feedback goes to the
        Script Editor (rig_tail_setup.roll_chain logs it per chain). Non-modal
        warnings cover an empty box, an unknown name or a zero step so a stray
        click is harmless. One bad name does not stop the rest: each chain is
        rolled on its own and failures are reported at the end, so a typo in a
        list of four still rolls the other three.
        '''
        import rig_tail_setup as rt_setup

        rignames = self._roll_chains()
        if not rignames:
            cmds.warning('Roll: no chain in the Chain box. Type a rig part '
                         'name, or select joints and click Select.')
            return
        unknown = [n for n in rignames if n not in rt_constants.RIGPARTS]
        if unknown:
            cmds.warning(f'Roll: not in RIGPARTS: {", ".join(unknown)}. Check '
                         'the spelling in the Chain box.')
            return
        angle = self.spn_roll.value() * sign
        if not angle:
            return

        # Persist the aim/up axes the roll uses, so it matches the dropdowns.
        self.save_current_values()
        failed = []
        for rigname in rignames:
            try:
                if not rt_setup.roll_chain(rigname, angle):
                    failed.append(rigname)
            except Exception as e:
                cmds.warning(f'Roll failed on {rigname}: {e}')
                failed.append(rigname)
        if failed:
            cmds.warning(f'Roll: no BN chain rolled for: {", ".join(failed)}. '
                         'Check RIGPARTS and that the skeleton is in the scene.')

    def run_setup(self):
        '''Commit options and run the Setup phase on the skeleton.'''
        import rig_tail_setup as rt_setup

        if not rt_constants.RIGPARTS:
            QtWidgets.QMessageBox.warning(self, 'Error',
                'RIGPARTS is empty. Add rig parts first.')
            return
        if not rt_setup._active():
            QtWidgets.QMessageBox.warning(self, 'Error',
                'Every rig part is excluded, so Setup has nothing to do. '
                "Move at least one part back to Include in 'Edit Rig Parts'.")
            return

        self.save_current_values()
        dry = self.chk_dryrun.isChecked()

        if not (self.chk_orient.isChecked()
                or self.chk_mirror_orient.isChecked()
                or self.chk_mirror_joints.isChecked()):
            # No batch operation selected: Setup has nothing to do. Close the
            # window rather than run - a real run would unbind geometry and
            # clear the rest pose up front (before the flag check in
            # run_setup), which is purely destructive with no orient/mirror
            # to justify it. (Roll Chain is a separate, immediate action.)
            QtWidgets.QMessageBox.warning(self, 'Setup not run',
                'None of Orient Joints, Mirror Orient or Mirror Joints was '
                'selected, so Setup was not run. Re-open Setup and enable at '
                'least one operation (or use Roll Chain for a single-chain '
                'fix-up).')
            self.close()
            return

        try:
            # ROOT is not needed here: the Setup phase detects BN chains by
            # RIGPARTS naming, not by the rig root. Build sets ROOT.
            result = rt_setup.setup_tails(root=None, dry_run=dry)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error',
                f'Setup failed:\n{str(e)}')
            return

        # One line in the dialog, the full account in the Script Editor:
        # every case below is already logged in detail by the Setup phase,
        # so repeating it here only buries the counts that matter.
        counts = [
            (result.get('created'), 'chain(s) created'),
            (result.get('superseded'), 'chain(s) rebuilt from the source'),
            (result.get('marked'), f"joint(s) marked '{rt_setup.STRAY_SUFFIX}'"),
            (result.get('incomplete'), 'not a rig part, blocking a rebuild'),
            (result.get('reparented'), 'chain(s) reparented'),
            (result.get('duplicates'), 'ambiguous, skipped'),
            (result.get('unresolved'), 'chain(s) not reconciled'),
            (result.get('missing_chains'), 'chain(s) missing'),
            (result.get('missing_geo'), 'without geometry'),
            (result.get('excluded'), 'excluded'),
        ]
        notes = [f'{len(items)} {label}' for items, label in counts if items]
        detail = f" - {', '.join(notes)}" if notes else ''
        preview = ' Preview only, nothing changed.' \
            if result.get('dry_run') else ''
        summary = (f"Oriented {result.get('oriented', 0)} joints, mirrored "
                   f"{result.get('mirrored', 0)}{detail}.{preview} ")
        # A part Setup had to hold back is the one result that must not read
        # as success: the counts alone look like an ordinary partial run.
        ambiguous = result.get('duplicates') or []
        unresolved = result.get('unresolved') or []
        blocked = result.get('incomplete') or []
        held_back = ambiguous + [p for p in unresolved + blocked
                                 if p not in ambiguous]
        if held_back:
            where = f" The chains involved are in '{rt_setup.REVIEW_SET}'." \
                if ambiguous else ''
            QtWidgets.QMessageBox.warning(self, 'Setup incomplete',
                f'{summary}\n\nThese rig parts were left untouched because '
                'Setup could not tell which chain they meant, or where the '
                f"mirrored chain belongs: {', '.join(held_back)}.{where} "
                'See the Script Editor for what to fix.')
        else:
            QtWidgets.QMessageBox.information(self, 'Setup complete',
                f'{summary}See the Script Editor for details.')
        self.update_display()
        # Close on a real run, like the Builder does; keep the window up
        # after a dry run so the previewed settings can be run for real.
        # Options were saved above, and the window reads them back on open.
        if not result.get('dry_run'):
            self.close()

    def closeEvent(self, event):
        # Persist option state on close, like the Builder does
        self.save_current_values()
        super().closeEvent(event)


def get_maya_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def show_ui():
    '''Show the Tail Setup window, closing any previous instance.'''
    global rig_tail_setup_window
    try:
        rig_tail_setup_window.close()
        rig_tail_setup_window.deleteLater()
    except Exception:
        pass
    parent = get_maya_window()
    rig_tail_setup_window = RigTailSetupUI(parent=parent)
    rig_tail_setup_window.show()
    return rig_tail_setup_window
