"""
readMenu Lambda handler.

Serves the read-only endpoints of the allergen-compliance system behind an
API Gateway HTTP API (v2) proxy integration:

  - GET /menus/{restaurantId}          -> list a restaurant's saved dishes
  - GET /menus/{uploadId}/status       -> poll an upload's processing status
  - GET /restaurants                   -> list all restaurant/menu IDs

Dispatch is driven by the API Gateway v2 ``routeKey`` (never by parsing the
raw request path), so the ``{restaurantId}`` and ``{uploadId}/status`` routes
can never be confused (design: readMenu route dispatch).

readMenu is strictly READ-ONLY. It reaches DynamoDB through the shared,
unmodified ``dynamo_service`` module (``list_items`` Query and ``get_item``
GetItem) for the per-restaurant routes, and — for ``GET /restaurants`` — via a
direct ``boto3`` DynamoDB Scan filtered to the dedicated restaurant registry
rows (``record_type == "restaurant"``). No PutItem / UpdateItem / DeleteItem is
issued anywhere in this module (R3.1, R3.4); every path is read-only.

Task 2.1 scope: handler scaffold, routeKey dispatch, and shared path-param
validation. The concrete GET path bodies are implemented in tasks 2.2 and 2.3;
they are stubbed here so the module imports and dispatches cleanly.
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from boto3.dynamodb.conditions import Attr

from services import dynamo_service  # shared Lambda layer (from services import ...)

# API Gateway v2 routeKeys this handler serves. Dispatch is by routeKey only.
ROUTE_LIST_MENU = "GET /menus/{restaurantId}"
ROUTE_UPLOAD_STATUS = "GET /menus/{uploadId}/status"
ROUTE_LIST_RESTAURANTS = "GET /restaurants"

# Item_Id prefix reserved for the upload-status sentinel row (Key Decision 1).
# Any row whose item_id begins with this string is excluded from dish listings
# (R1.4) — it is not a Dish_Item.
_SENTINEL_ITEM_ID_PREFIX = "upload#"

# Item_Id prefix used by a restaurant's own registry row. Since the registry-row
# change, a restaurant's registry row lives in the SAME menu_id partition as its
# dishes (shaped {"item_id": "restaurant#<id>", "record_type": "restaurant", ...}).
# It is not a Dish_Item and must also be excluded from dish listings, otherwise it
# would leak into GET /menus/{restaurantId} as a bogus dish (R1.4).
_RESTAURANT_ITEM_ID_PREFIX = "restaurant#"

# The supported translation language codes (per bedrock_service.LANGUAGES /
# the Translations_Map contract). A read must surface each present code's entry
# unchanged and flag every absent code as unavailable — without fabricating any
# translation content (R1.2).
_SUPPORTED_LANGUAGE_CODES = ("es", "de", "ja", "zh")

# Permitted character set for DynamoDB key path parameters (menu_id / upload_id).
# Restaurant slugs (e.g. "kiwi-cafe-queenstown") and upload identifiers
# (e.g. hex/UUID strings) use alphanumerics plus a small set of key-safe
# separators. Anything else (whitespace, path separators, control chars, etc.)
# is rejected as bad-charset before any DynamoDB call (R3.2).
_KEY_CHARSET = re.compile(r"^[A-Za-z0-9._:#-]+$")

# The ``record_type`` value stamped on a dedicated restaurant registry row.
# ``GET /restaurants`` selects exactly these rows so a restaurant's existence is
# independent of whether it has any dish rows (R6.1).
_RESTAURANT_RECORD_TYPE = "restaurant"

# DynamoDB table backing the registry Scan for GET /restaurants. Mirrors the
# lazy-``_table()`` boto3 pattern used by build/edit_menu/handler.py so importing
# this module never reaches AWS and tests can monkeypatch ``_table``.
TABLE_NAME = os.environ.get("MENU_TABLE_NAME", "")

# Lazily-created boto3 DynamoDB Table resource (cached in a module global so a warm
# Lambda reuses it). Tests replace ``_table()`` or reset ``_TABLE`` to inject a fake.
_TABLE = None


def _table():
    """Return (creating once) the boto3 DynamoDB ``Table`` resource for the menu table.

    Cached in a module global so repeated invocations in a warm Lambda reuse the
    same client; tests can replace this function or reset ``_TABLE`` to inject a fake.
    Only ``GET /restaurants`` uses this — it issues a single read-only Scan.

    The region is resolved exactly like ``dynamo_service`` (DYNAMODB_REGION →
    AWS_REGION → ap-southeast-2) because this project splits regions: the menu
    table lives in us-east-1 while AWS_REGION defaults to ap-southeast-2 for
    Bedrock. Using ``boto3.resource("dynamodb")`` with no region would follow the
    general AWS_REGION and hit the wrong region (500 "could not be retrieved").
    """
    global _TABLE
    if _TABLE is None:
        region = os.environ.get(
            "DYNAMODB_REGION", os.environ.get("AWS_REGION", "ap-southeast-2")
        )
        _TABLE = boto3.resource("dynamodb", region_name=region).Table(TABLE_NAME)
    return _TABLE


def _json_default(o):
    """json.dumps default hook: DynamoDB numbers come back as Decimal.

    Convert to int when the value is integral (e.g. updated_at epoch),
    otherwise float, so the JSON body is serializable and numerically faithful.
    """
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build an API Gateway v2 proxy response with a JSON body."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _error(status: int, description: str) -> Dict[str, Any]:
    """Standard error body: {"error": "<description>"}."""
    return _response(status, {"error": description})


def _get_path_param(event: Dict[str, Any], name: str) -> Optional[str]:
    """Read a path parameter from the API Gateway v2 event, if present."""
    params = event.get("pathParameters") or {}
    return params.get(name)


def _validate_key_param(value: Optional[str]) -> bool:
    """
    Shared path-param validator.

    Returns True when ``value`` is a usable DynamoDB key: present, not
    empty/whitespace-only, and composed solely of permitted key characters.
    Returns False otherwise so the caller can respond 400 *before* any
    DynamoDB call (R3.2). Performs no I/O and never mutates stored data.
    """
    if value is None:
        return False
    if value.strip() == "":
        return False
    return _KEY_CHARSET.match(value) is not None


def _is_sentinel_row(item: Dict[str, Any]) -> bool:
    """
    True when ``item`` is the upload-status sentinel row (its ``item_id`` begins
    with the reserved ``upload#`` prefix), and therefore not a Dish_Item to be
    listed (R1.4).
    """
    item_id = item.get("item_id")
    return isinstance(item_id, str) and item_id.startswith(_SENTINEL_ITEM_ID_PREFIX)


def _is_non_dish_row(item: Dict[str, Any]) -> bool:
    """
    True when ``item`` is not a Dish_Item and must be excluded from dish listings
    (R1.4). This is a blocklist covering every known non-dish row that can share a
    restaurant's menu_id partition:

      - the upload-status sentinel row (``item_id`` begins with ``upload#``), and
      - the restaurant's own registry row (``item_id`` begins with ``restaurant#``,
        or, defensively, ``record_type == "restaurant"`` in case a registry row's
        item_id were ever shaped differently).

    Kept as a blocklist rather than a ``dish-`` allowlist so legitimately-stored
    dish rows that may not follow the ``dish-`` convention on the live table are
    still listed.
    """
    if _is_sentinel_row(item):
        return True
    item_id = item.get("item_id")
    if isinstance(item_id, str) and item_id.startswith(_RESTAURANT_ITEM_ID_PREFIX):
        return True
    if item.get("record_type") == _RESTAURANT_RECORD_TYPE:
        return True
    return False


def _annotate_translations(dish: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of ``dish`` carrying its embedded Translations_Map unchanged
    plus a ``translations_status`` map that flags, for each of the four
    supported language codes, whether a translation entry is present or
    unavailable.

    The present entries are returned verbatim (no mutation); absent codes are
    flagged ``"unavailable"`` and NO translation content is fabricated for them
    (R1.2). The union of present entries and flagged-missing codes is exactly
    the four supported codes.
    """
    annotated = dict(dish)
    translations = dish.get("translations")
    if not isinstance(translations, dict):
        translations = {}

    status = {
        code: ("available" if code in translations else "unavailable")
        for code in _SUPPORTED_LANGUAGE_CODES
    }
    annotated["translations_status"] = status
    return annotated


def _handle_list_menu(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    GET /menus/{restaurantId} — validate the path param, then list dishes.

    Behaviour (R1.1–R1.6, R3.3, R3.6):
      - Validate the path param (400 already handled below).
      - ``dynamo_service.list_items(restaurantId)`` — a single Query returning
        every row in the partition (dish rows plus, when present, the single
        ``upload#`` sentinel).
      - Zero rows in the partition -> 404 "restaurant not found" (R3.3): the
        partition is unknown.
      - >=1 row -> 200 ``{"items": [...]}``; the sentinel row is excluded (R1.4)
        so the dish collection may legitimately be empty (R1.3/R1.5).
      - Each returned dish keeps its embedded Translations_Map unchanged (no
        per-translation query) and is annotated so absent supported codes are
        flagged unavailable without fabricated content (R1.2).
      - A ``list_items`` failure -> 500 "menu could not be retrieved" with no
        partial collection returned (R1.6/R3.6).
    """
    restaurant_id = _get_path_param(event, "restaurantId")
    if not _validate_key_param(restaurant_id):
        return _error(400, "invalid restaurantId")

    try:
        rows = dynamo_service.list_items(restaurant_id)
    except Exception:  # noqa: BLE001 — any read failure is a 500 (R1.6/R3.6).
        # Fail closed: no partial collection is returned.
        return _error(500, "menu could not be retrieved")

    # Zero rows total => the restaurant partition is unknown (R3.3).
    if not rows:
        return _error(404, "restaurant not found")

    # Partition exists (>=1 row). Build the dish collection: exclude every
    # non-dish row — the upload# sentinel AND the restaurant's own registry row
    # (R1.4) — and annotate translations (R1.2). A partition that holds only
    # non-dish rows (e.g. a registry-only partition) still represents an existing
    # restaurant, so it yields a valid 200 with an empty dish collection, never a
    # 404 (R1.3/R1.5).
    items = [
        _annotate_translations(row)
        for row in rows
        if not _is_non_dish_row(row)
    ]
    return _response(200, {"items": items})


def _handle_upload_status(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    GET /menus/{uploadId}/status — validate the path param, then read status.

    Task 2.1 provides validation + scaffold only; the get_item/status logic is
    implemented in task 2.3.
    """
    upload_id = _get_path_param(event, "uploadId")
    if upload_id is None or upload_id.strip() == "":
        # Missing/empty/whitespace-only uploadId: no retrieval performed (R2.6).
        return _error(400, "uploadId is required")
    if not _validate_key_param(upload_id):
        # Present but contains characters outside the permitted key charset (R3.2).
        return _error(400, "invalid uploadId")

    # Read the upload-status sentinel row via a single GetItem keyed on
    # menu_id = uploadId and item_id = "upload#" + uploadId (R2.1). The sentinel
    # is read-only here: readMenu observes its `status` and never mutates it.
    sentinel_item_id = "upload#" + upload_id
    try:
        sentinel = dynamo_service.get_item(menu_id=upload_id, item_id=sentinel_item_id)
    except Exception:  # pragma: no cover - exercised via mocked failure in tests
        # get_item read error: respond 500 and leave the sentinel unmodified (R2.7).
        return _error(500, "status could not be retrieved")

    if sentinel is None:
        # No sentinel row exists for this uploadId (R2.5).
        return _error(404, "uploadId not found")

    # Sentinel found: return the stored status verbatim, never interpreting or
    # advancing it (R2.2, R2.3, R2.4).
    return _response(200, {"uploadId": upload_id, "status": sentinel.get("status")})


def _handle_list_restaurants(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    GET /restaurants — list every registered restaurant in the table (R6).

    Powers the frontend "select your cafe" picker for both the public customer
    browsing view and the restaurant/admin login. There are no path parameters
    to validate.

    A restaurant is its own dedicated **registry row** — the source of truth for
    restaurant existence — and is INDEPENDENT of whether it has any dish rows. A
    registry row is shaped ``{"menu_id": <id>, "item_id": "restaurant#<id>",
    "record_type": "restaurant", "name": <display name>}``. This route therefore
    reads the registry directly rather than inferring restaurants from dish rows,
    so a restaurant whose dishes were all deleted still appears in the picker.

    Behaviour:

      - A direct ``boto3`` DynamoDB **Scan** filtered to registry rows
        (``FilterExpression = Attr("record_type").eq("restaurant")``), paginating
        over ``LastEvaluatedKey`` so no rows are missed (R6.1).
      - Each registry row becomes ``{"menu_id": <menu_id>, "name": <name>}``,
        falling back to the ``menu_id`` when ``name`` is absent so the picker always
        has a label. The list is sorted by ``menu_id`` for deterministic output and
        returned as ``{"restaurants": [...]}`` with HTTP 200. No registry rows yields
        ``{"restaurants": []}`` (R6.2).
      - Any Scan failure -> 500 "restaurants could not be retrieved" with no partial
        list returned; fail closed (R6.3).

    Read-only: this route issues only a Scan; no write of any kind (R6.4 / R3.1).
    """
    try:
        rows = []
        scan_kwargs: Dict[str, Any] = {
            "FilterExpression": Attr("record_type").eq(_RESTAURANT_RECORD_TYPE),
        }
        table = _table()
        while True:
            response = table.scan(**scan_kwargs)
            rows.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
    except Exception:  # noqa: BLE001 — any read failure is a 500 (R6.3).
        # Fail closed: no partial list is returned.
        return _error(500, "restaurants could not be retrieved")

    restaurants = [
        {"menu_id": row.get("menu_id"), "name": row.get("name") or row.get("menu_id")}
        for row in rows
    ]
    restaurants.sort(key=lambda r: r["menu_id"])
    return _response(200, {"restaurants": restaurants})


def handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Lambda entry point. Dispatches on the API Gateway v2 ``routeKey`` between
    the read routes. An unrecognized route yields 404.
    """
    route_key = (event or {}).get("routeKey")

    if route_key == ROUTE_LIST_MENU:
        return _handle_list_menu(event)
    if route_key == ROUTE_UPLOAD_STATUS:
        return _handle_upload_status(event)
    if route_key == ROUTE_LIST_RESTAURANTS:
        return _handle_list_restaurants(event)

    return _error(404, "route not found")
