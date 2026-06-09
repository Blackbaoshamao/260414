"""LiveRoomPage — extracted from ui.py."""
from __future__ import annotations
import time
from ui_constants import _DEFAULT_MESSAGE_FILTERS
from ui_constants import _MESSAGE_FILTER_OPTIONS
from ui_settings import _load_settings
from ui_settings import _save_settings
from ui_theme import FONT_MONO
from ui_theme import FONT_UI
from ui_theme import _build_input_field_stylesheet
from ui_theme import _mix_hex_colors
from ui_theme import apply_theme


from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QTimer, QRect, QSize, QPropertyAnimation, QEasingCurve, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush, QIcon
from PyQt5.QtSvg import QSvgRenderer
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
from loguru import logger
from ui import CaptureWorker, DanmakuDisplay, FilterCheckBox
from ui_components import MacButton, MacLineEdit, MacComboBox


def _fluent_icon(name: str, size: int = 18) -> QIcon:
    """Render a Fluent SVG icon (from PyQt-SiliconUI icon pack) to a QIcon."""
    try:
        svg = SiGlobal.siui.iconpack.get(name)
    except KeyError:
        return QIcon()
    if isinstance(svg, str):
        svg = svg.encode("utf-8")
    renderer = QSvgRenderer(svg)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class LiveRoomPage(SiPage):
    connect_requested = pyqtSignal(str, str)
    live_capture_toggle_requested = pyqtSignal(bool, str)
    clear_session_requested = pyqtSignal()

    def __init__(self, worker: CaptureWorker, parent=None):
        super().__init__(parent)
        self.setPadding(0)
        self.setScrollMaximumWidth(10000)
        self._worker = worker
        self._connected = False
        self._connection_state = "disconnected"
        self._live_capture_state = "disabled"
        self._live_capture_message = "抓取未启用"
        self._connect_time = 0.0
        self._parse_failure_count = 0
        self._message_filters = dict(_DEFAULT_MESSAGE_FILTERS)
        self._filter_checks: dict[str, QCheckBox] = {}
        container = QWidget()
        self._live_container = container

        # Top bar widget (internal QHBoxLayout for horizontal arrangement)
        self._top_bar_widget = QWidget(container)
        top_bar = QHBoxLayout(self._top_bar_widget)
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(10)

        self._capture_source = "douyin"
        self._source_combo = MacComboBox()
        self._source_combo.addItem(_fluent_icon("ic_fluent_music_note_2_filled"),
                                    "抖音", "douyin")
        self._source_combo.addItem(_fluent_icon("ic_fluent_chat_filled"),
                                    "微信", "wechat")
        self._source_combo.setFixedSize(130, 36)
        self._source_combo.setFont(FONT_UI)
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        top_bar.addWidget(self._source_combo)

        self._room_input = MacLineEdit(placeholder="输入直播间 ID 或 URL 后连接")
        self._room_input.setFont(FONT_MONO)
        self._room_input.returnPressed.connect(self._on_connect)
        top_bar.addWidget(self._room_input, stretch=1)

        self._live_capture_btn = MacButton("启动", variant="secondary")
        self._live_capture_btn.setFixedSize(120, 36)
        self._live_capture_btn.setFont(FONT_UI)
        self._live_capture_btn.clicked.connect(self._on_live_capture_button_clicked)
        top_bar.addWidget(self._live_capture_btn)

        self._connect_btn = MacButton("连接", variant="primary")
        self._connect_btn.setFixedSize(80, 36)
        self._connect_btn.setFont(FONT_UI)
        self._connect_btn.clicked.connect(self._on_connect)
        self._connect_btn.setEnabled(False)
        top_bar.addWidget(self._connect_btn)

        self._reset_btn = MacButton("重登", variant="destructive")
        self._reset_btn.setFixedSize(60, 36)
        self._reset_btn.setFont(FONT_UI)
        self._reset_btn.clicked.connect(self.clear_session_requested.emit)
        top_bar.addWidget(self._reset_btn)

        self._status_label = QLabel("未连接")
        self._status_label.setFont(FONT_UI)
        self._status_label.setFixedWidth(130)
        self._status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        top_bar.addWidget(self._status_label)

        self._metrics_label = QLabel("")
        self._metrics_label.setFont(FONT_MONO)
        self._metrics_label.setFixedHeight(28)
        self._metrics_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        top_bar.addWidget(self._metrics_label)

        self._top_bar_widget.setFixedHeight(48)

        # Title
        self._danmaku_title_label = QLabel("直播弹幕", container)
        self._danmaku_title_label.setFont(FONT_UI)

        # Filter bar widget (internal QHBoxLayout for checkboxes)
        self._filter_bar = QWidget(container)
        filter_layout = QHBoxLayout(self._filter_bar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(12)
        for key, label in _MESSAGE_FILTER_OPTIONS:
            checkbox = FilterCheckBox(label, self._filter_bar)
            checkbox.setChecked(self._message_filters.get(key, True))
            checkbox.stateChanged.connect(
                lambda state, filter_key=key: self._on_filter_toggled(filter_key, state)
            )
            self._filter_checks[key] = checkbox
            filter_layout.addWidget(checkbox)
        filter_layout.addStretch(1)
        self._filter_bar.setFixedHeight(32)

        # Danmaku displays — two independent displays, toggled by source
        self._display_dy = DanmakuDisplay(container)
        self._display_dy.set_allowed_types(self._active_message_types())
        self._display_wx = DanmakuDisplay(container)
        self._display_wx.set_allowed_types(self._active_message_types())
        self._display_wx.hide()
        self._persist_message_filters()

        self.setAttachment(container)

        # Wire signals
        worker.danmaku_received.connect(self._handle_danmaku_message)
        worker.connection_changed.connect(self._update_status)
        worker.live_capture_state_changed.connect(self._on_live_capture_state_changed)
        worker.error_occurred.connect(self._on_error)
        worker.metrics_updated.connect(self._on_metrics_updated)
        worker.parse_failure_count.connect(self._on_parse_failure)
        self._apply_theme_styles()
        self._update_source_specific_controls()
        self._render_live_capture_controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Defer to after all framework resize processing completes
        QTimer.singleShot(0, self._apply_danmaku_geometry)

    def _apply_danmaku_geometry(self):
        h = self.scroll_area.height()
        if h <= 0:
            return
        self._live_container.setFixedHeight(h)
        cw = self._live_container.width()
        if cw <= 0:
            return
        margin = 16
        spacing = 8
        inner_w = cw - margin * 2
        # Top bar
        self._top_bar_widget.setGeometry(margin, 8, inner_w, 48)
        # Title
        ty = self._top_bar_widget.geometry().bottom() + spacing
        self._danmaku_title_label.setGeometry(margin, ty, inner_w, 22)
        # Filter bar
        fy = self._danmaku_title_label.geometry().bottom() + spacing
        self._filter_bar.setGeometry(margin, fy, inner_w, 32)
        # Danmaku display fills the rest
        dy = self._filter_bar.geometry().bottom() + spacing
        display_h = h - dy - margin
        if display_h > 40:
            self._display_dy.setGeometry(margin, dy, inner_w, display_h)
            self._display_wx.setGeometry(margin, dy, inner_w, display_h)

    def _active_message_types(self) -> set[str]:
        return {key for key, enabled in self._message_filters.items() if enabled}

    def _persist_message_filters(self):
        data = _load_settings()
        data["message_filters"] = dict(self._message_filters)
        _save_settings(data)

    def _on_filter_toggled(self, key: str, state: int):
        self._message_filters[key] = bool(state)
        types = self._active_message_types()
        self._display_dy.set_allowed_types(types)
        self._display_wx.set_allowed_types(types)
        self._persist_message_filters()

    @pyqtSlot(dict)
    def _handle_danmaku_message(self, msg: dict):
        if msg.get("source") == "wechat":
            self._display_wx.append_message(msg)
        else:
            self._display_dy.append_message(msg)

    def _apply_theme_styles(self):
        # Mac* widgets refresh themselves
        for btn in (self._live_capture_btn, self._connect_btn, self._reset_btn):
            btn.apply_theme_styles()
        self._room_input.apply_theme_styles()
        # Title label inherits from global QLabel rule (CLR_TEXT_SEC) — clear inline
        self._danmaku_title_label.setStyleSheet("")
        for checkbox in self._filter_checks.values():
            checkbox.update()
        if hasattr(self, '_metrics_label'):
            self._metrics_label.setStyleSheet(
                f"color: {theme.CLR_TEXT_TERT}; border: none; background: transparent;")
        self._display_dy._apply_theme_styles()
        self._display_wx._apply_theme_styles()
        self._render_live_capture_controls()

    def _set_status_label(self, color: str, text: str):
        self._status_label.setText(f'<span style="color:{color}">●</span> {text}')
        self._status_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; border: none;")

    def _render_connection_status(self):
        colors = {"connected": theme.CLR_GREEN, "disconnected": theme.CLR_RED,
                  "connecting": theme.CLR_YELLOW, "stalled": theme.CLR_YELLOW}
        labels = {"connected": "已连接", "disconnected": "未连接",
                  "connecting": "连接中...", "stalled": "暂无数据"}
        c = colors.get(self._connection_state, theme.CLR_TEXT_SEC)
        t = labels.get(self._connection_state, self._connection_state)
        self._set_status_label(c, t)
        self._connected = (self._connection_state == "connected")
        if self._connection_state == "connected":
            self._connect_time = time.time()
            self._parse_failure_count = 0

    def _render_live_capture_controls(self):
        state = self._live_capture_state
        ready = state == "ready"
        busy = state in {"starting", "stopping"}
        if state == "ready":
            self._live_capture_btn.setText("关闭直播抓取")
        elif state == "starting":
            self._live_capture_btn.setText("正在启动...")
        elif state == "stopping":
            self._live_capture_btn.setText("正在关闭...")
        else:
            self._live_capture_btn.setText("启动")
        self._live_capture_btn.setEnabled(not busy)
        self._connect_btn.setEnabled(ready and not busy and self._capture_source != "wechat")
        self._reset_btn.setEnabled(self._capture_source != "wechat" and not busy)
        if ready:
            if self._capture_source == "wechat":
                self._set_status_label(theme.CLR_GREEN, "视频号助手监听中")
            else:
                self._render_connection_status()
            return
        self._connected = False
        self._metrics_label.setText("")
        colors = {
            "disabled": theme.CLR_RED,
            "starting": theme.CLR_YELLOW,
            "stopping": theme.CLR_YELLOW,
            "error": theme.CLR_RED,
        }
        self._set_status_label(colors.get(state, theme.CLR_TEXT_SEC), self._live_capture_message)

    def _update_source_specific_controls(self):
        is_wechat = self._capture_source == "wechat"
        self._room_input.setVisible(not is_wechat)
        self._connect_btn.setVisible(not is_wechat)
        self._reset_btn.setVisible(not is_wechat)
        self._live_capture_btn.setFixedWidth(138 if is_wechat else 120)
        self._status_label.setFixedWidth(172 if is_wechat else 130)
        if is_wechat:
            self._room_input.clearFocus()

    def _on_source_changed(self, idx: int):
        data = self._source_combo.itemData(idx)
        if isinstance(data, str):
            old = self._capture_source
            self._capture_source = data
            if self._live_capture_state in ("starting", "ready") and old != self._capture_source:
                self.live_capture_toggle_requested.emit(False, old)
            self._display_dy.setVisible(self._capture_source != "wechat")
            self._display_wx.setVisible(self._capture_source == "wechat")
            self._update_source_specific_controls()
            self._render_live_capture_controls()

    def _on_live_capture_button_clicked(self):
        if self._live_capture_state in {"starting", "stopping"}:
            return
        enable = self._live_capture_state != "ready"
        if enable:
            self._on_live_capture_state_changed("starting", "正在启动浏览器...")
        else:
            self._on_live_capture_state_changed("stopping", "正在关闭直播抓取...")
        self.live_capture_toggle_requested.emit(enable, self._capture_source)

    @pyqtSlot(str, str)
    def _on_live_capture_state_changed(self, state: str, message: str):
        self._live_capture_state = state
        self._live_capture_message = message or ("直播抓取已启用" if state == "ready" else "抓取未启用")
        if state != "ready":
            self._connection_state = "disconnected"
        self._render_live_capture_controls()

    def _on_connect(self):
        if self._live_capture_state != "ready":
            self._live_capture_message = "请先启用直播抓取"
            self._render_live_capture_controls()
            return
        text = self._room_input.text().strip()
        if not text: return
        if text.isdigit():
            text = f"https://live.douyin.com/{text}"
        self.connect_requested.emit(text, self._capture_source)

    def _update_status(self, state: str):
        self._connection_state = state
        self._render_live_capture_controls()

    @pyqtSlot(dict)
    def _on_metrics_updated(self, snap: dict):
        if not self._connected:
            self._metrics_label.setText("")
            return
        duration = int(time.time() - self._connect_time) if self._connect_time else 0
        mins, secs = divmod(duration, 60)
        hrs, mins = divmod(mins, 60)
        if hrs > 0:
            time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"
        else:
            time_str = f"{mins:02d}:{secs:02d}"
        qps = snap.get("input_qps", 0)
        loss = sum(snap.get("loss", {}).values())
        parts = [time_str, f"{qps:.1f}/s"]
        if loss > 0:
            parts.append(f"loss:{loss}")
        if self._parse_failure_count > 0:
            parts.append(f"fail:{self._parse_failure_count}")
        self._metrics_label.setText(" | ".join(parts))
        self._metrics_label.setStyleSheet(f"color: {theme.CLR_TEXT_TERT}; border: none; background: transparent;")

    @pyqtSlot(int)
    def _on_parse_failure(self, count: int):
        self._parse_failure_count = count

    def _on_error(self, msg: str):
        logger.error("LiveRoomPage: {}", msg)


# ---------------------------------------------------------------------------
# AIConfigPage — Gemini-style settings
# ---------------------------------------------------------------------------

