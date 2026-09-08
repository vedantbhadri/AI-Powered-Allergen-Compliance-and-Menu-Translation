# Feature: menu-read-edit-lambdas, Property 6: Correction retains only PEAL categories
"""
Property-based test for editMenu's allergen filtering.

Property 6 (design.md / tasks.md 6.1):
    For any proposed ``confirmed_allergens`` list mixing members and non-members
    of ``allergen_rules.PEAL_CATEGORIES``, the confirmed set editMenu applies
    equals the input intersected with ``PEAL_CATEGORIES`` (order-preserving, no
    non-member survives, no member is invented).

    Validates: Requirements 4.1

This is checked two ways:

1. At the unit boundary ``handler.filter_confirmed_allergens`` /
   ``handler.recompute_allergen_fields`` (the functions that implement R4.1).
2. Through the full handler path (``handler.handler``) with a fake DynamoDB
   ``Table`` capturing the exact ``UpdateItem`` kwargs, asserting the value bound
   to ``allergens.confirmed`` (``:confirmed``) is the input intersected with
   ``PEAL_CATEGORIES``.

DynamoDB is MOCKED end-to-end: ``handler._table`` is swapped for a fake whose
``update_item`` records its kwargs and echoes an ``ALL_NEW`` row. No AWS is ever
contacted. Minimum 100 iterations.
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


# The canonical PEAL members and a pool of clearly-non-member values.
_PEAL = list(allergen_rules.PEAL_CATEGORIES)
_NON_MEMBERS = [
    "Kryptonite",
    "Gluten",              # near-miss: not the canonical "Gluten (Cereals)"
    "egg",                 # wrong casing -> not a member
    "MILK",
    "Nuts",
    "",
    "Shellfish",
    "peanuts",
    "Water",
    "123",
]

# A single proposed allergen: either a real PEAL member or an obvious non-member.
proposed_allergen = st.one_of(st.sampled_from(_PEAL), st.sampled_from(_NON_MEMBERS))

# A proposed list mixing members and non-members in arbitrary order (may repeat).
proposed_lists = st.lists(proposed_allergen, min_size=0, max_size=25)


def _expected_confirmed(proposed):
    """The R4.1 spec: input intersected with PEAL, request order preserved, deduped."""
    peal = allergen_rules.PEAL_CATEGORIES
    expected = []
    for value in proposed:
        if value in peal and value not in expected:
            expected.append(value)
    return expected


class _FakeTable:
    """Fake DynamoDB Table capturing update_item kwargs and echoing ALL_NEW."""

    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        # Echo the corrected confirmed set back under the ALL_NEW shape editMenu
        # returns; the exact contents don't matter for this property.
        confirmed = kwargs["ExpressionAttributeValues"].get(":confirmed")
        return {"Attributes": {"allergens": {"confirmed": confirmed}}}


@contextmanager
def _mocked_table():
    """Swap handler._table for a fake capturing table for the block's duration.

    Used instead of the function-scoped ``monkeypatch`` fixture so the fake is
    installed and torn down for EACH Hypothesis-generated input. No AWS contacted.
    """
    fake = _FakeTable()
    original = handler._table
    handler._table = lambda: fake
    try:
        yield fake
    finally:
        handler._table = original


def _make_event(menu_id, item_id, proposed):
    """Minimal API Gateway v2 event carrying a confirmed_allergens correction."""
    return {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": json.dumps({"confirmed_allergens": proposed}),
    }


@settings(max_examples=200)
@given(proposed=proposed_lists)
def test_filter_confirmed_allergens_is_peal_intersection(proposed):
    """handler.filter_confirmed_allergens keeps exactly the PEAL members (R4.1)."""
    confirmed = handler.filter_confirmed_allergens(proposed)

    expected = _expected_confirmed(proposed)
    assert confirmed == expected
    # No non-member ever survives.
    assert all(c in allergen_rules.PEAL_CATEGORIES for c in confirmed)
    # Every kept value came from the input.
    assert all(c in proposed for c in confirmed)


@settings(max_examples=200)
@given(proposed=proposed_lists)
def test_recompute_confirmed_is_peal_intersection(proposed):
    """recompute_allergen_fields' confirmed field is the PEAL intersection (R4.1)."""
    fields = handler.recompute_allergen_fields(proposed)
    assert fields["confirmed"] == _expected_confirmed(proposed)


@settings(max_examples=200)
@given(proposed=proposed_lists)
def test_handler_persists_only_peal_confirmed(proposed):
    """Full handler path: the value written to allergens.confirmed is the PEAL
    intersection of the proposed list (R4.1)."""
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("menu-1", "dish-1", proposed))

    assert resp["statusCode"] == 200
    # Exactly one UpdateItem write was issued.
    assert len(fake.calls) == 1

    applied = fake.calls[0]["ExpressionAttributeValues"][":confirmed"]
    assert applied == _expected_confirmed(proposed)
    # No non-member leaked into the persisted confirmed set.
    assert all(c in allergen_rules.PEAL_CATEGORIES for c in applied)
