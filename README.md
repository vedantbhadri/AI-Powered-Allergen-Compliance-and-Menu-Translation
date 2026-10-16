# AI-Powered Allergen Compliance and Menu Translation

This project is a working prototype for New Zealand cafés and restaurants. It turns an uploaded menu image or PDF into a structured, multilingual menu with clear allergen information.

Restaurant staff can upload a menu or add a dish manually. The application reads the menu, identifies dishes, checks them for allergens, translates them into four languages, and saves the results for diners to browse. Staff can review and correct the output before it is published.

Because allergen information is safety-critical, the application does not rely on AI alone. Amazon Bedrock is cross-checked against a deterministic FSANZ Standard 1.2.3 rules engine, with optional regulatory retrieval and human review.

> **Prototype notice:** This application supports allergen review, but it is not a substitute for checking recipes, ingredient labels, supplier information, or advice from a qualified food-safety professional.

## What the application does

- Accepts menu images and single-page PDFs.
- Uses Amazon Textract to extract text from uploaded menus.
- Separates dish names and descriptions from headings, prices, and contact details.
- Detects potential allergens using Amazon Bedrock and an FSANZ rules engine.
- Optionally retrieves supporting regulatory context through a Bedrock Knowledge Base.
- Translates dishes into Spanish, German, Japanese, and Simplified Chinese.
- Shows clear `Contains ...` allergen labels and dietary filters.
- Allows staff to review dishes and correct allergens or translations.
- Stores menu files in Amazon S3 and processed results in Amazon DynamoDB.
- Supports multiple restaurants, including restaurants that do not yet have any dishes.

## Before you begin: the current architecture

The repository is partway through a migration from a Flask application to an event-driven Lambda architecture. The pieces currently sit in three groups:

### Live application

The working system is the Flask application in `app/application.py`, hosted on AWS Elastic Beanstalk. It runs the full process in one request:

```text
upload -> OCR -> dish parsing -> allergen checks -> compliance -> translation -> storage
```

When AWS mode is enabled, this application connects to the team's real S3 bucket, DynamoDB table, Textract service, and Bedrock models.

### Lambda migration work

Two Lambda handlers are complete and tested:

- `build/read_menu/handler.py`
- `build/edit_menu/handler.py`

Terraform definitions for these functions, API Gateway, IAM, and Cognito are included in the repository. Their deployment has not been confirmed, so they should not be described as the current live backend.

### Temporary Flask-to-Lambda bridge

The Flask app imports the two Lambda handlers and exposes them through `/api/v2/...` routes. It creates API-Gateway-shaped events and calls the handlers inside the Flask process.

This bridge makes it possible to test the Lambda code before deployment. It is temporary scaffolding, not the intended production design.

The target architecture contains six event-driven Lambda functions coordinated through status values in DynamoDB. The full design is documented in `beanstalk-to-lambda-migration/`. The implemented read/edit portion is documented in `menu-read-edit-lambdas/`.

## How a menu upload is processed

1. A staff member selects a restaurant and uploads a PDF, JPG, PNG, or WebP menu.
2. The browser converts WebP files to JPEG because Textract does not accept WebP directly.
3. The original file is stored in Amazon S3 for auditing or reprocessing.
4. Amazon Textract extracts the visible text as individual lines.
5. `menu_parser.py` identifies dishes using menu structure and price patterns. It joins wrapped descriptions and filters out headings, contact details, and other non-menu text.
6. Each dish is checked by Amazon Bedrock and the deterministic FSANZ rules engine.
7. The optional RAG layer retrieves regulatory context from a Bedrock Knowledge Base or the bundled local documents.
8. The allergen results are combined, and any differences between the checks are saved for staff review.
9. Amazon Bedrock translates the dish into the four supported languages. Amazon Translate is used as a fallback if the Bedrock translation fails.
10. The completed dish, translations, allergen information, dietary tags, and review status are stored in DynamoDB.

Uploaded dishes are analysed concurrently to reduce processing time. DynamoDB writes are completed after the parallel analysis finishes.

