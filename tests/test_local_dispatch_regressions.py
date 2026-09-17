import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from core.dispatcher import Dispatcher
from core.personal_store import PersonalStore
from core.operational_context import CONTEXT
from core.action_loader import ActionRegistry
from core.provider_manager import ProviderResult

class LocalDispatchRegressionTests(unittest.TestCase):
    def test_folder_request_does_not_bypass_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider=Mock()
            provider.interpret_request.return_value=ProviderResult(False,'none','','',0,'Unavailable')
            d=Dispatcher(PersonalStore(Path(temporary)/'p.db'), ActionRegistry({},lambda _:None), provider=provider)
            with patch('core.local_fastpath._run') as run:
                result=d.dispatch_user_request('abre documentos')
            self.assertEqual(result.state,'failed')
            run.assert_not_called()
            provider.interpret_request.assert_called_once()
            self.assertEqual(d.semantic.trace['execution'],[])
