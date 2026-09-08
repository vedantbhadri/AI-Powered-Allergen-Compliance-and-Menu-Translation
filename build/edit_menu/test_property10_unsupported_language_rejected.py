# Feature: menu-read-edit-lambdas, Property 10: Unsupported language codes are rejected without mutation
"""
Property-based test for editMenu's language-code validation.

Property 10 (design.md / tasks.md 6.5):
    For any correction whose ``translations`` include at least one language code
    outside the supported set ``{es, de, ja, zh}``, ``PATCH /menus/{menuId}/items/{itemId}``
    responds with HTTP 400, issues NO write to DynamoDB, and returns an error
    identifying the offending unsupported code (the handler returns
    ``{"error": "unsupported language code: <code>"}``).

    Validates: Requirements 4.7

DynamoDB is MOCKED: the handler's ``_table()`` is replaced with a fake whose
``update_item`` raises ``AssertionError`` if it is ever called — so any write
attempt on a rejected request fails the test loudly, proving the "no mutation"
guarantee. No AWS is ever contacted. Minimum 100 iterations.
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

SUPPORTED = handler.SUPPORTED_LANGUAGE_CODES  # {"es", "de", "ja", "zh"}


class _NoWriteTable:
    """Fake DynamoDB Table whose update_item must never be called.

    A rejected correction (Property 10) must issue no write, so any call here is a
    contract violation and fails the test immediately.
    """

    def __init__(self):
        self.update_item_called = False

    def update_item(self, **kwargs):  # noqa: D401 - stub
        self.update_item_called = True
        raise AssertionError(
            "update_item was called on a rejected correction — no write must occur "
            f"(kwargs={kwargs!r})"
        )


@contextmanager
def _mocked_table():
    """Install a no-write fake table for the block's duration, then restore.

    Uses a context manager (not the pytest ``monkeypatch`` fixture) so the mock is
    installed and torn down for EACH Hypothesis-generated input, avoiding the
    function-scoped-fixture health-check pitfall. ``handler._table`` is swapped so the
    handler never touches AWS.
    """
    fake = _NoWriteTable()
    original_table_fn = handler._table
    original_cached = handler._TABLE
    handler._table = lambda: fake
    handler._TABLE = fake
    try:
        yield fake
    finally:
        handler._table = original_table_fn
        handler._TABLE = original_cached


# --- Strategies -----------------------------------------------------------------------
# Unsupported language codes: any non-empty string that is NOT one of the four
# supported codes. Constrain to a readable charset so the counter-examples stay legible.
_CODE_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
unsupported_codes = st.text(alphabet=_CODE_CHARS, min_size=1, max_size=12).filter(
    lambda c: c not in SUPPORTED
)

# Values for a translation entry — arbitrary short strings; content is irrelevant since
# validation rejects on the KEY before any value is read.
translation_values = st.text(min_size=0, max_size=30)


@st.composite
def translations_with_unsupported(draw):
    """Build a ``translations`` dict containing >=1 unsupported code, possibly mixed
    with supported codes, and return ``(translations, offending_codes)``.
    """
    # At least one unsupported code.
    bad_codes = draw(
        st.lists(unsupported_codes, min_size=1, max_size=4, unique=True)
    )
    # Optionally sprinkle in some supported codes (mixed is fine per the task).
    good_codes = draw(
        st.lists(st.sampled_from(sorted(SUPPORTED)), min_size=0, max_size=4, unique=True)
    )

    translations = {}
    for code in good_codes:
        translations[code] = draw(translation_values)
    for code in bad_codes:
        translations[code] = draw(translation_values)

    return translations, set(bad_codes)


def _make_event(translations: dict) -> dict:
    """Minimal API Gateway v2 PATCH event carrying the given translations correction."""
    return {
        "routeKey": "PATCH /menus/{menuId}/items/{itemId}",
        "pathParameters": {"menuId": "menu-1", "itemId": "dish-1"},
        "body": json.dumps({"translations": translations}),
        "isBase64Encoded": False,
    }


@settings(max_examples=200)
@given(payload=translations_with_unsupported())
def test_unsupported_language_code_rejected_without_mutation(payload):
    """Translations with any unsupported code -> 400, no write, error names the code."""
    translations, offending_codes = payload

    with _mocked_table() as fake:
        resp = handler.handler(_make_event(translations))

    # No write was issued (the fake raises if update_item is ever called) (R4.7).
    assert fake.update_item_called is False

    # Rejected with HTTP 400 (R4.7).
    assert resp["statusCode"] == 400

    body = json.loads(resp["body"])
    assert "error" in body
    message = body["error"]

    # The error identifies an unsupported code, in the documented shape
    # {"error": "unsupported language code: <code>"} (R4.7).
    assert message.startswith("unsupported language code: ")
    named_code = message[len("unsupported language code: "):]
    # The named code is one of the offending codes actually present in the request.
    assert named_code in offending_codes
    # And it is genuinely unsupported.
    assert named_code not in SUPPORTED
