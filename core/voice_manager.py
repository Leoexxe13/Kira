# KIRA_V63_VOICE_MANAGER
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "voice.json"

DEFAULTS = {
    "mode": "gemini_primary_edge_fallback",
    "edge_enabled": True,
    "edge_voice": "es-MX-JorgeNeural",
    "edge_rate": "+3%",
    "edge_volume": "+0%",
    "gemini_start_timeout_seconds": 2.8,
    "idle_mic_after_seconds": 180,
    "idle_wake_level": 0.055,
}

def load_config() -> dict:
    data = {}
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update(data)
    return out

def sanitize_for_speech(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]+\)", r"\1", s)
    s = re.sub(r"https?://\S+|www\.\S+", "", s)
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s*[-*•>]+\s*", "", s, flags=re.M)
    s = re.sub(r"[*_~]+", "", s)
    s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", s)
    return re.sub(r"\s+", " ", s).strip()[:2600]

class VoiceManager:
    def __init__(self, logger=None, turns=None, speaking=None):
        self.logger = logger or (lambda _m: None)
        self._lock: Optional[asyncio.Lock] = None
        self._proc = None
        self._generation = 0
        self._last_speech = ("", 0.0)
        self.turns = turns
        self.speaking = speaking or (lambda _:None)

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def stop_now(self) -> None:
        self._generation += 1
        p = self._proc
        self._proc = None
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass

    async def speak_edge(self, text: str, reason: str = "fallback") -> bool:
        from core.network_state import NETWORK
        if not NETWORK.allowed:
            return await self.speak_offline(text)
        cfg = load_config()
        if not cfg.get("edge_enabled", True):
            return False
        speech = sanitize_for_speech(text)
        if not speech:
            return False
        try:
            import edge_tts
        except Exception as e:
            self.logger(f"ERR: Edge TTS no disponible // {e}")
            return False

        generation = self._generation
        async with self._get_lock():
            if generation != self._generation or self._duplicate(speech):
                return False
            tmp = None
            try:
                fd, name = tempfile.mkstemp(prefix="kira_voice_", suffix=".mp3")
                import os
                os.close(fd)
                tmp = Path(name)
                communicate = edge_tts.Communicate(
                    speech,
                    voice=str(cfg.get("edge_voice", "es-MX-JorgeNeural")),
                    rate=str(cfg.get("edge_rate", "+3%")),
                    volume=str(cfg.get("edge_volume", "+0%")),
                )
                await asyncio.wait_for(communicate.save(str(tmp)), timeout=15)
                if generation != self._generation:
                    return False
                self.logger(f"SYS: voz // Edge respaldo ({reason})")
                return await self._play_file(tmp, generation)
            except asyncio.CancelledError:
                self.stop_now()
                raise
            except Exception as e:
                NETWORK.failure(e)
                self.stop_now()
                self.logger(f"ERR: Edge TTS // {type(e).__name__}: {e}")
                return False
            finally:
                if tmp is not None:
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass

    def _duplicate(self, speech):
        import time
        now = time.monotonic()
        duplicate = self._last_speech[0] == speech and now-self._last_speech[1]<8
        if not duplicate: self._last_speech=(speech,now)
        return duplicate

    async def _play_file(self, source, generation):
        """Decode with built-in macOS utility, play on the selected device."""
        import os
        fd, name = tempfile.mkstemp(prefix='kira_pcm_',suffix='.wav')
        os.close(fd)
        wav=Path(name)
        try:
            self._proc=await asyncio.create_subprocess_exec('afconvert','-f','WAVE','-d','LEI16',str(source),str(wav),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            rc=await asyncio.wait_for(self._proc.wait(), timeout=15)
            self._proc=None
            if rc or generation!=self._generation:return False
            owner = object()
            acquired = False
            deadline = asyncio.get_running_loop().time()+10
            if self.turns:
                while not self.turns.acquire('output',owner):
                    if generation != self._generation or asyncio.get_running_loop().time()>deadline:return False
                    await asyncio.sleep(.03)
                acquired = True
            def play():
                import wave
                import sounddevice as sd
                from core.audio_devices import resolve
                from memory.config_manager import get_output_device
                with wave.open(str(wav),'rb') as audio:
                    with sd.RawOutputStream(samplerate=audio.getframerate(),channels=audio.getnchannels(),dtype='int16',device=resolve(get_output_device(),'output')) as stream:
                        self.speaking(True)
                        while generation==self._generation:
                            chunk=audio.readframes(max(1,audio.getframerate()//20))
                            if not chunk: return True
                            if self.turns:self.turns.output(chunk,audio.getframerate())
                            stream.write(chunk)
                return False
            playback = asyncio.create_task(asyncio.to_thread(play))
            try:
                return await asyncio.shield(playback)
            except asyncio.CancelledError:
                self.stop_now()
                await playback
                raise
            finally:
                if acquired:self.turns.release('output',owner)
                self.speaking(False)
        finally:
            if self._proc is not None:
                self.stop_now()
            wav.unlink(missing_ok=True)

    async def speak_offline(self, text):
        """macOS built-in synthesis only as OFFLINE fallback, not primary voice."""
        import os, platform
        if platform.system()!='Darwin':return False
        speech=sanitize_for_speech(text)
        if not speech:return False
        generation=self._generation
        async with self._get_lock():
            if generation!=self._generation or self._duplicate(speech):return False
            files=[]
            try:
                for suffix in ('.txt','.aiff'):
                    fd,name=tempfile.mkstemp(prefix='kira_offline_',suffix=suffix)
                    os.close(fd);files.append(Path(name))
                files[0].write_text(speech,encoding='utf-8')
                self.logger('SYS: Voz local de respaldo; Gemini no se está usando')
                self._proc=await asyncio.create_subprocess_exec('say','-o',str(files[1]),'-f',str(files[0]),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                rc=await asyncio.wait_for(self._proc.wait(),timeout=20)
                self._proc=None
                if rc or generation!=self._generation:return False
                return await self._play_file(files[1],generation)
            except asyncio.CancelledError:
                self.stop_now();raise
            except Exception as e:
                self.stop_now()
                self.logger(f'ERR: Voz local no disponible: {type(e).__name__}')
                return False
            finally:
                for path in files:path.unlink(missing_ok=True)
