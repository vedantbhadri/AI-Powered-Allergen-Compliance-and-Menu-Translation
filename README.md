# AI-Powered Allergen Compliance & Menu Translation

An AI system that helps New Zealand cafes and restaurants publish allergen-accurate, multilingual menus. Staff upload a menu (PDF/image) or add dishes by hand; the app runs OCR, extracts allergens with a Bedrock LLM cross-checked against a deterministic FSANZ Standard 1.2.3 rules engine, verifies compliance, and translates each dish into Spanish, German, Japanese, and Mandarin. Diners pick a cafe and browse its menu in their language with clear "Contains …" allergen tags; staff can log in to review and correct the AI's output (human-in-the-loop).

## ⚠️ Current architecture: a hybrid, mid-migration state

This repository is **not** a single finished architecture. It is a deliberate in-between state, and this README is honest about what is real versus pending.

- **The Flask app on Elastic Beanstalk is the live system.** `app/application.py` is a Flask monolith that runs the whole pipeline (upload → OCR → allergen extraction → compliance → translation → persist) as ordinary function calls in one request. It talks to a **real, already-provisioned DynamoDB table and S3 bucket** in the team's AWS account (region `us-east-1`). There is no local fake/mock in this file — running it reads and writes shared, live data.
- **The readMenu / editMenu Lambda pair is built and tested, but NOT deployed.** `build/read_menu/handler.py` and `build/edit_menu/handler.py` are complete, unit/property-tested Lambda handlers, and the Terraform to deploy them (`terraform/lambda.tf`, `lambda_iam.tf`, `apigateway.tf`, `cognito.tf`) exists. As of now that Terraform has not been confirmed applied — the Lambda side is not live.
- **A temporary local bridge connects the two.** So the built-but-undeployed Lambda handlers can be exercised today, `application.py` loads them directly (via `importlib`) and exposes them under `/api/v2/...` routes, building fake API-Gateway-shaped events and calling the handler functions in-process. This bridge is **scaffolding for local testing**, not the final architecture — in production these handlers would run as real Lambdas behind API Gateway, not be imported into the Flask app.

The broader target design (six event-driven Lambdas behind API Gateway, coordinated by a `status` field) is documented in the `beanstalk-to-lambda-migration/` spec. The `menu-read-edit-lambdas/` spec covers the specific readMenu/editMenu slice that has actually been built.

## The allergen pipeline (with optional RAG)

Allergen handling lives in two collaborating pieces:

- `app/services/allergen_rules.py` — the deterministic FSANZ Standard 1.2.3 rules engine (`PEAL_CATEGORIES`, `scan_text_for_allergens`, `reconcile_allergens`, `to_display_tags`, `derive_diet_tags`). Unmodified across the migration.
- `app/services/allergen_service.py` — a combined extraction + compliance module. It does keyword extraction, Bedrock-LLM extraction reconciled with the rules engine, a deterministic NZ PEAL compliance verdict (`COMPLIANT` / `ACTION_REQUIRED` / `UNVERIFIED`), and **RAG retrieval** of regulatory context. Its `verify_pipeline()` takes the **union** of three signals — LLM categories, rules-engine categories, and RAG-retrieved categories — as the confirmed allergen set (biased toward not under-declaring).

The pipeline in `application.py` (`_run_pipeline`) calls Bedrock extraction, the rules engine, and `allergen_service.retrieve_context()`, then merges them via `verify_pipeline()`.

**RAG is opt-in and off by default.** `allergen_service.retrieve_context()` uses a Bedrock Knowledge Base only when `KNOWLEDGE_BASE_ID` is set and not in local mode; otherwise it **degrades to a local search over `docs/*.md`**, so the pipeline never hard-fails without a KB. The Knowledge Base infrastructure in `terraform/bedrock_kb.tf` is entirely gated behind `var.create_knowledge_base` (default **false**) — a plain `terraform apply` creates no KB, no OpenSearch collection, and no KB IAM role unless you opt in.

## Restaurant registry pattern

A restaurant's existence is represented by its own dedicated **registry row**, independent of its dishes:

```json
{ "menu_id": "<restaurantId>", "item_id": "restaurant#<restaurantId>", "record_type": "restaurant", "name": "<Display Name>" }
```

`GET /restaurants` (readMenu) returns restaurants by scanning for these registry rows (filtered on `record_type == "restaurant"`), **not** by inferring them from dish rows. This fixes a real dead-end: previously, deleting all of a restaurant's dishes made the restaurant vanish from the picker. Now a restaurant with zero dishes still appears as long as its registry row exists. The dish-listing route (`GET /menus/{restaurantId}`) excludes both the `restaurant#` registry row and the `upload#` status sentinel so only real `dish-*` rows are returned.

