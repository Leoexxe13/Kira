from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
DEFAULT_DB_PATH = BASE_DIR / "memory" / "kira_memory.db"


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def is_fts5_available(conn: sqlite3.Connection | None = None) -> bool:
    """Check if the SQLite library has FTS5 module enabled."""
    should_close = False
    if conn is None:
        conn = sqlite3.connect(":memory:")
        should_close = True
    try:
        cur = conn.execute("SELECT 1 FROM pragma_compile_options WHERE compile_options = 'ENABLE_FTS5';")
        if cur.fetchone():
            return True
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _test_fts5 USING fts5(x);")
        conn.execute("DROP TABLE IF EXISTS _test_fts5;")
        return True
    except Exception:
        return False
    finally:
        if should_close:
            conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, key)
                );

                CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
                CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    language TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS whatsapp_contacts (
                    account TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    PRIMARY KEY (account, alias)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS memory_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    target_table TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_target ON memory_audit_log(target_table, record_id);
                """
            )

            # Setup FTS5 virtual tables and automatic synchronization triggers if supported
            if is_fts5_available(conn):
                conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                        category,
                        key,
                        value,
                        content=facts,
                        content_rowid=id
                    );

                    CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                        INSERT INTO facts_fts(rowid, category, key, value)
                        VALUES (new.id, new.category, new.key, new.value);
                    END;

                    CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                        INSERT INTO facts_fts(facts_fts, rowid, category, key, value)
                        VALUES ('delete', old.id, old.category, old.key, old.value);
                    END;

                    CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                        INSERT INTO facts_fts(facts_fts, rowid, category, key, value)
                        VALUES ('delete', old.id, old.category, old.key, old.value);
                        INSERT INTO facts_fts(rowid, category, key, value)
                        VALUES (new.id, new.category, new.key, new.value);
                    END;

                    CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                        summary,
                        date,
                        content=sessions,
                        content_rowid=id
                    );

                    CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
                        INSERT INTO sessions_fts(rowid, summary, date)
                        VALUES (new.id, new.summary, new.date);
                    END;

                    CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
                        INSERT INTO sessions_fts(sessions_fts, rowid, summary, date)
                        VALUES ('delete', old.id, old.summary, old.date);
                    END;

                    CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
                        INSERT INTO sessions_fts(sessions_fts, rowid, summary, date)
                        VALUES ('delete', old.id, old.summary, old.date);
                        INSERT INTO sessions_fts(rowid, summary, date)
                        VALUES (new.id, new.summary, new.date);
                    END;
                    """
                )
                # Rebuild FTS indices to index any pre-existing rows seamlessly
                try:
                    conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild');")
                    conn.execute("INSERT INTO sessions_fts(sessions_fts) VALUES('rebuild');")
                except Exception:
                    pass
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Facts API ─────────────────────────────────────────────────────────────────

