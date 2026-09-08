# Feature: menu-read-edit-lambdas, Property 7: Derived tags equal the real allergen_rules outputs
"""
Property-based test for editMenu's allergen tag recomputation.

Property 7 (design.md / tasks.md 6.2):
    For any filtered confirmed allergen set, the ``display_tags`` and ``diet_tags``
    editMenu persists equal ``allergen_rules.to_display_tags(confirmed)`` and
    ``allergen_rules.derive_diet_tags(confirmed)`` respectively — the handler reuses
    those real functions rather than reimplementing the logic.

    Validates: Requirements 4.2, 4.6

Strategy: generate arbitrary ``confirmed_allergens`` lists mixing real PEAL members
with non-members, drive them through the handler's ``recompute_allergen_fields`` AND
through the captured ``UpdateItem`` kwargs the handler would persist (via a mocked
DynamoDB table), then assert both the recomputed fields and the persisted values match
the REAL ``allergen_rules`` outputs computed on the SAME PEAL-filtered confirmed set.
No reimplementation of the tag logic is done here — we call the real module directly and
compare. DynamoDB is MOCKED (an in-memory fake ``update_item``); no AWS is contacted.
Minimum 100 iterations.
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


# Proposed confirmed allergens: mix real PEAL members with arbitrary non-members so
# the PEAL filter has something to discard on most inputs.
_NON_MEMBERS = ["", "Gluten", "peanuts", "Shellfish", "Wheat", "Nuts", "xyz", "123"]
allergen_tokens = st.one_of(
    st.sampled_from(allergen_rules.PEAL_CATEGORIES),
    st.sampled_from(_NON_MEMBERS),
    st.text(max_size=20),
)
confirmed_lists = st.lists(allergen_tokens, min_size=0, max_size=15)


class _UpdateItemSpy:
    """Fake DynamoDB Table capturing the update_item kwargs and echoing ALL_NEW.

    Builds a synthetic post-update row from the SET-clause values so the handler's
    200 response reflects exactly what it asked DynamoDB to persist. No AWS.
    """

    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        values = kwargs.get("ExpressionAttributeValues", {})
        # Reconstruct the persisted allergen/diet fields from the update values.
        attributes = {
            "allergens": {
                "confirmed": values.get(":confirmed"),
                "display_tags": values.get(":display_tags"),
            },
            "diet_tags": values.get(":diet_tags"),
        }
        return {"Attributes": attributes}


@contextmanager
def _mocked_table():
    """Swap handler._table() for a recording fake for the block's duration.

    Installed/torn down per generated input (not via the function-scoped monkeypatch
    fixture, which Hypothesis flags with a health check). No AWS is contacted.
    """
    spy = _UpdateItemSpy()
    original = handler._table
    handler._table = lambda: spy
    try:
        yield spy
    finally:
        handler._table = original


def _make_event(menu_id, item_id, confirmed_allergens) -> dict:
    """Minimal API Gateway v2 PATCH event carrying a confirmed_allergens correction."""
    return {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": json.dumps({"confirmed_allergens": confirmed_allergens}),
    }


@settings(max_examples=200)
@given(proposed=confirmed_lists)
def test_recompute_matches_real_allergen_rules(proposed):
    """recompute_allergen_fields returns tags identical to the REAL allergen_rules
    outputs computed on the same PEAL-filtered confirmed set (R4.2, R4.6)."""
    fields = handler.recompute_allergen_fields(proposed)
    confirmed = fields["confirmed"]

    # No reimplementation: compare against the real module on the SAME confirmed set.
    assert fields["display_tags"] == allergen_rules.to_display_tags(confirmed)
    assert fields["diet_tags"] == allergen_rules.derive_diet_tags(confirmed)


@settings(max_examples=200)
@given(proposed=confirmed_lists)
def test_persisted_tags_equal_real_allergen_rules(proposed):
    """The display_tags / diet_tags editMenu PERSISTS (captured UpdateItem kwargs and
    echoed in the 200 body) equal the real allergen_rules outputs on the filtered set."""
    with _mocked_table() as spy:
        resp = handler.handler(_make_event("menu-1", "dish-1", proposed))

    assert resp["statusCode"] == 200
    assert len(spy.calls) == 1

    # The confirmed set the handler applied = proposed intersected with PEAL_CATEGORIES.
    confirmed = handler.filter_confirmed_allergens(proposed)
    expected_display = allergen_rules.to_display_tags(confirmed)
    expected_diet = allergen_rules.derive_diet_tags(confirmed)

    # 1) The values written to DynamoDB match the real allergen_rules outputs.
    written = spy.calls[0]["ExpressionAttributeValues"]
    assert written[":confirmed"] == confirmed
    assert written[":display_tags"] == expected_display
    assert written[":diet_tags"] == expected_diet

    # 2) The 200 body echoes those same persisted values.
    body = json.loads(resp["body"])
    item = body["item"]
    assert item["allergens"]["display_tags"] == expected_display
    assert item["diet_tags"] == expected_diet
