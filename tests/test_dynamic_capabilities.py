import unittest
from unittest.mock import MagicMock, patch
from main import JarvisLive

class TestDynamicCapabilities(unittest.TestCase):
    def setUp(self):
        self.mock_ui = MagicMock()
        self.patcher_wake = patch('main.WakeWordDetector', autospec=True)
        self.patcher_wake.start()
        
        self.jarvis = JarvisLive(self.mock_ui)

    def tearDown(self):
        self.patcher_wake.stop()

    def test_build_capabilities_registry_format(self):
        # Mock some tool declarations
        self.jarvis._action_registry = MagicMock()
        self.jarvis._action_registry.get_tool_declarations.return_value = [
            {
                "name": "test_tool_1",
                "description": "Herramienta de prueba semantica",
                "parameters": {
                    "properties": {
                        "param_a": {"type": "STRING", "description": "Un parametro"},
                        "param_b": {"type": "INTEGER", "description": "Otro parametro"}
                    },
                    "required": ["param_a"]
                }
            }
        ]
        self.jarvis._plugin_registry = MagicMock()
        self.jarvis._plugin_registry.get_tool_declarations.return_value = []

        registry_text = self.jarvis._build_capabilities_registry()
        
        # Test headers
        self.assertIn("[REGISTRO SEMÁNTICO DINÁMICO DE CAPACIDADES]", registry_text)
        
        # Test tool inclusion
        self.assertIn("### Tool: test_tool_1", registry_text)
        self.assertIn("Descripción: Herramienta de prueba semantica", registry_text)
        
        # Test params and required tags
        self.assertIn("param_a (STRING) [OBLIGATORIO]: Un parametro", registry_text)
        self.assertIn("param_b (INTEGER) [OPCIONAL]: Otro parametro", registry_text)

    def test_build_config_injects_capabilities(self):
        with patch.object(self.jarvis, '_build_capabilities_registry') as mock_build:
            mock_build.return_value = "DUMMY_CAPABILITIES_BLOCK"
            config = self.jarvis._build_config()
            
            # The config's system instruction should contain the dummy block
            self.assertIn("DUMMY_CAPABILITIES_BLOCK", config.system_instruction)

    def test_tool_without_parameters_handles_gracefully(self):
        self.jarvis._action_registry = MagicMock()
        self.jarvis._action_registry.get_tool_declarations.return_value = [
            {
                "name": "empty_tool",
                "description": "No params",
                "parameters": {}
            }
        ]
        self.jarvis._plugin_registry = MagicMock()
        self.jarvis._plugin_registry.get_tool_declarations.return_value = []

        registry_text = self.jarvis._build_capabilities_registry()
        self.assertIn("### Tool: empty_tool", registry_text)
        self.assertIn("Parámetros: Ninguno", registry_text)

if __name__ == '__main__':
    unittest.main()
