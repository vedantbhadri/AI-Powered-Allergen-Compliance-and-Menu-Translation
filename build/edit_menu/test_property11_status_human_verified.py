# Feature: menu-read-edit-lambdas, Property 11: Successful correction sets status to human_verified
"""
Property-based test for editMenu's status stamping.

Property 11 (design.md / tasks.md 6.6):
    For any valid correction applied to an existing dish, regardless of which
    optional fields (``confirmed_allergens`` / ``translations`` / ``name`` /
    ``description``) the request includes, the resulting item's ``status`` is
    ``human_verified``.

    Validates: Requirements 5.1

We generate valid corrections carrying a random *non-empty* subset of the four
correctable fields (a non-empty subset guarantees the correction is accepted
rather than rejected 400 for being empty). Each generated field is constructed
to be individually valid:

- ``confirmed_allergens`` — a list of PEAL members and/or arbitrary non-members
  (filtering is orthogonal to the status stamp);
- ``translations`` — a non-empty map whose keys are drawn only from the supported
  codes ``{es, de, ja, zh}`` (any unsupported code would trigger a 400);
- ``name`` — a string of length 1-200 inclusive;
- ``description`` — a string of length 0-2000 inclusive.

The full handler path runs against a fake DynamoDB ``Table`` whose ``update_item``
records its kwargs and echoes an ``ALL_NEW`` row. We assert the captured
``UpdateItem`` binds ``:status`` to ``handler.HUMAN_VERIFIED_STATUS`` and that the
echoed item's ``status`` is ``human_verified``. DynamoDB is MOCKED end-to-end; no
AWS is ever contacted. Minimum 100 iterations.
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


_PEAL = list(allergen_rules.PEAL_CATEGORIES)
_SUPPORTED_CODES = sorted(handler.SUPPORTED_LANGUAGE_CODES)  # {es, de, ja, zh}


class _FakeTable:
    """Fake DynamoDB Table capturing update_item kwargs and echoing ALL_NEW.

    The echoed row reflects the ``:status`` value the handler bound, so both the
    captured request AND the returned item can be checked against human_verified.
    """

    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        status = kwargs["ExpressionAttributeValues"].get(":status")
        return {"Attributes": {"status": status}}


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


# --- Strategies for each individually-valid correctable field. ---

# confirmed_allergens: a list mixing real PEAL members and arbitrary non-members.
_confirmed_allergens = st.lists(
    st.one_of(st.sampled_from(_PEAL), st.text(max_size=12)),
    max_size=15,
)

# translations: a non-empty map whose keys are only supported codes.
_translations = st.dictionaries(
    keys=st.sampled_from(_SUPPORTED_CODES),
    values=st.text(max_size=40),
    min_size=1,
    max_size=len(_SUPPORTED_CODES),
)

# name: valid length 1-200 inclusive.
_name = st.text(min_size=1, max_size=200)

# description: valid length 0-2000 inclusive.
_description = st.text(min_size=0, max_size=2000)


@st.composite
def valid_corrections(draw):
    """A correction carrying a random NON-EMPTY subset of the four valid fields."""
    field_strategies = {
        "confirmed_allergens": _confirmed_allergens,
        "translations": _translations,
        "name": _name,
        "description": _description,
    }
    # Choose a non-empty subset of field names to include.
    present = draw(
        st.lists(
            st.sampled_from(list(field_strategies)),
            min_size=1,
            max_size=len(field_strategies),
            unique=True,
        )
    )
    return {field: draw(field_strategies[field]) for field in present}


def _make_event(menu_id, item_id, correction):
    """Minimal API Gateway v2 event carrying a correction body."""
    return {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": json.dumps(correction),
    }


@settings(max_examples=200)
@given(correction=valid_corrections())
def test_successful_correction_stamps_human_verified(correction):
    """Any valid correction on an existing dish stamps status=human_verified (R5.1)."""
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("menu-1", "dish-1", correction))

    # The correction is valid, so it is accepted and persisted.
    assert resp["statusCode"] == 200
    # Exactly one UpdateItem write was issued.
    assert len(fake.calls) == 1

    # The captured UpdateItem binds :status to the human_verified constant,
    # regardless of which optional fields were present.
    applied_status = fake.calls[0]["ExpressionAttributeValues"][":status"]
    assert applied_status == "human_verified"
    assert applied_status == handler.HUMAN_VERIFIED_STATUS

    # And the echoed ALL_NEW item carries that status.
    body = json.loads(resp["body"])
    assert body["item"]["status"] == "human_verified"
