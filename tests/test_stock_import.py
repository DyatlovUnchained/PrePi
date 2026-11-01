import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from imports.stock_import import read_spreadsheet


class StockImportSpreadsheetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workbook_path = Path(self.temp_dir.name) / "stock.xlsx"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_workbook(self, headers, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Stock Import"

        sheet.append(["PrepPi stock import"])
        sheet.append(["Instructions"])
        sheet.append([])
        sheet.append(headers)

        for row in rows:
            sheet.append(row)

        workbook.save(self.workbook_path)
        workbook.close()

    def test_legacy_five_column_format_remains_supported(self):
        self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date", "Qty"],
            [["Baked beans", "Food and Drink", "Beans",
              "2027-06-01", 4]],
        )

        records, errors = read_spreadsheet(self.workbook_path)

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["quantity"], 4)
        self.assertIsNone(records[0]["unit_size_grams"])

    def test_six_column_format_reads_pack_size(self):
        self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date",
             "Qty", "Unit size (g)"],
            [["Dog food", "Pet Supplies", "Pet Food",
              "2027-06-01", 6, 400]],
        )

        records, errors = read_spreadsheet(self.workbook_path)

        self.assertEqual(errors, [])
        self.assertEqual(records[0]["unit_size_grams"], 400)

    def test_pet_food_requires_pack_size(self):
        self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date",
             "Qty", "Unit size (g)"],
            [["Cat food", "Pet Supplies", "Pet Food",
              "2027-06-01", 6, None]],
        )

        records, errors = read_spreadsheet(self.workbook_path)

        self.assertEqual(records, [])
        self.assertTrue(any("pack size" in error.lower() for error in errors))

    def test_pack_size_must_be_positive(self):
        self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date",
             "Qty", "Unit size (g)"],
            [["Dog food", "Pet Supplies", "Pet Food",
              "2027-06-01", 6, 0]],
        )

        records, errors = read_spreadsheet(self.workbook_path)

        self.assertEqual(records, [])
        self.assertTrue(any("pack size" in error.lower() for error in errors))

    def test_non_pet_food_can_have_blank_pack_size(self):
        self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date",
             "Qty", "Unit size (g)"],
            [["Baked beans", "Food and Drink", "Beans",
              "2027-06-01", 4, None]],
        )

        records, errors = read_spreadsheet(self.workbook_path)

        self.assertEqual(errors, [])
        self.assertIsNone(records[0]["unit_size_grams"])


if __name__ == "__main__":
    unittest.main()


class StockImportDatabaseTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        from db_init import initialise_database

        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "stock.db"
        initialise_database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_workbook(self, headers, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Stock Import"
        sheet.append(["PrepPi stock import"])
        sheet.append(["Instructions"])
        sheet.append([])
        sheet.append(headers)

        for row in rows:
            sheet.append(row)

        path = Path(self.temp_dir.name) / "stock.xlsx"
        workbook.save(path)
        workbook.close()
        return path

    def get_stock(self, item):
        import sqlite3

        with sqlite3.connect(self.db_path) as db:
            return db.execute(
                "SELECT quantity, unit_size_grams FROM items WHERE item = ?",
                (item,),
            ).fetchone()

    def test_new_pet_food_import_saves_pack_size(self):
        from imports.stock_import import run_import

        workbook = self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date",
             "Qty", "Unit size (g)"],
            [["Dog food", "Pet Supplies", "Pet Food",
              "2027-06-01", 6, 400]],
        )

        self.assertEqual(run_import("Stocked", workbook, self.db_path, True), 0)
        self.assertEqual(self.get_stock("Dog food"), (6, 400.0))

    def test_existing_stock_updates_quantity_and_pack_size(self):
        from imports.stock_import import run_import
        import sqlite3

        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """INSERT INTO items
                   (item, category, subcategory, expiry_date, quantity,
                    unit_size_grams)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("Dog food", "Pet Supplies", "Pet Food",
                 "2027-06-01", 2, 400),
            )

        workbook = self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date",
             "Qty", "Unit size (g)"],
            [["Dog food", "Pet Supplies", "Pet Food",
              "2027-06-01", 3, 800]],
        )

        self.assertEqual(run_import("Stocked", workbook, self.db_path, True), 0)
        self.assertEqual(self.get_stock("Dog food"), (5, 800.0))

    def test_legacy_import_preserves_existing_pack_size(self):
        from imports.stock_import import run_import
        import sqlite3

        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """INSERT INTO items
                   (item, category, subcategory, expiry_date, quantity,
                    unit_size_grams)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("Baked beans", "Food and Drink", "Beans",
                 "2027-06-01", 2, 400),
            )

        workbook = self.create_workbook(
            ["Item", "Category", "Sub-category", "Expiry date", "Qty"],
            [["Baked beans", "Food and Drink", "Beans",
              "2027-06-01", 3]],
        )

        self.assertEqual(run_import("Stocked", workbook, self.db_path, True), 0)
        self.assertEqual(self.get_stock("Baked beans"), (5, 400.0))
