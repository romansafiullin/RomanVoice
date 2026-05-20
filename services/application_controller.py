"""Main Qt-facing application controller."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from typing import Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from config import config
from services.database import db
from services.dictation_service import RomanVoiceDictationService
from services.recorder import AudioRecorder
from services.runtime import (
    HotkeyRuntime,
    StreamingRuntime,
    TranscriptionRuntime,
)
from services.gpu_guard import gpu_guard
from services.settings import settings_manager
from transcriber import LocalWhisperBackend, TranscriptionBackend

logger = logging.getLogger(__name__)


class ApplicationController(QObject):
    """Main application controller integrating UI and logic."""

    transcription_completed = pyqtSignal(str)
    transcription_failed = pyqtSignal(str)
    status_update = pyqtSignal(str)
    device_info_update = pyqtSignal(str)
    stt_state_changed = pyqtSignal(bool)
    recording_state_changed = pyqtSignal(bool)
    partial_transcription = pyqtSignal(str, bool)
    streaming_text_update = pyqtSignal(str, bool)
    streaming_overlay_show = pyqtSignal()
    streaming_overlay_hide = pyqtSignal()
    caret_indicator_show = pyqtSignal()
    caret_indicator_hide = pyqtSignal()
    overlay_state_update = pyqtSignal(object)

    def __init__(self, ui_controller):
        super().__init__()
        self.ui_controller = ui_controller

        saved_device_id = settings_manager.load_audio_input_device()
        self.recorder = AudioRecorder(device_id=saved_device_id)
        self.executor = ThreadPoolExecutor(max_workers=2)

        self.hotkey_manager = None
        self.streaming_transcriber = None
        self._streaming_backend = None
        self._cpu_fallback_backend = None
        self._shutdown_requested = Event()
        self._transcription_lock = RLock()
        self.dictation_service = None

        self.transcription_backends: Dict[str, TranscriptionBackend] = {}
        self.current_backend: Optional[TranscriptionBackend] = None
        self._active_transcription_backend: Optional[TranscriptionBackend] = None
        self._current_model_name = "local_whisper"

        self._streaming_enabled = False
        self._streaming_paste_enabled = False
        self._last_streaming_text = ""
        self._best_streaming_text = ""
        self._streaming_guard_evaluated = False
        self._live_typed_text = ""
        self._live_typing_failed = False
        self._last_gpu_unload_time = 0.0
        self._last_gpu_warmup_defer_log_time = 0.0
        self._silence_auto_stop_started_at = 0.0
        self._last_voice_activity_time = 0.0
        self._silence_auto_stop_triggered = False

        self._pending_audio_path: Optional[str] = None
        self._pending_audio_duration: Optional[float] = None
        self._pending_file_size: Optional[int] = None
        self._transcription_start_time: Optional[float] = None
        self._transcription_job_id = 0

        self.hotkey_runtime = HotkeyRuntime(self)
        self.streaming_runtime = StreamingRuntime(self)
        self.transcription_runtime = TranscriptionRuntime(self)

        self._setup_transcription_backends()
        self._setup_ui_callbacks()
        self.hotkey_runtime.setup_hotkeys()
        self.streaming_runtime.setup_audio_level_callback()
        self.streaming_runtime.setup_streaming()
        self._start_dictation_service()
        self._connect_signals()
        self._setup_gpu_cooperation_monitor()
        self._setup_silence_auto_stop_monitor()
        if config.PRELOAD_WHISPER_ON_START:
            self._preload_local_whisper_async()
        self.hotkey_runtime.setup_hook_watchdog()

    def _start_dictation_service(self) -> None:
        try:
            self.dictation_service = RomanVoiceDictationService(self)
            self.dictation_service.start()
        except Exception as exc:
            self.dictation_service = None
            logger.warning("Failed to start RomanVoice dictation service: %s", exc)

    def _setup_transcription_backends(self) -> None:
        """Initialize transcription backends."""
        logger.info("Setting up transcription backends...")

        try:
            self.transcription_backends["local_whisper"] = LocalWhisperBackend(autoload=False)
        except TypeError:  # pragma: no cover - supports lightweight test doubles
            self.transcription_backends["local_whisper"] = LocalWhisperBackend()

        saved_model = settings_manager.load_model_selection()
        self.current_backend = self.transcription_backends.get(
            saved_model, self.transcription_backends["local_whisper"]
        )
        logger.info(f"Using transcription backend: {saved_model}")

    def _preload_local_whisper_async(self) -> None:
        """Warm the local model after Qt signals are connected."""
        local_backend = self.transcription_backends.get("local_whisper")
        if not local_backend:
            return

        self.status_update.emit("Warming Whisper engine...")

        def _load() -> None:
            try:
                if (
                    config.GPU_COOPERATIVE_MODE
                    and getattr(local_backend, "prefers_cuda", False)
                ):
                    while not self._shutdown_requested.is_set():
                        status = gpu_guard.query_status()
                        defer_reason = self._gpu_warmup_defer_reason(status)
                        if not defer_reason:
                            break
                        logger.info("Deferring Whisper warmup; %s", defer_reason)
                        self.status_update.emit(
                            "Ready (GPU busy; Whisper warmup deferred)"
                        )
                        if self._shutdown_requested.wait(
                            config.GPU_BUSY_WARMUP_RETRY_MS / 1000
                        ):
                            return

                if hasattr(local_backend, "ensure_loaded"):
                    local_backend.ensure_loaded()
                if hasattr(local_backend, "device_info"):
                    logger.info("Whisper warmed: %s", local_backend.device_info)
                    self.device_info_update.emit(local_backend.device_info)
                self.status_update.emit("Ready")
            except Exception as exc:
                logger.error("Whisper warmup failed: %s", exc)
                self.status_update.emit(f"Whisper warmup failed: {exc}")

        self.executor.submit(_load)

    def _setup_gpu_cooperation_monitor(self) -> None:
        """Periodically release/reload Whisper based on shared GPU pressure."""
        if not config.GPU_COOPERATIVE_MODE:
            self._gpu_coop_timer = None
            return

        self._gpu_coop_timer = QTimer()
        self._gpu_coop_timer.timeout.connect(self._on_gpu_cooperation_tick)
        self._gpu_coop_timer.start(config.GPU_COOPERATIVE_MONITOR_MS)

    def _setup_silence_auto_stop_monitor(self) -> None:
        """Monitor active recordings and stop after sustained silence."""
        if not config.AUTO_STOP_ON_SILENCE:
            self._silence_auto_stop_timer = None
            return

        self._silence_auto_stop_timer = QTimer()
        self._silence_auto_stop_timer.timeout.connect(
            self._on_silence_auto_stop_tick
        )
        self._silence_auto_stop_timer.start(config.AUTO_STOP_CHECK_INTERVAL_MS)

    def start_silence_auto_stop_monitor(self) -> None:
        now = time.monotonic()
        self._silence_auto_stop_started_at = now
        self._last_voice_activity_time = now
        self._silence_auto_stop_triggered = False

    def stop_silence_auto_stop_monitor(self) -> None:
        self._silence_auto_stop_started_at = 0.0
        self._last_voice_activity_time = 0.0

    def note_voice_activity(self) -> None:
        if self.recorder.is_recording and not self._silence_auto_stop_triggered:
            self._last_voice_activity_time = time.monotonic()

    def note_recording_audio_level(self, level: float) -> None:
        if level >= config.AUTO_STOP_SPEECH_LEVEL_THRESHOLD:
            self.note_voice_activity()

    def _on_silence_auto_stop_tick(self) -> None:
        if (
            not config.AUTO_STOP_ON_SILENCE
            or not self.recorder.is_recording
            or self._silence_auto_stop_triggered
            or self._silence_auto_stop_started_at <= 0
        ):
            return

        now = time.monotonic()
        last_activity = max(
            self._last_voice_activity_time,
            self._silence_auto_stop_started_at,
        )
        silence_for = now - last_activity
        if silence_for < config.AUTO_STOP_SILENCE_SECONDS:
            return

        self._silence_auto_stop_triggered = True
        logger.info(
            "Auto-stopping recording after %.1fs without speech",
            silence_for,
        )
        self.status_update.emit("No speech detected; stopping...")
        self.stop_recording()

    def _on_gpu_cooperation_tick(self) -> None:
        local_backend = self.transcription_backends.get("local_whisper")
        if not isinstance(local_backend, LocalWhisperBackend):
            return

        active_backend = self._active_transcription_backend or self.current_backend
        if self.recorder.is_recording or (
            active_backend and active_backend.is_transcribing
        ):
            return

        status = gpu_guard.query_status()
        busy_reason = status.busy_reason()

        if (
            busy_reason
            and local_backend.uses_cuda
            and local_backend.is_available()
            and not local_backend.is_loading
        ):
            logger.info("Unloading Whisper while CUDA is busy: %s", busy_reason)
            self._last_gpu_unload_time = time.monotonic()
            self.device_info_update.emit("Whisper unloaded while GPU busy")
            self.executor.submit(local_backend.cleanup)
            return

        if (
            not busy_reason
            and config.PRELOAD_WHISPER_ON_START
            and not local_backend.is_available()
            and not local_backend.is_loading
        ):
            defer_reason = self._gpu_warmup_defer_reason(status)
            if defer_reason:
                now = time.monotonic()
                if now - self._last_gpu_warmup_defer_log_time >= 60.0:
                    logger.info("Deferring Whisper warmup; %s", defer_reason)
                    self._last_gpu_warmup_defer_log_time = now
                return

            logger.info("CUDA budget recovered; warming Whisper again")
            self._preload_local_whisper_async()

    def _gpu_warmup_defer_reason(self, status) -> Optional[str]:
        busy_reason = status.busy_reason()
        if busy_reason:
            return f"CUDA busy: {busy_reason}"

        if getattr(status, "available", False):
            free_mb = status.memory_free_mb
            if (
                free_mb is not None
                and free_mb < config.GPU_WARMUP_MIN_FREE_MEMORY_MB
            ):
                return (
                    f"free memory {free_mb} MB below warmup threshold "
                    f"{config.GPU_WARMUP_MIN_FREE_MEMORY_MB} MB"
                )

        if self._last_gpu_unload_time:
            elapsed_ms = (time.monotonic() - self._last_gpu_unload_time) * 1000
            remaining_ms = config.GPU_COOPERATIVE_RELOAD_COOLDOWN_MS - elapsed_ms
            if remaining_ms > 0:
                remaining_sec = int((remaining_ms + 999) // 1000)
                return f"reload cooldown active for {remaining_sec}s"

        return None

    def _setup_ui_callbacks(self) -> None:
        """Setup UI event callbacks."""
        self.ui_controller.on_record_start = self.start_recording
        self.ui_controller.on_record_stop = self.stop_recording
        self.ui_controller.on_record_cancel = self.cancel
        self.ui_controller.on_model_changed = self.on_model_changed
        self.ui_controller.on_hotkeys_changed = self.update_hotkeys
        self.ui_controller.on_retranscribe = self.retranscribe_audio
        self.ui_controller.on_upload_audio = self.upload_audio_file
        self.ui_controller.on_whisper_settings_changed = self.reload_whisper_model
        self.ui_controller.on_audio_device_changed = self.change_audio_device
        self.ui_controller.on_streaming_settings_changed = self.reconfigure_streaming

    def reload_whisper_model(self) -> None:
        """Reload the local whisper model with current settings."""
        logger.info("Reloading whisper model...")
        self.ui_controller.set_status("Reloading whisper engine...")

        local_backend = self.transcription_backends.get("local_whisper")
        if local_backend:
            local_backend.reload_model()

            if hasattr(local_backend, "device_info"):
                self.ui_controller.set_device_info(local_backend.device_info)
                logger.info(f"Whisper reloaded: {local_backend.device_info}")

            self.ui_controller.set_status("Whisper engine reloaded")
        else:
            logger.warning("Local whisper backend not found")
            self.ui_controller.set_status("Ready")

    def change_audio_device(self, device_id: Optional[int]) -> None:
        """Change the audio input device."""
        logger.info(f"Changing audio device to: {device_id}")

        if self.recorder.is_recording:
            logger.warning("Cannot change audio device while recording")
            self.ui_controller.set_status("Stop recording before changing device")
            return

        self.recorder.cleanup()
        self.recorder = AudioRecorder(device_id=device_id)
        self.streaming_runtime.setup_audio_level_callback()

        device_name = "System Default" if device_id is None else f"Device {device_id}"
        logger.info(f"Audio device changed to: {device_name}")
        self.ui_controller.set_status("Audio device changed")

    def update_hotkeys(self, hotkeys: Dict[str, str]) -> None:
        self.hotkey_runtime.update_hotkeys(hotkeys)

    def reconfigure_streaming(self) -> None:
        self.streaming_runtime.reconfigure_streaming()

    def start_recording(self) -> None:
        """Start audio recording (UI callback target)."""
        self.transcription_runtime.start_recording()

    def stop_recording(self) -> None:
        """Stop recording and submit transcription (UI callback target)."""
        self.transcription_runtime.stop_recording()

    def toggle_recording(self) -> None:
        """Toggle recording on/off (hotkey callback target)."""
        self.transcription_runtime.toggle_recording()

    def cancel(self) -> None:
        """Cancel an active recording or transcription (UI/hotkey callback target)."""
        self.transcription_runtime.cancel()

    def retranscribe_audio(self, audio_path: str) -> None:
        """Re-transcribe an existing audio file (UI callback target)."""
        self.transcription_runtime.retranscribe_audio(audio_path)

    def upload_audio_file(self, audio_path: str) -> None:
        """Transcribe an uploaded audio file (UI callback target)."""
        self.transcription_runtime.upload_audio_file(audio_path)

    def on_model_changed(self, model_name: str) -> None:
        """Switch the active transcription backend (UI callback target)."""
        self.transcription_runtime.on_model_changed(model_name)

    def update_status_with_auto_hide(self, status: str) -> None:
        """Emit a thread-safe status update (HotkeyManager callback target)."""
        self.hotkey_runtime.update_status_with_auto_hide(status)

    def _connect_signals(self) -> None:
        """Connect Qt signals to UI controller methods."""
        self.transcription_completed.connect(self._on_transcription_complete)
        self.transcription_failed.connect(self._on_transcription_error)
        self.status_update.connect(self.ui_controller.set_status)
        self.device_info_update.connect(self.ui_controller.set_device_info)
        if hasattr(self.ui_controller, "set_overlay_state"):
            self.overlay_state_update.connect(self.ui_controller.set_overlay_state)
        self.stt_state_changed.connect(self.hotkey_runtime.on_stt_state_changed)
        self.recording_state_changed.connect(self._on_recording_state_changed)
        self.partial_transcription.connect(
            self.ui_controller.main_window.set_partial_transcription
        )
        if hasattr(self.ui_controller, "set_live_preview"):
            self.partial_transcription.connect(self.ui_controller.set_live_preview)
        self.streaming_text_update.connect(self.ui_controller.update_streaming_text)
        self.streaming_overlay_show.connect(self.ui_controller.show_streaming_overlay)
        self.streaming_overlay_hide.connect(self.ui_controller.hide_streaming_overlay)
        self.caret_indicator_show.connect(
            self.ui_controller.show_caret_paste_indicator
        )
        self.caret_indicator_hide.connect(
            self.ui_controller.hide_caret_paste_indicator
        )

    def _on_recording_state_changed(self, is_recording: bool) -> None:
        """Handle recording state change on main thread."""
        self.ui_controller.is_recording = is_recording
        if self.ui_controller.main_window.is_recording != is_recording:
            self.ui_controller.main_window.is_recording = is_recording
            self.ui_controller.main_window._update_recording_state()

    def _on_transcription_complete(self, transcript: str) -> None:
        self.transcription_runtime.on_transcription_complete(transcript)

    def _on_transcription_error(self, error_message: str) -> None:
        self.transcription_runtime.on_transcription_error(error_message)

    def cleanup(self) -> None:
        """Cleanup resources."""
        logger.info("Starting application cleanup...")
        self._shutdown_requested.set()

        try:
            active_backend = self._active_transcription_backend or self.current_backend
            if active_backend and active_backend.is_transcribing:
                logger.info("Canceling ongoing transcription...")
                active_backend.cancel_transcription()
        except Exception as exc:
            logger.debug(f"Error canceling transcription: {exc}")

        try:
            if self.dictation_service:
                self.dictation_service.stop()
                self.dictation_service = None
        except Exception as exc:
            logger.debug(f"Error stopping dictation service: {exc}")

        try:
            if hasattr(self, "_watchdog_timer") and self._watchdog_timer:
                self._watchdog_timer.stop()
            if hasattr(self, "_periodic_refresh_timer") and self._periodic_refresh_timer:
                self._periodic_refresh_timer.stop()
            if hasattr(self, "_gpu_coop_timer") and self._gpu_coop_timer:
                self._gpu_coop_timer.stop()
            if hasattr(self, "_silence_auto_stop_timer") and self._silence_auto_stop_timer:
                self._silence_auto_stop_timer.stop()
        except Exception as exc:
            logger.debug(f"Error stopping watchdog timers: {exc}")

        try:
            if self.hotkey_manager:
                self.hotkey_manager.cleanup()
        except Exception as exc:
            logger.debug(f"Error during hotkey cleanup: {exc}")

        try:
            if self.recorder:
                self.recorder.cleanup()
        except Exception as exc:
            logger.debug(f"Error during recorder cleanup: {exc}")

        try:
            self.streaming_runtime.cleanup()
        except Exception as exc:
            logger.debug(f"Error during streaming cleanup: {exc}")

        try:
            self.executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            self.executor.shutdown(wait=False)
        except Exception as exc:
            logger.debug(f"Error during executor shutdown: {exc}")

        try:
            for backend_name, backend in self.transcription_backends.items():
                try:
                    logger.info(f"Cleaning up transcription backend: {backend_name}")
                    backend.cleanup()
                except Exception as exc:
                    logger.debug(f"Error cleaning up {backend_name} backend: {exc}")
            self.transcription_backends.clear()
            self.current_backend = None
        except Exception as exc:
            logger.debug(f"Error during transcription backends cleanup: {exc}")

        try:
            if self._cpu_fallback_backend is not None:
                logger.info("Cleaning up CPU fallback backend")
                self._cpu_fallback_backend.cleanup()
                self._cpu_fallback_backend = None
        except Exception as exc:
            logger.debug(f"Error during CPU fallback backend cleanup: {exc}")

        try:
            self.ui_controller.cleanup()
        except Exception as exc:
            logger.debug(f"Error during UI controller cleanup: {exc}")

        try:
            db.close()
        except Exception as exc:
            logger.debug(f"Error closing database: {exc}")

        logger.info("Application controller cleaned up")
