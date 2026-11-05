import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from importlib.machinery import SourceFileLoader


HELPER_PATH = (
    Path(__file__).resolve().parents[1]
    / "packaging"
    / "preppi-receiver-control"
)

loader = SourceFileLoader("preppi_receiver_control", str(HELPER_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class ReceiverControlTests(unittest.TestCase):

    def test_next_receiver_check_uses_matching_json_timer(self):
        deadline_usec = 1793909851315559
        result = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps([
                {
                    "unit": "another.timer",
                    "next": 1793900000000000,
                },
                {
                    "unit": "preppi-email-receiver.timer",
                    "next": deadline_usec,
                },
            ]),
        })()

        with patch.object(
            helper, "systemctl", return_value=result
        ) as mocked:
            actual = helper.next_receiver_check(
                "preppi-email-receiver.timer"
            )

        expected = helper.datetime.fromtimestamp(
            deadline_usec / 1_000_000
        ).astimezone().isoformat(timespec="minutes")

        self.assertEqual(actual, expected)
        mocked.assert_called_once_with(
            "list-timers", "--all", "--no-pager", "--output=json"
        )

    def test_next_receiver_check_returns_empty_for_missing_timer(self):
        result = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps([
                {"unit": "another.timer", "next": 1793909851315559},
            ]),
        })()

        with patch.object(helper, "systemctl", return_value=result):
            actual = helper.next_receiver_check(
                "preppi-email-receiver.timer"
            )

        self.assertEqual(actual, "")

    def test_next_receiver_check_returns_empty_for_missing_or_zero_deadline(self):
        for entry in (
            {"unit": "preppi-email-receiver.timer"},
            {"unit": "preppi-email-receiver.timer", "next": 0},
        ):
            with self.subTest(entry=entry):
                result = type("Result", (), {
                    "returncode": 0,
                    "stdout": json.dumps([entry]),
                })()

                with patch.object(
                    helper, "systemctl", return_value=result
                ):
                    actual = helper.next_receiver_check(
                        "preppi-email-receiver.timer"
                    )

                self.assertEqual(actual, "")

    def test_next_receiver_check_rejects_invalid_deadline_types(self):
        for invalid_deadline in (True, 1.5, "1793909851315559"):
            with self.subTest(deadline=invalid_deadline):
                result = type("Result", (), {
                    "returncode": 0,
                    "stdout": json.dumps([{
                        "unit": "preppi-email-receiver.timer",
                        "next": invalid_deadline,
                    }]),
                })()

                with patch.object(
                    helper, "systemctl", return_value=result
                ):
                    actual = helper.next_receiver_check(
                        "preppi-email-receiver.timer"
                    )

                self.assertEqual(actual, "")

    def test_next_receiver_check_handles_command_and_json_failures(self):
        failures = (
            type("Result", (), {"returncode": 1, "stdout": ""})(),
            type("Result", (), {"returncode": 0, "stdout": "not json"})(),
        )

        for result in failures:
            with self.subTest(result=result.stdout):
                with patch.object(
                    helper, "systemctl", return_value=result
                ):
                    actual = helper.next_receiver_check(
                        "preppi-email-receiver.timer"
                    )

                self.assertEqual(actual, "")


if __name__ == "__main__":
    unittest.main()
