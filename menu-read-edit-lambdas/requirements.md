# Requirements Document

## Introduction

This feature defines two AWS Lambda functions that expose the saved-menu data of the
AI-powered allergen compliance and menu translation system: **readMenu** and **editMenu**.
Both functions sit behind the API Gateway HTTP API (v2) and operate over a single shared
DynamoDB table of menu items. They are a scoped slice of the larger Beanstalk-to-Lambda
migration and cover only the read and human-correction paths over already-saved data.

**readMenu** serves the read-only view of a saved menu. It powers two audiences over the
same read path: customers browsing the app who pick a restaurant and view its dishes, and
the restaurant itself viewing its own menu after an upload. readMenu also lets the
restaurant check whether an in-flight upload is still processing or has finished.

**editMenu** lets a restaurant correct an already-saved dish — fixing its confirmed
allergens, its translations, its name, or its description. When allergens are corrected,
editMenu recomputes the diner-facing display tags and diet tags so the published menu stays
consistent, and marks the dish as human-verified.

Both functions **reuse** the existing service modules without modifying them:
`app/services/dynamo_service.py` for all database reads and writes, and
`app/services/allergen_rules.py` for recomputing display tags and diet tags. The dish rows
and the upload-status sentinel row that these functions read are produced by upstream
components (upload handling, OCR/text extraction, allergen detection via Bedrock,
translation generation) that are **out of scope** for this feature and owned elsewhere;
those components may write to the same DynamoDB table, but their internals are not this
spec's concern.

## Glossary

- **readMenu**: The Lambda function serving the read-only endpoints for viewing a saved
  menu and checking an upload's processing status.
- **editMenu**: The Lambda function serving the human correction endpoint for an
  already-saved dish.
- **Dynamo_Service**: The existing, unmodified `app/services/dynamo_service.py` module.
  Its public functions used here are `put_item(item)`, `list_items(menu_id)` (a single
  Query on `menu_id`), `get_item(menu_id, item_id)` (a single GetItem), and
  `list_menus()` (a single Scan projecting `menu_id`, returning a sorted list of unique
  Menu_Ids).
- **Allergen_Rules**: The existing, unmodified `app/services/allergen_rules.py` module,
  exposing `PEAL_CATEGORIES`, `to_display_tags(confirmed)`, and
  `derive_diet_tags(confirmed, text="")`.
- **PEAL_Categories**: The FSANZ Standard 1.2.3 mandatory declarable allergen categories
  defined in `Allergen_Rules.PEAL_CATEGORIES`.
- **Menu_Id**: The DynamoDB hash key (`menu_id`, type S) identifying a restaurant's menu
  partition; also the identifier a customer selects when browsing.
- **Item_Id**: The DynamoDB range key (`item_id`, type S) identifying a single row within a
  menu partition. Dish rows use `dish-*`; the upload-status sentinel uses `upload#<uploadId>`.
- **Dish_Item**: One saved dish row in the table, with attributes `menu_id`, `item_id`,
  `name`, `description`, `source`, `status`, `allergens` (a map with `confirmed`,
  `display_tags`, and related keys), `diet_tags` (a list), `translations` (a map keyed by
  language code), and `updated_at`.
- **Translations_Map**: The `translations` attribute embedded on a Dish_Item, keyed by the
  language codes `es`, `de`, `ja`, `zh` (per `bedrock_service.LANGUAGES`). Translations are
  embedded on the dish item; there are no separate translation rows.
- **Upload_Id**: A unique identifier for a submitted menu upload, used to poll processing
  status.
- **Status_Sentinel**: A single non-dish row written by the out-of-scope upload handler in
  the same table under `menu_id = <uploadId>` and `item_id = "upload#" + <uploadId>`,
  carrying the upload's `status`. readMenu reads this row to answer status queries and
  excludes it from customer-facing dish listings.
