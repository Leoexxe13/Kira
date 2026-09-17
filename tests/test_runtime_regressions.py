"""Regression boundaries: real dispatcher/storage/registry, synthetic model/browser."""
import copy
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from core.action_loader import ActionRegistry
from core.dispatcher import Dispatcher
from core.personal_edits import PersonalEdits
from core.personal_store import PersonalStore
from core.provider_manager import ProviderManager, ProviderResult
from core.text_tools import whatsapp_result
from core.work import Work
from tests.test_semantic_runtime import RecordedModel, plan, step
from tests import test_whatsapp_safety as whatsapp_fixture


class RuntimeRegressionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PersonalStore(Path(temp.name) / 'personal.db')
        self.registry = ActionRegistry({}, lambda _: None)
        self.browser = whatsapp_fixture.SafetyTests()
        self.browser.setUp()
        self.worker = self.browser.w
        self.worker._chat_rows = Mock(return_value=[{'id':'test@lid','name':'Contacto Prueba','aliases':[]}])
        self.worker._contact_rows = Mock(return_value=[])
        self.worker._active_chat = Mock(return_value={'id':'test@lid','name':'Contacto Prueba'})
        self.calls = []
        def backend(parameters):
            self.calls.append(copy.deepcopy(parameters))
            return self.worker._dispatch(parameters)
        tool = dict(whatsapp_fixture.namespace['TOOL'])
        tool['handler'] = backend
        tool['structured_handler'] = lambda parameters: whatsapp_result(backend(parameters))
        self.registry.register(tool)

    def dispatcher(self, *plans):
        self.model = RecordedModel(*plans)
        return Dispatcher(self.store, self.registry, provider=self.model)

    def test_compound_recipient_request_uses_model_then_verified_tool(self):
        d = self.dispatcher(plan(step('whatsapp_web',action='send',chat='Contacto Prueba',message='está bien'),domain='whatsapp'),
                            plan(domain='whatsapp',confirmation=True))
        r = d.dispatch_user_request('abre el chat de Contacto Prueba y escríbele que está bien')
        self.assertEqual(r.state,'pending',r.text)
        self.assertEqual(self.calls, [])
        r = d.dispatch_user_request('Confirmo la operación propuesta')
        self.assertEqual(r.state,'verified',r.text)
        self.assertEqual(self.calls,[{'action':'send','chat':'Contacto Prueba','message':'está bien'}])
        self.worker._send.assert_called_once_with(self.browser.page,'test@lid','está bien')

    def test_compound_text_adapter_preserves_recipient_and_message(self):
        from core.text_tools import WhatsAppText
        adapter=WhatsAppText()
        registry=Mock(); registry.has.return_value=True
        registry.run.return_value='WHATSAPP_VERIFIED_SENT: {"chat":"Contacto Prueba","verified":true}'
        result=adapter.handle('abre el chat de Contacto Prueba y escríbele que está lloviendo mucho',registry)
        self.assertEqual(result.state,'verified',result.text)
        registry.run.assert_called_once_with('whatsapp_web',{'action':'send','chat':'Contacto Prueba','message':'está lloviendo mucho'})

    def test_recipient_clarification_preserves_call_and_message(self):
        d = self.dispatcher(plan(step('whatsapp_web',action='send',chat='Nombre incorrecto',message='texto original'),domain='whatsapp'),
                            plan(domain='whatsapp',confirmation=True),
                            plan(step('whatsapp_web',action='send',chat='Contacto Prueba',message='texto original'),domain='whatsapp'),
                            plan(domain='whatsapp',confirmation=True))
        d.dispatch_user_request('Escríbele a Nombre incorrecto que texto original')
        r=d.dispatch_user_request('Sí')
        self.assertEqual(r.state,'pending',r.text)
        pending=d.context.value('pending_clarification')
        self.assertEqual(pending['call']['arguments']['message'],'texto original')
        self.assertEqual(pending['data']['query'],'Nombre incorrecto')
        self.worker._send.assert_not_called()
        d.dispatch_user_request('Contacto Prueba')
        self.assertEqual(self.model.inputs[2][1]['pending_clarification'],pending)
        r=d.dispatch_user_request('Sí')
        self.assertEqual(r.state,'verified',r.text)
        self.worker._send.assert_called_once_with(self.browser.page,'test@lid','texto original')

    def test_wrong_chat_remains_failed_not_clarification_or_sent(self):
        raw='WHATSAPP_SEND_BLOCKED_WRONG_RECIPIENT: '+json.dumps({'reason':'ID diferente','expected':{'id':'a'}})
        self.assertEqual(whatsapp_result(raw)['state'],'failed')
        self.assertEqual(whatsapp_result('WHATSAPP_UNVERIFIED: '+json.dumps({'query':'x','expected':{'id':'a'},'candidates':[]}))['state'],'failed')

    def test_split_open_send_keeps_explicit_recipient_after_chat_changes(self):
        d=self.dispatcher(plan(step('whatsapp_web',action='open',chat='Contacto Prueba'),
                               step('whatsapp_web',action='send',message='texto original'),domain='whatsapp'),
                          plan(domain='whatsapp',confirmation=True))
        self.assertEqual(d.dispatch_user_request('Abre a Contacto Prueba y dile que texto original').state,'pending')
        self.worker._active_chat.return_value={'id':'other@lid','name':'Otra persona'}
        result=d.dispatch_user_request('Sí')
        self.assertEqual(self.calls[-1]['chat'],'Contacto Prueba')
        self.assertEqual(result.state,'failed',result.text)
        self.worker._send.assert_not_called()

    def test_real_structured_wrapper_passes_supported_arguments(self):
        from actions import whatsapp_web
        self.registry.register(whatsapp_web.TOOL)
        with patch.object(whatsapp_web,'whatsapp_web',autospec=True,return_value='WHATSAPP_VERIFIED_OPEN: {"open":true}'):
            result=self.registry.run_result('whatsapp_web',{'action':'open'},{'speak':object()})
        self.assertEqual(result['state'],'verified',result)

    def test_deleted_assignment_is_explained_and_not_restored(self):
        from actions.personal_hub import TOOL
        self.registry.register(TOOL)
        d=self.dispatcher()
        self.assertEqual(d.dispatch_user_request('Le di la ficha 23 a Dancel').state,'verified')
        self.assertIn('Dancel',d.dispatch_user_request('quien tiene la ficha 23 ?').text)
        PersonalEdits(self.store).change('work',Work(self.store).events(),delete=True)
        result=d.dispatch_user_request('quien tiene la ficha 23 ?')
        self.assertIn('eliminados',result.text)
        self.assertIsNone(Work(self.store).holder('23'))
        self.assertEqual(Work(self.store).events(),[])
        PersonalEdits(self.store).undo('work')
        self.assertIn('Dancel',d.dispatch_user_request('quien tiene la ficha 23 ?').text)

    def test_missing_projection_ignores_notes_and_preserves_correction(self):
        work=Work(self.store)
        work.event('assign','assignment',person='A',resource='23')
        work.event('correction','corrected',person='B',resource='23')
        work.event('note','note about resource',resource='23')
        with self.store.transaction() as db: db.execute('DELETE FROM resources')
        row=work.holder('23')
        self.assertEqual((row['person'],row['state']),('B','assigned'))

    def test_unavailable_interpreter_keeps_pending_call(self):
        d=self.dispatcher()
        pending={'question':'¿Qué contacto?','call':{'tool':'whatsapp_web','arguments':{'action':'send','message':'texto'}}}
        d.context.update(pending_clarification=pending)
        self.model.interpret_request=lambda *args: ProviderResult(False,'none','','',0,'Groq: HTTP 429. Modelo local desconectado.')
        result=d.dispatch_user_request('Contacto Prueba')
        self.assertEqual(result.state, 'failed')
        self.assertNotIn('HTTP 429',result.text)
        self.assertNotIn('resultado quieres conseguir',result.text)
        self.assertEqual(d.context.value('pending_clarification'),pending)
        self.assertFalse(self.calls)


