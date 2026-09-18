from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.provider_manager import ProviderResult
from core.runtime_journal import RequestEnvelope, RuntimeJournal
from core.text_dispatcher import TextDispatcher
from memory.sqlite_memory import MemoryStore


class PlanProvider:
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    def interpret_request(self, *_args, **_kwargs):
        self.calls += 1
        return ProviderResult(True, "recorded", "test", json.dumps(self.plan), 1)

    def provider_health(self):
        return {}


class ForeachRegistry:
    def __init__(self):
        self.calls = []
        self.failed_b = False

    def get_tool_declarations(self):
        return [
            {"name": "list_items", "description": "list", "parameters": {"type": "OBJECT", "properties": {}}},
            {"name": "process_item", "description": "process", "parameters": {
                "type": "OBJECT", "properties": {"value": {"type": "STRING"}}, "required": ["value"]}},
        ]

    def metadata(self, _name):
        return {"risk": "low", "read_only": True}

    def run(self, name, args, _context):
        self.calls.append((name, dict(args)))
        if name == "list_items":
            return {"state": "verified", "verified": True, "text": "2", "data": ["a", "b"]}
        if args["value"] == "b" and not self.failed_b:
            self.failed_b = True
            return {"state": "failed", "text": "temporary", "error": "temporary"}
        return {"state": "verified", "verified": True, "text": "ok", "data": {"value": args["value"]}}


class RuntimeJournalTests(unittest.TestCase):
    def test_idempotency_key_returns_terminal_result(self):
        with tempfile.TemporaryDirectory() as td:
            journal = RuntimeJournal(Path(td) / "runtime.db")
            first = RequestEnvelope.create(principal="p", session_id="s", source="chat",
                                           user_text="hola", idempotency_key="same")
            accepted, cached = journal.begin(first)
            self.assertTrue(accepted)
            self.assertIsNone(cached)
            journal.finish(first.request_id, "succeeded", {"state": "executed", "text": "ok"})
            second = RequestEnvelope.create(principal="p", session_id="s", source="remote",
                                            user_text="hola otra vez", idempotency_key="same")
            accepted, cached = journal.begin(second)
            self.assertFalse(accepted)
            self.assertEqual(cached["text"], "ok")

    def test_restart_reconciles_running_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime.db"
            journal = RuntimeJournal(path)
            envelope = RequestEnvelope.create(principal="p", session_id="s", source="chat", user_text="x")
            journal.begin(envelope)
            RuntimeJournal(path)
            job = journal.job(envelope.request_id)
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["error_code"], "process_interrupted")

    def test_foreach_retry_does_not_repeat_successful_items(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            registry = ForeachRegistry()
            provider = PlanProvider({
                "goal": "process two items",
                "steps": [
                    {"tool": "list_items", "arguments": {}},
                    {"tool": "process_item", "foreach": {"$ref": "steps.0"},
                     "arguments": {"value": {"$ref": "item"}}},
                ],
                "needs_clarification": False,
                "clarification": "",
                "response": "done",
            })
            dispatcher = TextDispatcher(
                base_dir=root,
                registry=registry,
                provider=provider,
                memory=MemoryStore(root / "memory.db", principal="p"),
                journal=RuntimeJournal(root / "runtime.db"),
            )
            first = dispatcher.dispatch("hazlo", request_id="request-one")
            self.assertEqual(first.state, "failed")
            second = dispatcher.dispatch("inténtalo otra vez", request_id="request-two")
            self.assertEqual(second.state, "verified")
            self.assertEqual(
                registry.calls,
                [("list_items", {}), ("process_item", {"value": "a"}),
                 ("process_item", {"value": "b"}), ("process_item", {"value": "b"})],
            )
            self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
