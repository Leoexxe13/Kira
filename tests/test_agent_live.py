"""Opt-in real provider evaluation; isolated filesystem, no personal history."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.action_loader import ActionRegistry
from core.dispatcher import Dispatcher
from core.personal_store import PersonalStore
from actions.file_controller import TOOL


@unittest.skipUnless(os.environ.get('KIRA_LIVE_EVAL') == '1', 'Requires configured live provider')
class LiveAgentEvaluation(unittest.TestCase):
    def test_real_model_discovers_then_moves_previous_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, target = root / 'inbox', root / 'outbox'
            source.mkdir(); target.mkdir()
            (source / 'report.pdf').write_text('synthetic document')
            (source / 'note.txt').write_text('synthetic note')
            registry = ActionRegistry({}, lambda _: None)
            registry.register(TOOL)
            dispatcher = Dispatcher(PersonalStore(root / 'personal.db'), registry)
            with patch('actions.file_controller._SAFE_ROOTS', [root]):
                first = dispatcher.dispatch_user_request(f'Me interesa saber qué hay guardado en {source}')
                self.assertEqual(first.state, 'verified', first.text)
                self.assertEqual(len(dispatcher.context.value('last_query_results')), 2)
                second = dispatcher.dispatch_user_request(f'De lo que acabas de mostrar, deja el PDF en {target}')
                if second.state == 'pending':
                    self.assertTrue(dispatcher.context.value('pending_clarification'))
                    second = dispatcher.dispatch_user_request('Trasládalo; el original no debe quedarse allí')
                self.assertEqual(second.state, 'verified', second.text)
            self.assertTrue((target / 'report.pdf').exists())
            self.assertFalse((source / 'report.pdf').exists())
            self.assertTrue((source / 'note.txt').exists())