## Allergen and compliance checks

Allergen processing is split between two services.

### Deterministic rules

`app/services/allergen_rules.py` contains the FSANZ Standard 1.2.3 PEAL categories and the keyword-based rules used to detect allergens. It also:

- reconciles results from multiple detectors;
- converts categories into labels such as `Contains Milk`;
- derives the gluten-free and dairy-free filters; and
- marks vegan dishes only when the menu describes them as vegan or containing no animal products.

The rules are suitable for a prototype and must be reviewed by a food-safety professional before commercial use.

### AI extraction and compliance

`app/services/allergen_service.py` handles:

- deterministic keyword extraction;
- Bedrock-based extraction;
- compliance verdicts: `COMPLIANT`, `ACTION_REQUIRED`, or `UNVERIFIED`;
- retrieval of regulatory context; and
- the final merge of the available signals.

The final allergen set is the union of:

1. categories identified by the Bedrock model;
2. categories identified by the deterministic rules; and
3. categories returned by regulatory retrieval.

This approach favours caution by reducing the chance of under-declaring an allergen. Differences between the three checks are kept so a staff member can review them.

### Optional RAG support

The RAG layer is optional and is disabled by default in Terraform.

- If `KNOWLEDGE_BASE_ID` is set and the application is using AWS mode, the service retrieves context from an Amazon Bedrock Knowledge Base.
- Otherwise, it searches the Markdown files in `docs/` locally.

The application therefore continues to work when a Knowledge Base has not been provisioned. In `terraform/bedrock_kb.tf`, all Knowledge Base, OpenSearch, and related IAM resources are controlled by `var.create_knowledge_base`, which defaults to `false`.

## Restaurant records in DynamoDB

Restaurants are stored independently from their dishes. Each restaurant has a registry row:

```json
{
  "menu_id": "<restaurantId>",
  "item_id": "restaurant#<restaurantId>",
  "record_type": "restaurant",
  "name": "<Display Name>"
}
```

This allows a restaurant to remain visible in the café picker even after all of its dishes have been cleared.

`GET /restaurants` reads these registry rows instead of trying to infer restaurants from dish data. `GET /menus/{restaurantId}` filters out restaurant registry rows and upload-status rows so that only dishes are returned.

Restaurant creation is currently manual. There is no admin screen or API endpoint for it, so registry rows must be added through the AWS CLI or another DynamoDB tool. If no registry rows exist, the restaurant list will be empty.

## Project structure

```text
app/
  application.py              Flask application and API routes
  services/
    allergen_rules.py         Deterministic FSANZ rules
    allergen_service.py       Extraction, compliance, and RAG
    bedrock_service.py        Bedrock allergen and translation calls
    dynamo_service.py         DynamoDB and local JSON storage
    menu_parser.py            OCR line-to-dish parsing
    s3_service.py             Uploaded-file storage
    textract_service.py       Textract OCR integration
  static/                     Single-page frontend

build/
  read_menu/                  Read-menu Lambda handler and tests
  edit_menu/                  Edit-menu Lambda handler and tests

terraform/                    AWS infrastructure definitions
docs/                         FSANZ and NZ food-labelling references
beanstalk-to-lambda-migration/ Full six-Lambda migration design
menu-read-edit-lambdas/        Read/edit Lambda design and requirements
tests/                        Flask-service tests
```

## Running the application

### Requirements

- Python 3.12
- `pip`
- AWS CLI for AWS-connected mode
- Valid AWS credentials and access to the project's AWS resources

### 1. Clone and install

```bash
git clone https://github.com/vedantbhadri/AI-Powered-Allergen-Compliance-and-Menu-Translation.git
cd AI-Powered-Allergen-Compliance-and-Menu-Translation

python3 -m venv app/.venv
source app/.venv/bin/activate
pip install -r app/requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.\app\.venv\Scripts\Activate.ps1
```

### 2. Choose a run mode

#### Local/offline mode

Local mode does not call Textract or Bedrock. AWS services are replaced with local fallbacks, and application data is stored in a local JSON file.

