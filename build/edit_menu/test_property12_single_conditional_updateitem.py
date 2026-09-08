# Feature: menu-read-edit-lambdas, Property 12: Persistence is a single conditional UpdateItem, never put_item
"""
Property-based test for editMenu's persistence discipline.

Property 12 (design.md / tasks.md 6.7):
    For any correction request, the only possible write is exactly one native
    ``UpdateItem`` whose ``ConditionExpression`` is ``attribute_exists(menu_id)``;
    the handler NEVER issues ``dynamo_service.put_item`` (it uses ``boto3``
    directly), and a validation-rejected request issues ZERO writes.

    Validates: Requirements 5.2, 5.6

This is checked three ways:

1. Valid corrections (arbitrary combinations of the four correctable fields, all
   in-bounds) -> exactly ONE ``update_item`` call, its kwargs bind
   ``ConditionExpression == "attribute_exists(menu_id)"``, and neither
   ``put_item`` nor ``delete_item`` is ever invoked on the fake table.
2. Invalid / rejected corrections (empty body, unsupported language code,
   out-of-bounds name / description) -> HTTP 400 and ZERO writes of any kind.
3. A static guarantee: the handler module never references ``dynamo_service``
   (no import, no ``put_item`` path) - it talks to DynamoDB with ``boto3`` only.

DynamoDB is MOCKED end-to-end: ``handler._table`` is swapped for a fake whose
``update_item`` records its kwargs and whose ``put_item`` / ``delete_item`` RAISE
if ever touched, so any non-UpdateItem write fails the test loudly. No AWS is ever
contacted. Minimum 100 iterations per property.
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
_SUPPORTED_CODES = sorted(handler.SUPPORTED_LANGUAGE_CODES)  # es, de, ja, zh
_UNSUPPORTED_CODES = ["en", "fr", "it", "ko", "ru", "pt", "ZH", "Es", "xx"]


class _PutItemForbidden(AssertionError):
    """Raised if editMenu ever attempts a put_item / delete_item write."""


class _FakeTable:
    """Fake DynamoDB Table: records update_item, forbids any other write.

    ``update_item`` captures its kwargs and echoes an ``ALL_NEW`` row. ``put_item``
    and ``delete_item`` raise :class:`_PutItemForbidden` so that the single-native-
    UpdateItem guarantee (R5.2/R5.6) is enforced - if the handler ever reached for a
    put/delete, the test fails immediately.
    """

    def __init__(self):
        self.update_calls = []

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        return {"Attributes": {"menu_id": kwargs["Key"]["menu_id"], "status": "human_verified"}}

    def put_item(self, **kwargs):  # pragma: no cover - must never be called
        raise _PutItemForbidden("editMenu must never call put_item")

    def delete_item(self, **kwargs):  # pragma: no cover - must never be called
        raise _PutItemForbidden("editMenu must never call delete_item")


@contextmanager
def _mocked_table():
    """Swap handler._table for a fresh fake for the block's duration (per input).

    Installed and torn down for EACH Hypothesis-generated input so state never
    leaks between examples. No AWS contacted.
    """
    fake = _FakeTable()
    original = handler._table
    handler._table = lambda: fake
    try:
        yield fake
    finally:
        handler._table = original


def _make_event(menu_id, item_id, body):
    """Minimal API Gateway v2 event carrying a JSON correction body."""
    return {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": json.dumps(body),
    }


# --- Strategies for VALID correction bodies (at least one in-bounds field) ----------
_valid_allergens = st.lists(st.sampled_from(_PEAL + ["Kryptonite", "Water"]), max_size=8)
_valid_translations = st.dictionaries(
    keys=st.sampled_from(_SUPPORTED_CODES),
    values=st.text(max_size=30),
    max_size=4,
)
_valid_name = st.text(min_size=1, max_size=200)
_valid_description = st.text(min_size=0, max_size=2000)


@st.composite
def _valid_correction_bodies(draw):
    """A correction body with a nonempty subset of the four fields, all in-bounds."""
    include_allergens = draw(st.booleans())
    include_translations = draw(st.booleans())
    include_name = draw(st.booleans())
    include_description = draw(st.booleans())

    body = {}
    if include_allergens:
        body["confirmed_allergens"] = draw(_valid_allergens)
    if include_translations:
        body["translations"] = draw(_valid_translations)
    if include_name:
        body["name"] = draw(_valid_name)
    if include_description:
        body["description"] = draw(_valid_description)

    # Guarantee at least one correctable field is present so the body is VALID.
    if not body:
        body["name"] = draw(_valid_name)
    return body


# --- Strategies for INVALID / rejected correction bodies ----------------------------
_empty_bodies = st.just({})

_unsupported_translation_bodies = st.builds(
    lambda code, value: {"translations": {code: value}},
    st.sampled_from(_UNSUPPORTED_CODES),
    st.text(max_size=20),
)

_bad_name_bodies = st.one_of(
    st.just({"name": ""}),                                   # too short (0 < 1)
    st.builds(lambda n: {"name": n}, st.text(min_size=201, max_size=260)),  # too long
)

_bad_description_bodies = st.builds(
    lambda d: {"description": d}, st.text(min_size=2001, max_size=2100)      # too long
)

_invalid_correction_bodies = st.one_of(
    _empty_bodies,
    _unsupported_translation_bodies,
    _bad_name_bodies,
    _bad_description_bodies,
)


@settings(max_examples=200)
@given(body=_valid_correction_bodies())
def test_valid_correction_issues_exactly_one_conditional_updateitem(body):
    """A valid correction persists via exactly one conditional UpdateItem (R5.2).

    Asserts: HTTP 200, exactly one ``update_item`` call, its
    ``ConditionExpression == "attribute_exists(menu_id)"``, and no ``put_item`` /
    ``delete_item`` was ever attempted on the fake table.
    """
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("menu-1", "dish-1", body))

    assert resp["statusCode"] == 200
    # Exactly one native UpdateItem - never zero, never more, never a put/delete.
    assert len(fake.update_calls) == 1

    kwargs = fake.update_calls[0]
    assert kwargs["ConditionExpression"] == "attribute_exists(menu_id)"
    # ReturnValues="ALL_NEW" per R5.5, and the key is addressed by menu_id/item_id.
    assert kwargs["ReturnValues"] == "ALL_NEW"
    assert set(kwargs["Key"]) == {"menu_id", "item_id"}


@settings(max_examples=200)
@given(body=_invalid_correction_bodies)
def test_rejected_correction_issues_zero_writes(body):
    """A validation-rejected correction issues ZERO writes of any kind (R5.6).

    Asserts: HTTP 400 and that ``update_item`` was never called (and by
    construction of the fake, no ``put_item`` / ``delete_item`` either).
    """
    with _mocked_table() as fake:
        resp = handler.handler(_make_event("menu-1", "dish-1", body))

    assert resp["statusCode"] == 400
    assert len(fake.update_calls) == 0


def test_handler_never_references_dynamo_service():
    """editMenu talks to DynamoDB via boto3 directly - never dynamo_service (R5.2/R5.6).

    A static guarantee complementing the runtime properties above: the handler
    module never *imports* ``dynamo_service`` and has no executable ``put_item``
    call path. Both are proven from the parsed AST rather than a substring scan,
    so the handler's prose docstrings (which spell out *why* it avoids
    ``dynamo_service.put_item``) don't produce false positives.
    """
    import ast

    # dynamo_service is not bound as a module attribute (never imported).
    assert not hasattr(handler, "dynamo_service")

    tree = ast.parse(Path(handler.__file__).read_text(encoding="utf-8"))

    # No import of a `dynamo_service` name/module anywhere in the module.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all("dynamo_service" not in alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "dynamo_service" not in module
            assert all(alias.name != "dynamo_service" for alias in node.names)

    # No attribute access named `dynamo_service` and no `put_item` call/attribute
    # in the actual code (comments and docstrings are not AST attribute accesses).
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("dynamo_service", "put_item")
        if isinstance(node, ast.Name):
            assert node.id != "dynamo_service"
