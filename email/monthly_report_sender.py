#!/usr/bin/env python3
"""Generate and optionally send the monthly PrepPi preparedness report."""
import argparse
import smtplib
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from monthly_report import read_inventory, build_html, build_workbook
from report_sender import load_report_config, make_message

from paths import DB_PATH
SUBJECT = "PrepPi — monthly preparedness report"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send", action="store_true",
        help="Actually send the email; without this option, preview only."
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="preppi-monthly-") as tmp:
        out = Path(tmp)
        stock, equipment, targets, shopping, pets = read_inventory(DB_PATH)

        from datetime import date
        today = date.today()
        html_path = out / "monthly-preparedness-report.html"
        xlsx_path = out / "monthly-owned-inventory.xlsx"

        html_path.write_text(
            build_html(stock, equipment, targets, shopping, today, pets=pets),
            encoding="utf-8",
        )
        build_workbook(stock, equipment, xlsx_path, pets=pets)

        if not args.send:
            print("PREVIEW ONLY — no credentials read and no email sent.")
            print(f"Subject: {SUBJECT}")
            print(f"HTML report generated: {html_path.name}")
            print(f"Excel attachment: {xlsx_path.name} ({xlsx_path.stat().st_size} bytes)")
            print(f"Owned stock records: {len(stock)}")
            print(f"Equipment records: {len(equipment)}")
            return 0

        sender, password, recipients = load_report_config()
        html_body = html_path.read_text(encoding="utf-8")
        msg = make_message(SUBJECT, html_body, sender, recipients)
        msg.add_attachment(
            xlsx_path.read_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="PrepPi-monthly-owned-inventory.xlsx",
        )

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.send_message(msg, from_addr=sender, to_addrs=recipients)

        print(f"Monthly report sent to {len(recipients)} configured recipient(s).")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        print(f"Monthly report failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
