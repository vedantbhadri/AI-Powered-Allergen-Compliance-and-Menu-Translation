"""
textract_service.py

OCR for uploaded weekly-specials menu files (PDF / JPG / PNG) using
AWS Textract. Per the project brief's "Day 1" assumption, a dish
description may already be typed in via the UI instead of scanned -
OCR is only invoked when a file is actually uploaded.
"""
from __future__ import annotations
import logging
import os
from typing import List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
LOCAL_MODE = os.environ.get("LOCAL_MODE", "false").lower() == "true"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("textract", region_name=AWS_REGION)
    return _client


def extract_text_from_bytes(file_bytes: bytes, content_type: str) -> List[str]:
    """Return a list of text lines detected in the uploaded image/PDF page.

    Textract's synchronous DetectDocumentText works on single-page
    images (JPG/PNG) and single-page PDFs. For simplicity this project
    treats each upload as a single page/menu card, matching the "Weekly
    Specials" upload flow in the mock-up.
    """
    if LOCAL_MODE:
        return ["[offline OCR stub - Textract not called] Sample Dish - description unavailable"]

    try:
        client = _get_client()
        response = client.detect_document_text(Document={"Bytes": file_bytes})
        lines = [
            block["Text"]
            for block in response.get("Blocks", [])
            if block.get("BlockType") == "LINE"
        ]
        return lines
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Textract OCR failed: %s", exc)
        return []
