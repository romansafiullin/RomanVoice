"""Recording and transcription helpers for the application controller."""

from __future__ import annotations

import logging
import os
import time
from contextlib import nullcontext
from typing import TYPE_CHECKING

import pyperclip

from config import config
from services.audio_processor import audio_processor
from services.gpu_guard import gpu_guard
from services.history_manager import history_manager
from services.polisher import local_polisher
from services.text_injector import text_injector
from transcriber import LocalWhisperBackend
try:
    from services.settings import SettingsKey, settings_manager
except ImportError:  # pragma: no cover - supports lightweight test stubs
    from services.settings import settings_manager

    class SettingsKey:
        AUTO_PASTE = "auto_paste"
        COPY_CLIPBOARD = "copy_clipboard"
        TEXT_INJECTION_MODE = "text_injection_mode"
        TEXT_INJECTION_KEY_DELAY_MS = "text_injection_key_delay_ms"
        TEXT_INJECTION_LONG_TEXT_THRESHOLD = "text_injection_long_text_threshold"
        LIVE_TYPE_ENABLED = "live_type_enabled"
        POLISH_ENABLED = "polish_enabled"
        POLISH_MODEL = "polish_model"
        POLISH_WORD_THRESHOLD = "polish_word_threshold"
        POLISH_TIMEOUT_MS = "polish_timeout_ms"
        POLISH_OLLAMA_URL = "polish_ollama_url"

from ui_qt.overlay_state import OverlayState

if TYPE_CHECKING:
    from services.application_controller import ApplicationController

logger = logging.getLogger(__name__)


