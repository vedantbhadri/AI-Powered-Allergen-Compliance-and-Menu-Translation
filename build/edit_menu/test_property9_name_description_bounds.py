# Feature: menu-read-edit-lambdas, Property 9: name and description are accepted iff within their length bounds
"""
Property-based test for editMenu's ``PATCH /menus/{menuId}/items/{itemId}``
name / description length validation.

Property 9 (design.md / tasks.md 6.4):
    For any ``name``, editMenu applies it (HTTP 200, write attempted with the
    value) iff its length is within 1-200 characters inclusive, else HTTP 400
    with no write; for any ``description``, editMenu applies it (HTTP 200, write
    attempted with the value) iff its length is within 0-2000 characters
    inclusive, else HTTP 400 with no write.

    Validates: Requirements 4.4, 4.5

DynamoDB is MOCKED: ``handler._table`` (the cached boto3 Table) is replaced with
a fake whose ``update_item`` records the kwargs it was called with and returns a
success shape (``{"Attributes": {...}}``) so an in-bounds correction reaches a
200 without contacting AWS. Out-of-bounds inputs are rejected before any write,
so the fake's ``update_item`` must never be called for them. Minimum 100
iterations across boundary and out-of-bounds lengths for both fields.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# --- Import wiring: put build/layer/python (for ``services``) and build/edit_menu
#     (for ``handler``) on sys.path, matching the Lambda runtime layout. ---
_EDIT_MENU_DIR = Path(__file__).resolve().parent          # build/edit_menu
_BUILD_DIR = _EDIT_MENU_DIR.parent                        # build
_LAYER_PYTHON_DIR = _BUILD_DIR / "layer" / "python"       # build/layer/python

for _p in (str(_LAYER_PYTHON_DIR), str(_EDIT_MENU_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import handler  # noqa: E402  (build/edit_menu/handler.py)

NAME_MIN, NAME_MAX = handler.NAME_MIN_LEN, handler.NAME_MAX_LEN            # 1, 200
DESC_MIN, DESC_MAX = handler.DESCRIPTION_MIN_LEN, handler.DESCRIPTION_MAX_LEN  # 0, 2000


def _make_event(field: str, value: str) -> dict:
    """Minimal API Gateway v2 PATCH event carrying a single correctable field."""
    return {
        "routeKey": "PATCH /menus/{menuId}/items/{itemId}",
        "pathParameters": {"menuId": "dish-menu-1", "itemId": "dish-1"},
        "body": json.dumps({field: value}),
        "isBase64Encoded": False,
    }


class _FakeTable:
    """Fake DynamoDB Table: records update_item kwargs, returns a success shape.

    The dish is treated as existing, so ``update_item`` returns
    ``{"Attributes": {...}}`` (never raising ConditionalCheckFailed), driving the
    handler to a 200 for an in-bounds correction. Every call is recorded so the
    test can assert whether a write was attempted and with what value.
    """

    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        # Echo back the applied values as the ALL_NEW attributes would.
        attributes = dict(kwargs.get("ExpressionAttributeValues") or {})
        return {"Attributes": {"applied": attributes}}


@contextmanager
def _mocked_table():
    """Swap handler._table for a fake returning our _FakeTable, per generated input.

    Used instead of the pytest ``monkeypatch`` fixture so the mock is installed
    and torn down for EACH Hypothesis-generated input. No AWS is contacted.
    """
    fake = _FakeTable()
    original = handler._table
    handler._table = lambda: fake
    try:
        yield fake
    finally:
        handler._table = original


# --- Length strategies -------------------------------------------------------
# In-bounds lengths bias toward the boundaries (min, min+1, max-1, max) plus a
# spread across the interior; out-of-bounds lengths sit just past each edge and
# further out. Strings are built at an exact target length so the boundary
# behaviour is exercised precisely.

name_in_bounds_len = st.one_of(
    st.sampled_from([NAME_MIN, NAME_MIN + 1, NAME_MAX - 1, NAME_MAX]),
    st.integers(min_value=NAME_MIN, max_value=NAME_MAX),
)
# name is invalid when empty (0) or longer than 200.
name_out_of_bounds_len = st.one_of(
    st.just(NAME_MIN - 1),  # 0 -> empty string, invalid
    st.integers(min_value=NAME_MAX + 1, max_value=NAME_MAX + 50),
)

desc_in_bounds_len = st.one_of(
    st.sampled_from([DESC_MIN, DESC_MIN + 1, DESC_MAX - 1, DESC_MAX]),
    st.integers(min_value=DESC_MIN, max_value=DESC_MAX),
)
# description is invalid only when longer than 2000 (0 is allowed).
desc_out_of_bounds_len = st.integers(min_value=DESC_MAX + 1, max_value=DESC_MAX + 50)


def _string_of_len(n: int) -> str:
    return "x" * n


@settings(max_examples=200)
@given(length=name_in_bounds_len)
def test_name_within_bounds_is_applied(length):
    """name of length 1-200 inclusive -> 200 and a single write carrying the value."""
    value = _string_of_len(length)
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("name", value))

    assert resp["statusCode"] == 200
    # Exactly one write attempted, carrying the provided name value (R4.4).
    assert len(fake.calls) == 1
    assert fake.calls[0]["ExpressionAttributeValues"][":name"] == value


@settings(max_examples=200)
@given(length=name_out_of_bounds_len)
def test_name_out_of_bounds_is_rejected_with_no_write(length):
    """name outside 1-200 -> 400 and no write attempted (R4.4)."""
    value = _string_of_len(length)
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("name", value))

    assert resp["statusCode"] == 400
    assert len(fake.calls) == 0
    assert "error" in json.loads(resp["body"])


@settings(max_examples=200)
@given(length=desc_in_bounds_len)
def test_description_within_bounds_is_applied(length):
    """description of length 0-2000 inclusive -> 200 and a single write with the value."""
    value = _string_of_len(length)
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("description", value))

    assert resp["statusCode"] == 200
    # Exactly one write attempted, carrying the provided description value (R4.5).
    assert len(fake.calls) == 1
    assert fake.calls[0]["ExpressionAttributeValues"][":description"] == value


@settings(max_examples=200)
@given(length=desc_out_of_bounds_len)
def test_description_out_of_bounds_is_rejected_with_no_write(length):
    """description outside 0-2000 -> 400 and no write attempted (R4.5)."""
    value = _string_of_len(length)
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("description", value))

    assert resp["statusCode"] == 400
    assert len(fake.calls) == 0
    assert "error" in json.loads(resp["body"])
