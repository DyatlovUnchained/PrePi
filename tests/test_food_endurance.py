import unittest

from shared.food_endurance import _row_pack_grams


class PackSizeTests(unittest.TestCase):
    def test_explicit_pack_size_takes_priority(self):
        row = {
            "item": "Premium dog food",
            "unit_size_grams": 1200,
        }
        self.assertEqual(_row_pack_grams(row), 1200)

    def test_legacy_name_weight_is_used_when_size_is_missing(self):
        row = {
            "item": "Tinned soup 400g",
            "unit_size_grams": None,
        }
        self.assertEqual(_row_pack_grams(row), 400)

    def test_legacy_kilogram_weight_is_converted(self):
        row = {
            "item": "Dry food 1.5kg",
            "unit_size_grams": None,
        }
        self.assertEqual(_row_pack_grams(row), 1500)

    def test_explicit_size_overrides_name_weight(self):
        row = {
            "item": "Dog food 400g",
            "unit_size_grams": 800,
        }
        self.assertEqual(_row_pack_grams(row), 800)

    def test_invalid_explicit_size_falls_back_to_name(self):
        row = {
            "item": "Tinned soup 400g",
            "unit_size_grams": -10,
        }
        self.assertEqual(_row_pack_grams(row), 400)

    def test_missing_size_and_name_weight_returns_none(self):
        row = {
            "item": "Premium dog food",
            "unit_size_grams": None,
        }
        self.assertIsNone(_row_pack_grams(row))

    def test_older_row_without_new_column_still_works(self):
        row = {"item": "Tinned beans 420g"}
        self.assertEqual(_row_pack_grams(row), 420)


class FoodEnduranceCalculationTests(unittest.TestCase):
    def setUp(self):
        from datetime import date
        self.today = date(2026, 10, 29)

    def make_item(self, name, quantity, pack_size):
        return {
            "item": name,
            "category": "Food and Drink",
            "subcategory": "Beans",
            "quantity": quantity,
            "expiry_date": "2027-12-31",
            "unit_size_grams": pack_size,
        }

    def test_calculation_uses_explicit_pack_size(self):
        from unittest.mock import patch
        from shared.food_endurance import calculate_food_endurance

        stock = [self.make_item("Premium dog food", 2, 1200)]

        with patch(
            "shared.food_endurance._cached_product",
            return_value=(100, "category estimate", None),
        ):
            result = calculate_food_endurance(
                stock, daily_target_kcal=4000, today=self.today
            )

        self.assertEqual(result["food_kcal"], 2400)
        self.assertEqual(result["breakdown"][0]["kcal"], 2400)

    def test_calculation_retains_legacy_name_weight(self):
        from unittest.mock import patch
        from shared.food_endurance import calculate_food_endurance

        stock = [self.make_item("Tinned beans 400g", 2, None)]

        with patch(
            "shared.food_endurance._cached_product",
            return_value=(100, "category estimate", None),
        ):
            result = calculate_food_endurance(
                stock, daily_target_kcal=4000, today=self.today
            )

        self.assertEqual(result["food_kcal"], 800)

    def test_explicit_size_is_passed_to_cached_lookup(self):
        from unittest.mock import patch
        from shared.food_endurance import calculate_food_endurance

        stock = [self.make_item("Premium dog food", 1, 1200)]

        with patch(
            "shared.food_endurance._cached_product",
            return_value=(100, "category estimate", None),
        ) as cached:
            calculate_food_endurance(
                stock, today=self.today, allow_lookup=True
            )

        self.assertEqual(cached.call_args.kwargs["pack_size_grams"], 1200)




if __name__ == "__main__":
    unittest.main()