```bash
export LOCAL_MODE=true
cd app
python application.py
```

This mode is useful for exploring the UI and deterministic rules, but it cannot perform real OCR or AI processing.

#### AWS-connected mode

> **Warning:** AWS-connected mode uses shared, live resources. Uploading, editing, seeding, or deleting menu items can affect data used by other team members.

The team uses temporary AWS SSO credentials. Export all three values in the terminal that will run the server:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."
```

Check that the credentials work:

```bash
aws sts get-caller-identity
```

Then start the application:

```bash
export LOCAL_MODE=false
cd app
python application.py
```

Open [http://localhost:8000](http://localhost:8000).

Elastic Beanstalk runs the production application with Gunicorn using `app/Procfile`; `python application.py` is only the development server.

## AWS configuration

The project uses resources in two AWS regions:

| Service | Environment variable | Current default |
|---|---|---|
| Bedrock inference | `BEDROCK_REGION` / `AWS_REGION` | `ap-southeast-2` |
| Bedrock Knowledge Base | `KB_REGION` | `ap-southeast-2` |
| DynamoDB | `DYNAMODB_REGION` | `us-east-1` |
| Amazon S3 | `S3_REGION` | `us-east-1` |
| Amazon Textract | `TEXTRACT_REGION` | `us-east-1` |

`application.py` currently sets the following shared resource names before importing the service modules:

- `DYNAMODB_TABLE` and `MENU_TABLE_NAME`: `allergen-demo-dev-menu-items`
- `S3_BUCKET`: `allergen-demo-dev-menu-uploads-669232219904`

Two DynamoDB environment-variable names are used because the Flask service and the edit-menu Lambda handler expect different names. Both point to the same table.

The Bedrock model is selected with `BEDROCK_MODEL_ID`. An environment variable set in the shell or by Elastic Beanstalk takes priority over the application's default.

See `.env.example`, `ONBOARDING.md`, and `setup_aws_env.ps1` for the complete environment reference and Windows setup instructions.

## User interface

The frontend is a single-page application built with Bootstrap 5.3.3 and custom styles. It has two main views.

### Café picker

The first screen lists restaurants returned by `GET /api/v2/restaurants`. Selecting a café opens its menu.

### Menu view

The main view includes:

- a language selector for English, Spanish, German, Japanese, and Simplified Chinese;
- a button for changing cafés;
- allergen labels and dietary filters;
- an admin login/logout control; and
- a responsive dish grid.

Users are logged out by default. After an admin logs in, the management panel becomes visible and provides menu upload, manual dish creation, FSANZ reference information, and a **Clear dishes** action. Admins can also open a dish to review or edit it.

When logged out, the management column is removed and the menu expands to a single-column layout. The header and controls adjust for tablet and phone screens.

An existing restaurant with no dishes is shown as an empty menu rather than an error. WebP images are converted to JPEG in the browser before upload.

## API reference

All routes are currently served by Flask:

- `/api/...` routes call the original Flask implementation directly.
- `/api/v2/...` routes call the read/edit Lambda handlers through the temporary local bridge.

### Public routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Serve the single-page application |
| `GET` | `/health` | Return application health and run mode |
| `GET` | `/api/allergen-categories` | Return the FSANZ PEAL categories |
| `GET` | `/api/languages` | Return the supported translation languages |
| `POST` | `/api/allergens/extract` | Extract allergens from a dish name and description |
| `POST` | `/api/compliance/verify` | Verify compliance; performs extraction if allergens are not supplied |
| `GET` | `/api/menus` | List menu IDs using the legacy dish-row scan |
| `GET` | `/api/menus/<menu_id>/items` | List menu items through Flask |
| `POST` | `/api/menus/<menu_id>/items` | Add a dish manually and run the full pipeline |
| `POST` | `/api/menus/<menu_id>/upload` | Upload a file, run OCR, and process the detected dishes |
| `POST` | `/api/menus/<menu_id>/seed` | Add the eight sample dishes to the real table in AWS mode |
| `DELETE` | `/api/menus/<menu_id>` | Clear the dishes belonging to a menu |
| `DELETE` | `/api/menus/<menu_id>/items/<item_id>` | Delete one dish |
| `PATCH` | `/api/menus/<menu_id>/items/<item_id>` | Update a dish through Flask |
| `GET` | `/api/v2/restaurants` | List restaurants from registry rows through the read-menu bridge |
| `GET` | `/api/v2/menus/<restaurant_id>` | List a restaurant's dishes through the read-menu bridge |
| `GET` | `/api/v2/menus/<upload_id>/status` | Read an upload-status row through the read-menu bridge |
| `POST` | `/api/v2/auth/login` | Return the temporary local admin token |

### Admin-protected route

| Method | Route | Purpose | Current authentication |
|---|---|---|---|
| `PATCH` | `/api/v2/menus/<menu_id>/items/<item_id>` | Edit a dish through the edit-menu bridge | `Authorization: Bearer <local-fake-admin-token>` |

The local token comes from `/api/v2/auth/login`. The direct Flask delete and clear routes currently have no backend authentication check, even though their controls are hidden until login in the UI.

## Team contributions

- **Vedant:** OCR, menu upload and parsing, translation flow, and `bedrock_service.translate_dish`.
- **Suresh:** read/edit Lambda handlers, DynamoDB schema, restaurant registry, Cognito Terraform, and frontend.
- **Leo:** Bedrock allergen extraction and allergen reasoning.
- **Banu and Dat Dao:** translation.

## Known limitations

### Upload status is not written by the Flask upload flow

The read-menu status endpoint expects an `upload#<uploadId>` row with `record_type: "upload_status"` and a `status` value. The synchronous Flask upload route processes all dishes and returns them directly, but it does not create this row. Polling the status of a Flask upload therefore returns `404`.

