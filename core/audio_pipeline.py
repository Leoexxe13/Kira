"""Common audio hygiene. No VAD, identity models or network dependencies."""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import dataclass, replace
import math
import queue
import threading
import time
from typing import Any


@dataclass(frozen=True)
class AudioFrame:
    origin: str
    stable_device_id: str | None
    stream_id: str
    timestamp: float  # monotonic capture/host arrival time, never wall time
    sample_rate: int
    channels: int
    data: bytes
    discontinuity: bool = False
    discontinuity_reason: str = ""
    blocked_at_capture: str = ""

    def as_queue_item(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "mime_type": "audio/pcm",
            "origin": self.origin,
            "stable_device_id": self.stable_device_id,
            "stream_id": self.stream_id,
            "timestamp": self.timestamp,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "discontinuity": self.discontinuity,
            "discontinuity_reason": self.discontinuity_reason,
        }


def audio_frame(**kwargs: Any) -> AudioFrame:
    """Build the transport envelope without changing the PCM payload."""
    return AudioFrame(**kwargs)


@dataclass(frozen=True)
class AudioPolicy:
    vad_hangover_seconds: float = 0.7
    vad_enabled: bool = False
    vad_aggressiveness: int = 2
    vad_voiced_ratio: float = 0.30
    max_age_seconds: float = 1.0
    playback_hold_seconds: float = 0.25
    pre_roll_seconds: float = 0.512
    idle_after_seconds: float = 180.0
    idle_wake_level: float = 0.055
    queue_frames: int = 32
    diagnostic_interval_seconds: float = 5.0

    def __post_init__(self):
        for name in ("vad_hangover_seconds", "max_age_seconds", "playback_hold_seconds", "pre_roll_seconds",
                     "idle_after_seconds", "idle_wake_level", "diagnostic_interval_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid audio policy: {name}")
        if self.max_age_seconds == 0 or self.queue_frames < 1:
            raise ValueError("Audio age and queue capacity must be positive")
        if not 0 <= int(self.vad_aggressiveness) <= 3 or not 0 <= float(self.vad_voiced_ratio) <= 1:
            raise ValueError("Invalid VAD policy")

    @classmethod
    def from_config(cls, config):
        defaults = cls()
        keys = {
            "vad_hangover_seconds": "audio_vad_hangover_seconds",
            "max_age_seconds": "audio_max_age_seconds",
            "playback_hold_seconds": "audio_playback_hold_seconds",
            "pre_roll_seconds": "audio_pre_roll_seconds",
            "idle_after_seconds": "idle_mic_after_seconds",
            "idle_wake_level": "idle_wake_level",
            "queue_frames": "audio_queue_frames",
            "diagnostic_interval_seconds": "audio_diagnostic_interval_seconds",
            "vad_enabled": "audio_vad_enabled",
            "vad_aggressiveness": "audio_vad_aggressiveness",
            "vad_voiced_ratio": "audio_vad_voiced_ratio",
        }
        values = {}
        for field, key in keys.items():
            try:
                if field in ("vad_enabled",):
                    value = bool(config[key])
                elif field in ("queue_frames", "vad_aggressiveness"):
                    value = int(config[key])
                else:
                    value = float(config[key])
                cls(**{field: value})
                values[field] = value
            except (KeyError, TypeError, ValueError, OverflowError):
                values[field] = getattr(defaults, field)
        return cls(**values)


class PlaybackState:
    """Tokens keep overlapping Gemini/Edge output gated until the last ends."""
    def __init__(self, hold_seconds=0.25, clock=time.monotonic):
        self.clock = clock
        self.hold_seconds = hold_seconds
        self._tokens = set()
        self._lock = threading.Lock()
        self._blocked_until = float("-inf")

    def start(self, token):
        with self._lock:
            self._tokens.add(token)

    def stop(self, token):
        with self._lock:
            if token in self._tokens:
                self._tokens.remove(token)
                self._blocked_until = max(self._blocked_until, self.clock() + self.hold_seconds)

    def rejects(self, timestamp):
        with self._lock:
            # Invalidates capture queued before/during a completed playback too.
            return bool(self._tokens) or timestamp <= self._blocked_until


@dataclass(frozen=True)
class InputState:
    muted: bool = False
    wake_enabled: bool = False
    awake: bool = True


