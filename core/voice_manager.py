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
    def __init__(self, logger=None):
        self.logger = logger or (lambda _m: None)
        self._lock: Optional[asyncio.Lock] = None
        self._proc = None
        self._generation = 0

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

        async with self._get_lock():
            generation = self._generation
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
                await communicate.save(str(tmp))
                if generation != self._generation:
                    return False
                self.logger(f"SYS: voz // Edge respaldo ({reason})")
                self._proc = await asyncio.create_subprocess_exec(
                    "afplay", str(tmp),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                rc = await self._proc.wait()
                self._proc = None
                return rc == 0
            except asyncio.CancelledError:
                self.stop_now()
                raise
            except Exception as e:
                self._proc = None
                self.logger(f"ERR: Edge TTS // {e}")
                return False
            finally:
                if tmp is not None:
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
