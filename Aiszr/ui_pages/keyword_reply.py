"""关键词回复编辑页 — 横向网格布局，对齐 HomePage 风格。

左主区: 关键词规则卡（内联操作行 + 规则列表 + per-row 删除 + 命中计数 caption）
右侧栏: 模板管理卡 + 相关设置卡 + 匹配模式说明卡
"""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QFont, QIntValidator
from PyQt5.QtWidgets import (
    QLabel, QTextEdit, QWidget, QHBoxLayout, QVBoxLayout,
    QGridLayout, QSizePolicy, QScrollArea, QFrame,
)
import ui_theme as theme

from ui_theme import _build_text_area_stylesheet
from ui_theme import SiSwitch
from ui_components import MacCard, MacButton, MacLineEdit, MacComboBox
from siui.components.page import SiPage

from keyword_engine import KeywordEngine, KeywordTemplate, KeywordRule
from ui_settings import _load_settings, _save_settings


_MODE_LABELS = ["包含", "完全匹配", "正则表达式"]
_MODE_VALUES = ["contains", "exact", "regex"]


def _make_back_button(parent, back_signal) -> QWidget:
    area = QWidget(parent)
    area.setFixedSize(74, 34)
    layout = QHBoxLayout(area)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    btn = MacButton("返回", variant="secondary", parent=area)
    btn.setFixedSize(68, 30)
    btn.setToolTip("返回上一页")
    btn.clicked.connect(back_signal.emit)
    layout.addWidget(btn)
    return area


