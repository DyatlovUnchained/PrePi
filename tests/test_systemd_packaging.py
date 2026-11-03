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


if __name__ == "__main__":
    unittest.main()
