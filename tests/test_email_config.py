import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import email_config


class EmailConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="preppi-email-test-")
        self.root = Path(self.temp_dir.name)
        self.config_dir = self.root / "config"
        self.config_file = self.config_dir / "email.env"
        self.config_patch = patch.multiple(
            email_config,
            CONFIG_DIR=self.config_dir,
            EMAIL_CONFIG=self.config_file,
        )
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def test_email_config_is_disabled_when_missing(self):
        result = email_config.read_email_config()
        self.assertFalse(result["enabled"])
        self.assertFalse(result["has_password"])

    def test_saves_config_with_restricted_permissions(self):
        email_config.save_email_config(
            "sender@gmail.com",
            "abcd efgh ijkl mnop",
            "person@example.com, partner@example.com",
        )
        self.assertEqual(self.config_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config_dir.stat().st_mode & 0o777, 0o700)
        result = email_config.read_email_config()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["address"], "sender@gmail.com")
        self.assertEqual(
            result["recipients"],
            "person@example.com, partner@example.com",
        )
        self.assertNotIn("password", result)

    def test_rejects_invalid_sender(self):
        with self.assertRaisesRegex(ValueError, "valid Gmail address"):
            email_config.save_email_config(
                "not-an-email", "password", "person@example.com"
            )

    def test_rejects_invalid_recipient(self):
        with self.assertRaisesRegex(ValueError, "valid recipient"):
            email_config.save_email_config(
                "sender@gmail.com", "password", "not-an-email"
            )

    def test_requires_password_when_no_existing_config(self):
        with self.assertRaisesRegex(ValueError, "app password"):
            email_config.save_email_config(
                "sender@gmail.com", "", "person@example.com"
            )

    def test_can_keep_existing_password_without_returning_it(self):
        email_config.save_email_config(
            "sender@gmail.com", "originalpassword", "person@example.com"
        )
        email_config.save_email_config(
            "sender@gmail.com",
            "",
            "partner@example.com",
            keep_existing_password=True,
        )
        result = email_config.read_email_config()
        self.assertEqual(result["recipients"], "partner@example.com")
        self.assertTrue(result["has_password"])
        self.assertNotIn("originalpassword", str(result))


if __name__ == "__main__":
    unittest.main()
