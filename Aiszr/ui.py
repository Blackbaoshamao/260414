"""Aiszr: AI Digital Human Streaming Assistant (Steam++ Style UI).

Built with PyQt-SiliconUI, featuring:
  - Left sidebar navigation (Steam++ style)
  - 3 pages: SearchCenter, LiveRoom, AIConfig + Settings
  - CaptureWorker bridges async Playwright capture to Qt UI
"""

import asyncio
import collections
import contextlib
import html as html_module
import json
import os
import re
import sys
import time
import wave

from PyQt5.QtCore import (
    QObject, pyqtSignal, pyqtSlot, pyqtProperty, QThread, QTimer, QRect, QSize, Qt,
    QPropertyAnimation, QEasingCurve, QPointF,
)
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QTextCursor, QBrush, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QTextEdit, QLabel, QPushButton, QLineEdit, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QListWidget, QMessageBox, QFileDialog,
    QFrame, QAbstractButton,
    QSpinBox, QSizePolicy, QComboBox, QScrollArea,
)

from loguru import logger

from app_paths import app_dir

from ops_metrics import OpsMetrics

from truth_stream import TruthStreamProcessor

from replay_log import ReplayLogger

from obs_actions import (
    DEFAULT_OBS_ACTION_SETTINGS,
    ObsActionController,
    ObsActionRule,
    ObsActionSettings,
)
from tts_worker import TTSWorker

from voice_manager import VoiceActionResult, VoiceManager
from voice_models import (
    VOICE_MODELS,
    VOICE_PROVIDER_LABELS,
    VOICE_PROVIDERS,
    VoiceSettings,
)

from siui.core import SiColor, SiGlobal, GlobalFont, Si
from siui.gui import SiFont

from siui.templates.application.application import SiliconApplication

from siui.components.page import SiPage

from siui.components.widgets import (
    SiDenseHContainer, SiDenseVContainer,
    SiLabel, SiLineEdit, SiPushButton, SiSwitch as SiBaseSwitch, SiSvgLabel,
)
from siui.components.titled_widget_group import SiTitledWidgetGroup

from siui.components.option_card import SiOptionCardLinear

from siui.components.combobox.combobox import SiComboBox

from siui.components.slider import SiSliderH

from siui.templates.application.components.dialog.modal import SiModalDialog

from ui_theme import _iOSToggle, SiSwitch, _THEMES, _THEME_MAP, _THEME_LABELS, _DEFAULT_THEME, _current_theme
from ui_theme import _APP_ICON_NAME, _THEME_SATURATION_BOOST, _SATURATION_BOOST_KEYS
from ui_theme import _boost_hex_saturation, _tune_theme, _mix_hex_colors, _hex_with_alpha
from ui_theme import _placeholder_h, _update_siui_color_group, _apply_qt_global_theme
from ui_theme import _build_input_field_stylesheet, _build_text_area_stylesheet
from ui_theme import _secret_reveal_icon, _install_secret_reveal_action
from ui_theme import _tune_font_quality, FONT_MONO, FONT_MONO_SMALL, FONT_UI, FONT_TITLE
from ui_constants import DEFAULT_DATA_SOURCE, _DATA_SOURCE_OPTIONS
from ui_constants import _MESSAGE_FILTER_OPTIONS, _DEFAULT_MESSAGE_FILTERS, _CARD_W, _CARD_H
from ui_constants import DEFAULT_VOICE_SETTINGS, _VOICE_PROVIDER_API_FIELDS
from ui_constants import _SECRET_INPUT_FIELD_KEYS, _BROKEN_TEXT_PLACEHOLDERS
from ui_constants import _MYSTERY_VIEWER_NAMES, _MOJIBAKE_MARKERS
from ui_constants import _is_broken_text, _text_or_default
from ui_constants import _normalize_display_nickname, _normalize_data_source
from ui_constants import _normalize_message_filters

from ui_settings import _load_settings, _save_settings, SETTINGS_FILE

import ui_theme
def apply_theme(theme_name: str):
    """Wrapper: update ui_theme module + this module's globals."""
    ui_theme.apply_theme(theme_name)
    # Also update this module's namespace for backward compat
    import ui_theme as _ut
    globals().update({k: getattr(_ut, k) for k in [
        "CLR_BG", "CLR_BG_ELEVATED", "CLR_BG_CARD", "CLR_BG_INSET",
        "CLR_BORDER", "CLR_INPUT_BG",
        "CLR_TEXT_PRI", "CLR_TEXT_SEC", "CLR_TEXT_TERT",
        "CLR_MSG_CHAT", "CLR_MSG_GIFT", "CLR_MSG_LIKE", "CLR_MSG_FOLLOW",
        "CLR_ACCENT", "CLR_ACCENT_LIGHT", "CLR_ACCENT_TEXT",
        "CLR_GREEN", "CLR_RED", "CLR_YELLOW",
    ]})




# ---------------------------------------------------------------------------
# Color & font constants
# ---------------------------------------------------------------------------



def _make_back_button(parent, back_signal) -> SiDenseHContainer:
    """返回一个带 Mac 风格"返回"按钮的容器。"""
    from ui_components import MacButton
    area = SiDenseHContainer(parent)
    area.setFixedHeight(32)
    btn = MacButton("返回", variant="secondary", parent=parent)
    btn.setFixedSize(80, 28)
    btn.clicked.connect(back_signal.emit)
    area.addWidget(btn)
    return area

class FilterCheckBox(QCheckBox):
    _INDICATOR_SIZE = 16
    _INDICATOR_RADIUS = 4
    _LABEL_SPACING = 8

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFont(FONT_UI)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def sizeHint(self):
        metrics = self.fontMetrics()
        width = (
            self._INDICATOR_SIZE
            + self._LABEL_SPACING
            + metrics.horizontalAdvance(self.text())
            + 8
        )
        height = max(self._INDICATOR_SIZE, metrics.height()) + 6
        return QSize(width, height)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())

        rect = self.rect()
        indicator_y = (rect.height() - self._INDICATOR_SIZE) / 2
        indicator_rect = QRect(
            0,
            int(indicator_y),
            self._INDICATOR_SIZE,
            self._INDICATOR_SIZE,
        )

        if self.isChecked():
            indicator_bg = QColor(CLR_ACCENT)
            indicator_border = QColor(CLR_ACCENT_LIGHT)
        else:
            indicator_bg = QColor(CLR_BG_ELEVATED)
            indicator_border = QColor(CLR_BORDER)

        if not self.isEnabled():
            indicator_bg.setAlpha(160)
            indicator_border.setAlpha(160)

        painter.setPen(QPen(indicator_border, 1))
        painter.setBrush(indicator_bg)
        painter.drawRoundedRect(
            indicator_rect,
            self._INDICATOR_RADIUS,
            self._INDICATOR_RADIUS,
        )

        if self.isChecked():
            tick_pen = QPen(
                QColor(CLR_ACCENT_TEXT),
                2.2,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
            if not self.isEnabled():
                tick_color = tick_pen.color()
                tick_color.setAlpha(180)
                tick_pen.setColor(tick_color)
            painter.setPen(tick_pen)
            left = indicator_rect.left()
            top = indicator_rect.top()
            size = self._INDICATOR_SIZE
            painter.drawLine(
                int(left + size * 0.28),
                int(top + size * 0.55),
                int(left + size * 0.46),
                int(top + size * 0.73),
            )
            painter.drawLine(
                int(left + size * 0.46),
                int(top + size * 0.73),
                int(left + size * 0.76),
                int(top + size * 0.32),
            )

        text_color = QColor(CLR_TEXT_PRI if self.underMouse() else CLR_TEXT_SEC)
        if not self.isEnabled():
            text_color.setAlpha(180)
        painter.setPen(text_color)
        text_rect = rect.adjusted(
            self._INDICATOR_SIZE + self._LABEL_SPACING,
            0,
            0,
            0,
        )
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.TextSingleLine, self.text())


# ---------------------------------------------------------------------------
# DanmakuDisplay
# ---------------------------------------------------------------------------

