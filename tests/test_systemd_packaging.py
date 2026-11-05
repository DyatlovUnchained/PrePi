import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SystemdPackagingTests(unittest.TestCase):
    def test_dashboard_service_allows_authorised_sudo(self):
        unit = (ROOT / "packaging/systemd/preppi.service").read_text()

        self.assertNotRegex(unit, r"(?m)^NoNewPrivileges\\s*=\\s*")
        self.assertIn("User=preppi", unit)
        self.assertIn("ExecStart=", unit)

    def test_dashboard_service_retains_other_security_controls(self):
        unit = (ROOT / "packaging/systemd/preppi.service").read_text()

        self.assertIn("PrivateTmp=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=true", unit)

    def test_fresh_installer_installs_packaged_dashboard_unit(self):
        installer = (ROOT / "install.sh").read_text()

        self.assertIn(
            'install -m 0644 "$SCRIPT_DIR/packaging/systemd/preppi.service" \\\n'
            '    "$SYSTEMD_DIR/preppi.service"',
            installer,
        )

    def test_fresh_installer_enables_and_starts_all_scheduled_timers(self):
        installer = (ROOT / "install.sh").read_text()

        for timer in (
            "preppi-email-receiver.timer",
            "preppi-expiry-alerts.timer",
            "preppi-monthly-report.timer",
        ):
            self.assertIn(timer, installer)

        self.assertIn('systemctl enable --now "$timer"', installer)
        self.assertIn('ENABLED_TIMERS+=("$timer")', installer)

    def test_installer_rollback_disables_enabled_timers(self):
        installer = (ROOT / "install.sh").read_text()

        self.assertIn(
            'for timer in "${ENABLED_TIMERS[@]}"; do',
            installer,
        )
        self.assertIn('systemctl disable --now "$timer"', installer)


if __name__ == "__main__":
    unittest.main()
