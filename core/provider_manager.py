from __future__ import annotations
import json, os, time, urllib.request, urllib.error, ssl
import socket
import builtins
from dataclasses import dataclass, field
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

@dataclass
class ProviderHealth:
    state: str = 'UNAVAILABLE'
    last_success: float | None = None
    last_error: str = ''
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    latency_ms: int = 0

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
        self.health = {name: ProviderHealth() for name in ('groq', 'local', 'gemini')}
        self.cooldowns = {'groq': 30.0, 'local': 60.0, 'gemini': 30.0}

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
            "health": {name: vars(value).copy() for name, value in self.health.items()},
            "openai": False,
            "anthropic": False,
        }

    def interpret_request(self, text, context, capabilities, instructions):
        """Online text interpreter first; existing local client is the fallback."""
        prompt = json.dumps({'input': text, 'context': context, 'capabilities': capabilities}, ensure_ascii=False)
        online = self.ask_free(prompt, system=instructions, structured=True)
        if online.ok:
            if self.provider_health()['groq']['last_success'] is None:
                self._mark_success('groq', online.latency_ms)
            return online
        if online.error and self._available('groq'):
            self._mark_failure('groq', online.error)
        # A rate limited/unavailable provider is not retried in the same turn.
        local_error = ''
        if not self._available('local'):
            gemini = self._interpret_gemini(prompt, instructions)
            if gemini.ok:
                return gemini
            return ProviderResult(False, 'none', '', '', 0, online.error + '. Modelo local en cooldown. ' + gemini.error)
        try:
            from core.llm_client import call_llm_generate
            from core.semantic import PLAN_SCHEMA
            started = time.monotonic()
            # Ollama runs locally on CPU in this environment. Keep its semantic
            # request compact; the full registry remains authoritative in Core.
            compact_caps = [{'name': c.get('name'), 'description': c.get('description','')[:180],
                             'parameters': {'type':'OBJECT', 'properties': {
                                 k: {'type': v.get('type','STRING'), **({'enum': v['enum']} if 'enum' in v else {})}
                                 for k,v in (c.get('parameters',{}).get('properties',{}) or {}).items()},
                             'required': c.get('parameters',{}).get('required',[])}}
                            for c in capabilities]
            # Keep the first local pass small enough for CPU Ollama.  The full
            # operational state is still available to Core; the interpreter
            # only needs the fields relevant to resolving this turn.
            compact_context = context if isinstance(context, dict) else {}
            compact_context = json.loads(json.dumps(compact_context, ensure_ascii=False, default=str))
            for key, value in list(compact_context.items()):
                if isinstance(value, str):
                    compact_context[key] = value[:800]
                elif isinstance(value, list):
                    compact_context[key] = value[:8]
            local_prompt = json.dumps({'input': text[:1200], 'context': compact_context,
                                       'capabilities': compact_caps[:40]}, ensure_ascii=False)
            local_instructions = ('Devuelve SOLO JSON válido con las claves exactas: goal, domain, confidence, '
                                  'steps, clarification, response, confirmation, cancel. Usa únicamente las tools '
                                  'proporcionadas. No ejecutes nada ni inventes datos. Si es conversación normal, '
                                  'steps=[] y escribe una respuesta breve en response.')
            content = call_llm_generate(local_prompt, system=local_instructions,
                                        timeout=12, output_schema=PLAN_SCHEMA)
            if not content:
                raise ValueError('Empty local response')
            json.loads(content)  # Malformed transport output must reach the next provider.
            latency = int((time.monotonic()-started)*1000)
            self._mark_success('local', latency)
            return ProviderResult(True, 'local', '', content, latency)
        except Exception as exc:
            # Keep diagnostics useful without exposing prompts, keys or URLs.
            import logging
            logging.getLogger('kira.providers').exception(
                'Text interpreter local failure: online=%s local=%s', online.error, type(exc).__name__)
            cause = exc.__cause__ or exc
            from requests.exceptions import ConnectionError, Timeout
            if isinstance(cause, ConnectionError):
                local_error = 'No se pudo conectar con el servidor del modelo local.'
            elif isinstance(cause, (Timeout, TimeoutError)) or 'timed out' in str(exc).lower():
                local_error = 'El modelo local agotó el tiempo de espera.'
            else:
                local_error = 'La llamada al modelo local falló (' + type(exc).__name__ + ').'
            self._mark_failure('local', local_error,
                               'DISCONNECTED' if isinstance(cause, ConnectionError) or isinstance(exc, (ConnectionError, builtins.ConnectionError)) else None)
            gemini = self._interpret_gemini(prompt, instructions)
            if gemini.ok:
                return gemini
            return ProviderResult(False, 'none', '', '', 0, online.error + '. ' + local_error + ' ' + gemini.error)

    def _available(self, name):
        self._ensure_health()
        health = self.health[name]
        if time.monotonic() < health.cooldown_until:
            return False
        return True

    def _mark_success(self, name, latency_ms):
        self._ensure_health()
        health = self.health[name]
        health.state = 'AVAILABLE'; health.last_success = time.time()
        health.last_error = ''; health.consecutive_failures = 0
        health.cooldown_until = 0.0; health.latency_ms = int(latency_ms or 0)

    def _mark_failure(self, name, error, state=None):
        self._ensure_health()
        health = self.health[name]
        health.consecutive_failures += 1; health.last_error = str(error)[:240]
        health.latency_ms = 0
        health.state = state or ('RATE_LIMITED' if '429' in str(error) else 'DEGRADED')
        health.cooldown_until = time.monotonic() + self.cooldowns[name]

    def provider_health(self):
        self._ensure_health()
        return {name: vars(value).copy() for name, value in self.health.items()}

    def _ensure_health(self):
        if not hasattr(self, 'health'):
            self.health = {}
        for name in ('groq', 'local', 'gemini'):
            self.health.setdefault(name, ProviderHealth())
        if not hasattr(self, 'cooldowns'):
            self.cooldowns = {'groq': 30.0, 'local': 60.0, 'gemini': 30.0}

    def _interpret_gemini(self, prompt, instructions):
        """Existing Gemini text SDK, no tools, automatic execution or Live session."""
        from core.network_state import NETWORK
        if not NETWORK.allowed:
            return ProviderResult(False, 'gemini', '', '', 0, 'Gemini textual: conexión no disponible.')
        if not self._available('gemini'):
            return ProviderResult(False, 'gemini', '', '', 0, 'Gemini textual en cooldown temporal.')
        try:
            config = json.loads((ROOT / 'config' / 'api_keys.json').read_text())
        except (OSError, ValueError):
            config = {}
        key = os.getenv('GEMINI_API_KEY', '').strip() or config.get('gemini_api_key', '')
        if not key:
            return ProviderResult(False, 'gemini', '', '', 0, 'Gemini textual no configurado.')
        model = config.get('gemini_text_model') or 'gemini-flash-latest'
        start = time.perf_counter()
        try:
            from google import genai
            from google.genai import types
            with genai.Client(api_key=key, http_options=types.HttpOptions(timeout=12000)) as client:
                response = client.models.generate_content(model=model, contents=prompt,
                    config=types.GenerateContentConfig(system_instruction=instructions,
                        response_mime_type='application/json', temperature=0,
                        max_output_tokens=1800,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            content = response.text or ''
            if not content.strip():
                raise ValueError('Empty response')
            self._mark_success('gemini', (time.perf_counter()-start)*1000)
            return ProviderResult(True, 'gemini', model, content, int((time.perf_counter()-start)*1000))
        except Exception as exc:
            code = getattr(exc, 'code', None)
            detail = 'HTTP ' + str(code) if isinstance(code, int) else type(exc).__name__
            state = 'UNAVAILABLE' if code in (404, 401, 403) else 'DEGRADED'
            self._mark_failure('gemini', 'Gemini textual: ' + detail + '.', state)
            return ProviderResult(False, 'gemini', model, '', 0, 'Gemini textual: ' + detail + '.')

    def ask_free(self, prompt, system="", structured=False):
        from core.network_state import NETWORK
        if not NETWORK.allowed:
            return ProviderResult(False, "groq", "", "", 0, "OFFLINE: solicitud online omitida")
        if not self._available('groq'):
            return ProviderResult(False, 'groq', '', '', 0, 'Groq en cooldown temporal.')
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
            "temperature": 0 if structured else 0.35,
            "max_tokens": 1800 if structured else 240,
            **({'response_format': {'type': 'json_object'}} if structured else {})
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
            self._mark_success('groq', (time.perf_counter()-t0)*1000)
            return ProviderResult(True, "groq", cfg.get("model",""), text,
                                  int((time.perf_counter()-t0)*1000))
        except Exception as e:
            NETWORK.failure(e)
            if isinstance(e, urllib.error.HTTPError):
                detail = {401:'credenciales rechazadas',403:'acceso rechazado',429:'límite de solicitudes o cuota alcanzado',
                          413:'solicitud demasiado grande',503:'servicio temporalmente no disponible'}.get(e.code,'solicitud rechazada')
                error = f'Groq: HTTP {e.code}, {detail}'
            elif isinstance(getattr(e, 'reason', e), (socket.timeout, TimeoutError)):
                error = 'Groq: se agotó el tiempo de espera'
            elif isinstance(e, urllib.error.URLError):
                error = 'Groq: fallo de conexión (' + type(e.reason).__name__ + ')'
            else:
                error = 'Groq: respuesta no utilizable (' + type(e).__name__ + ')'
            self._mark_failure('groq', error, 'RATE_LIMITED' if isinstance(e, urllib.error.HTTPError) and e.code == 429 else None)
            return ProviderResult(False, "groq", cfg.get("model",""), "",
                                  int((time.perf_counter()-t0)*1000), error)
