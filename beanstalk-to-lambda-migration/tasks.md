# Implementation Plan: Menu_Read_API and Menu_Edit_API

## Overview

This plan implements only the two Lambda functions covered by the design pass:
**Menu_Read_API** (read-only) and **Menu_Edit_API** (human-in-the-loop override), plus
the shared infrastructure they depend on. Language is **Python** (boto3, Hypothesis for
property tests), matching the design.

The upstream producers (Upload_Handler, OCR_Processor, Allergen_Extractor, Translator)
are out of scope; their internals are NOT implemented here. Only the pieces the two
in-scope functions depend on are built:

- The shared **Lambda Layer** packaging (pure copy of `app/services/*.py` per Open
  Question 3, honoring R1.1 — no edits).
- The two function packages (`build/menu_read_api/handler.py`,
  `build/menu_edit_api/handler.py`).
- The Terraform for the two Lambda functions, their two scoped IAM execution roles, and
  the API Gateway HTTP API v2 routes for the two functions' three endpoints.

Order of work: shared layer + handlers first (so they are importable and testable), then
tests, then Terraform once the handlers are testable.

## Tasks

- [ ] 1. Establish shared Lambda Layer packaging (Open Question 3, R1.1)
  - [ ] 1.1 Create the layer build layout and copy service modules verbatim
    - Create `build/layer/python/services/` directory structure
    - Copy `app/services/__init__.py`, `allergen_rules.py`, `dynamo_service.py`,
      `bedrock_service.py`, `s3_service.py`, `textract_service.py` into
      `build/layer/python/services/` as a pure byte-for-byte copy (no edits)
    - Provide a repeatable copy/packaging script (e.g. `build/package_layer.py` or a
      shell/PowerShell script) that performs the verbatim copy so the layer can be
      rebuilt deterministically without hand-editing
    - _Requirements: 1.1_
  - [ ]* 1.2 Write a test asserting the copied modules are byte-for-byte identical
    - Compare each `build/layer/python/services/*.py` against its `app/services/*.py`
      source (hash or exact-content equality) to enforce R1.1
    - _Requirements: 1.1_

- [ ] 2. Implement Menu_Read_API handler
  - [ ] 2.1 Create handler skeleton with API Gateway v2 dispatch by routeKey
    - Create `build/menu_read_api/handler.py`
    - Import `from services import dynamo_service` (from the shared layer)
    - Dispatch on the API Gateway v2 `routeKey` / resource path template so
      `GET /menus/{restaurantId}` and `GET /menus/{uploadId}/status` are never confused
    - Add path-parameter validation (reject empty/whitespace or out-of-charset params
      with HTTP 400 before any DynamoDB call); JSON error body `{"error": "..."}`
    - Read-only: no PutItem/UpdateItem/DeleteItem code path exists
    - _Requirements: 6.5, 6.7_
  - [ ] 2.2 Implement GET /menus/{restaurantId} (list + sentinel filter + translation annotation)
    - Call `dynamo_service.list_items(restaurantId)` (single Query) — R11.3
    - Filter out the upload-status sentinel row (item_id prefixed `upload#`) from the
      returned dish collection
    - Distinguish existence: zero rows total → 404 (restaurant not found); rows present
      but no dish rows → 200 `{"items": []}`; dish rows present → 200 with items
    - Annotate each dish's Translations_Map: include present codes, flag each missing
      Configured_Language (`es`, `de`, `ja`, `zh`) as unavailable; do not fabricate
      translations and do not write
    - _Requirements: 6.1, 6.2, 6.3, 6.6, 11.3, 11.6_
  - [ ] 2.3 Implement GET /menus/{uploadId}/status (sentinel GetItem)
    - Map to `dynamo_service.get_item(menu_id=uploadId, item_id="upload#"+uploadId)`
    - On `None` → 404 `{"error": "uploadId not found"}`; on found → 200
      `{"uploadId", "status", "previous_status"?}` returning the stored status verbatim
    - _Requirements: 6.4, 6.6, 8.5, 8.6_
  - [ ]* 2.4 Write property test — Read returns exactly stored dishes, sentinel excluded
    - **Property 1: Read returns exactly the stored dishes (with translations) in one query, sentinel excluded**
    - **Validates: Requirements 6.1, 6.2, 11.3**
    - Hypothesis, min 100 iterations, DynamoDB mocked; tag
      `# Feature: beanstalk-to-lambda-migration, Property 1: <text>`
  - [ ]* 2.5 Write property test — Missing translation codes reported exactly
    - **Property 2: Missing translation codes are reported exactly**
    - **Validates: Requirements 11.6**
    - Hypothesis, min 100 iterations, DynamoDB mocked; tag
      `# Feature: beanstalk-to-lambda-migration, Property 2: <text>`
  - [ ]* 2.6 Write property test — Menu_Read_API never mutates stored data
    - **Property 3: Menu_Read_API never mutates stored data**
    - **Validates: Requirements 6.5, 6.6, 6.7**
    - Hypothesis over valid/malformed/absent-resource requests, min 100 iterations,
      DynamoDB mocked with a spy asserting no PutItem/UpdateItem/DeleteItem; tag
      `# Feature: beanstalk-to-lambda-migration, Property 3: <text>`
  - [ ]* 2.7 Write property test — Status endpoint faithfully returns stored status
    - **Property 4: Status endpoint faithfully returns the stored status**
    - **Validates: Requirements 6.4, 6.6, 8.5, 8.6**
    - Hypothesis, min 100 iterations, DynamoDB mocked (found → exact value, absent →
      404, monotonic non-regression across a poll sequence); tag
      `# Feature: beanstalk-to-lambda-migration, Property 4: <text>`
  - [ ]* 2.8 Write example/edge unit tests for Menu_Read_API
    - 400 on malformed/empty `restaurantId` and `uploadId`
    - 404 on unknown restaurant and on missing uploadId sentinel
    - 200-empty for a restaurant partition containing only a sentinel row
    - Sentinel row excluded from `GET /menus/{restaurantId}` results
    - _Requirements: 6.3, 6.6, 6.7_

