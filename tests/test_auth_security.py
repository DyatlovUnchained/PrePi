import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as preppi_app
import auth
from db_init import initialise_database


class AuthenticationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="preppi-auth-test-")
        self.root = Path(self.temp_dir.name)

        self.config_patch = patch.object(auth, "CONFIG_DIR", self.root / "config")
        self.auth_patch = patch.object(auth, "AUTH_FILE", self.root / "config" / "auth.json")
        self.secret_patch = patch.object(auth, "SECRET_FILE", self.root / "config" / "secret_key")
        self.db_patch = patch.object(preppi_app, "DB_PATH", self.root / "data" / "stock.db")
        self.app_config_patch = patch.dict(
            preppi_app.app.config,
            {
                "TESTING": True,
                "PREPPI_AUTH_DISABLED": False,
                "WTF_CSRF_ENABLED": True,
                "SECRET_KEY": "preppi-security-test-secret-key",
            },
        )

        for patcher in (
            self.config_patch,
            self.auth_patch,
            self.secret_patch,
            self.db_patch,
            self.app_config_patch,
        ):
            patcher.start()

        initialise_database(self.root / "data" / "stock.db")
        self.client = preppi_app.app.test_client()

    def tearDown(self):
        for patcher in (
            self.app_config_patch,
            self.db_patch,
            self.secret_patch,
            self.auth_patch,
            self.config_patch,
        ):
            patcher.stop()
        self.temp_dir.cleanup()

    def csrf_token(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match, f"No CSRF token found on {path}")
        return match.group(1).decode()

    def test_first_run_redirects_to_password_creation(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/create-password", response.headers["Location"])

    def test_password_creation_requires_csrf(self):
        response = self.client.post(
            "/create-password",
            data={
                "password": "a-long-test-password",
                "confirm_password": "a-long-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(auth.password_is_configured())

    def test_password_is_hashed_and_stored_with_restricted_permissions(self):
        token = self.csrf_token("/create-password")
        password = "a-long-test-password"

        response = self.client.post(
            "/create-password",
            data={
                "csrf_token": token,
                "password": password,
                "confirm_password": password,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertTrue(auth.password_is_configured())

        stored = auth.AUTH_FILE.read_text()
        self.assertNotIn(password, stored)
        self.assertIn("scrypt:", stored)
        self.assertEqual(auth.AUTH_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(auth.AUTH_FILE.parent.stat().st_mode & 0o777, 0o700)

    def test_login_protects_pages_and_logout_ends_session(self):
        auth.set_household_password("a-long-test-password")

        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])

        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)

        token = self.csrf_token("/settings")
        response = self.client.post(
            "/logout",
            data={"csrf_token": token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_incorrect_password_does_not_authenticate(self):
        auth.set_household_password("a-long-test-password")
        token = self.csrf_token("/login")

        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "password": "incorrect-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Incorrect password", response.data)

        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


    def test_existing_password_cannot_be_replaced(self):
        original_password = "original-household-password"
        auth.set_household_password(original_password)

        token = self.csrf_token("/login")
        response = self.client.post(
            "/create-password",
            data={
                "csrf_token": token,
                "password": "replacement-household-password",
                "confirm_password": "replacement-household-password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertTrue(auth.verify_household_password(original_password))
        self.assertFalse(
            auth.verify_household_password("replacement-household-password")
        )

    def test_session_cookie_security_attributes(self):
        auth.set_household_password("a-long-test-password")
        token = self.csrf_token("/login")

        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "password": "a-long-test-password",
            },
        )

        self.assertEqual(response.status_code, 302)
        cookies = response.headers.getlist("Set-Cookie")
        session_cookie = next(
            cookie for cookie in cookies if cookie.startswith("session=")
        )

        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("SameSite=Lax", session_cookie)
        self.assertNotIn("Secure", session_cookie)


if __name__ == "__main__":
    unittest.main()
