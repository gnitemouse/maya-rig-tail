'''
# rig_tail_ui.py
author: Daisy Jane @dayzl

PySide2 UI for Rig Tail
Compatible with Maya 2024/2025.
Maya 2024/2025 ships with Qt5, not Qt6.
'''

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from shiboken2 import wrapInstance
from PySide2 import QtWidgets, QtCore, QtGui
import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam

class RigTailUI(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Rig Tail')
        self.setMinimumSize(500, 1100)
        self.resize(500, 1100)
        self.setup_ui()
        self.load_current_values()

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 10, 15, 10)

        title = QtWidgets.QLabel('TAIL RIG BUILDER')
        title.setStyleSheet('font-size: 18px; font-weight: bold; color: #FFFFFF;')
        title.setAlignment(QtCore.Qt.AlignCenter)
        main_layout.addWidget(title)

        author = QtWidgets.QLabel('author @dayzl Daisy Jane')
        author.setStyleSheet('font-size: 10px; font-weight: normal; color: #4A90E2;')
        author.setAlignment(QtCore.Qt.AlignRight)
        main_layout.addWidget(author)

        self.add_separator(main_layout)

        display_group = self.create_group_box('Current Configuration')
        display_layout = QtWidgets.QVBoxLayout()

        self.txt_display = QtWidgets.QTextEdit()
        self.txt_display.setReadOnly(True)
        self.txt_display.setMaximumHeight(160)
        self.txt_display.setStyleSheet('''
            QTextEdit {
                background-color: #2b2b2b;
                color: #cccccc;
                font-family: Consolas, monospace;
                font-size: 10px;
                border: 1px solid #555555;
                border-radius: 2px;
                padding: 2px;
            }
        ''')
        display_layout.addWidget(self.txt_display)

        config_btn_layout = QtWidgets.QHBoxLayout()
        self.btn_load_config = QtWidgets.QPushButton('Load Config')
        self.btn_save_config = QtWidgets.QPushButton('Save Config')
        self.btn_load_config.clicked.connect(self.load_config)
        self.btn_save_config.clicked.connect(self.save_config)
        self.style_button(self.btn_load_config, 0)
        self.style_button(self.btn_save_config, 0)
        config_btn_layout.addWidget(self.btn_load_config)
        config_btn_layout.addWidget(self.btn_save_config)
        display_layout.addLayout(config_btn_layout)

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        self.add_separator(main_layout)

        options_group = self.create_group_box('Build Options')
        options_layout = QtWidgets.QVBoxLayout()
        options_layout.setSpacing(10)

        root_layout = QtWidgets.QHBoxLayout()
        root_label = QtWidgets.QLabel('Root Name:')
        root_label.setMinimumWidth(100)
        self.txt_root = QtWidgets.QLineEdit()
        self.txt_root.setPlaceholderText('e.g. tail_root_grp')
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
        self.chk_noise = QtWidgets.QCheckBox('noise')
        self.chk_loop = QtWidgets.QCheckBox('loop')
        self.chk_stretchy.setChecked(True)
        self.chk_wave.setChecked(False)
        self.chk_curl.setChecked(False)
        self.chk_noise.setChecked(False)
        self.chk_loop.setChecked(False)
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

        self.chk_group_control = QtWidgets.QCheckBox('Group Control (for multiple tails)')
        self.chk_group_control.setEnabled(len(rt_cst.RIGPARTS) > 1)
        self.style_checkbox(self.chk_group_control)
        options_layout.addWidget(self.chk_group_control)

        self.chk_force = QtWidgets.QCheckBox('Force Rebuild (ignore cache)')
        self.style_checkbox(self.chk_force)
        options_layout.addWidget(self.chk_force)

        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)

        self.add_separator(main_layout)

        editors_group = self.create_group_box('Configuration Editor')
        editors_layout = QtWidgets.QVBoxLayout()
        editors_layout.setSpacing(6)

        editor_buttons = [
            ('Edit Rig Parts', lambda: self.open_rigparts_editor()),
            ('Edit Naming: Groups, Controls, Joints', lambda: self.open_naming_editor('groups')),
            ('Edit Naming: Curves, Clusters', lambda: self.open_naming_editor('curves')),
            ('Edit Naming: Spline', lambda: self.open_naming_editor('spline')),
            ('Edit Naming: Structure', lambda: self.open_naming_editor('structure')),
            ('Edit Naming: IKFK, Switch, Divider', lambda: self.open_naming_editor('ikfk')),
            ('Edit Constants: Number of Controls', lambda: self.open_constants_editor('num')),
            ('Edit Constants: Control Size', lambda: self.open_constants_editor('size')),
        ]

        for btn_text, btn_func in editor_buttons:
            btn = QtWidgets.QPushButton(btn_text)
            btn.clicked.connect(btn_func)
            self.style_button(btn, 0)
            editors_layout.addWidget(btn)

        editors_group.setLayout(editors_layout)
        main_layout.addWidget(editors_group)

        self.add_separator(main_layout)

        self.btn_get_rigparts = QtWidgets.QPushButton('Get RIGPARTS from Selected Joints')
        self.btn_get_rigparts.clicked.connect(self.get_rigparts_from_selection)
        self.style_button(self.btn_get_rigparts, 2)
        main_layout.addWidget(self.btn_get_rigparts)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        self.btn_cancel = QtWidgets.QPushButton('Cancel')
        self.btn_build = QtWidgets.QPushButton('Build Rig')

        self.btn_cancel.clicked.connect(self.close)
        self.btn_build.clicked.connect(self.build_rig)

        self.style_button(self.btn_cancel, 0)
        self.style_button(self.btn_build, 1)

        button_layout.addWidget(self.btn_cancel)
        button_layout.addWidget(self.btn_build)

        main_layout.addLayout(button_layout)
        main_layout.addStretch()

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

    def add_separator(self, layout):
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setStyleSheet('background-color: #555555;')
        layout.addWidget(line)

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
        self.txt_root.setText(rt_cst.ROOT)
        self.update_display()

    def update_display(self):
        display_text = f'''
            ROOT = '{rt_cst.ROOT}'
            RIGPARTS = {rt_cst.RIGPARTS}

            NUM_CTRL_FK = {rt_cst.NUM_CTRL_FK}
            NUM_CTRL_IK = {rt_cst.NUM_CTRL_IK}

            Control Sizes:
              ROOT = {rt_cst.ROOT_CTRL_SZ}  COG = {rt_cst.COG_CTRL_SZ}  BASE = {rt_cst.BASE_CTRL_SZ}
              FK = {rt_cst.VARFK_CTRL_SZ}  {rt_cst.FK_CTRL_SZ}    IK = {rt_cst.IK_CTRL_SZ}
              SPLINE_BOT = {rt_cst.SPLINE_BOT_SZ}  MID = {rt_cst.SPLINE_MID_SZ}  TOP = {rt_cst.SPLINE_TOP_SZ}
        '''
        self.txt_display.setText(display_text)
        self.chk_group_control.setEnabled(len(rt_cst.RIGPARTS) > 1)

    def load_config(self):
        if rt_cst.load_config():
            self.load_current_values()
            QtWidgets.QMessageBox.information(self, 'Success', 'Configuration loaded successfully!')
        else:
            QtWidgets.QMessageBox.warning(self, 'Warning', 'No saved configuration found or load failed.')

    def save_config(self):
        if rt_cst.save_config():
            QtWidgets.QMessageBox.information(self, 'Success', 'Configuration saved successfully!')
        else:
            QtWidgets.QMessageBox.critical(self, 'Error', 'Failed to save configuration.')

    def open_rigparts_editor(self):
        dialog = RigPartsEditor(self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def open_naming_editor(self, section):
        dialog = NamingTemplateEditor(section, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def open_constants_editor(self, section):
        dialog = ConstantsEditor(section, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def get_rigparts_from_selection(self):
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

        if rignames:
            rt_cst.RIGPARTS = sorted(rignames)
            self.update_display()
            QtWidgets.QMessageBox.information(
                self, 'Success',
                f'Found {len(rignames)} rig part(s):\n{", ".join(rt_cst.RIGPARTS)}'
            )
        else:
            QtWidgets.QMessageBox.warning(
                self, 'Warning',
                'Could not extract rig names from selected joints.\nEnsure joints follow naming conventions.'
            )

    def build_rig(self):
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
            'stretchy': self.chk_stretchy.isChecked(),
            'wave': self.chk_wave.isChecked(),
            'curl': self.chk_curl.isChecked(),
            'noise': self.chk_noise.isChecked(),
            'loop': self.chk_loop.isChecked()
            }
        rt_cst.FORCE_REBUILD = self.chk_force.isChecked()
        rt_cst.GROUP_CONTROL = self.chk_group_control.isChecked()

        try:
            rt.rig_tail_multiple(root=root, fk=fk, ik=ik)
            QtWidgets.QMessageBox.information(self, 'Success', 'Rig built successfully!')
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Failed to build rig:\n{str(e)}')


class RigPartsEditor(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit Rig Parts')
        self.setMinimumSize(350, 400)
        self.setup_ui()

    def setup_ui(self):
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
        self.btn_add.clicked.connect(self.add_item)
        self.btn_edit.clicked.connect(self.edit_item)
        self.btn_remove.clicked.connect(self.remove_item)
        self.parent().style_button(self.btn_add, 0)
        self.parent().style_button(self.btn_edit, 0)
        self.parent().style_button(self.btn_remove, 0)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_edit)
        btn_layout.addWidget(self.btn_remove)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(QtWidgets.QLabel('Rig Parts:'))
        layout.addWidget(self.list_widget)
        layout.addLayout(btn_layout)
        layout.addWidget(button_box)

    def add_item(self):
        text, ok = QtWidgets.QInputDialog.getText(self, 'Add Rig Part', 'Enter rig part name:')
        if ok and text:
            self.list_widget.addItem(text)

    def edit_item(self):
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
        current = self.list_widget.currentRow()
        if current >= 0:
            self.list_widget.takeItem(current)

    def accept(self):
        rt_cst.RIGPARTS = [self.list_widget.item(i).text()
                           for i in range(self.list_widget.count())]
        super().accept()


class NamingTemplateEditor(QtWidgets.QDialog):
    def __init__(self, section, parent=None):
        super().__init__(parent)
        self.section = section
        self.setWindowTitle(f'Edit Naming: {section.title()}')
        self.setMinimumSize(500, 500)
        self.fields = {}
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

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
        if self.section == 'groups':
            return {
                'BASECTRL_GRP': rt_cst.BASECTRL_GRP,
                'BASECTRL': rt_cst.BASECTRL,
                'CTRLROOT_GRP': rt_cst.CTRLROOT_GRP,
                'CTRL_GRP': rt_cst.CTRL_GRP,
                'CONTROL': rt_cst.CONTROL,
                'GROUP': rt_cst.GROUP,
                'JOINT': rt_cst.JOINT,
                'SDK_GRP': rt_cst.SDK_GRP,
                'SDK_CTRL': rt_cst.SDK_CTRL,
            }
        elif self.section == 'curves':
            return {
                'CURVE': rt_cst.CURVE,
                'CURVEINFO': rt_cst.CURVEINFO,
                'CLUSTER_GRP': rt_cst.CLUSTER_GRP,
                'CLUSTER': rt_cst.CLUSTER,
                'CLUSTER_HANDLE': rt_cst.CLUSTER_HANDLE,
                'UPV_CTRLGRP': rt_cst.UPV_CTRLGRP,
                'UPV_CTRL': rt_cst.UPV_CTRL,
            }
        elif self.section == 'spline':
            return {
                'SPLINE_GRP': rt_cst.SPLINE_GRP,
                'SPLINE_HANDLE': rt_cst.SPLINE_HANDLE,
                'SPLINE_EFFECTOR': rt_cst.SPLINE_EFFECTOR,
                'SPLINE_IK_CTRL': rt_cst.SPLINE_IK_CTRL,
                'SPLINE_FLOAT_CTRL': rt_cst.SPLINE_FLOAT_CTRL,
                'SPLINE_BOT': rt_cst.SPLINE_BOT,
                'SPLINE_MID': rt_cst.SPLINE_MID,
                'SPLINE_TOP': rt_cst.SPLINE_TOP,
            }
        elif self.section == 'structure':
            return {
                'ROOT_GRP': rt_cst.ROOT_GRP,
                'ROOT_CTRL': rt_cst.ROOT_CTRL,
                'COG_CTRL': rt_cst.COG_CTRL,
                'GEOMETRY_GRP': rt_cst.GEOMETRY_GRP,
                'CONTROL_GRP': rt_cst.CONTROL_GRP,
                'SKELETON_GRP': rt_cst.SKELETON_GRP,
                'RIG_SYSTEMS_GRP': rt_cst.RIG_SYSTEMS_GRP,
                'SCALE_GRP': rt_cst.SCALE_GRP,
            }
        elif self.section == 'ikfk':
            return {
                'IKFK': rt_cst.IKFK,
            }
        return {}

    def accept(self):
        for name, line_edit in self.fields.items():
            setattr(rt_cst, name, line_edit.text())
        super().accept()


class ConstantsEditor(QtWidgets.QDialog):
    def __init__(self, section, parent=None):
        super().__init__(parent)
        self.section = section
        self.setWindowTitle(f'Edit Constants: {section.title()}')
        self.setMinimumSize(400, 400)
        self.fields = {}
        self.setup_ui()

    def setup_ui(self):
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
        for name, spinbox in self.fields.items():
            setattr(rt_cst, name, spinbox.value())
        super().accept()

def get_maya_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)

def show_ui():
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
