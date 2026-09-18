from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "config" / "providers.json"
API_KEYS_PATH = ROOT / "config" / "api_keys.json"

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


@dataclass
class ProviderHealth:
    state: str = "UNAVAILABLE"
    last_success: float | None = None
    last_error: str = ""
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    latency_ms: int = 0


class ProviderManager:
    """Observable, bounded provider fallback for semantic interpretation."""

    DEFAULTS = {
        "mode": "free_first",
        "monthly_budget_usd": 0.0,
        "providers": {
            "groq": {
                "enabled": False,
                "api_key": "",
                "model": "openai/gpt-oss-20b",
                "base_url": "https://api.groq.com/openai/v1/chat/completions",
                "timeout_seconds": 10,
            },
            "local": {
                "enabled": True,
                "base_url": "http://localhost:11434",
                "model": "llama3.2:latest",
                "timeout_seconds": 6,
            },
            "gemini": {"enabled": True, "model": "gemini-2.5-flash", "timeout_seconds": 20},
        },
    }
    COOLDOWNS = {"groq": 30.0, "local": 10.0, "gemini": 30.0}

    def __init__(self):
        self.cfg = self._load()
        self.health = {name: ProviderHealth() for name in self.COOLDOWNS}

    def _load(self) -> dict:
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not CFG_PATH.exists():
            CFG_PATH.write_text(json.dumps(self.DEFAULTS, indent=2), encoding="utf-8")
            return json.loads(json.dumps(self.DEFAULTS))
        try:
            current = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        merged = json.loads(json.dumps(self.DEFAULTS))
        merged.update({key: value for key, value in current.items() if key != "providers"})
        for name, values in current.get("providers", {}).items():
            if name in merged["providers"] and isinstance(values, dict):
                merged["providers"][name].update(values)
        return merged

    def reload(self):
        self.cfg = self._load()

    def _config(self, name: str) -> dict:
        return self.cfg.get("providers", {}).get(name, {})

    def _groq_key(self) -> str:
        return os.getenv("GROQ_API_KEY", "").strip() or str(self._config("groq").get("api_key", "")).strip()

    def _available(self, name: str) -> bool:
        health = self.health[name]
        return time.monotonic() >= health.cooldown_until

    def _mark_success(self, name: str, latency_ms: int):
        health = self.health[name]
        health.state = "AVAILABLE"
        health.last_success = time.time()
        health.last_error = ""
        health.consecutive_failures = 0
        health.cooldown_until = 0.0
        health.latency_ms = int(latency_ms or 0)

    def _mark_failure(self, name: str, error: str, state: str | None = None):
        health = self.health[name]
        health.consecutive_failures += 1
        health.last_error = str(error)[:240]
        text = str(error)
        health.state = state or ("RATE_LIMITED" if "429" in text else "DEGRADED")
        health.cooldown_until = time.monotonic() + self.COOLDOWNS[name]
        health.latency_ms = 0

    def provider_health(self) -> dict:
        return {name: vars(value).copy() for name, value in self.health.items()}

    def status(self) -> dict:
        self.reload()
        try:
            import google.genai  # noqa: F401
            gemini_sdk = True
        except Exception:
            gemini_sdk = False
        return {
            "mode": self.cfg.get("mode", "free_first"),
            "gemini_live": True,
            "groq": bool(self._config("groq").get("enabled") and self._groq_key()),
            "ollama": bool(self._config("local").get("enabled", True)),
            "gemini_text": bool(self._gemini_key()),
            "gemini_sdk": gemini_sdk,
            "health": self.provider_health(),
        }

    def diagnose(self) -> dict:
        """Report readiness without printing or returning any secret value."""
        self.reload()
        local_cfg = self._config("local")
        local_url = str(local_cfg.get("base_url", "http://localhost:11434")).rstrip("/")
        local_reachable = False
        local_error = ""
        if local_cfg.get("enabled", True):
            try:
                with urllib.request.urlopen(f"{local_url}/api/tags", timeout=1.5):
                    local_reachable = True
            except Exception as exc:
                local_error = type(exc).__name__
        try:
            import google.genai  # noqa: F401
            gemini_sdk = True
        except Exception as exc:
            gemini_sdk = False
            gemini_error = type(exc).__name__
        else:
            gemini_error = ""
        return {
            "groq_configured": bool(self._config("groq").get("enabled") and self._groq_key()),
            "gemini_key_configured": bool(self._gemini_key()),
            "gemini_sdk_installed": gemini_sdk,
            "gemini_sdk_error": gemini_error,
            "ollama_enabled": bool(local_cfg.get("enabled", True)),
            "ollama_reachable": local_reachable,
            "ollama_error": local_error,
            "recommended": "gemini" if self._gemini_key() and gemini_sdk else ("ollama" if local_reachable else "configure_gemini_or_ollama"),
            "health": self.provider_health(),
        }

    def _gemini_key(self) -> str:
        try:
            config = json.loads(API_KEYS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        return os.getenv("GEMINI_API_KEY", "").strip() or str(config.get("gemini_api_key", "")).strip()

    @staticmethod
    def _messages(prompt: str, system: str) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        result.append({"role": "user", "content": prompt})
        return result

    def ask_free(self, prompt: str, system: str = "", *, structured: bool = False) -> ProviderResult:
        if not self._available("groq"):
            return ProviderResult(False, "groq", "", "", 0, "Groq en cooldown temporal")
        cfg = self._config("groq")
        key = self._groq_key()
        if not (cfg.get("enabled") and key):
            return ProviderResult(False, "groq", "", "", 0, "Groq no configurado")
        body = {
            "model": cfg.get("model") or "openai/gpt-oss-20b",
            "messages": self._messages(prompt, system),
            "temperature": 0 if structured else 0.35,
            "max_tokens": 1400 if structured else 240,
        }
        if structured:
            body["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            cfg.get("base_url") or self.DEFAULTS["providers"]["groq"]["base_url"],
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "KIRA/1.0"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=float(cfg.get("timeout_seconds", 10)), context=SSL_CONTEXT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            text = str(payload["choices"][0]["message"].get("content") or "").strip()
            if not text:
                raise ValueError("empty provider response")
            latency = int((time.perf_counter() - started) * 1000)
            self._mark_success("groq", latency)
            return ProviderResult(True, "groq", str(cfg.get("model", "")), text, latency)
        except urllib.error.HTTPError as exc:
            error = f"Groq HTTP {exc.code}"
            self._mark_failure("groq", error, "RATE_LIMITED" if exc.code == 429 else None)
            return ProviderResult(False, "groq", str(cfg.get("model", "")), "", int((time.perf_counter() - started) * 1000), error)
        except Exception as exc:
            error = f"Groq {type(exc).__name__}"
            self._mark_failure("groq", error)
            return ProviderResult(False, "groq", str(cfg.get("model", "")), "", int((time.perf_counter() - started) * 1000), error)

    def _ask_local(self, prompt: str, system: str) -> ProviderResult:
        if not self._available("local"):
            return ProviderResult(False, "local", "", "", 0, "Ollama en cooldown temporal")
        cfg = self._config("local")
        if not cfg.get("enabled", True):
            return ProviderResult(False, "local", "", "", 0, "Ollama no configurado")
        url = str(cfg.get("base_url", "http://localhost:11434")).rstrip("/")
        if not url.startswith("http://localhost:") and not url.startswith("http://127.0.0.1:"):
            self._mark_failure("local", "local endpoint rejected", "UNAVAILABLE")
            return ProviderResult(False, "local", "", "", 0, "Ollama local requiere localhost")
        payload = {"model": cfg.get("model", "llama3.2:latest"), "messages": self._messages(prompt, system), "stream": False, "options": {"num_predict": 900}}
        request = urllib.request.Request(f"{url}/api/chat", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=float(cfg.get("timeout_seconds", 6))) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = str(data.get("message", {}).get("content") or "").strip()
            if not text:
                raise ValueError("empty local response")
            latency = int((time.perf_counter() - started) * 1000)
            self._mark_success("local", latency)
            return ProviderResult(True, "local", str(cfg.get("model", "")), text, latency)
        except urllib.error.HTTPError as exc:
            error = f"Ollama HTTP {exc.code}"
            self._mark_failure("local", error)
            return ProviderResult(False, "local", str(cfg.get("model", "")), "", int((time.perf_counter() - started) * 1000), error)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            error = f"Ollama {type(exc).__name__}"
            self._mark_failure("local", error, "DISCONNECTED")
            return ProviderResult(False, "local", str(cfg.get("model", "")), "", int((time.perf_counter() - started) * 1000), error)
        except Exception as exc:
            error = f"Ollama {type(exc).__name__}"
            self._mark_failure("local", error)
            return ProviderResult(False, "local", str(cfg.get("model", "")), "", int((time.perf_counter() - started) * 1000), error)

    def _ask_gemini(self, prompt: str, system: str) -> ProviderResult:
        if not self._available("gemini"):
            return ProviderResult(False, "gemini", "", "", 0, "Gemini textual en cooldown temporal")
        key = self._gemini_key()
        if not key or not self._config("gemini").get("enabled", True):
            return ProviderResult(False, "gemini", "", "", 0, "Gemini textual no configurado")
        try:
            from google import genai
            from google.genai import types
            model = self._config("gemini").get("model", "gemini-2.5-flash")
            started = time.perf_counter()
            timeout_ms = int(float(self._config("gemini").get("timeout_seconds", 20)) * 1000)
            with genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms)) as client:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        temperature=0,
                        max_output_tokens=1400,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    ),
                )
            text = str(response.text or "").strip()
            if not text:
                raise ValueError("empty Gemini response")
            latency = int((time.perf_counter() - started) * 1000)
            self._mark_success("gemini", latency)
            return ProviderResult(True, "gemini", model, text, latency)
        except Exception as exc:
            detail = " ".join(str(exc).split())[:360]
            error = f"Gemini {type(exc).__name__}: {detail}" if detail else f"Gemini {type(exc).__name__}"
            self._mark_failure("gemini", error)
            return ProviderResult(False, "gemini", model if "model" in locals() else "", "", 0, error)

    def interpret_request(self, text: str, context: dict, capabilities: list[dict], instructions: str = "") -> ProviderResult:
        """Try each configured interpreter at most once, without long retries."""
        prompt = json.dumps({"input": str(text)[:2000], "context": context, "capabilities": capabilities}, ensure_ascii=False, default=str)
        failures: list[str] = []
        online = self.ask_free(prompt, system=instructions, structured=True)
        if online.ok:
            return online
        failures.append(online.error or "Groq unavailable")
        local = self._ask_local(prompt, instructions)
        if local.ok:
            return local
        failures.append(local.error or "Ollama unavailable")
        gemini = self._ask_gemini(prompt, instructions)
        if gemini.ok:
            return gemini
        failures.append(gemini.error or "Gemini unavailable")
        return ProviderResult(False, "none", "", "", 0, " | ".join(failures)[:700])
