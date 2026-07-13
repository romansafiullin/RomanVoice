"""Minimal WebSocket framing helpers for RomanVoice local clients."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any


class WebSocketProtocolError(Exception):
    """Raised when a peer sends an unsupported or invalid WebSocket frame."""


@dataclass(frozen=True)
class WebSocketMessage:
    kind: str
    data: bytes | str | None = None


def websocket_accept_key(client_key: str) -> str:
    digest = hashlib.sha1(
        (client_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class WebSocketConnection:
    """Small RFC 6455 subset sufficient for local JSON + binary audio streams."""

    def __init__(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        max_frame_bytes: int = 1024 * 1024,
    ) -> None:
        if max_frame_bytes <= 0:
            raise ValueError("max_frame_bytes must be positive")
        self.handler = handler
        self.max_frame_bytes = int(max_frame_bytes)
        self._send_lock = threading.Lock()
        self.closed = False

    @classmethod
    def accept(
        cls,
        handler: BaseHTTPRequestHandler,
        *,
        max_frame_bytes: int = 1024 * 1024,
    ) -> "WebSocketConnection":
        key = handler.headers.get("Sec-WebSocket-Key", "").strip()
        upgrade = handler.headers.get("Upgrade", "").lower()
        connection = handler.headers.get("Connection", "").lower()
        version = handler.headers.get("Sec-WebSocket-Version", "").strip()
        if (
            upgrade != "websocket"
            or "upgrade" not in {value.strip() for value in connection.split(",")}
            or not key
        ):
            raise WebSocketProtocolError("missing WebSocket upgrade headers")
        if version != "13":
            raise WebSocketProtocolError("unsupported WebSocket version")
        try:
            decoded_key = base64.b64decode(key, validate=True)
        except (ValueError, TypeError) as exc:
            raise WebSocketProtocolError("invalid WebSocket key") from exc
        if len(decoded_key) != 16:
            raise WebSocketProtocolError("invalid WebSocket key")

        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", websocket_accept_key(key))
        handler.end_headers()
        return cls(handler, max_frame_bytes=max_frame_bytes)

    def read_message(self) -> WebSocketMessage:
        while True:
            header = self._read_exact(2)
            first, second = header[0], header[1]
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if first & 0x70:
                raise WebSocketProtocolError("reserved WebSocket bits are not supported")
            if not fin:
                raise WebSocketProtocolError("fragmented WebSocket frames are not supported")
            if not masked:
                raise WebSocketProtocolError("client WebSocket frames must be masked")
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
                if length & (1 << 63):
                    raise WebSocketProtocolError("invalid WebSocket frame length")

            if opcode >= 0x8 and length > 125:
                raise WebSocketProtocolError("WebSocket control frame is too large")
            if length > self.max_frame_bytes:
                raise WebSocketProtocolError(
                    f"WebSocket frame exceeds {self.max_frame_bytes} byte limit"
                )

            mask = self._read_exact(4)
            payload = self._read_exact(length) if length else b""
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x8:
                self.closed = True
                return WebSocketMessage("close", payload)
            if opcode == 0x9:
                self.send_pong(payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                return WebSocketMessage("text", payload.decode("utf-8"))
            if opcode == 0x2:
                return WebSocketMessage("binary", payload)

            raise WebSocketProtocolError(f"unsupported WebSocket opcode {opcode}")

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(0x1, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def send_error(self, message: str) -> None:
        self.send_json({"type": "error", "ok": False, "error": message})

    def send_pong(self, payload: bytes = b"") -> None:
        self._send_frame(0xA, payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        payload = struct.pack("!H", code) + reason.encode("utf-8")
        try:
            self._send_frame(0x8, payload)
        finally:
            self.closed = True

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed and opcode != 0x8:
            return
        length = len(payload)
        if length < 126:
            header = bytes([0x80 | opcode, length])
        elif length <= 0xFFFF:
            header = bytes([0x80 | opcode, 126]) + struct.pack("!H", length)
        else:
            header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", length)

        with self._send_lock:
            self.handler.wfile.write(header + payload)
            self.handler.wfile.flush()

    def _read_exact(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self.handler.rfile.read(remaining)
            if not chunk:
                self.closed = True
                raise WebSocketProtocolError("WebSocket connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def make_client_frame(opcode: int, payload: bytes) -> bytes:
    """Build a masked client frame for tests."""
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = bytes([0x80 | opcode, 0x80 | length])
    elif length <= 0xFFFF:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", length)
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return header + mask + masked