- [ ] 3. Implement Menu_Edit_API handler
  - [ ] 3.1 Create handler skeleton and request parsing
    - Create `build/menu_edit_api/handler.py`
    - Import `from services import allergen_rules` (from the shared layer)
    - Parse `PATCH /menus/{menuId}/items/{itemId}` path params and JSON body
      (`confirmed_allergens?`, `translations?`, `name?`, `description?`); JSON error
      body `{"error": "..."}`
    - Create its own `boto3` DynamoDB table resource (does NOT use
      `dynamo_service.put_item`, which would require PutItem)
    - _Requirements: 7.4_
  - [ ] 3.2 Implement PEAL filtering and real allergen_rules recomputation
    - Filter `confirmed_allergens` to `allergen_rules.PEAL_CATEGORIES` (silently discard
      non-members)
    - Call the REAL `allergen_rules.to_display_tags(confirmed)` and
      `allergen_rules.derive_diet_tags(confirmed)` from the shared layer (no reimplementation)
    - _Requirements: 1.7, 7.1, 7.2_
  - [ ] 3.3 Implement native conditional UpdateItem persistence
    - Build a single `UpdateExpression`: per-code `SET translations.<code>` for each
      provided translation (merge preserving untouched codes), `SET allergens.confirmed`,
      `SET allergens.display_tags`, `SET diet_tags`, `SET status = human_verified`, plus
      optional `SET name` / `SET description` when present
    - Add `ConditionExpression = attribute_exists(menu_id)` and `ReturnValues="ALL_NEW"`
    - On success → 200 `{"item": <ALL_NEW attributes>}`; on
      `ConditionalCheckFailedException` → 404 `{"error": "dish item not found"}` with no
      change to stored data
    - _Requirements: 7.3, 7.5, 7.6, 7.7_
  - [ ]* 3.4 Write property test — Override retains only PEAL categories
    - **Property 5: Override retains only PEAL categories**
    - **Validates: Requirements 7.1**
    - Hypothesis over mixed valid/invalid allergen lists, min 100 iterations, DynamoDB
      mocked; tag `# Feature: beanstalk-to-lambda-migration, Property 5: <text>`
  - [ ]* 3.5 Write property test — Derived tags match the real allergen_rules functions
    - **Property 6: Derived tags match the real allergen_rules functions**
    - **Validates: Requirements 1.7, 7.2**
    - Hypothesis, min 100 iterations, DynamoDB mocked; assert written `display_tags` /
      `diet_tags` equal `allergen_rules.to_display_tags` / `derive_diet_tags`; tag
      `# Feature: beanstalk-to-lambda-migration, Property 6: <text>`
  - [ ]* 3.6 Write property test — Translation merge preserves untouched codes
    - **Property 7: Translation merge preserves untouched codes**
    - **Validates: Requirements 7.3**
    - Hypothesis over existing map + partial update, min 100 iterations, DynamoDB mocked
      (capture UpdateExpression effect); tag
      `# Feature: beanstalk-to-lambda-migration, Property 7: <text>`
  - [ ]* 3.7 Write property test — Successful override sets status to human_verified
    - **Property 8: Successful override sets status to human_verified**
    - **Validates: Requirements 7.5**
    - Hypothesis over any subset of optional fields, min 100 iterations, DynamoDB mocked;
      tag `# Feature: beanstalk-to-lambda-migration, Property 8: <text>`
  - [ ]* 3.8 Write property test — Override on absent item fails closed with no change
    - **Property 9: Override on an absent item fails closed with no change**
    - **Validates: Requirements 7.6, 7.7**
    - Hypothesis over absent menu_id/item_id, min 100 iterations, DynamoDB mocked to
      raise `ConditionalCheckFailedException`; assert 404 and no stored change; tag
      `# Feature: beanstalk-to-lambda-migration, Property 9: <text>`
  - [ ]* 3.9 Write example/edge unit tests for Menu_Edit_API
    - Optional `name`/`description` update applied when present
    - 404 example via condition failure against a mocked/local table
    - _Requirements: 7.4, 7.7_

