'''
# rig_tail_ui.py
author: Daisy Jane @gnitemouse

PySide2 UI for Rig Tail
Compatible with Maya 2024/2025.
Maya 2024/2025 ships with Qt5, not Qt6.

Main window (RigTailUI) shows the current configuration, the build
options, and buttons that open pop-up editors:
  - RigPartsEditor: edit RIGPARTS or fill it from selected joints
  - NamingTemplateEditor: edit naming templates per section
    (type labels, controls/joints, groups, curves/clusters, spline,
    ikfk attributes)
  - ConstantsEditor: edit numeric constants (control counts and sizes)

Three buttons along the bottom: Remove Rig strips an existing rig back
to skeleton + geometry (destructive, confirms first), Build Rig keeps
unchanged tails as they are (the joint cache decides), and Force Rebuild
tears everything down first. The force flag lasts exactly one click - it
is never persisted, so a saved config can never leave every build
forcing. The other options, including Preserve skinClusters
(PRESERVE_SKIN), are committed to rig_tail_constants on build AND on
close, so they survive reopening.

All edited values live in rig_tail_constants and can be imported or
exported through a user-chosen JSON config file (Load/Save Config).
'''

import os

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from shiboken2 import wrapInstance
from PySide2 import QtWidgets, QtCore, QtGui
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam

