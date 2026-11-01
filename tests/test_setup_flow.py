import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as preppi_app
import email_config
from db_init import initialise_database


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.app_config_patch = patch.dict(
            preppi_app.app.config,
            {"TESTING": True, "PREPPI_AUTH_DISABLED": True,
             "WTF_CSRF_ENABLED": False,
             "SECRET_KEY": "preppi-test-only-secret-key"},
        )
        self.app_config_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="preppi-test-")
        self.db_path = Path(self.temp_dir.name) / "stock.db"
        self.db_patch = patch.object(preppi_app, "DB_PATH", self.db_path)
        self.db_patch.start()
        initialise_database(self.db_path)
        self.client = preppi_app.app.test_client()

    def tearDown(self):
        self.app_config_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def get_setup_flag(self):
        with preppi_app.get_db() as db:
            row = db.execute(
                "SELECT setting_value FROM app_settings "
                "WHERE setting_key = 'setup_complete'"
            ).fetchone()
        return row["setting_value"]

    def valid_form(self):
        return {
            "adults": "2",
            "children": "1",
            "daily_kcal_per_person": "2100",
            "water_litres_per_person_per_day": "3",
            "expiry_urgent_days": "30",
            "expiry_warning_days": "90",
        }

    def test_fresh_install_shows_welcome(self):
        response = self.client.get("/", follow_redirects=True)
        self.assertIn(b"Welcome to PrepPi", response.data)

    def test_setup_page_loads(self):
        response = self.client.get("/setup")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"name=\"adults\"", response.data)
        self.assertIn(b"name=\"children\"", response.data)

    def test_invalid_submission_does_not_complete_setup(self):
        form = self.valid_form()
        form["adults"] = "0"
        form["children"] = "0"
        response = self.client.post("/setup", data=form)
        self.assertIn(b"between 1 and 30 people", response.data)
        self.assertEqual(self.get_setup_flag(), "0")

    def test_valid_submission_completes_setup(self):
        response = self.client.post(
            "/setup", data=self.valid_form(), follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_setup_flag(), "1")

    def test_completed_install_skips_welcome(self):
        self.client.post("/setup", data=self.valid_form())
        response = self.client.get("/welcome", follow_redirects=True)
        self.assertNotIn(b"Welcome to PrepPi", response.data)

    def test_setup_saves_household_water_target(self):
        self.client.post("/setup", data=self.valid_form())
        with preppi_app.get_db() as db:
            row = db.execute(
                "SELECT target_value FROM preparedness_targets "
                "WHERE target_key = 'water'"
            ).fetchone()
        self.assertEqual(row["target_value"], 126.0)


    def test_setup_completes_when_email_is_skipped(self):
        config_dir = Path(self.temp_dir.name) / "config"
        config_file = config_dir / "email.conf"

        with patch.object(email_config, "CONFIG_DIR", config_dir), \
             patch.object(email_config, "EMAIL_CONFIG", config_file):
            response = self.client.post(
                "/setup", data=self.valid_form(), follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_setup_flag(), "1")
        self.assertFalse(config_file.exists())

    def test_setup_saves_valid_optional_email_configuration(self):
        config_dir = Path(self.temp_dir.name) / "config"
        config_file = config_dir / "email.conf"
        form = self.valid_form()
        form.update({
            "email_enabled": "on",
            "gmail_address": "preppi.sender@gmail.com",
            "gmail_app_password": "abcd efgh ijkl mnop",
            "report_recipients": "jordan@example.com, partner@example.com",
        })

        with patch.object(email_config, "CONFIG_DIR", config_dir), \
             patch.object(email_config, "EMAIL_CONFIG", config_file):
            response = self.client.post(
                "/setup", data=form, follow_redirects=True
            )
            saved = email_config.read_email_config()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_setup_flag(), "1")
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["address"], "preppi.sender@gmail.com")
        self.assertEqual(
            saved["recipients"],
            "jordan@example.com, partner@example.com",
        )
        self.assertTrue(saved["has_password"])
        self.assertEqual(config_file.stat().st_mode & 0o777, 0o600)

    def test_invalid_optional_email_does_not_complete_setup(self):
        config_dir = Path(self.temp_dir.name) / "config"
        config_file = config_dir / "email.conf"
        form = self.valid_form()
        form.update({
            "email_enabled": "on",
            "gmail_address": "not-a-valid-address",
            "gmail_app_password": "abcd efgh ijkl mnop",
            "report_recipients": "jordan@example.com",
        })

        with patch.object(email_config, "CONFIG_DIR", config_dir), \
             patch.object(email_config, "EMAIL_CONFIG", config_file):
            response = self.client.post("/setup", data=form)

        self.assertIn(b"Enter a valid Gmail address", response.data)
        self.assertEqual(self.get_setup_flag(), "0")
        self.assertFalse(config_file.exists())


if __name__ == "__main__":
    unittest.main()
