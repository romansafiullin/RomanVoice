"""Small status overlay for background dictation."""
from __future__ import annotations

import math
import logging
import time
from typing import List

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from config import config

logger = logging.getLogger(__name__)


class CompactStatusOverlay(QWidget):
    """A compact, non-interactive recording/transcription indicator."""

    STATE_IDLE = "idle"
    STATE_RECORDING = "recording"
    STATE_PROCESSING = "processing"
    STATE_TRANSCRIBING = "transcribing"
    STATE_CANCELING = "canceling"
    STATE_ENABLED = "enabled"
    STATE_DISABLED = "disabled"

    _LABELS = {
        STATE_RECORDING: "Recording",
        STATE_PROCESSING: "Processing",
        STATE_TRANSCRIBING: "Transcribing",
        STATE_CANCELING: "Canceled",
        STATE_ENABLED: "Enabled",
        STATE_DISABLED: "Disabled",
    }

    _COLORS = {
        STATE_RECORDING: QColor(239, 68, 68),
        STATE_PROCESSING: QColor(251, 191, 36),
        STATE_TRANSCRIBING: QColor(56, 189, 248),
        STATE_CANCELING: QColor(148, 163, 184),
        STATE_ENABLED: QColor(34, 197, 94),
        STATE_DISABLED: QColor(148, 163, 184),
    }

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
        self._icon_size = 48
        self._preview_width = 320
        self._preview_height = 64
        self.setFixedSize(self._icon_size, self._icon_size)

        self.current_state = self.STATE_IDLE
        self.audio_level = 0.0
        self.preview_text = ""
        self.animation_time = 0.0
        self.last_frame_time = time.time()

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)

        self.auto_hide_timer = QTimer()
        self.auto_hide_timer.setSingleShot(True)
        self.auto_hide_timer.timeout.connect(self.hide)

    def set_status(self, state: str) -> None:
        """Show or update the compact indicator."""
        previous_state = self.current_state
        self.current_state = state
        self.animation_time = 0.0
        self.last_frame_time = time.time()

        if state == self.STATE_RECORDING and previous_state != self.STATE_RECORDING:
            self.preview_text = ""
            self._apply_size_for_preview()

        if state == self.STATE_IDLE:
            self.hide()
            return

        self._position()
        self.show()
        self.raise_()
        logger.debug(
            "Compact overlay shown: state=%s geometry=%s preview=%s",
            state,
            self.geometry().getRect(),
            bool(self.preview_text),
        )

        if not self.timer.isActive():
            self.timer.start(33)

        if state in {self.STATE_CANCELING, self.STATE_ENABLED, self.STATE_DISABLED}:
            self.auto_hide_timer.start(1200)
        else:
            self.auto_hide_timer.stop()

        self.update()

    def set_preview_text(self, text: str) -> None:
        """Update the live preview line."""
        text = " ".join((text or "").split())
        if len(text) > 115:
            text = "..." + text[-112:]
        self.preview_text = text
        self._apply_size_for_preview()
        if self.isVisible():
            self._position()
            self.update()
            if text:
                logger.debug("Compact overlay preview updated: %s chars", len(text))

    def update_audio_levels(self, levels: List[float]) -> None:
        """Update the simple audio activity meter."""
        if not levels:
            self.audio_level = 0.0
        else:
            self.audio_level = max(0.0, min(1.0, max(levels)))
        if self.isVisible():
            self.update()

    def hide(self) -> None:
        self.timer.stop()
        self.auto_hide_timer.stop()
        self.current_state = self.STATE_IDLE
        self.preview_text = ""
        self.setFixedSize(self._icon_size, self._icon_size)
        super().hide()

    def _apply_size_for_preview(self) -> None:
        if self.preview_text:
            self.setFixedSize(self._preview_width, self._preview_height)
        else:
            self.setFixedSize(self._icon_size, self._icon_size)

    def _position(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if not screen:
            return
        geometry = screen.availableGeometry()
        if config.COMPACT_OVERLAY_POSITION == "bottom_right":
            x = geometry.x() + geometry.width() - self.width() - 28
        else:
            x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + geometry.height() - self.height() - 76
        self.move(x, y)

    def _tick(self) -> None:
        now = time.time()
        self.animation_time += now - self.last_frame_time
        self.last_frame_time = now
        if self.current_state == self.STATE_IDLE:
            self.timer.stop()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(70, 72, 92, 180), 1))
        painter.setBrush(QColor(24, 24, 33, 232))
        painter.drawRoundedRect(rect, 10, 10)

        color = self._COLORS.get(self.current_state, QColor(148, 163, 184))
        pulse = 0.5 + 0.5 * math.sin(self.animation_time * 6.0)
        dot_alpha = 175 + int(80 * pulse)
        dot_color = QColor(color)
        dot_color.setAlpha(dot_alpha)

        self._draw_mic_icon(painter, dot_color)

        if self.current_state == self.STATE_RECORDING:
            self._draw_meter(painter, color)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 2))
            painter.drawArc(11, 10, 34, 34, int((self.animation_time * 240) % 360) * 16, 120 * 16)

        preview = self.preview_text
        if preview:
            painter.setPen(QColor(238, 242, 255))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            label = self._LABELS.get(self.current_state, "")
            painter.drawText(QRectF(58, 7, self.width() - 72, 20), Qt.AlignmentFlag.AlignVCenter, label)

            painter.setPen(QColor(203, 213, 225))
            painter.setFont(QFont("Segoe UI", 9))
            preview_rect = QRectF(58, 29, self.width() - 72, 28)
            painter.drawText(
                preview_rect,
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap,
                preview,
            )

    def _draw_mic_icon(self, painter: QPainter, color: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawEllipse(7, 7, 34, 34)

        painter.setPen(QPen(color, 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(19, 12, 10, 18), 5, 5)
        painter.drawArc(15, 21, 18, 15, 200 * 16, 140 * 16)
        painter.drawLine(24, 36, 24, 40)
        painter.drawLine(19, 40, 29, 40)

    def _draw_meter(self, painter: QPainter, color: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        active_bars = max(1, min(4, int(round(self.audio_level * 6))))
        for index in range(4):
            height = 5 + (index % 2) * 5
            if index < active_bars:
                bar_color = QColor(color)
                bar_color.setAlpha(210)
            else:
                bar_color = QColor(86, 88, 110, 180)
            painter.setBrush(bar_color)
            painter.drawRoundedRect(QRectF(35 + index * 3, 39 - height, 2, height), 1, 1)
