"""macOS Sonoma-style component primitives.

Replaces the ~50+ inline stylesheet duplications across ui_pages/ and
ui_dialogs/. All components consume design tokens from ui_theme so the
3 palettes (graphite/moonstone/midnight) propagate automatically.

Every component implements apply_theme_styles() so pages can walk
findChildren() on theme switch and refresh all colors live.
"""
from __future__ import annotations

import os
from typing import Optional, Union

from ui_constants import _CARD_W, _CARD_H

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QFrame, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QSpinBox, QComboBox,
    QGraphicsDropShadowEffect,
)

import ui_theme as theme


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_shadow(elevation: int) -> Optional[QGraphicsDropShadowEffect]:
    """Layered drop shadow tuned per elevation. None for elevation<=0."""
    if elevation <= 0:
        return None
    effect = QGraphicsDropShadowEffect()
    if elevation == 1:
        effect.setBlurRadius(24)
        effect.setOffset(0, 4)
        effect.setColor(QColor(0, 0, 0, 38))
    else:
        effect.setBlurRadius(40)
        effect.setOffset(0, 8)
        effect.setColor(QColor(0, 0, 0, 50))
    return effect


# ---------------------------------------------------------------------------
# SectionHeader — used standalone or inside MacCard
# ---------------------------------------------------------------------------


class SectionHeader(QWidget):
    """Title (FONT_HEADLINE) + optional subtitle + right-aligned accessory slot."""

    def __init__(self, title: str,
                 subtitle: Optional[str] = None,
                 accessory: Optional[QWidget] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACING_SM)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self._title_label = QLabel(title)
        self._title_label.setFont(theme.FONT_HEADLINE)
        text_col.addWidget(self._title_label)

        self._subtitle_label: Optional[QLabel] = None
        if subtitle:
            self._subtitle_label = QLabel(subtitle)
            self._subtitle_label.setFont(theme.FONT_CAPTION)
            text_col.addWidget(self._subtitle_label)

        row.addLayout(text_col)
        row.addStretch(1)

        if accessory is not None:
            row.addWidget(accessory)

        self.apply_theme_styles()

    def apply_theme_styles(self):
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
        )
        if self._subtitle_label is not None:
            self._subtitle_label.setStyleSheet(
                f"color: {theme.CLR_TEXT_TERT}; background: transparent; border: none;"
            )


# ---------------------------------------------------------------------------
# PageIntroHeader — clean page title matching OBS page style
# ---------------------------------------------------------------------------


