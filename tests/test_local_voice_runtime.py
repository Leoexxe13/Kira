from __future__ import annotations

import asyncio
from dataclasses import dataclass
import unittest

from core.local_voice_runtime import (
    FRAME_BYTES, LocalVoiceConfig, LocalVoiceRuntime, VoiceState, WebRtcTurnDetector,
)
from core.reasoning_core import DispatchResult


class FakeTranscriber:
    def ready(self):
        return True

    def transcribe(self, pcm):
        self.pcm = pcm
        return "abre el calendario"


class FakeOutput:
    def __init__(self):
        self.spoken = []
        self.interrupts = 0

    def ready(self):
        return True

    def interrupt(self):
        self.interrupts += 1

    async def speak(self, text):
        self.spoken.append(text)
        return True


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, text, *, source):
        self.calls.append((text, source))
        return DispatchResult("verified", "Calendario abierto.", goal=text)


class OneTurnDetector:
    def __init__(self):
        self.used = False

    def feed(self, _frame):
        if not self.used:
            self.used = True
            return "speech_end", b"\0\0" * 320
        return "silence", None


class TurnDetectorTests(unittest.TestCase):
    def test_endpointing_keeps_preroll_and_closes_after_silence(self):
        cfg = LocalVoiceConfig(start_frames=2, end_silence_frames=5, pre_roll_frames=3)
        decisions = iter([False, False, True, True, True, False, False, False, False, False])
        detector = WebRtcTurnDetector(cfg, classifier=lambda _frame: next(decisions))
        frame = b"\0" * FRAME_BYTES
        events = []
        audio = None
        for _ in range(10):
            event, segment = detector.feed(frame)
            events.append(event)
            audio = segment or audio
        self.assertIn("speech_start", events)
        self.assertEqual(events[-1], "speech_end")
        self.assertIsNotNone(audio)
        self.assertGreaterEqual(len(audio), FRAME_BYTES * 4)


class LocalVoiceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_uses_shared_dispatcher_and_speaks_result(self):
        dispatcher = FakeDispatcher()
        output = FakeOutput()
        transcriber = FakeTranscriber()
        states = []
        runtime = LocalVoiceRuntime(
            dispatcher=dispatcher,
            config=LocalVoiceConfig(),
            transcriber=transcriber,
            output=output,
            state_callback=states.append,
            detector=OneTurnDetector(),
        )
        task = asyncio.create_task(runtime._consume())
        await runtime._frames.put(b"\0" * FRAME_BYTES)
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(dispatcher.calls, [("abre el calendario", "voice-local")])
        self.assertEqual(output.spoken, ["Calendario abierto."])
        self.assertIn(VoiceState.TRANSCRIBING.value, states)
        self.assertIn(VoiceState.ROUTING.value, states)
        self.assertIn(VoiceState.SPEAKING.value, states)

    async def test_interrupt_cancels_current_speech_generation(self):
        output = FakeOutput()
        runtime = LocalVoiceRuntime(
            dispatcher=FakeDispatcher(), config=LocalVoiceConfig(),
            transcriber=FakeTranscriber(), output=output,
            detector=OneTurnDetector(),
        )
        runtime.interrupt()
        self.assertEqual(output.interrupts, 1)
        self.assertEqual(runtime.state, VoiceState.INTERRUPTED)


if __name__ == "__main__":
    unittest.main()