- **Upload_Status**: The `status` value on the Status_Sentinel, indicating whether an
  upload is still processing or has finished (done/ready).
- **Restaurant_Registry_Row**: A dedicated non-dish row that is the source of truth for a
  restaurant's existence, independent of whether the restaurant has any Dish_Items. It is
  keyed under `menu_id = <restaurantId>` and `item_id = "restaurant#" + <restaurantId>`,
  carries `record_type = "restaurant"` and a display `name`, and is read by readMenu's
  `GET /restaurants` picker. Creating registry rows is an out-of-scope admin write-path
  (seeded manually for now); readMenu only reads them.
- **Human_Verified**: The dish `status` value `human_verified`, set by editMenu when a
  correction is applied to a dish.
- **Staff_Pool**: The existing Cognito User Pool (`aws_cognito_user_pool.staff_pool`) and
  its app client (`aws_cognito_user_pool_client.staff_pool_client`) that issue and validate
  the JWT protecting the editMenu route. It holds exactly one shared admin account and has
  no self-signup enabled.
- **Admin_Login**: The single shared admin credential in Staff_Pool that authorizes dish
  corrections. It is not per-restaurant; the restaurant being edited is identified by the
  Menu_Id path param, not by the logged-in identity.

## Requirements

### Requirement 1: View a restaurant's saved menu

**User Story:** As a customer browsing the app and as a restaurant viewing its own menu, I want to view a restaurant's saved dishes, so that I can see the menu and its allergen and translation information.

#### Acceptance Criteria

1. WHEN a request to view the menu for an existing Menu_Id that has one or more dishes is received, THE readMenu SHALL return every Dish_Item for that Menu_Id retrieved via `Dynamo_Service.list_items`.
2. WHEN readMenu returns the dishes for a Menu_Id, THE readMenu SHALL include each Dish_Item's embedded Translations_Map from the same row, without issuing a separate query per translation.
3. WHEN a request to view the menu for an existing Menu_Id whose partition contains rows but no Dish_Item rows (for example, only the Status_Sentinel row) is received, THE readMenu SHALL respond with HTTP status 200 and an empty dish collection.
4. WHEN readMenu retrieves the rows for a Menu_Id, THE readMenu SHALL exclude every row whose item_id begins with the prefix "upload#" (the Status_Sentinel row) from the returned dish collection.
5. THE readMenu SHALL serve the customer browsing view and the restaurant's own post-upload view of a menu through the same read path, returning an identical dish collection for the same Menu_Id.
6. IF the `Dynamo_Service.list_items` read for a Menu_Id fails, THEN THE readMenu SHALL respond with HTTP status 500 and an error description indicating the menu could not be retrieved, and SHALL NOT return a partial dish collection.

### Requirement 2: Check upload processing status

**User Story:** As a restaurant that has uploaded a menu, I want to check whether the upload is still processing or finished, so that I know when the saved menu is ready to view.

#### Acceptance Criteria

1. WHEN a request for the status of an existing Upload_Id is received, THE readMenu SHALL retrieve the Status_Sentinel via `Dynamo_Service.get_item` with `menu_id = <uploadId>` and `item_id = "upload#" + <uploadId>`.
2. WHEN the Status_Sentinel for a requested Upload_Id is found, THE readMenu SHALL respond within 2 seconds with HTTP status 200 and the Upload_Status value recorded on that Status_Sentinel.
3. WHEN the Status_Sentinel indicates the upload is not yet finished, THE readMenu SHALL return the Upload_Status value equal to one of the in-progress states `processing`, `ocr_done`, `analyzing`, or `translating`.
4. WHEN the Status_Sentinel indicates the upload is finished, THE readMenu SHALL return the Upload_Status value equal to the terminal state `ready`.
5. IF no Status_Sentinel exists for a requested Upload_Id, THEN THE readMenu SHALL respond with HTTP status 404 and an error description indicating the Upload_Id was not found.
6. IF a status request is received with a missing or empty Upload_Id, THEN THE readMenu SHALL respond with HTTP status 400 and an error description indicating the Upload_Id is required, without performing a Status_Sentinel retrieval.
7. IF the Status_Sentinel retrieval fails due to a data store read error, THEN THE readMenu SHALL respond with HTTP status 500 and an error description indicating the status could not be retrieved, while leaving the Status_Sentinel unmodified.

