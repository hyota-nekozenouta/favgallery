"""Cloudflare R2 storage backend (S3-compatible via boto3).

When R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME
environment variables are all set, media is uploaded to R2 after every sync.
When any variable is absent the module degrades gracefully: callers get None
from ``r2_config_from_env`` and skip R2 operations.

Install the optional dependency:
    pip install 'xlikes-viewer[cloud]'
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Set


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def r2_config_from_env() -> R2Config | None:
    """Return R2Config from environment variables, or None if any var is missing."""
    account_id = os.environ.get("R2_ACCOUNT_ID", "")
    access_key_id = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    bucket_name = os.environ.get("R2_BUCKET_NAME", "")
    if not all([account_id, access_key_id, secret_access_key, bucket_name]):
        return None
    return R2Config(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket_name=bucket_name,
    )


class R2Client:
    """Thin wrapper around a boto3 S3 client pointed at Cloudflare R2."""

    def __init__(self, config: R2Config) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for R2 storage. "
                "Install it with: pip install 'xlikes-viewer[cloud]'"
            ) from exc
        from botocore.config import Config  # type: ignore[import-untyped]

        self._config = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name="auto",
            config=Config(connect_timeout=10, read_timeout=30),
        )
        self._bucket = config.bucket_name

    def upload_file(self, local_path: Path, key: str) -> None:
        """Upload ``local_path`` to R2 under ``key``."""
        self._client.upload_file(str(local_path), self._bucket, key)

    def delete_object(self, key: str) -> None:
        """Delete the object stored under ``key``.

        Idempotent: S3/R2 ``delete_object`` does not error when the key is
        already absent, so callers can use this freely to purge orphaned media.
        """
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def object_exists(self, key: str) -> bool:
        """Return True if an object with ``key`` exists in the bucket."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def list_all_keys(self) -> Set[str]:
        """Return the set of all object keys in the bucket.

        Uses paginated list_objects_v2 — far faster than per-file head_object
        when checking many files at once.
        """
        keys: Set[str] = set()
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket):
            for obj in page.get("Contents", []):
                keys.add(obj["Key"])
        return keys

    def stream_object(self, key: str) -> tuple[int, str, Iterator[bytes]]:
        """Stream an object from R2.

        Returns ``(content_length_bytes, content_type, byte_iterator)``.
        Raises ``botocore.exceptions.ClientError`` with code ``"NoSuchKey"``
        when the key is absent.
        """
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        content_length: int = int(response.get("ContentLength", 0))
        content_type: str = response.get("ContentType", "application/octet-stream")

        def _iter() -> Iterator[bytes]:
            yield from body.iter_chunks(64 * 1024)

        return content_length, content_type, _iter()
