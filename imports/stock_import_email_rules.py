#!/usr/bin/env python3

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from paths import DB_PATH as DEFAULT_DB_PATH
HEADERS = ["Item", "Category", "Sub-category", "Expiry date", "Qty"]
EXTENDED_HEADERS = HEADERS + ["Unit size (g)"]

VALID_CATEGORIES = {
    "Food and Drink": {
        "Water", "Soup", "Beans", "Tinned Tomatoes", "Tinned Fruit",
        "Staples", "Protein", "Other Food",
    },
    "Fuel": {"Wood Logs", "Camping Stove Fuel", "Firelighting", "Other Fuel"},
    "Equipment": {"Lighting", "Batteries", "Cooking Equipment", "Other Equipment"},
    "Hygiene": {"Personal Hygiene", "Sanitation", "Cleaning Supplies", "Other Hygiene"},
    "First Aid": {"First Aid Kit", "Dressings and Bandages", "Antiseptic", "Other First Aid"},
    "Medicines": {
        "Pain Relief", "Allergy Relief", "Cold and Flu",
        "Regular Medication", "Other Medicines",
    },
    "Shelter and Warmth": {
        "Bedding", "Blankets", "Weather Protection", "Other Shelter and Warmth",
    },
    "Communications and Power": {
        "Radio", "Power Banks", "Charging", "Other Communications and Power",
    },
    "Pet Supplies": {"Pet Food"},
}


def clean_text(value):
    return "" if value is None else str(value).strip()


def parse_expiry(value, row_number):
    if value is None or clean_text(value) == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(clean_text(value)).isoformat()
    except ValueError:
        raise ValueError(
            f"Row {row_number}: expiry must be YYYY-MM-DD or blank."
        )


def read_spreadsheet(file_path):
    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        if "Stock Import" not in workbook.sheetnames:
            raise ValueError("Workbook must contain a 'Stock Import' sheet.")

        sheet = workbook["Stock Import"]
        headers = [clean_text(cell.value) for cell in sheet[4]]

        if headers == HEADERS:
            has_pack_size = False
            max_col = 5
        elif headers == EXTENDED_HEADERS:
            has_pack_size = True
            max_col = 6
        else:
            raise ValueError(
                "Unexpected headings in row 4. Expected either: "
                + " | ".join(HEADERS)
                + " OR "
                + " | ".join(EXTENDED_HEADERS)
            )

        records = []
        errors = []

        for row_number, values in enumerate(
            sheet.iter_rows(min_row=5, max_col=max_col, values_only=True),
            start=5,
        ):
            if has_pack_size:
                item, category, subcategory, expiry_value, qty_value, pack_value = values
            else:
                item, category, subcategory, expiry_value, qty_value = values
                pack_value = None

            if all(value is None or clean_text(value) == "" for value in values):
                continue

            item = clean_text(item)
            category = clean_text(category)
            subcategory = clean_text(subcategory)

            if not item:
                errors.append(f"Row {row_number}: item name is required.")
                continue
            if category not in VALID_CATEGORIES:
                errors.append(f"Row {row_number}: invalid category '{category}'.")
                continue
            if subcategory not in VALID_CATEGORIES[category]:
                errors.append(
                    f"Row {row_number}: subcategory '{subcategory}' is not valid "
                    f"for category '{category}'."
                )
                continue
            if (
                isinstance(qty_value, bool)
                or not isinstance(qty_value, (int, float))
                or int(qty_value) != qty_value
                or qty_value <= 0
            ):
                errors.append(f"Row {row_number}: Qty must be a positive whole number.")
                continue

            pack_size = None
            if pack_value is not None and clean_text(pack_value) != "":
                if (
                    isinstance(pack_value, bool)
                    or not isinstance(pack_value, (int, float))
                    or pack_value <= 0
                ):
                    errors.append(
                        f"Row {row_number}: pack size must be a positive number in grams."
                    )
                    continue
                pack_size = float(pack_value)

            if category == "Pet Supplies" and subcategory == "Pet Food" and pack_size is None:
                errors.append(
                    f"Row {row_number}: pet food requires a pack size in grams."
                )
                continue

            try:
                expiry = parse_expiry(expiry_value, row_number)
            except ValueError as exc:
                errors.append(str(exc))
                continue

            records.append({
                "row": row_number,
                "item": item,
                "category": category,
                "subcategory": subcategory,
                "expiry_date": expiry,
                "quantity": int(qty_value),
                "unit_size_grams": pack_size,
            })

        if not records and not errors:
            errors.append("No stock rows found in the spreadsheet.")

        return records, errors
    finally:
        workbook.close()


