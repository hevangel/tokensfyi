"""Contributor contact encryption with blind indexing (Req 9.3).

The optional contributor email is:
- encrypted at rest with a deployment key (readable only inside the app),
- indexed by a keyed HMAC blind index so recovery can match the submitted
  address without storing or scanning the plaintext,
- purpose-bound: the only consumer is management-link recovery.
"""

from __future__ import annotations

import hashlib
import hmac


def _deployment_key() -> bytes:
    from django.conf import settings

    return hashlib.sha256(str(settings.SECRET_KEY).encode("utf-8")).digest()


def _encryption_key() -> bytes:
    return hmac.new(_deployment_key(), b"contact-encryption-v1", hashlib.sha256).digest()


def _blind_index_key() -> bytes:
    return hmac.new(_deployment_key(), b"contact-blind-index-v1", hashlib.sha256).digest()


def _xor_stream(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def encrypt_email(email: str) -> bytes:
    return _xor_stream(email.strip().lower().encode("utf-8"), _encryption_key())


def decrypt_email(blob: bytes) -> str:
    return _xor_stream(bytes(blob), _encryption_key()).decode("utf-8")


def email_blind_index(email: str) -> str:
    """Keyed HMAC over the normalized address; collision-free for equality."""
    normalized = email.strip().lower().encode("utf-8")
    return hmac.new(_blind_index_key(), normalized, hashlib.sha256).hexdigest()
