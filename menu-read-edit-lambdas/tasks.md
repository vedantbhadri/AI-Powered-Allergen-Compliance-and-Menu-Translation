# Implementation Plan: readMenu and editMenu

## Overview

This plan implements two AWS Lambda functions — **readMenu** (read-only menu view +
upload-status polling) and **editMenu** (human-in-the-loop dish correction) — plus the
shared infrastructure they depend on: a Lambda Layer holding byte-for-byte copies of the
existing `app/services/*.py` modules, and the Terraform for the layer, the two functions,
their scoped IAM roles, and the API Gateway HTTP API v2 routes.

Implementation language: **Python** (boto3 for AWS access, Hypothesis for property-based
tests), matching the design document.

The four upstream producers (upload handling, OCR/text extraction, Bedrock allergen
detection, translation generation) are **out of scope**. The only contract consumed from
them is the upload-status sentinel shape (`menu_id`, `item_id = "upload#"+uploadId`,
`status`), which readMenu reads verbatim and never mutates.

Work is ordered so the shared layer and both handlers exist and are importable before any
tests are written, and Terraform comes after the handlers are testable.

## Tasks

- [x] 1. Package the shared Lambda Layer (verbatim service modules)
  - Create the `build/layer/python/services/` directory structure.
  - Write a repeatable copy script (`build/build_layer.py` or `build/build_layer.sh`) that
    copies every file from `app/services/*.py` into `build/layer/python/services/`
    **byte-for-byte, with no edits** (including `__init__.py`, `allergen_rules.py`,
    `dynamo_service.py`, `bedrock_service.py`, `s3_service.py`, `textract_service.py`).
  - The copy step performs no transformation so the unmodified-module guarantee holds and
    the two functions cannot drift; handlers import as `from services import ...`.
  - Run the script once to materialize the layer contents.
  - _Requirements: 3.4, 4.6_

  - [x]* 1.1 Write layer packaging smoke test
    - Assert each copied module under `build/layer/python/services/` is **byte-for-byte
      identical** to its source in `app/services/` (hash/content equality).
    - Assert the layer contents are importable as `from services import dynamo_service`
      and `from services import allergen_rules` when `build/layer/python` is on `sys.path`.
    - This is packaging verification, not a property test.
    - _Requirements: 3.4, 4.6_

- [x] 2. Implement the readMenu handler (`build/read_menu/handler.py`)
  - [x] 2.1 Handler scaffold, routeKey dispatch, and path-param validation
    - Create `build/read_menu/handler.py` importing `from services import dynamo_service`.
    - Branch on the API Gateway v2 `routeKey` to dispatch between
      `GET /menus/{restaurantId}` and `GET /menus/{uploadId}/status` (dispatch by
      `routeKey`, never by parsing the raw path).
    - Add a shared path-param validator: reject empty, whitespace-only, or bad-charset
      values with HTTP 400 before any DynamoDB call; standard error body
      `{"error": "<description>"}`.
    - Enforce read-only: no PutItem/UpdateItem/DeleteItem anywhere in this module.
    - _Requirements: 3.1, 3.2, 3.4_

  - [x] 2.2 Implement `GET /menus/{restaurantId}` list path
    - Call `dynamo_service.list_items(restaurantId)` (single Query).
    - Zero rows in partition → 404 `{"error": "restaurant not found"}`.
    - ≥1 row → 200 `{"items": [...]}`, excluding every row whose `item_id` begins with
      `"upload#"` (the sentinel); an empty dish collection is valid (200-empty).
    - Include each dish's embedded Translations_Map unchanged (no per-translation query),
      and annotate each absent code among `{es, de, ja, zh}` as unavailable without
      fabricating content.
    - On `list_items` failure → 500 `{"error": "menu could not be retrieved"}` with no
      partial collection returned.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.3, 3.6_

  - [x] 2.3 Implement `GET /menus/{uploadId}/status` path
    - Missing/empty/whitespace `uploadId` → 400 `{"error": "uploadId is required"}` with
      **no** retrieval performed; bad-charset → 400 `{"error": "invalid uploadId"}`.
    - Call `dynamo_service.get_item(menu_id=uploadId, item_id="upload#"+uploadId)`.
    - Sentinel found → 200 `{"uploadId": ..., "status": ...}` returning the stored status
      value verbatim (never interpreted or advanced).
    - Sentinel absent → 404 `{"error": "uploadId not found"}`.
    - `get_item` read error → 500 `{"error": "status could not be retrieved"}` leaving the
      sentinel unmodified.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.6_

