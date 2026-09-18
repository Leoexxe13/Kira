from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.provider_manager import ProviderResult
from core.text_dispatcher import TextDispatcher
from tests.test_kira_stage1 import FakeRegistry


class Provider:
    def __init__(self, *plans):
        self.plans = list(plans)
        self.calls = []

    def interpret_request(self, text, context, capabilities, instructions=""):
        self.calls.append({"text": text, "context": context, "capabilities": capabilities, "instructions": instructions})
        return ProviderResult(True, "fixture", "", json.dumps(self.plans.pop(0)), 1)


class OfflineFileRegistry(FakeRegistry):
    def get_tool_declarations(self):
        declarations = super().get_tool_declarations()
        declarations.append({"name": "file_controller", "description": "Find files", "parameters": {
            "type": "OBJECT", "properties": {
                "action": {"type": "STRING"}, "path": {"type": "STRING"}, "extension": {"type": "STRING"}},
            "required": ["action"]}})
        return declarations


class TextDispatcherStage2Tests(unittest.TestCase):
    def test_dry_run_validates_plan_without_running_tool(self):
        registry = FakeRegistry()
        provider = Provider({
            "goal": "buscar PDFs", "steps": [{"tool": "find_files", "arguments": {"path": "downloads"}}],
            "needs_clarification": False, "clarification": "", "response": "",
        })
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TextDispatcher(
                base_dir=Path(td), registry=registry, provider=provider,
                memory_path=Path(td) / "memory.db", principal="test-user",
            )
            result = dispatcher.dispatch("Busca PDFs", source="test", dry_run=True)
        self.assertEqual(result.state, "planned")
        self.assertEqual(registry.calls, [])
        self.assertEqual(result.plan["steps"][0]["tool"], "find_files")
        self.assertTrue(provider.calls[0]["instructions"])

    def test_dry_run_shows_safe_pdf_plan_without_provider(self):
        registry = OfflineFileRegistry()
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TextDispatcher(
                base_dir=Path(td), registry=registry, provider=Provider(),
                memory_path=Path(td) / "memory.db", principal="test-user",
            )
            result = dispatcher.dispatch("Busca el último PDF que descargué", source="test", dry_run=True)
        self.assertEqual(result.state, "planned")
        self.assertEqual(registry.calls, [])
        self.assertEqual(result.plan["steps"][0]["tool"], "file_controller")

    def test_sequential_dispatch_reuses_same_operational_context(self):
        registry = FakeRegistry()
        provider = Provider(
            {"goal": "buscar PDFs", "steps": [{"tool": "find_files", "arguments": {"path": "downloads"}}],
             "needs_clarification": False, "clarification": "", "response": "Encontré los PDFs."},
            {"goal": "mover el PDF más reciente", "steps": [{"tool": "move_file", "arguments": {
                "path": {"$ref": "last_tool_result.data", "sort": "modified", "descending": True, "index": 0, "field": "path"},
                "destination": "desktop"}}], "needs_clarification": False, "clarification": "", "response": "Movido."},
        )
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TextDispatcher(
                base_dir=Path(td), registry=registry, provider=provider,
                memory_path=Path(td) / "memory.db", principal="test-user",
            )
            first = dispatcher.dispatch("Busca PDFs", source="test")
            second = dispatcher.dispatch("Muévelo al escritorio", source="test")
        self.assertEqual(first.state, "verified")
        self.assertEqual(second.state, "verified")
        self.assertEqual(registry.calls[1][1]["path"], "/tmp/new.pdf")
        self.assertEqual(dispatcher.context.value("last_user_goal"), "mover el PDF más reciente")

    def test_state_exposes_owner_context_and_provider_health_without_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TextDispatcher(
                base_dir=Path(td), registry=FakeRegistry(), provider=Provider(),
                memory_path=Path(td) / "memory.db", principal="trusted-owner",
            )
            state = dispatcher.state()
        self.assertEqual(state["principal"], "trusted-owner")
        self.assertNotIn("api_key", json.dumps(state))
        self.assertIn("context", state)

    def test_voice_observation_persists_without_dispatching_a_second_tool_call(self):
        registry = FakeRegistry()
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "voice.db"
            dispatcher = TextDispatcher(base_dir=Path(td), registry=registry, provider=Provider(),
                                        memory_path=db, principal="test-user")
            dispatcher.observe_turn("Abre el calendario", source="voice-live")
            restarted = TextDispatcher(base_dir=Path(td), registry=registry, provider=Provider(),
                                       memory_path=db, principal="test-user")
        self.assertEqual(restarted.context.value("last_user_goal"), "Abre el calendario")
        self.assertEqual(registry.calls, [])


if __name__ == "__main__":
    unittest.main()
