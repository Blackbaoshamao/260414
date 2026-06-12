"""Smooth transition helpers for PyQt5 — iOS-style fades, slides, and cross-fades."""

from __future__ import annotations

from PyQt5.QtCore import (
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
    QSequentialAnimationGroup, QPoint, QSize, pyqtProperty, QWidget,
)
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QGraphicsOpacityEffect


# ---------------------------------------------------------------------------
# Opacity helpers
# ---------------------------------------------------------------------------

def _get_or_create_opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        return effect
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    return effect


def fade_in(widget: QWidget, duration: int = 200, *, start: float = 0.0, end: float = 1.0,
            easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic) -> QPropertyAnimation:
    """Fade a widget from *start* opacity to *end*."""
    effect = _get_or_create_opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity")
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(easing)
    widget.show()
    anim.start()
    return anim


def fade_out(widget: QWidget, duration: int = 180, *, start: float = 1.0, end: float = 0.0,
             easing: QEasingCurve.Type = QEasingCurve.Type.InCubic,
             hide_on_done: bool = True) -> QPropertyAnimation:
    """Fade a widget out. Optionally hide when finished."""
    effect = _get_or_create_opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity")
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(easing)
    if hide_on_done:
        anim.finished.connect(widget.hide)
    anim.start()
    return anim


# ---------------------------------------------------------------------------
# Slide helpers
# ---------------------------------------------------------------------------

def slide_in_from_bottom(widget: QWidget, distance: int = 24, duration: int = 280,
                         *, opacity: bool = True) -> QParallelAnimationGroup:
    """Slide up from below + optional fade-in. iOS 'present' feel."""
    group = QParallelAnimationGroup()
    orig_pos = widget.pos()

    pos_anim = QPropertyAnimation(widget, b"pos")
    pos_anim.setDuration(duration)
    pos_anim.setStartValue(QPoint(orig_pos.x(), orig_pos.y() + distance))
    pos_anim.setEndValue(orig_pos)
    pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(pos_anim)

    if opacity:
        effect = _get_or_create_opacity_effect(widget)
        op_anim = QPropertyAnimation(effect, b"opacity")
        op_anim.setDuration(duration)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(op_anim)

    widget.show()
    group.start()
    return group


def slide_in_from_right(widget: QWidget, distance: int = 40, duration: int = 300,
                        *, opacity: bool = True) -> QParallelAnimationGroup:
    """Slide in from right edge — iOS push navigation feel."""
    group = QParallelAnimationGroup()
    orig_pos = widget.pos()

    pos_anim = QPropertyAnimation(widget, b"pos")
    pos_anim.setDuration(duration)
    pos_anim.setStartValue(QPoint(orig_pos.x() + distance, orig_pos.y()))
    pos_anim.setEndValue(orig_pos)
    pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(pos_anim)

    if opacity:
        effect = _get_or_create_opacity_effect(widget)
        op_anim = QPropertyAnimation(effect, b"opacity")
        op_anim.setDuration(duration)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(op_anim)

    widget.show()
    group.start()
    return group


# ---------------------------------------------------------------------------
# Cross-fade page transition
# ---------------------------------------------------------------------------

def cross_fade_pages(outgoing: QWidget, incoming: QWidget,
                     duration: int = 220) -> QSequentialAnimationGroup:
    """Fade out the old page, then fade in the new one.

    Both widgets must share the same parent layout. The outgoing widget is
    hidden after its fade completes; the incoming widget is shown at the start.
    """
    out_effect = _get_or_create_opacity_effect(outgoing)
    in_effect = _get_or_create_opacity_effect(incoming)

    fade_out_anim = QPropertyAnimation(out_effect, b"opacity")
    fade_out_anim.setDuration(duration)
    fade_out_anim.setStartValue(1.0)
    fade_out_anim.setEndValue(0.0)
    fade_out_anim.setEasingCurve(QEasingCurve.Type.InCubic)

    fade_in_anim = QPropertyAnimation(in_effect, b"opacity")
    fade_in_anim.setDuration(duration)
    fade_in_anim.setStartValue(0.0)
    fade_in_anim.setEndValue(1.0)
    fade_in_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    seq = QSequentialAnimationGroup()
    seq.addAnimation(fade_out_anim)
    seq.addAnimation(fade_in_anim)

    fade_out_anim.finished.connect(outgoing.hide)
    incoming.show()

    seq.start()
    return seq
