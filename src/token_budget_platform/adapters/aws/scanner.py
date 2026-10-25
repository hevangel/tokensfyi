"""clamd INSTREAM scanning adapter for the shared scanner service role.

The same OCI image runs a scanner task that reaches ClamAV over TCP, so no
local socket or filesystem access is required (Req 15.4).  Streams are uploaded
with the clamd INSTREAM protocol; only the sanitized ScanResult leaves this
module so security events never carry file content (Req 10.8).
"""

from __future__ import annotations

import socket
from typing import BinaryIO

from token_budget_platform.domain.ports import ScanResult

_CHUNK_SIZE = 8192
_MAX_STREAM_BYTES = 10 * 1024 * 1024  # matches the evidence intake allowlist
_DEFAULT_TIMEOUT_SECONDS = 30.0


class AwsClamAvScanner:
    """Scan byte streams through a TCP clamd daemon using the INSTREAM command."""

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 3310,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_stream_bytes: int = _MAX_STREAM_BYTES,
    ) -> None:
        if not host:
            raise ValueError("clamd host is required")
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.max_stream_bytes = max_stream_bytes
        self.version = "clamd-instream-1"

    def scan(self, stream: BinaryIO) -> ScanResult:
        """Stream the payload to clamd and translate the reply into a ScanResult."""
        data = stream.read(self.max_stream_bytes + 1)
        if not isinstance(data, bytes):
            raise TypeError("scanner streams must yield bytes")
        if len(data) > self.max_stream_bytes:
            return ScanResult(
                clean=False,
                outcome="ERROR",
                scanner_version=self.version,
                signature="oversized",
            )
        try:
            reply = self._instream(data)
        except OSError:
            return ScanResult(
                clean=False,
                outcome="ERROR",
                scanner_version=self.version,
                signature="unreachable",
            )
        return self._interpret(reply)

    def _instream(self, data: bytes) -> str:
        """Send zINSTREAM chunks and return the daemon's final reply line."""
        with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as conn:
            conn.sendall(b"zINSTREAM\0")
            for offset in range(0, len(data), _CHUNK_SIZE):
                chunk = data[offset : offset + _CHUNK_SIZE]
                conn.sendall(len(chunk).to_bytes(4, "big") + chunk)
            conn.sendall((0).to_bytes(4, "big"))
            reply = bytearray()
            while True:
                block = conn.recv(4096)
                if not block:
                    break
                reply.extend(block)
                if len(reply) > 4096:
                    break
        return bytes(reply).rstrip(b"\0").decode("utf-8", errors="replace").strip()

    def _interpret(self, reply: str) -> ScanResult:
        """Map clamd replies onto the sanitized outcome vocabulary."""
        normalized = reply.upper()
        if normalized.startswith("OK"):
            return ScanResult(clean=True, outcome="CLEAN", scanner_version=self.version)
        if "FOUND" in normalized:
            return ScanResult(
                clean=False,
                outcome="INFECTED",
                scanner_version=self.version,
                signature="match",
            )
        if "SIZE" in normalized:
            return ScanResult(
                clean=False, outcome="ERROR", scanner_version=self.version, signature="oversized"
            )
        return ScanResult(
            clean=False, outcome="ERROR", scanner_version=self.version, signature="invalid"
        )
