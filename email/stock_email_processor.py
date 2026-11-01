#!/usr/bin/env python3
"""Test-first stock email processor with atomic ledger and report retry."""

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from paths import DB_PATH
LIVE_DB = DB_PATH.resolve()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = load_module(
    "stock_import_rules",
    BASE_DIR / "imports" / "stock_import_email_rules.py",
)
reports = load_module(
    "stock_report_builder",
    BASE_DIR / "email" / "report_builder.py",
)
sender = load_module(
    "stock_report_sender",
    BASE_DIR / "email" / "report_sender.py",
)


def checked_db_path(value, allow_live=False):
    path = Path(value).expanduser().resolve()
    if path == LIVE_DB and not allow_live:
        raise ValueError(
            "Safety stop: live stock.db requires --allow-live-db."
        )
    if not path.is_file():
        raise ValueError(f"Database does not exist: {path}")
    return path


def connect_db(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def ensure_schema(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS email_imports (
            message_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            workbook TEXT NOT NULL,
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            report_status TEXT NOT NULL DEFAULT 'not_required',
            report_subject TEXT,
            report_html TEXT,
            change_details TEXT,
            report_error TEXT,
            import_error TEXT
        )
    """)
    existing = {
        row["name"] for row in db.execute("PRAGMA table_info(email_imports)")
    }
    additions = {
        "report_status": "TEXT NOT NULL DEFAULT 'not_required'",
        "report_subject": "TEXT",
        "report_html": "TEXT",
        "change_details": "TEXT",
        "report_error": "TEXT",
        "import_error": "TEXT",
    }
    for name, definition in additions.items():
        if name not in existing:
            db.execute(
                f"ALTER TABLE email_imports ADD COLUMN {name} {definition}"
            )


def report_subject(outcome, action, message_id):
    label = "SUCCESS" if outcome == "success" else "FAILURE"
    return f"PrepPi — stock import {label} ({action}) [{message_id}]"


def save_failure(db, action, workbook, message_id, error,
                 send=False, simulate_failure=False):
    """Record a failed import without applying stock changes."""
    db.rollback()
    db.execute("BEGIN IMMEDIATE")

    if db.execute(
        "SELECT 1 FROM email_imports WHERE message_id = ?", (message_id,)
    ).fetchone():
        db.rollback()
        print("DUPLICATE MESSAGE — existing ledger entry preserved.")
        return 3

    subject = report_subject("failure", action, message_id)
    html, _ = reports.build_report(
        db, "failure", action, message_id=message_id,
        error=str(error), workbook_name=Path(workbook).name,
        connection=db,
    )
    db.execute("""
        INSERT INTO email_imports
            (message_id, action, workbook, report_status, report_subject,
             report_html, change_details, report_error, import_error)
        VALUES (?, ?, ?, 'pending', ?, ?, '[]', NULL, ?)
    """, (message_id, action, str(workbook), subject, html, str(error)))
    db.commit()
    print("IMPORT FAILED — no stock changes made.")
    print("Failure report saved with status: pending")
    print("Reason:", error)
    if send or simulate_failure:
        return send_saved_report(db, message_id, simulate_failure=simulate_failure)
    return 1


def send_saved_report(db, message_id, simulate_failure=False):
    row = db.execute("""
        SELECT report_status, report_subject, report_html
        FROM email_imports WHERE message_id = ?
    """, (message_id,)).fetchone()

    if not row:
        print("No ledger entry found.")
        return 1
    if row["report_status"] == "sent":
        print("Report already marked sent; no email resent.")
        return 0
    if row["report_status"] != "pending" or not row["report_html"]:
        print("No saved pending report is available.")
        return 1

    try:
        if simulate_failure:
            raise RuntimeError("Simulated SMTP failure for testing.")
        sender.send_report(row["report_subject"], row["report_html"])
    except Exception as exc:
        db.execute("""
            UPDATE email_imports SET report_status = 'pending',
                report_error = ? WHERE message_id = ?
        """, (str(exc), message_id))
        db.commit()
        print("Report remains pending; stock will not be re-imported.")
        print("Send error:", exc)
        return 2

    db.execute("""
        UPDATE email_imports SET report_status = 'sent',
            report_error = NULL WHERE message_id = ?
    """, (message_id,))
    db.commit()
    print("Report marked sent.")
    return 0


def process_import(db_path, action, workbook, message_id, send, simulate_failure):
    if action not in {"Stocked", "Removed"}:
        raise ValueError("Action must be Stocked or Removed.")
    workbook = Path(workbook).expanduser().resolve()
    if not workbook.is_file():
        raise ValueError(f"Workbook does not exist: {workbook}")
    if not message_id.strip():
        raise ValueError("Message-ID must not be blank.")

    # Parse before opening the stock transaction; invalid sheets never change stock.
    try:
        records, errors = rules.read_spreadsheet(workbook)
    except Exception as exc:
        records, errors = [], [str(exc)]

    db = connect_db(db_path)
    try:
        ensure_schema(db)
        db.commit()
        db.execute("BEGIN IMMEDIATE")

        if db.execute(
            "SELECT 1 FROM email_imports WHERE message_id = ?", (message_id,)
        ).fetchone():
            db.rollback()
            print("DUPLICATE MESSAGE — stock unchanged.")
            print("If its report is pending, use the retry command.")
            return 3

        if errors:
            return save_failure(
                db, action, workbook, message_id, "; ".join(errors),
                send, simulate_failure,
            )

        plan = []
        try:
            # Validate every row and plan every change before writing.
            for record in records:
                existing = db.execute("""
                    SELECT id, quantity, unit_size_grams FROM items
                    WHERE item = ? AND category = ? AND subcategory = ?
                      AND COALESCE(expiry_date, '') = ?
                """, (
                    record["item"], record["category"],
                    record["subcategory"], record["expiry_date"],
                )).fetchall()

                if len(existing) > 1:
                    raise ValueError(
                        f"Multiple matching records for {record['item']}."
                    )

                old_qty = int(existing[0]["quantity"] or 0) if existing else 0
                delta = record["quantity"] if action == "Stocked" else -record["quantity"]
                new_qty = old_qty + delta

                if action == "Removed" and (not existing or new_qty < 0):
                    raise ValueError(
                        f"Cannot remove {record['quantity']} of "
                        f"{record['item']}; current quantity is {old_qty}."
                    )
                if action == "Removed" and not existing:
                    raise ValueError(
                        f"Cannot remove missing item {record['item']}."
                    )

                plan.append((record, existing[0]["id"] if existing else None,
                             old_qty, new_qty))

            changes = []
            for record, existing_id, old_qty, new_qty in plan:
                if existing_id is not None:
                    db.execute(
                        """UPDATE items
                           SET quantity = ?,
                               unit_size_grams = COALESCE(?, unit_size_grams)
                           WHERE id = ?""",
                        (new_qty, record["unit_size_grams"], existing_id),
                    )
                    operation = "UPDATE"
                elif action == "Stocked":
                    db.execute("""
                        INSERT INTO items
                            (item, category, subcategory, expiry_date,
                             logged, quantity, created_at, unit_size_grams)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        record["item"], record["category"],
                        record["subcategory"], record["expiry_date"],
                        date.today().isoformat(), new_qty,
                        datetime.now().isoformat(timespec="seconds"),
                        record["unit_size_grams"],
                    ))
                    operation = "INSERT"
                else:
                    raise ValueError(
                        f"Cannot remove missing item {record['item']}."
                    )

                changes.append({
                    "item": record["item"],
                    "category": record["category"],
                    "operation": operation,
                    "old_quantity": old_qty,
                    "new_quantity": new_qty,
                })

        except ValueError as exc:
            return save_failure(
                db, action, workbook, message_id, exc, send, simulate_failure
            )

        # Build against this transaction's connection, so the report sees
        # the post-import stock before the stock and ledger commit together.
        subject = report_subject("success", action, message_id)
        html, _ = reports.build_report(
            db, "success", action, changes=changes,
            message_id=message_id, workbook_name=workbook.name,
            connection=db,
        )
        db.execute("""
            INSERT INTO email_imports
                (message_id, action, workbook, report_status, report_subject,
                 report_html, change_details, report_error)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, NULL)
        """, (
            message_id, action, str(workbook), subject, html,
            json.dumps(changes),
        ))
        db.commit()

        print(f"IMPORT COMMITTED — {len(changes)} row(s) changed.")
        for change in changes:
            print(
                f"{change['operation']}: {change['item']} "
                f"{change['old_quantity']} -> {change['new_quantity']}"
            )
        print("Report saved with status: pending")

        if send or simulate_failure:
            return send_saved_report(
                db, message_id, simulate_failure=simulate_failure
            )
        print("Email not sent. Use --send to send, or retry later.")
        return 0
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    process = sub.add_parser("process", help="Import a workbook once")
    process.add_argument("--db", required=True)
    process.add_argument("--allow-live-db", action="store_true",
                         help="Explicitly permit the live stock database")
    process.add_argument("--action", required=True, choices=["Stocked", "Removed"])
    process.add_argument("--workbook", required=True)
    process.add_argument("--message-id", required=True)
    process.add_argument("--send", action="store_true",
                         help="Send the report after the database commit")
    process.add_argument("--simulate-send-failure", action="store_true",
                         help="Test retry handling without sending email")

    retry = sub.add_parser("retry", help="Retry a saved report only")
    retry.add_argument("--db", required=True)
    retry.add_argument("--allow-live-db", action="store_true",
                       help="Explicitly permit the live stock database")
    retry.add_argument("--message-id", required=True)
    retry.add_argument("--send", action="store_true",
                       help="Actually send the saved report")
    retry.add_argument("--simulate-send-failure", action="store_true",
                       help="Test failure handling without sending email")

    status = sub.add_parser("status", help="Read import status without changing anything")
    status.add_argument("--db", required=True)
    status.add_argument("--message-id", required=True)

    args = parser.parse_args()
    try:
        db_path = checked_db_path(
            args.db, getattr(args, "allow_live_db", False)
        )
        if args.command == "process":
            return process_import(
                db_path, args.action, args.workbook, args.message_id,
                args.send, args.simulate_send_failure,
            )

        db = connect_db(db_path)
        if args.command == "status":
            try:
                columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(email_imports)")
                }
                if "message_id" not in columns:
                    print("NOT FOUND — import ledger does not exist in this database.")
                    return 0
                row = db.execute(
                    "SELECT action, processed_at, report_status, import_error, report_error "
                    "FROM email_imports WHERE message_id = ?",
                    (args.message_id,),
                ).fetchone()
                if row is None:
                    print("NOT FOUND — message has no import ledger record.")
                else:
                    print(f"Message ID: {args.message_id}")
                    print(f"Action: {row['action']}")
                    print(f"Processed at: {row['processed_at']}")
                    print(f"Report status: {row['report_status']}")
                    print(f"Import error: {row['import_error'] or 'None'}")
                    print(f"Report error: {row['report_error'] or 'None'}")
                return 0
            finally:
                db.close()

        try:
            ensure_schema(db)
            db.commit()
            return send_saved_report(
                db, args.message_id, args.simulate_send_failure
            ) if args.send or args.simulate_send_failure else (
                print("No email sent. Add --send to retry delivery.") or 0
            )
        finally:
            db.close()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
