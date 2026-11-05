#!/usr/bin/env python3
"""Estimate food energy using cached product data and labelled fallbacks."""
import json
import re
import sqlite3
import time
from datetime import date
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
import sys
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from paths import DB_PATH
API_URL = "https://world.openfoodfacts.org/cgi/search.pl"

# Approximate kcal per 100 g, used only when product data cannot be verified.
CATEGORY_ESTIMATES = {
    "soup": (45, "category estimate"),
    "beans": (90, "category estimate"),
    "tinned fruit": (65, "category estimate"),
    "tinned tomatoes": (25, "category estimate"),
    "staples": (350, "category estimate"),
    "protein": (150, "category estimate"),
}


def _is_water(name, subcategory):
    """Exclude water without accidentally excluding foods such as watermelon."""
    return (
        (subcategory or "").strip().lower() == "water"
        or bool(re.search(r"\bwater\b", (name or "").lower()))
    )

USER_AGENT = "PrepPi-Household-Stock-Calculator/1.0 (personal project)"
CACHE_DAYS = 30


def _connect(read_only=False):
    if read_only:
        uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=10)
    else:
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.execute("""
            CREATE TABLE IF NOT EXISTS food_nutrition_cache (
                product_key TEXT PRIMARY KEY,
                kcal_per_100g REAL,
                source TEXT NOT NULL,
                matched_product TEXT,
                checked_at REAL NOT NULL
            )
        """)
    db.row_factory = sqlite3.Row
    return db


