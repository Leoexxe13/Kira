"""Offline contracts: no physical devices, accounts, network or model loading."""
import ast
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import threading
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core import audio_devices
from core.audio_pipeline import (
    AudioFrame, AudioPipeline, AudioPolicy, CaptureBridge, InputState, PlaybackState,
)
from core.voice_manager import VoiceManager

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = InputState()
        self.logs = []
        self.level = 1.0
        self.wake = Mock()
        self.policy = AudioPolicy(idle_after_seconds=1, queue_frames=2)
        self.pipeline = AudioPipeline(
            policy=self.policy, clock=self.clock, state=lambda: self.state,
            logger=self.logs.append, level=lambda _data: self.level, wake_feed=self.wake,
        )
        self.queue = asyncio.Queue(maxsize=2)
        self.pipeline.attach(self.queue)
        self.pipeline.start_stream("local", "headset-stream", "external-headset")

    def frame(self, origin="local", **kwargs):
        stream, device = self.pipeline._streams[origin]
        return replace(AudioFrame(origin, device, stream, self.clock(), 16000, 1, b"\x01\x00" * 1024), **kwargs)

    def phone(self, stream="phone-1"):
        self.pipeline.start_stream("phone", stream, "paired-phone")
        return self.frame("phone")

    def assert_playback_blocks(self, engine, origin):
        frame = self.phone() if origin == "phone" else self.frame()
        self.pipeline.playback.start(engine)
        self.assertFalse(self.pipeline.submit(frame))
        self.assertTrue(self.queue.empty())
        self.assertFalse(self.pipeline._pre_roll)

    def test_gemini_blocks_local(self):
        self.assert_playback_blocks("gemini", "local")

    def test_gemini_blocks_phone(self):
        self.assert_playback_blocks("gemini", "phone")

    def test_edge_blocks_local(self):
        self.assert_playback_blocks("edge", "local")

    def test_edge_blocks_phone(self):
        self.assert_playback_blocks("edge", "phone")

    def test_blocked_edge_audio_never_reappears_in_preroll(self):
        self.level = 0
        self.clock.advance(2)
        self.pipeline.submit(self.frame(data=b"\x00\x00"))
        self.assertTrue(self.pipeline._pre_roll)
        self.pipeline.playback.start("edge")
        self.pipeline.submit(self.frame(data=b"\x02\x00"))
        self.assertFalse(self.pipeline._pre_roll)
        self.pipeline.playback.stop("edge")
        self.clock.advance(.3)
        self.level = 1
        self.pipeline.submit(self.frame(data=b"\x03\x00"))
        self.assertEqual(self.queue.get_nowait().data, b"\x03\x00")
        self.assertTrue(self.queue.empty())

    def test_playback_ends_new_audio_passes(self):
        self.pipeline.playback.start("gemini")
        self.pipeline.playback.stop("gemini")
        self.clock.advance(.3)
        self.assertTrue(self.pipeline.submit(self.frame()))

    def test_hold_uses_fake_monotonic_clock(self):
        self.pipeline.playback.start("edge")
        self.pipeline.playback.stop("edge")
        self.clock.advance(.249)
        self.assertFalse(self.pipeline.submit(self.frame()))
        self.clock.advance(.002)
        self.assertTrue(self.pipeline.submit(self.frame()))

    def test_old_frame_expires_before_submission(self):
        frame = self.frame()
        self.clock.advance(1.01)
        self.assertFalse(self.pipeline.submit(frame))
        self.assertEqual(self.pipeline.counters["expired"], 1)

    def test_fresh_frame_passes(self):
        frame = self.frame()
        self.assertTrue(self.pipeline.submit(frame))
        self.assertIsNotNone(self.pipeline.ready_to_send(self.queue.get_nowait()))

    def test_expiry_rechecked_after_queue_wait(self):
        self.pipeline.submit(self.frame())
        self.clock.advance(1.01)
        self.assertIsNone(self.pipeline.ready_to_send(self.queue.get_nowait()))
        self.pipeline.submit(self.frame())
        self.assertTrue(self.queue.get_nowait().discontinuity)

    def test_local_queuefull_handled_on_loop(self):
        scheduled = []
        bridge = CaptureBridge(self.pipeline, SimpleNamespace(
            call_soon_threadsafe=lambda callback: scheduled.append(callback)))
        self.pipeline.submit(self.frame())
        self.pipeline.submit(self.frame())
        bridge.offer(self.frame())
        scheduled.pop()()  # QueueFull happens HERE, not when offer schedules it
        self.assertEqual(self.pipeline.counters["queue_full"], 1)

    def test_remote_queuefull_does_not_escape(self):
        frame = self.phone()
        self.pipeline.submit(frame)
        self.pipeline.submit(frame)
        self.assertFalse(self.pipeline.submit(frame))
        self.assertEqual(self.pipeline.counters["queue_full"], 1)

    def test_saturation_marks_next_accepted_audio(self):
        frame = self.phone()
        self.pipeline.submit(frame)
        self.pipeline.submit(frame)
        self.pipeline.submit(frame)
        # Sending an older accepted frame must not consume the enqueue gap.
        self.pipeline.ready_to_send(self.queue.get_nowait())
        self.queue.get_nowait()
        self.pipeline.submit(frame)
        self.assertTrue(self.queue.get_nowait().discontinuity)

    def test_capture_overflow_clears_preroll_and_marks_gap(self):
        self.level = 0
        self.clock.advance(2)
        self.pipeline.submit(self.frame())
        self.level = 1
        self.pipeline.submit(self.frame(discontinuity=True, discontinuity_reason="input_overflow"))
        self.assertFalse(self.pipeline._pre_roll)
        self.pipeline.submit(self.frame())
        self.assertTrue(self.queue.get_nowait().discontinuity)
        self.assertEqual(self.pipeline.counters["input_overflow"], 1)

    def test_local_and_phone_metadata_preserved(self):
        for origin in ("local", "phone"):
            frame = self.phone() if origin == "phone" else self.frame()
            self.pipeline.submit(frame)
            received = self.queue.get_nowait()
            for name in ("origin", "stable_device_id", "stream_id", "timestamp", "sample_rate", "channels", "data"):
                self.assertEqual(getattr(received, name), getattr(frame, name))

    def test_two_remote_streams_do_not_mix(self):
        old = self.phone("old")
        self.pipeline.submit(old)
        new = self.phone("new")
        self.assertFalse(self.pipeline.submit(old))
        self.assertTrue(self.pipeline.submit(new))
        self.assertIsNone(self.pipeline.ready_to_send(self.queue.get_nowait()))
        self.assertEqual(self.pipeline.ready_to_send(self.queue.get_nowait()).stream_id, "new")

    def test_old_disconnect_cannot_retire_new_stream(self):
        old = self.phone("old")
        new = self.phone("new")
        self.pipeline.end_stream("phone", "old")
        self.assertFalse(self.pipeline.submit(old))
        self.assertTrue(self.pipeline.submit(new))

    def test_old_connection_cannot_reclaim_after_new_disconnect(self):
        old = self.phone("old")
        self.phone("new")
        self.pipeline.end_stream("phone", "new")
        self.assertFalse(self.pipeline.submit(old))
        self.assertTrue(self.pipeline.submit(self.frame()))

    def test_mute_blocks_both_sources(self):
        self.state = InputState(muted=True)
        self.assertFalse(self.pipeline.submit(self.frame()))
        self.assertFalse(self.pipeline.submit(self.phone()))
        self.assertFalse(self.pipeline._pre_roll)

    def test_sleep_awake_both_sources(self):
        for origin in ("local", "phone"):
            frame = self.phone() if origin == "phone" else self.frame()
            self.state = InputState(wake_enabled=True, awake=False)
            self.assertFalse(self.pipeline.submit(frame))
            self.assertFalse(self.pipeline._pre_roll)
            self.state = InputState(wake_enabled=True, awake=True)
            self.assertTrue(self.pipeline.submit(frame))
            self.queue.get_nowait()
        self.assertEqual(self.wake.call_count, 2)

    def test_wake_disabled_does_not_block_on_awake_flag(self):
        self.state = InputState(wake_enabled=False, awake=False)
        self.assertTrue(self.pipeline.submit(self.frame()))
        self.wake.assert_not_called()

    def test_muted_or_playback_cannot_feed_wake_detector(self):
        self.state = InputState(muted=True, wake_enabled=True, awake=False)
        self.pipeline.submit(self.frame())
        self.state = InputState(wake_enabled=True, awake=False)
        self.pipeline.playback.start("edge")
        self.pipeline.submit(self.frame())
        self.wake.assert_not_called()

    def test_capture_snapshot_prevents_delayed_muted_replay(self):
        self.state = InputState(muted=True)
        frame = self.pipeline.stamp_capture(self.frame())
        self.state = InputState()
        self.assertFalse(self.pipeline.submit(frame))

    def test_queued_audio_captured_during_playback_rejected_after_end(self):
        self.pipeline.playback.start("edge")
        frame = self.frame()
        self.pipeline.playback.stop("edge")
        self.clock.advance(.3)
        self.assertFalse(self.pipeline.submit(frame))

    def test_playback_rechecked_before_send(self):
        self.pipeline.submit(self.frame())
        self.pipeline.playback.start("edge")
        self.assertIsNone(self.pipeline.ready_to_send(self.queue.get_nowait()))

    def test_overlapping_players_do_not_release_each_other(self):
        self.pipeline.playback.start("gemini")
        self.pipeline.playback.start("edge")
        self.pipeline.playback.stop("gemini")
        self.clock.advance(.3)
        self.assertFalse(self.pipeline.submit(self.frame()))
        self.pipeline.playback.stop("edge")
        self.clock.advance(.3)
        self.assertTrue(self.pipeline.submit(self.frame()))

    def test_preroll_preserves_capture_timestamp(self):
        self.level = 0
        self.clock.advance(2)
        timestamp = self.clock()
        self.pipeline.submit(self.frame())
        self.clock.advance(.1)
        self.level = 1
        self.pipeline.submit(self.frame())
        self.assertEqual(self.queue.get_nowait().timestamp, timestamp)

    def test_expired_preroll_not_rejuvenated(self):
        self.level = 0
        self.clock.advance(2)
        self.pipeline.submit(self.frame())
        self.clock.advance(1.1)
        self.level = 1
        self.pipeline.submit(self.frame())
        self.assertEqual(self.queue.get_nowait().timestamp, self.clock())
        self.assertTrue(self.queue.empty())

    def test_diagnostics_are_rate_limited(self):
        self.state = InputState(muted=True)
        for _ in range(100):
            self.pipeline.submit(self.frame())
        self.assertEqual(len(self.logs), 1)
        self.assertEqual(self.pipeline.counters["muted"], 100)
        self.clock.advance(5.1)
        self.pipeline.submit(self.frame())
        self.assertEqual(len(self.logs), 2)

    def test_bounded_capture_handoff_marks_overload(self):
        scheduled = []
        bridge = CaptureBridge(self.pipeline, SimpleNamespace(
            call_soon_threadsafe=lambda cb: scheduled.append(cb)))
        self.assertTrue(bridge.offer(self.frame()))
        self.assertTrue(bridge.offer(self.frame()))
        self.assertFalse(bridge.offer(self.frame()))
        self.assertEqual(len(scheduled), 1)
        scheduled.pop()()
        self.queue.get_nowait()
        self.queue.get_nowait()
        bridge.offer(self.frame())
        scheduled.pop()()
        self.assertTrue(self.queue.get_nowait().discontinuity)
        self.assertEqual(self.pipeline.counters["capture_queue_full"], 1)

    def test_session_reconnect_rejects_queued_old_capture(self):
        frame = self.frame()
        self.clock.advance(.1)
        self.pipeline.detach()
        self.pipeline.attach(asyncio.Queue())
        self.assertFalse(self.pipeline.submit(frame))

    def test_phone_blocks_local_until_disconnect(self):
        self.phone()
        self.assertFalse(self.pipeline.submit(self.frame()))
        self.pipeline.end_stream("phone", "phone-1")
        self.assertTrue(self.pipeline.submit(self.frame()))

    def test_blocked_local_does_not_erase_remote_preroll(self):
        self.phone()
        self.level = 0
        self.clock.advance(2)
        self.pipeline.submit(self.frame("phone"))
        self.assertTrue(self.pipeline._pre_roll)
        self.pipeline.submit(self.frame("local"))
        self.assertTrue(self.pipeline._pre_roll)

    def test_general_sounddevice_status_marks_discontinuity(self):
        self.assertFalse(self.pipeline.submit(self.frame(
            discontinuity=True, discontinuity_reason="capture_status")))
        self.pipeline.submit(self.frame())
        self.assertTrue(self.queue.get_nowait().discontinuity)
        self.assertEqual(self.pipeline.counters["capture_status"], 1)

    def test_off_pipeline_has_no_vad_or_model_imports(self):
        # Real execution plus static import boundary: this phase is always OFF
        # for voice identity and VAD, and PCM is preserved byte-for-byte.
        self.pipeline.submit(self.frame())
        self.assertEqual(self.queue.get_nowait().data, self.frame().data)
        tree = ast.parse((ROOT / "core/audio_pipeline.py").read_text())
        imported = {n.module.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.module}
        imported.update(alias.name.split(".")[0] for n in ast.walk(tree)
                        if isinstance(n, ast.Import) for alias in n.names)
        self.assertLessEqual(imported, {"__future__", "asyncio", "collections", "dataclasses",
                                       "math", "queue", "threading", "time", "typing", "core"})

    def test_invalid_config_falls_back_to_central_defaults(self):
        policy = AudioPolicy.from_config({"audio_max_age_seconds": -1, "audio_queue_frames": 0})
        self.assertEqual(policy.max_age_seconds, 1)
        self.assertEqual(policy.queue_frames, 32)

    def test_invalid_pcm_is_rejected(self):
        for changes in ({"data": b"x"}, {"data": b""}, {"sample_rate": 48000},
                        {"channels": 2}, {"data": b"x" * 10000}):
            self.assertFalse(self.pipeline.submit(self.frame(**changes)))

    def test_vad_is_optional_and_not_loaded_when_off(self):
        policy = AudioPolicy(vad_enabled=False)
        pipeline = AudioPipeline(policy=policy)
        self.assertIsNone(pipeline.vad)

    def test_vad_pauses_do_not_cut_active_utterance(self):
        self.pipeline.vad = Mock()
        self.pipeline.vad.accepts.side_effect = [True, False, True]
        self.pipeline.submit(self.frame())
        self.pipeline.ready_to_send(self.queue.get_nowait())
        self.clock.advance(.15)
        self.assertTrue(self.pipeline.submit(self.frame()))
        self.assertFalse(self.queue.get_nowait().discontinuity)
        self.clock.advance(.15)
        self.assertTrue(self.pipeline.submit(self.frame()))
        self.assertFalse(self.queue.get_nowait().discontinuity)

    def test_vad_onset_replays_eligible_preroll(self):
        self.pipeline.vad = Mock()
        self.pipeline.vad.accepts.side_effect = [False, True]
        first = self.frame(data=b"\x01\x00")
        self.assertFalse(self.pipeline.submit(first))
        self.clock.advance(.05)
        self.assertTrue(self.pipeline.submit(self.frame(data=b"\x02\x00")))
        self.assertEqual(self.queue.get_nowait().data, first.data)
        self.assertEqual(self.queue.get_nowait().data, b"\x02\x00")

    def test_vad_failure_degrades_without_losing_speech(self):
        self.pipeline.vad = Mock()
        self.pipeline.vad.accepts.side_effect = RuntimeError("failed")
        self.assertTrue(self.pipeline.submit(self.frame()))
        self.assertIsNone(self.pipeline.vad)
        self.assertTrue(self.logs)

    def test_missing_optional_vad_does_not_prevent_startup(self):
        with patch("core.audio_vad.WebRtcVoiceActivity", side_effect=RuntimeError("missing")):
            pipeline = AudioPipeline(policy=AudioPolicy(vad_enabled=True), logger=self.logs.append)
        self.assertIsNone(pipeline.vad)
        self.assertTrue(self.logs)

    def test_vad_detach_and_reconnect_reset_all_state(self):
        self.pipeline.vad = Mock()
        self.pipeline.detach()
        self.pipeline.vad.reset.assert_called_with()
        self.pipeline.attach(self.queue)
        self.assertTrue(self.pipeline.submit(self.frame()))

    def test_vad_rejects_silence_before_preroll(self):
        from core.audio_vad import WebRtcVoiceActivity
        vad = WebRtcVoiceActivity()
        self.assertFalse(vad.accepts("s", bytes(640)))

    def test_vad_accepts_mocked_voiced_frames(self):
        from core.audio_vad import WebRtcVoiceActivity
        vad = WebRtcVoiceActivity()
        vad._vad.is_speech = Mock(return_value=True)
        self.assertTrue(vad.accepts("s", bytes(640)))


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.sd = SimpleNamespace(
            query_devices=Mock(return_value={"name": "USB Headset", "hostapi": 0}),
            query_hostapis=Mock(return_value={"name": "Core Audio"}),
        )
        self.mock_sd = patch.dict(sys.modules, {"sounddevice": self.sd})
        self.mock_sd.start()
        self.addCleanup(self.mock_sd.stop)

    def test_explicit_system_default_opens_default(self):
        with patch.object(audio_devices, "resolve") as resolve:
            for name in ("", "System default"):
                selection = audio_devices.select_input(name)
                self.assertTrue(selection.available)
                self.assertTrue(selection.is_default)
                self.assertIsNone(selection.index)
            resolve.assert_not_called()

    def test_named_headset_opens_resolved_index(self):
        with patch.object(audio_devices, "resolve", return_value=7):
            selected = audio_devices.select_input("USB Headset")
        self.assertTrue(selected.available)
        self.assertEqual(selected.index, 7)
        self.assertFalse(selected.is_default)

    def test_missing_named_headset_is_unavailable_not_default(self):
        with patch.object(audio_devices, "resolve", return_value=None):
            selected = audio_devices.select_input("USB Headset")
        self.assertFalse(selected.available)
        self.assertFalse(selected.is_default)
        self.sd.query_devices.assert_not_called()

    def test_device_identity_survives_portaudio_index_change(self):
        with patch.object(audio_devices, "resolve", side_effect=[2, 8]):
            first = audio_devices.select_input("USB Headset")
            second = audio_devices.select_input("USB Headset")
        self.assertEqual(first.stable_device_id, second.stable_device_id)
        self.assertNotEqual(first.index, second.index)


class EdgeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.playback = PlaybackState(.25, self.clock)
        self.vm = VoiceManager(playback=self.playback)
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.proc = SimpleNamespace(returncode=None)
        async def wait():
            self.started.set()
            await self.finished.wait()
            return self.proc.returncode
        def terminate():
            self.proc.returncode = -15
            self.finished.set()
        self.proc.wait = wait
        self.proc.terminate = Mock(side_effect=terminate)
        edge = SimpleNamespace(Communicate=Mock(return_value=SimpleNamespace(save=AsyncMock())))
        for patcher in (
            patch.dict(sys.modules, {"edge_tts": edge}),
            patch("core.voice_manager.load_config", return_value={"edge_enabled": True}),
            patch("core.voice_manager.tempfile.mkstemp", return_value=(12345, "/fake/voice.mp3")),
            patch("os.close"),
            patch("core.voice_manager.Path.unlink"),
            patch("core.voice_manager.asyncio.create_subprocess_exec", AsyncMock(return_value=self.proc)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def start(self):
        task = asyncio.create_task(self.vm.speak_edge("Prueba"))
        await asyncio.wait_for(self.started.wait(), 1)
        self.assertTrue(self.playback.rejects(self.clock()))
        return task

    async def test_edge_success_releases_playback_with_hold(self):
        task = await self.start()
        self.proc.returncode = 0
        self.finished.set()
        self.assertTrue(await task)
        self.assertTrue(self.playback.rejects(self.clock()))
        self.clock.advance(.3)
        self.assertFalse(self.playback.rejects(self.clock()))

    async def test_edge_cancel_terminates_and_releases(self):
        task = await self.start()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.proc.terminate.assert_called()
        self.clock.advance(.3)
        self.assertFalse(self.playback.rejects(self.clock()))

    async def test_edge_stop_now_terminates_and_releases(self):
        task = await self.start()
        self.vm.stop_now()
        self.assertFalse(await task)
        self.clock.advance(.3)
        self.assertFalse(self.playback.rejects(self.clock()))

    async def test_edge_spawn_exception_releases_playback(self):
        with patch("core.voice_manager.asyncio.create_subprocess_exec",
                   AsyncMock(side_effect=OSError("afplay unavailable"))):
            self.assertFalse(await self.vm.speak_edge("Prueba"))
        self.clock.advance(.3)
        self.assertFalse(self.playback.rejects(self.clock()))

    async def test_real_edge_blocks_common_pipeline_for_both_sources(self):
        pipeline = AudioPipeline(clock=self.clock, playback=self.playback)
        pipeline.attach(asyncio.Queue())
        task = await self.start()
        for origin in ("local", "phone"):
            pipeline.start_stream(origin, origin, "external-device")
            frame = AudioFrame(origin, "external-device", origin, self.clock(), 16000, 1, b"\x01\x00")
            self.assertFalse(pipeline.submit(frame))
        self.assertFalse(pipeline._pre_roll)
        self.proc.returncode = 0
        self.finished.set()
        await task

    async def test_edge_wait_exception_terminates_and_releases(self):
        self.proc.wait = AsyncMock(side_effect=[RuntimeError("wait failed"), -15])
        self.assertFalse(await self.vm.speak_edge("Prueba"))
        self.proc.terminate.assert_called()
        self.clock.advance(.3)
        self.assertFalse(self.playback.rejects(self.clock()))

    async def test_edge_cancel_during_spawn_does_not_orphan_process(self):
        spawning = asyncio.Event()
        release = asyncio.Event()
        async def spawn(*args, **kwargs):
            spawning.set()
            await release.wait()
            return self.proc
        with patch("core.voice_manager.asyncio.create_subprocess_exec", spawn):
            task = asyncio.create_task(self.vm.speak_edge("Prueba"))
            await asyncio.wait_for(spawning.wait(), 1)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.proc.terminate.assert_called()
        self.clock.advance(.3)
        self.assertFalse(self.playback.rejects(self.clock()))


def real_method(filename, name, namespace):
    """Compile production entrypoints without importing GUI/server startup."""
    tree = ast.parse((ROOT / filename).read_text())
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


class EntryPointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.pipeline = AudioPipeline(clock=self.clock, policy=AudioPolicy(idle_after_seconds=0))
        self.queue = asyncio.Queue(maxsize=4)
        self.pipeline.attach(self.queue)
        self.pipeline.start_stream("local", "headset", "external")
        self.worker = SimpleNamespace(_audio_pipeline=self.pipeline, out_queue=self.queue,
                                      ui=SimpleNamespace(write_log=Mock(), set_state=Mock(), muted=False),
                                      _speaking_lock=threading.Lock(), _is_speaking=False)

    def frame(self):
        return AudioFrame("local", "external", "headset", self.clock(), 16000, 1, b"\x01\x00")

    async def test_closed_loop_rejects_and_closes_coroutine(self):
        loop = asyncio.new_event_loop()
        loop.close()
        self.worker._loop = loop
        method = real_method("main.py", "_schedule_coro", {"asyncio": asyncio})
        async def command():
            self.fail("closed-loop command must not run")
        coro = command()
        self.assertFalse(method(self.worker, coro))
        self.assertIsNone(coro.cr_frame)

    async def test_schedule_close_race_closes_coroutine(self):
        self.worker._loop = asyncio.get_running_loop()
        method = real_method("main.py", "_schedule_coro", {"asyncio": asyncio})
        async def command():
            pass
        coro = command()
        with patch("asyncio.run_coroutine_threadsafe", side_effect=RuntimeError("Event loop is closed")):
            self.assertFalse(method(self.worker, coro))
        self.assertIsNone(coro.cr_frame)

    async def test_real_sender_expires_queued_frame_before_gemini(self):
        self.pipeline.submit(self.frame())
        self.clock.advance(2)
        self.worker.session = SimpleNamespace(send_realtime_input=AsyncMock())
        method = real_method("main.py", "_send_realtime", {"types": SimpleNamespace(Blob=lambda **kw: kw)})
        task = asyncio.create_task(method(self.worker))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.worker.session.send_realtime_input.assert_not_called()

    async def test_real_sender_signals_discontinuity_to_transport(self):
        calls = []
        async def send(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                self.pipeline.mark_gap("local", "queue_full")
                self.pipeline.submit(self.frame())
            elif len(calls) == 3:
                raise asyncio.CancelledError
        self.worker.session = SimpleNamespace(send_realtime_input=send)
        self.pipeline.submit(self.frame())
        method = real_method("main.py", "_send_realtime", {"types": SimpleNamespace(Blob=lambda **kw: kw)})
        with self.assertRaises(asyncio.CancelledError):
            await method(self.worker)
        self.assertIn("audio", calls[0])
        self.assertEqual(calls[1], {"audio_stream_end": True})
        self.assertIn("audio", calls[2])

    async def test_real_gemini_speaking_hook_blocks_both_sources(self):
        method = real_method("main.py", "set_speaking", {})
        method(self.worker, True)
        self.assertFalse(self.pipeline.submit(self.frame()))
        self.pipeline.start_stream("phone", "phone-stream", "paired")
        phone = AudioFrame("phone", "paired", "phone-stream", self.clock(), 16000, 1, b"\x01\x00")
        self.assertFalse(self.pipeline.submit(phone))
        method(self.worker, False)
        self.clock.advance(.3)
        self.assertTrue(self.pipeline.submit(replace(phone, timestamp=self.clock())))

    async def test_real_local_capture_opens_default_or_headset_never_missing(self):
        for index, is_default, available in ((None, True, True), (7, False, True), (None, False, False)):
            selection = audio_devices.InputSelection("headset", index, is_default, available, "external")
            mic = Mock()
            mic.active = False
            mic.__enter__ = Mock(return_value=mic)
            mic.__exit__ = Mock(return_value=False)
            sd = SimpleNamespace(InputStream=Mock(return_value=mic))
            devices = SimpleNamespace(select_input=Mock(return_value=selection))
            method = real_method("main.py", "_listen_audio", {
                "asyncio": asyncio, "uuid": uuid, "CaptureBridge": CaptureBridge,
                "audio_devices": devices, "get_input_device": lambda: "headset",
                "sd": sd, "AudioFrame": AudioFrame, "SEND_SAMPLE_RATE": 16000,
                "CHANNELS": 1, "CHUNK_SIZE": 1024,
            })
            await method(self.worker)
            if available:
                self.assertEqual(sd.InputStream.call_args.kwargs["device"], index)
                self.assertEqual(sd.InputStream.call_count, 1)
            else:
                sd.InputStream.assert_not_called()

    async def test_remote_endpoint_auth_and_reconnect_identity(self):
        import hashlib
        class Disconnected(Exception):
            pass
        server = SimpleNamespace(_tokens={"token-a", "token-b"},
                                 _token_keys={"token-a": "persistent-pairing-key", "token-b": "persistent-pairing-key"},
                                 _audio_pipeline=self.pipeline, broadcast=AsyncMock())
        endpoint = real_method("dashboard/server.py", "phone_audio_ws", {
            "self": server, "WebSocket": object, "WebSocketDisconnect": Disconnected,
            "uuid": uuid, "asyncio": asyncio, "hashlib": hashlib, "AudioFrame": AudioFrame,
        })
        invalid = SimpleNamespace(close=AsyncMock(), accept=AsyncMock())
        await endpoint(invalid, "invalid-token")
        invalid.accept.assert_not_called()
        frames = []
        for token in ("token-a", "token-b"):
            async def receive():
                if not frames or frames[-1].stream_id != self.pipeline._streams["phone"][0]:
                    return b"\x01\x00"
                raise Disconnected
            original = self.pipeline.submit
            def collect(frame):
                frames.append(frame)
                return original(frame)
            ws = SimpleNamespace(close=AsyncMock(), accept=AsyncMock(), receive_bytes=receive)
            with patch.object(self.pipeline, "submit", side_effect=collect):
                await endpoint(ws, token)
        self.assertEqual(frames[0].stable_device_id, frames[1].stable_device_id)
        self.assertNotEqual(frames[0].stream_id, frames[1].stream_id)
        self.assertEqual(frames[0].timestamp, self.clock())
        self.assertNotIn("persistent-pairing-key", repr(frames))
        self.assertNotIn("phone", self.pipeline._streams)


if __name__ == "__main__":
    unittest.main()
