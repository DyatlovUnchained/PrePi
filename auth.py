"""Secure household authentication and persistent Flask secret storage."""

import json
import os
import secrets
import tempfile

from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

from paths import CONFIG_DIR

AUTH_FILE = CONFIG_DIR / "auth.json"
SECRET_FILE = CONFIG_DIR / "secret_key"


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)


def _atomic_write(path, content):
    _ensure_config_dir()

    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=CONFIG_DIR,
        text=True,
    )

    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)
        path.chmod(0o600)

    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass

        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass

        raise


def load_secret_key():
    """Load or securely generate the persistent Flask session key."""
    _ensure_config_dir()

    if SECRET_FILE.is_file():
        key = SECRET_FILE.read_text(encoding="utf-8").strip()
        if len(key) < 64:
            raise RuntimeError("PrepPi secret key file is invalid.")
        SECRET_FILE.chmod(0o600)
        return key

    key = secrets.token_hex(32)
    _atomic_write(SECRET_FILE, key + "\n")
    return key


def password_is_configured():
    return AUTH_FILE.is_file()


def set_household_password(password):
    """Store a password hash, never the original password."""
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Password must be at least 12 characters long.")

    if len(password) > 256:
        raise ValueError("Password is too long.")

    password_hash = generate_password_hash(password, method="scrypt")
    _atomic_write(
        AUTH_FILE,
        json.dumps({"password_hash": password_hash}) + "\n",
    )


def verify_household_password(password):
    """Check a password against the stored hash."""
    if not AUTH_FILE.is_file():
        return False

    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        password_hash = data.get("password_hash", "")
        return bool(password_hash) and check_password_hash(
            password_hash, password or ""
        )
    except (OSError, ValueError, TypeError):
        return False
