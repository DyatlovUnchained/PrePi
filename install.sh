#!/bin/bash
set -Eeuo pipefail

APP_NAME="PrepPi"
APP_VERSION="0.1.0"
APP_DIR="/opt/preppi"
DATA_DIR="/var/lib/preppi"
CONFIG_DIR="/etc/preppi"
SERVICE_USER="preppi"
SYSTEMD_DIR="/etc/systemd/system"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="/var/backups/preppi"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

log() {
    echo
    echo "[$APP_NAME] $1"
}

fail() {
    echo
    echo "ERROR: $1" >&2
    exit 1
}

# Track resources created by this installation for safe rollback.
APP_DIR_CREATED=0
SERVICE_ACCOUNT_CREATED=0
SERVICE_STARTED=0
SERVICE_ENABLED=0
SYSTEMD_RELOADED=0
INSTALLED_UNITS=()

cleanup() {
    if [[ -n "${STAGING_DB:-}" && -f "$STAGING_DB" ]]; then
        rm -f -- "$STAGING_DB"
    fi
}

rollback_on_error() {
    local status=$?
    local service_stopped=1
    trap - ERR
    set +e

    echo
    echo "[PrepPi] Installation failed (exit code $status)."
    echo "[PrepPi] Attempting conservative rollback..."

    if [[ "$SERVICE_STARTED" -eq 1 ]]; then
        systemctl stop preppi.service
        if systemctl is-active --quiet preppi.service; then
            service_stopped=0
            echo "[PrepPi] WARNING: Service is still active; preserving application files."
        else
            state="$(systemctl is-active preppi.service 2>/dev/null)"
            if [[ "$state" != "inactive" && "$state" != "failed" ]]; then
                service_stopped=0
                echo "[PrepPi] WARNING: Could not confirm service is stopped; preserving application files."
            fi
        fi
    fi

    if [[ "$SERVICE_ENABLED" -eq 1 ]]; then
        systemctl disable preppi.service
    fi

    if [[ "$service_stopped" -eq 1 ]]; then
        for unit in "${INSTALLED_UNITS[@]}"; do
            rm -f -- "$SYSTEMD_DIR/$unit"
        done
    else
        echo "[PrepPi] WARNING: Preserving systemd unit files because service shutdown was not confirmed."
    fi

    if [[ "$SYSTEMD_RELOADED" -eq 1 ]]; then
        systemctl daemon-reload
    fi

    if [[ "$APP_DIR_CREATED" -eq 1 && "$service_stopped" -eq 1 ]]; then
        rm -rf -- "$APP_DIR"
    fi

    if [[ "$SERVICE_ACCOUNT_CREATED" -eq 1 && "$service_stopped" -eq 1 ]]; then
        userdel "$SERVICE_USER"
    fi

    echo
    echo "[PrepPi] Rollback attempted."
    echo "[PrepPi] Inventory, configuration and backups have been preserved."
    echo "[PrepPi] Review the error above before retrying."
    exit "$status"
}

trap cleanup EXIT
trap rollback_on_error ERR

if [[ $EUID -ne 0 ]]; then
    fail "Please run this installer using: sudo ./install.sh"
fi

log "PrepPi $APP_VERSION installer"

if [[ ! -f "$SCRIPT_DIR/app.py" || ! -f "$SCRIPT_DIR/db_init.py" ]]; then
    fail "Application files not found. Run this installer from the release directory."
fi

if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
    fail "requirements.txt is missing."
fi

if [[ ! -f "$SCRIPT_DIR/packaging/preppi-splash.sh" ||
      ! -f "$SCRIPT_DIR/packaging/logo.ansi" ]]; then
    fail "Installer splash files are missing."
fi

for unit in preppi.service email-receiver.service email-receiver.timer expiry-alerts.service expiry-alerts.timer monthly-report.service monthly-report.timer; do
    if [[ ! -f "$SCRIPT_DIR/packaging/systemd/$unit" ]]; then
        fail "Required systemd file is missing: $unit"
    fi
done

if ! command -v apt-get >/dev/null 2>&1; then
    fail "This installer currently supports Debian-based Raspberry Pi OS."
fi

if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)'; then
    fail "Python 3.11 or newer is required."
fi

# Refuse to adopt or overwrite any pre-existing PrepPi resources.
for path in "$APP_DIR" "$DATA_DIR" "$CONFIG_DIR" "$BACKUP_DIR"; do
    if [[ -e "$path" ]]; then
        fail "$path already exists. This installer will not adopt or overwrite it."
    fi
done

if id "$SERVICE_USER" >/dev/null 2>&1; then
    fail "The $SERVICE_USER account already exists. Refusing to use an existing account."
fi

for unit in \
    preppi.service \
    preppi-email-receiver.service \
    preppi-email-receiver.timer \
    preppi-expiry-alerts.service \
    preppi-expiry-alerts.timer \
    preppi-monthly-report.service \
    preppi-monthly-report.timer
