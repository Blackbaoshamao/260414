"""AIConfigPage — live copilot/control settings."""
from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger
from fluent_page import FluentPage
from qfluentwidgets import (
    SettingCard, SettingCardGroup, PushButton, PrimaryPushButton,
    ComboBox, LineEdit as FLineEdit, FluentIcon, IconWidget,
)

import ui_theme as theme
from ui_theme import patch_setting_card_padding
from live_control_config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TEMPLATE_NAME,
    LiveControlSettings,
    LiveControlTemplate,
    TONE_STYLE_LABELS,
    normalize_scheduled_scripts,
)
from ui_settings import _load_settings, _save_settings
from ui_theme import (
    SiSwitch,
    _build_input_field_stylesheet,
    _build_text_area_stylesheet,
    _install_secret_reveal_action,
)


class _AutoGrowTextEdit(QTextEdit):
    """QTextEdit that auto-grows/shrinks to fit its content, no scrollbar."""

    def __init__(self, parent=None, *, min_h: int = 60):
        super().__init__(parent)
        self._min_h = min_h
        self._last_h = 0
        self._resize_pending = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.setMinimumHeight(min_h)
        self.document().contentsChanged.connect(self._schedule_recalc)

    def _schedule_recalc(self):
        if not self._resize_pending:
            self._resize_pending = True
            QTimer.singleShot(0, self._do_resize)

    def _do_resize(self):
        self._resize_pending = False
        w = self.viewport().width()
        if w < 10:
            return
        doc = self.document()
        old_tw = doc.textWidth()
        doc.setTextWidth(w)
        h = int(doc.size().height()) + 16
        doc.setTextWidth(old_tw)
        h = max(self._min_h, h)
        if h != self._last_h:
            self._last_h = h
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._schedule_recalc()


_MODEL_OPTIONS = (DEFAULT_MODEL, "deepseek-reasoner")

_TEMPLATE_FIELDS: tuple[tuple[str, str, str, str, int], ...] = (
    ("product_info", "本场直播商品信息", "ic_fluent_cart_filled", "商品名称、规格、价格口径、卖点和活动说明", 120),
    ("anchor_persona", "主播/助播人设", "ic_fluent_person_star_filled", "主播定位、称呼习惯、口播角色和直播间氛围", 80),
    ("after_sales_policy", "售后政策", "ic_fluent_receipt_filled", "退换货、发货、物流、客服等明确政策", 90),
    ("forbidden_commitments", "禁止承诺内容", "ic_fluent_shield_filled", "不能承诺的价格、功效、时效、赠品或绝对化表述", 100),
    ("reply_boundaries", "回复边界", "ic_fluent_comment_multiple_filled", "哪些问题能答、哪些要引导主播/客服确认", 110),
    ("platform_rules", "平台规则", "ic_fluent_gavel_filled", "平台敏感词、合规要求、诱导交易限制等", 100),
    ("faq", "常见问题说明", "ic_fluent_question_circle_filled", "高频问答、尺码选择、使用方法和注意事项", 130),
)


def _template_icon(name: str, color: str) -> QIcon:
    try:
        import qtawesome as qta

        return qta.icon(name, color=color)
    except Exception:
        pass

    pix = QPixmap(20, 20)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    if "trash" in name:
        painter.drawLine(5, 6, 15, 6)
        painter.drawLine(8, 4, 12, 4)
        painter.drawLine(7, 8, 8, 16)
        painter.drawLine(13, 8, 12, 16)
        painter.drawLine(8, 16, 12, 16)
        painter.drawLine(10, 9, 10, 15)
    elif "save" in name:
        painter.drawRect(5, 4, 10, 12)
        painter.drawLine(7, 4, 7, 8)
        painter.drawLine(7, 13, 13, 13)
        painter.drawLine(13, 4, 13, 8)
    elif "pen" in name or "edit" in name:
        painter.drawLine(5, 14, 13, 6)
        painter.drawLine(11, 4, 15, 8)
        painter.drawLine(4, 15, 8, 14)
    else:
        painter.drawLine(10, 4, 10, 16)
        painter.drawLine(4, 10, 16, 10)
    painter.end()
    return QIcon(pix)


