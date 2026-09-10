# Feature: menu-read-edit-lambdas, Regression: DynamoDB Decimal values are JSON-serializable in readMenu responses
"""
Regression tests for readMenu Decimal serialization.

boto3's DynamoDB resource deserializes every numeric attribute to
``decimal.Decimal``. ``_response`` used to call ``json.dumps(body)`` with no
``default=`` hook, so any real item carrying a number (e.g. ``updated_at``)
raised ``TypeError: Object of type Decimal is not JSON serializable``. These
plain example tests exercise a real ``Decimal`` flowing through ``_response``
(directly and through the list route) and therefore FAIL on the unfixed code
and PASS with the ``_json_default`` hook.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

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


def test_list_menu_route_serializes_decimal(monkeypatch):
    """A dish row with Decimal fields serializes through the list route (R1.1)."""
    dish_row = {
        "menu_id": "m",
        "item_id": "dish-1",
        "name": "X",
        "description": "Y",
        "updated_at": Decimal("1730000000"),
        "translations": {},
    }
    monkeypatch.setattr(dynamo_service, "list_items", lambda mid: [dish_row])

    resp = handler.handler(
        {
            "routeKey": handler.ROUTE_LIST_MENU,
            "pathParameters": {"restaurantId": "m"},
        }
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["items"][0]["updated_at"] == 1730000000
