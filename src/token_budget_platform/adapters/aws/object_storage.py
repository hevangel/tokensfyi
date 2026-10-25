"""Private S3 object-store adapter implementing the shared object port."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO

from token_budget_platform.domain.ports import ObjectMetadata


class AwsS3ObjectStore:
    """S3 storage using private keys and server-side KMS encryption."""

    def __init__(self, bucket: str, *, kms_key_id: str, client: Any) -> None:
        if not bucket or not kms_key_id:
            raise ValueError("S3 bucket and KMS key are required")
        self.bucket = bucket
        self.kms_key_id = kms_key_id
        self.client = client

    def put_encrypted(self, key: str, stream: BinaryIO, metadata: ObjectMetadata) -> None:
        _validate_key(key)
        body = stream.read()
        if not isinstance(body, bytes):
            raise TypeError("object streams must yield bytes")
        if metadata.byte_count and metadata.byte_count != len(body):
            raise ValueError("object metadata byte count does not match stream")
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.kms_key_id,
        }
        if metadata.content_type:
            request["ContentType"] = metadata.content_type
        if metadata.checksum:
            request["ChecksumSHA256"] = metadata.checksum
        if metadata.values:
            request["Metadata"] = dict(metadata.values)
        self.client.put_object(**request)

    @contextmanager
    def open_encrypted(self, key: str) -> Iterator[BinaryIO]:
        _validate_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            yield body
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()

    def delete(self, key: str) -> None:
        _validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)


def _validate_key(key: str) -> None:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError("object keys must be non-empty relative private keys")
