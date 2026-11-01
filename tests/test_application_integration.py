import tempfile
import unittest
from types import SimpleNamespace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import app as preppi_app
import email_config
from db_init import initialise_database


class ApplicationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app_config_patch = patch.dict(
            preppi_app.app.config,
            {"TESTING": True, "PREPPI_AUTH_DISABLED": True,
             "WTF_CSRF_ENABLED": False,
             "SECRET_KEY": "preppi-test-only-secret-key"},
        )
        self.app_config_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="preppi-integration-")
        root = Path(self.temp_dir.name)

        self.db_path = root / "data" / "stock.db"
        self.config_dir = root / "config"
        self.config_file = self.config_dir / "email.env"

        self.db_patch = patch.object(preppi_app, "DB_PATH", self.db_path)
        self.config_dir_patch = patch.object(email_config, "CONFIG_DIR", self.config_dir)
        self.config_file_patch = patch.object(email_config, "EMAIL_CONFIG", self.config_file)

        self.db_patch.start()
        self.config_dir_patch.start()
        self.config_file_patch.start()

        initialise_database(self.db_path)
        self.client = preppi_app.app.test_client()

    def tearDown(self):
        self.app_config_patch.stop()
        self.config_file_patch.stop()
        self.config_dir_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def setup_form(self):
        return {
            "adults": "2",
            "children": "0",
            "daily_kcal_per_person": "2000",
            "water_litres_per_person_per_day": "3",
            "expiry_warning_days": "90",
            "expiry_urgent_days": "30",
        }

    def stock_form(self, item="Test beans", quantity="2"):
        return {
            "item": item,
            "category": "Food",
            "subcategory": "Tinned food",
            "expiry_date": (date.today() + timedelta(days=180)).isoformat(),
            "quantity": quantity,
        }

    def complete_setup(self):
        response = self.client.post("/setup", data=self.setup_form())
        self.assertEqual(response.status_code, 302)

    def get_item(self, item):
        with preppi_app.get_db() as db:
            return db.execute(
                "SELECT * FROM items WHERE item = ?", (item,)
            ).fetchone()

    def test_fresh_setup_dashboard_and_stock_lifecycle(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/welcome", response.location)

        response = self.client.get("/welcome")
        self.assertEqual(response.status_code, 200)

        self.complete_setup()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        response = self.client.post("/stock/add", data=self.stock_form())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get_item("Test beans")["quantity"], 2)

        response = self.client.post(
            "/stock/add", data=self.stock_form(quantity="3")
        )
        self.assertEqual(response.status_code, 302)
        item = self.get_item("Test beans")
        self.assertEqual(item["quantity"], 5)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Test beans", response.data)

        edit_form = self.stock_form(item="Edited beans", quantity="4")
        response = self.client.post(
            f"/stock/{item['id']}/edit", data=edit_form
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.get_item("Test beans"))
        self.assertEqual(self.get_item("Edited beans")["quantity"], 4)

        response = self.client.post(f"/stock/{item['id']}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.get_item("Edited beans"))

    def test_stock_and_settings_persist_across_new_client(self):
        self.complete_setup()

        response = self.client.post(
            "/stock/add", data=self.stock_form(item="Persistent beans", quantity="7")
        )
        self.assertEqual(response.status_code, 302)

        settings_form = {
            "adults": "2",
            "children": "1",
            "daily_kcal_per_person": "2100",
            "water_litres_per_person_per_day": "4",
            "expiry_warning_days": "120",
            "expiry_urgent_days": "20",
        }
        response = self.client.post("/settings", data=settings_form)
        self.assertEqual(response.status_code, 200)

        # Create a fresh client to check that saved data is still available.
        self.client = preppi_app.app.test_client()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Persistent beans", response.data)

        item = self.get_item("Persistent beans")
        self.assertIsNotNone(item)
        self.assertEqual(item["quantity"], 7)

        with preppi_app.get_db() as db:
            settings = dict(db.execute(
                "SELECT setting_key, setting_value FROM app_settings"
            ).fetchall())

        self.assertEqual(settings["adults"], "2")
        self.assertEqual(settings["children"], "1")
        self.assertEqual(settings["daily_kcal_per_person"], "2100")
        self.assertEqual(float(settings["water_litres_per_person_per_day"]), 4.0)


    def test_dashboard_displays_all_expiry_statuses_and_boundaries(self):
        self.complete_setup()

        test_items = [
            ("Expired beans", -1, "Expired"),
            ("Urgent beans", 30, "Urgent"),
            ("Soon beans", 31, "Expiring soon"),
            ("Warning beans", 90, "Expiring soon"),
            ("In date beans", 91, "In date"),
        ]

        for name, days, expected_status in test_items:
            form = self.stock_form(item=name)
            form["expiry_date"] = (
                date.today() + timedelta(days=days)
            ).isoformat()
            response = self.client.post("/stock/add", data=form)
            self.assertEqual(response.status_code, 302)

        with preppi_app.get_db() as db:
            db.execute("""
                INSERT INTO items
                    (item, category, subcategory, expiry_date, logged, quantity)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                "Unknown date beans", "Food", "Tinned food", "",
                date.today().isoformat(), 2,
            ))

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        for name, days, expected_status in test_items:
            with self.subTest(item=name):
                self.assertIn(name.encode(), response.data)
                self.assertIn(expected_status.encode(), response.data)

        self.assertIn(b"Unknown date beans", response.data)
        self.assertIn(b"Date unknown", response.data)

    def test_invalid_stock_is_rejected(self):
        self.complete_setup()

        form = self.stock_form(quantity="0")
        response = self.client.post("/stock/add", data=form)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"at least 1", response.data)
        self.assertIsNone(self.get_item("Test beans"))

    def test_settings_update_household_and_water_target(self):
        self.complete_setup()

        form = {
            "adults": "2",
            "children": "1",
            "daily_kcal_per_person": "2100",
            "water_litres_per_person_per_day": "4",
            "expiry_warning_days": "120",
            "expiry_urgent_days": "20",
        }
        response = self.client.post("/settings", data=form)
        self.assertEqual(response.status_code, 200)

        with preppi_app.get_db() as db:
            settings = dict(db.execute(
                "SELECT setting_key, setting_value FROM app_settings"
            ).fetchall())
            water = db.execute(
                "SELECT target_value FROM preparedness_targets "
                "WHERE target_key = 'water'"
            ).fetchone()

        self.assertEqual(settings["adults"], "2")
        self.assertEqual(settings["children"], "1")
        self.assertEqual(settings["daily_kcal_per_person"], "2100")
        self.assertEqual(water["target_value"], 168.0)

    def test_email_settings_can_be_saved_and_disabled(self):
        self.complete_setup()

        form = {
            "email_enabled": "on",
            "gmail_address": "preppi.sender@gmail.com",
            "gmail_app_password": "abcd efgh ijkl mnop",
            "report_recipients": "jordan@example.com, partner@example.com",
        }
        response = self.client.post("/settings/email", data=form)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(email_config.read_email_config()["enabled"])

        response = self.client.post(
            "/settings/email", data={"email_enabled": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.config_file.exists())

    def test_report_routes_do_not_send_real_email_during_test(self):
        self.complete_setup()

        with patch.object(
            preppi_app.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stderr="", stdout=""),
        ) as mocked_run:
            response = self.client.post("/reports/stock")
            self.assertEqual(response.status_code, 302)
            self.assertTrue(mocked_run.called)

            response = self.client.post("/reports/monthly")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(mocked_run.call_count, 2)

        self.assertFalse(self.config_file.exists())


    def test_unit_size_migration_preserves_existing_stock_records(self):
        import sqlite3

        legacy_db = Path(self.temp_dir.name) / "legacy_stock.db"

        with sqlite3.connect(legacy_db) as db:
            db.execute("""
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT NOT NULL,
                    expiry_date TEXT NOT NULL,
                    logged TEXT NOT NULL DEFAULT 'No',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                INSERT INTO items
                    (item, category, subcategory, expiry_date, quantity)
                VALUES (?, ?, ?, ?, ?)
            """, ("Dog food", "Food and Drink", "Other Food",
                  "2027-06-01", 3))

            db.execute("""
                CREATE TABLE preparedness_targets (
                    target_key TEXT PRIMARY KEY,
                    target_name TEXT NOT NULL,
                    target_value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                )
            """)

        initialise_database(legacy_db)
        initialise_database(legacy_db)

        with sqlite3.connect(legacy_db) as db:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(items)")
            }
            record = db.execute(
                "SELECT item, quantity, unit_size_grams FROM items"
            ).fetchone()

        self.assertIn("unit_size_grams", columns)
        self.assertEqual(record, ("Dog food", 3, None))


    def test_equipment_category_migration_preserves_existing_records(self):
        import sqlite3

        legacy_db = Path(self.temp_dir.name) / "legacy.db"

        with sqlite3.connect(legacy_db) as db:
            db.execute("""
                CREATE TABLE equipment (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    quantity INTEGER,
                    notes TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute(
                "INSERT INTO equipment (item, quantity, notes) "
                "VALUES (?, ?, ?)",
                ("Camping stove", 1, "Stored in cupboard"),
            )

        initialise_database(legacy_db)
        initialise_database(legacy_db)

        with sqlite3.connect(legacy_db) as db:
            columns = {
                row[1] for row in db.execute(
                    "PRAGMA table_info(equipment)"
                )
            }
            record = db.execute(
                "SELECT id, item, quantity, notes, category "
                "FROM equipment"
            ).fetchone()

        self.assertIn("category", columns)
        self.assertEqual(
            record,
            (1, "Camping stove", 1, "Stored in cupboard", "General"),
        )


    def test_pets_table_migration_preserves_existing_records(self):
        import sqlite3

        legacy_db = Path(self.temp_dir.name) / "legacy_pets.db"

        with sqlite3.connect(legacy_db) as db:
            db.execute("""
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT NOT NULL,
                    expiry_date TEXT NOT NULL,
                    logged TEXT NOT NULL DEFAULT 'No',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                INSERT INTO items
                    (item, category, subcategory, expiry_date, quantity)
                VALUES (?, ?, ?, ?, ?)
            """, ("Tinned beans", "Food and Drink", "Tinned",
                  "2027-06-01", 4))
            db.execute("""
                CREATE TABLE preparedness_targets (
                    target_key TEXT PRIMARY KEY,
                    target_name TEXT NOT NULL,
                    target_value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute("""
                CREATE TABLE equipment (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    quantity INTEGER,
                    notes TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute(
                "INSERT INTO equipment (item, quantity) VALUES (?, ?)",
                ("Camping stove", 1),
            )

        initialise_database(legacy_db)
        initialise_database(legacy_db)

        with sqlite3.connect(legacy_db) as db:
            pet_columns = {
                row[1] for row in db.execute("PRAGMA table_info(pets)")
            }
            stock = db.execute(
                "SELECT item, quantity FROM items"
            ).fetchone()
            equipment = db.execute(
                "SELECT item, quantity FROM equipment"
            ).fetchone()
            pet_count = db.execute(
                "SELECT COUNT(*) FROM pets"
            ).fetchone()[0]

        self.assertTrue({
            "id", "name", "species", "daily_food_grams", "created_at"
        }.issubset(pet_columns))
        self.assertEqual(stock, ("Tinned beans", 4))
        self.assertEqual(equipment, ("Camping stove", 1))
        self.assertEqual(pet_count, 0)


    def test_pet_management_lifecycle_and_validation(self):
        response = self.client.get("/pets")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No pets have been added yet", response.data)

        response = self.client.get("/pets/add")
        self.assertEqual(response.status_code, 200)

        response = self.client.post("/pets/add", data={
            "name": "Milo",
            "species": "cat",
            "daily_food_grams": "60",
        })
        self.assertEqual(response.status_code, 302)

        with preppi_app.get_db() as db:
            pet = db.execute(
                "SELECT * FROM pets WHERE name = ?", ("Milo",)
            ).fetchone()

        self.assertIsNotNone(pet)
        self.assertEqual(pet["species"], "cat")
        self.assertEqual(pet["daily_food_grams"], 60)

        response = self.client.get("/pets")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Milo", response.data)
        self.assertIn(b"840 g", response.data)

        response = self.client.post(
            "/pets/add",
            data={"name": "Invalid", "species": "rabbit",
                  "daily_food_grams": "60"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Species must be Cat or Dog", response.data)

        response = self.client.post(
            "/pets/add",
            data={"name": "Invalid", "species": "dog",
                  "daily_food_grams": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"positive, finite number", response.data)

        response = self.client.post(
            f"/pets/{pet['id']}/edit",
            data={"name": "Milo Updated", "species": "dog",
                  "daily_food_grams": "250"},
        )
        self.assertEqual(response.status_code, 302)

        with preppi_app.get_db() as db:
            updated = db.execute(
                "SELECT name, species, daily_food_grams "
                "FROM pets WHERE id = ?", (pet["id"],)
            ).fetchone()

        self.assertEqual(
            tuple(updated), ("Milo Updated", "dog", 250)
        )

        response = self.client.post(f"/pets/{pet['id']}/delete")
        self.assertEqual(response.status_code, 302)

        with preppi_app.get_db() as db:
            remaining = db.execute(
                "SELECT COUNT(*) FROM pets"
            ).fetchone()[0]

        self.assertEqual(remaining, 0)


    def test_import_template_download(self):
        from io import BytesIO
        from openpyxl import load_workbook

        response = self.client.get("/import/template")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "PrepPi_Stock_Import_Template.xlsx",
            response.headers.get("Content-Disposition", ""),
        )
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            response.content_type,
        )

        workbook = load_workbook(BytesIO(response.data))
        self.assertEqual(
            workbook.sheetnames,
            ["Read me", "Stock Import", "_Lists"],
        )
        self.assertEqual(
            [workbook["Stock Import"].cell(4, col).value for col in range(1, 7)],
            ["Item", "Category", "Sub-category", "Expiry date", "Qty", "Unit size (g)"],
        )
        self.assertEqual(len(workbook["Read me"]._images), 1)
        self.assertEqual(
            workbook["Stock Import"].data_validations.dataValidation[1].formula1,
            '=INDIRECT(SUBSTITUTE($B5," ","_"))',
        )


if __name__ == "__main__":
    unittest.main()
