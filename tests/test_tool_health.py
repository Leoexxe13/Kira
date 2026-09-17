"""Direct registry calls against isolated storage; no language or service mocks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.action_loader import discover_actions
from core.personal_store import PersonalStore
from core.operational_context import OperationalContext, use_context


class DirectToolHealth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = discover_actions(Path(__file__).resolve().parents[1] / 'actions', logger=lambda _: None)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = PersonalStore(self.root / 'personal.db')
        context = use_context(OperationalContext())
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def call(self, tool, **args):
        result = self.registry.run_result(tool, args, {'session_memory': {'store': self.store}})
        self.assertEqual(result['state'], 'verified', result)
        return result

    def test_wallet_direct_crud_and_undo(self):
        created = self.call('personal_records', domain='wallet', action='create', kind='income', amount='11000')
        mid = created['data']['id']
        edited = self.call('personal_records', domain='wallet', action='update', ids=[mid], updates={'amount':'12000'})
        self.assertEqual(edited['data']['amount'], 1200000)
        self.call('personal_records', domain='wallet', action='delete', ids=[mid])
        self.assertEqual(self.call('personal_records', domain='wallet', action='list')['data'], [])
        self.call('personal_records', domain='wallet', action='undo')
        self.assertEqual(len(self.call('personal_records', domain='wallet', action='list')['data']), 1)

    def test_work_direct_state_reconstruction(self):
        record = self.call('personal_records', domain='work', action='assign', resource='75', person='Carlos')
        self.call('personal_records', domain='work', action='update', ids=[record['data']['id']], updates={'person':'Pedro'})
        self.assertIn('Pedro', self.call('personal_records', domain='work', action='read', resource='75')['text'])
        self.call('personal_records', domain='work', action='return', resource='75')
        self.assertNotIn('Pedro', self.call('personal_records', domain='work', action='read', resource='75')['text'])

    def test_tasks_direct_storage(self):
        from actions import kira_tasks
        with patch.object(kira_tasks, 'TASK_FILE', self.root / 'tasks.json'):
            result = self.registry.run_result('kira_tasks', {'action':'add', 'text':'Synthetic audit task'})
            self.assertEqual(result['state'], 'executed')  # legacy tool has no typed readback
            rows = kira_tasks._load()
            self.assertEqual(rows[0]['text'], 'Synthetic audit task')
            self.registry.run_result('kira_tasks', {'action':'complete', 'id':rows[0]['id']})
            self.assertTrue(kira_tasks._load()[0]['done'])

    def test_files_direct_list_find_move(self):
        from actions import file_controller
        with patch.object(file_controller, '_SAFE_ROOTS', [self.root]):
            source = self.root / 'download.pdf'
            source.write_text('test')
            destination = self.root / 'destination'
            destination.mkdir()
            self.call('file_controller', action='list', path=str(self.root))
            found = self.call('file_controller', action='find', path=str(self.root), extension='.pdf')
            self.assertEqual(found['data'][0]['path'], str(source.resolve()))
            self.call('file_controller', action='move', path=str(source), destination=str(destination))
            self.assertFalse(source.exists())
            self.assertTrue((destination / source.name).is_file())