### Requirement 3: readMenu is read-only and validates input

**User Story:** As a security reviewer, I want readMenu to only read data and to reject malformed or unknown requests cleanly, so that read traffic cannot alter stored data.

#### Acceptance Criteria

1. THE readMenu SHALL perform only read operations against DynamoDB, issuing no PutItem, UpdateItem, or DeleteItem.
2. IF a request is received with a Menu_Id or Upload_Id path parameter that is empty, whitespace-only, or contains characters outside the permitted key character set, THEN THE readMenu SHALL respond with HTTP status 400 and an error description identifying the invalid parameter, and SHALL make no change to stored data.
3. IF a request references a Menu_Id whose partition contains no rows at all, THEN THE readMenu SHALL respond with HTTP status 404 and an error description indicating the restaurant was not found, and SHALL make no change to stored data.
4. THE readMenu SHALL access stored data only through `Dynamo_Service`, without modifying `Dynamo_Service`.
5. WHEN readMenu responds to any read request, THE readMenu SHALL respond within 2 seconds.
6. IF a read operation against DynamoDB fails, THEN THE readMenu SHALL respond with HTTP status 500 and an error description, and SHALL make no change to stored data.

### Requirement 4: Correct an already-saved dish

**User Story:** As a restaurant staff member, I want to correct an already-saved dish's allergens, translations, name, or description, so that the published menu reflects verified information.

#### Acceptance Criteria

1. IF a correction request for a Dish_Item includes corrected confirmed allergens, THEN THE editMenu SHALL retain only the provided allergens that are members of PEAL_Categories, discarding any value that is not a member of PEAL_Categories.
2. IF a correction request includes corrected confirmed allergens, THEN THE editMenu SHALL recompute the display tags via `Allergen_Rules.to_display_tags` and the diet tags via `Allergen_Rules.derive_diet_tags` from the retained confirmed allergen set.
3. IF a correction request includes corrected translations, THEN THE editMenu SHALL merge the provided translations into the Dish_Item's Translations_Map, preserving existing entries for language codes not present in the request.
4. IF a correction request includes a corrected name, THEN THE editMenu SHALL update the `name` field on the Dish_Item with the provided value of length 1 to 200 characters inclusive.
5. IF a correction request includes a corrected description, THEN THE editMenu SHALL update the `description` field on the Dish_Item with the provided value of length 0 to 2000 characters inclusive.
6. THE editMenu SHALL recompute display tags and diet tags only through `Allergen_Rules`, without modifying `Allergen_Rules`.
7. IF a correction request includes corrected translations for any language code that is not a member of the set {es, de, ja, zh}, THEN THE editMenu SHALL reject the request with HTTP status 400, leave the Dish_Item's Translations_Map unchanged, and return an error indication identifying the unsupported language code.
8. IF a correction request contains none of the correctable fields (confirmed allergens, translations, name, or description), THEN THE editMenu SHALL reject the request with HTTP status 400, make no changes to the Dish_Item, and return an error indication that no correctable field was provided.

### Requirement 5: Persist corrections and mark human-verified

**User Story:** As a restaurant staff member, I want an applied correction to be saved and marked as verified by a human, so that downstream views trust the corrected dish.

#### Acceptance Criteria

