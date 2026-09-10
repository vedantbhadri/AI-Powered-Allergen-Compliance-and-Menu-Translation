"""
s3_service.py

Stores raw uploaded menu files (PDF/JPG/PNG) for audit/reprocessing.
Falls back to writing under /tmp when LOCAL_MODE=true.
"""
from __future__ import annotations
import logging
import os
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# Use S3_REGION if set, otherwise fall back to AWS_REGION
S3_REGION = os.environ.get("S3_REGION", os.environ.get("AWS_REGION", "ap-southeast-2"))
BUCKET_NAME = os.environ.get("S3_BUCKET", "")
LOCAL_MODE = os.environ.get("LOCAL_MODE", "false").lower() == "true"
LOCAL_UPLOAD_DIR = os.environ.get("LOCAL_UPLOAD_DIR", "/tmp/allergen_uploads")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=S3_REGION)
    return _client


def upload_raw_file(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Returns a storage key/path for the saved raw file."""
    key = f"uploads/{uuid.uuid4().hex[:10]}-{filename}"

    if LOCAL_MODE:
        os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)
        path = os.path.join(LOCAL_UPLOAD_DIR, key.replace("/", "_"))
        with open(path, "wb") as f:
            f.write(file_bytes)
        return path

    try:
        _get_client().put_object(
            Bucket=BUCKET_NAME, Key=key, Body=file_bytes, ContentType=content_type
        )
        return f"s3://{BUCKET_NAME}/{key}"
    except (BotoCoreError, ClientError) as exc:
        logger.error("S3 upload failed: %s", exc)
        raise
