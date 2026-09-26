import json
import tempfile
import unittest
from pathlib import Path

from memory import db
from memory import migration


class TestKiraMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "migrated.db"
        db.init_db(self.db_path)

        # Sample long_term.json
        self.lt_json_path = self.base_path / "long_term.json"
        self.lt_data = {
            "identity": {
                "user_name": {"value": "Leo", "updated": "2026-09-20"},
                "city": {"value": "Santo Domingo", "updated": "2026-09-21"},
            },
            "preferences": {
                "theme": {"value": "dark", "updated": "2026-09-22"},
            },
            "projects": {},
            "relationships": {},
            "wishes": {},
            "notes": {
                "quick_note": "A simple note string without dict",
            },
            "sessions": [
                {
                    "date": "2026-09-25",
                    "summary": "First conversation about weather",
                    "language": "es",
                },
                {
                    "date": "2026-09-26",
                    "summary": "Second conversation about tasks",
                    "language": "es",
                },
            ],
        }
        self.lt_json_path.write_text(
            json.dumps(self.lt_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Sample whatsapp_contacts.json
        self.wa_json_path = self.base_path / "whatsapp_contacts.json"
        self.wa_data = {
            "version": 1,
            "accounts": {
                "acc1@c.us": {
                    "guada": {
                        "id": "81909623341201@lid",
                        "name": "+52 1 312 119 3599",
                        "confirmed_at": "2026-09-21T23:40:00",
                    },
                    "nayeli": {
                        "id": "93716303900742@lid",
                        "name": "Nayeli",
                        "confirmed_at": "2026-09-22T08:29:31",
                    },
                }
            },
        }
        self.wa_json_path.write_text(
            json.dumps(self.wa_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Sample kira_tasks.json
        self.tasks_json_path = self.base_path / "kira_tasks.json"
        self.tasks_data = [
            {
                "id": 1,
                "text": "Comprar café",
                "done": False,
                "created": "2026-09-20 10:00:00",
            },
            {
                "id": 2,
                "text": "Revisar logs",
                "done": True,
                "created": "2026-09-21 11:00:00",
                "completed": "2026-09-21 12:00:00",
            },
        ]
        self.tasks_json_path.write_text(
            json.dumps(self.tasks_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_migration_correctness_and_equivalence(self):
        stats = migration.migrate_all(memory_dir=self.base_path, db_path=self.db_path)

        # Facts check
        self.assertEqual(stats["long_term"]["facts"], 4)
        name_fact = db.get_fact("identity", "user_name", db_path=self.db_path)
        self.assertIsNotNone(name_fact)
        self.assertEqual(name_fact["value"], "Leo")
        self.assertEqual(name_fact["updated_at"], "2026-09-20")

        note_fact = db.get_fact("notes", "quick_note", db_path=self.db_path)
        self.assertIsNotNone(note_fact)
        self.assertEqual(note_fact["value"], "A simple note string without dict")

        # Sessions check
        self.assertEqual(stats["long_term"]["sessions"], 2)
        sessions = db.list_sessions(limit=10, db_path=self.db_path)
        self.assertEqual(len(sessions), 2)
        summaries = {s["summary"] for s in sessions}
        self.assertIn("First conversation about weather", summaries)
        self.assertIn("Second conversation about tasks", summaries)

        # WhatsApp contacts check
        self.assertEqual(stats["whatsapp_contacts"]["contacts"], 2)
        c = db.get_whatsapp_contact("acc1@c.us", "guada", db_path=self.db_path)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "81909623341201@lid")
        self.assertEqual(c["name"], "+52 1 312 119 3599")

        # Tasks check
        self.assertEqual(stats["tasks"]["tasks"], 2)
        tasks = db.list_tasks(include_done=True, db_path=self.db_path)
        self.assertEqual(len(tasks), 2)
        task_map = {t["id"]: t for t in tasks}
        self.assertEqual(task_map[1]["text"], "Comprar café")
        self.assertEqual(task_map[1]["done"], 0)
        self.assertEqual(task_map[2]["text"], "Revisar logs")
        self.assertEqual(task_map[2]["done"], 1)

    def test_idempotence_second_run_no_duplicates(self):
        # Run 1
        migration.migrate_all(memory_dir=self.base_path, db_path=self.db_path)

        # Snapshot counts after run 1
        facts_count_1 = len(db.get_all_facts(db_path=self.db_path))
        sessions_count_1 = len(db.list_sessions(limit=100, db_path=self.db_path))
        wa_count_1 = len(db.get_all_whatsapp_contacts(db_path=self.db_path))
        tasks_count_1 = len(db.list_tasks(include_done=True, db_path=self.db_path))

        # Run 2
        stats_2 = migration.migrate_all(memory_dir=self.base_path, db_path=self.db_path)

        # Snapshot counts after run 2
        facts_count_2 = len(db.get_all_facts(db_path=self.db_path))
        sessions_count_2 = len(db.list_sessions(limit=100, db_path=self.db_path))
        wa_count_2 = len(db.get_all_whatsapp_contacts(db_path=self.db_path))
        tasks_count_2 = len(db.list_tasks(include_done=True, db_path=self.db_path))

        # Counts must not increase
        self.assertEqual(facts_count_1, facts_count_2)
        self.assertEqual(sessions_count_1, sessions_count_2)
        self.assertEqual(wa_count_1, wa_count_2)
        self.assertEqual(tasks_count_1, tasks_count_2)

        # Stats must report 0 new inserts and all skipped
        self.assertEqual(stats_2["long_term"]["facts"], 0)
        self.assertGreater(stats_2["long_term"]["skipped"], 0)
        self.assertEqual(stats_2["whatsapp_contacts"]["contacts"], 0)
        self.assertEqual(stats_2["tasks"]["tasks"], 0)

    def test_source_json_files_unmodified(self):
        lt_before = self.lt_json_path.read_text(encoding="utf-8")
        wa_before = self.wa_json_path.read_text(encoding="utf-8")
        tasks_before = self.tasks_json_path.read_text(encoding="utf-8")

        migration.migrate_all(memory_dir=self.base_path, db_path=self.db_path)

        # Source files must remain exactly as before
        self.assertEqual(self.lt_json_path.read_text(encoding="utf-8"), lt_before)
        self.assertEqual(self.wa_json_path.read_text(encoding="utf-8"), wa_before)
        self.assertEqual(self.tasks_json_path.read_text(encoding="utf-8"), tasks_before)

    def test_missing_or_corrupt_json_handled_safely(self):
        empty_dir = self.base_path / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        empty_db = self.base_path / "empty.db"

        # Missing files: should run without error, returning 0
        stats = migration.migrate_all(memory_dir=empty_dir, db_path=empty_db)
        self.assertEqual(stats["long_term"]["facts"], 0)
        self.assertEqual(stats["whatsapp_contacts"]["contacts"], 0)
        self.assertEqual(stats["tasks"]["tasks"], 0)

        # Corrupt files: invalid JSON should not crash
        corrupt_dir = self.base_path / "corrupt_dir"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "long_term.json").write_text("{ corrupt json ...", encoding="utf-8")
        (corrupt_dir / "whatsapp_contacts.json").write_text("invalid", encoding="utf-8")
        (corrupt_dir / "kira_tasks.json").write_text("not a list", encoding="utf-8")

        corrupt_db = self.base_path / "corrupt.db"
        stats_corrupt = migration.migrate_all(memory_dir=corrupt_dir, db_path=corrupt_db)
        self.assertEqual(stats_corrupt["long_term"]["facts"], 0)
        self.assertEqual(stats_corrupt["whatsapp_contacts"]["contacts"], 0)
        self.assertEqual(stats_corrupt["tasks"]["tasks"], 0)


if __name__ == "__main__":
    unittest.main()