def set_fact(
    category: str,
    key: str,
    value: str,
    updated_at: str | None = None,
    db_path: Path | str | None = None,
) -> bool:
    category = (category or "").strip()
    key = (key or "").strip()
    value = str(value) if value is not None else ""
    if not category or not key:
        return False

    now = updated_at or _now_iso()
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "SELECT id, value FROM facts WHERE category = ? AND key = ?",
                (category, key),
            )
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO facts (category, key, value, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (category, key, value, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("INSERT", "facts", f"{category}/{key}", None, value, now),
                )
                return True
            else:
                old_val = row["value"]
                if old_val != value:
                    conn.execute(
                        """
                        UPDATE facts SET value = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (value, now, row["id"]),
                    )
                    conn.execute(
                        """
                        INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        ("UPDATE", "facts", f"{category}/{key}", old_val, value, now),
                    )
                    return True
                return False
    finally:
        conn.close()


def get_fact(
    category: str, key: str, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT id, category, key, value, created_at, updated_at FROM facts WHERE category = ? AND key = ?",
            (category, key),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_fact(
    category: str, key: str, db_path: Path | str | None = None
) -> bool:
    now = _now_iso()
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "SELECT id, value FROM facts WHERE category = ? AND key = ?",
                (category, key),
            )
            row = cur.fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM facts WHERE id = ?",
                (row["id"],),
            )
            conn.execute(
                """
                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("DELETE", "facts", f"{category}/{key}", row["value"], None, now),
            )
            return True
    finally:
        conn.close()


def get_facts_by_category(
    category: str, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, category, key, value, created_at, updated_at
            FROM facts WHERE category = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (category,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_facts(
    db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, category, key, value, created_at, updated_at
            FROM facts
            ORDER BY updated_at DESC, id DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── Sessions API ──────────────────────────────────────────────────────────────

def add_session(
    summary: str,
    language: str = "",
    date_str: str | None = None,
    max_keep: int = 3,
    db_path: Path | str | None = None,
) -> int:
    summary = (summary or "").strip()
    if not summary:
        return 0
    now = _now_iso()
    d_str = date_str or datetime.now().strftime("%Y-%m-%d")

    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO sessions (date, summary, language, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (d_str, summary[:280], language or "", now),
            )
            session_id = cur.lastrowid or 0

            # Prune older sessions beyond max_keep
            if max_keep > 0:
                conn.execute(
                    """
                    DELETE FROM sessions
                    WHERE id NOT IN (
                        SELECT id FROM sessions ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (max_keep,),
                )
            return session_id
    finally:
        conn.close()


def pop_last_session(
    db_path: Path | str | None = None
) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "SELECT id, date, summary, language, created_at FROM sessions ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
            return dict(row)
    finally:
        conn.close()


def list_sessions(
    limit: int = 10, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT id, date, summary, language, created_at FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── WhatsApp Contacts API ─────────────────────────────────────────────────────

def get_whatsapp_contact(
    account: str, alias: str, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    if not account or not alias:
        return None
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT account, alias, chat_id AS id, name, confirmed_at
            FROM whatsapp_contacts
            WHERE account = ? AND alias = ?
            """,
            (account, alias),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_whatsapp_contact(
    account: str,
    alias: str,
    chat_id: str,
    name: str,
    confirmed_at: str | None = None,
    db_path: Path | str | None = None,
) -> None:
    if not all(isinstance(v, str) and v.strip() for v in (account, alias, chat_id, name)):
        raise ValueError("WhatsApp resolution requires account, alias, chat_id and name")
    now = confirmed_at or _now_iso()

    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "SELECT chat_id FROM whatsapp_contacts WHERE account = ? AND alias = ?",
                (account, alias),
            )
            prev = cur.fetchone()
            if prev and prev["chat_id"] != chat_id:
                raise ValueError("Forget the previous association before replacing it")

            conn.execute(
                """
                INSERT INTO whatsapp_contacts (account, alias, chat_id, name, confirmed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account, alias) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    name = excluded.name,
                    confirmed_at = excluded.confirmed_at
                """,
                (account, alias, chat_id, name, now),
            )
            conn.execute(
                """
                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "UPSERT",
                    "whatsapp_contacts",
                    f"{account}/{alias}",
                    prev["chat_id"] if prev else None,
                    f"{chat_id}|{name}",
                    now,
                ),
            )
    finally:
        conn.close()


def delete_whatsapp_contact(
    account: str, alias: str, db_path: Path | str | None = None
) -> bool:
    if not account or not alias:
        return False
    now = _now_iso()
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "SELECT chat_id, name FROM whatsapp_contacts WHERE account = ? AND alias = ?",
                (account, alias),
            )
            row = cur.fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM whatsapp_contacts WHERE account = ? AND alias = ?",
                (account, alias),
            )
            conn.execute(
                """
                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "DELETE",
                    "whatsapp_contacts",
                    f"{account}/{alias}",
                    f"{row['chat_id']}|{row['name']}",
                    None,
                    now,
                ),
            )
            return True
    finally:
        conn.close()


def get_all_whatsapp_contacts(
    account: str | None = None, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        if account:
            cur = conn.execute(
                """
                SELECT account, alias, chat_id AS id, name, confirmed_at
                FROM whatsapp_contacts
                WHERE account = ?
                ORDER BY alias ASC
                """,
                (account,),
            )
        else:
            cur = conn.execute(
                """
                SELECT account, alias, chat_id AS id, name, confirmed_at
                FROM whatsapp_contacts
                ORDER BY account ASC, alias ASC
                """
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── Tasks API ─────────────────────────────────────────────────────────────────

def add_task(
    text: str, created_at: str | None = None, db_path: Path | str | None = None
) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    now = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO tasks (text, done, created_at)
                VALUES (?, 0, ?)
                """,
                (text, now),
            )
            task_id = cur.lastrowid or 0
            conn.execute(
                """
                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("INSERT", "tasks", str(task_id), None, text, _now_iso()),
            )
            return task_id
    finally:
        conn.close()


def list_tasks(
    include_done: bool = True, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        if include_done:
            cur = conn.execute(
                "SELECT id, text, done, created_at, completed_at FROM tasks ORDER BY id ASC"
            )
        else:
            cur = conn.execute(
                "SELECT id, text, done, created_at, completed_at FROM tasks WHERE done = 0 ORDER BY id ASC"
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def complete_task(
    task_id: int, completed_at: str | None = None, db_path: Path | str | None = None
) -> bool:
    now = completed_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute("SELECT id, text, done FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE tasks SET done = 1, completed_at = ? WHERE id = ?",
                (now, task_id),
            )
            conn.execute(
                """
                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("UPDATE", "tasks", str(task_id), "done=0", f"done=1,completed={now}", _now_iso()),
            )
            return True
    finally:
        conn.close()


