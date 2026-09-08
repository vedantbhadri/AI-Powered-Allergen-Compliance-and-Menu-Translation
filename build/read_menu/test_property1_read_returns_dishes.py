# Feature: menu-read-edit-lambdas, Property 1: Read returns exactly the stored dishes (translations embedded), sentinel excluded, in one query
"""
Property-based test for readMenu Property 1.

*For any* restaurant partition containing an arbitrary set of dish rows
(``dish-*``) plus at most one ``upload#`` sentinel row,
``GET /menus/{restaurantId}`` returns a collection equal to the set of dish
rows with the sentinel row excluded, each dish carrying its embedded
Translations_Map unchanged, obtained via exactly one ``list_items`` Query and
zero per-translation queries; the same input yields the same collection
regardless of caller.

Validates: Requirements 1.1, 1.2, 1.4, 1.5

DynamoDB is MOCKED: ``dynamo_service.list_items`` is monkeypatched to return a
generated partition, and ``dynamo_service.get_item`` is monkeypatched to a
sentinel that fails the test if ever called (there must be zero per-translation
/ per-dish reads). No AWS access occurs.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from hypothesis import given, settings, strategies as st

# --- Import wiring: put the shared layer (build/layer/python) and the readMenu
# --- package (build/read_menu) on sys.path so `from services import ...` inside
# --- handler.py resolves to the layer copy, exactly as it does at runtime.
_THIS_DIR = Path(__file__).resolve().parent            # build/read_menu
_BUILD_DIR = _THIS_DIR.parent                          # build
_LAYER_PYTHON = _BUILD_DIR / "layer" / "python"        # build/layer/python

for _p in (str(_LAYER_PYTHON), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services import dynamo_service  # noqa: E402  (layer copy)
import handler  # noqa: E402  (build/read_menu/handler.py)

ROUTE_LIST_MENU = "GET /menus/{restaurantId}"
SUPPORTED_CODES = ("es", "de", "ja", "zh")


# ----------------------------------------------------------------- generators
# Restaurant / menu ids and dish item_ids must pass the handler's key-charset
# validator (^[A-Za-z0-9._:#-]+$) and, for the list route, be non-empty.
_key_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=24,
).filter(lambda s: s.strip() != "")

# A single translation entry (embedded on the dish row).
_translation_entry = st.fixed_dictionaries(
    {
        "name": st.text(max_size=40),
        "description": st.text(max_size=80),
    }
)

# A Translations_Map over an arbitrary subset of the four supported codes
# (may be empty, may be missing entirely on a row).
_translations_map = st.dictionaries(
    keys=st.sampled_from(SUPPORTED_CODES),
    values=_translation_entry,
    max_size=4,
)


@st.composite
def _dish_row(draw, menu_id: str):
    """A single ``dish-*`` row for the given menu partition."""
    suffix = draw(
        st.text(
            alphabet="abcdef0123456789",
            min_size=1,
            max_size=10,
        )
    )
    row = {
        "menu_id": menu_id,
        "item_id": "dish-" + suffix,
        "name": draw(st.text(max_size=40)),
        "description": draw(st.text(max_size=80)),
        "status": draw(st.sampled_from(["ready", "human_verified", "draft"])),
    }
    # translations may be absent on some rows (Property 2 covers the annotation
    # detail; here we assert the embedded map is passed through unchanged).
    include_translations = draw(st.booleans())
    if include_translations:
        row["translations"] = draw(_translations_map)
    return row


@st.composite
def _partition(draw):
    """
    A restaurant partition: a set of unique-item_id dish rows plus at most one
    ``upload#`` sentinel row, returned in an arbitrary order (mirrors what a
    single Query yields).
    """
    menu_id = draw(_key_text)

    dishes = draw(st.lists(_dish_row(menu_id=menu_id), min_size=0, max_size=6))
    # Enforce unique item_ids within the partition (DynamoDB range-key uniqueness).
    seen = set()
    unique_dishes = []
    for d in dishes:
        if d["item_id"] in seen:
            continue
        seen.add(d["item_id"])
        unique_dishes.append(d)

    rows = list(unique_dishes)

    # At most one upload# sentinel row.
    include_sentinel = draw(st.booleans())
    if include_sentinel:
        upload_id = draw(_key_text)
        rows.append(
            {
                "menu_id": menu_id,
                "item_id": "upload#" + upload_id,
                "record_type": "upload_status",
                "status": draw(
                    st.sampled_from(
                        ["processing", "ocr_done", "analyzing", "translating", "ready"]
                    )
                ),
            }
        )

    # Shuffle so ordering is arbitrary (a Query gives no guaranteed order here).
    rows = draw(st.permutations(rows))
    return menu_id, unique_dishes, list(rows)


def _make_event(menu_id: str) -> dict:
    return {
        "routeKey": ROUTE_LIST_MENU,
        "pathParameters": {"restaurantId": menu_id},
    }


@contextmanager
def _mocked_partition(menu_id, partition_rows, query_calls):
    """Swap dynamo_service.list_items/get_item for stubs for the block's duration.

    Used instead of the function-scoped ``monkeypatch`` fixture so the mocks are
    installed and torn down for EACH Hypothesis-generated input (the fixture is
    not reset between generated inputs). No AWS is contacted.
    """

    def fake_list_items(mid):
        query_calls["count"] += 1
        assert mid == menu_id, "list_items called with the wrong menu_id"
        return list(partition_rows)

    def fail_get_item(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError(
            "get_item must not be called on the list path "
            "(zero per-translation / per-row queries)"
        )

    original_list_items = dynamo_service.list_items
    original_get_item = dynamo_service.get_item
    dynamo_service.list_items = fake_list_items
    dynamo_service.get_item = fail_get_item
    try:
        yield
    finally:
        dynamo_service.list_items = original_list_items
        dynamo_service.get_item = original_get_item


# --------------------------------------------------------------------- test
@settings(max_examples=200)
@given(data=_partition())
def test_read_returns_stored_dishes_sentinel_excluded_one_query(data):
    menu_id, expected_dishes, partition_rows = data

    # Count Query calls; a get_item call would be a forbidden per-row read.
    query_calls = {"count": 0}

    with _mocked_partition(menu_id, partition_rows, query_calls):
        resp = handler.handler(_make_event(menu_id))

        import json

        # Zero-row partition would be a 404, but every generated partition with >=1
        # dish OR a sentinel has >=1 row; a truly empty partition (no dishes, no
        # sentinel) is the one 404 case we tolerate and skip the collection check.
        if not partition_rows:
            assert resp["statusCode"] == 404
            assert query_calls["count"] == 1  # exactly one Query, still no get_item
            return

        assert resp["statusCode"] == 200, resp
        body = json.loads(resp["body"])
        items = body["items"]

        # --- Exactly one Query, zero per-translation/per-row reads (R1.1) ---
        assert query_calls["count"] == 1, "must reach DynamoDB via exactly one Query"

        # --- Sentinel excluded; collection equals the dish rows (R1.4) ---
        returned_item_ids = [it["item_id"] for it in items]
        expected_item_ids = [d["item_id"] for d in expected_dishes]
        assert not any(iid.startswith("upload#") for iid in returned_item_ids), (
            "upload# sentinel must be excluded from the dish collection"
        )
        assert sorted(returned_item_ids) == sorted(expected_item_ids), (
            "returned collection must equal exactly the stored dish rows"
        )

        # --- Each dish carries its embedded Translations_Map UNCHANGED (R1.2) ---
        expected_by_id = {d["item_id"]: d for d in expected_dishes}
        for it in items:
            original = expected_by_id[it["item_id"]]
            # The handler only *adds* a translations_status annotation; the embedded
            # translations map itself must be passed through byte-for-byte.
            assert it.get("translations") == original.get("translations"), (
                "embedded Translations_Map must be returned unchanged"
            )
            # Every other stored dish field is preserved verbatim as well.
            for key, value in original.items():
                assert it[key] == value, f"dish field {key!r} was altered"

        # --- Caller-independent: same input -> same collection (R1.5) ---
        query_calls["count"] = 0
        resp2 = handler.handler(_make_event(menu_id))
        assert resp2["statusCode"] == 200
        body2 = json.loads(resp2["body"])
        assert body2["items"] == items, "same input must yield the same collection"
        assert query_calls["count"] == 1


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
