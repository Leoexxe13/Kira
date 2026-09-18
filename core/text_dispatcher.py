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
from core.runtime_journal import RequestEnvelope, RuntimeJournal
from memory.sqlite_memory import MemoryStore, use_memory


class TextDispatcher:
    """Safe, reusable text entry point for KIRA."""

    def __init__(self, *, base_dir: str | Path | None = None, registry=None,
                 provider=None, memory_path: str | Path | None = None,
                 principal: str | None = None, memory=None, context=None,
                 session_id: str = "desktop", logger: Callable[[str], None] | None = None,
                 journal=None):
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
        self.journal = journal or RuntimeJournal(self.base_dir / "memory" / "kira_runtime.db")
        try:
            self.context.restore(self.memory.load_state(self.session_id))
        except Exception as exc:
            self.logger(f"context restore unavailable: {type(exc).__name__}")
        self.core = ReasoningCore(
            registry=self.registry,
            provider=self.provider,
            context=self.context,
            memory=self.memory,
            journal=self.journal,
            logger=self.logger,
        )

    def dispatch(self, text: str, *, source: str = "chat", dry_run: bool = False,
                 request_id: str | None = None, idempotency_key: str = "") -> DispatchResult:
        envelope = RequestEnvelope.create(
            principal=self.memory.principal,
            session_id=self.session_id,
            source=source,
            user_text=text,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        accepted, cached = self.journal.begin(envelope)
        if not accepted and isinstance(cached, dict):
            return DispatchResult(
                state=str(cached.get("state", "failed")),
                text=str(cached.get("text", "")),
                goal=str(cached.get("goal", "")),
                plan=cached.get("plan") if isinstance(cached.get("plan"), dict) else {},
                needs_clarification=bool(cached.get("needs_clarification", False)),
            )
        if not accepted:
            return DispatchResult(
                state="pending",
                text="Esta solicitud ya está en curso; no la ejecutaré dos veces.",
                goal=str(text),
                needs_clarification=True,
            )
        with use_memory(self.memory):
            result = self.core.dispatch(
                text,
                source=source,
                dry_run=dry_run,
                request_id=envelope.request_id,
            )
        if not dry_run:
            try:
                self.memory.save_state(self.session_id, self.context.snapshot())
            except Exception as exc:
                self.logger(f"context persistence unavailable: {type(exc).__name__}")
        payload = {
            "state": result.state, "text": result.text, "goal": result.goal,
            "plan": result.plan, "results": [item.as_dict() for item in result.results],
            "needs_clarification": result.needs_clarification,
        }
        job_state = {
            "executed": "succeeded", "verified": "succeeded", "planned": "succeeded",
            "cancelled": "cancelled", "pending": "needs_approval",
        }.get(result.state, "failed")
        self.journal.finish(envelope.request_id, job_state, payload,
                            "" if result.ok else result.state)
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
            "journal_path": str(self.journal.path),
        }
