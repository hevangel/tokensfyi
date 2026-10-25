"""Deterministic local malware-scanning adapter."""

from __future__ import annotations

import hashlib
from typing import BinaryIO

from token_budget_platform.domain.ports import ScanResult


class LocalMalwareScanner:
    """Use explicit test signatures locally while keeping the scan port realistic."""

    def __init__(self, infected_markers: tuple[bytes, ...] = (b"EICAR", b"MALWARE")) -> None:
        self.infected_markers = infected_markers
        self.version = "local-signature-1"

    def scan(self, stream: BinaryIO) -> ScanResult:
        content = stream.read()
        if not isinstance(content, bytes):
            raise TypeError("scanner streams must yield bytes")
        digest = hashlib.sha256(content).hexdigest()
        infected = any(marker in content for marker in self.infected_markers)
        return ScanResult(
            clean=not infected,
            outcome="INFECTED" if infected else "CLEAN",
            scanner_version=self.version,
            signature=digest,
        )
