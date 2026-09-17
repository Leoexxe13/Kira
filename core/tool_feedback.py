"""Small, language-safe bridge from tool results to the conversational model."""
from __future__ import annotations

import re
import json
from collections import defaultdict

_failures = defaultdict(int)

_FAIL_RE = re.compile(
    r"(?:^|\b)(?:error|failed|failure|fall[oó]|no pude|unable|exception|blocked|unverified|"
    r"login_required|send_requested|timeout|timed out|not available|no se pudo|could not|not found|access denied|acceso denegado)\b|ERROR[_:]",
    re.IGNORECASE,
)


def _natural_detail(raw: str) -> str:
    text = str(raw or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    # Keep the technical reference in logs/results, but make the spoken bridge short.
    text = re.sub(r"^(?:Tool|Action) ['\"].*?['\"] (?:failed|crashed during run\(\)):?\s*", "", text, flags=re.I)
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return text


def classify_result(tool_name: str, result: str) -> tuple[str, bool, int]:
    """Return (result with status prefix, failed, consecutive failure count)."""
    raw = str(result or "").strip()
    if raw.startswith('WHATSAPP_UNVERIFIED:'):
        try:
            payload = json.loads(raw.split(':',1)[1])
            if payload.get('selection_supported') and payload.get('candidates'):
                _failures[tool_name] = 0
                return '[TOOL_PENDING] ' + raw, False, 0
        except (ValueError, AttributeError):
            pass
    unverified = raw.startswith(("WHATSAPP_SEND_REQUESTED", "[TOOL_UNVERIFIED]", "SOLICITADO:"))
    failed = bool(_FAIL_RE.search(raw)) or raw.startswith(("WHATSAPP_UNVERIFIED", "WHATSAPP_SEND_BLOCKED"))
    if unverified and not raw.startswith('[TOOL_FAILURE'):
        _failures[tool_name] = 0
        return f"[TOOL_UNVERIFIED] {raw}", False, 0
    if failed:
        _failures[tool_name] += 1
        count = _failures[tool_name]
        if count >= 2 and raw:
            # Do not repeat the same long explanation on consecutive failures.
            return f"[TOOL_FAILURE_REPEAT count={count}] {raw}", True, count
        return f"[TOOL_FAILURE] {raw}", True, count
    _failures[tool_name] = 0
    return f"[TOOL_RESULT] {raw}", False, 0


def reset() -> None:
    _failures.clear()


def spoken_summary(tool_name: str, result: str, verified: bool | None = None) -> str:
    """Natural Spanish instruction for the existing Gemini Live response turn."""
    raw = str(result or "").strip()
    if raw.startswith('[TOOL_PENDING]'):
        return 'Presenta las opciones con su nombre e identificación disponible. Pregunta cuál usar; conserva la operación pendiente. No repitas la búsqueda. Datos: ' + raw
    explicit_failure = raw.startswith(("[TOOL_FAILURE]", "[TOOL_FAILURE_REPEAT", "WHATSAPP_UNVERIFIED", "WHATSAPP_SEND_BLOCKED"))
    explicit_unverified = raw.startswith("[TOOL_UNVERIFIED]")
    if (verified is False or explicit_unverified) and not explicit_failure:
        return f"La acción fue solicitada, pero la herramienta {tool_name} no devolvió confirmación verificable. Comunícalo así y ofrece una alternativa; no afirmes que se completó."
    failed = bool(_FAIL_RE.search(raw)) or explicit_failure
    if failed:
        detail = _natural_detail(raw)
        if "TOOL_FAILURE_REPEAT" in raw:
            return f"La herramienta {tool_name} sigue fallando; no repetiré la misma explicación. Detalle: {detail}. Ofrece una alternativa útil."
        return f"La herramienta {tool_name} no pudo completar la acción. Explica brevemente el fallo real: {detail}. No inventes una causa ni digas que se completó."
    if verified is False:
        return f"La acción fue solicitada, pero {tool_name} no devolvió confirmación verificable. Comunícalo así y ofrece una alternativa; no afirmes que se completó."
    return f"Comunica únicamente el resultado confirmado de {tool_name} en español. Para acciones simples, una frase corta con el resultado primero, sin repetir la orden ni saludar; varía naturalmente. Distingue entre acción solicitada, ejecutada y verificada. Amplía solo si el usuario pidió análisis o explicación. Resultado: {raw}"


class EventSummary:
    """Compact UI events; callers retain technical details in their logger."""
    def __init__(self, clock=None):
        import time
        self.clock = clock or time.monotonic
        self.seen = {}

    def format(self, raw):
        from core.network_state import transport_failure
        text = str(raw).strip()
        if transport_failure(RuntimeError(text)):
            service = next((n for n in ('Groq', 'Gemini', 'Edge') if n.lower() in text.lower()), 'Servicio online')
            text = f'{service} no disponible: fallo de conexión.'
        elif 'unexpected keyword argument' in text:
            text = 'Herramienta no completada: incompatibilidad interna de argumentos.'
        elif 'sync api' in text.lower() and 'asyncio' in text.lower():
            text = 'WhatsApp encontró un problema interno al abrir el navegador.'
        else:
            text = re.sub(r'https?://\S+', '[enlace]', text)
            text = re.sub(r'(?i)(api[_-]?key|token|authorization|cookie)\s*[:=]\s*\S+', r'\1=[oculto]', text)
            text = _natural_detail(text)
        now = self.clock()
        self.seen = {k:v for k,v in self.seen.items() if now-v<60}
        if text in self.seen:
            return None
        self.seen[text] = now
        return text
