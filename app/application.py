"""
application.py

Elastic Beanstalk entry point. The Procfile runs:
    gunicorn --workers=3 --bind 127.0.0.1:8000 application:application

This single Flask app replaces the Amplify + API Gateway + Lambda chain
from the original architecture proposal: Beanstalk's EC2 instances run
this app directly, so the "Step 1 (Analyze & Compliance) / Step 2
(Translate)" two-step chain is just two function calls inside one
request handler instead of two separate Lambda invocations.

Per project scope: no knowledge-base / RAG lookup is used anywhere in
this app - allergen compliance is a deterministic rules engine
(services/allergen_rules.py) reconciled against a Bedrock LLM pass with
the category list embedded directly in the prompt.
"""
from __future__ import annotations
import json
import logging
import os
import sys

# --- make the new Lambda code reachable from this old Flask app -----------
# "sys.path.insert" just tells Python "also look in these folders" when
# something does `import X` or `from X import Y`.
sys.path.insert(0, "../build/layer/python")  # so `from services import ...` resolves
sys.path.insert(0, "../build/read_menu")
sys.path.insert(0, "../build/edit_menu")

# Both readMenu and editMenu were generated into files that are literally
# both named "handler.py". Python can only remember ONE thing called
# "handler" at a time, so a plain `import handler` twice would secretly
# give you the same file both times. importlib lets us load each file by
# its exact path instead, so they stay two separate, correct modules.
import importlib.util

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

read_menu_handler = _load_module("read_menu_handler", "../build/read_menu/handler.py")
edit_menu_handler = _load_module("edit_menu_handler", "../build/edit_menu/handler.py")

from flask import Flask, jsonify, request, send_from_directory

from services import allergen_rules, bedrock_service, dynamo_service, s3_service, textract_service

# --- fake AWS for local testing --------------------------------------------
# "moto" pretends to be real AWS. Once mock_aws() is started, ANY boto3
# call anywhere in this whole program (DynamoDB, S3, Bedrock, etc.) gets
# quietly redirected to an in-memory fake instead of the real internet.
# This is what lets you test without an AWS account or spending anything.
from moto import mock_aws

# All fake AWS resources must agree on the same region, or DynamoDB will
# say "resource not found" even though the table genuinely exists - it
# will just be sitting in the "wrong" pretend region.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["MENU_TABLE_NAME"] = "allergen-menu-items"

_mock = mock_aws()
_mock.start()

import boto3
_ddb = boto3.resource("dynamodb", region_name="us-east-1")

# Create the fake DynamoDB table itself - same key design as the real one:
# menu_id (which restaurant/menu) + item_id (which dish inside it).
_ddb.create_table(
    TableName="allergen-menu-items",
    KeySchema=[
        {"AttributeName": "menu_id", "KeyType": "HASH"},   # partition key
        {"AttributeName": "item_id", "KeyType": "RANGE"},  # sort key
    ],
    AttributeDefinitions=[
        {"AttributeName": "menu_id", "AttributeType": "S"},
        {"AttributeName": "item_id", "AttributeType": "S"},
    ],
    BillingMode="PAY_PER_REQUEST",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Where the 8 real sample dishes (Peanut Satay, Seafood Chowder, etc.) live.
# This MUST be defined before anything tries to open this file - that
# ordering mistake was the bug we fixed earlier.
SAMPLE_DATA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sample_data", "sample_menu.json"
)

# --- seed the fake table with the real sample menu -------------------------
# Reads the same sample_menu.json the old "Load Sample Menu" button uses,
# and puts each dish into the fake table under "kiwi-cafe-queenstown" -
# the exact menu_id the frontend (app.js) already expects.
# Allergens/translations are left empty here since this just loads the raw
# menu; the AI pipeline (Leo's/Banu's work) is what would normally fill
# those in.
with open(SAMPLE_DATA_PATH, "r") as f:
    _sample = json.load(f)

_menu_table = _ddb.Table("allergen-menu-items")
for i, dish in enumerate(_sample["items"]):
    _menu_table.put_item(Item={
        "menu_id": "kiwi-cafe-queenstown",
        "item_id": f"dish-{i:04d}",
        "name": dish["name"],
        "description": dish["description"],
        "status": "ready",
        "allergens": {"confirmed": [], "display_tags": []},
        "diet_tags": [],
        "translations": {},
    })

application = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app = application  # alias - some tooling/tests look for `app`

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
application.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