1. WHEN a correction is applied to an existing Dish_Item, THE editMenu SHALL set the Dish_Item `status` to Human_Verified.
2. WHEN a correction is applied to an existing Dish_Item, THE editMenu SHALL persist the updated Dish_Item via a native conditional UpdateItem guarded by a `ConditionExpression` requiring that the target row exists, and SHALL NOT use `Dynamo_Service.put_item`.
3. WHEN editMenu persists a correction, THE editMenu SHALL preserve Translations_Map entries for language codes that were not included in the correction request.
4. IF a correction request targets a Menu_Id and Item_Id for which no Dish_Item exists, THEN THE editMenu SHALL respond with HTTP status 404 and an error description indicating the dish was not found, and SHALL make no change to stored data.
5. WHEN a correction is applied successfully, THE editMenu SHALL respond within 2 seconds with HTTP status 200 and the updated Dish_Item, including its recomputed display tags and diet tags.
6. THE editMenu SHALL write stored data only through a native conditional UpdateItem and SHALL make no other write operations.

### Requirement 6: List all restaurants

**User Story:** As a customer browsing the app and as a restaurant staff member logging in, I want to see all restaurants/cafes that have a saved menu, so that I can select my cafe from a picker before viewing or correcting its menu.

#### Acceptance Criteria

1. WHEN a request to list all restaurants is received, THE readMenu SHALL return every Restaurant_Registry_Row present in the table — identified by `record_type == "restaurant"` (equivalently, an `item_id` beginning with `restaurant#`) — retrieved via a Scan filtered to those registry rows, with each row represented as an object of the form `{"menu_id": <Menu_Id>, "name": <display name>}`; WHEN a registry row carries no `name`, THE readMenu SHALL fall back to its `menu_id` as the name so every entry has a label. Restaurant existence is sourced from these dedicated registry rows and is INDEPENDENT of dish rows: a restaurant SHALL still appear as long as its registry row exists, even when it has zero dish rows. The returned list SHALL be sorted by `menu_id`.
2. WHEN the table contains no Restaurant_Registry_Row, THE readMenu SHALL respond with HTTP status 200 and an empty restaurant list.
3. IF the registry Scan read fails, THEN THE readMenu SHALL respond with HTTP status 500 and an error description indicating the restaurants could not be retrieved, and SHALL NOT return a partial restaurant list.
4. THE readMenu SHALL serve the list-all-restaurants request as a read-only operation, issuing only a Scan and no PutItem, UpdateItem, or DeleteItem, and SHALL require no path parameters.

> **Out of scope:** The admin write-path that CREATES Restaurant_Registry_Rows is explicitly out of scope for this feature; registry rows are seeded manually for now. This requirement covers only reading them.

### Requirement 7: Admin authentication for editing

**User Story:** As the operator of the allergen system, I want a single shared admin login to protect dish edits while leaving all menu-viewing public, so that only an authenticated admin can correct dishes and customers can still browse freely; the restaurant being edited is chosen via the Menu_Id path param rather than a per-restaurant account.

#### Acceptance Criteria

1. WHEN a request to the PATCH edit route (`PATCH /menus/{menuId}/items/{itemId}`) is received without a valid Cognito-issued JWT, THE Staff_Pool authorizer SHALL reject the request at the API Gateway with HTTP status 401 or 403 before editMenu is invoked, and SHALL make no change to stored data.
2. WHEN a request to the PATCH edit route is received with a valid Cognito-issued JWT whose audience is the Staff_Pool app client and whose issuer is the Staff_Pool, THE API Gateway SHALL invoke editMenu to process the correction.
3. THE API Gateway SHALL enforce editMenu authentication at the gateway layer (via a JWT authorizer) before editMenu runs, such that editMenu's handler contains no authentication logic and is unchanged by this requirement.
4. THE readMenu GET routes (`GET /menus/{restaurantId}`, `GET /menus/{uploadId}/status`, and `GET /restaurants`) SHALL remain publicly accessible with no authentication, requiring no JWT and no authorizer.
5. THE system SHALL provide exactly one shared Admin_Login credential in Staff_Pool, SHALL NOT enable self-signup, and SHALL NOT create per-restaurant accounts.
