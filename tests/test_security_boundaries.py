from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from actions import code_helper, desktop, open_app, send_message
from core.action_loader import ActionRecord, ActionRegistry


class SecurityBoundaryTests(unittest.TestCase):
    def test_whatsapp_never_reaches_generic_desktop_sender(self):
        result = send_message.send_message({
            "receiver": "Carlos",
            "message_text": "hola",
            "platform": "WhatsApp",
        })
        self.assertIsInstance(result, dict)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["error"], "exclusive_route_whatsapp_web")

    def test_open_app_cannot_bypass_whatsapp_route(self):
        result = open_app.open_app({"app_name": "WhatsApp"})
        self.assertIn("KIRA_ROUTE_BLOCKED", result)

    def test_generated_desktop_python_is_never_executed(self):
        result = desktop._execute_generated_code("raise RuntimeError('must not run')")
        self.assertIn("KIRA_ROUTE_BLOCKED", result)

    def test_code_run_is_preview_only(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "marker"
            program = Path(td) / "program.py"
            program.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n", encoding="utf-8")
            result = code_helper._run_action(str(program), [], 5, None)
            self.assertIn("KIRA_ROUTE_BLOCKED", result)
            self.assertFalse(marker.exists())

    def test_registry_requires_core_authorization_for_high_risk_call(self):
        calls = []

        def handler(parameters):
            calls.append(parameters)
            return "sent"

        record = ActionRecord(
            name="send_message",
            handler=handler,
            valid=True,
            metadata={"risk": "high", "confirmation_required": True},
        )
        registry = ActionRegistry({"send_message": record}, logger=lambda _msg: None)
        blocked = registry.run("send_message", {"receiver": "Carlos"}, {})
        self.assertEqual(blocked["state"], "pending")
        self.assertEqual(calls, [])
        allowed = registry.run(
            "send_message",
            {"receiver": "Carlos"},
            {"operation_authorized": True},
        )
        self.assertEqual(allowed, "sent")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
