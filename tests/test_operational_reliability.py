"""Offline integration tests; synthetic files, no personal data or OS actions."""
import asyncio
import io
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.network_state import NetworkState, State
from core.operational_context import OperationalContext
from core.resource_resolver import resolve_path
from core.download_watch import DownloadWatcher
from core.operation_guard import RetryGuard, Outcome, ResultState
from core.tool_feedback import EventSummary, classify_result
from core.vision_context import validate_frame, vision_intent, ProductObservation


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.context = OperationalContext()

    def test_aliases(self):
        for prefix in ('downloads', 'descargas', 'Downloads'):
            self.assertEqual(resolve_path(prefix+'/foto.jpg', home=self.home), self.home/'Downloads/foto.jpg')
        self.assertEqual(resolve_path('~/Documents/a.txt', home=self.home), self.home/'Documents/a.txt')

    def test_missing_reference_never_defaults(self):
        with self.assertRaises(ValueError):
            resolve_path('esa carpeta', context=self.context)

    def test_expiration_and_deleted_resource(self):
        now = [0]
        context = OperationalContext(clock=lambda: now[0], ttl=30)
        path = self.home/'x'; path.touch()
        context.resource(path)
        self.assertEqual(context.get('last_file'), path)
        now[0] = 30
        self.assertIsNone(context.get('last_file'))
        context.resource(path); path.unlink()
        self.assertIsNone(context.get('last_file'))

    def test_folder_then_file_same_exact_folder_and_no_duplicate(self):
        from core.local_fastpath import handle
        from actions import file_controller as files
        with patch('pathlib.Path.home', return_value=self.home), patch('core.operational_context.CONTEXT', self.context), patch('core.resource_resolver.CONTEXT', self.context), patch.object(files, 'CONTEXT', self.context), patch.object(files, '_is_safe_path', return_value=True), patch.object(files, 'push_undo'):
            first = handle('Créame una carpeta en el escritorio con el nombre que quieras')
            self.assertTrue(first.ok, first.text)
            folder = self.home/'Desktop/Nueva carpeta'
            self.assertEqual(resolve_path('esa carpeta', context=self.context), folder)
            second = handle('Dentro de esa carpeta crea un txt llamado hola mundo y pon dentro hola mundo')
            self.assertTrue(second.ok, second.text)
            self.assertEqual((folder/'hola mundo.txt').read_text(), 'hola mundo')
            self.assertFalse((folder.parent/'hola mundo.txt').exists())
            self.assertTrue(handle('Lo pusiste fuera de la carpeta').ok)
            self.assertTrue(handle('Crea una carpeta en el escritorio con nombre Nueva carpeta').ok)
            self.assertEqual(list(folder.parent.iterdir()), [folder])

    def test_new_download_only_after_stable_poll(self):
        (self.home/'old.txt').touch()
        watcher = DownloadWatcher(self.home, context=self.context)
        self.assertEqual(watcher.poll(), [])
        path = self.home/'image.jpg'; path.write_bytes(b'synthetic')
        (self.home/'incomplete.crdownload').touch()
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(watcher.poll(), [path])
        self.assertEqual(self.context.get('last_download'), path)
        self.assertEqual(resolve_path('esa imagen', context=self.context), path)
        self.assertEqual(watcher.poll(), [])

    def test_download_off(self):
        watcher = DownloadWatcher(self.home, mode='off', context=self.context)
        (self.home/'x').touch()
        self.assertEqual(watcher.poll(), [])
        self.assertIsNone(self.context.get('last_download'))

    def test_find_then_wallpaper_keeps_absolute_path(self):
        from actions import file_controller as files
        from core.local_fastpath import handle
        path = self.home/'Downloads/photo.jpg'; path.parent.mkdir(); path.touch()
        with patch('pathlib.Path.home', return_value=self.home), patch('core.operational_context.CONTEXT', self.context), patch('core.resource_resolver.CONTEXT', self.context), patch.object(files, 'CONTEXT', self.context), patch.object(files, '_is_safe_path', return_value=True), patch('actions.desktop.set_wallpaper', return_value='VERIFICADO: fondo aplicado') as wallpaper:
            found = handle('Busca photo.jpg en descargas')
            self.assertIn(str(path), found.text)
            result = handle('Pon esa imagen de fondo')
            self.assertTrue(result.ok, result.text)
            wallpaper.assert_called_once_with(str(path))

    def test_tasks_and_reminders_without_network(self):
        from core.local_fastpath import handle
        from actions import kira_tasks
        with patch.object(kira_tasks, 'TASK_FILE', self.home/'tasks.json'), patch('core.local_fastpath._schedule') as schedule, patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            self.assertTrue(handle('Agrega estudiar a pendientes').ok)
            self.assertEqual(kira_tasks._load()[0]['text'], 'estudiar')
            self.assertTrue(handle('Completa la tarea estudiar').ok)
            self.assertTrue(kira_tasks._load()[0]['done'])
            self.assertTrue(handle('Recuérdame descansar en 10 minutos').ok)
            schedule.assert_called_once_with(600, 'KIRA // RECORDATORIO', 'Recuerda: descansar')


