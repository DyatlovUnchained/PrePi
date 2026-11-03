import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as preppi_app
import email_config
from db_init import initialise_database


def automation_status():
    return {
        "available": True,
        "jobs": {
            "receiver": {
                "enabled": True,
                "active": True,
                "last_check": "2026-11-03 19:25",
                "next_check": "2026-11-03T19:30",
                "last_result": "processed",
                "last_processed": "2026-11-03 19:20:00",
            },
            "expiry": {
                "enabled": False, "active": False,
                "last_run": "", "next_run": "",
                "result": "", "exit_status": "",
            },
            "monthly": {
                "enabled": False, "active": False,
                "last_run": "", "next_run": "",
                "result": "", "exit_status": "",
            },
        },
    }


def command_result(payload=None, returncode=0, stderr=""):
    return type("Result", (), {
        "returncode": returncode,
        "stdout": json.dumps(payload or {"available": True}),
        "stderr": stderr,
    })()


class EmailSettingsRouteTests(unittest.TestCase):
    def setUp(self):
        self.app_config_patch = patch.dict(
            preppi_app.app.config,
            {
                "TESTING": True,
                "PREPPI_AUTH_DISABLED": True,
                "WTF_CSRF_ENABLED": False,
                "SECRET_KEY": "preppi-test-only-secret-key",
            },
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

    def configure_email(self):
        email_config.save_email_config(
            "sender@gmail.com",
            "abcdefghijklmnop",
            "one@example.com",
        )

    def test_settings_page_loads_email_and_automation_sections(self):
        with patch(
            "app.subprocess.run",
            return_value=command_result(automation_status()),
        ):
            response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Optional email configuration", response.data)
        self.assertIn(b"AUTOMATED TASKS", response.data)
        self.assertIn(b"Inbox receiver", response.data)
        self.assertIn(b"Daily expiry alerts", response.data)
        self.assertIn(b"Monthly preparedness report", response.data)

    @patch("app.subprocess.run")
    def test_settings_page_shows_receiver_status(self, mock_run):
        mock_run.return_value = command_result(automation_status())

        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enabled", response.data)
        self.assertIn(b"Successfully processed", response.data)

    @patch("app.subprocess.run")
    def test_automation_status_failure_does_not_break_settings_page(
        self, mock_run
    ):
        mock_run.return_value = command_result(returncode=1, stderr="Unavailable")

        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Automation status is unavailable", response.data)

    @patch("app.subprocess.run")
    def test_legacy_receiver_enable_uses_fixed_receiver_job(self, mock_run):
        mock_run.return_value = command_result()

        response = self.client.post(
            "/settings/receiver", data={"action": "enable"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            ["sudo", str(preppi_app.RECEIVER_CONTROL), "enable", "receiver"],
            mock_run.call_args.args,
        )
        self.assertFalse(email_config.EMAIL_CONFIG.exists())

    @patch("app.subprocess.run")
    def test_legacy_receiver_disable_preserves_email_configuration(
        self, mock_run
    ):
        self.configure_email()
        before = email_config.read_email_config()
        mock_run.return_value = command_result()

        response = self.client.post(
            "/settings/receiver", data={"action": "disable"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            mock_run.call_args.args[0],
            ["sudo", str(preppi_app.RECEIVER_CONTROL), "disable", "receiver"],
        )
        self.assertEqual(email_config.read_email_config(), before)

    @patch("app.subprocess.run")
    def test_expiry_preview_does_not_require_email_configuration(
        self, mock_run
    ):
        mock_run.return_value = command_result({
            "available": True,
            "action": "preview",
            "job": "expiry",
            "output": "No expiry alerts are currently due.",
        })

        response = self.client.post(
            "/settings/automation",
            data={"job": "expiry", "action": "preview"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            mock_run.call_args.args[0],
            ["sudo", str(preppi_app.RECEIVER_CONTROL), "preview", "expiry"],
        )
        self.assertFalse(email_config.EMAIL_CONFIG.exists())

        with self.client.session_transaction() as session:
            messages = session.get("_flashes", [])
        self.assertTrue(any("No email was sent" in message for _, message in messages))

    @patch("app.subprocess.run")
    def test_monthly_preview_does_not_send_email(self, mock_run):
        mock_run.return_value = command_result({
            "available": True,
            "action": "preview",
            "job": "monthly",
            "output": "Report generated.",
        })

        response = self.client.post(
            "/settings/automation",
            data={"job": "monthly", "action": "preview"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            mock_run.call_args.args[0],
            ["sudo", str(preppi_app.RECEIVER_CONTROL), "preview", "monthly"],
        )

    @patch("app.subprocess.run")
    def test_expiry_enable_is_blocked_without_email_configuration(
        self, mock_run
    ):
        response = self.client.post(
            "/settings/automation",
            data={"job": "expiry", "action": "enable"},
        )

        self.assertEqual(response.status_code, 302)
        mock_run.assert_not_called()
        with self.client.session_transaction() as session:
            messages = session.get("_flashes", [])
        self.assertTrue(any("Configure and save email settings" in message
                            for _, message in messages))

    @patch("app.subprocess.run")
    def test_monthly_send_is_blocked_without_email_configuration(
        self, mock_run
    ):
        response = self.client.post(
            "/settings/automation",
            data={"job": "monthly", "action": "run"},
        )

        self.assertEqual(response.status_code, 302)
        mock_run.assert_not_called()

    @patch("app.subprocess.run")
    def test_expiry_enable_calls_fixed_helper_command(self, mock_run):
        self.configure_email()
        mock_run.return_value = command_result()

        response = self.client.post(
            "/settings/automation",
            data={"job": "expiry", "action": "enable"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            mock_run.call_args.args[0],
            ["sudo", str(preppi_app.RECEIVER_CONTROL), "enable", "expiry"],
        )

    @patch("app.subprocess.run")
    def test_monthly_run_requires_configuration_and_calls_helper(
        self, mock_run
    ):
        self.configure_email()
        mock_run.return_value = command_result({
            "available": True, "action": "run", "job": "monthly",
        })

        response = self.client.post(
            "/settings/automation",
            data={"job": "monthly", "action": "run"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            mock_run.call_args.args[0],
            ["sudo", str(preppi_app.RECEIVER_CONTROL), "run", "monthly"],
        )
        self.assertEqual(mock_run.call_args.kwargs["timeout"], 190)

    @patch("app.subprocess.run")
    def test_unknown_job_is_rejected_without_running_helper(self, mock_run):
        response = self.client.post(
            "/settings/automation",
            data={"job": "anything", "action": "enable"},
        )

        self.assertEqual(response.status_code, 302)
        mock_run.assert_not_called()

    @patch("app.subprocess.run")
    def test_receiver_preview_is_rejected(self, mock_run):
        response = self.client.post(
            "/settings/automation",
            data={"job": "receiver", "action": "preview"},
        )

        self.assertEqual(response.status_code, 302)
        mock_run.assert_not_called()

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
        self.configure_email()

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
