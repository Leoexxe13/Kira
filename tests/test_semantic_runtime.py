"""Integration below a recorded model boundary; no mocks in registry/files/SQLite.

These tests verify execution/context/safety, not a model's linguistic quality.
The read-only live evaluation in scripts/test_kira_dispatch.py tests that separately.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.action_loader import ActionRegistry
from core.dispatcher import Dispatcher
from core.personal_store import PersonalStore
from core.provider_manager import ProviderResult
from core.wallet import Wallet
from core.work import Work
from actions.file_controller import TOOL as FILES
from actions.personal_records import TOOL as PERSONAL


def plan(*steps, domain='files', question=None, **extra):
    result = dict(goal='requested_goal', domain=domain, confidence=.97, steps=list(steps),
                  clarification=question, response='')
    result.update(extra)
    return result


def step(tool='file_controller', **arguments):
    return {'tool':tool,'arguments':arguments}


class RecordedModel:
    def __init__(self, *plans): self.plans=list(plans); self.inputs=[]
    def interpret_request(self,text,context,capabilities,instructions):
        self.inputs.append(copy.deepcopy((text,context,capabilities)))
        output=self.plans.pop(0)
        return ProviderResult(True,'recorded-model','fixture',json.dumps(output),0)


class SemanticRuntimeTests(unittest.TestCase):
    def test_nested_reference_uses_data_only(self):
        from core.semantic import reference
        outputs=[[{'path':'/synthetic/report.pdf'}]]
        self.assertEqual(reference({'$ref':'steps.0[0].path'}, {}, outputs), '/synthetic/report.pdf')
        with self.assertRaises(ValueError):
            reference({'$ref':'steps.0.__class__'}, {}, outputs)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        for name in ('Desktop','Downloads','Documents'): (self.home/name).mkdir()
        self.store=PersonalStore(self.home/'personal.db')
        self.registry=ActionRegistry({},lambda _:None)
        self.registry.register(FILES); self.registry.register(PERSONAL)
        self.events=[]
        self.addCleanup(patch.stopall)
        patch('pathlib.Path.home',return_value=self.home).start()
        patch('actions.file_controller._SAFE_ROOTS',[self.home]).start()

    def dispatcher(self,*plans):
        self.model=RecordedModel(*plans)
        self.d=Dispatcher(self.store,self.registry,self.events.append,self.model)
        return self.d

    def test_query_then_indirect_bulk_move_real_files(self):
        for name in ('one.py','two.py','notes.pdf'): (self.home/'Downloads'/name).write_text(name)
        move=step(action='move',path={'$ref':'item','field':'path'},destination='desktop')
        move['foreach']={'$ref':'last_query_results','extension':'.py'}
        d=self.dispatcher(plan(step(action='list',path='downloads')),plan(move))
        d.dispatch_user_request('Enséñame lo recién bajado')
        result=d.dispatch_user_request("Esos de Python, pásamelos pa'l escritorio")
        self.assertEqual(result.state,'verified',result.text)
        self.assertTrue((self.home/'Desktop'/'one.py').exists())
        self.assertTrue((self.home/'Desktop'/'two.py').exists())
        self.assertTrue((self.home/'Downloads'/'notes.pdf').exists())
        self.assertEqual(len(self.model.inputs[1][1]['last_query_results']),3)

    def test_missing_artist_continues_without_verb(self):
        calls=[]
        def play(parameters):
            calls.append(parameters)
            return 'Solicitud de reproducción recibida.'
        self.registry.register({'name':'test_player','description':'Reproduce música por artista',
            'parameters':{'type':'OBJECT','properties':{'artist':{'type':'STRING'}},'required':['artist']},
            'handler':play,'safe_actions':('*',)})
        d=self.dispatcher(plan(domain='music',question={'question':'¿Qué artista?','missing_field':'artist'}),
                          plan(step('test_player',artist='Bad Bunny'),domain='music'))
        self.assertEqual(d.dispatch_user_request('Anímame el ambiente con algo').state,'pending')
        result=d.dispatch_user_request('de Bad Bunny')
        self.assertEqual(calls,[{'artist':'Bad Bunny'}])
        self.assertEqual(result.state,'executed')  # fixture does not prove playback
        self.assertEqual(self.model.inputs[1][1]['pending_clarification']['missing_field'],'artist')

    def test_failed_move_retry_then_destination_change(self):
        source=self.home/'Downloads'/'a.txt'; source.write_text('a')
        (self.home/'Desktop'/'a.txt').write_text('conflict')
        d=self.dispatcher(plan(step(action='move',path=str(source),destination='desktop')),
                          plan(step(action='move',path=str(source),destination='desktop')),
                          plan(step(action='move',path=str(source),destination='desktop')),
                          plan(step(action='move',path=str(source),destination='documents')))
        self.assertEqual(d.dispatch_user_request('Lleva el adjunto al escritorio').state,'failed')
        (self.home/'Desktop'/'a.txt').unlink()
        self.assertEqual(d.dispatch_user_request('Dale otra oportunidad').state,'verified')
        self.assertIn('last_failed_operation',self.model.inputs[1][1])
        # Next proposal must use the actual moved path, not the stale source.
        self.model.plans[0]['steps'][0]['arguments']['path']=str(self.home/'Desktop'/'a.txt')
        self.assertEqual(d.dispatch_user_request('Mejor en documentos').state,'verified')
        self.assertTrue((self.home/'Documents'/'a.txt').exists())

    def test_multi_step_uses_observed_latest_pdf(self):
        import os
        a=self.home/'Downloads'/'a.pdf'; b=self.home/'Downloads'/'b.pdf'
        a.write_text('old'); b.write_text('new'); os.utime(a,(10,10)); os.utime(b,(20,20))
        d=self.dispatcher(plan(step(action='find',path='downloads',extension='.pdf'),
            step(action='move',path={'$ref':'steps.0','sort':'modified','descending':True,'index':0,'field':'path'},destination='desktop')))
        self.assertEqual(d.dispatch_user_request('Sácame al escritorio el PDF más fresco que haya bajado').state,'verified')
        self.assertTrue((self.home/'Desktop'/'b.pdf').exists()); self.assertTrue(a.exists())

    def test_personal_typed_services_storage_and_event(self):
        d=self.dispatcher(plan(step('personal_records',domain='wallet',action='create',kind='income',amount='11000',description='salario'),domain='wallet'),
            plan(step('personal_records',domain='work',action='assign',resource='75',person='Carlos'),domain='work'))
        self.assertEqual(d.dispatch_user_request('Entraron once mil a mi bolsillo').state,'verified')
        self.assertEqual(Wallet(self.store).summary()['income'],1100000)
        self.assertEqual(d.dispatch_user_request('El control 75 quedó en manos de Carlos').state,'verified')
        self.assertEqual(Work(self.store).holder('75')['person'],'Carlos')
        self.assertEqual(self.events[-1]['domain'],'work')

    def test_destructive_confirmation_bound_to_rows(self):
        mid=Wallet(self.store).record('expense','1500','Universidad')
        d=self.dispatcher(plan(step('personal_records',domain='wallet',action='delete',ids=[mid]),domain='wallet'),
                          plan(domain='wallet',confirmation=True))
        d.context.update(last_wallet_transaction=Wallet(self.store).movements()[0])
        self.assertEqual(d.dispatch_user_request('Retira ese apunte').state,'pending')
        self.assertTrue(Wallet(self.store).movements())
        self.assertEqual(d.dispatch_user_request('Adelante con lo que acabas de mostrar').state,'verified')
        self.assertFalse(Wallet(self.store).movements())

    def test_stale_confirmation_cannot_delete_changed_record(self):
        from core.personal_edits import PersonalEdits
        mid=Wallet(self.store).record('expense','1500','Universidad')
        d=self.dispatcher(plan(step('personal_records',domain='wallet',action='delete',ids=[mid]),domain='wallet'),plan(domain='wallet',confirmation=True))
        d.context.update(last_wallet_transaction=Wallet(self.store).movements()[0])
        d.dispatch_user_request('Retira el registro seleccionado')
        PersonalEdits(self.store).change('wallet',Wallet(self.store).movements(),{'amount':'1700'})
        self.assertEqual(d.dispatch_user_request('Dale adelante').state,'pending')
        self.assertEqual(Wallet(self.store).movements()[0]['amount'],170000)

    def test_unknown_capability_rejected_without_execution(self):
        d=self.dispatcher(plan(step('imaginary_tool',path='desktop')))
        self.assertEqual(d.dispatch_user_request('Atiende esto como sabes').state,'pending')
        self.assertEqual(d.semantic.trace['tools_selected'],[])

    def test_real_capabilities_passed_with_schemas(self):
        d=self.dispatcher(plan(response='Puedo gestionar archivos registrados.'))
        d.dispatch_user_request('¿Con qué recursos cuentas?')
        caps={v['name']:v for v in self.model.inputs[0][2]}
        self.assertEqual(caps['file_controller']['parameters'],FILES['parameters'])
        self.assertIn('personal_records',caps)

    def test_malformed_argument_never_reaches_storage(self):
        d=self.dispatcher(plan(step(action='move',path=123,destination='desktop')))
        result=d.dispatch_user_request('Colócalo en otro lugar')
        self.assertEqual(result.state,'failed')
        self.assertEqual(d.semantic.trace['execution'],[])

    def test_provider_failure_does_not_claim_action(self):
        d=self.dispatcher()
        self.model.interpret_request=lambda *a:ProviderResult(False,'none','','',0,'unavailable')
        self.assertEqual(d.dispatch_user_request('Haz lo que te comenté').state,'failed')
        self.assertEqual(d.semantic.trace['execution'],[])

    def test_task_change_confirmation_does_not_authorize_new_arguments(self):
        d=self.dispatcher(plan(step(action='delete',path='documents')),
                          plan(step(action='delete',path='desktop'),confirmation=True))
        self.assertEqual(d.dispatch_user_request('Retira la carpeta indicada').state,'pending')
        self.assertEqual(d.dispatch_user_request('Sí, esa').state,'pending')
        self.assertTrue((self.home/'Documents').exists())

    def test_session_shared_for_home_chat_hub(self):
        from core.dispatcher import shared_dispatcher
        first=shared_dispatcher(self.store,registry=self.registry)
        self.assertIs(first,shared_dispatcher(self.store))

    def test_paraphrases_not_embedded_in_production(self):
        root=Path(__file__).resolve().parents[1]
        source='\n'.join(p.read_text() for directory in ('core','actions') for p in (root/directory).glob('*.py'))
        for phrase in ('Esos de Python, pásamelos', 'Anímame el ambiente', 'Dale otra oportunidad', 'Sácame al escritorio el PDF más fresco'):
            self.assertNotIn(phrase,source)
