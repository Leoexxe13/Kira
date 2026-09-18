"""Turn-based local voice runtime for KIRA.

This module deliberately owns no reasoning. Completed transcripts are submitted
through the same TextDispatcher used by chat and Remote. Audio capture continues
while TTS is active so a confirmed speech onset can cancel the current answer.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from typing import Callable

from core.voice_manager import sanitize_for_speech


SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    ROUTING = "ROUTING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class LocalVoiceConfig:
    enabled: bool = True
    vad_mode: int = 2
    start_frames: int = 5
    end_silence_frames: int = 25
    pre_roll_frames: int = 15
    max_turn_seconds: float = 15.0
    barge_in: bool = True
    whisper_threads: int = 4
    language: str = "es"
    say_voice: str = "Jorge"
    say_rate: int = 190

    @classmethod
    def from_dict(cls, data: dict | None) -> "LocalVoiceConfig":
        data = data if isinstance(data, dict) else {}
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        clean = {key: value for key, value in data.items() if key in allowed}
        cfg = cls(**clean)
        if not 0 <= cfg.vad_mode <= 3:
            raise ValueError("vad_mode must be between 0 and 3")
        if not 2 <= cfg.start_frames <= 20:
            raise ValueError("start_frames must be between 2 and 20")
        if not 5 <= cfg.end_silence_frames <= 100:
            raise ValueError("end_silence_frames must be between 5 and 100")
        if not 3 <= cfg.max_turn_seconds <= 60:
            raise ValueError("max_turn_seconds must be between 3 and 60")
        return cfg


class WebRtcTurnDetector:
    def __init__(self, config: LocalVoiceConfig, classifier=None):
        self.config = config
        if classifier is None:
            try:
                import webrtcvad
            except Exception as exc:
                raise RuntimeError("webrtcvad-wheels is not installed") from exc
            vad = webrtcvad.Vad(config.vad_mode)
            classifier = lambda frame: vad.is_speech(frame, SAMPLE_RATE)
        self._classifier = classifier
        self._pre_roll: deque[bytes] = deque(maxlen=config.pre_roll_frames)
        self._segment: list[bytes] = []
        self._recent: deque[bool] = deque(maxlen=max(config.start_frames + 1, 8))
        self._speaking = False
        self._silence = 0
        self._max_frames = int(config.max_turn_seconds * 1000 / FRAME_MS)

    @property
    def speaking(self) -> bool:
        return self._speaking

    def feed(self, frame: bytes) -> tuple[str, bytes | None]:
        if len(frame) != FRAME_BYTES:
            return "ignored", None
        voiced = bool(self._classifier(frame))
        self._recent.append(voiced)
        if not self._speaking:
            self._pre_roll.append(frame)
            if sum(self._recent) >= self.config.start_frames:
                self._speaking = True
                self._silence = 0
                self._segment = list(self._pre_roll)
                return "speech_start", None
            return "silence", None
        self._segment.append(frame)
        self._silence = 0 if voiced else self._silence + 1
        if self._silence >= self.config.end_silence_frames or len(self._segment) >= self._max_frames:
            trim = min(self._silence, max(0, self.config.end_silence_frames // 2))
            frames = self._segment[:-trim] if trim else self._segment
            audio = b"".join(frames)
            self.reset()
            return "speech_end", audio
        return "speech", None

    def reset(self) -> None:
        self._pre_roll.clear()
        self._segment = []
        self._recent.clear()
        self._speaking = False
        self._silence = 0


class WhisperCppTranscriber:
    def __init__(self, binary: str | Path, model: str | Path, *, threads: int = 4, language: str = "es"):
        self.binary = Path(binary).expanduser()
        self.model = Path(model).expanduser()
        self.threads = max(1, min(int(threads), 8))
        self.language = str(language or "es")

    def ready(self) -> bool:
        return self.binary.exists() and os.access(self.binary, os.X_OK) and self.model.exists()

    def transcribe(self, pcm: bytes) -> str:
        if not self.ready():
            raise RuntimeError("whisper.cpp binary or model is missing")
        fd, name = tempfile.mkstemp(prefix="kira_turn_", suffix=".wav")
        os.close(fd)
        path = Path(name)
        try:
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            command = [
                str(self.binary), "-m", str(self.model), "-f", str(path),
                "-l", self.language, "-t", str(self.threads), "-nt", "-np",
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=90,
                encoding="utf-8", errors="replace",
            )
            if completed.returncode != 0:
                raise RuntimeError(f"whisper.cpp exited with code {completed.returncode}")
            return " ".join(completed.stdout.split()).strip()
        finally:
            path.unlink(missing_ok=True)


class MacSayOutput:
    def __init__(self, *, voice: str = "Jorge", rate: int = 190):
        self.voice = voice
        self.rate = int(rate)
        self._proc: asyncio.subprocess.Process | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    def ready(self) -> bool:
        return shutil.which("say") is not None

    def interrupt(self) -> None:
        self._generation += 1
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    async def speak(self, text: str) -> bool:
        speech = sanitize_for_speech(text)
        if not speech or not self.ready():
            return False
        async with self._lock:
            generation = self._generation
            self._proc = await asyncio.create_subprocess_exec(
                "say", "-v", self.voice, "-r", str(self.rate), speech,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                rc = await self._proc.wait()
                return rc == 0 and generation == self._generation
            except asyncio.CancelledError:
                self.interrupt()
                raise
            finally:
                self._proc = None


class LocalVoiceRuntime:
    def __init__(self, *, dispatcher, config: LocalVoiceConfig,
                 transcriber: WhisperCppTranscriber, output: MacSayOutput,
                 logger: Callable[[str], None] | None = None,
                 state_callback: Callable[[str], None] | None = None,
                 detector=None):
        self.dispatcher = dispatcher
        self.config = config
        self.transcriber = transcriber
        self.output = output
        self.logger = logger or (lambda _message: None)
        self.state_callback = state_callback or (lambda _state: None)
        self.state = VoiceState.IDLE
        self.detector = detector or WebRtcTurnDetector(config)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=250)
        self._stream = None
        self._task: asyncio.Task | None = None
        self._speech_task: asyncio.Task | None = None
        self.dropped_frames = 0

    def diagnose(self) -> dict:
        return {
            "mode": "local_turn",
            "ready": self.transcriber.ready() and self.output.ready(),
            "whisper_binary": self.transcriber.binary.exists(),
            "whisper_model": self.transcriber.model.exists(),
            "tts_say": self.output.ready(),
            "vad": type(self.detector).__name__,
            "dropped_frames": self.dropped_frames,
        }

    def _set_state(self, state: VoiceState) -> None:
        self.state = state
        self.state_callback(state.value)

    def _enqueue(self, frame: bytes) -> None:
        if self._frames.full():
            try:
                self._frames.get_nowait()
                self.dropped_frames += 1
            except asyncio.QueueEmpty:
                pass
        self._frames.put_nowait(frame)

    def _audio_callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            self.logger(f"VOICE: PortAudio status {status}")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue, bytes(indata))

    async def start(self, device=None) -> None:
        if not self.transcriber.ready():
            raise RuntimeError("Local voice not ready: install whisper.cpp and a model")
        if not self.output.ready():
            raise RuntimeError("Local voice not ready: macOS say is unavailable")
        import sounddevice as sd
        self._loop = asyncio.get_running_loop()
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1,
            dtype="int16", device=device, callback=self._audio_callback,
        )
        self._stream.start()
        self._set_state(VoiceState.LISTENING)
        self._task = asyncio.create_task(self._consume(), name="kira-local-voice")

    async def stop(self) -> None:
        self.interrupt()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._stream is not None:
            self._stream.abort()
            self._stream.close()
            self._stream = None
        self._set_state(VoiceState.IDLE)

    def interrupt(self) -> None:
        self.output.interrupt()
        if self._speech_task is not None:
            self._speech_task.cancel()
            self._speech_task = None
        self._set_state(VoiceState.INTERRUPTED)

    async def speak_text(self, text: str) -> bool:
        if self._speech_task is not None:
            self.interrupt()
        self._set_state(VoiceState.SPEAKING)
        self._speech_task = asyncio.create_task(self.output.speak(text), name="kira-local-tts")
        try:
            return await self._speech_task
        finally:
            self._speech_task = None
            self._set_state(VoiceState.LISTENING)

    async def _consume(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            frame = await self._frames.get()
            event, segment = self.detector.feed(frame)
            if event == "speech_start" and self.state == VoiceState.SPEAKING and self.config.barge_in:
                self.interrupt()
            if event != "speech_end" or not segment:
                continue
            self._set_state(VoiceState.TRANSCRIBING)
            try:
                transcript = await loop.run_in_executor(None, self.transcriber.transcribe, segment)
                if not transcript:
                    self._set_state(VoiceState.LISTENING)
                    continue
                self.logger(f"You: {transcript}")
                self._set_state(VoiceState.ROUTING)
                result = await loop.run_in_executor(
                    None,
                    lambda: self.dispatcher.dispatch(transcript, source="voice-local"),
                )
                self.logger(f"KIRA: {result.text}")
                if result.text:
                    await self.speak_text(result.text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger(f"VOICE: local turn failed ({type(exc).__name__})")
                self._set_state(VoiceState.DEGRADED)
                await asyncio.sleep(0.2)
                self._set_state(VoiceState.LISTENING)


def load_local_voice_config(base_dir: str | Path) -> tuple[LocalVoiceConfig, Path, Path]:
    base = Path(base_dir)
    path = base / "config" / "voice.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    cfg = LocalVoiceConfig.from_dict(payload.get("local", {}))
    binary = Path(payload.get("whisper_binary") or base / "vendor" / "whisper.cpp" / "build" / "bin" / "whisper-cli")
    model = Path(payload.get("whisper_model") or base / "models" / "whisper" / "ggml-base.bin")
    if not binary.is_absolute():
        binary = base / binary
    if not model.is_absolute():
        model = base / model
    return cfg, binary, model
