#!/usr/bin/env python3
"""PrepPi expiry alerts. Defaults to preview-only; --send sends due alerts."""
import argparse
import sqlite3
import sys
from datetime import date
from html import escape
from pathlib import Path

import smtplib

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from paths import DB_PATH, EXPIRY_ALERT_DB as ALERT_DB_PATH
from report_sender import load_report_config, make_message

ALERTS = (
    (30, "30-day warning"),
    (7, "7-day warning"),
    (0, "Expiry-day alert"),
)


def get_due_alerts(db_path, alert_db_path, today):
    """Read stock without modifying it; initialise a separate alert ledger."""
    db_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(db_uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT id, item, category, subcategory, expiry_date, quantity
            FROM items
            WHERE quantity > 0 AND expiry_date IS NOT NULL
              AND TRIM(expiry_date) <> ''
            ORDER BY expiry_date, item, id
        """).fetchall()

    already_sent = set()
    if alert_db_path.is_file():
        with sqlite3.connect(
            f"{alert_db_path.resolve().as_uri()}?mode=ro", uri=True
        ) as ledger:
            table_exists = ledger.execute("""
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'sent_alerts'
            """).fetchone()
            if table_exists:
                already_sent = {
                    (row[0], row[1], row[2])
                    for row in ledger.execute(
                        "SELECT item_id, expiry_date, alert_days FROM sent_alerts"
                    )
                }

    due = []
    for row in rows:
        expiry_text = row["expiry_date"].strip()
        try:
            expiry = date.fromisoformat(expiry_text)
        except ValueError:
            print(
                f"WARNING: skipping invalid expiry date for item ID {row['id']}: "
                f"{expiry_text!r}",
                file=sys.stderr,
            )
            continue

        days_left = (expiry - today).days
        for alert_days, label in ALERTS:
            key = (row["id"], expiry_text, alert_days)
            if days_left == alert_days and key not in already_sent:
                due.append({
                    "id": row["id"],
                    "item": row["item"],
                    "quantity": row["quantity"],
                    "expiry_date": expiry_text,
                    "days_left": days_left,
                    "alert_days": alert_days,
                    "label": label,
                })

    return due


def build_html(due, today):
    if not due:
        body = "<p>No expiry alerts are due today.</p>"
    else:
        rows = []
        for item in due:
            if item["days_left"] == 0:
                timing = "expires today"
            else:
                timing = f'expires in {item["days_left"]} days'
            rows.append(
                "<tr>"
                f'<td>{escape(str(item["item"]))}</td>'
                f'<td>{escape(str(item["quantity"]))}</td>'
                f'<td>{escape(item["expiry_date"])}</td>'
                f'<td>{escape(timing)}</td>'
                f'<td>{escape(item["label"])}</td>'
                "</tr>"
            )
        body = (
            "<p>The following owned stock needs an expiry-date check:</p>"
            "<table border='1' cellpadding='6' cellspacing='0'>"
            "<thead><tr><th>Item</th><th>Quantity</th><th>Expiry date</th>"
            "<th>Timing</th><th>Alert</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
            "<p>Please check the product label and storage instructions.</p>"
        )

    return (
        "<html><body>"
        "<h2>PrepPi — stock expiry alerts</h2>"
        f"<p>Checked on {escape(today.isoformat())}.</p>"
        f"{body}"
        "</body></html>"
    )


def send_due_alerts(due, today):
    """Send one email containing all due alerts; ledger updates only after success."""
    from email.utils import formatdate
    from email.message import EmailMessage

    sender, password, recipients = load_report_config()
    subject = (
        f"PrepPi — expiry alerts ({len(due)} item alert"
        f"{'' if len(due) == 1 else 's'})"
    )
    msg = make_message(subject, build_html(due, today), sender, recipients)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, password)
        smtp.send_message(msg, from_addr=sender, to_addrs=recipients)

    try:
        ALERT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(ALERT_DB_PATH) as ledger:
            ledger.execute("""
                CREATE TABLE IF NOT EXISTS sent_alerts (
                    item_id INTEGER NOT NULL,
                    expiry_date TEXT NOT NULL,
                    alert_days INTEGER NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (item_id, expiry_date, alert_days)
                )
            """)
            ledger.executemany(
                """
                INSERT OR IGNORE INTO sent_alerts
                    (item_id, expiry_date, alert_days)
                VALUES (?, ?, ?)
                """,
                [
                    (item["id"], item["expiry_date"], item["alert_days"])
                    for item in due
                ],
            )
    except sqlite3.Error:
        print(
            "WARNING: email was sent, but the alert ledger could not be updated. "
            "A duplicate alert may be sent on a later run.",
            file=sys.stderr,
        )
        raise

    print(f"Expiry alert email sent to {len(recipients)} configured recipient(s).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--alert-db", default=str(ALERT_DB_PATH))
    parser.add_argument("--today", help="Override date for testing (YYYY-MM-DD).")
    parser.add_argument(
        "--send", action="store_true",
        help="Actually send due alerts and record them after successful sending.",
    )
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    due = get_due_alerts(Path(args.db), Path(args.alert_db), today)

    print("MODE:", "SEND" if args.send else "PREVIEW ONLY")
    print("Date checked:", today)
    print("Due alerts:", len(due))
    for item in due:
        print(
            f'  ID {item["id"]}: {item["item"]} | qty={item["quantity"]} | '
            f'{item["label"]} | expiry={item["expiry_date"]}'
        )

    if not args.send:
        print("No email sent.")
        print("Alert ledger initialised/read; inventory database was read-only.")
        return 0

    if not due:
        print("No email sent because no alerts are due.")
        return 0

    send_due_alerts(due, today)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, sqlite3.Error, smtplib.SMTPException, ValueError) as exc:
        print(f"Expiry alert check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