class NetworkTests(unittest.TestCase):
    def test_dns_offline_and_recover(self):
        clock = [0]
        probe = Mock(return_value=False)
        network = NetworkState(clock=lambda: clock[0], probe=probe)
        self.assertTrue(network.failure(socket.gaierror('synthetic')))
        self.assertEqual(network.state, State.OFFLINE)
        self.assertFalse(network.allowed)
        self.assertFalse(network.recover())
        self.assertFalse(network.recover())
        probe.assert_called_once()
        clock[0]=15; probe.return_value=True
        self.assertTrue(network.recover())
        self.assertEqual(network.state, State.ONLINE)

    def test_service_error_does_not_claim_no_internet(self):
        network = NetworkState(probe=Mock())
        self.assertFalse(network.failure(RuntimeError('HTTP 401')))
        self.assertEqual(network.state, State.DEGRADED)
        self.assertTrue(network.allowed)

    def test_single_recovery(self):
        network = NetworkState()
        network.transition(State.OFFLINE)
        def probe():
            self.assertFalse(network.recover())
            return True
        network.probe=probe
        self.assertTrue(network.recover())

    def test_offline_provider_never_calls_groq(self):
        from core.provider_manager import ProviderManager
        pm = object.__new__(ProviderManager)
        network=NetworkState(); network.transition(State.OFFLINE)
        with patch('core.network_state.NETWORK', network), patch('urllib.request.urlopen') as request, patch.object(pm, 'reload') as reload:
            self.assertFalse(pm.ask_free('synthetic').ok)
            request.assert_not_called(); reload.assert_not_called()

    def test_local_llm_rejects_remote_url(self):
        from core.llm_client import call_llm
        with patch('core.llm_client.get_llm_settings', return_value=('https://example.invalid','model')), patch('core.llm_client.get_llm_provider', return_value='ollama'), patch('requests.post') as post:
            with self.assertRaises(ValueError):call_llm([],local_only=True,allow_restart=False)
            post.assert_not_called()


class ResultTests(unittest.TestCase):
    def test_failure_not_promoted(self):
        outcome=Outcome(ResultState.FAILED,'failed').verify(True)
        self.assertEqual(outcome.state,ResultState.FAILED)
        self.assertEqual(Outcome(ResultState.REQUESTED,'pending').verify(False).state,ResultState.REQUESTED)

    def test_two_failures_block_both_browsers(self):
        guard=RetryGuard()
        error="unexpected keyword argument 'speak'"
        guard.record('browser',{'browser':'Chrome'},error,True)
        self.assertIsNone(guard.blocked('browser',{'browser':'Safari'}))
        guard.record('browser',{'browser':'Chrome'},error,True)
        self.assertIsNotNone(guard.blocked('browser',{'browser':'Safari'}))
        self.assertIsNone(guard.blocked('browser',{'browser':'Safari'},generation=1))

    def test_retry_expiration_and_different_parameters(self):
        now=[0]; guard=RetryGuard(clock=lambda:now[0])
        for _ in range(2):guard.record('find',{'name':'a'},'not found',True)
        self.assertIsNotNone(guard.blocked('find',{'name':'a'}))
        self.assertIsNone(guard.blocked('find',{'name':'b'}))
        now[0]=60
        self.assertIsNone(guard.blocked('find',{'name':'a'}))

    def test_real_failure_and_requested(self):
        for message in ('ERROR_VERIFICACION: archivo', 'Could not create folder', 'Not found: a', 'Access denied: a'):
            self.assertTrue(classify_result('file',message)[1])
        tag,failed,_=classify_result('file','SOLICITADO: abrir archivo')
        self.assertFalse(failed); self.assertTrue(tag.startswith('[TOOL_UNVERIFIED]'))

    def test_events_spanish_dedup_and_no_invented_cause(self):
        events=EventSummary()
        raw='ERR: Groq <urlopen error [Errno 8] nodename nor servname provided>'
        self.assertEqual(events.format(raw),'Groq no disponible: fallo de conexión.')
        self.assertIsNone(events.format(raw))
        message=events.format('ERR: Playwright Sync API inside asyncio loop')
        self.assertIn('problema interno', message)
        self.assertNotIn('sesión', message)

    def test_handler_without_speak(self):
        from core.action_loader import _call_handler
        from functools import wraps
        calls=[]
        def original(parameters, player=None):
            calls.append(parameters);return 'ok'
        @wraps(original)
        def wrapper(*args,**kwargs):return original(*args,**kwargs)
        self.assertEqual(_call_handler(wrapper,{'a':1},{'speak':Mock()}),'ok')
        self.assertEqual(calls,[{'a':1}])