class DanmakuDisplay(QTextBrowser):
    MAX_MESSAGES = 10000
    FLUSH_INTERVAL_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(FONT_MONO)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._apply_theme_styles()
        self._auto_scroll = True
        self._pending_html: list = []
        self._allowed_types = {key for key, _ in _MESSAGE_FILTER_OPTIONS}
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self.FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._flush_timer.start()

        sb = self.verticalScrollBar()
        sb.valueChanged.connect(self._on_scroll)

        self._scroll_btn = QPushButton("回到底部", self)
        self._scroll_btn.setFont(FONT_MONO_SMALL)
        self._scroll_btn.setFixedHeight(26)
        self._scroll_btn.setCursor(Qt.PointingHandCursor)
        self._apply_theme_styles()
        self._scroll_btn.hide()
        self._scroll_btn.clicked.connect(self._scroll_to_bottom)

    def _apply_theme_styles(self):
        self.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {CLR_INPUT_BG}; color: {CLR_TEXT_PRI};
                border: 1px solid {CLR_BORDER}; border-radius: 8px;
                padding: 8px 12px;
            }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: #555; border-radius: 3px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        """)
        if hasattr(self, "_scroll_btn"):
            self._scroll_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {CLR_BG_ELEVATED}; color: {CLR_TEXT_SEC};
                    border: 1px solid {CLR_BORDER}; border-radius: 13px;
                    padding: 3px 12px; font-size: 11pt;
                }}
                QPushButton:hover {{ background-color: {CLR_BG_CARD}; color: {CLR_TEXT_PRI}; }}
                QPushButton:pressed {{
                    background-color: {_mix_hex_colors(CLR_BG_CARD, "#000000", 0.15)};
                    padding-top: 4px; padding-bottom: 2px;
                    padding-left: 12px; padding-right: 12px;
                }}
            """)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scroll_btn.move(
            self.width() - self._scroll_btn.sizeHint().width() - 12,
            self.height() - 36,
        )

    def _on_scroll(self, value):
        sb = self.verticalScrollBar()
        was_auto = self._auto_scroll
        self._auto_scroll = (value >= sb.maximum() - 50)
        if was_auto and not self._auto_scroll:
            self._scroll_btn.show()
        elif self._auto_scroll:
            self._scroll_btn.hide()

    def _scroll_to_bottom(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        self._auto_scroll = True
        self._scroll_btn.hide()

    _DOM_GARBAGE_PATTERNS = (
        "刷新", "退出网页", "全屏", "开启小窗", "关注 G:", "标清", "全部商品", "充值",
        "本场点赞", "观看历史", "稍后再看", "退出网页全屏", "热门", "推荐", "直播",
        "清屏", "分享直播间", "粉丝团", "福袋", "心愿单", "购物车", "小黄车",
    )

    @pyqtSlot(dict)
    def append_message(self, msg: dict):
        if msg.get("type") not in self._allowed_types:
            return
        if self._is_dom_garbage(msg):
            return
        self._pending_html.append(self._format_message(msg))

    def _is_dom_garbage(self, msg: dict) -> bool:
        content = str(msg.get("content", "")).strip()
        nickname = _normalize_display_nickname(msg.get("nickname", ""))
        if not content:
            return True
        if len(content) > 100:
            return True
        for pat in self._DOM_GARBAGE_PATTERNS:
            if pat in content:
                return True
        if "¥" in content:
            return True
        if nickname:
            return False
        if re.search(r"x\d+", content):
            return True
        if not nickname and len(content) < 2:
            return True
        return False

    def set_allowed_types(self, allowed_types):
        self._allowed_types = set(allowed_types)

    def _flush_pending(self):
        if not self._pending_html:
            return
        for h in self._pending_html:
            self.append(h)
        self._pending_html.clear()
        doc = self.document()
        if doc.blockCount() > self.MAX_MESSAGES:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor,
                                doc.blockCount() - self.MAX_MESSAGES)
            cursor.removeSelectedText()
            cursor.deleteChar()
        if self._auto_scroll:
            self._scroll_to_bottom()

    @staticmethod
    def _format_message(msg: dict) -> str:
        msg_type = msg.get("type", "")
        time_str = msg.get("time", "")[11:19] if msg.get("time") else ""
        raw_nickname = _normalize_display_nickname(msg.get("nickname", ""))
        nickname = html_module.escape(raw_nickname)
        ts = f'<span style="color:{CLR_TEXT_TERT}">{time_str}</span>'

        if msg_type == "chat":
            content = html_module.escape(msg.get("content", ""))
            if nickname:
                return (
                    f'{ts} <span style="color:{CLR_MSG_CHAT}">{nickname}</span>'
                    f'<span style="color:{CLR_TEXT_TERT}">: </span>'
                    f'<span style="color:{CLR_MSG_CHAT}">{content}</span>'
                )
            return f'{ts} <span style="color:{CLR_MSG_CHAT}">{content}</span>'

        if msg_type == "gift":
            gift_name = str(msg.get("gift_name", "")).strip()
            gift_count = msg.get("gift_count", 0)
            if gift_name and gift_count > 0:
                text = f"{raw_nickname} \u9001\u51fa\u4e86 {gift_name} x{gift_count}" if raw_nickname else f"\u9001\u51fa\u4e86 {gift_name} x{gift_count}"
            elif gift_name:
                text = f"{raw_nickname} \u9001\u51fa\u4e86 {gift_name}" if raw_nickname else f"\u9001\u51fa\u4e86 {gift_name}"
            else:
                text = f"{raw_nickname} \u9001\u51fa\u4e86\u793c\u7269" if raw_nickname else "\u9001\u51fa\u4e86\u793c\u7269"
            return f'{ts} <span style="color:{CLR_MSG_GIFT}">{html_module.escape(text)}</span>'

        if msg_type == "like":
            text = f"{raw_nickname} \u70b9\u8d5e\u4e86" if raw_nickname else "\u70b9\u8d5e\u4e86"
            return f'{ts} <span style="color:{CLR_MSG_LIKE}">{html_module.escape(text)}</span>'

        if msg_type == "follow":
            text = f"{raw_nickname} \u5173\u6ce8\u4e86\u4e3b\u64ad" if raw_nickname else "\u5173\u6ce8\u4e86\u4e3b\u64ad"
            return f'{ts} <span style="color:{CLR_MSG_FOLLOW}">{html_module.escape(text)}</span>'

        if msg_type == "enter":
            text = f"{raw_nickname} \u8fdb\u5165\u4e86\u76f4\u64ad\u95f4" if raw_nickname else "\u6709\u89c2\u4f17\u8fdb\u5165\u4e86\u76f4\u64ad\u95f4"
            return f'{ts} <span style="color:{CLR_MSG_FOLLOW}">{html_module.escape(text)}</span>'

        if msg_type == "share":
            text = f"{raw_nickname} \u5206\u4eab\u4e86\u76f4\u64ad\u95f4" if raw_nickname else "\u6709\u89c2\u4f17\u5206\u4eab\u4e86\u76f4\u64ad\u95f4"
            return f'{ts} <span style="color:{CLR_MSG_LIKE}">{html_module.escape(text)}</span>'

        return f'{ts} <span style="color:{CLR_TEXT_TERT}">{html_module.escape(str(msg))}</span>'


# ---------------------------------------------------------------------------
# AIReplyDisplay
# ---------------------------------------------------------------------------

class AIReplyDisplay(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(FONT_MONO)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self._apply_theme_styles()
        self._auto_scroll = True
        self.verticalScrollBar().valueChanged.connect(
            lambda v: setattr(self, '_auto_scroll', v >= self.verticalScrollBar().maximum() - 50))

    def _apply_theme_styles(self):
        self.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {CLR_INPUT_BG}; color: {CLR_TEXT_PRI};
                border: 1px solid {CLR_BORDER}; border-radius: 8px;
                padding: 8px 8px;
            }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: #555; border-radius: 3px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def add_reply(self, target_user: str, target_msg: str, reply: str):
        u = html_module.escape(target_user)
        m = html_module.escape(target_msg)
        r = html_module.escape(reply)
        bubble = (
            f'<div style="background-color:{CLR_BG_ELEVATED}; border-radius:8px; '
            f'padding:8px 10px; margin:3px 0px; border:1px solid {CLR_BORDER};">'
            f'<span style="color:{CLR_ACCENT_LIGHT};font-size: 10pt">@{u}</span>'
            f'<span style="color:{CLR_TEXT_SEC};font-size: 11pt"> {m}</span><br>'
            f'<span style="color:{CLR_TEXT_PRI}">{r}</span>'
            f'</div>'
        )
        self.append(bubble)
        if self._auto_scroll:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


# ---------------------------------------------------------------------------
# CaptureWorker
# ---------------------------------------------------------------------------

class CaptureWorker(QObject):
    DEDUP_WINDOW_SEC = 1.0
    DEDUP_CACHE_MAX = 500

    login_state = pyqtSignal(str)
    danmaku_received = pyqtSignal(dict)
    connection_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    browser_ready = pyqtSignal()
    parse_failure_count = pyqtSignal(int)
    ai_reply_ready = pyqtSignal(str, str, str)
    ai_runtime_changed = pyqtSignal(object)
    obs_runtime_changed = pyqtSignal(object)
    obs_host_discovered = pyqtSignal(str)
    voice_runtime_changed = pyqtSignal(object)
    voice_action_finished = pyqtSignal(object)
    metrics_updated = pyqtSignal(dict)
    live_capture_state_changed = pyqtSignal(str, str)
    digital_human_state_changed = pyqtSignal(object)
    # Keyword auto-reply (Phase 10 第二批) — emit on 命中且通过冷却/速率限制
    # args: (keyword, reply_text, nickname, hit_count_after, injected)
    keyword_reply_fired = pyqtSignal(str, str, str, int, bool)

    def __init__(self):
        super().__init__()
        self._loop = None
        self._pw = None
        self._context = None
        self._capture = None
        self._decoder = None
        self._wechat = None
        self._queue = None
        self._ws_task = None
        self._running = False
        self._last_message_time = 0.0
        self._ai_engine = None
        self._dedup_cache: dict[tuple, float] = {}
        self._current_room_id = ""
        self._obs_action_settings = DEFAULT_OBS_ACTION_SETTINGS.to_dict()
        self._obs_controller: ObsActionController | None = None
        self._voice_settings = DEFAULT_VOICE_SETTINGS.to_dict()
        self._voice_manager = VoiceManager(VoiceSettings.from_dict(self._voice_settings))
        self._tts_worker: TTSWorker | None = None
        self._digital_human_pipeline = None
        self._digital_human_task: asyncio.Task | None = None
        self._digital_human_starting = False
        self._ops_metrics = OpsMetrics()
        self._truth_stream = TruthStreamProcessor(room_id="")
        self._replay_logger = ReplayLogger(path=app_dir() / "data" / "replay_log.jsonl")
        self._last_fail_count = 0
        self._live_capture_enabled = False
        self._live_capture_state = "disabled"

        # Keyword auto-reply pipeline (Phase 10 第二批)
        # KeywordEngine 持有完整模板字典；只对 source=="wechat" 弹幕生效。
        self._keyword_engine = None
        self._keyword_auto_reply_enabled = False
        self._keyword_global_cooldown_sec = 30
        self._keyword_rate_limit_per_min = 20
        self._keyword_last_hit: dict[str, float] = {}
        self._keyword_hit_log: collections.deque = collections.deque()
        self._keyword_hit_count: dict[str, int] = {}

    @pyqtSlot()
    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_init())
            self._loop.run_forever()
        except Exception as e:
            logger.error("CaptureWorker fatal: {}", e)
            self.error_occurred.emit(str(e))
        finally:
            self._loop.run_until_complete(self._async_cleanup())
            self._loop.close()

    async def _async_init(self):
        from ws_server import run_ws_server
        self._queue = asyncio.Queue(maxsize=5000)
        self._ws_task = self._loop.create_task(run_ws_server(self._queue))
        self._running = True
        await self._async_set_obs_action_settings(self._obs_action_settings)
        await self._async_set_voice_settings(self._voice_settings)
        self._emit_live_capture_state("disabled", "抓取未启用")
        self._loop.create_task(self._delayed_obs_discovery())

    async def _delayed_obs_discovery(self):
        await asyncio.sleep(3.0)
        await self._auto_discover_obs()

    async def _startup_with_status(self):
        from fetcher import startup
        task = self._loop.create_task(startup())
        done, _ = await asyncio.wait({task}, timeout=3.0)
        if not done:
            self.login_state.emit("scanning")
        return await task

    def _emit_live_capture_state(self, state: str, message: str):
        self._live_capture_state = state
        self.live_capture_state_changed.emit(state, message)

    async def _async_close_live_capture_resources(self):
        if self._capture:
            try:
                await self._capture.stop()
            except Exception:
                pass
            self._capture = None
        self._decoder = None
        self._current_room_id = ""
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._context = None
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None

    @pyqtSlot(bool, str)
    def set_live_capture_enabled(self, enabled: bool, source: str = "douyin"):
        if self._loop and self._loop.is_running():
            if source == "wechat":
                coro = self._async_start_wechat() if enabled else self._async_stop_wechat()
            else:
                coro = self._async_set_live_capture_enabled(bool(enabled))
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            return
        self._emit_live_capture_state("error", "后台线程尚未启动，请稍后再试")
        self.error_occurred.emit("后台线程尚未启动，请稍后再试")

    async def _async_set_live_capture_enabled(self, enabled: bool):
        if enabled:
            if self._context:
                self._live_capture_enabled = True
                self._emit_live_capture_state("ready", "直播抓取已启用")
                self.connection_changed.emit("disconnected")
                self.browser_ready.emit()
                return
            if self._live_capture_state == "starting":
                return
            self._live_capture_enabled = True
            self._emit_live_capture_state("starting", "正在启动浏览器...")
            self.login_state.emit("checking")
            try:
                self._pw, self._context = await self._startup_with_status()
            except Exception as exc:
                self._live_capture_enabled = False
                await self._async_close_live_capture_resources()
                self._emit_live_capture_state("error", f"直播抓取启动失败：{exc}")
                self.connection_changed.emit("disconnected")
                self.error_occurred.emit(f"直播抓取启动失败：{exc}")
                return
            self.login_state.emit("logged_in")
            self._emit_live_capture_state("ready", "直播抓取已启用")
            self.connection_changed.emit("disconnected")
            self.browser_ready.emit()
            return

        self._live_capture_enabled = False
        if not self._context and not self._pw and not self._capture:
            self._emit_live_capture_state("disabled", "抓取未启用")
            self.connection_changed.emit("disconnected")
            return
        self._emit_live_capture_state("stopping", "正在关闭直播抓取...")
        await self._async_close_live_capture_resources()
        self._emit_live_capture_state("disabled", "抓取未启用")
        self.connection_changed.emit("disconnected")

    async def _async_start_wechat(self):
        from wechat_capture import WeChatCapture
        if self._live_capture_state == "starting":
            return
        if self._wechat is not None:
            self._live_capture_enabled = True
            self._emit_live_capture_state("ready", "视频号助手监听中")
            self.connection_changed.emit("disconnected")
            return
        self._live_capture_enabled = True
        self._emit_live_capture_state("starting", "正在启动视频号助手...")
        try:
            self._wechat = WeChatCapture(self._on_message)
            await self._wechat.start()
        except Exception as exc:
            self._live_capture_enabled = False
            try:
                if self._wechat:
                    await self._wechat.stop()
            except Exception:
                pass
            self._wechat = None
            self._emit_live_capture_state("error", f"视频号助手启动失败：{exc}")
            self.connection_changed.emit("disconnected")
            self.error_occurred.emit(f"视频号助手启动失败：{exc}")
            return
        self._emit_live_capture_state("ready", "视频号助手监听中")
        self.connection_changed.emit("disconnected")

    async def _async_stop_wechat(self):
        self._live_capture_enabled = False
        if self._wechat is None:
            self._emit_live_capture_state("disabled", "抓取未启用")
            self.connection_changed.emit("disconnected")
            return
        self._emit_live_capture_state("stopping", "正在关闭视频号助手...")
        try:
            await self._wechat.stop()
        except Exception:
            pass
        self._wechat = None
        self._emit_live_capture_state("disabled", "抓取未启用")
        self.connection_changed.emit("disconnected")

    async def _async_cleanup(self):
        from audio_output import stop_all_audio
        stop_all_audio()
        if self._digital_human_pipeline:
            try:
                await self._digital_human_pipeline.stop()
            except Exception:
                pass
        if self._digital_human_task and not self._digital_human_task.done():
            self._digital_human_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._digital_human_task
            self._digital_human_task = None
        if self._obs_controller:
            try:
                await self._obs_controller.close()
            except Exception:
                pass
        if self._tts_worker:
            try:
                await self._tts_worker.stop()
            except Exception:
                pass
        if self._wechat:
            try:
                await self._wechat.stop()
            except Exception:
                pass
            self._wechat = None
        await self._async_close_live_capture_resources()
        # Cancel all remaining tasks on this loop
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._ws_task, timeout=1.5)
            self._ws_task = None
        if self._queue:
            while not self._queue.empty():
                try: self._queue.get_nowait()
                except: break

    @pyqtSlot(str, str)
    def connect_room(self, url: str, source: str = "douyin"):
        if source == "wechat":
            return
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_connect_room(url), self._loop)

    def set_obs_action_settings(self, settings_data: dict | None):
        self._obs_action_settings = dict(settings_data or {})
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_set_obs_action_settings(self._obs_action_settings),
                self._loop,
            )

    def set_voice_settings(self, settings_data: dict | None):
        self._voice_settings = dict(settings_data or {})
        # Update voice_manager synchronously so pipeline always has latest settings
        settings = VoiceSettings.from_dict(self._voice_settings)
        self._voice_manager.set_settings(settings)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_set_voice_settings(self._voice_settings),
                self._loop,
            )

    def run_voice_action(self, action: dict):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_run_voice_action(dict(action or {})), self._loop)

    def check_obs_runtime(self, settings_data: dict | None = None):
        payload = dict(settings_data or self._obs_action_settings or {})
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_check_obs_runtime(payload),
                self._loop,
            )
            return
        self.obs_runtime_changed.emit(
            {
                "state": "disconnected",
                "connected": False,
                "short_text": "未就绪",
                "message": "后台线程尚未启动，暂时无法检测 OBS",
            }
        )

    def start_digital_human(self, config_data: dict):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_start_digital_human(dict(config_data or {})), self._loop
            )

    def stop_digital_human(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_stop_digital_human(), self._loop)

    async def _async_start_digital_human(self, config_data: dict):
        from digital_human_pipeline import DigitalHumanPipeline, PipelineConfig

        # Stop previous pipeline (kills background ffmpeg process)
        if self._digital_human_pipeline:
            await self._digital_human_pipeline.stop()
            self._digital_human_pipeline = None

        if self._digital_human_task and not self._digital_human_task.done():
            self._digital_human_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._digital_human_task
        self._digital_human_task = None

        state_labels = {
            "SYNTHESIZING": "TTS 合成中...",
            "LIVETALKING_PREPARING": "准备 LiveTalking...",
            "LIVETALKING_STARTING": "启动 LiveTalking...",
            "STARTING_SERVER": "启动 RTMP 服务...",
            "CONFIGURING_OBS": "配置 OBS...",
            "PUSHING": "正在推流...",
            "STREAMING": "推流中",
            "ERROR": "出错",
            "CANCELLED": "已取消",
        }

        def log_and_notify(msg: str):
            logger.info("DigitalHuman: {}", msg)
            if msg.startswith("状态: "):
                state_name = msg.split("状态: ", 1)[1].strip()
                label = state_labels.get(state_name, state_name)
                self.digital_human_state_changed.emit(
                    {"ok": None, "message": label, "state": state_name.lower()}
                )

        self._digital_human_pipeline = DigitalHumanPipeline(
            voice_manager=self._voice_manager,
            log_callback=log_and_notify,
        )
        config = PipelineConfig(**{
            k: v for k, v in config_data.items() if k in PipelineConfig.__dataclass_fields__
        })
        self._digital_human_task = self._loop.create_task(
            self._digital_human_pipeline.run(config)
        )
        result = await self._digital_human_task
        self.digital_human_state_changed.emit(result)

    async def _async_stop_digital_human(self):
        if self._digital_human_pipeline:
            await self._digital_human_pipeline.stop()
        if self._digital_human_task and not self._digital_human_task.done():
            self._digital_human_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._digital_human_task
        self._digital_human_task = None
        self.digital_human_state_changed.emit({"ok": True, "message": "已停止", "state": "idle"})

    async def _async_set_obs_action_settings(self, settings_data: dict | None):
        if self._obs_controller is None:
            self._obs_controller = ObsActionController(
                log_callback=lambda message: logger.info("OBS: {}", message)
            )
        await self._obs_controller.configure(settings_data or {})
        await self._async_check_obs_runtime(settings_data or {})

    async def _async_check_obs_runtime(self, settings_data: dict | None):
        if self._obs_controller is None:
            self._obs_controller = ObsActionController(
                log_callback=lambda message: logger.info("OBS: {}", message)
            )
        try:
            result = await self._obs_controller.probe(settings_data or {})
        except Exception as exc:
            result = {
                "state": "disconnected",
                "connected": False,
                "short_text": "未连接",
                "message": f"OBS 检测失败：{exc}",
            }
        logger.info("OBS probe: {}", result.get("message", ""))
        self.obs_runtime_changed.emit(result)

    async def _auto_discover_obs(self):
        from obs_actions import ObsWebSocketClient, ObsActionSettings

        settings = ObsActionSettings.from_dict(self._obs_action_settings)

        self.obs_runtime_changed.emit({
            "state": "discovering",
            "connected": False,
            "short_text": "检测中",
            "message": "正在检测本机 OBS WebSocket...",
        })

        found_host = None
        for host in ("127.0.0.1", settings.host):
            if host == found_host:
                continue
            client = ObsWebSocketClient()
            try:
                await client.connect(host, settings.port, settings.password)
                await client.close()
                found_host = host
                break
            except Exception:
                pass

        if found_host:
            logger.info("OBS auto-detected at {}:{}", found_host, settings.port)
            self._obs_action_settings["host"] = found_host
            await self._async_set_obs_action_settings(self._obs_action_settings)
            self.obs_host_discovered.emit(found_host)
        else:
            logger.info("OBS not detected on local machine")
            self.obs_runtime_changed.emit({
                "state": "disconnected",
                "connected": False,
                "short_text": "未发现",
                "message": "本机未检测到 OBS，请确认 OBS 已启动且 WebSocket 已开启",
            })

    async def _async_set_voice_settings(self, settings_data: dict | None):
        settings = VoiceSettings.from_dict(settings_data or {})
        self._voice_settings = settings.to_dict()
        self._voice_manager.set_settings(settings)
        if self._tts_worker is None:
            self._tts_worker = TTSWorker(
                on_speech=self._on_tts_event,
                synthesizer=lambda reply: self._voice_manager.synthesize_for_tts_worker(reply, "copilot"),
                timeout_ms=30000,
                queue_size=50,
            )
            await self._tts_worker.start()
        self._emit_voice_runtime()

    def _emit_voice_runtime(self):
        s = self._voice_manager.settings
        def _voice_status(voice_id):
            v = s.find_voice(voice_id)
            return v.clone_status if v else ""
        self.voice_runtime_changed.emit({
            "state": "ready",
            "provider": s.provider,
            "model_id": s.model_id,
            "anchor_status": _voice_status(s.anchor.voice_id),
            "copilot_status": _voice_status(s.copilot.voice_id),
            "copilot_auto_broadcast": s.copilot_auto_broadcast,
        })

    async def _async_run_voice_action(self, action: dict):
        action_type = str(action.get("type", "")).strip()
        role = str(action.get("role", "")).strip()
        if action_type == "validate_provider":
            result = await self._voice_manager.validate_provider()
            self.voice_action_finished.emit({"type": action_type, "result": result})
            return
        if action_type == "clone":
            action_settings = action.get("settings")
            if isinstance(action_settings, dict):
                settings = VoiceSettings.from_dict(action_settings)
                self._voice_settings = settings.to_dict()
                self._voice_manager.set_settings(settings)
            voice_id = str(action.get("voice_id", "")).strip()
            target_id = voice_id
            if not target_id and role in {"anchor", "copilot"}:
                target_id = self._voice_manager._role(role).voice_id
            if not target_id:
                result = VoiceActionResult(False, "请先选择要克隆的声音")
                self.voice_action_finished.emit({"type": action_type, "role": role, "voice_id": "", "result": result})
                return
            result = await self._voice_manager.create_clone(target_id)
            self._voice_settings = self._voice_manager.settings.to_dict()
            self.voice_action_finished.emit({
                "type": action_type,
                "role": role,
                "voice_id": target_id,
                "result": result,
                "settings": self._voice_settings,
            })
            self._emit_voice_runtime()
            return
        if action_type == "preview":
            text = str(action.get("text", "")).strip()
            if role not in {"anchor", "copilot"}:
                result = VoiceActionResult(False, "试听角色无效")
                self.voice_action_finished.emit({"type": action_type, "role": role, "result": result})
                return
            if not text:
                result = VoiceActionResult(False, "试听文本为空")
                self.voice_action_finished.emit({"type": action_type, "role": role, "result": result})
                return
            result = await self._voice_manager.synthesize_and_play(text, role)
            self.voice_action_finished.emit({"type": action_type, "role": role, "result": result})
            return

    async def _async_connect_room(self, url: str):
        from decoder import DanmakuDecoder
        from capture import RoomCapture
        if not self._live_capture_enabled or not self._context:
            message = "请先启用直播抓取"
            if self._live_capture_state == "starting":
                message = "直播抓取正在启动，请稍后再连接"
            self.error_occurred.emit(message)
            self.connection_changed.emit("disconnected")
            return
        self.connection_changed.emit("connecting")
        m = re.search(r"live\.douyin\.com/(\d+)", url)
        self._current_room_id = m.group(1) if m else ""
        self._dedup_cache.clear()
        self._truth_stream.set_room_id(self._current_room_id)
        if self._capture:
            try: await self._capture.stop()
            except: pass
            self._capture = None
        try:
            self._decoder = DanmakuDecoder()
            self._capture = RoomCapture(self._context, url, self._decoder)
            await self._capture.start(self._on_message)
            if self._capture._page:
                page_url = self._capture._page.url
                if any(d in page_url for d in ("passport.douyin.com", "sso.douyin.com", "captcha")):
                    self.login_state.emit("anomaly")
                    self.connection_changed.emit("anomaly")
                    self.error_occurred.emit("Login anomaly detected")
                    return
            self.connection_changed.emit("connected")
            self._last_message_time = time.time()
            self._loop.create_task(self._monitor_capture())
        except Exception as e:
            self.error_occurred.emit(f"Connect failed: {e}")
            self.connection_changed.emit("disconnected")

    async def _monitor_capture(self):
        while self._running and self._capture:
            await asyncio.sleep(5)
            if not self._capture or not self._running: break
            if not self._capture.running:
                self.connection_changed.emit("disconnected")
                break
            if self._decoder and self._decoder.fail_count > 0:
                self.parse_failure_count.emit(self._decoder.fail_count)
            # Feed decode failures into OpsMetrics loss counter
            if self._decoder:
                delta = self._decoder.fail_count - self._last_fail_count
                if delta > 0:
                    self._ops_metrics.inc_loss("decode_fail", delta)
                self._last_fail_count = self._decoder.fail_count
            # Emit metrics snapshot (D-01)
            if self._ops_metrics:
                snap = self._ops_metrics.snapshot()
                self.metrics_updated.emit(snap)
            if self._last_message_time and time.time() - self._last_message_time > 60:
                self.connection_changed.emit("stalled")

    @staticmethod
    def _build_message_dedup_key(msg: dict) -> tuple | None:
        msg_type = str(msg.get("type", "")).strip()
        if not msg_type:
            return None

        user_id = str(msg.get("user_id", "")).strip()
        nickname = _normalize_display_nickname(msg.get("nickname", ""))
        identity = user_id or nickname

        if msg_type == "chat":
            payload = " ".join(str(msg.get("content", "")).strip().split())
        elif msg_type == "gift":
            payload = (
                str(msg.get("gift_name", "")).strip(),
                int(msg.get("gift_count", 0) or 0),
            )
        else:
            payload = ""

        if msg_type == "chat" and not payload:
            return None
        return (msg_type, identity, payload)

    def _is_duplicate_message(self, msg: dict) -> bool:
        key = self._build_message_dedup_key(msg)
        if key is None:
            return False

        now = time.time()
        last_seen = self._dedup_cache.get(key)
        if last_seen is not None and now - last_seen < self.DEDUP_WINDOW_SEC:
            return True

        self._dedup_cache[key] = now
        if len(self._dedup_cache) > self.DEDUP_CACHE_MAX:
            self._dedup_cache = {
                cached_key: cached_time
                for cached_key, cached_time in self._dedup_cache.items()
                if now - cached_time < self.DEDUP_WINDOW_SEC
            }
        return False

    async def _on_message(self, msg: dict):
        msg["source"] = "wechat" if self._wechat is not None else "douyin"
        self._last_message_time = time.time()
        if self._is_duplicate_message(msg):
            return

        # Pipeline observability (D-01, D-02)
        self._ops_metrics.record_input()
        events = self._truth_stream.ingest(msg)
        for evt in events:
            self._replay_logger.log_event(evt)
        self._replay_logger.log_event(msg)

        self.danmaku_received.emit(msg)
        if self._queue:
            try: self._queue.put_nowait(msg)
            except: pass
        if msg.get("type") == "chat":
            if self._obs_controller:
                asyncio.create_task(
                    self._obs_controller.handle_chat_message(str(msg.get("content", "")))
                )
            elif not getattr(self, "_logged_obs_none_once", False):
                logger.warning("OBS: dispatch skipped — _obs_controller is None (configure 从未跑过)")
                self._logged_obs_none_once = True

        # Keyword auto-reply：只对视频号弹幕生效，命中后短路 AI Reply
        if (
            msg.get("type") == "chat"
            and msg.get("source") == "wechat"
            and self._keyword_auto_reply_enabled
            and self._keyword_engine is not None
        ):
            content = str(msg.get("content", ""))
            result = self._keyword_engine.match(content)
            if result.matched and result.rule is not None:
                rule = result.rule
                now = time.monotonic()
                last = self._keyword_last_hit.get(rule.keyword, 0.0)
                if (now - last) >= self._keyword_global_cooldown_sec:
                    while self._keyword_hit_log and (now - self._keyword_hit_log[0]) > 60.0:
                        self._keyword_hit_log.popleft()
                    if len(self._keyword_hit_log) < self._keyword_rate_limit_per_min:
                        self._keyword_last_hit[rule.keyword] = now
                        self._keyword_hit_log.append(now)
                        count = self._keyword_hit_count.get(rule.keyword, 0) + 1
                        self._keyword_hit_count[rule.keyword] = count
                        nick = str(msg.get("nickname", ""))
                        # 异步注入到视频号助手评论框
                        asyncio.create_task(
                            self._dispatch_keyword_reply(rule.keyword, rule.reply, nick, count)
                        )
                        return  # 短路：不再走 AI engine
                    else:
                        logger.debug(f"Keyword: 速率限制压制 {rule.keyword!r}")
                else:
                    logger.debug(f"Keyword: 冷却压制 {rule.keyword!r} ({now - last:.1f}s < {self._keyword_global_cooldown_sec}s)")

        if self._ai_engine:
            try:
                _ai_start = time.time()
                result = await self._ai_engine.process_message(msg)
                if result:
                    # Observe latency only when a reply is actually produced — empty
                    # process_message returns (cooldown/filter) shouldn't pollute p95.
                    self._ops_metrics.observe_latency_ms((time.time() - _ai_start) * 1000.0)
                    self.ai_reply_ready.emit(result.target_user, result.target_msg, result.reply)
                    self._ops_metrics.record_reply()
                    if (
                        self._tts_worker
                        and VoiceSettings.from_dict(self._voice_settings).copilot_auto_broadcast
                    ):
                        self._tts_worker.enqueue(
                            {
                                "reply_id": f"copilot-{int(time.time() * 1000)}",
                                "text": result.reply,
                                "priority": 5,
                                "room_id": self._current_room_id,
                                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            },
                            interrupt=False,
                        )
            except Exception as e:
                logger.warning("AI reply error: {}", e)

    def set_ai_config(self, config):
        from ai_reply import AIReplyEngine
        from memory_manager import MemoryManager

        # Preserve state from existing engine (D-05)
        old_history = []
        old_cooldowns = {}
        if self._ai_engine:
            old_history = list(self._ai_engine._conversation_history)
            old_cooldowns = dict(self._ai_engine._user_cooldowns)
            # Cancel any pending timer on old engine
            if self._ai_engine._pending_timer and not self._ai_engine._pending_timer.done():
                self._ai_engine._pending_timer.cancel()

        self._ai_engine = AIReplyEngine(config)
        # Restore preserved state
        self._ai_engine._conversation_history = old_history
        self._ai_engine._user_cooldowns = old_cooldowns
        # Discard pending_message -- room may have changed (D-05)
        self._ai_engine.set_memory(MemoryManager(room_id=self._current_room_id))
        self._ai_engine._on_reply = self._emit_pending_reply
        self._ai_engine._on_status = lambda payload: self.ai_runtime_changed.emit(payload)
        self._ai_engine.publish_status()

    async def _emit_pending_reply(self, result):
        self.ai_reply_ready.emit(result.target_user, result.target_msg, result.reply)
        self._ops_metrics.record_reply()

    async def _dispatch_keyword_reply(self, keyword: str, reply: str, nickname: str, count: int):
        injected = False
        if self._wechat is not None:
            try:
                injected = await self._wechat.send_comment(reply)
            except Exception as e:
                logger.warning(f"Keyword: 注入异常 {e}")
                injected = False
        self.keyword_reply_fired.emit(keyword, reply, nickname, count, injected)

    def set_keyword_reply_config(
        self,
        enabled: bool,
        templates: dict,
        active_template: str = "",
        global_cooldown_sec: int = 30,
        rate_limit_per_min: int = 20,
    ):
        from keyword_engine import KeywordEngine

        self._keyword_auto_reply_enabled = bool(enabled)
        self._keyword_global_cooldown_sec = max(0, int(global_cooldown_sec))
        self._keyword_rate_limit_per_min = max(1, int(rate_limit_per_min))
        if self._keyword_engine is None:
            self._keyword_engine = KeywordEngine()
        self._keyword_engine.load_templates({"keyword_templates": templates or {}})
        names = self._keyword_engine.get_template_names()
        if active_template and active_template in names:
            self._keyword_engine.set_active(active_template)
        elif names:
            self._keyword_engine.set_active(names[0])

    async def _on_tts_event(self, event: dict):
        tts_detail = {
            "state": "tts",
            "provider": VoiceSettings.from_dict(self._voice_settings).provider,
            "model_id": VoiceSettings.from_dict(self._voice_settings).model_id,
            "tts_status": event.get("status", ""),
            "tts_text": event.get("text", ""),
            "tts_detail": event.get("detail", ""),
        }
        # Add TTS queue status (D-07)
        if self._tts_worker:
            try:
                tts_detail["tts_queue_depth"] = self._tts_worker._queue.qsize()
                tts_detail["tts_playing"] = self._tts_worker._current is not None
            except Exception:
                pass
        self.voice_runtime_changed.emit(tts_detail)

    @pyqtSlot()
    def clear_and_restart(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_clear_restart(), self._loop)

    async def _async_clear_restart(self):
        from fetcher import clear_session
        was_enabled = self._live_capture_enabled or self._context is not None or self._pw is not None
        self._live_capture_enabled = False
        self._emit_live_capture_state("stopping", "正在重置登录状态...")
        await self._async_close_live_capture_resources()
        await asyncio.sleep(0.5)
        clear_session()
        self.connection_changed.emit("disconnected")
        if was_enabled:
            try:
                await self._async_set_live_capture_enabled(True)
            except Exception as e:
                self.error_occurred.emit(f"重置登录状态失败: {e}")
        else:
            self._emit_live_capture_state("disabled", "抓取未启用")

    @pyqtSlot()
    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)

    async def _async_stop(self):
        await self._async_cleanup()
        self._loop.stop()


# ---------------------------------------------------------------------------
# QLogHandler — Routes loguru output to Qt signal
# ---------------------------------------------------------------------------

class QLogHandler(QObject):
    log_message = pyqtSignal(str)

    def write(self, message):
        record = message.record
        ts = record["time"].strftime("%H:%M:%S")
        level = record["level"].name
        text = record["message"]
        self.log_message.emit(f"{ts} | {level:8} | {text}")


# ---------------------------------------------------------------------------
# HomePage — commercial dashboard: checklist + quick-start + metrics + activity
# ---------------------------------------------------------------------------

# Activity feed type icons — kept module-level so _build_activity_feed and
# append_activity reference the same mapping.
_ACTIVITY_ICONS = {
    "chat":   "💬",
    "gift":   "🎁",
    "follow": "👤",
    "like":   "❤",
    "enter":  "🚪",
    "stats":  "",   # stats updates are silent on activity feed
    "ai":     "🤖",
}


class _MetricCard(QWidget):
    """Dashboard metric card: icon + big number + label, on a rounded panel."""

    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._label = label
        self._value = "—"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self._icon_label = QLabel(icon, self)
        self._icon_label.setAlignment(Qt.AlignCenter)
        self._value_label = QLabel(self._value, self)
        self._value_label.setAlignment(Qt.AlignCenter)
        self._caption_label = QLabel(label, self)
        self._caption_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._icon_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._caption_label)
        self.setMinimumHeight(96)
        self._apply_style()

    def set_value(self, text: str):
        self._value = text
        self._value_label.setText(text)

    def _apply_style(self):
        self.setStyleSheet(f"""
            _MetricCard {{
                background: {CLR_BG_CARD};
                border: 1px solid {CLR_BORDER};
                border-radius: 10px;
            }}
        """)
        self._icon_label.setStyleSheet(
            f"font-size: 22px; color: {CLR_TEXT_PRI}; background: transparent; border: none;"
        )
        self._value_label.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {CLR_TEXT_PRI}; "
            "background: transparent; border: none;"
        )
        self._caption_label.setStyleSheet(
            f"font-size: 13px; color: {CLR_TEXT_SEC}; background: transparent; border: none;"
        )


class _ChecklistItem(QWidget):
    """Pre-stream checklist row: status dot + title + status text + jump link."""

    _STATE_COLORS = {
        # Map: (dot color, status label color)
        "green":  None,
        "yellow": None,
        "red":    None,
        "gray":   None,
    }

    jump_requested = pyqtSignal(int)

    def __init__(self, title: str, jump_page: int | None = None, parent=None):
        super().__init__(parent)
        self._title = title
        self._jump_page = jump_page
        self._state = "gray"
        self._status_text = "未检测"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)
        self._dot_label = QLabel("●", self)
        self._dot_label.setFixedWidth(14)
        self._title_label = QLabel(title, self)
        self._title_label.setMinimumWidth(96)
        self._status_label = QLabel(self._status_text, self)
        layout.addWidget(self._dot_label)
        layout.addWidget(self._title_label)
        layout.addWidget(self._status_label, stretch=1)
        self._jump_btn = QPushButton("去配置 →", self)
        self._jump_btn.setCursor(Qt.PointingHandCursor)
        self._jump_btn.setVisible(False)
        if jump_page is not None:
            self._jump_btn.clicked.connect(lambda: self.jump_requested.emit(jump_page))
        layout.addWidget(self._jump_btn)
        self._apply_style()

    def set_state(self, color: str, text: str):
        self._state = color
        self._status_text = text
        self._status_label.setText(text)
        # Show jump link only when something is off and a jump target exists.
        if self._jump_page is not None and color in ("red", "yellow", "gray"):
            self._jump_btn.setVisible(True)
        else:
            self._jump_btn.setVisible(False)
        self._apply_style()

    def state(self) -> str:
        return self._state

    def _apply_style(self):
        dot_colors = {
            "green":  CLR_GREEN,
            "yellow": CLR_YELLOW,
            "red":    CLR_RED,
            "gray":   CLR_TEXT_TERT,
        }
        c = dot_colors.get(self._state, CLR_TEXT_TERT)
        self._dot_label.setStyleSheet(
            f"color: {c}; font-size: 16px; background: transparent; border: none;"
        )
        self._title_label.setStyleSheet(
            f"color: {CLR_TEXT_PRI}; font-size: 14px; background: transparent; border: none;"
        )
        self._status_label.setStyleSheet(
            f"color: {CLR_TEXT_SEC}; font-size: 14px; background: transparent; border: none;"
        )
        self._jump_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CLR_ACCENT};
                border: none;
                font-size: 13px;
                padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {CLR_ACCENT_LIGHT}; }}
        """)



