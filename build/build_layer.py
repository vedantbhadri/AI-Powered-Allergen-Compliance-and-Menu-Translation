#!/usr/bin/env python3
"""Package the shared Lambda Layer for the readMenu / editMenu functions.

This script copies every Python module from ``app/services/*.py`` into
``build/layer/python/services/`` **byte-for-byte, with no edits**. The copy is a
pure passthrough (``shutil.copy2``) so the "unmodified module" guarantee holds:
both Lambda functions import the exact same, unaltered service modules via
``from services import ...`` and therefore cannot drift.

Layer layout produced (Lambda puts ``python/`` on ``sys.path``):

    build/layer/python/services/
        __init__.py
        allergen_rules.py
        bedrock_service.py
        dynamo_service.py
        s3_service.py
        textract_service.py

Run from anywhere; paths are resolved relative to the repository root:

    python build/build_layer.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Repository root = parent of this script's directory (build/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "app" / "services"
LAYER_SERVICES_DIR = REPO_ROOT / "build" / "layer" / "python" / "services"


def build_layer() -> list[Path]:
    """Copy every ``*.py`` module from app/services into the layer, verbatim.

    Returns the list of destination paths that were written.
    """
    if not SOURCE_DIR.is_dir():
        raise SystemExit(f"Source services directory not found: {SOURCE_DIR}")

    LAYER_SERVICES_DIR.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for source in sorted(SOURCE_DIR.glob("*.py")):
        destination = LAYER_SERVICES_DIR / source.name
        # copy2 performs a byte-for-byte copy (and preserves metadata) with no
        # transformation whatsoever -- this is the unmodified-module guarantee.
        shutil.copy2(source, destination)
        copied.append(destination)

    return copied


def main() -> int:
    copied = build_layer()
    if not copied:
        print(f"No .py modules found in {SOURCE_DIR}", file=sys.stderr)
        return 1

    print(f"Packaged {len(copied)} module(s) into {LAYER_SERVICES_DIR}:")
    for path in copied:
        print(f"  - {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
