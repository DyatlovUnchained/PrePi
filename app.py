from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_file
)
from flask_wtf.csrf import CSRFProtect
from auth import (
    load_secret_key,
    password_is_configured,
    set_household_password,
    verify_household_password,
)
import sqlite3
import json
import re
from pathlib import Path
from datetime import date
import subprocess
import sys
from shared.endurance import calculate_endurance
from shared.recommendations import build_recommendations
from shared.food_endurance import calculate_food_endurance, refresh_product_data
from paths import DB_PATH, DATA_DIR
from db_init import initialise_database
from settings import get_settings
from email_config import read_email_config, save_email_config
from imports.template_builder import build_import_template

BASE_DIR = Path(__file__).resolve().parent
RECEIVER_CONTROL = Path("/usr/local/sbin/preppi-receiver-control")

app = Flask(__name__)

# Security defaults. The persistent secret key is loaded at application startup.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,  # PrepPi initially runs over a local HTTP connection.
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    WTF_CSRF_TIME_LIMIT=60 * 60 * 12,
)

csrf = CSRFProtect(app)


class ClosingConnection(sqlite3.Connection):
    """SQLite connection that closes automatically when its context exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_db():
    db = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    return db


def require_authentication():
    """Redirect visitors until a household password has been configured and used."""
    if app.config.get("PREPPI_AUTH_DISABLED", False) and app.testing:
        return None

    if request.endpoint == "static":
        return None

    if not password_is_configured():
        if request.endpoint != "create_password":
            return redirect(url_for("create_password"))
        return None

    if request.endpoint in ("create_password", "login"):
        return None

    if not session.get("authenticated"):
        return redirect(url_for("login"))

    return None


app.before_request(require_authentication)


@app.route("/create-password", methods=["GET", "POST"])
def create_password():
    if password_is_configured():
        return redirect(url_for("login"))

    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")

        if password != confirmation:
            error = "The passwords do not match."
        else:
            try:
                set_household_password(password)
                return redirect(url_for("login"))
            except ValueError as exc:
                error = str(exc)

    return render_template("create_password.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not password_is_configured():
        return redirect(url_for("create_password"))

    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if verify_household_password(password):
            session.clear()
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("dashboard"))

        error = "Incorrect password. Please try again."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def setup_is_complete():
    """Return whether first-run setup has been completed."""
    with get_db() as db:
        row = db.execute(
            "SELECT setting_value FROM app_settings "
            "WHERE setting_key = 'setup_complete'"
        ).fetchone()
    return row is not None and row["setting_value"] == "1"


@app.route("/welcome")
def welcome():
    if setup_is_complete():
        return redirect(url_for("dashboard"))
    return render_template("splash.html")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if setup_is_complete():
        return redirect(url_for("dashboard"))

    error = ""
    with get_db() as db:
        settings = get_settings(db)

        if request.method == "POST":
            try:
                adults = int(request.form.get("adults", ""))
                children = int(request.form.get("children", ""))
                household_size = adults + children
                daily_kcal = int(request.form.get("daily_kcal_per_person", ""))
                daily_water = float(
                    request.form.get("water_litres_per_person_per_day", "")
                )
                warning_days = int(request.form.get("expiry_warning_days", ""))
                urgent_days = int(request.form.get("expiry_urgent_days", ""))

                if adults < 0 or children < 0 or not 1 <= household_size <= 30:
                    raise ValueError(
                        "Household must contain between 1 and 30 people. "
                        "Adult and child counts cannot be negative."
                    )
                if not 500 <= daily_kcal <= 6000:
                    raise ValueError("Daily calories per person must be between 500 and 6000.")
                if not 0.5 <= daily_water <= 20:
                    raise ValueError("Daily water per person must be between 0.5 and 20 litres.")
                if not 1 <= urgent_days <= 3650:
                    raise ValueError("Urgent expiry threshold must be between 1 and 3650 days.")
                if not urgent_days <= warning_days <= 3650:
                    raise ValueError(
                        "Warning threshold must be at least the urgent threshold "
                        "and no more than 3650 days."
                    )

                # Email reports are optional during first-run setup.
                if request.form.get("email_enabled") == "on":
                    save_email_config(
                        request.form.get("gmail_address", ""),
                        request.form.get("gmail_app_password", ""),
                        request.form.get("report_recipients", ""),
                        allowed_senders=request.form.get("allowed_senders", ""),
                    )

                updates = {
                    "adults": adults,
                    "children": children,
                    "daily_kcal_per_person": daily_kcal,
                    "water_litres_per_person_per_day": daily_water,
                    "expiry_warning_days": warning_days,
                    "expiry_urgent_days": urgent_days,
                }
                db.executemany(
                    "INSERT INTO app_settings (setting_key, setting_value) "
                    "VALUES (?, ?) ON CONFLICT(setting_key) "
                    "DO UPDATE SET setting_value = excluded.setting_value",
                    [(key, str(value)) for key, value in updates.items()],
                )
                db.execute(
                    "UPDATE preparedness_targets SET target_value = ?, unit = 'litres' "
                    "WHERE target_key = 'water'",
                    (household_size * daily_water * 14,),
                )
                db.execute(
                    "INSERT INTO app_settings (setting_key, setting_value) "
                    "VALUES ('setup_complete', '1') "
                    "ON CONFLICT(setting_key) DO UPDATE SET setting_value = '1'"
                )
                return redirect(url_for("dashboard"))

            except (TypeError, ValueError) as exc:
                error = str(exc) or "Please enter valid settings."
                settings = get_settings(db)

    return render_template("setup.html", settings=settings, error=error)


@app.route("/")
def dashboard():
    if not setup_is_complete():
        return redirect(url_for("welcome"))
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sort = request.args.get("sort", "expiry_date")
    direction = request.args.get("direction", "asc")

    allowed_sorts = {
        "item", "category", "subcategory", "expiry_date", "quantity"
    }
    if sort not in allowed_sorts:
        sort = "expiry_date"
    if direction not in {"asc", "desc"}:
        direction = "asc"

    sql = "SELECT * FROM items WHERE 1=1"
    params = []

    if search:
        sql += " AND (item LIKE ? OR subcategory LIKE ? OR category LIKE ?)"
        params.extend([f"%{search}%"] * 3)

    if category:
        sql += " AND category = ?"
        params.append(category)

    sql += f" ORDER BY {sort} {direction}, item ASC"

    with get_db() as db:
        items = db.execute(sql, params).fetchall()
        categories = [
            row["category"] for row in db.execute(
                "SELECT DISTINCT category FROM items ORDER BY category"
            ).fetchall()
        ]
        total = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        types = db.execute(
            "SELECT COUNT(DISTINCT item) FROM items"
        ).fetchone()[0]

        all_stock = db.execute(
            """SELECT item, category, subcategory, quantity,
                      expiry_date, unit_size_grams FROM items"""
        ).fetchall()
        pets = db.execute(
            "SELECT id, name, species, daily_food_grams FROM pets ORDER BY species, name"
        ).fetchall()
        equipment = db.execute(
            "SELECT item, category, quantity FROM equipment"
        ).fetchall()
        targets = {
            row["target_key"]: dict(row)
            for row in db.execute(
                "SELECT target_key, target_name, target_value, unit FROM preparedness_targets"
            ).fetchall()
        }
        outstanding = db.execute(
            "SELECT item, category FROM shopping_list WHERE acquired = 0"
        ).fetchall()
        settings = get_settings(db)

    # Derive the two-week water target from the configured household allowance.
    if "water" in targets:
        targets["water"]["target_value"] = (
            settings["daily_water_target_litres"] * 14
        )
    endurance = calculate_endurance(all_stock, targets)
    food_endurance = calculate_food_endurance(
        all_stock,
        daily_target_kcal=settings["daily_kcal_target"],
    )
    water_days = endurance.get("water_days")
    food_days = food_endurance.get("food_days")
    endurance["food_days"] = food_days
    endurance["overall_days"] = (
        min(water_days, food_days)
        if water_days is not None and food_days is not None
        else None
    )


    today = date.today()
    stock = []
    for row in items:
        item = dict(row)
        expiry_text = (item["expiry_date"] or "").strip()
        if expiry_text:
            expiry = date.fromisoformat(expiry_text)
            days_left = (expiry - today).days
            item["days_left"] = days_left
            item["expiry_display"] = expiry.strftime("%d/%m/%Y")
            if days_left < 0:
                item["status"] = "Expired"
            elif days_left <= settings["expiry_urgent_days"]:
                item["status"] = "Urgent"
            elif days_left <= settings["expiry_warning_days"]:
                item["status"] = "Expiring soon"
            else:
                item["status"] = "In date"
        else:
            item["days_left"] = 99999
            item["expiry_display"] = "Not entered"
            item["status"] = "Date unknown"
        stock.append(item)


    recommendations = build_recommendations(
        all_stock, equipment, targets, outstanding, pets=pets
    )

    return render_template(
        "dashboard.html",
        items=stock,
        categories=categories,
        selected_category=category,
        search=search,
        sort=sort,
        direction=direction,
        total=total,
        types=types,
        today=today.strftime("%d/%m/%Y"),
        recommendations=recommendations,
        pets=pets,
        endurance=endurance,
        food_endurance=food_endurance,
        settings=settings,
        nutrition_status=request.args.get("nutrition_status", ""),
        report_status=request.args.get("report_status", ""),
    )


@app.route("/shopping/<int:item_id>/toggle", methods=["POST"])
def toggle_shopping_item(item_id):
    with get_db() as db:
        row = db.execute(
            "SELECT acquired FROM shopping_list WHERE id = ?",
            (item_id,)
        ).fetchone()
        if row is not None:
            new_value = 0 if row["acquired"] else 1
            db.execute(
                "UPDATE shopping_list SET acquired = ? WHERE id = ?",
                (new_value, item_id)
            )
    return redirect(url_for("dashboard"))




@app.route("/nutrition/refresh", methods=["POST"])
def refresh_nutrition():
    with get_db() as db:
        all_stock = db.execute(
            "SELECT item, category, subcategory, quantity, expiry_date FROM items"
        ).fetchall()

    try:
        refreshed = refresh_product_data(all_stock)
        status = "refreshed" if refreshed else "refresh_failed"
    except Exception as exc:
        print(f"Nutrition refresh failed: {exc}")
        status = "refresh_failed"

    return redirect(url_for("dashboard", nutrition_status=status))


@app.route("/import/template", methods=["GET"])
def download_import_template():
    workbook = build_import_template()
    return send_file(
        workbook,
        as_attachment=True,
        download_name="PrepPi_Stock_Import_Template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/reports/monthly", methods=["POST"])
def send_monthly_report():
    script = BASE_DIR / "email" / "monthly_report_sender.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--send"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode == 0:
            status = "sent"
        else:
            print("Monthly report sender failed:", result.stderr or result.stdout)
            status = "failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Monthly report sender could not run: {exc}")
        status = "failed"

    return redirect(url_for("dashboard", report_status=status))


@app.route("/reports/stock", methods=["POST"])
def send_stock_report():
    script = BASE_DIR / "email" / "stock_report_sender.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--send"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode == 0:
            status = "stock_sent"
        else:
            print("On-demand stock report failed:", result.stderr or result.stdout)
            status = "stock_failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"On-demand stock report could not run: {exc}")
        status = "stock_failed"

    return redirect(url_for("dashboard", report_status=status))



def get_automation_status():
    """Read the status of all PrepPi automation timers."""
    empty_job = {
        "enabled": False,
        "active": False,
        "last_run": "",
        "next_run": "",
        "result": "",
        "exit_status": "",
    }
    try:
        result = subprocess.run(
            ["sudo", str(RECEIVER_CONTROL), "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Automation status helper failed.")

        status = json.loads(result.stdout)
        jobs = status.get("jobs") if isinstance(status, dict) else None
        if not status.get("available") or not isinstance(jobs, dict):
            raise RuntimeError("Automation status is unavailable.")

        receiver = jobs.get("receiver")
        expiry = jobs.get("expiry")
        monthly = jobs.get("monthly")
        if not all(isinstance(job, dict) for job in (receiver, expiry, monthly)):
            raise RuntimeError("Automation status is incomplete.")

        return {
            "available": True,
            "jobs": jobs,
            "receiver": receiver,
            "expiry": expiry,
            "monthly": monthly,
        }

    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, AttributeError):
        return {
            "available": False,
            "jobs": {
                "receiver": dict(empty_job),
                "expiry": dict(empty_job),
                "monthly": dict(empty_job),
            },
            "receiver": {
                **empty_job,
                "last_check": "",
                "next_check": "",
                "last_result": "",
                "last_processed": "",
            },
            "expiry": dict(empty_job),
            "monthly": dict(empty_job),
        }


def get_receiver_status():
    """Backward-compatible receiver status accessor."""
    status = get_automation_status()
    return {"available": status["available"], **status["receiver"]}


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    error = ""
    saved = request.args.get("saved", "") == "1"

    with get_db() as db:
        settings = get_settings(db)

        if request.method == "POST":
            try:
                adults = int(request.form.get("adults", ""))
                children = int(request.form.get("children", ""))
                household_size = adults + children
                daily_kcal = int(request.form.get("daily_kcal_per_person", ""))
                daily_water = float(request.form.get("water_litres_per_person_per_day", ""))
                warning_days = int(request.form.get("expiry_warning_days", ""))
                urgent_days = int(request.form.get("expiry_urgent_days", ""))

                if adults < 0 or children < 0 or not 1 <= household_size <= 30:
                    raise ValueError(
                        "Household must contain between 1 and 30 people. "
                        "Adult and child counts cannot be negative."
                    )
                if not 500 <= daily_kcal <= 6000:
                    raise ValueError("Daily calories per person must be between 500 and 6000.")
                if not 0.5 <= daily_water <= 20:
                    raise ValueError("Daily water per person must be between 0.5 and 20 litres.")
                if not 1 <= urgent_days <= 3650:
                    raise ValueError("Urgent expiry threshold must be between 1 and 3650 days.")
                if not urgent_days <= warning_days <= 3650:
                    raise ValueError("Warning threshold must be at least the urgent threshold and no more than 3650 days.")

                updates = {
                    "adults": adults,
                    "children": children,
                    "daily_kcal_per_person": daily_kcal,
                    "water_litres_per_person_per_day": daily_water,
                    "expiry_warning_days": warning_days,
                    "expiry_urgent_days": urgent_days,
                }
                db.executemany("""
                    INSERT INTO app_settings (setting_key, setting_value)
                    VALUES (?, ?)
                    ON CONFLICT(setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value
                """, [(key, str(value)) for key, value in updates.items()])

                # Keep the existing two-week water target in sync with settings.
                db.execute("""
                    UPDATE preparedness_targets
                    SET target_value = ?, unit = 'litres'
                    WHERE target_key = 'water'
                """, (household_size * daily_water * 14,))

                settings = get_settings(db)
                saved = True

            except (TypeError, ValueError) as exc:
                error = str(exc) or "Please enter valid values."
                settings = {
                    **settings,
                    "adults": request.form.get("adults", settings["adults"]),
                    "children": request.form.get("children", settings["children"]),
                    "household_size": household_size,
                    "daily_kcal_per_person": request.form.get("daily_kcal_per_person", settings["daily_kcal_per_person"]),
                    "water_litres_per_person_per_day": request.form.get("water_litres_per_person_per_day", settings["water_litres_per_person_per_day"]),
                    "expiry_warning_days": request.form.get("expiry_warning_days", settings["expiry_warning_days"]),
                    "expiry_urgent_days": request.form.get("expiry_urgent_days", settings["expiry_urgent_days"]),
                }

    email_status = request.args.get("email_status", "")
    email_error = request.args.get("email_error", "")
    email_config = read_email_config()
    automation = get_automation_status()
    receiver = {
        "available": automation["available"],
        **automation["receiver"],
    }
    expiry = automation["expiry"]
    monthly = automation["monthly"]
    automation_available = automation["available"]

    return render_template(
        "settings.html",
        settings=settings,
        error=error,
        saved=saved,
        email_config=email_config,
        email_status=email_status,
        email_error=email_error,
        receiver=receiver,
        expiry=expiry,
        monthly=monthly,
        automation_available=automation_available,
    )


@app.route("/settings/email", methods=["POST"])
def save_email_settings():
    try:
        enabled = request.form.get("email_enabled") == "on"

        if not enabled:
            # Disable email by removing the saved configuration.
            from email_config import EMAIL_CONFIG
            if EMAIL_CONFIG.exists():
                EMAIL_CONFIG.unlink()
            return redirect(url_for("settings_page", email_status="disabled"))

        save_email_config(
            request.form.get("gmail_address", ""),
            request.form.get("gmail_app_password", ""),
            request.form.get("report_recipients", ""),
            keep_existing_password=(
                request.form.get("keep_existing_password") == "on"
            ),
            allowed_senders=request.form.get("allowed_senders", ""),
        )
        return redirect(url_for("settings_page", email_status="saved"))

    except (ValueError, OSError) as exc:
        return redirect(url_for("settings_page", email_error=str(exc)))


@app.route("/settings/receiver", methods=["POST"])
def control_email_receiver():
    """Enable or disable only the inbox receiver timer."""
    action = request.form.get("action", "")
    if action not in ("enable", "disable"):
        return redirect(url_for("settings_page", receiver_status="error"))

    try:
        result = subprocess.run(
            ["sudo", str(RECEIVER_CONTROL), action, "receiver"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Receiver control command failed.")

        status = json.loads(result.stdout)
        if not isinstance(status, dict) or not status.get("available"):
            raise RuntimeError("Receiver status unavailable.")

        return redirect(url_for("settings_page", receiver_status=action))

    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
        return redirect(url_for("settings_page", receiver_status="error"))


@app.route("/settings/automation", methods=["POST"])
def control_automation():
    """Control a fixed PrepPi automation job from Settings."""
    job = request.form.get("job", "")
    action = request.form.get("action", "")

    if job not in {"receiver", "expiry", "monthly"}:
        flash("Unknown automation task.", "error")
        return redirect(url_for("settings_page"))

    if action not in {"enable", "disable", "preview", "run"}:
        flash("Unknown automation action.", "error")
        return redirect(url_for("settings_page"))

    if action in {"preview", "run"} and job == "receiver":
        flash("Preview and manual sending are not available for the inbox receiver.", "error")
        return redirect(url_for("settings_page"))

    if action in {"enable", "run"} and job in {"expiry", "monthly"}:
        if not read_email_config().get("enabled"):
            flash("Configure and save email settings before enabling or sending scheduled reports.", "error")
            return redirect(url_for("settings_page"))

    try:
        result = subprocess.run(
            ["sudo", str(RECEIVER_CONTROL), action, job],
            capture_output=True,
            text=True,
            timeout=190,
            check=False,
        )

        try:
            response = json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError:
            response = {}

        if result.returncode != 0 or response.get("available") is False:
            detail = response.get("error") or result.stderr.strip()
            flash(
                f"Could not {action} the {job} task."
                + (f" {detail}" if detail else ""),
                "error",
            )
        elif action == "preview":
            output = response.get("output", "").strip()
            flash(
                "Preview completed. No email was sent."
                + (f"\\n{output[-1200:]}" if output else ""),
                "success",
            )
        elif action == "run":
            flash(f"The {job} task completed. Check its report output and email inbox.", "success")
        else:
            flash(
                f"{job.title()} task {action}d successfully.",
                "success",
            )

    except (OSError, subprocess.SubprocessError) as exc:
        flash(f"Automation control failed: {exc}", "error")

    return redirect(url_for("settings_page"))


def stock_form_values():
    """Validate common stock fields submitted by the dashboard forms."""
    item = request.form.get("item", "").strip()
    category = request.form.get("category", "").strip()
    subcategory = request.form.get("subcategory", "").strip()
    expiry_text = request.form.get("expiry_date", "").strip()
    quantity_text = request.form.get("quantity", "").strip()

    if not item or not category or not subcategory:
        raise ValueError("Item, category and sub-category are required.")

    try:
        expiry_date = date.fromisoformat(expiry_text)
    except ValueError:
        raise ValueError("Enter a valid expiry date.")

    try:
        quantity = int(quantity_text)
    except ValueError:
        raise ValueError("Quantity must be a whole number.")

    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    return item, category, subcategory, expiry_date.isoformat(), quantity


@app.route("/stock/add", methods=["GET", "POST"])
def add_stock_item():
    error = ""
    values = {}

    if request.method == "POST":
        values = request.form.to_dict()
        try:
            item, category, subcategory, expiry_date, quantity = stock_form_values()
            if quantity < 1:
                raise ValueError("New stock must have a quantity of at least 1.")

            with get_db() as db:
                matches = db.execute("""
                    SELECT id, quantity FROM items
                    WHERE item = ? AND category = ? AND subcategory = ?
                      AND COALESCE(expiry_date, '') = ?
                """, (item, category, subcategory, expiry_date)).fetchall()

                if len(matches) > 1:
                    raise ValueError(
                        "Several matching batches already exist. Edit those records "
                        "before adding this batch."
                    )

                if matches:
                    db.execute(
                        "UPDATE items SET quantity = quantity + ? WHERE id = ?",
                        (quantity, matches[0]["id"]),
                    )
                else:
                    db.execute("""
                        INSERT INTO items
                            (item, category, subcategory, expiry_date,
                             logged, quantity)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        item, category, subcategory, expiry_date,
                        date.today().isoformat(), quantity,
                    ))

            return redirect(url_for("dashboard", stock_status="added"))

        except ValueError as exc:
            error = str(exc)

    return render_template(
        "stock_form.html",
        mode="add",
        values=values,
        error=error,
    )


