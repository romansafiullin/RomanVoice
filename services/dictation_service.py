"""Local HTTP dictation service hosted by the RomanVoice tray app."""

from __future__ import annotations

import json
import logging
import math
import secrets
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

from config import config, ensure_service_token
from services.polisher import local_polisher
from services.settings import SettingsKey, settings_manager
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
        self.max_audio_bytes = int(
            (max_audio_mb if max_audio_mb is not None else config.SERVICE_MAX_AUDIO_MB)
            * 1024
            * 1024
        )
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
        query = urllib.parse.parse_qs(parsed.query)
        polish_mode = (query.get("polish") or ["settings"])[0].strip().lower()

        temp_path = self._write_temp_audio(audio_bytes, content_type)
        started = time.monotonic()
        try:
            payload = self._transcribe_file(
                temp_path,
                content_type=content_type,
                bytes_received=len(audio_bytes),
                polish_mode=polish_mode,
                started=started,
            )
            return ServiceResponse(HTTPStatus.OK, payload)
        except Exception as exc:
            logger.warning("Service transcription failed: %s", exc, exc_info=True)
            return ServiceResponse(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "error": str(exc)},
            )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove service temp audio: %s", temp_path)

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

    @staticmethod
    def _should_prefer_streaming_preview(
        final_text: str,
        rolling_text: str,
        *,
        duration_seconds: float,
    ) -> bool:
        final_text = (final_text or "").strip()
        rolling_text = (rolling_text or "").strip()
        if not final_text or not rolling_text:
            return False

        char_delta = len(rolling_text) - len(final_text)
        final_ratio = len(final_text) / max(len(rolling_text), 1)
        return (
            duration_seconds >= config.LONG_FORM_STREAMING_FALLBACK_MIN_SECONDS
            and len(rolling_text) >= config.LONG_FORM_STREAMING_FALLBACK_MIN_CHARS
            and char_delta >= config.LONG_FORM_STREAMING_FALLBACK_MIN_CHAR_DELTA
            and final_ratio <= config.LONG_FORM_STREAMING_FALLBACK_RATIO
        )

    def _transcribe_file(
        self,
        audio_path: Path,
        *,
        content_type: str,
        bytes_received: int,
        polish_mode: str,
        started: float,
    ) -> dict[str, Any]:
        lock = getattr(self.controller, "_transcription_lock", None)
        if lock is None:
            lock = threading.RLock()

        with lock:
            backend = self.controller.transcription_runtime._select_backend_for_transcription()
            self.controller._active_transcription_backend = backend
            try:
                raw_text = backend.transcribe(str(audio_path)).strip()
                polished = self._maybe_polish(raw_text, polish_mode)
                text = polished["text"]
                device_info = getattr(backend, "device_info", "")
                return {
                    "ok": True,
                    "text": text,
                    "transcript": text,
                    "raw_text": raw_text,
                    "backend": getattr(backend, "name", backend.__class__.__name__),
                    "device_info": device_info,
                    "bytes_received": bytes_received,
                    "content_type": content_type or "application/octet-stream",
                    "used_polish": polished["used_polish"],
                    "polish_mode": polish_mode or "settings",
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            finally:
                self.controller._active_transcription_backend = None

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
            websocket = WebSocketConnection.accept(handler)
        except WebSocketProtocolError as exc:
            self._send_json(
                handler,
                ServiceResponse(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}),
            )
            return

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
            streamer = None
            raw_text = rolling_text
            final_source = "streaming_preview"
            backend = state.get("backend")
            audio_bytes = bytes(stream_audio)
            sample_rate = int(state["sample_rate"])
            metrics = self._pcm16_metrics(audio_bytes, sample_rate)
            debug_audio_path = self._save_last_stream_wav(audio_bytes, sample_rate)

            if metrics["audio_peak"] > 0:
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

                    if self._should_prefer_streaming_preview(
                        final_raw_text,
                        rolling_text,
                        duration_seconds=metrics["audio_duration_seconds"],
                    ):
                        raw_text = rolling_text
                        final_source = "streaming_preview_long_form_guard"
                        logger.warning(
                            "Using rolling phone stream transcript because final pass "
                            "was much shorter (duration=%.1fs, rolling_chars=%s, "
                            "final_chars=%s)",
                            metrics["audio_duration_seconds"],
                            len(rolling_text),
                            len(final_raw_text),
                        )
                    elif final_raw_text:
                        raw_text = final_raw_text
                        final_source = "final_wav"
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
                logger.info(
                    "Phone stream contained no non-zero PCM samples "
                    "(bytes=%s, duration=%.3fs)",
                    total_audio_bytes,
                    metrics["audio_duration_seconds"],
                )

            polished = self._maybe_polish(raw_text, state["polish_mode"])
            logger.info(
                "Phone stream final source=%s bytes=%s duration=%.3fs peak=%s rms=%.1f "
                "rolling_chars=%s final_chars=%s debug_audio=%s",
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
                        sample_rate = int(payload.get("sample_rate") or config.WHISPER_TARGET_SAMPLE_RATE)
                        if sample_rate <= 0:
                            websocket.send_error("sample_rate must be positive")
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
            logger.info("Streaming WebSocket closed: %s", exc)
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
                }
            )
        return ServiceResponse(HTTPStatus.OK, payload)

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, response: ServiceResponse) -> None:
        body = json.dumps(response.payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(int(response.status))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
