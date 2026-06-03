"""VoiceApiDialog — extracted from ui.py."""
from __future__ import annotations
from ui_constants import DEFAULT_VOICE_SETTINGS
from ui_constants import _SECRET_INPUT_FIELD_KEYS
from ui_constants import _VOICE_PROVIDER_API_FIELDS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import _build_input_field_stylesheet
from ui_theme import _install_secret_reveal_action
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme
from voice_manager import VoiceActionResult
from voice_models import VOICE_MODELS
from voice_models import VOICE_PROVIDERS
from voice_models import VOICE_PROVIDER_LABELS
from voice_models import VoiceSettings


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt
from PyQt5.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QBrush, QIcon
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QFormLayout,
    QDialogButtonBox, QFrame, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QListWidgetItem, QMessageBox, QSizePolicy, QLabel, QPushButton, QLineEdit, QCheckBox, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFrame, QSpinBox, QDoubleSpinBox, QSizePolicy, QComboBox, QListWidgetItem)
from siui.core import SiGlobal
from siui.components.widgets import SiPushButton, SiDenseHContainer, SiLineEdit, SiLabel
from siui.components.combobox.combobox import SiComboBox
from siui.components.option_card import SiOptionCardLinear
import ui_theme as theme
from ui_settings import _save_settings
from ui_components import MacButton, MacLineEdit, MacComboBox


