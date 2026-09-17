import json
import unittest
from unittest.mock import patch, Mock
from core.llm_client import call_llm
from core.semantic import PLAN_SCHEMA
from core.provider_manager import ProviderManager, ProviderResult

class LocalStructuredOutputTests(unittest.TestCase):
    def test_ollama_receives_schema_and_separate_connect_read_timeout(self):
        response=Mock();response.json.return_value={'message':{'content':'{}'}}
        with patch('core.llm_client.get_llm_provider',return_value='ollama'), \
             patch('core.llm_client.get_llm_settings',return_value=('http://localhost:11434','llama3.2')), \
             patch('core.llm_client.requests.post',return_value=response) as post:
            call_llm([],output_schema=PLAN_SCHEMA,timeout=(2,60))
        self.assertEqual(post.call_args.kwargs['json']['format'],PLAN_SCHEMA)
        self.assertEqual(post.call_args.kwargs['timeout'],(2,60))

    def test_bad_local_json_falls_back(self):
        pm=ProviderManager()
        gemini=ProviderResult(True,'gemini','','{}',1)
        with patch.object(pm,'ask_free',return_value=ProviderResult(False,'groq','','',0,'HTTP 429')), \
             patch('core.llm_client.call_llm_generate',return_value='Hola!'), \
             patch.object(pm,'_interpret_gemini',return_value=gemini):
            self.assertIs(pm.interpret_request('greeting',{},[],'instructions'),gemini)
        self.assertEqual(pm.provider_health()['local']['state'],'DEGRADED')

    def test_cooldown_does_not_make_fake_exception_or_extend_deadline(self):
        pm=ProviderManager();pm._mark_failure('local','offline','DISCONNECTED')
        deadline=pm.health['local'].cooldown_until
        with patch.object(pm,'ask_free',return_value=ProviderResult(False,'groq','','',0,'HTTP 429')), \
             patch('core.llm_client.call_llm_generate') as local, \
             patch.object(pm,'_interpret_gemini',return_value=ProviderResult(False,'gemini','','',0,'HTTP 503')):
            self.assertFalse(pm.interpret_request('greeting',{},[],'instructions').ok)
        local.assert_not_called()
        self.assertEqual(pm.health['local'].cooldown_until,deadline)

    def test_local_latency_budget_and_timeout_cooldown(self):
        import requests
        pm=ProviderManager()
        failed=ProviderResult(False,'groq','','',0,'HTTP 429')
        error=RuntimeError('Ollama timed out')
        error.__cause__=requests.exceptions.ReadTimeout('local read timeout')
        with patch.object(pm,'ask_free',return_value=failed), \
             patch('core.llm_client.call_llm_generate',side_effect=error) as local, \
             patch.object(pm,'_interpret_gemini',return_value=ProviderResult(False,'gemini','','',0,'HTTP 503')):
            pm.interpret_request('conversation',{},[],'instructions')
            self.assertEqual(local.call_args.kwargs['timeout'],12)
            pm.interpret_request('next turn',{},[],'instructions')
            self.assertEqual(local.call_count,1)
        self.assertEqual(pm.health['local'].state,'DEGRADED')
        self.assertEqual(pm.cooldowns['local'],60)
