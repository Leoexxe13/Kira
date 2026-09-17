import json
import tempfile
import unittest
from pathlib import Path
from core.personal_store import PersonalStore
from memory.user_memory import UserMemory, use_memory
from core.dispatcher import Dispatcher
from core.action_loader import ActionRegistry
from core.provider_manager import ProviderResult
from actions.user_memory import TOOL

class UserMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PersonalStore(Path(self.tmp.name)/'personal.db')

    def test_restart_and_user_session_isolation(self):
        a=UserMemory(self.store,'trusted:A','one')
        a.remember('preferences','language','Spanish','explicit')
        a.save_state({'pending_operation':{'plan':{'goal':'music','steps':[]},'missing_field':'artist'}})
        restored=UserMemory(PersonalStore(self.store.path),'trusted:A','one')
        self.assertEqual(restored.user_id,a.user_id)
        self.assertEqual(restored.recall()[0]['content'],'Spanish')
        self.assertIn('calls',restored.load_state()['pending_operation'])
        self.assertEqual(restored.load_state()['pending_operation']['created_at'],0)
        second=UserMemory(self.store,'trusted:A','two')
        self.assertTrue(second.recall());self.assertEqual(second.load_state(),{})
        b=UserMemory(self.store,'trusted:B','one')
        self.assertEqual(b.recall(),[]);self.assertEqual(b.load_state(),{})

    def test_update_forget_and_expiry(self):
        a=UserMemory(self.store,'A','s')
        a.remember('preferences','language','Spanish','explicit')
        a.remember('preferences','language','French','correction')
        self.assertEqual([r['content'] for r in a.recall()],['French'])
        self.assertEqual(a.forget('preferences','language'),1)
        self.assertEqual(a.context('language'),[])
        a.remember('notes','old','expired','explicit',expires_at=1)
        self.assertEqual(a.recall(),[])
        with self.assertRaises(ValueError):a.remember('notes','api_key','secret','explicit')

    def test_dispatch_restart_provider_failure_and_context(self):
        registry=ActionRegistry({},lambda _:None);registry.register(TOOL)
        class Provider:
            def interpret_request(self,text,context,*args):
                self.context=context
                return ProviderResult(False,'offline','','',0,'offline')
        provider=Provider()
        d=Dispatcher(self.store,registry,provider=provider,principal='A',session_id='s')
        d.memory.remember('preferences','language','Spanish','explicit')
        d.context.update(pending_operation={'kind':'clarification','plan':{'goal':'music','steps':[]},'missing_field':'artist'},
                         pending_clarification={'question':'Artist?','missing_field':'artist'})
        d.dispatch_user_request('details',source='remote')
        self.assertTrue(provider.context['user_memories'])
        restored=Dispatcher(PersonalStore(self.store.path),registry,provider=provider,principal='A',session_id='s')
        self.assertTrue(restored.context.value('pending_operation'))
        self.assertEqual(restored.semantic.trace,{}) # Restore did not execute.
        self.assertEqual(restored.context.value('pending_operation')['created_at'],0)
        self.assertEqual(restored.memory.user_id,d.memory.user_id)
        b=Dispatcher(self.store,registry,provider=provider,principal='B',session_id='s')
        self.assertFalse(b.context.value('pending_operation'))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_messages WHERE user_id=?',(d.user_id,)).fetchone()[0],2)

    def test_tool_uses_bound_identity_and_cannot_choose_owner(self):
        registry=ActionRegistry({},lambda _:None);registry.register(TOOL)
        a=UserMemory(self.store,'A','s');b=UserMemory(self.store,'B','s')
        with use_memory(a):
            result=registry.run_result('user_memory',{'action':'remember','category':'notes','key':'color','value':'blue'})
            self.assertEqual(result['state'],'verified')
        with use_memory(b):
            self.assertEqual(registry.run_result('user_memory',{'action':'recall'})['data'],[])
        self.assertEqual(registry.run_result('user_memory',{'action':'recall'})['state'],'failed')

    def test_real_dispatch_confirmation_then_restore_and_forget(self):
        from tests.test_semantic_runtime import RecordedModel, plan, step
        registry=ActionRegistry({},lambda _:None);registry.register(TOOL)
        model=RecordedModel(plan(step('user_memory',action='remember',category='preferences',key='language',value='Spanish'),domain='memory'),
                            plan(domain='memory',confirmation=True))
        d=Dispatcher(self.store,registry,provider=model,principal='A',session_id='s')
        self.assertEqual(d.dispatch_user_request('Recuerda que prefiero español').state,'pending')
        self.assertEqual(d.memory.recall(),[])
        self.assertEqual(d.dispatch_user_request('Confirmo').state,'verified')
        self.assertIsNone(d.context.value('pending_operation'))
        restored=Dispatcher(PersonalStore(self.store.path),registry,provider=RecordedModel(plan(response='ok')),principal='A',session_id='s')
        restored.dispatch_user_request('Idioma preferido',source='home')
        self.assertEqual(restored.provider.inputs[0][1]['user_memories'][0]['content'],'Spanish')
        restored.memory.forget('preferences','language')
        d.provider=RecordedModel(plan(response='ok'))
        d.dispatch_user_request('Idioma preferido',source='chat')
        self.assertEqual(d.provider.inputs[0][1]['user_memories'],[])
        self.assertNotIn('Spanish',json.dumps(d.provider.inputs[0][1].get('conversation',[])))

    def test_secret_redaction_and_legacy_read_adapter(self):
        from memory.memory_manager import load_memory
        memory=UserMemory(self.store,'A','s')
        memory.remember('preferences','language','Spanish','explicit')
        with use_memory(memory):
            self.assertEqual(load_memory()['preferences']['language']['value'],'Spanish')
        with self.assertRaises(ValueError):
            memory.remember('notes','connection','api_key=super-secret-value','explicit')
        memory.message('user','password=super-secret-value')
        with self.store.transaction() as db:
            self.assertNotIn('super-secret-value',db.execute('SELECT content FROM memory_messages WHERE user_id=?',(memory.user_id,)).fetchone()[0])

    def test_persistence_in_a_new_python_process(self):
        import subprocess
        import sys
        memory=UserMemory(self.store,'trusted-A','session-1')
        memory.remember('preferences','language','Spanish','explicit')
        memory.save_state({'pending_operation':{'plan':{'goal':'send','steps':[]},'calls':[
            {'tool':'whatsapp_web','arguments':{'action':'send','chat':'recipient','message':'body'}}]}})
        program="""
import json,sys
from core.personal_store import PersonalStore
from memory.user_memory import UserMemory
m=UserMemory(PersonalStore(sys.argv[1]),'trusted-A','session-1')
print(json.dumps({'facts':m.recall(),'state':m.load_state()}))
"""
        result=json.loads(subprocess.check_output([sys.executable,'-c',program,str(self.store.path)],text=True))
        self.assertEqual(result['facts'][0]['content'],'Spanish')
        self.assertEqual(result['state']['pending_operation']['calls'][0]['arguments']['message'],'body')
        self.assertEqual(result['state']['pending_operation']['created_at'],0)