@app.route("/stock/<int:item_id>/edit", methods=["GET", "POST"])
def edit_stock_item(item_id):
    error = ""
    with get_db() as db:
        existing = db.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()

    if existing is None:
        return redirect(url_for("dashboard", stock_status="missing"))

    values = dict(existing)

    if request.method == "POST":
        values = request.form.to_dict()
        try:
            item, category, subcategory, expiry_date, quantity = stock_form_values()

            with get_db() as db:
                collision = db.execute("""
                    SELECT id FROM items
                    WHERE item = ? AND category = ? AND subcategory = ?
                      AND COALESCE(expiry_date, '') = ? AND id != ?
                """, (item, category, subcategory, expiry_date, item_id)).fetchone()

                if collision:
                    raise ValueError(
                        "A batch with those same details already exists. "
                        "Edit that batch instead to avoid duplicate records."
                    )

                db.execute("""
                    UPDATE items
                    SET item = ?, category = ?, subcategory = ?,
                        expiry_date = ?, quantity = ?
                    WHERE id = ?
                """, (item, category, subcategory, expiry_date, quantity, item_id))

            return redirect(url_for("dashboard", stock_status="updated"))

        except ValueError as exc:
            error = str(exc)

    return render_template(
        "stock_form.html",
        mode="edit",
        values=values,
        error=error,
    )


