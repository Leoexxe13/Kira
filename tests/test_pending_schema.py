import json
import unittest

from core.semantic import make_pending_operation, normalize_pending_operation


class PendingSchemaTests(unittest.TestCase):
    def test_canonical_pending_keeps_calls_and_tool_arguments(self):
        plan = {'goal': 'send', 'steps': [{'tool': 'whatsapp_web', 'arguments': {'action': 'send', 'chat': 'Nika', 'message': 'hola'}}]}
        value = make_pending_operation(plan, calls=[{'tool': 'whatsapp_web', 'arguments': plan['steps'][0]['arguments']}], kind='confirmation')
        self.assertEqual(value['goal'], 'send')
        self.assertEqual(value['calls'][0]['arguments']['message'], 'hola')
        self.assertIn('missing_fields', value)

    def test_legacy_pending_without_calls_is_normalized(self):
        raw = {'plan': {'goal': 'move', 'steps': [{'tool': 'file_controller', 'arguments': {'destination': 'Desktop'}}]},
               'missing_field': 'path', 'call': {'tool': 'file_controller', 'arguments': {'destination': 'Desktop'}}}
        value = normalize_pending_operation(raw)
        self.assertEqual(value['goal'], 'move')
        self.assertEqual(value['calls'][0]['arguments']['destination'], 'Desktop')
        self.assertEqual(value['missing_fields'], ['path'])
        self.assertEqual(value['kind'], 'clarification')

    def test_legacy_extra_tool_data_survives(self):
        raw = {'plan': {'goal': 'send'}, 'call': {'tool': 'whatsapp_web', 'arguments': {'message': 'texto'}},
               'data': {'query': 'Nika'}, 'created': 12}
        value = normalize_pending_operation(raw)
        self.assertEqual(value['data']['query'], 'Nika')
        self.assertEqual(value['calls'][0]['arguments']['message'], 'texto')


if __name__ == '__main__':
    unittest.main()
