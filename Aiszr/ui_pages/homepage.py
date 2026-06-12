"""AI 直播间首页 — LiveRoom 嵌入网格 + Sonoma 风快捷面板。"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PyQt5.QtCore import (
    pyqtSignal, Qt, QRectF, QAbstractNativeEventFilter, QTimer, QSize,
)
from PyQt5.QtGui import QColor, QIcon, QIntValidator, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QTextBrowser, QToolTip, QApplication, QDialog,
    QSizePolicy,
)
import ui_theme as theme
from ui_components import MacCard, MacButton, MacComboBox, MacLineEdit

from fluent_page import FluentPage

try:
    from qfluentwidgets import FluentIcon
except ImportError:
    FluentIcon = None

from obs_actions import ObsActionSettings
from ui_settings import _load_settings, _save_settings


# Windows 设备插拔通知
_WM_DEVICECHANGE = 0x0219
_DBT_DEVNODES_CHANGED = 0x0007


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class _DeviceChangeFilter(QAbstractNativeEventFilter):
    """监听 Windows WM_DEVICECHANGE，设备节点变化时回调。"""

    def __init__(self, callback):
        super().__init__()
        self._cb = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                msg = _MSG.from_address(int(message))
                if msg.message == _WM_DEVICECHANGE and \
                        msg.wParam == _DBT_DEVNODES_CHANGED:
                    self._cb()
            except Exception:
                pass
        return False, 0


_ICON_SVGS = {
    "ic_fluent_mic_filled": '<svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>',
    "ic_fluent_desktop_speaker_filled": '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>',
}


def _icon_svg(name: str) -> str:
    """Resolve Fluent UI icon name → SVG string."""
    return _ICON_SVGS.get(name, "")


def _fluent_icon(name: str, size: int = 18) -> QIcon:
    svg_data = _icon_svg(name)
    if not svg_data:
        return QIcon()
    if isinstance(svg_data, str):
        svg_data = svg_data.encode("utf-8")
    renderer = QSvgRenderer(svg_data)
    ratio = _device_pixel_ratio()
    physical_size = max(1, int(size * ratio + 0.5))
    pixmap = QPixmap(physical_size, physical_size)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


def _device_pixel_ratio(widget=None) -> float:
    try:
        if widget is not None:
            return max(1.0, float(widget.devicePixelRatioF()))
    except Exception:
        pass
    app = QApplication.instance()
    if app is not None:
        try:
            screen = app.primaryScreen()
            if screen is not None:
                return max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            pass
    return 1.0


class _VolumeIcon(QWidget):
    """音量响应图标 — mic/speaker SVG，内部根据实时音量从底部往上填充绿→黄→红色条。

    mode="input"    监听选中的输入设备（麦克风）
    mode="loopback" 监听选中输出设备的 WASAPI loopback（系统输出）
    """

    ICON_SIZE = 22
    LEVEL_BOOST = 4.0   # 放大量，让小声也能看见
    ATTACK = 1.0        # 峰值瞬间打满
    DECAY = 0.85        # 衰减系数，越大降得越慢

    def __init__(self, icon_name: str, mode: str = "input", parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._mode = mode
        svg_data = _icon_svg(icon_name)
        if isinstance(svg_data, str):
            svg_data = svg_data.encode("utf-8")
        self._renderer = QSvgRenderer(svg_data) if svg_data else QSvgRenderer()
        self._level = 0.0
        self._lock = threading.Lock()
        self._pa = None
        self._stream = None
        self._target_device: int | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30Hz repaint
        self._timer.timeout.connect(self.update)
        self._timer.start()

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_stream)

    # ── Public ─────────────────────────────────────────────

    def set_device(self, device_index):
        """切换监听设备 — input 模式传输入设备 idx；loopback 模式传 *输出* 设备 idx。"""
        try:
            idx = int(device_index) if device_index is not None else -1
        except (TypeError, ValueError):
            idx = -1
        if idx < 0:
            self._stop_stream()
            self._target_device = None
            return
        if idx == self._target_device and self._stream is not None:
            return
        self._target_device = idx
        self._stop_stream()
        self._open_stream(idx)

    # ── Internal: stream ──────────────────────────────────

    def _open_stream(self, idx: int):
        try:
            import pyaudiowpatch as paw
        except ImportError:
            return
        try:
            self._pa = paw.PyAudio()
            if self._mode == "loopback":
                target_idx = self._resolve_loopback_idx(self._pa, idx)
            else:
                target_idx = idx
            if target_idx is None:
                self._stop_stream()
                return
            info = self._pa.get_device_info_by_index(target_idx)
            channels = max(1, min(2, int(info.get("maxInputChannels") or 1)))
            rate = int(info.get("defaultSampleRate") or 48000)
            self._stream = self._pa.open(
                format=paw.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=target_idx,
                frames_per_buffer=1024,
                stream_callback=self._cb,
            )
        except Exception as e:
            try:
                from loguru import logger
                logger.warning(f"_VolumeIcon[{self._mode}] open failed (device={idx}): {e}")
            except Exception:
                pass
            self._stop_stream()

    @staticmethod
    def _resolve_loopback_idx(pa, output_idx: int):
        """根据用户选中的输出设备，找它的 [Loopback] 伴生输入设备。"""
        try:
            target_name = pa.get_device_info_by_index(output_idx).get("name", "")
        except Exception:
            target_name = ""
        if target_name:
            try:
                for d in pa.get_loopback_device_info_generator():
                    if target_name and target_name in d.get("name", ""):
                        return d["index"]
            except Exception:
                pass
        try:
            return pa.get_default_wasapi_loopback()["index"]
        except Exception:
            return None

    def _stop_stream(self):
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        with self._lock:
            self._level = 0.0

    def _cb(self, in_data, frame_count, time_info, status):
        try:
            import pyaudiowpatch as paw
            arr = np.frombuffer(in_data, dtype=np.float32)
            peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        except Exception:
            return (None, 0)
        with self._lock:
            if peak > self._level:
                self._level = peak
            else:
                self._level = self._level * self.DECAY + peak * (1.0 - self.DECAY)
        return (None, paw.paContinue)

    def closeEvent(self, event):
        self._stop_stream()
        super().closeEvent(event)

    # ── Painting ──────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        sz = self.size()
        iw = ih = self.ICON_SIZE
        ox = (sz.width() - iw) // 2
        oy = (sz.height() - ih) // 2

        ratio = _device_pixel_ratio(self)
        mic_pix = QPixmap(
            max(1, int(iw * ratio + 0.5)),
            max(1, int(ih * ratio + 0.5)),
        )
        mic_pix.setDevicePixelRatio(ratio)
        mic_pix.fill(Qt.transparent)
        if self._renderer.isValid():
            p2 = QPainter(mic_pix)
            p2.setRenderHint(QPainter.Antialiasing, True)
            p2.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self._renderer.render(p2, QRectF(0, 0, iw, ih))
            p2.end()

        with self._lock:
            lvl = self._level
        boosted = min(1.0, lvl * self.LEVEL_BOOST)

        fill_pix = QPixmap(
            max(1, int(iw * ratio + 0.5)),
            max(1, int(ih * ratio + 0.5)),
        )
        fill_pix.setDevicePixelRatio(ratio)
        fill_pix.fill(Qt.transparent)
        if boosted > 0.01:
            p3 = QPainter(fill_pix)
            p3.setRenderHint(QPainter.Antialiasing, True)
            p3.setRenderHint(QPainter.SmoothPixmapTransform, True)
            h = int(ih * boosted)
            p3.fillRect(0, ih - h, iw, h, self._level_color(boosted))
            p3.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            p3.drawPixmap(0, 0, mic_pix)
            p3.end()

        painter.drawPixmap(ox, oy, mic_pix)
        if boosted > 0.01:
            painter.setOpacity(0.75)
            painter.drawPixmap(ox, oy, fill_pix)
            painter.setOpacity(1.0)

    @staticmethod
    def _level_color(lvl: float) -> QColor:
        """0..0.5 绿→黄，0.5..1 黄→红。"""
        if lvl < 0.5:
            t = lvl * 2
            return QColor(int(255 * t), 255, 0)
        t = (lvl - 0.5) * 2
        return QColor(255, int(255 * (1 - t)), 0)


class HomePage(FluentPage):
    navigate_to_page = pyqtSignal(int)
    quick_start_requested = pyqtSignal()
    quick_stop_requested = pyqtSignal()
    obs_status_check_requested = pyqtSignal(object)
    obs_settings_changed = pyqtSignal(object)
    ai_assistant_reply_toggled = pyqtSignal(bool)
    ai_assistant_voice_toggled = pyqtSignal(bool)
    keyword_auto_reply_toggled = pyqtSignal(bool)
    keyword_voice_toggled = pyqtSignal(bool)
    scheduled_scripts_toggled = pyqtSignal(bool)
    scheduled_random_space_toggled = pyqtSignal(bool)
    keyword_template_switch_requested = pyqtSignal(str)
    keyword_related_settings_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPadding(8)
        self.setScrollMaximumWidth(1600)
        self._audio_devices: list[dict] = []
        self._selected_in_index: int = -1
        self._selected_out_index: int = -1
        self._worker = None
        self._cooldown_timer: QTimer | None = None

        root = QWidget(self)
        self._root = root
        outer = QVBoxLayout(root)
        outer.setContentsMargins(4, 0, 4, 4)
        outer.setSpacing(6)

        # Hero greeting — brand title
        self._hero_row = self._build_hero()
        outer.addWidget(self._hero_row)

        grid = QGridLayout()
        grid.setSpacing(theme.SPACING_MD)
        grid.setRowStretch(0, 7)
        grid.setRowStretch(1, 2)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        # ① LiveRoom slot (col 0-1, row 0)
        self._monitor_slot = QVBoxLayout()
        grid.addLayout(self._monitor_slot, 0, 0, 1, 2)

        # Right column: 快捷操作 + 自动场控 + 关键词回复
        right_col = QVBoxLayout()
        right_col.setSpacing(theme.SPACING_MD)
        right_col.addWidget(self._build_quick_card(), stretch=3)
        right_col.addWidget(self._build_scheduled_card(), stretch=2)
        right_col.addWidget(self._build_keyword_card(), stretch=5)
        grid.addLayout(right_col, 0, 2, 2, 1)

        # Bottom row: 音频设备 + 推流控制, split evenly inside the left span.
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(theme.SPACING_MD)
        bottom_row.addWidget(self._build_audio_card(), stretch=1)
        bottom_row.addWidget(self._build_stream_card(), stretch=1)
        grid.addLayout(bottom_row, 1, 0, 1, 2)

        outer.addLayout(grid, stretch=1)
        self.setAttachment(root)
        self._load_audio_devices()
        self._install_hotplug_watch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self._scroll.height()
        if h > 0:
            self._root.setFixedHeight(h)

    def selected_out_index(self) -> int:
        """Output device index chosen by the speaker dropdown. -1 means none.

        Reads directly from the combo so we never disagree with what the user sees.
        """
        combo = getattr(self, "_spk_combo", None)
        if combo is None or combo.count() == 0:
            return -1
        data = combo.currentData()
        if data is None:
            return -1
        try:
            return int(data)
        except (TypeError, ValueError):
            return -1

    def set_live_page(self, live_page):
        live_page.setParent(self)
        live_scroll = getattr(live_page, "_scroll", None)
        if live_scroll is not None:
            if hasattr(live_scroll, "setVerticalScrollBarPolicy"):
                live_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                live_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            else:
                for name in (
                    "scroll_bar_frame_vertical",
                    "scroll_bar_frame_horizontal",
                    "scroll_bar_vertical",
                    "scroll_bar_horizontal",
                ):
                    bar = getattr(live_scroll, name, None)
                    if bar is not None:
                        bar.hide()
        self._monitor_slot.addWidget(live_page)

    # ── Hero ───────────────────────────────────────────────

    def _build_hero(self) -> QWidget:
        """Lightweight brand header."""
        row = QWidget(self)
        ly = QHBoxLayout(row)
        ly.setContentsMargins(8, 4, 8, 4)
        ly.setSpacing(8)

        self._hero_title = QLabel("Aiszr.")
        self._hero_title.setFont(theme.FONT_TITLE_2)
        ly.addWidget(self._hero_title)
        ly.addStretch(1)
        self._apply_hero_styles()
        return row

    def _apply_hero_styles(self):
        if hasattr(self, "_hero_title"):
            self._hero_title.setStyleSheet(
                f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
            )

    # ── Stream control card ───────────────────────────────

    def _build_stream_card(self) -> MacCard:
        card = MacCard(self, title="推流控制")
        body = card.body()
        body.setSpacing(6)

        self._stream_asset_label = QLabel("主播形象：未选择")
        self._stream_asset_label.setWordWrap(True)
        self._stream_asset_label.setFont(theme.FONT_CAPTION)
        self._stream_asset_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_SEC}; border: none; background: transparent;"
        )
        body.addWidget(self._stream_asset_label)
        self._stream_state_label = QLabel("推流：空闲")
        self._stream_state_label.setWordWrap(True)
        self._stream_state_label.setFont(theme.FONT_CAPTION)
        self._stream_state_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_SEC}; border: none; background: transparent;"
        )
        body.addWidget(self._stream_state_label)
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING_SM)
        self._home_stream_start_btn = MacButton("一键推流", variant="primary")
        self._home_stream_start_btn.setEnabled(False)
        self._home_stream_start_btn.clicked.connect(self.quick_start_requested.emit)
        row.addWidget(self._home_stream_start_btn)
        self._home_stream_stop_btn = MacButton("停止推流", variant="secondary")
        self._home_stream_stop_btn.clicked.connect(self.quick_stop_requested.emit)
        row.addWidget(self._home_stream_stop_btn)
        body.addLayout(row)
        return card

    # ── Scheduled live-control card ───────────────────────

    def _build_scheduled_card(self) -> MacCard:
        card = MacCard(self, title="自动场控")
        body = card.body()
        body.setSpacing(8)

        self._scheduled_template_label = QLabel("模板：未设置")
        self._scheduled_template_label.setFont(theme.FONT_BODY)
        self._scheduled_template_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; border: none; background: transparent;"
        )
        body.addWidget(self._scheduled_template_label)

        self._scheduled_summary_label = QLabel("话术：0 条 · 未启用")
        self._scheduled_summary_label.setWordWrap(True)
        self._scheduled_summary_label.setFont(theme.FONT_CAPTION)
        self._scheduled_summary_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_SEC}; border: none; background: transparent;"
        )
        body.addWidget(self._scheduled_summary_label)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACING_SM)
        self._scheduled_toggle_btn = MacButton("开启自动场控", variant="pill")
        self._scheduled_toggle_btn.setCheckable(True)
        self._scheduled_toggle_btn.setFixedHeight(30)
        self._scheduled_toggle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._scheduled_toggle_btn.toggled.connect(self._on_scheduled_toggled)
        row.addWidget(self._scheduled_toggle_btn, stretch=1)

        self._scheduled_random_space_btn = MacButton("开启随机空格", variant="pill")
        self._scheduled_random_space_btn.setCheckable(True)
        self._scheduled_random_space_btn.setFixedHeight(30)
        self._scheduled_random_space_btn.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self._scheduled_random_space_btn.toggled.connect(
            self._on_scheduled_random_space_toggled
        )
        row.addWidget(self._scheduled_random_space_btn, stretch=1)
        body.addLayout(row)
        self.refresh_scheduled_card()
        return card

    def refresh_scheduled_card(self, settings: object | None = None):
        if not hasattr(self, "_scheduled_summary_label"):
            return
        if settings is None:
            from live_control_config import LiveControlSettings

            settings = LiveControlSettings.from_settings(_load_settings())
        template = settings.get_active_template()
        scripts = list(getattr(template, "scheduled_scripts", []) or [])
        enabled = bool(getattr(settings, "scheduled_scripts_enabled", False))
        random_space = bool(getattr(settings, "scheduled_scripts_random_space_enabled", False))
        self._scheduled_template_label.setText(f"模板：{template.name}")
        self._scheduled_summary_label.setText(
            f"话术：{len(scripts)} 条 · {'自动已开' if enabled else '自动未开'} · "
            f"{'空格已开' if random_space else '空格未开'}"
        )
        self.set_scheduled_scripts_checked(enabled)
        self.set_scheduled_random_space_checked(random_space)

    def _on_scheduled_toggled(self, checked: bool):
        self._set_toggle_text(
            self._scheduled_toggle_btn,
            bool(checked),
            "开启自动场控",
            "关闭自动场控",
        )
        self.scheduled_scripts_toggled.emit(bool(checked))

    def _on_scheduled_random_space_toggled(self, checked: bool):
        self._set_toggle_text(
            self._scheduled_random_space_btn,
            bool(checked),
            "开启随机空格",
            "关闭随机空格",
        )
        self.scheduled_random_space_toggled.emit(bool(checked))

    def set_scheduled_scripts_checked(self, checked: bool):
        if not hasattr(self, "_scheduled_toggle_btn"):
            return
        self._set_checkable_button(
            self._scheduled_toggle_btn,
            bool(checked),
            "开启自动场控",
            "关闭自动场控",
        )

    def set_scheduled_random_space_checked(self, checked: bool):
        if not hasattr(self, "_scheduled_random_space_btn"):
            return
        self._set_checkable_button(
            self._scheduled_random_space_btn,
            bool(checked),
            "开启随机空格",
            "关闭随机空格",
        )

    # ── Keyword reply card ────────────────────────────────

    def _build_keyword_card(self) -> MacCard:
        card = MacCard(self, title="关键词回复")
        body = card.body()
        self._kw_combo = MacComboBox()
        self._kw_combo.currentTextChanged.connect(self._on_kw_template_changed)
        body.addWidget(self._kw_combo)

        self._kw_summary_label = QLabel("未选择模板")
        self._kw_summary_label.setFont(theme.FONT_CAPTION)
        self._kw_summary_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_SEC}; border: none; background: transparent;"
        )
        body.addWidget(self._kw_summary_label)

        self._kw_preview = QTextBrowser()
        self._kw_preview.setMinimumHeight(120)
        self._kw_preview.setMaximumHeight(180)
        self._apply_kw_preview_style()
        body.addWidget(self._kw_preview)

        # 时长调整 — 左右一半：冷却秒 / 每分钟最多响应
        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(theme.SPACING_SM)

        cd_box = QHBoxLayout()
        cd_box.setSpacing(theme.SPACING_XS)
        cd_label = QLabel("冷却秒")
        cd_label.setFont(theme.FONT_CAPTION)
        cd_box.addWidget(cd_label)
        self._kw_cooldown_edit = MacLineEdit(placeholder="30")
        self._kw_cooldown_edit.setValidator(QIntValidator(0, 36000, self))
        self._kw_cooldown_edit.editingFinished.connect(self._on_kw_related_changed)
        cd_box.addWidget(self._kw_cooldown_edit, stretch=1)
        cfg_row.addLayout(cd_box, stretch=1)

        rt_box = QHBoxLayout()
        rt_box.setSpacing(theme.SPACING_XS)
        rt_label = QLabel("每分钟")
        rt_label.setFont(theme.FONT_CAPTION)
        rt_box.addWidget(rt_label)
        self._kw_rate_edit = MacLineEdit(placeholder="20")
        self._kw_rate_edit.setValidator(QIntValidator(1, 600, self))
        self._kw_rate_edit.editingFinished.connect(self._on_kw_related_changed)
        rt_box.addWidget(self._kw_rate_edit, stretch=1)
        cfg_row.addLayout(rt_box, stretch=1)

        body.addLayout(cfg_row)
        return card

    def _apply_kw_preview_style(self):
        if not hasattr(self, "_kw_preview"):
            return
        self._kw_preview.setStyleSheet(
            f"QTextBrowser {{"
            f"background-color: {theme.CLR_BG_INSET};"
            f"color: {theme.CLR_TEXT_PRI};"
            f"border: 1px solid {theme.CLR_HAIRLINE};"
            f"border-radius: {theme.RADIUS_MD}px;"
            f"padding: 8px 10px;"
            f"}}"
        )

    def _on_kw_related_changed(self):
        try:
            cd = int(self._kw_cooldown_edit.text() or "30")
        except ValueError:
            cd = 30
        try:
            rt = int(self._kw_rate_edit.text() or "20")
        except ValueError:
            rt = 20
        cd = max(0, cd)
        rt = max(1, rt)
        self.keyword_related_settings_changed.emit(cd, rt)

    def set_keyword_related_settings(self, cooldown_sec: int, rate_per_min: int):
        if hasattr(self, "_kw_cooldown_edit"):
            self._kw_cooldown_edit.blockSignals(True)
            self._kw_cooldown_edit.setText(str(int(cooldown_sec)))
            self._kw_cooldown_edit.blockSignals(False)
        if hasattr(self, "_kw_rate_edit"):
            self._kw_rate_edit.blockSignals(True)
            self._kw_rate_edit.setText(str(int(rate_per_min)))
            self._kw_rate_edit.blockSignals(False)

    def get_keyword_template_name(self) -> str:
        if not hasattr(self, "_kw_combo"):
            return ""
        return self._kw_combo.currentText()

    def _on_kw_template_changed(self, name: str):
        if not name:
            return
        self._render_kw_preview(name)
        self.keyword_template_switch_requested.emit(name)

    def _render_kw_preview(self, name: str):
        if not hasattr(self, "_kw_preview"):
            return
        data = _load_settings()
        tmpl = (data.get("keyword_templates") or {}).get(name) or {}
        rules = tmpl.get("rules") or []
        valid_rules = []
        for r in rules:
            kw = (r.get("keyword") or "").strip()
            if kw:
                valid_rules.append(r)
        if not valid_rules:
            if hasattr(self, "_kw_summary_label"):
                self._kw_summary_label.setText("0 条规则 · 语音未开")
            self._kw_preview.setPlainText("（该模板下还没有规则）")
            self.set_keyword_voice_checked(False)
            return
        voice_enabled = self._keyword_template_voice_enabled(name)
        if hasattr(self, "_kw_summary_label"):
            self._kw_summary_label.setText(
                f"{len(valid_rules)} 条规则 · {'语音已开' if voice_enabled else '语音未开'}"
            )
        lines = []
        for index, r in enumerate(valid_rules[:6], start=1):
            kw = (r.get("keyword") or "").strip()
            reply = " ".join((r.get("reply") or "").strip().split())
            lines.append(
                f"{index}. {self._short_text(kw, 12)} → {self._short_text(reply, 34)}"
            )
        remaining = len(valid_rules) - len(lines)
        if remaining > 0:
            lines.append(f"... 还有 {remaining} 条")
        self._kw_preview.setPlainText("\n".join(lines))
        self.set_keyword_voice_checked(voice_enabled)

    @staticmethod
    def _short_text(text: str, limit: int) -> str:
        text = str(text or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)] + "…"

    def _keyword_template_voice_enabled(self, name: str) -> bool:
        data = _load_settings()
        tmpl = (data.get("keyword_templates") or {}).get(name) or {}
        rules = [rule for rule in (tmpl.get("rules") or []) if (rule.get("keyword") or "").strip()]
        return bool(rules) and all(bool(rule.get("generate_voice", False)) for rule in rules)

    def refresh_keyword_card(self, active_template: str = ""):
        if not hasattr(self, "_kw_combo"):
            return
        data = _load_settings()
        names = list((data.get("keyword_templates") or {}).keys())
        self._kw_combo.blockSignals(True)
        self._kw_combo.clear()
        if names:
            self._kw_combo.addItems(names)
            target = active_template if active_template in names else names[0]
            self._kw_combo.setCurrentText(target)
        self._kw_combo.blockSignals(False)
        current = self._kw_combo.currentText()
        if current:
            self._render_kw_preview(current)
        else:
            if hasattr(self, "_kw_summary_label"):
                self._kw_summary_label.setText("未选择模板")
            self._kw_preview.setPlainText("（还没有模板）")
            self.set_keyword_voice_checked(False)

    # ── OBS 联动 card (status + connect btn + rules btn + cooldown) ──

    def _build_obs_card(self) -> MacCard:
        self._obs_connect_btn = MacButton("连接", variant="primary")
        self._obs_connect_btn.setFixedSize(72, 26)
        self._obs_connect_btn.clicked.connect(self._on_obs_connect_clicked)

        card = MacCard(self, title="OBS 联动", accessory=self._obs_connect_btn)
        body = card.body()

        status_row = QHBoxLayout()
        status_row.setSpacing(theme.SPACING_SM)
        status_row.addWidget(QLabel("状态"))
        self._obs_status_label = QLabel("未检测")
        self._obs_status_label.setFont(theme.FONT_BODY)
        status_row.addWidget(self._obs_status_label, stretch=1)
        body.addLayout(status_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(theme.SPACING_SM)
        self._obs_rules_btn = MacButton("配置规则库", variant="secondary")
        self._obs_rules_btn.clicked.connect(self._on_obs_rules_clicked)
        bottom_row.addWidget(self._obs_rules_btn)
        self._obs_cooldown_label = QLabel("可触发")
        self._obs_cooldown_label.setFont(theme.FONT_CAPTION)
        self._obs_cooldown_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom_row.addWidget(self._obs_cooldown_label, stretch=1)
        body.addLayout(bottom_row)

        return card

    def _on_obs_connect_clicked(self):
        settings = _load_settings().get("obs_actions") or {}
        self.obs_status_check_requested.emit(settings)

    def _on_obs_rules_clicked(self):
        from ui_dialogs.obsrulesmanagerdialog import ObsRulesManagerDialog
        data = _load_settings()
        obs_settings = ObsActionSettings.from_dict(data.get("obs_actions"))
        dialog = ObsRulesManagerDialog(list(obs_settings.rules), parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        new_rules = dialog.get_rules()
        # Preserve all non-rule OBS fields — replace rules only.
        updated = ObsActionSettings(
            enabled=obs_settings.enabled,
            host=obs_settings.host,
            port=obs_settings.port,
            password=obs_settings.password,
            main_scene=obs_settings.main_scene,
            ignore_during_playback=obs_settings.ignore_during_playback,
            global_cooldown_sec=obs_settings.global_cooldown_sec,
            match_window_sec=obs_settings.match_window_sec,
            min_hits=obs_settings.min_hits,
            rules=tuple(new_rules),
        )
        data["obs_actions"] = updated.to_dict()
        _save_settings(data)
        self.obs_settings_changed.emit(updated.to_dict())

    def attach_worker(self, worker):
        """Hold a worker ref so the cooldown tick can poll the OBS controller.

        Why: the OBS controller's global_cooldown_until is an asyncio-loop-side
        float that we want to surface as a countdown on the home card. A 1s
        QTimer on the Qt main thread reads it directly (lock-free atomic float).
        """
        self._worker = worker
        if self._cooldown_timer is None:
            self._cooldown_timer = QTimer(self)
            self._cooldown_timer.setInterval(1000)
            self._cooldown_timer.timeout.connect(self._tick_cooldown)
            self._cooldown_timer.start()

    def _tick_cooldown(self):
        if not hasattr(self, "_obs_cooldown_label"):
            return
        controller = getattr(self._worker, "_obs_controller", None) if self._worker else None
        if controller is None:
            self._obs_cooldown_label.setText("未启用")
            return
        remaining = controller.cooldown_remaining()
        if remaining <= 0:
            self._obs_cooldown_label.setText("可触发")
        else:
            self._obs_cooldown_label.setText(f"冷却 {remaining:.0f}s")

    # ── Quick action card ─────────────────────────────────

    def _build_quick_card(self) -> MacCard:
        card = MacCard(self, title="快捷操作")
        body = card.body()
        body.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._ai_reply_btn = self._make_toggle_button(
            "开启AI助播回复",
            "关闭AI助播回复",
            self._on_ai_reply_toggled,
            "ic_fluent_brain_circuit_filled",
        )
        self._ai_voice_btn = self._make_toggle_button(
            "开启AI助播语音",
            "关闭AI助播语音",
            self._on_ai_voice_toggled,
            "ic_fluent_mic_sparkle_filled",
        )
        self._keyword_auto_reply_btn = self._make_toggle_button(
            "开启关键词自动回复",
            "关闭关键词回复",
            self._on_keyword_pill_toggled,
            "ic_fluent_comment_filled",
        )
        self._keyword_voice_quick_btn = self._make_toggle_button(
            "开启关键词语音",
            "关闭关键词语音",
            self._on_keyword_voice_toggled,
            "ic_fluent_speaker_2_filled",
        )

        grid.addWidget(self._ai_reply_btn, 0, 0)
        grid.addWidget(self._ai_voice_btn, 0, 1)
        grid.addWidget(self._keyword_auto_reply_btn, 1, 0)
        grid.addWidget(self._keyword_voice_quick_btn, 1, 1)
        body.addLayout(grid)

        obs_row = QHBoxLayout()
        obs_row.setSpacing(theme.SPACING_SM)
        self._obs_connect_btn = MacButton("连接OBS", variant="secondary")
        self._obs_connect_btn.setIcon(_fluent_icon("ic_fluent_tv_arrow_right_filled", 16))
        self._obs_connect_btn.setIconSize(QSize(16, 16))
        self._obs_connect_btn.setFixedHeight(34)
        self._obs_connect_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._obs_connect_btn.clicked.connect(self._on_obs_connect_clicked)
        obs_row.addWidget(self._obs_connect_btn, stretch=1)
        self._obs_status_dot = QLabel()
        self._obs_status_dot.setFixedSize(10, 10)
        obs_row.addWidget(self._obs_status_dot)
        body.addLayout(obs_row)
        self._set_obs_connected(False)
        return card

    def _make_toggle_button(
        self,
        off_text: str,
        on_text: str,
        handler,
        icon_name: str = "",
    ) -> MacButton:
        btn = MacButton(off_text, variant="pill")
        btn.setCheckable(True)
        btn.setFixedHeight(34)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if icon_name:
            btn.setIcon(_fluent_icon(icon_name, 16))
            btn.setIconSize(QSize(16, 16))
        btn._off_text = off_text
        btn._on_text = on_text
        btn.toggled.connect(handler)
        return btn

    def _set_toggle_text(self, btn: MacButton, checked: bool, off_text: str, on_text: str):
        btn.setText(on_text if checked else off_text)

    def _set_checkable_button(self, btn: MacButton, checked: bool, off_text: str, on_text: str):
        btn.blockSignals(True)
        try:
            btn.setChecked(bool(checked))
            self._set_toggle_text(btn, bool(checked), off_text, on_text)
        finally:
            btn.blockSignals(False)

    def _on_ai_reply_toggled(self, checked: bool):
        self._set_toggle_text(
            self._ai_reply_btn,
            bool(checked),
            "开启AI助播回复",
            "关闭AI助播回复",
        )
        self.ai_assistant_reply_toggled.emit(bool(checked))

    def _on_ai_voice_toggled(self, checked: bool):
        self._set_toggle_text(
            self._ai_voice_btn,
            bool(checked),
            "开启AI助播语音",
            "关闭AI助播语音",
        )
        self.ai_assistant_voice_toggled.emit(bool(checked))

    def _on_keyword_pill_toggled(self, checked: bool):
        self._set_toggle_text(
            self._keyword_auto_reply_btn,
            bool(checked),
            "开启关键词自动回复",
            "关闭关键词回复",
        )
        self.keyword_auto_reply_toggled.emit(bool(checked))

    def _on_keyword_voice_toggled(self, checked: bool):
        self.set_keyword_voice_checked(bool(checked))
        self.keyword_voice_toggled.emit(bool(checked))

    def set_ai_assistant_reply_checked(self, checked: bool):
        if hasattr(self, "_ai_reply_btn"):
            self._set_checkable_button(
                self._ai_reply_btn,
                bool(checked),
                "开启AI助播回复",
                "关闭AI助播回复",
            )

    def set_ai_assistant_voice_checked(self, checked: bool):
        if hasattr(self, "_ai_voice_btn"):
            self._set_checkable_button(
                self._ai_voice_btn,
                bool(checked),
                "开启AI助播语音",
                "关闭AI助播语音",
            )

    def set_keyword_auto_reply_checked(self, checked: bool):
        if hasattr(self, "_keyword_auto_reply_btn"):
            self._set_checkable_button(
                self._keyword_auto_reply_btn,
                bool(checked),
                "开启关键词自动回复",
                "关闭关键词回复",
            )

    def set_keyword_voice_checked(self, checked: bool):
        for attr in ("_keyword_voice_quick_btn",):
            btn = getattr(self, attr, None)
            if btn is None:
                continue
            self._set_checkable_button(
                btn,
                bool(checked),
                "开启关键词语音",
                "关闭关键词语音",
            )

    # ── Audio device card ─────────────────────────────────

    def _build_audio_card(self) -> MacCard:
        card = MacCard(self, title="音频设备")
        body = card.body()

        row = QHBoxLayout()
        row.setSpacing(theme.SPACING_MD)
        row.addStretch(1)

        # Mic
        mic_col = QVBoxLayout()
        mic_col.setAlignment(Qt.AlignCenter)
        self._mic_icon = _VolumeIcon("ic_fluent_mic_filled", mode="input", parent=self)
        mic_col.addWidget(self._mic_icon, alignment=Qt.AlignCenter)
        self._mic_combo = MacComboBox()
        self._mic_combo.setMinimumWidth(180)
        self._mic_combo.currentIndexChanged.connect(
            lambda idx: self._on_device_combo_changed("in", idx))
        self._mic_combo.view().entered.connect(
            lambda i: QToolTip.showText(
                self._mic_combo.view().viewport().mapToGlobal(
                    self._mic_combo.view().visualRect(i).bottomLeft()),
                self._mic_combo.itemText(i.row()), self._mic_combo))
        mic_col.addWidget(self._mic_combo)
        row.addLayout(mic_col)

        row.addStretch(2)

        # Speaker
        spk_col = QVBoxLayout()
        spk_col.setAlignment(Qt.AlignCenter)
        self._spk_icon = _VolumeIcon("ic_fluent_desktop_speaker_filled", mode="loopback", parent=self)
        spk_col.addWidget(self._spk_icon, alignment=Qt.AlignCenter)
        self._spk_combo = MacComboBox()
        self._spk_combo.setMinimumWidth(180)
        self._spk_combo.currentIndexChanged.connect(
            lambda idx: self._on_device_combo_changed("out", idx))
        self._spk_combo.view().entered.connect(
            lambda i: QToolTip.showText(
                self._spk_combo.view().viewport().mapToGlobal(
                    self._spk_combo.view().visualRect(i).bottomLeft()),
                self._spk_combo.itemText(i.row()), self._spk_combo))
        spk_col.addWidget(self._spk_combo)
        row.addLayout(spk_col)

        row.addStretch(1)
        body.addLayout(row)
        return card

    # ── Audio device enumeration ──────────────────────────

    def _install_hotplug_watch(self):
        """订阅 Windows 设备插拔事件，自动刷新音频设备列表。"""
        self._hotplug_timer = QTimer(self)
        self._hotplug_timer.setSingleShot(True)
        self._hotplug_timer.setInterval(250)
        self._hotplug_timer.timeout.connect(self._on_audio_devices_hotplug)

        self._hotplug_filter = _DeviceChangeFilter(self._hotplug_timer.start)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._hotplug_filter)

    def _on_audio_devices_hotplug(self):
        """设备变更后重新枚举，并尽量按名称保持原选中。"""
        in_name = self._mic_combo.currentText() if self._mic_combo.count() else None
        out_name = self._spk_combo.currentText() if self._spk_combo.count() else None
        self._load_audio_devices(preferred_in_name=in_name,
                                  preferred_out_name=out_name)

    def _load_audio_devices(self, preferred_in_name: str | None = None,
                             preferred_out_name: str | None = None):
        """Enumerate audio devices with multiple fallback strategies.

        Order: 1) pyaudio (MME → fallback to all hostApis), 2) PyQt5 QtMultimedia,
        3) sounddevice, 4) Windows powershell. Always shows something.

        preferred_*_name 用于热插拔刷新时按名称保持原选中。
        """
        in_devices, out_devices, default_in_idx, default_out_idx = \
            self._enum_via_pyaudio()
        if not in_devices and not out_devices:
            in_devices, out_devices = self._enum_via_qt_multimedia()
            default_in_idx = default_out_idx = None
        if not in_devices and not out_devices:
            in_devices, out_devices = self._enum_via_sounddevice()
            default_in_idx = default_out_idx = None

        self._fill_device_combo(self._mic_combo, in_devices, default_in_idx,
                                 attr="_selected_in_index",
                                 preferred_name=preferred_in_name)
        self._fill_device_combo(self._spk_combo, out_devices, default_out_idx,
                                 attr="_selected_out_index",
                                 preferred_name=preferred_out_name)
        # 设备枚举走的是 blockSignals，combo 的 currentIndexChanged 不会触发 —
        # 这里手动把选中的设备 idx 推给音量图标，让它们开流。
        if hasattr(self, "_mic_icon"):
            self._mic_icon.set_device(self._selected_in_index)
        if hasattr(self, "_spk_icon"):
            self._spk_icon.set_device(self._selected_out_index)

    def _enum_via_pyaudio(self):
        """Returns (in_list, out_list, default_in_idx, default_out_idx).

        优先 WASAPI（Windows 真实设备名）→ DirectSound → MME。
        过滤 "Microsoft 声音映射器" 这类 MME 虚拟聚合设备。
        Empty lists if pyaudio missing or fails.
        """
        try:
            import pyaudio
        except ImportError:
            return [], [], None, None
        try:
            p = pyaudio.PyAudio()
        except Exception:
            return [], [], None, None
        try:
            host_info = self._pick_preferred_host(p)
            preferred_host = host_info["index"] if host_info else None
            # 默认设备从所选 host API 内部拿，避免回退到 MME 的虚拟 Sound Mapper
            default_in_idx = None
            default_out_idx = None
            if host_info is not None:
                in_def = host_info.get("defaultInputDevice", -1)
                out_def = host_info.get("defaultOutputDevice", -1)
                default_in_idx = in_def if in_def not in (-1, None) else None
                default_out_idx = out_def if out_def not in (-1, None) else None

            in_devices: list[dict] = []
            out_devices: list[dict] = []
            in_devices_all: list[dict] = []
            out_devices_all: list[dict] = []
            for i in range(p.get_device_count()):
                try:
                    info = p.get_device_info_by_index(i)
                except Exception:
                    continue
                name = str(info.get("name", f"设备 {i}"))
                if self._is_virtual_device(name):
                    continue
                host_api = info.get("hostApi", -1)
                if info.get("maxInputChannels", 0) > 0:
                    entry = {"index": i, "name": name}
                    in_devices_all.append(entry)
                    if host_api == preferred_host:
                        in_devices.append(entry)
                if info.get("maxOutputChannels", 0) > 0:
                    entry = {"index": i, "name": name}
                    out_devices_all.append(entry)
                    if host_api == preferred_host:
                        out_devices.append(entry)
            # 偏好 host API 没有设备时，回退到所有 host API（已过滤虚拟项）
            if not in_devices:
                in_devices = in_devices_all
            if not out_devices:
                out_devices = out_devices_all
            return in_devices, out_devices, default_in_idx, default_out_idx
        finally:
            try:
                p.terminate()
            except Exception:
                pass

    @staticmethod
    def _pick_preferred_host(p):
        """按 WASAPI > DirectSound > MME 顺序选 host API。
        匹配 name 或 PortAudio type id 任一即可，避免不同 PyAudio 版本字符串差异。
        """
        # PortAudio host API type IDs: WASAPI=13, DirectSound=1, MME=2
        priorities = [
            ({"Windows WASAPI", "WASAPI"}, {13}),
            ({"Windows DirectSound", "DirectSound"}, {1}),
            ({"MME"}, {2}),
        ]
        hosts = []
        for h in range(p.get_host_api_count()):
            try:
                hosts.append(p.get_host_api_info_by_index(h))
            except Exception:
                continue
        for names, type_ids in priorities:
            for info in hosts:
                if info.get("name") in names or info.get("type") in type_ids:
                    return info
        return None

    @staticmethod
    def _is_virtual_device(name: str) -> bool:
        """过滤 PortAudio 的虚拟聚合设备（Microsoft Sound Mapper 等）。"""
        if not name:
            return True
        lower = name.lower()
        return ("sound mapper" in lower
                or "声音映射" in name
                or "primary sound" in lower)

    def _enum_via_qt_multimedia(self):
        """PyQt5 QtMultimedia fallback. No device index needed by name only."""
        try:
            from PyQt5.QtMultimedia import QAudioDeviceInfo, QAudio
        except ImportError:
            return [], []
        in_devices = [{"index": -1, "name": d.deviceName()}
                      for d in QAudioDeviceInfo.availableDevices(QAudio.AudioInput)]
        out_devices = [{"index": -1, "name": d.deviceName()}
                       for d in QAudioDeviceInfo.availableDevices(QAudio.AudioOutput)]
        return in_devices, out_devices

    def _enum_via_sounddevice(self):
        """sounddevice fallback."""
        try:
            import sounddevice as sd
        except ImportError:
            return [], []
        try:
            devices = sd.query_devices()
        except Exception:
            return [], []
        in_devices = []
        out_devices = []
        for i, info in enumerate(devices):
            name = str(info.get("name", f"设备 {i}"))
            if info.get("max_input_channels", 0) > 0:
                in_devices.append({"index": i, "name": name})
            if info.get("max_output_channels", 0) > 0:
                out_devices.append({"index": i, "name": name})
        return in_devices, out_devices

    def _fill_device_combo(self, combo, devices, default_idx, attr,
                            preferred_name: str | None = None):
        combo.blockSignals(True)
        combo.clear()
        if not devices:
            combo.addItem("未检测到设备")
            combo.blockSignals(False)
            return
        for d in devices:
            combo.addItem(d["name"], d["index"])
        selected = False
        # 1) 优先按名称匹配 — 热插拔刷新时保留原选中
        if preferred_name:
            for k in range(combo.count()):
                if combo.itemText(k) == preferred_name:
                    combo.setCurrentIndex(k)
                    setattr(self, attr, combo.itemData(k))
                    selected = True
                    break
        # 2) 然后系统默认设备
        if not selected and default_idx is not None:
            for k in range(combo.count()):
                if combo.itemData(k) == default_idx:
                    combo.setCurrentIndex(k)
                    setattr(self, attr, default_idx)
                    selected = True
                    break
        # 3) 兜底：第一个
        if not selected:
            setattr(self, attr, combo.itemData(0))
        combo.blockSignals(False)
        combo.setToolTip(combo.currentText())

    def _on_device_combo_changed(self, io_type: str, idx: int):
        combo = self._mic_combo if io_type == "in" else self._spk_combo
        device_index = combo.itemData(idx)
        if device_index is not None:
            if io_type == "in":
                self._selected_in_index = device_index
                if hasattr(self, "_mic_icon"):
                    self._mic_icon.set_device(device_index)
            else:
                self._selected_out_index = device_index
                if hasattr(self, "_spk_icon"):
                    self._spk_icon.set_device(device_index)

    # ── OBS state sync (push from worker) ─────────────────

    def update_obs_state(self, payload: object):
        if not isinstance(payload, dict):
            return
        state = str(payload.get("state", "")).strip()
        connected = state == "connected"
        if hasattr(self, "_obs_connect_btn"):
            self._obs_connect_btn.setText("OBS已连接" if connected else "连接OBS")
            self._obs_connect_btn.setEnabled(not connected)
        self._set_obs_connected(connected)

    def _set_obs_connected(self, connected: bool):
        if not hasattr(self, "_obs_status_dot"):
            return
        color = theme.CLR_GREEN if connected else theme.CLR_TEXT_TERT
        self._obs_status_dot.setStyleSheet(
            "QLabel {"
            f"background-color: {color};"
            "border: none;"
            f"border-radius: {theme.RADIUS_SM}px;"
            "}"
        )

    # ── Theme hot-switch ──────────────────────────────────

    def _apply_theme_styles(self):
        """Re-apply theme-dependent styles. Called by AiszrApp._refresh_theme_styles."""
        # Hero title — needs CLR_TEXT_PRI, not the global QLabel default (text_sec)
        if hasattr(self, "_hero_title"):
            self._apply_hero_styles()
        if hasattr(self, "_kw_summary_label"):
            self._kw_summary_label.setStyleSheet(
                f"color: {theme.CLR_TEXT_SEC}; border: none; background: transparent;"
            )
        self._apply_kw_preview_style()
        # Propagate to every Mac* component. Some widgets (e.g. DanmakuDisplay)
        # use the underscored `_apply_theme_styles` convention — accept either.
        for w in self.findChildren(QWidget):
            fn = getattr(w, "apply_theme_styles", None) or getattr(w, "_apply_theme_styles", None)
            if callable(fn):
                fn()

    # ── Backward-compat stubs ─────────────────────────────

    def update_login_state(self, state): pass
    def update_connection_state(self, state): pass
    def update_capture_state(self, state, msg): pass
    def update_ai_state(self, payload): pass
    def update_voice_state(self, payload): pass
    def update_dh_state(self, payload):
        if not isinstance(payload, dict) or not hasattr(self, "_stream_state_label"):
            return
        message = str(payload.get("message", "") or "").strip()
        state = str(payload.get("state", "") or "").strip()
        text = message or state or "空闲"
        self._stream_state_label.setText(f"推流：{text}")

    def update_streaming_assets_state(self, payload):
        if not isinstance(payload, dict) or not hasattr(self, "_stream_asset_label"):
            return
        count = int(payload.get("video_count") or 0)
        max_count = int(payload.get("max_video_count") or 6)
        ready = bool(payload.get("avatar_ready"))
        status = str(payload.get("avatar_status") or "")
        stage = str(payload.get("avatar_stage") or "")
        progress = int(payload.get("avatar_progress") or 0)
        name = str(payload.get("avatar_display_name") or "").strip()
        quality = str(payload.get("avatar_quality") or "").strip()
        error = str(payload.get("avatar_error") or "").strip()

        if count <= 0:
            text = f"主播形象：未选择（0/{max_count}）"
        elif ready:
            suffix = f" · {quality}" if quality else ""
            display = f" · {name}" if name else ""
            text = f"主播形象：可推流{suffix}{display}（{count}/{max_count}）"
        elif status == "FAILED":
            text = f"主播形象：处理失败（{count}/{max_count}）"
            if error:
                text += f" {error}"
        else:
            progress_text = f" {progress}%" if progress else ""
            text = f"主播形象：{stage or '处理中'}{progress_text}（{count}/{max_count}）"
        self._stream_asset_label.setText(text)
        if hasattr(self, "_home_stream_start_btn"):
            self._home_stream_start_btn.setEnabled(ready)
            self._home_stream_start_btn.setToolTip("" if ready else "主播形象处理完成后可推流")

    def update_dashboard_metrics(self, snap): pass
    def update_uptime_start(self, state): pass
    def append_activity(self, msg): pass
    def append_ai_activity(self, user, msg, reply): pass