class PageIntroHeader(QWidget):
    """Page title + optional subtitle, tight spacing, no decorations."""

    def __init__(self, title: str, subtitle: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("PageIntroTitle")
        self._title_label.setFont(theme.FONT_TITLE_2)
        layout.addWidget(self._title_label)

        self._subtitle_label: Optional[QLabel] = None
        if subtitle:
            self._subtitle_label = QLabel(subtitle)
            self._subtitle_label.setObjectName("PageIntroSubtitle")
            self._subtitle_label.setFont(theme.FONT_BODY)
            self._subtitle_label.setWordWrap(True)
            layout.addWidget(self._subtitle_label)

        self.apply_theme_styles()

    def apply_theme_styles(self):
        self._title_label.setStyleSheet(
            f"color: {theme.CLR_TEXT_PRI}; background: transparent; border: none;"
        )
        if self._subtitle_label is not None:
            self._subtitle_label.setStyleSheet(
                f"color: {theme.CLR_TEXT_TERT}; background: transparent; border: none;"
            )


# ---------------------------------------------------------------------------
# MacCard — the workhorse
# ---------------------------------------------------------------------------


class MacCard(QFrame):
    """Rounded card: hairline border + drop shadow + optional vibrancy.

    Replaces the inline QFrame { bg + border + radius + padding } pattern
    duplicated across every panel in homepage.py and similar pages.

    Add content via .body() — it returns the inner QVBoxLayout.
    """

    def __init__(self, parent: Optional[QWidget] = None, *,
                 title: Optional[str] = None,
                 subtitle: Optional[str] = None,
                 accessory: Optional[QWidget] = None,
                 vibrancy: bool = False,
                 radius: int = theme.RADIUS_LG,
                 elevation: int = 1,
                 padding: tuple = (14, 12, 14, 12)):
        super().__init__(parent)
        self._vibrancy = vibrancy
        self._radius = radius
        self._elevation = elevation
        self._hovered = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*padding)
        outer.setSpacing(theme.SPACING_SM)

        if title is not None:
            outer.addWidget(SectionHeader(title, subtitle, accessory))

        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(theme.SPACING_SM)
        outer.addLayout(self._body_layout)

        shadow = _build_shadow(elevation)
        if shadow is not None:
            self.setGraphicsEffect(shadow)

        self.apply_theme_styles()

    def enterEvent(self, event):
        self._hovered = True
        self._apply_shadow(is_hover=True)
        self.setStyleSheet(self._current_style().replace(
            f"background-color: {self._bg_color()};",
            f"background-color: {theme.CLR_BG_CARD_HOVER};",
        ))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_shadow(is_hover=False)
        self.setStyleSheet(self._current_style())
        super().leaveEvent(event)

    def _bg_color(self) -> str:
        if self._vibrancy:
            return theme.CLR_BG_VIBRANCY
        if self._hovered:
            return theme.CLR_BG_CARD_HOVER
        return theme.CLR_BG_CARD

    def _apply_shadow(self, is_hover: bool):
        if self._elevation <= 0:
            return
        effect = QGraphicsDropShadowEffect()
        if is_hover:
            effect.setBlurRadius(32)
            effect.setOffset(0, 4)
            effect.setColor(QColor(0, 0, 0, 56))
        elif self._elevation == 1:
            effect.setBlurRadius(24)
            effect.setOffset(0, 4)
            effect.setColor(QColor(0, 0, 0, 38))
        else:
            effect.setBlurRadius(40)
            effect.setOffset(0, 8)
            effect.setColor(QColor(0, 0, 0, 50))
        self.setGraphicsEffect(effect)

    def _current_style(self) -> str:
        accent_top = theme._hex_with_alpha(theme.CLR_ACCENT, 38)
        return f"""
            MacCard {{
                background-color: {self._bg_color()};
                border: 1px solid {theme.CLR_HAIRLINE};
                border-top: 2px solid {accent_top};
                border-radius: {self._radius}px;
            }}
        """

    def apply_theme_styles(self):
        self.setStyleSheet(self._current_style())

    def body(self) -> QVBoxLayout:
        """Inner layout — add content widgets here."""
        return self._body_layout


# ---------------------------------------------------------------------------
# MacButton — 5 variants cover all current usage
# ---------------------------------------------------------------------------


