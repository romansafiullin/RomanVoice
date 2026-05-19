"""Small live transcription preview positioned near the active text caret."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional, Tuple

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QWidget


if sys.platform == "win32":
    _USER32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", _RECT),
        ]
else:
    _USER32 = None
    _RECT = None
    _GUITHREADINFO = None


logger = logging.getLogger(__name__)


class CaretPreviewOverlay(QWidget):
    """Non-focus live preview that follows the focused text caret when possible."""

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._width = 420
        self._height = 76
        self.setFixedSize(self._width, self._height)
        self._text = ""

        self._position_timer = QTimer()
        self._position_timer.timeout.connect(self._tick)

    def set_preview_text(self, text: str) -> bool:
        """Show/update preview near the caret. Returns False if no caret exists."""
        text = " ".join((text or "").split())
        if not text:
            self.hide_preview()
            return False

        if len(text) > 180:
            text = "..." + text[-177:]

        caret = self._get_caret_anchor()
        if caret is None:
            self.hide_preview()
            return False

        self._text = text
        self._position_near(caret)
        if not self.isVisible():
            self.show()
            self.raise_()
            self._position_timer.start(250)
        self.update()
        return True

    def hide_preview(self) -> None:
        self._position_timer.stop()
        self._text = ""
        self.hide()

    def _tick(self) -> None:
        caret = self._get_caret_anchor()
        if caret is None:
            self.hide_preview()
            return
        self._position_near(caret)

    def _position_near(self, caret: QPoint) -> None:
        screen = QGuiApplication.screenAt(caret) or QGuiApplication.primaryScreen()
        if not screen:
            return

        bounds = screen.availableGeometry()
        x = caret.x() + 10
        y = caret.y() + 18

        if x + self.width() > bounds.right():
            x = max(bounds.left(), caret.x() - self.width() - 10)
        if y + self.height() > bounds.bottom():
            y = max(bounds.top(), caret.y() - self.height() - 18)

        x = max(bounds.left(), min(x, bounds.right() - self.width()))
        y = max(bounds.top(), min(y, bounds.bottom() - self.height()))
        self.move(x, y)

    def _get_caret_anchor(self) -> Optional[QPoint]:
        if _USER32 is None:
            return None

        try:
            foreground = _USER32.GetForegroundWindow()
            if not foreground:
                return None

            thread_id = _USER32.GetWindowThreadProcessId(foreground, None)
            gui = _GUITHREADINFO()
            gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
            if not _USER32.GetGUIThreadInfo(thread_id, ctypes.byref(gui)):
                return None
            if not gui.hwndCaret:
                return None

            bottom_left = wintypes.POINT(gui.rcCaret.left, gui.rcCaret.bottom)
            if not _USER32.ClientToScreen(gui.hwndCaret, ctypes.byref(bottom_left)):
                return None

            x, y = self._scale_point_for_qt(gui.hwndCaret, int(bottom_left.x), int(bottom_left.y))
            return QPoint(x, y)
        except Exception as exc:
            logger.debug("Failed to read caret position for preview: %s", exc)
            return None

    def _scale_point_for_qt(self, hwnd, x: int, y: int) -> Tuple[int, int]:
        if _USER32 is None or not hasattr(_USER32, "GetDpiForWindow"):
            return (x, y)

        try:
            dpi = _USER32.GetDpiForWindow(hwnd)
            if dpi:
                scale = dpi / 96.0
                return (int(x / scale), int(y / scale))
        except Exception:
            pass
        return (x, y)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(71, 85, 105, 190), 1))
        painter.setBrush(QColor(15, 23, 42, 238))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(QPen(QColor(56, 189, 248, 210), 2))
        painter.drawLine(14, 12, 14, self.height() - 12)

        painter.setPen(QColor(226, 232, 240))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            QRectF(26, 10, self.width() - 40, self.height() - 20),
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextWordWrap,
            self._text,
        )

        painter.end()
