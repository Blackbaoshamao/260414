"""Theme system: colors, fonts, stylesheet factories — extracted from ui.py."""

import sys
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush, QIcon
from PyQt5.QtCore import Qt, QPointF, QPropertyAnimation, QEasingCurve, QRect, QSize, \
    pyqtProperty
from PyQt5.QtWidgets import QApplication, QLineEdit, QWidget, QAbstractButton
from siui.core import SiColor, SiGlobal


# ---------------------------------------------------------------------------
# iOS-style toggle switch
# ---------------------------------------------------------------------------


class _iOSToggle(QAbstractButton):
    _ON_COLOR = "#34C759"
    _OFF_COLOR = "#E9E9EB"
    _OFF_COLOR_DARK = "#39393D"
    _THUMB_COLOR = "#FFFFFF"

    def __init__(self, parent=None, *, width: int = 48, height: int = 28):
        super().__init__(parent)
        self.setCheckable(True)
        self._toggle_w = max(28, int(width))
        self._toggle_h = max(18, int(height))
        self._pad = max(2, round(self._toggle_h * 0.11))
        self._thumb_r = max(7, (self._toggle_h - self._pad * 2) // 2)
        self.setFixedSize(self._toggle_w, self._toggle_h)
        self._thumb_x = float(self._off_thumb_x())
        self._thumb_anim = QPropertyAnimation(self, b"_anim_thumb_x")
        self._thumb_anim.setDuration(250)
        self._thumb_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)
        self._update_thumb_pos(self._off_thumb_x())

    def get_anim_thumb_x(self):
        return self._thumb_x

    def set_anim_thumb_x(self, v):
        self._thumb_x = v
        self.update()

    _anim_thumb_x = pyqtProperty(float, get_anim_thumb_x, set_anim_thumb_x)

    def _animate(self, checked):
        self._thumb_anim.setStartValue(self._thumb_x)
        self._thumb_anim.setEndValue(float(self._on_thumb_x() if checked else self._off_thumb_x()))
        self._thumb_anim.start()

    def _update_thumb_pos(self, pos):
        self._thumb_x = float(pos)
        self.update()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._thumb_x = float(self._on_thumb_x() if checked else self._off_thumb_x())
        self.update()

    def _off_thumb_x(self) -> int:
        return self._pad

    def _on_thumb_x(self) -> int:
        return self._toggle_w - self._pad - (self._thumb_r * 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_on = self.isChecked()
        bg = QColor(self._ON_COLOR if is_on else self._OFF_COLOR_DARK)
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(
            0,
            0,
            self._toggle_w,
            self._toggle_h,
            self._toggle_h // 2,
            self._toggle_h // 2,
        )

        thumb_r = self._thumb_r
        cx = self._thumb_x + thumb_r
        cy = self._toggle_h / 2
        p.setBrush(QBrush(QColor(self._THUMB_COLOR)))
        thumb_shadow = QColor(0, 0, 0, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx + 1, cy + 1), thumb_r, thumb_r)

        p.setBrush(QBrush(QColor(self._THUMB_COLOR)))
        p.drawEllipse(QPointF(cx, cy), thumb_r, thumb_r)
        p.end()

    def hitButton(self, pos):
        return self.rect().contains(pos)



SiSwitch = _iOSToggle


# ---------------------------------------------------------------------------
# Color & font constants (macOS Sonoma)
# ---------------------------------------------------------------------------

# Spacing & radius scale tuned for a restrained iOS control-center feel.
RADIUS_SM, RADIUS_MD, RADIUS_LG, RADIUS_XL = 5, 7, 8, 12
SPACING_XS, SPACING_SM, SPACING_MD, SPACING_LG, SPACING_XL = 4, 8, 12, 18, 28

