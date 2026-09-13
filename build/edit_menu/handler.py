"""
editMenu Lambda handler.

Serves the human-in-the-loop dish-correction endpoint:

    PATCH /menus/{menuId}/items/{itemId}

Behaviour (see .kiro/specs/menu-read-edit-lambdas/{requirements,design}.md):

- Parses the JSON request body (base64-decoded when API Gateway flags it) with the
  four optional correctable fields ``confirmed_allergens``, ``translations``, ``name``,
  and ``description``.
- Validates the correction *before* any DynamoDB write:
    * an empty correction (none of the four fields present) is rejected 400 (R4.8);
    * ``translations`` carrying any code outside ``{es, de, ja, zh}`` is rejected 400 (R4.7);
    * ``name`` outside 1-200 chars is rejected 400 (R4.4);
    * ``description`` outside 0-2000 chars is rejected 400 (R4.5).
- Recomputes allergen/diet tags through the *real* ``allergen_rules`` module (task 3.2)
  and persists via a native conditional ``UpdateItem`` (task 3.3).

This module imports the shared service layer as ``from services import allergen_rules``
and talks to DynamoDB with ``boto3`` directly (never ``dynamo_service``), so its
execution role can stay scoped to ``dynamodb:UpdateItem`` only.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import boto3

from services import allergen_rules  # noqa: F401  (used by task 3.2)

# The only language codes editMenu accepts in a translations correction (R4.7).
SUPPORTED_LANGUAGE_CODES = frozenset({"es", "de", "ja", "zh"})

# The correctable fields a PATCH body may carry (R4.1/R4.3/R4.4/R4.5).
CORRECTABLE_FIELDS = ("confirmed_allergens", "translations", "name", "description")

# name / description length bounds, inclusive (R4.4 / R4.5).
NAME_MIN_LEN, NAME_MAX_LEN = 1, 200
DESCRIPTION_MIN_LEN, DESCRIPTION_MAX_LEN = 0, 2000

TABLE_NAME = os.environ.get("MENU_TABLE_NAME", "")

# The dish status editMenu stamps on every applied correction (R5.1).
HUMAN_VERIFIED_STATUS = "human_verified"

# Lazily-created boto3 DynamoDB Table resource, so importing this module never
# reaches out to AWS and unit tests can monkeypatch `_table()` freely.
_TABLE = None


def _table():
    """Return (creating once) the boto3 DynamoDB ``Table`` resource for the menu table.

    Cached in a module global so repeated invocations in a warm Lambda reuse the
    same client; tests can replace this function or reset ``_TABLE`` to inject a fake.

    The region is resolved exactly like ``dynamo_service`` (DYNAMODB_REGION →
    AWS_REGION → ap-southeast-2) because this project splits regions: the menu
    table lives in us-east-1 while AWS_REGION defaults to ap-southeast-2 for
    Bedrock. Using ``boto3.resource("dynamodb")`` with no region would follow the
    general AWS_REGION and hit the wrong region (missing table -> 500/404).
    """
    global _TABLE
    if _TABLE is None:
        region = os.environ.get(
            "DYNAMODB_REGION", os.environ.get("AWS_REGION", "ap-southeast-2")
        )
        _TABLE = boto3.resource("dynamodb", region_name=region).Table(TABLE_NAME)
    return _TABLE


class ValidationError(Exception):
    """Raised when a correction request fails validation; carries the 400 message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _json_default(o):
    """json.dumps default hook: DynamoDB numbers come back as Decimal.

    Convert to int when the value is integral (e.g. updated_at epoch),
    otherwise float, so the JSON body is serializable and numerically faithful.
    """
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build an API Gateway HTTP API v2 proxy response with a JSON body."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _error(status_code: int, message: str) -> Dict[str, Any]:
    """Standard error envelope: ``{"error": "<description>"}``."""
    return _response(status_code, {"error": message})


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """Decode and parse the request body into a JSON object.

    Handles the API Gateway ``isBase64Encoded`` flag and rejects a missing,
    non-JSON, or non-object body. Returns the parsed mapping.
    """
    raw = event.get("body")
    if raw is None or raw == "":
        raise ValidationError("request body is required")

    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise ValidationError("request body could not be decoded")

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValidationError("request body is not valid JSON")

    if not isinstance(parsed, dict):
        raise ValidationError("request body must be a JSON object")

    return parsed


