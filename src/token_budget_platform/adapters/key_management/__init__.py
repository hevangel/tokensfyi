"""Local envelope-key adapter with an injected development master key."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import UUID, uuid4

from token_budget_platform.domain.ports import WrappedDataKey


class LocalKeyManagement:
    """Ephemeral local substitute; the master key must be supplied by configuration."""

    def __init__(self, master_key: bytes | None = None) -> None:
        self._master_key = master_key or secrets.token_bytes(32)
        if len(self._master_key) < 32:
            raise ValueError("local master key must contain at least 32 bytes")
        self._destroyed: set[UUID] = set()

    def generate_data_key(self, purpose: str) -> WrappedDataKey:
        if not purpose or len(purpose) > 128:
            raise ValueError("key purpose must be non-empty and bounded")
        key_id = uuid4()
        plaintext = secrets.token_bytes(32)
        return WrappedDataKey(key_id=key_id, ciphertext=self._crypt(key_id, plaintext))

    def decrypt_data_key(self, wrapped: WrappedDataKey) -> bytes:
        if wrapped.key_id in self._destroyed:
            raise KeyError("data-key reference has been destroyed")
        return self._crypt(wrapped.key_id, wrapped.ciphertext)

    def destroy_reference(self, key_id: UUID) -> None:
        self._destroyed.add(key_id)

    def _crypt(self, key_id: UUID, content: bytes) -> bytes:
        result = bytearray()
        for counter in range((len(content) + 31) // 32):
            block = hmac.new(
                self._master_key,
                key_id.bytes + counter.to_bytes(4, "big"),
                hashlib.sha256,
            ).digest()
            result.extend(block)
        return bytes(value ^ result[index] for index, value in enumerate(content))
