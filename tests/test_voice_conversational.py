import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from main import JarvisLive
import time

class TestVoiceConversational(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch('main.JarvisLive.__init__', return_value=None):
            self.mock_ui = MagicMock()
            self.jarvis = JarvisLive(ui=self.mock_ui)
            
            # Setup state manually since we mocked init
            self.jarvis.ui = self.mock_ui
            self.jarvis._dictation_mode = False
            self.jarvis._is_speaking = False
            self.jarvis._interrupted = False
            self.jarvis._dictation_buffer = bytearray()
            self.jarvis._session_log = []
            self.jarvis._dashboard = None
            self.jarvis._tool_call_count = 0
            self.jarvis._turn_done_event = None
            self.jarvis._resume_handle = None
            self.jarvis._speaking_lock = __import__('threading').Lock()
            self.jarvis._last_user_speech = 0.0
            self.jarvis._asst_name = "KIRA"
            self.jarvis._pending_vision = None
            self.jarvis._vision_close_pending = False
            self.jarvis._vision_busy = False
            self.jarvis._vision_cam_active = False
            
            # Mock audio pipeline components
            self.jarvis._audio_pipeline = MagicMock()
            self.jarvis.out_queue = asyncio.Queue()
            self.jarvis.audio_in_queue = asyncio.Queue()

            self.jarvis.session = AsyncMock()
            self.jarvis.session.send_realtime_input = AsyncMock()
            self.jarvis.session.send_tool_response = AsyncMock()

    async def asyncTearDown(self):
        pass

    async def test_dictation_mode_buffers_audio_and_transcribes(self):
        self.jarvis._toggle_dictation(True)
        self.assertTrue(self.jarvis._dictation_mode)
        
        frame1 = MagicMock()
        frame1.data = b'\x01\x02'
        frame1.discontinuity = False
        self.jarvis.out_queue.put_nowait(frame1)
        
        frame2 = MagicMock()
        frame2.data = b'\x03\x04'
        frame2.discontinuity = True
        self.jarvis.out_queue.put_nowait(frame2)
        
        self.jarvis._audio_pipeline.ready_to_send = lambda f: f

        process_mock = AsyncMock()
        self.jarvis._process_dictation = process_mock

        task = asyncio.create_task(self.jarvis._send_realtime())
        await asyncio.sleep(0.05)
        task.cancel()
        
        process_mock.assert_called_once_with(b'\x01\x02\x03\x04')
        self.assertFalse(self.jarvis._dictation_mode)
        self.jarvis.session.send_realtime_input.assert_not_called()

    async def test_barge_in_cancels_current_response(self):
        self.jarvis._is_speaking = True
        self.jarvis.audio_in_queue.put_nowait(b'old_audio')
        
        frame = MagicMock()
        frame.data = b'user_voice'
        frame.discontinuity = False
        self.jarvis.out_queue.put_nowait(frame)
        self.jarvis._audio_pipeline.ready_to_send = lambda f: f
        
        cancel_mock = MagicMock()
        self.jarvis.cancel_current_response = cancel_mock
        
        task = asyncio.create_task(self.jarvis._send_realtime())
        await asyncio.sleep(0.05)
        task.cancel()
        
        cancel_mock.assert_called_once()
        self.jarvis.session.send_realtime_input.assert_called()

    def test_cancel_current_response_drains_audio(self):
        self.jarvis._is_speaking = True
        self.jarvis.audio_in_queue.put_nowait(b'chunk1')
        self.jarvis.audio_in_queue.put_nowait(b'chunk2')
        
        self.jarvis.cancel_current_response()
        
        self.assertTrue(self.jarvis._interrupted)
        self.assertEqual(self.jarvis.audio_in_queue.qsize(), 0)
        self.assertFalse(self.jarvis._is_speaking)
        self.jarvis._audio_pipeline.playback.stop.assert_called_with("gemini")

    @patch('main.JarvisLive._execute_tool', new_callable=AsyncMock)
    async def test_interrupted_turn_ignores_tool_calls(self, mock_execute):
        self.jarvis._interrupted = True
        
        response = MagicMock()
        response.tool_call = MagicMock()
        fc = MagicMock()
        fc.id = "call_123"
        fc.name = "some_tool"
        response.tool_call.function_calls = [fc]
        response.server_content = None
        response.data = None
        
        async def mock_receive():
            yield response
            await asyncio.sleep(999)
            
        self.jarvis.session.receive = mock_receive
        
        task = asyncio.create_task(self.jarvis._receive_audio())
        await asyncio.sleep(0.05)
        task.cancel()
        
        mock_execute.assert_not_called()
        args, kwargs = self.jarvis.session.send_tool_response.call_args
        self.assertIn("Cancelled", kwargs["function_responses"][0].response["result"])

    async def test_text_modality_is_captured(self):
        # We no longer rely on model_turn.parts! We use output_transcription!
        response = MagicMock()
        response.server_content.output_transcription.text = "Markdown text **bold**"
        response.server_content.input_transcription = None
        response.server_content.turn_complete = True
        response.data = None
        
        async def mock_receive():
            yield response
            await asyncio.sleep(999)
            
        self.jarvis.session.receive = mock_receive
        self.jarvis.ui = self.mock_ui
        self.jarvis._session_log = []
        
        task = asyncio.create_task(self.jarvis._receive_audio())
        await asyncio.sleep(0.05)
        task.cancel()
        
        self.mock_ui.write_log.assert_any_call(f"{self.jarvis._asst_name}: Markdown text **bold**")

if __name__ == '__main__':
    unittest.main()