### Admin authentication is only a local placeholder

`/api/v2/auth/login` accepts the demonstration credentials `admin` / `localtest123` and returns a fixed token. The edit bridge checks only that token string.

Terraform includes a Cognito User Pool and an API Gateway JWT authorizer for the edit route, but they have not been confirmed as deployed and are not connected to the frontend.

### Two edit routes are active

The direct Flask route and the Lambda-bridge route can both edit dishes:

- `/api/menus/.../items/...`
- `/api/v2/menus/.../items/...`

This duplication is intentional during the migration and should be removed when one implementation becomes the source of truth.

### Restaurant creation is manual

There is no admin interface or API for creating restaurant registry rows. They must currently be added through the AWS CLI or another DynamoDB tool.

### The Bedrock Knowledge Base is optional

`var.create_knowledge_base` defaults to `false`. Until a Knowledge Base is provisioned and `KNOWLEDGE_BASE_ID` is configured, regulatory retrieval uses the local files in `docs/`.

### The local Lambda bridge is temporary

The Lambda handlers are imported into Flask only to support local integration testing. Production Lambda functions should run behind API Gateway.

### Cognito uses `USER_PASSWORD_AUTH`

This is acceptable for the shared demonstration account but should be reviewed before introducing individual production users.

## Next steps

1. Deploy and verify the Lambda, API Gateway, IAM, and Cognito Terraform.
2. Point the frontend at the deployed API Gateway and remove the in-process Lambda bridge.
3. Replace the temporary login endpoint and token with a real Cognito login and JWT.
4. Add an admin workflow for creating restaurant registry rows.
5. Either add upload-status rows to the upload flow or remove the unused status endpoint.
6. Consolidate the two dish-edit implementations.

## Additional documentation

- `ONBOARDING.md` — local and AWS setup
- `DEPLOY_GUIDE.md` — Terraform and Elastic Beanstalk deployment
- `INTEGRATION_REPORT.md` — integration history
- `docs/allergen-api.md` — allergen and compliance API details
- `docs/knowledge-base-setup.md` — Bedrock Knowledge Base setup
- `docs/knowledge-base-usage.md` — RAG usage and verification
- `docs/nz_peal_allergens.md` — PEAL category reference

