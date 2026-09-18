"""Durable request journal and idempotency ledger for KIRA."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_jobs (
    request_id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    idempotency_key TEXT,
    state TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(principal, idempotency_key)
);
CREATE INDEX IF NOT EXISTS runtime_jobs_state_idx ON runtime_jobs(principal,state,updated_at);
CREATE TABLE IF NOT EXISTS runtime_events (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(request_id, sequence),
    FOREIGN KEY(request_id) REFERENCES runtime_jobs(request_id)
);
CREATE TABLE IF NOT EXISTS runtime_operations (
    operation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    state TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(request_id) REFERENCES runtime_jobs(request_id)
);
"""

_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


@dataclass(frozen=True)
class RequestEnvelope:
    request_id: str
    principal: str
    session_id: str
    source: str
    user_text: str
    created_at: float
    deadline_at: float
    idempotency_key: str = ""

    @classmethod
    def create(cls, *, principal: str, session_id: str, source: str, user_text: str,
               request_id: str | None = None, idempotency_key: str = "",
               timeout_seconds: float = 60.0) -> "RequestEnvelope":
        now = time.time()
        return cls(
            request_id=request_id or secrets.token_hex(16),
            principal=principal,
            session_id=session_id,
            source=source,
            user_text=str(user_text),
            created_at=now,
            deadline_at=now + max(1.0, float(timeout_seconds)),
            idempotency_key=str(idempotency_key or ""),
        )


class RuntimeJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
        self.reconcile_interrupted()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _safe_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)[:65536]

    @staticmethod
    def operation_id(request_id: str, step_index: int, item_index: int, tool: str, arguments: dict) -> str:
        stable = json.dumps(
            {"request_id": request_id, "step": step_index, "item": item_index,
             "tool": tool, "arguments": arguments},
            ensure_ascii=False, sort_keys=True, default=str,
        )
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def begin(self, envelope: RequestEnvelope) -> tuple[bool, dict | None]:
        digest = hashlib.sha256(envelope.user_text.encode("utf-8")).hexdigest()
        now = time.time()
        with self._db() as db:
            if envelope.idempotency_key:
                row = db.execute(
                    "SELECT * FROM runtime_jobs WHERE principal=? AND idempotency_key=?",
                    (envelope.principal, envelope.idempotency_key),
                ).fetchone()
                if row:
                    cached = json.loads(row["result_json"]) if row["result_json"] else None
                    return False, cached
            row = db.execute("SELECT * FROM runtime_jobs WHERE request_id=?", (envelope.request_id,)).fetchone()
            if row:
                cached = json.loads(row["result_json"]) if row["result_json"] else None
                return False, cached
            db.execute(
                """INSERT INTO runtime_jobs(request_id,principal,session_id,source,input_hash,
                   idempotency_key,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (envelope.request_id, envelope.principal, envelope.session_id, envelope.source,
                 digest, envelope.idempotency_key or None, "running", now, now),
            )
        self.event(envelope.request_id, "accepted", {"source": envelope.source})
        return True, None

    def event(self, request_id: str, event_type: str, payload: Any) -> str:
        with self._db() as db:
            seq = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM runtime_events WHERE request_id=?",
                (request_id,),
            ).fetchone()[0])
            event_id = secrets.token_hex(16)
            db.execute(
                "INSERT INTO runtime_events(event_id,request_id,sequence,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (event_id, request_id, seq, event_type, self._safe_json(payload), time.time()),
            )
        return event_id

    def finish(self, request_id: str, state: str, result: Any = None, error_code: str = "") -> None:
        if state not in _TERMINAL | {"needs_approval", "pending"}:
            raise ValueError(f"invalid job state: {state}")
        with self._db() as db:
            db.execute(
                "UPDATE runtime_jobs SET state=?,result_json=?,error_code=?,updated_at=? WHERE request_id=?",
                (state, self._safe_json(result) if result is not None else None,
                 str(error_code)[:120], time.time(), request_id),
            )
        self.event(request_id, state, {"error_code": str(error_code)[:120]})

    def reserve_operation(self, operation_id: str, request_id: str, tool: str) -> tuple[bool, dict | None]:
        now = time.time()
        with self._db() as db:
            row = db.execute("SELECT state,result_json FROM runtime_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row:
                cached = json.loads(row["result_json"]) if row["result_json"] else None
                if row["state"] == "succeeded":
                    return False, cached
                db.execute(
                    "UPDATE runtime_operations SET state='running',error_code='',updated_at=? WHERE operation_id=?",
                    (now, operation_id),
                )
                return True, None
            db.execute(
                "INSERT INTO runtime_operations(operation_id,request_id,tool,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (operation_id, request_id, tool, "running", now, now),
            )
        return True, None

    def finish_operation(self, operation_id: str, *, succeeded: bool, result: Any, error_code: str = "") -> None:
        with self._db() as db:
            db.execute(
                "UPDATE runtime_operations SET state=?,result_json=?,error_code=?,updated_at=? WHERE operation_id=?",
                ("succeeded" if succeeded else "failed", self._safe_json(result),
                 str(error_code)[:120], time.time(), operation_id),
            )

    def reconcile_interrupted(self) -> int:
        with self._db() as db:
            cur = db.execute(
                "UPDATE runtime_jobs SET state='failed',error_code='process_interrupted',updated_at=? WHERE state='running'",
                (time.time(),),
            )
            db.execute(
                "UPDATE runtime_operations SET state='failed',error_code='process_interrupted',updated_at=? WHERE state='running'",
                (time.time(),),
            )
            return int(cur.rowcount)

    def job(self, request_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM runtime_jobs WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def events(self, request_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT event_id,sequence,event_type,payload_json,created_at FROM runtime_events WHERE request_id=? ORDER BY sequence",
                (request_id,),
            ).fetchall()
        return [dict(row) for row in rows]
