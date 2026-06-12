"""场控话术管理页 — 独立维护定时发送话术。"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QIntValidator, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from fluent_page import FluentPage

import ui_theme as theme
from live_control_config import (
    DEFAULT_TEMPLATE_NAME,
    LiveControlSettings,
    LiveControlTemplate,
    normalize_scheduled_scripts,
)
from ui_components import MacButton, MacCard, MacComboBox, MacLineEdit
from ui_settings import _load_settings, _save_settings
from ui_theme import SiSwitch, _build_text_area_stylesheet


def _scaled_font(base: QFont, delta: int) -> QFont:
    font = QFont(base)
    size = font.pointSize()
    if size > 0:
        font.setPointSize(max(7, size + delta))
    return font


def _trash_icon(color: str) -> QIcon:
    try:
        import qtawesome as qta

        return qta.icon("fa5s.trash-alt", color=color)
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
    painter.drawLine(5, 6, 15, 6)
    painter.drawLine(8, 4, 12, 4)
    painter.drawLine(7, 8, 8, 16)
    painter.drawLine(13, 8, 12, 16)
    painter.drawLine(8, 16, 12, 16)
    painter.drawLine(10, 9, 10, 15)
    painter.end()
    return QIcon(pix)


class ScheduledScriptsPage(FluentPage):
    settings_changed = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._settings = LiveControlSettings.from_settings({})
        self._script_widgets: list[dict] = []
        self._empty_card: QFrame | None = None
        self._dirty = False
        self._loading = False
        self.setPadding(18)
        self.setScrollMaximumWidth(1520)

        root = QWidget(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(theme.SPACING_MD)

        outer.addWidget(self._build_hero())

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(theme.SPACING_MD)
        grid.setColumnStretch(0, 7)
        grid.setColumnStretch(1, 2)
        grid.addWidget(self._build_scripts_card(), 0, 0)
        grid.setRowStretch(0, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.addWidget(self._build_template_card())
        right_col.addWidget(self._build_settings_card())
        right_col.addWidget(self._build_help_card())
        right_col.addStretch(1)
        grid.addLayout(right_col, 0, 1)

        outer.addLayout(grid, stretch=1)
        self.setAttachment(root)
        self.load_live_control_settings(_load_settings())
        self._set_dirty(False)

    def _build_hero(self) -> QWidget:
        row = QFrame(self)
        row.setObjectName("ScheduledHeroBar")
        row.setFixedHeight(64)
        self._hero_bar = row
        ly = QHBoxLayout(row)
        ly.setContentsMargins(14, 0, 14, 0)
        ly.setSpacing(12)

        title_wrap = QWidget(row)
        title_wrap.setStyleSheet("background: transparent;")
        title_layout = QHBoxLayout(title_wrap)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        self._hero_title_accent = QFrame(title_wrap)
        self._hero_title_accent.setObjectName("ScheduledHeroTitleAccent")
        self._hero_title_accent.setFixedSize(4, 26)
        title_layout.addWidget(self._hero_title_accent)
        self._hero_title = QLabel("场控话术")
        title_font = QFont(theme.FONT_TITLE_1)
        title_font.setPointSize(20)
        title_font.setWeight(QFont.DemiBold)
        title_font.setLetterSpacing(QFont.PercentageSpacing, 100)
        self._hero_title.setFont(title_font)
        title_layout.addWidget(self._hero_title)
        self._hero_badge = QLabel("定时发送")
        self._hero_badge.setObjectName("ScheduledHeroBadge")
        self._hero_badge.setFixedHeight(24)
        self._hero_badge.setAlignment(Qt.AlignCenter)
        title_layout.addWidget(self._hero_badge)
        title_layout.addStretch(1)
        ly.addWidget(title_wrap, stretch=1)
        ly.addStretch(1)

        self._hero_switch_wrap = QFrame(row)
        self._hero_switch_wrap.setObjectName("ScheduledHeroSwitch")
        self._hero_switch_wrap.setFixedHeight(36)
        switch_layout = QHBoxLayout(self._hero_switch_wrap)
        switch_layout.setContentsMargins(12, 0, 10, 0)
        switch_layout.setSpacing(8)
        self._hero_switch_label = QLabel("自动发送")
        self._hero_switch_label.setObjectName("ScheduledHeroSwitchLabel")
        self._hero_switch_label.setFont(theme.FONT_BODY_EMPH)
        switch_layout.addWidget(self._hero_switch_label)
        self._enabled_switch = SiSwitch(self._hero_switch_wrap)
        self._enabled_switch.toggled.connect(self._on_enabled_toggled)
        switch_layout.addWidget(self._enabled_switch)
        ly.addWidget(self._hero_switch_wrap)
        self._apply_hero_styles()
        return row

    def _build_template_card(self) -> MacCard:
        card = MacCard(self, title="模板管理", subtitle="和 AI 助播设定共用模板")
        body = card.body()
        body.setSpacing(10)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(theme.SPACING_SM)
        lbl = QLabel("当前")
        lbl.setFont(theme.FONT_BODY)
        combo_row.addWidget(lbl)
        self._template_combo = MacComboBox()
        self._template_combo.currentTextChanged.connect(self._on_template_switch)
        combo_row.addWidget(self._template_combo, stretch=1)
        body.addLayout(combo_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(theme.SPACING_SM)
        add_btn = MacButton("+ 新增", variant="secondary")
        add_btn.clicked.connect(self._add_template)
        btn_row.addWidget(add_btn, stretch=1)
        del_btn = MacButton("删除", variant="destructive")
        del_btn.clicked.connect(self._delete_template)
        btn_row.addWidget(del_btn, stretch=1)
        body.addLayout(btn_row)
        return card

    def _build_settings_card(self) -> MacCard:
        card = MacCard(self, title="发送设置", subtitle="控制自动话术发送节奏")
        body = card.body()
        body.setSpacing(10)

        interval_row = QHBoxLayout()
        interval_row.setSpacing(theme.SPACING_SM)
        interval_label = QLabel("发送间隔（秒）")
        interval_label.setFont(theme.FONT_BODY)
        interval_row.addWidget(interval_label)
        interval_row.addStretch(1)
        self._interval_edit = MacLineEdit(placeholder="120")
        self._interval_edit.setFixedSize(84, 28)
        self._interval_edit.setValidator(QIntValidator(10, 36000, self))
        self._interval_edit.editingFinished.connect(lambda: self._set_dirty(True))
        interval_row.addWidget(self._interval_edit)
        body.addLayout(interval_row)

        order_row = QHBoxLayout()
        order_row.setSpacing(theme.SPACING_SM)
        order_label = QLabel("发送顺序")
        order_label.setFont(theme.FONT_BODY)
        order_row.addWidget(order_label)
        self._order_combo = MacComboBox()
        self._order_combo.setFixedHeight(32)
        self._order_combo.addItem("顺序轮播", False)
        self._order_combo.addItem("随机抽取", True)
        self._order_combo.currentIndexChanged.connect(lambda _idx: self._set_dirty(True))
        order_row.addWidget(self._order_combo, stretch=1)
        body.addLayout(order_row)

        self._random_space_switch = self._add_switch_row(
            body,
            "随机空格",
            "发送前插入一个随机空格",
        )
        self._voice_switch = self._add_switch_row(
            body,
            "语音播报",
            "插入数字人语音，不发送文字",
        )
        return card

    def _add_switch_row(self, body: QVBoxLayout, label: str, desc: str) -> SiSwitch:
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING_SM)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = QLabel(label)
        title.setFont(theme.FONT_BODY)
        detail = QLabel(desc)
        detail.setFont(theme.FONT_CAPTION)
        detail.setWordWrap(True)
        text_col.addWidget(title)
        text_col.addWidget(detail)
        row.addLayout(text_col, stretch=1)
        switch = SiSwitch(self)
        switch.toggled.connect(lambda _checked: self._set_dirty(True))
        row.addWidget(switch)
        body.addLayout(row)
        return switch

    def _build_help_card(self) -> MacCard:
        card = MacCard(self, title="发送规则")
        body = card.body()
        body.setSpacing(6)
        for title, desc in (
            ("独立话术", "每一行卡片是一条完整话术，可单独新增或删除"),
            ("模板共用", "这里的模板和 AI 助播设定模板保持一致"),
            ("保存生效", "修改话术或发送设置后点击保存，会立即同步到自动发送任务"),
        ):
            wrap = QFrame(card)
            wrap.setObjectName("ScheduledHelpItem")
            wrap.setStyleSheet(
                f"QFrame#ScheduledHelpItem {{ background: {theme.CLR_BG_ELEVATED}; "
                f"border: 1px solid {theme.CLR_HAIRLINE}; border-radius: {theme.RADIUS_SM}px; }}"
            )
            ly = QVBoxLayout(wrap)
            ly.setContentsMargins(8, 5, 8, 5)
            ly.setSpacing(1)
            t = QLabel(title)
            t.setFont(_scaled_font(theme.FONT_BODY_EMPH, -2))
            d = QLabel(desc)
            d.setFont(_scaled_font(theme.FONT_CAPTION, -2))
            d.setWordWrap(True)
            ly.addWidget(t)
            ly.addWidget(d)
            body.addWidget(wrap)
        return card

    def _build_scripts_card(self) -> MacCard:
        card = MacCard(self, title="场控话术列表", subtitle="按间隔自动发送当前模板的话术")
        body = card.body()
        body.setSpacing(12)

        ops = QHBoxLayout()
        ops.setSpacing(theme.SPACING_SM)
        add_btn = MacButton("+ 添加话术", variant="primary")
        add_btn.setFixedSize(118, 32)
        add_btn.clicked.connect(self._add_script)
        ops.addWidget(add_btn)

        self._summary_label = QLabel("0 条话术")
        self._summary_label.setFont(theme.FONT_CAPTION)
        self._summary_label.setFixedHeight(26)
        self._summary_label.setStyleSheet(self._summary_stylesheet())
        ops.addWidget(self._summary_label)
        ops.addStretch(1)

        self._save_btn = MacButton("保存", variant="primary")
        self._save_btn.setFixedSize(92, 32)
        self._save_btn.clicked.connect(self._persist)
        ops.addWidget(self._save_btn)
        body.addLayout(ops)

        scripts_wrap = QWidget(card)
        self._scripts_layout = QVBoxLayout(scripts_wrap)
        self._scripts_layout.setContentsMargins(0, 0, 0, 0)
        self._scripts_layout.setSpacing(12)
        self._scripts_layout.setAlignment(Qt.AlignTop)
        scripts_wrap.setStyleSheet("background: transparent;")

        self._scripts_scroll = QScrollArea(card)
        self._scripts_scroll.setWidget(scripts_wrap)
        self._scripts_scroll.setWidgetResizable(True)
        self._scripts_scroll.setFrameShape(QFrame.NoFrame)
        self._scripts_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scripts_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scripts_scroll.setStyleSheet(self._scroll_stylesheet())
        body.addWidget(self._scripts_scroll, stretch=1)
        return card

    def _make_script_widget(self, text: str = "") -> tuple[QFrame, dict]:
        card = QFrame(self)
        card.setObjectName("ScheduledScriptRow")
        card.setFixedHeight(104)
        self._apply_script_row_style(card)
        body = QVBoxLayout(card)
        body.setSpacing(8)
        body.setContentsMargins(14, 10, 14, 10)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        title = QLabel("场控话术")
        title.setFont(theme.FONT_BODY_EMPH)
        top.addWidget(title)
        top.addStretch(1)
        del_btn = QPushButton(card)
        del_btn.setFixedSize(34, 28)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setToolTip("删除话术")
        del_btn.setIcon(_trash_icon(theme.CLR_RED))
        del_btn.setStyleSheet(self._trash_button_stylesheet())
        top.addWidget(del_btn)
        body.addLayout(top)

        edit = QTextEdit()
        edit.setPlaceholderText("输入一条要定时发送的场控话术")
        edit.setPlainText(text)
        edit.setFixedHeight(48)
        edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        edit.setStyleSheet(_build_text_area_stylesheet(
            radius=theme.RADIUS_MD,
            padding="8px 10px",
        ))
        edit.textChanged.connect(lambda: self._set_dirty(True))
        body.addWidget(edit)

        widgets = {"card": card, "edit": edit}
        del_btn.clicked.connect(lambda: self._delete_script(widgets))
        return card, widgets

    def _add_script(self):
        self._remove_empty_card()
        card, widgets = self._make_script_widget()
        self._scripts_layout.addWidget(card)
        self._script_widgets.append(widgets)
        self._refresh_summary()
        self._set_dirty(True)

    def _delete_script(self, widgets: dict):
        if widgets not in self._script_widgets:
            return
        self._script_widgets.remove(widgets)
        card = widgets["card"]
        card.setParent(None)
        card.deleteLater()
        if not self._script_widgets:
            self._show_empty_card()
        self._refresh_summary()
        self._set_dirty(True)

    def _clear_scripts(self):
        for widgets in self._script_widgets:
            widgets["card"].setParent(None)
            widgets["card"].deleteLater()
        self._script_widgets.clear()
        self._remove_empty_card()

    def _flush_widgets_to_settings(self):
        active = self._settings.active_template
        if active not in self._settings.templates:
            self._settings.templates[active] = LiveControlTemplate(name=active)
        template = self._settings.templates[active]
        template.name = active
        template.scheduled_scripts = normalize_scheduled_scripts(
            [widgets["edit"].toPlainText() for widgets in self._script_widgets]
        )

    def _on_template_switch(self, name: str):
        if self._loading or not name:
            return
        self._flush_widgets_to_settings()
        if name not in self._settings.templates:
            return
        self._settings.active_template = name
        self._rebuild_scripts()
        self._set_dirty(True)

    def _add_template(self):
        self._flush_widgets_to_settings()
        name = self._next_template_name()
        self._settings.templates[name] = LiveControlTemplate(name=name)
        self._settings.active_template = name
        self._refresh_template_combo(name)
        self._rebuild_scripts()
        self._set_dirty(True)

    def _delete_template(self):
        name = self._template_combo.currentText().strip()
        if not name:
            return
        self._settings.templates.pop(name, None)
        if not self._settings.templates:
            self._settings.templates[DEFAULT_TEMPLATE_NAME] = LiveControlTemplate()
        next_name = next(iter(self._settings.templates.keys()))
        self._settings.active_template = next_name
        self._refresh_template_combo(next_name)
        self._rebuild_scripts()
        self._set_dirty(True)

    def _next_template_name(self) -> str:
        index = len(self._settings.templates) + 1
        while True:
            name = f"场控模板 {index}"
            if name not in self._settings.templates:
                return name
            index += 1

    def _refresh_template_combo(self, active_name: str | None = None):
        names = list(self._settings.templates.keys())
        if not names:
            names = [DEFAULT_TEMPLATE_NAME]
            self._settings.templates[DEFAULT_TEMPLATE_NAME] = LiveControlTemplate()
        active_name = active_name or self._settings.active_template
        if active_name not in names:
            active_name = names[0]
        self._template_combo.blockSignals(True)
        try:
            self._template_combo.clear()
            self._template_combo.addItems(names)
            self._template_combo.setCurrentText(active_name)
        finally:
            self._template_combo.blockSignals(False)
        self._settings.active_template = active_name

    def _rebuild_scripts(self):
        self._clear_scripts()
        template = self._settings.get_active_template()
        for script in template.scheduled_scripts:
            card, widgets = self._make_script_widget(script)
            self._scripts_layout.addWidget(card)
            self._script_widgets.append(widgets)
        if not self._script_widgets:
            self._show_empty_card()
        self._refresh_summary()
        self._set_dirty(False)

    def load_live_control_settings(self, settings: dict | LiveControlSettings | None):
        live_settings = (
            settings
            if isinstance(settings, LiveControlSettings)
            else LiveControlSettings.from_settings(settings or {})
        )
        self._settings = live_settings
        self._loading = True
        try:
            self._enabled_switch.setChecked(live_settings.scheduled_scripts_enabled)
            self._interval_edit.setText(str(live_settings.scheduled_scripts_interval_sec))
            self._set_order(live_settings.scheduled_scripts_random_order)
            self._random_space_switch.setChecked(
                live_settings.scheduled_scripts_random_space_enabled
            )
            self._voice_switch.setChecked(live_settings.scheduled_scripts_voice_enabled)
            self._refresh_template_combo(live_settings.active_template)
            self._rebuild_scripts()
        finally:
            self._loading = False
        self._set_dirty(False)

    def get_live_control_settings(self) -> LiveControlSettings:
        self._flush_widgets_to_settings()
        active = self._settings.active_template
        if active not in self._settings.templates:
            active = next(iter(self._settings.templates.keys()), DEFAULT_TEMPLATE_NAME)
        try:
            interval = int(self._interval_edit.text() or "120")
        except ValueError:
            interval = 120
        state = LiveControlSettings(
            templates=dict(self._settings.templates),
            active_template=active,
            auto_reply=self._settings.auto_reply,
            api_key=self._settings.api_key,
            base_url=self._settings.base_url,
            model=self._settings.model,
            reply_char_limit=self._settings.reply_char_limit,
            user_cooldown_sec=self._settings.user_cooldown_sec,
            global_cooldown_sec=self._settings.global_cooldown_sec,
            tone_style=self._settings.tone_style,
            mention_user=self._settings.mention_user,
            voice_reply_enabled=self._settings.voice_reply_enabled,
            scheduled_scripts_enabled=self._enabled_switch.isChecked(),
            scheduled_scripts_interval_sec=max(10, min(36000, interval)),
            scheduled_scripts_random_order=self._current_order(),
            scheduled_scripts_random_space_enabled=self._random_space_switch.isChecked(),
            scheduled_scripts_voice_enabled=self._voice_switch.isChecked(),
        )
        self._settings = state
        return state

    def _persist(self):
        live_settings = self.get_live_control_settings()
        data = _load_settings()
        data["live_control"] = live_settings.to_settings_payload()
        _save_settings(data)
        self._set_dirty(False)
        self.settings_changed.emit()

    def _on_enabled_toggled(self, _checked: bool):
        if self._loading:
            return
        self._persist()

    def _set_order(self, enabled: bool):
        target = bool(enabled)
        self._order_combo.blockSignals(True)
        try:
            for idx in range(self._order_combo.count()):
                if bool(self._order_combo.itemData(idx)) == target:
                    self._order_combo.setCurrentIndex(idx)
                    return
            self._order_combo.setCurrentIndex(0)
        finally:
            self._order_combo.blockSignals(False)

    def _current_order(self) -> bool:
        return bool(self._order_combo.currentData())

    def _refresh_summary(self):
        count = len(self._script_widgets)
        enabled = "已启用" if self._enabled_switch.isChecked() else "未启用"
        self._summary_label.setText(f"{count} 条话术 · {enabled}")

    def _set_dirty(self, dirty: bool):
        if self._loading:
            return
        self._dirty = bool(dirty)
        if hasattr(self, "_save_btn"):
            self._save_btn.setText("保存*" if self._dirty else "保存")
            self._save_btn.setEnabled(True)
        if hasattr(self, "_summary_label"):
            self._refresh_summary()

    def _make_empty_state_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("ScheduledEmptyState")
        card.setFixedHeight(92)
        card.setStyleSheet(
            f"QFrame#ScheduledEmptyState {{ background-color: {theme.CLR_BG_ELEVATED}; "
            f"border: 1px dashed {theme.CLR_BORDER}; border-radius: {theme.RADIUS_MD}px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        label = QLabel("还没有场控话术。点击上方「+ 添加话术」开始配置。")
        label.setFont(theme.FONT_CAPTION)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; background: transparent; border: none;")
        layout.addWidget(label)
        return card

    def _show_empty_card(self):
        if self._empty_card is None:
            self._empty_card = self._make_empty_state_card()
            self._scripts_layout.addWidget(self._empty_card)

    def _remove_empty_card(self):
        if self._empty_card is not None:
            self._empty_card.setParent(None)
            self._empty_card.deleteLater()
            self._empty_card = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        sa = getattr(self, "scroll_area", None)
        if sa is not None:
            att = sa.attachment()
            if att is not None and att.height() != sa.height():
                att.resize(att.width(), sa.height())

    def _summary_stylesheet(self) -> str:
        bg = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_BG_CARD, 0.52)
        border = theme._mix_hex_colors(theme.CLR_HAIRLINE, theme.CLR_ACCENT, 0.12)
        return (
            "QLabel {"
            f"color: {theme.CLR_TEXT_SEC};"
            f"background-color: {bg};"
            f"border: 1px solid {border};"
            "border-radius: 13px;"
            "padding: 3px 11px;"
            "}"
        )

    def _scroll_stylesheet(self) -> str:
        handle = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_TEXT_PRI, 0.10)
        handle_hover = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_ACCENT, 0.22)
        return (
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 0 2px 0; }"
            f"QScrollBar::handle:vertical {{ background: {handle}; border-radius: 3px; min-height: 28px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {handle_hover}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        )

    def _trash_button_stylesheet(self) -> str:
        hover = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_RED, 0.12)
        pressed = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_RED, 0.20)
        return (
            "QPushButton {"
            "background-color: transparent;"
            "border: none;"
            f"border-radius: {theme.RADIUS_MD}px;"
            "padding: 0;"
            "}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
            f"QPushButton:pressed {{ background-color: {pressed}; }}"
        )

    def _apply_script_row_style(self, card: QFrame):
        bg = theme._mix_hex_colors(theme.CLR_BG_CARD, theme.CLR_BG_ELEVATED, 0.38)
        hover = theme._mix_hex_colors(bg, theme.CLR_ACCENT, 0.04)
        border = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_ACCENT, 0.08)
        card.setStyleSheet(
            f"QFrame#ScheduledScriptRow {{"
            f"background-color: {bg};"
            f"border: 1px solid {border};"
            f"border-radius: {theme.RADIUS_MD}px;"
            "}"
            f"QFrame#ScheduledScriptRow:hover {{"
            f"background-color: {hover};"
            f"border-color: {theme.CLR_BORDER};"
            "}"
        )

    def _apply_hero_styles(self):
        if hasattr(self, "_hero_bar"):
            self._hero_bar.setStyleSheet(
                f"QFrame#ScheduledHeroBar {{"
                f"background-color: {theme.CLR_BG_CARD};"
                "border: none;"
                f"border-radius: {theme.RADIUS_LG}px;"
                "}"
            )
        if hasattr(self, "_hero_title_accent"):
            accent = theme._mix_hex_colors(theme.CLR_ACCENT, theme.CLR_TEXT_PRI, 0.06)
            self._hero_title_accent.setStyleSheet(
                f"QFrame#ScheduledHeroTitleAccent {{"
                f"background-color: {accent};"
                "border: none;"
                "border-radius: 2px;"
                "}"
            )
        if hasattr(self, "_hero_title"):
            self._hero_title.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
            )
        if hasattr(self, "_hero_badge"):
            badge_bg = theme._mix_hex_colors(theme.CLR_BG_CARD, theme.CLR_ACCENT, 0.10)
            badge_border = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_ACCENT, 0.34)
            self._hero_badge.setStyleSheet(
                "QLabel#ScheduledHeroBadge {"
                f"background-color: {badge_bg};"
                f"color: {theme.CLR_ACCENT};"
                f"border: 1px solid {badge_border};"
                "border-radius: 12px;"
                "padding: 2px 10px;"
                "font-weight: 600;"
                "}"
            )
        if hasattr(self, "_hero_switch_wrap"):
            self._hero_switch_wrap.setStyleSheet(
                "QFrame#ScheduledHeroSwitch {"
                f"background-color: {theme.CLR_BG_ELEVATED};"
                f"border: 1px solid {theme.CLR_HAIRLINE};"
                "border-radius: 18px;"
                "}"
            )
        if hasattr(self, "_hero_switch_label"):
            self._hero_switch_label.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
            )

    def _apply_theme_styles(self):
        self.setStyleSheet(f"FluentPage {{ background-color: {theme.CLR_BG}; }}")
        if hasattr(self, "_scroll"):
            self._scroll.setStyleSheet(
                f"QScrollArea {{ background-color: {theme.CLR_BG}; border: none; }}"
            )
        self._apply_hero_styles()
        if hasattr(self, "_summary_label"):
            self._summary_label.setStyleSheet(self._summary_stylesheet())
        if hasattr(self, "_scripts_scroll"):
            self._scripts_scroll.setStyleSheet(self._scroll_stylesheet())
        for widget in self.findChildren(QWidget):
            fn = getattr(widget, "apply_theme_styles", None)
            if callable(fn):
                fn()
        for frame in self.findChildren(QFrame, "ScheduledScriptRow"):
            self._apply_script_row_style(frame)
        for frame in self.findChildren(QFrame, "ScheduledEmptyState"):
            frame.setStyleSheet(
                f"QFrame#ScheduledEmptyState {{ background-color: {theme.CLR_BG_ELEVATED}; "
                f"border: 1px dashed {theme.CLR_BORDER}; border-radius: {theme.RADIUS_MD}px; }}"
            )
        for frame in self.findChildren(QFrame, "ScheduledHelpItem"):
            frame.setStyleSheet(
                f"QFrame#ScheduledHelpItem {{ background: {theme.CLR_BG_ELEVATED}; "
                f"border: 1px solid {theme.CLR_HAIRLINE}; border-radius: {theme.RADIUS_SM}px; }}"
            )
