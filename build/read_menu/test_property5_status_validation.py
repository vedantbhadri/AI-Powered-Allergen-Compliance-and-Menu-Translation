# Feature: menu-read-edit-lambdas, Property 5: Status requests validate the uploadId before any retrieval
"""
Property 5 — status requests validate the uploadId before any retrieval.

*For any* missing, empty, or whitespace-only uploadId, ``GET /menus/{uploadId}/status``
responds HTTP 400 and performs **no** ``get_item`` retrieval (the mocked
``dynamo_service.get_item`` must never be called).

DynamoDB is mocked: ``dynamo_service.get_item`` is replaced with a spy that records
every call and raises if invoked, so we can assert the handler never reached the
retrieval step. No AWS access is performed.

Runs a minimum of 100 iterations (Hypothesis default max_examples is raised to 200
here to comfortably clear the floor), generating three disjoint families of invalid
uploadIds: the missing/None case, the empty string, and arbitrary whitespace-only
strings.

**Validates: Requirements 2.6**
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# --- make the shared layer and the handler importable (see env notes) ---------
_THIS_DIR = Path(__file__).resolve().parent            # build/read_menu
_BUILD_DIR = _THIS_DIR.parent                          # build
_LAYER_PYTHON = _BUILD_DIR / "layer" / "python"        # build/layer/python (services pkg)

for _p in (str(_LAYER_PYTHON), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import handler  # noqa: E402  (build/read_menu/handler.py)


# Unicode whitespace characters that ``str.strip()`` treats as blank. Building
# whitespace-only strings from this alphabet exercises the "whitespace-only"
# branch beyond the plain ASCII space.
_WHITESPACE_CHARS = " \t\n\r\v\f\u00a0\u2003\u3000"


def _status_event(upload_id):
    """
    Build a minimal API Gateway v2 event for GET /menus/{uploadId}/status.

    When ``upload_id`` is None the ``uploadId`` path parameter is omitted entirely,
    modelling the "missing" case; otherwise it is set to the provided value.
    """
    path_params = {} if upload_id is None else {"uploadId": upload_id}
    return {
        "routeKey": handler.ROUTE_UPLOAD_STATUS,
        "pathParameters": path_params,
    }


# Missing (None + omitted param), empty string, and whitespace-only strings.
_invalid_upload_ids = st.one_of(
    st.none(),
    st.just(""),
    st.text(alphabet=_WHITESPACE_CHARS, min_size=1, max_size=12),
)


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(upload_id=_invalid_upload_ids)
def test_missing_empty_whitespace_uploadid_returns_400_without_retrieval(upload_id):
    """
    For any missing/empty/whitespace-only uploadId the status route returns 400 and
    never calls dynamo_service.get_item (validation happens before any retrieval).
    """
    def _fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(
            "dynamo_service.get_item must NOT be called for an invalid uploadId "
            f"(called with args={args!r} kwargs={kwargs!r})"
        )

    with mock.patch.object(
        handler.dynamo_service, "get_item", side_effect=_fail_if_called
    ) as spy:
        response = handler.handler(_status_event(upload_id))

    # No retrieval was attempted.
    assert spy.call_count == 0, "get_item was called despite an invalid uploadId"

    # Responds 400 with the documented error body (R2.6).
    assert response["statusCode"] == 400, (
        f"expected 400 for invalid uploadId {upload_id!r}, got {response['statusCode']}"
    )
    import json

    body = json.loads(response["body"])
    assert body == {"error": "uploadId is required"}, (
        f"unexpected error body for {upload_id!r}: {body!r}"
    )
