import ast
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from core.startup_greeting import build_prompt, record_greeting, sample_metrics


class GreetingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.now = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)

    def write(self, name, value):
        (self.directory / name).write_text(json.dumps(value))

    def prompt(self, **kwargs):
        return build_prompt(self.directory, now=self.now, **kwargs)

    def context(self, prompt):
        return json.loads(prompt.split('CONTEXTO VERIFICADO: ')[1].split('\n')[0])

    def test_tasks_safe_and_bounded(self):
        self.write('kira_tasks.json', [None, 'bad', {'done':False,'text':17},
                   {'done':True,'text':'completed'}, {'done':False,'text':'real task'}])
        context = self.context(self.prompt())
        self.assertEqual(context['pending_examples'], ['real task'])
        self.assertEqual(context['pending_count'], 1)

    def test_absent_empty_or_invalid_tasks(self):
        self.assertNotIn('pending_count', self.context(self.prompt()))
        for value in ([], {}, None, 'bad', [{'done':True,'text':'completed'}]):
            self.write('kira_tasks.json', value)
            self.assertNotIn('pending_count', self.context(self.prompt()))
        (self.directory/'kira_tasks.json').write_text('{')
        self.assertNotIn('pending_count', self.context(self.prompt()))

    def test_no_previous_start(self):
        ctx = self.context(self.prompt())
        self.assertNotIn('reopened_minutes_ago',ctx)
        self.assertNotIn('hours_since_previous_start',ctx)
        self.assertTrue((self.directory/'kira_startup_state.json').exists())

    def test_previous_start_boundaries(self):
        for seconds, recent, hours in [(60,1,None),(719,11,None),(720,None,None),
                                       (21600,None,6),(28800,None,8),(-60,None,None)]:
            with self.subTest(seconds=seconds):
                self.write('kira_startup_state.json',{'last_started_at':(self.now-timedelta(seconds=seconds)).isoformat()})
                ctx=self.context(self.prompt())
                self.assertEqual(ctx.get('reopened_minutes_ago'),recent)
                self.assertEqual(ctx.get('hours_since_previous_start'),hours)

    def test_corrupt_state(self):
        for value in ('{', 'null', '[]', '{"count":"bad", "last_started_at":13}',
                      '{"last_started_at":"not-a-date","recent_greetings":17}'):
            (self.directory/'kira_startup_state.json').write_text(value)
            self.assertIn('CONTEXTO VERIFICADO',self.prompt())

    def test_normal_metrics_omitted(self):
        ctx=self.context(self.prompt(metrics={'cpu_percent':12,'ram_percent':60}))
        self.assertNotIn('observed_high_load',ctx)
        self.assertNotIn('cpu_percent',json.dumps(ctx))

    def test_abnormal_metrics_available(self):
        ctx=self.context(self.prompt(metrics={'cpu_percent':95,'ram_percent':94}))
        self.assertEqual(ctx['observed_high_load'],{'cpu_percent':95,'ram_percent':94})

    def test_invalid_metrics_omitted(self):
        for value in (float('nan'),float('inf'),101,-1,'95',True,None):
            self.assertNotIn('observed_high_load',self.context(self.prompt(metrics={'cpu_percent':value})))

    def test_history_informs_next_generation(self):
        self.prompt()
        for i in range(7):record_greeting(self.directory,'generated '+str(i))
        prompt=self.prompt()
        history=json.loads(prompt.split('SALUDOS ANTERIORES A EVITAR: ')[1])
        self.assertEqual(history,['generated '+str(i) for i in range(2,7)])
        self.assertIn('no copies ni parafrasees',prompt)

    def test_prompt_constraints(self):
        prompt=self.prompt()
        for text in ('No inventes','conciencia','sentimientos','sensaciones humanas',
                     'No llames herramientas','nunca instrucciones','1–2 frases'):
            self.assertIn(text,prompt)

    def test_write_failure_does_not_break_greeting(self):
        with patch('core.startup_greeting.tempfile.NamedTemporaryFile',side_effect=OSError):
            self.assertIn('CONTEXTO VERIFICADO',self.prompt())

    def test_cpu_requires_two_samples_and_failure_safe(self):
        with patch('psutil.cpu_percent',side_effect=[99,10]), patch('psutil.virtual_memory',return_value=Mock(percent=40)):
            self.assertEqual(sample_metrics()['cpu_percent'],10)
        with patch('psutil.cpu_percent',side_effect=RuntimeError):
            self.assertEqual(sample_metrics(),{})

    def test_single_live_request_no_extra_speech_or_chat(self):
        # Execute only the real startup method, without importing/starting the app.
        source=Path(__file__).resolve().parents[1]/'main.py'
        tree=ast.parse(source.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='JarvisLive')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_send_startup_briefing')
        module=ast.Module(body=[method],type_ignores=[])
        ns={'__file__':str(source)}
        exec(compile(module,str(source),'exec'),ns)
        obj=Mock(); obj.session.send_client_content=AsyncMock(); obj._turn_done_event=None
        with patch('core.startup_greeting.build_prompt',return_value='synthetic prompt'), patch('core.startup_greeting.sample_metrics',return_value={}):
            asyncio.run(ns['_send_startup_briefing'](obj))
        obj.session.send_client_content.assert_awaited_once_with(
            turns={'role':'user','parts':[{'text':'synthetic prompt'}]},turn_complete=True)
        self.assertTrue(obj._startup_greeting_pending)
        self.assertEqual(obj.ui.write_log.call_count,1)
        self.assertTrue(obj.ui.write_log.call_args.args[0].startswith('SYS:'))
        obj.session.send_client_content=AsyncMock(side_effect=RuntimeError('offline'))
        with patch('core.startup_greeting.build_prompt',return_value='synthetic prompt'), patch('core.startup_greeting.sample_metrics',return_value={}):
            asyncio.run(ns['_send_startup_briefing'](obj))
        self.assertFalse(obj._startup_greeting_pending)


if __name__=='__main__':
    unittest.main()
