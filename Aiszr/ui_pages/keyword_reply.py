"""关键词回复编辑页 — 横向网格布局，对齐 HomePage 风格。

左主区: 关键词规则卡（内联操作行 + 规则列表 + per-row 删除 + 命中计数 caption）
右侧栏: 模板管理卡 + 相关设置卡 + 匹配模式说明卡
"""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QIntValidator
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
from ui import _make_back_button


_MODE_LABELS = ["包含", "完全匹配", "正则表达式"]
_MODE_VALUES = ["contains", "exact", "regex"]


class KeywordReplyPage(SiPage):
    back_requested = pyqtSignal()
    auto_reply_toggled = pyqtSignal(bool)
    rules_changed = pyqtSignal()                # 用户保存了规则
    related_settings_changed = pyqtSignal()     # 用户改了冷却 / 速率限制

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._engine = KeywordEngine()
        self._rule_widgets: list[dict] = []
        self._empty_card: MacCard | None = None
        self._hit_stats: dict[str, tuple[int, str]] = {}  # keyword → (count, hh:mm:ss)
        self._dirty = False
        self.setPadding(12)
        self.setScrollMaximumWidth(1600)

        root = QWidget(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(theme.SPACING_SM)

        outer.addWidget(self._build_hero())

        grid = QGridLayout()
        grid.setSpacing(theme.SPACING_MD)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 1)

        grid.addWidget(self._build_rules_card(), 0, 0, alignment=Qt.AlignTop)

        right_col = QVBoxLayout()
        right_col.setSpacing(theme.SPACING_MD)
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
        row = QWidget(self)
        row.setFixedHeight(36)
        ly = QHBoxLayout(row)
        ly.setContentsMargins(8, 0, 8, 0)
        ly.setSpacing(theme.SPACING_SM)
        ly.addWidget(_make_back_button(self, self.back_requested))
        self._hero_title = QLabel("关键词回复（视频号）")
        self._hero_title.setFont(theme.FONT_TITLE_2)
        ly.addWidget(self._hero_title)
        ly.addStretch(1)

        switch_label = QLabel("自动回复")
        switch_label.setFont(theme.FONT_BODY)
        ly.addWidget(switch_label)
        self._auto_reply_switch = SiSwitch(row)
        self._auto_reply_switch.toggled.connect(self._on_auto_reply_toggled)
        ly.addWidget(self._auto_reply_switch)
        return row

    # ── Template card ───────────────────────────────────────

    def _build_template_card(self) -> MacCard:
        card = MacCard(self, title="模板管理")
        body = card.body()

        desc = QLabel("每个模板独立保存一组关键词与回复，可随时切换。")
        desc.setFont(theme.FONT_CAPTION)
        desc.setWordWrap(True)
        body.addWidget(desc)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(theme.SPACING_SM)
        lbl = QLabel("模板")
        lbl.setFont(theme.FONT_BODY)
        combo_row.addWidget(lbl)
        self._tmpl_combo = MacComboBox()
        self._tmpl_combo.currentTextChanged.connect(self._on_tmpl_switch)
        combo_row.addWidget(self._tmpl_combo, stretch=1)
        body.addLayout(combo_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(theme.SPACING_SM)
        add_tmpl_btn = MacButton("+ 模板", variant="secondary")
        add_tmpl_btn.clicked.connect(self._add_template)
        btn_row.addWidget(add_tmpl_btn, stretch=1)
        del_tmpl_btn = MacButton("删除模板", variant="destructive")
        del_tmpl_btn.clicked.connect(self._del_template)
        btn_row.addWidget(del_tmpl_btn, stretch=1)
        body.addLayout(btn_row)
        return card

    # ── Related settings card ──────────────────────────────

    def _build_related_settings_card(self) -> MacCard:
        card = MacCard(self, title="相关设置")
        body = card.body()

        desc = QLabel("控制自动回复的触发频率，避免被风控。")
        desc.setFont(theme.FONT_CAPTION)
        desc.setWordWrap(True)
        body.addWidget(desc)

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
        card = MacCard(self, title="匹配模式说明")
        body = card.body()
        for title, desc in (
            ("包含", "弹幕里含关键词即触发（最常用）"),
            ("完全匹配", "弹幕与关键词一字不差才触发"),
            ("正则表达式", "高级用法，写 ^价格.*多少 这种规则"),
        ):
            t = QLabel(title)
            t.setFont(theme.FONT_BODY_EMPH)
            body.addWidget(t)
            d = QLabel(desc)
            d.setFont(theme.FONT_CAPTION)
            d.setWordWrap(True)
            body.addWidget(d)
        return card

    # ── Rules card ──────────────────────────────────────────

    def _build_rules_card(self) -> MacCard:
        card = MacCard(self, title="关键词规则")
        body = card.body()

        ops = QHBoxLayout()
        ops.setSpacing(theme.SPACING_SM)
        add_btn = MacButton("+ 添加一行", variant="primary")
        add_btn.setFixedSize(110, 32)
        add_btn.clicked.connect(self._add_rule)
        ops.addWidget(add_btn)

        ops.addStretch(1)

        self._save_btn = MacButton("保存", variant="primary")
        self._save_btn.setFixedSize(80, 32)
        self._save_btn.clicked.connect(self._persist)
        ops.addWidget(self._save_btn)
        body.addLayout(ops)

        rules_wrap = QWidget(card)
        self._rules_layout = QVBoxLayout(rules_wrap)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(theme.SPACING_SM)
        self._rules_layout.setAlignment(Qt.AlignTop)
        rules_wrap.setStyleSheet("background: transparent;")

        self._rules_scroll = QScrollArea(card)
        self._rules_scroll.setWidget(rules_wrap)
        self._rules_scroll.setWidgetResizable(True)
        self._rules_scroll.setFrameShape(QFrame.NoFrame)
        self._rules_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._rules_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._rules_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body.addWidget(self._rules_scroll)
        return card

    # 规则区高度始终跟窗口走 — 规则少内部留白、规则多内部出滚动条，卡片永远撑到窗口底。
    # 减掉的是 hero (~36) + 卡片标题 + ops 行 + grid 间距 + 页面 padding 与状态栏。
    _CHROME_OFFSET = 140

    def _update_rules_scroll_height(self):
        if not hasattr(self, "_rules_scroll"):
            return
        sa = getattr(self, "scroll_area", None)
        avail = sa.height() if sa is not None and sa.height() > 0 else 600
        self._rules_scroll.setFixedHeight(max(240, avail - self._CHROME_OFFSET))

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

    def _make_rule_widget(self, rule: KeywordRule | None = None) -> tuple[MacCard, dict]:
        card = MacCard(elevation=0, radius=theme.RADIUS_MD, padding=(12, 8, 12, 8))
        card.setFixedHeight(116)
        body = card.body()
        body.setSpacing(theme.SPACING_XS)
        body.setContentsMargins(0, 0, 0, 0)

        top_row_widget = QWidget(card)
        top_row_widget.setFixedHeight(36)
        top_row = QHBoxLayout(top_row_widget)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(theme.SPACING_SM)

        kw_edit = MacLineEdit(placeholder="关键词")
        kw_edit.setFixedSize(200, 32)
        if rule:
            kw_edit.setText(rule.keyword)
        kw_edit.textChanged.connect(lambda _: self._set_dirty(True))
        top_row.addWidget(kw_edit)

        mode_combo = MacComboBox()
        mode_combo.setFixedSize(130, 32)
        mode_combo.addItems(_MODE_LABELS)
        if rule:
            try:
                mode_combo.setCurrentIndex(_MODE_VALUES.index(rule.match_mode))
            except ValueError:
                mode_combo.setCurrentIndex(0)
        mode_combo.currentIndexChanged.connect(lambda _: self._set_dirty(True))
        top_row.addWidget(mode_combo)

        voice_cluster = QWidget(card)
        voice_cluster.setFixedHeight(32)
        voice_layout = QHBoxLayout(voice_cluster)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(theme.SPACING_XS)
        voice_label = QLabel("语音")
        voice_label.setFont(theme.FONT_BODY)
        voice_layout.addWidget(voice_label)
        voice_switch = SiSwitch(voice_cluster)
        if rule:
            voice_switch.setChecked(rule.generate_voice)
        voice_switch.toggled.connect(lambda _: self._set_dirty(True))
        voice_layout.addWidget(voice_switch)
        top_row.addWidget(voice_cluster)

        top_row.addStretch(1)

        del_btn = MacButton("删除", variant="destructive")
        del_btn.setFixedSize(64, 28)
        top_row.addWidget(del_btn)
        body.addWidget(top_row_widget)

        reply_edit = QTextEdit()
        reply_edit.setPlaceholderText("回复话术")
        reply_edit.setFixedHeight(40)
        reply_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        reply_edit.setStyleSheet(_build_text_area_stylesheet(
            radius=theme.RADIUS_MD, padding="4px 8px"))
        if rule:
            reply_edit.setPlainText(rule.reply)
        reply_edit.textChanged.connect(lambda: self._set_dirty(True))
        body.addWidget(reply_edit)

        hit_label = QLabel("未启用")
        hit_label.setFont(theme.FONT_CAPTION)
        body.addWidget(hit_label)

        widgets = {
            "kw": kw_edit, "reply": reply_edit, "mode": mode_combo,
            "voice": voice_switch, "card": card, "hit": hit_label,
        }
        del_btn.clicked.connect(lambda: self._delete_rule(widgets))
        return card, widgets

    # ── Empty state ─────────────────────────────────────────

    def _make_empty_state_card(self) -> MacCard:
        card = MacCard(elevation=0, radius=theme.RADIUS_MD,
                       padding=(theme.SPACING_MD,) * 4)
        card.setFixedHeight(72)
        label = QLabel("还没有规则。点击上方「+ 添加一行」开始配置第一条关键词回复。")
        label.setFont(theme.FONT_CAPTION)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        card.body().addWidget(label)
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
            self._hero_title.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
            )
        for w in self.findChildren(QWidget):
            fn = getattr(w, "apply_theme_styles", None)
            if callable(fn):
                fn()
        for te in self.findChildren(QTextEdit):
            te.setStyleSheet(_build_text_area_stylesheet(
                radius=theme.RADIUS_MD, padding="4px 8px"))
