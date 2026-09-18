"""Bounded operational context for KIRA's semantic text runtime.

This state is intentionally separate from persistent user memory. It stores
short-lived references to verified resources and the last operation result so a
follow-up such as "muévelo al escritorio" can resolve "lo" without linguistic
pattern lists.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import threading
import time
from typing import Any


DEFAULT_TTL_SECONDS = 30 * 60
MAX_HISTORY_TURNS = 8
MAX_CANDIDATES = 30
MAX_RESULT_TEXT = 2000


class OperationalContext:
    """Thread-safe, bounded, expiring operational state.

    Values are data, not instructions. Paths are stored only when they exist
    and are checked again when read. Pending operations are data for the core to
    resume, never an authorization to execute.
    """

    def __init__(self, *, clock=time.monotonic, ttl: float = DEFAULT_TTL_SECONDS):
        self.clock = clock
        self.ttl = float(ttl)
        self._lock = threading.RLock()
        self._items: dict[str, tuple[Any, float]] = {}
        self._state: dict[str, Any] = {}

    def _now(self) -> float:
        return float(self.clock())

    def remember(self, key: str, value: Any) -> bool:
        """Remember a resource or scalar with a TTL.

        Existing filesystem paths are normalized and must exist. Other values
        are copied and bounded by the caller's structured-result contract.
        """
        if not key:
            return False
        if isinstance(value, (str, Path)) and key.startswith("last_"):
            path = Path(value).expanduser().resolve()
            if not path.exists():
                return False
            value = str(path)
        with self._lock:
            self._items[str(key)] = (deepcopy(value), self._now())
        return True

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return deepcopy(default)
            value, created = entry
            if self._now() - created >= self.ttl:
                self._items.pop(key, None)
                return deepcopy(default)
            if isinstance(value, str) and key.startswith("last_"):
                path = Path(value)
                if not path.exists():
                    self._items.pop(key, None)
                    return deepcopy(default)
                return path
            return deepcopy(value)

    def forget(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def resource(self, path: str | Path, *, created=False, opened=False,
                 downloaded=False, verified=False) -> bool:
        """Register a real resource and derive standard last_* references."""
        candidate = Path(path).expanduser().resolve()
        if not candidate.exists():
            return False
        self.remember("last_folder" if candidate.is_dir() else "last_file", candidate)
        self.remember("last_resource", candidate)
        if created:
            self.remember("last_created_resource", candidate)
        if opened:
            self.remember("last_opened_resource", candidate)
        if downloaded:
            self.remember("last_download", candidate)
        if verified:
            self.remember("last_verified_resource", candidate)
        if candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            self.remember("last_image", candidate)
        return True

    def update(self, **values: Any) -> None:
        """Update bounded structured state. Values are never executed as code."""
        with self._lock:
            for key, value in values.items():
                self._state[key] = deepcopy(value)
            history = self._state.get("conversation")
            if isinstance(history, list):
                self._state["conversation"] = history[-MAX_HISTORY_TURNS:]
            candidates = self._state.get("pending_candidates")
            if isinstance(candidates, list):
                self._state["pending_candidates"] = candidates[:MAX_CANDIDATES]
            result = self._state.get("last_tool_result")
            if isinstance(result, dict) and isinstance(result.get("text"), str):
                result["text"] = result["text"][:MAX_RESULT_TEXT]

    def restore(self, values: dict[str, Any]) -> None:
        """Restore only persisted operational keys; conversation is never restored."""
        if not isinstance(values, dict):
            return
        allowed = {
            "last_file", "last_folder", "last_download", "last_app", "last_chat",
            "last_contact", "last_tool_result", "last_created_resource",
            "last_opened_resource", "last_verified_chat", "pending_operation",
            "pending_candidates", "last_user_goal",
        }
        with self._lock:
            for key in allowed:
                if key not in values:
                    continue
                value = values[key]
                if key in {"last_file", "last_folder", "last_download", "last_created_resource", "last_opened_resource"}:
                    if not isinstance(value, str) or not Path(value).expanduser().exists():
                        continue
                self._state[key] = deepcopy(value)

    def value(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return deepcopy(self._state.get(key, default))

    def turn(self, role: str, text: str) -> None:
        with self._lock:
            history = self._state.setdefault("conversation", [])
            if not isinstance(history, list):
                history = []
                self._state["conversation"] = history
            history.append({"role": str(role), "text": str(text)[:MAX_RESULT_TEXT]})
            del history[:-MAX_HISTORY_TURNS]

    def snapshot(self) -> dict[str, Any]:
        """Return prompt-safe state, revalidating filesystem references."""
        with self._lock:
            state = deepcopy(self._state)
            for key in tuple(self._items):
                value = self.get(key)
                if value is not None:
                    state[key] = str(value) if isinstance(value, Path) else value
            return state

    def prompt_context(self) -> dict[str, Any]:
        """Expose only relevant, bounded operational data to an interpreter."""
        snapshot = self.snapshot()
        allowed = {
            "conversation", "last_file", "last_folder", "last_download",
            "last_app", "last_chat", "last_contact", "last_tool_result",
            "last_created_resource", "last_opened_resource", "last_verified_chat",
            "pending_operation", "pending_candidates", "last_user_goal",
        }
        return {key: snapshot[key] for key in allowed if key in snapshot}


_DEFAULT_CONTEXT = OperationalContext()
_ACTIVE_CONTEXT: ContextVar[OperationalContext] = ContextVar(
    "kira_operational_context", default=_DEFAULT_CONTEXT
)


@contextmanager
def use_context(context: OperationalContext):
    token = _ACTIVE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_CONTEXT.reset(token)


class _ContextProxy:
    """Compatibility facade for tools that need the active dispatcher context."""

    def __getattr__(self, name):
        return getattr(_ACTIVE_CONTEXT.get(), name)

    def __setattr__(self, name, value):
        setattr(_ACTIVE_CONTEXT.get(), name, value)


CONTEXT = _ContextProxy()


def active_context() -> OperationalContext:
    return _ACTIVE_CONTEXT.get()
