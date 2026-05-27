"""VoiceCloneDialog — extracted from ui.py."""
from __future__ import annotations
from app_paths import app_dir
from ui_constants import DEFAULT_VOICE_SETTINGS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import _build_input_field_stylesheet
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme
from voice_manager import VoiceActionResult
from voice_models import VoiceSettings


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QBrush, QIcon
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QFormLayout,
    QDialogButtonBox, QFrame, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QListWidgetItem, QMessageBox, QFileDialog, QLabel, QPushButton, QLineEdit, QCheckBox, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog, QFrame, QSpinBox, QDoubleSpinBox, QComboBox, QListWidgetItem)
from siui.core import SiGlobal
from siui.components.widgets import SiPushButton, SiDenseHContainer, SiLineEdit, SiLabel
from siui.components.combobox.combobox import SiComboBox
from siui.components.option_card import SiOptionCardLinear
import ui_theme as theme
from ui_settings import _save_settings
from ui_components import MacButton, MacLineEdit


class VoiceCloneDialog(QDialog):
    voice_action_requested = pyqtSignal(object)
    voice_settings_changed = pyqtSignal(object)
    DEFAULT_SAMPLE_PATH = str(app_dir() / "data" / "voice" / "samples" / "anchor.wav")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("语音克隆")
        self.resize(560, 440)
        self.setModal(False)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())
        self._sample_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self._title_label = QLabel("语音克隆", self)
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        self._subtitle_label = QLabel("选择本地 wav 文件，使用阿里云在线克隆新声音", self)
        layout.addWidget(self._subtitle_label)

        self._name_edit = MacLineEdit(placeholder="输入声音名称")
        self._name_edit.setText("001")
        self._name_edit.setFixedHeight(36)
        layout.addWidget(self._name_edit)

        upload_row = QWidget(self)
        upload_layout = QHBoxLayout(upload_row)
        upload_layout.setContentsMargins(0, 0, 0, 0)
        upload_layout.setSpacing(theme.SPACING_SM)
        self._upload_btn = MacButton("选择 wav", variant="secondary", parent=self)
        self._upload_btn.setMinimumSize(140, 34)
        upload_layout.addWidget(self._upload_btn)
        upload_layout.addStretch(1)
        layout.addWidget(upload_row)

        self._sample_path_edit = MacLineEdit(placeholder="本地 wav 路径")
        self._sample_path_edit.setText(self.DEFAULT_SAMPLE_PATH)
        self._sample_path_edit.setFixedHeight(36)
        layout.addWidget(self._sample_path_edit)

        self._sample_label = QLabel("未选择样本", self)
        self._sample_label.setWordWrap(True)
        layout.addWidget(self._sample_label)

        layout.addSpacing(6)
        self._clone_btn = MacButton("开始克隆", variant="primary", parent=self)
        self._clone_btn.setMinimumSize(140, 34)
        layout.addWidget(self._clone_btn)

        self._loading_label = QLabel("", self)
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_label.setFixedHeight(20)
        self._loading_label.hide()
        layout.addWidget(self._loading_label)

        self._loading_dots_timer = QTimer(self)
        self._loading_dots_count = 0
        self._loading_dots_timer.timeout.connect(self._tick_loading_dots)

        layout.addSpacing(4)
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self._upload_btn.clicked.connect(self._on_upload_clicked)
        self._clone_btn.clicked.connect(self._on_clone_clicked)
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        self.setStyleSheet(f"QDialog {{ background-color: {theme.CLR_BG}; }}")
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        for w in (self._name_edit, self._sample_path_edit,
                  self._upload_btn, self._clone_btn):
            w.apply_theme_styles()
        # Loading label stays accent — it's a status indicator
        self._loading_label.setStyleSheet(
            f"color: {theme.CLR_ACCENT}; border: none; font-size: 14px;")

    def _on_upload_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 wav 样本", "", "WAV Files (*.wav)")
        if not path:
            return
        ok, message = VoiceConfigPage._validate_wav_duration(path, 15.0)
        if not ok:
            self._status_label.setText(message)
            return
        self._sample_path = path
        self._sample_path_edit.setText(path)
        self._sample_label.setText(path)
        self._status_label.setText(message)

    def _tick_loading_dots(self):
        self._loading_dots_count = (self._loading_dots_count + 1) % 4
        self._loading_label.setText("●  " * (self._loading_dots_count + 1))

    def _start_loading(self):
        self._loading_dots_count = 0
        self._loading_label.show()
        self._loading_dots_timer.start(400)

    def _stop_loading(self):
        self._loading_dots_timer.stop()
        self._loading_label.hide()

    def _on_clone_clicked(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("请输入声音名称")
            return
        sample_ref = self._sample_path_edit.text().strip() or self.DEFAULT_SAMPLE_PATH
        if not sample_ref:
            self._status_label.setText("请先选择 wav 样本")
            return
        if not os.path.exists(sample_ref):
            self._status_label.setText(f"样本文件不存在：{sample_ref}")
            return
        ok, message = VoiceConfigPage._validate_wav_duration(sample_ref, 15.0)
        if not ok:
            self._status_label.setText(message)
            return
        from voice_models import VoiceEntry
        entry = VoiceEntry(
            id=VoiceEntry.make_id(),
            name=name,
            sample_wav_path=sample_ref,
        )
        self._voice_settings_state.voices.append(entry)
        self._voice_settings_state.anchor.voice_id = entry.id
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
        self._status_label.setText("正在上传并克隆...")
        self._clone_btn.setEnabled(False)
        self._clone_btn.setText("克隆中...")
        self._start_loading()
        action = {
            "type": "clone",
            "voice_id": entry.id,
            "settings": self._voice_settings_state.to_dict(),
        }
        QTimer.singleShot(120, lambda payload=action: self.voice_action_requested.emit(payload))

    def load_voice_settings(self, value: object):
        settings = VoiceSettings.from_dict(value)
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())

    def handle_voice_action_result(self, payload: object):
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if not isinstance(result, VoiceActionResult):
            return
        action_type = payload.get("type", "")
        if action_type == "clone":
            voice_id = payload.get("voice_id", "")
            next_settings = VoiceSettings.from_dict(payload.get("settings") or self._voice_settings_state.to_dict())
            voice = next_settings.find_voice(voice_id)
            if voice:
                if result.ok:
                    voice.clone_voice_id = result.clone_voice_id or voice.clone_voice_id
                    voice.clone_status = result.clone_status or "ready"
                    voice.last_error = ""
                else:
                    voice.clone_status = result.clone_status or "error"
                    voice.last_error = result.message
                self._voice_settings_state = VoiceSettings.from_dict(next_settings.to_dict())
                data = _load_settings()
                data["voice"] = self._voice_settings_state.to_dict()
                _save_settings(data)
                self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
            self._status_label.setText(result.message)
            self._clone_btn.setEnabled(True)
            self._clone_btn.setText("开始克隆")
            self._stop_loading()


# ---------------------------------------------------------------------------
# AnchorSettingsDialog
# ---------------------------------------------------------------------------