# ---------------------------------------------------------------- static UI
@application.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@application.route("/health")
def health():
    return jsonify({"status": "ok", "local_mode": os.environ.get("LOCAL_MODE", "false")})


# ---------------------------------------------------------------- reference data
@application.route("/api/allergen-categories")
def allergen_categories():
    return jsonify({"categories": allergen_rules.PEAL_CATEGORIES})


@application.route("/api/languages")
def languages():
    return jsonify({"languages": bedrock_service.LANGUAGES})


# ---------------------------------------------------------------- NEW: readMenu / editMenu bridge
# These routes are the bridge to Suresh's new Lambda-style functions.
# They don't contain any real logic themselves - they just build a fake
# "API Gateway event" dictionary (the same shape real API Gateway would
# send), hand it to the real handler function, and pass the response back.

@application.route("/api/v2/menus/<restaurant_id>")
def v2_get_menu(restaurant_id):
    event = {
        "routeKey": "GET /menus/{restaurantId}",
        "pathParameters": {"restaurantId": restaurant_id},
    }
    result = read_menu_handler.handler(event)
    return result["body"], result["statusCode"], {"Content-Type": "application/json"}


@application.route("/api/v2/menus/<upload_id>/status")
def v2_get_status(upload_id):
    event = {
        "routeKey": "GET /menus/{uploadId}/status",
        "pathParameters": {"uploadId": upload_id},
    }
    result = read_menu_handler.handler(event)
    return result["body"], result["statusCode"], {"Content-Type": "application/json"}


@application.route("/api/v2/restaurants")
def v2_list_restaurants():
    event = {
        "routeKey": "GET /restaurants",
        "pathParameters": {},
    }
    result = read_menu_handler.handler(event)
    return result["body"], result["statusCode"], {"Content-Type": "application/json"}


# for the login
# ROUGH LOCAL APPROXIMATION ONLY - NOT REAL AUTH. Real auth is Cognito's JWT
# authorizer in terraform/apigateway.tf, enforced entirely inside API Gateway.
LOCAL_FAKE_ADMIN_TOKEN = "local-fake-admin-token"

@application.route("/api/v2/auth/login", methods=["POST"])
def v2_fake_login():
    body = request.get_json(force=True) or {}
    if body.get("username") == "admin" and body.get("password") == "localtest123":
        return jsonify({"token": LOCAL_FAKE_ADMIN_TOKEN})
    return jsonify({"error": "invalid credentials"}), 401


@application.route("/api/v2/menus/<menu_id>/items/<item_id>", methods=["PATCH"])
def v2_edit_item(menu_id, item_id):
    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {LOCAL_FAKE_ADMIN_TOKEN}":
        return jsonify({"error": "unauthorized - login as admin first"}), 401

    event = {
        "pathParameters": {"menuId": menu_id, "itemId": item_id},
        "body": request.get_data(as_text=True),
        "isBase64Encoded": False,
    }
    result = edit_menu_handler.handler(event)
    return result["body"], result["statusCode"], {"Content-Type": "application/json"}


# ---------------------------------------------------------------- OLD core pipeline (Bedrock + rules)
def _run_pipeline(menu_id: str, name: str, description: str, source: str) -> dict:
    """Shared two-step chain: (1) allergen analyze+verify (2) translate."""
    llm_result = bedrock_service.extract_allergens(name, description)
    rule_categories = allergen_rules.scan_text_for_allergens(f"{name} {description}")
    reconciled = allergen_rules.reconcile_allergens(llm_result.get("categories", []), rule_categories)
    confirmed = reconciled["confirmed"]

    translations = bedrock_service.translate_dish(name, description)

    item = {
        "menu_id": menu_id,
        "name": name,
        "description": description,
        "source": source,
        "status": "ai_verified",
        "allergens": {
            "confirmed": confirmed,
            "display_tags": allergen_rules.to_display_tags(confirmed),
            "llm_reasoning": llm_result.get("reasoning", ""),
            "llm_source": llm_result.get("source", "bedrock"),
            "disagreements": {
                "llm_only": reconciled["llm_only"],
                "rule_only": reconciled["rule_only"],
            },
        },
        "diet_tags": allergen_rules.derive_diet_tags(confirmed, f"{name} {description}"),
        "translations": translations,
    }
    return dynamo_service.put_item(item)


# ---------------------------------------------------------------- OLD menu routes
@application.route("/api/menus")
def get_menus():
    return jsonify({"menus": dynamo_service.list_menus()})