**Creating a restaurant is currently manual / CLI-only.** There is no admin UI or API write-path to create a registry row yet — these rows are seeded by hand (e.g. via the AWS CLI). Until rows are seeded, `GET /restaurants` returns an empty list.

## Running locally

The local Flask app talks to **real, shared AWS resources** (real DynamoDB table `allergen-demo-dev-menu-items`, real S3 bucket `allergen-demo-dev-menu-uploads-669232219904`, real Bedrock/Textract). Uploads, edits, and deletes affect live data other teammates may be using — there is no local fake in `application.py`.

### 1. AWS credentials (temporary SSO session tokens)

The team uses short-lived AWS SSO session credentials, **not** `aws configure`. Export them as environment variables in the shell you'll run the server from:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."        # required for temporary SSO credentials
```

Verify they work before starting the app:

```bash
aws sts get-caller-identity
```

If that fails, the server will fail the same way once it makes its first AWS call.

### 2. What env vars application.py sets, and why

`application.py` sets these itself at import time so every component agrees on the same real resources:

- `AWS_REGION` / `AWS_DEFAULT_REGION` = `us-east-1` (set via `setdefault`, so your shell can override) — the region the real DynamoDB/S3 live in.
- `DYNAMODB_TABLE` and `MENU_TABLE_NAME` = `allergen-demo-dev-menu-items` — the same table name under two different variable names, because `dynamo_service.py` and editMenu's `handler.py` each read a differently-named variable. Both are set so they point at the same table.
- `S3_BUCKET` = `allergen-demo-dev-menu-uploads-669232219904` — the bucket `s3_service.py` reads.

Note: `allergen_service.py`'s RAG layer defaults its Bedrock/KB region to `ap-southeast-2` (override with `BEDROCK_REGION` / `KB_REGION`), separate from the `us-east-1` used for DynamoDB/S3. With no `KNOWLEDGE_BASE_ID` set, RAG uses the local `docs/` fallback.

### 3. Start the server

```bash
cd app
python application.py          # dev server on http://localhost:8000
```

In production, Beanstalk runs it via gunicorn (see `Procfile`), not `python application.py`.

## The UI

The frontend (`app/static/index.html`, `app.js`, `style.css`; Bootstrap 5.3.3 via CDN plus a custom warm palette) is a single page with two states:

- **Cafe picker** — the first screen, a full-bleed dark restaurant photo with a dark overlay and the heading "Welcome! Please select your cafe". It lists cafes from `GET /api/v2/restaurants` as buttons; picking one loads that menu.
- **Main app** — a sticky header (brand, display-language selector for EN/ES/DE/JA/ZH, "Change cafe", and a "Login as Admin" / "Logout" toggle), and a dish grid that shows each dish with its allergen "Contains …" chips and diet tags in the selected language.

Behavior details grounded in the current code:

- **Login/logout toggle.** Logged-out is the default. Logging in (admin) reveals the **Management panel** (upload a file, add a dish manually, an FSANZ compliance reference, and a "Clear dishes" action) and lets you click dishes to open the review/edit modal. Logging out hides the panel again.
- **Responsive layout.** When logged out, the layout collapses to a single column (`.layout.single-column`) so there's no empty left gutter; when logged in, the management panel occupies the left column. The header wraps and stacks on tablet/phone widths.
- **Zero-dish menus aren't errors.** `loadItems()` treats a 404 from the menu read as "this menu simply has no dishes yet" and shows an empty grid rather than an error.
- **WebP uploads** are converted to JPEG client-side before upload.

## API endpoints

All routes are served by the Flask app. `/api/...` are the original direct-Flask routes; `/api/v2/...` are the local bridge to the readMenu/editMenu Lambda handlers.

### Public — no auth

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serve the single-page UI |
| GET | `/health` | Health check |
| GET | `/api/allergen-categories` | The FSANZ PEAL category list |
| GET | `/api/languages` | Configured translation languages |
| POST | `/api/allergens/extract` | Allergen extraction for a dish (name/description) |
| POST | `/api/compliance/verify` | Compliance verdict for a dish (runs extraction first if no allergens supplied) |
| GET | `/api/menus` | List menu_ids (legacy; scans dish rows) |
| GET | `/api/menus/<menu_id>/items` | List a menu's items (direct Flask) |
| POST | `/api/menus/<menu_id>/items` | Add a dish manually → runs the full pipeline |
| POST | `/api/menus/<menu_id>/upload` | Upload a menu file → OCR → pipeline per dish |
| POST | `/api/menus/<menu_id>/seed` | Seed the 8 sample dishes (writes to the REAL table) |
| DELETE | `/api/menus/<menu_id>` | Delete all dishes under a menu ("Clear dishes") |
| DELETE | `/api/menus/<menu_id>/items/<item_id>` | Delete one dish |
| PATCH | `/api/menus/<menu_id>/items/<item_id>` | Direct-Flask human override of a dish |
| GET | `/api/v2/restaurants` | List restaurants from registry rows (readMenu bridge) |
| GET | `/api/v2/menus/<restaurant_id>` | List a restaurant's dishes (readMenu bridge) |
| GET | `/api/v2/menus/<upload_id>/status` | Poll upload status sentinel (readMenu bridge) |
| POST | `/api/v2/auth/login` | Local fake admin login → returns a placeholder token |

### Requires admin auth

| Method | Path | Purpose | Auth as built today |
|---|---|---|---|
| PATCH | `/api/v2/menus/<menu_id>/items/<item_id>` | Edit a dish via the editMenu Lambda bridge | Requires `Authorization: Bearer <local-fake-admin-token>` from `/api/v2/auth/login` |

Note the two direct-Flask write routes used by the modal's "Delete dish" and the "Clear dishes" button (`DELETE /api/menus/...`) currently have **no** auth check — only the `/api/v2/...` PATCH edit path enforces the local token. See Known limitations.

## Team roles

- **Vedant** — OCR / upload / translation pipeline (`textract_service`, `menu_parser`, upload flow, `bedrock_service.translate_dish`).
- **Suresh** — readMenu / editMenu Lambdas, DynamoDB schema, restaurant registry, authentication (Cognito Terraform), and the frontend.
- **Leo** — allergen extraction / Bedrock (`bedrock_service.extract_allergens`, allergen reasoning).
- **Banu / Dat Dao** — translation.

## Known limitations (honest list)

- **Upload flow does not write the status-polling sentinel.** `readMenu`'s `GET /menus/{uploadId}/status` expects an `upload#<uploadId>` sentinel row (`record_type: "upload_status"`, a `status` field). The current synchronous upload path in `application.py` never writes such a row — it processes and returns dishes in one request. So status polling against a Flask-driven upload would 404. This is a documented contract gap, not a crash.
- **Admin login is a local, insecure placeholder.** `/api/v2/auth/login` accepts a hardcoded `admin` / `localtest123` and hands back a fixed fake token; the bridge PATCH route just checks for that token string. The **real** authentication — a Cognito User Pool + JWT authorizer on the PATCH route — is fully written in `terraform/cognito.tf` and `apigateway.tf` but is **not deployed and not wired into the frontend**.
- **Two parallel dish-edit paths exist, intentionally.** The old direct-Flask `PATCH /api/menus/.../items/...` and the new Lambda-bridge `PATCH /api/v2/menus/.../items/...` both edit dishes. This coexistence is deliberate during the migration, not a bug.
- **Restaurant creation is manual / CLI-only.** No admin UI or write endpoint creates registry rows yet; they're seeded by hand.
- **The Bedrock Knowledge Base is opt-in.** Off by default (`var.create_knowledge_base = false`); RAG falls back to local `docs/` search until a KB is provisioned and `KNOWLEDGE_BASE_ID` is set.
- **The local bridge is scaffolding.** Importing the Lambda handlers into the Flask process is a testing convenience, not the deployment model.
- **The Cognito app client allows `USER_PASSWORD_AUTH`.** Fine for a shared demo credential; revisit before real multi-user use.

## Next steps

1. **Deploy the Lambda / API Gateway / Cognito Terraform for real** (`lambda.tf`, `lambda_iam.tf`, `apigateway.tf`, `cognito.tf`) so readMenu/editMenu run as actual Lambdas behind API Gateway.
2. **Retire the local bridge** — once the Lambdas are live, point the frontend at the API Gateway URL and remove the `importlib` bridge and `/api/v2/...` shims from `application.py`.
3. **Wire real Cognito into the frontend** — replace the fake `/api/v2/auth/login` + placeholder token with a real Cognito login that obtains a JWT, and send that JWT on the (now authorizer-protected) PATCH edit route.
4. **Build an admin UI for restaurant creation** so registry rows aren't hand-seeded.
5. **Decide on the upload-sentinel gap** — either have the upload path write the `upload#` status sentinel readMenu expects, or formally drop the status-polling endpoint from scope.
6. **Consolidate the two dish-edit paths** once the Lambda path is the source of truth.
