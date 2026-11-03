import re
from datetime import date


def calculate_endurance(all_stock, targets, today=None):
    """Estimate water endurance from recorded stock and household targets."""
    today = today or date.today()

    water_target = targets.get("water", {})
    daily_water_litres = (
        float(water_target.get("target_value", 84)) / 14
    )

    in_date_water_litres = 0.0

    for row in all_stock:
        name = (row["item"] or "").lower()
        subcategory = (row["subcategory"] or "").lower()
        combined = name + " " + subcategory

        if "water" not in combined:
            continue

        expiry_text = (row["expiry_date"] or "").strip()
        if not expiry_text:
            continue

        try:
            expiry = date.fromisoformat(expiry_text)
        except ValueError:
            continue

        if expiry < today:
            continue

        match = re.search(
            r"([0-9]+(?:\.[0-9]+)?)\s*l(?:itre|iter)?s?\b",
            name,
        )
        if match:
            quantity = max(0, row["quantity"] or 0)
            in_date_water_litres += float(match.group(1)) * quantity

    water_days = (
        in_date_water_litres / daily_water_litres
        if daily_water_litres > 0
        else None
    )

    return {
        "water_litres": in_date_water_litres,
        "water_daily_target": daily_water_litres,
        "water_days": water_days,
        "food_days": None,
        "overall_days": None,
        "food_status": "Food endurance is calculated separately.",
        "overall_status": "Overall endurance is calculated separately.",
    }
