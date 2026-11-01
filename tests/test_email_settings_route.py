import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as preppi_app
import email_config
from db_init import initialise_database


class EmailSettingsRouteTests(unittest.TestCase):
    def setUp(self):
        self.app_config_patch = patch.dict(
            preppi_app.app.config,
            {"TESTING": True, "PREPPI_AUTH_DISABLED": True,
             "WTF_CSRF_ENABLED": False,
             "SECRET_KEY": "preppi-test-only-secret-key"},
        )
        self.app_config_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="preppi-email-test-")
        self.root = Path(self.temp_dir.name)

        self.db_patch = patch.object(
            preppi_app, "DB_PATH", self.root / "stock.db"
        )
        self.config_patch = patch.multiple(
            email_config,
            CONFIG_DIR=self.root / "config",
            EMAIL_CONFIG=self.root / "config" / "email.env",
        )

        self.db_patch.start()
        self.config_patch.start()
        initialise_database(self.root / "stock.db")
        self.client = preppi_app.app.test_client()

    def tearDown(self):
        self.app_config_patch.stop()
        self.config_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_settings_page_loads_email_section(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Optional email configuration", response.data)

    def test_saves_email_configuration(self):
        response = self.client.post(
            "/settings/email",
            data={
                "email_enabled": "on",
                "gmail_address": "sender@gmail.com",
                "gmail_app_password": "abcd efgh ijkl mnop",
                "report_recipients": "one@example.com, two@example.com",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("email_status=saved", response.location)
        config = email_config.read_email_config()
        self.assertTrue(config["enabled"])
        self.assertEqual(config["address"], "sender@gmail.com")
        self.assertEqual(
            config["recipients"], "one@example.com, two@example.com"
        )
        self.assertNotIn("GMAIL_APP_PASSWORD", config)

    def test_disabling_email_removes_saved_configuration(self):
        email_config.save_email_config(
            "sender@gmail.com",
            "abcdefghijklmnop",
            "one@example.com",
        )

        response = self.client.post("/settings/email", data={})

        self.assertEqual(response.status_code, 302)
        self.assertIn("email_status=disabled", response.location)
        self.assertFalse(email_config.EMAIL_CONFIG.exists())
        self.assertFalse(email_config.read_email_config()["enabled"])

    def test_invalid_configuration_is_not_saved(self):
        response = self.client.post(
            "/settings/email",
            data={
                "email_enabled": "on",
                "gmail_address": "not-an-email",
                "gmail_app_password": "abcdefghijklmnop",
                "report_recipients": "one@example.com",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("email_error=", response.location)
        self.assertFalse(email_config.EMAIL_CONFIG.exists())


if __name__ == "__main__":
    unittest.main()
