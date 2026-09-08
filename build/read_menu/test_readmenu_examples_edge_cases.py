"""
Example / edge-case unit tests for the readMenu handler (task 7.1).

These are concrete-case pytest functions (NOT property tests — no @given), each
pinning a specific documented edge of readMenu's contract:

  - 200 with an empty ``items`` list when a partition holds only the sentinel row
    (R1.3): a restaurant that exists but has no confirmed dishes yet.
  - 404 for a zero-row partition (R3.3): an unknown restaurant.
  - 500 when ``list_items`` raises (R1.6 / R3.6): a read failure on the list route,
    with no partial collection returned.
  - 500 when ``get_item`` raises (R2.7 / R3.6): a read failure on the status route,
    leaving the sentinel unmodified.
  - 404 when the status sentinel is absent (R2.5): an uploadId with no sentinel row.

DynamoDB is MOCKED: the handler's ``dynamo_service`` dependency is monkeypatched
with a small fake exposing only ``list_items`` / ``get_item``. No AWS is contacted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# --- Import wiring: put build/layer/python (for ``services``) and build/read_menu
#     on sys.path, mirroring the Lambda runtime layout. Both handlers are named
#     ``handler`` module-wide, so load THIS package's handler from its explicit
#     file path under a unique module name to avoid a cross-directory collision
#     when editMenu's handler is imported in the same pytest run. ---
_HERE = Path(__file__).resolve().parent            # build/read_menu
_BUILD = _HERE.parent                              # build
_LAYER_PYTHON = _BUILD / "layer" / "python"        # build/layer/python

for _p in (str(_LAYER_PYTHON), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location("read_menu_handler", _HERE / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)


class _FakeDynamo:
    """Minimal fake dynamo_service exposing only the two read functions.

    ``list_items`` / ``get_item`` return canned data, or raise when configured to
    inject a read failure. Only read methods exist, so readMenu stays read-only.
    """

    def __init__(self, *, list_result=None, get_result=None,
                 fail_list=False, fail_get=False):
        self._list_result = list_result if list_result is not None else []
        self._get_result = get_result
        self._fail_list = fail_list
        self._fail_get = fail_get

    def list_items(self, menu_id):
        if self._fail_list:
            raise RuntimeError("injected list_items read failure")
        return self._list_result

    def get_item(self, menu_id, item_id):
        if self._fail_get:
            raise RuntimeError("injected get_item read failure")
        return self._get_result


@pytest.fixture
def install_dynamo(monkeypatch):
    """Return an installer that swaps handler.dynamo_service for a fake."""
    def _install(fake):
        monkeypatch.setattr(handler, "dynamo_service", fake)
        return fake
    return _install


def _list_event(restaurant_id):
    return {
        "routeKey": handler.ROUTE_LIST_MENU,
        "pathParameters": {"restaurantId": restaurant_id},
    }


def _status_event(upload_id):
    return {
        "routeKey": handler.ROUTE_UPLOAD_STATUS,
        "pathParameters": {"uploadId": upload_id},
    }


def _body(response):
    return json.loads(response["body"])


# --- readMenu list route ------------------------------------------------------

def test_list_sentinel_only_partition_returns_200_empty_items(install_dynamo):
    """A partition holding ONLY the upload sentinel returns 200 with empty items (R1.3).

    The restaurant exists (>=1 row), but every dish has been excluded because the
    only row is the ``upload#`` sentinel — a valid empty dish collection, not a 404.
    """
    restaurant = "kiwi-cafe-queenstown"
    sentinel_only = [
        {
            "menu_id": restaurant,
            "item_id": f"upload#{restaurant}",
            "status": "ready",
        }
    ]
    install_dynamo(_FakeDynamo(list_result=sentinel_only))

    response = handler.handler(_list_event(restaurant))

    assert response["statusCode"] == 200
    body = _body(response)
    assert body == {"items": []}


def test_list_zero_row_partition_returns_404(install_dynamo):
    """An empty partition (unknown restaurant) returns 404 (R3.3)."""
    install_dynamo(_FakeDynamo(list_result=[]))

    response = handler.handler(_list_event("no-such-restaurant"))

    assert response["statusCode"] == 404
    assert _body(response) == {"error": "restaurant not found"}


def test_list_read_failure_returns_500_no_partial(install_dynamo):
    """A raising ``list_items`` returns 500 with no partial collection (R1.6 / R3.6)."""
    install_dynamo(_FakeDynamo(fail_list=True))

    response = handler.handler(_list_event("kiwi-cafe-queenstown"))

    assert response["statusCode"] == 500
    body = _body(response)
    assert body == {"error": "menu could not be retrieved"}
    assert "items" not in body  # no partial collection returned


# --- readMenu status route ----------------------------------------------------

def test_status_get_item_failure_returns_500(install_dynamo):
    """A raising ``get_item`` on the status route returns 500 (R2.7 / R3.6).

    The read failed, so no status is reported and the sentinel is left unmodified.
    """
    install_dynamo(_FakeDynamo(fail_get=True))

    response = handler.handler(_status_event("upload-abc123"))

    assert response["statusCode"] == 500
    assert _body(response) == {"error": "status could not be retrieved"}


def test_status_absent_sentinel_returns_404(install_dynamo):
    """An uploadId whose sentinel row is absent returns 404 (R2.5)."""
    install_dynamo(_FakeDynamo(get_result=None))

    response = handler.handler(_status_event("upload-missing"))

    assert response["statusCode"] == 404
    assert _body(response) == {"error": "uploadId not found"}
