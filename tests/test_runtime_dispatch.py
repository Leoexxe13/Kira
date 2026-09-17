"""Near-runtime dispatch tests: real dispatcher, interpreter, services and SQLite."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.personal_store import PersonalStore
from core.work import Work
from core.wallet import Wallet
from core.intent_interpreter import run


class FakeUI:
    def __init__(self):
        self.logs=[]; self.refreshed=0
    def write_log(self, text): self.logs.append(text)
    def refresh_tasks(self): self.refreshed += 1


class RuntimeDispatchTests(unittest.TestCase):
    def setUp(self):
        from main import JarvisLive
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=PersonalStore(Path(self.temp.name)/'personal.db')
        self.ui=FakeUI(); self.controller=object.__new__(JarvisLive)
        self.controller.ui=self.ui; self.controller._loop=None
        self.controller._personal_store=self.store
        self.controller.dispatch_user_request = JarvisLive.dispatch_user_request.__get__(self.controller)

    def _dispatch(self, text, source):
        return self.controller.dispatch_user_request(text, source=source)

    def test_chat_input_reaches_wallet_service_and_ui(self):
        result=self._dispatch('Cobré once mil','chat')
        self.assertTrue(result.handled); self.assertEqual(Wallet(self.store).summary()['income'],1100000)
        self.assertGreater(self.ui.refreshed,0)
        self.assertFalse(any('[VERIFY]' in line for line in self.ui.logs))  # diagnostics never become chat bubbles

    def test_unambiguous_app_launch_does_not_require_provider(self):
        from core.action_loader import ActionRegistry
        from actions.open_app import TOOL
        registry = ActionRegistry({}, lambda _: None)
        registry.register(TOOL)
        controller = self.controller
        controller._text_dispatcher = __import__('core.dispatcher', fromlist=['Dispatcher']).Dispatcher(
            self.store, registry=registry, provider=object())
        with patch('actions.open_app._OS_LAUNCHERS', {'Darwin': lambda _: True}):
            result = controller._text_dispatcher.dispatch_user_request('abre Chrome')
        self.assertEqual(result.state, 'executed')

    def test_home_voice_and_remote_share_dispatch(self):
        self.assertEqual(self._dispatch('Pagué mil quinientos de universidad','voice').domain,'wallet')
        self.assertEqual(self._dispatch('Le entregué la ficha 75 a Carlos','remote').domain,'work')
        self.assertEqual(Work(self.store).holder('75')['person'],'Carlos')

    def test_context_update_delete_confirmation_and_bulk(self):
        self._dispatch('Gasté 1200 en transporte','chat')
        self.assertEqual(self._dispatch('Eran 1800','chat').state,'verified')
        self.assertEqual(Wallet(self.store).movements()[0]['amount'],180000)
        self.assertEqual(self._dispatch('Bórralo','chat').state,'pending')
        self.assertEqual(self._dispatch('Sí','chat').state,'verified')

    def test_unrecognized_personal_scope_is_clarification_not_legacy_error(self):
        # Context domain lets “los eventos que tengo aquí” resolve to Work.
        self._dispatch('Le entregué la ficha 75 a Carlos','chat')
        result=self._dispatch('Elimina los eventos que tengo aquí','chat')
        self.assertEqual(result.state,'pending'); self.assertNotIn('Orden no reconocida',result.text)

    def test_personal_action_is_discoverable_and_invocable(self):
        from core.action_loader import discover_actions
        registry = discover_actions(Path(__file__).parents[1] / 'actions', logger=lambda _: None)
        self.assertTrue(registry.has('personal_hub'))
        result = registry.run('personal_hub', {'command':'¿Cuánto me queda?'})
        self.assertIn('VERIFICADO', result)


if __name__=='__main__': unittest.main()
