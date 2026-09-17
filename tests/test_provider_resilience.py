import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.dispatcher import Dispatcher
from core.personal_store import PersonalStore
from core.provider_manager import ProviderManager, ProviderResult


class ProviderResilienceTests(unittest.TestCase):
    def manager(self):
        pm = object.__new__(ProviderManager)
        pm.cfg = ProviderManager.DEFAULTS.copy()
        pm.health = {}
        pm.cooldowns = {'groq': 60.0, 'local': 10.0, 'gemini': 60.0}
        return pm

    def test_groq_success_is_selected_and_marked_available(self):
        pm = self.manager()
        pm._ensure_health()
        pm.cfg['providers']['groq']['enabled'] = True
        ok = ProviderResult(True, 'groq', 'fixture', '{}', 12)
        with patch.object(pm, 'ask_free', return_value=ok), patch('core.llm_client.call_llm_generate') as local:
            result = pm.interpret_request('test', {}, [], '{}')
        self.assertIs(result, ok)
        local.assert_not_called()
        self.assertEqual(pm.provider_health()['groq']['state'], 'AVAILABLE')

    def test_429_enters_cooldown_and_does_not_retry_groq(self):
        pm = self.manager()
        pm._ensure_health()
        pm.cfg['providers']['groq']['enabled'] = True
        failed = ProviderResult(False, 'groq', '', '', 0, 'Groq: HTTP 429')
        with patch.object(pm, 'reload'), patch.object(pm, '_groq_key', return_value='secret'), \
             patch('urllib.request.urlopen', side_effect=__import__('urllib').error.HTTPError('x', 429, 'x', {}, None)):
            first = pm.ask_free('{}', structured=True)
            second = pm.ask_free('{}', structured=True)
        self.assertFalse(first.ok); self.assertFalse(second.ok)
        self.assertEqual(pm.provider_health()['groq']['state'], 'RATE_LIMITED')
        self.assertIn('cooldown', second.error)

    def test_local_failure_falls_through_to_gemini(self):
        pm = self.manager()
        pm._ensure_health()
        online = ProviderResult(False, 'groq', '', '', 0, 'HTTP 429')
        gemini = ProviderResult(True, 'gemini', 'fixture', '{}', 3)
        with patch.object(pm, 'ask_free', return_value=online), \
             patch('core.llm_client.call_llm_generate', side_effect=ConnectionError('offline')), \
             patch.object(pm, '_interpret_gemini', return_value=gemini):
            result = pm.interpret_request('test', {}, [], '{}')
        self.assertIs(result, gemini)
        self.assertEqual(pm.provider_health()['local']['state'], 'DISCONNECTED')

    def test_all_providers_fail_without_leaking_technical_error_to_chat(self):
        pm = self.manager()
        pm._ensure_health()
        with patch.object(pm, 'ask_free', return_value=ProviderResult(False, 'groq', '', '', 0, 'HTTP 429')), \
             patch('core.llm_client.call_llm_generate', side_effect=ConnectionError('offline')), \
             patch.object(pm, '_interpret_gemini', return_value=ProviderResult(False, 'gemini', '', '', 0, 'HTTP 503')):
            provider = pm
            with tempfile.TemporaryDirectory() as td:
                dispatcher = Dispatcher(PersonalStore(Path(td) / 'p.db'), provider=provider)
                dispatcher.context.update(pending_clarification={'question':'¿Artista?', 'missing_field':'artist'},
                                          last_plan={'goal':'music','steps':[]})
                result = dispatcher.dispatch_user_request('Bad Bunny')
        self.assertEqual(result.state, 'failed')
        self.assertNotIn('429', result.text)
        self.assertNotIn('503', result.text)
        self.assertEqual(dispatcher.context.value('pending_clarification')['missing_field'], 'artist')

    def test_health_recovers_after_cooldown(self):
        pm = self.manager()
        pm._ensure_health()
        pm.health['groq'].cooldown_until = time.monotonic() - 1
        pm.health['groq'].state = 'RATE_LIMITED'
        pm._mark_success('groq', 7)
        self.assertEqual(pm.provider_health()['groq']['state'], 'AVAILABLE')
        self.assertEqual(pm.provider_health()['groq']['consecutive_failures'], 0)

    def test_single_pending_field_can_be_filled_without_provider(self):
        from core.action_loader import ActionRegistry
        from core.provider_manager import ProviderResult
        registry = ActionRegistry({}, lambda _: None)
        registry.register({'name':'test_player','description':'Play selected media.',
                           'parameters':{'type':'OBJECT','properties':{'artist':{'type':'STRING'}},'required':['artist']},
                           'handler':lambda parameters: 'played', 'safe_actions':('*',)})
        class Model:
            def interpret_request(self, *_):
                return ProviderResult(True, 'fixture', '', '{"goal":"play music","domain":"music","confidence":0.5,"steps":[{"tool":"test_player","arguments":{}}],"clarification":{"question":"¿Artista?","missing_field":"artist"},"response":"","confirmation":false,"cancel":false}', 0)
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dispatcher = Dispatcher(PersonalStore(Path(tmp.name)/'personal.db'), registry=registry, provider=Model())
        first = dispatcher.dispatch_user_request('pon música')
        self.assertEqual(first.state, 'pending')
        dispatcher.provider = object()  # would fail if a provider call were needed
        second = dispatcher.dispatch_user_request('Bad Bunny')
        self.assertEqual(second.state, 'executed')
