import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from ui import MainWindow


class BlackOpsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_and_header_are_compact(self):
        window = MainWindow("face.png")
        self.addCleanup(window.close)
        self.assertEqual(
            [button.text() for button in window._kira_nav_buttons.values()],
            ["HOME", "CHAT", "TASKS", "SYSTEM", "TOOLS", "HUB", "SETTINGS"],
        )
        self.assertFalse(window._title_lbl.isVisible())
        self.assertFalse(window._sub_lbl.isVisible())
        self.assertEqual(window._workspace_stack.currentIndex(), 0)
        self.assertNotEqual(window._workspace_stack.widget(5), window._chat_page)
        self.assertNotEqual(window._workspace_stack.widget(6), window._home_page)

    def test_tools_and_knowledge_have_dedicated_pages_and_settings_opens(self):
        window = MainWindow("face.png")
        self.addCleanup(window.close)
        window._switch_workspace("tools")
        self.assertEqual(window._workspace_stack.currentIndex(), 5)
        window._switch_workspace("knowledge")
        self.assertEqual(window._workspace_stack.currentIndex(), 6)
        window._switch_workspace("settings")
        self.assertTrue(window._drawer_btn.isChecked())
        self.assertEqual(window._workspace_stack.currentIndex(), 7)

    def test_home_keeps_real_task_and_system_panels(self):
        window = MainWindow("face.png")
        self.addCleanup(window.close)
        self.assertTrue(window._home_pending_preview.isVisible() or not window.isVisible())
        self.assertTrue(window._home_cpu.parentWidget() is not None)
        window._update_kira_page_metrics()
        self.assertNotEqual(window._home_cpu.text(), "")

    def test_home_activity_and_conversation_are_separated(self):
        window = MainWindow("face.png")
        self.addCleanup(window.close)
        window._home_activity_append("SYS: herramienta lista")
        window._home_activity_append("KIRA: respuesta")
        window._home_conversation_append("SYS: no debe aparecer")
        window._home_conversation_append("KIRA: respuesta")
        self.assertIn("herramienta", window._home_activity.toPlainText())
        self.assertNotIn("respuesta", window._home_activity.toPlainText())
        self.assertIn("respuesta", window._home_conversation.toPlainText())
        self.assertNotIn("herramienta", window._home_conversation.toPlainText())

    def test_knowledge_clear_removes_active_context(self):
        window = MainWindow("face.png")
        self.addCleanup(window.close)
        window._current_file = "/tmp/documento.txt"
        window._knowledge_clear()
        self.assertIsNone(window._current_file)
        self.assertIn("No hay un archivo", window._knowledge_recent.toPlainText())


if __name__ == "__main__":
    unittest.main()