class MacButton(QPushButton):
    """Sonoma button. variant ∈ {primary, secondary, ghost, destructive, pill}.

    primary      — filled accent, dominant CTA (1 per dialog/page).
    secondary    — bg_elevated + hairline border (default, most common).
    ghost        — transparent + accent text (links / tertiary actions).
    destructive  — red text on hairline border, flips to filled red on hover.
    pill         — checkable capsule, accent fill when :checked.
    """

    _VARIANTS = ("primary", "secondary", "ghost", "destructive", "pill")

    def __init__(self, text: str = "",
                 variant: str = "secondary",
                 icon: Optional[Union[QIcon, str]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._variant = variant if variant in self._VARIANTS else "secondary"
        self.setCursor(Qt.PointingHandCursor)
        if self._variant == "pill":
            self.setCheckable(True)
        if icon is not None:
            self._set_icon(icon)
        self.apply_theme_styles()

    def _set_icon(self, icon: Union[QIcon, str]):
        if isinstance(icon, QIcon):
            self.setIcon(icon)
        elif isinstance(icon, str):
            try:
                import qtawesome as qta
                self.setIcon(qta.icon(icon, color=theme.CLR_TEXT_PRI))
            except ImportError:
                pass  # qtawesome not installed yet — degrade silently

    def apply_theme_styles(self):
        r = theme.RADIUS_MD
        v = self._variant
        if v == "primary":
            hover = theme._mix_hex_colors(theme.CLR_ACCENT, theme.CLR_ACCENT_TEXT, 0.12)
            pressed = theme._mix_hex_colors(theme.CLR_ACCENT, "#000000", 0.08)
            ss = f"""
                QPushButton {{
                    background-color: {theme.CLR_ACCENT};
                    color: {theme.CLR_ACCENT_TEXT};
                    border: none;
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {hover}; }}
                QPushButton:pressed {{ background-color: {pressed}; }}
                QPushButton:disabled {{
                    background-color: {theme.CLR_BG_ELEVATED};
                    color: {theme.CLR_TEXT_TERT};
                }}
            """
        elif v == "ghost":
            ss = f"""
                QPushButton {{
                    background-color: transparent;
                    color: {theme.CLR_ACCENT_LIGHT};
                    border: none;
                    border-radius: {r}px;
                    padding: 6px 12px;
                }}
                QPushButton:hover {{
                    color: {theme.CLR_ACCENT_TEXT};
                    background-color: {theme.CLR_SELECTED_BG};
                }}
                QPushButton:disabled {{ color: {theme.CLR_TEXT_TERT}; }}
            """
        elif v == "destructive":
            ss = f"""
                QPushButton {{
                    background-color: transparent;
                    color: {theme.CLR_RED};
                    border: 1px solid {theme.CLR_RED};
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {theme.CLR_RED};
                    color: {theme.CLR_ACCENT_TEXT};
                    border-color: {theme.CLR_RED};
                }}
                QPushButton:disabled {{
                    color: {theme.CLR_TEXT_TERT};
                    border-color: {theme.CLR_HAIRLINE};
                }}
            """
        elif v == "pill":
            off_fill = theme._mix_hex_colors(theme.CLR_BG_INSET, theme.CLR_BG_CARD, 0.30)
            off_hover = theme._mix_hex_colors(theme.CLR_BG_INSET, theme.CLR_BG_CARD, 0.58)
            checked_fill = theme._hex_with_alpha(theme.CLR_ACCENT, 34)
            checked_hover = theme._hex_with_alpha(theme.CLR_ACCENT, 48)
            checked_border = theme._hex_with_alpha(theme.CLR_ACCENT, 96)
            ss = f"""
                QPushButton {{
                    background-color: {off_fill};
                    color: {theme.CLR_TEXT_SEC};
                    border: 1px solid {theme.CLR_HAIRLINE};
                    border-radius: {r}px;
                    padding: 5px 12px;
                    font-weight: 600;
                    text-align: center;
                }}
                QPushButton:hover {{
                    background-color: {off_hover};
                    color: {theme.CLR_TEXT_PRI};
                    border-color: {theme.CLR_BORDER};
                }}
                QPushButton:checked {{
                    background-color: {checked_fill};
                    color: {theme.CLR_ACCENT};
                    border-color: {checked_border};
                }}
                QPushButton:checked:hover {{
                    background-color: {checked_hover};
                    color: {theme.CLR_ACCENT};
                    border-color: {checked_border};
                }}
            """
        else:  # secondary — visibly fills on dark bg via brighter computed fill
            fill = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.03)
            fill_hover = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.07)
            border_color = theme.CLR_HAIRLINE
            ss = f"""
                QPushButton {{
                    background-color: {fill};
                    color: {theme.CLR_TEXT_PRI};
                    border: 1px solid {border_color};
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {fill_hover};
                    border-color: {theme.CLR_ACCENT};
                }}
                QPushButton:pressed {{ background-color: {fill}; }}
                QPushButton:disabled {{
                    color: {theme.CLR_TEXT_TERT};
                    background-color: {theme.CLR_BG_ELEVATED};
                }}
            """
        self.setStyleSheet(ss)


# ---------------------------------------------------------------------------
# Input controls — reuse the existing factory stylesheets in ui_theme
# ---------------------------------------------------------------------------


