from __future__ import annotations

import base64
import json
import socket
import struct
import threading
import wave
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from config import config
from services.dictation_service import RomanVoiceDictationService
from services.websocket_protocol import make_client_frame


class FakeBackend:
    name = "fake-whisper"
    device_info = "test-device"

    def __init__(self):
        self.seen_path = None
        self.seen_bytes = None
        self.seen_decode_options = None

    def transcribe(self, audio_path: str, *, decode_options=None) -> str:
        path = Path(audio_path)
        self.seen_path = path
        self.seen_bytes = path.read_bytes()
        self.seen_decode_options = dict(decode_options or {})
        assert path.exists()
        assert path.suffix == ".webm"
        return "add clean the window"


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class FakeStreamingModel:
    def __init__(self, text: str = "streamed words"):
        self.text = text
        self.seen_samples = 0
        self.seen_kwargs = None

    def transcribe(self, audio_array, **kwargs):
        self.seen_samples = len(audio_array)
        self.seen_kwargs = kwargs
        return [FakeSegment(self.text)], SimpleNamespace(language="en")


class FakeStreamingBackend:
    name = "fake-streaming-whisper"
    device_info = "stream-device"

    def __init__(
        self,
        final_text: str = "final phone words",
        rolling_text: str = "streamed words",
    ):
        self.model = FakeStreamingModel(rolling_text)
        self.final_text = final_text
        self.final_seen_path = None
        self.final_wave_info = None
        self.final_decode_options = None

    def ensure_loaded(self):
        return None

    def transcribe(self, audio_path: str, *, decode_options=None) -> str:
        path = Path(audio_path)
        self.final_seen_path = path
        self.final_decode_options = dict(decode_options or {})
        assert path.exists()
        assert path.suffix == ".wav"
        with wave.open(str(path), "rb") as wav_file:
            self.final_wave_info = {
                "channels": wav_file.getnchannels(),
                "sample_width": wav_file.getsampwidth(),
                "frame_rate": wav_file.getframerate(),
                "frames": wav_file.getnframes(),
            }
        return self.final_text

    def _clean_transcript_text(self, text: str) -> str:
        return text.strip()


class FakeTimedOutStreamingTranscriber:
    last_stop_timed_out = True

    def __init__(self, **_kwargs):
        self.audio_chunks = []

    def start_streaming(self, *, sample_rate: int, callback):
        self.sample_rate = sample_rate
        self.callback = callback

    def feed_audio(self, audio_chunk):
        self.audio_chunks.append(audio_chunk)

    def stop_streaming(self) -> str:
        return "stale rolling words"


class FakeChunkingBackend:
    name = "fake-chunking-whisper"
    device_info = "chunk-device"

    def __init__(self, text: str = "chunked long-form transcript"):
        self.text = text
        self.single_calls = 0
        self.chunk_files = []
        self.seen_decode_options = None
        self.seen_chunk_decode_options = None

    def transcribe(self, audio_path: str, *, decode_options=None) -> str:
        self.single_calls += 1
        self.seen_decode_options = dict(decode_options or {})
        return "single pass transcript"

    def transcribe_chunks(self, chunk_files, *, decode_options=None):
        self.chunk_files = list(chunk_files)
        self.seen_chunk_decode_options = dict(decode_options or {})
        for chunk_file in self.chunk_files:
            with wave.open(str(chunk_file), "rb") as wav_file:
                assert wav_file.getnframes() > 0
        return self.text


class FakeRuntime:
    def __init__(self, backend):
        self.backend = backend
        self.select_count = 0

    def _select_backend_for_transcription(self):
        self.select_count += 1
        return self.backend


class FakeController:
    def __init__(self, backend=None):
        self.backend = backend or FakeBackend()
        self.current_backend = self.backend
        self.transcription_runtime = FakeRuntime(self.backend)
        self._active_transcription_backend = None
        self._transcription_lock = threading.RLock()