def _pack_grams(name):
    """Read a package size such as 400g, 420 g or 0.4 kg from the item name."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)\b", name.lower())
    if not match:
        return None
    amount = float(match.group(1))
    return amount * 1000 if match.group(2) == "kg" else amount


def _row_pack_grams(row):
    """Use the recorded pack size, falling back to the legacy item name."""
    try:
        explicit_size = row["unit_size_grams"]
    except (KeyError, IndexError):
        explicit_size = None

    try:
        explicit_size = float(explicit_size)
    except (TypeError, ValueError):
        explicit_size = None

    if explicit_size is not None and 0 < explicit_size < float("inf"):
        return explicit_size

    return _pack_grams(row["item"] or "")


def _normalise(value):
    value = (value or "").lower()
    value = re.sub(r"\b\d+(?:\.\d+)?\s*(?:g|kg|ml|l)\b", " ", value)
    return set(re.findall(r"[a-z]{3,}", value))


def _lookup_online(item_name, pack_size_grams=None):
    """Return a cautious product match, or None when it cannot be verified."""
    pack_size = pack_size_grams or _pack_grams(item_name)
    if not pack_size:
        return None

    params = urllib.parse.urlencode({
        "search_terms": item_name,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 5,
        "fields": "product_name,product_name_en,brands,quantity,product_quantity,product_quantity_unit,nutriments",
    })
    request = urllib.request.Request(
        API_URL + "?" + params,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TimeoutError):
        return None

    wanted = _normalise(item_name)
    best = None
    best_score = 0

    for product in data.get("products", []):
        name = product.get("product_name") or product.get("product_name_en") or ""
        candidate = _normalise(name + " " + (product.get("brands") or ""))
        if not candidate or not wanted:
            continue

        score = len(wanted & candidate) / len(wanted)
        quantity = product.get("product_quantity")
        unit = (product.get("product_quantity_unit") or "").lower()

        # Only accept a strong name match. Do not guess if pack size
        # conflicts with a clearly stated product quantity.
        if score < 0.75:
            continue
        if quantity and unit in ("g", "gram", "grams") and abs(float(quantity) - pack_size) > max(10, pack_size * 0.10):
            continue

        nutriments = product.get("nutriments") or {}
        kcal = nutriments.get("energy-kcal_100g")
        if kcal is None:
            kj = nutriments.get("energy-kj_100g")
            if kj is not None:
                kcal = float(kj) / 4.184

        try:
            kcal = float(kcal)
        except (TypeError, ValueError):
            continue

        if not 0 < kcal < 900:
            continue
        if score > best_score:
            best = (kcal, name)
            best_score = score

    return best


def _cached_product(item_name, category, allow_lookup=False,
                    pack_size_grams=None, connection=None,
                    cache_writes=True):
    """Read cached nutrition, optionally using a caller-owned connection.

    A supplied connection is never committed or closed here. When cache
    writes are disabled, stale/missing entries use estimates without
    modifying the database.
    """
    now = time.time()
    owns_connection = connection is None
    db = connection
    if owns_connection:
        db = _connect(read_only=not cache_writes)

    try:
        row = db.execute(
            "SELECT * FROM food_nutrition_cache WHERE product_key = ?",
            (item_name.lower(),),
        ).fetchone()

        if row and now - row["checked_at"] < CACHE_DAYS * 86400:
            if row["source"] == "product data":
                return row["kcal_per_100g"], row["source"], row["matched_product"]
            if row["source"] == "category estimate":
                return row["kcal_per_100g"], row["source"], None
            if row["source"] == "unmeasured":
                if (category or "").strip().lower() not in CATEGORY_ESTIMATES:
                    return None, "unmeasured", None

        result = _lookup_online(item_name, pack_size_grams) if allow_lookup else None
        if result:
            kcal, matched_name = result
            source = "product data"
        else:
            kcal, source = CATEGORY_ESTIMATES.get(
                (category or "").strip().lower(), (None, "unmeasured")
            )
            matched_name = None

        if cache_writes:
            db.execute("""
                INSERT INTO food_nutrition_cache
                    (product_key, kcal_per_100g, source, matched_product, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    kcal_per_100g = excluded.kcal_per_100g,
                    source = excluded.source,
                    matched_product = excluded.matched_product,
                    checked_at = excluded.checked_at
            """, (item_name.lower(), kcal, source, matched_name, now))
            if owns_connection:
                db.commit()

        return kcal, source, matched_name
    finally:
        if owns_connection:
            db.close()


def refresh_product_data(all_stock):
    """Explicitly refresh product lookups; no network requests on dashboard GET."""
    now = time.time()
    refreshed = 0
    db = _connect()
    try:
        seen = set()
        for row in all_stock:
            category = (row["category"] or "").strip().lower()
            if category not in ("food and drink", "food"):
                continue
            name = (row["item"] or "").strip()
            if not name or _is_water(name, row["subcategory"]):
                continue
            key = name.lower()
            pack_grams = _row_pack_grams(row)
            if key in seen or not pack_grams:
                continue
            seen.add(key)

            result = _lookup_online(name, pack_grams)
            if result:
                kcal, matched_name = result
                source = "product data"
            else:
                kcal, source = CATEGORY_ESTIMATES.get(
                    (row["subcategory"] or "").strip().lower(),
                    (None, "unmeasured"),
                )
                matched_name = None

            db.execute("""
                INSERT INTO food_nutrition_cache
                    (product_key, kcal_per_100g, source, matched_product, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    kcal_per_100g = excluded.kcal_per_100g,
                    source = excluded.source,
                    matched_product = excluded.matched_product,
                    checked_at = excluded.checked_at
            """, (key, kcal, source, matched_name, now))
            refreshed += 1
        db.commit()
        return refreshed
    finally:
        db.close()


def calculate_food_endurance(
    all_stock, daily_target_kcal=4000, today=None, allow_lookup=False,
    connection=None, cache_writes=True,
):
    """Return estimated energy days and the breakdown of data quality."""
    today = today or date.today()
    total_kcal = 0.0
    product_kcal = 0.0
    estimated_kcal = 0.0
    unmeasured_items = []
    breakdown = []

    owns_connection = connection is None
    if owns_connection:
        connection = _connect(read_only=not cache_writes)

    try:
        result = _calculate_food_endurance_rows(
            all_stock, daily_target_kcal, today, allow_lookup,
            connection, cache_writes,
        )
        if owns_connection and cache_writes:
            connection.commit()
        return result
    finally:
        if owns_connection:
            connection.close()


def _calculate_food_endurance_rows(
    all_stock, daily_target_kcal, today, allow_lookup,
    connection, cache_writes,
):

    total_kcal = 0.0
    product_kcal = 0.0
    estimated_kcal = 0.0
    unmeasured_items = []
    breakdown = []

    for row in all_stock:
        category = (row["category"] or "").strip().lower()
        if category not in ("food and drink", "food"):
            continue

        name = row["item"] or ""
        quantity = max(0, row["quantity"] or 0)
        if not quantity or _is_water(name, row["subcategory"]):
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

        pack_grams = _row_pack_grams(row)
        if not pack_grams:
            unmeasured_items.append(name)
            continue

        kcal_per_100g, source, matched_name = _cached_product(
            name,
            row["subcategory"] or "",
            allow_lookup=allow_lookup,
            pack_size_grams=pack_grams,
            connection=connection,
            cache_writes=cache_writes,
        )
        if kcal_per_100g is None:
            unmeasured_items.append(name)
            continue

        energy = kcal_per_100g * pack_grams / 100 * quantity
        total_kcal += energy
        if source == "product data":
            product_kcal += energy
        else:
            estimated_kcal += energy

        breakdown.append({
            "item": name,
            "quantity": quantity,
            "kcal": round(energy),
            "source": source,
            "matched_product": matched_name,
        })

    target = max(1, float(daily_target_kcal))
    return {
        "food_kcal": round(total_kcal),
        "food_days": total_kcal / target,
        "daily_target_kcal": target,
        "product_kcal": round(product_kcal),
        "estimated_kcal": round(estimated_kcal),
        "unmeasured_count": len(unmeasured_items),
        "unmeasured_items": sorted(set(unmeasured_items)),
        "breakdown": breakdown,
    }
