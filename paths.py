"""Standard filesystem locations for PrepPi."""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(
    os.environ.get(
        "PREPPI_DATA_DIR",
        Path.home() / ".local" / "share" / "preppi",
    )
).expanduser()

CONFIG_DIR = Path(
    os.environ.get(
        "PREPPI_CONFIG_DIR",
        Path.home() / ".config" / "preppi",
    )
).expanduser()

DB_PATH = DATA_DIR / "stock.db"
EMAIL_DIR = DATA_DIR / "email"
EMAIL_CONFIG = CONFIG_DIR / "email.env"
EMAIL_LOG_DB = EMAIL_DIR / "email_log.db"
EXPIRY_ALERT_DB = EMAIL_DIR / "expiry_alerts.db"
