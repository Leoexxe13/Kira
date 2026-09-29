import unittest
import asyncio
from unittest.mock import MagicMock, patch
from google.genai import types
from main import JarvisLive

class TestAutomaticMemory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_ui = MagicMock()
        self.mock_ui.muted = False
        self.mock_session = MagicMock()
        
        self.patcher_wake = patch('main.WakeWordDetector', autospec=True)
        self.patcher_wake.start()
        
        self.jarvis = JarvisLive(self.mock_ui)
        self.jarvis.session = self.mock_session

    def tearDown(self):
        self.patcher_wake.stop()

    def _make_fc(self, name, args):
        fc = MagicMock()
        fc.name = name
        fc.args = args
        fc.id = "test-call-id"
        return fc

    @patch('main.update_memory')
    async def test_save_stable_fact(self, mock_update_memory):
        fc = self._make_fc("save_memory", {"category": "identity", "key": "name", "value": "Leo"})
        response = await self.jarvis._execute_tool(fc)
        
        mock_update_memory.assert_called_once_with({"identity": {"name": {"value": "Leo"}}})
        self.assertEqual(response.response["result"], "ok")

    @patch('main.update_memory')
    async def test_update_existing_fact(self, mock_update_memory):
        fc = self._make_fc("save_memory", {"category": "preferences", "key": "food", "value": "Sushi instead of Pizza"})
        response = await self.jarvis._execute_tool(fc)
        
        mock_update_memory.assert_called_once_with({"preferences": {"food": {"value": "Sushi instead of Pizza"}}})
        self.assertEqual(response.response["result"], "ok")

    @patch('memory.memory_manager.forget_memory')
    async def test_delete_existing_mechanism(self, mock_forget):
        fc = self._make_fc("forget_memory", {"category": "notes", "key": "temporary_plan"})
        response = await self.jarvis._execute_tool(fc)
        
        mock_forget.assert_called_once_with("temporary_plan", "notes")
        self.assertEqual(response.response["result"], "ok")

    @patch('main.update_memory')
    async def test_memory_failure_safe(self, mock_update_memory):
        mock_update_memory.side_effect = Exception("SQLite locked")
        fc = self._make_fc("save_memory", {"category": "notes", "key": "fail", "value": "test"})
        
        # execution should catch or propagate, but wait, _execute_tool currently
        # doesn't try/except the body for save_memory. It uses a try/except further down.
        # Actually save_memory has no try/except in _execute_tool, let's see what it does.
        # It's better to ensure it doesn't crash the conversation.
        try:
            response = await self.jarvis._execute_tool(fc)
        except Exception as e:
            self.fail(f"_execute_tool raised an exception: {e}")

    def test_prompt_rules(self):
        # Verify the prompt has the rules preventing duplicates, secrets, trivial info
        tools = self.jarvis._build_config().tools[0].function_declarations
        save_tool = next((t for t in tools if getattr(t, 'name', '') == "save_memory"), None)
        self.assertIsNotNone(save_tool)
        desc = getattr(save_tool, 'description', '')
        self.assertIn("Do NOT save passwords", desc)
        self.assertIn("Do NOT save greetings", desc)
        self.assertIn("Avoid duplicates", desc)
        
        forget_tool = next((t for t in tools if getattr(t, 'name', '') == "forget_memory"), None)
        self.assertIsNotNone(forget_tool)

if __name__ == '__main__':
    unittest.main()