@app.route("/stock/<int:item_id>/delete", methods=["POST"])
def delete_stock_item(item_id):
    with get_db() as db:
        db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return redirect(url_for("dashboard", stock_status="deleted"))


EQUIPMENT_CATEGORIES = (
    "Cooking", "Lighting", "Heating", "Power",
    "Tools", "Communications", "Camping", "General",
)


def equipment_form_values():
    """Validate fields submitted through the equipment forms."""
    item = request.form.get("item", "").strip()
    submitted_category = request.form.get("category", "").strip() or "General"
    category_lookup = {value.casefold(): value for value in EQUIPMENT_CATEGORIES}
    category = category_lookup.get(submitted_category.casefold(), "")
    if not category:
        raise ValueError("Please choose a valid equipment category.")
    quantity_text = request.form.get("quantity", "").strip()
    notes = request.form.get("notes", "").strip()

    if not item:
        raise ValueError("An equipment item name is required.")
    if len(item) > 200:
        raise ValueError("Item names must be 200 characters or fewer.")
    if len(category) > 100:
        raise ValueError("Categories must be 100 characters or fewer.")
    if len(notes) > 1000:
        raise ValueError("Notes must be 1000 characters or fewer.")

    if quantity_text:
        try:
            quantity = int(quantity_text)
        except ValueError:
            raise ValueError("Quantity must be a whole number.")
        if quantity < 0:
            raise ValueError("Quantity cannot be negative.")
    else:
        quantity = None

    return item, category, quantity, notes


