"""Tests for saved lists, profiles and the migration from the old folder name."""

import json
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QSettings

from core.migrate import migrate_data_dir, migrate_settings
from core.saved_lists import CorruptList, InvalidListName, SavedListsManager, clean_list_name
from core.settings_manager import load_profiles, save_profiles


def ini(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


class SavedListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.lists_dir = self.root / "lists"
        self.manager = SavedListsManager(self.lists_dir)

    def assert_inside(self, path):
        self.assertEqual(path.resolve().parent, self.lists_dir.resolve())

    def test_slash_in_the_name_is_saved_inside_the_folder(self):
        path, txt = self.manager.save_list("fiets/racefiets", [{"title": "a"}])
        self.assert_inside(path)
        self.assert_inside(txt)

    def test_dot_dot_cannot_escape_the_folder(self):
        path, _ = self.manager.save_list("../../ontsnapt", [])
        self.assert_inside(path)
        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_name_with_nothing_usable_is_refused(self):
        for name in ("", "   ", "///", "..."):
            with self.assertRaises(InvalidListName):
                self.manager.save_list(name, [])

    def test_windows_reserved_names_are_renamed(self):
        self.assertNotEqual(clean_list_name("con").upper(), "CON")

    def test_accents_digits_spaces_dash_and_underscore_survive(self):
        self.assertEqual(clean_list_name("Fiets-é 2_x y"), "Fiets-é 2_x y")

    def test_damaged_file_is_reported_and_left_alone(self):
        damaged = self.lists_dir / "kapot.json"
        damaged.write_text("{nope", encoding="utf-8")
        with self.assertRaises(CorruptList):
            self.manager.load_list("kapot")
        self.assertEqual(damaged.read_text(encoding="utf-8"), "{nope")

    def test_file_that_is_not_a_list_is_reported(self):
        (self.lists_dir / "dict.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(CorruptList):
            self.manager.load_list("dict")

    def test_round_trip(self):
        items = [{"id": "m1", "title": "Fiets", "price": "€ 10,00"}]
        self.manager.save_list("mijn lijst", items)
        self.assertEqual(self.manager.load_list("mijn lijst"), items)
        self.assertEqual(self.manager.list_names(), ["mijn lijst"])

    def test_list_from_an_older_version_keeps_its_name(self):
        old = self.lists_dir / "fietsen (2026).json"
        old.write_text(json.dumps([{"id": "m1"}]), encoding="utf-8")
        self.assertEqual(self.manager.load_list("fietsen (2026)"), [{"id": "m1"}])
        self.manager.save_list("fietsen (2026)", [{"id": "m1"}, {"id": "m2"}])
        self.assertEqual(self.manager.list_names(), ["fietsen (2026)"])

    def test_missing_list_reads_as_empty(self):
        self.assertEqual(self.manager.load_list("bestaat niet"), [])


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.settings = ini(Path(tempfile.mkdtemp()) / "profiles.ini")

    def test_round_trip_keeps_every_field(self):
        profile = {
            "name": "Racefiets", "term": "racefiets", "category_id": "445",
            "region": "8022RT", "distance": 25, "max_price": 400.0, "interval": 90,
            "free_only": True, "hide_promoted": False,
        }
        save_profiles(self.settings, [profile])
        self.assertEqual(load_profiles(self.settings), [profile])

    def test_interval_below_the_minimum_is_raised(self):
        save_profiles(self.settings, [{"name": "x", "interval": 10}])
        self.assertEqual(load_profiles(self.settings)[0]["interval"], 30)

    def test_profile_from_an_older_version_gets_defaults(self):
        self.settings.setValue("profiles/count", 1)
        self.settings.setValue("profiles/0/name", "Oud")
        self.settings.setValue("profiles/0/term", "fiets")
        loaded = load_profiles(self.settings)[0]
        self.assertEqual(loaded["distance"], 0)
        self.assertFalse(loaded["free_only"])
        self.assertTrue(loaded["hide_promoted"])

    def test_deleting_a_profile_leaves_no_stale_entries(self):
        save_profiles(self.settings, [{"name": "a"}, {"name": "b"}])
        save_profiles(self.settings, [{"name": "a"}])
        self.assertEqual([p["name"] for p in load_profiles(self.settings)], ["a"])
        self.assertIsNone(self.settings.value("profiles/1/name"))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_settings_move_and_the_old_file_disappears(self):
        old_path = self.root / "PerplexityLocal" / "app.conf"
        old = ini(old_path)
        old.setValue("search/term", "racefiets")
        old.setValue("telegram/chat_id", "123")
        old.sync()

        new = ini(self.root / "MIAW" / "app.conf")
        self.assertEqual(migrate_settings(old, new), 2)
        self.assertEqual(new.value("search/term"), "racefiets")
        self.assertEqual(new.value("telegram/chat_id"), "123")
        self.assertFalse(old_path.exists())
        self.assertFalse(old_path.parent.exists())

    def test_existing_new_settings_are_never_overwritten(self):
        old = ini(self.root / "old.conf")
        old.setValue("search/term", "oud")
        new = ini(self.root / "new.conf")
        new.setValue("search/term", "nieuw")
        self.assertEqual(migrate_settings(old, new), 0)
        self.assertEqual(new.value("search/term"), "nieuw")

    def test_data_folder_moves_across(self):
        old_dir = self.root / "PerplexityLocal" / "App"
        (old_dir / "saved_lists").mkdir(parents=True)
        (old_dir / "seen_ids.json").write_text(json.dumps({"fiets": {}}))
        (old_dir / "saved_lists" / "lijst.json").write_text("[]")

        new_dir = self.root / "MIAW" / "App"
        self.assertEqual(migrate_data_dir(old_dir, new_dir), 2)
        self.assertTrue((new_dir / "seen_ids.json").is_file())
        self.assertTrue((new_dir / "saved_lists" / "lijst.json").is_file())
        self.assertFalse(old_dir.exists())

    def test_data_folder_with_content_is_left_alone(self):
        old_dir = self.root / "old"
        old_dir.mkdir()
        (old_dir / "seen_ids.json").write_text("{}")
        new_dir = self.root / "new"
        new_dir.mkdir()
        (new_dir / "seen_ids.json").write_text('{"keep": {}}')
        self.assertEqual(migrate_data_dir(old_dir, new_dir), 0)
        self.assertEqual((new_dir / "seen_ids.json").read_text(), '{"keep": {}}')


if __name__ == "__main__":
    unittest.main()