@application.route("/api/menus/<menu_id>/items", methods=["GET"])
def get_items(menu_id):
    return jsonify({"items": dynamo_service.list_items(menu_id)})


@application.route("/api/menus/<menu_id>/items", methods=["POST"])
def create_item(menu_id):
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    item = _run_pipeline(menu_id, name, description, source="manual")
    return jsonify({"item": item}), 201


@application.route("/api/menus/<menu_id>/items/<item_id>", methods=["PATCH"])
def update_item(menu_id, item_id):
    """Human-in-the-loop override: kitchen manager corrects allergen tags
    or translations after AI verification."""
    existing = dynamo_service.get_item(menu_id, item_id)
    if not existing:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(force=True) or {}
    if "confirmed_allergens" in body:
        confirmed = [c for c in body["confirmed_allergens"] if c in allergen_rules.PEAL_CATEGORIES]
        existing["allergens"]["confirmed"] = confirmed
        existing["allergens"]["display_tags"] = allergen_rules.to_display_tags(confirmed)
        existing["diet_tags"] = allergen_rules.derive_diet_tags(confirmed)
    if "translations" in body:
        existing["translations"].update(body["translations"])
    if "name" in body:
        existing["name"] = body["name"]
    if "description" in body:
        existing["description"] = body["description"]
    existing["status"] = "human_verified"
    existing["item_id"] = item_id
    existing["menu_id"] = menu_id
    saved = dynamo_service.put_item(existing)
    return jsonify({"item": saved})


@application.route("/api/menus/<menu_id>/items/<item_id>", methods=["DELETE"])
def delete_item(menu_id, item_id):
    dynamo_service.delete_item(menu_id, item_id)
    return jsonify({"deleted": item_id})


# ---------------------------------------------------------------- upload / OCR
def _split_ocr_lines_into_dishes(lines: list[str]) -> list[dict]:
    """Very simple heuristic: treat each non-empty line as a dish name and
    the following line as its description, alternating. Real menu layouts
    vary a lot - this is a starting point matching the mock-up's
    "name + description" card format and is designed to be improved
    with the human-in-the-loop editor rather than perfected here."""
    cleaned = [l.strip() for l in lines if l.strip()]
    dishes = []
    i = 0
    while i < len(cleaned):
        name = cleaned[i]
        description = cleaned[i + 1] if i + 1 < len(cleaned) else ""
        dishes.append({"name": name, "description": description})
        i += 2
    return dishes


@application.route("/api/menus/<menu_id>/upload", methods=["POST"])
def upload_menu(menu_id):
    if "file" not in request.files:
        return jsonify({"error": "multipart file field 'file' is required"}), 400
    file = request.files["file"]
    file_bytes = file.read()
    content_type = file.content_type or "application/octet-stream"

    stored_path = s3_service.upload_raw_file(file_bytes, file.filename, content_type)
    lines = textract_service.extract_text_from_bytes(file_bytes, content_type)
    dishes = _split_ocr_lines_into_dishes(lines)

    created = []
    for dish in dishes:
        if not dish["name"]:
            continue
        item = _run_pipeline(menu_id, dish["name"], dish["description"], source="upload")
        created.append(item)

    return jsonify({"stored_path": stored_path, "ocr_lines": lines, "items": created}), 201


# ---------------------------------------------------------------- sample data (OLD button)
# NOTE: this button calls the REAL Bedrock API (bedrock_service.extract_allergens
# inside _run_pipeline). Since moto is mocking ALL of boto3 right now, and moto
# doesn't yet support Bedrock's "converse" operation, clicking this button will
# fail with NotImplementedError. That's expected for now - it's unrelated to
# readMenu/editMenu and not something you need working today.
@application.route("/api/menus/<menu_id>/seed", methods=["POST"])
def seed_sample_menu(menu_id):
    """Loads sample_data/sample_menu.json and runs every dish through the
    full pipeline - used to demo the system without needing a real
    upload, and by the local smoke-test script."""
    with open(SAMPLE_DATA_PATH, "r") as f:
        sample = json.load(f)

    created = []
    for dish in sample["items"]:
        item = _run_pipeline(menu_id, dish["name"], dish["description"], source="sample")
        created.append(item)
    return jsonify({"menu_id": menu_id, "items": created}), 201


if __name__ == "__main__":
    # Local dev server only - Beanstalk uses gunicorn via the Procfile.
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)