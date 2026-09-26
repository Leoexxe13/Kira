import inspect
import tempfile
import unittest
from pathlib import Path

from actions import kira_tasks as kt
from memory import db


class TestKiraTasksSQLite(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.orig_task_file = kt.TASK_FILE
        kt.TASK_FILE = self.temp_path / "kira_tasks.json"

    def tearDown(self):
        kt.TASK_FILE = self.orig_task_file
        self.temp_dir.cleanup()

    def test_signature_and_tool_declaration(self):
        sig = inspect.signature(kt.kira_tasks)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["parameters", "response", "player", "session_memory"])

        self.assertIn("name", kt.TOOL)
        self.assertEqual(kt.TOOL["name"], "kira_tasks")
        self.assertIn("handler", kt.TOOL)
        self.assertEqual(kt.TOOL["handler"], kt.kira_tasks)

    def test_tasks_lifecycle_via_sqlite(self):
        # 1. Empty list
        res = kt.kira_tasks({"action": "list"})
        self.assertEqual(res, "No tienes pendientes registrados.")

        # 2. Add task 1
        res = kt.kira_tasks({"action": "add", "text": "Comprar café"})
        self.assertIn("Listo. Guardé como pendiente: Comprar café", res)

        # 3. Add task 2
        res = kt.kira_tasks({"action": "add", "text": "Llamar al médico"})
        self.assertIn("Listo. Guardé como pendiente: Llamar al médico", res)

        # 4. List pending
        res = kt.kira_tasks({"action": "list"})
        self.assertIn("1. Comprar café", res)
        self.assertIn("2. Llamar al médico", res)

        # 5. Complete task 1 by text
        res = kt.kira_tasks({"action": "complete", "text": "café"})
        self.assertIn("Marcado como hecho: Comprar café", res)

        # 6. List again (only pending should show)
        res = kt.kira_tasks({"action": "list"})
        self.assertNotIn("Comprar café", res)
        self.assertIn("Llamar al médico", res)

        # 7. Delete task 2 by ID
        # The id in db for task 2 is 2
        res = kt.kira_tasks({"action": "delete", "id": 2})
        self.assertIn("Eliminé el pendiente: Llamar al médico", res)

        # 8. List should be empty now
        res = kt.kira_tasks({"action": "list"})
        self.assertEqual(res, "No tienes pendientes registrados.")

    def test_error_handling(self):
        # Add without text
        res = kt.kira_tasks({"action": "add", "text": ""})
        self.assertIn("Necesito saber qué pendiente quieres guardar.", res)

        # Complete without match
        res = kt.kira_tasks({"action": "complete", "id": 999})
        self.assertIn("No encontré ese pendiente.", res)

        # Invalid action
        res = kt.kira_tasks({"action": "unknown"})
        self.assertEqual(res, "Acción de pendientes no reconocida.")


if __name__ == "__main__":
    unittest.main()
