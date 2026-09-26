import unittest
from unittest.mock import MagicMock, patch
from main import JarvisLive

class TestContextualMemory(unittest.TestCase):
    def setUp(self):
        self.mock_ui = MagicMock()
        self.mock_ui.muted = False
        self.mock_session = MagicMock()
        
        # Patch WakeWordDetector to avoid loading openwakeword
        self.patcher = patch('main.WakeWordDetector', autospec=True)
        self.mock_wake_word = self.patcher.start()
        
        self.jarvis = JarvisLive(self.mock_ui)
        self.jarvis.session = self.mock_session
        self.jarvis._loop = MagicMock()

    def tearDown(self):
        self.patcher.stop()

    @patch('memory.memory_manager.search_memory')
    def test_retrieval_for_relevant_fact(self, mock_search_memory):
        mock_search_memory.return_value = "category: user\nfact_key: favorite_color\nvalue: blue"
        self.jarvis._on_text_command("What is my favorite color?")
        
        mock_search_memory.assert_called_once_with("What is my favorite color?", limit=3)
        call_args = self.jarvis.session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertIn("[RELEVANT MEMORY]", call_args)
        self.assertIn("favorite_color", call_args)

    @patch('memory.memory_manager.search_memory')
    def test_retrieval_for_relevant_session(self, mock_search_memory):
        mock_search_memory.return_value = "session from yesterday: discussed AI."
        self.jarvis._on_text_command("What did we talk about yesterday?")
        
        mock_search_memory.assert_called_once_with("What did we talk about yesterday?", limit=3)
        call_args = self.jarvis.session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertIn("[RELEVANT MEMORY]", call_args)
        self.assertIn("discussed AI.", call_args)

    @patch('memory.memory_manager.search_memory')
    def test_no_retrieval_for_trivial_greeting(self, mock_search_memory):
        self.jarvis._on_text_command("hola")
        mock_search_memory.assert_not_called()
        call_args = self.jarvis.session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertNotIn("[RELEVANT MEMORY]", call_args)
        self.assertEqual(call_args, "hola")

    @patch('memory.memory_manager.search_memory')
    def test_no_results(self, mock_search_memory):
        mock_search_memory.return_value = "No matching memories found."
        self.jarvis._on_text_command("Some random query")
        call_args = self.jarvis.session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertNotIn("[RELEVANT MEMORY]", call_args)

    @patch('memory.memory_manager.search_memory')
    def test_memory_failure_safe(self, mock_search_memory):
        mock_search_memory.side_effect = Exception("FTS5 crashed")
        self.jarvis._on_text_command("Safe query")
        call_args = self.jarvis.session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertNotIn("[RELEVANT MEMORY]", call_args)
        self.assertEqual(call_args, "Safe query")

    @patch('memory.memory_manager.search_memory')
    def test_context_limit(self, mock_search_memory):
        self.jarvis._on_text_command("A sufficiently long query")
        mock_search_memory.assert_called_once_with("A sufficiently long query", limit=3)

    @patch('memory.memory_manager.search_memory')
    def test_no_duplication(self, mock_search_memory):
        mock_search_memory.return_value = "Duplicate avoidance tested internally by FTS5, just need to see we append it."
        self.jarvis._on_text_command("A completely different long query")
        call_args = self.jarvis.session.send_client_content.call_args[1]['turns']['parts'][0]['text']
        self.assertEqual(call_args.count("[RELEVANT MEMORY]"), 1)

if __name__ == '__main__':
    unittest.main()
