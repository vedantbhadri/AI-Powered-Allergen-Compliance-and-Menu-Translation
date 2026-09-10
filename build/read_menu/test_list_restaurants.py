# Feature: menu-read-edit-lambdas, Property 14: List restaurants returns exactly the registry rows as {menu_id,name} objects, read-only
"""
Tests for readMenu's ``GET /restaurants`` endpoint (Requirement 6).

The route reads dedicated **restaurant registry rows** — the source of truth for
restaurant existence, independent of dish rows — via a direct ``boto3`` DynamoDB
Scan filtered to ``record_type == "restaurant"``. A registry row is shaped
``{"menu_id": <id>, "item_id": "restaurant#<id>", "record_type": "restaurant",
"name": <display name>}``.

Covers:
  - 200 with each registry row projected to ``{"menu_id", "name"}``, sorted by
    ``menu_id`` (R6.1).
  - 200 with an empty ``restaurants`` list when no registry rows exist (R6.2).
  - 500 ``{"error": "restaurants could not be retrieved"}`` when the Scan raises,
    with no partial list (R6.3).
  - Only a Scan is issued — no get_item / query / put / update / delete — so the
    route is strictly read-only (R6.4).
  - Scan pagination over ``LastEvaluatedKey`` is handled (multi-page fake).
  - A restaurant whose registry row exists but which has ZERO dish rows still
    appears in the listing (the key regression this change fixes).

Plus Property 14 as a Hypothesis property test.

DynamoDB is MOCKED: ``handler._table`` is swapped for a fake table whose ``.scan``
returns registry rows. No AWS is contacted. The property test installs/restores the
fake per generated input via a ``@contextmanager`` (NOT the function-scoped
``monkeypatch`` fixture) so it is safe to pair with ``@given`` — mirroring the pattern
in ``build/edit_menu/test_property11_status_human_verified.py``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Import wiring: put build/layer/python (for ``services``) and build/read_menu on
#     sys.path, mirroring the Lambda runtime layout. Both handler dirs expose a module
#     named ``handler``, so load THIS package's handler from its explicit file path under
#     a unique module name to avoid a cross-directory collision. ---
_HERE = Path(__file__).resolve().parent            # build/read_menu
_BUILD = _HERE.parent                              # build
_LAYER_PYTHON = _BUILD / "layer" / "python"        # build/layer/python

for _p in (str(_LAYER_PYTHON), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location("read_menu_handler", _HERE / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)


def _registry_row(menu_id, name=None):
    """Build a canonical restaurant registry row (optionally without a ``name``)."""
    row = {
        "menu_id": menu_id,
        "item_id": "restaurant#" + menu_id,
        "record_type": "restaurant",
    }
    if name is not None:
        row["name"] = name
    return row


class _FakeTable:
    """Fake DynamoDB Table backing the registry Scan.

    ``pages`` is a list of ``Items`` lists; ``.scan`` returns them one at a time,
    setting ``LastEvaluatedKey`` on all but the last so pagination is exercised. When
    ``fail`` is set the Scan raises, simulating a read failure. Every ``.scan`` call is
    recorded, and calling any other DynamoDB verb records it too so tests can assert the
    route is read-only.
    """

    # Verbs that must NEVER be used by this read-only route.
    _FORBIDDEN = (
        "get_item",
        "query",
        "put_item",
        "update_item",
        "delete_item",
        "batch_writer",
    )

    def __init__(self, *, pages=None, fail=False):
        # Default to a single empty page when nothing is provided.
        self._pages = list(pages) if pages is not None else [[]]
        self._fail = fail
        self.scan_calls = []
        self.forbidden_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        if self._fail:
            raise RuntimeError("injected Scan read failure")
        # Which page to return is driven by how many scans have run so far.
        index = len(self.scan_calls) - 1
        if index >= len(self._pages):
            return {"Items": []}
        items = self._pages[index]
        result = {"Items": list(items)}
        if index < len(self._pages) - 1:
            # Signal there is another page to fetch.
            result["LastEvaluatedKey"] = {"menu_id": f"__page_{index}__"}
        return result

    def __getattr__(self, name):
        # Any non-scan DynamoDB verb access is a read-only violation; record and raise.
        if name in self._FORBIDDEN:
            def _forbidden(*args, **kwargs):
                self.forbidden_calls.append(name)
                raise AssertionError(f"read-only route issued {name}")
            return _forbidden
        raise AttributeError(name)


@contextmanager
def _install_table(fake):
    """Swap handler._table for a callable returning ``fake`` and reset the cache.

    Restores the original ``_table`` and ``_TABLE`` afterwards. Used instead of the
    function-scoped ``monkeypatch`` fixture so it pairs safely with ``@given``.
    """
    original_table = handler._table
    original_cache = handler._TABLE
    handler._TABLE = None
    handler._table = lambda: fake
    try:
        yield fake
    finally:
        handler._table = original_table
        handler._TABLE = original_cache


def _restaurants_event():
    """Minimal API Gateway v2 event for the GET /restaurants route (no path params)."""
    return {"routeKey": handler.ROUTE_LIST_RESTAURANTS}


def _body(response):
    return json.loads(response["body"])


# --- Example / edge-case tests -----------------------------------------------

def test_list_restaurants_projects_registry_rows_sorted(monkeypatch):
    """Registry rows -> 200 with {menu_id,name} objects, sorted by menu_id (R6.1)."""
    fake = _FakeTable(pages=[[
        _registry_row("wellington-diner", "Wellington Diner"),
        _registry_row("kiwi-cafe-queenstown", "Kiwi Cafe"),
        _registry_row("otago-bistro", "Otago Bistro"),
    ]])
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert _body(response) == {
        "restaurants": [
            {"menu_id": "kiwi-cafe-queenstown", "name": "Kiwi Cafe"},
            {"menu_id": "otago-bistro", "name": "Otago Bistro"},
            {"menu_id": "wellington-diner", "name": "Wellington Diner"},
        ]
    }
    # Exactly one Scan page was read; no forbidden verbs (R6.4).
    assert len(fake.scan_calls) == 1
    assert fake.forbidden_calls == []


def test_list_restaurants_name_falls_back_to_menu_id(monkeypatch):
    """A registry row without a ``name`` falls back to its menu_id as the label."""
    fake = _FakeTable(pages=[[_registry_row("nameless-cafe")]])
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert _body(response) == {
        "restaurants": [{"menu_id": "nameless-cafe", "name": "nameless-cafe"}]
    }


def test_list_restaurants_paginates_over_last_evaluated_key(monkeypatch):
    """Two Scan pages joined via LastEvaluatedKey are both included (R6.1)."""
    fake = _FakeTable(pages=[
        [_registry_row("alpha-cafe", "Alpha Cafe")],
        [_registry_row("beta-bistro", "Beta Bistro")],
    ])
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert _body(response) == {
        "restaurants": [
            {"menu_id": "alpha-cafe", "name": "Alpha Cafe"},
            {"menu_id": "beta-bistro", "name": "Beta Bistro"},
        ]
    }
    # Two scans: the first returned LastEvaluatedKey, the second finished the page walk.
    assert len(fake.scan_calls) == 2
    assert "ExclusiveStartKey" in fake.scan_calls[1]


def test_list_restaurants_empty_returns_200_empty(monkeypatch):
    """No registry rows -> 200 with an empty list (R6.2)."""
    fake = _FakeTable(pages=[[]])
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert _body(response) == {"restaurants": []}
    assert len(fake.scan_calls) == 1


def test_list_restaurants_scan_failure_returns_500_no_partial(monkeypatch):
    """A raising Scan yields 500 with no partial list (R6.3)."""
    fake = _FakeTable(fail=True)
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 500
    body = _body(response)
    assert body == {"error": "restaurants could not be retrieved"}
    assert "restaurants" not in body  # no partial list returned


def test_list_restaurants_is_read_only(monkeypatch):
    """The route issues only a Scan — no get_item / query / write verbs (R6.4)."""
    fake = _FakeTable(pages=[[_registry_row("a", "A"), _registry_row("b", "B")]])
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    handler.handler(_restaurants_event())

    assert fake.forbidden_calls == []
    assert len(fake.scan_calls) == 1


def test_restaurant_with_zero_dishes_still_listed(monkeypatch):
    """A restaurant with a registry row but ZERO dish rows still appears (regression).

    The fake Scan returns exactly one registry row and no dish rows; the restaurant
    must still show up in GET /restaurants because existence is sourced from the
    registry, not from dish rows.
    """
    fake = _FakeTable(pages=[[
        {
            "menu_id": "empty-cafe",
            "item_id": "restaurant#empty-cafe",
            "record_type": "restaurant",
            "name": "Empty Cafe",
        }
    ]])
    monkeypatch.setattr(handler, "_TABLE", None, raising=False)
    monkeypatch.setattr(handler, "_table", lambda: fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert {"menu_id": "empty-cafe", "name": "Empty Cafe"} in _body(response)["restaurants"]


# --- Property 14 -------------------------------------------------------------

# menu_id strings resemble restaurant slugs: the handler applies no validation on this
# route, so any non-empty string is a valid stored id. Keep them unique within a set so
# the sorted projection is unambiguous.
_MENU_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:#-"
_menu_id = st.text(alphabet=_MENU_ID_CHARS, min_size=1, max_size=40)

# A registry row: a menu_id plus an OPTIONAL name (None means the row omits ``name``).
registry_rows = st.lists(
    st.tuples(_menu_id, st.one_of(st.none(), st.text(max_size=40))),
    min_size=0,
    max_size=25,
    unique_by=lambda pair: pair[0],
)


@settings(max_examples=200)
@given(rows=registry_rows)
def test_property14_list_restaurants_projects_registry_rows(rows):
    """Property 14: the response contains exactly one {"menu_id","name"} object per
    registry row (sorted by menu_id), name falling back to menu_id when absent, empty
    stays empty, and the route is read-only (only a Scan is issued)."""
    fake_rows = [_registry_row(mid, name) for mid, name in rows]
    fake = _FakeTable(pages=[fake_rows])

    with _install_table(fake):
        response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    body = _body(response)

    expected = sorted(
        ({"menu_id": mid, "name": (name or mid)} for mid, name in rows),
        key=lambda r: r["menu_id"],
    )
    assert body == {"restaurants": expected}

    # Read-only: exactly one Scan page here, and no forbidden verbs (R6.4).
    assert len(fake.scan_calls) == 1
    assert fake.forbidden_calls == []
