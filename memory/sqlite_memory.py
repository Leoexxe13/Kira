"""Small local SQLite memory store scoped to a trusted principal.

Conversation history and operational context are separate concerns. This store
contains only explicit personal facts and deliberately refuses recognizable
credentials. The model never supplies user_id; the service binds it from the
trusted principal passed to ``MemoryStore``.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Iterator


DEFAULT_PATH = Path(__file__).resolve().parent / "kira_memory.db"
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:password|contraseña|api[_ -]?key|access[_ -]?token|secret|credential|clave)\s*[:=]\s*\S+"
    r"|\b(?:sk-|gsk_|ghp_|xoxb-|AIza)[A-Za-z0-9_\-]{12,}"
)
_SECRET_LABELS = {"password", "contraseña", "api_key", "apikey", "token", "secret", "credential", "clave"}
_CATEGORIES = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
_ACTIVE_MEMORY: ContextVar["MemoryStore | None"] = ContextVar("kira_active_memory", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    principal TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, category, topic)
);
CREATE INDEX IF NOT EXISTS memories_user_active_idx
    ON memories(user_id, active, updated_at DESC);
CREATE TABLE IF NOT EXISTS session_state (
    user_id TEXT NOT NULL REFERENCES users(id),
    session_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, session_id)
);
"""


@dataclass(frozen=True)
class Memory:
    id: str
    user_id: str
    category: str
    topic: str
    content: str
    confidence: float
    source: str
    created_at: float
    updated_at: float
    active: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "category": self.category,
            "topic": self.topic,
            "content": self.content,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "active": self.active,
        }


