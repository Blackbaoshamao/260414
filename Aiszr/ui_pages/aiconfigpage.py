"""AIConfigPage — extracted from ui.py."""
from __future__ import annotations
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import SiSwitch
from ui_theme import _build_input_field_stylesheet
from ui_theme import _build_text_area_stylesheet
from ui_theme import _install_secret_reveal_action
from ui_theme import apply_theme


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QTimer, QRect, QSize, QPropertyAnimation, QEasingCurve, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush, QIcon
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog,
    QInputDialog, QFrame, QAbstractButton, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QComboBox, QScrollArea, QApplication, QApplication, QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog, QInputDialog, QFrame, QAbstractButton, QSpinBox, QDoubleSpinBox, QSizePolicy, QComboBox, QScrollArea)
from siui.core import SiColor, SiGlobal, GlobalFont, Si
from siui.gui import SiFont
from siui.components.page import SiPage
from siui.components.widgets import (SiDenseHContainer, SiDenseVContainer,
    SiLabel, SiLineEdit, SiPushButton, SiSvgLabel)
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.option_card import SiOptionCardLinear
from siui.components.combobox.combobox import SiComboBox
from siui.components.slider import SiSliderH
from siui.templates.application.components.dialog.modal import SiModalDialog
import ui_theme as theme
from ui_settings import _save_settings
from loguru import logger


