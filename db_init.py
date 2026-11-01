"""Initialise a fresh PrepPi household database."""

import sqlite3
from contextlib import closing
from pathlib import Path


def initialise_database(db_path):
    """Create the core tables and default water target if missing."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Existing databases must not be sent through first-run setup.
    was_existing_install = False
    if db_path.exists():
        with closing(sqlite3.connect(db_path)) as check_db, check_db:
            tables = {
                row[0] for row in check_db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            was_existing_install = {
                "items", "preparedness_targets"
            }.issubset(tables)

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                logged TEXT NOT NULL DEFAULT 'No',
                quantity INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Safely add pack size to older stock databases.
        item_columns = {
            row[1] for row in db.execute(
                "PRAGMA table_info(items)"
            ).fetchall()
        }
        if "unit_size_grams" not in item_columns:
            db.execute("""
                ALTER TABLE items
                ADD COLUMN unit_size_grams REAL
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                quantity INTEGER,
                notes TEXT NOT NULL DEFAULT ''
            )
        """)

        # Safely migrate older equipment tables without rebuilding them.
        equipment_columns = {
            row[1] for row in db.execute(
                "PRAGMA table_info(equipment)"
            ).fetchall()
        }
        if "category" not in equipment_columns:
            db.execute("""
                ALTER TABLE equipment
                ADD COLUMN category TEXT NOT NULL DEFAULT 'General'
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS pets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                species TEXT NOT NULL CHECK (species IN ('cat', 'dog')),
                daily_food_grams REAL NOT NULL CHECK (daily_food_grams > 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS preparedness_targets (
                target_key TEXT PRIMARY KEY,
                target_name TEXT NOT NULL,
                target_value REAL NOT NULL,
                unit TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS shopping_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                category TEXT NOT NULL,
                target_quantity REAL NOT NULL DEFAULT 1,
                unit TEXT NOT NULL DEFAULT 'item',
                notes TEXT NOT NULL DEFAULT '',
                acquired INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS food_nutrition_cache (
                product_key TEXT PRIMARY KEY,
                kcal_per_100g REAL,
                source TEXT NOT NULL,
                matched_product TEXT,
                checked_at REAL NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)

        default_settings = [
            ("household_size", "2"),
            ("daily_kcal_per_person", "2000"),
            ("water_litres_per_person_per_day", "3"),
            ("expiry_warning_days", "90"),
            ("expiry_urgent_days", "30"),
            ("setup_complete", "0"),
        ]
        db.executemany("""
            INSERT OR IGNORE INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
        """, default_settings)

        if was_existing_install:
            db.execute("""
                UPDATE app_settings
                SET setting_value = '1'
                WHERE setting_key = 'setup_complete'
            """)

        db.execute("""
            INSERT OR IGNORE INTO preparedness_targets
                (target_key, target_name, target_value, unit, notes)
            VALUES
                ('water', 'Drinking water', 84, 'litres',
                 'Illustrative 14-day household target; adjust for household needs.')
        """)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 db_init.py PATH_TO_DATABASE")

    initialise_database(sys.argv[1])
    print(f"Database initialised: {sys.argv[1]}")