class AIConfigPage(FluentPage):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setPadding(64)
        self.setScrollMaximumWidth(800)
        self._suspend_auto_save = False
        self._loading_template = False
        self._persona_labels: list[QLabel] = []
        self._template_field_labels: list[QLabel] = []
        self._template_edits: dict[str, QTextEdit] = {}
        self._live_control_settings = LiveControlSettings.from_settings({})

        self._init_legacy_persona_compat()

        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 0, 8, 0)
        container_layout.setSpacing(8)

        _title = QLabel("AI 助播", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container_layout.addWidget(_title)

        self._settings_group = SettingCardGroup("", self)
        self._settings_group.layout().setContentsMargins(0, 0, 8, 0)
        self._build_model_section(self._settings_group)
        self._build_template_section(self._settings_group)
        self._build_control_section(self._settings_group)
        self._build_action_section(self._settings_group)
        container_layout.addWidget(self._settings_group)
        self.setAttachment(container)
        self._connect_signals()
        self._apply_theme_styles()
        self.load_live_control_settings(_load_settings())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_legacy_persona_compat(self):
        """Keep old attributes alive for ui.py until it is migrated."""
        self._legacy_persona_container = QWidget(self)
        self._legacy_persona_container.hide()

        self._name_input = FLineEdit(self._legacy_persona_container)
        self._role_edit = QTextEdit(self._legacy_persona_container)
        self._strategy_edit = QTextEdit(self._legacy_persona_container)
        self._scene_edit = QTextEdit(self._legacy_persona_container)
        self._tone_edit = QTextEdit(self._legacy_persona_container)
        self._limit_edit = QTextEdit(self._legacy_persona_container)
        self._taboo_edit = QTextEdit(self._legacy_persona_container)
        self._scheduled_scripts_switch = SiSwitch(self._legacy_persona_container)
        self._scheduled_interval_spin = QSpinBox(self._legacy_persona_container)
        self._scheduled_interval_spin.setRange(10, 36000)
        self._scheduled_interval_spin.setSuffix(" 秒")
        self._scheduled_order_combo = QComboBox(self._legacy_persona_container)
        self._scheduled_order_combo.addItem("顺序轮播", False)
        self._scheduled_order_combo.addItem("随机抽取", True)
        self._scheduled_random_space_switch = SiSwitch(self._legacy_persona_container)
        self._scheduled_voice_switch = SiSwitch(self._legacy_persona_container)

    def _build_model_section(self, container: SettingCardGroup):
        _title = QLabel("模型配置", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container.vBoxLayout.addWidget(_title)

        api_card = SettingCard(FluentIcon.PEOPLE, "API Key", "DeepSeek API 密钥", parent=self)
        self._api_key_input = FLineEdit(self)
        self._api_key_input.setFixedSize(300, 32)
        self._api_key_input.setFont(theme.FONT_BODY)
        _install_secret_reveal_action(self._api_key_input)
        api_card.hBoxLayout.addWidget(self._api_key_input, 0, Qt.AlignRight)
        patch_setting_card_padding(api_card)
        container.addSettingCard(api_card)

        url_card = SettingCard(FluentIcon.LINK, "Base URL", "API 接口地址", parent=self)
        self._base_url_input = FLineEdit(self)
        self._base_url_input.setFixedSize(300, 32)
        self._base_url_input.setText(DEFAULT_BASE_URL)
        url_card.hBoxLayout.addWidget(self._base_url_input, 0, Qt.AlignRight)
        patch_setting_card_padding(url_card)
        container.addSettingCard(url_card)

        model_card = SettingCard(FluentIcon.ROBOT, "模型", "选择 DeepSeek 模型", parent=self)
        self._model_combo = ComboBox(self)
        self._model_combo.setFixedSize(200, 32)
        for model in _MODEL_OPTIONS:
            self._model_combo.addItem(model, userData=model)
        self._select_combo_value(self._model_combo, DEFAULT_MODEL)
        model_card.hBoxLayout.addWidget(self._model_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(model_card)
        container.addSettingCard(model_card)

    def _build_template_section(self, container: SettingCardGroup):
        _title = QLabel("AI 助播设定", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container.vBoxLayout.addWidget(_title)

        template_card = SettingCard(FluentIcon.DOCUMENT, "模板套装", "按不同直播商品或场次切换助播设定", parent=self)

        template_row = QWidget(self)
        template_row.setFixedSize(386, 34)
        row_layout = QHBoxLayout(template_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(theme.SPACING_SM)

        self._template_combo = QComboBox(self)
        self._template_combo.setEditable(False)
        self._template_combo.setFixedSize(210, 32)
        row_layout.addWidget(self._template_combo)

        self._new_template_btn = QPushButton(self)
        self._new_template_btn.setFixedSize(34, 32)
        self._new_template_btn.setCursor(Qt.PointingHandCursor)
        self._new_template_btn.setToolTip("新增模板")
        self._new_template_btn.setIcon(_template_icon("fa5s.plus", theme.CLR_ACCENT))
        self._new_template_btn.setIconSize(QSize(14, 14))
        row_layout.addWidget(self._new_template_btn)

        self._rename_template_btn = QPushButton(self)
        self._rename_template_btn.setFixedSize(34, 32)
        self._rename_template_btn.setCursor(Qt.PointingHandCursor)
        self._rename_template_btn.setToolTip("重命名当前模板")
        self._rename_template_btn.setIcon(_template_icon("fa5s.pen", theme.CLR_ACCENT))
        self._rename_template_btn.setIconSize(QSize(14, 14))
        row_layout.addWidget(self._rename_template_btn)

        self._delete_template_btn = QPushButton(self)
        self._delete_template_btn.setFixedSize(34, 32)
        self._delete_template_btn.setCursor(Qt.PointingHandCursor)
        self._delete_template_btn.setToolTip("删除当前模板")
        self._delete_template_btn.setIcon(_template_icon("fa5s.trash-alt", theme.CLR_RED))
        self._delete_template_btn.setIconSize(QSize(14, 14))
        row_layout.addWidget(self._delete_template_btn)

        self._save_template_btn = QPushButton(self)
        self._save_template_btn.setFixedSize(34, 32)
        self._save_template_btn.setCursor(Qt.PointingHandCursor)
        self._save_template_btn.setToolTip("保存当前模板")
        self._save_template_btn.setIcon(_template_icon("fa5s.save", theme.CLR_ACCENT))
        self._save_template_btn.setIconSize(QSize(14, 14))
        row_layout.addWidget(self._save_template_btn)

        template_card.hBoxLayout.addWidget(template_row, 0, Qt.AlignRight)
        patch_setting_card_padding(template_card)
        container.addSettingCard(template_card)

        for field_name, label, icon_name, placeholder, height in _TEMPLATE_FIELDS:
            self._add_template_text_block(container, field_name, label, icon_name, placeholder, height)

    def _build_control_section(self, container: SettingCardGroup):
        _title = QLabel("回复控制", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container.vBoxLayout.addWidget(_title)

        reply_card = SettingCard(FluentIcon.MESSAGE, "自动回复", "开启后 AI 会按场控设定生成回复", parent=self)
        self._auto_reply_switch = SiSwitch(self)
        reply_card.hBoxLayout.addWidget(self._auto_reply_switch, 0, Qt.AlignRight)
        patch_setting_card_padding(reply_card)
        container.addSettingCard(reply_card)

        char_limit_card = SettingCard(FluentIcon.FONT_SIZE, "回复字数上限", "限制单条 AI 回复长度", parent=self)
        self._reply_char_limit_spin = QSpinBox(self)
        self._reply_char_limit_spin.setRange(20, 500)
        self._reply_char_limit_spin.setSuffix(" 字")
        self._reply_char_limit_spin.setFixedSize(100, 32)
        char_limit_card.hBoxLayout.addWidget(self._reply_char_limit_spin, 0, Qt.AlignRight)
        patch_setting_card_padding(char_limit_card)
        container.addSettingCard(char_limit_card)

        user_cooldown_card = SettingCard(FluentIcon.CALENDAR, "单用户冷却", "同一观众两次 AI 回复的最小间隔", parent=self)
        self._user_cooldown_spin = QSpinBox(self)
        self._user_cooldown_spin.setRange(0, 36000)
        self._user_cooldown_spin.setSuffix(" 秒")
        self._user_cooldown_spin.setFixedSize(100, 32)
        user_cooldown_card.hBoxLayout.addWidget(self._user_cooldown_spin, 0, Qt.AlignRight)
        patch_setting_card_padding(user_cooldown_card)
        container.addSettingCard(user_cooldown_card)

        interval_card = SettingCard(FluentIcon.CALENDAR, "全局回复冷却", "两次自动回复的全局最小间隔", parent=self)
        self._interval_spin = QSpinBox(self)
        self._interval_spin.setRange(0, 36000)
        self._interval_spin.setSuffix(" 秒")
        self._interval_spin.setFixedSize(100, 32)
        self._global_cooldown_spin = self._interval_spin
        interval_card.hBoxLayout.addWidget(self._interval_spin, 0, Qt.AlignRight)
        patch_setting_card_padding(interval_card)
        container.addSettingCard(interval_card)

        tone_card = SettingCard(FluentIcon.MUSIC, "语气风格", "控制助播回复的表达倾向", parent=self)
        self._tone_style_combo = QComboBox(self)
        self._tone_style_combo.setFixedSize(150, 32)
        for value, label in TONE_STYLE_LABELS.items():
            self._tone_style_combo.addItem(label, value)
        tone_card.hBoxLayout.addWidget(self._tone_style_combo, 0, Qt.AlignRight)
        patch_setting_card_padding(tone_card)
        container.addSettingCard(tone_card)

        mention_card = SettingCard(FluentIcon.PEOPLE, "@ 提问观众", "发送回复时附带观众昵称", parent=self)
        self._mention_user_switch = SiSwitch(self)
        mention_card.hBoxLayout.addWidget(self._mention_user_switch, 0, Qt.AlignRight)
        patch_setting_card_padding(mention_card)
        container.addSettingCard(mention_card)

        voice_card = SettingCard(FluentIcon.VOLUME, "语音播报", "生成回复后交给数字人语音播报", parent=self)
        self._voice_reply_switch = SiSwitch(self)
        voice_card.hBoxLayout.addWidget(self._voice_reply_switch, 0, Qt.AlignRight)
        patch_setting_card_padding(voice_card)
        container.addSettingCard(voice_card)

    def _build_action_section(self, container: SettingCardGroup):
        _title = QLabel("操作", self)
        _title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        container.vBoxLayout.addWidget(_title)
        btn_area = QWidget(self)
        btn_area.setFixedHeight(36)
        btn_layout = QHBoxLayout(btn_area)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(theme.SPACING_SM)

        self._save_btn = PrimaryPushButton("保存设置", self)
        self._save_btn.setFixedSize(110, 32)
        btn_layout.addWidget(self._save_btn)

        self._reset_btn = PushButton("恢复默认", self)
        self._reset_btn.setFixedSize(100, 32)
        btn_layout.addWidget(self._reset_btn)

        btn_layout.addStretch()
        container.vBoxLayout.addWidget(btn_area)

    def _add_template_text_block(
        self,
        container: SettingCardGroup,
        field_name: str,
        label: str,
        icon_name: str,
        placeholder: str,
        height: int,
    ):
        row = QWidget(self)
        row.setFixedHeight(24)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        lbl = QLabel(label)
        row_layout.addWidget(lbl)
        row_layout.addStretch()
        self._template_field_labels.append(lbl)
        container.vBoxLayout.addWidget(row)

        edit = _AutoGrowTextEdit(self, min_h=height)
        edit.setPlaceholderText(placeholder)
        self._template_edits[field_name] = edit
        setattr(self, f"_{field_name}_edit", edit)
        container.vBoxLayout.addWidget(edit)

    def _connect_signals(self):
        self._save_btn.clicked.connect(self._auto_save)
        self._reset_btn.clicked.connect(self._reset_defaults)
        self._new_template_btn.clicked.connect(self._add_template)
        self._rename_template_btn.clicked.connect(self._rename_template)
        self._delete_template_btn.clicked.connect(self._delete_template)
        self._save_template_btn.clicked.connect(self._auto_save)
        self._template_combo.currentTextChanged.connect(self._on_template_changed)

        self._auto_reply_switch.toggled.connect(lambda _checked: self._auto_save())
        self._auto_reply_switch.toggled.connect(self._on_auto_reply_changed)
        self._interval_spin.valueChanged.connect(lambda _value: self._auto_save())
        self._reply_char_limit_spin.valueChanged.connect(lambda _value: self._auto_save())
        self._user_cooldown_spin.valueChanged.connect(lambda _value: self._auto_save())
        self._tone_style_combo.currentIndexChanged.connect(lambda _index: self._auto_save())
        self._mention_user_switch.toggled.connect(lambda _checked: self._auto_save())
        self._voice_reply_switch.toggled.connect(lambda _checked: self._auto_save())
        self._scheduled_scripts_switch.toggled.connect(lambda _checked: self._auto_save())
        self._scheduled_interval_spin.valueChanged.connect(lambda _value: self._auto_save())
        self._scheduled_order_combo.currentIndexChanged.connect(lambda _index: self._auto_save())
        self._scheduled_random_space_switch.toggled.connect(lambda _checked: self._auto_save())
        self._scheduled_voice_switch.toggled.connect(lambda _checked: self._auto_save())
        self._api_key_input.editingFinished.connect(self._auto_save)
        self._base_url_input.editingFinished.connect(self._auto_save)
        self._model_combo.currentIndexChanged.connect(lambda _index: self._auto_save())

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme_styles(self):
        line_edit_ss = _build_input_field_stylesheet("QLineEdit", radius=theme.RADIUS_MD)
        spin_ss = _build_input_field_stylesheet("QSpinBox", radius=theme.RADIUS_MD)
        combo_ss = _build_input_field_stylesheet(
            "QComboBox",
            radius=theme.RADIUS_MD,
            include_combo=True,
        )

        self._api_key_input.setStyleSheet(line_edit_ss)
        _install_secret_reveal_action(self._api_key_input)
        self._base_url_input.setStyleSheet(line_edit_ss)
        self._name_input.setStyleSheet(line_edit_ss)

        for spin in (
            self._interval_spin,
            self._reply_char_limit_spin,
            self._user_cooldown_spin,
            self._scheduled_interval_spin,
        ):
            spin.setStyleSheet(spin_ss)

        self._template_combo.setStyleSheet(combo_ss)
        self._tone_style_combo.setStyleSheet(combo_ss)
        self._scheduled_order_combo.setStyleSheet(combo_ss)
        self._new_template_btn.setIcon(_template_icon("fa5s.plus", theme.CLR_ACCENT))
        self._rename_template_btn.setIcon(_template_icon("fa5s.pen", theme.CLR_ACCENT))
        self._delete_template_btn.setIcon(_template_icon("fa5s.trash-alt", theme.CLR_RED))
        self._save_template_btn.setIcon(_template_icon("fa5s.save", theme.CLR_ACCENT))
        self._new_template_btn.setStyleSheet(self._template_icon_button_stylesheet())
        self._rename_template_btn.setStyleSheet(self._template_icon_button_stylesheet())
        self._delete_template_btn.setStyleSheet(self._template_icon_button_stylesheet(destructive=True))
        self._save_template_btn.setStyleSheet(self._template_icon_button_stylesheet())

        edit_ss = _build_text_area_stylesheet(radius=theme.RADIUS_LG, padding="8px 10px")
        for edit in tuple(self._template_edits.values()) + (
            self._role_edit,
            self._strategy_edit,
            self._scene_edit,
            self._tone_edit,
            self._limit_edit,
            self._taboo_edit,
        ):
            edit.setStyleSheet(edit_ss)

        for lbl in self._persona_labels + self._template_field_labels:
            lbl.setStyleSheet("")

        pass

    def _template_icon_button_stylesheet(self, destructive: bool = False) -> str:
        hover = theme._mix_hex_colors(
            theme.CLR_BG_ELEVATED,
            theme.CLR_RED if destructive else theme.CLR_ACCENT,
            0.10,
        )
        border_hover = theme.CLR_RED if destructive else theme.CLR_ACCENT
        return (
            "QPushButton {"
            f"background-color: {theme.CLR_BG_ELEVATED};"
            f"border: 1px solid {theme.CLR_HAIRLINE};"
            f"border-radius: {theme.RADIUS_MD}px;"
            "padding: 0;"
            "}"
            "QPushButton:hover {"
            f"background-color: {hover};"
            f"border-color: {border_hover};"
            "}"
            "QPushButton:pressed {"
            f"background-color: {theme.CLR_SELECTED_BG};"
            "}"
        )

    # ------------------------------------------------------------------
    # Template management
    # ------------------------------------------------------------------

    def _on_template_changed(self, name: str):
        if self._loading_template or not name:
            return
        self._sync_template_from_widgets()
        if name not in self._live_control_settings.templates:
            return
        self._live_control_settings.active_template = name
        self._load_template_to_widgets(self._live_control_settings.templates[name])
        self._auto_save()

    def _rename_template(self):
        if self._loading_template:
            return
        old_name = self._live_control_settings.active_template
        if not old_name:
            return
        new_name, ok = QInputDialog.getText(
            self,
            "重命名场控模板",
            "模板名称：",
            text=old_name,
        )
        if not ok:
            return
        new_name = str(new_name or "").strip()
        if not self._validate_template_name(new_name, ignore_name=old_name, title="重命名场控模板"):
            return
        if new_name == old_name:
            return

        self._sync_template_from_widgets()
        template = self._live_control_settings.templates.pop(
            old_name,
            LiveControlTemplate(name=new_name),
        )
        template.name = new_name
        self._live_control_settings.templates[new_name] = template
        self._live_control_settings.active_template = new_name
        self._refresh_template_combo(new_name)
        self._auto_save()

    def _add_template(self):
        self._sync_template_from_widgets()
        name, ok = QInputDialog.getText(
            self,
            "新建场控模板",
            "模板名称：",
            text=self._next_template_name(),
        )
        if not ok:
            return
        name = str(name or "").strip()
        if not self._validate_template_name(name, title="新建场控模板"):
            return

        self._live_control_settings.templates[name] = LiveControlTemplate(name=name)
        self._live_control_settings.active_template = name
        self._refresh_template_combo(name)
        self._load_template_to_widgets(self._live_control_settings.templates[name])
        self._auto_save()

    def _delete_template(self):
        name = self._template_combo.currentText().strip()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "删除场控模板",
            f"确认删除「{name}」？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self._live_control_settings.templates.pop(name, None)
        if not self._live_control_settings.templates:
            self._live_control_settings.templates[DEFAULT_TEMPLATE_NAME] = LiveControlTemplate()

        next_name = next(iter(self._live_control_settings.templates.keys()))
        self._live_control_settings.active_template = next_name
        self._refresh_template_combo(next_name)
        self._load_template_to_widgets(self._live_control_settings.templates[next_name])
        self._auto_save()

    def _next_template_name(self) -> str:
        index = len(self._live_control_settings.templates) + 1
        while True:
            name = f"场控模板 {index}"
            if name not in self._live_control_settings.templates:
                return name
            index += 1

    def _validate_template_name(
        self,
        name: str,
        ignore_name: str = "",
        title: str = "场控模板",
    ) -> bool:
        if not name:
            QMessageBox.warning(self, title, "模板名称不能为空。")
            return False
        for existing in self._live_control_settings.templates:
            if existing == ignore_name:
                continue
            if existing == name:
                QMessageBox.warning(self, title, "已存在同名模板，请换一个名称。")
                return False
        return True

    def _sync_template_from_widgets(self):
        if self._loading_template:
            return
        active = self._live_control_settings.active_template
        if not active:
            active = self._template_combo.currentText().strip() or DEFAULT_TEMPLATE_NAME
            self._live_control_settings.active_template = active
        if active not in self._live_control_settings.templates:
            self._live_control_settings.templates[active] = LiveControlTemplate(name=active)

        template = self._live_control_settings.templates[active]
        template.name = active
        for field_name, edit in self._template_edits.items():
            if field_name == "scheduled_scripts":
                template.scheduled_scripts = normalize_scheduled_scripts(edit.toPlainText())
            else:
                setattr(template, field_name, edit.toPlainText())

    def _load_template_to_widgets(self, template: LiveControlTemplate):
        self._loading_template = True
        try:
            for field_name, edit in self._template_edits.items():
                if field_name == "scheduled_scripts":
                    edit.setPlainText("\n\n".join(template.scheduled_scripts or []))
                else:
                    edit.setPlainText(str(getattr(template, field_name, "") or ""))
        finally:
            self._loading_template = False

    def _refresh_template_combo(self, active_name: str | None = None):
        active_name = active_name or self._live_control_settings.active_template
        names = list(self._live_control_settings.templates.keys())
        if not names:
            names = [DEFAULT_TEMPLATE_NAME]
            self._live_control_settings.templates[DEFAULT_TEMPLATE_NAME] = LiveControlTemplate()

        if active_name not in names:
            active_name = names[0]

        self._template_combo.blockSignals(True)
        try:
            self._template_combo.clear()
            self._template_combo.addItems(names)
            self._template_combo.setCurrentText(active_name)
        finally:
            self._template_combo.blockSignals(False)
        self._live_control_settings.active_template = active_name

    # ------------------------------------------------------------------
    # Persistence and public API
    # ------------------------------------------------------------------

    def load_live_control_settings(self, settings: dict):
        live_settings = (
            settings
            if isinstance(settings, LiveControlSettings)
            else LiveControlSettings.from_settings(settings or {})
        )
        self._live_control_settings = live_settings

        self._suspend_auto_save = True
        try:
            self._api_key_input.setText(live_settings.api_key)
            self._base_url_input.setText(live_settings.base_url or DEFAULT_BASE_URL)
            self._select_combo_value(self._model_combo, live_settings.model or DEFAULT_MODEL)
            self._auto_reply_switch.setChecked(live_settings.auto_reply)
            self._reply_char_limit_spin.setValue(live_settings.reply_char_limit)
            self._user_cooldown_spin.setValue(live_settings.user_cooldown_sec)
            self._interval_spin.setValue(live_settings.global_cooldown_sec)
            self._set_tone_style(live_settings.tone_style)
            self._mention_user_switch.setChecked(live_settings.mention_user)
            self._voice_reply_switch.setChecked(live_settings.voice_reply_enabled)
            self._scheduled_scripts_switch.setChecked(live_settings.scheduled_scripts_enabled)
            self._scheduled_interval_spin.setValue(live_settings.scheduled_scripts_interval_sec)
            self._set_scheduled_random_order(live_settings.scheduled_scripts_random_order)
            self._scheduled_random_space_switch.setChecked(
                live_settings.scheduled_scripts_random_space_enabled
            )
            self._scheduled_voice_switch.setChecked(live_settings.scheduled_scripts_voice_enabled)
            self._refresh_template_combo(live_settings.active_template)
            self._load_template_to_widgets(live_settings.get_active_template())
        finally:
            self._suspend_auto_save = False

    def get_live_control_settings(self) -> LiveControlSettings:
        self._sync_template_from_widgets()
        active = self._live_control_settings.active_template
        if active not in self._live_control_settings.templates:
            active = next(iter(self._live_control_settings.templates.keys()), DEFAULT_TEMPLATE_NAME)

        state = LiveControlSettings(
            templates=dict(self._live_control_settings.templates),
            active_template=active,
            auto_reply=self._auto_reply_switch.isChecked(),
            api_key=self._api_key_input.text(),
            base_url=self._base_url_input.text().strip() or DEFAULT_BASE_URL,
            model=self._current_model(),
            reply_char_limit=self._reply_char_limit_spin.value(),
            user_cooldown_sec=self._user_cooldown_spin.value(),
            global_cooldown_sec=self._interval_spin.value(),
            tone_style=self._current_tone_style(),
            mention_user=self._mention_user_switch.isChecked(),
            voice_reply_enabled=self._voice_reply_switch.isChecked(),
            scheduled_scripts_enabled=self._scheduled_scripts_switch.isChecked(),
            scheduled_scripts_interval_sec=self._scheduled_interval_spin.value(),
            scheduled_scripts_random_order=self._current_scheduled_random_order(),
            scheduled_scripts_random_space_enabled=self._scheduled_random_space_switch.isChecked(),
            scheduled_scripts_voice_enabled=self._scheduled_voice_switch.isChecked(),
        )
        self._live_control_settings = state
        return state

    def _auto_save(self):
        if self._suspend_auto_save:
            return
        live_settings = self.get_live_control_settings()
        settings = _load_settings()
        settings.update({
            "auto_reply": live_settings.auto_reply,
            "reply_interval": live_settings.global_cooldown_sec,
            "api_key": live_settings.api_key,
            "base_url": live_settings.base_url,
            "model": live_settings.model,
            "live_control": live_settings.to_settings_payload(),
        })
        _save_settings(settings)
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref._update_ai_engine()
        logger.info("AI live control settings saved")

    def _on_auto_reply_changed(self, checked: bool):
        if self._suspend_auto_save:
            return
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref._update_ai_engine()

    def _reset_defaults(self):
        self.load_live_control_settings({})
        self._auto_save()
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref._update_ai_engine()
        logger.info("AI live control config reset to defaults")

    def get_ai_config_dict(self):
        from ai_reply import build_live_control_system_prompt

        live_settings = self.get_live_control_settings()
        template = live_settings.get_active_template()
        template_payload = template.to_dict()
        live_payload = live_settings.to_settings_payload()
        system_prompt = build_live_control_system_prompt(
            template,
            reply_char_limit=live_settings.reply_char_limit,
            tone_style=live_settings.tone_style,
        )

        return {
            "api_key": live_settings.api_key,
            "base_url": live_settings.base_url,
            "model": live_settings.model,
            "system_prompt": system_prompt,
            "auto_reply": live_settings.auto_reply,
            "reply_interval": live_settings.global_cooldown_sec,
            "live_control": live_payload,
            "live_control_template": template_payload,
            "live_control_active_template": live_settings.active_template,
            "reply_char_limit": live_settings.reply_char_limit,
            "user_cooldown_sec": live_settings.user_cooldown_sec,
            "global_cooldown_sec": live_settings.global_cooldown_sec,
            "tone_style": live_settings.tone_style,
            "mention_user": live_settings.mention_user,
            "voice_reply_enabled": live_settings.voice_reply_enabled,
            "scheduled_scripts_enabled": live_settings.scheduled_scripts_enabled,
            "scheduled_scripts_interval_sec": live_settings.scheduled_scripts_interval_sec,
            "scheduled_scripts_random_order": live_settings.scheduled_scripts_random_order,
            "scheduled_scripts_random_space_enabled": (
                live_settings.scheduled_scripts_random_space_enabled
            ),
            "scheduled_scripts_voice_enabled": live_settings.scheduled_scripts_voice_enabled,
        }

    # ------------------------------------------------------------------
    # Small widget helpers
    # ------------------------------------------------------------------

    def _select_combo_value(self, combo: ComboBox, target: str):
        target = str(target or DEFAULT_MODEL)
        idx = combo.findText(target)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)

    def _current_model(self) -> str:
        try:
            value = self._model_combo.currentData()
            if value:
                return str(value)
        except Exception:
            pass
        return DEFAULT_MODEL

    def _set_tone_style(self, tone_style: str):
        tone_style = str(tone_style or "natural")
        for index in range(self._tone_style_combo.count()):
            if self._tone_style_combo.itemData(index) == tone_style:
                self._tone_style_combo.setCurrentIndex(index)
                return
        self._tone_style_combo.setCurrentIndex(0)

    def _current_tone_style(self) -> str:
        value = self._tone_style_combo.currentData()
        return str(value or "natural")

    def _set_scheduled_random_order(self, enabled: bool):
        target = bool(enabled)
        for index in range(self._scheduled_order_combo.count()):
            if bool(self._scheduled_order_combo.itemData(index)) == target:
                self._scheduled_order_combo.setCurrentIndex(index)
                return
        self._scheduled_order_combo.setCurrentIndex(0)

    def _current_scheduled_random_order(self) -> bool:
        return bool(self._scheduled_order_combo.currentData())