class MacLineEdit(QLineEdit):
    """Styled with ui_theme._build_input_field_stylesheet.

    secret=True wires the existing _install_secret_reveal_action (eye toggle).
    """

    def __init__(self, parent: Optional[QWidget] = None, *,
                 placeholder: str = "", secret: bool = False):
        super().__init__(parent)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self._secret = secret
        if secret:
            theme._install_secret_reveal_action(self)
        self.apply_theme_styles()

    def apply_theme_styles(self):
        self.setStyleSheet(theme._build_input_field_stylesheet(
            selector="QLineEdit", radius=theme.RADIUS_MD, padding="6px 12px",
        ))


class MacSpinBox(QSpinBox):
    """Styled with the input-field factory."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.apply_theme_styles()

    def apply_theme_styles(self):
        self.setStyleSheet(theme._build_input_field_stylesheet(
            selector="QSpinBox", radius=theme.RADIUS_MD, padding="4px 8px",
        ))


class MacComboBox(QComboBox):
    """Styled with the input-field factory; includes drop-down chrome + arrow.

    Sets a sensible minimum width (160) so common device names like
    "VoiceMeeter VAIO" / "扬声器 (Realtek...)" don't truncate. Caller can override.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setMinimumHeight(max(36, self.fontMetrics().height() + 16))
        self.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.apply_theme_styles()

    def apply_theme_styles(self):
        self.setStyleSheet(theme._build_input_field_stylesheet(
            selector="QComboBox",
            radius=theme.RADIUS_MD,
            padding="5px 12px",
            include_combo=True,
        ))


# ---------------------------------------------------------------------------
# Separator — 1px hairline divider
# ---------------------------------------------------------------------------


class MacSeparator(QFrame):
    """1px hairline divider. orientation ∈ {'horizontal', 'vertical'}."""

    def __init__(self, orientation: str = "horizontal",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        if orientation == "vertical":
            self.setFrameShape(QFrame.VLine)
            self.setFixedWidth(1)
        else:
            self.setFrameShape(QFrame.HLine)
            self.setFixedHeight(1)
        self.setFrameShadow(QFrame.Plain)
        self.apply_theme_styles()

    def apply_theme_styles(self):
        self.setStyleSheet(
            f"background-color: {theme.CLR_HAIRLINE}; border: none;"
        )


# ---------------------------------------------------------------------------
# Video thumbnail cards — used by voiceconfigpage & digitalhumanpage
# ---------------------------------------------------------------------------


class VideoThumbCard(QFrame):
    """Clickable video thumbnail with selection border and status badge."""
    clicked = pyqtSignal(int)
    remove_requested = pyqtSignal(int)

    _BORDER_W = 2
    _CONTENT_MARGIN = 2
    _INNER_INSET = _BORDER_W + _CONTENT_MARGIN

    def __init__(self, index: int, video_path: str, pixmap: Optional[QPixmap],
                 parent=None, status_text: str = ""):
        super().__init__(parent)
        self._index = index
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        self._pixmap = pixmap
        self._apply_border_style(theme.CLR_BORDER)

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
            f"border-radius: 9px; background-color: {theme.CLR_INPUT_BG};"
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
            f"color: {theme.CLR_TEXT_SEC};"
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
                background-color: {theme.CLR_INPUT_BG};
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
        self._apply_border_style(theme.CLR_ACCENT if selected else theme.CLR_BORDER)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._index)
        elif event.button() == Qt.RightButton:
            self.remove_requested.emit(self._index)
        super().mousePressEvent(event)


class AddCard(QFrame):
    """Dashed-border '+' card for adding new items."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.CLR_INPUT_BG};
                border: 1px dashed {theme.CLR_BORDER};
                border-radius: 12px;
            }}
            QFrame:hover {{
                border-color: {theme.CLR_ACCENT};
                background-color: {theme.CLR_BG_ELEVATED};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        plus_label = QLabel("+", self)
        plus_label.setAlignment(Qt.AlignCenter)
        plus_label.setStyleSheet(f"color: {theme.CLR_TEXT_SEC}; font-size: 32pt; border: none; background: transparent;")
        layout.addWidget(plus_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


__all__ = (
    "MacCard",
    "SectionHeader",
    "MacButton",
    "MacLineEdit",
    "MacSpinBox",
    "MacComboBox",
    "MacSeparator",
    "VideoThumbCard",
    "AddCard",
)