def validate_correction(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the correction body and return the recognised correctable fields.

    Enforces, in order:

    - at least one correctable field is present, else R4.8;
    - every ``translations`` code is supported, else R4.7;
    - ``name`` length is within 1-200, else R4.4;
    - ``description`` length is within 0-2000, else R4.5.

    Raises :class:`ValidationError` (mapped to HTTP 400) on any failure so that no
    write is ever attempted for a rejected request. On success returns a dict holding
    only the correctable fields that were present in the request.
    """
    correction: Dict[str, Any] = {
        field: body[field] for field in CORRECTABLE_FIELDS if field in body
    }

    # R4.8 - an empty correction (none of the four fields) is rejected with no write.
    if not correction:
        raise ValidationError("no correctable field provided")

    # R4.7 - reject translations carrying any unsupported language code.
    if "translations" in correction:
        translations = correction["translations"]
        if not isinstance(translations, dict):
            raise ValidationError("translations must be an object")
        for code in translations:
            if code not in SUPPORTED_LANGUAGE_CODES:
                raise ValidationError(f"unsupported language code: {code}")

    # R4.4 - name must be a string of length 1-200 inclusive.
    if "name" in correction:
        name = correction["name"]
        if not isinstance(name, str) or not (NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN):
            raise ValidationError("invalid name")

    # R4.5 - description must be a string of length 0-2000 inclusive.
    if "description" in correction:
        description = correction["description"]
        if not isinstance(description, str) or not (
            DESCRIPTION_MIN_LEN <= len(description) <= DESCRIPTION_MAX_LEN
        ):
            raise ValidationError("invalid description")

    return correction


def _path_params(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``menuId`` / ``itemId`` from the API Gateway v2 path parameters."""
    params = event.get("pathParameters") or {}
    return params.get("menuId"), params.get("itemId")


def filter_confirmed_allergens(proposed: Any) -> list:
    """Retain only proposed allergens that are members of ``PEAL_CATEGORIES`` (R4.1).

    Every value that is not a member of ``allergen_rules.PEAL_CATEGORIES`` is silently
    discarded, preserving the request order of the members that are kept and dropping any
    duplicates. A non-list ``proposed`` (or ``None``) yields an empty confirmed set.
    """
    if not isinstance(proposed, (list, tuple)):
        return []

    peal = allergen_rules.PEAL_CATEGORIES
    confirmed: list = []
    for value in proposed:
        if value in peal and value not in confirmed:
            confirmed.append(value)
    return confirmed


def recompute_allergen_fields(confirmed_allergens: Any) -> Dict[str, Any]:
    """PEAL-filter the proposed allergens and recompute the derived tags (R4.1/R4.2/R4.6).

    Returns the fields editMenu will persist for an allergen correction:

    - ``confirmed`` — the proposed allergens intersected with ``PEAL_CATEGORIES`` (R4.1);
    - ``display_tags`` — the *real* ``allergen_rules.to_display_tags(confirmed)`` (R4.2);
    - ``diet_tags`` — the *real* ``allergen_rules.derive_diet_tags(confirmed)`` (R4.2).

    Both tag lists come straight from the shared, unmodified ``allergen_rules`` module — no
    reimplementation (R4.6). Task 3.3 persists these into ``allergens.confirmed`` /
    ``allergens.display_tags`` and the top-level ``diet_tags`` via the conditional
    ``UpdateItem``.
    """
    confirmed = filter_confirmed_allergens(confirmed_allergens)
    return {
        "confirmed": confirmed,
        "display_tags": allergen_rules.to_display_tags(confirmed),
        "diet_tags": allergen_rules.derive_diet_tags(confirmed),
    }


def handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Entry point for ``PATCH /menus/{menuId}/items/{itemId}``.

    Task 3.1 covers the scaffold, body parsing, and validation. It parses the body,
    validates the correction, and hands the validated fields to the allergen filtering
    / tag recomputation (task 3.2) and native conditional ``UpdateItem`` persistence
    (task 3.3), which are wired in by those tasks.
    """
    menu_id, item_id = _path_params(event)

    try:
        body = parse_body(event)
        correction = validate_correction(body)
    except ValidationError as exc:
        return _error(400, exc.message)

    # --- task 3.2 (allergen filtering / tag recomputation) and task 3.3 (native
    # conditional UpdateItem persistence) build on the validated `correction` here. ---
    return apply_correction(menu_id, item_id, correction)


def apply_correction(
    menu_id: Optional[str], item_id: Optional[str], correction: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply a validated correction to a dish and persist it.

    Task 3.2 performs the allergen filtering and tag recomputation: when the correction
    carries ``confirmed_allergens`` it PEAL-filters them (R4.1) and recomputes
    ``display_tags`` / ``diet_tags`` through the real ``allergen_rules`` module (R4.2/R4.6).

    Task 3.3 persists the correction via a native conditional ``UpdateItem`` — merging the
    provided ``translations`` per code while preserving untouched codes (R4.3/R5.3), setting
    ``allergens.confirmed`` / ``allergens.display_tags`` and top-level ``diet_tags`` when
    allergens were corrected, applying optional ``name`` / ``description``, and stamping
    ``status = human_verified`` (R5.1) — guarded by ``attribute_exists(menu_id)`` with
    ``ReturnValues="ALL_NEW"`` (R5.2). A ``ConditionalCheckFailedException`` maps to 404 with
    no stored change (R5.4); success returns the echoed ``ALL_NEW`` attributes as 200 (R5.5).
    """
    # --- task 3.2: allergen filtering + tag recomputation (R4.1 / R4.2 / R4.6) ----------
    # Only recompute when the correction actually carries allergens; a correction limited
    # to translations / name / description leaves the stored allergen fields untouched.
    allergen_fields: Optional[Dict[str, Any]] = None
    if "confirmed_allergens" in correction:
        allergen_fields = recompute_allergen_fields(correction["confirmed_allergens"])

    # --- task 3.3: native conditional UpdateItem persistence (R5.1-R5.6) ----------------
    # `allergen_fields` (when present) supplies the PEAL-filtered `confirmed` plus the
    # recomputed `display_tags` / `diet_tags`. Assemble a single UpdateExpression that
    # merges translations per code (preserving untouched codes), sets the allergen/diet
    # fields, applies optional name/description, and stamps `status = human_verified`,
    # then persist it guarded by `attribute_exists(menu_id)` so a missing dish fails closed.
    update_kwargs = build_update_item_kwargs(menu_id, item_id, correction, allergen_fields)

    try:
        result = _table().update_item(**update_kwargs)
    except Exception as exc:  # noqa: BLE001 - narrow to the conditional-failure below.
        # A `ConditionalCheckFailedException` means the target row did not exist, so the
        # guard rejected the write and nothing was stored -> 404, failing closed (R5.4).
        if _is_conditional_check_failed(exc):
            return _error(404, "dish not found")
        raise

    # ReturnValues="ALL_NEW" echoes the full post-update row back to the caller (R5.5).
    return _response(200, {"item": result.get("Attributes", {})})


def _is_conditional_check_failed(exc: Exception) -> bool:
    """True when ``exc`` is a DynamoDB ``ConditionalCheckFailedException``.

    Detected without importing botocore: boto3 raises a ``ClientError`` whose
    ``response["Error"]["Code"]`` is ``ConditionalCheckFailedException``. Fakes used in
    tests may instead expose a class named ``ConditionalCheckFailedException`` directly,
    so both shapes are accepted.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return True
    return type(exc).__name__ == "ConditionalCheckFailedException"


def build_update_item_kwargs(
    menu_id: Optional[str],
    item_id: Optional[str],
    correction: Dict[str, Any],
    allergen_fields: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the ``table.update_item`` keyword arguments for a validated correction.

    Builds a single ``UpdateExpression`` (R5.2) whose SET clauses cover:

    - each provided ``translations`` code as its own map path
      (``SET translations.#es = :es`` …), which leaves untouched codes intact (R4.3/R5.3);
    - ``allergens.confirmed`` / ``allergens.display_tags`` and top-level ``diet_tags`` when
      the correction carried allergens (R4.1/R4.2);
    - optional ``name`` / ``description`` when present (R4.4/R4.5);
    - ``status = human_verified`` on every applied correction (R5.1).

    Guards the write with ``ConditionExpression = attribute_exists(menu_id)`` and requests
    ``ReturnValues="ALL_NEW"`` so a successful call echoes the updated row (R5.4/R5.5).
    Reserved words (``name``, ``description``, ``status``) and the language-code map keys
    are addressed through expression-attribute *names* to stay collision-free.
    """
    set_clauses: list = []
    expr_names: Dict[str, str] = {}
    expr_values: Dict[str, Any] = {}

    # --- translation merge: one map path per provided code, untouched codes preserved ---
    # Each supported code is set at its own path `translations.<code>` (R4.3/R5.3): only the
    # provided codes are written, so any code already stored under other keys is left intact
    # (a nested-path SET never rewrites its siblings). DynamoDB forbids referencing both a
    # map and a path inside it in one UpdateExpression, so we deliberately do NOT emit a
    # `SET translations = if_not_exists(translations, :empty_map)` clause alongside these
    # nested assignments — that overlap would be rejected. Dish rows produced upstream always
    # carry a `translations` map (see the data model), so the parent path always resolves.
    translations = correction.get("translations")
    if isinstance(translations, dict) and translations:
        expr_names["#translations"] = "translations"
        for code, value in translations.items():
            name_key = f"#tr_{code}"
            value_key = f":tr_{code}"
            expr_names[name_key] = code
            expr_values[value_key] = value
            set_clauses.append(f"#translations.{name_key} = {value_key}")

    # --- allergen fields + diet tags (only when the correction carried allergens) -------
    if allergen_fields is not None:
        expr_names["#allergens"] = "allergens"
        expr_names["#confirmed"] = "confirmed"
        expr_names["#display_tags"] = "display_tags"
        expr_values[":confirmed"] = allergen_fields["confirmed"]
        expr_values[":display_tags"] = allergen_fields["display_tags"]
        expr_values[":diet_tags"] = allergen_fields["diet_tags"]
        set_clauses.append("#allergens.#confirmed = :confirmed")
        set_clauses.append("#allergens.#display_tags = :display_tags")
        set_clauses.append("diet_tags = :diet_tags")

    # --- optional name / description (validated in task 3.1) ----------------------------
    if "name" in correction:
        expr_names["#name"] = "name"
        expr_values[":name"] = correction["name"]
        set_clauses.append("#name = :name")
    if "description" in correction:
        expr_names["#description"] = "description"
        expr_values[":description"] = correction["description"]
        set_clauses.append("#description = :description")

    # --- status = human_verified on every applied correction (R5.1) ---------------------
    expr_names["#status"] = "status"
    expr_values[":status"] = HUMAN_VERIFIED_STATUS
    set_clauses.append("#status = :status")

    update_expression = "SET " + ", ".join(set_clauses)

    return {
        "Key": {"menu_id": menu_id, "item_id": item_id},
        "UpdateExpression": update_expression,
        "ExpressionAttributeNames": expr_names,
        "ExpressionAttributeValues": expr_values,
        "ConditionExpression": "attribute_exists(menu_id)",
        "ReturnValues": "ALL_NEW",
    }
