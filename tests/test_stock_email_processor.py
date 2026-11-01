import importlib.util
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from db_init import initialise_database


BASE_DIR = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "stock_email_processor",
    BASE_DIR / "email" / "stock_email_processor.py",
)
processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(processor)


class StockEmailProcessorTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="preppi-email-test-"
        )
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "stock.db"
        initialise_database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_workbook(self, filename, rows, extended=True):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Stock Import"

        headers = [
            "Item", "Category", "Sub-category",
            "Expiry date", "Qty",
        ]
        if extended:
            headers.append("Unit size (g)")

        for column, heading in enumerate(headers, start=1):
            sheet.cell(row=4, column=column, value=heading)

        for row_number, row in enumerate(rows, start=5):
            for column, value in enumerate(row, start=1):
                sheet.cell(row=row_number, column=column, value=value)

        path = self.root / filename
        workbook.save(path)
        workbook.close()
        return path

    def get_item(self, item):
        db = processor.connect_db(self.db_path)
        try:
            return db.execute(
                "SELECT * FROM items WHERE item = ?", (item,)
            ).fetchone()
        finally:
            db.close()

    def test_new_pet_food_saves_pack_size(self):
        workbook = self.create_workbook(
            "new-pet-food.xlsx",
            [["Cat food", "Pet Supplies", "Pet Food", None, 4, 400]],
        )

        result = processor.process_import(
            self.db_path, "Stocked", workbook,
            "<new-pet-food>", False, False,
        )

        self.assertEqual(result, 0)
        item = self.get_item("Cat food")
        self.assertEqual(item["quantity"], 4)
        self.assertEqual(item["unit_size_grams"], 400)

    def test_existing_pet_food_updates_quantity_and_pack_size(self):
        with processor.connect_db(self.db_path) as db:
            db.execute("""
                INSERT INTO items
                    (item, category, subcategory, expiry_date, quantity, unit_size_grams)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("Dog food", "Pet Supplies", "Pet Food", "", 2, 500))

        workbook = self.create_workbook(
            "updated-pet-food.xlsx",
            [["Dog food", "Pet Supplies", "Pet Food", None, 3, 1000]],
        )

        result = processor.process_import(
            self.db_path, "Stocked", workbook,
            "<updated-pet-food>", False, False,
        )

        self.assertEqual(result, 0)
        item = self.get_item("Dog food")
        self.assertEqual(item["quantity"], 5)
        self.assertEqual(item["unit_size_grams"], 1000)

    def test_blank_pack_size_preserves_existing_size(self):
        with processor.connect_db(self.db_path) as db:
            db.execute("""
                INSERT INTO items
                    (item, category, subcategory, expiry_date, quantity, unit_size_grams)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("Baked beans", "Food and Drink", "Beans", "", 2, 415))

        workbook = self.create_workbook(
            "beans-update.xlsx",
            [["Baked beans", "Food and Drink", "Beans", None, 3, None]],
        )

        result = processor.process_import(
            self.db_path, "Stocked", workbook,
            "<beans-update>", False, False,
        )

        self.assertEqual(result, 0)
        item = self.get_item("Baked beans")
        self.assertEqual(item["quantity"], 5)
        self.assertEqual(item["unit_size_grams"], 415)

    def test_pet_food_without_pack_size_is_rejected(self):
        workbook = self.create_workbook(
            "missing-pack-size.xlsx",
            [["Cat food", "Pet Supplies", "Pet Food", None, 4, None]],
        )

        result = processor.process_import(
            self.db_path, "Stocked", workbook,
            "<missing-pack-size>", False, False,
        )

        self.assertEqual(result, 1)
        self.assertIsNone(self.get_item("Cat food"))

        with processor.connect_db(self.db_path) as db:
            ledger = db.execute(
                "SELECT report_status, import_error "
                "FROM email_imports WHERE message_id = ?",
                ("<missing-pack-size>",),
            ).fetchone()

        self.assertEqual(ledger["report_status"], "pending")
        self.assertIn("requires a pack size", ledger["import_error"])

    def test_duplicate_message_does_not_import_stock_twice(self):
        workbook = self.create_workbook(
            "duplicate-pet-food.xlsx",
            [["Dog food", "Pet Supplies", "Pet Food", None, 3, 500]],
        )

        first_result = processor.process_import(
            self.db_path, "Stocked", workbook,
            "<duplicate-message>", False, False,
        )
        second_result = processor.process_import(
            self.db_path, "Stocked", workbook,
            "<duplicate-message>", False, False,
        )

        self.assertEqual(first_result, 0)
        self.assertEqual(second_result, 3)

        item = self.get_item("Dog food")
        self.assertEqual(item["quantity"], 3)
        self.assertEqual(item["unit_size_grams"], 500)
