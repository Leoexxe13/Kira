import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from google.genai import types
from main import JarvisLive
from collections import deque

class TestOperationalContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_ui = MagicMock()
        self.mock_ui.muted = False
        self.mock_session = AsyncMock()
        
        self.patcher_wake = patch('main.WakeWordDetector', autospec=True)
        self.patcher_wake.start()
        
        self.jarvis = JarvisLive(self.mock_ui)
        self.jarvis.session = self.mock_session
        self.jarvis._loop = MagicMock()

    def tearDown(self):
        self.patcher_wake.stop()

    def _make_fc(self, name, args):
        fc = MagicMock()
        fc.name = name
        fc.args = args
        fc.id = "test-call-id"
        return fc

    @patch('main.search_memory')
    async def test_successful_tool_updates_context(self, mock_search):
        # We simulate a successful file list or search. For simplicity we mock the execution of a tool.
        # But wait, we can just call _execute_tool directly.
        # We need a tool that returns a valid string. We'll mock the registry.
        self.jarvis._action_registry = MagicMock()
        self.jarvis._action_registry.has.return_value = True
        self.jarvis._action_registry.run.return_value = "['informe.pdf']"

        fc = self._make_fc("file_controller", {"action": "list", "path": "/Downloads"})
        response = await self.jarvis._execute_tool(fc)

        # Ensure the response has the operational context
        self.assertIn("operational_context", response.response)
        op_ctx = response.response["operational_context"]
        self.assertEqual(len(op_ctx), 1)
        self.assertIn("informe.pdf", op_ctx[0])
        self.assertIn("/Downloads", op_ctx[0])
        self.assertIn("file_controller", op_ctx[0])

    async def test_failed_tool_does_not_replace_context(self):
        # Seed a valid context
        self.jarvis._op_context = deque(maxlen=5)
        self.jarvis._op_context.append("Valid Context: previous file")

        # Mock a failure
        self.jarvis._action_registry = MagicMock()
        self.jarvis._action_registry.has.return_value = True
        self.jarvis._action_registry.run.side_effect = Exception("Crash")

        fc = self._make_fc("file_controller", {"action": "list", "path": "/Invalid"})
        response = await self.jarvis._execute_tool(fc)

        # Check it didn't overwrite the valid context with the error
        op_ctx = response.response.get("operational_context", [])
        self.assertEqual(len(op_ctx), 1)
        self.assertEqual(op_ctx[0], "Valid Context: previous file")

    async def test_multiple_resources_available(self):
        self.jarvis._action_registry = MagicMock()
        self.jarvis._action_registry.has.return_value = True
        
        # Tool 1
        self.jarvis._action_registry.run.return_value = "['file1.txt']"
        fc1 = self._make_fc("file_controller", {"action": "list", "path": "/Dir1"})
        await self.jarvis._execute_tool(fc1)

        # Tool 2
        self.jarvis._action_registry.run.return_value = "Found Python.org"
        fc2 = self._make_fc("web_search", {"query": "python"})
        response2 = await self.jarvis._execute_tool(fc2)

        op_ctx = response2.response.get("operational_context", [])
        self.assertEqual(len(op_ctx), 2)
        self.assertIn("file1.txt", op_ctx[0])
        self.assertIn("Python.org", op_ctx[1])

    async def test_gemini_receives_context_in_text_command(self):
        self.jarvis._op_context = deque(maxlen=5)
        self.jarvis._op_context.append("Tool: search | Args: {'query': 'docs'} | Result: 'docs.pdf'")
        
        self.jarvis._on_text_command("Open it")
        
        call_args = self.mock_session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertIn("[OPERATIONAL CONTEXT]", call_args)
        self.assertIn("docs.pdf", call_args)

    async def test_no_sqlite_writes(self):
        # Ensure we don't save operational context to db.
        # This is proven by the fact that _execute_tool only updates self._op_context (an in-memory deque).
        # We can assert that db module is not called when updating op_context.
        with patch('memory.db.set_fact') as mock_set_fact:
            self.jarvis._action_registry = MagicMock()
            self.jarvis._action_registry.has.return_value = True
            self.jarvis._action_registry.run.return_value = "['informe.pdf']"

            fc = self._make_fc("file_controller", {"action": "list", "path": "/Downloads"})
            await self.jarvis._execute_tool(fc)

            mock_set_fact.assert_not_called()

if __name__ == '__main__':
    unittest.main()