class AIConfigPage(SiPage):
    back_requested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(800)
        self._persona_labels = []
        self._suspend_auto_save = False

        container = SiTitledWidgetGroup(self)
        container.setSpacing(theme.SPACING_MD)

        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(theme.SPACING_SM)
        from ui_components import MacButton
        self._back_btn = MacButton("返回", variant="secondary", parent=self)
        self._back_btn.setFixedSize(80, 30)
        self._back_btn.clicked.connect(self.back_requested.emit)
        top_layout.addWidget(self._back_btn)
        top_layout.addStretch(1)
        container.addWidget(top_row)

        # Model config
        container.addTitle("模型配置")

        api_card = SiOptionCardLinear(self)
        api_card.setTitle("API Key", "DeepSeek API 密钥")
        api_card.load("ic_fluent_key_filled")
        self._api_key_input = SiLineEdit(self)
        self._api_key_input.setFixedSize(300, 32)
        _install_secret_reveal_action(self._api_key_input.lineEdit())
        api_card.addWidget(self._api_key_input)
        container.addWidget(api_card)

        url_card = SiOptionCardLinear(self)
        url_card.setTitle("Base URL", "API 接口地址")
        url_card.load("ic_fluent_link_filled")
        self._base_url_input = SiLineEdit(self)
        self._base_url_input.setFixedSize(300, 32)
        self._base_url_input.lineEdit().setText("https://api.deepseek.com/v1")
        url_card.addWidget(self._base_url_input)
        container.addWidget(url_card)

        model_card = SiOptionCardLinear(self)
        model_card.setTitle("模型", "选择 DeepSeek 模型")
        model_card.load("ic_fluent_brain_circuit_filled")
        self._model_combo = SiComboBox(self)
        self._model_combo.setFixedSize(200, 32)
        self._model_combo.addOption("deepseek-chat")
        self._model_combo.addOption("deepseek-reasoner")
        model_card.addWidget(self._model_combo)
        container.addWidget(model_card)

        # Behavior
        container.addTitle("回复设置")

        reply_card = SiOptionCardLinear(self)
        reply_card.setTitle("自动回复", "开启后 AI 会自动生成回复")
        reply_card.load("ic_fluent_chat_filled")
        self._auto_reply_switch = SiSwitch(self)
        reply_card.addWidget(self._auto_reply_switch)
        container.addWidget(reply_card)

        interval_card = SiOptionCardLinear(self)
        interval_card.setTitle("回复间隔", "两次自动回复的最小间隔")
        interval_card.load("ic_fluent_clock_filled")
        self._interval_spin = QSpinBox(self)
        self._interval_spin.setRange(5, 120)
        self._interval_spin.setSuffix(" 秒")
        self._interval_spin.setFixedHeight(32)
        self._interval_spin.setFixedWidth(90)
        interval_card.addWidget(self._interval_spin)
        container.addWidget(interval_card)

        # Persona
        container.addTitle("人设设定")

        name_card = SiOptionCardLinear(self)
        name_card.setTitle("角色名称", "AI 对外显示的人设名字")
        name_card.load("ic_fluent_person_filled")
        self._name_input = SiLineEdit(self)
        self._name_input.setFixedSize(200, 32)
        self._name_input.lineEdit().setText("慕斯")
        name_card.addWidget(self._name_input)
        container.addWidget(name_card)

        def _add_text_block(label: str, icon_name: str, height: int, attr: str):
            row = SiDenseHContainer(self)
            row.setFixedHeight(24)
            ico = SiSvgLabel(self)
            ico.load(SiGlobal.siui.iconpack.get(icon_name))
            ico.setSvgSize(16, 16)
            ico.setFixedSize(16, 16)
            row.addWidget(ico)
            lbl = QLabel(label)  # inherits color from global QLabel rule → theme-aware
            row.addWidget(lbl)
            self._persona_labels.append(lbl)
            container.addWidget(row)
            edit = QTextEdit(self)
            edit.setFixedHeight(height)
            container.addWidget(edit)
            setattr(self, attr, edit)

        _add_text_block("角色定位", "ic_fluent_person_star_filled", 60, "_role_edit")
        _add_text_block("回复策略", "ic_fluent_comment_multiple_filled", 110, "_strategy_edit")
        _add_text_block("场景话术", "ic_fluent_megaphone_filled", 100, "_scene_edit")
        _add_text_block("语气风格", "ic_fluent_comment_filled", 130, "_tone_edit")
        _add_text_block("回复限制", "ic_fluent_text_number_format_filled", 120, "_limit_edit")
        _add_text_block("禁用内容", "ic_fluent_shield_filled", 160, "_taboo_edit")

        # Placeholder future features
        container.addTitle("更多能力（预留）")
        placeholder = SiOptionCardLinear(self)
        placeholder.setTitle("观众画像", "后续将支持 AI 观众偏好分析")
        placeholder.load("ic_fluent_person_star_filled")
        container.addWidget(placeholder)

        placeholder2 = SiOptionCardLinear(self)
        placeholder2.setTitle("商品话术", "AI 带货场景扩展")
        placeholder2.load("ic_fluent_cart_filled")
        container.addWidget(placeholder2)

        # Save / Reset buttons
        container.addTitle("操作")
        btn_area = SiDenseHContainer(self)
        btn_area.setFixedHeight(36)
        self._save_btn = SiPushButton(self)
        self._save_btn.resize(100, 28)
        self._save_btn.attachment().setText("保存设置")
        self._save_btn.clicked.connect(lambda: self._auto_save())
        btn_area.addWidget(self._save_btn)
        self._reset_btn = SiPushButton(self)
        self._reset_btn.resize(100, 28)
        self._reset_btn.attachment().setText("恢复默认")
        self._reset_btn.clicked.connect(self._reset_defaults)
        btn_area.addWidget(self._reset_btn)
        container.addWidget(btn_area)

        self.setAttachment(container)

        # Auto-save on changes
        self._auto_reply_switch.toggled.connect(lambda: self._auto_save())
        self._auto_reply_switch.toggled.connect(self._on_auto_reply_changed)
        self._interval_spin.valueChanged.connect(lambda: self._auto_save())
        self._apply_theme_styles()

    def _apply_theme_styles(self):
        line_edit_ss = _build_input_field_stylesheet("QLineEdit", radius=theme.RADIUS_MD)
        spin_ss = _build_input_field_stylesheet("QSpinBox", radius=theme.RADIUS_MD)
        self._api_key_input.lineEdit().setStyleSheet(line_edit_ss)
        _install_secret_reveal_action(self._api_key_input.lineEdit())
        self._base_url_input.lineEdit().setStyleSheet(line_edit_ss)
        self._name_input.lineEdit().setStyleSheet(line_edit_ss)
        self._interval_spin.setStyleSheet(spin_ss)

        edit_ss = _build_text_area_stylesheet(radius=theme.RADIUS_LG, padding="8px 10px")
        for edit in (
            self._role_edit, self._strategy_edit, self._scene_edit,
            self._tone_edit, self._limit_edit, self._taboo_edit,
        ):
            edit.setStyleSheet(edit_ss)
        # _persona_labels inherit from global QLabel rule — no inline override
        for lbl in self._persona_labels:
            lbl.setStyleSheet("")
        self._back_btn.apply_theme_styles()

    def _auto_save(self):
        if self._suspend_auto_save:
            return
        settings = _load_settings()
        settings.update({
            "auto_reply": self._auto_reply_switch.isChecked(),
            "reply_interval": self._interval_spin.value(),
            "api_key": self._api_key_input.lineEdit().text(),
            "base_url": self._base_url_input.lineEdit().text().strip() or "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "persona_name": self._name_input.lineEdit().text(),
            "persona_role": self._role_edit.toPlainText(),
            "persona_strategy": self._strategy_edit.toPlainText(),
            "persona_scene": self._scene_edit.toPlainText(),
            "persona_tone": self._tone_edit.toPlainText(),
            "persona_limit": self._limit_edit.toPlainText(),
            "persona_taboo": self._taboo_edit.toPlainText(),
        })
        _save_settings(settings)
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref._update_ai_engine()
        logger.info("Settings auto-saved")

    def _on_auto_reply_changed(self, checked: bool):
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref._update_ai_engine()

    def _reset_defaults(self):
        from ai_reply import (
            DEFAULT_PERSONA_NAME, DEFAULT_PERSONA_ROLE, DEFAULT_PERSONA_STRATEGY,
            DEFAULT_PERSONA_SCENE, DEFAULT_PERSONA_TONE, DEFAULT_PERSONA_LIMIT,
            DEFAULT_PERSONA_TABOO,
        )
        self._api_key_input.lineEdit().setText("")
        self._base_url_input.lineEdit().setText("https://api.deepseek.com/v1")
        self._auto_reply_switch.setChecked(False)
        self._interval_spin.setValue(30)
        self._name_input.lineEdit().setText(DEFAULT_PERSONA_NAME)
        self._role_edit.setPlainText(DEFAULT_PERSONA_ROLE)
        self._strategy_edit.setPlainText(DEFAULT_PERSONA_STRATEGY)
        self._scene_edit.setPlainText(DEFAULT_PERSONA_SCENE)
        self._tone_edit.setPlainText(DEFAULT_PERSONA_TONE)
        self._limit_edit.setPlainText(DEFAULT_PERSONA_LIMIT)
        self._taboo_edit.setPlainText(DEFAULT_PERSONA_TABOO)
        self._auto_save()
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref._update_ai_engine()
        logger.info("AI config reset to defaults")

    def get_ai_config_dict(self):
        from ai_reply import build_system_prompt
        system_prompt = build_system_prompt(
            name=self._name_input.lineEdit().text(),
            role=self._role_edit.toPlainText(),
            tone=self._tone_edit.toPlainText(),
            strategy=self._strategy_edit.toPlainText(),
            scene=self._scene_edit.toPlainText(),
            limit=self._limit_edit.toPlainText(),
            taboo=self._taboo_edit.toPlainText(),
        )
        return {
            "api_key": self._api_key_input.lineEdit().text(),
            "base_url": self._base_url_input.lineEdit().text(),
            "model": "deepseek-chat",
            "system_prompt": system_prompt,
            "auto_reply": self._auto_reply_switch.isChecked(),
            "reply_interval": self._interval_spin.value(),
        }



