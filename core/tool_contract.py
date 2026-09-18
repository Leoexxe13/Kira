"""Canonical tool result contract and legacy adapters for KIRA."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any


_FAILURE_MARKERS = (
    "error", "failed", "falló", "fallo", "no pude", "no se pudo", "timeout",
    "unavailable", "not found", "unknown action", "access denied", "blocked",
    "could not", "cannot ", "can't ",
)
_PENDING_MARKERS = (
    "login_required", "requires_confirmation", "unverified", "candidates",
    "necesito saber", "no pude identificar", "falta el nombre", "falta el texto",
)
_VERIFIED_PREFIXES = (
    "WHATSAPP_VERIFIED_", "VERIFIED_", "FILE_VERIFIED_", "APP_VERIFIED_",
)


@dataclass
class ToolExecution:
    tool: str
    state: str
    text: str
    data: Any = None
    verified: bool = False
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state in {"executed", "verified"}

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "state": self.state,
            "text": self.text,
            "data": deepcopy(self.data),
            "verified": bool(self.verified),
            "error": self.error,
            "metadata": deepcopy(self.metadata),
        }


def _parse_prefixed_json(text: str) -> tuple[str, Any] | None:
    if ":" not in text:
        return None
    prefix, payload = text.split(":", 1)
    prefix = prefix.strip()
    if not prefix or not prefix.replace("_", "").isalnum():
        return None
    payload = payload.strip()
    if not payload.startswith(("{", "[")):
        return None
    try:
        return prefix, json.loads(payload)
    except (TypeError, ValueError):
        return None


def normalize_tool_result(tool: str, raw: Any) -> ToolExecution:
    """Convert every handler return value into one explicit result object."""
    if isinstance(raw, ToolExecution):
        return raw
    if hasattr(raw, "as_dict") and callable(raw.as_dict):
        try:
            raw = raw.as_dict()
        except Exception:
            pass
    if isinstance(raw, dict):
        state = str(raw.get("state") or ("verified" if raw.get("verified") else "executed"))
        text = str(raw.get("text", raw.get("result", "")) or "")
        error = str(raw.get("error", "") or "")
        if error and state in {"executed", "verified"}:
            state = "failed"
        return ToolExecution(
            tool=tool,
            state=state,
            text=text,
            data=deepcopy(raw.get("data")),
            verified=bool(raw.get("verified", state == "verified")) and state == "verified",
            error=error,
            metadata=deepcopy(raw.get("metadata") or {}),
        )

    text = str(raw or "").strip() or "Sin resultado."
    parsed = _parse_prefixed_json(text)
    if parsed:
        marker, payload = parsed
        upper = marker.upper()
        verified = upper.startswith(_VERIFIED_PREFIXES) or "_VERIFIED_" in upper
        pending = any(token in upper for token in ("LOGIN_REQUIRED", "UNVERIFIED", "CANDIDATE", "PENDING"))
        failed = any(token in upper for token in ("ERROR", "FAILED", "BLOCKED", "UNAVAILABLE"))
        state = "verified" if verified else "pending" if pending else "failed" if failed else "executed"
        message = ""
        if isinstance(payload, dict):
            message = str(payload.get("text") or payload.get("message") or payload.get("reason") or "")
        return ToolExecution(
            tool=tool,
            state=state,
            text=message or text,
            data=payload,
            verified=verified,
            error=(message or text) if failed else "",
            metadata={"legacy_marker": marker},
        )

    low = text.casefold()
    if any(marker in low for marker in _PENDING_MARKERS):
        state = "pending"
    elif any(marker in low for marker in _FAILURE_MARKERS):
        state = "failed"
    else:
        state = "executed"
    return ToolExecution(
        tool=tool,
        state=state,
        text=text,
        verified=False,
        error=text if state == "failed" else "",
        metadata={"legacy_string": True},
    )
