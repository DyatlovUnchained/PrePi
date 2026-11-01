#!/usr/bin/env python3
"""Send a prepared stock report via Gmail SMTP."""
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
from paths import EMAIL_CONFIG as CONFIG
LOGO_PATH = BASE_DIR / "static" / "logo.png"
LOGO_CID = "stock-report-logo"


def load_report_config():
    values = {}
    if not CONFIG.is_file():
        raise RuntimeError(f"Email configuration file is missing: {CONFIG}")

    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    address = values.get("GMAIL_ADDRESS", "").strip()
    password = values.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    raw_recipients = values.get("REPORT_RECIPIENTS", "").strip()
    recipients = [
        address for _, address in getaddresses([raw_recipients])
        if address and "@" in address
    ]

    if not address or not password:
        raise RuntimeError("Gmail sender credentials are missing from email.env.")
    if not recipients:
        raise RuntimeError("REPORT_RECIPIENTS is missing or contains no valid email addresses.")
    if not LOGO_PATH.is_file():
        raise RuntimeError(f"Report logo is missing: {LOGO_PATH}")

    return address, password, recipients


def make_message(subject, html_body, sender, recipients):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        "This email contains an HTML stock-import report. "
        "Please view it in an email client that supports HTML."
    )
    msg.add_alternative(html_body, subtype="html")

    html_part = msg.get_payload()[-1]
    html_part.add_related(
        LOGO_PATH.read_bytes(),
        maintype="image",
        subtype="png",
        cid=f"<{LOGO_CID}>",
        filename="logo.png",
        disposition="inline",
    )
    return msg


def send_report(subject, html_body, preview_only=False):
    if preview_only:
        print("PREVIEW ONLY: no credentials read and no email sent.")
        print(f"Subject: {subject}")
        print(f"HTML body length: {len(html_body)} characters")
        return

    sender, password, recipients = load_report_config()
    msg = make_message(subject, html_body, sender, recipients)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, password)
        smtp.send_message(msg, from_addr=sender, to_addrs=recipients)

    print(f"Report sent to {len(recipients)} configured recipient(s).")


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: report_sender.py report.html [--preview]")
        return 2

    report_path = Path(sys.argv[1]).expanduser().resolve()
    preview_only = len(sys.argv) == 3 and sys.argv[2] == "--preview"
    if len(sys.argv) == 3 and not preview_only:
        print("The only supported option is --preview.")
        return 2
    if not report_path.is_file():
        print(f"Report file not found: {report_path}")
        return 2

    html_body = report_path.read_text(encoding="utf-8")
    send_report("PrepPi — stock import report", html_body, preview_only)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        print(f"Report send failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