class ProviderDiagnosticsTests(unittest.TestCase):
    def test_local_success_stops_fallback(self):
        pm=object.__new__(ProviderManager)
        unavailable=ProviderResult(False,'groq','','',0,'HTTP 429')
        with patch.object(pm,'ask_free',return_value=unavailable), patch('core.llm_client.call_llm',return_value={'content':'{}'}), patch.object(pm,'_interpret_gemini') as gemini:
            result=pm.interpret_request('synthetic',{},[],'schema')
        self.assertEqual(result.provider,'local')
        gemini.assert_not_called()

    def test_gemini_text_fallback_after_online_and_local_failure(self):
        pm=object.__new__(ProviderManager)
        unavailable=ProviderResult(False,'groq','','',0,'HTTP 429')
        fallback=ProviderResult(True,'gemini','fixture','{}',0)
        with patch.object(pm,'ask_free',return_value=unavailable), patch('core.llm_client.call_llm',side_effect=TimeoutError), patch.object(pm,'_interpret_gemini',return_value=fallback) as gemini:
            result=pm.interpret_request('synthetic',{},[],'schema')
        self.assertIs(result,fallback)
        gemini.assert_called_once()

    def test_http_error_is_safe_and_specific(self):
        pm=object.__new__(ProviderManager)
        pm.cfg=copy.deepcopy(ProviderManager.DEFAULTS)
        pm.cfg['providers']['groq'].update(enabled=True,api_key='synthetic-secret')
        from core.network_state import NetworkState
        error=urllib.error.HTTPError('https://invalid/synthetic-secret',429,'sensitive details',{},None)
        with patch.object(pm,'reload'), patch('core.network_state.NETWORK',NetworkState()), patch('urllib.request.urlopen',side_effect=error):
            result=pm.ask_free('synthetic',structured=True)
        self.assertIn('HTTP 429',result.error)
        self.assertNotIn('synthetic-secret',result.error)
        self.assertNotIn('sensitive',result.error)

    def test_fallback_failure_retains_both_provider_causes(self):
        import requests
        pm=object.__new__(ProviderManager)
        online=ProviderResult(False,'groq','','',0,'Groq: HTTP 429')
        local=RuntimeError('El modelo local no está disponible')
        local.__cause__=requests.ConnectionError('sensitive endpoint')
        with patch.object(pm,'ask_free',return_value=online), patch('core.llm_client.call_llm',side_effect=local), patch.object(pm, '_interpret_gemini', return_value=ProviderResult(False,'gemini','','',0,'Gemini HTTP 503')):
            result=pm.interpret_request('test',{},[],'schema')
        self.assertFalse(result.ok)
        self.assertIn('429',result.error)
        self.assertIn('servidor del modelo local',result.error)
        self.assertNotIn('sensitive',result.error)
