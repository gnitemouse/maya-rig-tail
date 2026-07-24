'''
rig_tail_setup_ui.py
author: Daisy Jane @gnitemouse

PySide2 UI for the Tail Rig Setup phase, the optional skeleton-prep step
that runs before the Tail Rig Builder. Nothing here affects the build; it
only re-orients the raw BN skeleton so tails move coherently.

Two operations, exposed as checkboxes:
    Orient Chains (MIRROR_ORIENT): aim-orient each chain so a tail bends
        in a plane (removes intra-chain twist).
    Mirror Joints (MIRROR_JOINTS): behavior-mirror matching 'L_'/'R_'
        pairs so the two sides move as mirror images.
Dropdowns set the source side and the aim, up and mirror axes. Dry Run
only logs the intended changes. Joint positions are never changed;
affected geometry is unbound for the build to rebind.

Run Setup calls rig_tail_setup.setup_tails. Values live in
rig_tail_constants and round-trip through the same JSON config as the
Builder. The window is modeled on rig_tail_ui.RigTailUI and reuses its
RIGPARTS editor. Compatible with Maya 2024/2025 (Qt5).

Classes and functions:
    RigTailSetupUI: the Setup window
    show_ui: build and show the window, closing any previous instance
    get_maya_window: the Maya main window as a QWidget for parenting
'''

import os

import maya.OpenMayaUI as omui
import maya.cmds as cmds
from shiboken2 import wrapInstance
from PySide2 import QtWidgets, QtCore

import rig_tail_constants as rt_cst
import rig_tail_naming as rt_nam
import rig_tail_ui as rt_ui  # reuse RigPartsEditor + styling conventions


