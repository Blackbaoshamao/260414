"""VoiceConfigPage — extracted from ui.py."""
from __future__ import annotations
import wave
from app_paths import app_dir
from ui_constants import DEFAULT_VOICE_SETTINGS
from ui_constants import _SECRET_INPUT_FIELD_KEYS
from ui_constants import _VOICE_PROVIDER_API_FIELDS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import _build_input_field_stylesheet
from ui_theme import _build_text_area_stylesheet
from ui_theme import _install_secret_reveal_action
from ui_theme import apply_theme
from ui_theme import patch_setting_card_padding
from voice_manager import VoiceActionResult
from voice_models import VOICE_MODELS
from voice_models import VOICE_PROVIDERS
from voice_models import VOICE_PROVIDER_LABELS
from voice_models import VoiceSettings
from voice_models import compatible_voice_entries


from pathlib import Path

from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QTimer, QRect, QSize, QPropertyAnimation, QEasingCurve, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush, QIcon
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog,
    QInputDialog, QFrame, QAbstractButton, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QComboBox, QScrollArea, QApplication, QApplication, QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog, QInputDialog, QFrame, QAbstractButton, QSpinBox, QDoubleSpinBox, QSizePolicy, QComboBox, QScrollArea)
from qfluentwidgets import SettingCard, FluentIcon
from fluent_page import FluentPage
import ui_theme as theme
from loguru import logger
from ui_constants import _CARD_W, _CARD_H
from ui_components import AddCard as _AddCard, VideoThumbCard as _VideoThumbCard
from ui_components import MacButton
from avatar_library import (
    AvatarLibrary,
    DEFAULT_QUALITY,
    MAX_AVATAR_RECORDS,
    STATUS_CHECKING,
    STATUS_FAILED,
    STATUS_IMPORTED,
    STATUS_NORMALIZING,
    STATUS_PROCESSING_CPU,
    STATUS_PROCESSING_GPU,
    STATUS_READY,
)
from avatar_preprocessor import AvatarPreprocessManager
from livetalking_runtime import default_livetalking_root


class _HalfHighlightTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        font = QFont(self.font())
        font.setPixelSize(20)
        font.setBold(True)
        self.setFont(font)
        self.setFixedHeight(26)
        self.setStyleSheet("background: transparent; border: none;")

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 10, 26)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        text_width = self.fontMetrics().horizontalAdvance(self.text())
        highlight = QColor(theme.CLR_ACCENT_LIGHT)
        highlight.setAlpha(95)
        painter.setBrush(QBrush(highlight))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(
            QRect(0, self.height() - 10, min(self.width(), text_width + 10), 8),
            4,
            4,
        )

        painter.setPen(QColor(theme.CLR_TEXT_PRI))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, self.text())
        painter.end()


def _voice_display_name(voice) -> str:
    """Return display name with provider suffix, e.g. '000-阿里云百炼'."""
    name = voice.name or voice.id
    label = VOICE_PROVIDER_LABELS.get(voice.provider, "")
    if label and not name.endswith(f"-{label}"):
        return f"{name}-{label}"
    return name


