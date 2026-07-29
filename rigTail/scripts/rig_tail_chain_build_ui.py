'''
rig_tail_chain_build_ui.py
author: Daisy Jane @gnitemouse

PySide2 UI for the Tail Chain Builder.
Create and re-space BN joint chains before the Setup phase.

Own copies of create_group_box / style_button — never modifies shared
widgets in rig_tail_ui.py (see §2.2 of the Chain Builder plan).

Classes and functions:
    TailChainBuilderUI: the Chain Builder window
    show_ui: build and show the window, closing any previous instance
    get_maya_window: the Maya main window as a QWidget for parenting
'''

import os
import json

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from shiboken2 import wrapInstance
from PySide2 import QtWidgets, QtCore

import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_joint as rt_jnt
import rig_tail_chain_build as rt_chb
import rig_tail_chain_spacing as rt_spc


class TailChainBuilderUI(QtWidgets.QDialog):
    '''Tail Chain Builder window: create and re-space joint chains.'''

    FIELD_W = 150       # dropdown / spin box width
    FIELD_H = 28        # height of every input widget
    LABEL_W = 120       # leading label column

    FIELD_STYLE = '''
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            background-color: #3a3a3a; color: #cccccc;
            border: 1px solid #555555; border-radius: 4px; padding: 2px 8px;
        }
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
            border-color: #F5D041;
        }
        QComboBox:disabled { color: #777777; border-color: #444444; }
    '''

    MODES = ['Keep', 'Uniform', 'Power', 'Ratio']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Tail Chain Builder')
        self.setMinimumWidth(480)
        self.setup_ui()
        self.load_prefs()
        self.resize(480, self.sizeHint().height())

    # LAYOUT ============================================================

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 10, 15, 10)

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(2)
        title = QtWidgets.QLabel('TAIL CHAIN BUILDER')
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
        src_layout.setSpacing(6)

        self.rad_rebuild = QtWidgets.QRadioButton('Rebuild selected chain')
        self.rad_new = QtWidgets.QRadioButton('New chain between two selected objects')
        self.rad_rebuild.setChecked(True)
        self.rad_rebuild.toggled.connect(self._sync_source_mode)
        for rad in (self.rad_rebuild, self.rad_new):
            rad.setStyleSheet('color: #cccccc; spacing: 4px;')

        chain_row = QtWidgets.QHBoxLayout()
        chain_label = QtWidgets.QLabel('Chain:')
        chain_label.setMinimumWidth(self.LABEL_W)
        self.txt_chain = QtWidgets.QLineEdit()
        self.txt_chain.setPlaceholderText('(select in viewport)')
        self.txt_chain.setReadOnly(True)
        self.txt_chain.setStyleSheet(self.FIELD_STYLE)
        self.txt_chain.setFixedHeight(self.FIELD_H)
        self.btn_select = QtWidgets.QPushButton('Select')
        self.btn_select.setToolTip('Fill from viewport selection')
        self.style_button(self.btn_select, 0)
        self.btn_select.setFixedSize(70, self.FIELD_H)
        self.btn_select.clicked.connect(self.select_from_viewport)
        chain_row.addWidget(chain_label)
        chain_row.addWidget(self.txt_chain, 1)
        chain_row.addWidget(self.btn_select)

        rigname_row = QtWidgets.QHBoxLayout()
        rigname_label = QtWidgets.QLabel('Rig Name:')
        rigname_label.setMinimumWidth(self.LABEL_W)
        self.txt_rigname = QtWidgets.QLineEdit()
        self.txt_rigname.setPlaceholderText('(auto)')
        self.txt_rigname.setStyleSheet(self.FIELD_STYLE)
        self.txt_rigname.setFixedHeight(self.FIELD_H)
        self.txt_rigname.setToolTip('Rig part name for naming joints (auto-detected '
                                    'from selection on rebuild)')
        rigname_row.addWidget(rigname_label)
        rigname_row.addWidget(self.txt_rigname, 1)

        src_layout.addWidget(self.rad_rebuild)
        src_layout.addWidget(self.rad_new)
        src_layout.addLayout(chain_row)
        src_layout.addLayout(rigname_row)
        src_group.setLayout(src_layout)
        main_layout.addWidget(src_group)

        # Spacing section ------------------------------------------------
        spc_group = self.create_group_box('Spacing')
        spc_layout = QtWidgets.QVBoxLayout()
        spc_layout.setSpacing(6)

        # Joint Count
        count_row = QtWidgets.QHBoxLayout()
        self.lbl_count = QtWidgets.QLabel('Joint Count:')
        self.lbl_count.setMinimumWidth(self.LABEL_W)
        self.spn_count = QtWidgets.QSpinBox()
        self.spn_count.setRange(2, 200)
        self.spn_count.setValue(21)
        self.spn_count.setStyleSheet(self.FIELD_STYLE)
        self.spn_count.setFixedHeight(self.FIELD_H)
        self.spn_count.setAlignment(QtCore.Qt.AlignCenter)
        self.spn_count.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spn_count.valueChanged.connect(self.update_status)
        self.lbl_detected = QtWidgets.QLabel('(detected: —)')
        self.lbl_detected.setStyleSheet('color: #999999; font-size: 10px;')
        count_row.addWidget(self.lbl_count)
        count_row.addWidget(self.spn_count)
        count_row.addWidget(self.lbl_detected)
        count_row.addStretch()
        spc_layout.addLayout(count_row)

        # Mode
        mode_row = QtWidgets.QHBoxLayout()
        mode_label = QtWidgets.QLabel('Mode:')
        mode_label.setMinimumWidth(self.LABEL_W)
        self.cmb_mode = QtWidgets.QComboBox()
        self.cmb_mode.addItems(self.MODES)
        self.cmb_mode.setStyleSheet(self.FIELD_STYLE)
        self.cmb_mode.setFixedSize(self.FIELD_W, self.FIELD_H)
        self.cmb_mode.currentIndexChanged.connect(self._sync_mode)
        self.cmb_mode.currentIndexChanged.connect(self.update_status)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.cmb_mode)
        mode_row.addStretch()
        spc_layout.addLayout(mode_row)

        # Parameter
        param_row = QtWidgets.QHBoxLayout()
        self.lbl_param = QtWidgets.QLabel('Exponent:')
        self.lbl_param.setMinimumWidth(self.LABEL_W)
        self.spn_param = QtWidgets.QDoubleSpinBox()
        self.spn_param.setDecimals(2)
        self.spn_param.setSingleStep(0.05)
        self.spn_param.setRange(0.2, 5.0)
        self.spn_param.setValue(1.70)
        self.spn_param.setStyleSheet(self.FIELD_STYLE)
        self.spn_param.setFixedHeight(self.FIELD_H)
        self.spn_param.valueChanged.connect(self.update_status)
        param_row.addWidget(self.lbl_param)
        param_row.addWidget(self.spn_param)
        param_row.addStretch()
        spc_layout.addLayout(param_row)

        # Invert
        self.chk_invert = QtWidgets.QCheckBox('Invert distribution')
        self.chk_invert.setToolTip('Mirror the spacing profile (base <-> tip)')
        self.style_checkbox(self.chk_invert)
        self.chk_invert.toggled.connect(self.update_status)
        spc_layout.addWidget(self.chk_invert)

        # Snap
        self.chk_snap = QtWidgets.QCheckBox('Snap to existing joints')
        self.chk_snap.setChecked(True)
        self.chk_snap.setToolTip(
            'When the target count divides the source evenly, keep exact '
            'original positions at matching joints (lossless).')
        self.style_checkbox(self.chk_snap)
        spc_layout.addWidget(self.chk_snap)

        # Orient on create
        self.chk_orient = QtWidgets.QCheckBox('Orient on create')
        self.chk_orient.setToolTip('Aim-orient new joints (new chains only; '
                                   'existing chains use Tail Rig Setup)')
        self.style_checkbox(self.chk_orient)
        spc_layout.addWidget(self.chk_orient)

        spc_group.setLayout(spc_layout)
        main_layout.addWidget(spc_group)

        # Status section -------------------------------------------------
        self.lbl_status = QtWidgets.QLabel('')
        self.lbl_status.setStyleSheet('''
            QLabel {
                color: #999999; font-size: 10px; padding: 2px 0;
            }
        ''')
        self.lbl_status.setWordWrap(True)
        main_layout.addWidget(self.lbl_status)

        # Buttons --------------------------------------------------------
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(7)

        self.btn_reset = QtWidgets.QPushButton('Reset Original')
        self.btn_reset.setToolTip('Re-baseline the session original cache for '
                                  'the selected chain')
        self.btn_load = QtWidgets.QPushButton('Load Prefs')
        self.btn_save = QtWidgets.QPushButton('Save Prefs')
        self.btn_build = QtWidgets.QPushButton('Build / Rebuild')

        self.btn_reset.clicked.connect(self.reset_original)
        self.btn_load.clicked.connect(self.load_prefs_dialog)
        self.btn_save.clicked.connect(self.save_prefs_dialog)
        self.btn_build.clicked.connect(self.run_build)

        self.style_button(self.btn_reset, 0)
        self.style_button(self.btn_load, 0)
        self.style_button(self.btn_save, 0)
        self.style_button(self.btn_build, 4)  # olive primary

        button_layout.addWidget(self.btn_reset)
        button_layout.addWidget(self.btn_load)
        button_layout.addWidget(self.btn_save)
        button_layout.addStretch()
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
        if style == 0:  # grey
            button.setStyleSheet('''
                QPushButton {
                    background-color: #3a3a3a; color: #cccccc;
                    border: 1px solid #555555; border-radius: 4px; padding: 6px 12px;
                }
                QPushButton:hover { background-color: #4a4a4a; border-color: #666666; }
                QPushButton:pressed { background-color: #2a2a2a; }
            ''')
        elif style == 4:  # olive green (primary)
            button.setStyleSheet('''
                QPushButton {
                    background-color: #6B7A45; color: white; border: none;
                    border-radius: 4px; padding: 8px 16px; font-weight: bold;
                }
                QPushButton:hover { background-color: #7C8C52; }
                QPushButton:pressed { background-color: #525E33; }
            ''')
        button.setMinimumHeight(32)

    def style_checkbox(self, checkbox):
        checkbox.setStyleSheet('''
            QCheckBox { spacing: 6px; color: #cccccc; }
            QCheckBox::indicator { width: 18px; height: 18px; }
        ''')
        checkbox.setMinimumHeight(self.FIELD_H)

    # STATE =============================================================

    def _sync_source_mode(self, checked):
        '''Enable/disable the rigname textbox based on new-chain vs rebuild.'''
        is_new = self.rad_new.isChecked()
        self.txt_rigname.setEnabled(is_new)
        self.chk_orient.setEnabled(is_new)
        if not is_new:
            self.txt_rigname.clear()
        self.update_status()

    def _sync_mode(self):
        '''Update the parameter field label and range for the selected mode.'''
        mode = self.cmb_mode.currentText().lower()
        if mode == 'power':
            self.lbl_param.setText('Exponent:')
            self.spn_param.setRange(*rt_spc.K_RANGE)
            self.spn_param.setSingleStep(0.05)
            self.spn_param.setEnabled(True)
        elif mode == 'ratio':
            self.lbl_param.setText('Ratio:')
            self.spn_param.setRange(*rt_spc.R_RANGE)
            self.spn_param.setSingleStep(0.01)
            self.spn_param.setEnabled(True)
        elif mode == 'keep':
            self.lbl_param.setText('Exponent:')
            self.spn_param.setEnabled(False)
        else:  # uniform
            self.lbl_param.setText('Exponent:')
            self.spn_param.setEnabled(False)
        # Keep is disabled in new-chain mode
        if self.rad_new.isChecked() and mode == 'keep':
            self.cmb_mode.setCurrentIndex(self.MODES.index('Uniform'))

    def load_prefs(self):
        '''Load saved preferences into the UI.'''
        prefs = rt_chb._load_prefs()
        mode = prefs.get('mode', 'keep')
        mode_index = [m.lower() for m in self.MODES].index(mode) \
            if mode in [m.lower() for m in self.MODES] else 0
        self.cmb_mode.setCurrentIndex(mode_index)
        self._sync_mode()
        param_key = 'ratio' if mode == 'ratio' else 'power'
        self.spn_param.setValue(prefs.get(param_key, 0.90 if mode == 'ratio' else 1.7))
        self.chk_invert.setChecked(prefs.get('invert', False))
        self.chk_snap.setChecked(prefs.get('snap', True))
        self.chk_orient.setChecked(prefs.get('orient_on_create', False))
        self._sync_mode()

    def save_prefs(self):
        '''Save current UI state to preferences file.'''
        prefs = {
            'mode': self.cmb_mode.currentText().lower(),
            'power': self.spn_param.value() if self.cmb_mode.currentText().lower() == 'power'
                     else rt_chb._load_prefs().get('power', 1.7),
            'ratio': self.spn_param.value() if self.cmb_mode.currentText().lower() == 'ratio'
                     else rt_chb._load_prefs().get('ratio', 0.90),
            'invert': self.chk_invert.isChecked(),
            'snap': self.chk_snap.isChecked(),
            'orient_on_create': self.chk_orient.isChecked(),
        }
        rt_chb._save_prefs(prefs)

    def load_prefs_dialog(self):
        self.load_prefs()

    def save_prefs_dialog(self):
        self.save_prefs()

    # ACTIONS ===========================================================

    def select_from_viewport(self):
        '''Read selection and fill the Chain / Rig Name fields.'''
        try:
            specs = rt_chb.resolve_selection()
        except Exception as exc:
            cmds.warning(f'Could not resolve selection: {exc}')
            return
        if not specs:
            cmds.warning('No chain specs resolved from selection.')
            return
        spec = specs[0]
        self.txt_chain.setText(spec.rigname or
                               ('{} → {}'.format(spec.start, spec.end)
                                if spec.is_new else str(spec.root)))
        if spec.rigname:
            self.txt_rigname.setText(spec.rigname)
        self.rad_rebuild.setChecked(not spec.is_new)
        self.rad_new.setChecked(spec.is_new)

        # Show detected joint count
        if not spec.is_new and spec.root:
            chain = rt_jnt.get_joint_chain(spec.root)
            n = len(chain)
            self.spn_count.setValue(n)
            self.lbl_detected.setText(f'(detected: {n})')
        else:
            self.lbl_detected.setText('(detected: —)')
        self.update_status()

    def reset_original(self):
        '''Clear the session original cache for the active chain.'''
        rigname = self.txt_chain.text().strip()
        if not rigname:
            cmds.warning('No chain selected to reset.')
            return
        rt_chb.clear_cache()
        cmds.warning(f'Cache cleared for {rigname}.')

    def run_build(self):
        '''Execute the build or rebuild with current settings.'''
        rigname = (self.txt_rigname.text().strip() if self.rad_new.isChecked()
                   else self.txt_chain.text().strip())
        if not rigname or rigname == 'None':
            cmds.warning('Enter a Rig Name for a new chain.' if self.rad_new.isChecked()
                         else 'Select a chain first.')
            return
        if not self.rad_new.isChecked() and not self.txt_chain.text().strip():
            cmds.warning('Select a chain first.')
            return

        n = self.spn_count.value()
        mode = self.cmb_mode.currentText().lower()
        param = self.spn_param.value() if self.spn_param.isEnabled() else None
        invert = self.chk_invert.isChecked()
        snap = self.chk_snap.isChecked()

        try:
            if self.rad_new.isChecked():
                # Build new: need start and end from selection
                sel = cmds.ls(selection=True, transforms=True)
                if len(sel) < 2:
                    cmds.warning('Select two transforms for a new chain.')
                    return
                rt_chb.build_new(sel[0], sel[1], n, rigname,
                                 mode, param, invert)
            else:
                # Rebuild
                root = cmds.ls(selection=True, type='joint')
                if not root:
                    cmds.warning('Select a joint in the chain to rebuild.')
                    return
                rt_chb.rebuild(root[0], n, mode, param, invert, snap)

            cmds.warning(f'Chain {rigname}: built with {n} joints ({mode}).')
            self.update_status()
        except Exception as exc:
            cmds.warning(f'Chain build failed: {exc}')

    def update_status(self):
        '''Update the status line with current chain info.'''
        rigname = self.txt_chain.text().strip()
        if not rigname:
            self.lbl_status.setText('')
            return
        n = self.spn_count.value()
        mode = self.cmb_mode.currentText()
        invert = ' inverted' if self.chk_invert.isChecked() else ''
        snap_txt = ', snap' if self.chk_snap.isChecked() else ', no snap'
        self.lbl_status.setText(
            f'{rigname}: {n} joints, {mode}{invert}{snap_txt}')

    def closeEvent(self, event):
        self.save_prefs()
        super().closeEvent(event)


def get_maya_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def show_ui():
    '''Show the Tail Chain Builder window, closing any previous instance.'''
    global tail_chain_builder_window
    try:
        tail_chain_builder_window.close()
        tail_chain_builder_window.deleteLater()
    except Exception:
        pass
    parent = get_maya_window()
    tail_chain_builder_window = TailChainBuilderUI(parent=parent)
    tail_chain_builder_window.show()
    return tail_chain_builder_window
