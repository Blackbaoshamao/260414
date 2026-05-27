"""macOS Sonoma-style component primitives.

Replaces the ~50+ inline stylesheet duplications across ui_pages/ and
ui_dialogs/. All components consume design tokens from ui_theme so the
3 palettes (graphite/moonstone/midnight) propagate automatically.

Every component implements apply_theme_styles() so pages can walk
findChildren() on theme switch and refresh all colors live.
"""
from __future__ import annotations

from typing import Optional, Union

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon
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
        effect.setBlurRadius(8)
        effect.setOffset(0, 2)
        effect.setColor(QColor(0, 0, 0, 40))   # ~16% alpha
    else:
        effect.setBlurRadius(16)
        effect.setOffset(0, 6)
        effect.setColor(QColor(0, 0, 0, 56))   # ~22% alpha
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
                 padding: tuple = (16, 14, 16, 14)):
        super().__init__(parent)
        self._vibrancy = vibrancy
        self._radius = radius

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

    def apply_theme_styles(self):
        bg = theme.CLR_BG_VIBRANCY if self._vibrancy else theme.CLR_BG_CARD
        self.setStyleSheet(f"""
            MacCard {{
                background-color: {bg};
                border: 1px solid {theme.CLR_HAIRLINE};
                border-radius: {self._radius}px;
            }}
        """)

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
            ss = f"""
                QPushButton {{
                    background-color: {theme.CLR_ACCENT};
                    color: {theme.CLR_ACCENT_TEXT};
                    border: none;
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {theme.CLR_ACCENT_LIGHT}; }}
                QPushButton:pressed {{ background-color: {theme.CLR_ACCENT}; }}
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
            ss = f"""
                QPushButton {{
                    background-color: {theme.CLR_BG_ELEVATED};
                    color: {theme.CLR_TEXT_SEC};
                    border: 1px solid {theme.CLR_BORDER};
                    border-radius: 13px;
                    padding: 4px 14px;
                }}
                QPushButton:hover {{ border-color: {theme.CLR_ACCENT}; }}
                QPushButton:checked {{
                    background-color: {theme.CLR_ACCENT};
                    color: {theme.CLR_ACCENT_TEXT};
                    border-color: {theme.CLR_ACCENT};
                }}
            """
        else:  # secondary — visibly fills on dark bg via brighter computed fill
            fill = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.08)
            fill_hover = theme._mix_hex_colors(theme.CLR_BG_ELEVATED, theme.CLR_TEXT_PRI, 0.16)
            border_color = theme._mix_hex_colors(theme.CLR_BORDER, theme.CLR_TEXT_PRI, 0.18)
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
        self.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.apply_theme_styles()

    def apply_theme_styles(self):
        self.setStyleSheet(theme._build_input_field_stylesheet(
            selector="QComboBox",
            radius=theme.RADIUS_MD,
            padding="4px 12px",
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


__all__ = (
    "MacCard",
    "SectionHeader",
    "MacButton",
    "MacLineEdit",
    "MacSpinBox",
    "MacComboBox",
    "MacSeparator",
)
