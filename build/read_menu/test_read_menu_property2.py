# Feature: menu-read-edit-lambdas, Property 2: Missing translation codes are reported exactly, none fabricated
"""
Property-based test for readMenu Property 2.

Validates that, for any dish whose embedded Translations_Map contains an
arbitrary subset of the supported language codes ``{es, de, ja, zh}``, the read
output (GET /menus/{restaurantId}):

  - returns every present code's entry unchanged (byte-for-byte the stored value),
  - flags each absent code as unavailable,
  - such that the union of (present/available codes) and (flagged-missing codes)
    is exactly those four codes,
  - fabricates no translation content for absent codes, and
  - issues no write against DynamoDB.

DynamoDB is mocked by monkeypatching ``dynamo_service.list_items`` (and guarding
the write-capable ``put_item``) so the test exercises readMenu's own logic, never
AWS. Runs a minimum of 100 iterations.

**Validates: Requirements 1.2**
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# --- Import wiring: put the shared layer (build/layer/python) and the readMenu
# --- package (build/read_menu) on sys.path so `from services import ...` and
# --- `import handler` resolve exactly as they do at Lambda runtime.
_THIS_DIR = Path(__file__).resolve().parent            # build/read_menu
_BUILD_DIR = _THIS_DIR.parent                          # build
_REPO_ROOT = _BUILD_DIR.parent                         # repo root
_LAYER_PYTHON = _BUILD_DIR / "layer" / "python"

for _p in (str(_LAYER_PYTHON), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services import dynamo_service  # noqa: E402  (shared Lambda layer)
import handler  # noqa: E402  (build/read_menu/handler.py)


SUPPORTED_CODES = ("es", "de", "ja", "zh")


# A single translation entry as produced upstream: {"name": ..., "description": ...}.
_translation_entry = st.fixed_dictionaries(
    {
        "name": st.text(min_size=0, max_size=40),
        "description": st.text(min_size=0, max_size=80),
    }
)

# A Translations_Map over an ARBITRARY SUBSET of the four supported codes
# (including the empty subset and the full set).
_translations_map = st.dictionaries(
    keys=st.sampled_from(SUPPORTED_CODES),
    values=_translation_entry,
    min_size=0,
    max_size=4,
)


def _make_dish(translations: dict, suffix: str) -> dict:
    """Build a plausible dish row carrying the given Translations_Map."""
    return {
        "menu_id": "kiwi-cafe",
        "item_id": f"dish-{suffix}",
        "name": "Seafood Chowder",
        "description": "Creamy chowder",
        "source": "upload",
        "status": "ready",
        "allergens": {"confirmed": ["Fish"], "display_tags": ["Contains Fish"]},
        "diet_tags": [],
        "translations": translations,
        "updated_at": 1730000000,
    }


class _WriteAttempted(AssertionError):
    """Raised if readMenu attempts any DynamoDB write during a read."""


@given(translations=_translations_map)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_missing_translation_codes_reported_exactly_none_fabricated(monkeypatch, translations):
    # Mock the ONLY read path readMenu uses for listing: a single Query.
    call_log = {"list_items": 0}

    def fake_list_items(menu_id):
        call_log["list_items"] += 1
        return [_make_dish(translations, "1a2b")]

    monkeypatch.setattr(dynamo_service, "list_items", fake_list_items)

    # Guard every write-capable entry point: a read must never invoke them.
    def _forbid_write(*_args, **_kwargs):
        raise _WriteAttempted("readMenu attempted a DynamoDB write during a read")

    if hasattr(dynamo_service, "put_item"):
        monkeypatch.setattr(dynamo_service, "put_item", _forbid_write)

    event = {
        "routeKey": handler.ROUTE_LIST_MENU,
        "pathParameters": {"restaurantId": "kiwi-cafe"},
    }
    resp = handler.handler(event, None)

    # Read succeeded via exactly one Query, zero per-translation queries.
    assert resp["statusCode"] == 200
    assert call_log["list_items"] == 1

    body = json.loads(resp["body"])
    assert len(body["items"]) == 1
    dish = body["items"][0]

    status = dish["translations_status"]

    # 1. The union of available + unavailable codes is EXACTLY the four codes.
    assert set(status.keys()) == set(SUPPORTED_CODES)

    present_codes = set(translations.keys())
    for code in SUPPORTED_CODES:
        if code in present_codes:
            # Present codes are flagged available...
            assert status[code] == "available"
        else:
            # ...and absent codes are flagged unavailable.
            assert status[code] == "unavailable"

    # 2. Present entries are returned UNCHANGED (byte-for-byte the stored value);
    #    the embedded Translations_Map is not mutated.
    assert dish["translations"] == translations

    # 3. No fabricated content: no code absent from the stored map appears in the
    #    returned translations map, and nothing is flagged available that wasn't
    #    actually present.
    assert set(dish["translations"].keys()) == present_codes
    available = {c for c, s in status.items() if s == "available"}
    assert available == present_codes


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
