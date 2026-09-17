"""Shared voice state, stream ownership and persistent-speech interruption gate.

No capture/recognition backend is created here. The existing streams provide
PCM and playback references. Correlation rejects direct output echo; this is
a conservative signal guard, not a claim to identify a human by RMS alone.
"""
from collections import deque
from enum import Enum
import threading
import time
import numpy as np
import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def bounded_live_session(manager):
    session = await asyncio.wait_for(manager.__aenter__(), timeout=20)
    try:
        yield session
    finally:
        await asyncio.wait_for(manager.__aexit__(None,None,None), timeout=5)


class VoiceState(str, Enum):
    IDLE='IDLE'
    LISTENING='LISTENING'
    THINKING='THINKING'
    EXECUTING='EXECUTING'
    SPEAKING='SPEAKING'


class VoiceTurns:
    def __init__(self, on_state=None, clock=time.monotonic, debounce=.32):
        self.state = VoiceState.IDLE
        self.on_state = on_state or (lambda state: None)
        self.clock = clock
        self.debounce = debounce
        self.events = deque(maxlen=200)
        self._lock = threading.RLock()
        self._owners = {}
        self._speech_since = None
        self._echo_until = 0
        self._refs = deque(maxlen=24)
        self.generation = 0

    def _event(self, name):
        self.events.append((name,self.clock()))
        print(f'[VoiceTurn] {name} {self.clock():.3f}')

    def request(self, state):
        state = VoiceState(state)
        with self._lock:
            # A router finishing must not label continuing playback LISTENING.
            if self.state == VoiceState.SPEAKING and state != VoiceState.IDLE:
                return
            self._set(state)

    def _set(self, state):
        if state != self.state:
            self.state = state
            self.on_state(state.value)

    def acquire(self, kind, owner):
        with self._lock:
            if kind in self._owners:
                return False
            self._owners[kind] = owner
            if kind == 'mic':
                self._event('listen_start')
                if self.state != VoiceState.SPEAKING:
                    self._set(VoiceState.LISTENING)
            return True

    def release(self, kind, owner):
        with self._lock:
            if self._owners.get(kind) is owner:
                del self._owners[kind]

    def start_tts(self):
        with self._lock:
            if self.state != VoiceState.SPEAKING:
                self._speech_since = None
                self._event('tts_start')
                self._set(VoiceState.SPEAKING)

    def end_tts(self, cancelled=False):
        with self._lock:
            if self.state != VoiceState.SPEAKING:
                return
            if cancelled:self.generation += 1
            self._event('tts_cancel' if cancelled else 'tts_end')
            self._echo_until = self.clock()+.18
            self._speech_since = None
            self._set(VoiceState.LISTENING)
            self._event('listen_resume')

    def cancel(self):
        with self._lock:
            if self.state == VoiceState.SPEAKING:
                self.end_tts(cancelled=True)
            else:
                self.generation += 1
                self._set(VoiceState.LISTENING)

    @staticmethod
    def _samples(pcm, rate):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768
        if rate != 16000 and len(samples):
            samples = np.interp(np.arange(0,len(samples),rate/16000),np.arange(len(samples)),samples)
        return samples

    def output(self, pcm, rate=24000):
        samples = self._samples(pcm,rate)
        with self._lock:
            self._refs.append((self.clock(),samples))

    def microphone(self, pcm, rate=16000):
        """Returns True once persistent non-correlated speech warrants cancel."""
        samples = self._samples(pcm,rate)
        if len(samples)<32:return False
        now = self.clock()
        with self._lock:
            if self.state != VoiceState.SPEAKING:
                return False
            rms = float(np.sqrt(np.mean(samples*samples)))
            if rms < .025:
                self._speech_since = None
                return False
            refs = [s for t,s in self._refs if now-t<.8]
            if refs:
                reference = np.concatenate(refs)
                # Downsample for a cheap lag search, allowing speaker/mic delay.
                a = samples[::4]
                b = reference[::4]
                if len(b)>=len(a):
                    energy = np.convolve(b*b,np.ones(len(a)),mode='valid')
                    corr = np.correlate(b,a,mode='valid') / np.sqrt(np.maximum(energy*np.dot(a,a),1e-12))
                    if float(np.max(np.abs(corr)))>.65:
                        self._speech_since=None
                        return False
            if self._speech_since is None:
                self._speech_since=now
                self._event('speech_detected')
            return now-self._speech_since>=self.debounce

    def suppress_tail(self):
        return self.clock()<self._echo_until

    def snapshot(self):
        with self._lock:
            return {'state':self.state.value, 'streams':list(self._owners), 'events':list(self.events)}
