"""Synthetic PCM and mocked streams: these do not calibrate a real microphone."""
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock
import numpy as np
from core.voice_turns import VoiceTurns, VoiceState, bounded_live_session
from core.latency import Trace, RECENT


class VoiceTurnTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.states = []
        self.turns = VoiceTurns(self.states.append,clock=lambda:self.now)
        self.signal = np.random.default_rng(3).integers(-10000,10000,800,dtype=np.int16).tobytes()

    def test_own_playback_does_not_interrupt(self):
        self.turns.start_tts()
        for i in range(12):
            self.now=i*.05
            self.turns.output(self.signal,16000)
            self.assertFalse(self.turns.microphone(self.signal,16000))
        self.assertEqual(self.turns.state,VoiceState.SPEAKING)

    def test_brief_noise_does_not_interrupt(self):
        self.turns.start_tts()
        self.assertFalse(self.turns.microphone(self.signal))
        self.now=.06
        self.assertFalse(self.turns.microphone(bytes(1600)))
        self.now=.4
        self.assertFalse(self.turns.microphone(self.signal))

    def test_persistent_independent_voice_can_interrupt(self):
        self.turns.start_tts()
        self.assertFalse(self.turns.microphone(self.signal))
        self.now=.31
        self.assertFalse(self.turns.microphone(self.signal))
        self.now=.33
        self.assertTrue(self.turns.microphone(self.signal))
        self.turns.cancel()
        self.assertEqual(self.turns.state,VoiceState.LISTENING)
        self.assertIn('tts_cancel',[e[0] for e in self.turns.events])

    def test_normal_completion_resumes_once(self):
        self.turns.request('THINKING')
        self.turns.start_tts()
        self.turns.request('LISTENING')
        self.assertEqual(self.turns.state,VoiceState.SPEAKING)
        self.turns.end_tts(); self.turns.end_tts()
        self.assertEqual(self.states,['THINKING','SPEAKING','LISTENING'])
        self.assertEqual([e[0] for e in self.turns.events].count('listen_resume'),1)

    def test_one_microphone_and_one_output_owner(self):
        a,b = object(),object()
        for kind in ('mic','output'):
            self.assertTrue(self.turns.acquire(kind,a))
            self.assertFalse(self.turns.acquire(kind,b))
            self.turns.release(kind,b)
            self.assertFalse(self.turns.acquire(kind,b))
            self.turns.release(kind,a)
            self.assertTrue(self.turns.acquire(kind,b))

    def test_timestamps_are_monotonic(self):
        self.turns.acquire('mic',object())
        self.now=1;self.turns.start_tts()
        self.now=2;self.turns.end_tts()
        self.assertEqual([name for name,_ in self.turns.events],['listen_start','tts_start','tts_end','listen_resume'])
        self.assertEqual([at for _,at in self.turns.events],[0,1,2,2])

    def test_latency_known_stages_only_no_content(self):
        trace = Trace(clock=lambda:self.now)
        with trace.stage('tool'):self.now=2
        trace.finish()
        self.assertEqual(RECENT[-1]['stages'],{'tool':2})
        self.assertEqual(RECENT[-1]['total'],2)


class LiveLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_session_releases_on_exception(self):
        manager=Mock()
        manager.__aenter__=AsyncMock(return_value='session')
        manager.__aexit__=AsyncMock()
        with self.assertRaises(ValueError):
            async with bounded_live_session(manager) as session:
                self.assertEqual(session,'session')
                raise ValueError('synthetic')
        manager.__aexit__.assert_awaited_once()


if __name__=='__main__':unittest.main()