do
    if [[ -e "$SYSTEMD_DIR/$unit" ]]; then
        fail "Systemd unit $unit already exists. Refusing to overwrite it."
    fi
done

# Display the installer welcome screen after all preflight checks.
bash "$SCRIPT_DIR/packaging/preppi-splash.sh"

echo
echo "Choose how to initialise PrepPi:"
echo
echo "  1) Fresh installation (empty inventory)"
echo "  2) Import an existing PrepPi database"
echo
read -rp "Choose 1 or 2: " DB_CHOICE

IMPORT_DB=""

case "$DB_CHOICE" in
    1)
        log "Fresh installation selected."
        ;;
    2)
        echo
        read -er -p "Full path to existing stock.db: " IMPORT_DB
        [[ -f "$IMPORT_DB" ]] || fail "Database file not found: $IMPORT_DB"
        IMPORT_DB="$(realpath "$IMPORT_DB")"
        [[ "$IMPORT_DB" != "$DATA_DIR/stock.db" ]] || fail "Source and destination database paths are identical."
        log "Existing database selected: $IMPORT_DB"
        ;;
    *)
        fail "Invalid selection."
        ;;
esac

log "Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip rsync

log "Creating service account"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    SERVICE_ACCOUNT_CREATED=1
    useradd --system --home-dir "$DATA_DIR" --create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi

log "Creating application, data and configuration directories"
APP_DIR_CREATED=1
install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONFIG_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR/email"
install -d -o root -g root -m 0750 "$BACKUP_DIR"

log "Installing application files"
rsync -a --exclude=".git/" --exclude=".venv/" --exclude="__pycache__/" --exclude="*.pyc" --exclude="*.pre-auth-backup" --exclude="*.pre-csrf-backup" "$SCRIPT_DIR/" "$APP_DIR/"
rm -rf "$APP_DIR/tests" "$APP_DIR/packaging"
chown -R root:root "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 0755 {} \;
find "$APP_DIR" -type f -exec chmod 0644 {} \;

log "Creating Python virtual environment"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ "$DB_CHOICE" == "2" ]]; then
    log "Backing up and importing existing database"

    SOURCE_BACKUP="$BACKUP_DIR/import-source-$TIMESTAMP.db"
    STAGING_DB="$DATA_DIR/stock.db.import-$TIMESTAMP"

    python3 - "$IMPORT_DB" "$SOURCE_BACKUP" "$STAGING_DB" <<'PY'
import sqlite3
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
backup_path = Path(sys.argv[2])
staging_path = Path(sys.argv[3])

source = sqlite3.connect(
    f"{source_path.resolve().as_uri()}?mode=ro", uri=True
)
backup = sqlite3.connect(backup_path)
destination = sqlite3.connect(staging_path)

try:
    tables = {
        row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required = {"items", "preparedness_targets"}
    missing = required - tables

    if missing:
        raise SystemExit(
            "Not a recognised PrepPi database. Missing tables: "
            + ", ".join(sorted(missing))
        )

    source.backup(backup)
    source.backup(destination)

    for db in (backup, destination):
        result = db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise SystemExit(f"Database integrity check failed: {result}")

finally:
    destination.close()
    backup.close()
    source.close()

print("Database backup and import copy verified.")
PY

    chmod 0600 "$SOURCE_BACKUP"
    chown "$SERVICE_USER:$SERVICE_USER" "$STAGING_DB"
    chmod 0600 "$STAGING_DB"
    mv -- "$STAGING_DB" "$DATA_DIR/stock.db"
    unset STAGING_DB
fi

log "Installing systemd service definitions"
INSTALLED_UNITS+=("preppi.service")
install -m 0644 "$SCRIPT_DIR/packaging/systemd/preppi.service" \
    "$SYSTEMD_DIR/preppi.service"

for unit in email-receiver expiry-alerts monthly-report; do
    INSTALLED_UNITS+=("preppi-$unit.service")
    install -m 0644 "$SCRIPT_DIR/packaging/systemd/$unit.service" \
        "$SYSTEMD_DIR/preppi-$unit.service"

    INSTALLED_UNITS+=("preppi-$unit.timer")
    install -m 0644 "$SCRIPT_DIR/packaging/systemd/$unit.timer" \
        "$SYSTEMD_DIR/preppi-$unit.timer"
done

# Mark this before reloading so rollback retries even if reload fails.
SYSTEMD_RELOADED=1
systemctl daemon-reload

# Set rollback flags before commands that may partially succeed.
SERVICE_ENABLED=1
systemctl enable preppi.service

SERVICE_STARTED=1
systemctl start preppi.service
systemctl is-active --quiet preppi.service

log "Installation complete"
echo
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):5000"
echo
echo "Optional email timers are installed but DISABLED."
echo "Enable them only after configuring email settings in PrepPi."
echo
echo "Database: $DATA_DIR/stock.db"
echo "Configuration: $CONFIG_DIR"
echo "Application: $APP_DIR"
echo
echo "Check status with: sudo systemctl status preppi"
