"""Read and validate persistent PrepPi application settings."""

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
