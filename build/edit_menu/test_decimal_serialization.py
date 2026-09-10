# Feature: menu-read-edit-lambdas, Regression: DynamoDB Decimal values are JSON-serializable in editMenu responses
"""
Regression tests for editMenu Decimal serialization.

boto3's DynamoDB resource deserializes every numeric attribute to
``decimal.Decimal``. ``_response`` used to call ``json.dumps(body)`` with no
``default=`` hook, so an ``ALL_NEW`` row echoed from ``UpdateItem`` carrying a
number (e.g. ``updated_at``) raised
``TypeError: Object of type Decimal is not JSON serializable``. These plain
example tests exercise a real ``Decimal`` flowing through ``_response``
(directly and through the apply_correction/handler path) and therefore FAIL on
the unfixed code and PASS with the ``_json_default`` hook.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

# --- Import wiring: put build/layer/python (for ``services``) and build/edit_menu
#     (for ``handler``) on sys.path, matching the Lambda runtime layout. ---
_EDIT_MENU_DIR = Path(__file__).resolve().parent          # build/edit_menu
_BUILD_DIR = _EDIT_MENU_DIR.parent                        # build
_LAYER_PYTHON_DIR = _BUILD_DIR / "layer" / "python"       # build/layer/python

for _p in (str(_LAYER_PYTHON_DIR), str(_EDIT_MENU_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import handler  # noqa: E402  (build/edit_menu/handler.py)


class _FakeTable:
    """Fake DynamoDB Table echoing an ALL_NEW row containing a Decimal field."""

    def update_item(self, **kwargs):
        return {
            "Attributes": {
                "menu_id": "m",
                "item_id": "dish-1",
                "status": "human_verified",
                "updated_at": Decimal("1730000000"),
            }
        }


def test_response_serializes_decimal_directly():
    """Decimal values flowing through _response are JSON-serializable (int/float)."""
    resp = handler._response(
        200, {"updated_at": Decimal("1730000000"), "score": Decimal("1.5")}
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["updated_at"] == 1730000000
    assert isinstance(body["updated_at"], int)
    assert body["score"] == 1.5
    assert isinstance(body["score"], float)


def test_apply_correction_serializes_decimal(monkeypatch):
    """An ALL_NEW row with a Decimal field serializes through the handler (R5.5)."""
    fake = _FakeTable()
    monkeypatch.setattr(handler, "_table", lambda: fake)

    resp = handler.handler(
        {
            "pathParameters": {"menuId": "m", "itemId": "dish-1"},
            "body": json.dumps({"name": "New"}),
        }
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["item"]["updated_at"] == 1730000000
