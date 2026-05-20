from __future__ import annotations

import base64
import json
import socket
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from services.dictation_service import RomanVoiceDictationService
from services.websocket_protocol import make_client_frame


class FakeBackend:
    name = "fake-whisper"
    device_info = "test-device"

    def __init__(self):
        self.seen_path = None
        self.seen_bytes = None

    def transcribe(self, audio_path: str) -> str:
        path = Path(audio_path)
        self.seen_path = path
        self.seen_bytes = path.read_bytes()
        assert path.exists()
        assert path.suffix == ".webm"
        return "add clean the window"


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class FakeStreamingModel:
    def __init__(self):
        self.seen_samples = 0
        self.seen_kwargs = None

    def transcribe(self, audio_array, **kwargs):
        self.seen_samples = len(audio_array)
        self.seen_kwargs = kwargs
        return [FakeSegment("streamed words")], SimpleNamespace(language="en")


class FakeStreamingBackend:
    name = "fake-streaming-whisper"
    device_info = "stream-device"

    def __init__(self):
        self.model = FakeStreamingModel()

    def ensure_loaded(self):
        return None

    def _clean_transcript_text(self, text: str) -> str:
        return text.strip()


class FakeRuntime:
    def __init__(self, backend):
        self.backend = backend

    def _select_backend_for_transcription(self):
        return self.backend


class FakeController:
    def __init__(self, backend=None):
        self.backend = backend or FakeBackend()
        self.current_backend = self.backend
        self.transcription_runtime = FakeRuntime(self.backend)
        self._active_transcription_backend = None
        self._transcription_lock = threading.RLock()


def post(url: str, body: bytes, *, token: str | None = None):
    headers = {"Content-Type": "audio/webm;codecs=opus"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
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
    key = base64.b64encode(b"romanvoice-test-key").decode("ascii")
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
    assert payload["used_polish"] is False
    assert controller.backend.seen_bytes == b"fake-audio"
    assert not controller.backend.seen_path.exists()


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


def test_service_streams_pcm16_audio_with_bearer_token():
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
    assert final["used_polish"] is False
    assert backend.model.seen_samples == 1600
    assert backend.model.seen_kwargs["vad_filter"] is True


def test_service_streaming_returns_empty_for_all_zero_audio():
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
    assert backend.model.seen_samples == 0
