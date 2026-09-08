"""Layer packaging smoke test.

Packaging verification (NOT a property test) for the shared Lambda Layer built by
``build/build_layer.py``. Validates two guarantees:

1. **Byte-for-byte copy** - every module under ``build/layer/python/services/`` is
   identical to its source in ``app/services/`` (content + SHA-256 hash equality).
   This upholds the "unmodified module" guarantee (R3.4 / R4.6): both Lambda
   functions import the exact, unaltered service modules so they cannot drift.
2. **Importable layout** - with ``build/layer/python`` on ``sys.path`` the layer
   contents import as ``from services import dynamo_service`` and
   ``from services import allergen_rules`` (the shape Lambda exposes at runtime).

Run with plain Python (matches the existing ``_smoke_status.py`` convention; no
pytest dependency required)::

    py build/test_layer_packaging.py

Exits non-zero on the first failed assertion.

Requirements: 3.4, 4.6
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Repository root = parent of this script's directory (build/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "app" / "services"
LAYER_PYTHON_DIR = REPO_ROOT / "build" / "layer" / "python"
LAYER_SERVICES_DIR = LAYER_PYTHON_DIR / "services"

# Modules that MUST be present in the layer, verbatim copies of app/services/*.py.
EXPECTED_MODULES = (
    "__init__.py",
    "allergen_rules.py",
    "bedrock_service.py",
    "dynamo_service.py",
    "s3_service.py",
    "textract_service.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_source_module_copied_byte_for_byte() -> None:
    """Each app/services module has a byte-identical copy in the layer."""
    source_modules = sorted(p.name for p in SOURCE_DIR.glob("*.py"))
    assert source_modules, f"No source modules found in {SOURCE_DIR}"

    for name in source_modules:
        source = SOURCE_DIR / name
        layer_copy = LAYER_SERVICES_DIR / name

        assert layer_copy.is_file(), (
            f"Missing layer copy for source module: {name} "
            f"(expected {layer_copy})"
        )

        src_bytes = source.read_bytes()
        layer_bytes = layer_copy.read_bytes()

        # Content equality (the primary guarantee).
        assert src_bytes == layer_bytes, (
            f"Layer copy of {name} differs from source "
            f"({len(src_bytes)} vs {len(layer_bytes)} bytes)"
        )

        # Hash equality (redundant belt-and-braces check the task calls for).
        src_hash = _sha256(source)
        layer_hash = _sha256(layer_copy)
        assert src_hash == layer_hash, (
            f"SHA-256 mismatch for {name}: source {src_hash} != layer {layer_hash}"
        )


def test_expected_modules_present() -> None:
    """All the named service modules exist in the layer."""
    for name in EXPECTED_MODULES:
        layer_copy = LAYER_SERVICES_DIR / name
        assert layer_copy.is_file(), f"Expected layer module missing: {name}"


def test_layer_contents_importable() -> None:
    """With build/layer/python on sys.path, `from services import ...` works."""
    layer_python = str(LAYER_PYTHON_DIR)
    added = False
    if layer_python not in sys.path:
        sys.path.insert(0, layer_python)
        added = True

    # Ensure we import the layer's copy, not any other 'services' package that may
    # already be loaded (e.g. from app/services on the path).
    for mod in [m for m in list(sys.modules) if m == "services" or m.startswith("services.")]:
        del sys.modules[mod]

    try:
        from services import dynamo_service  # noqa: F401
        from services import allergen_rules  # noqa: F401

        # The imported modules must resolve to the layer copies.
        assert Path(dynamo_service.__file__).resolve() == (
            LAYER_SERVICES_DIR / "dynamo_service.py"
        ).resolve(), (
            f"dynamo_service imported from {dynamo_service.__file__}, "
            f"not the layer copy"
        )
        assert Path(allergen_rules.__file__).resolve() == (
            LAYER_SERVICES_DIR / "allergen_rules.py"
        ).resolve(), (
            f"allergen_rules imported from {allergen_rules.__file__}, "
            f"not the layer copy"
        )
    finally:
        if added and layer_python in sys.path:
            sys.path.remove(layer_python)


def main() -> int:
    tests = [
        test_every_source_module_copied_byte_for_byte,
        test_expected_modules_present,
        test_layer_contents_importable,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("ALL LAYER PACKAGING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