class KeywordReplyPage(SiPage):
    back_requested = pyqtSignal()
    auto_reply_toggled = pyqtSignal(bool)
    rules_changed = pyqtSignal()                # 用户保存了规则
    related_settings_changed = pyqtSignal()     # 用户改了冷却 / 速率限制

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._engine = KeywordEngine()
        self._rule_widgets: list[dict] = []
        self._empty_card: QFrame | None = None
        self._hit_stats: dict[str, tuple[int, str]] = {}  # keyword → (count, hh:mm:ss)
        self._dirty = False
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

        grid.addWidget(self._build_rules_card(), 0, 0, alignment=Qt.AlignTop)

        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.addWidget(self._build_template_card())
        right_col.addWidget(self._build_related_settings_card())
        right_col.addWidget(self._build_help_card())
        right_col.addStretch(1)
        grid.addLayout(right_col, 0, 1)

        outer.addLayout(grid, stretch=1)
        self.setAttachment(root)
        self._load_from_settings()
        self._set_dirty(False)

    # ── Hero ────────────────────────────────────────────────

    def _build_hero(self) -> QWidget:
        row = QFrame(self)
        row.setObjectName("KeywordHeroBar")
        row.setFixedHeight(64)
        self._hero_bar = row
        ly = QHBoxLayout(row)
        ly.setContentsMargins(14, 0, 14, 0)
        ly.setSpacing(12)
        ly.addWidget(_make_back_button(self, self.back_requested))

        self._hero_divider = QFrame(row)
        self._hero_divider.setObjectName("KeywordHeroDivider")
        self._hero_divider.setFixedSize(1, 30)
        ly.addWidget(self._hero_divider)

        title_wrap = QWidget(row)
        title_wrap.setStyleSheet("background: transparent;")
        title_layout = QHBoxLayout(title_wrap)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        self._hero_title_accent = QFrame(title_wrap)
        self._hero_title_accent.setObjectName("KeywordHeroTitleAccent")
        self._hero_title_accent.setFixedSize(4, 26)
        title_layout.addWidget(self._hero_title_accent)
        self._hero_title = QLabel("关键词回复")
        title_font = QFont(theme.FONT_TITLE_1)
        title_font.setPointSize(20)
        title_font.setWeight(QFont.DemiBold)
        title_font.setLetterSpacing(QFont.PercentageSpacing, 100)
        self._hero_title.setFont(title_font)
        title_layout.addWidget(self._hero_title)
        self._hero_badge = QLabel("视频号")
        self._hero_badge.setObjectName("KeywordHeroBadge")
        self._hero_badge.setFixedHeight(24)
        self._hero_badge.setAlignment(Qt.AlignCenter)
        title_layout.addWidget(self._hero_badge)
        title_layout.addStretch(1)
        ly.addWidget(title_wrap, stretch=1)
        ly.addStretch(1)

        self._hero_switch_wrap = QFrame(row)
        self._hero_switch_wrap.setObjectName("KeywordHeroSwitch")
        self._hero_switch_wrap.setFixedHeight(36)
        switch_layout = QHBoxLayout(self._hero_switch_wrap)
        switch_layout.setContentsMargins(12, 0, 10, 0)
        switch_layout.setSpacing(8)
        self._hero_switch_label = QLabel("自动回复")
        self._hero_switch_label.setObjectName("KeywordHeroSwitchLabel")
        self._hero_switch_label.setFont(theme.FONT_BODY_EMPH)
        switch_layout.addWidget(self._hero_switch_label)
        self._auto_reply_switch = SiSwitch(self._hero_switch_wrap)
        self._auto_reply_switch.toggled.connect(self._on_auto_reply_toggled)
        switch_layout.addWidget(self._auto_reply_switch)
        ly.addWidget(self._hero_switch_wrap)
        self._apply_hero_styles()
        return row

    def _apply_hero_styles(self):
        if hasattr(self, "_hero_bar"):
            self._hero_bar.setStyleSheet(
                f"QFrame#KeywordHeroBar {{"
                f"background-color: {theme.CLR_BG_CARD};"
                f"border: none;"
                f"border-radius: {theme.RADIUS_LG}px;"
                f"}}"
            )
        if hasattr(self, "_hero_divider"):
            self._hero_divider.setStyleSheet(
                f"QFrame#KeywordHeroDivider {{ background-color: {theme.CLR_HAIRLINE}; border: none; }}"
            )
        if hasattr(self, "_hero_title_accent"):
            accent = theme._mix_hex_colors(theme.CLR_ACCENT, theme.CLR_TEXT_PRI, 0.06)
            self._hero_title_accent.setStyleSheet(
                f"QFrame#KeywordHeroTitleAccent {{"
                f"background-color: {accent};"
                f"border: none;"
                f"border-radius: 2px;"
                f"}}"
            )
        if hasattr(self, "_hero_title"):
            self._hero_title.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
            )
        if hasattr(self, "_hero_badge"):
            badge_bg = theme._mix_hex_colors(theme.CLR_BG_CARD, theme.CLR_ACCENT, 0.10)
            badge_border = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_ACCENT, 0.34)
            self._hero_badge.setStyleSheet(
                f"QLabel#KeywordHeroBadge {{"
                f"background-color: {badge_bg};"
                f"color: {theme.CLR_ACCENT};"
                f"border: 1px solid {badge_border};"
                f"border-radius: 12px;"
                f"padding: 2px 10px;"
                f"font-weight: 600;"
                f"}}"
            )
        if hasattr(self, "_hero_switch_wrap"):
            self._hero_switch_wrap.setStyleSheet(
                f"QFrame#KeywordHeroSwitch {{"
                f"background-color: {theme.CLR_BG_ELEVATED};"
                f"border: 1px solid {theme.CLR_HAIRLINE};"
                f"border-radius: 18px;"
                f"}}"
            )
        if hasattr(self, "_hero_switch_label"):
            self._hero_switch_label.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
            )

    # ── Template card ───────────────────────────────────────

    def _build_template_card(self) -> MacCard:
        card = MacCard(self, title="模板管理", subtitle="按直播场景切换话术")
        body = card.body()
        body.setSpacing(10)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(theme.SPACING_SM)
        lbl = QLabel("当前")
        lbl.setFont(theme.FONT_BODY)
        combo_row.addWidget(lbl)
        self._tmpl_combo = MacComboBox()
        self._tmpl_combo.currentTextChanged.connect(self._on_tmpl_switch)
        combo_row.addWidget(self._tmpl_combo, stretch=1)
        body.addLayout(combo_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(theme.SPACING_SM)
        add_tmpl_btn = MacButton("+ 新增", variant="secondary")
        add_tmpl_btn.clicked.connect(self._add_template)
        btn_row.addWidget(add_tmpl_btn, stretch=1)
        del_tmpl_btn = MacButton("删除", variant="destructive")
        del_tmpl_btn.clicked.connect(self._del_template)
        btn_row.addWidget(del_tmpl_btn, stretch=1)
        body.addLayout(btn_row)
        return card

    # ── Related settings card ──────────────────────────────

    def _build_related_settings_card(self) -> MacCard:
        card = MacCard(self, title="频率控制", subtitle="降低重复触发风险")
        body = card.body()
        body.setSpacing(10)

        cd_row = QHBoxLayout()
        cd_row.setSpacing(theme.SPACING_SM)
        cd_label = QLabel("同关键词冷却（秒）")
        cd_label.setFont(theme.FONT_BODY)
        cd_row.addWidget(cd_label)
        cd_row.addStretch(1)
        self._cooldown_edit = MacLineEdit(placeholder="30")
        self._cooldown_edit.setFixedSize(80, 28)
        self._cooldown_edit.setValidator(QIntValidator(0, 36000, self))
        self._cooldown_edit.editingFinished.connect(self._on_related_settings_changed)
        cd_row.addWidget(self._cooldown_edit)
        body.addLayout(cd_row)

        rt_row = QHBoxLayout()
        rt_row.setSpacing(theme.SPACING_SM)
        rt_label = QLabel("每分钟最多响应")
        rt_label.setFont(theme.FONT_BODY)
        rt_row.addWidget(rt_label)
        rt_row.addStretch(1)
        self._rate_edit = MacLineEdit(placeholder="20")
        self._rate_edit.setFixedSize(80, 28)
        self._rate_edit.setValidator(QIntValidator(1, 600, self))
        self._rate_edit.editingFinished.connect(self._on_related_settings_changed)
        rt_row.addWidget(self._rate_edit)
        body.addLayout(rt_row)
        return card

    # ── Help card (match-mode legend) ───────────────────────

    def _build_help_card(self) -> MacCard:
        card = MacCard(self, title="匹配模式")
        body = card.body()
        body.setSpacing(6)
        for title, desc in (
            ("包含", "弹幕里含关键词即触发（最常用）"),
            ("完全匹配", "弹幕与关键词一字不差才触发"),
            ("正则表达式", "高级用法，写 ^价格.*多少 这种规则"),
        ):
            wrap = QFrame(card)
            wrap.setObjectName("KeywordHelpItem")
            wrap.setStyleSheet(
                f"QFrame#KeywordHelpItem {{ background: {theme.CLR_BG_ELEVATED}; "
                f"border: 1px solid {theme.CLR_HAIRLINE}; border-radius: {theme.RADIUS_SM}px; }}"
            )
            ly = QVBoxLayout(wrap)
            ly.setContentsMargins(10, 7, 10, 7)
            ly.setSpacing(2)
            t = QLabel(title)
            t.setFont(theme.FONT_BODY_EMPH)
            ly.addWidget(t)
            d = QLabel(desc)
            d.setFont(theme.FONT_CAPTION)
            d.setWordWrap(True)
            ly.addWidget(d)
            body.addWidget(wrap)
        return card

    # ── Rules card ──────────────────────────────────────────

    def _build_rules_card(self) -> MacCard:
        card = MacCard(self, title="关键词规则", subtitle="命中后直接注入视频号评论框")
        body = card.body()
        body.setSpacing(12)

        ops = QHBoxLayout()
        ops.setSpacing(theme.SPACING_SM)
        add_btn = MacButton("+ 添加规则", variant="primary")
        add_btn.setFixedSize(118, 32)
        add_btn.clicked.connect(self._add_rule)
        ops.addWidget(add_btn)

        self._rules_summary_label = QLabel("0 条规则")
        self._rules_summary_label.setFont(theme.FONT_CAPTION)
        self._rules_summary_label.setFixedHeight(26)
        self._rules_summary_label.setStyleSheet(self._rules_summary_stylesheet())
        ops.addWidget(self._rules_summary_label)
        ops.addStretch(1)

        self._save_btn = MacButton("保存修改", variant="primary")
        self._save_btn.setFixedSize(92, 32)
        self._save_btn.clicked.connect(self._persist)
        ops.addWidget(self._save_btn)
        body.addLayout(ops)

        rules_wrap = QWidget(card)
        self._rules_layout = QVBoxLayout(rules_wrap)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(12)
        self._rules_layout.setAlignment(Qt.AlignTop)
        rules_wrap.setStyleSheet("background: transparent;")

        self._rules_scroll = QScrollArea(card)
        self._rules_scroll.setWidget(rules_wrap)
        self._rules_scroll.setWidgetResizable(True)
        self._rules_scroll.setFrameShape(QFrame.NoFrame)
        self._rules_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rules_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._rules_scroll.setStyleSheet(self._rules_scroll_stylesheet())
        body.addWidget(self._rules_scroll)
        return card

    def _rules_summary_stylesheet(self) -> str:
        bg = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_BG_CARD, 0.52)
        border = theme._mix_hex_colors(theme.CLR_HAIRLINE, theme.CLR_ACCENT, 0.12)
        return (
            f"QLabel {{"
            f"color: {theme.CLR_TEXT_SEC};"
            f"background-color: {bg};"
            f"border: 1px solid {border};"
            f"border-radius: 13px;"
            f"padding: 3px 11px;"
            f"}}"
        )

    def _rules_scroll_stylesheet(self) -> str:
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

    def _voice_toggle_stylesheet(self) -> str:
        bg = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_BG_CARD, 0.40)
        border = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_TEXT_PRI, 0.04)
        return (
            f"QFrame#KeywordVoiceToggle {{"
            f"background-color: {bg};"
            f"border: 1px solid {border};"
            f"border-radius: 16px;"
            f"}}"
        )

    # 规则区按内容收缩；规则很多时才在内部滚动，避免截图里的大面积空白。
    _CHROME_OFFSET = 198

    def _update_rules_scroll_height(self):
        if not hasattr(self, "_rules_scroll"):
            return
        sa = getattr(self, "scroll_area", None)
        avail = sa.height() if sa is not None and sa.height() > 0 else 600
        row_count = max(1, len(self._rule_widgets))
        desired = row_count * 138 + 16
        max_height = max(260, avail - self._CHROME_OFFSET)
        self._rules_scroll.setFixedHeight(max(150, min(desired, max_height)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # SiPage 只同步 attachment 的宽度，不管高度 → root 高度永远是初始值，
        # 导致 setFixedHeight 在父布局空间不足时被压缩。这里手动把 attachment
        # 高度顶到 scroll_area 的高度，让内部布局能拿到全部窗口高度。
        sa = getattr(self, "scroll_area", None)
        if sa is not None:
            att = sa.attachment()
            if att is not None and att.height() != sa.height():
                att.resize(att.width(), sa.height())
        self._update_rules_scroll_height()

    # ── Rule row ────────────────────────────────────────────

    def _make_rule_widget(self, rule: KeywordRule | None = None) -> tuple[QFrame, dict]:
        card = QFrame(self)
        card.setObjectName("KeywordRuleRow")
        card.setFixedHeight(126)
        self._apply_rule_row_style(card)
        body = QVBoxLayout(card)
        body.setSpacing(9)
        body.setContentsMargins(14, 12, 14, 10)

        top_row_widget = QWidget(card)
        top_row_widget.setFixedHeight(34)
        top_row = QHBoxLayout(top_row_widget)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        kw_edit = MacLineEdit(placeholder="关键词")
        kw_edit.setFixedSize(240, 32)
        if rule:
            kw_edit.setText(rule.keyword)
        kw_edit.textChanged.connect(lambda _: self._set_dirty(True))
        top_row.addWidget(kw_edit)

        mode_combo = MacComboBox()
        mode_combo.setFixedSize(128, 32)
        mode_combo.addItems(_MODE_LABELS)
        if rule:
            try:
                mode_combo.setCurrentIndex(_MODE_VALUES.index(rule.match_mode))
            except ValueError:
                mode_combo.setCurrentIndex(0)
        mode_combo.currentIndexChanged.connect(lambda _: self._set_dirty(True))
        top_row.addWidget(mode_combo)

        voice_cluster = QFrame(card)
        voice_cluster.setObjectName("KeywordVoiceToggle")
        voice_cluster.setFixedHeight(32)
        voice_cluster.setStyleSheet(self._voice_toggle_stylesheet())
        voice_layout = QHBoxLayout(voice_cluster)
        voice_layout.setContentsMargins(10, 0, 8, 0)
        voice_layout.setSpacing(theme.SPACING_XS)
        voice_label = QLabel("语音")
        voice_label.setFont(theme.FONT_CAPTION)
        voice_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_SEC}; background: transparent; border: none;"
        )
        voice_layout.addWidget(voice_label)
        voice_switch = SiSwitch(voice_cluster)
        if rule:
            voice_switch.setChecked(rule.generate_voice)
        voice_switch.toggled.connect(lambda _: (self._set_dirty(True), self._refresh_rules_summary()))
        voice_layout.addWidget(voice_switch)
        top_row.addWidget(voice_cluster)

        top_row.addStretch(1)

        del_btn = MacButton("删除", variant="secondary")
        del_btn.setFixedSize(58, 30)
        top_row.addWidget(del_btn)
        body.addWidget(top_row_widget)

        reply_edit = QTextEdit()
        reply_edit.setPlaceholderText("回复话术")
        reply_edit.setFixedHeight(46)
        reply_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        reply_edit.setStyleSheet(_build_text_area_stylesheet(
            radius=theme.RADIUS_MD, padding="8px 10px"))
        if rule:
            reply_edit.setPlainText(rule.reply)
        reply_edit.textChanged.connect(lambda: self._set_dirty(True))
        body.addWidget(reply_edit)

        hit_label = QLabel("命中 0 次")
        hit_label.setFont(theme.FONT_CAPTION)
        hit_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_TERT}; background: transparent; border: none;"
        )
        body.addWidget(hit_label)

        widgets = {
            "kw": kw_edit, "reply": reply_edit, "mode": mode_combo,
            "voice": voice_switch, "card": card, "hit": hit_label,
        }
        del_btn.clicked.connect(lambda: self._delete_rule(widgets))
        return card, widgets

    def _apply_rule_row_style(self, card: QFrame):
        bg = theme._mix_hex_colors(theme.CLR_BG_CARD, theme.CLR_BG_ELEVATED, 0.38)
        hover = theme._mix_hex_colors(bg, theme.CLR_ACCENT, 0.04)
        border = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_ACCENT, 0.08)
        card.setStyleSheet(
            f"QFrame#KeywordRuleRow {{"
            f"background-color: {bg};"
            f"border: 1px solid {border};"
            f"border-radius: {theme.RADIUS_MD}px;"
            f"}}"
            f"QFrame#KeywordRuleRow:hover {{"
            f"background-color: {hover};"
            f"border-color: {theme.CLR_BORDER};"
            f"}}"
        )

    # ── Empty state ─────────────────────────────────────────

    def _make_empty_state_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("KeywordEmptyState")
        card.setFixedHeight(92)
        card.setStyleSheet(
            f"QFrame#KeywordEmptyState {{ background-color: {theme.CLR_BG_ELEVATED}; "
            f"border: 1px dashed {theme.CLR_BORDER}; border-radius: {theme.RADIUS_MD}px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        label = QLabel("还没有规则。点击上方「+ 添加一行」开始配置第一条关键词回复。")
        label.setFont(theme.FONT_CAPTION)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; background: transparent; border: none;")
        layout.addWidget(label)
        return card

    def _show_empty_card(self):
        if self._empty_card is None:
            self._empty_card = self._make_empty_state_card()
            self._rules_layout.addWidget(self._empty_card)

    def _remove_empty_card(self):
        if self._empty_card is not None:
            self._empty_card.setParent(None)
            self._empty_card.deleteLater()
            self._empty_card = None

    # ── Rules management ────────────────────────────────────

    def _add_rule(self):
        self._remove_empty_card()
        card, widgets = self._make_rule_widget()
        self._rules_layout.addWidget(card)
        self._rule_widgets.append(widgets)
        self._refresh_rules_summary()
        self._update_rules_scroll_height()
        self._set_dirty(True)

    def _delete_rule(self, widgets: dict):
        if widgets not in self._rule_widgets:
            return
        self._rule_widgets.remove(widgets)
        card = widgets["card"]
        card.setParent(None)
        card.deleteLater()
        if not self._rule_widgets:
            self._show_empty_card()
        self._refresh_rules_summary()
        self._update_rules_scroll_height()
        self._set_dirty(True)

    def _clear_rules(self):
        for w in self._rule_widgets:
            w["card"].setParent(None)
            w["card"].deleteLater()
        self._rule_widgets.clear()
        self._remove_empty_card()

    # ── Template management ─────────────────────────────────

    def _flush_widgets_to_engine(self):
        """切模板 / 保存前，把当前 widget 编辑写回 engine 中当前 active 模板。

        Why: _on_tmpl_switch 直接 set_active + rebuild 会丢掉用户在屏上的未保存改动。
        """
        tmpl = self._engine.get_template()
        if tmpl is None:
            return
        tmpl.rules = [
            KeywordRule(
                keyword=w["kw"].text(),
                reply=w["reply"].toPlainText(),
                match_mode=_MODE_VALUES[w["mode"].currentIndex()],
                generate_voice=w["voice"].isChecked(),
            )
            for w in self._rule_widgets
        ]

    def _on_tmpl_switch(self, name: str):
        if not name:
            return
        # flush BEFORE switching — set_active 之后 get_template() 就指向新模板了
        self._flush_widgets_to_engine()
        self._engine.set_active(name)
        self._rebuild_rules()

    def _add_template(self):
        name = f"模板{self._tmpl_combo.count() + 1}"
        self._engine._templates[name] = KeywordTemplate(name=name)
        self._tmpl_combo.addItem(name)
        self._tmpl_combo.setCurrentText(name)  # → triggers _on_tmpl_switch → flush

    def _del_template(self):
        name = self._tmpl_combo.currentText()
        if not name:
            return
        # block signal so the about-to-be-deleted template doesn't get re-flushed
        self._tmpl_combo.blockSignals(True)
        self._tmpl_combo.removeItem(self._tmpl_combo.currentIndex())
        self._engine._templates.pop(name, None)
        remaining = self._tmpl_combo.currentText()
        if remaining:
            self._engine.set_active(remaining)
        self._tmpl_combo.blockSignals(False)
        self._rebuild_rules()

    def _rebuild_rules(self):
        self._clear_rules()
        tmpl = self._engine.get_template()
        if tmpl:
            for rule in tmpl.rules:
                card, widgets = self._make_rule_widget(rule)
                self._rules_layout.addWidget(card)
                self._rule_widgets.append(widgets)
                self._refresh_hit_caption(widgets)
        if not self._rule_widgets:
            self._show_empty_card()
        self._refresh_rules_summary()
        self._update_rules_scroll_height()
        self._set_dirty(False)

    def _refresh_hit_caption(self, widgets: dict):
        kw = widgets["kw"].text()
        stat = self._hit_stats.get(kw)
        if stat is None:
            widgets["hit"].setText("命中 0 次")
        else:
            count, ts = stat
            widgets["hit"].setText(f"命中 {count} 次 · 最近 {ts}")

    def _refresh_rules_summary(self):
        if not hasattr(self, "_rules_summary_label"):
            return
        count = len(self._rule_widgets)
        enabled_voice = sum(1 for w in self._rule_widgets if w["voice"].isChecked())
        self._rules_summary_label.setText(f"{count} 条规则 · {enabled_voice} 条启用语音")

    # ── Persistence ─────────────────────────────────────────

    def _load_from_settings(self):
        data = _load_settings()
        self._engine.load_templates(data)
        names = self._engine.get_template_names()
        self._tmpl_combo.blockSignals(True)
        self._tmpl_combo.clear()
        if names:
            self._tmpl_combo.addItems(names)
            self._engine.set_active(names[0])
        else:
            self._tmpl_combo.addItem("模板1")
            self._engine._templates["模板1"] = KeywordTemplate(name="模板1")
            self._engine.set_active("模板1")
        self._tmpl_combo.blockSignals(False)
        self._rebuild_rules()

    def _persist(self):
        self._flush_widgets_to_engine()
        data = _load_settings()
        tmpl_data = {
            name: {
                "rules": [{
                    "keyword": r.keyword,
                    "reply": r.reply,
                    "match_mode": r.match_mode,
                    "generate_voice": r.generate_voice,
                } for r in tmpl.rules]
            }
            for name, tmpl in self._engine._templates.items()
        }
        data["keyword_templates"] = tmpl_data
        _save_settings(data)
        self._set_dirty(False)
        self.rules_changed.emit()

    # ── Dirty save button ──────────────────────────────────

    def _set_dirty(self, dirty: bool):
        self._dirty = bool(dirty)
        if hasattr(self, "_save_btn"):
            self._save_btn.setText("保存*" if self._dirty else "保存")
            self._save_btn.setEnabled(True)  # 始终允许点（dirty 仅做视觉提示）

    # ── Public API (worker / parent app uses these) ────────

    def get_active_template_name(self) -> str:
        return self._tmpl_combo.currentText()

    def set_active_template_name(self, name: str):
        if not name:
            return
        names = [self._tmpl_combo.itemText(i) for i in range(self._tmpl_combo.count())]
        if name not in names:
            return
        if self._tmpl_combo.currentText() == name:
            return
        # 让 _on_tmpl_switch 跑（flush + rebuild），但不再 emit 给首页绕回来
        self._tmpl_combo.setCurrentText(name)

    def set_auto_reply_checked(self, checked: bool):
        if hasattr(self, "_auto_reply_switch"):
            self._auto_reply_switch.blockSignals(True)
            self._auto_reply_switch.setChecked(bool(checked))
            self._auto_reply_switch.blockSignals(False)

    def set_related_settings(self, cooldown_sec: int, rate_per_min: int):
        if hasattr(self, "_cooldown_edit"):
            self._cooldown_edit.blockSignals(True)
            self._cooldown_edit.setText(str(int(cooldown_sec)))
            self._cooldown_edit.blockSignals(False)
        if hasattr(self, "_rate_edit"):
            self._rate_edit.blockSignals(True)
            self._rate_edit.setText(str(int(rate_per_min)))
            self._rate_edit.blockSignals(False)

    def on_rule_hit(self, keyword: str, count: int):
        ts = datetime.now().strftime("%H:%M:%S")
        self._hit_stats[keyword] = (count, ts)
        for w in self._rule_widgets:
            if w["kw"].text() == keyword:
                w["hit"].setText(f"命中 {count} 次 · 最近 {ts}")

    # ── Internal signal slots ──────────────────────────────

    def _on_auto_reply_toggled(self, checked: bool):
        self.auto_reply_toggled.emit(bool(checked))

    def _on_related_settings_changed(self):
        try:
            cd = int(self._cooldown_edit.text() or "30")
        except ValueError:
            cd = 30
        try:
            rt = int(self._rate_edit.text() or "20")
        except ValueError:
            rt = 20
        data = _load_settings()
        data["keyword_auto_reply_global_cooldown_sec"] = max(0, cd)
        data["keyword_auto_reply_rate_limit_per_min"] = max(1, rt)
        _save_settings(data)
        self.related_settings_changed.emit()

    # ── Theme hot-switch ────────────────────────────────────

    def _apply_theme_styles(self):
        # 让 SiPage chrome 跟主题走 — 默认 siui 给一个亮灰色，跟我们的 CLR_BG_CARD 黑对比刺眼
        self.setStyleSheet(f"SiPage {{ background-color: {theme.CLR_BG}; }}")
        if hasattr(self, "scroll_area"):
            self.scroll_area.setStyleSheet(
                f"QScrollArea {{ background-color: {theme.CLR_BG}; border: none; }}"
            )
        if hasattr(self, "_hero_title"):
            self._apply_hero_styles()
        if hasattr(self, "_rules_summary_label"):
            self._rules_summary_label.setStyleSheet(self._rules_summary_stylesheet())
        if hasattr(self, "_rules_scroll"):
            self._rules_scroll.setStyleSheet(self._rules_scroll_stylesheet())
        for w in self.findChildren(QWidget):
            fn = getattr(w, "apply_theme_styles", None)
            if callable(fn):
                fn()
        for frame in self.findChildren(QFrame, "KeywordRuleRow"):
            self._apply_rule_row_style(frame)
        for frame in self.findChildren(QFrame, "KeywordVoiceToggle"):
            frame.setStyleSheet(self._voice_toggle_stylesheet())
        for frame in self.findChildren(QFrame, "KeywordEmptyState"):
            frame.setStyleSheet(
                f"QFrame#KeywordEmptyState {{ background-color: {theme.CLR_BG_ELEVATED}; "
                f"border: 1px dashed {theme.CLR_BORDER}; border-radius: {theme.RADIUS_MD}px; }}"
            )
        for frame in self.findChildren(QFrame, "KeywordHelpItem"):
            frame.setStyleSheet(
                f"QFrame#KeywordHelpItem {{ background: {theme.CLR_BG_ELEVATED}; "
                f"border: 1px solid {theme.CLR_HAIRLINE}; border-radius: {theme.RADIUS_SM}px; }}"
            )
        for te in self.findChildren(QTextEdit):
            te.setStyleSheet(_build_text_area_stylesheet(
                radius=theme.RADIUS_MD, padding="8px 10px"))