class AudioPipeline:
    """submit/ready_to_send run on the transport loop; capture only stamps state."""
    def __init__(self, *, policy=None, playback=None, clock=time.monotonic,
                 state=InputState, level=lambda _data: 1.0,
                 wake_feed=None, meter=None, logger=None):
        self.clock = clock
        self.policy = policy or AudioPolicy()
        self.playback = playback or PlaybackState(self.policy.playback_hold_seconds, clock)
        self.state = state
        self.level = level  # existing amplitude meter, NOT a speech detector
        self.wake_feed = wake_feed
        self.meter = meter
        self.logger = logger or (lambda _message: None)
        self.out_queue = None
        self._session_since = float("inf")
        self._streams = {}  # origin -> (stream_id, stable_device_id)
        self._source_since = {}
        self._pre_roll = deque(maxlen=self.policy.queue_frames)
        self._gaps = {}
        self._send_gaps = {}
        self._last_activity = self.clock()
        self._idle = False
        self._last_sent_source = None
        self.counters = Counter()
        self._last_diagnostic = float("-inf")
        self.vad = None
        self._vad_until = float("-inf")
        if self.policy.vad_enabled:
            try:
                from core.audio_vad import WebRtcVoiceActivity
                self.vad = WebRtcVoiceActivity(self.policy.vad_aggressiveness,
                                               self.policy.vad_voiced_ratio)
            except Exception as exc:
                self.logger(f"SYS: VAD unavailable; using standard audio gates: {exc}")

    def attach(self, out_queue):
        self.out_queue = out_queue
        self._session_since = self.clock()
        self._pre_roll.clear()
        if self.vad:
            self.vad.reset()
        self._last_sent_source = None
        self._last_activity = self.clock()
        self._idle = False
        for origin in self._streams:
            self.mark_gap(origin, "session_start")

    def detach(self):
        self.out_queue = None
        self._pre_roll.clear()
        if self.vad:
            self.vad.reset()
        self._session_since = float("inf")

    def start_stream(self, origin, stream_id, stable_device_id):
        # Lifecycle only: arriving frames can NEVER claim ownership.
        self._streams[origin] = (stream_id, stable_device_id)
        self._source_since[origin] = self.clock()
        self._pre_roll.clear()
        self._last_activity = self.clock()
        self._idle = False
        self.mark_gap(origin, "stream_start")
        if origin == "phone":
            self.mark_gap("local", "phone_selected")

    def end_stream(self, origin, stream_id):
        if self._streams.get(origin, (None,))[0] != stream_id:
            return False  # an old socket cannot retire its successor
        del self._streams[origin]
        self._pre_roll.clear()
        self.mark_gap(origin, "stream_end")
        if origin == "phone":
            self._source_since["local"] = self.clock()
            self.mark_gap("local", "phone_ended")
        return True

    def mark_gap(self, origin, reason):
        self._gaps[origin] = reason
        self._send_gaps[origin] = reason
        if self._pre_roll and self._pre_roll[0].origin == origin:
            self._pre_roll.clear()
        if origin == "phone" or "phone" not in self._streams:
            self._vad_until = float("-inf")
            if self.vad:
                self.vad.reset()

    def _drop(self, frame, reason):
        self.counters[reason] += 1
        if self._streams.get(frame.origin) == (frame.stream_id, frame.stable_device_id):
            self.mark_gap(frame.origin, reason)
        now = self.clock()
        if now - self._last_diagnostic >= self.policy.diagnostic_interval_seconds:
            self._last_diagnostic = now
            try:
                self.logger(f"SYS: audio discarded ({reason}); counts={dict(self.counters)}")
            except Exception:
                pass  # diagnostics must never break capture

    def _reason(self, frame):
        if frame.origin not in ("local", "phone"):
            return "invalid_origin"
        if self._streams.get(frame.origin) != (frame.stream_id, frame.stable_device_id):
            return "inactive_stream"
        if self.out_queue is None or frame.timestamp < self._session_since:
            return "inactive_session"
        if frame.timestamp < self._source_since.get(frame.origin, float("inf")):
            return "inactive_stream"
        if (frame.sample_rate != 16000 or frame.channels != 1 or not frame.data
                or len(frame.data) % 2 or len(frame.data) > 8192):
            return "invalid_pcm"
        age = self.clock() - frame.timestamp
        if not math.isfinite(age) or age < 0 or age > self.policy.max_age_seconds:
            return "expired"
        if frame.origin == "local" and "phone" in self._streams:
            return "source_blocked"
        state = self.state()
        if state.muted:
            return "muted"
        if self.playback.rejects(frame.timestamp):
            return "playback"
        if state.wake_enabled and not state.awake:
            return "asleep"
        return ""

    def stamp_capture(self, frame):
        """Cheap eligibility snapshot before an async/thread handoff."""
        return replace(frame, blocked_at_capture=self._reason(frame))

    def submit(self, frame):
        reason = frame.blocked_at_capture or self._reason(frame)
        if reason:
            # Only eligible sleeping input may reach the local wake detector.
            if reason == "asleep" and self._reason(frame) == "asleep" and self.wake_feed:
                self.wake_feed(frame.data)
            self._drop(frame, reason)
            return False
        if frame.discontinuity:
            self.mark_gap(frame.origin, frame.discontinuity_reason or "capture_status")
            self.counters["capture_discontinuity"] += 1
            if frame.discontinuity_reason in ("input_overflow", "capture_status"):
                self._drop(frame, frame.discontinuity_reason)
                return False
        if self.vad:
            try:
                voiced = self.vad.accepts(frame.stream_id, frame.data)
            except Exception as exc:
                self.logger(f"SYS: VAD failed; using standard audio gates: {exc}")
                self.vad = None
                self._pre_roll.clear()
                return self._enqueue(frame)
            if voiced:
                self._vad_until = self.clock() + self.policy.vad_hangover_seconds
                buffered = list(self._pre_roll)
                self._pre_roll.clear()
                for previous in buffered:
                    if not self._reason(previous):
                        self._enqueue(previous)
            elif self.clock() > self._vad_until:
                # Normal silence is not a transport fault. Keep eligible onset
                # context and let Gemini see natural pauses during hangover.
                self.counters["vad_silence"] += 1
                self._pre_roll.append(frame)
                while self._pre_roll and self.clock() - self._pre_roll[0].timestamp > self.policy.pre_roll_seconds:
                    self._pre_roll.popleft()
                return False
            return self._enqueue(frame)
        now = self.clock()
        level = self.level(frame.data)
        if self.meter:
            try:
                self.meter(level)
            except Exception:
                pass
        if not self.state().wake_enabled and self.policy.idle_after_seconds:
            if level >= self.policy.idle_wake_level:
                self._last_activity = now
                if self._idle:
                    self._idle = False
                    buffered = list(self._pre_roll)
                    self._pre_roll.clear()
                    for previous in buffered:
                        if self._reason(previous):
                            self._drop(previous, "expired_preroll")
                        else:
                            self._enqueue(previous)
            elif now - self._last_activity >= self.policy.idle_after_seconds:
                self._idle = True
            if self._idle:
                # Only eligible audio enters; replay keeps original timestamps.
                self._pre_roll.append(frame)
                while self._pre_roll and now - self._pre_roll[0].timestamp > self.policy.pre_roll_seconds:
                    self._pre_roll.popleft()
                return False
        return self._enqueue(frame)

    def _enqueue(self, frame):
        gap = self._gaps.pop(frame.origin, "")
        if gap:
            frame = replace(frame, discontinuity=True, discontinuity_reason=gap)
        try:
            self.out_queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._drop(frame, "queue_full")
            return False
        return True

    def ready_to_send(self, frame):
        """Revalidate immediately before Gemini, including time spent queued."""
        reason = frame.blocked_at_capture or self._reason(frame)
        if reason:
            self._drop(frame, reason)
            return None
        gap = self._send_gaps.pop(frame.origin, "")
        source = (frame.origin, frame.stream_id)
        if gap or source != self._last_sent_source:
            frame = replace(frame, discontinuity=True,
                            discontinuity_reason=gap or "source_change")
        self._last_sent_source = source
        return frame


class CaptureBridge:
    """Bounded, nonblocking sounddevice -> loop handoff. No callback logging."""
    def __init__(self, pipeline, loop):
        self.pipeline = pipeline
        self.loop = loop
        self._queue = queue.Queue(maxsize=pipeline.policy.queue_frames)
        self._lock = threading.Lock()
        self._scheduled = False
        self._lost = 0

    def offer(self, frame):
        frame = self.pipeline.stamp_capture(frame)
        with self._lock:
            try:
                if self._lost:
                    frame = replace(frame, discontinuity=True, discontinuity_reason="capture_queue_full")
                self._queue.put_nowait(frame)
            except queue.Full:
                self._lost += 1
                return False
            if not self._scheduled:
                self._scheduled = True
                try:
                    self.loop.call_soon_threadsafe(self._drain)
                except RuntimeError:
                    self._scheduled = False
                    return False
        return True

    def _drain(self):
        with self._lock:
            frames = []
            while True:
                try:
                    frames.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            lost, self._lost = self._lost, 0
            self._scheduled = False
        for frame in frames:
            self.pipeline.submit(frame)
        if lost and frames:
            self.pipeline.counters["capture_queue_full"] += lost - 1
            self.pipeline._drop(frames[-1], "capture_queue_full")