class VisionTests(unittest.TestCase):
    def test_valid_frame_and_invalid_frame(self):
        from PIL import Image
        buf=io.BytesIO(); Image.new('RGB',(32,32),'white').save(buf,format='PNG')
        self.assertEqual(validate_frame(buf.getvalue(),'image/png')[0],buf.getvalue())
        for data in (b'',b'not an image'):
            with self.assertRaises(Exception):validate_frame(data,'image/png')

    def test_capture_camera_releases_without_frame(self):
        from actions import screen_processor as screen
        cap=Mock(); cap.isOpened.return_value=True; cap.read.return_value=(False,None)
        with patch.object(screen,'_CV2',True), patch.object(screen,'_get_camera_index',return_value=0), patch.object(screen,'_cv2_backend',return_value=0), patch.object(screen.cv2,'VideoCapture',return_value=cap):
            with self.assertRaises(RuntimeError):screen._capture_camera()
        cap.release.assert_called_once()

    def test_capture_camera_valid_frame(self):
        import numpy as np
        from actions import screen_processor as screen
        cap=Mock(); cap.isOpened.return_value=True; cap.read.return_value=(True,np.full((32,32,3),127,dtype=np.uint8))
        with patch.object(screen,'_get_camera_index',return_value=0), patch.object(screen,'_cv2_backend',return_value=0), patch.object(screen.cv2,'VideoCapture',return_value=cap):
            data,mime=screen._capture_camera()
            self.assertTrue(validate_frame(data,mime))
        cap.release.assert_called_once()

    def test_screen_permission_error_propagates(self):
        from actions import screen_processor as screen
        with patch.object(screen,'_MSS',True), patch.object(screen.mss,'mss',side_effect=PermissionError('Screen Recording denied')):
            with self.assertRaises(PermissionError):screen._capture_screen()

    def test_screen_uses_full_desktop_and_validates(self):
        from actions import screen_processor as screen
        capture=Mock(); monitor={'left':0,'top':0,'width':32,'height':32}
        capture.monitors=[monitor]
        shot=Mock();shot.rgb=b'\xff'*32*32*3;shot.size=(32,32)
        capture.grab.return_value=shot
        manager=Mock();manager.__enter__=Mock(return_value=capture);manager.__exit__=Mock(return_value=False)
        with patch.object(screen.mss,'mss',return_value=manager):
            data,mime=screen._capture_screen()
            self.assertTrue(validate_frame(data,mime))
        capture.grab.assert_called_once_with(monitor)

    def test_sources_and_shopping_never_purchase(self):
        self.assertEqual(vision_intent('¿Puedes verme?'),'camera')
        self.assertEqual(vision_intent('Mira mi escritorio'),'screen')
        self.assertEqual(vision_intent('Cierra la cámara'),'close')
        self.assertIsNone(vision_intent('lee esto'))
        self.assertEqual(ProductObservation().next_step(),'confirm_identity')
        self.assertFalse(ProductObservation(model='synthetic',confidence=1).purchase_allowed)


if __name__=='__main__':unittest.main()