@app.route("/equipment/add", methods=["GET", "POST"])
def add_equipment():
    error = ""
    values = {"category": "General"}

    if request.method == "POST":
        values = request.form.to_dict()
        try:
            item, category, quantity, notes = equipment_form_values()

            with get_db() as db:
                db.execute("""
                    INSERT INTO equipment (item, category, quantity, notes)
                    VALUES (?, ?, ?, ?)
                """, (item, category, quantity, notes))

            return redirect(url_for("equipment_page", status="added"))

        except ValueError as exc:
            error = str(exc)

    return render_template(
        "equipment_form.html",
        mode="add",
        values=values,
        error=error,
        equipment_categories=EQUIPMENT_CATEGORIES,
    )


@app.route("/equipment/<int:item_id>/edit", methods=["GET", "POST"])
def edit_equipment(item_id):
    with get_db() as db:
        equipment = db.execute(
            "SELECT id, item, category, quantity, notes "
            "FROM equipment WHERE id = ?",
            (item_id,),
        ).fetchone()

    if equipment is None:
        return redirect(url_for("equipment_page", status="not_found"))

    values = dict(equipment)
    error = ""

    if request.method == "POST":
        values = request.form.to_dict()
        try:
            item, category, quantity, notes = equipment_form_values()

            with get_db() as db:
                db.execute("""
                    UPDATE equipment
                    SET item = ?, category = ?, quantity = ?, notes = ?
                    WHERE id = ?
                """, (item, category, quantity, notes, item_id))

            return redirect(url_for("equipment_page", status="updated"))

        except ValueError as exc:
            error = str(exc)

    return render_template(
        "equipment_form.html",
        mode="edit",
        values=values,
        error=error,
        equipment_categories=EQUIPMENT_CATEGORIES,
    )