- [ ] 4. Checkpoint - handlers and logic tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Integration tests for API Gateway v2 event shape
  - [ ]* 5.1 Write API Gateway v2 dispatch integration test
    - Feed realistic API Gateway HTTP API v2 events (with `routeKey` and
      `pathParameters`) into each handler; assert correct route dispatch for
      `GET /menus/{restaurantId}`, `GET /menus/{uploadId}/status`, and
      `PATCH /menus/{menuId}/items/{itemId}`
    - _Requirements: 6.1, 6.4, 7.6_
  - [ ]* 5.2 Write ConditionExpression 404 integration test
    - Exercise the `attribute_exists(menu_id)` condition producing a 404 against a
      local/in-memory table
    - _Requirements: 7.7_

- [ ] 6. Provision Terraform for the two Lambda functions and their IAM roles
  - [ ] 6.1 Define the shared Lambda Layer and the two Lambda functions
    - In `terraform/lambda.tf`, define the shared layer (from `build/layer`) and the two
      functions Menu_Read_API and Menu_Edit_API (from `build/menu_read_api` and
      `build/menu_edit_api`), attaching the shared layer to both and wiring the
      `DYNAMODB_TABLE` env to `${local.name_prefix}-menu-items`
    - _Requirements: 10.1, 10.7_
  - [ ] 6.2 Define the two scoped IAM execution roles and policies
    - In `terraform/iam.tf`, define one dedicated execution role per function
    - Menu_Read_API policy: `dynamodb:GetItem` + `dynamodb:Query` scoped to the
      menu-items table ARN, plus baseline CloudWatch Logs
      (`logs:CreateLogGroup/CreateLogStream/PutLogEvents`) scoped to its own log group
    - Menu_Edit_API policy: `dynamodb:UpdateItem` only, scoped to the menu-items table
      ARN, plus the same baseline CloudWatch Logs scoped to its own log group (no
      GetItem, no PutItem)
    - Compose ARNs from `data.aws_caller_identity.current.account_id`, provider region,
      and `${local.name_prefix}-menu-items`
    - _Requirements: 9.6, 9.7, 9.8, 9.9_
  - [ ] 6.3 Define API Gateway HTTP API v2 routes for the three endpoints
    - In `terraform/api_gateway.tf`, define/extend the HTTP API (v2) and route
      `GET /menus/{restaurantId}` and `GET /menus/{uploadId}/status` to Menu_Read_API,
      and `PATCH /menus/{menuId}/items/{itemId}` to Menu_Edit_API, with the Lambda
      proxy integrations and invoke permissions
    - _Requirements: 10.2, 10.7_

- [ ] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
- Each task references specific requirements and/or the correctness property it implements.
- Property tests use Hypothesis with a minimum of 100 iterations and mock DynamoDB so they
  exercise our logic, not AWS.
- The shared layer is a pure verbatim copy of `app/services/*.py` (R1.1) — no edits.
- Menu_Edit_API deliberately avoids `dynamo_service.put_item` (which needs PutItem) and
  uses a native conditional `UpdateItem`, matching the scoped IAM role (R9.7).
- Out of scope: Upload_Handler, OCR_Processor, Allergen_Extractor, Translator internals.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2"] },
    { "id": 3, "tasks": ["2.4", "2.5", "2.6", "2.7", "2.8", "3.3"] },
    { "id": 4, "tasks": ["3.4", "3.5", "3.6", "3.7", "3.8", "3.9"] },
    { "id": 5, "tasks": ["5.1", "5.2", "6.1"] },
    { "id": 6, "tasks": ["6.2", "6.3"] }
  ]
}
```
