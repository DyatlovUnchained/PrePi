#!/usr/bin/env python3
"""Generate a monthly PrepPi preparedness report and owned-inventory workbook."""
import argparse
import html
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from shared.endurance import calculate_endurance
from shared.food_endurance import calculate_food_endurance
from shared.recommendations import build_recommendations
from settings import read_daily_kcal_target

LOGO_CID = "stock-report-logo"


def esc(value):
    return html.escape(str(value if value is not None else ""))


def read_inventory(db_path):
    """Read all inventory data without permitting database writes."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        stock = [dict(r) for r in db.execute(
            "SELECT item, category, subcategory, expiry_date, logged, "
            "quantity, unit_size_grams "
            "FROM items ORDER BY category, item, expiry_date"
        )]
        equipment = [dict(r) for r in db.execute(
            "SELECT item, quantity, notes FROM equipment ORDER BY item"
        )]
        pets = [dict(r) for r in db.execute(
            "SELECT id, name, species, daily_food_grams "
            "FROM pets ORDER BY species, name"
        )]
        targets = {
            r["target_key"]: dict(r)
            for r in db.execute("SELECT * FROM preparedness_targets")
        }
        shopping = [dict(r) for r in db.execute(
            "SELECT item, category, target_quantity, unit, notes "
            "FROM shopping_list WHERE acquired = 0 ORDER BY category, item"
        )]
    return stock, equipment, targets, shopping, pets


def usable_stock(stock, today):
    """Exclude expired items from calculations; retain undated stock."""
    result = []
    for row in stock:
        expiry = row.get("expiry_date")
        if not expiry:
            continue
        try:
            if date.fromisoformat(expiry) < today:
                continue
        except (ValueError, TypeError):
            # Unknown or malformed dates are not assumed safe.
            continue
        result.append(row)
    return result


def build_workbook(stock, equipment, path, pets=None):
    """Export owned inventory, equipment and pet preparedness."""
    pets = pets or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Owned stock"
    headers = ["Item", "Category", "Sub-category", "Quantity",
               "Unit size (g)", "Expiry date", "Logged"]
    ws.append(headers)
    for r in stock:
        ws.append([r.get(k) for k in (
            "item", "category", "subcategory", "quantity",
            "unit_size_grams", "expiry_date", "logged"
        )])

    eq = wb.create_sheet("Equipment")
    eq.append(["Item", "Quantity", "Notes"])
    for r in equipment:
        eq.append([r.get("item"), r.get("quantity"), r.get("notes")])

    pet_ws = wb.create_sheet("Pet preparedness")
    pet_ws.append([
        "Pet", "Species", "Daily food allowance (g)",
        "14-day food requirement (g)"
    ])

    daily_total = 0
    for pet in pets:
        daily = float(pet["daily_food_grams"])
        daily_total += daily
        pet_ws.append([
            pet["name"], pet["species"], daily, daily * 14
        ])

    if pets:
        pet_food_grams = sum(
            (float(r.get("quantity") or 0) *
             float(r.get("unit_size_grams") or 0))
            for r in usable_stock(stock, date.today())
            if r.get("category") == "Pet Supplies"
            and r.get("subcategory") == "Pet Food"
        )
        target = daily_total * 14
        pet_ws.append([])
        pet_ws.append(["Combined daily allowance (g)", daily_total])
        pet_ws.append(["Combined 14-day requirement (g)", target])
        pet_ws.append(["In-date pet food stock (g)", pet_food_grams])
        pet_ws.append(["14-day shortfall (g)", max(0, target - pet_food_grams)])
    else:
        pet_ws.append(["No pets registered."])


    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="172B3A")
            cell.alignment = Alignment(vertical="center")
        for column in sheet.columns:
            letter = get_column_letter(column[0].column)
            width = max(
                (len(str(c.value or "")) for c in column),
                default=10
            )
            sheet.column_dimensions[letter].width = min(max(width + 2, 12), 48)

    wb.save(path)


def build_html(stock, equipment, targets, shopping, today, pets=None, daily_target_kcal=4000):
    usable = usable_stock(stock, today)
    endurance = calculate_endurance(usable, targets, today=today)
    # Keep email recommendations focused; the dashboard retains its own
    # shopping-list cross-references.
    recommendations = build_recommendations(
        usable, equipment, targets, shopping, pets=pets
    )

    pet_html = ""
    if pets:
        daily_pet_food = sum(
            float(pet["daily_food_grams"]) for pet in pets
        )
        target_pet_food = daily_pet_food * 14
        available_pet_food = sum(
            float(row.get("quantity") or 0) *
            float(row.get("unit_size_grams") or 0)
            for row in usable
            if row.get("category") == "Pet Supplies"
            and row.get("subcategory") == "Pet Food"
        )
        pet_days = (
            available_pet_food / daily_pet_food
            if daily_pet_food else 0
        )
        pet_shortfall = max(0, target_pet_food - available_pet_food)

        pet_rows = "".join(
            f"<tr><td>{esc(pet['name'])}</td>"
            f"<td>{esc(pet['species'].title())}</td>"
            f"<td>{float(pet['daily_food_grams']):g} g</td>"
            f"<td>{float(pet['daily_food_grams']) * 14:g} g</td></tr>"
            for pet in pets
        )
        pet_html = f"""
