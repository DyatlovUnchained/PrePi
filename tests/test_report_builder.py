import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "email"))

from db_init import initialise_database
import shared.food_endurance as food_endurance
from report_builder import build_report


class StockImportReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "stock.db"
        self.original_nutrition_db = food_endurance.DB_PATH
        food_endurance.DB_PATH = (
            Path(self.temp_dir.name) / "nutrition-cache.db"
        )

        initialise_database(self.db_path)
        expiry = (date.today() + timedelta(days=365)).isoformat()

        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                INSERT INTO items
                    (item, category, subcategory, expiry_date, logged,
                     quantity, unit_size_grams)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "Baked beans", "Food and Drink", "Beans",
                expiry, "No", 4, 400,
            ))
            db.execute("""
                UPDATE app_settings
                SET setting_value = '2500'
                WHERE setting_key = 'daily_kcal_per_person'
            """)

    def tearDown(self):
        food_endurance.DB_PATH = self.original_nutrition_db
        self.temp_dir.cleanup()

    def test_report_calculates_food_energy_and_uses_household_target(self):
        document, context = build_report(
            self.db_path, "success", "Stocked"
        )

        food = context["food_endurance"]

        # 4 tins × 400 g × 90 kcal per 100 g = 1,440 kcal.
        self.assertEqual(food["food_kcal"], 1440)
        self.assertEqual(food["estimated_kcal"], 1440)
        self.assertEqual(food["product_kcal"], 0)

        # Two adults × 2,500 kcal per person = 5,000 kcal per day.
        self.assertEqual(food["daily_target_kcal"], 5000)
        self.assertAlmostEqual(food["food_days"], 1440 / 5000)

        self.assertIn("1,440 kcal recorded", document)
        self.assertIn("5,000", document)
        self.assertIn("category-estimated energy: 1,440 kcal", document)
        self.assertIn("Food endurance:", document)
        self.assertNotIn("meal portions are not recorded", document)

    def test_failure_report_still_shows_inventory_without_claiming_changes(self):
        document, context = build_report(
            self.db_path,
            "failure",
            "Stocked",
            changes=[{"item": "Baked beans", "operation": "UPDATE"}],
            error="Test failure",
        )

        self.assertIn("IMPORT FAILED", document)
        self.assertIn("1,440 kcal recorded", document)
        self.assertIn("Test failure", document)
        self.assertEqual(context["changes"], [])
        self.assertEqual(context["food_endurance"]["food_kcal"], 1440)


if __name__ == "__main__":
    unittest.main()
