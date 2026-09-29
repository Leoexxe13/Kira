import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
import sys

# Create the QApplication instance before importing UI to avoid Qt errors
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from ui import CapabilitiesOverlay, MainWindow

class TestCapabilitiesGUI(unittest.TestCase):
    def setUp(self):
        self.mock_submit = MagicMock()
        self.capabilities = [
            {
                "name": "test_safe_tool",
                "description": "Una herramienta segura",
                "parameters": {
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "test_sensitive_tool",
                "description": "Herramienta destructiva",
                "parameters": {
                    "properties": {
                        "target": {"type": "STRING", "description": "Objetivo"}
                    },
                    "required": ["target"]
                }
            },
            {
                # Invalid or incomplete tool to test robustness
                "name": "broken_tool"
            }
        ]
        self.overlay = CapabilitiesOverlay(self.capabilities, self.mock_submit)
        self.overlay.show()

    def test_automatic_discovery_rendering(self):
        # Check if rows were generated correctly
        self.assertEqual(len(self.overlay.rows), 3)
        self.assertEqual(self.overlay.rows[0][1], "test_safe_tool")
        self.assertEqual(self.overlay.rows[1][1], "test_sensitive_tool")
        self.assertEqual(self.overlay.rows[2][1], "broken_tool")

    def test_search_filtering(self):
        # Simulate typing in the search box
        self.overlay.search_input.setText("destructiva")
        # test_safe_tool should be hidden
        self.assertFalse(self.overlay.rows[0][0].isVisible())
        # test_sensitive_tool should be visible
        self.assertTrue(self.overlay.rows[1][0].isVisible())

    @patch('PyQt6.QtWidgets.QInputDialog.getText')
    def test_use_flow_required_params(self, mock_input):
        # Simulate the user entering "test_target"
        mock_input.return_value = ("test_target", True)
        
        # Trigger "Usar" on test_sensitive_tool which has required param "target"
        self.overlay._use_capability("test_sensitive_tool", ["target"])
        
        # Verify it prompted the user
        mock_input.assert_called_once()
        
        # Verify it submitted the intent
        self.mock_submit.assert_called_with("Usa la capacidad test_sensitive_tool con target='test_target'")

    @patch('PyQt6.QtWidgets.QInputDialog.getText')
    def test_use_flow_cancelled(self, mock_input):
        # Simulate user cancelling the dialog
        mock_input.return_value = ("", False)
        
        self.overlay._use_capability("test_sensitive_tool", ["target"])
        
        # Intent should not be submitted
        self.mock_submit.assert_not_called()

    def test_test_capability_flow(self):
        # Trigger "Probar" on test_safe_tool
        self.overlay._test_capability("test_safe_tool")
        self.mock_submit.assert_called_with("Prueba la capacidad test_safe_tool con valores seguros de demostración")

    def test_invalid_tool_does_not_break(self):
        # broken_tool has no description or parameters
        self.overlay._use_capability("broken_tool", [])
        self.mock_submit.assert_called_with("Usa la capacidad broken_tool")

if __name__ == '__main__':
    unittest.main()
