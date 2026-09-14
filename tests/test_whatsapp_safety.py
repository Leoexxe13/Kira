"""Offline safety regressions: no browser, worker thread, or personal data."""
import ast
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'actions' / 'whatsapp_web.py'
# Avoid constructing the module's live worker; execute the real definitions.
tree = ast.parse(SOURCE.read_text())
tree.body = [n for n in tree.body if not (
    isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_WORKER' for t in n.targets))]
namespace = {'__file__': str(SOURCE), '__name__': 'offline_whatsapp'}
exec(compile(tree, str(SOURCE), 'exec'), namespace)
Worker = namespace['_WhatsAppWorker']


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.w = object.__new__(Worker)
        self.w._clear_candidates()
        self.w._last_send = ('', '', 0)
        self.w._ctx = self.w._pw = self.w._page = None
        self.page = Mock()
        self.w._ensure_page = Mock(return_value=self.page)
        self.w._bring_front = Mock()
        self.w._ensure_ready = Mock(return_value=True)
        self.w._open_id = Mock(return_value={'ok': True})
        self.w._active_chat = Mock(return_value={'id': 'b', 'name': 'Alex Two'})
        self.w._send = Mock(return_value={'ok': True, 'verified': True})
        self.w._messages = Mock(return_value=[])
        self.rows = [dict(id='a', name='Alex One', score=.9), dict(id='b', name='Alex Two', score=.9)]

    def pending(self, action='send'):
        self.w._resolve_chat = Mock(return_value=(None, self.rows))
        result = self.w._dispatch(dict(action=action, chat='Alex', message='synthetic'))
        self.assertIn('candidates', result)
        return result

    def test_homonyms_and_near_scores(self):
        for names in [('Alex', 'Alex'), ('Alex One', 'Alex Two')]:
            with self.subTest(names=names):
                self.w._chat_rows = Mock(return_value=[dict(id=str(i), name=n, aliases=[n], timestamp=i) for i,n in enumerate(names)])
                self.w._contact_rows = Mock(return_value=[])
                best, candidates = Worker._resolve_chat(self.w, self.page, 'Alex')
                self.assertIsNone(best)
                self.assertEqual(len(candidates), 2)

    def test_unique_and_duplicate_id(self):
        self.w._chat_rows = Mock(return_value=[dict(id='a', name='Alex', aliases=['Alex'])])
        self.w._contact_rows = Mock(return_value=[dict(id='a', name='Alex', aliases=['Alex'])])
        best, candidates = self.w._resolve_chat(self.page, 'Alex')
        self.assertEqual(best['id'], 'a')
        self.assertEqual(len(candidates), 1)

    def test_selection_continues_original_send(self):
        for choice in ({'candidate_index': 2}, {'candidate_id': 'b'}, {'candidate_index': 2, 'candidate_id': 'b'}):
            with self.subTest(choice=choice):
                self.pending()
                result = self.w._dispatch(dict(action='select', **choice))
                self.assertIn('WHATSAPP_VERIFIED_SENT', result)
                self.w._resolve_chat.assert_called_once()
                self.w._send.assert_called_with(self.page, 'b', 'synthetic')
                self.assertIsNone(self.w._pending_operation)
                self.assertFalse(self.w._last_candidates)

    def test_expiry_boundary(self):
        for age, allowed in [(299, True), (299.999, True), (300, False), (301, False)]:
            self.pending()
            self.w._last_candidates_at = 100
            with patch.object(time, 'monotonic', return_value=100+age):
                result = self.w._dispatch(dict(action='select', candidate_index=2))
            self.assertEqual('WHATSAPP_VERIFIED_SENT' in result, allowed)

    def test_invalid_indices_never_fallback(self):
        for value in (0, -1, 1.5, 2.0, '2', 'bad', None, True, 6, 3):
            for pending in (False, True):
                with self.subTest(value=value, pending=pending):
                    self.setUp()
                    if pending: self.pending()
                    result = self.w._dispatch(dict(action='send', candidate_index=value, message='synthetic'))
                    self.assertIn('UNVERIFIED', result)
                    self.w._send.assert_not_called()

    def test_contradictions(self):
        for extra in ({'candidate_id': 'a'}, {'chat': 'Other'}, {'message': 'different'}, {'action': 'read'}, {'direction': 'incoming'}, {'limit': 3}):
            self.setUp(); self.pending()
            args = dict(action='select', candidate_index=2); args.update(extra)
            self.assertIn('UNVERIFIED', self.w._dispatch(args))
            self.w._send.assert_not_called()

    def test_missing_foreign_and_empty_candidates(self):
        for cid in ('b', '', 'foreign'):
            self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='send', candidate_id=cid, message='synthetic')))
        self.pending()
        self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='select', candidate_id='foreign')))
        self.w._send.assert_not_called()

    def test_new_search_replaces_context_and_never_sends(self):
        self.pending()
        self.w._dispatch(dict(action='search', chat='Other'))
        self.assertEqual(self.w._pending_operation['action'], 'search')
        self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='send', candidate_index=2)))
        self.w._dispatch(dict(action='select', candidate_index=2))
        self.w._send.assert_not_called()

    def test_close_invalidates_and_reopen_cannot_resume(self):
        self.pending()
        self.w._dispatch(dict(action='close'))
        self.assertFalse(self.w._last_candidates)
        self.assertIsNone(self.w._pending_operation)
        self.w._dispatch(dict(action='open'))
        self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='select', candidate_index=2)))
        self.w._send.assert_not_called()

    def test_pending_cannot_fallback_to_active(self):
        self.pending()
        self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='send', message='synthetic')))
        self.w._send.assert_not_called()

    def test_changed_chat_before_send(self):
        self.pending()
        self.w._active_chat.side_effect = [{'id': 'b'}, {'id': 'a'}]
        self.assertIn('WHATSAPP_SEND_BLOCKED_WRONG_RECIPIENT', self.w._dispatch(dict(action='select', candidate_index=2)))
        self.w._send.assert_not_called()

    def test_open_mismatch_blocks_read_and_send(self):
        for action in ('read', 'send'):
            self.setUp(); self.pending(action)
            self.w._open_id.return_value = {'ok': False}
            self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='select', candidate_index=2)))
            self.w._messages.assert_not_called(); self.w._send.assert_not_called()

    def test_get_active_failure_blocks(self):
        self.pending()
        self.page.evaluate.side_effect = RuntimeError('offline')
        self.w._active_chat = lambda page: Worker._active_chat(self.w, page)
        self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='select', candidate_index=2)))
        self.w._send.assert_not_called()

    def test_read_retains_context(self):
        self.pending('read')
        result = self.w._dispatch(dict(action='select', candidate_index=2))
        self.assertIn('VERIFIED_MESSAGES', result)
        self.w._messages.assert_called_once_with(self.page, 'b', 20)
        self.w._send.assert_not_called()

    def test_message_confirmation_requires_exact_new_id(self):
        for returned, observed, from_me, verified in [('new','old',True,False), ('','old',True,False), ('new','new',False,False), ('new','new',True,True)]:
            self.w._last_send = ('', '', 0)
            self.page.evaluate.return_value = dict(ok=True, messageId=returned)
            self.w._messages.return_value = [dict(id=observed, fromMe=from_me, text='synthetic')]
            result = Worker._send(self.w, self.page, 'b', 'synthetic')
            self.assertEqual(result['verified'], verified)

    def test_duplicate_guard_does_not_claim_confirmation(self):
        self.w._last_send = ('b', 'synthetic', time.time())
        result = Worker._send(self.w, self.page, 'b', 'synthetic')
        self.assertFalse(result['verified'])
        self.page.evaluate.assert_not_called()

    def test_actual_open_checks_exact_id(self):
        self.w._ensure_composer_visible = Mock()
        for active, ok in [({'id': 'a'}, False), (None, False), ({'id': 'b'}, True)]:
            self.w._active_chat.return_value = active
            self.assertEqual(Worker._open_id(self.w, self.page, 'b')['ok'], ok)

    def test_confirmation_lookup_failure_is_unverified(self):
        self.page.evaluate.return_value = {'ok': True, 'messageId': 'new'}
        self.w._messages.side_effect = RuntimeError('offline')
        self.assertFalse(Worker._send(self.w, self.page, 'b', 'synthetic')['verified'])

    def test_failed_new_search_invalidates_old_list(self):
        self.pending()
        self.w._resolve_chat.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            self.w._dispatch(dict(action='search', chat='Other'))
        self.assertFalse(self.w._last_candidates)
        self.assertIn('UNVERIFIED', self.w._dispatch(dict(action='select', candidate_index=2)))
        self.w._send.assert_not_called()

    def test_unconfirmed_status_and_send_failure(self):
        for payload, prefix in [({'ok':True,'verified':False}, 'WHATSAPP_SEND_REQUESTED'), ({'ok':False}, 'WHATSAPP_UNVERIFIED')]:
            self.pending()
            self.w._send.return_value = payload
            self.assertTrue(self.w._dispatch(dict(action='select', candidate_index=2)).startswith(prefix))


if __name__ == '__main__':
    unittest.main()