class _VideoThumbCard(QFrame):
    clicked = pyqtSignal(int)
    remove_requested = pyqtSignal(int)

    # Border width is constant (2px) across selected/unselected — only the
    # color changes. Thumb label is sized to fit *inside* the 2px border +
    # 2px content margin so the border always renders cleanly without being
    # clipped or overlapped, which was the "歪" effect.
    _BORDER_W = 2
    _CONTENT_MARGIN = 2
    _INNER_INSET = _BORDER_W + _CONTENT_MARGIN  # 4px on each side

    def __init__(
        self,
        index: int,
        video_path: str,
        pixmap: QPixmap | None,
        parent=None,
        status_text: str = "",
    ):
        super().__init__(parent)
        self._index = index
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        self._pixmap = pixmap
        self._apply_border_style(CLR_BORDER)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._CONTENT_MARGIN, self._CONTENT_MARGIN,
                                  self._CONTENT_MARGIN, self._CONTENT_MARGIN)
        layout.setSpacing(0)

        inner_w = _CARD_W - 2 * self._INNER_INSET
        status_h = 24
        inner_h = _CARD_H - 2 * self._INNER_INSET - status_h
        self._thumb_label = QLabel(self)
        self._thumb_label.setFixedSize(inner_w, inner_h)
        self._thumb_label.setAlignment(Qt.AlignCenter)
        self._thumb_label.setStyleSheet(
            f"border-radius: 9px; background-color: {CLR_INPUT_BG};"
        )
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                inner_w, inner_h,
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
            )
            self._thumb_label.setPixmap(scaled)
        else:
            self._thumb_label.setText(os.path.basename(video_path)[:12])
        layout.addWidget(self._thumb_label)
        self._badge_label = QLabel(self)
        self._badge_label.setAlignment(Qt.AlignCenter)
        self._badge_label.setStyleSheet(
            "QLabel {"
            "background-color: transparent;"
            f"color: {CLR_TEXT_SEC};"
            "border: none;"
            "font-size: 11px;"
            "padding: 2px 0;"
            "}"
        )
        self._badge_label.setFixedSize(inner_w, status_h)
        self.set_status(status_text)
        layout.addWidget(self._badge_label)

    def _apply_border_style(self, color: str):
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {CLR_INPUT_BG};
                border: {self._BORDER_W}px solid {color};
                border-radius: 12px;
            }}
        """)

    @property
    def index(self):
        return self._index

    def set_status(self, text: str):
        text = (text or "").strip()
        self._badge_label.setText(text)

    def set_selected(self, selected: bool):
        self._selected = selected
        # Only the color changes — width and layout stay identical, so the
        # border is always centred and never partially hidden by the thumb.
        self._apply_border_style(CLR_ACCENT if selected else CLR_BORDER)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._index)
        elif event.button() == Qt.RightButton:
            self.remove_requested.emit(self._index)
        super().mousePressEvent(event)


class _AddCard(QFrame):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {CLR_INPUT_BG};
                border: 1px dashed {CLR_BORDER};
                border-radius: 12px;
            }}
            QFrame:hover {{
                border-color: {CLR_ACCENT};
                background-color: {CLR_BG_ELEVATED};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        plus_label = QLabel("+", self)
        plus_label.setAlignment(Qt.AlignCenter)
        plus_label.setStyleSheet(f"color: {CLR_TEXT_SEC}; font-size: 32pt; border: none; background: transparent;")
        layout.addWidget(plus_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)



class _BottomStatusBar(QWidget):
    """Persistent 32px-tall status bar pinned to the bottom of AiszrApp.

    Renders six clickable status dots (login / capture / ai / obs / voice / dh)
    plus a scene-mode placeholder and a connection uptime clock. Clicking a dot
    emits ``navigate_requested`` with the target page index so AiszrApp can
    route via its own ``_set_page`` slot — no SiliconUI library changes needed.
    """

    navigate_requested = pyqtSignal(int)

    # Maps dot key -> target page index in AiszrApp's sidebar (see finish_init).
    # After merging DigitalHumanPage into VoiceConfigPage:
    #   0=home, 1=live, 2=ai, 3=voice (streaming console), 4=obs, 5=settings
    # The "dh" dot now jumps to the voice page where streaming lives.
    _DOT_PAGES = {
        "login": 1, "capture": 1, "ai": 2, "voice": 3, "obs": 4, "dh": 3,
    }
    _DOT_TITLES = {
        "login": "登录", "capture": "抓取", "ai": "AI",
        "obs": "OBS", "voice": "语音", "dh": "推流",
    }
    _DOT_ORDER = ("login", "capture", "ai", "obs", "voice", "dh")
    # Streaming-focused subset of dots actually shown in the bar. Other slots
    # (login/capture/ai/obs) retain their update_*_state slots for
    # signal-wiring compatibility but are kept off-screen since OBS 联动
    # is a 抖音-only feature, unrelated to the 视频号 streaming pipeline.
    _DOT_VISIBLE = ("voice", "dh")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        # Each dot: (state_color_name, optional_short_text)
        self._states: dict[str, tuple[str, str]] = {
            k: ("gray", "") for k in self._DOT_ORDER
        }
        self._connect_start_ts: float | None = None
        self._scene = "未设置"
        self._dot_labels: dict[str, QLabel] = {}
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_uptime)
        self._timer.start()
        self._apply_theme_styles()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        # Render only the streaming-relevant dot subset. Hidden dots still get
        # labels created (kept off-screen) so update_*_state slots can write to
        # them without guarding for missing keys.
        visible_keys = list(self._DOT_VISIBLE)
        for key in self._DOT_ORDER:
            lbl = QLabel(self)
            lbl.setTextFormat(Qt.RichText)
            lbl.setCursor(Qt.PointingHandCursor)
            lbl.mousePressEvent = (lambda evt, k=key: self._on_dot_click(k))
            self._dot_labels[key] = lbl
            if key not in visible_keys:
                lbl.setVisible(False)
        for i, key in enumerate(visible_keys):
            layout.addWidget(self._dot_labels[key])
            if i < len(visible_keys) - 1:
                sep = QLabel("│", self)
                sep.setObjectName("sep")
                layout.addWidget(sep)
        # Group separator before scene label
        scene_sep = QLabel("│", self)
        scene_sep.setObjectName("sep")
        layout.addWidget(scene_sep)
        self._scene_label = QLabel(f"场景: {self._scene}", self)
        layout.addWidget(self._scene_label)
        uptime_sep = QLabel("│", self)
        uptime_sep.setObjectName("sep")
        layout.addWidget(uptime_sep)
        self._uptime_label = QLabel("⏱ --:--:--", self)
        layout.addWidget(self._uptime_label)
        layout.addStretch()
        self._refresh_dots()

    def _on_dot_click(self, key: str):
        target = self._DOT_PAGES.get(key)
        if target is not None:
            self.navigate_requested.emit(target)

    def _refresh_dots(self):
        color_map = {
            "green":  CLR_GREEN,
            "yellow": CLR_YELLOW,
            "red":    CLR_RED,
            "gray":   CLR_TEXT_TERT,
        }
        for key in self._DOT_ORDER:
            state, _ = self._states[key]
            c = color_map.get(state, CLR_TEXT_TERT)
            title = self._DOT_TITLES[key]
            html = (
                f'<span style="color:{c}">●</span> '
                f'<span style="color:{CLR_TEXT_SEC};font-size: 13px">{title}</span>'
            )
            self._dot_labels[key].setText(html)

    # ------------------------------------------------------------------
    # Worker signal slots — signatures match HomePage equivalents so the
    # same worker signals can fan out to both the HomePage checklist and
    # this global bar.
    # ------------------------------------------------------------------

    def update_login_state(self, state: str):
        mapping = {"logged_in": "green", "checking": "yellow", "scanning": "yellow"}
        self._states["login"] = (mapping.get(state, "red"), "")
        self._refresh_dots()

    def update_connection_state(self, state: str):
        mapping = {"connected": "green", "connecting": "yellow", "stalled": "red"}
        self._states["capture"] = (mapping.get(state, "gray"), "")
        if state == "connected" and self._connect_start_ts is None:
            self._connect_start_ts = time.time()
        elif state != "connected":
            self._connect_start_ts = None
            self._uptime_label.setText("⏱ --:--:--")
        self._refresh_dots()

    def update_capture_state(self, state: str, msg: str):
        # Capture runtime supplements connection state. Only downgrade to red
        # on a hard error — otherwise leave whatever update_connection_state
        # last wrote (so a green "connected" dot doesn't flicker on a benign
        # "ready"/"starting" capture transition).
        if state == "error":
            self._states["capture"] = ("red", msg or "")
            self._refresh_dots()

    def update_ai_state(self, payload):
        if not isinstance(payload, dict):
            return
        state = payload.get("state", "disabled")
        color = "green" if state in ("idle", "cooldown", "queued", "generating") else "gray"
        self._states["ai"] = (color, "")
        self._refresh_dots()

    def update_obs_state(self, payload):
        if not isinstance(payload, dict):
            return
        state = payload.get("state", "disabled")
        mapping = {"connected": "green", "warning": "yellow", "discovering": "yellow", "error": "red"}
        self._states["obs"] = (mapping.get(state, "gray"), "")
        self._refresh_dots()

    def update_voice_state(self, payload):
        if not isinstance(payload, dict):
            return
        state = payload.get("state", "")
        anchor = payload.get("anchor_status", "")
        if state == "ready" and anchor == "ready":
            self._states["voice"] = ("green", "")
        elif state == "ready":
            self._states["voice"] = ("yellow", "")
        else:
            self._states["voice"] = ("gray", "")
        self._refresh_dots()

    def update_dh_state(self, payload):
        if not isinstance(payload, dict):
            return
        state = payload.get("state", "")
        mapping = {
            "streaming": "green",
            "synthesizing": "yellow",
            "pushing": "yellow",
            "configuring_obs": "yellow",
            "error": "red",
            "cancelled": "gray",
        }
        self._states["dh"] = (mapping.get(state, "gray"), "")
        self._refresh_dots()

    def set_scene(self, name: str):
        self._scene = name or "未设置"
        self._scene_label.setText(f"场景: {self._scene}")

    def _tick_uptime(self):
        if self._connect_start_ts is None:
            return
        secs = int(time.time() - self._connect_start_ts)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        self._uptime_label.setText(f"⏱ {h:02d}:{m:02d}:{s:02d}")

    def _apply_theme_styles(self):
        self.setStyleSheet(f"""
            _BottomStatusBar {{
                background: {CLR_BG_CARD};
                border-top: 1px solid {CLR_BORDER};
            }}
            QLabel {{
                color: {CLR_TEXT_SEC};
                background: transparent;
                border: none;
            }}
            QLabel#sep {{
                color: {CLR_BORDER};
            }}
        """)
        self._refresh_dots()


# ---------------------------------------------------------------------------
# AiszrApp — Main application window
# ---------------------------------------------------------------------------

class AiszrApp(SiliconApplication):
    _BOTTOM_BAR_HEIGHT = 32

    def __init__(self, worker: CaptureWorker, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._worker = worker
        self.resize(1400, 900)
        self.setMinimumSize(1280, 900)
        self.layerMain().setTitle("Aiszr")
        self._apply_app_icon()
        self.setWindowTitle("Aiszr - AI 直播助手")

        # Persistent global bottom status bar. Created as a direct child of the
        # main window (alongside the modal/drawer/overlay layers) so it survives
        # all page switches. Positioning happens in resizeEvent below.
        self._bottom_bar = _BottomStatusBar(self)
        self._bottom_bar.show()

    def resizeEvent(self, event):
        # SiliconApplication.resizeEvent sizes group_main_interface and
        # layer_main to the full window. We shrink them by _BOTTOM_BAR_HEIGHT
        # when the bottom bar is visible so it doesn't overlap content.
        super().resizeEvent(event)
        self._relayout_bottom_bar()

    def _relayout_bottom_bar(self):
        """Position bottom bar and adjust main layers based on bar visibility."""
        size = self.size()
        w, h = size.width(), size.height()
        bar_visible = hasattr(self, "_bottom_bar") and self._bottom_bar.isVisible()
        bar_h = self._BOTTOM_BAR_HEIGHT if bar_visible else 0
        if hasattr(self, "group_main_interface"):
            self.group_main_interface.resize(w, h - bar_h)
        if hasattr(self, "layer_main"):
            self.layer_main.resize(w, h - bar_h)
        if hasattr(self, "layer_child_page"):
            self.layer_child_page.resize(w, h - bar_h)
        if bar_visible:
            self._bottom_bar.setGeometry(0, h - bar_h, w, bar_h)
            self._bottom_bar.raise_()

    def finish_init(self):
        """Create pages and connect signals. Called after splash is visible."""
        worker = self._worker

        # Create pages
        self._home_page = HomePage(self)
        self._home_page.navigate_to_page.connect(self._set_page)
        self._home_page.obs_status_check_requested.connect(self._on_obs_status_check_requested)
        self._home_page.obs_settings_changed.connect(self._on_obs_settings_changed)
        self._home_page.attach_worker(worker)

        self._live_page = LiveRoomPage(worker, self)
        self._live_page.connect_requested.connect(self._on_connect_room)
        self._live_page.live_capture_toggle_requested.connect(self._on_live_capture_toggle_requested)
        self._live_page.clear_session_requested.connect(self._on_clear_session)
        self._home_page.set_live_page(self._live_page)

        self._ai_config_page = AIConfigPage(self)
        self._ai_config_page.back_requested.connect(lambda: self._set_page(0))
        self._ai_config_page._app_ref = self

        self._voice_page = VoiceConfigPage(self)
        self._voice_page.back_requested.connect(lambda: self._set_page(0))
        self._voice_page.api_settings_requested.connect(self._open_voice_api_dialog)
        self._voice_page.clone_dialog_requested.connect(self._open_voice_clone_dialog)
        self._voice_page.anchor_settings_requested.connect(self._open_anchor_settings_dialog)
        self._voice_page.copilot_settings_requested.connect(self._open_copilot_settings_dialog)
        self._voice_page.voice_settings_changed.connect(self._on_voice_settings_changed)
        self._voice_page.voice_action_requested.connect(self._on_voice_action_requested)
        # Streaming console (merged from DigitalHumanPage) lives on the voice page.
        self._voice_page.digital_human_start_requested.connect(self._on_digital_human_start)
        self._voice_page.digital_human_stop_requested.connect(self._on_digital_human_stop)
        self._voice_page.streaming_config_changed.connect(self._on_streaming_config_changed)

        self._voice_api_dialog = VoiceApiDialog(self)
        self._voice_api_dialog.voice_settings_changed.connect(self._on_voice_settings_changed)
        self._voice_api_dialog.voice_action_requested.connect(self._on_voice_action_requested)

        self._voice_clone_dialog = VoiceCloneDialog(self)
        self._voice_clone_dialog.voice_settings_changed.connect(self._on_voice_settings_changed)
        self._voice_clone_dialog.voice_action_requested.connect(self._on_voice_action_requested)

        self._anchor_settings_dialog = AnchorSettingsDialog(self)
        self._anchor_settings_dialog.voice_settings_changed.connect(self._on_voice_settings_changed)

        self._copilot_settings_dialog = CopilotSettingsDialog(self)
        self._copilot_settings_dialog.voice_settings_changed.connect(self._on_voice_settings_changed)

        self._obs_page = ObsActionPage(self)
        self._obs_page.back_requested.connect(lambda: self._set_page(0))
        self._obs_page.obs_settings_changed.connect(self._on_obs_settings_changed)
        self._obs_page.obs_status_check_requested.connect(self._on_obs_status_check_requested)

        # DigitalHumanPage class is kept for fallback/rollback but NOT
        # instantiated — instantiating it parents a free-floating widget tree
        # at default geometry (0,0), which renders ghost UI in the title bar.
        # Its functionality now lives on VoiceConfigPage; signal wiring goes
        # through the voice page below.
        self._digital_human_page = None

        self._settings_page = GeneralSettingsPage(self)
        self._settings_page.back_requested.connect(lambda: self._set_page(0))

        self._keyword_reply_page = KeywordReplyPage(self)
        self._keyword_reply_page.back_requested.connect(lambda: self._set_page(0))

        # Sidebar pages
        # Indices after merge: 0=home, 1=live, 2=ai, 3=voice (streaming console),
        # 4=obs, 5=settings. DigitalHumanPage is kept instantiated above for
        # fallback/rollback but is intentionally NOT registered in the sidebar —
        # its UI now lives at the bottom of VoiceConfigPage.
        self.layerMain().addPage(self._home_page,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_window_console_filled"),
            hint="首页", side="top")
        self.layerMain().addPage(self._ai_config_page,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_brain_circuit_filled"),
            hint="AI 配置", side="top")
        self.layerMain().addPage(self._voice_page,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_mic_sparkle_filled"),
            hint="AI 语音 / 推流", side="top")
        self.layerMain().addPage(self._keyword_reply_page,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_comment_filled"),
            hint="关键词回复", side="top")
        self.layerMain().addPage(self._obs_page,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_tv_arrow_right_filled"),
            hint="OBS 联动", side="top")
        self.layerMain().addPage(self._settings_page,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_wrench_screwdriver_filled"),
            hint="设置", side="bottom")

        # Sidebar PageButton clicks bypass _set_page — they call
        # stacked_container.setCurrentIndex directly, which SiliconUI's
        # StackedContainerWithShowUpAnimation does NOT emit a signal for.
        # Wrap setCurrentIndex to hook our visibility logic into every switch.
        _stacked = self.layerMain().page_view.stacked_container
        _orig_set_idx = _stacked.setCurrentIndex
        def _wrapped_set_idx(index: int, *args, **kwargs):
            _orig_set_idx(index, *args, **kwargs)
            self._apply_bottom_bar_visibility(index)
        _stacked.setCurrentIndex = _wrapped_set_idx

        self._set_page(0)
        SiGlobal.siui.reloadAllWindowsStyleSheet()

        worker.login_state.connect(self._on_login_state)
        worker.login_state.connect(self._home_page.update_login_state)
        worker.error_occurred.connect(self._on_error)
        worker.connection_changed.connect(self._home_page.update_connection_state)
        worker.live_capture_state_changed.connect(self._home_page.update_capture_state)
        worker.ai_runtime_changed.connect(self._home_page.update_ai_state)
        worker.obs_runtime_changed.connect(self._obs_page.update_obs_runtime_status)
        worker.obs_runtime_changed.connect(self._home_page.update_obs_state)
        worker.obs_host_discovered.connect(self._obs_page.set_discovered_host)
        worker.voice_runtime_changed.connect(self._voice_page.update_voice_runtime)
        worker.voice_runtime_changed.connect(self._voice_api_dialog.update_voice_runtime)
        worker.voice_action_finished.connect(self._voice_page.handle_voice_action_result)
        worker.voice_action_finished.connect(self._voice_api_dialog.handle_voice_action_result)
        worker.voice_action_finished.connect(self._voice_clone_dialog.handle_voice_action_result)
        worker.digital_human_state_changed.connect(self._voice_page.update_streaming_status)
        worker.digital_human_state_changed.connect(self._home_page.update_dh_state)
        worker.digital_human_state_changed.connect(self._on_digital_human_result)

        # Global bottom status bar — fan out the same worker signals.
        worker.login_state.connect(self._bottom_bar.update_login_state)
        worker.connection_changed.connect(self._bottom_bar.update_connection_state)
        worker.live_capture_state_changed.connect(self._bottom_bar.update_capture_state)
        worker.ai_runtime_changed.connect(self._bottom_bar.update_ai_state)
        worker.obs_runtime_changed.connect(self._bottom_bar.update_obs_state)
        worker.voice_runtime_changed.connect(self._bottom_bar.update_voice_state)
        worker.digital_human_state_changed.connect(self._bottom_bar.update_dh_state)
        self._bottom_bar.navigate_requested.connect(self._set_page)

        # HomePage dashboard wiring
        self._home_page.quick_start_requested.connect(self._on_quick_start)
        self._home_page.quick_stop_requested.connect(self._on_digital_human_stop)
        worker.danmaku_received.connect(self._home_page.append_activity)
        worker.metrics_updated.connect(self._home_page.update_dashboard_metrics)
        worker.connection_changed.connect(self._home_page.update_uptime_start)
        worker.ai_reply_ready.connect(self._home_page.append_ai_activity)

        # Keyword auto-reply wiring (Phase 10 第二批)
        worker.keyword_reply_fired.connect(self._on_keyword_reply_fired)
        self._keyword_reply_page.auto_reply_toggled.connect(self._on_keyword_auto_reply_toggled)
        self._keyword_reply_page.rules_changed.connect(self._push_keyword_config_to_worker)
        self._keyword_reply_page.rules_changed.connect(self._refresh_home_keyword_card)
        self._keyword_reply_page.related_settings_changed.connect(self._push_keyword_config_to_worker)
        self._keyword_reply_page.related_settings_changed.connect(self._mirror_sidebar_related_to_home)
        self._home_page.keyword_auto_reply_toggled.connect(self._on_keyword_auto_reply_toggled)
        self._home_page.keyword_template_switch_requested.connect(self._on_home_keyword_template_switch)
        self._home_page.keyword_related_settings_changed.connect(self._on_home_keyword_related_changed)

        # Load saved settings and apply
        self._apply_saved_settings()

    def _apply_saved_settings(self):
        from ai_reply import (
            DEFAULT_PERSONA_NAME, DEFAULT_PERSONA_ROLE, DEFAULT_PERSONA_STRATEGY,
            DEFAULT_PERSONA_SCENE, DEFAULT_PERSONA_TONE, DEFAULT_PERSONA_LIMIT,
            DEFAULT_PERSONA_TABOO,
        )
        settings = _load_settings()
        page = self._ai_config_page
        page._suspend_auto_save = True
        try:
            if settings.get("api_key"):
                page._api_key_input.lineEdit().setText(settings["api_key"])
            if settings.get("base_url"):
                page._base_url_input.lineEdit().setText(settings["base_url"])
            if settings.get("reply_interval"):
                page._interval_spin.setValue(settings["reply_interval"])
            if settings.get("auto_reply"):
                page._auto_reply_switch.setChecked(settings["auto_reply"])
            page._name_input.lineEdit().setText(_text_or_default(settings.get("persona_name"), DEFAULT_PERSONA_NAME))
            page._role_edit.setPlainText(_text_or_default(settings.get("persona_role"), DEFAULT_PERSONA_ROLE))
            page._strategy_edit.setPlainText(_text_or_default(settings.get("persona_strategy"), DEFAULT_PERSONA_STRATEGY))
            page._scene_edit.setPlainText(_text_or_default(settings.get("persona_scene"), DEFAULT_PERSONA_SCENE))
            page._tone_edit.setPlainText(_text_or_default(settings.get("persona_tone"), DEFAULT_PERSONA_TONE))
            page._limit_edit.setPlainText(_text_or_default(settings.get("persona_limit"), DEFAULT_PERSONA_LIMIT))
            page._taboo_edit.setPlainText(_text_or_default(settings.get("persona_taboo"), DEFAULT_PERSONA_TABOO))
        finally:
            page._suspend_auto_save = False
        data_source = _normalize_data_source(settings.get("data_source"))
        for idx, option in enumerate(self._settings_page._source_combo.menu().options()):
            if option.value() == data_source or option.text() == data_source:
                self._settings_page._source_combo.menu().setIndex(idx)
                break
        theme_name = settings.get("theme", _DEFAULT_THEME)
        if theme_name not in _THEME_MAP:
            theme_name = _DEFAULT_THEME
        self._obs_page.load_obs_action_settings(settings.get("obs_actions"))
        self._worker.set_obs_action_settings(
            self._obs_page.get_obs_action_settings().to_dict()
        )
        self._voice_page.load_voice_settings(settings.get("voice"))
        self._voice_api_dialog.load_voice_settings(settings.get("voice"))
        self._voice_clone_dialog.load_voice_settings(settings.get("voice"))
        self._anchor_settings_dialog.load_voice_settings(settings.get("voice"))
        self._copilot_settings_dialog.load_voice_settings(settings.get("voice"))
        self._home_page.update_voice_state(settings.get("voice") or {})
        self._worker.set_voice_settings(
            self._voice_page.get_voice_settings().to_dict()
        )
        apply_theme(theme_name)
        self._refresh_theme_styles()
        self._voice_page.load_streaming_settings(settings.get("digital_human"))
        # NOTE: do NOT forward obs_actions host/port/password into the
        # streaming console — they're independent. The streaming pipeline
        # uses 127.0.0.1:4455 by default (the digital-human pipeline runs
        # ffmpeg locally, OBS must be on the same machine to consume the
        # local HLS stream).
        if settings.get("auto_reply") and settings.get("api_key"):
            self._activate_ai()

        # Keyword auto-reply (Phase 10 第二批) — 把 settings 推到 worker，并镜像 UI 状态
        kw_enabled = bool(settings.get("keyword_auto_reply_enabled", False))
        kw_cooldown = int(settings.get("keyword_auto_reply_global_cooldown_sec", 30))
        kw_rate = int(settings.get("keyword_auto_reply_rate_limit_per_min", 20))
        kw_templates = settings.get("keyword_templates", {}) or {}
        active_tmpl = self._keyword_reply_page.get_active_template_name()
        self._worker.set_keyword_reply_config(
            enabled=kw_enabled,
            templates=kw_templates,
            active_template=active_tmpl,
            global_cooldown_sec=kw_cooldown,
            rate_limit_per_min=kw_rate,
        )
        self._keyword_reply_page.set_auto_reply_checked(kw_enabled)
        self._keyword_reply_page.set_related_settings(kw_cooldown, kw_rate)
        self._home_page.set_keyword_auto_reply_checked(kw_enabled)
        self._home_page.set_keyword_related_settings(kw_cooldown, kw_rate)
        self._home_page.refresh_keyword_card(active_tmpl)

    def _open_voice_api_dialog(self):
        self._voice_api_dialog.load_voice_settings(_load_settings().get("voice"))
        self._style_voice_api_dialog()
        self._voice_api_dialog.show()
        self._voice_api_dialog.raise_()
        self._voice_api_dialog.activateWindow()

    def _open_voice_clone_dialog(self):
        self._voice_clone_dialog.load_voice_settings(_load_settings().get("voice"))
        self._voice_clone_dialog._apply_theme_styles()
        self._voice_clone_dialog.show()
        self._voice_clone_dialog.raise_()
        self._voice_clone_dialog.activateWindow()

    def _open_anchor_settings_dialog(self):
        self._anchor_settings_dialog.load_voice_settings(_load_settings().get("voice"))
        self._anchor_settings_dialog._apply_theme_styles()
        self._anchor_settings_dialog.show()
        self._anchor_settings_dialog.raise_()
        self._anchor_settings_dialog.activateWindow()

    def _open_copilot_settings_dialog(self):
        self._copilot_settings_dialog.load_voice_settings(_load_settings().get("voice"))
        self._copilot_settings_dialog._apply_theme_styles()
        self._copilot_settings_dialog.show()
        self._copilot_settings_dialog.raise_()
        self._copilot_settings_dialog.activateWindow()

    def _style_voice_api_dialog(self):
        self._voice_api_dialog._apply_theme_styles()

    def _on_keyword_reply_fired(self, keyword: str, reply: str, nickname: str, count: int, injected: bool):
        status = "✓" if injected else "✗未注入"
        line = f"💬 命中『{keyword}』→ 回复『{reply}』 {status}"
        try:
            self._home_page.append_activity(line)
        except Exception:
            pass
        try:
            self._keyword_reply_page.on_rule_hit(keyword, count)
        except Exception:
            pass

    def _on_keyword_auto_reply_toggled(self, enabled: bool):
        data = _load_settings()
        data["keyword_auto_reply_enabled"] = bool(enabled)
        _save_settings(data)
        self._keyword_reply_page.set_auto_reply_checked(enabled)
        self._home_page.set_keyword_auto_reply_checked(enabled)
        self._push_keyword_config_to_worker()

    def _push_keyword_config_to_worker(self):
        data = _load_settings()
        self._worker.set_keyword_reply_config(
            enabled=bool(data.get("keyword_auto_reply_enabled", False)),
            templates=data.get("keyword_templates", {}) or {},
            active_template=self._keyword_reply_page.get_active_template_name(),
            global_cooldown_sec=int(data.get("keyword_auto_reply_global_cooldown_sec", 30)),
            rate_limit_per_min=int(data.get("keyword_auto_reply_rate_limit_per_min", 20)),
        )

    def _refresh_home_keyword_card(self):
        self._home_page.refresh_keyword_card(self._keyword_reply_page.get_active_template_name())

    def _on_home_keyword_template_switch(self, name: str):
        self._keyword_reply_page.set_active_template_name(name)
        self._push_keyword_config_to_worker()

    def _on_home_keyword_related_changed(self, cooldown_sec: int, rate_per_min: int):
        data = _load_settings()
        data["keyword_auto_reply_global_cooldown_sec"] = int(cooldown_sec)
        data["keyword_auto_reply_rate_limit_per_min"] = int(rate_per_min)
        _save_settings(data)
        self._keyword_reply_page.set_related_settings(cooldown_sec, rate_per_min)
        self._push_keyword_config_to_worker()

    def _mirror_sidebar_related_to_home(self):
        data = _load_settings()
        self._home_page.set_keyword_related_settings(
            int(data.get("keyword_auto_reply_global_cooldown_sec", 30)),
            int(data.get("keyword_auto_reply_rate_limit_per_min", 20)),
        )

    def _on_obs_settings_changed(self, settings_data: dict):
        self._worker.set_obs_action_settings(settings_data)
        # NOTE: OBS 联动 (keyword-driven scene switching) and 推流 (digital
        # human Media Source) are independent. The 联动 page's host/port/
        # password are NOT forwarded to the streaming pipeline anymore —
        # the streaming console keeps its own defaults (127.0.0.1:4455)
        # because the digital-human stream is always pushed to OBS on the
        # same machine that runs ffmpeg. Forwarding the 联动 fields broke
        # this when the user pointed 联动 at a remote OBS for unrelated
        # keyword rules.

    def _on_streaming_config_changed(self, payload: object):
        # Forward streaming config changes (selected video / video count) to
        # the HomePage checklist so its "绿幕素材" indicator stays current.
        if hasattr(self, "_home_page") and hasattr(self._home_page, "update_streaming_assets_state"):
            self._home_page.update_streaming_assets_state(payload)

    def _on_obs_status_check_requested(self, settings_data: dict):
        self._worker.check_obs_runtime(settings_data)

    def _on_voice_settings_changed(self, settings_data: dict):
        self._worker.set_voice_settings(settings_data)
        self._voice_page.load_voice_settings(settings_data)
        self._voice_api_dialog.load_voice_settings(settings_data)
        self._voice_clone_dialog.load_voice_settings(settings_data)
        self._anchor_settings_dialog.load_voice_settings(settings_data)
        self._copilot_settings_dialog.load_voice_settings(settings_data)
        self._home_page.update_voice_state(settings_data)

    def _on_voice_action_requested(self, action: dict):
        self._worker.run_voice_action(action)

    def _on_digital_human_start(self, config_data: dict):
        self._worker.start_digital_human(config_data)

    def _on_digital_human_stop(self):
        self._worker.stop_digital_human()

    # Pages where the bottom status bar adds no value and should be hidden:
    # - HomePage (0): checklist already covers the same info
    # - SettingsPage (5): settings have no live state worth surfacing
    # After merging DigitalHumanPage into VoiceConfigPage, settings shifted
    # from index 6 down to 5 (no DH slot between OBS and Settings).
    _BOTTOM_BAR_HIDDEN_PAGES = {0, 5}

    def _set_page(self, index: int):
        self.layerMain().setPage(index)
        self.layerMain().page_view.page_navigator.setCurrentIndex(index)
        self._apply_bottom_bar_visibility(index)

    def _apply_bottom_bar_visibility(self, index: int):
        """Sync bottom bar visibility with the active page index.

        Called both from `_set_page` (when navigation is programmatic / via
        HomePage nav buttons) and from the stacked_container.currentChanged
        signal (when the user clicks a sidebar icon — SiliconUI bypasses
        _set_page in that path).
        """
        if not hasattr(self, "_bottom_bar"):
            return
        self._bottom_bar.setVisible(index not in self._BOTTOM_BAR_HIDDEN_PAGES)
        self._relayout_bottom_bar()

    def _apply_app_icon(self):
        icon_svg = os.path.join(os.path.dirname(__file__), "icon.svg")
        icon_png = os.path.join(os.path.dirname(__file__), "icon.png")
        # Prefer icon.png: the on-disk icon.svg embeds a JPEG (mislabeled as PNG)
        # which loses the alpha channel and renders transparent areas as black.
        # Rebuild the SVG wrapper from the real PNG bytes at runtime.
        if os.path.exists(icon_png):
            import base64
            with open(icon_png, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink" '
                'width="256" height="256" viewBox="0 0 256 256">'
                f'<image width="256" height="256" '
                f'xlink:href="data:image/png;base64,{b64}"/></svg>'
            )
            self.layerMain().app_icon.load(svg.encode("utf-8"))
            self.setWindowIcon(QIcon(icon_png))
        elif os.path.exists(icon_svg):
            self.layerMain().app_icon.load(icon_svg)
        else:
            icon_color = SiGlobal.siui.colors["TEXT_THEME"]
            self.layerMain().app_icon.load(
                SiGlobal.siui.iconpack.get(
                    _APP_ICON_NAME,
                    color_code=icon_color,
                )
            )

    def _refresh_theme_styles(self):
        pages = [
            self._home_page, self._live_page, self._ai_config_page,
            self._voice_page, self._obs_page, self._settings_page,
            self._keyword_reply_page,
        ]
        if getattr(self, "_digital_human_page", None) is not None:
            pages.append(self._digital_human_page)
        for page in pages:
            if hasattr(page, "_apply_theme_styles"):
                page._apply_theme_styles()
        if hasattr(self, "_bottom_bar"):
            self._bottom_bar._apply_theme_styles()
        self._apply_app_icon()
        self._style_voice_api_dialog()
        try:
            SiGlobal.siui.reloadAllWindowsStyleSheet()
        except Exception:
            pass
        _apply_qt_global_theme()

    def _activate_ai(self):
        config_dict = self._ai_config_page.get_ai_config_dict()
        if config_dict.get("api_key"):
            from ai_reply import AIConfig
            config = AIConfig(
                api_key=config_dict["api_key"],
                base_url=config_dict.get("base_url", "https://api.deepseek.com/v1"),
                model=config_dict.get("model", "deepseek-chat"),
                system_prompt=config_dict.get("system_prompt", ""),
                auto_reply=self._ai_config_page._auto_reply_switch.isChecked(),
                reply_interval=config_dict.get("reply_interval", 30),
            )
            self._worker.set_ai_config(config)
            logger.info("AI engine activated (auto_reply={})", config.auto_reply)

    def _update_ai_engine(self):
        if self._worker._ai_engine:
            config_dict = self._ai_config_page.get_ai_config_dict()
            engine = self._worker._ai_engine
            engine.config.auto_reply = self._ai_config_page._auto_reply_switch.isChecked()
            engine.config.reply_interval = config_dict.get("reply_interval", 30)
            engine.config.system_prompt = config_dict.get("system_prompt", "")
            engine.config.api_key = config_dict.get("api_key", "")
            engine.config.base_url = config_dict.get("base_url", "https://api.deepseek.com/v1")
            engine.publish_status()
            logger.info("AI engine updated (auto_reply={})", engine.config.auto_reply)

    def _on_connect_room(self, url: str, source: str = "douyin"):
        if source == "wechat":
            return
        self._activate_ai()
        self._worker.connect_room(url, source)

    def _on_quick_start(self):
        """One-click start: kick off the digital-human → OBS streaming pipeline.

        The project has shifted from a 抖音 弹幕助手 to a 视频号 数字人直播带货
        platform, so "一键开播" no longer connects to a 抖音 room or starts
        capture. Pre-flight requirements:
          - 主播音色 cloned (voice runtime payload anchor_status == "ready")
          - OBS WebSocket reachable (handled inside the pipeline)
          - 绿幕素材 selected on the voice page's gallery
        Capture / 抖音 connection stays accessible via the 直播间 sidebar entry
        for users who still want danmaku-driven AI replies.
        """
        # Pull the current streaming config from VoiceConfigPage. We don't
        # block on local checks (the pipeline surfaces precise failures), but
        # we do short-circuit when no green-screen clip is selected since
        # ffmpeg requires a real video_path.
        if not hasattr(self, "_voice_page"):
            logger.warning("Quick start: voice page not ready")
            return
        config = self._voice_page.get_streaming_config_dict()
        if not config.get("video_path"):
            self._set_page(3)
            self.LayerRightMessageSidebar().send(
                title="一键开播",
                text="请先在 AI 语音 / 推流 页选择一段绿幕素材，再次点击一键开播。",
                msg_type=2,
                icon=SiGlobal.siui.iconpack.get("ic_fluent_info_filled"),
                fold_after=4000,
            )
            return
        if config.get("avatar_ready") is False:
            self._set_page(3)
            self.LayerRightMessageSidebar().send(
                title="一键开播",
                text="当前主播形象还在处理，完成后再点击一键开播。",
                msg_type=2,
                icon=SiGlobal.siui.iconpack.get("ic_fluent_info_filled"),
                fold_after=4000,
            )
            return
        try:
            self._worker.start_digital_human(config)
        except Exception as e:
            logger.warning("Quick start: start_digital_human failed: {}", e)
            self.LayerRightMessageSidebar().send(
                title="开播失败",
                text=f"无法启动推流任务：{e}",
                msg_type=4,
                icon=SiGlobal.siui.iconpack.get("ic_fluent_error_circle_filled"),
                fold_after=6000,
            )
            return
        logger.info("Quick start dispatched (digital-human stream): {}", config.get("video_path"))

    def _on_live_capture_toggle_requested(self, enabled: bool, source: str = "douyin"):
        self._worker.set_live_capture_enabled(enabled, source)

    def _on_clear_session(self):
        self._worker.clear_and_restart()

    def _on_login_state(self, state: str):
        if state == "logged_in":
            logger.info("Login confirmed")
        elif state == "scanning":
            logger.info("Waiting for QR scan...")

    def _on_error(self, msg: str):
        logger.error("App error: {}", msg)
        self.LayerRightMessageSidebar().send(
            title="错误", text=msg, msg_type=4,
            icon=SiGlobal.siui.iconpack.get("ic_fluent_error_circle_filled"),
            fold_after=5000,
        )

    def _on_digital_human_result(self, payload: object):
        """Surface streaming pipeline failures and OBS auto-config warnings
        as Toast notifications. Other listeners (voice_page / home_page /
        bottom_bar) keep handling the same payload independently."""
        if not isinstance(payload, dict):
            return
        # Hard failure: pipeline didn't reach STREAMING state
        if payload.get("ok") is False:
            msg = payload.get("message", "推流失败")
            self.LayerRightMessageSidebar().send(
                title="开播失败", text=msg, msg_type=4,
                icon=SiGlobal.siui.iconpack.get("ic_fluent_error_circle_filled"),
                fold_after=8000,
            )
            return
        # Soft warning: ffmpeg is streaming but OBS config failed
        warning = payload.get("obs_warning")
        if warning:
            self.LayerRightMessageSidebar().send(
                title="OBS 自动配置失败", text=warning, msg_type=3,
                icon=SiGlobal.siui.iconpack.get("ic_fluent_warning_filled"),
                fold_after=12000,
            )

    def closeEvent(self, event):
        from audio_output import stop_all_audio
        if hasattr(self, "_voice_page"):
            with contextlib.suppress(Exception):
                self._voice_page.shutdown()
        stop_all_audio()
        self._worker.stop()
        QApplication.instance().quit()
        super().closeEvent(event)


from ui_pages.homepage import HomePage
from ui_pages.aiconfigpage import AIConfigPage
from ui_pages.voiceconfigpage import VoiceConfigPage
from ui_dialogs.voiceapidialog import VoiceApiDialog
from ui_dialogs.voiceclonedialog import VoiceCloneDialog
from ui_dialogs.anchorsettingsdialog import AnchorSettingsDialog
from ui_dialogs.copilotsettingsdialog import CopilotSettingsDialog
from ui_dialogs.obsruledialog import ObsRuleDialog
from ui_dialogs.obsrulesmanagerdialog import ObsRulesManagerDialog
from ui_pages.obsactionpage import ObsActionPage
from ui_pages.liveroompage import LiveRoomPage
from ui_pages.general_settings import GeneralSettingsPage
from ui_pages.keyword_reply import KeywordReplyPage
from ui_pages.digitalhumanpage import DigitalHumanPage

apply_theme(_DEFAULT_THEME)