@app.route("/equipment/<int:item_id>/delete", methods=["POST"])
def delete_equipment(item_id):
    with get_db() as db:
        db.execute(
            "DELETE FROM equipment WHERE id = ?",
            (item_id,),
        )

    return redirect(url_for("equipment_page", status="deleted"))


@app.route("/equipment")
def equipment_page():
    with get_db() as db:
        equipment = db.execute("""
            SELECT id, item, category, quantity, notes
            FROM equipment
            ORDER BY category, item
        """).fetchall()

    return render_template(
        "equipment.html",
        equipment=equipment,
    )


def pet_form_values():
    """Validate a pet profile submitted through the pet forms."""
    name = request.form.get("name", "").strip()
    species = request.form.get("species", "").strip().lower()
    food_text = request.form.get("daily_food_grams", "").strip()

    if not name:
        raise ValueError("A pet name is required.")
    if len(name) > 100:
        raise ValueError("Pet names must be 100 characters or fewer.")
    if species not in ("cat", "dog"):
        raise ValueError("Species must be Cat or Dog.")

    try:
        daily_food_grams = float(food_text)
    except (TypeError, ValueError):
        raise ValueError("Daily food allowance must be a number.")

    if not 0 < daily_food_grams < float("inf"):
        raise ValueError("Daily food allowance must be a positive, finite number.")

    return name, species, daily_food_grams


