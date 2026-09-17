"""Text runtime integration uses real services and isolated local storage."""
import asyncio
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from core.dispatcher import Dispatcher
from core.personal_store import PersonalStore
from core.wallet import Wallet
from core.work import Work

class TextRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=PersonalStore(Path(self.temp.name)/'personal.db')
        self.events=[]
        self.provider=Mock(); self.provider.ask_free.side_effect=AssertionError('Unexpected network')
        self.d=Dispatcher(self.store,on_event=self.events.append,provider=self.provider)

    def test_wallet_crud_all_sources(self):
        for source in ('chat','home','remote'):
            with self.subTest(source=source):
                self.assertEqual(self.d.dispatch_user_request('Cobré once mil',source).state,'verified')
        self.d.dispatch_user_request('Pagué mil quinientos de universidad')
        self.assertEqual(self.d.dispatch_user_request('Eran 1800').state,'verified')
        self.assertEqual(Wallet(self.store).movements()[0]['amount'],180000)
        self.assertEqual(self.d.dispatch_user_request('Bórralo').state,'pending')
        self.assertEqual(self.d.dispatch_user_request('Sí').state,'verified')
        self.d.dispatch_user_request('Deshaz eso')
        self.assertEqual(Wallet(self.store).movements()[0]['amount'],180000)
        self.assertTrue(self.events)

    def test_work_correction_delete_rebuild(self):
        self.d.dispatch_user_request('Le entregué la ficha 75 a Carlos')
        self.assertEqual(self.d.dispatch_user_request('No era Carlos, era Pedro').state,'verified')
        self.assertEqual(Work(self.store).holder('75')['person'],'Pedro')
        self.assertIn('Pedro',self.d.dispatch_user_request('¿Quién tiene la 75?').text)
        self.assertEqual(self.d.dispatch_user_request('Borra ese evento').state,'pending')
        self.d.dispatch_user_request('Sí')
        self.assertIsNone(Work(self.store).holder('75'))
        self.d.dispatch_user_request('Deshaz eso')
        self.assertEqual(Work(self.store).holder('75')['person'],'Pedro')

    def test_multi_operation_exact_totals(self):
        from tests.test_semantic_runtime import RecordedModel, plan, step
        self.d.provider=RecordedModel(plan(
            step('personal_records',domain='wallet',action='create',kind='income',amount='11000',description='salario'),
            step('personal_records',domain='wallet',action='create',kind='expense',amount='1500',description='universidad'),
            step('personal_records',domain='wallet',action='create',kind='expense',amount='2000',description='mamá'),domain='wallet'))
        result=self.d.dispatch_user_request('Hoy cobré once mil, pagué mil quinientos de universidad y le di dos mil a mi mamá')
        self.assertEqual(result.state,'verified',result.text)
        self.assertEqual(Wallet(self.store).summary()['available'],750000)

    def test_planned_paid_no_double_count(self):
        self.d.dispatch_user_request('Cobré once mil')
        self.d.dispatch_user_request('Voy a pagar 5000 de universidad')
        self.d.dispatch_user_request('Ya pagué lo de universidad')
        self.assertEqual(Wallet(self.store).summary()['available'],600000)
        self.assertEqual(len(Wallet(self.store).movements()),2)

    def test_compound_files_real_storage(self):
        from tests.test_semantic_runtime import RecordedModel, plan, step
        self.d.provider=RecordedModel(plan(step(action='create_folder',path='desktop',name='Nueva carpeta'),
            step(action='create_file',path={'$ref':'steps.0','field':'path'},name='Nuevo archivo.txt',content='')))
        from core.operational_context import CONTEXT
        saved = dict(CONTEXT._items)
        CONTEXT._items.clear()
        self.addCleanup(lambda: setattr(CONTEXT, "_items", saved))
        from actions import file_controller
        with patch('pathlib.Path.home',return_value=Path(self.temp.name)), patch.object(file_controller,'_SAFE_ROOTS',[Path(self.temp.name)]):
            result=self.d.dispatch_user_request('Crea una carpeta en el escritorio y dentro crea un txt')
        self.assertTrue((Path(self.temp.name)/'Desktop'/'Nueva carpeta'/'Nuevo archivo.txt').is_file(),result.text)

    def test_natural_file_query_understands_type_and_location(self):
        from core.local_fastpath import handle
        from actions import file_controller
        with patch('pathlib.Path.home', return_value=Path(self.temp.name)), patch.object(file_controller, '_SAFE_ROOTS', [Path(self.temp.name)]):
            (Path(self.temp.name) / 'Downloads').mkdir()
            (Path(self.temp.name) / 'Downloads' / 'archivo.zip').write_bytes(b'x')
            result = handle('archivos .zip en descargas', personal=False)
        self.assertTrue(result.handled)
        self.assertIn('archivo.zip', result.text)
        self.assertNotIn('ID', result.text)

    def test_natural_desktop_query_and_bulk_move_use_context(self):
        from tests.test_semantic_runtime import RecordedModel, plan, step
        from core.operational_context import CONTEXT
        from actions import file_controller
        saved = dict(CONTEXT._items); CONTEXT._items.clear()
        self.addCleanup(lambda: setattr(CONTEXT, '_items', saved))
        with patch('pathlib.Path.home', return_value=Path(self.temp.name)), patch.object(file_controller, '_SAFE_ROOTS', [Path(self.temp.name)]):
            (Path(self.temp.name) / 'Downloads').mkdir(); (Path(self.temp.name) / 'Desktop').mkdir()
            (Path(self.temp.name) / 'Downloads' / 'uno.py').write_text('x')
            move=step(action='move',path={'$ref':'item','field':'path'},destination='desktop')
            move['foreach']={'$ref':'last_query_results','extension':'.py'}
            self.d.provider=RecordedModel(plan(step(action='list',path='downloads')), plan(move))
            listed = self.d.dispatch_user_request('cuales fueron mis ultimas descargas')
            self.assertTrue(listed.handled)
            moved = self.d.dispatch_user_request('mueve los archivos .py al escritorio')
        self.assertTrue(moved.handled, moved.text)
        self.assertTrue((Path(self.temp.name) / 'Desktop' / 'uno.py').exists())

    def test_offline_no_provider(self):
        from core.network_state import NETWORK
        with patch.object(type(NETWORK),'allowed',new_callable=lambda:property(lambda _:False)):
            self.assertEqual(self.d.dispatch_user_request('Cobré once mil').state,'verified')
            self.assertEqual(self.d.dispatch_user_request('Le entregué la ficha 75 a Carlos').state,'verified')
        self.provider.ask_free.assert_not_called()

    def test_ai_schema_validated_and_no_invented_amount(self):
        from core.intent_interpreter import structured_fallback
        bad=lambda _: {'domain':'wallet','intent':'CREATE','entities':{'kind':'expense','amount':'2000','description':'universidad'},'confidence':.99}
        p=structured_fallback('mil y pico de universidad',bad)
        self.assertEqual(p.confidence,'LOW')
        self.assertIsNone(structured_fallback('hola',lambda _: {'domain':'wallet','intent':'SQL','entities':{}}))

    def test_no_technical_chat_logs(self):
        with self.assertLogs('kira.dispatch',level='INFO') as logs:
            r=self.d.dispatch_user_request('Cobré once mil')
        self.assertNotIn('[INPUT]',r.text)
        self.assertTrue(any('[VERIFY]' in line for line in logs.output))
        self.assertFalse(any('once mil' in line for line in logs.output))

    def test_qt_home_and_chat_to_real_dispatcher(self):
        from PyQt6.QtWidgets import QApplication
        from ui import MainWindow
        app=QApplication.instance() or QApplication([])
        with patch.object(MainWindow,'_load_kira_tasks',return_value=[]), patch.object(MainWindow,'_refresh_provider_status_v4'):
            window=MainWindow('missing.png')
        self.addCleanup(window.close)
        window._metric_tmr.stop()
        import threading
        done=threading.Event()
        def actual_input(text, source="chat"):
            try:self.d.dispatch_user_request(text,source)
            finally:done.set()
        window.on_text_command=actual_input
        window._input.setText('Cobré once mil'); window._send()
        self.assertTrue(done.wait(3)); done.clear()
        window._home_command.setText('Pagué 1500 de universidad'); window._send_home_command()
        self.assertTrue(done.wait(3))
        app.processEvents()
        self.assertEqual(Wallet(self.store).summary()['available'],950000)

    def test_hub_input_uses_same_core(self):
        from PyQt6.QtWidgets import QApplication
        from ui_personal import PersonalPage
        app=QApplication.instance() or QApplication([])
        page=PersonalPage('wallet',self.store); self.addCleanup(page.close)
        self.assertTrue(page.execute('Cobré once mil'))
        self.assertEqual(Wallet(self.store).summary()['income'],1100000)

    def test_task_edit_delete_confirmation(self):
        from actions import kira_tasks
        with patch.object(kira_tasks,'TASK_FILE',Path(self.temp.name)/'tasks.json'):
            self.d.dispatch_user_request('Crea una tarea estudiar')
            self.assertEqual(self.d.dispatch_user_request('Cambia la tarea estudiar a "repasar"').state,'verified')
            self.assertEqual(self.d.dispatch_user_request('Borra la tarea repasar').state,'pending')
            self.assertTrue(kira_tasks._load())
            self.d.dispatch_user_request('Sí')
            self.assertEqual(kira_tasks._load(),[])

    def test_text_boot_never_starts_live_or_audio(self):
        from main import JarvisLive
        controller=object.__new__(JarvisLive)
        controller.ui=Mock()
        controller._voice_state=Mock()
        async def never_ending(): await asyncio.Event().wait()
        controller._local_observers=never_ending
        async def check():
            task=asyncio.create_task(controller.run())
            await asyncio.sleep(.03)
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        with patch('dashboard.server.DashboardServer',side_effect=RuntimeError('isolated test')), patch('main.audio_devices.configure') as configure, patch('main.audio_devices.prefetch') as prefetch, patch.object(JarvisLive,'_build_config',side_effect=AssertionError('Live forbidden')):
            asyncio.run(check())
            configure.assert_not_called(); prefetch.assert_not_called()
        controller._voice_state.assert_called_with('IDLE')

    def test_voice_disabled_by_default(self):
        from core.runtime_config import VOICE_ENABLED
        self.assertFalse(VOICE_ENABLED)

if __name__=='__main__':unittest.main()
