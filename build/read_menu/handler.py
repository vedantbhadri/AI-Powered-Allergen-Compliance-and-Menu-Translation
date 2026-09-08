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

readMenu is strictly READ-ONLY. It reaches DynamoDB exclusively through the
shared, unmodified ``dynamo_service`` module (``list_items`` Query,
``get_item`` GetItem, and ``list_menus`` Scan). No PutItem / UpdateItem /
DeleteItem is issued anywhere in this module (R3.1, R3.4).

Task 2.1 scope: handler scaffold, routeKey dispatch, and shared path-param
validation. The concrete GET path bodies are implemented in tasks 2.2 and 2.3;
they are stubbed here so the module imports and dispatches cleanly.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from services import dynamo_service  # shared Lambda layer (from services import ...)

# API Gateway v2 routeKeys this handler serves. Dispatch is by routeKey only.
ROUTE_LIST_MENU = "GET /menus/{restaurantId}"
ROUTE_UPLOAD_STATUS = "GET /menus/{uploadId}/status"
ROUTE_LIST_RESTAURANTS = "GET /restaurants"

# Item_Id prefix reserved for the upload-status sentinel row (Key Decision 1).
# Any row whose item_id begins with this string is excluded from dish listings
# (R1.4) — it is not a Dish_Item.
_SENTINEL_ITEM_ID_PREFIX = "upload#"

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


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build an API Gateway v2 proxy response with a JSON body."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
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

    # Partition exists (>=1 row). Build the dish collection: exclude the
    # upload# sentinel (R1.4) and annotate translations (R1.2). An empty dish
    # collection is a valid 200 (R1.3/R1.5).
    items = [
        _annotate_translations(row)
        for row in rows
        if not _is_sentinel_row(row)
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
    GET /restaurants — list every restaurant/menu ID in the table (R6).

    Powers the frontend "select your cafe" picker for both the public customer
    browsing view and the restaurant/admin login. There are no path parameters
    to validate. Behaviour:

      - ``dynamo_service.list_menus()`` — a single Scan projecting ``menu_id``
        that returns a sorted list of unique menu_id STRINGS (R6.1).
      - Each bare menu_id string is wrapped into an object ``{"menu_id": mid}``
        and returned as ``{"restaurants": [...]}`` with HTTP 200. An empty table
        yields ``{"restaurants": []}`` (R6.2).
      - A ``list_menus`` failure -> 500 "restaurants could not be retrieved"
        with no partial list returned (R6.3).

    Read-only: no write of any kind is issued (R6.4 / R3.1).
    """
    try:
        menu_ids = dynamo_service.list_menus()
    except Exception:  # noqa: BLE001 — any read failure is a 500 (R6.3).
        # Fail closed: no partial list is returned.
        return _error(500, "restaurants could not be retrieved")

    restaurants = [{"menu_id": mid} for mid in menu_ids]
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
