from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from core.operational_context import OperationalContext
from core.reasoning_core import ReasoningCore, PlanError, resolve_reference
from core.provider_manager import ProviderManager, ProviderResult
from memory.sqlite_memory import MemoryStore


class FakeRegistry:
    def __init__(self):
        self.calls = []
        self.files = [{"path": "/tmp/old.pdf", "extension": ".pdf", "modified": 1, "name": "old.pdf"},
                      {"path": "/tmp/new.pdf", "extension": ".pdf", "modified": 2, "name": "new.pdf"}]

    def get_tool_declarations(self):
        return [
            {"name": "find_files", "description": "Find files", "parameters": {
                "type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
            {"name": "move_file", "description": "Move file", "parameters": {
                "type": "OBJECT", "properties": {
                    "path": {"type": "STRING"}, "destination": {"type": "STRING"}},
                "required": ["path", "destination"]}},
        ]

    def run(self, name, args, context):
        self.calls.append((name, args, context))
        if name == "find_files":
            return {"state": "verified", "verified": True, "text": "Encontré dos PDFs.", "data": self.files}
        if name == "move_file":
            return {"state": "verified", "verified": True, "text": f"Moví {args['path']} a {args['destination']}.",
                    "data": {"last_file": args["path"]}}
        return {"state": "failed", "text": "No existe esa herramienta."}


class RecordedProvider:
    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []

    def interpret_request(self, text, context, capabilities):
        self.calls.append((text, context, capabilities))
        return ProviderResult(True, "recorded", "fixture", json.dumps(self.plans.pop(0)), 1)


class MemoryConversationTests(unittest.TestCase):
    def test_remember_restart_update_forget_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "kira.db"
            first = MemoryStore(db, principal="local-os:A")
            first.remember("preferences", "favorite_language", "Python")
            restarted = MemoryStore(db, principal="local-os:A")
            self.assertEqual(restarted.recall("language")[0].content, "Python")
            restarted.remember("preferences", "favorite_language", "Rust", source="user_correction")
            self.assertEqual(restarted.recall("language")[0].content, "Rust")
            self.assertTrue(restarted.forget("preferences", "favorite_language"))
            self.assertEqual(restarted.recall("language"), [])
            other = MemoryStore(db, principal="local-os:B")
            self.assertEqual(other.recall(), [])

    def test_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            memory = MemoryStore(Path(td) / "kira.db", principal="A")
            with self.assertRaises(ValueError):
                memory.remember("notes", "api_key", "secret-value")
            with self.assertRaises(ValueError):
                memory.remember("notes", "connection", "api_key=secret-value")

    def test_memory_tool_uses_bound_owner(self):
        from actions.user_memory import TOOL
        from core.action_loader import ActionRecord, ActionRegistry
        from memory.sqlite_memory import use_memory
        with tempfile.TemporaryDirectory() as td:
            memory = MemoryStore(Path(td) / "kira.db", principal="A")
            record = ActionRecord(name=TOOL["name"], description=TOOL["description"],
                                  parameters=TOOL["parameters"], handler=TOOL["handler"], valid=True)
            registry = ActionRegistry({TOOL["name"]: record}, lambda _message: None)
            with use_memory(memory):
                saved = registry.run(TOOL["name"], {"action": "remember", "category": "preferences",
                                                    "topic": "favorite_language", "content": "Python"})
                recalled = registry.run(TOOL["name"], {"action": "recall", "query": "language"})
            self.assertEqual(saved["state"], "verified")
            self.assertIn("Python", recalled["text"])


class OperationalContextTests(unittest.TestCase):
    def test_resource_reference_expires_and_requires_existing_path(self):
        with tempfile.TemporaryDirectory() as td:
            now = [0.0]
            context = OperationalContext(clock=lambda: now[0], ttl=10)
            path = Path(td) / "download.pdf"
            path.write_text("data", encoding="utf-8")
            self.assertTrue(context.resource(path, downloaded=True, verified=True))
            self.assertEqual(context.get("last_download"), path.resolve())
            path.unlink()
            self.assertIsNone(context.get("last_download"))
            path.write_text("data", encoding="utf-8")
            context.resource(path)
            now[0] = 10.0
            self.assertIsNone(context.get("last_file"))


class ReasoningCoreTests(unittest.TestCase):
    def test_sequential_query_then_move_uses_observed_result(self):
        registry = FakeRegistry()
        provider = RecordedProvider([
            {"goal": "mover el PDF más reciente", "steps": [
                {"tool": "find_files", "arguments": {"path": "downloads"}},
                {"tool": "move_file", "arguments": {
                    "path": {"$ref": "steps.0", "sort": "modified", "descending": True, "index": 0, "field": "path"},
                    "destination": "desktop"}},
            ], "needs_clarification": False, "clarification": "", "response": "Operación verificada."}
        ])
        core = ReasoningCore(registry=registry, provider=provider)
        result = core.dispatch("Busca el último PDF de Descargas y muévelo al escritorio.")
        self.assertEqual(result.state, "verified")
        self.assertEqual([call[0] for call in registry.calls], ["find_files", "move_file"])
        self.assertEqual(registry.calls[1][1]["path"], "/tmp/new.pdf")
        self.assertEqual(registry.calls[1][1]["destination"], "desktop")

    def test_clarification_is_pending_and_tool_does_not_run(self):
        registry = FakeRegistry()
        provider = RecordedProvider([{
            "goal": "reproducir música", "steps": [], "needs_clarification": True,
            "clarification": "¿Qué artista prefieres?", "response": ""}])
        core = ReasoningCore(registry=registry, provider=provider)
        result = core.dispatch("Ponme música")
        self.assertEqual(result.state, "pending")
        self.assertTrue(core.context.value("pending_operation"))
        self.assertEqual(registry.calls, [])

    def test_provider_failure_preserves_context_and_does_not_execute(self):
        registry = FakeRegistry()
        core = ReasoningCore(registry=registry, provider=lambda **_: (_ for _ in ()).throw(TimeoutError()))
        result = core.dispatch("Mueve el archivo")
        self.assertEqual(result.state, "failed")
        self.assertEqual(registry.calls, [])
        self.assertEqual(core.context.value("last_user_goal"), "Mueve el archivo")

    def test_tool_failure_preserves_pending_operation(self):
        registry = FakeRegistry()
        registry.run = lambda name, args, context: {
            "state": "failed", "verified": False, "text": "No pude mover el archivo.",
            "error": "destination unavailable",
        }
        provider = RecordedProvider([{
            "goal": "mover archivo", "steps": [{"tool": "move_file", "arguments": {
                "path": "/tmp/a.pdf", "destination": "desktop"}}],
            "needs_clarification": False, "clarification": "", "response": ""}])
        core = ReasoningCore(registry=registry, provider=provider)
        result = core.dispatch("Mueve el archivo al escritorio")
        self.assertEqual(result.state, "failed")
        self.assertEqual(core.context.value("pending_operation")["step_index"], 0)
        self.assertIn("No pude mover", core.context.value("last_tool_result")["text"])

    def test_references_cannot_access_attributes(self):
        with self.assertRaises(PlanError):
            resolve_reference({"$ref": "steps.0.__class__"}, {}, [[{"path": "/tmp/a"}]])


class ProviderFallbackTests(unittest.TestCase):
    def test_groq_429_cools_down_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as td, patch("core.provider_manager.CFG_PATH", Path(td) / "providers.json"):
            manager = ProviderManager()
            manager.cfg["providers"]["groq"].update({"enabled": True, "api_key": "test"})
            error = urllib.error.HTTPError("url", 429, "limited", {}, None)
            with patch("urllib.request.urlopen", side_effect=error) as request:
                first = manager.ask_free("{}", structured=True)
                second = manager.ask_free("{}", structured=True)
            self.assertFalse(first.ok)
            self.assertFalse(second.ok)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(manager.provider_health()["groq"]["state"], "RATE_LIMITED")

    def test_local_success_stops_fallback(self):
        with tempfile.TemporaryDirectory() as td, patch("core.provider_manager.CFG_PATH", Path(td) / "providers.json"):
            manager = ProviderManager()
            manager.cfg["providers"]["groq"].update({"enabled": False})
            response = type("Response", (), {"read": lambda self: json.dumps({"message": {"content": '{"goal":"ok"}'}}).encode(), "__enter__": lambda self: self, "__exit__": lambda *args: None})()
            with patch("urllib.request.urlopen", return_value=response):
                result = manager._ask_local("{}", "return json")
            self.assertTrue(result.ok)
            self.assertEqual(result.provider, "local")
            self.assertEqual(manager.provider_health()["local"]["state"], "AVAILABLE")

    def test_interpretation_falls_from_groq_to_local_once(self):
        with tempfile.TemporaryDirectory() as td, patch("core.provider_manager.CFG_PATH", Path(td) / "providers.json"):
            manager = ProviderManager()
            with patch.object(manager, "ask_free", return_value=ProviderResult(False, "groq", "", "", 0, "Groq HTTP 429")) as groq, \
                 patch.object(manager, "_ask_local", return_value=ProviderResult(True, "local", "model", "{}", 2)) as local, \
                 patch.object(manager, "_ask_gemini") as gemini:
                result = manager.interpret_request("test", {}, [], "json")
            self.assertTrue(result.ok)
            self.assertEqual(result.provider, "local")
            groq.assert_called_once()
            local.assert_called_once()
            gemini.assert_not_called()

    def test_interpretation_reports_all_provider_failures(self):
        with tempfile.TemporaryDirectory() as td, patch("core.provider_manager.CFG_PATH", Path(td) / "providers.json"):
            manager = ProviderManager()
            with patch.object(manager, "ask_free", return_value=ProviderResult(False, "groq", "", "", 1, "Groq no configurado")), \
                 patch.object(manager, "_ask_local", return_value=ProviderResult(False, "local", "", "", 2, "Ollama URLError")), \
                 patch.object(manager, "_ask_gemini", return_value=ProviderResult(False, "gemini", "", "", 3, "Gemini ModuleNotFoundError")):
                result = manager.interpret_request("test", {}, [], "json")
            self.assertFalse(result.ok)
            self.assertIn("Groq no configurado", result.error)
            self.assertIn("Ollama URLError", result.error)
            self.assertIn("Gemini ModuleNotFoundError", result.error)


if __name__ == "__main__":
    unittest.main()
