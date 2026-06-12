"""FluentPage — drop-in replacement for SiPage using pure PyQt5."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QScrollArea


class FluentPage(QWidget):
    """Scrollable page with centered, max-width constrained content.

    API-compatible with SiPage: setPadding, setScrollMaximumWidth,
    setAttachment, attachment, setTitle.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._padding = 0
        self._max_width = 10000
        self._alignment = Qt.AlignCenter
        self._title_height = 0
        self._attachment = None

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._outer.addWidget(self._scroll)

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)
        self._inner_layout.setSpacing(0)
        self._scroll.setWidget(self._inner)

    def setAttachment(self, widget):
        """Set the content widget inside the scroll area."""
        if self._attachment is not None:
            self._inner_layout.removeWidget(self._attachment)
        self._attachment = widget
        if widget is not None:
            self._inner_layout.addWidget(widget)
            self._update_attachment_geometry()

    def attachment(self):
        return self._attachment

    def setScrollMaximumWidth(self, width):
        self._max_width = width
        self._update_attachment_geometry()

    def setScrollAlignment(self, alignment):
        self._alignment = alignment
        self._update_attachment_geometry()

    def setPadding(self, padding):
        self._padding = padding
        self._update_attachment_geometry()

    def setTitle(self, title):
        pass  # Titles are handled by page content, not the page shell

    def reloadStyleSheet(self):
        pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_attachment_geometry()

    def _update_attachment_geometry(self):
        if self._attachment is None:
            return
        page_w = self.width()
        avail_w = page_w - self._padding * 2
        w = min(avail_w, self._max_width)
        # Center by giving _inner equal left/right margins
        margin = (page_w - w) // 2
        self._inner_layout.setContentsMargins(margin, 0, margin, 0)