- [x] 3. Implement the editMenu handler (`build/edit_menu/handler.py`)
  - [x] 3.1 Handler scaffold, body parsing, and validation
    - Create `build/edit_menu/handler.py` importing `from services import allergen_rules`
      and using `boto3` directly for DynamoDB (no `dynamo_service`).
    - Parse the `PATCH /menus/{menuId}/items/{itemId}` JSON body (base64-decode if flagged)
      with optional fields `confirmed_allergens?`, `translations?`, `name?`, `description?`.
    - Reject an empty correction (none of the four fields present) → 400
      `{"error": "no correctable field provided"}` with no write.
    - Reject `translations` containing any language code outside `{es, de, ja, zh}` → 400
      `{"error": "unsupported language code: <code>"}` with no write.
    - Reject `name` outside 1–200 chars → 400 `{"error": "invalid name"}`; reject
      `description` outside 0–2000 chars → 400 `{"error": "invalid description"}`; no write.
    - _Requirements: 4.4, 4.5, 4.7, 4.8_

  - [x] 3.2 Implement allergen filtering and tag recomputation
    - Filter `confirmed_allergens` to members of `allergen_rules.PEAL_CATEGORIES`,
      discarding non-members.
    - Recompute `display_tags` via the real `allergen_rules.to_display_tags(confirmed)` and
      `diet_tags` via the real `allergen_rules.derive_diet_tags(confirmed)` (imported from
      the shared layer; no reimplementation).
    - _Requirements: 4.1, 4.2, 4.6_

  - [x] 3.3 Implement native conditional UpdateItem persistence
    - Persist via a direct `boto3` `table.update_item(...)` — **not** `dynamo_service.put_item`.
    - Build the `UpdateExpression` with: per-map-path SET for each provided translation
      code (`SET translations.#es = :es`, …) seeding with
      `if_not_exists(translations, :empty_map)` to merge while preserving untouched codes;
      SET for `allergens.confirmed` / `allergens.display_tags` and `diet_tags`; SET
      `status = human_verified`; optional SET for `name` / `description` when valid.
    - Use `ConditionExpression = attribute_exists(menu_id)` and `ReturnValues = "ALL_NEW"`.
    - Map `ConditionalCheckFailedException` → 404 `{"error": "dish not found"}` with no
      stored change (fails closed).
    - Success → 200 `{"item": {...updated...}}` echoing the `ALL_NEW` attributes.
    - _Requirements: 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

- [x] 4. Checkpoint - handlers importable and layer built
  - Ensure the layer is built, both handlers import cleanly from `services`, and all tests
    pass. Ask the user if questions arise.

- [x] 5. Property-based tests for readMenu (Hypothesis, DynamoDB mocked, min 100 iterations)
  - [x]* 5.1 Property 1 — read returns stored dishes, sentinel excluded, one query
    - `# Feature: menu-read-edit-lambdas, Property 1: Read returns exactly the stored dishes (translations embedded), sentinel excluded, in one query`
    - For any partition of `dish-*` rows plus at most one `upload#` sentinel, the returned
      collection equals the dish rows with the sentinel excluded, each carrying its embedded
      Translations_Map unchanged, via exactly one `list_items` Query and zero
      per-translation queries; caller-independent.
    - _Validates: Requirements 1.1, 1.2, 1.4, 1.5_

  - [x]* 5.2 Property 2 — missing translation codes reported exactly, none fabricated
    - `# Feature: menu-read-edit-lambdas, Property 2: Missing translation codes are reported exactly, none fabricated`
    - For any dish whose map has an arbitrary subset of `{es, de, ja, zh}`, present entries
      are returned unchanged and absent codes flagged, union is exactly the four codes, no
      fabricated content, no write.
    - _Validates: Requirements 1.2_

  - [x]* 5.3 Property 3 — readMenu never mutates stored data
    - `# Feature: menu-read-edit-lambdas, Property 3: readMenu never mutates stored data`
    - For any request (valid, malformed, absent resource, or failing read), no write op is
      issued and any failure path returns 500 with no partial collection.
    - _Validates: Requirements 3.1, 1.6, 2.7, 3.2, 3.6_

  - [x]* 5.4 Property 4 — status endpoint faithfully returns the stored status
    - `# Feature: menu-read-edit-lambdas, Property 4: Status endpoint faithfully returns the stored status`
    - For any sentinel with an arbitrary stored `status` (incl. `processing`, `ocr_done`,
      `analyzing`, `translating`, `ready`), status is read via
      `get_item(uploadId, "upload#"+uploadId)` and returned verbatim with 200; any uploadId
      with no sentinel → 404 with no status value.
    - _Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x]* 5.5 Property 5 — status requests validate uploadId before any retrieval
    - `# Feature: menu-read-edit-lambdas, Property 5: Status requests validate the uploadId before any retrieval`
    - For any missing/empty/whitespace-only uploadId, respond 400 and perform no `get_item`.
    - _Validates: Requirements 2.6_

