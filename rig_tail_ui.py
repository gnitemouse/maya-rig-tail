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

        title = QtWidgets.QLabel('TAIL RIG BUILDER')
        title.setStyleSheet('font-size: 18px; font-weight: bold; color: #FFFFFF;')
        title.setAlignment(QtCore.Qt.AlignCenter)
        main_layout.addWidget(title)

        author = QtWidgets.QLabel('author Daisy Jane @gnitemouse')
        author.setStyleSheet('font-size: 10px; font-weight: normal; color: #4A90E2;')
        author.setAlignment(QtCore.Qt.AlignRight)
        main_layout.addWidget(author)

        display_group = self.create_group_box('Current Configuration')
        display_layout = QtWidgets.QVBoxLayout()

        self.txt_display = QtWidgets.QTextEdit()
        self.txt_display.setReadOnly(True)
        self.txt_display.setMaximumHeight(105)
        self.txt_display.setStyleSheet('''
            QTextEdit {
                background-color: #2b2b2b;
                color: #cccccc;
                font-family: Consolas, monospace;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 2px;
                padding: 0px 8px;
            }
        ''')
        self.txt_display.document().setDocumentMargin(4)
        self.txt_display.setToolTip('Summary of the configuration the next build will use.')
        display_layout.setContentsMargins(4, 2, 4, 2)
        display_layout.setSpacing(4)
        display_layout.addWidget(self.txt_display)

        self.txt_config = QtWidgets.QLineEdit()
        self.txt_config.setPlaceholderText('[Default Config]')
        self.txt_config.setStyleSheet('''
            QLineEdit {
                background-color: #2b2b2b;
                color: #4A90E2;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 2px;
                padding: 2px 6px;
            }
            QLineEdit:focus {
                border-color: #F5D041;
            }
        ''')
        self.txt_config.setToolTip(
            'Config file currently in effect ([Default Config] = built-in '
            'defaults). Type a path and press Enter to load it directly; '
            'Load/Save Config update it too.')
        self.txt_config.returnPressed.connect(self.load_config_from_text)
        display_layout.addWidget(self.txt_config)

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
        display_layout.addSpacing(6)

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        options_group = self.create_group_box('Build Options')
        options_layout = QtWidgets.QVBoxLayout()
        options_layout.setSpacing(10)

        root_layout = QtWidgets.QHBoxLayout()
        root_label = QtWidgets.QLabel('Root Name:')
        root_label.setMinimumWidth(100)
        self.txt_root = QtWidgets.QLineEdit()
        self.txt_root.setPlaceholderText('e.g. tail_root_grp')
        self.txt_root.setToolTip(
            'Name of the rig root group (ROOT). A trailing group label '
            'is stripped, e.g. tail_root_grp -> tail_root.')
        self.style_line_edit(self.txt_root)
        root_layout.addWidget(root_label)
        root_layout.addWidget(self.txt_root)
        options_layout.addLayout(root_layout)

        build_layout = QtWidgets.QHBoxLayout()
        build_label = QtWidgets.QLabel('Build:')
        build_label.setMinimumWidth(100)
        self.chk_fk = QtWidgets.QCheckBox('FK')
        self.chk_ik = QtWidgets.QCheckBox('IK')
        self.chk_fk.setChecked(True)
        self.chk_ik.setChecked(True)
        self.chk_fk.toggled.connect(self.on_build_mode_changed)
        self.chk_ik.toggled.connect(self.on_build_mode_changed)
        self.chk_fk.setToolTip(
            'Build the FK chain: variable-FK sliding controls with '
            'rotation falloff. At least one of FK/IK must stay checked.')
        self.chk_ik.setToolTip(
            'Build the IK chain: spline IK with clusters, plus IK and '
            'Float control modes. At least one of FK/IK must stay checked.')
        self.style_checkbox(self.chk_fk)
        self.style_checkbox(self.chk_ik)
        build_layout.addWidget(build_label)
        build_layout.addWidget(self.chk_fk)
        build_layout.addWidget(self.chk_ik)
        build_layout.addStretch()
        options_layout.addLayout(build_layout)

        features_layout = QtWidgets.QHBoxLayout()
        features_label = QtWidgets.QLabel('Features:')
        features_label.setMinimumWidth(100)
        self.chk_stretchy = QtWidgets.QCheckBox('Stretchy')
        self.chk_stretchy.setChecked(True)
        self.chk_stretchy.setToolTip(
            'Build the squash & stretch network. Requires IK: the '
            'stretch nodes read the ikfk switch attribute.')
        self.style_checkbox(self.chk_stretchy)
        features_layout.addWidget(features_label)
        features_layout.addWidget(self.chk_stretchy)
        features_layout.addStretch()
        options_layout.addLayout(features_layout)

        features_layout = QtWidgets.QHBoxLayout()
        empty_label = QtWidgets.QLabel('')
        empty_label.setMinimumWidth(100)
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
        features_layout.addWidget(empty_label)
        features_layout.addWidget(self.chk_wave)
        features_layout.addWidget(self.chk_curl)
        features_layout.addWidget(self.chk_noise)
        features_layout.addWidget(self.chk_loop)
        features_layout.addStretch()
        options_layout.addLayout(features_layout)

        toggles_layout = QtWidgets.QHBoxLayout()
        self.chk_main = QtWidgets.QCheckBox('Main Controller (multiple)')
        self.chk_main.setEnabled(len(rt_cst.RIGPARTS) > 1)
        self.chk_main.setToolTip(
            'Build one centralized dashboard control with proxy '
            'attributes (IKFK switch, IK Twist, Stretch, Animation '
            'effects) for every tail in RIGPARTS. Enabled when RIGPARTS '
            'has 2+ parts.')
        self.chk_force = QtWidgets.QCheckBox('Force Rebuild (ignore cache)')
        self.chk_force.setToolTip(
            'Tear the existing rig down completely and rebuild, even if '
            'the joints are unchanged since the last build.')
        self.style_checkbox(self.chk_main)
        self.style_checkbox(self.chk_force)
        toggles_layout.addWidget(self.chk_main)
        toggles_layout.addWidget(self.chk_force)
        options_layout.addLayout(toggles_layout)

        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)

        editors_group = self.create_group_box('Configuration Editor')
        editors_layout = QtWidgets.QVBoxLayout()
        editors_layout.setSpacing(6)

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

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        self.btn_cancel = QtWidgets.QPushButton('Cancel')
        self.btn_build = QtWidgets.QPushButton('Build Rig')
        self.btn_cancel.setToolTip('Close the window without building.')
        self.btn_build.setToolTip('Build the rig with the settings above.')

        self.btn_cancel.clicked.connect(self.close)
        self.btn_build.clicked.connect(self.build_rig)

        self.style_button(self.btn_cancel, 0)
        self.style_button(self.btn_build, 1)

        button_layout.addWidget(self.btn_cancel)
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
                }
                QPushButton:hover {
                    background-color: #ECBE0C;
                }
                QPushButton:pressed {
                    background-color: #A58509;
                }
            ''')
        button.setMinimumHeight(32)

    def style_line_edit(self, line_edit):
        line_edit.setStyleSheet('''
            QLineEdit {
                background-color: #3a3a3a;
                color: #cccccc;
                border: 1px solid #4A90E2;
                border-radius: 4px;
                padding: 6px;
            }
            QLineEdit:focus {
                border-color: #F5D041;
            }
        ''')
        line_edit.setMinimumHeight(32)

    def style_checkbox(self, checkbox):
        checkbox.setStyleSheet('''
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        ''')

    def load_current_values(self):
        '''Refresh UI fields and checkboxes from rig_tail_constants.'''
        self.txt_root.setText(rt_cst.ROOT)
        self.chk_stretchy.setChecked(rt_cst.EFFECTS.get('stretchy', False))
        self.chk_wave.setChecked(rt_cst.EFFECTS.get('wave', False))
        self.chk_curl.setChecked(rt_cst.EFFECTS.get('curl', False))
        self.chk_noise.setChecked(rt_cst.EFFECTS.get('noise', False))
        self.chk_loop.setChecked(rt_cst.EFFECTS.get('loop', False))
        self.chk_force.setChecked(rt_cst.FORCE_REBUILD)
        self.chk_main.setChecked(rt_cst.MAIN_CONTROLLER)
        self.update_display()

    def update_display(self):
        '''Refresh the config-file textbox and configuration summary.'''
        self.txt_config.setText(rt_cst.LOADED_CONFIG or '')
        display_text = '\n'.join([
            f"ROOT = '{rt_cst.ROOT}'",
            f'RIGPARTS = {rt_cst.RIGPARTS}',
            f'NUM_CTRL_FK = {rt_cst.NUM_CTRL_FK}   NUM_CTRL_IK = {rt_cst.NUM_CTRL_IK}',
            'Control Sizes:',
            f'  ROOT = {rt_cst.ROOT_CTRL_SZ}  COG = {rt_cst.COG_CTRL_SZ}  BASE = {rt_cst.BASE_CTRL_SZ}',
            f'  FK = {rt_cst.VARFK_CTRL_SZ}  {rt_cst.FK_CTRL_SZ}    IK = {rt_cst.IK_CTRL_SZ}',
            f'  SPLINE_BOT = {rt_cst.SPLINE_BOT_SZ}  MID = {rt_cst.SPLINE_MID_SZ}  TOP = {rt_cst.SPLINE_TOP_SZ}',
        ])
        self.txt_display.setText(display_text)
        self.chk_main.setEnabled(len(rt_cst.RIGPARTS) > 1)

    def on_build_mode_changed(self):
        '''
        Enforce build mode rules:
        - At least one of FK/IK stays checked (unchecking the last one
          is reverted).
        - Stretchy requires IK: the stretch network reads the ikfk
          switch attribute, which only exists when IK is built.
        '''
        if not self.chk_fk.isChecked() and not self.chk_ik.isChecked():
            sender = self.sender()
            if sender in (self.chk_fk, self.chk_ik):
                sender.blockSignals(True)
                sender.setChecked(True)
                sender.blockSignals(False)
        ik = self.chk_ik.isChecked()
        self.chk_stretchy.setEnabled(ik)
        if not ik:
            self.chk_stretchy.setChecked(False)

    def config_start_path(self):
        '''Config path to preselect in file dialogs: the textbox path if
        one is typed/displayed, otherwise the default CONFIG_FILE.'''
        return self.txt_config.text().strip() or rt_cst.CONFIG_FILE

    def load_config_path(self, filepath):
        '''Load the given config file and refresh the UI.'''
        if rt_cst.load_config(filepath):
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
        dialog = RigPartsEditor(self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def open_naming_editor(self, section):
        '''Open the naming template pop-up editor for the given section.'''
        dialog = NamingTemplateEditor(section, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def open_constants_editor(self, section):
        '''Open the numeric constants pop-up editor for the given section.'''
        dialog = ConstantsEditor(section, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def build_rig(self):
        '''Apply the UI options to rig_tail_constants and build the rig.'''
        import rig_tail as rt

        root = self.txt_root.text() or None
        if root:
            rt_cst.ROOT = root.split(rt_cst.GRP, 1)[0]

        if not rt_cst.RIGPARTS:
            QtWidgets.QMessageBox.warning(self, 'Error', 'RIGPARTS is empty. Add rig parts first.')
            return

        fk = self.chk_fk.isChecked()
        ik = self.chk_ik.isChecked()
        if not fk and not ik:
            QtWidgets.QMessageBox.warning(self, 'Error', 'Select at least FK or IK to build.')
            return

        rt_cst.EFFECTS = {
            # stretch network needs the ikfk switch attr from the IK build
            'stretchy': self.chk_stretchy.isChecked() and ik,
            'wave': self.chk_wave.isChecked(),
            'curl': self.chk_curl.isChecked(),
            'noise': self.chk_noise.isChecked(),
            'loop': self.chk_loop.isChecked()
            }
        rt_cst.FORCE_REBUILD = self.chk_force.isChecked()
        rt_cst.MAIN_CONTROLLER = self.chk_main.isChecked()

        try:
            rt.rig_tail_multiple(root=root, fk=fk, ik=ik)
            QtWidgets.QMessageBox.information(self, 'Success', 'Rig built successfully!')
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Failed to build rig:\n{str(e)}')


class RigPartsEditor(QtWidgets.QDialog):
    '''
    Pop-up editor for RIGPARTS.

    Rig parts can be added/edited/removed manually, or filled from the
    current joint selection via 'Get RIGPARTS from Selected Joints'.
    '''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit Rig Parts')
        self.setMinimumSize(350, 400)
        self.setup_ui()

    def setup_ui(self):
        '''Build the dialog layout.'''
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.addItems(rt_cst.RIGPARTS)
        self.list_widget.setStyleSheet('''
            QListWidget {
                background-color: #2b2b2b;
                color: #cccccc;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 4px;
            }
            QListWidget::item:selected {
                background-color: #4A90E2;
            }
        ''')

        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton('Add')
        self.btn_edit = QtWidgets.QPushButton('Edit')
        self.btn_remove = QtWidgets.QPushButton('Remove')
        self.btn_add.setToolTip('Add a new rig part name to the list.')
        self.btn_edit.setToolTip('Rename the selected rig part.')
        self.btn_remove.setToolTip('Delete the selected rig part from the list.')
        self.btn_add.clicked.connect(self.add_item)
        self.btn_edit.clicked.connect(self.edit_item)
        self.btn_remove.clicked.connect(self.remove_item)
        self.parent().style_button(self.btn_add, 0)
        self.parent().style_button(self.btn_edit, 0)
        self.parent().style_button(self.btn_remove, 0)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_edit)
        btn_layout.addWidget(self.btn_remove)

        self.btn_get_rigparts = QtWidgets.QPushButton('Get RIGPARTS from Selected Joints')
        self.btn_get_rigparts.setToolTip(
            'Replace the list with {rigname}s extracted from the joints '
            'selected in the scene (joints must follow the JOINT template).')
        self.btn_get_rigparts.clicked.connect(self.get_rigparts_from_selection)
        self.parent().style_button(self.btn_get_rigparts, 2)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(QtWidgets.QLabel('Rig Parts:'))
        layout.addWidget(self.list_widget)
        layout.addLayout(btn_layout)
        layout.addWidget(self.btn_get_rigparts)
        layout.addWidget(button_box)

    def get_rigparts_from_selection(self):
        '''Replace the list with rig names extracted from selected joints.'''
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

        self.list_widget.clear()
        self.list_widget.addItems(sorted(rignames))

    def add_item(self):
        '''Prompt for a new rig part name and append it to the list.'''
        text, ok = QtWidgets.QInputDialog.getText(self, 'Add Rig Part', 'Enter rig part name:')
        if ok and text:
            self.list_widget.addItem(text)

    def edit_item(self):
        '''Rename the selected rig part.'''
        current = self.list_widget.currentRow()
        if current >= 0:
            current_item = self.list_widget.item(current)
            text, ok = QtWidgets.QInputDialog.getText(
                self, 'Edit Rig Part',
                'Edit rig part name:',
                QtWidgets.QLineEdit.Normal,
                current_item.text()
            )
            if ok and text:
                current_item.setText(text)
        else:
            QtWidgets.QMessageBox.warning(self, 'Warning', 'No rig part selected.')

    def remove_item(self):
        '''Delete the selected rig part from the list.'''
        current = self.list_widget.currentRow()
        if current >= 0:
            self.list_widget.takeItem(current)

    def accept(self):
        '''Commit the list contents to rt_cst.RIGPARTS.'''
        rt_cst.RIGPARTS = [self.list_widget.item(i).text()
                           for i in range(self.list_widget.count())]
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
                'string is generated from IKFK_MODES automatically.\n'
                'Dividers are (longName, niceName, enumLabel).')
            note.setStyleSheet('color: #999999; font-size: 10px;')
            layout.addWidget(note)

        form_layout = QtWidgets.QFormLayout()
        form_layout.setSpacing(8)

        templates = self.get_templates_for_section()

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
                'IKFK_MODES': ', '.join(rt_cst.IKFK_MODES),
                # Show (longName, niceName, dv); enum derives from IKFK_MODES
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
                    # Placeholder enum; rebuild_derived() fills it from
                    # IKFK_MODES below
                    parsed[name] = (items[0], items[1], '', dv)
                elif name in self.TUPLE_FIELDS:
                    parsed[name] = tuple(items)
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

    def __init__(self, section, parent=None):
        super().__init__(parent)
        self.section = section
        self.setWindowTitle(f'Edit Constants: {section.title()}')
        self.setMinimumSize(400, 400)
        self.fields = {}
        self.setup_ui()

    def setup_ui(self):
        '''Build one spinbox per constant in this section.'''
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

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
            ''')
            self.fields[name] = spinbox
            form_layout.addRow(f'{name}:', spinbox)

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
        '''Commit all spinbox values to rig_tail_constants.'''
        for name, spinbox in self.fields.items():
            setattr(rt_cst, name, spinbox.value())
        rt_cst.rebuild_derived()
        super().accept()

def get_maya_window():
    '''Return the Maya main window as a QWidget for parenting.'''
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)

def show_ui():
    '''Show the Tail Rig Builder, closing any previous instance.'''
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