def run_import(action, file_path, db_path, apply_changes, message_id=None):
    records, errors = read_spreadsheet(file_path)

    print(f"Action: {action}")
    print(f"Spreadsheet: {file_path}")
    print(f"Database: {db_path}")
    print(f"Mode: {'APPLY' if apply_changes else 'DRY RUN'}\n")

    if errors:
        print("VALIDATION ERRORS — no changes made:")
        for error in errors:
            print(f"  - {error}")
        return 1

    connection = None
    try:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE" if apply_changes else "BEGIN")

        if message_id:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS email_imports (
                    message_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    workbook TEXT NOT NULL,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            already_done = connection.execute(
                "SELECT 1 FROM email_imports WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if already_done:
                connection.rollback()
                print("DUPLICATE MESSAGE — stock unchanged.")
                return 3

        # Plan every change first. No writes occur until all rows pass.
        plan = []
        for record in records:
            existing = connection.execute(
                """
                SELECT id, quantity
                FROM items
                WHERE item = ?
                  AND category = ?
                  AND subcategory = ?
                  AND COALESCE(expiry_date, '') = ?
                """,
                (
                    record["item"],
                    record["category"],
                    record["subcategory"],
                    record["expiry_date"],
                ),
            ).fetchall()

            if len(existing) > 1:
                raise ValueError(
                    f"Row {record['row']}: multiple matching stock records "
                    f"for '{record['item']}'."
                )

            current = int(existing[0]["quantity"] or 0) if existing else 0
            delta = record["quantity"] if action == "Stocked" else -record["quantity"]
            proposed = current + delta

            if action == "Removed" and (not existing or proposed < 0):
                raise ValueError(
                    f"Row {record['row']}: cannot remove {record['quantity']} "
                    f"of '{record['item']}'. Current matching quantity: {current}."
                )

            plan.append((record, existing[0]["id"] if existing else None, current, proposed))

        for record, existing_id, current, proposed in plan:
            if existing_id is not None:
                connection.execute(
                    "UPDATE items SET quantity = ? WHERE id = ?",
                    (proposed, existing_id),
                )
                operation = "UPDATE"
            elif action == "Stocked":
                connection.execute(
                    """
                    INSERT INTO items
                        (item, category, subcategory, expiry_date, logged, quantity, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["item"],
                        record["category"],
                        record["subcategory"],
                        record["expiry_date"],
                        date.today().isoformat(),
                        proposed,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                operation = "INSERT"
            else:
                raise ValueError(
                    f"Row {record['row']}: cannot remove an item that doesn't exist."
                )

            print(
                f"Row {record['row']}: {operation} | {record['item']} | "
                f"{current} -> {proposed}"
            )

        if apply_changes:
            if message_id:
                connection.execute(
                    "INSERT INTO email_imports (message_id, action, workbook) VALUES (?, ?, ?)",
                    (message_id, action, str(file_path)),
                )
            connection.commit()
            print(f"\nApplied successfully: {len(records)} row(s) changed.")
        else:
            connection.rollback()
            print(f"\nDry run passed: {len(records)} row(s) validated.")
            print("No changes have been made to the database.")

        return 0

    except (sqlite3.Error, ValueError) as exc:
        if connection is not None:
            connection.rollback()
        print(f"\nIMPORT REJECTED — no changes made:\n  {exc}")
        return 1
    finally:
        if connection is not None:
            connection.close()


def main():
    parser = argparse.ArgumentParser(
        description="Validate or apply a stock-import Excel workbook."
    )
    parser.add_argument(
        "action", choices=["Stocked", "Removed"],
        help="Stocked adds quantities; Removed subtracts quantities.",
    )
    parser.add_argument("workbook", help="Path to the Excel workbook.")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH),
        help="Database path (defaults to live stock.db).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply changes. Without this flag, the importer only previews.",
    )
    parser.add_argument(
        "--message-id",
        help="Email Message-ID for duplicate protection in this test copy.",
    )
    args = parser.parse_args()

    file_path = Path(args.workbook).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()

    if not file_path.is_file():
        print(f"Workbook not found: {file_path}", file=sys.stderr)
        return 2
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if args.apply and db_path == DEFAULT_DB_PATH.resolve():
        print(
            "SAFETY STOP: --apply is disabled for the live database. "
            "Use a separate test database.",
            file=sys.stderr,
        )
        return 2

    return run_import(args.action, file_path, db_path, args.apply, args.message_id)


if __name__ == "__main__":
    raise SystemExit(main())
