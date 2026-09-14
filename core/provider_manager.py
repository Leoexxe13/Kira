from __future__ import annotations
import json, os, time, urllib.request, urllib.error, ssl
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "config" / "providers.json"

# KIRA_GROQ_SSL_CONTEXT_V1
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()

@dataclass
class ProviderResult:
    ok: bool
    provider: str
    model: str
    text: str
    latency_ms: int
    error: str = ""

class ProviderManager:
    DEFAULTS = {
        "mode": "free_first",
        "monthly_budget_usd": 0.0,
        "providers": {
            "groq": {
                "enabled": False,
                "api_key": "",
                "model": "openai/gpt-oss-20b",
                "base_url": "https://api.groq.com/openai/v1/chat/completions",
                "timeout_seconds": 12
            },
            "openai": {"enabled": False, "api_key": "", "paid": True},
            "anthropic": {"enabled": False, "api_key": "", "paid": True}
        }
    }

    def __init__(self):
        self.cfg = self._load()

    def _load(self):
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not CFG_PATH.exists():
            CFG_PATH.write_text(json.dumps(self.DEFAULTS, indent=2), encoding="utf-8")
            return json.loads(json.dumps(self.DEFAULTS))
        try:
            cur = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
        merged = json.loads(json.dumps(self.DEFAULTS))
        merged.update({k:v for k,v in cur.items() if k != "providers"})
        for name, cfg in cur.get("providers", {}).items():
            if name in merged["providers"] and isinstance(cfg, dict):
                merged["providers"][name].update(cfg)
        return merged

    def reload(self):
        self.cfg = self._load()

    def _groq_key(self):
        return os.getenv("GROQ_API_KEY", "").strip() or str(
            self.cfg["providers"]["groq"].get("api_key", "")
        ).strip()

    def status(self):
        self.reload()
        g = self.cfg["providers"]["groq"]
        return {
            "mode": self.cfg.get("mode", "free_first"),
            "gemini_live": True,
            "groq": bool(g.get("enabled") and self._groq_key()),
            "openai": False,
            "anthropic": False,
        }

    def ask_free(self, prompt, system=""):
        self.reload()
        cfg = self.cfg["providers"]["groq"]
        key = self._groq_key()
        if not (cfg.get("enabled") and key):
            return ProviderResult(False, "groq", "", "", 0, "Groq no configurado")

        messages = []
        if system:
            messages.append({"role":"system","content":system})
        messages.append({"role":"user","content":prompt})
        body = json.dumps({
            "model": cfg.get("model") or "openai/gpt-oss-20b",
            "messages": messages,
            "temperature": 0.35,
            "max_tokens": 240
        }).encode()

        req = urllib.request.Request(
            cfg.get("base_url") or "https://api.groq.com/openai/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "KIRA/1.0"
            },
            method="POST"
        )

        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=int(cfg.get("timeout_seconds", 12)), context=SSL_CONTEXT) as r:
                payload = json.loads(r.read().decode())
            text = payload["choices"][0]["message"]["content"].strip()
            return ProviderResult(True, "groq", cfg.get("model",""), text,
                                  int((time.perf_counter()-t0)*1000))
        except Exception as e:
            return ProviderResult(False, "groq", cfg.get("model",""), "",
                                  int((time.perf_counter()-t0)*1000), str(e))
