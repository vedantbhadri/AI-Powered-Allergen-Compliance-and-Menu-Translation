# Feature: menu-read-edit-lambdas, Property 14: List restaurants returns exactly the unique menu_ids as objects, read-only
"""
Tests for readMenu's ``GET /restaurants`` endpoint (task 11.4, Requirement 6).

Covers:
  - 200 with each menu_id wrapped as ``{"menu_id": ...}`` for the set returned by a
    mocked ``dynamo_service.list_menus`` (R6.1), order + multiplicity preserved.
  - 200 with an empty ``restaurants`` list when ``list_menus`` returns ``[]`` (R6.2).
  - 500 ``{"error": "restaurants could not be retrieved"}`` when ``list_menus`` raises,
    with no partial list (R6.3).
  - ``get_item`` / ``list_items`` are NOT called for this route (R6.4 read-only, no
    per-restaurant reads).

Plus Property 14 as a Hypothesis property test.

DynamoDB is MOCKED: ``dynamo_service`` is swapped for an in-memory fake that records
which functions were called. No AWS is contacted. The property test installs/restores
the mock per generated input via a ``@contextmanager`` (NOT the function-scoped
``monkeypatch`` fixture) so it is safe to pair with ``@given`` — mirroring the pattern in
``test_property4_status_faithful.py``.
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


class _FakeDynamo:
    """Fake dynamo_service recording calls to each read function.

    ``list_menus`` returns canned data or raises when configured; ``get_item`` /
    ``list_items`` record calls so tests can assert they are never used by this route.
    No write methods exist, so readMenu stays read-only.
    """

    def __init__(self, *, menus=None, fail_menus=False):
        self._menus = list(menus) if menus is not None else []
        self._fail_menus = fail_menus
        self.list_menus_calls = 0
        self.get_item_calls = []
        self.list_items_calls = []

    def list_menus(self):
        self.list_menus_calls += 1
        if self._fail_menus:
            raise RuntimeError("injected list_menus read failure")
        return list(self._menus)

    def get_item(self, menu_id, item_id):
        self.get_item_calls.append({"menu_id": menu_id, "item_id": item_id})
        return None

    def list_items(self, menu_id):
        self.list_items_calls.append(menu_id)
        return []


def _restaurants_event():
    """Minimal API Gateway v2 event for the GET /restaurants route (no path params)."""
    return {"routeKey": handler.ROUTE_LIST_RESTAURANTS}


def _body(response):
    return json.loads(response["body"])


@contextmanager
def _mocked_dynamo(fake):
    """Swap handler.dynamo_service for ``fake`` for the block's duration, then restore.

    Used instead of the function-scoped ``monkeypatch`` fixture so the mock is installed
    and torn down for EACH Hypothesis-generated input. No AWS is contacted.
    """
    original = handler.dynamo_service
    handler.dynamo_service = fake
    try:
        yield fake
    finally:
        handler.dynamo_service = original


# --- Example / edge-case tests -----------------------------------------------

def test_list_restaurants_wraps_each_menu_id(monkeypatch):
    """A set of menu_ids is returned wrapped as {"menu_id": ...} objects (R6.1)."""
    menus = ["kiwi-cafe-queenstown", "otago-bistro", "wellington-diner"]
    fake = _FakeDynamo(menus=menus)
    monkeypatch.setattr(handler, "dynamo_service", fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert _body(response) == {
        "restaurants": [{"menu_id": m} for m in menus]
    }
    # A single list_menus Scan backs the route; no per-restaurant reads (R6.4).
    assert fake.list_menus_calls == 1
    assert fake.get_item_calls == []
    assert fake.list_items_calls == []


def test_list_restaurants_empty_table_returns_200_empty(monkeypatch):
    """An empty table (list_menus returns []) yields 200 with an empty list (R6.2)."""
    fake = _FakeDynamo(menus=[])
    monkeypatch.setattr(handler, "dynamo_service", fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    assert _body(response) == {"restaurants": []}
    assert fake.list_menus_calls == 1


def test_list_restaurants_read_failure_returns_500_no_partial(monkeypatch):
    """A raising list_menus yields 500 with no partial list (R6.3)."""
    fake = _FakeDynamo(fail_menus=True)
    monkeypatch.setattr(handler, "dynamo_service", fake)

    response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 500
    body = _body(response)
    assert body == {"error": "restaurants could not be retrieved"}
    assert "restaurants" not in body  # no partial list returned


def test_list_restaurants_does_not_call_get_item_or_list_items(monkeypatch):
    """The route uses only list_menus — never get_item / list_items (R6.4)."""
    fake = _FakeDynamo(menus=["a", "b"])
    monkeypatch.setattr(handler, "dynamo_service", fake)

    handler.handler(_restaurants_event())

    assert fake.get_item_calls == []
    assert fake.list_items_calls == []


# --- Property 14 -------------------------------------------------------------

# menu_id strings resemble restaurant slugs / upload ids: the handler applies no
# validation on this route, so any non-empty string is a valid stored id.
_MENU_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:#-"
menu_id_lists = st.lists(
    st.text(alphabet=_MENU_ID_CHARS, min_size=1, max_size=40),
    min_size=0,
    max_size=25,
)


@settings(max_examples=200)
@given(menus=menu_id_lists)
def test_property14_list_restaurants_wraps_exactly(menus):
    """Property 14: the response contains exactly one {"menu_id": id} object per id
    returned by list_menus, in the same order and multiplicity, empty stays empty, and
    no write op is issued (get_item/list_items untouched)."""
    fake = _FakeDynamo(menus=menus)
    with _mocked_dynamo(fake):
        response = handler.handler(_restaurants_event())

    assert response["statusCode"] == 200
    body = _body(response)
    # Exactly the returned ids, wrapped, order + multiplicity preserved.
    assert body == {"restaurants": [{"menu_id": m} for m in menus]}

    # Read-only: exactly one Scan, no per-restaurant reads (R6.4).
    assert fake.list_menus_calls == 1
    assert fake.get_item_calls == []
    assert fake.list_items_calls == []