- [x] 6. Property-based tests for editMenu (Hypothesis, DynamoDB mocked, min 100 iterations)
  - [x]* 6.1 Property 6 — correction retains only PEAL categories
    - `# Feature: menu-read-edit-lambdas, Property 6: Correction retains only PEAL categories`
    - For any list mixing members/non-members of `allergen_rules.PEAL_CATEGORIES`, the
      applied confirmed set equals the input intersected with `PEAL_CATEGORIES`.
    - _Validates: Requirements 4.1_

  - [x]* 6.2 Property 7 — derived tags equal the real allergen_rules outputs
    - `# Feature: menu-read-edit-lambdas, Property 7: Derived tags equal the real allergen_rules outputs`
    - For any filtered confirmed set, persisted `display_tags` / `diet_tags` equal
      `allergen_rules.to_display_tags(confirmed)` / `derive_diet_tags(confirmed)`.
    - _Validates: Requirements 4.2, 4.6_

  - [x]* 6.3 Property 8 — translation merge preserves untouched codes
    - `# Feature: menu-read-edit-lambdas, Property 8: Translation merge preserves untouched codes`
    - For any existing map and any partial supported-code update, the result equals the
      existing map overlaid with the update (present codes take request values, absent codes
      retain prior values).
    - _Validates: Requirements 4.3, 5.3_

  - [x]* 6.4 Property 9 — name and description accepted iff within length bounds
    - `# Feature: menu-read-edit-lambdas, Property 9: name and description are accepted iff within their length bounds`
    - For any `name`, applied iff length 1–200 inclusive else 400 no write; for any
      `description`, applied iff length 0–2000 inclusive else 400 no write.
    - _Validates: Requirements 4.4, 4.5_

  - [x]* 6.5 Property 10 — unsupported language codes rejected without mutation
    - `# Feature: menu-read-edit-lambdas, Property 10: Unsupported language codes are rejected without mutation`
    - For any correction whose `translations` include a code outside `{es, de, ja, zh}`,
      respond 400, issue no write, and return an error identifying the unsupported code.
    - _Validates: Requirements 4.7_

  - [x]* 6.6 Property 11 — successful correction sets status to human_verified
    - `# Feature: menu-read-edit-lambdas, Property 11: Successful correction sets status to human_verified`
    - For any valid correction on an existing dish, regardless of optional fields present,
      the resulting item's `status` is `human_verified`.
    - _Validates: Requirements 5.1_

  - [x]* 6.7 Property 12 — persistence is a single conditional UpdateItem, never put_item
    - `# Feature: menu-read-edit-lambdas, Property 12: Persistence is a single conditional UpdateItem, never put_item`
    - For any correction request, the only possible write is exactly one native `UpdateItem`
      with `ConditionExpression = attribute_exists(menu_id)`; never `dynamo_service.put_item`;
      validation-rejected requests issue zero writes.
    - _Validates: Requirements 5.2, 5.6_

  - [x]* 6.8 Property 13 — correction on an absent dish fails closed with no change
    - `# Feature: menu-read-edit-lambdas, Property 13: Correction on an absent dish fails closed with no change`
    - For any PATCH targeting a non-existent `menu_id`/`item_id`, the conditional UpdateItem
      fails its condition, editMenu responds 404, and no stored data is changed.
    - _Validates: Requirements 5.4_