def post(url: str, body: bytes, *, token: str | None = None):
    return post_audio(url, body, content_type="audio/webm;codecs=opus", token=token)


def post_audio(url: str, body: bytes, *, content_type: str, token: str | None = None):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wav_bytes(duration_seconds: float, *, sample_rate: int = 16000, amplitude: int = 1200) -> bytes:
    frame_count = int(duration_seconds * sample_rate)
    frames = []
    for index in range(frame_count):
        sample = amplitude if (index // 80) % 2 == 0 else -amplitude
        frames.append(struct.pack("<h", sample))
    data = b"".join(frames)
    buffer = bytearray()
    import io

    with io.BytesIO() as handle:
        with wave.open(handle, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(data)
        return handle.getvalue()


def post_json(url: str, payload: dict, *, token: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, *, token: str | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def recv_until(sock: socket.socket, marker: bytes) -> bytes:
    data = b""
    while marker not in data:
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
    return data


def recv_exact(sock: socket.socket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise AssertionError("socket closed")
        data += chunk
    return data


def recv_server_json(sock: socket.socket) -> dict:
    header = recv_exact(sock, 2)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    payload = recv_exact(sock, length) if length else b""
    if opcode == 0x8:
        return {"type": "close"}
    assert opcode == 0x1
    return json.loads(payload.decode("utf-8"))


def open_websocket(url: str, *, token: str | None = None) -> tuple[socket.socket, bytes]:
    parsed = urllib.parse.urlparse(url)
    sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
    key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    path = parsed.path
    if parsed.query:
        path += f"?{parsed.query}"
    headers = [
        f"GET {path} HTTP/1.1",
        f"Host: {parsed.hostname}:{parsed.port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if token:
        headers.append(f"Authorization: Bearer {token}")
    sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
    return sock, recv_until(sock, b"\r\n\r\n")


def send_text(sock: socket.socket, payload: dict) -> None:
    sock.sendall(make_client_frame(0x1, json.dumps(payload).encode("utf-8")))


def send_binary(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(make_client_frame(0x2, payload))


def test_unauthenticated_health_is_loopback_only():
    controller = FakeController()
    service = RomanVoiceDictationService(
        controller,
        host="127.0.0.1",
        port=0,
        token="secret",
    )
    service.start()
    try:
        payload = get_json(f"{service.base_url}/health")
    finally:
        service.stop()

    assert payload == {"ok": True, "service": "RomanVoice"}
    assert service._is_loopback_request(
        SimpleNamespace(client_address=("127.0.0.1", 1234))
    )
    assert service._is_loopback_request(
        SimpleNamespace(client_address=("::1", 1234))
    )
    assert not service._is_loopback_request(
        SimpleNamespace(client_address=("100.64.0.10", 1234))
    )


def test_request_source_is_sanitized_and_does_not_include_authorization():
    source = RomanVoiceDictationService._request_source(
        SimpleNamespace(
            client_address=("100.64.0.10", 1234),
            headers={
                "X-RomanVoice-Client": "android-floating\r\nignored",
                "Authorization": "Bearer secret-token",
            },
        )
    )

    assert source == "100.64.0.10/android-floating ignored"
    assert "secret-token" not in source


def test_service_rejects_unauthenticated_transcribe():
    controller = FakeController()
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        try:
            post(f"{service.base_url}/v1/transcribe", b"fake-audio")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
            payload = json.loads(exc.read().decode("utf-8"))
            assert payload["ok"] is False
        else:
            raise AssertionError("Expected HTTP 401")
    finally:
        service.stop()


def test_service_transcribes_raw_audio_with_bearer_token():
    controller = FakeController()
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        payload = post(
            f"{service.base_url}/v1/transcribe?polish=off",
            b"fake-audio",
            token="secret",
        )
    finally:
        service.stop()

    assert payload["ok"] is True
    assert payload["text"] == "add clean the window"
    assert payload["raw_text"] == "add clean the window"
    assert payload["backend"] == "fake-whisper"
    assert payload["device_info"] == "test-device"
    assert payload["bytes_received"] == len(b"fake-audio")
    assert payload["decode_profile"] == config.SERVICE_HTTP_DECODE_PROFILE
    assert payload["used_polish"] is False
    assert controller.backend.seen_bytes == b"fake-audio"
    assert controller.backend.seen_decode_options["language"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_LANGUAGE
    )
    assert controller.backend.seen_decode_options["condition_on_previous_text"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT
    )
    assert controller.backend.seen_decode_options["initial_prompt"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_INITIAL_PROMPT
    )
    assert controller.backend.seen_decode_options["vad_filter"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED
    )
    if config.SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED:
        assert controller.backend.seen_decode_options["vad_parameters"] == {
            "min_silence_duration_ms": (
                config.SERVICE_HTTP_FASTER_WHISPER_VAD_MIN_SILENCE_MS
            )
        }
    else:
        assert "vad_parameters" not in controller.backend.seen_decode_options
    assert not controller.backend.seen_path.exists()


def test_service_chunks_long_http_audio_and_saves_recovery_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "SERVICE_HTTP_LONG_FORM_CHUNK_MIN_SECONDS", 1.0)
    monkeypatch.setattr(config, "SERVICE_HTTP_SUSPECT_LOW_DENSITY_MIN_SECONDS", 999.0)
    backend = FakeChunkingBackend("chunk one chunk two")
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        payload = post_audio(
            f"{service.base_url}/v1/transcribe?polish=off",
            wav_bytes(2.0),
            content_type="audio/wav",
            token="secret",
        )
    finally:
        service.stop()

    assert payload["ok"] is True
    assert payload["text"] == "chunk one chunk two"
    assert payload["final_source"] == "http_chunked_wav"
    assert payload["chunk_count"] >= 1
    assert payload["audio_duration_seconds"] == 2.0
    assert payload["transcript_char_count"] == len("chunk one chunk two")
    assert payload["suspect_truncated"] is False
    assert Path(payload["debug_audio_path"]).name.startswith("romanvoice_service_upload_")
    assert Path(payload["debug_audio_path"]).exists()
    assert (tmp_path / "romanvoice_service_upload_last.wav").exists()
    assert backend.single_calls == 0
    assert backend.chunk_files
    assert backend.seen_chunk_decode_options["condition_on_previous_text"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT
    )
    assert backend.seen_chunk_decode_options["initial_prompt"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_INITIAL_PROMPT
    )


def test_service_keeps_bounded_timestamped_http_upload_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "SERVICE_HTTP_DIAGNOSTIC_UPLOAD_KEEP_COUNT", 2)
    controller = FakeController()
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        payloads = [
            post(f"{service.base_url}/v1/transcribe?polish=off", f"fake-audio-{index}".encode(), token="secret")
            for index in range(3)
        ]
    finally:
        service.stop()

    diagnostic_dir = tmp_path / "service_uploads"
    uploads = sorted(diagnostic_dir.glob("romanvoice_service_upload_*"))
    assert len(uploads) == 2
    assert payloads[-1]["debug_audio_path"] == str(uploads[-1])
    assert (tmp_path / "romanvoice_service_upload_last.webm").read_bytes() == b"fake-audio-2"
    assert Path(payloads[0]["debug_audio_path"]).exists() is False


def test_service_flags_suspiciously_short_long_http_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "SERVICE_HTTP_LONG_FORM_CHUNK_MIN_SECONDS", 1.0)
    monkeypatch.setattr(config, "SERVICE_HTTP_SUSPECT_LOW_DENSITY_MIN_SECONDS", 1.0)
    monkeypatch.setattr(config, "SERVICE_HTTP_SUSPECT_MIN_EXPECTED_CHARS", 100)
    monkeypatch.setattr(config, "SERVICE_HTTP_SUSPECT_MIN_CHARS_PER_MINUTE", 1000.0)
    backend = FakeChunkingBackend("tiny")
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        payload = post_audio(
            f"{service.base_url}/v1/transcribe?polish=off",
            wav_bytes(2.0),
            content_type="audio/wav",
            token="secret",
        )
    finally:
        service.stop()

    assert payload["text"] == "tiny"
    assert payload["suspect_truncated"] is True
    assert "Transcription looks incomplete" in payload["transcription_warning"]
    assert payload["debug_audio_path"]


def test_service_tracks_phone_floating_heartbeat_status():
    controller = FakeController()
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    base_url = service.base_url
    try:
        initial = get_json(f"{base_url}/v1/phone/status", token="secret")
        heartbeat = post_json(
            f"{base_url}/v1/phone/heartbeat",
            {
                "surface": "floating",
                "event": "heartbeat",
                "available": True,
                "recording": False,
                "connecting": False,
            },
            token="secret",
        )
        inactive = post_json(
            f"{base_url}/v1/phone/heartbeat",
            {
                "surface": "tile",
                "event": "floating_service_unavailable",
                "available": False,
                "recording": False,
                "connecting": False,
            },
            token="secret",
        )
        detailed = get_json(f"{base_url}/v1/health", token="secret")
    finally:
        service.stop()

    assert initial["phone"]["status"] == "unseen"
    assert initial["phone"]["ok"] is False
    assert heartbeat["phone"]["status"] == "ok"
    assert heartbeat["phone"]["ok"] is True
    assert heartbeat["phone"]["surface"] == "floating"
    assert inactive["phone"]["status"] == "inactive"
    assert inactive["phone"]["ok"] is False
    assert inactive["phone"]["event"] == "floating_service_unavailable"
    assert detailed["phone"]["status"] == "inactive"
    assert detailed["runtime"]["pid"] > 0
    assert detailed["runtime"]["executable"]
    assert detailed["runtime"]["cwd"]
    assert detailed["runtime"]["service_port"] == service.port
    assert detailed["runtime"]["base_url"] == base_url
    assert detailed["http_decode_profile"]["name"] == config.SERVICE_HTTP_DECODE_PROFILE
    assert detailed["http_decode_profile"]["language"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_LANGUAGE
    )
    assert detailed["http_decode_profile"]["condition_on_previous_text"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_CONDITION_ON_PREVIOUS_TEXT
    )
    assert detailed["http_decode_profile"]["vad_filter"] == (
        config.SERVICE_HTTP_FASTER_WHISPER_VAD_ENABLED
    )


def test_service_rejects_unauthenticated_phone_status_and_heartbeat():
    controller = FakeController()
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        for request in (
            urllib.request.Request(f"{service.base_url}/v1/phone/status", method="GET"),
            urllib.request.Request(
                f"{service.base_url}/v1/phone/heartbeat",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
        ):
            try:
                urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 401
            else:
                raise AssertionError("Expected HTTP 401")
    finally:
        service.stop()


def test_service_rejects_unauthenticated_streaming_websocket():
    controller = FakeController(FakeStreamingBackend())
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(f"{service.base_url}/v1/transcribe/stream")
        sock.close()
    finally:
        service.stop()

    assert b" 401 " in response


def test_service_rejects_unmasked_client_websocket_frames():
    controller = FakeController(FakeStreamingBackend())
    service = RomanVoiceDictationService(
        controller,
        host="127.0.0.1",
        port=0,
        token="secret",
    )
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        sock.sendall(b"\x81\x02{}")
        assert recv_server_json(sock)["type"] == "close"
        sock.close()
    finally:
        service.stop()


def test_service_rejects_oversized_websocket_frame_before_payload_read():
    controller = FakeController(FakeStreamingBackend())
    service = RomanVoiceDictationService(
        controller,
        host="127.0.0.1",
        port=0,
        token="secret",
    )
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        oversized_length = (4 * 1024 * 1024) + 1
        sock.sendall(b"\x82\xff" + struct.pack("!Q", oversized_length))
        assert recv_server_json(sock)["type"] == "close"
        sock.close()
    finally:
        service.stop()


def test_service_streams_pcm16_audio_with_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    backend = FakeStreamingBackend()
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        send_text(sock, {"type": "start", "sample_rate": 16000, "polish": "off"})
        assert recv_server_json(sock)["type"] == "started"
        assert controller.transcription_runtime.select_count == 0

        send_binary(sock, (b"\x01\x00" * 1600))
        send_text(sock, {"type": "stop"})

        messages = []
        while True:
            payload = recv_server_json(sock)
            messages.append(payload)
            if payload["type"] in {"final", "close"}:
                break
        sock.close()
    finally:
        service.stop()

    final = next(message for message in messages if message["type"] == "final")
    assert final["ok"] is True
    assert final["text"] == "streamed words"
    assert final["raw_text"] == "streamed words"
    assert final["backend"] == "fake-streaming-whisper"
    assert final["device_info"] == "stream-device"
    assert final["bytes_received"] == 3200
    assert final["sample_rate"] == 16000
    assert final["final_source"] == "streaming_preview"
    assert final["audio_duration_seconds"] == 0.1
    assert final["audio_peak"] == 1
    assert final["audio_rms"] == 1.0
    assert final["rolling_text_length"] == len("streamed words")
    assert Path(final["debug_audio_path"]).name == "romanvoice_phone_stream_last.wav"
    assert final["used_polish"] is False
    assert backend.model.seen_samples == 1600
    assert backend.model.seen_kwargs["vad_filter"] is True
    assert controller.transcription_runtime.select_count == 0
    assert backend.final_seen_path is None


def test_service_streaming_returns_empty_for_all_zero_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    backend = FakeStreamingBackend()
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        send_text(sock, {"type": "start", "sample_rate": 16000, "polish": "off"})
        assert recv_server_json(sock)["type"] == "started"

        send_binary(sock, b"\x00\x00" * 1600)
        send_text(sock, {"type": "stop"})

        while True:
            payload = recv_server_json(sock)
            if payload["type"] == "final":
                final = payload
                break
        sock.close()
    finally:
        service.stop()

    assert final["ok"] is True
    assert final["text"] == ""
    assert final["raw_text"] == ""
    assert final["bytes_received"] == 3200
    assert final["final_source"] == "streaming_preview"
    assert final["audio_peak"] == 0
    assert backend.model.seen_samples == 0
    assert backend.final_seen_path is None


def test_service_streaming_falls_back_to_preview_when_final_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "PHONE_STREAM_FINAL_PASS_ENABLED", True)
    backend = FakeStreamingBackend(final_text="")
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        send_text(sock, {"type": "start", "sample_rate": 16000, "polish": "off"})
        assert recv_server_json(sock)["type"] == "started"

        send_binary(sock, (b"\x01\x00" * 1600))
        send_text(sock, {"type": "stop"})

        while True:
            payload = recv_server_json(sock)
            if payload["type"] == "final":
                final = payload
                break
        sock.close()
    finally:
        service.stop()

    assert final["ok"] is True
    assert final["text"] == "streamed words"
    assert final["raw_text"] == "streamed words"
    assert final["final_source"] == "streaming_preview_fallback"
    assert backend.final_wave_info["frames"] == 1600


def test_service_streaming_runs_final_pass_when_rolling_worker_times_out(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "PHONE_STREAM_FINAL_PASS_ENABLED", False)
    import services.dictation_service as dictation_service_module

    monkeypatch.setattr(
        dictation_service_module,
        "StreamingTranscriber",
        FakeTimedOutStreamingTranscriber,
    )
    backend = FakeStreamingBackend(final_text="full final phone words")
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        send_text(sock, {"type": "start", "sample_rate": 16000, "polish": "off"})
        assert recv_server_json(sock)["type"] == "started"

        send_binary(sock, (b"\x01\x00" * 1600))
        send_text(sock, {"type": "stop"})

        while True:
            payload = recv_server_json(sock)
            if payload["type"] == "final":
                final = payload
                break
        sock.close()
    finally:
        service.stop()

    assert final["ok"] is True
    assert final["text"] == "full final phone words"
    assert final["raw_text"] == "full final phone words"
    assert final["final_source"] == "final_wav_after_stream_timeout"
    assert final["rolling_text_length"] == len("stale rolling words")
    assert backend.final_wave_info["frames"] == 1600


def test_service_streaming_prefers_long_preview_when_final_is_truncated(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "PHONE_STREAM_FINAL_PASS_ENABLED", True)
    rolling_text = "rolling phone words " * 45
    final_text = "late phone words " * 12
    backend = FakeStreamingBackend(final_text=final_text, rolling_text=rolling_text)
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        send_text(sock, {"type": "start", "sample_rate": 16000, "polish": "off"})
        assert recv_server_json(sock)["type"] == "started"

        send_binary(sock, (b"\x01\x00" * 800000))
        send_text(sock, {"type": "stop"})

        while True:
            payload = recv_server_json(sock)
            if payload["type"] == "final":
                final = payload
                break
        sock.close()
    finally:
        service.stop()

    assert final["ok"] is True
    assert final["text"] == rolling_text.strip()
    assert final["raw_text"] == rolling_text.strip()
    assert final["final_source"] == "streaming_preview_long_form_guard"
    assert final["audio_duration_seconds"] == 50.0
    assert backend.final_wave_info["frames"] == 800000


def test_service_streaming_prefers_long_preview_when_final_drops_prefix(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "RECORDINGS_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "PHONE_STREAM_FINAL_PASS_ENABLED", True)
    rolling_text = (
        "Here are a couple of thoughts on labeling and new task creation. For example, "
        "routine, colon, morning lunch is repeated twice. Not quite clear what it is. "
        "No context. Open now for the first move makes no sense because there's no "
        "accessible path there. Secondly, I just got an email from Panera about a pickup "
        "order. It would be super useful if the PA could look into the email, gather that "
        "information, the address, the order number, all the information related to that, "
        "and put that on the VA so it could potentially be able to just tap it and get "
        "directions and go and get the order. Stuff like that would be super helpful."
    )
    final_text = (
        ", lunch. It's repeated twice. Not quite clear what it is. No context. Open now "
        "for the first move makes no sense because there's no accessible path there. "
        "Secondly, I just got an email from Panera about a pickup order. It would be "
        "super useful if the PA could look into the email, gather that information, the "
        "address, the order number, all the information related to that, and put that on "
        "the PA so I could potentially be able to just tap it and get the directions and "
        "go and get the order. Stuff like that would be super helpful."
    )
    backend = FakeStreamingBackend(final_text=final_text, rolling_text=rolling_text)
    controller = FakeController(backend)
    service = RomanVoiceDictationService(controller, host="127.0.0.1", port=0, token="secret")
    service.start()
    try:
        sock, response = open_websocket(
            f"{service.base_url}/v1/transcribe/stream",
            token="secret",
        )
        assert b" 101 " in response
        assert recv_server_json(sock)["type"] == "ready"

        send_text(sock, {"type": "start", "sample_rate": 16000, "polish": "off"})
        assert recv_server_json(sock)["type"] == "started"

        send_binary(sock, (b"\x01\x00" * 1008000))
        send_text(sock, {"type": "stop"})

        while True:
            payload = recv_server_json(sock)
            if payload["type"] == "final":
                final = payload
                break
        sock.close()
    finally:
        service.stop()

    assert final["ok"] is True
    assert final["text"] == rolling_text
    assert final["raw_text"] == rolling_text
    assert final["final_source"] == "streaming_preview_long_form_guard"
    assert final["audio_duration_seconds"] == 63.0
    assert backend.final_wave_info["frames"] == 1008000
