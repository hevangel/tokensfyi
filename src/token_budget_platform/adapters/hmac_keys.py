"""Versioned HMAC key provider for capability token hashing.

In production, keys are loaded from KMS/Secrets Manager.
Locally, a deterministic key is derived from the local master key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


class LocalHmacKeyProvider:
    """Local HMAC key provider deriving versioned keys from a master secret.

    Key derivation: HMAC-SHA256(master_key, b"capability-hmac-v{version}")
    """

    def __init__(self, master_key: bytes | None = None) -> None:
        self._master_key = master_key or secrets.token_bytes(32)
        if len(self._master_key) < 32:
            raise ValueError("HMAC master key must be at least 32 bytes")
        self._version = 1

    def current_key_version(self) -> int:
        return self._version

    def get_key(self, version: int) -> bytes:
        """Derive a key for the given version."""
        if version < 1:
            raise ValueError("Key version must be positive")
        label = f"capability-hmac-v{version}".encode()
        return hmac.new(self._master_key, label, hashlib.sha256).digest()

    def rotate(self) -> int:
        """Advance to the next key version. Returns the new version."""
        self._version += 1
        return self._version
