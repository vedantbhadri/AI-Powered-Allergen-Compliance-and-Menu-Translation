"""
Example / edge-case unit tests for the editMenu handler (task 7.1).

Concrete-case pytest functions (NOT property tests — no @given), each pinning a
specific documented edge of editMenu's contract:

  - 400 for an empty-correction body (R4.8): a PATCH carrying none of the four
    correctable fields is rejected with no write.
  - 200 body echoing the ``ALL_NEW`` attributes including recomputed tags (R5.5):
    a valid allergen correction returns the post-update row with
    ``display_tags`` / ``diet_tags`` recomputed via the real ``allergen_rules``.

DynamoDB is MOCKED: ``handler._table`` is swapped for a fake whose ``update_item``
records its kwargs and echoes an ``ALL_NEW`` row assembled from the update
expression. No AWS is contacted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# --- Import wiring: put build/layer/python (for ``services``) and build/edit_menu
#     on sys.path, mirroring the Lambda runtime layout. Both handlers are named
#     ``handler`` module-wide, so load THIS package's handler from its explicit
#     file path under a unique module name to avoid a cross-directory collision
#     when readMenu's handler was imported first in the same pytest run. ---
_HERE = Path(__file__).resolve().parent            # build/edit_menu
_BUILD = _HERE.parent                              # build
_LAYER_PYTHON = _BUILD / "layer" / "python"        # build/layer/python

for _p in (str(_LAYER_PYTHON), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location("edit_menu_handler", _HERE / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

from services import allergen_rules  # noqa: E402  (shared layer)


class _FakeTable:
    """Fake DynamoDB Table capturing update_item kwargs and echoing ALL_NEW.

    The echoed ``Attributes`` are reconstructed from the UpdateExpression's bound
    values so the returned row reflects exactly what editMenu asked to persist —
    letting the 200-echo assertion inspect the recomputed tags (R5.5).
    """

    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        values = kwargs["ExpressionAttributeValues"]
        attributes = {
            "menu_id": kwargs["Key"]["menu_id"],
            "item_id": kwargs["Key"]["item_id"],
            "status": values.get(":status"),
        }
        if ":confirmed" in values:
            attributes["allergens"] = {
                "confirmed": values[":confirmed"],
                "display_tags": values[":display_tags"],
            }
            attributes["diet_tags"] = values[":diet_tags"]
        return {"Attributes": attributes}


@pytest.fixture
def fake_table(monkeypatch):
    """Swap handler._table for a fake capturing table; return the fake."""
    fake = _FakeTable()
    monkeypatch.setattr(handler, "_table", lambda: fake)
    return fake


def _event(menu_id, item_id, body):
    return {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": json.dumps(body),
    }


def _body(response):
    return json.loads(response["body"])


def test_empty_correction_body_returns_400_no_write(fake_table):
    """A body with none of the four correctable fields is rejected 400 (R4.8)."""
    response = handler.handler(_event("menu-1", "dish-1", {"unrelated": "x"}))

    assert response["statusCode"] == 400
    assert _body(response) == {"error": "no correctable field provided"}
    # No write was ever attempted for the rejected correction.
    assert fake_table.calls == []


def test_valid_correction_returns_200_echoing_recomputed_tags(fake_table):
    """A valid allergen correction returns 200 echoing ALL_NEW with recomputed tags (R5.5)."""
    proposed = ["Milk", "Peanuts", "NotAnAllergen"]
    response = handler.handler(
        _event("menu-1", "dish-1", {"confirmed_allergens": proposed})
    )

    assert response["statusCode"] == 200

    # Exactly one UpdateItem was issued.
    assert len(fake_table.calls) == 1

    item = _body(response)["item"]

    # The echoed row carries the PEAL-filtered confirmed set (non-member dropped)
    # and the tags recomputed straight from the real allergen_rules module (R5.5).
    expected_confirmed = ["Milk", "Peanuts"]
    assert item["allergens"]["confirmed"] == expected_confirmed
    assert item["allergens"]["display_tags"] == allergen_rules.to_display_tags(
        expected_confirmed
    )
    assert item["diet_tags"] == allergen_rules.derive_diet_tags(expected_confirmed)

    # And the correction stamped status = human_verified.
    assert item["status"] == handler.HUMAN_VERIFIED_STATUS
