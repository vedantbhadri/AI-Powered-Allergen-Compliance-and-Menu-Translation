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


if __name__ == "__main__":
    unittest.main()
