"""
MinIO (S3-compatible) client wrapper.

- Lazy-init singleton
- Ensures the project bucket exists
- Sets an anonymous-read policy on the `posters/` and `backdrops/` prefixes
  so Streamlit (any browser, really) can load images directly without auth
- Upload helpers that return the public URL
"""

from __future__ import annotations

import io
import json
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from app.config import (
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_PUBLIC_BASE,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
)


@lru_cache(maxsize=1)
def get_client() -> Minio:
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )
    _ensure_bucket(client, MINIO_BUCKET)
    _ensure_public_read_prefixes(
        client, MINIO_BUCKET, prefixes=("posters/", "backdrops/")
    )
    return client


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        print(f"[minio] created bucket {bucket!r}")


def _ensure_public_read_prefixes(
    client: Minio, bucket: str, prefixes: tuple[str, ...]
) -> None:
    """
    Apply a bucket policy that allows ANONYMOUS GET on the given prefixes.
    Writes still require credentials. Idempotent.
    """
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [
                    f"arn:aws:s3:::{bucket}/{p}*" for p in prefixes
                ],
            }
        ],
    }
    try:
        current = client.get_bucket_policy(bucket)
    except S3Error as e:
        if e.code != "NoSuchBucketPolicy":
            raise
        current = None

    desired = json.dumps(policy)
    if current == desired:
        return  # already set
    client.set_bucket_policy(bucket, desired)
    print(f"[minio] public-read policy applied to prefixes: {', '.join(prefixes)}")


# ----- Upload helpers -----
def put_bytes(key: str, data: bytes, content_type: str = "image/jpeg") -> str:
    """Upload raw bytes under <bucket>/<key>. Returns the public URL."""
    client = get_client()
    client.put_object(
        MINIO_BUCKET,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return public_url(key)


def public_url(key: str) -> str:
    """Deterministic URL for an object we uploaded with a public-read prefix."""
    base = MINIO_PUBLIC_BASE.rstrip("/")
    return f"{base}/{MINIO_BUCKET}/{key.lstrip('/')}"


def exists(key: str) -> bool:
    """True if <bucket>/<key> is already uploaded."""
    client = get_client()
    try:
        client.stat_object(MINIO_BUCKET, key)
        return True
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchBucket"):
            return False
        raise