<section><h2>Pet preparedness</h2>
<table><thead><tr><th>Pet</th><th>Species</th>
<th>Daily food</th><th>14-day requirement</th></tr></thead>
<tbody>{pet_rows}</tbody></table>
<div class="metric"><strong>{daily_pet_food:g} g</strong>
Combined daily allowance</div>
<div class="metric"><strong>{target_pet_food:g} g</strong>
Combined 14-day requirement</div>
<div class="metric"><strong>{available_pet_food:g} g</strong>
In-date pet food recorded</div>
<div class="metric"><strong>{pet_days:.1f} days</strong>
Estimated pet food endurance</div>
<div class="metric"><strong>{pet_shortfall:g} g</strong>
14-day shortfall</div>
</section>"""


    water_litres = endurance.get("water_litres", 0)
    water_days = endurance.get("water_days")
    daily_target = endurance.get("water_daily_target", 6)
    water_days_text = (
        f"{water_days:.1f} days" if water_days is not None
        else "Not measurable"
    )

    food = calculate_food_endurance(
        usable, daily_target_kcal=daily_target_kcal,
        today=today, allow_lookup=False, cache_writes=False
    )
    food_days = food.get("food_days")
    food_days_text = (
        f"{food_days:.1f} days" if food_days is not None
        else "Not measurable"
    )
    overall_days = (
        min(water_days, food_days)
        if water_days is not None and food_days is not None
        else None
    )
    limiting_factor = (
        "food and water" if food_days == water_days
        else "food" if food_days < water_days
        else "water"
    ) if water_days is not None and food_days is not None else None
    overall_days_text = (
        f"{overall_days:.1f} days (limited by {limiting_factor})"
        if overall_days is not None
        else "Not measurable — water or food endurance is unknown"
    )

    expiry_items = []
    for item in stock:
        if not (item.get("quantity") or 0):
            continue
        expiry_text = item.get("expiry_date")
        if not expiry_text:
            continue
        try:
            expiry_date = date.fromisoformat(str(expiry_text).strip())
        except (ValueError, TypeError):
            continue

        days_left = (expiry_date - today).days
        if days_left <= 30:
            if days_left < 0:
                timing = f"Expired {-days_left} days ago"
            elif days_left == 0:
                timing = "Expires today"
            else:
                timing = f"Expires in {days_left} days"

            # Correct known display typos without changing stored inventory.
            display_name = item.get("item") or ""
            display_name = display_name.replace("Reief", "Relief")
            display_name = display_name.replace("reief", "relief")
            display_name = display_name.replace("Tabelts", "Tablets")
            display_name = display_name.replace("tabelts", "tablets")

            expiry_items.append(
                (days_left, display_name, expiry_text, timing)
            )

    expiry_items.sort(key=lambda entry: (entry[0], entry[1].lower()))
    if expiry_items:
        expiry_rows = "".join(
            "<tr>"
            f"<td>{esc(name)}</td>"
            f"<td>{esc(expiry_text)}</td>"
            f"<td>{esc(timing)}</td>"
            "</tr>"
            for _, name, expiry_text, timing in expiry_items
        )
        expiry_html = (
            "<section><h2>Expiry alerts</h2>"
            "<p>Check these items and replace or safely dispose of expired stock "
            "as appropriate.</p>"
            "<table><thead><tr><th>Item</th><th>Expiry date</th>"
            "<th>Status</th></tr></thead><tbody>"
            + expiry_rows +
            "</tbody></table></section>"
        )
    else:
        expiry_html = (
            "<section><h2>Expiry alerts</h2>"
            "<p>No owned items are expired or due to expire within 30 days.</p>"
            "</section>"
        )

    rows = "".join(
        "<tr>"
        f"<td>{esc(r.get('icon', ''))} {esc(r.get('title', ''))}</td>"
        f"<td><strong>{esc(r.get('status', ''))}</strong><br>"
        f"{esc(r.get('description', ''))}</td></tr>"
        for r in recommendations
    )

    now = datetime.now().astimezone().strftime("%d %b %Y, %H:%M %Z")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PrepPi monthly preparedness report</title>
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
.metric{{display:inline-block;background:#f7f8fa;border-radius:8px;padding:12px 16px;margin:4px 8px 4px 0}}
.metric strong{{display:block;font-size:21px;margin-bottom:4px}}
footer{{padding:18px 24px;color:#667085;font-size:12px}}
</style></head><body><main>
<header><img src="cid:{LOGO_CID}" alt="PrepPi">
<h1>Monthly preparedness report</h1><div>{esc(now)}</div></header>
<section><h2>Water reserve</h2>
<div class="metric"><strong>{water_litres:g} L</strong>In-date water recorded</div>
<div class="metric"><strong>{esc(water_days_text)}</strong>Estimated at target usage</div>
<p>Household target: {daily_target:g} litres per day.
Target reserve: {esc(targets.get('water', {}).get('target_value', 84))} litres.</p>
</section>
<section><h2>Food and overall endurance</h2>
<div class="metric"><strong>{food['food_kcal']:,} kcal</strong>
Estimated energy in recorded food</div>
<div class="metric"><strong>{esc(food_days_text)}</strong>
Food endurance at {food['daily_target_kcal']:,.0f} kcal/day</div>
<div class="metric"><strong>{esc(overall_days_text)}</strong>
Overall endurance — limiting factor</div>
<div class="metric"><strong>{food['product_kcal']:,} kcal</strong>
Based on product data</div>
<div class="metric"><strong>{food['estimated_kcal']:,} kcal</strong>
Based on category estimates</div>
<p>Food items without usable pack sizes or nutrition estimates:
{food['unmeasured_count']}.</p>
<p>Food endurance is an estimate from recorded, in-date stock, not a meal
plan. Category estimates may differ from the actual product. The household
calorie target is taken from PrepPi settings.</p>
</section>
{pet_html}\n{expiry_html}
<section><h2>Preparedness recommendations</h2>
<table><thead><tr><th>Area</th><th>Status and guidance</th></tr></thead>
<tbody>{rows}</tbody></table></section>
<footer>
Prepared from the current inventory on {esc(today.isoformat())}.
Expired or incorrectly dated items are excluded from preparedness calculations,
but remain listed in the attached owned-inventory workbook.
Undated items are retained in the workbook but excluded from calculations.
Food counts are not treated as meal portions. Recommendations are not owned stock.
</footer></main></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Database to read")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    stock, equipment, targets, shopping, pets = read_inventory(args.db)
    today = date.today()
    html_path = out_dir / "monthly-preparedness-report.html"
    xlsx_path = out_dir / "monthly-owned-inventory.xlsx"

    with sqlite3.connect(
        f"file:{Path(args.db).resolve()}?mode=ro", uri=True
    ) as db:
        daily_target_kcal = read_daily_kcal_target(db)

    document = build_html(
        stock, equipment, targets, shopping, today, pets=pets,
        daily_target_kcal=daily_target_kcal,
    )
    html_path.write_text(document, encoding="utf-8")
    build_workbook(stock, equipment, xlsx_path, pets=pets)

    print(f"HTML report: {html_path}")
    print(f"Excel inventory: {xlsx_path}")
    print(f"Owned stock rows: {len(stock)}")
    print(f"Equipment rows: {len(equipment)}")
    print("Preview generation only: no database writes and no email sent.")


if __name__ == "__main__":
    main()
