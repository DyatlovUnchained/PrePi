"""Read and validate persistent PrepPi application settings."""

import sqlite3

from paths import DB_PATH

DEFAULTS = {
    "adults": 2,
    "children": 0,
    "daily_kcal_per_person": 2000,
    "water_litres_per_person_per_day": 3,
    "expiry_warning_days": 90,
    "expiry_urgent_days": 30,
}


def get_settings(db):
    """Return validated settings from an open SQLite connection."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    """)

    saved = dict(db.execute(
        "SELECT setting_key, setting_value FROM app_settings"
    ).fetchall())

    # Migrate older installations while preserving their existing
    # household total as the initial adult count.
    if "adults" not in saved and "children" not in saved:
        try:
            legacy_size = int(float(saved.get("household_size", 2)))
        except (TypeError, ValueError):
            legacy_size = 2

        if not 1 <= legacy_size <= 30:
            legacy_size = 2

        db.execute("""
            INSERT OR IGNORE INTO app_settings (setting_key, setting_value)
            VALUES ('adults', ?)
        """, (str(legacy_size),))
        db.execute("""
            INSERT OR IGNORE INTO app_settings (setting_key, setting_value)
            VALUES ('children', '0')
        """)

    db.executemany("""
        INSERT OR IGNORE INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
    """, [(key, str(value)) for key, value in DEFAULTS.items()])

    saved = dict(db.execute(
        "SELECT setting_key, setting_value FROM app_settings"
    ).fetchall())

    settings = {}

    for key in ("adults", "children"):
        try:
            value = int(float(saved.get(key, DEFAULTS[key])))
            if value < 0 or value > 30:
                raise ValueError
            settings[key] = value
        except (TypeError, ValueError):
            settings[key] = DEFAULTS[key]

    settings["household_size"] = settings["adults"] + settings["children"]

    if not 1 <= settings["household_size"] <= 30:
        settings["adults"] = DEFAULTS["adults"]
        settings["children"] = DEFAULTS["children"]
        settings["household_size"] = 2

    for key in (
        "daily_kcal_per_person",
        "water_litres_per_person_per_day",
        "expiry_warning_days",
        "expiry_urgent_days",
    ):
        default = DEFAULTS[key]
        try:
            value = float(saved.get(key, default))
            if not value > 0:
                raise ValueError
            settings[key] = int(value) if isinstance(default, int) else value
        except (TypeError, ValueError):
            settings[key] = default

    # Urgent threshold must not exceed the general warning threshold.
    settings["expiry_urgent_days"] = min(
        settings["expiry_urgent_days"],
        settings["expiry_warning_days"],
    )

    settings["daily_kcal_target"] = (
        settings["household_size"] * settings["daily_kcal_per_person"]
    )
    settings["daily_water_target_litres"] = (
        settings["household_size"]
        * settings["water_litres_per_person_per_day"]
    )

    return settings





def read_daily_kcal_target(db):
    """Read the household calorie target without modifying the database."""
    try:
        saved = dict(db.execute(
            "SELECT setting_key, setting_value FROM app_settings"
        ).fetchall())
    except sqlite3.OperationalError:
        return 4000

    # Match get_settings() legacy household-size migration behaviour.
    if "adults" not in saved and "children" not in saved:
        try:
            legacy_size = int(float(saved.get("household_size", 2)))
        except (TypeError, ValueError):
            legacy_size = 2

        if not 1 <= legacy_size <= 30:
            legacy_size = 2

        adults, children = legacy_size, 0
    else:
        def household_value(key, default):
            try:
                value = int(float(saved.get(key, default)))
                return value if 0 <= value <= 30 else default
            except (TypeError, ValueError):
                return default

        adults = household_value("adults", 2)
        children = household_value("children", 0)

    household_size = adults + children
    if not 1 <= household_size <= 30:
        household_size = 2

    try:
        kcal_per_person = float(saved.get("daily_kcal_per_person", 2000))
        if not kcal_per_person > 0:
            raise ValueError
    except (TypeError, ValueError):
        kcal_per_person = 2000

    return int(household_size * kcal_per_person)
