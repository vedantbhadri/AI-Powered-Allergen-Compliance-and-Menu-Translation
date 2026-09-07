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
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory

from services import (
    allergen_rules,
    bedrock_service,
    dynamo_service,
    menu_parser,
    s3_service,
    textract_service,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SAMPLE_DATA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sample_data", "sample_menu.json"
)

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


# ---------------------------------------------------------------- core pipeline
def _run_pipeline(
    menu_id: str,
    name: str,
    description: str,
    source: str,
    persist: bool = True,
) -> dict:
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
    return dynamo_service.put_item(item) if persist else item


@application.route("/api/menus")
def get_menus():
    return jsonify({"menus": dynamo_service.list_menus()})


@application.route("/api/menus/<menu_id>", methods=["DELETE"])
def delete_menu(menu_id):
    deleted_count = dynamo_service.delete_menu(menu_id)
    return jsonify({"menu_id": menu_id, "deleted_count": deleted_count})


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
    """Convert Textract lines into dish cards using menu-aware heuristics."""
    return menu_parser.parse_ocr_lines(lines)


@application.route("/api/menus/<menu_id>/upload", methods=["POST"])
def upload_menu(menu_id):
    if "file" not in request.files:
        return jsonify({"error": "multipart file field 'file' is required"}), 400
    file = request.files["file"]
    file_bytes = file.read()
    content_type = file.content_type or "application/octet-stream"

    stored_path = s3_service.upload_raw_file(file_bytes, file.filename, content_type)
    lines = textract_service.extract_text_from_bytes(file_bytes, content_type)
    if not lines:
        return jsonify({
            "error": "No text could be extracted from this file. Try a clearer, "
                     "upright JPG or PNG with readable menu text.",
            "stored_path": stored_path,
        }), 422
    dishes = _split_ocr_lines_into_dishes(lines)
    if not dishes:
        return jsonify({
            "error": "Text was detected, but no menu dishes could be identified.",
            "ocr_lines": lines,
            "stored_path": stored_path,
        }), 422

    valid_dishes = [dish for dish in dishes if dish["name"]]
    worker_count = min(int(os.environ.get("PIPELINE_WORKERS", "4")), len(valid_dishes))
    with ThreadPoolExecutor(max_workers=max(worker_count, 1)) as executor:
        analyzed = executor.map(
            lambda dish: _run_pipeline(
                menu_id,
                dish["name"],
                dish["description"],
                source="upload",
                persist=False,
            ),
            valid_dishes,
        )
        # Keep persistence sequential: boto3 resources are not guaranteed to
        # be thread-safe, and map preserves the visual order from the menu.
        created = [dynamo_service.put_item(item) for item in analyzed]

    return jsonify({"stored_path": stored_path, "ocr_lines": lines, "items": created}), 201


# ---------------------------------------------------------------- sample data
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