# ARGB notation #AARRGGBB. bg_vibrancy α=184 (72%), hairline α=23 (~9%), selected_bg α=46 (~18%)
_THEMES = {
    "moonstone": {
        "bg": "#F4F5F7", "bg_elevated": "#FAFAFC", "bg_card": "#FFFFFF",
        "bg_inset": "#EEF0F4", "border": "#D9DCE2", "input_bg": "#FFFFFF", "is_light": True,
        "text_pri": "#000000", "text_sec": "#6C6C70", "text_tert": "#AEAEB2",
        "msg_chat": "#1C1C1E", "msg_gift": "#FF9500", "msg_like": "#FF2D55",
        "msg_follow": "#AF52DE", "accent": "#007AFF", "accent_light": "#5AC8FA",
        "accent_text": "#FFFFFF", "green": "#34C759", "red": "#FF3B30", "yellow": "#FFCC00",
        "bg_vibrancy": "#E6FFFFFF", "hairline": "#12000000", "selected_bg": "#1F007AFF",
    },
}

_THEME_MAP = ["moonstone"]
_THEME_LABELS = ["\u6708\u5149"]
_DEFAULT_THEME = "moonstone"
_current_theme = _DEFAULT_THEME
_APP_ICON_NAME = "ic_fluent_camera_sparkles_filled"
_THEME_SATURATION_BOOST = 1.00
_SATURATION_BOOST_KEYS = (
    "bg",
    "bg_elevated",
    "bg_card",
    "bg_inset",
    "border",
    "accent",
    "accent_light",
    "msg_gift",
    "msg_like",
    "msg_follow",
    "green",
    "red",
    "yellow",
)


def _boost_hex_saturation(color_code: str, factor: float) -> str:
    if factor == 1.0:
        return color_code  # avoid HSL roundtrip rounding when boost is identity
    color = QColor(color_code)
    if not color.isValid():
        return color_code

    hue, saturation, lightness, alpha = color.getHsl()
    if saturation <= 0:
        return color_code

    boosted = QColor()
    boosted.setHsl(hue, min(255, round(saturation * factor)), lightness, alpha)
    return boosted.name(QColor.HexRgb)


def _tune_theme(theme: dict) -> dict:
    tuned = dict(theme)
    for key in _SATURATION_BOOST_KEYS:
        value = tuned.get(key)
        if isinstance(value, str) and value.startswith("#"):
            tuned[key] = _boost_hex_saturation(value, _THEME_SATURATION_BOOST)
    return tuned


