"""Local HTTP dictation service hosted by the RomanVoice tray app."""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import wave
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from config import config, ensure_service_token, service_token_configuration
from services.polisher import local_polisher
from services.settings import SettingsKey, settings_manager
from services.streaming_transcript_guard import choose_streaming_transcript
from services.streaming_transcriber import StreamingTranscriber
from services.websocket_protocol import WebSocketConnection, WebSocketProtocolError

if TYPE_CHECKING:
    from services.application_controller import ApplicationController

logger = logging.getLogger(__name__)

_CONTENT_TYPE_SUFFIXES = (
    ("audio/webm", ".webm"),
    ("audio/mp4", ".m4a"),
    ("audio/mpeg", ".mp3"),
    ("audio/ogg", ".ogg"),
    ("audio/wav", ".wav"),
    ("audio/x-wav", ".wav"),
)
_PHONE_HEARTBEAT_STALE_SECONDS = 180
_WEBSOCKET_IDLE_TIMEOUT_SECONDS = 30
_WEBSOCKET_MAX_FRAME_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ServiceResponse:
    status: HTTPStatus
    payload: dict[str, Any]


def audio_suffix_for_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    for prefix, suffix in _CONTENT_TYPE_SUFFIXES:
        if normalized == prefix:
            return suffix
    return ".webm"


def http_batch_decode_options() -> dict[str, Any]:
    """Decode profile used by PA/browser HTTP uploads."""
    options: dict[str, Any] = {
        "beam_size": config.SERVICE_HTTP_FASTER_WHISPER_BEAM_SIZE,
        "language": config.SERVICE_HTTP_FASTER_WHISPER_LANGUAGE,
        "condition_on_previous_text": (
            config.SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT
        ),
        "initial_prompt": config.SERVICE_HTTP_FASTER_WHISPER_INITIAL_PROMPT,
        "compression_ratio_threshold": config.FASTER_WHISPER_COMPRESSION_RATIO_THRESHOLD,
        "log_prob_threshold": config.FASTER_WHISPER_LOG_PROB_THRESHOLD,
        "no_speech_threshold": config.FASTER_WHISPER_NO_SPEECH_THRESHOLD,
        "vad_filter": config.SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED,
    }
    if config.SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED:
        options["vad_parameters"] = {
            "min_silence_duration_ms": (
                config.SERVICE_HTTP_FASTER_WHISPER_VAD_MIN_SILENCE_MS
            )
        }
    return options


