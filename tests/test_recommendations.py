import unittest

from shared.recommendations import build_recommendations, stock_expiry_status


def recommendation(title, stock=None, equipment=None, targets=None, pets=None):
    results = build_recommendations(
        stock or [],
        equipment or [],
        targets or {},
        [],
        pets=pets,
    )
    return next(item for item in results if item["title"] == title)


def equipment_item(item, quantity=1, notes=""):
    return {"item": item, "quantity": quantity, "notes": notes}


class RecommendationTests(unittest.TestCase):
    def test_cooking_fuel_is_not_flagged_without_cooking_equipment(self):
        result = recommendation("Cooking fuel")
        self.assertNotEqual(result["status"], "CHECK FUEL")

    def test_camping_stove_without_fuel_flags_fuel(self):
        result = recommendation(
            "Cooking fuel",
            equipment=[equipment_item("Camping stove")],
        )
        self.assertEqual(result["status"], "CHECK FUEL")

    def test_wood_stove_alone_does_not_require_gas_canisters(self):
        result = recommendation(
            "Cooking fuel",
            equipment=[equipment_item("Wood-burning stove")],
        )
        self.assertNotIn("gas fuel", result["description"].lower())
        self.assertIn("logs and kindling", result["description"].lower())

    def test_torches_without_batteries_flag_batteries(self):
        result = recommendation(
            "Lighting and power",
            equipment=[equipment_item("Torches", None)],
        )
        self.assertEqual(result["status"], "CHECK LIGHTING & BATTERIES")




    def test_camping_light_counts_as_lighting_equipment(self):
        result = recommendation(
            "Lighting and power",
            equipment=[equipment_item("Camping light", 1)],
        )
        self.assertIn("Lighting equipment is recorded", result["description"])
        self.assertNotIn("not recorded in equipment", result["description"])
        self.assertEqual(result["status"], "CHECK LIGHTING & BATTERIES")

    def test_lantern_counts_as_lighting_equipment(self):
        result = recommendation(
            "Lighting and power",
            equipment=[equipment_item("LED lantern", 1)],
        )
        self.assertIn("Lighting equipment is recorded", result["description"])

    def test_torches_owned_but_quantity_unknown_is_explained(self):
        result = recommendation(
            "Lighting and power",
            equipment=[equipment_item("Torches", None)],
        )
        self.assertIn("quantity is unconfirmed", result["description"].lower())

    def test_missing_torches_are_flagged(self):
        result = recommendation("Lighting and power")
        self.assertIn("not recorded in equipment", result["description"].lower())
        self.assertEqual(result["status"], "CHECK LIGHTING & BATTERIES")

    def test_batteries_are_not_assumed_compatible(self):
        result = recommendation(
            "Lighting and power",
            stock=[{
                "item": "AA batteries",
                "category": "Equipment",
                "subcategory": "Batteries",
                "quantity": 4,
                "expiry_date": "2099-12-31",
            }],
            equipment=[equipment_item("Torches", 1)],
        )
        self.assertIn(
            "check that their type and charge suit your lights",
            result["description"].lower(),
        )
        self.assertEqual(result["status"], "VERIFY BATTERIES")


class RecommendationExpiryTests(unittest.TestCase):
    def setUp(self):
        from datetime import date, timedelta
        self.today = date.today()
        self.in_date = (self.today + timedelta(days=30)).isoformat()
        self.expired = (self.today - timedelta(days=1)).isoformat()

    def test_expired_food_does_not_count(self):
        result = recommendation(
            "Tinned food and produce",
            stock=[{
                "item": "Tinned beans",
                "category": "Food and Drink",
                "subcategory": "Tinned",
                "quantity": 5,
                "expiry_date": self.expired,
            }],
        )
        self.assertEqual(result["status"], "GAP IDENTIFIED")

    def test_in_date_food_counts(self):
        result = recommendation(
            "Tinned food and produce",
            stock=[{
                "item": "Tinned beans",
                "category": "Food and Drink",
                "subcategory": "Tinned",
                "quantity": 5,
                "expiry_date": self.in_date,
            }],
        )
        self.assertEqual(result["status"], "STOCK RECORDED")

    def test_food_with_unknown_expiry_does_not_count(self):
        result = recommendation(
            "Tinned food and produce",
            stock=[{
                "item": "Tinned beans",
                "category": "Food and Drink",
                "subcategory": "Tinned",
                "quantity": 5,
                "expiry_date": "",
            }],
        )
        self.assertEqual(result["status"], "GAP IDENTIFIED")


class PetRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.pets = [
            {"name": "Milo", "species": "cat", "daily_food_grams": 60},
            {"name": "Rex", "species": "dog", "daily_food_grams": 250},
        ]
        self.in_date_pet_food = {
            "item": "Pet food tins",
            "category": "Pet Supplies",
            "subcategory": "Pet Food",
            "quantity": 10,
            "unit_size_grams": 400,
            "expiry_date": "2099-12-31",
        }

    def test_multiple_pets_use_combined_fourteen_day_requirement(self):
        result = recommendation(
            "Pet food",
            stock=[self.in_date_pet_food],
            pets=self.pets,
        )
        # 310g/day × 14 days = 4,340g; stock is 4,000g.
        self.assertEqual(result["status"], "FOOD SHORTFALL")
        self.assertIn("4340g 14-day target", result["description"])
        self.assertIn("Shortfall: 340g", result["description"])
        self.assertIn("12.9 days", result["description"])

    def test_expired_pet_food_does_not_count(self):
        stock = dict(self.in_date_pet_food, expiry_date="2000-01-01")
        result = recommendation("Pet food", stock=[stock], pets=self.pets)
        self.assertIn("0g of in-date pet food", result["description"])
        self.assertEqual(result["status"], "FOOD SHORTFALL")

    def test_missing_or_invalid_pack_size_does_not_count(self):
        stock = dict(self.in_date_pet_food, unit_size_grams=None)
        result = recommendation("Pet food", stock=[stock], pets=self.pets)
        self.assertIn("0g of in-date pet food", result["description"])

    def test_no_pet_recommendation_without_registered_pets(self):
        results = build_recommendations([], [], {}, [])
        self.assertNotIn("Pet food", [item["title"] for item in results])


class ExpiryStatusTests(unittest.TestCase):
    def setUp(self):
        self.today = __import__("datetime").date(2026, 10, 26)

    def test_expiry_today_counts_as_in_date(self):
        self.assertEqual(
            stock_expiry_status("2026-10-26", self.today), "in_date"
        )

    def test_future_expiry_counts_as_in_date(self):
        self.assertEqual(
            stock_expiry_status("2026-10-27", self.today), "in_date"
        )

    def test_past_expiry_is_expired(self):
        self.assertEqual(
            stock_expiry_status("2026-10-25", self.today), "expired"
        )

    def test_missing_expiry_is_unknown(self):
        self.assertEqual(stock_expiry_status("", self.today), "unknown")
        self.assertEqual(stock_expiry_status(None, self.today), "unknown")

    def test_invalid_expiry_is_unknown(self):
        self.assertEqual(
            stock_expiry_status("not-a-date", self.today), "unknown"
        )


if __name__ == "__main__":
    unittest.main()