class VoiceApiDialog(QDialog):
    voice_settings_changed = pyqtSignal(object)
    voice_action_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI语音 API 设置")
        self.resize(700, 520)
        self.setModal(False)

        self._voice_settings_state = VoiceSettings.from_dict(DEFAULT_VOICE_SETTINGS.to_dict())
        self._active_provider = self._voice_settings_state.provider
        self._api_field_panels: dict[str, QWidget] = {}
        self._api_field_labels: dict[str, QLabel] = {}
        self._api_field_edits: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self._title_label = QLabel("语音模型与 API", self)
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(theme.SPACING_SM)
        self._provider_combo = MacComboBox(self)
        self._provider_combo.setFixedSize(210, 34)
        for provider in VOICE_PROVIDERS:
            self._provider_combo.addItem(VOICE_PROVIDER_LABELS[provider], provider)
        self._model_combo = MacComboBox(self)
        self._model_combo.setFixedSize(210, 34)
        row_layout.addWidget(self._provider_combo)
        row_layout.addWidget(self._model_combo)
        row_layout.addStretch(1)
        layout.addWidget(row)

        self._api_provider_title = QLabel("当前供应商凭据", self)
        self._api_provider_title.setWordWrap(True)
        self._api_provider_title.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none;")
        layout.addWidget(self._api_provider_title)

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
        layout.addWidget(self._api_fields_wrap)

        # --- 带货文案 DeepSeek API ---
        self._cw_title_label = QLabel("带货文案 DeepSeek API", self)
        self._cw_title_label.setFont(theme.FONT_HEADLINE)
        layout.addWidget(self._cw_title_label)

        cw_api_panel, cw_api_label, self._copywriting_api_key_edit = self._create_api_field_panel("API Key", "DeepSeek API Key")
        _install_secret_reveal_action(self._copywriting_api_key_edit)
        layout.addWidget(cw_api_panel)

        cw_url_panel, cw_url_label, self._copywriting_base_url_edit = self._create_api_field_panel("Base URL", "https://api.deepseek.com/v1")
        self._copywriting_base_url_edit.setText("https://api.deepseek.com/v1")
        layout.addWidget(cw_url_panel)

        cw_model_row = QWidget(self)
        cw_model_layout = QHBoxLayout(cw_model_row)
        cw_model_layout.setContentsMargins(0, 0, 0, 0)
        cw_model_layout.setSpacing(theme.SPACING_SM)
        cw_model_label = QLabel("模型", self)
        cw_model_label.setFixedWidth(60)
        self._copywriting_model_combo = MacComboBox(self)
        self._copywriting_model_combo.setFixedHeight(32)
        self._copywriting_model_combo.addItems(["deepseek-chat", "deepseek-reasoner"])
        cw_model_layout.addWidget(cw_model_label)
        cw_model_layout.addWidget(self._copywriting_model_combo, 1)
        layout.addWidget(cw_model_row)

        action_row = QWidget(self)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 6, 0, 0)
        action_layout.setSpacing(theme.SPACING_SM)
        self._save_btn = MacButton("保存 API", variant="primary", parent=self)
        self._save_btn.setMinimumSize(108, 34)
        self._validate_btn = MacButton("测试连接", variant="secondary", parent=self)
        self._validate_btn.setMinimumSize(108, 34)
        action_layout.addWidget(self._save_btn)
        action_layout.addWidget(self._validate_btn)
        action_layout.addStretch(1)
        layout.addWidget(action_row)

        self._status_label = QLabel("未检测", self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._save_btn.clicked.connect(self._save_voice_settings)
        self._validate_btn.clicked.connect(self._on_validate_clicked)

        self._apply_provider_models(DEFAULT_VOICE_SETTINGS.provider, DEFAULT_VOICE_SETTINGS.model_id)
        self._load_provider_api_inputs(DEFAULT_VOICE_SETTINGS.provider)
        self._apply_provider_fields(DEFAULT_VOICE_SETTINGS.provider)
        self._apply_theme_styles()

    def _create_api_field_panel(self, label_text: str, placeholder: str) -> tuple[QWidget, QLabel, QLineEdit]:
        panel = QWidget(self)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        panel.setFixedHeight(58)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(4)
        label = QLabel(label_text, panel)
        edit = MacLineEdit(panel, placeholder=placeholder)
        edit.setFixedHeight(36)
        panel_layout.addWidget(label)
        panel_layout.addWidget(edit)
        return panel, label, edit

    def _apply_theme_styles(self):
        self.setStyleSheet(f"QDialog {{ background-color: {theme.CLR_BG}; }}")
        # Title
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        self._cw_title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;")
        # Mac* widgets self-manage
        for w in (
            self._provider_combo, self._model_combo,
            self._copywriting_model_combo,
            self._copywriting_api_key_edit, self._copywriting_base_url_edit,
            self._save_btn, self._validate_btn,
        ):
            w.apply_theme_styles()
        for field_key, edit in self._api_field_edits.items():
            edit.apply_theme_styles()
            if field_key in _SECRET_INPUT_FIELD_KEYS:
                _install_secret_reveal_action(edit)
        for label in self._api_field_labels.values():
            label.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; border: none; "
                f"background: transparent; font-weight: 600;"
            )
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
        provider_cfg = _VOICE_PROVIDER_API_FIELDS.get(provider, {})
        field_specs = provider_cfg.get("fields", {})
        hint = provider_cfg.get("hint", "")
        provider_label = VOICE_PROVIDER_LABELS.get(provider, provider)
        self._api_provider_title.setText(f"当前供应商：{provider_label}。{hint}".strip())
        has_fields = bool(field_specs)
        self._api_provider_title.setVisible(has_fields)
        self._api_fields_wrap.setVisible(has_fields)
        for panel in self._api_field_panels.values():
            self._api_fields_layout.removeWidget(panel)
            panel.hide()
        if not has_fields:
            self._api_fields_wrap.setFixedHeight(0)
            return
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
        self._active_provider = provider

    def _build_api_only_settings(self) -> VoiceSettings:
        provider = self._current_provider()
        self._sync_provider_api_inputs(provider)
        merged = VoiceSettings.from_dict(_load_settings().get("voice"))
        merged.provider = provider
        merged.model_id = self._model_combo.currentData() or VOICE_MODELS[provider][0]
        merged.api = self._voice_settings_state.api
        merged.copywriting_api_key = self._copywriting_api_key_edit.text().strip()
        merged.copywriting_base_url = self._copywriting_base_url_edit.text().strip() or "https://api.deepseek.com/v1"
        merged.copywriting_model = self._copywriting_model_combo.currentText()
        self._voice_settings_state = VoiceSettings.from_dict(merged.to_dict())
        return merged

    def _save_voice_settings(self):
        settings = self._build_api_only_settings()
        data = _load_settings()
        data["voice"] = settings.to_dict()
        _save_settings(data)
        self.voice_settings_changed.emit(settings.to_dict())
        self._status_label.setText("API 配置已保存")

    def _on_validate_clicked(self):
        self._save_voice_settings()
        self.voice_action_requested.emit({"type": "validate_provider"})

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
        self._copywriting_api_key_edit.setText(settings.copywriting_api_key)
        self._copywriting_base_url_edit.setText(settings.copywriting_base_url)
        idx = self._copywriting_model_combo.findText(settings.copywriting_model)
        if idx >= 0:
            self._copywriting_model_combo.setCurrentIndex(idx)

    def update_voice_runtime(self, payload: object):
        if not isinstance(payload, dict):
            return
        provider = payload.get("provider")
        model_id = payload.get("model_id")
        if provider:
            self._status_label.setText(f"供应商：{provider} / 模型：{model_id or '-'}")

    def handle_voice_action_result(self, payload: object):
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if not isinstance(result, VoiceActionResult):
            return
        action_type = payload.get("type", "")
        if action_type == "validate_provider":
            self._status_label.setText(result.message)


# ---------------------------------------------------------------------------
# VoiceCloneDialog
# ---------------------------------------------------------------------------