class RomanVoiceDictationService:
    """Small loopback API exposing RomanVoice dictation to trusted clients."""

    def __init__(
        self,
        controller: "ApplicationController",
        *,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        max_audio_mb: int | None = None,
    ) -> None:
        self.controller = controller
        self.host = host or config.SERVICE_HOST
        self.port = int(port if port is not None else config.SERVICE_PORT)
        self.token = token if token is not None else ensure_service_token()
        self._token_configuration = (
            {"source": "explicit"}
            if token is not None
            else service_token_configuration()
        )
        if self._token_configuration.get("environment_file_mismatch"):
            raise RuntimeError(
                "Refusing to start RomanVoice because ROMANVOICE_SERVICE_TOKEN "
                "differs from the durable service token file"
            )
        self.max_audio_bytes = int(
            (max_audio_mb if max_audio_mb is not None else config.SERVICE_MAX_AUDIO_MB)
            * 1024
            * 1024
        )
        self._phone_lock = threading.RLock()
        self._phone_status: dict[str, Any] | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is not None:
            host, port = self._server.server_address[:2]
            return f"http://{host}:{port}"
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if not config.SERVICE_ENABLED:
            logger.info("RomanVoice dictation service disabled")
            return
        if self._server is not None:
            return
        if not self.token:
            logger.warning("RomanVoice dictation service not started: missing token")
            return

        handler_cls = self._make_handler()
        server = ThreadingHTTPServer((self.host, self.port), handler_cls)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="RomanVoiceDictationService",
            daemon=True,
        )
        self._thread.start()
        logger.info("RomanVoice dictation service listening on %s", self.base_url)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        logger.info("RomanVoice dictation service stopped")

    def _make_handler(self):
        service = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = "RomanVoiceDictation/1.0"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    if not service._is_loopback_request(self):
                        service._send_json(
                            self,
                            ServiceResponse(
                                HTTPStatus.FORBIDDEN,
                                {"ok": False, "error": "loopback access required"},
                            ),
                        )
                        return
                    service._send_json(
                        self,
                        ServiceResponse(
                            HTTPStatus.OK,
                            {
                                "ok": True,
                                "service": "RomanVoice",
                            },
                        ),
                    )
                    return
                if parsed.path == "/v1/health":
                    auth_error = service._require_auth(self)
                    if auth_error is not None:
                        service._send_json(self, auth_error)
                        return
                    service._send_json(self, service._health_response(detailed=True))
                    return
                if parsed.path == "/v1/phone/status":
                    auth_error = service._require_auth(self)
                    if auth_error is not None:
                        service._send_json(self, auth_error)
                        return
                    service._send_json(self, service._phone_status_response())
                    return
                if parsed.path == "/v1/transcribe/stream":
                    auth_error = service._require_auth(self)
                    if auth_error is not None:
                        service._send_json(self, auth_error)
                        return
                    service._handle_stream_websocket(self, parsed)
                    return
                service._send_json(
                    self,
                    ServiceResponse(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"}),
                )

            def do_POST(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/v1/phone/heartbeat":
                    auth_error = service._require_auth(self)
                    if auth_error is not None:
                        service._send_json(self, auth_error)
                        return
                    service._send_json(self, service._handle_phone_heartbeat(self))
                    return

                if parsed.path != "/v1/transcribe":
                    service._send_json(
                        self,
                        ServiceResponse(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"}),
                    )
                    return

                auth_error = service._require_auth(self)
                if auth_error is not None:
                    service._send_json(self, auth_error)
                    return

                service._send_json(self, service._handle_transcribe(self, parsed))

            def log_message(self, fmt: str, *args: Any) -> None:
                logger.debug("service request: " + fmt, *args)

        return RequestHandler

    def _require_auth(self, handler: BaseHTTPRequestHandler) -> ServiceResponse | None:
        header = handler.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        if not secrets.compare_digest(header, expected):
            logger.warning(
                "RomanVoice service rejected authentication path=%s source=%s",
                urllib.parse.urlparse(handler.path).path,
                self._request_source(handler),
            )
            return ServiceResponse(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "missing or invalid bearer token"},
            )
        return None

    def _handle_transcribe(
        self,
        handler: BaseHTTPRequestHandler,
        parsed: urllib.parse.ParseResult,
    ) -> ServiceResponse:
        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            return ServiceResponse(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "invalid Content-Length"},
            )

        if content_length <= 0:
            return ServiceResponse(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "no audio was uploaded"},
            )
        if content_length > self.max_audio_bytes:
            return ServiceResponse(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "ok": False,
                    "error": f"audio upload is too large; limit is {config.SERVICE_MAX_AUDIO_MB} MB",
                },
            )

        audio_bytes = handler.rfile.read(content_length)
        content_type = handler.headers.get("Content-Type") or "application/octet-stream"
        request_source = self._request_source(handler)
        logger.info(
            "RomanVoice HTTP upload received source=%s content_type=%s bytes=%s",
            request_source,
            content_type,
            len(audio_bytes),
        )
        query = urllib.parse.parse_qs(parsed.query)
        polish_mode = (query.get("polish") or ["settings"])[0].strip().lower()

        temp_path = self._write_temp_audio(audio_bytes, content_type)
        debug_audio_path = self._save_last_http_audio(temp_path, content_type)
        started = time.monotonic()
        try:
            payload = self._transcribe_file(
                temp_path,
                content_type=content_type,
                bytes_received=len(audio_bytes),
                polish_mode=polish_mode,
                started=started,
                debug_audio_path=debug_audio_path,
            )
            logger.info(
                "RomanVoice HTTP upload completed source=%s final_source=%s chars=%s duration=%.3fs",
                request_source,
                payload.get("final_source", ""),
                len(str(payload.get("text") or "")),
                float(payload.get("duration_seconds") or 0.0),
            )
            return ServiceResponse(HTTPStatus.OK, payload)
        except Exception as exc:
            logger.warning(
                "Service transcription failed source=%s: %s",
                request_source,
                exc,
                exc_info=True,
            )
            return ServiceResponse(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "error": str(exc)},
            )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove service temp audio: %s", temp_path)

    def _handle_phone_heartbeat(self, handler: BaseHTTPRequestHandler) -> ServiceResponse:
        payload = self._read_json_payload(handler, max_bytes=4096)
        if payload is None:
            return ServiceResponse(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "invalid JSON heartbeat payload"},
            )

        now = time.time()
        status = {
            "seen": True,
            "last_seen_at_epoch": now,
            "last_seen_at_utc": self._epoch_to_utc(now),
            "surface": str(payload.get("surface") or "")[:64],
            "event": str(payload.get("event") or "")[:64],
            "available": bool(payload.get("available", True)),
            "recording": bool(payload.get("recording", False)),
            "connecting": bool(payload.get("connecting", False)),
        }
        with self._phone_lock:
            self._phone_status = status
        logger.info(
            "RomanVoice phone heartbeat surface=%s event=%s available=%s recording=%s connecting=%s",
            status["surface"],
            status["event"],
            status["available"],
            status["recording"],
            status["connecting"],
        )
        return self._phone_status_response(now=now)

    def _read_json_payload(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        max_bytes: int,
    ) -> dict[str, Any] | None:
        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if content_length <= 0 or content_length > max_bytes:
            return None
        try:
            payload = json.loads(handler.rfile.read(content_length).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_temp_audio(self, audio_bytes: bytes, content_type: str | None) -> Path:
        suffix = audio_suffix_for_content_type(content_type)
        with tempfile.NamedTemporaryFile(
            prefix="romanvoice_service_",
            suffix=suffix,
            delete=False,
        ) as handle:
            handle.write(audio_bytes)
            return Path(handle.name)

    def _write_temp_pcm16_wav(self, pcm_bytes: bytes, sample_rate: int) -> Path:
        with tempfile.NamedTemporaryFile(
            prefix="romanvoice_stream_",
            suffix=".wav",
            delete=False,
        ) as handle:
            path = Path(handle.name)
        self._write_pcm16_wav(path, pcm_bytes, sample_rate)
        return path

    def _write_pcm16_wav(self, path: Path, pcm_bytes: bytes, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        aligned_length = len(pcm_bytes) - (len(pcm_bytes) % 2)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(sample_rate))
            wav_file.writeframes(pcm_bytes[:aligned_length])

    def _save_last_stream_wav(self, pcm_bytes: bytes, sample_rate: int) -> str | None:
        if not config.SERVICE_SAVE_LAST_STREAM_WAV or not pcm_bytes:
            return None

        try:
            path = Path(config.RECORDINGS_FOLDER) / "romanvoice_phone_stream_last.wav"
            self._write_pcm16_wav(path, pcm_bytes, sample_rate)
            return str(path)
        except OSError:
            logger.debug("Failed to save last phone stream WAV", exc_info=True)
            return None

    def _save_last_http_audio(self, audio_path: Path, content_type: str | None) -> str | None:
        if not config.SERVICE_SAVE_LAST_HTTP_AUDIO:
            return None
        try:
            suffix = audio_suffix_for_content_type(content_type) or audio_path.suffix or ".bin"
            recordings_dir = Path(config.RECORDINGS_FOLDER)
            recordings_dir.mkdir(parents=True, exist_ok=True)
            last_path = recordings_dir / f"romanvoice_service_upload_last{suffix}"
            keep_count = max(0, int(config.SERVICE_HTTP_DIAGNOSTIC_UPLOAD_KEEP_COUNT))
            if keep_count <= 0:
                shutil.copyfile(audio_path, last_path)
                return str(last_path)

            diagnostic_dir = recordings_dir / "service_uploads"
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            diagnostic_path = self._unique_http_diagnostic_path(diagnostic_dir, suffix)
            shutil.copyfile(audio_path, diagnostic_path)
            shutil.copyfile(audio_path, last_path)
            self._prune_http_diagnostic_uploads(diagnostic_dir, keep_count)
            return str(diagnostic_path)
        except OSError:
            logger.debug("Failed to save last service upload", exc_info=True)
            return None

    @staticmethod
    def _unique_http_diagnostic_path(diagnostic_dir: Path, suffix: str) -> Path:
        base = time.strftime("romanvoice_service_upload_%Y%m%d-%H%M%S")
        millis = int((time.time() % 1) * 1000)
        stem = f"{base}-{millis:03d}"
        candidate = diagnostic_dir / f"{stem}-000{suffix}"
        index = 1
        while candidate.exists():
            candidate = diagnostic_dir / f"{stem}-{index:03d}{suffix}"
            index += 1
        return candidate

    @staticmethod
    def _prune_http_diagnostic_uploads(diagnostic_dir: Path, keep_count: int) -> None:
        if keep_count <= 0:
            return
        uploads = sorted(
            diagnostic_dir.glob("romanvoice_service_upload_*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in uploads[keep_count:]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to prune diagnostic service upload: %s", path)

    @staticmethod
    def _pcm16_metrics(pcm_bytes: bytes, sample_rate: int) -> dict[str, Any]:
        aligned_length = len(pcm_bytes) - (len(pcm_bytes) % 2)
        if aligned_length <= 0:
            return {
                "audio_duration_seconds": 0.0,
                "audio_peak": 0,
                "audio_rms": 0.0,
                "sample_count": 0,
            }

        samples = np.frombuffer(pcm_bytes[:aligned_length], dtype=np.int16)
        if samples.size == 0:
            return {
                "audio_duration_seconds": 0.0,
                "audio_peak": 0,
                "audio_rms": 0.0,
                "sample_count": 0,
            }

        samples_32 = samples.astype(np.int32)
        peak = int(np.max(np.abs(samples_32)))
        rms = math.sqrt(float(np.mean(samples_32.astype(np.float64) ** 2)))
        duration = samples.size / sample_rate if sample_rate > 0 else 0.0
        return {
            "audio_duration_seconds": round(duration, 3),
            "audio_peak": peak,
            "audio_rms": round(rms, 1),
            "sample_count": int(samples.size),
        }

    def _transcribe_file(
        self,
        audio_path: Path,
        *,
        content_type: str,
        bytes_received: int,
        polish_mode: str,
        started: float,
        debug_audio_path: str | None = None,
    ) -> dict[str, Any]:
        lock = getattr(self.controller, "_transcription_lock", None)
        if lock is None:
            lock = threading.RLock()

        preview = self._audio_file_preview(audio_path)
        decode_options = http_batch_decode_options()
        with lock:
            backend = self.controller.transcription_runtime._select_backend_for_transcription()
            self.controller._active_transcription_backend = backend
            try:
                if self._should_chunk_http_audio(preview):
                    raw_text, final_source, chunk_count = self._transcribe_http_chunks(
                        backend,
                        audio_path,
                        decode_options=decode_options,
                    )
                else:
                    raw_text = self._transcribe_with_decode_options(
                        backend,
                        str(audio_path),
                        decode_options,
                    ).strip()
                    final_source = "http_final_file"
                    chunk_count = 1
                polished = self._maybe_polish(raw_text, polish_mode)
                text = polished["text"]
                device_info = getattr(backend, "device_info", "")
                integrity = self._http_transcript_integrity(text, preview)
                if integrity["suspect_truncated"]:
                    logger.warning(
                        "HTTP transcription looks too short for long audio "
                        "(duration=%.1fs, chars=%s, chars_per_minute=%.1f, bytes=%s, source=%s, debug_audio=%s)",
                        integrity["audio_duration_seconds"],
                        integrity["transcript_char_count"],
                        integrity["transcript_chars_per_minute"],
                        bytes_received,
                        final_source,
                        debug_audio_path or "",
                    )
                return {
                    "ok": True,
                    "text": text,
                    "transcript": text,
                    "raw_text": raw_text,
                    "backend": getattr(backend, "name", backend.__class__.__name__),
                    "device_info": device_info,
                    "bytes_received": bytes_received,
                    "content_type": content_type or "application/octet-stream",
                    "final_source": final_source,
                    "chunk_count": chunk_count,
                    "decode_profile": config.SERVICE_HTTP_DECODE_PROFILE,
                    "debug_audio_path": debug_audio_path,
                    **integrity,
                    "used_polish": polished["used_polish"],
                    "polish_mode": polish_mode or "settings",
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            finally:
                self.controller._active_transcription_backend = None

    def _audio_file_preview(self, audio_path: Path) -> dict[str, Any]:
        try:
            from services.audio_processor import audio_processor

            preview = audio_processor.preview_file(str(audio_path))
        except Exception as exc:
            logger.info("Could not inspect service upload audio metadata: %s", exc)
            return {}
        return {
            "audio_duration_seconds": round(float(preview.duration_seconds), 3),
            "audio_sample_rate": int(preview.sample_rate),
            "audio_channels": int(preview.channels),
            "file_size_mb": round(float(preview.file_size_mb), 3),
            "estimated_chunks": int(preview.estimated_chunks),
        }

    def _should_chunk_http_audio(self, preview: dict[str, Any]) -> bool:
        duration = float(preview.get("audio_duration_seconds") or 0.0)
        return duration >= float(config.SERVICE_HTTP_LONG_FORM_CHUNK_MIN_SECONDS)

    def _transcribe_http_chunks(
        self,
        backend: Any,
        audio_path: Path,
        *,
        decode_options: dict[str, Any],
    ) -> tuple[str, str, int]:
        from services.audio_processor import audio_processor

        chunk_files = audio_processor.split_audio_file(str(audio_path))
        try:
            if hasattr(backend, "transcribe_chunks"):
                try:
                    text = str(
                        backend.transcribe_chunks(
                            chunk_files,
                            decode_options=decode_options,
                        )
                        or ""
                    ).strip()
                except TypeError:
                    text = str(backend.transcribe_chunks(chunk_files) or "").strip()
            else:
                parts = [
                    str(
                        self._transcribe_with_decode_options(
                            backend,
                            chunk_file,
                            decode_options,
                        )
                        or ""
                    ).strip()
                    for chunk_file in chunk_files
                ]
                text = audio_processor.combine_transcriptions(parts)
            logger.info("HTTP long-form transcription used %s chunk(s)", len(chunk_files))
            return text, "http_chunked_wav", len(chunk_files)
        finally:
            audio_processor.cleanup_temp_files()

    @staticmethod
    def _transcribe_with_decode_options(
        backend: Any,
        audio_path: str,
        decode_options: dict[str, Any],
    ) -> str:
        try:
            return backend.transcribe(audio_path, decode_options=decode_options)
        except TypeError:
            return backend.transcribe(audio_path)

    def _http_transcript_integrity(self, text: str, preview: dict[str, Any]) -> dict[str, Any]:
        duration = float(preview.get("audio_duration_seconds") or 0.0)
        char_count = len((text or "").strip())
        chars_per_minute = (char_count / (duration / 60.0)) if duration > 0 else 0.0
        min_expected_chars = max(
            int(config.SERVICE_HTTP_SUSPECT_MIN_EXPECTED_CHARS),
            int((duration / 60.0) * float(config.SERVICE_HTTP_SUSPECT_MIN_CHARS_PER_MINUTE)),
        )
        suspect = (
            duration >= float(config.SERVICE_HTTP_SUSPECT_LOW_DENSITY_MIN_SECONDS)
            and char_count < min_expected_chars
        )
        warning = ""
        if suspect:
            warning = (
                f"Transcription looks incomplete for {self._format_duration(duration)} of audio "
                f"({char_count} chars). The uploaded audio was saved for recovery."
            )
        return {
            **preview,
            "transcript_char_count": char_count,
            "transcript_chars_per_minute": round(chars_per_minute, 1) if duration > 0 else 0.0,
            "suspect_truncated": suspect,
            "transcription_warning": warning,
        }

    @staticmethod
    def _format_duration(duration_seconds: float) -> str:
        total = max(0, int(round(duration_seconds)))
        minutes, seconds = divmod(total, 60)
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    def _handle_stream_websocket(
        self,
        handler: BaseHTTPRequestHandler,
        parsed: urllib.parse.ParseResult,
    ) -> None:
        streamer: StreamingTranscriber | None = None
        total_audio_bytes = 0
        stream_audio = bytearray()
        started = time.monotonic()
        final_sent = False
        state: dict[str, Any] = {
            "polish_mode": "settings",
            "sample_rate": config.WHISPER_TARGET_SAMPLE_RATE,
            "sequence": 0,
            "backend": None,
        }

        try:
            handler.connection.settimeout(_WEBSOCKET_IDLE_TIMEOUT_SECONDS)
            websocket = WebSocketConnection.accept(
                handler,
                max_frame_bytes=min(self.max_audio_bytes, _WEBSOCKET_MAX_FRAME_BYTES),
            )
        except WebSocketProtocolError as exc:
            self._send_json(
                handler,
                ServiceResponse(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}),
            )
            return

        request_source = self._request_source(handler)
        logger.info("RomanVoice phone stream connected source=%s", request_source)

        def send_partial(text: str, is_final: bool) -> None:
            state["sequence"] += 1
            websocket.send_json(
                {
                    "type": "partial",
                    "ok": True,
                    "text": text or "",
                    "is_final": bool(is_final),
                    "replace": True,
                    "sequence": state["sequence"],
                }
            )

        def finish_stream() -> None:
            nonlocal streamer, final_sent
            if streamer is None or final_sent:
                return
            rolling_text = streamer.stop_streaming().strip()
            rolling_stop_timed_out = bool(getattr(streamer, "last_stop_timed_out", False))
            streamer = None
            raw_text = rolling_text
            final_source = "streaming_preview"
            backend = state.get("backend")
            audio_bytes = bytes(stream_audio)
            sample_rate = int(state["sample_rate"])
            metrics = self._pcm16_metrics(audio_bytes, sample_rate)
            debug_audio_path = self._save_last_stream_wav(audio_bytes, sample_rate)

            should_run_final_pass = (
                metrics["audio_peak"] > 0
                and (
                    config.PHONE_STREAM_FINAL_PASS_ENABLED
                    or not rolling_text
                    or rolling_stop_timed_out
                )
            )
            if should_run_final_pass:
                final_pass_reason = (
                    "streaming_timeout"
                    if rolling_stop_timed_out
                    else "enabled_or_empty_preview"
                )
                temp_wav_path: Path | None = None
                try:
                    temp_wav_path = self._write_temp_pcm16_wav(audio_bytes, sample_rate)
                    lock = getattr(self.controller, "_transcription_lock", None)
                    if lock is None:
                        lock = threading.RLock()

                    with lock:
                        backend = self.controller.transcription_runtime._select_backend_for_transcription()
                        self.controller._active_transcription_backend = backend
                        final_raw_text = backend.transcribe(str(temp_wav_path)).strip()

                    decision = choose_streaming_transcript(
                        final_raw_text,
                        rolling_text,
                        duration_seconds=metrics["audio_duration_seconds"],
                    )
                    if decision.prefer_streaming:
                        raw_text = rolling_text
                        final_source = "streaming_preview_long_form_guard"
                        logger.warning(
                            "Using rolling phone stream transcript because final pass "
                            "looks truncated (reason=%s, duration=%.1fs, rolling_chars=%s, "
                            "final_chars=%s, missing_prefix=%s, overlap=%s)",
                            decision.reason,
                            metrics["audio_duration_seconds"],
                            len(rolling_text),
                            len(final_raw_text),
                            decision.missing_prefix_chars,
                            decision.overlap_chars,
                        )
                    elif final_raw_text:
                        raw_text = final_raw_text
                        final_source = (
                            "final_wav_after_stream_timeout"
                            if rolling_stop_timed_out
                            else "final_wav"
                        )
                        logger.info(
                            "Using final phone WAV pass (reason=%s, rolling_chars=%s, "
                            "final_chars=%s)",
                            final_pass_reason,
                            len(rolling_text),
                            len(final_raw_text),
                        )
                    elif rolling_text:
                        raw_text = rolling_text
                        final_source = "streaming_preview_fallback"
                        logger.info(
                            "Using rolling stream transcript after empty final phone pass (%s chars)",
                            len(raw_text),
                        )
                    else:
                        raw_text = ""
                        final_source = "final_wav_empty"
                except Exception as exc:
                    logger.warning(
                        "Final phone stream transcription failed; using rolling preview: %s",
                        exc,
                        exc_info=True,
                    )
                    raw_text = rolling_text
                    final_source = "streaming_preview_error_fallback"
                finally:
                    if temp_wav_path is not None:
                        try:
                            temp_wav_path.unlink(missing_ok=True)
                        except OSError:
                            logger.debug(
                                "Failed to remove service temp stream WAV: %s",
                                temp_wav_path,
                            )
            else:
                if metrics["audio_peak"] > 0:
                    logger.info(
                        "Using rolling phone stream transcript without final WAV pass "
                        "(chars=%s, duration=%.3fs)",
                        len(raw_text),
                        metrics["audio_duration_seconds"],
                    )
                else:
                    logger.info(
                        "Phone stream contained no non-zero PCM samples "
                        "(bytes=%s, duration=%.3fs)",
                        total_audio_bytes,
                        metrics["audio_duration_seconds"],
                    )

            polished = self._maybe_polish(raw_text, state["polish_mode"])
            logger.info(
                "Phone stream final client=%s source=%s bytes=%s duration=%.3fs peak=%s rms=%.1f "
                "rolling_chars=%s final_chars=%s debug_audio=%s",
                request_source,
                final_source,
                total_audio_bytes,
                metrics["audio_duration_seconds"],
                metrics["audio_peak"],
                metrics["audio_rms"],
                len(rolling_text),
                len(raw_text),
                debug_audio_path or "",
            )
            websocket.send_json(
                {
                    "type": "final",
                    "ok": True,
                    "text": polished["text"],
                    "transcript": polished["text"],
                    "raw_text": raw_text,
                    "backend": getattr(backend, "name", backend.__class__.__name__),
                    "device_info": getattr(backend, "device_info", ""),
                    "bytes_received": total_audio_bytes,
                    "content_type": "audio/raw;encoding=pcm_s16le",
                    "sample_rate": state["sample_rate"],
                    "channel_count": 1,
                    "final_source": final_source,
                    "audio_duration_seconds": metrics["audio_duration_seconds"],
                    "audio_peak": metrics["audio_peak"],
                    "audio_rms": metrics["audio_rms"],
                    "rolling_text_length": len(rolling_text),
                    "debug_audio_path": debug_audio_path,
                    "used_polish": polished["used_polish"],
                    "polish_mode": state["polish_mode"],
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
            final_sent = True

        try:
            websocket.send_json(
                {
                    "type": "ready",
                    "ok": True,
                    "service": "RomanVoice",
                    "protocol": "romanvoice.streaming.v1",
                    "sample_format": "pcm_s16le",
                    "sample_rate": config.WHISPER_TARGET_SAMPLE_RATE,
                    "channel_count": 1,
                    "max_audio_bytes": self.max_audio_bytes,
                }
            )

            while not websocket.closed:
                message = websocket.read_message()
                if message.kind == "close":
                    break

                if message.kind == "text":
                    try:
                        payload = json.loads(str(message.data or "{}"))
                    except json.JSONDecodeError:
                        websocket.send_error("invalid JSON control message")
                        continue

                    message_type = str(payload.get("type", "")).lower()
                    if message_type == "start":
                        if streamer is not None:
                            websocket.send_error("stream already started")
                            continue
                        sample_rate = payload.get(
                            "sample_rate",
                            config.WHISPER_TARGET_SAMPLE_RATE,
                        )
                        if (
                            isinstance(sample_rate, bool)
                            or not isinstance(sample_rate, int)
                            or sample_rate != config.WHISPER_TARGET_SAMPLE_RATE
                        ):
                            websocket.send_error(
                                "sample_rate must be "
                                f"{config.WHISPER_TARGET_SAMPLE_RATE} Hz"
                            )
                            continue
                        state["sample_rate"] = sample_rate
                        state["polish_mode"] = str(payload.get("polish") or "settings").lower()

                        backend = self.controller.current_backend
                        if backend is None:
                            websocket.send_error("no transcription backend is configured")
                            continue
                        if not getattr(backend, "model", None) and not hasattr(backend, "ensure_loaded"):
                            websocket.send_error("streaming requires a local faster-whisper backend")
                            continue
                        state["backend"] = backend
                        self.controller._active_transcription_backend = backend
                        streamer = StreamingTranscriber(
                            backend=backend,
                            chunk_duration_sec=float(config.STREAMING_CHUNK_DURATION_SEC),
                            transcription_lock=getattr(self.controller, "_transcription_lock", None),
                            vad_filter=True,
                        )
                        streamer.start_streaming(sample_rate=sample_rate, callback=send_partial)
                        websocket.send_json(
                            {
                                "type": "started",
                                "ok": True,
                                "sample_rate": sample_rate,
                                "channel_count": 1,
                                "sample_format": "pcm_s16le",
                                "polish_mode": state["polish_mode"],
                            }
                        )
                    elif message_type == "stop":
                        finish_stream()
                        websocket.close()
                        break
                    else:
                        websocket.send_error("unknown control message type")
                    continue

                if message.kind == "binary":
                    if streamer is None:
                        websocket.send_error("send a start message before audio chunks")
                        continue
                    audio_bytes = bytes(message.data or b"")
                    if len(audio_bytes) % 2:
                        audio_bytes = audio_bytes[:-1]
                    if total_audio_bytes + len(audio_bytes) > self.max_audio_bytes:
                        websocket.send_error(
                            f"audio stream is too large; limit is {config.SERVICE_MAX_AUDIO_MB} MB"
                        )
                        websocket.close(1009, "audio stream too large")
                        break
                    if audio_bytes:
                        total_audio_bytes += len(audio_bytes)
                        stream_audio.extend(audio_bytes)
                        streamer.feed_audio(np.frombuffer(audio_bytes, dtype=np.int16))
                    continue

        except (OSError, WebSocketProtocolError) as exc:
            logger.info("Streaming WebSocket closed source=%s: %s", request_source, exc)
        except Exception as exc:
            logger.warning("Streaming transcription failed: %s", exc, exc_info=True)
            try:
                websocket.send_error(str(exc))
                websocket.close(1011, "streaming transcription failed")
            except Exception:
                pass
        finally:
            try:
                if streamer is not None and not final_sent and not websocket.closed:
                    finish_stream()
            except Exception:
                logger.debug("Failed to finalize streaming WebSocket", exc_info=True)
            try:
                if streamer is not None:
                    streamer.cleanup()
            except Exception:
                logger.debug("Failed to clean up streaming transcriber", exc_info=True)
            self.controller._active_transcription_backend = None
            try:
                websocket.close()
            except Exception:
                pass

    def _maybe_polish(self, transcript: str, polish_mode: str) -> dict[str, Any]:
        mode = (polish_mode or "settings").lower()
        if mode in {"0", "false", "off", "raw", "none"}:
            return {"text": transcript, "used_polish": False}

        settings = settings_manager.load_all_settings()
        if mode in {"1", "true", "on", "polish"}:
            enabled = True
        else:
            enabled = settings.get(SettingsKey.POLISH_ENABLED, config.POLISH_ENABLED)

        result = local_polisher.maybe_polish(
            transcript,
            enabled=enabled,
            model=settings.get(SettingsKey.POLISH_MODEL, config.POLISH_MODEL),
            word_threshold=int(
                settings.get(SettingsKey.POLISH_WORD_THRESHOLD, config.POLISH_WORD_THRESHOLD)
            ),
            timeout_ms=int(settings.get(SettingsKey.POLISH_TIMEOUT_MS, config.POLISH_TIMEOUT_MS)),
            ollama_url=settings.get(SettingsKey.POLISH_OLLAMA_URL, config.POLISH_OLLAMA_URL),
        )
        return {"text": result.text, "used_polish": result.used_polish}

    def _health_response(self, *, detailed: bool) -> ServiceResponse:
        payload: dict[str, Any] = {
            "ok": True,
            "service": "RomanVoice",
        }
        if detailed:
            backend = self.controller.current_backend
            payload.update(
                {
                    "backend": getattr(backend, "name", "") if backend else "",
                    "device_info": getattr(backend, "device_info", "") if backend else "",
                    "token_file": config.SERVICE_TOKEN_FILE,
                    "token_configuration": dict(self._token_configuration),
                    "runtime": self._runtime_identity(),
                    "http_decode_profile": {
                        "name": config.SERVICE_HTTP_DECODE_PROFILE,
                        "language": config.SERVICE_HTTP_FASTER_WHISPER_LANGUAGE,
                        "condition_on_previous_text": (
                            config.SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT
                        ),
                        "vad_filter": config.SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED,
                        "vad_min_silence_ms": (
                            config.SERVICE_HTTP_FASTER_WHISPER_VAD_MIN_SILENCE_MS
                        ),
                    },
                    "phone": self._phone_status_payload(),
                    "transport": {
                        "authentication": "bearer",
                        "websocket_idle_timeout_seconds": _WEBSOCKET_IDLE_TIMEOUT_SECONDS,
                        "websocket_max_frame_bytes": min(
                            self.max_audio_bytes,
                            _WEBSOCKET_MAX_FRAME_BYTES,
                        ),
                    },
                }
            )
        return ServiceResponse(HTTPStatus.OK, payload)

    def _runtime_identity(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "executable": sys.executable,
            "cwd": os.getcwd(),
            "service_host": self.host,
            "service_port": self.port,
            "base_url": self.base_url,
        }

    def _phone_status_response(self, *, now: float | None = None) -> ServiceResponse:
        payload = {
            "ok": True,
            "service": "RomanVoice",
            "phone": self._phone_status_payload(now=now),
        }
        return ServiceResponse(HTTPStatus.OK, payload)

    def _phone_status_payload(self, *, now: float | None = None) -> dict[str, Any]:
        current_time = time.time() if now is None else now
        with self._phone_lock:
            status = dict(self._phone_status or {})

        if not status:
            return {
                "seen": False,
                "status": "unseen",
                "ok": False,
                "stale_after_seconds": _PHONE_HEARTBEAT_STALE_SECONDS,
                "last_seen_age_seconds": None,
            }

        last_seen = float(status.get("last_seen_at_epoch") or 0.0)
        age = max(0.0, current_time - last_seen) if last_seen else None
        available = bool(status.get("available", False))
        if not available:
            phone_status = "inactive"
        elif age is not None and age > _PHONE_HEARTBEAT_STALE_SECONDS:
            phone_status = "stale"
        else:
            phone_status = "ok"

        status.update(
            {
                "status": phone_status,
                "ok": phone_status == "ok",
                "stale_after_seconds": _PHONE_HEARTBEAT_STALE_SECONDS,
                "last_seen_age_seconds": round(age, 1) if age is not None else None,
            }
        )
        return status

    @staticmethod
    def _epoch_to_utc(value: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))

    @staticmethod
    def _is_loopback_request(handler: BaseHTTPRequestHandler) -> bool:
        try:
            return ipaddress.ip_address(str(handler.client_address[0])).is_loopback
        except (IndexError, TypeError, ValueError):
            return False

    @staticmethod
    def _request_source(handler: BaseHTTPRequestHandler) -> str:
        try:
            remote = str(handler.client_address[0])
        except (AttributeError, IndexError, TypeError):
            remote = "unknown"
        client = (
            handler.headers.get("X-RomanVoice-Client", "")
            or handler.headers.get("User-Agent", "")
            or "unspecified"
        )
        safe_client = " ".join(str(client).split())[:96]
        return f"{remote}/{safe_client}"

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, response: ServiceResponse) -> None:
        body = json.dumps(response.payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(int(response.status))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
