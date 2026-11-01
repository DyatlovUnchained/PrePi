import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import sys

from openpyxl import load_workbook

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "email"))

from db_init import initialise_database
from monthly_report import (read_inventory, build_workbook, build_html, usable_stock)
import shared.food_endurance as food_endurance
from shared.food_endurance import calculate_food_endurance


class MonthlyReportPetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "stock.db"
        self.original_nutrition_db = food_endurance.DB_PATH
        food_endurance.DB_PATH = (
            Path(self.temp_dir.name) / "nutrition-cache.db"
        )
        initialise_database(self.db_path)
        self.today = date.today()
        self.future = (self.today + timedelta(days=365)).isoformat()

        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                """INSERT INTO pets (name, species, daily_food_grams)
                   VALUES (?, ?, ?)""",
                [
                    ("Milo", "cat", 60),
                    ("Rex", "dog", 250),
                ],
            )
            db.executemany(
                """INSERT INTO items
                   (item, category, subcategory, expiry_date, logged,
                    quantity, unit_size_grams)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    ("Pet food tins", "Pet Supplies", "Pet Food",
                     self.future, "No", 10, 400),
                    ("Expired pet food", "Pet Supplies", "Pet Food",
                     "2000-01-01", "No", 5, 400),
                    ("Baked beans", "Food and Drink", "Beans",
                     self.future, "No", 4, 400),
                ],
            )

    def tearDown(self):
        food_endurance.DB_PATH = self.original_nutrition_db
        self.temp_dir.cleanup()

    def test_read_inventory_includes_pets_and_pack_sizes(self):
        stock, equipment, targets, shopping, pets = read_inventory(
            self.db_path
        )

        self.assertEqual(len(pets), 2)
        self.assertEqual(
            {pet["name"] for pet in pets}, {"Milo", "Rex"}
        )
        pet_food = next(row for row in stock if row["item"] == "Pet food tins")
        self.assertEqual(pet_food["unit_size_grams"], 400)

    def test_workbook_includes_combined_pet_preparedness(self):
        stock, equipment, targets, shopping, pets = read_inventory(
            self.db_path
        )
        output = Path(self.temp_dir.name) / "report.xlsx"

        build_workbook(stock, equipment, output, pets=pets)

        workbook = load_workbook(output, data_only=True)
        self.assertIn("Pet preparedness", workbook.sheetnames)
        sheet = workbook["Pet preparedness"]

        values = [
            [cell.value for cell in row]
            for row in sheet.iter_rows()
        ]
        self.assertIn(["Milo", "cat", 60, 840], values)
        self.assertIn(["Rex", "dog", 250, 3500], values)
        summary = {row[0]: row[1] for row in values if row[0]}
        self.assertEqual(summary["Combined daily allowance (g)"], 310)
        self.assertEqual(summary["Combined 14-day requirement (g)"], 4340)
        self.assertEqual(summary["In-date pet food stock (g)"], 4000)
        self.assertEqual(summary["14-day shortfall (g)"], 340)
        workbook.close()

    def test_html_includes_pet_preparedness_and_recommendation(self):
        stock, equipment, targets, shopping, pets = read_inventory(
            self.db_path
        )

        report = build_html(
            stock, equipment, targets, shopping, self.today, pets=pets
        )

        self.assertIn("Pet preparedness", report)
        self.assertIn("Milo", report)
        self.assertIn("Rex", report)
        self.assertIn("4340 g", report)
        self.assertIn("4000 g", report)
        self.assertIn("340 g", report)
        self.assertIn("Pet food", report)

    def test_no_pet_section_when_no_pets_registered(self):
        stock, equipment, targets, shopping, pets = read_inventory(
            self.db_path
        )

        report = build_html(
            stock, equipment, targets, shopping, self.today, pets=[]
        )

        self.assertNotIn("Pet preparedness", report)

    def test_pet_food_does_not_inflate_human_food_calories(self):
        stock, equipment, targets, shopping, pets = read_inventory(
            self.db_path
        )
        usable = usable_stock(stock, self.today)

        with_pet_food = calculate_food_endurance(
            usable, today=self.today, allow_lookup=False
        )
        without_pet_food = calculate_food_endurance(
            [
                row for row in usable
                if row.get("category") != "Pet Supplies"
            ],
            today=self.today,
            allow_lookup=False,
        )

        self.assertEqual(
            with_pet_food["food_kcal"],
            without_pet_food["food_kcal"],
        )
        self.assertEqual(
            with_pet_food["food_days"],
            without_pet_food["food_days"],
        )



if __name__ == "__main__":
    unittest.main()
