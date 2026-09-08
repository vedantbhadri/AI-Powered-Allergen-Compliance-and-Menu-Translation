# Feature: menu-read-edit-lambdas, Property 13: Correction on an absent dish fails closed with no change
"""
Property-based test for editMenu's fail-closed behaviour on an absent dish.

Property 13 (design.md / tasks.md 6.8):
    For any PATCH correction (with varied valid fields) that targets a
    non-existent ``menu_id``/``item_id``, the guarded conditional ``UpdateItem``
    fails its ``attribute_exists(menu_id)`` condition. editMenu maps that failure
    to HTTP 404 ``{"error": "dish not found"}`` and makes NO change to stored data
    (the failed conditional write is itself the no-op).

    Validates: Requirements 5.4

DynamoDB is MOCKED end-to-end: ``handler._table`` is swapped for a fake whose
``update_item`` unconditionally raises a ``ConditionalCheckFailedException`` —
simulating the guard rejecting a write against a partition/row that does not
exist. The handler detects this via ``handler._is_conditional_check_failed``,
which accepts either a botocore ``ClientError`` carrying
``response["Error"]["Code"] == "ConditionalCheckFailedException"`` OR any
exception whose class name is ``ConditionalCheckFailedException`` (used here).

Because the fake raises on every ``update_item`` call, the "no stored data is
changed" clause is exercised directly: the failed conditional write leaves the
fake's simulated store untouched. We also assert that a single ``update_item``
attempt was made and that it was guarded by ``attribute_exists(menu_id)`` — i.e.
the write only failed closed *because* of the condition, never through an
unconditional put. No AWS is ever contacted. Minimum 100 iterations.
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
from services import allergen_rules  # noqa: E402  (shared layer)


# The exception a real DynamoDB conditional-write rejection surfaces. The handler
# detects it by class name (``_is_conditional_check_failed``), so a bare subclass
# named exactly ``ConditionalCheckFailedException`` reproduces the failure path.
class ConditionalCheckFailedException(Exception):
    """Simulates DynamoDB rejecting a write whose ConditionExpression failed."""


_PEAL = list(allergen_rules.PEAL_CATEGORIES)
_SUPPORTED_CODES = sorted(handler.SUPPORTED_LANGUAGE_CODES)  # ["de", "es", "ja", "zh"]

# --- Strategies for VALID corrections spanning the four correctable fields. ---
# Every generated correction is well-formed (would pass validate_correction); the
# 404 arises solely from the absent dish, not from a validation rejection.

_confirmed_allergens = st.lists(st.sampled_from(_PEAL), min_size=0, max_size=8)
_translations = st.dictionaries(
    keys=st.sampled_from(_SUPPORTED_CODES),
    values=st.text(max_size=40),
    max_size=len(_SUPPORTED_CODES),
)
_name = st.text(min_size=1, max_size=200)
_description = st.text(min_size=0, max_size=2000)


@st.composite
def _valid_corrections(draw):
    """A non-empty, valid correction carrying an arbitrary subset of the fields."""
    field_pool = {
        "confirmed_allergens": _confirmed_allergens,
        "translations": _translations,
        "name": _name,
        "description": _description,
    }
    # Choose at least one field so the correction is never empty (avoids the 400
    # "no correctable field provided" path — we want to reach the UpdateItem).
    chosen = draw(
        st.lists(
            st.sampled_from(list(field_pool)),
            min_size=1,
            max_size=len(field_pool),
            unique=True,
        )
    )
    return {field: draw(field_pool[field]) for field in chosen}


# Non-existent target keys: arbitrary (but well-formed) menu_id / item_id values.
_key_text = st.text(min_size=1, max_size=30)


class _AbsentDishTable:
    """Fake DynamoDB Table that always fails its conditional UpdateItem.

    Simulates an absent ``menu_id``/``item_id``: the guarded
    ``attribute_exists(menu_id)`` condition can never hold, so every
    ``update_item`` raises ``ConditionalCheckFailedException`` and the (empty)
    simulated store is never mutated.
    """

    def __init__(self):
        self.calls = []
        # A stand-in for "stored data": empty, and it must stay empty because the
        # conditional write fails before any mutation. We snapshot it to assert
        # no change occurred.
        self.store = {}

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        raise ConditionalCheckFailedException(
            "The conditional request failed"
        )


@contextmanager
def _mocked_absent_table():
    """Swap handler._table for a fake that fails closed, per generated input.

    Installed/torn down for EACH Hypothesis example (instead of a function-scoped
    fixture) so the fake is fresh every iteration and no AWS is contacted.
    """
    fake = _AbsentDishTable()
    original = handler._table
    handler._table = lambda: fake
    try:
        yield fake
    finally:
        handler._table = original


def _make_event(menu_id, item_id, correction):
    """Minimal API Gateway v2 PATCH event carrying a valid correction body."""
    return {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": json.dumps(correction),
    }


@settings(max_examples=200)
@given(menu_id=_key_text, item_id=_key_text, correction=_valid_corrections())
def test_absent_dish_correction_fails_closed(menu_id, item_id, correction):
    """A valid correction on a non-existent dish returns 404 and changes nothing (R5.4)."""
    with _mocked_absent_table() as fake:
        store_before = dict(fake.store)
        resp = handler.handler(_make_event(menu_id, item_id, correction))
        store_after = dict(fake.store)

    # --- editMenu responds 404 with the standard "dish not found" body (R5.4). ---
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"]) == {"error": "dish not found"}

    # --- The failure came from a guarded conditional UpdateItem, not an
    #     unconditional write: exactly one attempt, targeting the requested key,
    #     guarded by attribute_exists(menu_id). ---
    assert len(fake.calls) == 1
    attempt = fake.calls[0]
    assert attempt["ConditionExpression"] == "attribute_exists(menu_id)"
    assert attempt["Key"] == {"menu_id": menu_id, "item_id": item_id}

    # --- No stored data was changed: the failed conditional write is the no-op. ---
    assert store_after == store_before == {}


@settings(max_examples=200)
@given(menu_id=_key_text, item_id=_key_text, correction=_valid_corrections())
def test_conditional_failure_is_recognised(menu_id, item_id, correction):
    """The raised ConditionalCheckFailedException is recognised as fail-closed (R5.4)."""
    exc = ConditionalCheckFailedException("boom")
    # The handler must classify this class-name-based failure as the 404 path.
    assert handler._is_conditional_check_failed(exc) is True
