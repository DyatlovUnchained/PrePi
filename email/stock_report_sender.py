#!/usr/bin/env python3
"""Send an on-demand owned-stock and equipment report."""
import argparse
import smtplib
import sqlite3
import sys
import tempfile
from datetime import date
from html import escape
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from monthly_report import read_inventory, build_workbook
from monthly_report import usable_stock
from shared.endurance import calculate_endurance
from shared.food_endurance import calculate_food_endurance
from report_sender import load_report_config, make_message

from paths import DB_PATH
from settings import read_daily_kcal_target
SUBJECT = "PrepPi — on-demand stock inventory"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send the email; otherwise preview only.",
    )
    args = parser.parse_args()

    stock, equipment, targets, shopping, pets = read_inventory(DB_PATH)

    db_uri = f"file:{Path(DB_PATH).resolve()}?mode=ro"
    with sqlite3.connect(db_uri, uri=True) as db:
        daily_target_kcal = read_daily_kcal_target(db)

    today = date.today()
    usable = usable_stock(stock, today)
    water = calculate_endurance(usable, targets, today=today)
    food = calculate_food_endurance(
        usable,
        daily_target_kcal=daily_target_kcal,
        today=today,
        allow_lookup=False,
        cache_writes=False,
    )

    pet_html = ""
    if pets:
        daily_pet_food = sum(
            float(pet["daily_food_grams"]) for pet in pets
        )
        pet_target = daily_pet_food * 14
        pet_stock = sum(
            float(row.get("quantity") or 0) *
            float(row.get("unit_size_grams") or 0)
            for row in usable
            if row.get("category") == "Pet Supplies"
            and row.get("subcategory") == "Pet Food"
        )
        pet_days = pet_stock / daily_pet_food if daily_pet_food else 0
        pet_shortfall = max(0, pet_target - pet_stock)

        pet_html = f"""
        <h3>Pet preparedness</h3>
        <ul>
          <li>Registered pets: {len(pets)}</li>
          <li>Combined daily food allowance: {daily_pet_food:g} g</li>
          <li>14-day food requirement: {pet_target:g} g</li>
          <li>In-date pet food recorded: {pet_stock:g} g</li>
          <li>Estimated pet food endurance: {pet_days:.1f} days</li>
          <li>14-day shortfall: {pet_shortfall:g} g</li>
        </ul>
        """

    water_days = water.get("water_days")
    food_days = food.get("food_days")
    water_text = f"{water_days:.1f} days" if water_days is not None else "Not measurable"
    food_text = f"{food_days:.1f} days" if food_days is not None else "Not measurable"
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
    overall_text = (
        f"{overall_days:.1f} days (limited by {limiting_factor})"
        if overall_days is not None
        else "Not measurable — water or food endurance is unknown"
    )

    with tempfile.TemporaryDirectory(prefix="preppi-stock-") as tmp:
        out = Path(tmp)
        workbook = out / "owned-stock-inventory.xlsx"
        build_workbook(stock, equipment, workbook, pets=pets)

        html = f"""
        <html><body>
        <h2>PrepPi — on-demand stock inventory</h2>
        <p>Inventory snapshot generated on {escape(date.today().strftime("%d/%m/%Y"))}.</p>
        <p>The attached Excel workbook contains the recorded owned stock
        and equipment. It does not include the shopping list.</p>
        <ul>
          <li>Owned stock records: {len(stock)}</li>
          <li>Equipment records: {len(equipment)}</li>
        </ul>
        <h3>Current stock endurance</h3>
        <ul>
          <li>In-date water recorded: {water.get("water_litres", 0):g} litres</li>
          <li>Estimated water endurance: {escape(water_text)}</li>
          <li>Estimated food energy: {food["food_kcal"]:,} kcal</li>
          <li>Estimated food endurance: {escape(food_text)} at
              {food["daily_target_kcal"]:,.0f} kcal/day</li>
          <li>Energy based on product data: {food["product_kcal"]:,} kcal</li>
          <li>Energy based on category estimates: {food["estimated_kcal"]:,} kcal</li>
          <li>Food items not measurable: {food["unmeasured_count"]}</li>
          <li>Overall endurance — limiting factor:
              {escape(overall_text)}</li>
        </ul>
        {pet_html}
        <p>These are planning estimates based on recorded, in-date stock.
        Category nutrition estimates may differ from actual products.
        Items with missing or invalid expiry dates are excluded from these
        calculations.</p>
        <p>This is a snapshot of the inventory currently recorded in PrepPi.</p>
        </body></html>
        """

        if not args.send:
            print("PREVIEW ONLY — no credentials read and no email sent.")
            print(f"Subject: {SUBJECT}")
            print(f"Excel attachment: {workbook.name} ({workbook.stat().st_size} bytes)")
            print(f"Owned stock records: {len(stock)}")
            print(f"Equipment records: {len(equipment)}")
            return 0

        sender, password, recipients = load_report_config()
        msg = make_message(SUBJECT, html, sender, recipients)
        msg.add_attachment(
            workbook.read_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="PrepPi-owned-stock-inventory.xlsx",
        )

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.send_message(msg, from_addr=sender, to_addrs=recipients)

        print(
            f"On-demand stock report sent to "
            f"{len(recipients)} configured recipient(s)."
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        print(f"Stock report failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
