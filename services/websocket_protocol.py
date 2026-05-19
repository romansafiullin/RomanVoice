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

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        self.handler = handler
        self._send_lock = threading.Lock()
        self.closed = False

    @classmethod
    def accept(cls, handler: BaseHTTPRequestHandler) -> "WebSocketConnection":
        key = handler.headers.get("Sec-WebSocket-Key", "").strip()
        upgrade = handler.headers.get("Upgrade", "").lower()
        if upgrade != "websocket" or not key:
            raise WebSocketProtocolError("missing WebSocket upgrade headers")

        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", websocket_accept_key(key))
        handler.end_headers()
        return cls(handler)

    def read_message(self) -> WebSocketMessage:
        while True:
            header = self._read_exact(2)
            first, second = header[0], header[1]
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if not fin:
                raise WebSocketProtocolError("fragmented WebSocket frames are not supported")
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]

            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
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
        data = self.handler.rfile.read(length)
        if len(data) != length:
            self.closed = True
            raise WebSocketProtocolError("WebSocket connection closed")
        return data


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