- [x] 7. Example / edge-case and integration tests
  - [x]* 7.1 Example / edge-case unit tests
    - readMenu: 200 with empty `items` when a partition holds only the sentinel row (R1.3);
      404 for a zero-row partition (R3.3); 500 when `list_items` raises (R1.6, R3.6); 500
      when `get_item` raises (R2.7, R3.6); 404 when the status sentinel is absent (R2.5).
    - editMenu: 400 for an empty-correction body (R4.8); 200 body echoing the `ALL_NEW`
      attributes including recomputed tags (R5.5).
    - _Requirements: 1.3, 2.5, 2.7, 3.3, 3.6, 1.6, 4.8, 5.5_

  - [x]* 7.2 API Gateway v2 event-shape integration tests
    - Feed API Gateway HTTP API v2 event shapes and assert readMenu dispatches correctly by
      `routeKey` across its two GET routes.
    - Assert the editMenu `ConditionExpression` produces a 404 against a real/local DynamoDB
      table when the target dish is absent (R5.4).
    - _Requirements: 2.2, 3.5, 5.4_

- [x] 8. Checkpoint - all handler and test work green
  - Ensure all property, example, and integration tests pass. Ask the user if questions
    arise.

- [x] 9. Terraform: shared Lambda layer and the two functions
  - [x] 9.1 Define the shared Lambda layer and both Lambda functions
    - In `terraform/`, add an `aws_lambda_layer_version` packaging `build/layer/` (Python
      runtime), consumed by both functions.
    - Define `read_menu` (handler `handler.handler`, package `build/read_menu/`) and
      `edit_menu` (handler `handler.handler`, package `build/edit_menu/`) Lambda functions,
      each attaching the shared layer, with the table name passed via environment.
    - _Requirements: 3.4, 4.6_

  - [x] 9.2 Define the two scoped IAM execution roles
    - readMenu role: `dynamodb:GetItem` + `dynamodb:Query` on the menu-items table ARN, plus
      baseline CloudWatch Logs (`CreateLogGroup`, `CreateLogStream`, `PutLogEvents`) scoped
      to its own log group. No write actions.
    - editMenu role: `dynamodb:UpdateItem` only on the menu-items table ARN, plus baseline
      CloudWatch Logs scoped to its own log group. No `GetItem`, no `PutItem`.
    - Compose the table ARN from `data.aws_caller_identity.current.account_id`, the provider
      region, and `${local.name_prefix}-menu-items`.
    - _Requirements: 3.1, 5.2, 5.6_

  - [x] 9.3 Define API Gateway HTTP API v2 routes, integrations, and permissions
    - Routes `GET /menus/{restaurantId}` and `GET /menus/{uploadId}/status` → readMenu;
      `PATCH /menus/{menuId}/items/{itemId}` → editMenu.
    - Use AWS_PROXY (v2) integrations and add `aws_lambda_permission` invoke permissions for
      each function from API Gateway.
    - _Requirements: 1.1, 2.1, 4.1_

- [x] 10. Final checkpoint - ensure everything is wired and tests pass
  - Ensure the layer builds, handlers import from `services`, all tests pass, and the
    Terraform validates. Ask the user if questions arise.

