from datetime import date


def stock_expiry_status(expiry_text, today=None):
    """Return 'in_date', 'expired', or 'unknown' for a stock expiry date."""
    today = today or date.today()
    expiry_text = (expiry_text or "").strip()

    if not expiry_text:
        return "unknown"

    try:
        expiry = date.fromisoformat(expiry_text)
    except (TypeError, ValueError):
        return "unknown"

    return "expired" if expiry < today else "in_date"


def build_recommendations(all_stock, equipment, targets, outstanding, pets=None):
    import re

    # Only confirmed, in-date stock contributes to preparedness counts.
    usable_stock = [
        row for row in all_stock
        if stock_expiry_status(row["expiry_date"]) == "in_date"
    ]

    water_litres = 0.0
    food_units = 0
    staples = 0
    proteins = 0
    fuel_units = 0
    batteries = 0
    first_aid = 0
    hygiene = 0

    for row in usable_stock:
        name = (row["item"] or "").lower()
        category_name = (row["category"] or "").lower()
        subcategory = (row["subcategory"] or "").lower()
        combined = name + " " + subcategory
        qty = max(0, row["quantity"] or 0)

        if "water" in combined:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*l(?:itre|iter)?s?\b", name)
            if match:
                water_litres += float(match.group(1)) * qty

        if category_name == "food and drink" and "water" not in combined:
            food_units += qty

        if any(word in combined for word in
               ("rice", "pasta", "oat", "cereal", "cracker", "noodle", "grain")):
            staples += qty

        if any(word in combined for word in
               ("bean", "lentil", "pulse", "fish", "meat", "protein", "pea and ham", "peanut butter")):
            proteins += qty

        if any(word in combined for word in
               ("gas canister", "camping gas", "stove fuel", "cooking fuel")):
            fuel_units += qty

        if "batter" in name:
            batteries += qty
        if "first aid" in combined:
            first_aid += qty
        if any(word in combined for word in
               ("toilet roll", "toilet paper", "soap", "sanitary", "hygiene", "wipes")):
            hygiene += qty

    # Keep all recorded equipment names, including items whose quantity
    # is unknown, so the recommendations can distinguish "owned" from
    # "not recorded".
    all_equipment_names = {
        (row["item"] or "").lower()
        for row in equipment
    }
    equipment_names = {
        (row["item"] or "").lower()
        for row in equipment
        if (row["quantity"] or 0) > 0
    }

    camping_stove = any(
        "camping stove" in name or "gas stove" in name
        for name in equipment_names
    )
    wood_stove = any(
        "wood-burning stove" in name
        or "wood burning stove" in name
        or "log burner" in name
        for name in equipment_names
    )
    cooking_equipment = camping_stove or wood_stove

    wood_fuel_units = sum(
        max(0, row["quantity"] or 0)
        for row in usable_stock
        if any(
            word in ((row["item"] or "") + " " + (row["subcategory"] or "")).lower()
            for word in ("firewood", "logs", "kindling")
        )
    )

    # Keep medicine suggestions in the Medicines recommendation only.
    shopping_names = {
        row["item"].lower()
        for row in outstanding
        if "medicin" not in (row["category"] or "").lower()
    }

    target_row = targets.get("water")
    water_target = float(target_row["target_value"]) if target_row else 84.0

    recommendations = [
        {
            "icon": "💧",
            "title": "Drinking water",
            "description": f"{water_litres:g} litres recorded against an illustrative {water_target:g}-litre household target.",
            "status": "PRIORITY" if water_litres < water_target else "TARGET REACHED",
            "status_class": "status-close" if water_litres < water_target else "status-good",
            "tag": "HIGH PRIORITY",
        },
        {
            "icon": "🥫",
            "title": "Tinned food and produce",
            "description": f"{food_units} food containers recorded, excluding water. This does not confirm how many meals they provide.",
            "status": "STOCK RECORDED" if food_units else "GAP IDENTIFIED",
            "status_class": "status-good" if food_units else "status-poor",
            "tag": "FOOD RESERVE",
        },
        {
            "icon": "🍚",
            "title": "Carbohydrates and staples",
            "description": f"{staples} likely staple items recorded. Consider adding rice, pasta, oats or crackers if useful for your household.",
            "status": "STOCK RECORDED" if staples else "GAP IDENTIFIED",
            "status_class": "status-good" if staples else "status-poor",
            "tag": "FOOD RESERVE",
        },
        {
            "icon": "🥫",
            "title": "Protein and balanced meals",
            "description": f"{proteins} likely protein-containing items recorded. Broaden the range if needed.",
            "status": "SOME OPTIONS" if proteins else "GAP IDENTIFIED",
            "status_class": "status-good" if proteins else "status-poor",
            "tag": "FOOD RESERVE",
        },
        {
            "icon": "🔥",
            "title": "Cooking fuel",
            "description": (
                (
                    "Camping stove recorded, but no possible compatible gas fuel is recorded. "
                    if not fuel_units
                    else f"{fuel_units} possible gas-fuel items recorded; check stove compatibility. "
                ) if camping_stove else ""
            ) + (
                (
                    "Wood-burning stove recorded, but logs and kindling are not recorded."
                    if not wood_fuel_units
                    else f"{wood_fuel_units} possible logs/firewood/kindling items recorded; check that supplies are sufficient."
                ) if wood_stove else ""
            ) if cooking_equipment else "No cooking equipment is recorded, so no cooking-fuel gap has been inferred.",
            "status": (
                "CHECK FUEL" if (camping_stove and not fuel_units)
                or (wood_stove and not wood_fuel_units)
                else "VERIFY COMPATIBILITY" if camping_stove and fuel_units
                else "STOCK RECORDED" if wood_stove and wood_fuel_units
                else "NOT APPLICABLE" if not cooking_equipment
                else "CHECK FUEL"
            ),
            "status_class": (
                "status-close" if (
                    (camping_stove and not fuel_units)
                    or (wood_stove and not wood_fuel_units)
                    or (camping_stove and fuel_units)
                ) else "status-good" if wood_stove and wood_fuel_units
                else "status-good"
            ),
            "tag": "ACTION NEEDED" if cooking_equipment else "EQUIPMENT CHECK",
        },
        {
            "icon": "🔦",
            "title": "Lighting and power",
            "description": (
                (
                    "Lighting equipment is recorded as owned, but its quantity is unconfirmed. "
                    if any(
                        any(term in n for term in (
                            "torch", "flashlight", "lantern",
                            "headtorch", "head torch",
                        )) or re.search(r"\blights?\b", n)
                        for n in all_equipment_names
                    ) and not any(
                        any(term in n for term in (
                            "torch", "flashlight", "lantern",
                            "headtorch", "head torch",
                        )) or re.search(r"\blights?\b", n)
                        for n in equipment_names
                    )
                    else "Lighting equipment is recorded. "
                    if any(
                        any(term in n for term in (
                            "torch", "flashlight", "lantern",
                            "headtorch", "head torch",
                        )) or re.search(r"\blights?\b", n)
                        for n in equipment_names
                    )
                    else "Lighting equipment is not recorded in equipment. "
                )
                + (
                    "No battery items are recorded; check the type and size required by your lights."
                    if not batteries
                    else f"{batteries} battery items recorded; check that their type and charge suit your lights."
                )
            ),
            "status": (
                "CHECK LIGHTING & BATTERIES"
                if not batteries or not any(
                    any(term in n for term in (
                        "torch", "flashlight", "lantern",
                        "headtorch", "head torch",
                    )) or re.search(r"\blights?\b", n)
                    for n in equipment_names
                )
                else "VERIFY BATTERIES"
            ),
            "status_class": (
                "status-close"
                if not batteries or not any(
                    any(term in n for term in (
                        "torch", "flashlight", "lantern",
                        "headtorch", "head torch",
                    )) or re.search(r"\blights?\b", n)
                    for n in equipment_names
                )
                else "status-good"
            ),
            "tag": "OUTAGE ESSENTIAL",
        },
        {
            "icon": "🩹",
            "title": "First aid",
            "description": f"{first_aid} first-aid items recorded. A complete kit is not confirmed by this count.",
            "status": "CHECK FIRST AID" if not first_aid else "VERIFY KIT",
            "status_class": "status-close" if not first_aid else "status-good",
            "tag": "OUTAGE ESSENTIAL",
        },
        {
            "icon": "🧼",
            "title": "Hygiene and sanitation",
            "description": f"{hygiene} likely hygiene items recorded. Supplies not entered in the inventory may still be at home.",
            "status": "CHECK SUPPLIES" if not hygiene else "STOCK RECORDED",
            "status_class": "status-close" if not hygiene else "status-good",
            "tag": "HOUSEHOLD NEEDS",
        },
    ]

    # Medicines appear as one recommendation card, matching the other areas.
    owned_medicines = [
        row for row in usable_stock
        if "medicin" in (row["category"] or "").lower()
        and (row["quantity"] or 0) > 0
    ]
    owned_text = " ".join(
        (row["item"] or "").lower() + " " +
        (row["subcategory"] or "").lower()
        for row in owned_medicines
    )

    owned_labels = []
    if any(word in owned_text for word in ("allergy relief", "antihistamine")):
        owned_labels.append("allergy relief")
    if "ibuprofen" in owned_text:
        owned_labels.append("ibuprofen")
    if "paracetamol" in owned_text:
        owned_labels.append("paracetamol")

    medicine_suggestions = []
    for row in outstanding:
        if "medicin" not in (row["category"] or "").lower():
            continue
        name = (row["item"] or "").lower()
        if "antihistamine" in name and "allergy relief" in owned_text:
            continue
        if "ibuprofen" in name and "ibuprofen" in owned_text:
            continue
        if "paracetamol" in name and "paracetamol" in owned_text:
            continue
        medicine_suggestions.append(row["item"])

    if owned_labels:
        medicine_status = "SOME STOCK RECORDED"
        medicine_status_class = "status-good"
        medicine_description = "Recorded in date: " + ", ".join(owned_labels) + "."
    else:
        medicine_status = "GAP IDENTIFIED"
        medicine_status_class = "status-poor"
        medicine_description = "No in-date medicines are recorded."

    if medicine_suggestions:
        shown_suggestions = medicine_suggestions[:4]
        medicine_description += (
            " Consider adding: " + ", ".join(shown_suggestions)
            + (" and other listed items." if len(medicine_suggestions) > 4 else ".")
        )

    medicine_description += (
        " Check suitability, packet instructions and expiry dates; "
        "avoid doubling up on ingredients such as paracetamol."
    )

    recommendations.append({
        "icon": "💊",
        "title": "Medicines",
        "description": medicine_description,
        "status": medicine_status,
        "status_class": medicine_status_class,
        "tag": "HOUSEHOLD HEALTH",
    })


    # Pet food is tracked separately from human food and only in-date
    # Pet Supplies stock with a recorded pack size contributes.
    if pets:
        daily_pet_food = sum(
            float(pet["daily_food_grams"]) for pet in pets
        )
        pet_target_grams = daily_pet_food * 14

        available_pet_food = 0.0
        for row in usable_stock:
            if (row["category"] or "").strip().lower() != "pet supplies":
                continue
            if (row["subcategory"] or "").strip().lower() != "pet food":
                continue

            try:
                pack_size = float(row["unit_size_grams"])
                quantity = max(0, float(row["quantity"] or 0))
            except (KeyError, IndexError, TypeError, ValueError):
                continue

            if 0 < pack_size < float("inf"):
                available_pet_food += pack_size * quantity

        shortfall = max(0.0, pet_target_grams - available_pet_food)
        days_covered = available_pet_food / daily_pet_food

        if shortfall <= 0:
            pet_status = "14-DAY TARGET REACHED"
            pet_status_class = "status-good"
            pet_description = (
                f"{available_pet_food:g}g of in-date pet food recorded "
                f"against a {pet_target_grams:g}g 14-day target."
            )
        else:
            pet_status = "FOOD SHORTFALL"
            pet_status_class = "status-poor"
            pet_description = (
                f"{available_pet_food:g}g of in-date pet food recorded "
                f"against a {pet_target_grams:g}g 14-day target. "
                f"Shortfall: {shortfall:g}g."
            )

        pet_description += (
            f" This represents approximately {days_covered:.1f} days "
            "of food at the recorded daily allowances."
        )

        recommendations.append({
            "icon": "🐾",
            "title": "Pet food",
            "description": pet_description,
            "status": pet_status,
            "status_class": pet_status_class,
            "tag": "PET PREPAREDNESS",
        })


    keywords_by_title = {
        "Drinking water": ("water",),
        "Tinned food and produce": ("tin", "canned"),
        "Carbohydrates and staples": ("rice", "pasta", "oat", "cracker", "grain"),
        "Protein and balanced meals": ("bean", "lentil", "fish", "protein"),
        "Cooking fuel": ("fuel", "canister", "gas"),
        "Lighting and power": ("torch", "flashlight", "light", "lantern", "battery", "power bank"),
        "First aid": ("first aid", "bandage", "plaster"),
        "Hygiene and sanitation": ("hygiene", "soap", "toilet", "sanitation"),
    }

    for rec in recommendations:
        keywords = keywords_by_title.get(rec["title"], ())
        matches = [
            item for item in shopping_names
            if any(word in item for word in keywords)
        ]
        if matches:
            rec["description"] += (
                " Already on your background shopping list: "
                + ", ".join(sorted(matches)[:3]) + "."
            )

    return recommendations