def _mix_hex_colors(color_a: str, color_b: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    qa = QColor(color_a)
    qb = QColor(color_b)
    if not qa.isValid() or not qb.isValid():
        return color_a

    mixed = QColor(
        round(qa.red() + (qb.red() - qa.red()) * amount),
        round(qa.green() + (qb.green() - qa.green()) * amount),
        round(qa.blue() + (qb.blue() - qa.blue()) * amount),
        round(qa.alpha() + (qb.alpha() - qa.alpha()) * amount),
    )
    return mixed.name(QColor.HexRgb)


def _hex_with_alpha(color_code: str, alpha: int) -> str:
    color = QColor(color_code)
    if not color.isValid():
        return color_code
    color.setAlpha(max(0, min(255, alpha)))
    return color.name(QColor.HexArgb)


def _arrow_svg_file(direction: str, color: str) -> str:
    """Write a 12x12 triangle arrow SVG to OS temp dir; return Qt-friendly path.

    Qt 5.15 stylesheet `image: url(...)` does not reliably support data: URIs,
    so we materialise the SVG on disk once per (direction, color) combo and
    let the stylesheet reference it by absolute path. The file is cached —
    subsequent calls with the same args reuse the existing file.

    Returns forward-slash absolute path that Qt accepts on Windows.
    """
    import os
    import tempfile
    paths = {
        "down":  "M2 4 L6 8 L10 4 Z",
        "up":    "M2 8 L6 4 L10 8 Z",
        "left":  "M8 2 L4 6 L8 10 Z",
        "right": "M4 2 L8 6 L4 10 Z",
    }
    path_d = paths.get(direction, paths["down"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12" '
           f'fill="{color}"><path d="{path_d}"/></svg>')
    color_safe = color.lstrip("#")
    out_dir = os.path.join(tempfile.gettempdir(), "aiszr_arrows")
    os.makedirs(out_dir, exist_ok=True)
    fpath = os.path.join(out_dir, f"arrow_{direction}_{color_safe}.svg")
    if not os.path.exists(fpath):
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(svg)
    return fpath.replace("\\", "/")


def _placeholder_h(height: int) -> QWidget:
    """Return a transparent spacer widget of the given pixel height."""
    w = QWidget()
    w.setFixedHeight(height)
    w.setStyleSheet("background: transparent;")
    return w


def _update_siui_color_group(theme: dict):
    from siui.gui.color_group.bright import BrightColorGroup
    from siui.gui.color_group.dark import DarkColorGroup

    base_group = BrightColorGroup() if theme.get("is_light") else DarkColorGroup()
    current_group = getattr(SiGlobal.siui, "colors", None)

    if current_group is None:
        SiGlobal.siui.colors = base_group
        current_group = SiGlobal.siui.colors
    else:
        current_group.colors = dict(base_group.colors)

    bg = theme["bg"]
    bg_elevated = theme["bg_elevated"]
    bg_card = theme["bg_card"]
    border = theme["border"]
    input_bg = theme["input_bg"]
    text_pri = theme["text_pri"]
    text_sec = theme["text_sec"]
    text_tert = theme["text_tert"]
    accent = theme["accent"]
    accent_light = theme["accent_light"]
    accent_text = theme["accent_text"]

    current_group.assign(SiColor.THEME, accent)
    current_group.assign(SiColor.THEME_TRANSITION_A, accent)
    current_group.assign(SiColor.THEME_TRANSITION_B, accent_light)
    current_group.assign(SiColor.SVG_THEME, accent)
    current_group.assign(SiColor.TOOLTIP_BG, SiColor.trans(bg_card, 0.94))

    current_group.assign(SiColor.INTERFACE_BG_A, bg)
    current_group.assign(SiColor.INTERFACE_BG_B, bg_elevated)
    current_group.assign(SiColor.INTERFACE_BG_C, bg_card)
    current_group.assign(SiColor.INTERFACE_BG_D, border)
    if theme.get("is_light"):
        current_group.assign(SiColor.INTERFACE_BG_E, input_bg)
    else:
        current_group.assign(SiColor.INTERFACE_BG_E, SiColor.mix(border, text_pri, 0.85))

    current_group.assign(SiColor.TEXT_A, text_pri)
    current_group.assign(SiColor.TEXT_B, text_pri)
    current_group.assign(SiColor.TEXT_C, text_sec)
    current_group.assign(SiColor.TEXT_D, text_tert)
    current_group.assign(SiColor.TEXT_E, SiColor.mix(text_tert, bg, 0.8))
    current_group.assign(SiColor.TEXT_THEME, accent if theme.get("is_light") else accent_light)

    current_group.assign(SiColor.SIDE_MSG_THEME_NORMAL, bg_elevated)
    current_group.assign(SiColor.SIDE_MSG_THEME_INFO, accent)
    current_group.assign(SiColor.SIDE_MSG_THEME_WARNING, theme["yellow"])
    current_group.assign(SiColor.SIDE_MSG_THEME_ERROR, theme["red"])
    current_group.assign(SiColor.SIDE_MSG_THEME_SUCCESS, theme["green"])

    current_group.assign(SiColor.MENU_BG, bg_card)
    current_group.assign(SiColor.TITLE_INDICATOR, accent if theme.get("is_light") else accent_light)
    current_group.assign(SiColor.TITLE_HIGHLIGHT, SiColor.mix(accent, bg, 0.25))

    current_group.assign(SiColor.BUTTON_PANEL, bg_elevated)
    current_group.assign(SiColor.BUTTON_SHADOW, SiColor.mix(bg_card, "#000000", 0.9))
    current_group.assign(SiColor.BUTTON_THEMED_BG_A, accent)
    current_group.assign(SiColor.BUTTON_THEMED_BG_B, accent_light)
    current_group.assign(SiColor.BUTTON_THEMED_SHADOW_A, SiColor.mix(accent, "#000000", 0.75))
    current_group.assign(SiColor.BUTTON_THEMED_SHADOW_B, SiColor.mix(accent_light, "#000000", 0.75))
    current_group.assign(SiColor.BUTTON_ON, accent)
    current_group.assign(SiColor.BUTTON_OFF, accent_light)
    current_group.assign(SiColor.BUTTON_TEXT_BUTTON_IDLE, accent if theme.get("is_light") else accent_light)
    current_group.assign(SiColor.BUTTON_TEXT_BUTTON_FLASH, accent if theme.get("is_light") else accent_light)
    current_group.assign(SiColor.BUTTON_TEXT_BUTTON_HOVER, accent_text)

    current_group.assign(SiColor.RADIO_BUTTON_CHECKED, accent)
    current_group.assign(SiColor.CHECKBOX_CHECKED, accent)
    current_group.assign(SiColor.SWITCH_ACTIVATE, accent)
    current_group.assign(SiColor.SCROLL_BAR, SiColor.trans(text_pri, 0.3))
    current_group.assign(SiColor.PROGRESS_BAR_TRACK, bg_elevated)
    current_group.assign(SiColor.PROGRESS_BAR_PROCESSING, accent_light)
    current_group.assign(SiColor.PROGRESS_BAR_COMPLETING, theme["yellow"])


def _apply_qt_global_theme():
    app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(f"""
        /* Global font baseline — propagates to every stylesheet-rendered widget.
           Without this rule, widgets that have their own stylesheet (e.g.
           color/border declarations) lose the application-level QFont family. */
        * {{
            font-family: {UI_FONT_STACK_CSS};
        }}
        QLabel {{
            color: {CLR_TEXT_SEC};
            background: transparent;
        }}
        QLabel:disabled {{
            color: {CLR_TEXT_TERT};
        }}
        QCheckBox, QRadioButton, QGroupBox {{
            color: {CLR_TEXT_PRI};
            background: transparent;
        }}
        QCheckBox:disabled, QRadioButton:disabled, QGroupBox:disabled {{
            color: {CLR_TEXT_TERT};
        }}
        /* Default text-area chrome — pages that need a different look
           override via widget-level setStyleSheet (more specific wins). */
        QTextEdit, QTextBrowser {{
            background-color: {CLR_BG_ELEVATED};
            color: {CLR_TEXT_PRI};
            border: 1px solid {CLR_BORDER};
            border-radius: 8px;
            padding: 6px 10px;
            selection-background-color: {_hex_with_alpha(CLR_ACCENT_LIGHT, 172)};
            selection-color: {CLR_ACCENT_TEXT};
        }}
        QTextEdit:focus, QTextBrowser:focus {{
            border-color: {_mix_hex_colors(CLR_ACCENT, CLR_ACCENT_LIGHT, 0.42)};
        }}
        QToolTip {{
            color: {CLR_TEXT_PRI};
            background-color: {CLR_BG_CARD};
            border: 1px solid {CLR_BORDER};
        }}
        QMenu {{
            color: {CLR_TEXT_PRI};
            background-color: {CLR_BG_CARD};
            border: 1px solid {CLR_BORDER};
        }}
        QMenu::item:selected {{
            color: {CLR_ACCENT_TEXT};
            background-color: {CLR_ACCENT};
        }}
        QPushButton:focus {{
            outline: none;
        }}
        QMessageBox {{
            background-color: {CLR_BG};
            color: {CLR_TEXT_PRI};
        }}
        QMessageBox QLabel {{
            color: {CLR_TEXT_PRI};
        }}
        /* macOS Sonoma-style thin scrollbar */
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {_hex_with_alpha(CLR_TEXT_TERT, 110)};
            min-height: 28px;
            border-radius: 4px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {_hex_with_alpha(CLR_TEXT_SEC, 160)};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            background: transparent;
            border: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 0;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {_hex_with_alpha(CLR_TEXT_TERT, 110)};
            min-width: 28px;
            border-radius: 4px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {_hex_with_alpha(CLR_TEXT_SEC, 160)};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
            background: transparent;
            border: none;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
    """)


_CLR_KEY_MAP = [
    ("CLR_BG", "bg"), ("CLR_BG_ELEVATED", "bg_elevated"),
    ("CLR_BG_CARD", "bg_card"), ("CLR_BG_INSET", "bg_inset"),
    ("CLR_BORDER", "border"), ("CLR_INPUT_BG", "input_bg"),
    ("CLR_TEXT_PRI", "text_pri"), ("CLR_TEXT_SEC", "text_sec"),
    ("CLR_TEXT_TERT", "text_tert"),
    ("CLR_MSG_CHAT", "msg_chat"), ("CLR_MSG_GIFT", "msg_gift"),
    ("CLR_MSG_LIKE", "msg_like"), ("CLR_MSG_FOLLOW", "msg_follow"),
    ("CLR_ACCENT", "accent"), ("CLR_ACCENT_LIGHT", "accent_light"),
    ("CLR_ACCENT_TEXT", "accent_text"),
    ("CLR_GREEN", "green"), ("CLR_RED", "red"), ("CLR_YELLOW", "yellow"),
    ("CLR_BG_VIBRANCY", "bg_vibrancy"),
    ("CLR_HAIRLINE", "hairline"),
    ("CLR_SELECTED_BG", "selected_bg"),
]


def apply_theme(theme_name: str):
    global _current_theme
    t = _tune_theme(_THEMES.get(theme_name, _THEMES[_DEFAULT_THEME]))
    _current_theme = theme_name
    mod = sys.modules[__name__]
    for clr_key, t_key in _CLR_KEY_MAP:
        setattr(mod, clr_key, t[t_key])
    _update_siui_color_group(t)
    try:
        SiGlobal.siui.reloadAllWindowsStyleSheet()
    except Exception:
        pass
    _apply_qt_global_theme()


# apply_theme called from ui.py after QApplication is ready
# apply_theme(_DEFAULT_THEME)


def _build_input_field_stylesheet(
    selector: str = "QLineEdit, QSpinBox",
    *,
    radius: int = 10,
    padding: str = "5px 12px",
    include_combo: bool = False,
) -> str:
    fill = _mix_hex_colors(CLR_INPUT_BG, CLR_BG_ELEVATED, 0.18)
    fill_hover = _mix_hex_colors(fill, CLR_BG_CARD, 0.26)
    fill_focus = _mix_hex_colors(fill, CLR_BG_CARD, 0.14)
    border = _mix_hex_colors(CLR_BORDER, CLR_TEXT_PRI, 0.08)
    hover_border = _mix_hex_colors(CLR_BORDER, CLR_ACCENT, 0.30)
    focus_border = _mix_hex_colors(CLR_ACCENT, CLR_ACCENT_LIGHT, 0.42)
    divider = _hex_with_alpha(CLR_BORDER, 170)
    selection_bg = _hex_with_alpha(CLR_ACCENT_LIGHT, 172)
    control_fill = _mix_hex_colors(fill, CLR_BG_CARD, 0.52)
    control_hover = _mix_hex_colors(control_fill, CLR_ACCENT, 0.14)
    inner_radius = max(6, radius - 2)

    # SVG arrows materialised on disk — Qt 5.15 cannot reliably load data: URIs
    arrow_up = _arrow_svg_file("up", CLR_TEXT_SEC)
    arrow_down = _arrow_svg_file("down", CLR_TEXT_SEC)
    arrow_up_hover = _arrow_svg_file("up", CLR_TEXT_PRI)
    arrow_down_hover = _arrow_svg_file("down", CLR_TEXT_PRI)

    combo_block = ""
    if include_combo:
        combo_block = f"""
            QComboBox {{
                padding: 4px 32px 4px 10px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border: none;
                border-left: 1px solid {divider};
                border-top-right-radius: {inner_radius}px;
                border-bottom-right-radius: {inner_radius}px;
                background-color: {control_fill};
                margin: 1px 1px 1px 0;
            }}
            QComboBox::drop-down:hover {{
                background-color: {control_hover};
            }}
            QComboBox::down-arrow {{
                image: url({arrow_down});
                width: 12px;
                height: 12px;
            }}
            QComboBox::down-arrow:hover {{
                image: url({arrow_down_hover});
            }}
            QComboBox QAbstractItemView {{
                background-color: {CLR_BG_CARD};
                color: {CLR_TEXT_PRI};
                border: 1px solid {hover_border};
                border-radius: {radius}px;
                selection-background-color: {selection_bg};
                selection-color: {CLR_ACCENT_TEXT};
                padding: 4px;
            }}
        """

    return f"""
        {selector} {{
            background-color: {fill};
            color: {CLR_TEXT_PRI};
            border: 1px solid {border};
            border-radius: {radius}px;
            padding: {padding};
            selection-background-color: {selection_bg};
            selection-color: {CLR_ACCENT_TEXT};
        }}
        {selector}:hover {{
            background-color: {fill_hover};
            border-color: {hover_border};
        }}
        {selector}:focus {{
            background-color: {fill_focus};
            border-color: {focus_border};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 22px;
            border: none;
            border-left: 1px solid {divider};
            background-color: {control_fill};
            margin: 1px 1px 1px 0;
        }}
        QSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            border-top-right-radius: {inner_radius}px;
            border-bottom: 1px solid {divider};
        }}
        QSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            border-bottom-right-radius: {inner_radius}px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: {control_hover};
        }}
        QSpinBox::up-arrow {{
            image: url({arrow_up});
            width: 10px;
            height: 10px;
        }}
        QSpinBox::down-arrow {{
            image: url({arrow_down});
            width: 10px;
            height: 10px;
        }}
        QSpinBox::up-arrow:hover {{ image: url({arrow_up_hover}); }}
        QSpinBox::down-arrow:hover {{ image: url({arrow_down_hover}); }}
        {combo_block}
    """


def _build_text_area_stylesheet(*, radius: int = 12, padding: str = "8px 10px") -> str:
    fill = _mix_hex_colors(CLR_INPUT_BG, CLR_BG_ELEVATED, 0.18)
    fill_hover = _mix_hex_colors(fill, CLR_BG_CARD, 0.26)
    fill_focus = _mix_hex_colors(fill, CLR_BG_CARD, 0.14)
    border = _mix_hex_colors(CLR_BORDER, CLR_TEXT_PRI, 0.08)
    hover_border = _mix_hex_colors(CLR_BORDER, CLR_ACCENT, 0.30)
    focus_border = _mix_hex_colors(CLR_ACCENT, CLR_ACCENT_LIGHT, 0.42)
    selection_bg = _hex_with_alpha(CLR_ACCENT_LIGHT, 172)
    return f"""
        QTextEdit {{
            background-color: {fill};
            color: {CLR_TEXT_PRI};
            border: 1px solid {border};
            border-radius: {radius}px;
            padding: {padding};
            selection-background-color: {selection_bg};
            selection-color: {CLR_ACCENT_TEXT};
        }}
        QTextEdit:hover {{
            background-color: {fill_hover};
            border-color: {hover_border};
        }}
        QTextEdit:focus {{
            background-color: {fill_focus};
            border-color: {focus_border};
        }}
    """


def _secret_reveal_icon(revealed: bool) -> QIcon:
    pix = QPixmap(20, 20)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    eye_color = QColor(CLR_TEXT_PRI if revealed else CLR_TEXT_SEC)
    muted_color = QColor(CLR_TEXT_TERT)
    painter.setPen(QPen(eye_color, 1.6))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(3, 6, 14, 8)
    painter.setBrush(QBrush(eye_color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QPointF(10, 10), 2.1, 2.1)

    if not revealed:
        painter.setPen(QPen(muted_color, 1.8))
        painter.drawLine(4, 16, 16, 4)

    painter.end()
    return QIcon(pix)


def _install_secret_reveal_action(edit: QLineEdit):
    action = getattr(edit, "_secret_reveal_action", None)
    revealed = bool(getattr(edit, "_secret_revealed", False))

    def _apply_state():
        is_revealed = bool(getattr(edit, "_secret_revealed", False))
        edit.setEchoMode(QLineEdit.Normal if is_revealed else QLineEdit.Password)
        action.setIcon(_secret_reveal_icon(is_revealed))
        action.setToolTip("隐藏内容" if is_revealed else "显示内容")

    if action is None:
        edit._secret_revealed = False
        action = edit.addAction(_secret_reveal_icon(False), QLineEdit.TrailingPosition)
        edit._secret_reveal_action = action

        def _toggle_secret():
            edit._secret_revealed = not bool(getattr(edit, "_secret_revealed", False))
            _apply_state()

        action.triggered.connect(_toggle_secret)
        revealed = False

    edit._secret_revealed = revealed
    _apply_state()


def _tune_font_quality(font: QFont) -> None:
    """Maximize text rendering quality: anti-alias + full hinting."""
    font.setKerning(True)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    try:
        font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    except AttributeError:
        pass


UI_FONT_FAMILIES = [
    "SF Pro Text",
    "SF Pro Display",
    "Segoe UI Variable Text",
    "Segoe UI",
    "PingFang SC",
    "Microsoft YaHei UI",
    "Noto Sans CJK SC",
    "Arial",
]
MONO_FONT_FAMILIES = [
    "SF Mono",
    "Cascadia Mono",
    "JetBrains Mono",
    "Consolas",
    "Microsoft YaHei UI",
]
UI_FONT_STACK_CSS = ", ".join(f'"{name}"' for name in UI_FONT_FAMILIES) + ", sans-serif"


def _make_font(families: list[str], size: int, weight: int = QFont.Normal) -> QFont:
    f = QFont(families[0], size, weight)
    try:
        f.setFamilies(families)
    except AttributeError:
        pass
    _tune_font_quality(f)
    return f


FONT_MONO = _make_font(MONO_FONT_FAMILIES, 11)
FONT_MONO_SMALL = _make_font(MONO_FONT_FAMILIES, 9)


def _make_ui_font(size: int, weight: int = QFont.Normal) -> QFont:
    """UI font with iOS/Windows-friendly cascade and CJK-safe fallbacks."""
    return _make_font(UI_FONT_FAMILIES, size, weight)


# Sonoma type scale
FONT_CAPTION   = _make_ui_font(9)
FONT_BODY      = _make_ui_font(10)
FONT_BODY_EMPH = _make_ui_font(10, QFont.DemiBold)
FONT_HEADLINE  = _make_ui_font(12, QFont.DemiBold)
FONT_TITLE_2   = _make_ui_font(16, QFont.DemiBold)
FONT_TITLE_2.setLetterSpacing(QFont.PercentageSpacing, 100)
FONT_TITLE_1   = _make_ui_font(21, QFont.Bold)
FONT_TITLE_1.setLetterSpacing(QFont.PercentageSpacing, 100)

# Backward-compat aliases — existing imports of FONT_UI / FONT_TITLE keep working
FONT_UI    = FONT_BODY
FONT_TITLE = FONT_TITLE_2


def register_app_fonts() -> int:
    """Load bundled Inter TTFs. Call once after QApplication created.

    Returns number of fonts loaded. Missing fonts/ folder → silently returns 0
    and the cascade falls back to PingFang SC / 阿里巴巴普惠体 / YaHei.
    """
    import os
    from PyQt5.QtGui import QFontDatabase
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if not os.path.isdir(fonts_dir):
        return 0
    loaded = 0
    for name in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
        path = os.path.join(fonts_dir, name)
        if os.path.isfile(path) and QFontDatabase.addApplicationFont(path) >= 0:
            loaded += 1
    return loaded
