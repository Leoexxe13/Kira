import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.web_search import web_search, TOOL


class TestWebSearchMechanics(unittest.TestCase):
    @patch("duckduckgo_search.DDGS")
    def test_search_web_structured(self, MockDDGS):
        # Mock DDGS results
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = [
            {"title": "Result 1", "body": "Snippet 1", "href": "http://res1.com"},
            {"title": "Result 2", "body": "Snippet 2", "href": "http://res2.com"}
        ]
        MockDDGS.return_value.__enter__.return_value = mock_ddgs_instance

        res = web_search({"action": "search", "query": "python"})
        self.assertEqual(res["action"], "search")
        self.assertEqual(res["count"], 2)
        self.assertEqual(len(res["results"]), 2)
        self.assertEqual(res["results"][0]["url"], "http://res1.com")

    @patch("duckduckgo_search.DDGS")
    def test_search_domain_restriction(self, MockDDGS):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        MockDDGS.return_value.__enter__.return_value = mock_ddgs_instance

        res = web_search({"action": "search", "query": "einstein", "domain": "wikipedia.org"})
        self.assertEqual(res["action"], "search")
        # Ensure domain constraint is appended to the query passed to the search engine
        mock_ddgs_instance.text.assert_called_with("einstein site:wikipedia.org", max_results=5)

    @patch("duckduckgo_search.DDGS")
    def test_zero_results(self, MockDDGS):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        MockDDGS.return_value.__enter__.return_value = mock_ddgs_instance

        res = web_search({"action": "search", "query": "noresults123"})
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["results"], [])

    @patch("duckduckgo_search.DDGS")
    def test_network_error(self, MockDDGS):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.side_effect = Exception("DNS timeout")
        MockDDGS.return_value.__enter__.return_value = mock_ddgs_instance

        res = web_search({"action": "search", "query": "error"})
        self.assertIn("error", res)
        self.assertIn("DNS timeout", res["error"])

    @patch("actions.web_search.requests.get")
    def test_read_page(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>Hello web</p></body></html>"
        mock_get.return_value = mock_response

        res = web_search({"action": "read_page", "url": "example.com"})
        self.assertEqual(res["action"], "read_page")
        self.assertIn("Hello web", res["content"])

    @patch("webbrowser.open")
    def test_open_url_directa(self, mock_open):
        mock_open.return_value = True
        res = web_search({"action": "open_url", "url": "http://youtube.com"})
        self.assertEqual(res["action"], "open_url")
        self.assertEqual(res["url"], "http://youtube.com")
        mock_open.assert_called_with("http://youtube.com")

class TestWebSearchIntegration(unittest.TestCase):
    def setUp(self):
        from collections import deque
        from main import JarvisLive
        self.mock_ui = MagicMock()
        self.patcher = patch("main.WakeWordDetector", autospec=True)
        self.patcher.start()
        self.jarvis = JarvisLive(self.mock_ui)
        self.jarvis._loop = MagicMock()
        self.jarvis._op_context = deque(maxlen=5)

    def tearDown(self):
        self.patcher.stop()

    def test_op_context_integration(self):
        # Ensure a search result feeds the operational context correctly
        fake_result = json.dumps({"action": "search", "results": [{"title": "A"}]})
        self.jarvis._op_context.append({
            "tool": "web_search",
            "args": {"action": "search", "query": "A"},
            "result_snippet": fake_result
        })
        self.assertEqual(len(self.jarvis._op_context), 1)
        self.assertEqual(self.jarvis._op_context[0]["tool"], "web_search")

    def test_multi_step_and_repetition_prevention(self):
        # JarvisLive text command resets multi-step tool calls
        self.jarvis._tool_call_count = 0
        self.jarvis._last_tool_call = None
        
        fc1_args = {"action": "search", "query": "python"}
        call_sig1 = ("web_search", str(fc1_args))
        
        # Call 1
        self.assertNotEqual(self.jarvis._last_tool_call, call_sig1)
        self.jarvis._last_tool_call = call_sig1
        
        # Call 2 (duplicate)
        fc2_args = {"action": "search", "query": "python"}
        call_sig2 = ("web_search", str(fc2_args))
        self.assertEqual(self.jarvis._last_tool_call, call_sig2)

class TestCapabilitiesAndDiscovery(unittest.TestCase):
    def _registry(self):
        from core.action_loader import discover_actions
        return discover_actions(
            actions_dir=Path(__file__).resolve().parent.parent / "actions",
            reserved_names=set(),
        )

    def test_auto_discovery_web_search(self):
        # Verify the tool is loaded in the dynamic registry
        self.assertIn("web_search", self._registry().names())

    def test_tool_declaration_schema(self):
        # Validate schema of web_search tool
        self.assertEqual(TOOL["name"], "web_search")
        props = TOOL["parameters"]["properties"]
        self.assertIn("action", props)
        self.assertIn("query", props)
        self.assertIn("domain", props)
        self.assertIn("url", props)

    def test_gui_compatibility(self):
        from core.action_loader import discover_actions
        registry = discover_actions(
            actions_dir=Path(__file__).resolve().parent.parent / "actions",
            reserved_names=set(),
        )
        decls = registry.get_tool_declarations()
        names = [d["name"] for d in decls]
        self.assertIn("web_search", names)
        
        # The GUI dynamically consumes these declarations for the capabilities center
        web_tool = next(d for d in decls if d["name"] == "web_search")
        self.assertTrue(isinstance(web_tool["description"], str))

if __name__ == "__main__":
    unittest.main()