class VoiceConfigPage(FluentPage):
    _GALLERY_COLUMNS = 3
    _GALLERY_GAP = 12

    api_settings_requested = pyqtSignal()
    clone_dialog_requested = pyqtSignal()
    anchor_settings_requested = pyqtSignal()
    copilot_settings_requested = pyqtSignal()
    voice_settings_changed = pyqtSignal(object)
    voice_action_requested = pyqtSignal(object)
    # Streaming console signals (merged from DigitalHumanPage).
    digital_human_start_requested = pyqtSignal(object)
    digital_human_stop_requested = pyqtSignal()
    # Emitted when the streaming-relevant configuration changes so HomePage can
    # update its pre-stream checklist (e.g. anchor_voice / green_screen).
    streaming_config_changed = pyqtSignal(object)
    avatar_preprocess_changed = pyqtSignal(object)
    _ROLE_LABELS = {"anchor": "主播", "copilot": "助播"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(820)
        self._voice_runtime_payload = {}
        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())
        self._active_provider = self._voice_settings_state.provider
        self._preview_playing_role: str | None = None
        self._preview_token = 0
        self._active_preview_token: int | None = None
        self._api_field_panels: dict[str, QWidget] = {}
        self._api_field_labels: dict[str, QLabel] = {}
        self._api_field_edits: dict[str, QLineEdit] = {}
        self._voice_param_labels: list[QLabel] = []
        self._inline_api_visible = False
        # Streaming-console state (merged from DigitalHumanPage). The same
        # backend signals (digital_human_start_requested / stop_requested) are
        # emitted from the buttons we build below.
        self._video_paths: list[str] = []
        self._selected_index: int = -1
        self._avatar_library = AvatarLibrary()
        self.avatar_preprocess_changed.connect(self._on_avatar_preprocess_changed)
        self._avatar_preprocessor = AvatarPreprocessManager(
            self._avatar_library,
            status_callback=self.avatar_preprocess_changed.emit,
            log_callback=lambda msg: logger.info("AvatarPreprocess: {}", msg),
        )
        self._obs_host = "127.0.0.1"
        self._obs_port = 4455
        self._obs_password = ""
        self._livetalking_audio_folder_path: str = str(
            app_dir() / "data" / "voice" / "anchor" / "generated"
        )

        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 0, 8, 0)
        container_layout.setSpacing(8)

        field_ss = _build_input_field_stylesheet(
            "QLineEdit, QSpinBox", radius=theme.RADIUS_MD, padding="4px 8px")

        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(theme.SPACING_SM)
        top_layout.addStretch(1)
        self._voice_api_btn = MacButton("语音API设置", variant="secondary", parent=self)
        self._voice_api_btn.setMinimumSize(120, 30)
        self._voice_api_btn.clicked.connect(self.api_settings_requested.emit)
        top_layout.addWidget(self._voice_api_btn)
        self._voice_clone_btn = MacButton("语音克隆", variant="secondary", parent=self)
        self._voice_clone_btn.setMinimumSize(100, 30)
        self._voice_clone_btn.clicked.connect(self.clone_dialog_requested.emit)
        top_layout.addWidget(self._voice_clone_btn)
        self._anchor_settings_btn = MacButton("主播设置", variant="secondary", parent=self)
        self._anchor_settings_btn.setMinimumSize(100, 30)
        self._anchor_settings_btn.clicked.connect(self.anchor_settings_requested.emit)
        top_layout.addWidget(self._anchor_settings_btn)
        self._copilot_settings_btn = MacButton("助播设置", variant="secondary", parent=self)
        self._copilot_settings_btn.setMinimumSize(100, 30)
        self._copilot_settings_btn.clicked.connect(self.copilot_settings_requested.emit)
        top_layout.addWidget(self._copilot_settings_btn)
        container_layout.addWidget(top_row)
        self._back_area = top_row

        provider_card = SettingCard(FluentIcon.INFO, "语音供应商", "主播和助播共用同一供应商与模型", parent=self)
        self._provider_combo = QComboBox(self)
        self._provider_combo.setFixedSize(220, 32)
        for provider in VOICE_PROVIDERS:
            self._provider_combo.addItem(VOICE_PROVIDER_LABELS[provider], provider)
        provider_card.hBoxLayout.addWidget(self._provider_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(provider_card)
        if self._inline_api_visible:
            container_layout.addWidget(provider_card)
        self._provider_card = provider_card

        model_card = SettingCard(FluentIcon.ROBOT, "语音模型", "根据供应商切换模型", parent=self)
        self._model_combo = QComboBox(self)
        self._model_combo.setFixedSize(220, 32)
        model_card.hBoxLayout.addWidget(self._model_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(model_card)
        if self._inline_api_visible:
            container_layout.addWidget(model_card)
        self._model_card = model_card

        self._api_provider_title = QLabel("当前供应商凭据", self)
        self._api_provider_title.setWordWrap(True)
        self._api_provider_title.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none;")
        if self._inline_api_visible:
            container_layout.addWidget(self._api_provider_title)
        self._api_fields_wrap = QWidget(self)
        self._api_fields_layout = QVBoxLayout(self._api_fields_wrap)
        self._api_fields_layout.setContentsMargins(0, 0, 0, 0)
        self._api_fields_layout.setSpacing(8)
        for field_key, attr_name in (
            ("app_id", "_api_app_id"),
            ("api_key", "_api_key"),
            ("api_secret", "_api_secret"),
            ("access_key_id", "_api_access_key_id"),
            ("access_key_secret", "_api_access_key_secret"),
            ("endpoint", "_api_endpoint"),
            ("region", "_api_region"),
            ("reference_audio", "_api_reference_audio"),
            ("prompt_text", "_api_prompt_text"),
            ("prompt_lang", "_api_prompt_lang"),
            ("text_lang", "_api_text_lang"),
        ):
            panel, label, edit = self._create_api_field_panel("", "")
            self._api_field_panels[field_key] = panel
            self._api_field_labels[field_key] = label
            self._api_field_edits[field_key] = edit
            if field_key in _SECRET_INPUT_FIELD_KEYS:
                _install_secret_reveal_action(edit)
            setattr(self, attr_name, edit)
            self._api_fields_layout.addWidget(panel)
        if self._inline_api_visible:
            container_layout.addWidget(self._api_fields_wrap)

        api_btn_row = QHBoxLayout()
        api_btn_row.setContentsMargins(0, 14, 0, 4)
        api_btn_row.setSpacing(10)
        self._voice_save_btn = MacButton("保存语音配置", variant="primary", parent=self)
        self._voice_save_btn.setMinimumSize(136, 36)
        self._voice_validate_btn = MacButton("测试连接", variant="secondary", parent=self)
        self._voice_validate_btn.setMinimumSize(112, 36)
        api_btn_row.addWidget(self._voice_save_btn)
        api_btn_row.addWidget(self._voice_validate_btn)
        api_btn_row.addStretch(1)
        self._api_btn_wrap = QWidget(self)
        self._api_btn_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Explicit height so the row is tall enough for its inner buttons.
        self._api_btn_wrap.setFixedHeight(54)
        self._api_btn_wrap.setLayout(api_btn_row)
        if self._inline_api_visible:
            container_layout.addWidget(self._api_btn_wrap)

        self._voice_status_label = QLabel("未检测", self)
        self._voice_status_label.setWordWrap(True)
        self._voice_status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none;")
        if self._inline_api_visible:
            container_layout.addWidget(self._voice_status_label)
        else:
            self._hide_inline_api_widgets()

        _title = QLabel("主播音色", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)
        anchor_card = SettingCard(FluentIcon.MUSIC, "选择声音", "从声音库中选择主播声音", parent=self)
        self._anchor_voice_combo = QComboBox(self)
        self._anchor_voice_combo.setFixedSize(220, 32)
        anchor_card.hBoxLayout.addWidget(self._anchor_voice_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(anchor_card)
        container_layout.addWidget(anchor_card)
        anchor_params = QWidget(self)
        anchor_params.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        anchor_params.setFixedHeight(56)
        anchor_params_layout = QHBoxLayout(anchor_params)
        anchor_params_layout.setContentsMargins(0, 10, 0, 10)
        anchor_params_layout.setSpacing(8)
        self._anchor_speed = QSpinBox(self)
        self._anchor_speed.setRange(50, 150)
        self._anchor_speed.setSuffix("%")
        self._anchor_speed.setValue(100)
        self._anchor_speed.setFixedSize(90, 32)
        self._anchor_speed.setStyleSheet(field_ss)
        self._anchor_volume = QSpinBox(self)
        self._anchor_volume.setRange(0, 200)
        self._anchor_volume.setSuffix("%")
        self._anchor_volume.setValue(100)
        self._anchor_volume.setFixedSize(90, 32)
        self._anchor_volume.setStyleSheet(field_ss)
        self._anchor_speed_label = QLabel("语速", self)
        self._voice_param_labels.append(self._anchor_speed_label)
        anchor_params_layout.addWidget(self._anchor_speed_label)
        anchor_params_layout.addWidget(self._anchor_speed)
        self._anchor_volume_label = QLabel("音量", self)
        self._voice_param_labels.append(self._anchor_volume_label)
        anchor_params_layout.addWidget(self._anchor_volume_label)
        anchor_params_layout.addWidget(self._anchor_volume)
        anchor_params_layout.addStretch(1)
        self._anchor_preview_btn = MacButton("▶ 试听", variant="primary", parent=self)
        self._anchor_preview_btn.setMinimumSize(72, 34)
        self._anchor_preview_btn.setFixedHeight(34)
        anchor_params_layout.addWidget(self._anchor_preview_btn)
        self._anchor_delete_btn = MacButton("删除音色", variant="destructive", parent=self)
        self._anchor_delete_btn.setMinimumSize(88, 34)
        self._anchor_delete_btn.setFixedHeight(34)
        anchor_params_layout.addWidget(self._anchor_delete_btn)
        container_layout.addWidget(anchor_params)
        self._copilot_runtime_status = QLabel("最近播报：无", self)
        self._copilot_runtime_status.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none;")
        container_layout.addWidget(self._copilot_runtime_status)

        _title = QLabel("助播音色", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)
        copilot_card = SettingCard(FluentIcon.MUSIC, "选择声音", "从声音库中选择助播声音", parent=self)
        self._copilot_voice_combo = QComboBox(self)
        self._copilot_voice_combo.setFixedSize(220, 32)
        copilot_card.hBoxLayout.addWidget(self._copilot_voice_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(copilot_card)
        container_layout.addWidget(copilot_card)
        copilot_params = QWidget(self)
        copilot_params.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        copilot_params.setFixedHeight(56)
        copilot_params_layout = QHBoxLayout(copilot_params)
        copilot_params_layout.setContentsMargins(0, 10, 0, 10)
        copilot_params_layout.setSpacing(8)
        self._copilot_speed = QSpinBox(self)
        self._copilot_speed.setRange(50, 150)
        self._copilot_speed.setSuffix("%")
        self._copilot_speed.setValue(100)
        self._copilot_speed.setFixedSize(90, 32)
        self._copilot_speed.setStyleSheet(field_ss)
        self._copilot_volume = QSpinBox(self)
        self._copilot_volume.setRange(0, 200)
        self._copilot_volume.setSuffix("%")
        self._copilot_volume.setValue(100)
        self._copilot_volume.setFixedSize(90, 32)
        self._copilot_volume.setStyleSheet(field_ss)
        self._copilot_speed_label = QLabel("语速", self)
        self._voice_param_labels.append(self._copilot_speed_label)
        copilot_params_layout.addWidget(self._copilot_speed_label)
        copilot_params_layout.addWidget(self._copilot_speed)
        self._copilot_volume_label = QLabel("音量", self)
        self._voice_param_labels.append(self._copilot_volume_label)
        copilot_params_layout.addWidget(self._copilot_volume_label)
        copilot_params_layout.addWidget(self._copilot_volume)
        copilot_params_layout.addStretch(1)
        self._copilot_preview_btn = MacButton("▶ 试听", variant="primary", parent=self)
        self._copilot_preview_btn.setMinimumSize(72, 34)
        self._copilot_preview_btn.setFixedHeight(34)
        copilot_params_layout.addWidget(self._copilot_preview_btn)
        self._copilot_delete_btn = MacButton("删除音色", variant="destructive", parent=self)
        self._copilot_delete_btn.setMinimumSize(88, 34)
        self._copilot_delete_btn.setFixedHeight(34)
        copilot_params_layout.addWidget(self._copilot_delete_btn)
        container_layout.addWidget(copilot_params)

        # ------------------------------------------------------------------
        # Streaming console (merged from DigitalHumanPage)
        # ------------------------------------------------------------------
        _title = QLabel("主播形象（右键移除，左键选中）", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)
        gallery_stream_row = QWidget(self)
        self._gallery_stream_row = gallery_stream_row
        gallery_area_height = self._gallery_height_for_count(MAX_AVATAR_RECORDS)
        self._gallery_stream_row.setFixedHeight(gallery_area_height)
        self._gallery_stream_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        gallery_stream_layout = QHBoxLayout(gallery_stream_row)
        gallery_stream_layout.setContentsMargins(0, 0, 0, 0)
        gallery_stream_layout.setSpacing(36)

        self._gallery_inner = QWidget(gallery_stream_row)
        self._gallery_inner.setFixedHeight(gallery_area_height)
        self._gallery_inner.setStyleSheet("background: transparent;")
        self._gallery_inner.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._gallery_layout = QGridLayout(self._gallery_inner)
        self._gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._gallery_layout.setHorizontalSpacing(self._GALLERY_GAP)
        self._gallery_layout.setVerticalSpacing(self._GALLERY_GAP)
        self._gallery_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        gallery_stream_layout.addWidget(self._gallery_inner, 0, Qt.AlignLeft | Qt.AlignTop)

        stream_panel = QWidget(gallery_stream_row)
        stream_panel.setMinimumWidth(260)
        stream_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        stream_panel_layout = QVBoxLayout(stream_panel)
        stream_panel_layout.setContentsMargins(0, 0, 0, 0)
        stream_panel_layout.setSpacing(10)

        stream_title_row = QWidget(stream_panel)
        stream_title_layout = QHBoxLayout(stream_title_row)
        stream_title_layout.setContentsMargins(0, 0, 0, 0)
        stream_title_layout.setSpacing(8)
        self._stream_title_marker = QFrame(stream_title_row)
        self._stream_title_marker.setFixedSize(4, 20)
        self._stream_title_marker.setStyleSheet(
            f"background-color: {theme.CLR_ACCENT}; border: none; border-radius: 2px;"
        )
        self._stream_title_label = _HalfHighlightTitle("推流控制", stream_title_row)
        self._apply_stream_title_style()
        stream_title_layout.addWidget(self._stream_title_marker)
        stream_title_layout.addWidget(self._stream_title_label)
        stream_title_layout.addStretch(1)
        stream_panel_layout.addWidget(stream_title_row)

        self._stream_status_label = QLabel("空闲", stream_panel)
        self._stream_status_label.setWordWrap(True)
        self._stream_status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none; font-size: 13px;")
        stream_panel_layout.addWidget(self._stream_status_label)

        stream_btn_row = QWidget(stream_panel)
        stream_btn_row.setFixedHeight(46)
        stream_btn_layout = QHBoxLayout(stream_btn_row)
        stream_btn_layout.setContentsMargins(0, 2, 0, 2)
        stream_btn_layout.setSpacing(8)
        self._stream_start_btn = MacButton("一键推流", variant="primary", parent=stream_panel)
        self._stream_start_btn.setMinimumSize(120, 38)
        self._stream_start_btn.clicked.connect(self._on_stream_start)
        stream_btn_layout.addWidget(self._stream_start_btn)
        self._stream_stop_btn = MacButton("停止推流", variant="secondary", parent=stream_panel)
        self._stream_stop_btn.setMinimumSize(100, 38)
        self._stream_stop_btn.clicked.connect(self._on_stream_stop)
        stream_btn_layout.addWidget(self._stream_stop_btn)
        stream_btn_layout.addStretch(1)
        stream_panel_layout.addWidget(stream_btn_row)
        stream_panel_layout.addStretch(1)
        gallery_stream_layout.addWidget(stream_panel, 1, Qt.AlignTop)
        container_layout.addWidget(gallery_stream_row)
        self._rebuild_gallery()

        self.setAttachment(container)

        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._voice_save_btn.clicked.connect(self._save_voice_settings)
        self._voice_validate_btn.clicked.connect(lambda: self.voice_action_requested.emit({"type": "validate_provider"}))
        self._anchor_preview_btn.clicked.connect(lambda: self._on_preview_clicked("anchor"))
        self._copilot_preview_btn.clicked.connect(lambda: self._on_preview_clicked("copilot"))
        self._anchor_delete_btn.clicked.connect(lambda: self._delete_selected_voice("anchor"))
        self._copilot_delete_btn.clicked.connect(lambda: self._delete_selected_voice("copilot"))
        self._anchor_voice_combo.currentIndexChanged.connect(self._on_anchor_voice_changed)
        self._copilot_voice_combo.currentIndexChanged.connect(self._on_copilot_voice_changed)
        self._apply_provider_models(DEFAULT_VOICE_SETTINGS.provider, DEFAULT_VOICE_SETTINGS.model_id)
        self._load_provider_api_inputs(DEFAULT_VOICE_SETTINGS.provider)
        self._apply_provider_fields(DEFAULT_VOICE_SETTINGS.provider)
        self._apply_theme_styles()

    def _create_api_field_panel(self, label_text: str, placeholder: str) -> tuple[QWidget, QLabel, QLineEdit]:
        panel = QWidget(self)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        panel.setFixedHeight(57)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text, panel)
        edit = QLineEdit(panel)
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(36)
        layout.addWidget(label)
        layout.addWidget(edit)
        return panel, label, edit

    def _apply_stream_title_style(self):
        if hasattr(self, "_stream_title_marker"):
            self._stream_title_marker.setStyleSheet(
                f"background-color: {theme.CLR_ACCENT}; border: none; border-radius: 2px;"
            )
        if hasattr(self, "_stream_title_label"):
            self._stream_title_label.update()

    def _apply_theme_styles(self):
        self._apply_stream_title_style()

        field_ss = _build_input_field_stylesheet(
            "QLineEdit, QSpinBox", radius=theme.RADIUS_MD)
        combo_ss = _build_input_field_stylesheet(
            "QComboBox", radius=theme.RADIUS_MD, include_combo=True)

        for combo in (self._provider_combo, self._model_combo,
                      self._anchor_voice_combo, self._copilot_voice_combo):
            combo.setStyleSheet(combo_ss)

        for field in (
            self._api_app_id, self._api_key, self._api_secret,
            self._api_access_key_id, self._api_access_key_secret,
            self._api_endpoint, self._api_region,
            self._anchor_speed, self._anchor_volume,
            self._copilot_speed, self._copilot_volume,
        ):
            field.setStyleSheet(field_ss)
        for field_key, edit in self._api_field_edits.items():
            if field_key in _SECRET_INPUT_FIELD_KEYS:
                _install_secret_reveal_action(edit)

        # All MacButton instances self-manage styles via apply_theme_styles()
        for btn in (
            self._voice_api_btn, self._voice_clone_btn,
            self._anchor_settings_btn, self._copilot_settings_btn,
            self._voice_save_btn, self._voice_validate_btn,
            self._anchor_preview_btn, self._copilot_preview_btn,
            self._anchor_delete_btn, self._copilot_delete_btn,
            self._stream_start_btn, self._stream_stop_btn,
        ):
            btn.apply_theme_styles()

        # Inline labels — clear stylesheet so they inherit global QLabel rule
        for label in (
            self._api_provider_title, self._voice_status_label,
            self._copilot_runtime_status, *self._voice_param_labels,
        ):
            label.setStyleSheet("")
        # API field labels — emphasised primary text
        for label in self._api_field_labels.values():
            label.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; border: none; "
                f"background: transparent; font-weight: 600;"
            )
        if hasattr(self, "_stream_status_label"):
            self._stream_status_label.setStyleSheet("")

    def _current_provider(self) -> str:
        value = self._provider_combo.currentData()
        return value if value in VOICE_PROVIDERS else DEFAULT_VOICE_SETTINGS.provider

    def _sync_provider_api_inputs(self, provider: str | None = None):
        provider_name = provider or self._active_provider
        if provider_name not in VOICE_PROVIDERS:
            return
        api_cfg = self._voice_settings_state.api[provider_name]
        api_cfg.app_id = self._api_app_id.text().strip()
        api_cfg.api_key = self._api_key.text().strip()
        api_cfg.api_secret = self._api_secret.text().strip()
        api_cfg.access_key_id = self._api_access_key_id.text().strip()
        api_cfg.access_key_secret = self._api_access_key_secret.text().strip()
        api_cfg.endpoint = self._api_endpoint.text().strip()
        api_cfg.region = self._api_region.text().strip()
        api_cfg.reference_audio = self._api_reference_audio.text().strip()
        api_cfg.prompt_text = self._api_prompt_text.text().strip()
        api_cfg.prompt_lang = self._api_prompt_lang.text().strip() or "zh"
        api_cfg.text_lang = self._api_text_lang.text().strip() or "zh"

    def _load_provider_api_inputs(self, provider: str):
        if provider not in VOICE_PROVIDERS:
            return
        api_cfg = self._voice_settings_state.api[provider]
        self._api_app_id.setText(api_cfg.app_id)
        self._api_key.setText(api_cfg.api_key)
        self._api_secret.setText(api_cfg.api_secret)
        self._api_access_key_id.setText(api_cfg.access_key_id)
        self._api_access_key_secret.setText(api_cfg.access_key_secret)
        self._api_endpoint.setText(api_cfg.endpoint)
        self._api_region.setText(api_cfg.region)
        self._api_reference_audio.setText(api_cfg.reference_audio)
        self._api_prompt_text.setText(api_cfg.prompt_text)
        self._api_prompt_lang.setText(api_cfg.prompt_lang)
        self._api_text_lang.setText(api_cfg.text_lang)

    def _apply_provider_fields(self, provider: str):
        if not self._inline_api_visible:
            self._api_fields_wrap.setFixedHeight(0)
            return
        provider_cfg = _VOICE_PROVIDER_API_FIELDS.get(provider, {})
        field_specs = provider_cfg.get("fields", {})
        hint = provider_cfg.get("hint", "")
        provider_label = VOICE_PROVIDER_LABELS.get(provider, provider)
        self._api_provider_title.setText(f"当前供应商：{provider_label}。{hint}".strip())
        for panel in self._api_field_panels.values():
            self._api_fields_layout.removeWidget(panel)
            panel.hide()
        for field_key, panel in self._api_field_panels.items():
            field_meta = field_specs.get(field_key)
            if field_meta is None:
                continue
            label_text, placeholder = field_meta
            self._api_field_labels[field_key].setText(label_text)
            self._api_field_edits[field_key].setPlaceholderText(placeholder)
            panel.show()
            self._api_fields_layout.addWidget(panel)
        visible_panels = [panel for panel in self._api_field_panels.values() if not panel.isHidden()]
        total_height = 0
        if visible_panels:
            total_height = sum(panel.height() for panel in visible_panels)
            total_height += self._api_fields_layout.spacing() * (len(visible_panels) - 1)
        self._api_fields_wrap.setFixedHeight(total_height)
        try:
            self.attachment().adjustSize()
        except Exception:
            pass

    def _hide_inline_api_widgets(self):
        widgets = (
            self._provider_card,
            self._model_card,
            self._api_provider_title,
            self._api_fields_wrap,
            self._api_btn_wrap,
            self._voice_status_label,
        )
        for widget in widgets:
            widget.setVisible(False)
            widget.setMinimumHeight(0)
            widget.setMaximumHeight(0)
            widget.setFixedHeight(0)

    def _apply_provider_models(self, provider: str, selected_model: str = ""):
        self._model_combo.clear()
        for model in VOICE_MODELS.get(provider, ()):
            self._model_combo.addItem(model, model)
        models = VOICE_MODELS.get(provider, ())
        if not models:
            return
        target = selected_model if selected_model in models else models[0]
        for idx in range(self._model_combo.count()):
            if self._model_combo.itemData(idx) == target:
                self._model_combo.setCurrentIndex(idx)
                break
        self._apply_provider_fields(provider)

    def _on_provider_changed(self, index: int):
        self._sync_provider_api_inputs(self._active_provider)
        current_model = self._model_combo.currentData()
        if isinstance(current_model, str) and current_model:
            self._voice_settings_state.model_id = current_model
        provider = self._current_provider()
        self._voice_settings_state.provider = provider
        self._apply_provider_models(provider, "")
        self._load_provider_api_inputs(provider)
        self._populate_voice_combos(force=True)
        self._active_provider = provider

    def set_dialog_mode(self, enabled: bool) -> None:
        self.setPadding(24 if enabled else 64)
        self.setScrollMaximumWidth(960 if enabled else 820)
        self._back_area.setVisible(not enabled)

    @staticmethod
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

    def _populate_voice_combos(self, force: bool = False):
        voices = [
            v for v in compatible_voice_entries(
                self._voice_settings_state.voices,
                self._voice_settings_state.provider,
            )
            if v.clone_status == "ready"
        ]
        for combo, role_key in (
            (self._anchor_voice_combo, "anchor"),
            (self._copilot_voice_combo, "copilot"),
        ):
            target_id = getattr(self._voice_settings_state, role_key).voice_id
            # If voice list hasn't changed, just update selection
            if not force and combo.count() == len(voices) + 1:
                same = True
                for i, v in enumerate(voices):
                    if combo.itemData(i + 1) != v.id or combo.itemText(i + 1) != _voice_display_name(v):
                        same = False
                        break
                if same:
                    combo.blockSignals(True)
                    for idx in range(combo.count()):
                        if combo.itemData(idx) == target_id:
                            combo.setCurrentIndex(idx)
                            break
                    combo.blockSignals(False)
                    continue
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("（未选择）", "")
            for v in voices:
                combo.addItem(_voice_display_name(v), v.id)
            for idx in range(combo.count()):
                if combo.itemData(idx) == target_id:
                    combo.setCurrentIndex(idx)
                    break
            combo.blockSignals(False)

    def _on_anchor_voice_changed(self, index: int):
        voice_id = str(self._anchor_voice_combo.currentData() or "").strip()
        self._voice_settings_state.anchor.voice_id = voice_id
        self._save_voice_settings()

    def _on_copilot_voice_changed(self, index: int):
        voice_id = str(self._copilot_voice_combo.currentData() or "").strip()
        self._voice_settings_state.copilot.voice_id = voice_id
        self._save_voice_settings()

    def _role_combo(self, role_key: str) -> QComboBox:
        return self._anchor_voice_combo if role_key == "anchor" else self._copilot_voice_combo

    def _preview_button(self, role_key: str):
        return self._anchor_preview_btn if role_key == "anchor" else self._copilot_preview_btn

    def _set_preview_button_playing(self, role_key: str, playing: bool):
        if role_key not in {"anchor", "copilot"}:
            return
        button = self._preview_button(role_key)
        button.setText("⏸ 暂停" if playing else "▶ 试听")
        button.setProperty("state", "playing" if playing else "")
        button.update()

    def _set_voice_status(self, text: str):
        self._copilot_runtime_status.setText(text)

    def _preview_text_for_role(self, role_key: str) -> str:
        settings = _load_settings()
        voice_settings = VoiceSettings.from_dict(settings.get("voice"))
        if role_key == "anchor":
            return voice_settings.anchor_script.strip()
        return "大家好，欢迎来到直播间！"

    def _on_preview_clicked(self, role_key: str):
        combo = self._role_combo(role_key)
        voice_id = str(combo.currentData() or "").strip()
        role_label = self._ROLE_LABELS.get(role_key, role_key)
        if self._preview_playing_role:
            from audio_output import stop_all_audio

            stopped_role = self._preview_playing_role
            stop_all_audio()
            self._preview_playing_role = None
            self._active_preview_token = None
            self._set_preview_button_playing(stopped_role, False)
            if stopped_role == role_key:
                self._set_voice_status(f"{role_label}试听已暂停")
                return
        if not voice_id:
            self._set_voice_status(f"{role_label}试听失败：请先选择音色")
            return
        text = self._preview_text_for_role(role_key)
        if not text:
            self._set_voice_status("主播试听失败：请先在主播设置中保存主播话术")
            return
        self._save_voice_settings()
        self._set_voice_status(f"{role_label}音频播放中")
        self._preview_token += 1
        self._active_preview_token = self._preview_token
        self._preview_playing_role = role_key
        self._set_preview_button_playing(role_key, True)
        self.voice_action_requested.emit({
            "type": "preview",
            "role": role_key,
            "text": text,
            "preview_token": self._preview_token,
        })

    def _delete_selected_voice(self, role_key: str):
        combo = self._role_combo(role_key)
        voice_id = str(combo.currentData() or "").strip()
        role_label = self._ROLE_LABELS.get(role_key, role_key)
        if not voice_id:
            self._set_voice_status(f"{role_label}删除失败：请先选择音色")
            return
        voice = self._voice_settings_state.find_voice(voice_id)
        voice_name = _voice_display_name(voice) if voice else voice_id
        answer = QMessageBox.question(
            self,
            "删除音色",
            f"确认删除音色“{voice_name}”？\n这只会删除配置中的音色记录，不会删除你原始上传的 wav 文件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._voice_settings_state.voices = [
            item for item in self._voice_settings_state.voices if item.id != voice_id
        ]
        if self._voice_settings_state.anchor.voice_id == voice_id:
            self._voice_settings_state.anchor.voice_id = ""
        if self._voice_settings_state.copilot.voice_id == voice_id:
            self._voice_settings_state.copilot.voice_id = ""
        self._populate_voice_combos()
        self._save_voice_settings()
        self._set_voice_status(f"已删除音色：{voice_name}")

    def load_voice_settings(self, value: object):
        settings = VoiceSettings.from_dict(value)
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())
        provider = settings.provider
        self._active_provider = provider
        self._provider_combo.blockSignals(True)
        for idx in range(self._provider_combo.count()):
            if self._provider_combo.itemData(idx) == provider:
                self._provider_combo.setCurrentIndex(idx)
                break
        self._provider_combo.blockSignals(False)
        self._apply_provider_models(provider, settings.model_id)
        self._load_provider_api_inputs(provider)
        self._populate_voice_combos()
        self._anchor_speed.setValue(settings.anchor.speed)
        self._anchor_volume.setValue(settings.anchor.volume_gain)
        self._copilot_speed.setValue(settings.copilot.speed)
        self._copilot_volume.setValue(settings.copilot.volume_gain)

    def get_voice_settings(self) -> VoiceSettings:
        provider = self._current_provider()
        self._sync_provider_api_inputs(provider)
        settings = VoiceSettings.from_dict(self._voice_settings_state.to_dict())
        settings.provider = provider
        settings.model_id = self._model_combo.currentData() or VOICE_MODELS[provider][0]
        settings.anchor.speed = self._anchor_speed.value()
        settings.anchor.volume_gain = self._anchor_volume.value()
        settings.anchor.voice_id = str(self._anchor_voice_combo.currentData() or "").strip()
        settings.copilot.speed = self._copilot_speed.value()
        settings.copilot.volume_gain = self._copilot_volume.value()
        settings.copilot.voice_id = str(self._copilot_voice_combo.currentData() or "").strip()
        self._voice_settings_state = VoiceSettings.from_dict(settings.to_dict())
        return settings

    def _save_voice_settings(self):
        settings = self.get_voice_settings()
        data = _load_settings()
        data["voice"] = settings.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(settings.to_dict())

    def update_voice_runtime(self, payload: object):
        if not isinstance(payload, dict):
            return
        self._voice_runtime_payload = dict(payload)
        provider = payload.get("provider")
        model_id = payload.get("model_id")
        tts_status = payload.get("tts_status")
        tts_detail = payload.get("tts_detail", "")
        if provider:
            self._voice_status_label.setText(f"供应商：{provider} / 模型：{model_id or '-'}")
        if tts_status:
            queue_depth = payload.get("tts_queue_depth", 0)
            playing = payload.get("tts_playing", False)
            if queue_depth > 0 or playing:
                state_text = "播放中" if playing else "等待"
                tts_info = f" | TTS: {state_text} 队列:{queue_depth}"
            else:
                tts_info = ""
            self._copilot_runtime_status.setText(f"最近播报：{tts_status} {tts_detail}".strip() + tts_info)

    def handle_voice_action_result(self, payload: object):
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if not isinstance(result, VoiceActionResult):
            return
        action_type = payload.get("type", "")
        if action_type == "validate_provider":
            self._voice_status_label.setText(result.message)
        elif action_type == "preview":
            role = str(payload.get("role", "")).strip()
            preview_token = payload.get("preview_token")
            if preview_token is not None and preview_token != self._active_preview_token:
                return
            if self._preview_playing_role == role:
                self._preview_playing_role = None
                self._active_preview_token = None
            self._set_preview_button_playing(role, False)
            role_label = self._ROLE_LABELS.get(role, role or "语音")
            prefix = "试听完成" if result.ok else "试听失败"
            self._set_voice_status(f"{role_label}{prefix}：{result.message}")

    # ------------------------------------------------------------------
    # Streaming console (merged from DigitalHumanPage). Logic mirrors that
    # class so behavior stays identical — the DigitalHumanPage class is
    # kept defined in this module for fallback / rollback.
    # ------------------------------------------------------------------

    def _gallery_height_for_count(self, count: int) -> int:
        rows = max(1, (max(1, count) + self._GALLERY_COLUMNS - 1) // self._GALLERY_COLUMNS)
        return rows * _CARD_H + (rows - 1) * self._GALLERY_GAP

    def _rebuild_gallery(self):
        while self._gallery_layout.count():
            item = self._gallery_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                # already removed from layout by takeAt
                pass
        self._sync_video_paths_from_library()
        records = self._avatar_library.list()
        gallery_height = self._gallery_height_for_count(MAX_AVATAR_RECORDS)
        self._gallery_inner.setFixedHeight(gallery_height)
        if hasattr(self, "_gallery_stream_row"):
            self._gallery_stream_row.setFixedHeight(gallery_height)
        self._gallery_inner.setMinimumWidth(
            self._GALLERY_COLUMNS * _CARD_W + (self._GALLERY_COLUMNS - 1) * self._GALLERY_GAP
        )
        for col in range(self._GALLERY_COLUMNS):
            self._gallery_layout.setColumnMinimumWidth(col, _CARD_W)

        next_slot = 0
        for i, record in enumerate(records):
            path = record.video_path_for_pipeline()
            pixmap = self._load_thumbnail(path)
            card = _VideoThumbCard(
                i,
                path,
                pixmap,
                self._gallery_inner,
                status_text=self._avatar_badge_text(record),
            )
            if record.error:
                card.setToolTip(record.error)
            card.clicked.connect(self._on_thumb_clicked)
            card.remove_requested.connect(self._on_thumb_remove)
            card.set_selected(i == self._selected_index)
            self._gallery_layout.addWidget(
                card,
                next_slot // self._GALLERY_COLUMNS,
                next_slot % self._GALLERY_COLUMNS,
                Qt.AlignLeft | Qt.AlignTop,
            )
            next_slot += 1
        if len(records) < MAX_AVATAR_RECORDS:
            add_card = _AddCard(self._gallery_inner)
            add_card.clicked.connect(self._on_add_video)
            self._gallery_layout.addWidget(
                add_card,
                next_slot // self._GALLERY_COLUMNS,
                next_slot % self._GALLERY_COLUMNS,
                Qt.AlignLeft | Qt.AlignTop,
            )

    def _load_thumbnail(self, video_path: str) -> QPixmap | None:
        from pathlib import Path
        thumb_dir = app_dir() / "data" / "digital_human" / "thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumb_dir / (Path(video_path).stem + ".jpg")
        if thumb_path.exists():
            return QPixmap(str(thumb_path))
        try:
            from ffmpeg_ops import _resolve_ffmpeg_path
            import subprocess
            ffmpeg = _resolve_ffmpeg_path()
            subprocess.run(
                [ffmpeg, "-y", "-i", video_path, "-ss", "0.5",
                 "-vframes", "1", "-q:v", "2", str(thumb_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if thumb_path.exists():
                return QPixmap(str(thumb_path))
        except Exception:
            pass
        return None

    def _on_thumb_clicked(self, index: int):
        records = self._avatar_library.list()
        if 0 <= index < len(records):
            record = records[index]
            self._avatar_library.set_selected(record.id)
            if record.status != STATUS_READY:
                self._avatar_preprocessor.enqueue(record.id, priority=True)
        self._selected_index = index
        for i in range(self._gallery_layout.count()):
            item = self._gallery_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), _VideoThumbCard):
                item.widget().set_selected(item.widget().index == index)
        self._save_streaming_config()

    def _on_thumb_remove(self, index: int):
        records = self._avatar_library.list()
        if index < 0 or index >= len(records):
            return
        record = records[index]
        reply = QMessageBox.question(
            self,
            "移除主播形象",
            "确认移除该主播形象？\n将同时删除该形象的视频副本和口型缓存。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._avatar_preprocessor.cancel(record.id)
        self._avatar_library.remove(record.id, livetalking_root=str(default_livetalking_root()))
        self._sync_video_paths_from_library()
        self._rebuild_gallery()
        self._save_streaming_config()

    def _on_add_video(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择绿幕视频", "",
            "视频文件 (*.mp4 *.avi *.mov);;所有文件 (*)",
        )
        if not paths:
            return
        remaining = MAX_AVATAR_RECORDS - len(self._avatar_library.list())
        if remaining <= 0:
            QMessageBox.information(
                self,
                "主播形象已满",
                f"主播形象最多支持 {MAX_AVATAR_RECORDS} 个，请先移除一个再添加。",
            )
            return
        if len(paths) > remaining:
            QMessageBox.information(
                self,
                "只导入部分视频",
                f"主播形象最多支持 {MAX_AVATAR_RECORDS} 个，本次只导入前 {remaining} 个。",
            )
            paths = paths[:remaining]
        quality = self._ask_avatar_quality()
        imported_ids = []
        errors = []
        for path in paths:
            try:
                record = self._avatar_library.import_video(path, quality)
                imported_ids.append(record.id)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        self._sync_video_paths_from_library()
        for record_id in imported_ids:
            self._avatar_preprocessor.enqueue(
                record_id,
                priority=(record_id == self._avatar_library.selected_id),
            )
        if errors:
            QMessageBox.warning(self, "导入失败", "\n".join(errors[:5]))
        self._rebuild_gallery()
        self._save_streaming_config()

    # -- Stream control --

    def _on_stream_start(self):
        config = self.get_streaming_config_dict()
        if not config.get("video_path"):
            QMessageBox.warning(
                self, "需要选择主播视频",
                "请先在主播形象里选一个视频。",
            )
            return
        record = self._avatar_library.selected()
        if record and not record.is_ready():
            QMessageBox.information(
                self,
                "主播形象处理中",
                self._stream_block_message(record),
            )
            if record.status != STATUS_FAILED:
                self._avatar_preprocessor.enqueue(record.id, priority=True)
            return
        self.digital_human_start_requested.emit(config)

    def _on_stream_stop(self):
        self.digital_human_stop_requested.emit()

    def _save_streaming_config(self):
        data = _load_settings()
        data["digital_human"] = {
            "video_paths": list(self._video_paths),
            "selected_index": self._selected_index,
            "selected_avatar_record_id": self._avatar_library.selected_id,
            **self.get_streaming_config_dict(),
        }
        _save_settings(data)
        self.streaming_config_changed.emit(self._streaming_state_payload())

    def _selected_video_path(self) -> str:
        record = self._avatar_library.selected()
        if record:
            return record.video_path_for_pipeline()
        return ""

    def get_streaming_config_dict(self) -> dict:
        record = self._avatar_library.selected()
        ready = bool(record and record.is_ready())
        return {
            "video_path": self._selected_video_path(),
            "obs_scene": "",
            "obs_host": self._obs_host,
            "obs_port": self._obs_port,
            "obs_password": self._obs_password,
            "use_livetalking": True,
            "audio_folder_path": self._livetalking_audio_folder_path,
            "livetalking_avatar_id": record.livetalking_avatar_id if ready else "",
            "avatar_record_id": record.id if record else "",
            "avatar_ready": ready,
            "avatar_status": record.status if record else "",
        }

    def set_obs_connection_settings(self, host: str, port: int, password: str):
        self._obs_host = host
        self._obs_port = port
        self._obs_password = password

    def update_streaming_status(self, payload: dict):
        if not isinstance(payload, dict):
            return
        message = payload.get("message", "")
        state = payload.get("state", "")
        text = message if message else state
        if text and hasattr(self, "_stream_status_label"):
            self._stream_status_label.setText(text)

    def load_streaming_settings(self, value: object):
        self._selected_index = -1
        if isinstance(value, dict):
            paths = value.get("video_paths", [])
            legacy_paths = [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []
            single = value.get("video_path", "")
            if isinstance(single, str) and single and single not in legacy_paths:
                legacy_paths.append(single)
            sel = value.get("selected_index", 0)
            self._avatar_library.migrate_video_paths(
                legacy_paths,
                sel,
                livetalking_root=str(default_livetalking_root()),
            )
            selected_record_id = value.get("selected_avatar_record_id", "")
            if isinstance(selected_record_id, str) and selected_record_id:
                self._avatar_library.set_selected(selected_record_id)
            audio_folder_path = value.get("audio_folder_path", "")
            if isinstance(audio_folder_path, str) and audio_folder_path:
                self._livetalking_audio_folder_path = audio_folder_path
        self._sync_video_paths_from_library()
        self._avatar_preprocessor.enqueue_pending()
        self._rebuild_gallery()
        # Emit so HomePage's checklist refreshes its "绿幕素材" item.
        self.streaming_config_changed.emit(self._streaming_state_payload())

    def _sync_video_paths_from_library(self):
        records = self._avatar_library.list()
        self._video_paths = [record.video_path_for_pipeline() for record in records]
        self._selected_index = self._avatar_library.selected_index()

    def _avatar_badge_text(self, record) -> str:
        if record.status == STATUS_READY:
            return f"可推流 · {record.quality}"
        if record.status == STATUS_FAILED:
            return "失败"
        if record.status == STATUS_PROCESSING_GPU:
            return f"GPU {record.progress}%"
        if record.status == STATUS_PROCESSING_CPU:
            return f"CPU {record.progress}%"
        if record.status in (STATUS_IMPORTED, STATUS_NORMALIZING, STATUS_CHECKING):
            return record.stage or "处理中"
        return record.stage or ""

    def _ask_avatar_quality(self) -> str:
        items = ["快速 720p", "高清 1080p"]
        choice, ok = QInputDialog.getItem(
            self,
            "主播形象质量",
            "选择该主播形象的处理质量：",
            items,
            0,
            False,
        )
        if not ok:
            return DEFAULT_QUALITY
        return "1080p" if "1080" in choice else "720p"

    def _stream_block_message(self, record) -> str:
        if record.status == STATUS_FAILED:
            detail = record.error or "该主播形象处理失败，请移除后重新导入。"
            return f"当前主播形象处理失败：{detail}"
        stage = record.stage or "处理中"
        progress = f" {record.progress}%" if record.progress else ""
        return f"当前主播形象正在{stage}{progress}，完成后可推流。"

    def _on_avatar_preprocess_changed(self, payload: object):
        if not isinstance(payload, dict):
            return
        self._avatar_library.load()
        self._sync_video_paths_from_library()
        self._rebuild_gallery()
        record = self._avatar_library.get(str(payload.get("record_id", "")))
        if record and record.id == self._avatar_library.selected_id:
            text = record.stage
            if record.progress and record.status != STATUS_READY:
                text = f"{text} {record.progress}%"
            if record.error and record.status == STATUS_FAILED:
                text = f"{text}: {record.error}"
            if hasattr(self, "_stream_status_label"):
                self._stream_status_label.setText(text or "空闲")
        self._save_streaming_config()

    def _streaming_state_payload(self) -> dict:
        record = self._avatar_library.selected()
        return {
            "selected_video": self._selected_video_path(),
            "video_count": len(self._video_paths),
            "max_video_count": MAX_AVATAR_RECORDS,
            "avatar_record_id": record.id if record else "",
            "avatar_ready": bool(record and record.is_ready()),
            "avatar_status": record.status if record else "",
            "avatar_stage": record.stage if record else "",
            "avatar_progress": record.progress if record else 0,
            "avatar_error": record.error if record else "",
            "avatar_quality": record.quality if record else "",
            "avatar_display_name": record.display_name if record else "",
        }

    def shutdown(self) -> None:
        self._avatar_preprocessor.stop()