- [ ] 11. Add `GET /restaurants` to readMenu (list all restaurant/menu IDs)
  - [ ] 11.1 Handler route + dispatch (`build/read_menu/handler.py`)
    - Add a `ROUTE_LIST_RESTAURANTS = "GET /restaurants"` constant next to the existing
      route constants.
    - Add a `_handle_list_restaurants(event)` that calls
      `dynamo_service.list_menus()` (a single Scan projecting `menu_id`), wraps each
      returned menu_id string into `{"menu_id": <id>}`, and returns 200
      `{"restaurants": [...]}`; an empty table yields `{"restaurants": []}`.
    - On a `list_menus` failure → 500 `{"error": "restaurants could not be retrieved"}`
      with no partial list; no path params to validate; read-only (no writes).
    - Add the `routeKey` dispatch branch before the 404 fallback.
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ] 11.2 IAM Scan grant (`terraform/lambda_iam.tf`)
    - Add `dynamodb:Scan` to the readMenu `MenuItemsReadOnly` statement (required by
      `list_menus()` for `GET /restaurants`); leave editMenu's role unchanged. Scan is a
      read action, so the read-only guarantee holds.
    - _Requirements: 6.1, 6.4_

  - [ ] 11.3 API Gateway route (`terraform/apigateway.tf`)
    - Add an `aws_apigatewayv2_route` `GET /restaurants` targeting the existing
      `read_menu` integration (no new Lambda function, no new integration). The existing
      `read_menu` `aws_lambda_permission` `/*/*` wildcard already covers it — add no
      duplicate permission.
    - _Requirements: 6.1_

  - [ ]* 11.4 Property 14 / example tests for `GET /restaurants`
    - `# Feature: menu-read-edit-lambdas, Property 14: List restaurants returns exactly the unique menu_ids as objects, read-only`
    - For any set of menu_id strings returned by a mocked `list_menus`, the response is
      exactly one `{"menu_id": <id>}` object per string (order + multiplicity preserved),
      the empty result yields an empty list, and no write op is issued.
    - Example tests: 200 empty when `list_menus` returns `[]`; 500 when `list_menus`
      raises (no partial); `get_item`/`list_items` are NOT called for this route.
    - _Validates: Requirements 6.1, 6.2, 6.3, 6.4_

- [ ] 12. Admin authentication for the editMenu route (Cognito JWT)
  - [ ] 12.1 Add the shared Cognito admin user + password variable
    - In `terraform/variables.tf`, add `variable "admin_username"` (default `"admin"`) and
      `variable "admin_password"` (type string, `sensitive = true`, no default so it is
      supplied via tfvars / `TF_VAR_admin_password`).
    - In `terraform/cognito.tf`, add `aws_cognito_user.admin` in
      `aws_cognito_user_pool.staff_pool.id`, `username = var.admin_username`, with a
      permanent `password = var.admin_password` (no temporary-password / message_action
      flow). This is the single shared admin account; restaurants are chosen via the
      `menu_id` path param, not per-restaurant logins. Confirm no self-signup is enabled.
    - Export the staff pool app client id (JWT authorizer audience + needed to obtain a
      login token); `cognito_user_pool_id` is already output.
    - _Requirements: 7.5, 7.2_

  - [ ] 12.2 Add the JWT authorizer and attach it to the PATCH route only
    - In `terraform/apigateway.tf`, add `aws_apigatewayv2_authorizer.admin_jwt`
      (`authorizer_type = "JWT"`, `identity_sources = ["$request.header.Authorization"]`,
      `jwt_configuration` with `audience = [aws_cognito_user_pool_client.staff_pool_client.id]`
      and `issuer = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.staff_pool.id}"`).
    - On `aws_apigatewayv2_route.patch_menu_item` ONLY, set `authorization_type = "JWT"` and
      `authorizer_id = aws_apigatewayv2_authorizer.admin_jwt.id`. Leave the three GET
      read_menu routes public (default NONE). No handler changes — enforcement is at the
      gateway before editMenu runs.
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

## Notes

- Tasks marked with `*` are optional (property, example, and integration tests) and can be
  skipped for a faster MVP; core implementation tasks are never optional.
- Each task references the specific requirements and/or correctness properties it implements.
- DynamoDB is mocked for property tests so runs are cheap and exercise our logic, not AWS;
  each property test runs a minimum of 100 iterations.
- The four upstream producers remain out of scope; only the upload-status sentinel shape is
  consumed as a read contract.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2"] },
    { "id": 3, "tasks": ["3.3"] },
    { "id": 4, "tasks": ["5.1", "5.2", "5.3", "5.4", "5.5", "6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "7.1", "7.2"] },
    { "id": 5, "tasks": ["9.1"] },
    { "id": 6, "tasks": ["9.2", "9.3"] },
    { "id": 7, "tasks": ["11.1", "11.2", "11.3", "11.4"] },
    { "id": 8, "tasks": ["12.1", "12.2"] }
  ]
}
```