class TranscriptionRuntime:
    """Owns recording flow and transcription job orchestration."""

    def __init__(self, controller: "ApplicationController"):
        self.controller = controller

    def start_recording(self) -> None:
        """Start audio recording."""
        if self.controller.recorder.start_recording():
            logger.info("Recording started")
            self.controller._last_streaming_text = ""
            self.controller._best_streaming_text = ""
            self.controller._streaming_guard_evaluated = False
            self.controller._live_typed_text = ""
            self.controller._live_typing_failed = False
            self.controller.ui_controller.clear_transcription_stats()
            self.controller.ui_controller.main_window.clear_partial_transcription()
            self.controller.start_silence_auto_stop_monitor()
            self.controller.streaming_runtime.start_streaming_session()
            self.controller.recording_state_changed.emit(True)
            self.controller.overlay_state_update.emit(OverlayState.RECORDING)
            self.controller.status_update.emit("Recording...")
        else:
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Failed to start recording")

    def stop_recording(self) -> None:
        """Stop audio recording and start transcription."""
        self.controller.stop_silence_auto_stop_monitor()

        if self.controller._streaming_paste_enabled:
            self.controller.streaming_overlay_hide.emit()
            settings = settings_manager.load_all_settings()
            if settings.get(SettingsKey.AUTO_PASTE, True):
                self.controller.caret_indicator_show.emit()

        self.controller.streaming_runtime.stop_streaming_session()

        if not self.controller.recorder.stop_recording():
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Failed to stop recording")
            return

        self.controller.recording_state_changed.emit(False)
        self.controller.overlay_state_update.emit(OverlayState.PROCESSING)
        self.controller.status_update.emit("Processing...")

        if not self.controller.recorder.wait_for_stop_completion():
            logger.warning(
                "Proceeding without confirmed post-roll completion; "
                "tail of recording may be short"
            )

        if not self.controller.recorder.has_recording_data():
            logger.error("No recording data available")
            self.on_transcription_error("No audio data recorded")
            return

        if not self.controller.recorder.save_recording():
            logger.error("Failed to save recording")
            self.on_transcription_error("Failed to save audio file")
            return

        if not os.path.exists(config.RECORDED_AUDIO_FILE):
            logger.error(f"Audio file not found: {config.RECORDED_AUDIO_FILE}")
            self.on_transcription_error("Audio file not created")
            return

        file_size = os.path.getsize(config.RECORDED_AUDIO_FILE)
        logger.info(f"Audio file size: {file_size} bytes")
        if file_size < 100:
            logger.error(f"Audio file too small: {file_size} bytes")
            self.on_transcription_error("Audio file is empty or corrupted")
            return

        metrics_fn = getattr(self.controller.recorder, "get_recording_signal_metrics", None)
        if callable(metrics_fn):
            metrics = metrics_fn()
            logger.info("Recording signal metrics: %s", metrics)
            samples = int(metrics.get("samples", 0))
            peak = int(metrics.get("peak", 0))
            if samples <= 0 or peak <= 0:
                self.on_transcription_error(
                    "No microphone input detected. Check the selected microphone/input level."
                )
                return
            if peak < config.MIN_MIC_INPUT_PEAK:
                logger.warning(
                    "Recording peak is low (%s < %s); continuing because Whisper can "
                    "still transcribe quiet microphone input",
                    peak,
                    config.MIN_MIC_INPUT_PEAK,
                )

        self.controller._pending_audio_path = config.RECORDED_AUDIO_FILE
        self.controller._pending_audio_duration = (
            self.controller.recorder.get_recording_duration()
        )
        self.controller._pending_file_size = file_size

        try:
            self._submit_transcription_job(config.RECORDED_AUDIO_FILE)
            logger.info(
                "Transcription started. Duration: "
                f"{self.controller.recorder.get_recording_duration():.2f}s"
            )
        except Exception as exc:
            logger.error(f"Failed to start transcription: {exc}")
            self.on_transcription_error(f"Failed to process audio: {exc}")

    def toggle_recording(self) -> None:
        """Toggle between starting and stopping recording."""
        logger.info(
            f"Toggle recording. Current state: {self.controller.recorder.is_recording}"
        )
        if not self.controller.recorder.is_recording:
            active_backend = self._get_active_transcription_backend()
            if active_backend and active_backend.is_transcribing:
                logger.info("Start requested during transcription; canceling and restarting")
                self._cancel_transcription()
            self.start_recording()
        else:
            self.stop_recording()

    def cancel(self) -> None:
        """Cancel an active recording or transcription, depending on state."""
        logger.info(f"Cancel called. Recording: {self.controller.recorder.is_recording}")

        if self.controller.recorder.is_recording:
            self._cancel_recording()
        elif (
            self._get_active_transcription_backend()
            and self._get_active_transcription_backend().is_transcribing
        ):
            self._cancel_transcription()
        else:
            self.controller.overlay_state_update.emit(OverlayState.CANCELING)
            self.controller.status_update.emit("Canceled")

    def _cancel_recording(self) -> None:
        """Discard the active recording without transcribing."""
        self.controller.stop_silence_auto_stop_monitor()
        self.controller.streaming_runtime.cancel_streaming_session()
        self.controller.recording_state_changed.emit(False)
        self.controller.recorder.stop_recording()
        self.controller.recorder.clear_recording_data()
        self.controller.overlay_state_update.emit(OverlayState.CANCELING)
        self.controller.status_update.emit("Recording canceled")
        logger.info("Recording canceled")

    def _cancel_transcription(self) -> None:
        """Cancel an in-progress transcription job."""
        self.controller._transcription_job_id += 1
        active_backend = self._get_active_transcription_backend()
        if active_backend:
            active_backend.cancel_transcription()
        self.controller._pending_audio_path = None
        self.controller._pending_audio_duration = None
        self.controller._pending_file_size = None
        self.controller._transcription_start_time = None
        self.controller.overlay_state_update.emit(OverlayState.CANCELING)
        self.controller.status_update.emit("Transcription canceled")
        logger.info("Transcription canceled")

    def retranscribe_audio(self, audio_path: str) -> None:
        """Re-transcribe an existing audio file."""
        if not os.path.exists(audio_path):
            logger.error(
                f"Audio file not found for re-transcription: {audio_path}"
            )
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Error: Audio file not found")
            return

        logger.info(f"Re-transcribing audio file: {audio_path}")
        self.controller._pending_audio_path = None
        self.controller.overlay_state_update.emit(OverlayState.PROCESSING)
        self.controller.status_update.emit("Processing...")

        try:
            self.controller._pending_file_size = os.path.getsize(audio_path)
            self.controller._pending_audio_duration = None
            self._submit_transcription_job(audio_path)
        except Exception as exc:
            logger.error(f"Failed to start re-transcription: {exc}")
            self.on_transcription_error(f"Failed to process audio: {exc}")

    def upload_audio_file(self, audio_path: str) -> None:
        """Transcribe an uploaded audio file."""
        if not os.path.exists(audio_path):
            logger.error(f"Uploaded audio file not found: {audio_path}")
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Error: Audio file not found")
            return

        logger.info(f"Processing uploaded audio file: {audio_path}")
        self.controller._pending_audio_path = None
        self.controller.overlay_state_update.emit(OverlayState.PROCESSING)
        self.controller.status_update.emit("Processing uploaded file...")

        try:
            self.controller._pending_file_size = os.path.getsize(audio_path)
            self.controller._pending_audio_duration = None
            self._submit_transcription_job(audio_path)
        except Exception as exc:
            logger.error(f"Failed to process uploaded audio: {exc}")
            self.on_transcription_error(f"Failed to process audio: {exc}")

    def transcribe_audio_file(self, audio_path: str, job_id: int) -> None:
        """Transcribe a single audio file in a background thread."""
        try:
            if self.controller._pending_file_size is None:
                self.controller._pending_file_size = os.path.getsize(audio_path)
            self.controller.overlay_state_update.emit(OverlayState.TRANSCRIBING)
            self.controller.status_update.emit("Transcribing...")
            self.controller._transcription_start_time = time.time()
            lock = getattr(self.controller, "_transcription_lock", None)
            with lock if lock is not None else nullcontext():
                backend = self._select_backend_for_transcription()
                self.controller._active_transcription_backend = backend
                transcript = backend.transcribe(audio_path)
                transcript = self._choose_transcript_with_streaming_guard(
                    transcript,
                    context="final pass",
                )
                self.controller._streaming_guard_evaluated = True
                transcript = self._maybe_polish_transcript(transcript)
            if self._is_current_job(job_id):
                self.controller.transcription_completed.emit(transcript)
            else:
                logger.info("Discarded stale transcription result for job %s", job_id)
        except Exception as exc:
            logger.error(f"Transcription failed: {exc}")
            if self._is_current_job(job_id):
                self.controller.transcription_failed.emit(str(exc))
        finally:
            self.controller._active_transcription_backend = None

    def transcribe_large_audio_file(self, audio_path: str, job_id: int) -> None:
        """Transcribe a large audio file by splitting it into chunks."""
        chunk_files = []
        if self.controller._pending_file_size is None:
            self.controller._pending_file_size = os.path.getsize(audio_path)
        self.controller._transcription_start_time = time.time()
        try:
            def progress_callback(message: str) -> None:
                self.controller.status_update.emit(message)

            chunk_files = audio_processor.split_audio_file(
                audio_path, progress_callback
            )
            if not chunk_files:
                raise Exception("Failed to split audio file")

            lock = getattr(self.controller, "_transcription_lock", None)
            with lock if lock is not None else nullcontext():
                backend = self._select_backend_for_transcription()
                self.controller._active_transcription_backend = backend

                if hasattr(backend, "transcribe_chunks"):
                    self.controller.overlay_state_update.emit(OverlayState.TRANSCRIBING)
                    self.controller.status_update.emit(
                        f"Transcribing {len(chunk_files)} chunks..."
                    )
                    transcript = backend.transcribe_chunks(chunk_files)
                else:
                    transcripts = []
                    for index, chunk_file in enumerate(chunk_files):
                        self.controller.overlay_state_update.emit(OverlayState.TRANSCRIBING)
                        self.controller.status_update.emit(
                            f"Transcribing chunk {index + 1}/{len(chunk_files)}..."
                        )
                        transcripts.append(backend.transcribe(chunk_file))
                    transcript = audio_processor.combine_transcriptions(transcripts)

                transcript = self._choose_transcript_with_streaming_guard(
                    transcript,
                    context="large final pass",
                )
                self.controller._streaming_guard_evaluated = True

                transcript = self._maybe_polish_transcript(transcript)
            if self._is_current_job(job_id):
                self.controller.transcription_completed.emit(transcript)
            else:
                logger.info("Discarded stale large transcription result for job %s", job_id)
        except Exception as exc:
            logger.error(f"Large audio transcription failed: {exc}")
            if self._is_current_job(job_id):
                self.controller.transcription_failed.emit(str(exc))
        finally:
            self.controller._active_transcription_backend = None
            try:
                audio_processor.cleanup_temp_files()
            except Exception as cleanup_error:
                logger.warning(
                    f"Failed to cleanup temp files: {cleanup_error}"
                )

    def on_transcription_complete(self, transcript: str) -> None:
        """Handle transcription completion."""
        if self.controller._streaming_guard_evaluated:
            transcript = (transcript or "").strip()
        else:
            transcript = self._choose_transcript_with_streaming_guard(
                transcript,
                context="completion",
            )

        if not transcript.strip():
            logger.info("Empty transcript; skipping history and text injection")
            self.controller.ui_controller.set_transcript("")
            self.controller.ui_controller.set_status("No speech detected")
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller._pending_audio_path = None
            self.controller._pending_audio_duration = None
            self.controller._pending_file_size = None
            self.controller._transcription_start_time = None
            if self.controller._streaming_paste_enabled:
                self.controller.caret_indicator_hide.emit()
            self.controller._last_streaming_text = ""
            self.controller._best_streaming_text = ""
            self.controller._streaming_guard_evaluated = False
            self.controller._live_typed_text = ""
            self.controller._live_typing_failed = False
            return

        self.controller.ui_controller.set_transcript(transcript)
        self.controller.ui_controller.set_status("Transcription complete!")
        self.controller.overlay_state_update.emit(OverlayState.NONE)

        transcription_time = None
        if self.controller._transcription_start_time is not None:
            transcription_time = time.time() - self.controller._transcription_start_time
            self.controller._transcription_start_time = None

        if transcription_time is not None:
            self.controller.ui_controller.set_transcription_stats(
                transcription_time,
                self.controller._pending_audio_duration or 0.0,
                self.controller._pending_file_size or 0,
            )

        try:
            model_info = self.controller._current_model_name
            if self.controller._current_model_name == "local_whisper":
                local_backend = self.controller.transcription_backends.get("local_whisper")
                if local_backend and hasattr(local_backend, "device_info"):
                    model_info = f"local_whisper ({local_backend.device_info})"

            history_manager.add_entry(
                text=transcript,
                model=model_info,
                source_audio_path=self.controller._pending_audio_path,
                transcription_time=transcription_time,
                audio_duration=self.controller._pending_audio_duration,
                file_size=self.controller._pending_file_size,
            )
            self.controller.ui_controller.refresh_history()
            logger.info("Transcription saved to history")
        except Exception as exc:
            logger.error(f"Failed to save transcription to history: {exc}")
        finally:
            self.controller._pending_audio_path = None
            self.controller._pending_audio_duration = None
            self.controller._pending_file_size = None

        settings = settings_manager.load_all_settings()
        copy_clipboard = settings.get(
            SettingsKey.COPY_CLIPBOARD, config.DEFAULT_COPY_CLIPBOARD
        )
        auto_paste = settings.get(SettingsKey.AUTO_PASTE, config.DEFAULT_AUTO_PASTE)
        live_type_enabled = settings.get(
            SettingsKey.LIVE_TYPE_ENABLED, config.LIVE_TYPE_ENABLED
        )
        key_delay_ms = int(
            settings.get(
                SettingsKey.TEXT_INJECTION_KEY_DELAY_MS,
                config.TEXT_INJECTION_KEY_DELAY_MS,
            )
        )
        injection_mode = settings.get(
            SettingsKey.TEXT_INJECTION_MODE, config.TEXT_INJECTION_MODE
        )
        long_text_threshold = int(
            settings.get(
                SettingsKey.TEXT_INJECTION_LONG_TEXT_THRESHOLD,
                config.TEXT_INJECTION_LONG_TEXT_THRESHOLD,
            )
        )
        inserted_transcript = False
        text_injection_failed = False

        if live_type_enabled:
            previous_live_text = self.controller._live_typed_text
            result = text_injector.update_live_text(
                previous_live_text,
                transcript,
                key_delay_ms=key_delay_ms,
            )
            if result.success:
                self.controller._live_typed_text = transcript
                inserted_transcript = True
                logger.info("Live typed final transcript (%s chars)", len(transcript))
            else:
                logger.error("Failed to reconcile live typed transcript: %s", result.error)
                if not previous_live_text:
                    fallback = text_injector.inject(
                        transcript,
                        mode=injection_mode,
                        key_delay_ms=key_delay_ms,
                        long_text_threshold=long_text_threshold,
                    )
                    if fallback.success:
                        inserted_transcript = True
                        logger.info(
                            "Transcription inserted via %s after live typing failed",
                            fallback.method,
                        )
                    else:
                        text_injection_failed = True
                        logger.error(
                            "Failed to inject transcription after live typing failed: %s",
                            fallback.error,
                        )
                else:
                    text_injection_failed = True
                    logger.warning(
                        "Skipping full-text fallback because %s chars were already live typed",
                        len(previous_live_text),
                    )

        if auto_paste and not live_type_enabled:
            result = text_injector.inject(
                transcript,
                mode=injection_mode,
                key_delay_ms=key_delay_ms,
                long_text_threshold=long_text_threshold,
            )
            if result.success:
                inserted_transcript = True
                logger.info("Transcription injected via %s", result.method)
            else:
                text_injection_failed = True
                logger.error("Failed to inject transcription: %s", result.error)

        if inserted_transcript:
            self.controller.ui_controller.set_status("Ready (Pasted)")
        elif text_injection_failed:
            self.controller.ui_controller.set_status(
                "Transcription complete (text injection failed)"
            )
        else:
            self.controller.ui_controller.set_status("Ready")

        if copy_clipboard:
            try:
                pyperclip.copy(transcript)
                logger.info("Transcription copied to clipboard")
            except Exception as exc:
                logger.error(f"Failed to copy to clipboard: {exc}")

        if self.controller._streaming_paste_enabled:
            self.controller.caret_indicator_hide.emit()

        self.controller._last_streaming_text = ""
        self.controller._best_streaming_text = ""
        self.controller._streaming_guard_evaluated = False
        self.controller._live_typed_text = ""
        self.controller._live_typing_failed = False

    def _choose_transcript_with_streaming_guard(
        self,
        transcript: str,
        *,
        context: str,
    ) -> str:
        final_text = (transcript or "").strip()
        streaming_text = self.controller._last_streaming_text.strip()
        best_streaming_text = getattr(self.controller, "_best_streaming_text", "").strip()
        if len(best_streaming_text) > len(streaming_text):
            streaming_text = best_streaming_text
        if not streaming_text:
            return final_text

        if not final_text:
            logger.info(
                "Using streaming transcript fallback after empty %s (%s chars)",
                context,
                len(streaming_text),
            )
            return streaming_text

        duration = self.controller._pending_audio_duration or 0.0
        char_delta = len(streaming_text) - len(final_text)
        final_ratio = len(final_text) / max(len(streaming_text), 1)
        if (
            duration >= config.LONG_FORM_STREAMING_FALLBACK_MIN_SECONDS
            and len(streaming_text) >= config.LONG_FORM_STREAMING_FALLBACK_MIN_CHARS
            and char_delta >= config.LONG_FORM_STREAMING_FALLBACK_MIN_CHAR_DELTA
            and final_ratio <= config.LONG_FORM_STREAMING_FALLBACK_RATIO
        ):
            logger.warning(
                "Using streaming transcript for long-form %s because final pass "
                "was much shorter (duration=%.1fs, streaming_chars=%s, "
                "final_chars=%s, ratio=%.2f)",
                context,
                duration,
                len(streaming_text),
                len(final_text),
                final_ratio,
            )
            return streaming_text

        return final_text

    def on_transcription_error(self, error_message: str) -> None:
        """Handle transcription error."""
        self.controller.ui_controller.set_status(f"Error: {error_message}")
        self.controller.ui_controller.set_transcript(f"Error: {error_message}")
        self.controller.overlay_state_update.emit(OverlayState.NONE)
        if self.controller._streaming_paste_enabled:
            self.controller.caret_indicator_hide.emit()
        self.controller._live_typed_text = ""
        self.controller._best_streaming_text = ""
        self.controller._streaming_guard_evaluated = False
        self.controller._live_typing_failed = False

    def on_model_changed(self, model_name: str) -> None:
        """Handle model selection change."""
        model_value = config.MODEL_VALUE_MAP.get(model_name)
        if model_value and model_value in self.controller.transcription_backends:
            self.controller.current_backend = self.controller.transcription_backends[
                model_value
            ]
            self.controller._current_model_name = model_value
            settings_manager.save_model_selection(model_value)
            logger.info(f"Switched to model: {model_value}")

            if model_value == "local_whisper":
                local_backend = self.controller.transcription_backends.get("local_whisper")
                if local_backend and hasattr(local_backend, "device_info"):
                    self.controller.ui_controller.set_device_info(
                        local_backend.device_info
                    )
            else:
                self.controller.ui_controller.set_device_info("")

    def show_large_file_overlay(self, file_size_mb: float, is_splitting: bool) -> None:
        """Show the large-file overlay state."""
        overlay = self.controller.ui_controller.overlay
        overlay.set_large_file_info(file_size_mb)

        if is_splitting:
            overlay.show_at_cursor(overlay.STATE_LARGE_FILE_SPLITTING)
        else:
            overlay.show_at_cursor(overlay.STATE_LARGE_FILE_PROCESSING)

    def _submit_transcription_job(self, audio_path: str) -> None:
        self.controller._transcription_job_id += 1
        job_id = self.controller._transcription_job_id
        needs_splitting, file_size_mb = audio_processor.check_file_size(audio_path)
        should_split = (
            needs_splitting and self.controller.current_backend.requires_file_splitting
        )

        if should_split:
            logger.info(
                f"Large file ({file_size_mb:.2f} MB), backend requires splitting"
            )
            self.show_large_file_overlay(file_size_mb, is_splitting=True)
            self.controller.status_update.emit(
                f"Splitting large file ({file_size_mb:.1f} MB)..."
            )
            self.controller.executor.submit(
                self.transcribe_large_audio_file, audio_path, job_id
            )
        elif needs_splitting:
            logger.info(
                f"Large file ({file_size_mb:.2f} MB), processing without splitting"
            )
            self.show_large_file_overlay(file_size_mb, is_splitting=False)
            self.controller.status_update.emit(
                f"Processing large file ({file_size_mb:.1f} MB)..."
            )
            self.controller.executor.submit(self.transcribe_audio_file, audio_path, job_id)
        else:
            self.controller.executor.submit(self.transcribe_audio_file, audio_path, job_id)

    def _is_current_job(self, job_id: int) -> bool:
        return job_id == self.controller._transcription_job_id

    def _get_active_transcription_backend(self):
        return (
            getattr(self.controller, "_active_transcription_backend", None)
            or self.controller.current_backend
        )

    def _select_backend_for_transcription(self):
        backend = self.controller.current_backend
        if not self._should_guard_cuda_backend(backend):
            return backend

        if gpu_guard.wait_for_cuda_budget(
            "final transcription",
            max_wait_ms=config.GPU_BUSY_TRANSCRIBE_MAX_WAIT_MS,
        ):
            return backend

        logger.warning("Using CPU fallback because CUDA is still busy")
        self.controller.status_update.emit("GPU busy; using CPU fallback...")
        return self._get_cpu_fallback_backend()

    def _should_guard_cuda_backend(self, backend) -> bool:
        return (
            config.GPU_COOPERATIVE_MODE
            and isinstance(backend, LocalWhisperBackend)
            and getattr(backend, "prefers_cuda", False)
        )

    def _get_cpu_fallback_backend(self):
        backend = getattr(self.controller, "_cpu_fallback_backend", None)
        if backend is None:
            logger.info(
                "Loading CPU fallback backend: %s",
                config.GPU_BUSY_CPU_FALLBACK_MODEL,
            )
            backend = LocalWhisperBackend(
                model_name=config.GPU_BUSY_CPU_FALLBACK_MODEL,
                device="cpu",
                compute_type="int8",
                autoload=True,
            )
            self.controller._cpu_fallback_backend = backend
        return backend

    def _maybe_polish_transcript(self, transcript: str) -> str:
        settings = settings_manager.load_all_settings()
        result = local_polisher.maybe_polish(
            transcript,
            enabled=settings.get(SettingsKey.POLISH_ENABLED, config.POLISH_ENABLED),
            model=settings.get(SettingsKey.POLISH_MODEL, config.POLISH_MODEL),
            word_threshold=int(
                settings.get(
                    SettingsKey.POLISH_WORD_THRESHOLD,
                    config.POLISH_WORD_THRESHOLD,
                )
            ),
            timeout_ms=int(
                settings.get(SettingsKey.POLISH_TIMEOUT_MS, config.POLISH_TIMEOUT_MS)
            ),
            ollama_url=settings.get(
                SettingsKey.POLISH_OLLAMA_URL, config.POLISH_OLLAMA_URL
            ),
        )
        if result.used_polish:
            logger.info("Applied local polish")
        elif result.error:
            logger.info("Using raw transcript after polish fallback: %s", result.error)
        return result.text
