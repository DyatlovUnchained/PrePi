#!/usr/bin/env python3
"""Build branded stock-import reports without modifying the inventory."""
import argparse
import html
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared.endurance import calculate_endurance
from shared.food_endurance import calculate_food_endurance
from settings import read_daily_kcal_target
from shared.recommendations import build_recommendations

BASE_DIR = Path(__file__).resolve().parent.parent
LOGO_CID = "stock-report-logo"


def esc(value):
    return html.escape(str(value if value is not None else ""))


def build_report(db_path, outcome, action, changes=None, message_id=None,
                 error=None, workbook_name=None, connection=None):
    """Return report HTML and context, optionally reading through a supplied connection."""
    changes = changes or []
    if outcome not in {"success", "failure"}:
        raise ValueError("outcome must be 'success' or 'failure'")
    success = outcome == "success"
    if not success:
        changes = []

    owns_connection = connection is None
    db = connection
    if owns_connection:
        db = sqlite3.connect(
            f"file:{Path(db_path).resolve()}?mode=ro", uri=True
        )
    db.row_factory = sqlite3.Row
    try:
        stock = db.execute(
            "SELECT item, category, subcategory, quantity, expiry_date, "
            "unit_size_grams FROM items"
        ).fetchall()
        equipment = db.execute("SELECT item, quantity FROM equipment").fetchall()
        targets = {
            row["target_key"]: dict(row)
            for row in db.execute(
                "SELECT target_key, target_name, target_value, unit "
                "FROM preparedness_targets"
            ).fetchall()
        }
        outstanding = db.execute(
            "SELECT item, category FROM shopping_list WHERE acquired = 0"
        ).fetchall()
        daily_target_kcal = read_daily_kcal_target(db)

        today = date.today()
        endurance = calculate_endurance(stock, targets, today=today)
        food = calculate_food_endurance(
            stock, daily_target_kcal=daily_target_kcal,
            today=today, allow_lookup=False,
            connection=db, cache_writes=not owns_connection,
        )
        recommendations = build_recommendations(
            stock, equipment, targets, outstanding
        )
    finally:
        if owns_connection:
            db.close()

    status = "IMPORT SUCCESSFUL" if success else "IMPORT FAILED"
    status_colour = "#247447" if success else "#b42318"
    timestamp = datetime.now().astimezone().strftime("%d %b %Y, %H:%M %Z")

    if success and changes:
        change_rows = []
        for change in changes:
            item = esc(change.get("item", "Unknown item"))
            operation = esc(change.get("operation", "UPDATE"))
            old = change.get("old_quantity", "—")
            new = change.get("new_quantity", "—")
            category = esc(change.get("category", ""))
            change_rows.append(
                "<tr>"
                f"<td>{item}<br><small>{category}</small></td>"
                f"<td>{operation}</td><td>{esc(old)}</td><td>{esc(new)}</td>"
                "</tr>"
            )
        changes_html = (
            '<table><thead><tr><th>Item</th><th>Change</th>'
            '<th>Previous qty</th><th>New qty</th></tr></thead><tbody>'
            + "".join(change_rows) + "</tbody></table>"
        )
    elif success:
        changes_html = "<p>The import completed, but no row-level change details were supplied.</p>"
    else:
        changes_html = (
            "<p><strong>No stock changes were applied by this failed import.</strong></p>"
            + (f"<p>{esc(error)}</p>" if error else "")
        )

    water_days = endurance.get("water_days")
    water_text = (
        f"{water_days:.1f} days at the current household water target"
        if water_days is not None else "Not measurable"
    )
    water_litres = endurance.get("water_litres", 0)
    daily_target = endurance.get("water_daily_target", 0)
    food_days = food["food_days"]
    food_text = f"{food_days:.1f} days at the household calorie target"
    overall_days = (
        min(water_days, food_days)
        if water_days is not None and food_days is not None else None
    )
    overall_text = f"{overall_days:.1f} days" if overall_days is not None else "Not measurable"

    recommendation_rows = []
    for rec in recommendations:
        recommendation_rows.append(
            "<tr>"
            f"<td>{esc(rec.get('icon', ''))} {esc(rec.get('title', ''))}</td>"
            f"<td><strong>{esc(rec.get('status', ''))}</strong><br>"
            f"{esc(rec.get('description', ''))}</td>"
            "</tr>"
        )

    message_line = (
        f"<p><small>Message ID: {esc(message_id)}</small></p>"
        if message_id else ""
    )
    workbook_line = (
        f"<p><small>Workbook: {esc(workbook_name)}</small></p>"
        if workbook_name else ""
    )

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock import report</title>
<style>
body{{margin:0;background:#f2f4f7;font-family:Arial,Helvetica,sans-serif;color:#202b36}}
main{{max-width:760px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden}}
header{{padding:24px;background:#172b3a;color:#fff}}
header img{{max-width:150px;max-height:90px;object-fit:contain}}
header h1{{margin:14px 0 4px;font-size:23px}}
section{{padding:20px 24px;border-bottom:1px solid #e5e7eb}}
h2{{font-size:17px;margin:0 0 12px;color:#172b3a}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:10px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top}}
th{{background:#f7f8fa}}
.status{{font-weight:bold;color:{status_colour};font-size:17px}}
small{{color:#667085;font-size:12px;overflow-wrap:anywhere}}
.metric{{display:inline-block;background:#f7f8fa;border-radius:8px;padding:12px 16px;margin:4px 8px 4px 0}}
.metric strong{{display:block;font-size:21px;margin-bottom:4px}}
footer{{padding:18px 24px;color:#667085;font-size:12px}}
</style></head><body><main>
<header>
<img src="cid:{LOGO_CID}" alt="PrepPi">
<h1>Stock import report</h1>
<div>{esc(timestamp)}</div>
</header>
<section>
<div class="status">{status}</div>
<p>Action: <strong>{esc(action)}</strong></p>
{workbook_line}{message_line}
{changes_html}
</section>
<section>
<h2>Current stock endurance</h2>
<div class="metric"><strong>{water_litres:g} L</strong>Recorded water</div>
<div class="metric"><strong>{water_text}</strong>Estimated water endurance</div>
<p>Daily water target: {daily_target:g} L.</p>
    <p>Daily calorie target: {food["daily_target_kcal"]:,.0f} kcal.</p>
<p><strong>Food energy:</strong> {food["food_kcal"]:,} kcal recorded.</p>
<p><strong>Food endurance:</strong> {food_text}.</p>
<p>Product-data energy: {food["product_kcal"]:,} kcal; category-estimated energy: {food["estimated_kcal"]:,} kcal.</p>
<p>Unmeasured food items: {food["unmeasured_count"]}.</p>
<p><strong>Overall endurance:</strong> {overall_text}.</p>
</section>
<section>
<h2>Current recommendations</h2>
<table><thead><tr><th>Area</th><th>Status and guidance</th></tr></thead>
<tbody>{''.join(recommendation_rows)}</tbody></table>
</section>
<footer>
This report reflects the current inventory and preparedness recommendations.
Water endurance is an estimate based on recorded, in-date water and the configured target.
Food energy is estimated from recorded, in-date pack sizes and the configured household calorie target. Category estimates may differ from actual products.
</footer></main></body></html>"""

    context = {
        "outcome": outcome,
        "action": action,
        "changes": changes if success else [],
        "recommendations": recommendations,
        "endurance": endurance,
        "food_endurance": food,
    }
    return document, context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Database to read (read-only)")
    parser.add_argument("--preview", required=True, help="HTML preview output path")
    args = parser.parse_args()

    sample_changes = [{
        "item": "Example item — preview only",
        "category": "Preview",
        "operation": "UPDATE",
        "old_quantity": 1,
        "new_quantity": 2,
    }]
    document, _ = build_report(
        args.db, "success", "Stocked", changes=sample_changes,
        workbook_name="PREVIEW ONLY — no import performed",
    )
    preview_path = Path(args.preview).resolve()
    preview_path.write_text(
        document.replace(f"cid:{LOGO_CID}", "../static/logo.png"),
        encoding="utf-8",
    )
    failure_path = preview_path.with_name("report-failure-preview.html")
    failure_document, failure_context = build_report(
        args.db, "failure", "Stocked",
        changes=sample_changes,
        error="Preview test: deliberately simulated failure.",
        workbook_name="PREVIEW ONLY — no import performed",
    )
    failure_path.write_text(
        failure_document.replace(f"cid:{LOGO_CID}", "../static/logo.png"),
        encoding="utf-8",
    )
    assert "IMPORT FAILED" in failure_document
    assert "IMPORT SUCCESSFUL" not in failure_document
    assert "Example item — preview only" not in failure_document
    assert failure_context["changes"] == []
    print(f"Success preview written: {preview_path}")
    print(f"Failure preview written: {failure_path}")
    print("Failure preview passed safety assertions.")
    print("Read-only database used; no stock changes or emails were made.")


if __name__ == "__main__":
    main()
