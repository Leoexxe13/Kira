import unittest
from unittest.mock import MagicMock
from ui import MainWindow

class TestGoalsDeliverables(unittest.TestCase):
    def setUp(self):
        # We test the GUI components for Progress and Deliverables
        self.app = __import__('PyQt6.QtWidgets').QtWidgets.QApplication.instance()
        if not self.app:
            self.app = __import__('PyQt6.QtWidgets').QtWidgets.QApplication([])
        self.win = MainWindow(face_path=None)

    def test_progress_signal(self):
        # Trigger progress signal and check UI state
        self.win._progress_sig.emit("Investigando fuentes...")
        self.assertEqual(self.win._kira_context_badge.text(), "PROGRESO")
        self.assertEqual(self.win._kira_context_text.text(), "● Investigando fuentes...")

    def test_deliverable_signal(self):
        # Verify the deliverable card is inserted as a widget
        count_before = self.win._log._lay.count()
        self.win._deliverable_sig.emit("Proyecto Jarvis", "Presentación de 10 slides", "/tmp/jarvis.pptx")
        
        # In PyQt, signals might need the event loop to process
        self.app.processEvents()
        count_after = self.win._log._lay.count()
        self.assertGreater(count_after, count_before)

    def test_system_prompt_personality(self):
        import os
        prompt_path = "core/prompt.txt"
        if os.path.exists(prompt_path):
            with open(prompt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertIn("PERSONALITY INTENSITY", content)
            self.assertIn("SARCASM RULES", content)
            self.assertIn("DELIVERABLES", content)

if __name__ == '__main__':
    unittest.main()
