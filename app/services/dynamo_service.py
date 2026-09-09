"""
dynamo_service.py

Persistence layer for menus/menu items and their allergen + translation
metadata. Uses DynamoDB in AWS; falls back to a local JSON file when
LOCAL_MODE=true (no AWS account needed) so the UI can be smoke-tested
before/without a real deployment.

Table design (single-table):
  PK: menu_id (str)          - e.g. "kiwi-cafe-queenstown"
  SK: item_id (str)          - e.g. "dish-0001"
  Other attributes: name, description, category, allergens (list),
                    diet_tags (list), translations (map), status,
                    updated_at, source ("sample" | "upload")
"""
from __future__ import annotations
import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "allergen-menu-items")
LOCAL_MODE = os.environ.get("LOCAL_MODE", "false").lower() == "true"
LOCAL_DB_PATH = os.environ.get("LOCAL_DB_PATH", "/tmp/allergen_local_db.json")

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)
    return _table


# ---------------------------------------------------------------- local fallback
def _load_local() -> List[Dict]:
    if not os.path.exists(LOCAL_DB_PATH):
        return []
    with open(LOCAL_DB_PATH, "r") as f:
        return json.load(f)


def _save_local(items: List[Dict]) -> None:
    with open(LOCAL_DB_PATH, "w") as f:
        json.dump(items, f, indent=2)


# ---------------------------------------------------------------- public API
def put_item(item: Dict) -> Dict:
    item.setdefault("item_id", f"dish-{uuid.uuid4().hex[:8]}")
    item["updated_at"] = int(time.time())

    if LOCAL_MODE:
        items = _load_local()
        items = [i for i in items if not (i["menu_id"] == item["menu_id"] and i["item_id"] == item["item_id"])]
        items.append(item)
        _save_local(items)
        return item

    try:
        _get_table().put_item(Item=item)
        return item
    except (BotoCoreError, ClientError) as exc:
        logger.error("DynamoDB put_item failed: %s", exc)
        raise


def list_items(menu_id: str) -> List[Dict]:
    if LOCAL_MODE:
        return [i for i in _load_local() if i["menu_id"] == menu_id]

    try:
        response = _get_table().query(KeyConditionExpression=Key("menu_id").eq(menu_id))
        return response.get("Items", [])
    except (BotoCoreError, ClientError) as exc:
        logger.error("DynamoDB query failed: %s", exc)
        raise


def get_item(menu_id: str, item_id: str) -> Optional[Dict]:
    if LOCAL_MODE:
        for i in _load_local():
            if i["menu_id"] == menu_id and i["item_id"] == item_id:
                return i
        return None

    try:
        response = _get_table().get_item(Key={"menu_id": menu_id, "item_id": item_id})
        return response.get("Item")
    except (BotoCoreError, ClientError) as exc:
        logger.error("DynamoDB get_item failed: %s", exc)
        raise


def delete_item(menu_id: str, item_id: str) -> None:
    if LOCAL_MODE:
        items = [i for i in _load_local() if not (i["menu_id"] == menu_id and i["item_id"] == item_id)]
        _save_local(items)
        return

    try:
        _get_table().delete_item(Key={"menu_id": menu_id, "item_id": item_id})
    except (BotoCoreError, ClientError) as exc:
        logger.error("DynamoDB delete_item failed: %s", exc)
        raise


def delete_menu(menu_id: str) -> int:
    """Delete every dish belonging to a menu and return the deleted count."""
    if LOCAL_MODE:
        items = _load_local()
        remaining = [item for item in items if item["menu_id"] != menu_id]
        deleted_count = len(items) - len(remaining)
        _save_local(remaining)
        return deleted_count

    try:
        table = _get_table()
        keys = []
        query_args = {
            "KeyConditionExpression": Key("menu_id").eq(menu_id),
            "ProjectionExpression": "menu_id, item_id",
        }
        while True:
            response = table.query(**query_args)
            keys.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_args["ExclusiveStartKey"] = last_key

        with table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key={
                    "menu_id": key["menu_id"],
                    "item_id": key["item_id"],
                })
        return len(keys)
    except (BotoCoreError, ClientError) as exc:
        logger.error("DynamoDB delete_menu failed: %s", exc)
        raise


def list_menus() -> List[str]:
    if LOCAL_MODE:
        return sorted({i["menu_id"] for i in _load_local()})

    try:
        response = _get_table().scan(ProjectionExpression="menu_id")
        return sorted({i["menu_id"] for i in response.get("Items", [])})
    except (BotoCoreError, ClientError) as exc:
        logger.error("DynamoDB scan failed: %s", exc)
        raise
