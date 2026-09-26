import inspect
import tempfile
import unittest
from pathlib import Path

from memory import memory_manager as mm
from memory import db


class TestMemoryManagerCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        # Mock MEMORY_PATH to a temporary location so tests do not pollute real data
        self.original_mem_path = mm.MEMORY_PATH
        mm.MEMORY_PATH = self.temp_path / "long_term.json"

    def tearDown(self):
        mm.MEMORY_PATH = self.original_mem_path
        self.temp_dir.cleanup()

    def test_public_function_signatures_exist(self):
        expected_signatures = {
            "load_memory": 0,
            "save_memory": 1,
            "update_memory": 1,
            "format_memory_for_prompt": 1,
            "search_memory": 1,  # query, limit=8
            "all_entries_for_ui": 0,
            "remember": 2,  # key, value, category='notes'
            "forget": 1,    # key, category='notes'
            "forget_memory": 1,
            "set_trim_notifier": 1,
            "save_session_summary": 1,  # summary, language=''
            "pop_last_session": 0,
            "get_whatsapp_contact": 2,
            "remember_whatsapp_contact": 4,
            "forget_whatsapp_contact": 2,
        }

        for func_name, min_args in expected_signatures.items():
            self.assertTrue(hasattr(mm, func_name), f"Missing public function: {func_name}")
            sig = inspect.signature(getattr(mm, func_name))
            req_params = [
                p for p in sig.parameters.values()
                if p.default == inspect.Parameter.empty
            ]
            self.assertEqual(
                len(req_params), min_args,
                f"Signature mismatch on {func_name}: expected {min_args} required params, got {len(req_params)}"
            )

    def test_facts_crud_flow_in_memory_manager(self):
        # 1. Update memory
        res = mm.update_memory({
            "identity": {"name": {"value": "Tony"}},
            "preferences": {"os": {"value": "macOS"}},
        })
        self.assertIn("identity", res)
        self.assertEqual(res["identity"]["name"]["value"], "Tony")
        self.assertEqual(res["preferences"]["os"]["value"], "macOS")

        # 2. Remember
        rem_msg = mm.remember("project_x", "building jarvis", category="projects")
        self.assertIn("Remembered:", rem_msg)

        # 3. Load memory
        loaded = mm.load_memory()
        self.assertEqual(loaded["identity"]["name"]["value"], "Tony")
        self.assertEqual(loaded["projects"]["project_x"]["value"], "building jarvis")

        # 4. Search memory
        search_res = mm.search_memory("Tony")
        self.assertIn("Tony", search_res)

        # 5. UI entries
        ui_rows = mm.all_entries_for_ui()
        self.assertIsInstance(ui_rows, list)
        keys = {r["key"] for r in ui_rows}
        self.assertIn("name", keys)
        self.assertIn("project_x", keys)

        # 6. Forget
        forget_msg = mm.forget("project_x", category="projects")
        self.assertIn("Forgotten:", forget_msg)
        self.assertNotIn("project_x", mm.load_memory()["projects"])

    def test_session_summary_and_pop(self):
        mm.save_session_summary("Discussed coding and architecture", language="Spanish")
        mm.save_session_summary("Second brief check-in", language="Spanish")

        loaded = mm.load_memory()
        self.assertIn("sessions", loaded)
        self.assertEqual(len(loaded["sessions"]), 2)

        # Pop session
        popped = mm.pop_last_session()
        self.assertIsNotNone(popped)
        self.assertEqual(popped["summary"], "Second brief check-in")

        loaded_after = mm.load_memory()
        self.assertEqual(len(loaded_after["sessions"]), 1)
        self.assertEqual(loaded_after["sessions"][0]["summary"], "Discussed coding and architecture")

    def test_format_memory_for_prompt(self):
        mm.update_memory({
            "identity": {"name": {"value": "Tony"}, "language": {"value": "Spanish"}},
            "preferences": {"music": {"value": "Rock"}},
        })
        mem = mm.load_memory()
        prompt_block = mm.format_memory_for_prompt(mem)
        self.assertIn("[WHAT YOU KNOW ABOUT THIS PERSON", prompt_block)
        self.assertIn("Name: Tony", prompt_block)
        self.assertIn("Rock", prompt_block)


if __name__ == "__main__":
    unittest.main()
