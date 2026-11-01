#!/usr/bin/env python3

import argparse
import email
import hashlib
import imaplib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from paths import EMAIL_CONFIG as CONFIG, EMAIL_DIR, EMAIL_LOG_DB as LOG_DB, DB_PATH

INBOX_DIR = EMAIL_DIR / "inbox"
ALLOWED_SUBJECTS = {"Stocked", "Removed"}


def load_config():
    for line in CONFIG.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    allowed = {
        item.strip().lower()
        for item in os.environ.get("ALLOWED_SENDERS", "").split(",")
        if item.strip()
    }

    if not address or not password or not allowed:
        raise RuntimeError("Gmail credentials or allowed senders are missing.")

    return address, password, allowed


def decode_mime(value):
    result = []
    for part, encoding in decode_header(value or ""):
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result).strip()


def initialise_log():
    with sqlite3.connect(LOG_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                subject TEXT NOT NULL,
                attachment_hash TEXT,
                status TEXT NOT NULL,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                details TEXT
            )
        """)
    LOG_DB.chmod(0o600)



def run_stock_processor(action, workbook, message_id, db_path,
                        allow_live=False, send_report=False):
    """Invoke the stock processor with explicit live/report permissions."""
    base_dir = Path(__file__).resolve().parent.parent
    processor = base_dir / "email" / "stock_email_processor.py"
    live_db = DB_PATH.resolve()
    selected_db = Path(db_path).expanduser().resolve()
    selected_workbook = Path(workbook).expanduser().resolve()

    if action not in ALLOWED_SUBJECTS:
        raise ValueError("Action must be Stocked or Removed.")
    if selected_db == live_db and not allow_live:
        raise ValueError("Safety stop: live stock.db requires explicit permission.")
    if not selected_db.is_file():
        raise ValueError(f"Test database does not exist: {selected_db}")
    if not selected_workbook.is_file():
        raise ValueError(f"Workbook does not exist: {selected_workbook}")
    if not message_id or not message_id.strip():
        raise ValueError("Message-ID must not be blank.")

    command = [
        sys.executable, str(processor), "process",
        "--db", str(selected_db),
        "--action", action,
        "--workbook", str(selected_workbook),
        "--message-id", message_id,
    ]
    if allow_live:
        command.append("--allow-live-db")
    if send_report:
        command.append("--send")
    return subprocess.run(command, check=False).returncode


def process_saved_attachment(unique_id, subject, workbook):
    """Import once; on duplicates, retry only the saved report."""
    base_dir = Path(__file__).resolve().parent.parent
    live_db = DB_PATH
    processor = base_dir / "email" / "stock_email_processor.py"

    rc = run_stock_processor(
        subject, workbook, unique_id, live_db,
        allow_live=True, send_report=True,
    )

    if rc == 3:
        # Existing ledger entry: do not re-import; retry its saved report only.
        retry = subprocess.run([
            sys.executable, str(processor), "retry",
            "--db", str(live_db), "--allow-live-db",
            "--message-id", unique_id, "--send",
        ], check=False)
        rc = retry.returncode

    status = "processed" if rc == 0 else (
        "report_pending" if rc == 2 else "processor_failed"
    )
    with sqlite3.connect(LOG_DB) as conn:
        conn.execute(
            "UPDATE processed_emails SET status = ?, details = ? WHERE message_id = ?",
            (status, f"Auto-processing enabled; processor exit code: {rc}; workbook: {Path(workbook).name}", unique_id),
        )
    print(f"Stock processor status: {status} (exit {rc})")
    return rc


def main():
    address, password, allowed = load_config()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_DIR.chmod(0o700)
    initialise_log()

    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)

    try:
        mail.login(address, password)
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Could not open inbox read-only.")

        status, data = mail.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Could not search inbox.")

        message_ids = data[0].split()
        print(f"Unread messages: {len(message_ids)}")
        matched = 0
        skipped = 0

        with sqlite3.connect(LOG_DB) as conn:
            for message_id in message_ids:
                uid = message_id.decode("ascii", errors="replace")

                # IMAP UIDs are stable for a message within this mailbox.
                # Keep fetching the message body without marking it as read.
                status, fetched = mail.uid("fetch", message_id, "(BODY.PEEK[])")
                if status != "OK":
                    continue

                raw = next(
                    (part[1] for part in fetched if isinstance(part, tuple)),
                    None,
                )
                if not raw:
                    continue

                msg = email.message_from_bytes(raw)
                sender = parseaddr(msg.get("From", ""))[1].strip().lower()
                subject = decode_mime(msg.get("Subject"))

                if sender not in allowed or subject not in ALLOWED_SUBJECTS:
                    continue

                matched += 1
                attachments = []
                attachment_hashes = []

                for part in msg.walk():
                    if part.is_multipart():
                        continue

                    filename = decode_mime(part.get_filename())
                    if not filename.lower().endswith(".xlsx"):
                        continue

                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    digest = hashlib.sha256(payload).hexdigest()
                    attachment_hashes.append(digest)
                    attachments.append((filename, payload, digest))

                # A stable Internet Message-ID is preferable to an IMAP sequence
                # number, which can change. Fall back to a mailbox-scoped value.
                header_id = (msg.get("Message-ID") or "").strip()
                unique_id = header_id or f"INBOX-UID-{uid}"

                existing = conn.execute(
                    "SELECT status, details FROM processed_emails WHERE message_id = ?",
                    (unique_id,),
                ).fetchone()

                if existing:
                    details = existing[1] or ""
                    if (
                        "Auto-processing enabled" in details
                        and existing[0] in {"downloaded", "report_pending", "processor_failed"}
                    ):
                        if existing[0] == "report_pending":
                            # The stock transaction already committed; retry report only.
                            processor = Path(__file__).resolve().parent / "stock_email_processor.py"
                            result = subprocess.run([
                                sys.executable, str(processor), "retry",
                                "--db", str(DB_PATH),
                                "--allow-live-db", "--message-id", unique_id, "--send",
                            ], check=False)
                            new_status = "processed" if result.returncode == 0 else "report_pending"
                            conn.execute(
                                "UPDATE processed_emails SET status = ?, details = ? WHERE message_id = ?",
                                (new_status, f"Auto-processing enabled; report retry exit code: {result.returncode}", unique_id),
                            )
                            conn.commit()
                            print(f"Report retry status: {new_status} (exit {result.returncode})")
                        else:
                            prefix = hashlib.sha256(unique_id.encode()).hexdigest()[:12]
                            saved_files = sorted(INBOX_DIR.glob(f"{prefix}_*.xlsx"))
                            if len(saved_files) == 1:
                                process_saved_attachment(unique_id, subject, saved_files[0])
                            else:
                                print(
                                    "Recovery needs review: expected one saved workbook, "
                                    f"found {len(saved_files)}."
                                )
                    else:
                        skipped += 1
                        print(f"\nSkipping previously logged email: {unique_id}")
                        print(f"Previous status: {existing[0]}")
                    continue

                saved = []
                for filename, payload, digest in attachments:
                    safe_name = f"{hashlib.sha256(unique_id.encode()).hexdigest()[:12]}_{digest[:12]}.xlsx"
                    destination = INBOX_DIR / safe_name

                    if not destination.exists():
                        with destination.open("xb") as handle:
                            handle.write(payload)
                        destination.chmod(0o600)

                    saved.append(f"{filename} -> {safe_name}")

                # Log as downloaded only; this is NOT a stock-import record.
                conn.execute(
                    """
                    INSERT INTO processed_emails
                        (message_id, sender, subject, attachment_hash, status, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unique_id,
                        sender,
                        subject,
                        ",".join(attachment_hashes) or None,
                        "downloaded" if saved else "no_attachment",
                        f"Auto-processing enabled; saved attachments: {len(saved)}",
                    ),
                )
                conn.commit()

                print(f"\nSender: {sender}")
                print(f"Subject: {subject}")
                print(f"Attachments: {saved or 'No .xlsx attachments'}")
                if len(saved) == 1:
                    prefix = hashlib.sha256(unique_id.encode()).hexdigest()[:12]
                    saved_files = sorted(INBOX_DIR.glob(f"{prefix}_*.xlsx"))
                    if len(saved_files) == 1:
                        process_saved_attachment(unique_id, subject, saved_files[0])
                    else:
                        print("Automatic import skipped: workbook could not be uniquely identified.")
                elif saved:
                    with sqlite3.connect(LOG_DB) as update_conn:
                        update_conn.execute(
                            "UPDATE processed_emails SET status = ?, details = ? WHERE message_id = ?",
                            ("needs_review", "Multiple Excel attachments; no automatic import performed.", unique_id),
                        )
                    print("Automatic import skipped: multiple Excel attachments need review.")

        print(f"\nMatching messages: {matched}")
        print(f"Already logged and skipped: {skipped}")
        print("Automatic processing was attempted for eligible messages above.")
        print("Check each processor status above for import or report outcome.")

    finally:
        try:
            mail.logout()
        except Exception:
            pass


def test_handoff_cli():
    parser = argparse.ArgumentParser(
        description="Explicit test-only handoff; normal runs remain download-only."
    )
    parser.add_argument(
        "--test-handoff", action="store_true", required=True,
        help="Explicitly run the stock processor in test mode"
    )
    parser.add_argument("--db", required=True, help="Existing isolated test database")
    parser.add_argument("--action", choices=sorted(ALLOWED_SUBJECTS), required=True)
    parser.add_argument("--workbook", required=True, help="Test workbook path")
    parser.add_argument("--message-id", required=True, help="Unique synthetic message ID")
    args = parser.parse_args()

    return run_stock_processor(
        args.action, args.workbook, args.message_id, args.db
    )


if __name__ == "__main__":
    if len(__import__("sys").argv) == 1:
        main()
    else:
        raise SystemExit(test_handoff_cli())
