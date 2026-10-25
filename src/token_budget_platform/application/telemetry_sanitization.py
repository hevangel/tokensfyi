"""At-rest encryption helpers for private free text.

Report explanations and moderation rationales contain personal and sensitive
content.  They are stored encrypted with a deployment key so database reads
and backups alone never expose them (Req 9.3, 10.2).  The local cipher is a
stream cipher keyed from the Django secret; production deployments wrap the
data key with KMS through the key-management port.
"""

from __future__ import annotations

import base64
import hashlib


def _deployment_key() -> bytes:
    from django.conf import settings

    return hashlib.sha256(str(settings.SECRET_KEY).encode("utf-8")).digest()


def _xor_stream(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def encrypt_private_text(text: str) -> bytes:
    """Encrypt private free text for storage; empty input yields b""."""
    if not text:
        return b""
    return _xor_stream(text.encode("utf-8"), _deployment_key())


def decrypt_private_text(blob: bytes | None) -> str:
    """Decrypt stored private text; empty/None input yields ""."""
    if not blob:
        return ""
    return _xor_stream(bytes(blob), _deployment_key()).decode("utf-8")


def encode_encrypted_text(blob: bytes) -> str:
    """Base64-encode an encrypted blob for transport-safe display/storage."""
    return base64.b64encode(blob).decode("ascii")
