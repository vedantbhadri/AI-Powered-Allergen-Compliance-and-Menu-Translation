# Feature: menu-read-edit-lambdas, Property 8: Translation merge preserves untouched codes
"""
Property-based test for editMenu's translation-merge semantics.

Property 8 (design.md / tasks.md 6.3):
    For any existing Translations_Map and any partial supported-code update, the
    result equals the existing map overlaid with the update: codes present in the
    request take the request values, codes absent from the request retain their
    prior values.

    Validates: Requirements 4.3, 5.3

How the merge is realised (see the design note in handler.build_update_item_kwargs):
    The handler does NOT persist a whole merged ``translations`` map. Instead it
    emits one nested-path SET clause per provided supported code
    (``SET translations.#tr_es = :tr_es`` …) in the UpdateExpression and
    deliberately AVOIDS any parent ``translations = if_not_exists(...)`` clause,
    because DynamoDB rejects overlapping document paths. The merge (untouched codes
    preserved) is therefore produced by DynamoDB applying only the provided leaf
    paths and leaving sibling keys intact.

So this test asserts, over 100+ generated (existing map, partial update) pairs, that
``build_update_item_kwargs``:
  1. emits exactly one SET clause per provided supported code, keyed at
     ``translations.<code>`` with the request value, and
  2. emits NO clause that rewrites the whole ``translations`` map (no
     ``#translations = ...`` / ``translations = ...`` assignment),
and that *applying* those leaf-path SETs to the existing map reproduces the overlay
merge exactly. DynamoDB is MOCKED — we never contact AWS; the leaf-path application is
a faithful in-memory model of DynamoDB nested-path SET semantics.
"""

from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

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


SUPPORTED = sorted(handler.SUPPORTED_LANGUAGE_CODES)  # ["de", "es", "ja", "zh"]

# Translation values are arbitrary strings (the handler treats them opaquely).
_translation_text = st.text(min_size=0, max_size=40)

# A Translations_Map: an arbitrary subset of the supported codes -> text.
supported_maps = st.dictionaries(
    keys=st.sampled_from(SUPPORTED),
    values=_translation_text,
    max_size=len(SUPPORTED),
)

# A partial update: a NON-empty subset of the supported codes -> text (the handler
# only emits per-code SET clauses when ``translations`` is a non-empty dict).
partial_updates = st.dictionaries(
    keys=st.sampled_from(SUPPORTED),
    values=_translation_text,
    min_size=1,
    max_size=len(SUPPORTED),
)


@contextmanager
def _no_dynamo():
    """Guard: fail loudly if the test path ever tries to touch DynamoDB.

    Property 8 is proven purely at the ``build_update_item_kwargs`` level plus an
    in-memory model of nested-path SET, so no ``update_item`` call should occur.
    Installed/torn down per generated input (not via the ``monkeypatch`` fixture,
    which is not reset between Hypothesis examples).
    """
    original = handler._table
    called = {"hit": False}

    def _boom(*_args, **_kwargs):  # pragma: no cover - only runs if contract breaks
        called["hit"] = True
        raise AssertionError("DynamoDB must not be contacted in Property 8 test")

    handler._table = _boom
    try:
        yield called
    finally:
        handler._table = original


def _apply_leaf_path_sets(existing_map: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Faithfully model DynamoDB applying the UpdateExpression's translation leaf SETs.

    Starts from a copy of ``existing_map`` and, for every SET clause of the form
    ``#translations.#tr_<code> = :tr_<code>``, writes ``resolved_code -> resolved_value``
    where the code comes from ExpressionAttributeNames and the value from
    ExpressionAttributeValues. Sibling keys are left untouched — exactly how a
    DynamoDB nested-path SET behaves. Returns the resulting merged map.
    """
    names = kwargs["ExpressionAttributeNames"]
    values = kwargs["ExpressionAttributeValues"]
    update_expr = kwargs["UpdateExpression"]

    result = dict(existing_map)

    # Grab the SET body (drop the leading "SET ") and split on top-level commas.
    assert update_expr.startswith("SET ")
    clauses = [c.strip() for c in update_expr[len("SET "):].split(",")]

    # Match a translations leaf-path assignment: "#translations.#tr_es = :tr_es".
    leaf_re = re.compile(r"^#translations\.(#tr_[A-Za-z]+)\s*=\s*(:tr_[A-Za-z]+)$")
    for clause in clauses:
        m = leaf_re.match(clause)
        if not m:
            continue
        name_key, value_key = m.group(1), m.group(2)
        code = names[name_key]
        result[code] = values[value_key]
    return result


@settings(max_examples=200)
@given(existing=supported_maps, update=partial_updates)
def test_translation_merge_overlays_and_preserves(existing, update):
    """The kwargs' leaf-path SETs merge the update over the existing map, preserving
    untouched codes (R4.3 / R5.3)."""
    with _no_dynamo() as guard:
        kwargs = handler.build_update_item_kwargs(
            menu_id="menu-1",
            item_id="dish-1",
            correction={"translations": update},
            allergen_fields=None,
        )
    assert guard["hit"] is False  # pure kwargs construction, no AWS

    names = kwargs["ExpressionAttributeNames"]
    values = kwargs["ExpressionAttributeValues"]
    update_expr = kwargs["UpdateExpression"]

    # (1) Exactly one leaf-path SET per provided supported code, keyed correctly.
    leaf_clauses = re.findall(r"#translations\.#tr_[A-Za-z]+ = :tr_[A-Za-z]+", update_expr)
    assert len(leaf_clauses) == len(update)

    for code, value in update.items():
        assert names.get(f"#tr_{code}") == code
        assert values.get(f":tr_{code}") == value

    # (2) NO clause rewrites the whole translations map: the parent path is only ever
    #     used as a prefix (``#translations.<...>``), never assigned on its own.
    #     Guard against both the aliased and the literal forms.
    assert not re.search(r"#translations\s*=", update_expr)
    assert not re.search(r"(^|,|\s)translations\s*=", update_expr)

    # (3) Applying the leaf-path SETs to the existing map yields the overlay merge:
    #     present codes take request values, absent codes retain prior values.
    merged = _apply_leaf_path_sets(existing, kwargs)

    expected = dict(existing)
    expected.update(update)
    assert merged == expected

    # Explicitly: every code NOT in the update retains its prior stored value.
    for code, prior in existing.items():
        if code not in update:
            assert merged[code] == prior
    # And every code in the update takes the request value.
    for code, requested in update.items():
        assert merged[code] == requested


@settings(max_examples=200)
@given(existing=supported_maps)
def test_absent_code_never_written_when_not_in_update(existing):
    """A single-code update touches only that code; all other stored codes survive."""
    if not existing:
        return  # need at least one prior code to have something to preserve

    # Update just one supported code to a sentinel value.
    target = SUPPORTED[0]
    update = {target: "NEW_VALUE"}

    with _no_dynamo():
        kwargs = handler.build_update_item_kwargs(
            menu_id="menu-1",
            item_id="dish-1",
            correction={"translations": update},
            allergen_fields=None,
        )

    merged = _apply_leaf_path_sets(existing, kwargs)

    assert merged[target] == "NEW_VALUE"
    for code, prior in existing.items():
        if code != target:
            assert merged[code] == prior
