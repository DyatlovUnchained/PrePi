import sqlite3
import unittest

from settings import get_settings


class SettingsTests(unittest.TestCase):
    def test_fresh_install_defaults_to_two_adults(self):
        db = sqlite3.connect(":memory:")
        settings = get_settings(db)

        self.assertEqual(settings["adults"], 2)
        self.assertEqual(settings["children"], 0)
        self.assertEqual(settings["household_size"], 2)
        self.assertEqual(settings["daily_kcal_target"], 4000)
        self.assertEqual(settings["daily_water_target_litres"], 6)

        db.close()

    def test_legacy_household_size_is_preserved(self):
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE app_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)
        db.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES ('household_size', '3')
        """)

        settings = get_settings(db)

        self.assertEqual(settings["adults"], 3)
        self.assertEqual(settings["children"], 0)
        self.assertEqual(settings["household_size"], 3)
        self.assertEqual(settings["daily_kcal_target"], 6000)
        self.assertEqual(settings["daily_water_target_litres"], 9)

        db.close()

    def test_adult_and_child_counts_drive_resource_targets(self):
        db = sqlite3.connect(":memory:")
        settings = get_settings(db)

        db.execute("""
            UPDATE app_settings
            SET setting_value = '2'
            WHERE setting_key = 'adults'
        """)
        db.execute("""
            UPDATE app_settings
            SET setting_value = '1'
            WHERE setting_key = 'children'
        """)

        settings = get_settings(db)

        self.assertEqual(settings["household_size"], 3)
        self.assertEqual(settings["daily_kcal_target"], 6000)
        self.assertEqual(settings["daily_water_target_litres"], 9)

        db.close()


if __name__ == "__main__":
    unittest.main()
