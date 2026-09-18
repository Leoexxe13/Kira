"""Deterministic safety policy for KIRA tool execution.

The planner may propose work, but it never decides whether an operation is safe
or whether user confirmation is required. This module classifies concrete tool
calls after arguments have been resolved and before the handler runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import json
from typing import Any


class RiskLevel(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30


@dataclass(frozen=True)
class PolicyDecision:
    risk: RiskLevel
    confirmation_required: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "risk": self.risk.name.lower(),
            "confirmation_required": self.confirmation_required,
            "reason": self.reason,
        }


_READ_ACTIONS = {
    "status", "list", "read", "find", "search", "recent", "unread",
    "last", "last_incoming", "last_outgoing", "last_audio", "info",
    "disk_usage", "largest", "recall", "get", "query", "open", "show",
}
_MEDIUM_ACTIONS = {
    "create", "create_file", "create_folder", "copy", "move", "rename",
    "write", "append", "update", "remember", "forget", "complete", "add",
    "select", "open_chat", "organize_desktop", "set", "change",
}
_HIGH_ACTIONS = {
    "delete", "remove", "erase", "trash", "send", "reply", "message",
    "shutdown", "restart", "reboot", "logout", "format", "reset",
    "run", "execute", "build", "screen_debug", "install",
}
_HIGH_TOOLS = {"send_message"}


def _action(arguments: dict[str, Any]) -> str:
    for key in ("action", "operation", "command"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold().replace(" ", "_")
    return ""


def classify_operation(tool: str, arguments: dict[str, Any], metadata: dict | None = None) -> PolicyDecision:
    """Classify one resolved tool call.

    Tool metadata is authoritative when present. Legacy tools are classified by
    their stable tool/action identifiers, never by arbitrary user text.
    """
    metadata = metadata or {}
    declared = str(metadata.get("risk", "")).casefold()
    if declared in {"low", "medium", "high"}:
        risk = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}[declared]
        required = bool(metadata.get("confirmation_required", risk is RiskLevel.HIGH))
        return PolicyDecision(risk, required, "tool metadata")

    name = str(tool or "").casefold()
    action = _action(arguments)
    if name == "code_helper" and action in {"auto", "run", "execute", "build", "screen_debug"}:
        return PolicyDecision(RiskLevel.HIGH, True, f"code execution or screen disclosure {name}:{action}")
    if name in _HIGH_TOOLS or action in _HIGH_ACTIONS:
        return PolicyDecision(RiskLevel.HIGH, True, f"high-impact {name}:{action or 'default'}")
    if action in _MEDIUM_ACTIONS:
        return PolicyDecision(RiskLevel.MEDIUM, False, f"state-changing {name}:{action}")
    if action in _READ_ACTIONS:
        return PolicyDecision(RiskLevel.LOW, False, f"read-only {name}:{action}")

    # Unknown legacy operations are not silently treated as harmless. They may
    # execute without a confirmation only when the tool explicitly declares
    # read_only=True; otherwise they remain medium risk and observable.
    if metadata.get("read_only") is True:
        return PolicyDecision(RiskLevel.LOW, False, "declared read-only")
    return PolicyDecision(RiskLevel.MEDIUM, False, f"unclassified legacy operation {name}")


def confirmation_summary(tool: str, arguments: dict[str, Any], decision: PolicyDecision) -> str:
    safe = {}
    for key, value in arguments.items():
        low = str(key).casefold()
        if any(secret in low for secret in ("password", "token", "secret", "api_key", "credential")):
            safe[key] = "[omitido]"
        elif isinstance(value, str):
            safe[key] = value[:180]
        else:
            safe[key] = value
    detail = json.dumps(safe, ensure_ascii=False, default=str)
    return f"Confirma la operación de alto riesgo `{tool}` con estos datos: {detail}"
