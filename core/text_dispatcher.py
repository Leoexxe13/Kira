"""Single text boundary for KIRA's desktop and manual test harness.

This adapter binds trusted memory and operational context around one
ReasoningCore instance. It does not own audio, Qt, WhatsApp, or Live session
state; those remain outside this stage.
"""
from __future__ import annotations

import getpass
from pathlib import Path
from typing import Callable

from core.action_loader import discover_actions
from core.operational_context import OperationalContext
from core.provider_manager import ProviderManager
from core.reasoning_core import DispatchResult, ReasoningCore
from memory.sqlite_memory import MemoryStore, use_memory


class TextDispatcher:
    """Safe, reusable text entry point for KIRA."""

    def __init__(self, *, base_dir: str | Path | None = None, registry=None,
                 provider=None, memory_path: str | Path | None = None,
                 principal: str | None = None, memory=None, context=None,
                 session_id: str = "desktop", logger: Callable[[str], None] | None = None):
        self.base_dir = Path(base_dir or Path(__file__).resolve().parents[1]).resolve()
        self.logger = logger or (lambda _message: None)
        if registry is None:
            registry = discover_actions(self.base_dir / "actions", logger=self.logger)
        self.registry = registry
        self.provider = provider or ProviderManager()
        self.memory = memory or MemoryStore(
            memory_path or self.base_dir / "memory" / "kira_memory.db",
            principal=principal or f"local-os:{getpass.getuser()}",
        )
        self.context = context or OperationalContext()
        self.session_id = session_id
        try:
            self.context.restore(self.memory.load_state(self.session_id))
        except Exception as exc:
            self.logger(f"context restore unavailable: {type(exc).__name__}")
        self.core = ReasoningCore(
            registry=self.registry,
            provider=self.provider,
            context=self.context,
            memory=self.memory,
            logger=self.logger,
        )

    def dispatch(self, text: str, *, source: str = "chat", dry_run: bool = False) -> DispatchResult:
        with use_memory(self.memory):
            result = self.core.dispatch(text, source=source, dry_run=dry_run)
        if not dry_run:
            try:
                self.memory.save_state(self.session_id, self.context.snapshot())
            except Exception as exc:
                self.logger(f"context persistence unavailable: {type(exc).__name__}")
        return result

    def observe_turn(self, text: str, *, source: str = "voice-live") -> None:
        """Record a turn already handled by Gemini Live without executing it twice."""
        text = str(text or "").strip()
        if not text:
            return
        self.context.turn("user", text)
        self.context.update(last_user_goal=text)
        try:
            self.memory.save_state(self.session_id, self.context.snapshot())
        except Exception as exc:
            self.logger(f"voice context persistence unavailable: {type(exc).__name__}")

    def state(self) -> dict:
        return {
            "principal": self.memory.principal,
            "user_id": self.memory.user_id,
            "memory_path": str(self.memory.path),
            "context": self.context.snapshot(),
            "providers": self.provider.provider_health() if hasattr(self.provider, "provider_health") else {},
        }