class RigTailUI(QtWidgets.QDialog):
    '''Main Tail Rig Builder window.'''

    # Label column of the configuration summary's list settings, sized to
    # its longest label so their values line up. The counts and control
    # sizes below keep their own inline layout.
    SUMMARY_W = len('IKFK_MODES')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Rig Tail')
        self.setMinimumWidth(500)
        self.setup_ui()
        self.load_current_values()
        # Fit height to content so no dead space is left under the buttons
        self.resize(500, self.sizeHint().height())

    def setup_ui(self):
        '''Build the main window layout.'''
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 10, 15, 10)

        # Title and its one-line descriptor read as one heading, so they sit
        # in their own tight layout instead of taking the main 10px spacing.
        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(2)
        title = QtWidgets.QLabel('TAIL RIG BUILDER')
        title.setStyleSheet('font-size: 18px; font-weight: bold; color: #FFFFFF;')
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(title)

        subtitle = QtWidgets.QLabel('Build FK / IK / spline controls on the tail skeleton')
        subtitle.setStyleSheet('font-size: 10px; color: #999999;')
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        title_layout.addWidget(subtitle)
        main_layout.addLayout(title_layout)

        author = QtWidgets.QLabel('author Daisy Jane @gnitemouse')
        author.setStyleSheet('font-size: 10px; font-weight: normal; color: #4A90E2;')
        author.setAlignment(QtCore.Qt.AlignRight)
        main_layout.addWidget(author)

        display_group = self.create_group_box('Current Configuration')
        display_layout = QtWidgets.QVBoxLayout()

        self.txt_display = QtWidgets.QTextEdit()
        self.txt_display.setReadOnly(True)
        self.txt_display.setMaximumHeight(120)
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
        self.txt_display.setToolTip('Summary of build configuration')
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

        options_group = self.create_group_box('Build Options')
        options_layout = QtWidgets.QVBoxLayout()
        options_layout.setSpacing(8)

        root_layout = QtWidgets.QHBoxLayout()
        root_label = QtWidgets.QLabel('Root Name:')
        root_label.setMinimumWidth(100)
        self.txt_root = QtWidgets.QLineEdit()
        self.txt_root.setPlaceholderText('e.g. tail_root_grp')
        self.txt_root.setToolTip(
            'Name of the rig root group (ROOT). A trailing group label '
            'is stripped, e.g. tail_root_grp -> tail_root.')
        self.txt_root.editingFinished.connect(self.apply_root_name)
        self.txt_root.setStyleSheet('''
            QLineEdit {
                background-color: #3a3a3a;
                color: #cccccc;
                font-size: 11px;
                border: 1px solid #4A90E2;
                border-radius: 4px;
                padding: 4px 6px;
            }
            QLineEdit:focus {
                border-color: #F5D041;
            }
        ''')
        root_layout.addWidget(root_label)
        root_layout.addWidget(self.txt_root)
        options_layout.addLayout(root_layout)
        options_layout.addSpacing(8)

        # Build: two rows of two columns.
        #   Row 1: FK          Indiv FK (requires FK)
        #   Row 2: IK          Stretchy (requires IK)
        self.chk_fk = QtWidgets.QCheckBox('FK')
        self.chk_ik = QtWidgets.QCheckBox('IK')
        self.chk_indiv_fk = QtWidgets.QCheckBox('Indiv FK')
        self.chk_stretchy = QtWidgets.QCheckBox('Stretchy')
        # getattr defaults: reopening the tool reloads the code modules but
        # deliberately not rig_tail_constants (it holds the session state),
        # so a constant added after this session started may be missing
        # from the cached module. Fall back rather than crash the window.
        self.chk_fk.setChecked(getattr(rt_cst, 'BUILD_FK', True))
        self.chk_ik.setChecked(getattr(rt_cst, 'BUILD_IK', True))
        self.chk_indiv_fk.setChecked(rt_cst.INDIV_FK)
        self.chk_stretchy.setChecked(True)
        # First-column boxes share a width so the second column aligns
        self.chk_fk.setMinimumWidth(52)
        self.chk_ik.setMinimumWidth(52)
        self.chk_fk.toggled.connect(self.on_build_mode_changed)
        self.chk_ik.toggled.connect(self.on_build_mode_changed)
        self.chk_fk.setToolTip(
            'Build the FK chain: variable-FK sliding controls with '
            'rotation falloff. At least one of FK/IK must stay checked.')
        self.chk_ik.setToolTip(
            'Build the IK chain: spline IK with clusters, plus IK and '
            'Float control modes. At least one of FK/IK must stay checked.')
        self.chk_indiv_fk.setToolTip(
            'Also build an individual FK control at each joint (nested '
            'along the chain) for direct per-joint rotation, on top of '
            'the variable-FK sliding controls. Requires FK.')
        self.chk_stretchy.setToolTip(
            'Build the squash & stretch network. Requires IK: the '
            'stretch nodes read the ikfk switch attribute.')
        self.style_checkbox(self.chk_fk)
        self.style_checkbox(self.chk_ik)
        self.style_checkbox(self.chk_indiv_fk, sub=True)
        self.style_checkbox(self.chk_stretchy, sub=True)

        build_row1 = QtWidgets.QHBoxLayout()
        build_label = QtWidgets.QLabel('Build:')
        build_label.setMinimumWidth(100)
        build_row1.addWidget(build_label)
        build_row1.addWidget(self.chk_fk)
        build_row1.addWidget(self.chk_indiv_fk)
        build_row1.addStretch()
        options_layout.addLayout(build_row1)

        build_row2 = QtWidgets.QHBoxLayout()
        build_empty_label = QtWidgets.QLabel('')
        build_empty_label.setMinimumWidth(100)
        build_row2.addWidget(build_empty_label)
        build_row2.addWidget(self.chk_ik)
        build_row2.addWidget(self.chk_stretchy)
        build_row2.addStretch()
        options_layout.addLayout(build_row2)
        options_layout.addSpacing(8)

        features_layout = QtWidgets.QHBoxLayout()
        features_label = QtWidgets.QLabel('Features:')
        features_label.setMinimumWidth(100)
        self.chk_wave = QtWidgets.QCheckBox('Wave')
        self.chk_curl = QtWidgets.QCheckBox('Curl')
        self.chk_noise = QtWidgets.QCheckBox('Noise')
        self.chk_loop = QtWidgets.QCheckBox('Loop')
        self.chk_wave.setChecked(False)
        self.chk_curl.setChecked(False)
        self.chk_noise.setChecked(False)
        self.chk_loop.setChecked(False)
        self.chk_wave.setToolTip(
            'Add animatable wave attributes: a traveling sine ripple '
            'along the tail (amplitude/frequency per axis).')
        self.chk_curl.setToolTip(
            'Add animatable curl attributes: roll the tail up around '
            'its base (curl X/Y/Z).')
        self.chk_noise.setToolTip(
            'Add animatable noise attributes: random jitter on the '
            'joints for organic motion.')
        self.chk_loop.setToolTip(
            'Add a looping time driver so wave/curl/noise effects cycle '
            'seamlessly over the timeline.')
        self.style_checkbox(self.chk_wave)
        self.style_checkbox(self.chk_curl)
        self.style_checkbox(self.chk_noise)
        self.style_checkbox(self.chk_loop)
        features_layout.addWidget(features_label)
        features_layout.addWidget(self.chk_wave)
        features_layout.addWidget(self.chk_curl)
        features_layout.addWidget(self.chk_noise)
        features_layout.addWidget(self.chk_loop)
        features_layout.addStretch()
        options_layout.addLayout(features_layout)
        options_layout.addSpacing(8)

        toggles_layout = QtWidgets.QHBoxLayout()
        self.chk_main = QtWidgets.QCheckBox('Control All Tails (cog)')
        self.chk_main.setEnabled(len(rt_cst.RIGPARTS) > 1)
        self.chk_main.setToolTip(
            'Drive every tail from one ALL section on the cog, with a '
            'per-tail Override flag to opt out. Needs 2+ rig parts.')
        self.chk_preserve = QtWidgets.QCheckBox('Preserve skinClusters')
        self.chk_preserve.setToolTip(
            'Keep existing skinClusters when rebuilding: rig joints are '
            'added to the cluster (new ones at weight 0) and painted '
            'weights survive. Off unbinds and rebinds from scratch, '
            'losing the weights.')
        self.style_checkbox(self.chk_main)
        self.style_checkbox(self.chk_preserve)
        # Stretch shares of the row's slack: 3/20 before Control All
        # Tails, 13/20 between, 4/20 after. Leading + middle still comes
        # to 80%, the same as the previous 4:12:4, so nudging Control All
        # Tails left moves only that checkbox - Preserve skinClusters
        # keeps its position and the gap between them widens.
        toggles_layout.addStretch(3)
        toggles_layout.addWidget(self.chk_main)
        toggles_layout.addStretch(13)
        toggles_layout.addWidget(self.chk_preserve)
        toggles_layout.addStretch(4)
        options_layout.addLayout(toggles_layout)

        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)

        editors_group = self.create_group_box('Configuration Editor')
        editors_layout = QtWidgets.QVBoxLayout()
        editors_layout.setSpacing(4)

        editor_buttons = [
            ('Edit Rig Parts', lambda: self.open_rigparts_editor(),
             'Edit RIGPARTS: the list of {rigname} components to rig, '
             'one tail per entry. Can also be filled from selected joints.'),
            ('Edit Naming: Type Labels', lambda: self.open_naming_editor('types'),
             'Edit the naming labels used as suffixes in every template '
             '(grp, ctrl, jnt, sdk, ...).'),
            ('Edit Naming: Controls, Joints', lambda: self.open_naming_editor('controls'),
             'Edit naming templates for control curves, their offset '
             'groups, and joints.'),
            ('Edit Naming: Groups', lambda: self.open_naming_editor('groups'),
             'Edit naming templates for the rig hierarchy groups and '
             'SDK nodes.'),
            ('Edit Naming: Curves, Clusters', lambda: self.open_naming_editor('curves'),
             'Edit naming templates for curves, curveInfo, clusters, and '
             'up-vector controls.'),
            ('Edit Naming: Spline', lambda: self.open_naming_editor('spline'),
             'Edit naming templates for spline IK handle, effector, and '
             'spline mode controls.'),
            ('Edit Naming: IKFK, Switch, Divider', lambda: self.open_naming_editor('ikfk'),
             'Edit the ikfk attribute name, switch modes, and channel '
             'box divider attributes.'),
            ('Edit Constants: Number of Controls', lambda: self.open_constants_editor('num'),
             'Edit how many FK and IK controls are built along the tail.'),
            ('Edit Constants: Control Size', lambda: self.open_constants_editor('size'),
             'Edit the size of each control curve.'),
        ]

        for btn_text, btn_func, btn_tip in editor_buttons:
            btn = QtWidgets.QPushButton(btn_text)
            btn.clicked.connect(btn_func)
            btn.setToolTip(btn_tip)
            self.style_button(btn, 0)
            editors_layout.addWidget(btn)

        editors_group.setLayout(editors_layout)
        main_layout.addWidget(editors_group)
        main_layout.addSpacing(10)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(7)

        self.btn_remove = QtWidgets.QPushButton('Remove Rig')
        self.btn_force = QtWidgets.QPushButton('Force Rebuild')
        self.btn_build = QtWidgets.QPushButton('Build Rig')
        self.btn_remove.setToolTip(
            'Delete the whole rig, leaving only the posed skeleton and '
            'the geometry still bound to it. Not an undo: asks first.')
        self.btn_force.setToolTip(
            'Rebuild everything from scratch, ignoring the cache that '
            'normally leaves unchanged tails alone.')
        self.btn_build.setToolTip(
            'Build with the settings above, reusing tails whose joints '
            'have not changed since the last build.')

        # Lambdas so Qt's clicked(checked) bool cannot land in `force`
        self.btn_remove.clicked.connect(lambda: self.remove_rig())
        self.btn_force.clicked.connect(lambda: self.build_rig(force=True))
        self.btn_build.clicked.connect(lambda: self.build_rig())

        # Remove Rig wears the same grey outline as Setup's Cancel: it is
        # the step back, not the loud one. Force Rebuild carries the
        # burnt orange warning, Build Rig the olive primary.
        self.style_button(self.btn_remove, 0)
        self.style_button(self.btn_force, 3)
        self.style_button(self.btn_build, 4)

        button_layout.addWidget(self.btn_remove)
        button_layout.addWidget(self.btn_force)
        button_layout.addWidget(self.btn_build)

        main_layout.addLayout(button_layout)

    def create_group_box(self, title):
        group = QtWidgets.QGroupBox(title)
        group.setStyleSheet('''
            QGroupBox {
                font-weight: bold;
                border: 2px solid #555555;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        ''')
        return group

    def style_button(self, button, style):
        if style == 0: # grey
            button.setStyleSheet('''
                QPushButton {
                    background-color: #3a3a3a;
                    color: #cccccc;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 12px;
                }
                QPushButton:hover {
                    background-color: #4a4a4a;
                    border-color: #666666;
                }
                QPushButton:pressed {
                    background-color: #2a2a2a;
                }
            ''')
        elif style == 1: # blue
            button.setStyleSheet('''
                QPushButton {
                    background-color: #4166F5;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-weight: bold;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #4153F5;
                }
                QPushButton:pressed {
                    background-color: #304EB8;
                }
            ''')
        elif style == 2: # yellow
            button.setStyleSheet('''
                QPushButton {
                    background-color: #F5D041;
                    color: #555555;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-weight: normal;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #ECBE0C;
                }
                QPushButton:pressed {
                    background-color: #A58509;
                }
            ''')
        elif style == 3: # burnt orange (Force Rebuild)
            button.setStyleSheet('''
                QPushButton {
                    background-color: #8F3B12;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-weight: normal;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #A6461A;
                }
                QPushButton:pressed {
                    background-color: #66290C;
                }
            ''')
        elif style == 4: # olive green (primary action)
            button.setStyleSheet('''
                QPushButton {
                    background-color: #6B7A45;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-weight: bold;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #7C8C52;
                }
                QPushButton:pressed {
                    background-color: #525E33;
                }
            ''')
        button.setMinimumHeight(32)

    def style_checkbox(self, checkbox, sub=False):
        '''
        Style a build-option checkbox. sub=True marks a secondary option
        (Indiv FK, Stretchy) with lighter grey text so it reads as a
        sub-option of the primary above it. The disabled color is set
        explicitly, otherwise the stylesheet text color would override
        Qt's disabled dimming and a disabled sub-option would look enabled.
        '''
        style = '''
            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        '''
        if sub:
            style += '''
            QCheckBox {
                color: #999999;
            }
            QCheckBox:disabled {
                color: #5a5a5a;
            }
            '''
        checkbox.setStyleSheet(style)

    def load_current_values(self):
        '''Refresh UI fields and checkboxes from rig_tail_constants.'''
        self.txt_root.setText(rt_cst.ROOT)
        self.chk_indiv_fk.setChecked(rt_cst.INDIV_FK)
        self.chk_indiv_fk.setEnabled(self.chk_fk.isChecked())
        self.chk_stretchy.setChecked(rt_cst.EFFECTS.get('stretchy', False))
        self.chk_wave.setChecked(rt_cst.EFFECTS.get('wave', False))
        self.chk_curl.setChecked(rt_cst.EFFECTS.get('curl', False))
        self.chk_noise.setChecked(rt_cst.EFFECTS.get('noise', False))
        self.chk_loop.setChecked(rt_cst.EFFECTS.get('loop', False))
        self.chk_fk.setChecked(getattr(rt_cst, 'BUILD_FK', True))
        self.chk_ik.setChecked(getattr(rt_cst, 'BUILD_IK', True))
        # getattr: a session started before PRESERVE_SKIN existed has a
        # stale constants module without it (constants are never reloaded)
        self.chk_preserve.setChecked(getattr(rt_cst, 'PRESERVE_SKIN', True))
        self.chk_main.setChecked(rt_cst.MAIN_CONTROLLER)
        self.update_display()

    def save_current_values(self):
        '''
        Snapshot the main-window checkboxes into rig_tail_constants.

        The RIGPARTS / naming / size editors already write to the constants
        module when their OK is clicked, so those survive reopening the
        window. The main-window checkboxes had no such path - they were
        read only at build time - so they reset on reopen. Called from
        closeEvent so any close persists them, not only a build.

        Conditioned the same way as the build: stretchy needs IK (its
        network reads the ikfk switch attribute), and individual FK needs
        FK, so an unreachable combination is never stored.
        '''
        fk = self.chk_fk.isChecked()
        ik = self.chk_ik.isChecked()
        setattr(rt_cst, 'BUILD_FK', fk)
        setattr(rt_cst, 'BUILD_IK', ik)
        rt_cst.INDIV_FK = self.chk_indiv_fk.isChecked() and fk
        rt_cst.PRESERVE_SKIN = self.chk_preserve.isChecked()
        rt_cst.MAIN_CONTROLLER = self.chk_main.isChecked()
        rt_cst.EFFECTS = {
            'stretchy': self.chk_stretchy.isChecked() and ik,
            'wave': self.chk_wave.isChecked(),
            'curl': self.chk_curl.isChecked(),
            'noise': self.chk_noise.isChecked(),
            'loop': self.chk_loop.isChecked(),
            }

    def closeEvent(self, event):
        '''Persist the checkboxes before the window goes away.'''
        try:
            self.save_current_values()
        except Exception:
            # Never let a persistence error prevent the window closing
            pass
        super().closeEvent(event)

    def _summary_line(self, label, value):
        '''One 'label = value' summary line, padded into the value column.'''
        return f'{label:<{self.SUMMARY_W}} = {value}'

    def update_display(self):
        '''Refresh the config-file textbox and configuration summary.'''
        self.txt_config.setText(rt_cst.LOADED_CONFIG or '')
        parts = list(rt_cst.RIGPARTS)
        lines = [
            self._summary_line('ROOT', f"'{rt_cst.ROOT}'"),
            self._summary_line('RIGPARTS', parts),
        ]
        # Excluded parts are not built, so say so here rather than leaving
        # RIGPARTS reading as the build list it no longer is. Labelled
        # EXCLUDE, not RIGPARTS_EXCLUDE, to keep the value column near the
        # left edge.
        excluded = [p for p in parts
                    if p in set(getattr(rt_cst, 'RIGPARTS_EXCLUDE', None) or [])]
        if excluded:
            lines.append(self._summary_line(
                'EXCLUDE',
                f'{excluded}   ({len(parts) - len(excluded)} of '
                f'{len(parts)} built)'))
        display_text = '\n'.join(lines + [
            self._summary_line('IKFK_MODES', rt_cst.IKFK_MODES),
            f'NUM_CTRL_FK = {rt_cst.NUM_CTRL_FK}   NUM_CTRL_IK = {rt_cst.NUM_CTRL_IK}',
            'Control Sizes:',
            f'  ROOT = {rt_cst.ROOT_CTRL_SZ}  COG = {rt_cst.COG_CTRL_SZ}  BASE = {rt_cst.BASE_CTRL_SZ}',
            f'  FK = {rt_cst.VARFK_CTRL_SZ}  {rt_cst.FK_CTRL_SZ}    IK = {rt_cst.IK_CTRL_SZ}',
            f'  SPLINE_BOT = {rt_cst.SPLINE_BOT_SZ}  MID = {rt_cst.SPLINE_MID_SZ}  TOP = {rt_cst.SPLINE_TOP_SZ}',
        ])
        self.txt_display.setText(display_text)
        self.chk_main.setEnabled(len(rt_cst.RIGPARTS) > 1)

    def apply_root_name(self):
        '''Commit the Root Name textbox to rt_cst.ROOT (stripping a
        trailing group label) so the configuration summary and Save
        Config always reflect what is typed. A name already used by an
        unrelated scene node is refused: the build would adopt that
        node as the rig root group. The root group of a previous build
        is still allowed, so renaming ROOT across rebuilds keeps
        working.'''
        root = self.txt_root.text().strip()
        if root:
            stripped = rt_nam.strip_group_suffix(root)
            if stripped != rt_cst.ROOT:
                import rig_tail_cleanup as rt_cln
                if cmds.objExists(stripped) and \
                        stripped != rt_cln.find_existing_root_grp():
                    QtWidgets.QMessageBox.warning(
                        self, 'Invalid Root Name',
                        f"'{stripped}' is already a node in the scene. "
                        "The build would take over that node as the rig "
                        "root group. Choose a name not used by an "
                        "existing node.")
                    self.txt_root.setText(rt_cst.ROOT)
                    return
                rt_cst.ROOT = stripped
                self.update_display()

    def on_build_mode_changed(self):
        '''
        Enforce build mode rules:
        - At least one of FK/IK stays checked (unchecking the last one
          is reverted).
        - Stretchy requires IK: the stretch network reads the ikfk
          switch attribute, which only exists when IK is built.
        - IKFK_MODES follows the build options: IK-only drops 'FK' from
          the switch modes, re-checking FK restores it (the same check
          also runs at build time in setup_rig).
        '''
        if not self.chk_fk.isChecked() and not self.chk_ik.isChecked():
            sender = self.sender()
            if sender in (self.chk_fk, self.chk_ik):
                sender.blockSignals(True)
                sender.setChecked(True)
                sender.blockSignals(False)
        fk = self.chk_fk.isChecked()
        ik = self.chk_ik.isChecked()
        self.chk_stretchy.setEnabled(ik)
        if not ik:
            self.chk_stretchy.setChecked(False)
        # Individual FK controls require FK
        self.chk_indiv_fk.setEnabled(fk)
        if not fk:
            self.chk_indiv_fk.setChecked(False)
        if rt_cst.update_ikfk_modes(fk, ik):
            self.update_display()

    def config_start_path(self):
        '''Config path to preselect in file dialogs: the textbox path if
        one is typed/displayed, otherwise the default CONFIG_FILE.'''
        return self.txt_config.text().strip() or rt_cst.CONFIG_FILE

    def load_config_path(self, filepath):
        '''Load the given config file and refresh the UI.'''
        if rt_cst.load_config(filepath):
            # Reconcile the loaded mode list with the build checkboxes
            rt_cst.update_ikfk_modes(self.chk_fk.isChecked(),
                                     self.chk_ik.isChecked())
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
        self.apply_root_name()
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save Config', self.config_start_path(),
            'JSON Files (*.json);;All Files (*)')
        if not filepath:
            return
        if rt_cst.save_config(filepath):
            self.update_display()
            QtWidgets.QMessageBox.information(
                self, 'Success', f'Configuration saved to:\n{filepath}')
        else:
            QtWidgets.QMessageBox.critical(
                self, 'Error', f'Failed to save configuration to:\n{filepath}')

    def open_rigparts_editor(self):
        '''Open the RIGPARTS pop-up editor.'''
        dialog = RigPartsEditor(self, phase='Build')
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def open_naming_editor(self, section):
        '''Open the naming template pop-up editor for the given section.'''
        dialog = NamingTemplateEditor(section, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            if section == 'ikfk':
                # Re-derive the active modes from the edited
                # IKFK_MODES_ALL for the current build checkboxes
                rt_cst.update_ikfk_modes(self.chk_fk.isChecked(),
                                         self.chk_ik.isChecked())
            self.update_display()

    def open_constants_editor(self, section):
        '''Open the numeric constants pop-up editor for the given section.'''
        dialog = ConstantsEditor(section, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def build_rig(self, force=False):
        '''
        Apply the UI options to rig_tail_constants and build the rig.

        force=True (the Force Rebuild button) tears every included part
        down and rebuilds it even when its joints are unchanged since the
        last build; the default keeps unchanged tails as they are.
        '''
        import rig_tail as rt

        self.apply_root_name()
        # Pass the raw text through; rt_cln.set_root strips the group
        # label and renames the previous root group in the scene
        root = self.txt_root.text().strip() or None

        if not rt_cst.RIGPARTS:
            QtWidgets.QMessageBox.warning(self, 'Error',
                'RIGPARTS is empty. Add rig parts first.')
            return

        # Every part excluded means the build has nothing to do; say so
        # here rather than letting it run through and report success
        import rig_tail_cache as rt_cache
        parts = rt_cache.active_parts()
        if not parts:
            QtWidgets.QMessageBox.warning(self, 'Error',
                'Every rig part is Excluded, so there is nothing to build.\n'
                "Move at least one part back to Include in 'Edit Rig Parts'.")
            return

        fk = self.chk_fk.isChecked()
        ik = self.chk_ik.isChecked()
        if not fk and not ik:
            QtWidgets.QMessageBox.warning(self, 'Error',
                'Select at least FK or IK to build.')
            return

        # Warn about rig parts with no joints instead of failing mid-build
        import rig_tail_cleanup as rt_cln
        missing = [p for p in parts if not rt_cln.rigpart_has_joints(p)]
        if missing:
            QtWidgets.QMessageBox.warning(self, 'Missing Joints',
                'No BN joints found for: ' + ', '.join(missing) + '.\n'
                'Add joints matching the naming template, or Exclude/remove '
                'these parts in RIGPARTS, then build again.')
            return

        # Commit the checkbox state (BUILD_FK/IK, INDIV_FK, PRESERVE_SKIN,
        # MAIN_CONTROLLER, EFFECTS) the same way closeEvent does. The
        # force flag is per-click, not a setting: it lasts exactly one
        # build and is never persisted.
        self.save_current_values()
        rt_cst.FORCE_REBUILD = force

        try:
            rt.rig_tail_multiple(root=root, fk=fk, ik=ik)
            QtWidgets.QMessageBox.information(self, 'Success', 'Rig built successfully!')
            # Close on success only: a failed build leaves the window up so
            # the settings that produced it can be corrected and retried.
            # Every setting was written to rig_tail_constants above, and the
            # window reads them back on construction, so closing loses
            # nothing (see show_ui).
            self.close()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Failed to build rig:\n{str(e)}')

    def remove_rig(self):
        '''
        Strip the rig back to skeleton + geometry (the Remove Rig button).

        Confirms first: this deletes controls and their animation, and
        Maya's undo is not a reliable way back from a teardown this
        large. The scene is left with the posed BN skeleton and the
        geometry still bound to it, ready to build again or hand on.
        '''
        import rig_tail_cleanup as rt_cln

        # A session started before this feature existed holds a stale
        # rig_tail_cleanup (modules are only reloaded by TailReload), and
        # the call below would die with a bare AttributeError
        if not hasattr(rt_cln, 'remove_rig'):
            QtWidgets.QMessageBox.warning(self, 'Remove Rig',
                'This Maya session is running an older rig_tail_cleanup.\n'
                'Run the TailReload shelf button, then try again.')
            return

        root_grp = rt_cln.find_existing_root_grp()
        if not root_grp:
            QtWidgets.QMessageBox.information(self, 'Remove Rig',
                'No built rig found in this scene.')
            return

        answer = QtWidgets.QMessageBox.warning(
            self, 'Remove Rig',
            f"Delete the rig under '{root_grp}'?\n\n"
            'Controls, curves, clusters, FX networks and the FK/IK joint '
            'chains are deleted, along with any animation on them.\n\n'
            'The BN skeleton is kept in its current pose and the geometry '
            'stays bound to it.\n\nThis cannot be reliably undone.',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel)
        if answer != QtWidgets.QMessageBox.Yes:
            return

        try:
            # One undo chunk, so a failed teardown is not left half-applied
            cmds.undoInfo(openChunk=True, chunkName='rigTail Remove Rig')
            try:
                removed = rt_cln.remove_rig()
            finally:
                cmds.undoInfo(closeChunk=True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error',
                f'Failed to remove rig:\n{str(e)}')
            return

        if removed:
            QtWidgets.QMessageBox.information(self, 'Remove Rig',
                'Rig removed. The skeleton and geometry are still here.')
            self.update_display()
        else:
            QtWidgets.QMessageBox.warning(self, 'Remove Rig',
                'Nothing was removed - no rig root group was found.')


class RigPartsEditor(QtWidgets.QDialog):
    '''
    Pop-up editor for RIGPARTS, with an Include / Exclude split.

    Rig parts can be added/removed manually or filled from the current
    joint selection via 'Get RIGPARTS from Selected Joints'. Rename
    renames the part in the scene immediately (not deferred to build), so
    the list and the scene node names never drift apart.

    Two lists side by side, moved between with the arrow buttons or by
    double-clicking an entry, in the manner of Maya's channel editor.
    Excluded parts stay in RIGPARTS - they keep their name, stay
    renameable, and still resolve for L/R pairing - they are simply left
    alone: Setup does not orient or mirror them and leaves their geometry
    bound, and the build neither tears their rig down nor rebuilds it.
    See rt_cst.RIGPARTS_EXCLUDE.

    The editor is shared by both windows, so `phase` names the caller
    ('Setup' or 'Build') for the wording that would otherwise have to
    describe both at once.
    '''

    def __init__(self, parent=None, phase='Setup'):
        super().__init__(parent)
        self.phase = phase
        self.setWindowTitle('Edit Rig Parts')
        self.setMinimumSize(380, 380)
        self.setup_ui()

    LIST_STYLE = '''
        QListWidget {
            background-color: #2b2b2b;
            color: #cccccc;
            border: 1px solid #555555;
            border-radius: 4px;
            padding: 2px;
        }
        QListWidget::item {
            padding: 2px 4px;
        }
        QListWidget::item:selected {
            background-color: #4A90E2;
        }
    '''

    # The move column is just two arrows: keep it as narrow as the glyphs.
    ARROW_W = 20
    ARROW_H = 22
    BUTTON_H = 26

    def setup_ui(self):
        '''Build the dialog layout.'''
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        excluded = set(getattr(rt_cst, 'RIGPARTS_EXCLUDE', None) or [])
        self.list_widget = self._make_list(
            [p for p in rt_cst.RIGPARTS if p not in excluded],
            f'Rig parts {self.phase} will process.')
        self.list_exclude = self._make_list(
            [p for p in rt_cst.RIGPARTS if p in excluded],
            'Rig parts held back from both Setup and Build. They keep their '
            'place in RIGPARTS and their geometry stays bound; Setup leaves '
            'their joints alone, and the build neither tears their rig down '
            'nor rebuilds it.\n'
            'Use this to freeze a finished tail while the rest of the '
            'roster is iterated on.')
        # Double-click sends an entry to the other side, like the channel
        # editor. Move buttons handle multi-selection.
        self.list_widget.itemDoubleClicked.connect(
            lambda _: self.move_selected(to_exclude=True))
        self.list_exclude.itemDoubleClicked.connect(
            lambda _: self.move_selected(to_exclude=False))

        self.btn_to_exclude = QtWidgets.QToolButton()
        self.btn_to_exclude.setArrowType(QtCore.Qt.RightArrow)
        self.btn_to_exclude.setToolTip('Exclude the selected rig part(s) '
                                       'from Setup and Build.')
        self.btn_to_include = QtWidgets.QToolButton()
        self.btn_to_include.setArrowType(QtCore.Qt.LeftArrow)
        self.btn_to_include.setToolTip('Include the selected rig part(s) '
                                       'in Setup and Build again.')
        for btn in (self.btn_to_exclude, self.btn_to_include):
            btn.setStyleSheet('''
                QToolButton {
                    background-color: #3a3a3a; color: #cccccc;
                    border: 1px solid #555555; border-radius: 3px;
                }
                QToolButton:hover { background-color: #4a4a4a; border-color: #666666; }
                QToolButton:pressed { background-color: #2a2a2a; }
            ''')
            btn.setFixedSize(self.ARROW_W, self.ARROW_H)
        self.btn_to_exclude.clicked.connect(
            lambda: self.move_selected(to_exclude=True))
        self.btn_to_include.clicked.connect(
            lambda: self.move_selected(to_exclude=False))

        move_col = QtWidgets.QVBoxLayout()
        move_col.setSpacing(4)
        move_col.setContentsMargins(2, 0, 2, 0)
        move_col.addStretch()
        move_col.addWidget(self.btn_to_exclude)
        move_col.addWidget(self.btn_to_include)
        move_col.addStretch()

        self.lbl_include = QtWidgets.QLabel()
        self.lbl_exclude = QtWidgets.QLabel()
        include_col = QtWidgets.QVBoxLayout()
        include_col.setSpacing(3)
        include_col.addWidget(self.lbl_include)
        include_col.addWidget(self.list_widget)
        exclude_col = QtWidgets.QVBoxLayout()
        exclude_col.setSpacing(3)
        exclude_col.addWidget(self.lbl_exclude)
        exclude_col.addWidget(self.list_exclude)

        lists_layout = QtWidgets.QHBoxLayout()
        lists_layout.setSpacing(0)
        lists_layout.addLayout(include_col, 1)
        lists_layout.addLayout(move_col)
        lists_layout.addLayout(exclude_col, 1)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(4)
        self.btn_add = QtWidgets.QPushButton('Add')
        self.btn_rename = QtWidgets.QPushButton('Rename')
        self.btn_remove = QtWidgets.QPushButton('Remove')
        self.btn_add.setToolTip(
            'Add a new rig part name to the list. Warns if no matching '
            'joints exist in the scene yet.')
        self.btn_rename.setToolTip(
            'Rename the selected rig part immediately in the scene: every '
            'node belonging to the part is renamed and RIGPARTS is updated '
            'to match. Reverts to the old name if the rename fails.')
        self.btn_remove.setToolTip('Delete the selected rig part from the list.')
        self.btn_add.clicked.connect(self.add_item)
        self.btn_rename.clicked.connect(self.rename_item)
        self.btn_remove.clicked.connect(self.remove_item)
        # Compact: these are small list actions, not primary buttons, so
        # they keep the dialog narrow instead of stretching across it.
        for btn in (self.btn_add, self.btn_rename, self.btn_remove):
            self.parent().style_button(btn, 0)
            btn.setFixedHeight(self.BUTTON_H)
            btn.setMinimumWidth(0)
            btn_layout.addWidget(btn, 1)

        self.btn_get_rigparts = QtWidgets.QPushButton('Get from Selected Joints')
        self.btn_get_rigparts.setToolTip(
            'Replace both lists with {rigname}s extracted from the joints '
            'selected in the scene (joints must follow the JOINT template). '
            'Everything lands in Include; any existing exclusion is cleared.')
        self.btn_get_rigparts.clicked.connect(self.get_rigparts_from_selection)
        self.parent().style_button(self.btn_get_rigparts, 2)
        self.btn_get_rigparts.setFixedHeight(self.BUTTON_H)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        # Named for the window that opened the editor: both phases honour
        # the exclusion, and this is the one place a user checks before
        # relying on it, so it states the caller's own behaviour rather
        # than describing both at once.
        hint = QtWidgets.QLabel(
            f'{self.phase} runs on Included parts only.')
        hint.setStyleSheet('color: #999999; font-size: 10px;')
        hint.setWordWrap(True)

        layout.addLayout(lists_layout)
        layout.addWidget(hint)
        layout.addLayout(btn_layout)
        layout.addWidget(self.btn_get_rigparts)
        layout.addWidget(button_box)
        self._refresh_counts()

    def _make_list(self, items, tooltip):
        '''One styled, multi-select list column.'''
        widget = QtWidgets.QListWidget()
        widget.addItems(items)
        widget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        widget.setStyleSheet(self.LIST_STYLE)
        widget.setToolTip(tooltip)
        return widget

    def _refresh_counts(self):
        '''Keep the column headings showing the current counts.'''
        self.lbl_include.setText(
            f'Include ({self.list_widget.count()}):')
        self.lbl_exclude.setText(
            f'Exclude ({self.list_exclude.count()}):')

    def _current_list(self):
        '''
        The list Add/Rename/Remove should act on: whichever holds the
        current selection, preferring the focused one when both do.
        '''
        if self.list_exclude.hasFocus() and self.list_exclude.selectedItems():
            return self.list_exclude
        if self.list_widget.selectedItems():
            return self.list_widget
        if self.list_exclude.selectedItems():
            return self.list_exclude
        return self.list_widget

    def move_selected(self, to_exclude):
        '''Move the selected entries to the other column.'''
        source = self.list_widget if to_exclude else self.list_exclude
        target = self.list_exclude if to_exclude else self.list_widget
        # Take from the bottom up so the rows above keep their indices,
        # then add in the original top-down order so a multi-selection does
        # not arrive reversed.
        rows = sorted(source.row(item) for item in source.selectedItems())
        taken = [source.takeItem(row) for row in reversed(rows)]
        for item in reversed(taken):
            target.addItem(item)
        self._refresh_counts()

    def get_rigparts_from_selection(self):
        '''Replace both lists with rig names extracted from selected joints.'''
        selected = cmds.ls(selection=True, type='joint')
        if not selected:
            QtWidgets.QMessageBox.warning(self, 'Warning', 'No joints selected.')
            return

        rignames = list()
        for jnt in selected:
            rigname = rt_nam.get_rigname(jnt, rt_cst.JOINT)
            if rigname:
                rignames.append(rigname)
            else:
                rignames.append(jnt)

        # A full reset of the roster, so any previous exclusion goes too -
        # keeping it would silently hold back a part the user just picked.
        self.list_widget.clear()
        self.list_exclude.clear()
        self.list_widget.addItems(sorted(rignames))
        self._refresh_counts()

    def add_item(self):
        '''Prompt for a new rig part name and append it to Include.'''
        text, ok = QtWidgets.QInputDialog.getText(self, 'Add Rig Part', 'Enter rig part name:')
        if not (ok and text):
            return
        text = text.strip()
        if not text:
            return
        self.list_widget.addItem(text)
        self._refresh_counts()
        # Validate/warn: an added name with no joints builds nothing
        import rig_tail_cleanup as rt_cln
        if not rt_cln.rigpart_has_joints(text):
            QtWidgets.QMessageBox.warning(self, 'Missing Joints',
                f"No BN joints found for rig part '{text}'.")

    def rename_item(self):
        '''
        Rename the selected rig part immediately in the scene.

        The scene rename happens now (not on OK): the backend swaps the
        rigname in every node for the part and updates RIGPARTS/caches, so
        the list and the scene never drift. A failed rename is reverted and
        the old name is kept.
        '''
        widget = self._current_list()
        item = widget.currentItem()
        if item is None or not item.isSelected():
            QtWidgets.QMessageBox.warning(self, 'Warning', 'No rig part selected.')
            return
        old = item.text()
        new, ok = QtWidgets.QInputDialog.getText(
            self, 'Rename Rig Part', f"Rename '{old}' to:",
            QtWidgets.QLineEdit.Normal, old)
        if not ok:
            return
        new = new.strip()
        if not new or new == old:
            return

        import rig_tail_cleanup as rt_cln
        success, message = rt_cln.rename_rigpart(old, new)
        if success:
            item.setText(new)
            # The scene rename is already committed, so keep the stored
            # exclusion in step even if the dialog is cancelled afterwards -
            # otherwise it would still name a part that no longer exists.
            stored = getattr(rt_cst, 'RIGPARTS_EXCLUDE', None) or []
            rt_cst.RIGPARTS_EXCLUDE = [new if p == old else p for p in stored]
            # Backend already updated RIGPARTS/ROOT/caches; refresh main UI
            if self.parent():
                self.parent().load_current_values()
            QtWidgets.QMessageBox.information(self, 'Renamed', message)
        else:
            QtWidgets.QMessageBox.warning(self, 'Rename Failed', message)

    def remove_item(self):
        '''Delete the selected rig part(s) from whichever column holds them.'''
        widget = self._current_list()
        for item in sorted(widget.selectedItems(), key=widget.row, reverse=True):
            widget.takeItem(widget.row(item))
        self._refresh_counts()

    def accept(self):
        '''
        Commit both columns to rt_cst.RIGPARTS / RIGPARTS_EXCLUDE.

        RIGPARTS keeps every name, included first then excluded, so the
        roster survives an exclusion intact; RIGPARTS_EXCLUDE records which
        of them the Setup phase should skip.
        '''
        included = [self.list_widget.item(i).text()
                    for i in range(self.list_widget.count())]
        excluded = [self.list_exclude.item(i).text()
                    for i in range(self.list_exclude.count())]
        rt_cst.RIGPARTS = included + excluded
        rt_cst.RIGPARTS_EXCLUDE = excluded
        super().accept()


class NamingTemplateEditor(QtWidgets.QDialog):
    '''
    Pop-up editor for one section of the naming templates.

    Most fields are plain string templates set directly on
    rig_tail_constants. The 'ikfk' section also edits list/tuple
    attribute templates: those are shown as comma-separated values and
    parsed back on OK. IKFK_SWITCH's enum string is derived from
    IKFK_MODES, so it is not edited directly; it is rebuilt via
    rt_cst.rebuild_derived() whenever the dialog is accepted.
    '''

    # Human-readable titles per section key
    SECTION_TITLES = {
        'types': 'Type Labels',
        'controls': 'Controls, Joints',
        'groups': 'Groups',
        'curves': 'Curves, Clusters',
        'spline': 'Spline',
        'ikfk': 'IKFK, Switch, Divider',
    }

    # Attrs edited as comma-separated values: name -> expected item count
    # (None = any number of items)
    LIST_FIELDS = {'IKFK_MODES': None}
    TUPLE_FIELDS = {
        'IKFK_SWITCH': 3,  # (longName, niceName, dv); enum from IKFK_MODES
        'IKFK_DIVIDER': 3,
        'STRETCH_DIVIDER': 3,
        'ANIM_DIVIDER': 3,
        'TWIST_DIVIDER': 3,
        'SCALE_DIVIDER': 3,
    }

    def __init__(self, section, parent=None):
        super().__init__(parent)
        self.section = section
        title = self.SECTION_TITLES.get(section, section.title())
        self.setWindowTitle(f'Edit Naming: {title}')
        self.setMinimumSize(500, 500)
        self.fields = {}
        self.setup_ui()

    def setup_ui(self):
        '''Build one line edit per template in this section.'''
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        if self.section == 'ikfk':
            note = QtWidgets.QLabel(
                'List/tuple values are comma-separated.\n'
                'IKFK_SWITCH is (longName, niceName, default index); its enum\n'
                'string is generated from IKFK_MODES.\n'
                'Dividers are (longName, niceName, enumLabel).\n')
            note.setStyleSheet('color: #999999; font-size: 10px;')
            layout.addWidget(note)

        form_layout = QtWidgets.QFormLayout()
        form_layout.setSpacing(8)

        templates = self.get_templates_for_section()
        tooltips = self.get_tooltips_for_section()

        for name, value in templates.items():
            line_edit = QtWidgets.QLineEdit(value)
            line_edit.setStyleSheet('''
                QLineEdit {
                    background-color: #3a3a3a;
                    color: #cccccc;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px;
                }
            ''')
            if name in tooltips:
                line_edit.setToolTip(tooltips[name])
            self.fields[name] = line_edit
            form_layout.addRow(f'{name}:', line_edit)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setLayout(form_layout)
        scroll.setWidget(scroll_widget)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(scroll)
        layout.addWidget(button_box)

    def get_templates_for_section(self):
        '''Return {attr name: display string} for this section.'''
        if self.section == 'types':
            return {
                'GRP': rt_cst.GRP,
                'CTRL': rt_cst.CTRL,
                'JNT': rt_cst.JNT,
                'SDK': rt_cst.SDK,
                'CRV': rt_cst.CRV,
                'CSR': rt_cst.CSR,
                'HDL': rt_cst.HDL,
                'EFF': rt_cst.EFF,
                'VIS': rt_cst.VIS,
                'COND': rt_cst.COND,
                'CST': rt_cst.CST,
            }
        elif self.section == 'controls':
            return {
                'ROOT_CTRL': rt_cst.ROOT_CTRL,
                'COG_CTRL': rt_cst.COG_CTRL,
                'BASECTRL_GRP': rt_cst.BASECTRL_GRP,
                'BASECTRL': rt_cst.BASECTRL,
                'CTRLROOT_GRP': rt_cst.CTRLROOT_GRP,
                'CTRL_GRP': rt_cst.CTRL_GRP,
                'CONTROL': rt_cst.CONTROL,
                'JOINT': rt_cst.JOINT,
            }
        elif self.section == 'groups':
            return {
                'ROOT_GRP': rt_cst.ROOT_GRP,
                'GEOMETRY_GRP': rt_cst.GEOMETRY_GRP,
                'CONTROL_GRP': rt_cst.CONTROL_GRP,
                'SKELETON_GRP': rt_cst.SKELETON_GRP,
                'RIG_SYSTEMS_GRP': rt_cst.RIG_SYSTEMS_GRP,
                'CLUSTERS_GRP': rt_cst.CLUSTERS_GRP,
                'SCALE_GRP': rt_cst.SCALE_GRP,
                'SDK_GRP': rt_cst.SDK_GRP,
                'SDK_JNT': rt_cst.SDK_JNT,
                'GROUP': rt_cst.GROUP,
            }
        elif self.section == 'curves':
            return {
                'CURVE': rt_cst.CURVE,
                'CURVE_SCALE': rt_cst.CURVE_SCALE,
                'CURVEINFO': rt_cst.CURVEINFO,
                'CLUSTER_GRP': rt_cst.CLUSTER_GRP,
                'CLUSTER': rt_cst.CLUSTER,
                'CLUSTER_HANDLE': rt_cst.CLUSTER_HANDLE,
                'UPV_CTRLGRP': rt_cst.UPV_CTRLGRP,
                'UPV_CTRL': rt_cst.UPV_CTRL,
                'CLUSTER_UPV': rt_cst.CLUSTER_UPV,
                'CLUSTER_UPV_HANDLE': rt_cst.CLUSTER_UPV_HANDLE,
            }
        elif self.section == 'spline':
            return {
                'SPLINE_GRP': rt_cst.SPLINE_GRP,
                'SPLINE_HANDLE': rt_cst.SPLINE_HANDLE,
                'SPLINE_EFFECTOR': rt_cst.SPLINE_EFFECTOR,
                'SPLINE_IK_CTRL': rt_cst.SPLINE_IK_CTRL,
                'SPLINE_FLOAT_CTRL': rt_cst.SPLINE_FLOAT_CTRL,
                'SPLINE_BOT': rt_cst.SPLINE_BOT,
                'SPLINE_BOT_SML': rt_cst.SPLINE_BOT_SML,
                'SPLINE_MID_ROT': rt_cst.SPLINE_MID_ROT,
                'SPLINE_MID': rt_cst.SPLINE_MID,
                'SPLINE_TOP_SML': rt_cst.SPLINE_TOP_SML,
                'SPLINE_TOP': rt_cst.SPLINE_TOP,
            }
        elif self.section == 'ikfk':
            return {
                'IKFK': rt_cst.IKFK,
                # Edit the full list; the active subset (IKFK_MODES) is
                # re-derived from it for the current build options
                'IKFK_MODES': ', '.join(rt_cst.IKFK_MODES_ALL),
                # Show (longName, niceName, dv); enum derived from IKFK_MODES
                'IKFK_SWITCH': ', '.join([rt_cst.IKFK_SWITCH[0],
                                          rt_cst.IKFK_SWITCH[1],
                                          str(rt_cst.IKFK_SWITCH[3])]),
                'IKFK_DIVIDER': ', '.join(rt_cst.IKFK_DIVIDER),
                'STRETCH_DIVIDER': ', '.join(rt_cst.STRETCH_DIVIDER),
                'ANIM_DIVIDER': ', '.join(rt_cst.ANIM_DIVIDER),
                'TWIST_DIVIDER': ', '.join(rt_cst.TWIST_DIVIDER),
                'SCALE_DIVIDER': ', '.join(rt_cst.SCALE_DIVIDER),
            }
        return {}

    def get_tooltips_for_section(self):
        '''Return {attr name: tooltip} for this section.'''
        if self.section == 'ikfk':
            mode_lines = '\n'.join(
                f'{mode}: {desc}'
                for mode, desc in rt_cst.IKFK_MODE_DESCRIPTIONS.items())
            return {
                'IKFK': 'Name template of the per-tail switch attribute '
                        'on the cog control (e.g. tail_ikfk).',
                'IKFK_MODES': 'Modes offered by the IKFK switch:\n'
                              + mode_lines +
                              '\nModes are positional (1=SplineIK, 2=IK, '
                              '3=Float, 4=FK), so they can be renamed '
                              'freely. The FK mode is only offered when '
                              'FK is built alongside IK.',
                'IKFK_SWITCH': 'Switch attribute shown on every control: '
                               '(longName, niceName, default mode index).',
            }
        return {}

    def accept(self):
        '''Validate and commit all fields to rig_tail_constants.'''
        parsed = {}
        for name, line_edit in self.fields.items():
            text = line_edit.text()
            if name in self.LIST_FIELDS or name in self.TUPLE_FIELDS:
                items = [s.strip() for s in text.split(',') if s.strip()]
                count = self.TUPLE_FIELDS.get(name)
                if count is not None and len(items) != count:
                    QtWidgets.QMessageBox.warning(
                        self, 'Warning',
                        f'{name} needs {count} comma-separated values.')
                    return
                if name == 'IKFK_SWITCH':
                    try:
                        dv = int(items[2])
                    except ValueError:
                        QtWidgets.QMessageBox.warning(
                            self, 'Warning',
                            'IKFK_SWITCH default index must be an integer.')
                        return
                    # Placeholder enum; rebuild_derived() fills it from IKFK_MODES below
                    parsed[name] = (items[0], items[1], '', dv)
                elif name in self.TUPLE_FIELDS:
                    parsed[name] = tuple(items)
                elif name == 'IKFK_MODES':
                    if len(items) not in (3, 4):
                        QtWidgets.QMessageBox.warning(
                            self, 'Warning',
                            'IKFK_MODES needs 3 or 4 comma-separated '
                            'names (SplineIK, IK, Float and optionally '
                            'FK positions).')
                        return
                    # Commit to the full list; the active IKFK_MODES
                    # subset is derived from it per build options
                    parsed['IKFK_MODES_ALL'] = items
                else:
                    parsed[name] = items
            else:
                parsed[name] = text

        for name, value in parsed.items():
            setattr(rt_cst, name, value)
        rt_cst.rebuild_derived()
        super().accept()


class ConstantsEditor(QtWidgets.QDialog):
    '''
    Pop-up editor for numeric constants: control counts ('num') and
    control sizes ('size').
    '''

    # Which PRESERVE_CTRL control types each size constant governs.
    # Several sizes share a type (every spline control is sized from its
    # own constant but preserved as one set, and IK_CTRL_SZ builds both
    # the IK and Float sets), so their checkboxes are kept in sync.
    PRESERVE_KEYS = {
        'ROOT_CTRL_SZ': ['root'],
        'COG_CTRL_SZ': ['cog'],
        'BASE_CTRL_SZ': ['base'],
        'VARFK_CTRL_SZ': ['varfk'],
        'FK_CTRL_SZ': ['fk'],
        'IK_CTRL_SZ': ['ik', 'float'],
        'SPLINE_UPV_SZ': ['upvec'],
        'SPLINE_BOT_SZ': ['spline'],
        'SPLINE_BOT_SML_SZ': ['spline'],
        'SPLINE_MID_ROT_SZ': ['spline'],
        'SPLINE_MID_SZ': ['spline'],
        'SPLINE_TOP_SML_SZ': ['spline'],
        'SPLINE_TOP_SZ': ['spline'],
    }

    def __init__(self, section, parent=None):
        super().__init__(parent)
        self.section = section
        self.setWindowTitle(f'Edit Constants: {section.title()}')
        self.setMinimumSize(480, 440)
        self.fields = {}
        self.preserve_fields = {}
        self.setup_ui()

    def on_preserve_toggled(self, name, checked):
        '''
        Grey out the size this checkbox governs, and keep checkboxes that
        share a control type in step so the same flag cannot be shown
        checked on one row and unchecked on another.
        '''
        spinbox = self.fields.get(name)
        if spinbox:
            spinbox.setEnabled(not checked)

        keys = set(self.PRESERVE_KEYS.get(name, []))
        for other, box in self.preserve_fields.items():
            if other == name or not keys & set(self.PRESERVE_KEYS.get(other, [])):
                continue
            if box.isChecked() != checked:
                box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(False)
                other_spin = self.fields.get(other)
                if other_spin:
                    other_spin.setEnabled(not checked)

    def setup_ui(self):
        '''Build one spinbox per constant in this section.'''
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        if self.section == 'size':
            note = QtWidgets.QLabel(
                'Preserve keeps an existing control\'s curves as they are, '
                'so hand-tuned shapes survive a rebuild. The size next to it '
                'then has no effect and is greyed out. Controls that do not '
                'exist yet are always built from the size.')
            note.setWordWrap(True)
            note.setStyleSheet('color: #999999; padding-bottom: 6px;')
            layout.addWidget(note)

        form_layout = QtWidgets.QFormLayout()
        form_layout.setSpacing(8)

        constants = self.get_constants_for_section()

        for name, value in constants.items():
            if self.section == 'num':
                spinbox = QtWidgets.QSpinBox()
                spinbox.setRange(1, 20)
                spinbox.setValue(value)
            else:
                spinbox = QtWidgets.QDoubleSpinBox()
                spinbox.setRange(0.1, 100.0)
                spinbox.setDecimals(1)
                spinbox.setValue(value)

            spinbox.setStyleSheet('''
                QSpinBox, QDoubleSpinBox {
                    background-color: #3a3a3a;
                    color: #cccccc;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px;
                }
                QSpinBox:disabled, QDoubleSpinBox:disabled {
                    background-color: #333333;
                    color: #666666;
                    border: 1px solid #444444;
                }
            ''')
            self.fields[name] = spinbox

            keys = self.PRESERVE_KEYS.get(name) if self.section == 'size' else None
            if not keys:
                form_layout.addRow(f'{name}:', spinbox)
                continue

            checkbox = QtWidgets.QCheckBox('Preserve')
            checkbox.setChecked(bool(rt_cst.PRESERVE_CTRL.get(keys[0], False)))
            checkbox.setToolTip(
                f"Keep existing shapes for: {', '.join(keys)}.\n"
                f'{name} is ignored while this is checked.')
            checkbox.toggled.connect(
                lambda checked, n=name: self.on_preserve_toggled(n, checked))
            spinbox.setEnabled(not checkbox.isChecked())
            self.preserve_fields[name] = checkbox

            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(spinbox, 1)
            row_layout.addWidget(checkbox)
            form_layout.addRow(f'{name}:', row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setLayout(form_layout)
        scroll.setWidget(scroll_widget)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(scroll)
        layout.addWidget(button_box)

    def get_constants_for_section(self):
        '''Return {attr name: current value} for this section.'''
        if self.section == 'num':
            return {
                'NUM_CTRL_FK': rt_cst.NUM_CTRL_FK,
                'NUM_CTRL_IK': rt_cst.NUM_CTRL_IK,
            }
        elif self.section == 'size':
            return {
                'ROOT_CTRL_SZ': rt_cst.ROOT_CTRL_SZ,
                'COG_CTRL_SZ': rt_cst.COG_CTRL_SZ,
                'BASE_CTRL_SZ': rt_cst.BASE_CTRL_SZ,
                'VARFK_CTRL_SZ': rt_cst.VARFK_CTRL_SZ,
                'FK_CTRL_SZ': rt_cst.FK_CTRL_SZ,
                'IK_CTRL_SZ': rt_cst.IK_CTRL_SZ,
                'SPLINE_UPV_SZ': rt_cst.SPLINE_UPV_SZ,
                'SPLINE_BOT_SZ': rt_cst.SPLINE_BOT_SZ,
                'SPLINE_BOT_SML_SZ': rt_cst.SPLINE_BOT_SML_SZ,
                'SPLINE_MID_ROT_SZ': rt_cst.SPLINE_MID_ROT_SZ,
                'SPLINE_MID_SZ': rt_cst.SPLINE_MID_SZ,
                'SPLINE_TOP_SML_SZ': rt_cst.SPLINE_TOP_SML_SZ,
                'SPLINE_TOP_SZ': rt_cst.SPLINE_TOP_SZ,
            }
        return {}

    def accept(self):
        '''Commit all spinbox values and Preserve flags to rig_tail_constants.'''
        for name, spinbox in self.fields.items():
            setattr(rt_cst, name, spinbox.value())
        # Written into the existing dict so any control type without a
        # size constant of its own keeps its current setting
        for name, checkbox in self.preserve_fields.items():
            for key in self.PRESERVE_KEYS.get(name, []):
                rt_cst.PRESERVE_CTRL[key] = checkbox.isChecked()
        rt_cst.rebuild_derived()
        super().accept()

def get_maya_window():
    '''Return the Maya main window as a QWidget for parenting.'''
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)

def show_ui():
    '''
    Show the Tail Rig Builder, closing any previous instance.

    Settings are not held by the window. Every editor writes straight to
    rig_tail_constants, and the window reads them back when it is built,
    so closing and reopening keeps the current values and whichever
    config file was loaded.

    What does reset them is reloading rig_tail_constants, since that
    re-executes the module and runs load_config() again. Reopening with

        rig_tail.main()          (or rig_tail_ui.show_ui())

    keeps the session's settings; the usual development snippet

        il.reload(rig_tail); rig_tail.main()

    reloads every module, constants included, and starts from the config
    file or the module defaults.
    '''
    global rig_tail_window
    try:
        rig_tail_window.close()
        rig_tail_window.deleteLater()
    except:
        pass

    parent = get_maya_window()
    rig_tail_window = RigTailUI(parent=parent)
    rig_tail_window.show()
    return rig_tail_window
