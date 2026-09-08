"""
API Gateway HTTP API v2 event-shape integration tests (task 7.2).

Two integration concerns, exercised through realistic API Gateway HTTP API v2
proxy event shapes (full ``requestContext.http``, ``routeKey``, ``rawPath``, and
``pathParameters``) rather than the trimmed events the unit tests use:

  1. readMenu dispatches strictly by ``routeKey`` across its two GET routes
     (``GET /menus/{restaurantId}`` and ``GET /menus/{uploadId}/status``). We feed
     each full v2 event and assert the correct branch handled it — the list route
     lists dishes (R2.2 / R3.5 read path) and the status route reads the sentinel
     (R2.2). ``dynamo_service`` reads are mocked so no AWS is contacted for these.

  2. editMenu's ``ConditionExpression = attribute_exists(menu_id)`` produces a 404
     when the target dish is absent (R5.4). This is exercised against a real local
     DynamoDB table stood up with ``moto`` (partition key ``menu_id``, sort key
     ``item_id``): editMenu is pointed at it, a PATCH for an absent dish is fed as a
     full v2 event, and the conditional write is asserted to fail closed with a 404
     "dish not found" and no row written.

Both handler modules are named ``handler`` package-wide, so each is loaded from its
explicit file path under a unique module name to avoid a cross-directory import
collision within one pytest run (mirroring the existing test files).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# --- Import wiring: put build/layer/python (for ``services``) on sys.path so both
#     handlers resolve ``from services import ...`` the way the Lambda layer does. ---
_HERE = Path(__file__).resolve().parent                 # build
_LAYER_PYTHON = _HERE / "layer" / "python"              # build/layer/python
_READ_MENU_DIR = _HERE / "read_menu"                    # build/read_menu
_EDIT_MENU_DIR = _HERE / "edit_menu"                    # build/edit_menu

for _p in (str(_LAYER_PYTHON), str(_READ_MENU_DIR), str(_EDIT_MENU_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_handler(module_name: str, handler_dir: Path):
    """Load a ``handler.py`` from an explicit path under a unique module name."""
    spec = importlib.util.spec_from_file_location(module_name, handler_dir / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


read_handler = _load_handler("read_menu_handler_it", _READ_MENU_DIR)
edit_handler = _load_handler("edit_menu_handler_it", _EDIT_MENU_DIR)


# --------------------------------------------------------------------------- #
# API Gateway HTTP API v2 event builders (realistic proxy event shapes)
# --------------------------------------------------------------------------- #
def _v2_event(method: str, route_key: str, raw_path: str,
              path_parameters: dict, *, body=None, is_base64=False) -> dict:
    """Build a realistic API Gateway HTTP API (payload v2.0) proxy event.

    Includes the ``requestContext.http`` block, ``routeKey``, ``rawPath``,
    ``rawQueryString``, and ``pathParameters`` the real integration delivers, so
    the handlers are exercised against the shape they see in production.
    """
    event = {
        "version": "2.0",
        "routeKey": route_key,
        "rawPath": raw_path,
        "rawQueryString": "",
        "headers": {
            "accept": "application/json",
            "content-type": "application/json",
            "host": "abcd1234.execute-api.ap-southeast-2.amazonaws.com",
        },
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abcd1234",
            "domainName": "abcd1234.execute-api.ap-southeast-2.amazonaws.com",
            "http": {
                "method": method,
                "path": raw_path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.9",
                "userAgent": "integration-test",
            },
            "requestId": "req-id-0001",
            "routeKey": route_key,
            "stage": "$default",
            "time": "01/Jan/2024:00:00:00 +0000",
            "timeEpoch": 1704067200000,
        },
        "pathParameters": path_parameters,
        "isBase64Encoded": is_base64,
    }
    if body is not None:
        event["body"] = body
    return event


# --------------------------------------------------------------------------- #
# Fake dynamo_service for the readMenu dispatch tests (reads mocked, no AWS)
# --------------------------------------------------------------------------- #
class _FakeDynamo:
    """Minimal read-only fake recording which read function was invoked.

    Exposes only ``list_items`` and ``get_item`` (readMenu is read-only), so a
    branch that dispatched to the wrong route would call the wrong recorder and
    the assertions would catch it.
    """

    def __init__(self, *, list_result=None, get_result=None):
        self._list_result = list_result if list_result is not None else []
        self._get_result = get_result
        self.list_items_calls = []
        self.get_item_calls = []

    def list_items(self, menu_id):
        self.list_items_calls.append(menu_id)
        return self._list_result

    def get_item(self, menu_id, item_id):
        self.get_item_calls.append({"menu_id": menu_id, "item_id": item_id})
        return self._get_result


# --------------------------------------------------------------------------- #
# 1. readMenu dispatches by routeKey across its two GET routes (R2.2 / R3.5)
# --------------------------------------------------------------------------- #
def test_readmenu_dispatches_list_route_by_routekey(monkeypatch):
    """A full v2 event for ``GET /menus/{restaurantId}`` hits the list branch only."""
    dish = {
        "menu_id": "kiwi-cafe-queenstown",
        "item_id": "dish-0001",
        "name": "Green Curry",
        "translations": {"es": "Curry verde", "de": "Grunes Curry"},
    }
    sentinel = {"menu_id": "kiwi-cafe-queenstown",
                "item_id": "upload#abc", "status": "ready"}
    fake = _FakeDynamo(list_result=[dish, sentinel])
    monkeypatch.setattr(read_handler, "dynamo_service", fake)

    event = _v2_event(
        method="GET",
        route_key="GET /menus/{restaurantId}",
        raw_path="/menus/kiwi-cafe-queenstown",
        path_parameters={"restaurantId": "kiwi-cafe-queenstown"},
    )

    resp = read_handler.handler(event)

    # Dispatched to the list branch: list_items ran, get_item did not.
    assert fake.list_items_calls == ["kiwi-cafe-queenstown"]
    assert fake.get_item_calls == []

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    # Sentinel excluded (R1.4); the single dish is returned with translations intact.
    assert [i["item_id"] for i in body["items"]] == ["dish-0001"]
    assert body["items"][0]["translations"] == dish["translations"]


def test_readmenu_dispatches_status_route_by_routekey(monkeypatch):
    """A full v2 event for ``GET /menus/{uploadId}/status`` hits the status branch only."""
    fake = _FakeDynamo(get_result={
        "menu_id": "upload-xyz-789",
        "item_id": "upload#upload-xyz-789",
        "status": "analyzing",
    })
    monkeypatch.setattr(read_handler, "dynamo_service", fake)

    event = _v2_event(
        method="GET",
        route_key="GET /menus/{uploadId}/status",
        raw_path="/menus/upload-xyz-789/status",
        path_parameters={"uploadId": "upload-xyz-789"},
    )

    resp = read_handler.handler(event)

    # Dispatched to the status branch: get_item ran keyed on the sentinel, list did not.
    assert fake.list_items_calls == []
    assert fake.get_item_calls == [
        {"menu_id": "upload-xyz-789", "item_id": "upload#upload-xyz-789"}
    ]

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    # Stored status returned verbatim (R2.2).
    assert body == {"uploadId": "upload-xyz-789", "status": "analyzing"}


def test_readmenu_status_and_list_routes_do_not_cross_dispatch(monkeypatch):
    """The two routes share a raw path prefix but never cross-dispatch (by routeKey).

    Both events carry the same restaurant/upload token in ``rawPath``; only the
    ``routeKey`` distinguishes them, proving dispatch is by ``routeKey`` and not by
    parsing the path.
    """
    # Status route: same token, status routeKey -> only get_item runs.
    status_fake = _FakeDynamo(get_result={"status": "processing"})
    monkeypatch.setattr(read_handler, "dynamo_service", status_fake)
    status_event = _v2_event(
        method="GET",
        route_key="GET /menus/{uploadId}/status",
        raw_path="/menus/token-42/status",
        path_parameters={"uploadId": "token-42"},
    )
    read_handler.handler(status_event)
    assert status_fake.get_item_calls and not status_fake.list_items_calls

    # List route: token as restaurantId, list routeKey -> only list_items runs.
    list_fake = _FakeDynamo(list_result=[{"menu_id": "token-42", "item_id": "dish-1"}])
    monkeypatch.setattr(read_handler, "dynamo_service", list_fake)
    list_event = _v2_event(
        method="GET",
        route_key="GET /menus/{restaurantId}",
        raw_path="/menus/token-42",
        path_parameters={"restaurantId": "token-42"},
    )
    read_handler.handler(list_event)
    assert list_fake.list_items_calls and not list_fake.get_item_calls


def test_readmenu_unknown_routekey_yields_404(monkeypatch):
    """An unrecognized ``routeKey`` yields 404 and touches no read function."""
    fake = _FakeDynamo()
    monkeypatch.setattr(read_handler, "dynamo_service", fake)
    event = _v2_event(
        method="DELETE",
        route_key="DELETE /menus/{restaurantId}",
        raw_path="/menus/whatever",
        path_parameters={"restaurantId": "whatever"},
    )
    resp = read_handler.handler(event)
    assert resp["statusCode"] == 404
    assert fake.list_items_calls == [] and fake.get_item_calls == []


# --------------------------------------------------------------------------- #
# 2. editMenu ConditionExpression -> 404 against a real local DynamoDB table (R5.4)
# --------------------------------------------------------------------------- #
moto = pytest.importorskip(
    "moto",
    reason="moto is required for the local-DynamoDB editMenu 404 integration test",
)
from moto import mock_aws  # noqa: E402
import boto3  # noqa: E402

_MENU_TABLE_NAME = "test-menu-items"


@pytest.fixture
def local_dynamo_table():
    """Stand up a local DynamoDB menu table with moto and yield the boto3 Table.

    Schema mirrors the production single-table design: partition key ``menu_id``
    (S), sort key ``item_id`` (S). editMenu is pointed at this table by setting
    ``MENU_TABLE_NAME`` and resetting the handler's cached ``_TABLE`` so its lazy
    ``_table()`` binds to the moto-backed resource.
    """
    with mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-2"
        dynamodb = boto3.resource("dynamodb", region_name="ap-southeast-2")
        dynamodb.create_table(
            TableName=_MENU_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "menu_id", "KeyType": "HASH"},
                {"AttributeName": "item_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "menu_id", "AttributeType": "S"},
                {"AttributeName": "item_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table = dynamodb.Table(_MENU_TABLE_NAME)
        table.wait_until_exists()

        # Point editMenu at the local table and rebind its cached resource.
        edit_handler.TABLE_NAME = _MENU_TABLE_NAME
        edit_handler._TABLE = dynamodb.Table(_MENU_TABLE_NAME)
        try:
            yield table
        finally:
            edit_handler._TABLE = None


def test_editmenu_condition_expression_404_on_absent_dish(local_dynamo_table):
    """PATCH against an absent dish fails the condition -> 404, no row written (R5.4).

    The table is empty, so ``ConditionExpression = attribute_exists(menu_id)`` on the
    ``UpdateItem`` cannot be satisfied; moto raises ``ConditionalCheckFailedException``
    and editMenu maps it to 404 "dish not found" without creating the row.
    """
    body = json.dumps({"name": "Corrected Name"})
    event = _v2_event(
        method="PATCH",
        route_key="PATCH /menus/{menuId}/items/{itemId}",
        raw_path="/menus/ghost-restaurant/items/dish-does-not-exist",
        path_parameters={"menuId": "ghost-restaurant", "itemId": "dish-does-not-exist"},
        body=body,
    )

    resp = edit_handler.handler(event)

    assert resp["statusCode"] == 404
    assert json.loads(resp["body"]) == {"error": "dish not found"}

    # Fail-closed: the conditional write created no row in the real local table.
    got = local_dynamo_table.get_item(
        Key={"menu_id": "ghost-restaurant", "item_id": "dish-does-not-exist"}
    )
    assert "Item" not in got


def test_editmenu_condition_expression_allows_update_on_existing_dish(local_dynamo_table):
    """Contrast case: the same conditional UpdateItem succeeds when the dish exists.

    Seeds a real dish row, PATCHes its name, and asserts a 200 with the row updated
    and ``status = human_verified`` — confirming the 404 above is driven by the
    absent-row condition, not a broken update path.
    """
    local_dynamo_table.put_item(Item={
        "menu_id": "real-restaurant",
        "item_id": "dish-0001",
        "name": "Old Name",
        "status": "ready",
        "translations": {"es": "Nombre viejo"},
    })

    body = json.dumps({"name": "New Name"})
    event = _v2_event(
        method="PATCH",
        route_key="PATCH /menus/{menuId}/items/{itemId}",
        raw_path="/menus/real-restaurant/items/dish-0001",
        path_parameters={"menuId": "real-restaurant", "itemId": "dish-0001"},
        body=body,
    )

    resp = edit_handler.handler(event)

    assert resp["statusCode"] == 200
    item = json.loads(resp["body"])["item"]
    assert item["name"] == "New Name"
    assert item["status"] == "human_verified"

    # Persisted to the real local table.
    stored = local_dynamo_table.get_item(
        Key={"menu_id": "real-restaurant", "item_id": "dish-0001"}
    )["Item"]
    assert stored["name"] == "New Name"
    assert stored["status"] == "human_verified"
