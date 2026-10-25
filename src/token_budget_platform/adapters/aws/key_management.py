"""AWS KMS envelope-key adapter."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from token_budget_platform.domain.ports import WrappedDataKey


class AwsKmsKeyManagement:
    """Generate and decrypt per-record AES-256 data keys with customer KMS."""

    def __init__(self, key_id: str, *, client: Any) -> None:
        if not key_id:
            raise ValueError("KMS key id is required")
        self.key_id = key_id
        self.client = client
        self._destroyed: set[UUID] = set()

    def generate_data_key(self, purpose: str) -> WrappedDataKey:
        if not purpose or len(purpose) > 128:
            raise ValueError("key purpose must be non-empty and bounded")
        response = self.client.generate_data_key(
            KeyId=self.key_id,
            KeySpec="AES_256",
            EncryptionContext={"purpose": purpose},
        )
        key_id = uuid4()
        return WrappedDataKey(
            key_id=key_id,
            ciphertext=bytes(response["CiphertextBlob"]),
            algorithm="aws-kms-aes-256",
            provider_reference=self.key_id,
        )

    def decrypt_data_key(self, wrapped: WrappedDataKey) -> bytes:
        if wrapped.key_id in self._destroyed:
            raise KeyError("data-key reference has been destroyed")
        response = self.client.decrypt(
            CiphertextBlob=wrapped.ciphertext,
            KeyId=wrapped.provider_reference or self.key_id,
        )
        plaintext = bytes(response["Plaintext"])
        if len(plaintext) != 32:
            raise ValueError("KMS returned an invalid AES-256 data key")
        return plaintext

    def destroy_reference(self, key_id: UUID) -> None:
        # KMS ciphertexts are immutable; destroying the application reference is
        # the crypto-erasure boundary and prevents future decryption by this port.
        self._destroyed.add(key_id)
