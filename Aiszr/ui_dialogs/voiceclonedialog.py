"""VoiceCloneDialog — extracted from ui.py."""
from __future__ import annotations
import os
import threading
import wave
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


def _validate_wav_duration(path: str, max_seconds: float = 15.0) -> tuple[bool, str]:
    try:
        with wave.open(path, "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            duration = (frames / float(rate)) if rate else 0.0
    except Exception:
        return False, "WAV 文件读取失败，请重新选择有效的 wav 文件"
    if duration <= 0:
        return False, "WAV 文件时长无效，请重新选择"
    if duration > max_seconds:
        return False, f"WAV 时长 {duration:.2f}s，超过 {max_seconds:.0f}s 上限"
    return True, f"样本时长 {duration:.2f}s，校验通过"


class VoiceCloneDialog(QDialog):
    voice_action_requested = pyqtSignal(object)
    voice_settings_changed = pyqtSignal(object)
    _train_progress_sig = pyqtSignal(str)
    _train_done_sig = pyqtSignal(object)
    DEFAULT_SAMPLE_PATH = str(app_dir() / "data" / "voice" / "samples" / "anchor.wav")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("语音克隆")
        self.resize(560, 560)
        self.setModal(False)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())
        self._sample_path = ""
        self._training = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self._title_label = QLabel("语音克隆", self)
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        self._provider_hint = QLabel("", self)
        self._provider_hint.setWordWrap(True)
        layout.addWidget(self._provider_hint)

        name_row = QWidget(self)
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.addWidget(QLabel("声音名称："))
        self._name_edit = MacLineEdit(placeholder="输入声音名称")
        self._name_edit.setText("")
        self._name_edit.setFixedHeight(36)
        name_layout.addWidget(self._name_edit)
        layout.addWidget(name_row)

        upload_row = QWidget(self)
        upload_layout = QHBoxLayout(upload_row)
        upload_layout.setContentsMargins(0, 0, 0, 0)
        upload_layout.setSpacing(theme.SPACING_SM)
        self._upload_btn = MacButton("选择音频文件", variant="secondary", parent=self)
        self._upload_btn.setMinimumSize(140, 34)
        upload_layout.addWidget(self._upload_btn)
        upload_layout.addStretch(1)
        layout.addWidget(upload_row)

        self._sample_path_edit = MacLineEdit(placeholder="支持 wav/mp3/flac，可多选")
        self._sample_path_edit.setFixedHeight(36)
        self._sample_path_edit.setReadOnly(True)
        layout.addWidget(self._sample_path_edit)

        self._sample_label = QLabel("未选择样本")
        self._sample_label.setWordWrap(True)
        layout.addWidget(self._sample_label)

        self._progress_label = QLabel("", self)
        self._progress_label.setWordWrap(True)
        self._progress_label.setStyleSheet(
            f"color: {theme.CLR_ACCENT}; border: none; font-size: 13px;")
        layout.addWidget(self._progress_label)

        layout.addSpacing(6)

        btn_row = QWidget(self)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)

        self._clone_btn = MacButton("快速克隆", variant="primary", parent=self)
        self._clone_btn.setMinimumSize(120, 34)
        btn_layout.addWidget(self._clone_btn)

        self._train_clone_btn = MacButton("一键训练克隆", variant="primary", parent=self)
        self._train_clone_btn.setMinimumSize(140, 34)
        btn_layout.addWidget(self._train_clone_btn)

        btn_layout.addStretch(1)
        layout.addWidget(btn_row)

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
        self._train_clone_btn.clicked.connect(self._on_train_clone_clicked)
        self._train_progress_sig.connect(self._progress_label.setText)
        self._train_done_sig.connect(self._handle_train_result)
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        self.setStyleSheet(f"QDialog {{ background-color: {theme.CLR_BG}; }}")
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        for w in (self._name_edit, self._sample_path_edit,
                  self._upload_btn, self._clone_btn, self._train_clone_btn):
            w.apply_theme_styles()
        self._loading_label.setStyleSheet(
            f"color: {theme.CLR_ACCENT}; border: none; font-size: 14px;")

    def _update_provider_ui(self) -> None:
        provider = self._voice_settings_state.provider
        is_local = provider == "local_voice"
        self._train_clone_btn.setVisible(is_local)
        if is_local:
            self._provider_hint.setText(
                "当前使用 GPT-SoVITS 本地语音。\n"
                "• 快速克隆：使用参考音频直接推理（零样本，无需训练）\n"
                "• 一键训练克隆：微调训练模型，音色更精准自然"
            )
        else:
            self._provider_hint.setText(
                "当前使用阿里云百炼。\n上传参考音频后点击快速克隆，将音频上传到云端进行音色克隆。"
            )

    def _on_upload_clicked(self):
        provider = self._voice_settings_state.provider
        if provider == "local_voice":
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择音频文件", "",
                "Audio Files (*.wav *.mp3 *.flac);;WAV Files (*.wav);;All Files (*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 wav 样本", "", "WAV Files (*.wav)"
            )
            paths = [path] if path else []
        if not paths:
            return
        if provider == "local_voice":
            from voice_train_service import VoiceTrainService
            ok, msg, total_sec = VoiceTrainService.validate_audio_files(paths)
            if not ok:
                self._status_label.setText(msg)
                return
            self._sample_path = ";".join(paths)
            self._sample_path_edit.setText(self._sample_path)
            self._sample_label.setText(f"{len(paths)} 个文件，{msg}")
            self._status_label.setText("")
        else:
            path = paths[0]
            ok, message = _validate_wav_duration(path, 15.0)
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
        if any(v.name == name for v in self._voice_settings_state.voices):
            self._status_label.setText(f"声音名称「{name}」已存在，请使用其他名称")
            return
        provider = self._voice_settings_state.provider
        if provider == "local_voice":
            sample_ref = self._sample_path.split(";")[0] if self._sample_path else ""
        else:
            sample_ref = self._sample_path_edit.text().strip() or self.DEFAULT_SAMPLE_PATH
        if not sample_ref:
            self._status_label.setText("请先选择音频文件")
            return
        if not os.path.exists(sample_ref):
            self._status_label.setText(f"样本文件不存在：{sample_ref}")
            return
        if provider != "local_voice":
            ok, message = _validate_wav_duration(sample_ref, 15.0)
            if not ok:
                self._status_label.setText(message)
                return
        from voice_models import VoiceEntry
        entry = VoiceEntry(
            id=VoiceEntry.make_id(),
            name=name,
            provider=provider,
            sample_wav_path=sample_ref,
        )
        self._voice_settings_state.voices.append(entry)
        self._voice_settings_state.anchor.voice_id = entry.id
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
        self._status_label.setText("正在克隆...")
        self._clone_btn.setEnabled(False)
        self._clone_btn.setText("克隆中...")
        self._start_loading()
        action = {
            "type": "clone",
            "voice_id": entry.id,
            "settings": self._voice_settings_state.to_dict(),
        }
        QTimer.singleShot(120, lambda payload=action: self.voice_action_requested.emit(payload))

    def _on_train_clone_clicked(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("请输入声音名称")
            return
        if any(v.name == name for v in self._voice_settings_state.voices):
            self._status_label.setText(f"声音名称「{name}」已存在，请使用其他名称")
            return
        if not self._sample_path:
            self._status_label.setText("请先选择音频文件")
            return
        files = [p.strip() for p in self._sample_path.split(";") if p.strip()]
        if not files:
            self._status_label.setText("请先选择音频文件")
            return

        self._training = True
        self._clone_btn.setEnabled(False)
        self._train_clone_btn.setEnabled(False)
        self._train_clone_btn.setText("训练中...")
        self._start_loading()
        self._status_label.setText("准备训练...")
        self._progress_label.setText("")

        from voice_models import VoiceEntry
        entry = VoiceEntry(
            id=VoiceEntry.make_id(),
            name=name,
            provider="local_voice",
            sample_wav_path=files[0],
            clone_status="training",
        )
        self._voice_settings_state.voices.append(entry)
        self._voice_settings_state.anchor.voice_id = entry.id
        data = _load_settings()
        data["voice"] = self._voice_settings_state.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(self._voice_settings_state.to_dict())

        def _run_training():
            try:
                from local_voice_runtime import resolve_gpt_sovits_root, resolve_python_exe
                from voice_train_service import VoiceTrainService, TrainProgress

                root = resolve_gpt_sovits_root()
                python = resolve_python_exe()

                def on_progress(p: TrainProgress):
                    self._train_progress_sig.emit(
                        f"[{p.step}] {p.percent}% — {p.message}"
                    )

                service = VoiceTrainService(root, python, on_progress)
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    results = loop.run_until_complete(
                        service.run_full_pipeline(
                            input_files=files,
                            voice_name=name,
                            language=self._voice_settings_state.api["local_voice"].text_lang or "zh",
                        )
                    )
                finally:
                    loop.close()
                return results, entry.id
            except Exception as e:
                return None, str(e)

        def _thread_wrapper():
            result = _run_training()
            self._train_done_sig.emit(result)

        threading.Thread(target=_thread_wrapper, daemon=True).start()

    def _handle_train_result(self, result):
        results, voice_id_or_error = result
        self._training = False
        self._clone_btn.setEnabled(True)
        self._train_clone_btn.setEnabled(True)
        self._train_clone_btn.setText("一键训练克隆")
        self._stop_loading()

        if results is None:
            self._status_label.setText(f"训练失败：{voice_id_or_error}")
            return

        voice = self._voice_settings_state.find_voice(voice_id_or_error)
        if voice:
            voice.clone_status = "ready"
            voice.clone_voice_id = results.get("ref_audio", "")
            voice.trained_model_dir = results.get("model_dir", "")
            voice.last_error = ""
            # 用训练参考音频对应的 ASR 文本更新 prompt_text/prompt_lang
            local_api = self._voice_settings_state.api.get("local_voice")
            prompt_text = results.get("prompt_text", "")
            if prompt_text and local_api:
                local_api.prompt_text = prompt_text
                local_api.prompt_lang = results.get("prompt_lang", "zh") or "zh"
            data = _load_settings()
            data["voice"] = self._voice_settings_state.to_dict()
            _save_settings(data)
            self.voice_settings_changed.emit(self._voice_settings_state.to_dict())
        self._status_label.setText(
            f"训练完成！GPT: {results.get('gpt_ckpt', 'N/A')}\n"
            f"SoVITS: {results.get('sovits_ckpt', 'N/A')}"
        )

    def load_voice_settings(self, value: object):
        settings = VoiceSettings.from_dict(value)
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())
        self._update_provider_ui()

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
            self._clone_btn.setText("快速克隆")
            self._stop_loading()


# ---------------------------------------------------------------------------
# AnchorSettingsDialog
# ---------------------------------------------------------------------------