class RigTailSetupUI(QtWidgets.QDialog):
    '''Tail Rig Setup window: orient / mirror the skeleton before build.'''

    AXES = ['x', 'y', 'z']
    SIDES = ['R', 'L']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Rig Tail Setup')
        self.setMinimumWidth(500)
        self.setup_ui()
        self.load_current_values()
        self.resize(500, self.sizeHint().height())

    # LAYOUT ===========================================================

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 10, 15, 10)

        title = QtWidgets.QLabel('TAIL RIG SETUP')
        title.setStyleSheet('font-size: 18px; font-weight: bold; color: #FFFFFF;')
        title.setAlignment(QtCore.Qt.AlignCenter)
        main_layout.addWidget(title)

        subtitle = QtWidgets.QLabel('Orient / mirror the skeleton before building')
        subtitle.setStyleSheet('font-size: 10px; color: #999999;')
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        main_layout.addWidget(subtitle)

        author = QtWidgets.QLabel('author Daisy Jane @gnitemouse')
        author.setStyleSheet('font-size: 10px; font-weight: normal; color: #4A90E2;')
        author.setAlignment(QtCore.Qt.AlignRight)
        main_layout.addWidget(author)

        # Current configuration + config file row
        display_group = self.create_group_box('Current Configuration')
        display_layout = QtWidgets.QVBoxLayout()
        self.txt_display = QtWidgets.QTextEdit()
        self.txt_display.setReadOnly(True)
        self.txt_display.setMaximumHeight(110)
        self.txt_display.setStyleSheet('''
            QTextEdit {
                background-color: #2b2b2b; color: #cccccc;
                font-family: Consolas, monospace; font-size: 11px;
                border: 1px solid #555555; border-radius: 4px;
                padding: 2px 20px;
            }
        ''')
        self.txt_display.document().setDocumentMargin(4)
        display_layout.setContentsMargins(8, 2, 8, 2)
        display_layout.setSpacing(4)
        display_layout.addWidget(self.txt_display)

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
        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        # Setup options
        options_group = self.create_group_box('Setup Options')
        options_layout = QtWidgets.QVBoxLayout()
        options_layout.setSpacing(8)

        root_layout = QtWidgets.QHBoxLayout()
        root_label = QtWidgets.QLabel('Root Name:')
        root_label.setMinimumWidth(110)
        self.txt_root = QtWidgets.QLineEdit()
        self.txt_root.setPlaceholderText('e.g. squid')
        self.txt_root.setToolTip('Rig root name (ROOT), same as the Builder.')
        self.txt_root.setStyleSheet('''
            QLineEdit {
                background-color: #3a3a3a; color: #cccccc; font-size: 11px;
                border: 1px solid #4A90E2; border-radius: 4px; padding: 4px 6px;
            }
            QLineEdit:focus { border-color: #F5D041; }
        ''')
        root_layout.addWidget(root_label)
        root_layout.addWidget(self.txt_root)
        options_layout.addLayout(root_layout)

        btn_parts = QtWidgets.QPushButton('Edit Rig Parts')
        btn_parts.setToolTip('Edit RIGPARTS: the tails to set up (shared with the Builder).')
        btn_parts.clicked.connect(self.open_rigparts_editor)
        self.style_button(btn_parts, 0)
        options_layout.addWidget(btn_parts)
        options_layout.addSpacing(6)

        # Operation toggles
        self.chk_orient = QtWidgets.QCheckBox('Orient Chains (fix twist)')
        self.chk_orient.setToolTip(
            'Aim-orient every chain: re-aim each joint down its own chain '
            'with one up-axis (the chain plane normal), so the tail bends '
            'in a plane. Removes intra-chain twist. (MIRROR_ORIENT)')
        self.chk_mirror = QtWidgets.QCheckBox('Mirror Joints (L/R behavior)')
        self.chk_mirror.setToolTip(
            'Behavior-mirror each matching L_/R_ pair: overwrite the target '
            "side's orientation with the mirror of the source side so the "
            'two sides move as mirror images. Does not remove twist by '
            'itself, so enable Orient Chains too. (MIRROR_JOINTS)')
        self.chk_dryrun = QtWidgets.QCheckBox('Dry Run (preview only)')
        self.chk_dryrun.setToolTip(
            'Only log the intended changes; do not modify any joints or '
            'unbind geometry. Use this first to verify. (MIRROR_ORIENT_DRYRUN)')
        for chk in (self.chk_orient, self.chk_mirror, self.chk_dryrun):
            self.style_checkbox(chk)
            options_layout.addWidget(chk)
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
        options_layout.addLayout(self._labeled_row('Mirror Source Side:', self.cmb_source))
        options_layout.addLayout(self._labeled_row('Mirror Axis (plane normal):', self.cmb_axis))
        options_layout.addLayout(self._labeled_row('Aim Axis (down chain):', self.cmb_aim))
        options_layout.addLayout(self._labeled_row('Up Axis (plane normal):', self.cmb_up))

        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)
        main_layout.addSpacing(8)

        # Action buttons
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
        combo = QtWidgets.QComboBox()
        combo.addItems(items)
        combo.setToolTip(tip)
        combo.setStyleSheet('''
            QComboBox {
                background-color: #3a3a3a; color: #cccccc;
                border: 1px solid #555555; border-radius: 4px; padding: 4px 8px;
            }
            QComboBox:focus { border-color: #F5D041; }
        ''')
        combo.currentIndexChanged.connect(self.update_display)
        return combo

    def _labeled_row(self, label_text, widget):
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(label_text)
        label.setMinimumWidth(170)
        row.addWidget(label)
        row.addWidget(widget, 1)
        return row

    # STYLING (mirrors rig_tail_ui) ====================================

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
        if style == 1:  # blue (primary)
            button.setStyleSheet('''
                QPushButton {
                    background-color: #4166F5; color: white; border: none;
                    border-radius: 4px; padding: 8px 16px; font-weight: bold;
                }
                QPushButton:hover { background-color: #4153F5; }
                QPushButton:pressed { background-color: #304EB8; }
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

    # STATE ============================================================

    def _combo_set(self, combo, value, default_index=0):
        idx = combo.findText(str(value))
        combo.setCurrentIndex(idx if idx >= 0 else default_index)

    def load_current_values(self):
        '''Refresh fields from rig_tail_constants (getattr for stale sessions).'''
        self.txt_root.setText(getattr(rt_cst, 'ROOT', ''))
        self.chk_orient.setChecked(bool(getattr(rt_cst, 'MIRROR_ORIENT', True)))
        self.chk_mirror.setChecked(bool(getattr(rt_cst, 'MIRROR_JOINTS', True)))
        self.chk_dryrun.setChecked(bool(getattr(rt_cst, 'MIRROR_ORIENT_DRYRUN', False)))
        self._combo_set(self.cmb_source, getattr(rt_cst, 'MIRROR_SOURCE_SIDE', 'R'))
        self._combo_set(self.cmb_axis, getattr(rt_cst, 'MIRROR_AXIS', 'x'))
        self._combo_set(self.cmb_aim, getattr(rt_cst, 'ORIENT_AIM_AXIS', 'x'))
        self._combo_set(self.cmb_up, getattr(rt_cst, 'ORIENT_UP_AXIS', 'z'))
        self.update_display()

    def save_current_values(self):
        '''Write the UI state into rig_tail_constants.'''
        rt_cst.MIRROR_ORIENT = self.chk_orient.isChecked()
        rt_cst.MIRROR_JOINTS = self.chk_mirror.isChecked()
        rt_cst.MIRROR_ORIENT_DRYRUN = self.chk_dryrun.isChecked()
        rt_cst.MIRROR_SOURCE_SIDE = self.cmb_source.currentText()
        rt_cst.MIRROR_AXIS = self.cmb_axis.currentText()
        rt_cst.ORIENT_AIM_AXIS = self.cmb_aim.currentText()
        rt_cst.ORIENT_UP_AXIS = self.cmb_up.currentText()

    def _mirror_pairs(self):
        '''(pairs, unpaired-sided) preview using the selected source side.'''
        prev = getattr(rt_cst, 'MIRROR_SOURCE_SIDE', 'R')
        rt_cst.MIRROR_SOURCE_SIDE = self.cmb_source.currentText()
        try:
            import rig_tail_setup as rt_set
            pairs, _ = rt_set.find_mirror_pairs(rt_cst.RIGPARTS)
        except Exception:
            pairs = []
        finally:
            rt_cst.MIRROR_SOURCE_SIDE = prev
        return pairs

    def update_display(self):
        parts = rt_cst.RIGPARTS
        pairs = self._mirror_pairs()
        pair_txt = ', '.join(f'{s}->{t}' for s, t in pairs) if pairs else '(none)'
        lines = [
            f'ROOT = {getattr(rt_cst, "ROOT", "")}',
            f'RIGPARTS ({len(parts)}): {", ".join(parts) if parts else "(empty)"}',
            f'Orient chains: {self.chk_orient.isChecked()}   '
            f'Mirror joints: {self.chk_mirror.isChecked()}   '
            f'Dry run: {self.chk_dryrun.isChecked()}',
            f'L/R pairs: {pair_txt}',
            f'aim={self.cmb_aim.currentText()}  up={self.cmb_up.currentText()}  '
            f'mirror axis={self.cmb_axis.currentText()}',
        ]
        self.txt_display.setText('\n'.join(lines))

    # ACTIONS ==========================================================

    def open_rigparts_editor(self):
        '''Reuse the Builder's RIGPARTS editor.'''
        dialog = rt_ui.RigPartsEditor(self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.update_display()

    def config_start_path(self):
        return getattr(rt_cst, 'CONFIG_FILE', '')

    def load_config(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Load Config', self.config_start_path(),
            'JSON Files (*.json);;All Files (*)')
        if not filepath:
            return
        if rt_cst.load_config(filepath):
            self.load_current_values()
            QtWidgets.QMessageBox.information(
                self, 'Success', f'Configuration loaded from:\n{filepath}')
        else:
            QtWidgets.QMessageBox.warning(
                self, 'Warning', f'Failed to load configuration from:\n{filepath}')

    def save_config(self):
        self.save_current_values()
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save Config', self.config_start_path(),
            'JSON Files (*.json);;All Files (*)')
        if not filepath:
            return
        if rt_cst.save_config(filepath):
            QtWidgets.QMessageBox.information(
                self, 'Success', f'Configuration saved to:\n{filepath}')
        else:
            QtWidgets.QMessageBox.critical(
                self, 'Error', f'Failed to save configuration to:\n{filepath}')

    def run_setup(self):
        '''Commit options and run the Setup phase on the skeleton.'''
        import rig_tail_setup as rt_set

        if not rt_cst.RIGPARTS:
            QtWidgets.QMessageBox.warning(self, 'Error',
                'RIGPARTS is empty. Add rig parts first.')
            return

        self.save_current_values()
        root = self.txt_root.text().strip() or None
        dry = self.chk_dryrun.isChecked()

        if not (self.chk_orient.isChecked() or self.chk_mirror.isChecked()):
            QtWidgets.QMessageBox.warning(self, 'Nothing to do',
                'Enable Orient Chains and/or Mirror Joints first.')
            return

        try:
            result = rt_set.setup_tails(root=root, dry_run=dry)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error',
                f'Setup failed:\n{str(e)}')
            return

        preview = ' (preview only, nothing changed)' if result.get('dry_run') else ''
        QtWidgets.QMessageBox.information(self, 'Setup complete',
            f"Oriented {result.get('oriented', 0)} joints, "
            f"mirrored {result.get('mirrored', 0)} joints{preview}.\n\n"
            'See the Script Editor log for per-chain details. '
            'Build the rig next.')
        self.update_display()

    def closeEvent(self, event):
        # Persist option state on close, like the Builder does
        self.save_current_values()
        super().closeEvent(event)


def get_maya_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def show_ui():
    '''Show the Tail Rig Setup window, closing any previous instance.'''
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
