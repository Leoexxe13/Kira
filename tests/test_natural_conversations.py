from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.provider_manager import ProviderResult
from core.text_dispatcher import TextDispatcher
from tests.test_kira_stage1 import FakeRegistry


class NaturalProvider:
    def __init__(self, *plans):
        self.plans = list(plans)

    def interpret_request(self, text, context, capabilities, instructions=""):
        if not self.plans:
            raise AssertionError("The provider should not be called for a pending retry")
        return ProviderResult(True, "fixture", "", json.dumps(self.plans.pop(0)), 1)


class FailOnceRegistry(FakeRegistry):
    def __init__(self):
        super().__init__()
        self.fail = True

    def run(self, name, args, context):
        self.calls.append((name, args, context))
        if self.fail:
            self.fail = False
            return {"state": "failed", "verified": False, "text": "No pude acceder a Descargas.",
                    "error": "temporary filesystem failure"}
        return {"state": "verified", "verified": True, "text": "Moví el archivo al escritorio.",
                "data": {"last_file": args["path"]}}


class DestructiveRegistry:
    def __init__(self):
        self.calls = []

    def get_tool_declarations(self):
        return [{"name": "file_controller", "description": "File operation", "parameters": {
            "type": "OBJECT", "properties": {"action": {"type": "STRING"}, "path": {"type": "STRING"}},
            "required": ["action", "path"]}}]

    def run(self, name, args, context):
        self.calls.append((name, args, context))
        return {"state": "verified", "verified": True, "text": "Archivo eliminado.", "data": {"deleted": args["path"]}}


class NaturalConversationTests(unittest.TestCase):
    def test_everyday_spanish_file_followup_is_sequential(self):
        registry = FakeRegistry()
        provider = NaturalProvider(
            {"goal": "encontrar el PDF más reciente", "steps": [
                {"tool": "find_files", "arguments": {"path": "downloads"}}],
             "needs_clarification": False, "clarification": "", "response": "Encontré los documentos."},
            {"goal": "mover el PDF más reciente al escritorio", "steps": [
                {"tool": "move_file", "arguments": {
                    "path": {"$ref": "last_tool_result.data", "sort": "modified", "descending": True,
                             "index": 0, "field": "path"}, "destination": "desktop"}}],
             "needs_clarification": False, "clarification": "", "response": "Listo, ya lo moví."},
        )
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "daily.db"
            dispatcher = TextDispatcher(base_dir=Path(td), registry=registry, provider=provider,
                                        memory_path=db, principal="mac:tester")
            first = dispatcher.dispatch("Oye, busca el PDF más reciente que descargué", source="chat")
            second = dispatcher.dispatch("Perfecto, ahora muévelo al escritorio", source="chat")
        self.assertEqual(first.state, "verified")
        self.assertEqual(second.state, "verified")
        self.assertEqual(registry.calls[-1][1]["path"], "/tmp/new.pdf")

    def test_failed_request_survives_restart_and_intentalo_otra_vez_resumes(self):
        registry = FailOnceRegistry()
        provider = NaturalProvider({
            "goal": "mover el archivo descargado", "steps": [{"tool": "move_file", "arguments": {
                "path": "/tmp/downloaded.pdf", "destination": "desktop"}}],
            "needs_clarification": False, "clarification": "", "response": "Hecho."})
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "restart.db"
            first_dispatcher = TextDispatcher(base_dir=Path(td), registry=registry, provider=provider,
                                              memory_path=db, principal="mac:tester")
            failed = first_dispatcher.dispatch("Mueve el archivo descargado al escritorio", source="chat")
            second_dispatcher = TextDispatcher(base_dir=Path(td), registry=registry, provider=NaturalProvider(),
                                               memory_path=db, principal="mac:tester")
            retried = second_dispatcher.dispatch("No pasa nada, inténtalo otra vez", source="chat")
        self.assertEqual(failed.state, "failed")
        self.assertEqual(retried.state, "verified")
        self.assertEqual(len(registry.calls), 2)

    def test_everyday_memory_question_survives_new_process(self):
        from actions.user_memory import TOOL
        from core.action_loader import ActionRecord, ActionRegistry
        from memory.sqlite_memory import MemoryStore
        from memory.sqlite_memory import use_memory

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            memory = MemoryStore(db, principal="mac:tester")
            record = ActionRecord(name=TOOL["name"], description=TOOL["description"],
                                  parameters=TOOL["parameters"], handler=TOOL["handler"], valid=True)
            registry = ActionRegistry({TOOL["name"]: record}, lambda _message: None)
            with use_memory(memory):
                saved = registry.run(TOOL["name"], {"action": "remember", "category": "preferences",
                                                    "topic": "coffee", "content": "Prefiero café sin azúcar."})
            self.assertEqual(saved["state"], "verified")
            restarted = MemoryStore(db, principal="mac:tester")
            self.assertIn("café", restarted.recall("¿Cómo tomo el café?")[0].content)

    def test_destructive_request_waits_for_explicit_confirmation(self):
        registry = DestructiveRegistry()
        provider = NaturalProvider({
            "goal": "eliminar el archivo temporal", "steps": [{"tool": "file_controller", "arguments": {
                "action": "delete", "path": "/tmp/example.txt"}}],
            "needs_clarification": False, "clarification": "", "response": "Eliminado.",
        })
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TextDispatcher(base_dir=Path(td), registry=registry, provider=provider,
                                        memory_path=Path(td) / "safe.db", principal="mac:tester")
            pending = dispatcher.dispatch("Borra el archivo temporal", source="chat")
            calls_before_confirmation = list(registry.calls)
            confirmed = dispatcher.dispatch("Sí, confirmo", source="chat")
        self.assertEqual(pending.state, "pending")
        self.assertEqual(calls_before_confirmation, [])
        self.assertEqual(confirmed.state, "verified")
        self.assertEqual(len(registry.calls), 1)

    def test_memory_commands_work_offline_without_provider(self):
        from actions.user_memory import TOOL
        from core.action_loader import ActionRecord, ActionRegistry

        record = ActionRecord(name=TOOL["name"], description=TOOL["description"],
                              parameters=TOOL["parameters"], handler=TOOL["handler"], valid=True)
        registry = ActionRegistry({TOOL["name"]: record}, lambda _message: None)
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TextDispatcher(base_dir=Path(td), registry=registry, provider=NaturalProvider(),
                                        memory_path=Path(td) / "offline.db", principal="mac:tester")
            saved = dispatcher.dispatch("Recuerda que prefiero el café con azúcar", source="chat")
            recalled = dispatcher.dispatch("¿Qué recuerdas del café?", source="chat")
            natural_question = dispatcher.dispatch("¿Cómo prefiero el café?", source="chat")
        self.assertEqual(saved.state, "verified")
        self.assertIn("Lo recordaré", saved.text)
        self.assertEqual(recalled.state, "verified")
        self.assertIn("café", recalled.text)
        self.assertEqual(natural_question.state, "verified")
        self.assertIn("café", natural_question.text)


if __name__ == "__main__":
    unittest.main()
