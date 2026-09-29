import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from google.genai import types
from main import JarvisLive

class TestMultiStepTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_ui = MagicMock()
        self.mock_ui.muted = False
        self.mock_session = AsyncMock()
        
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

    def _make_tool_call_response(self, function_calls):
        response = MagicMock()
        response.tool_call = MagicMock()
        response.tool_call.function_calls = function_calls
        return response

    async def _simulate_receive_audio_step(self, response):
        # We simulate the inner logic of `_receive_audio` tool loop
        if response.tool_call:
            self.jarvis._tool_call_count += 1
            if self.jarvis._tool_call_count > 5:
                fn_responses = []
                for fc in response.tool_call.function_calls:
                    fn_responses.append(types.FunctionResponse(
                        id=fc.id, name=fc.name,
                        response={"result": "Error: Maximum tool execution limit (5) reached for this turn."}
                    ))
                return fn_responses

            fn_responses = []
            for fc in response.tool_call.function_calls:
                call_sig = (fc.name, str(dict(fc.args or {})))
                if call_sig == self.jarvis._last_tool_call:
                    fn_responses.append(types.FunctionResponse(
                        id=fc.id, name=fc.name,
                        response={"result": "Error: You just called this tool with the exact same arguments. Please try a different approach."}
                    ))
                    continue
                self.jarvis._last_tool_call = call_sig

                fr = await self.jarvis._execute_tool(fc)
                fn_responses.append(fr)
            return fn_responses

    @patch('main.search_memory')
    async def test_sequence_of_2_tools(self, mock_search):
        # Step 1: recall_memory
        mock_search.return_value = "Memory result"
        fc1 = self._make_fc("recall_memory", {"query": "test"})
        res1 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc1]))
        
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0].response["result"], "Memory result")
        
        # Step 2: save_memory (depends on the first)
        with patch('main.update_memory') as mock_update:
            fc2 = self._make_fc("save_memory", {"category": "notes", "key": "test", "value": "Memory result"})
            res2 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc2]))
            
            self.assertEqual(len(res2), 1)
            self.assertEqual(res2[0].response["result"], "ok")
            mock_update.assert_called_once()
            
        self.assertEqual(self.jarvis._tool_call_count, 2)

    async def test_duplicate_loop_detection(self):
        fc1 = self._make_fc("system_status", {})
        res1 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc1]))
        self.assertNotRegex(str(res1[0].response["result"]), "Error.*exact same arguments")

        fc2 = self._make_fc("system_status", {})
        res2 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc2]))
        self.assertRegex(str(res2[0].response["result"]), "Error: You just called this tool with the exact same arguments")

    async def test_maximum_step_limit(self):
        for i in range(1, 6):
            fc = self._make_fc("recall_memory", {"query": f"test_{i}"})
            res = await self._simulate_receive_audio_step(self._make_tool_call_response([fc]))
            self.assertNotRegex(str(res[0].response["result"]), "Maximum tool execution limit")
            
        # 6th should fail
        fc6 = self._make_fc("recall_memory", {"query": "test_6"})
        res6 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc6]))
        self.assertRegex(str(res6[0].response["result"]), "Error: Maximum tool execution limit \\(5\\) reached")

    @patch('main.search_memory')
    async def test_recovery_after_failure(self, mock_search):
        # Step 1: fails
        mock_search.side_effect = Exception("Search crashed")
        fc1 = self._make_fc("recall_memory", {"query": "test"})
        res1 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc1]))
        
        self.assertRegex(str(res1[0].response["result"]), "Tool 'recall_memory' failed: Search crashed")
        
        # Step 2: tries something else and succeeds
        mock_search.side_effect = None
        mock_search.return_value = "Second attempt worked"
        fc2 = self._make_fc("recall_memory", {"query": "test_different"})
        res2 = await self._simulate_receive_audio_step(self._make_tool_call_response([fc2]))
        
        self.assertEqual(res2[0].response["result"], "Second attempt worked")

    def test_counters_reset_on_text(self):
        self.jarvis._tool_call_count = 5
        self.jarvis._last_tool_call = ("some_tool", "args")
        
        self.jarvis._on_text_command("Hello")
        
        self.assertEqual(self.jarvis._tool_call_count, 0)
        self.assertIsNone(self.jarvis._last_tool_call)

if __name__ == '__main__':
    unittest.main()
