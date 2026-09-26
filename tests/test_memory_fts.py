import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory import db
from memory import memory_manager as mm


class TestMemoryFTS5(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_fts_memory.db"
        db.init_db(self.db_path)

        # Mock memory_manager to use this isolated temp database
        self.orig_mem_path = mm.MEMORY_PATH
        mm.MEMORY_PATH = Path(self.temp_dir.name) / "long_term.json"

    def tearDown(self):
        mm.MEMORY_PATH = self.orig_mem_path
        self.temp_dir.cleanup()

    def test_fts5_detection(self):
        # Must detect whether FTS5 is available
        available = db.is_fts5_available()
        self.assertIsInstance(available, bool)
        self.assertTrue(available, "SQLite on this Mac should support FTS5")

    def test_exact_and_partial_search_across_fields(self):
        # Insert facts across different categories
        db.set_fact("preferences", "editor", "Visual Studio Code with Python", db_path=self.db_path)
        db.set_fact("projects", "kira_voice", "Asistente inteligente con síntesis de voz", db_path=self.db_path)
        db.set_fact("identity", "residence", "Madrid, España", db_path=self.db_path)

        # 1. Exact match on value
        results = db.search_memory_fts("Madrid", limit=5, db_path=self.db_path)
        self.assertTrue(any("Madrid" in r["value"] for r in results))

        # 2. Match on key
        results_key = db.search_memory_fts("editor", limit=5, db_path=self.db_path)
        self.assertTrue(any(r["key"] == "editor" for r in results_key))

        # 3. Match on category
        results_cat = db.search_memory_fts("projects", limit=5, db_path=self.db_path)
        self.assertTrue(any(r["category"] == "projects" for r in results_cat))

        # 4. Prefix / partial word matching
        results_prefix = db.search_memory_fts("sint", limit=5, db_path=self.db_path)
        self.assertTrue(any("síntesis" in r["value"] or "sint" in r["value"].lower() for r in results_prefix))

    def test_bm25_ranking_priority(self):
        # Direct key match should rank higher than incidental word match in value
        db.set_fact("notes", "python_tips", "Tips for general coding", db_path=self.db_path)
        db.set_fact("notes", "random_note", "I once used python for a quick script", db_path=self.db_path)

        results = db.search_memory_fts("python", limit=5, db_path=self.db_path)
        self.assertGreaterEqual(len(results), 2)
        # First result should be the one where python is in the key
        self.assertEqual(results[0]["key"], "python_tips")

    def test_search_combines_facts_and_sessions(self):
        db.set_fact("preferences", "food", "Pizza napolitana artesanal", db_path=self.db_path)
        db.add_session("Hablamos sobre cenar pizza y programar en python", language="es", db_path=self.db_path)

        results = db.search_memory_fts("pizza", limit=5, db_path=self.db_path)
        sources = {r["source"] for r in results}
        self.assertIn("fact", sources)
        self.assertIn("session", sources)

    def test_limit_parameter_respected(self):
        for i in range(10):
            db.set_fact("notes", f"item_{i}", f"Important task number {i}", db_path=self.db_path)

        results = db.search_memory_fts("Important", limit=3, db_path=self.db_path)
        self.assertEqual(len(results), 3)

        results_more = db.search_memory_fts("Important", limit=7, db_path=self.db_path)
        self.assertEqual(len(results_more), 7)

    def test_fts_index_synchronization_on_update_and_delete(self):
        # 1. Insert
        db.set_fact("notes", "secret_pass", "AlphaBravoDelta", db_path=self.db_path)
        res = db.search_memory_fts("AlphaBravoDelta", limit=5, db_path=self.db_path)
        self.assertEqual(len(res), 1)

        # 2. Update
        db.set_fact("notes", "secret_pass", "EchoFoxtrotGolf", db_path=self.db_path)
        old_res = db.search_memory_fts("AlphaBravoDelta", limit=5, db_path=self.db_path)
        self.assertEqual(len(old_res), 0)
        new_res = db.search_memory_fts("EchoFoxtrotGolf", limit=5, db_path=self.db_path)
        self.assertEqual(len(new_res), 1)

        # 3. Delete
        db.delete_fact("notes", "secret_pass", db_path=self.db_path)
        deleted_res = db.search_memory_fts("EchoFoxtrotGolf", limit=5, db_path=self.db_path)
        self.assertEqual(len(deleted_res), 0)

    def test_session_fts_synchronization_on_pop(self):
        db.add_session("Sesión efímera de prueba con keyword_test_xyz", db_path=self.db_path)
        res = db.search_memory_fts("keyword_test_xyz", limit=5, db_path=self.db_path)
        self.assertEqual(len(res), 1)

        # Pop session removes it
        popped = db.pop_last_session(db_path=self.db_path)
        self.assertIsNotNone(popped)

        res_after = db.search_memory_fts("keyword_test_xyz", limit=5, db_path=self.db_path)
        self.assertEqual(len(res_after), 0)

    def test_public_search_memory_integration(self):
        mm.remember("language", "Python y TypeScript", category="preferences")
        mm.save_session_summary("El usuario preguntó sobre Python para backend")

        # search_memory must return formatted string
        out = mm.search_memory("Python", limit=5)
        self.assertIsInstance(out, str)
        self.assertTrue(out.startswith("Stored facts matching 'Python':"))
        self.assertIn("preferences/language: Python y TypeScript", out)
        self.assertIn("session/", out)

    def test_fallback_when_fts5_is_disabled(self):
        mm.remember("city", "Caracas", category="identity")

        # Force is_fts5_available to return False to test fallback
        with patch.object(db, "is_fts5_available", return_value=False):
            fallback_out = mm.search_memory("Caracas", limit=5)
            self.assertIsInstance(fallback_out, str)
            self.assertIn("Caracas", fallback_out)
            self.assertTrue(fallback_out.startswith("Stored facts matching 'Caracas':"))

    def test_empty_query_returns_everything_header(self):
        mm.remember("hobby", "Ajedrez", category="preferences")
        out = mm.search_memory("")
        self.assertIn("Everything currently stored:", out)
        self.assertIn("Ajedrez", out)


if __name__ == "__main__":
    unittest.main()
