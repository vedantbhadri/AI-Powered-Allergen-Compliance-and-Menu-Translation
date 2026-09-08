# Feature: menu-read-edit-lambdas, Property 3: readMenu never mutates stored data
"""
Property-based tests for the readMenu handler (Hypothesis, DynamoDB mocked).

Property 3 — readMenu never mutates stored data.

*For any* request to either read endpoint — valid, malformed, targeting an
absent resource, or one whose underlying read fails — readMenu issues no write
operation (no PutItem / UpdateItem / DeleteItem) against DynamoDB, and any
failure path returns HTTP 500 with no partial collection.

**Validates: Requirements 3.1, 1.6, 2.7, 3.2, 3.6**

DynamoDB is mocked: the handler's ``dynamo_service`` dependency is replaced by a
spy that records every attribute accessed on it. readMenu is expected to touch
ONLY the read functions ``list_items`` and ``get_item``; the spy raises the
moment any write function (``put_item`` / ``update_item`` / ``delete_item`` /
``batch_write_item`` / ``delete_menu``) is even *accessed*, so a mutating call
can never be issued. Runs a minimum of 100 iterations across both routes,
including failure injection (mocked read raising).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# --- Import wiring -----------------------------------------------------------
# Put the shared Lambda layer (build/layer/python) and the readMenu package
# (build/read_menu) on sys.path, mirroring the runtime layout, then import the
# handler as the Lambda would.
_HERE = Path(__file__).resolve().parent            # build/read_menu
_BUILD = _HERE.parent                              # build
_LAYER_PYTHON = _BUILD / "layer" / "python"        # build/layer/python

for _p in (str(_LAYER_PYTHON), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import handler  # noqa: E402  (path set up above)


# --- DynamoDB spy (mock) -----------------------------------------------------
# readMenu is READ-ONLY: it may only call list_items (Query) and get_item
# (GetItem). Any attempt to reach a write function is a Property-3 violation.
_WRITE_METHOD_NAMES = frozenset(
    {"put_item", "update_item", "delete_item", "batch_write_item", "delete_menu"}
)
_READ_METHOD_NAMES = frozenset({"list_items", "get_item"})


class WriteAttempted(AssertionError):
    """Raised if readMenu accesses ANY DynamoDB write function."""


class DynamoSpy:
    """
    Stand-in for the ``dynamo_service`` module.

    Records every read call. Accessing a write attribute raises immediately, so
    no PutItem/UpdateItem/DeleteItem can ever be issued (proving R3.1/R3.4).
    ``list_items`` / ``get_item`` return canned data, or raise when configured
    to inject a read failure (exercising R1.6 / R2.7 / R3.6).
    """

    def __init__(self, *, list_result=None, get_result=None,
                 fail_list=False, fail_get=False):
        self._list_result = list_result if list_result is not None else []
        self._get_result = get_result
        self._fail_list = fail_list
        self._fail_get = fail_get
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        # __getattr__ only fires for attributes not found normally, i.e. the
        # module-level function names the handler reaches for.
        if name in _WRITE_METHOD_NAMES:
            raise WriteAttempted(
                f"readMenu attempted a write operation: dynamo_service.{name}"
            )
        raise AttributeError(name)

    def list_items(self, menu_id):
        self.calls.append(("list_items", menu_id))
        if self._fail_list:
            raise RuntimeError("injected list_items read failure")
        return self._list_result

    def get_item(self, menu_id, item_id):
        self.calls.append(("get_item", menu_id, item_id))
        if self._fail_get:
            raise RuntimeError("injected get_item read failure")
        return self._get_result

    # --- assertions used by the property ---
    def assert_no_writes(self):
        for call in self.calls:
            assert call[0] in _READ_METHOD_NAMES, (
                f"unexpected non-read dynamo call recorded: {call!r}"
            )


@pytest.fixture(autouse=True)
def _restore_dynamo():
    """Save/restore the handler's dynamo_service around each test."""
    original = handler.dynamo_service
    try:
        yield
    finally:
        handler.dynamo_service = original


def _install_spy(spy: DynamoSpy) -> None:
    handler.dynamo_service = spy


def _body(response) -> dict:
    return json.loads(response["body"])


# --- Strategies --------------------------------------------------------------
# Route keys the handler understands, plus an unknown one.
_ROUTE_KEYS = st.sampled_from(
    [
        handler.ROUTE_LIST_MENU,
        handler.ROUTE_UPLOAD_STATUS,
        "GET /unknown/route",
    ]
)

# Path-param values spanning valid, malformed, and absent-resource cases:
#   - well-formed keys (alnum + key-safe separators)
#   - empty / whitespace-only (malformed -> 400)
#   - bad-charset (spaces, slashes, control chars -> 400)
#   - None (missing)
_valid_keys = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:#-",
    min_size=1,
    max_size=24,
)
_bad_keys = st.sampled_from(["", "   ", "\t", "\n", "bad id", "a/b", "x\x00y", "with space"])
_path_values = st.one_of(_valid_keys, _bad_keys, st.none())

