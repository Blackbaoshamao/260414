"""CopilotSettingsDialog — extracted from ui.py."""
from __future__ import annotations
from ui_constants import DEFAULT_VOICE_SETTINGS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme
from voice_models import VoiceSettings


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt
from PyQt5.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QBrush, QIcon
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QFormLayout,
    QDialogButtonBox, QFrame, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QListWidgetItem, QMessageBox, QLabel, QPushButton, QLineEdit, QCheckBox, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFrame, QSpinBox, QDoubleSpinBox, QComboBox, QListWidgetItem)
from siui.core import SiGlobal
from siui.components.widgets import SiPushButton, SiDenseHContainer, SiLineEdit, SiLabel
from siui.components.combobox.combobox import SiComboBox
from siui.components.option_card import SiOptionCardLinear
import ui_theme as theme
from ui_settings import _save_settings
from ui_components import MacButton


class CopilotSettingsDialog(QDialog):
    voice_settings_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("助播设置")
        self.resize(420, 300)
        self.setModal(False)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self._title_label = QLabel("助播设置", self)
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        self._auto_broadcast_check = QCheckBox("跟随 AI 回复自动播报", self)
        layout.addWidget(self._auto_broadcast_check)

        self._auto_reply_check = QCheckBox("自动回复弹幕（开发中）", self)
        self._auto_reply_check.setEnabled(False)
        layout.addWidget(self._auto_reply_check)

        layout.addStretch(1)

        self._save_btn = MacButton("保存", variant="primary", parent=self)
        self._save_btn.setMinimumSize(100, 34)
        layout.addWidget(self._save_btn)

        self._status_label = QLabel("", self)
        layout.addWidget(self._status_label)

        self._save_btn.clicked.connect(self._on_save)
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        self.setStyleSheet(f"QDialog {{ background-color: {theme.CLR_BG}; }}")
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        self._save_btn.apply_theme_styles()
        # Checkboxes / status label inherit from global rules

    def _on_save(self):
        self._voice_settings_state.copilot_auto_broadcast = self._auto_broadcast_check.isChecked()
        self._voice_settings_state.copilot_auto_reply = self._auto_reply_check.isChecked()
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
        self._status_label.setText("已保存")

    def load_voice_settings(self, value: object):
        settings = VoiceSettings.from_dict(value)
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())
        self._auto_broadcast_check.blockSignals(True)
        self._auto_broadcast_check.setChecked(settings.copilot_auto_broadcast)
        self._auto_broadcast_check.blockSignals(False)
        self._auto_reply_check.blockSignals(True)
        self._auto_reply_check.setChecked(settings.copilot_auto_reply)
        self._auto_reply_check.blockSignals(False)


# ---------------------------------------------------------------------------
# ObsRuleDialog
# ---------------------------------------------------------------------------

