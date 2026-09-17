import unittest
from core.tool_feedback import classify_result, reset, spoken_summary


class ToolFeedbackTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_real_error_is_preserved_and_natural_summary(self):
        tagged, failed, count = classify_result('open_app', "Tool 'open_app' failed: FileNotFoundError: app not found")
        self.assertTrue(failed); self.assertEqual(count, 1)
        self.assertIn('FileNotFoundError', tagged)
        summary = spoken_summary('open_app', tagged)
        self.assertIn('fallo real', summary); self.assertIn('FileNotFoundError', summary)
        self.assertNotIn('actualiza el sistema', summary.lower())

    def test_repeated_failure_changes_guidance(self):
        classify_result('x', 'failed: connection refused')
        tagged, failed, count = classify_result('x', 'failed: connection refused')
        self.assertTrue(failed); self.assertEqual(count, 2)
        self.assertIn('TOOL_FAILURE_REPEAT', tagged)
        self.assertIn('alternativa', spoken_summary('x', tagged).lower())

    def test_success_and_unverified_are_distinct(self):
        tagged, failed, count = classify_result('send', 'acción ejecutada; verified=true')
        self.assertFalse(failed); self.assertEqual(count, 0)
        self.assertIn('TOOL_RESULT', tagged)
        self.assertIn('no devolvió confirmación', spoken_summary('send', tagged, verified=False))

    def test_spanish_and_no_invented_cause(self):
        summary = spoken_summary('remote', '[TOOL_FAILURE] TimeoutError: conexión cerrada')
        self.assertIn('herramienta remote', summary)
        self.assertIn('TimeoutError', summary)
        for forbidden in ('português', 'sessão expirada', 'necesita actualizar'):
            self.assertNotIn(forbidden, summary.lower())

    def test_status_prefixes_are_failures_only_when_supported(self):
        for result in ('WHATSAPP_SEND_REQUESTED: enviado solicitado',):
            tagged, failed, _ = classify_result('whatsapp_web', result)
            self.assertFalse(failed)
            self.assertIn('TOOL_UNVERIFIED', tagged)
            self.assertIn(result, tagged)
            self.assertIn('no devolvió confirmación', spoken_summary('whatsapp_web', tagged).lower())
        tagged, failed, _ = classify_result('whatsapp_web', 'WHATSAPP_UNVERIFIED: falta confirmación')
        self.assertTrue(failed)


if __name__ == '__main__':
    unittest.main()
