# Feature: menu-read-edit-lambdas, Property 4: Status endpoint faithfully returns the stored status
"""
Property-based test for readMenu's ``GET /menus/{uploadId}/status`` endpoint.

Property 4 (design.md / tasks.md 5.4):
    For any upload-status sentinel with an arbitrary stored ``status`` value
    (including the in-progress states ``processing``, ``ocr_done``,
    ``analyzing``, ``translating`` and the terminal state ``ready``),
    ``GET /menus/{uploadId}/status`` reads it via
    ``get_item(uploadId, "upload#"+uploadId)`` and returns exactly that stored
    value with HTTP 200; and for any uploadId with no sentinel, it returns HTTP
    404 with no status value.

    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5

DynamoDB is MOCKED: ``dynamo_service.get_item`` is monkeypatched to a stub that
serves an in-memory sentinel (or ``None``) and records the exact key arguments
it was called with. No AWS is ever contacted. Minimum 100 iterations.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# --- Import wiring: put build/layer/python (for ``services``) and build/read_menu
#     (for ``handler``) on sys.path, matching the Lambda runtime layout. ---
_READ_MENU_DIR = Path(__file__).resolve().parent          # build/read_menu
_BUILD_DIR = _READ_MENU_DIR.parent                        # build
_LAYER_PYTHON_DIR = _BUILD_DIR / "layer" / "python"       # build/layer/python

for _p in (str(_LAYER_PYTHON_DIR), str(_READ_MENU_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import handler  # noqa: E402  (build/read_menu/handler.py)
from services import dynamo_service  # noqa: E402  (shared layer)


# Path-param charset the handler accepts (^[A-Za-z0-9._:#-]+$) — generate valid,
# non-empty, non-whitespace uploadIds so validation passes and retrieval runs.
_KEY_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
upload_ids = st.text(alphabet=_KEY_CHARS, min_size=1, max_size=40)

# Arbitrary stored status values, including the documented in-progress and
# terminal states plus free-form strings — the endpoint must return whatever is
# stored verbatim, never interpreting it.
_KNOWN_STATES = ["processing", "ocr_done", "analyzing", "translating", "ready"]
status_values = st.one_of(
    st.sampled_from(_KNOWN_STATES),
    st.text(min_size=0, max_size=50),
)


def _make_event(upload_id: str) -> dict:
    """Minimal API Gateway v2 event for the status route."""
    return {
        "routeKey": handler.ROUTE_UPLOAD_STATUS,
        "pathParameters": {"uploadId": upload_id},
    }


class _GetItemSpy:
    """Stub for dynamo_service.get_item that serves one sentinel and records calls."""

    def __init__(self, sentinel):
        self._sentinel = sentinel  # dict to return, or None for "absent"
        self.calls = []

    def __call__(self, menu_id, item_id):
        self.calls.append({"menu_id": menu_id, "item_id": item_id})
        return self._sentinel


@contextmanager
def _mocked_get_item(sentinel):
    """Swap dynamo_service.get_item for a recording stub for the block's duration.

    Used instead of the function-scoped ``monkeypatch`` fixture so the mock is
    installed and torn down for EACH Hypothesis-generated input (the fixture is
    not reset between generated inputs). No AWS is contacted.
    """
    spy = _GetItemSpy(sentinel)
    original = dynamo_service.get_item
    dynamo_service.get_item = spy
    try:
        yield spy
    finally:
        dynamo_service.get_item = original


@settings(max_examples=200)
@given(upload_id=upload_ids, status=status_values)
def test_status_returned_verbatim_when_sentinel_present(upload_id, status):
    """Sentinel present -> 200 with the stored status returned verbatim, read via
    get_item(uploadId, "upload#"+uploadId)."""
    sentinel = {
        "menu_id": upload_id,
        "item_id": "upload#" + upload_id,
        "record_type": "upload_status",
        "status": status,
        "updated_at": 1730000000,
    }
    with _mocked_get_item(sentinel) as spy:
        resp = handler.handler(_make_event(upload_id))

    # Read exactly once, keyed on menu_id = uploadId, item_id = "upload#"+uploadId (R2.1).
    assert len(spy.calls) == 1
    assert spy.calls[0] == {"menu_id": upload_id, "item_id": "upload#" + upload_id}

    # 200 with the stored status echoed verbatim (R2.2, R2.3, R2.4).
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["uploadId"] == upload_id
    # Faithful: the returned status equals the stored one exactly, uninterpreted.
    assert body["status"] == status


@settings(max_examples=200)
@given(upload_id=upload_ids)
def test_absent_sentinel_yields_404_with_no_status(upload_id):
    """No sentinel for the uploadId -> 404 and no status value in the body (R2.5)."""
    with _mocked_get_item(None) as spy:
        resp = handler.handler(_make_event(upload_id))

    # Retrieval was attempted via the sentinel key.
    assert len(spy.calls) == 1
    assert spy.calls[0] == {"menu_id": upload_id, "item_id": "upload#" + upload_id}

    # 404 with an error body carrying no status value (R2.5).
    assert resp["statusCode"] == 404
    body = json.loads(resp["body"])
    assert "status" not in body
    assert "error" in body
