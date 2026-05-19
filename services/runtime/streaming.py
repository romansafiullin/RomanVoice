"""Streaming transcription helpers for the application controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from config import config
try:
    from services.settings import SettingsKey, settings_manager
except ImportError:  # pragma: no cover - supports lightweight test stubs
    from services.settings import settings_manager

    class SettingsKey:
        STREAMING_ENABLED = "streaming_enabled"
        STREAMING_CHUNK_DURATION = "streaming_chunk_duration"
        STREAMING_PASTE_ENABLED = "streaming_paste_enabled"
        STREAMING_TINY_MODEL_ENABLED = "streaming_tiny_model_enabled"
        LIVE_TYPE_ENABLED = "live_type_enabled"
        TEXT_INJECTION_KEY_DELAY_MS = "text_injection_key_delay_ms"

if TYPE_CHECKING:
    from services.recorder import AudioLevelCallback
else:
    AudioLevelCallback = Callable[[float], None]
from services.streaming_transcriber import StreamingTranscriber
from services.text_injector import text_injector
from transcriber import LocalWhisperBackend

if TYPE_CHECKING:
    from services.application_controller import ApplicationController

logger = logging.getLogger(__name__)


class StreamingRuntime:
    """Owns streaming transcription setup and lifecycle."""

    def __init__(self, controller: "ApplicationController"):
        self.controller = controller

    def setup_audio_level_callback(self) -> None:
        """Setup audio level callback for waveform display."""

        def audio_level_callback(level: float) -> None:
            levels = [level] * 20
            self.controller.ui_controller.update_audio_levels(levels)

        callback: AudioLevelCallback = audio_level_callback
        self.controller.recorder.set_audio_level_callback(callback)

    def setup_streaming(self) -> None:
        """Initialize streaming transcriber if enabled."""
        self._configure_streaming(initial_setup=True)

    def reconfigure_streaming(self) -> None:
        """Reconfigure streaming transcriber based on current settings."""
        logger.info("Reconfiguring streaming transcription...")

        if self.controller.recorder.is_recording:
            logger.warning("Cannot reconfigure streaming while recording")
            self.controller.ui_controller.set_status(
                "Stop recording before changing streaming mode"
            )
            return

        self._cleanup_streaming_resources()
        self._configure_streaming(initial_setup=False)

    def on_partial_transcription(self, text: str, is_final: bool) -> None:
        """Handle partial transcription from the streaming worker."""
        self.controller._last_streaming_text = text or ""
        self._type_live_update(text or "")
        self.controller.partial_transcription.emit(text, is_final)
        if self.controller._streaming_paste_enabled and text:
            self.controller.streaming_text_update.emit(text, is_final)

    def start_streaming_session(self) -> None:
        """Start real-time streaming transcription for an active recording."""
        if not self.controller.streaming_transcriber:
            return

        self.controller.recorder.set_streaming_callback(
            self.controller.streaming_transcriber.feed_audio
        )
        self.controller.streaming_transcriber.start_streaming(
            sample_rate=self.controller.recorder.rate,
            callback=self.on_partial_transcription,
        )
        logger.info("Streaming transcription started")

        if self.controller._streaming_paste_enabled:
            self.controller.streaming_overlay_show.emit()

    def stop_streaming_session(self) -> str:
        """Stop streaming transcription and return the accumulated text."""
        if not self.controller.streaming_transcriber:
            return ""

        streaming_text = self.controller.streaming_transcriber.stop_streaming()
        if streaming_text:
            self.controller._last_streaming_text = streaming_text
        self.controller.recorder.set_streaming_callback(None)
        logger.info(
            f"Streaming transcription stopped, got {len(streaming_text)} chars"
        )
        return streaming_text

    def cancel_streaming_session(self) -> None:
        """Cancel any active streaming session."""
        if self.controller.streaming_transcriber:
            self.controller.streaming_transcriber.stop_streaming()
            self.controller.recorder.set_streaming_callback(None)
            logger.info("Streaming transcription canceled")

        if self.controller._streaming_paste_enabled:
            self.controller.streaming_overlay_hide.emit()
            self.controller.caret_indicator_hide.emit()

    def cleanup(self) -> None:
        """Release streaming resources."""
        self._cleanup_streaming_resources()

    def _configure_streaming(self, *, initial_setup: bool) -> None:
        try:
            settings = settings_manager.load_all_settings()
            self.controller._streaming_enabled = settings.get(
                SettingsKey.STREAMING_ENABLED, config.STREAMING_ENABLED
            )
            self.controller._streaming_paste_enabled = settings.get(
                SettingsKey.STREAMING_PASTE_ENABLED, False
            )
            streaming_tiny_enabled = settings.get(SettingsKey.STREAMING_TINY_MODEL_ENABLED, False)

            if (
                self.controller._streaming_enabled
                and isinstance(self.controller.current_backend, LocalWhisperBackend)
            ):
                chunk_duration = settings.get(
                    SettingsKey.STREAMING_CHUNK_DURATION, config.STREAMING_CHUNK_DURATION_SEC
                )

                if streaming_tiny_enabled:
                    logger.info("Creating dedicated tiny.en backend for streaming...")
                    self.controller._streaming_backend = LocalWhisperBackend(
                        model_name="tiny.en"
                    )
                    streaming_backend = self.controller._streaming_backend
                    logger.info(
                        "Streaming %s dedicated tiny.en model",
                        "will use" if initial_setup else "reconfigured with",
                    )
                else:
                    streaming_backend = self.controller.current_backend
                    logger.info(
                        "Streaming %s main transcription model",
                        "will share" if initial_setup else "reconfigured to share",
                    )

                self.controller.streaming_transcriber = StreamingTranscriber(
                    backend=streaming_backend,
                    chunk_duration_sec=chunk_duration,
                )
                logger.info(
                    "Streaming transcription enabled "
                    f"(chunk_duration={chunk_duration}s, "
                    f"paste_overlay={self.controller._streaming_paste_enabled})"
                )
                if not initial_setup:
                    self.controller.ui_controller.set_status("Streaming mode enabled")
            else:
                if self.controller._streaming_enabled:
                    logger.info(
                        "Streaming requested but not available "
                        "(requires Local Whisper backend)"
                    )
                    if not initial_setup:
                        self.controller.ui_controller.set_status(
                            "Streaming requires Local Whisper backend"
                        )
                else:
                    logger.info("Streaming transcription disabled")
                    if not initial_setup:
                        self.controller.ui_controller.set_status("Streaming mode disabled")

                self.controller._streaming_enabled = False
                self.controller._streaming_paste_enabled = False
        except Exception as exc:
            logger.error(f"Failed to setup streaming: {exc}")
            self.controller._streaming_enabled = False
            self.controller._streaming_paste_enabled = False
            if not initial_setup:
                self.controller.ui_controller.set_status("Failed to reconfigure streaming")

    def _type_live_update(self, text: str) -> None:
        if not text or self.controller._live_typing_failed:
            return

        try:
            settings = settings_manager.load_all_settings()
            enabled = settings.get(SettingsKey.LIVE_TYPE_ENABLED, config.LIVE_TYPE_ENABLED)
            if not enabled:
                return

            result = text_injector.update_live_text(
                self.controller._live_typed_text,
                text,
                key_delay_ms=int(
                    settings.get(
                        SettingsKey.TEXT_INJECTION_KEY_DELAY_MS,
                        config.TEXT_INJECTION_KEY_DELAY_MS,
                    )
                ),
            )
            if result.success:
                self.controller._live_typed_text = text
                logger.info("Live typed streaming update (%s chars)", len(text))
            else:
                self.controller._live_typing_failed = True
                logger.error("Live typing failed: %s", result.error)
        except Exception as exc:
            self.controller._live_typing_failed = True
            logger.error("Live typing failed: %s", exc)

    def _cleanup_streaming_resources(self) -> None:
        if self.controller.streaming_transcriber:
            try:
                self.controller.streaming_transcriber.cleanup()
                logger.info("Cleaned up existing streaming transcriber")
            except Exception as exc:
                logger.warning(f"Error cleaning up streaming transcriber: {exc}")
            self.controller.streaming_transcriber = None

        if self.controller._streaming_backend:
            try:
                logger.info("Cleaning up dedicated streaming backend...")
                self.controller._streaming_backend.cleanup()
                logger.info("Cleaned up dedicated streaming backend")
            except Exception as exc:
                logger.warning(f"Error cleaning up streaming backend: {exc}")
            self.controller._streaming_backend = None