def delete_task(
    task_id: int, db_path: Path | str | None = None
) -> bool:
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute("SELECT id, text FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.execute(
                """
                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("DELETE", "tasks", str(task_id), row["text"], None, _now_iso()),
            )
            return True
    finally:
        conn.close()


# ── Audit Log API ─────────────────────────────────────────────────────────────

def get_audit_log(
    limit: int = 50, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, action, target_table, record_id, old_value, new_value, created_at
            FROM memory_audit_log
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── FTS5 Full-Text Search API ──────────────────────────────────────────────────

def sanitize_fts_query(query: str) -> str:
    """Extract alpha-numeric tokens and format for FTS5 prefix matching."""
    tokens = [re.sub(r"[^\w\u00C0-\u017F]+", "", w) for w in (query or "").split()]
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return ""
    # "tok1"* OR "tok2"*
    return " OR ".join(f'"{t}"*' for t in tokens)


def search_memory_fts(
    query: str, limit: int = 8, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """
    Search facts and sessions using FTS5 with BM25 ranking.
    Returns list of dicts: [{'source': 'fact'|'session', 'category': str, 'key': str, 'value': str, 'score': float}]
    Ordered by highest relevance first.
    """
    fts_query = sanitize_fts_query(query)
    if not fts_query:
        return []

    conn = get_connection(db_path)
    try:
        if not is_fts5_available(conn):
            return []

        # Make sure FTS tables exist
        cur_tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='facts_fts';")
        if not cur_tbl.fetchone():
            return []

        # Weights: key & value have higher priority than category.
        # FTS5 bm25(table, w0, w1, w2) calculates score (more negative = more relevant).
        # We negate it so higher score = better match.
        cur = conn.execute(
            """
            SELECT
                'fact' AS source,
                f.category AS category,
                f.key AS key,
                f.value AS value,
                (-bm25(facts_fts, 5.0, 10.0, 4.0)) AS score
            FROM facts_fts
            JOIN facts f ON f.id = facts_fts.rowid
            WHERE facts_fts MATCH ?
            UNION ALL
            SELECT
                'session' AS source,
                'sessions' AS category,
                s.date AS key,
                s.summary AS value,
                (-bm25(sessions_fts, 3.0, 1.0) * 0.75) AS score
            FROM sessions_fts
            JOIN sessions s ON s.id = sessions_fts.rowid
            WHERE sessions_fts MATCH ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (fts_query, fts_query, max(1, limit)),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[Memory] ⚠️ FTS search error: {e}")
        return []
    finally:
        conn.close()

