import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from memory import db


class TestKiraMemoryDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_kira_memory.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wal_and_foreign_keys_enabled(self):
        conn = db.get_connection(self.db_path)
        try:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")
            self.assertEqual(foreign_keys, 1)
        finally:
            conn.close()

    def test_tables_created(self):
        conn = db.get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            )
            tables = {row[0] for row in cur.fetchall()}
            expected = {"facts", "sessions", "whatsapp_contacts", "tasks", "memory_audit_log"}
            self.assertTrue(expected.issubset(tables))
        finally:
            conn.close()

    def test_facts_crud(self):
        # Insert
        created = db.set_fact("identity", "user_name", "Leo", db_path=self.db_path)
        self.assertTrue(created)

        # Read
        fact = db.get_fact("identity", "user_name", db_path=self.db_path)
        self.assertIsNotNone(fact)
        self.assertEqual(fact["value"], "Leo")
        self.assertEqual(fact["category"], "identity")

        # Unchanged update
        unchanged = db.set_fact("identity", "user_name", "Leo", db_path=self.db_path)
        self.assertFalse(unchanged)

        # Update
        updated = db.set_fact("identity", "user_name", "Leonardo", db_path=self.db_path)
        self.assertTrue(updated)
        fact_up = db.get_fact("identity", "user_name", db_path=self.db_path)
        self.assertEqual(fact_up["value"], "Leonardo")

        # Category list
        db.set_fact("preferences", "food", "Pizza", db_path=self.db_path)
        id_facts = db.get_facts_by_category("identity", db_path=self.db_path)
        self.assertEqual(len(id_facts), 1)
        self.assertEqual(id_facts[0]["key"], "user_name")

        all_facts = db.get_all_facts(db_path=self.db_path)
        self.assertEqual(len(all_facts), 2)

        # Delete
        deleted = db.delete_fact("identity", "user_name", db_path=self.db_path)
        self.assertTrue(deleted)
        self.assertIsNone(db.get_fact("identity", "user_name", db_path=self.db_path))

    def test_sessions_fifo_and_pruning(self):
        s1 = db.add_session("Summary 1", language="es", max_keep=2, db_path=self.db_path)
        s2 = db.add_session("Summary 2", language="es", max_keep=2, db_path=self.db_path)
        s3 = db.add_session("Summary 3", language="es", max_keep=2, db_path=self.db_path)

        sessions = db.list_sessions(db_path=self.db_path)
        # Because max_keep=2, s1 must have been pruned
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["summary"], "Summary 3")
        self.assertEqual(sessions[1]["summary"], "Summary 2")

        # Pop last session (consumes the most recent)
        popped = db.pop_last_session(db_path=self.db_path)
        self.assertIsNotNone(popped)
        self.assertEqual(popped["summary"], "Summary 3")

        remaining = db.list_sessions(db_path=self.db_path)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["summary"], "Summary 2")

    def test_whatsapp_contacts_crud(self):
        db.save_whatsapp_contact(
            "account1", "hermana", "12345@c.us", "María", db_path=self.db_path
        )
        c = db.get_whatsapp_contact("account1", "hermana", db_path=self.db_path)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "12345@c.us")
        self.assertEqual(c["name"], "María")

        # Conflict check: replacing ID without deleting must raise ValueError
        with self.assertRaises(ValueError):
            db.save_whatsapp_contact(
                "account1", "hermana", "99999@c.us", "Otra", db_path=self.db_path
            )

        # Delete and recreate
        self.assertTrue(db.delete_whatsapp_contact("account1", "hermana", db_path=self.db_path))
        self.assertIsNone(db.get_whatsapp_contact("account1", "hermana", db_path=self.db_path))

        db.save_whatsapp_contact(
            "account1", "hermana", "99999@c.us", "Otra", db_path=self.db_path
        )
        c2 = db.get_whatsapp_contact("account1", "hermana", db_path=self.db_path)
        self.assertEqual(c2["id"], "99999@c.us")

    def test_tasks_crud(self):
        t1 = db.add_task("Revisar SQLite", db_path=self.db_path)
        t2 = db.add_task("Configurar audio", db_path=self.db_path)

        tasks = db.list_tasks(include_done=False, db_path=self.db_path)
        self.assertEqual(len(tasks), 2)

        # Complete
        self.assertTrue(db.complete_task(t1, db_path=self.db_path))
        pending = db.list_tasks(include_done=False, db_path=self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], t2)

        all_tasks = db.list_tasks(include_done=True, db_path=self.db_path)
        self.assertEqual(len(all_tasks), 2)

        # Delete
        self.assertTrue(db.delete_task(t2, db_path=self.db_path))
        self.assertEqual(len(db.list_tasks(include_done=True, db_path=self.db_path)), 1)

    def test_audit_log_tracks_operations(self):
        db.set_fact("notes", "topic", "AI", db_path=self.db_path)
        db.set_fact("notes", "topic", "AGI", db_path=self.db_path)
        db.delete_fact("notes", "topic", db_path=self.db_path)

        logs = db.get_audit_log(limit=10, db_path=self.db_path)
        # Should contain INSERT, UPDATE, and DELETE in reverse order
        actions = [log["action"] for log in logs]
        self.assertIn("DELETE", actions)
        self.assertIn("UPDATE", actions)
        self.assertIn("INSERT", actions)


if __name__ == "__main__":
    unittest.main()
