from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from memory.sqlite_memory import MAX_STATE_BYTES, MemoryStore


class MemoryMigrationTests(unittest.TestCase):
    def test_legacy_import_is_backed_up_idempotent_and_preserves_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "long_term.json"
            legacy.write_text(json.dumps({
                "preferences": {
                    "favorite_language": {"value": "Python", "updated": "2026-01-01"},
                    "coffee": "con azúcar",
                },
                "sessions": [{"summary": "legacy"}],
            }), encoding="utf-8")
            store = MemoryStore(root / "kira.db", principal="mac:leo")
            store.remember("preferences", "favorite_language", "Rust", source="user_explicit")
            first = store.migrate_legacy_json(legacy)
            second = store.migrate_legacy_json(legacy)
            self.assertEqual(first["state"], "imported")
            self.assertEqual(first["imported"], 1)
            self.assertGreaterEqual(first["skipped"], 2)
            self.assertTrue(Path(first["backup"]).exists())
            self.assertEqual(second["state"], "already_imported")
            self.assertEqual(store.recall("language")[0].content, "Rust")
            self.assertEqual(store.recall("coffee")[0].content, "con azúcar")

    def test_updates_and_forget_keep_history(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(Path(td) / "kira.db", principal="mac:leo")
            store.remember("preferences", "language", "Python")
            store.remember("preferences", "language", "Rust", source="user_correction")
            self.assertTrue(store.forget("preferences", "language"))
            history = store.history("preferences", "language")
            self.assertEqual([row["status"] for row in history], ["retracted", "superseded"])
            self.assertEqual({row["content"] for row in history}, {"Python", "Rust"})

    def test_schema_is_versioned_and_wal_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "kira.db"
            MemoryStore(path, principal="mac:leo")
            with sqlite3.connect(path) as db:
                self.assertGreaterEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

    def test_extended_secret_families_are_rejected(self):
        secrets = [
            "Bearer abcdefghijklmnopqrstuvwxyz012345",
            "eyJabcdefghijk.abcdefghijklmnop.abcdefghijklmnop",
            "-----BEGIN PRIVATE KEY-----\nabc",
            "AKIA1234567890ABCDEF",
            "https://leo:password@example.com/data",
        ]
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(Path(td) / "kira.db", principal="mac:leo")
            for index, secret in enumerate(secrets):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    store.remember("notes", f"note_{index}", secret)

    def test_session_state_is_bounded_and_redacted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "kira.db"
            store = MemoryStore(path, principal="mac:leo")
            store.save_state("desktop", {
                "last_user_goal": "x" * (MAX_STATE_BYTES * 2),
                "last_tool_result": {"text": "Bearer abcdefghijklmnopqrstuvwxyz012345", "data": ["y" * 8000] * 100},
            })
            with sqlite3.connect(path) as db:
                payload = db.execute("SELECT state FROM session_state").fetchone()[0]
            self.assertLessEqual(len(payload.encode("utf-8")), MAX_STATE_BYTES + 2048)
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", payload)


if __name__ == "__main__":
    unittest.main()
