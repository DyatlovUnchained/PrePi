"""Secure storage and status helpers for optional PrepPi email configuration."""

import os
import re

from paths import CONFIG_DIR, EMAIL_CONFIG


def read_email_config():
    """Read configured email details without returning the app password."""
    values = {}
    if EMAIL_CONFIG.is_file():
        for line in EMAIL_CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")

    return {
        "enabled": bool(
            values.get("GMAIL_ADDRESS", "").strip()
            and values.get("GMAIL_APP_PASSWORD", "").strip()
            and values.get("REPORT_RECIPIENTS", "").strip()
        ),
        "address": values.get("GMAIL_ADDRESS", "").strip(),
        "recipients": values.get("REPORT_RECIPIENTS", "").strip(),
        "allowed_senders": values.get("ALLOWED_SENDERS", "").strip(),
        "has_password": bool(values.get("GMAIL_APP_PASSWORD", "").strip()),
    }


def save_email_config(
    address,
    app_password,
    recipients,
    keep_existing_password=False,
    allowed_senders="",
):
    """Validate and securely save email configuration."""
    address = (address or "").strip()
    app_password = (app_password or "").strip().replace(" ", "")
    recipients = (recipients or "").strip()
    allowed_senders = (allowed_senders or "").strip()

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
        raise ValueError("Enter a valid Gmail address.")

    if not recipients:
        raise ValueError("Enter at least one report recipient email address.")

    recipient_list = [item.strip() for item in recipients.split(",") if item.strip()]
    if not recipient_list or any(
        not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", item)
        for item in recipient_list
    ):
        raise ValueError("Enter valid recipient addresses separated by commas.")

    sender_list = [
        item.strip() for item in allowed_senders.split(",") if item.strip()
    ]
    if any(
        not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", item)
        for item in sender_list
    ):
        raise ValueError("Enter valid allowed sender addresses separated by commas.")

    if not app_password and not keep_existing_password:
        raise ValueError("Enter a Gmail app password to enable email reports.")

    existing_password = ""
    if keep_existing_password and EMAIL_CONFIG.is_file():
        for line in EMAIL_CONFIG.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GMAIL_APP_PASSWORD="):
                existing_password = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    password = app_password or existing_password
    if not password:
        raise ValueError("No saved app password is available; enter one to continue.")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)

    content = (
        f"GMAIL_ADDRESS={address}\n"
        f"GMAIL_APP_PASSWORD={password}\n"
        f"REPORT_RECIPIENTS={', '.join(recipient_list)}\n"
        f"ALLOWED_SENDERS={', '.join(sender_list)}\n"
    )

    # Create with owner-only permissions from the outset.
    fd = os.open(EMAIL_CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
    except Exception:
        raise

    EMAIL_CONFIG.chmod(0o600)