class MemoryStore:
    """SQLite-backed facts for one trusted principal."""

    def __init__(self, path: str | Path = DEFAULT_PATH, *, principal: str):
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("A trusted principal is required")
        self.path = Path(path).expanduser()
        self.principal = principal.strip()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._user_id = self._ensure_user()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.touch(mode=0o600, exist_ok=True)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(SCHEMA)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _ensure_user(self) -> str:
        with self._db() as db:
            row = db.execute("SELECT id FROM users WHERE principal=?", (self.principal,)).fetchone()
            if row:
                return str(row[0])
            user_id = hashlib.sha256(self.principal.encode("utf-8")).hexdigest()[:32]
            db.execute(
                "INSERT INTO users(id, principal, created_at) VALUES (?, ?, ?)",
                (user_id, self.principal, time.time()),
            )
            return user_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @staticmethod
    def _validate_text(name: str, value: str, *, max_length: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        value = value.strip()
        if len(value) > max_length:
            raise ValueError(f"{name} is too long")
        return value

    @classmethod
    def _validate_memory(cls, category: str, topic: str, content: str, source: str, confidence: float):
        category = cls._validate_text("category", category, max_length=40).lower()
        if category not in _CATEGORIES:
            raise ValueError("Unsupported memory category")
        topic = cls._validate_text("topic", topic, max_length=160)
        content = cls._validate_text("content", content, max_length=2000)
        source = cls._validate_text("source", source, max_length=120)
        if any(label in topic.casefold().replace("-", "_") for label in _SECRET_LABELS):
            raise ValueError("Credentials are not stored in memory")
        if _CREDENTIAL_RE.search(content) or _CREDENTIAL_RE.search(topic):
            raise ValueError("Credentials are not stored in memory")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            raise ValueError("confidence must be numeric") from None
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return category, topic, content, source, confidence

    def remember(self, category: str, topic: str, content: str, *, source: str = "user_explicit", confidence: float = 1.0) -> Memory:
        category, topic, content, source, confidence = self._validate_memory(category, topic, content, source, confidence)
        now = time.time()
        memory_id = secrets.token_hex(16)
        with self._db() as db:
            db.execute(
                """INSERT INTO memories(id,user_id,category,topic,content,confidence,source,created_at,updated_at,active)
                   VALUES(?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(user_id,category,topic) DO UPDATE SET
                     content=excluded.content, confidence=excluded.confidence,
                     source=excluded.source, updated_at=excluded.updated_at, active=1""",
                (memory_id, self.user_id, category, topic, content, confidence, source, now, now),
            )
            row = db.execute(
                "SELECT * FROM memories WHERE user_id=? AND category=? AND topic=?",
                (self.user_id, category, topic),
            ).fetchone()
        return self._row(row)

    def recall(self, query: str = "", *, limit: int = 8) -> list[Memory]:
        query = str(query or "").strip().casefold()
        limit = max(1, min(int(limit), 50))
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM memories WHERE user_id=? AND active=1 ORDER BY updated_at DESC",
                (self.user_id,),
            ).fetchall()
        if not query:
            return [self._row(row) for row in rows[:limit]]
        words = [word for word in re.split(r"[^\w]+", query) if len(word) > 1]
        ranked: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            haystack = f"{row['category']} {row['topic']} {row['content']}".casefold()
            score = sum((5 if word in str(row['topic']).casefold() else 0) + (2 if word in haystack else 0) for word in words)
            if score > 0:
                ranked.append((score, row))
        ranked.sort(key=lambda item: (-item[0], -float(item[1]["updated_at"])))
        return [self._row(row) for _, row in ranked[:limit]]

    def forget(self, category: str, topic: str) -> bool:
        category = self._validate_text("category", category, max_length=40).lower()
        topic = self._validate_text("topic", topic, max_length=160)
        with self._db() as db:
            result = db.execute(
                "UPDATE memories SET active=0, updated_at=? WHERE user_id=? AND category=? AND topic=? AND active=1",
                (time.time(), self.user_id, category, topic),
            )
        return result.rowcount > 0

    def context(self, query: str = "", *, limit: int = 8, max_chars: int = 2400) -> list[dict]:
        selected: list[dict] = []
        used = 0
        for memory in self.recall(query, limit=limit):
            item = {
                "category": memory.category,
                "topic": memory.topic,
                "content": memory.content,
                "confidence": memory.confidence,
                "source": memory.source,
            }
            cost = len(str(item))
            if used + cost > max_chars:
                continue
            selected.append(item)
            used += cost
        return selected

    def save_state(self, session_id: str, state: dict) -> None:
        """Persist bounded operational state, never credentials or full history."""
        session_id = self._validate_text("session_id", session_id, max_length=120)
        allowed = {
            "last_file", "last_folder", "last_download", "last_app", "last_chat",
            "last_contact", "last_tool_result", "last_created_resource",
            "last_opened_resource", "last_verified_chat", "pending_operation",
            "pending_candidates", "last_user_goal",
        }
        filtered = {key: state[key] for key in allowed if key in state}
        payload = json.dumps(self._redact_state(filtered), ensure_ascii=False, default=str)
        with self._db() as db:
            db.execute(
                """INSERT INTO session_state(user_id,session_id,state,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(user_id,session_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at""",
                (self.user_id, session_id, payload, time.time()),
            )

    def load_state(self, session_id: str) -> dict:
        session_id = self._validate_text("session_id", session_id, max_length=120)
        with self._db() as db:
            row = db.execute(
                "SELECT state FROM session_state WHERE user_id=? AND session_id=?",
                (self.user_id, session_id),
            ).fetchone()
        if not row:
            return {}
        try:
            value = json.loads(row[0])
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    @classmethod
    def _redact_state(cls, value):
        if isinstance(value, str):
            return _CREDENTIAL_RE.sub("[credencial omitida]", value)
        if isinstance(value, list):
            return [cls._redact_state(item) for item in value[:50]]
        if isinstance(value, dict):
            return {str(key): cls._redact_state(item) for key, item in list(value.items())[:80]}
        return value

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Memory:
        if row is None:
            raise ValueError("Memory row not found")
        return Memory(
            id=str(row["id"]), user_id=str(row["user_id"]), category=str(row["category"]),
            topic=str(row["topic"]), content=str(row["content"]), confidence=float(row["confidence"]),
            source=str(row["source"]), created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]), active=bool(row["active"]),
        )


@contextmanager
def use_memory(memory: MemoryStore):
    """Bind a trusted memory owner for one dispatch scope."""
    token = _ACTIVE_MEMORY.set(memory)
    try:
        yield memory
    finally:
        _ACTIVE_MEMORY.reset(token)


def active_memory() -> MemoryStore:
    memory = _ACTIVE_MEMORY.get()
    if memory is None:
        raise RuntimeError("No trusted memory owner is bound to this request")
    return memory
