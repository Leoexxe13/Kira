from __future__ import annotations

import unittest

from core.operation_policy import RiskLevel, classify_operation
from core.tool_contract import normalize_tool_result


class OperationPolicyTests(unittest.TestCase):
    def test_reads_are_low_risk(self):
        decision = classify_operation("file_controller", {"action": "find", "path": "downloads"})
        self.assertEqual(decision.risk, RiskLevel.LOW)
        self.assertFalse(decision.confirmation_required)

    def test_move_is_medium_without_confirmation(self):
        decision = classify_operation("file_controller", {"action": "move", "path": "a", "destination": "b"})
        self.assertEqual(decision.risk, RiskLevel.MEDIUM)
        self.assertFalse(decision.confirmation_required)

    def test_delete_and_send_require_confirmation(self):
        delete = classify_operation("file_controller", {"action": "delete", "path": "a"})
        send = classify_operation("whatsapp_web", {"action": "send", "chat": "x", "message": "hola"})
        self.assertTrue(delete.confirmation_required)
        self.assertTrue(send.confirmation_required)
        self.assertEqual(send.risk, RiskLevel.HIGH)

    def test_declared_metadata_overrides_legacy_default(self):
        decision = classify_operation("custom", {}, {"risk": "high", "confirmation_required": True})
        self.assertEqual(decision.risk, RiskLevel.HIGH)
        self.assertTrue(decision.confirmation_required)


class ToolContractTests(unittest.TestCase):
    def test_structured_failure_never_becomes_success(self):
        result = normalize_tool_result("x", {"state": "verified", "verified": True, "error": "boom"})
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.ok)

    def test_whatsapp_verified_marker_is_parsed(self):
        result = normalize_tool_result(
            "whatsapp_web",
            'WHATSAPP_VERIFIED_SENT: {"chat":"Carlos","verified":true}',
        )
        self.assertEqual(result.state, "verified")
        self.assertTrue(result.verified)
        self.assertEqual(result.data["chat"], "Carlos")

    def test_legacy_error_string_is_failed(self):
        result = normalize_tool_result("file_controller", "Could not move file: permission denied")
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.verified)


if __name__ == "__main__":
    unittest.main()
