import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RECEIVER_PATH = (
    Path(__file__).resolve().parent.parent
    / "email"
    / "email_receiver.py"
)

spec = importlib.util.spec_from_file_location(
    "preppi_email_receiver", RECEIVER_PATH
)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class EmailReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="preppi-receiver-test-"
        )
        self.config_file = Path(self.temp_dir.name) / "email.env"
        self.config_patch = patch.object(
            receiver, "CONFIG", self.config_file
        )
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def test_loads_multiple_allowed_senders(self):
        self.config_file.write_text(
            "GMAIL_ADDRESS=receiver@gmail.com\n"
            "GMAIL_APP_PASSWORD=abcdefghijklmnop\n"
            "ALLOWED_SENDERS=Jordan@example.com, partner@example.com\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            address, password, allowed = receiver.load_config()

        self.assertEqual(address, "receiver@gmail.com")
        self.assertEqual(password, "abcdefghijklmnop")
        self.assertEqual(
            allowed,
            {"jordan@example.com", "partner@example.com"},
        )

    def test_sender_matching_is_case_insensitive(self):
        self.config_file.write_text(
            "GMAIL_ADDRESS=receiver@gmail.com\n"
            "GMAIL_APP_PASSWORD=abcdefghijklmnop\n"
            "ALLOWED_SENDERS=Jordan@example.com\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            _, _, allowed = receiver.load_config()

        incoming_sender = "JORDAN@EXAMPLE.COM".strip().lower()
        self.assertIn(incoming_sender, allowed)

    def test_missing_allowed_senders_prevents_startup(self):
        self.config_file.write_text(
            "GMAIL_ADDRESS=receiver@gmail.com\n"
            "GMAIL_APP_PASSWORD=abcdefghijklmnop\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                RuntimeError, "allowed senders are missing"
            ):
                receiver.load_config()


if __name__ == "__main__":
    unittest.main()
