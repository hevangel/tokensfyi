"""In-memory local object storage with private, stream-oriented semantics."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from token_budget_platform.domain.ports import ObjectMetadata


@dataclass(frozen=True, slots=True)
class StoredObject:
    content: bytes
    metadata: ObjectMetadata


class LocalObjectStore:
    """A deterministic object-store substitute; no object is publicly addressable."""

    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}

    def put_encrypted(self, key: str, stream: BinaryIO, metadata: ObjectMetadata) -> None:
        if not key or key.startswith("/"):
            raise ValueError("object keys must be non-empty relative private keys")
        content = stream.read()
        if not isinstance(content, bytes):
            raise TypeError("object streams must yield bytes")
        if metadata.byte_count and metadata.byte_count != len(content):
            raise ValueError("object metadata byte count does not match stream")
        self._objects[key] = StoredObject(content=content, metadata=metadata)

    @contextmanager
    def open_encrypted(self, key: str) -> Iterator[BinaryIO]:
        try:
            stored = self._objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc
        yield BytesIO(stored.content)

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    def metadata(self, key: str) -> ObjectMetadata:
        return self._objects[key].metadata

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._objects))
