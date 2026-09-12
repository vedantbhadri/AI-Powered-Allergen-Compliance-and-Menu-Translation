import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from services import dynamo_service


class DynamoServiceTests(unittest.TestCase):
    def test_delete_menu_only_removes_items_for_requested_menu(self):
        original_mode = dynamo_service.LOCAL_MODE
        original_path = dynamo_service.LOCAL_DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "menus.json"
            db_path.write_text(json.dumps([
                {"menu_id": "current", "item_id": "one"},
                {"menu_id": "current", "item_id": "two"},
                {"menu_id": "other", "item_id": "three"},
            ]))
            try:
                dynamo_service.LOCAL_MODE = True
                dynamo_service.LOCAL_DB_PATH = str(db_path)

                deleted = dynamo_service.delete_menu("current")

                self.assertEqual(deleted, 2)
                self.assertEqual(json.loads(db_path.read_text()), [
                    {"menu_id": "other", "item_id": "three"},
                ])
            finally:
                dynamo_service.LOCAL_MODE = original_mode
                dynamo_service.LOCAL_DB_PATH = original_path

    def test_delete_menu_preserves_registry_and_sentinel_rows(self):
        original_mode = dynamo_service.LOCAL_MODE
        original_path = dynamo_service.LOCAL_DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "menus.json"
            db_path.write_text(json.dumps([
                {"menu_id": "kiwi-cafe", "item_id": "restaurant#kiwi-cafe",
                 "record_type": "restaurant", "name": "Kiwi Cafe"},
                {"menu_id": "kiwi-cafe", "item_id": "dish-0001", "name": "Flat White"},
                {"menu_id": "kiwi-cafe", "item_id": "dish-0002", "name": "Scone"},
                {"menu_id": "kiwi-cafe", "item_id": "upload#kiwi-cafe",
                 "record_type": "upload_status", "status": "ready"},
                {"menu_id": "other-cafe", "item_id": "dish-9999", "name": "Espresso"},
            ]))
            try:
                dynamo_service.LOCAL_MODE = True
                dynamo_service.LOCAL_DB_PATH = str(db_path)

                deleted = dynamo_service.delete_menu("kiwi-cafe")

                self.assertEqual(deleted, 2)

                remaining = json.loads(db_path.read_text())
                remaining_ids = {(r["menu_id"], r["item_id"]) for r in remaining}

                # Non-dish rows in the target partition survive.
                self.assertIn(("kiwi-cafe", "restaurant#kiwi-cafe"), remaining_ids)
                self.assertIn(("kiwi-cafe", "upload#kiwi-cafe"), remaining_ids)
                # Cross-partition row untouched.
                self.assertIn(("other-cafe", "dish-9999"), remaining_ids)
                # Dish rows are gone.
                self.assertNotIn(("kiwi-cafe", "dish-0001"), remaining_ids)
                self.assertNotIn(("kiwi-cafe", "dish-0002"), remaining_ids)
            finally:
                dynamo_service.LOCAL_MODE = original_mode
                dynamo_service.LOCAL_DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