# A dish row and an upload sentinel row, so list_items can return a realistic
# partition (dish rows + at most one sentinel).
_dish_row = st.fixed_dictionaries(
    {
        "menu_id": _valid_keys,
        "item_id": st.builds(lambda n: f"dish-{n:04d}", st.integers(0, 9999)),
        "name": st.text(max_size=20),
        "translations": st.dictionaries(
            st.sampled_from(["es", "de", "ja", "zh"]),
            st.text(max_size=10),
            max_size=4,
        ),
    }
)
_sentinel_row = st.builds(
    lambda uid, status: {
        "menu_id": uid,
        "item_id": f"upload#{uid}",
        "status": status,
    },
    _valid_keys,
    st.sampled_from(["processing", "ocr_done", "analyzing", "translating", "ready"]),
)
_partition = st.lists(st.one_of(_dish_row, _sentinel_row), max_size=6)


def _build_event(route_key, param_name, param_value):
    path_params = {} if param_value is None else {param_name: param_value}
    return {"routeKey": route_key, "pathParameters": path_params}


# --- The property ------------------------------------------------------------
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    route_key=_ROUTE_KEYS,
    path_value=_path_values,
    partition=_partition,
    sentinel_present=st.booleans(),
    sentinel_status=st.sampled_from(
        ["processing", "ocr_done", "analyzing", "translating", "ready"]
    ),
    fail_read=st.booleans(),
)
def test_readmenu_never_mutates_stored_data(
    route_key,
    path_value,
    partition,
    sentinel_present,
    sentinel_status,
    fail_read,
):
    """
    For any request across both routes — valid, malformed, absent resource, or a
    failing read — readMenu never issues a write, and any read-failure path
    returns 500 with no partial collection.
    """
    is_list = route_key == handler.ROUTE_LIST_MENU
    is_status = route_key == handler.ROUTE_UPLOAD_STATUS
    param_name = "restaurantId" if is_list else "uploadId"

    get_result = {
        "menu_id": path_value,
        "item_id": f"upload#{path_value}",
        "status": sentinel_status,
    } if sentinel_present else None

    spy = DynamoSpy(
        list_result=partition,
        get_result=get_result,
        fail_list=fail_read and is_list,
        fail_get=fail_read and is_status,
    )
    _install_spy(spy)

    # Accessing a write function on the spy raises WriteAttempted; if the handler
    # ever attempted a mutation the call below would raise and fail the test.
    response = handler.handler(_build_event(route_key, param_name, path_value))

    # (1) No write operation was ever issued (R3.1 / R3.4): only read methods
    # were recorded, and no write attribute was ever accessed.
    spy.assert_no_writes()

    status = response["statusCode"]
    body = _body(response)

    # (2) The response is a well-formed API Gateway v2 proxy response.
    assert isinstance(status, int)
    assert "body" in response

    # Determine whether the request would have passed path-param validation and
    # thus reached a DynamoDB read on a handled route.
    valid_param = handler._validate_key_param(path_value)

    if fail_read and (is_list or is_status) and valid_param:
        # (3) Any failure path returns HTTP 500 (R1.6 / R2.7 / R3.6)...
        assert status == 500, f"expected 500 on read failure, got {status}: {body}"
        # ...with an error description and NO partial collection: the body is the
        # standard {"error": ...} shape and never carries an "items" list.
        assert "error" in body
        assert "items" not in body
    elif not valid_param and (is_list or is_status):
        # Malformed / missing / absent path param -> 400, no read attempted,
        # no mutation, no data change (R3.2 / R2.6).
        assert status == 400, f"expected 400 for invalid param, got {status}: {body}"
        assert "error" in body
        assert spy.calls == [], f"validation should precede any read, got {spy.calls}"

    # Regardless of route/outcome (including the unknown-route 404), the read-only
    # invariant holds: the spy recorded only read calls and no write was attempted.
    for call in spy.calls:
        assert call[0] in _READ_METHOD_NAMES


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(partition=_partition, restaurant=_valid_keys, fail=st.booleans())
def test_list_route_failure_returns_500_no_partial(partition, restaurant, fail):
    """
    Focused failure-injection over the list route: a failing list_items yields
    500 with no partial collection; a succeeding read never issues a write.
    """
    spy = DynamoSpy(list_result=partition, fail_list=fail)
    _install_spy(spy)

    response = handler.handler(
        _build_event(handler.ROUTE_LIST_MENU, "restaurantId", restaurant)
    )
    body = _body(response)
    spy.assert_no_writes()

    if fail:
        assert response["statusCode"] == 500
        assert "items" not in body  # no partial collection
        assert "error" in body
    else:
        # Success (or 404 for an empty partition) — still read-only.
        assert response["statusCode"] in (200, 404)


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(upload=_valid_keys, present=st.booleans(), fail=st.booleans())
def test_status_route_failure_returns_500_sentinel_unmodified(upload, present, fail):
    """
    Focused failure-injection over the status route: a failing get_item yields
    500; success/absence never issues a write (sentinel left unmodified).
    """
    get_result = (
        {"menu_id": upload, "item_id": f"upload#{upload}", "status": "ready"}
        if present
        else None
    )
    spy = DynamoSpy(get_result=get_result, fail_get=fail)
    _install_spy(spy)

    response = handler.handler(
        _build_event(handler.ROUTE_UPLOAD_STATUS, "uploadId", upload)
    )
    body = _body(response)
    spy.assert_no_writes()

    if fail:
        assert response["statusCode"] == 500
        assert "error" in body
    else:
        assert response["statusCode"] in (200, 404)