@app.route("/pets")
def pets_page():
    with get_db() as db:
        pets = db.execute("""
            SELECT id, name, species, daily_food_grams, created_at
            FROM pets
            ORDER BY species, name
        """).fetchall()

    return render_template("pets.html", pets=pets)


@app.route("/pets/add", methods=["GET", "POST"])
def add_pet():
    error = ""
    values = {"species": "cat"}

    if request.method == "POST":
        values = request.form.to_dict()
        try:
            name, species, daily_food_grams = pet_form_values()

            with get_db() as db:
                db.execute("""
                    INSERT INTO pets (name, species, daily_food_grams)
                    VALUES (?, ?, ?)
                """, (name, species, daily_food_grams))

            return redirect(url_for("pets_page", status="added"))

        except ValueError as exc:
            error = str(exc)

    return render_template(
        "pet_form.html", mode="add", values=values, error=error
    )


@app.route("/pets/<int:pet_id>/edit", methods=["GET", "POST"])
def edit_pet(pet_id):
    with get_db() as db:
        pet = db.execute("""
            SELECT id, name, species, daily_food_grams
            FROM pets WHERE id = ?
        """, (pet_id,)).fetchone()

    if pet is None:
        return redirect(url_for("pets_page", status="not_found"))

    values = dict(pet)
    error = ""

    if request.method == "POST":
        values = request.form.to_dict()
        try:
            name, species, daily_food_grams = pet_form_values()

            with get_db() as db:
                db.execute("""
                    UPDATE pets
                    SET name = ?, species = ?, daily_food_grams = ?
                    WHERE id = ?
                """, (name, species, daily_food_grams, pet_id))

            return redirect(url_for("pets_page", status="updated"))

        except ValueError as exc:
            error = str(exc)

    return render_template(
        "pet_form.html", mode="edit", values=values, error=error
    )


@app.route("/pets/<int:pet_id>/delete", methods=["POST"])
def delete_pet(pet_id):
    with get_db() as db:
        db.execute("DELETE FROM pets WHERE id = ?", (pet_id,))

    return redirect(url_for("pets_page", status="deleted"))


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.config["SECRET_KEY"] = load_secret_key()
    initialise_database(DB_PATH)
    app.run(host="0.0.0.0", port=5000, debug=False)
