from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from memory import db


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def migrate_long_term(
    json_path: Path | str, db_path: Path | str | None = None
) -> dict[str, int]:
    """
    Migrates long_term.json data (facts and sessions) to SQLite.
    Idempotent: Running multiple times does not duplicate records.
    Does not modify or delete the source JSON.
    """
    path = Path(json_path)
    stats = {"facts": 0, "sessions": 0, "skipped": 0}
    if not path.exists():
        return stats

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return stats
    except Exception:
        return stats

    now = _now_iso()
    conn = db.get_connection(db_path)
    try:
        with conn:
            # 1. Facts from categories
            for cat, items in data.items():
                if cat == "sessions":
                    continue
                if not isinstance(items, dict):
                    continue

                for key, entry in items.items():
                    key_str = str(key).strip()
                    if not key_str:
                        continue

                    if isinstance(entry, dict) and "value" in entry:
                        val = str(entry.get("value", "") or "").strip()
                        updated = str(entry.get("updated", "") or "").strip() or now
                    elif isinstance(entry, dict):
                        # Complex item (e.g. monitor dict) without 'value' field
                        val = json.dumps(entry, ensure_ascii=False)
                        updated = str(entry.get("updated", "") or entry.get("added", "") or now)
                    else:
                        val = str(entry or "").strip()
                        updated = now

                    cur = conn.execute(
                        "SELECT id, value FROM facts WHERE category = ? AND key = ?",
                        (cat, key_str),
                    )
                    existing = cur.fetchone()

                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO facts (category, key, value, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (cat, key_str, val, updated, updated),
                        )
                        conn.execute(
                            """
                            INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            ("MIGRATE_INSERT", "facts", f"{cat}/{key_str}", None, val, now),
                        )
                        stats["facts"] += 1
                    else:
                        if existing["value"] != val:
                            conn.execute(
                                """
                                UPDATE facts SET value = ?, updated_at = ?
                                WHERE id = ?
                                """,
                                (val, updated, existing["id"]),
                            )
                            conn.execute(
                                """
                                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                ("MIGRATE_UPDATE", "facts", f"{cat}/{key_str}", existing["value"], val, now),
                            )
                            stats["facts"] += 1
                        else:
                            stats["skipped"] += 1

            # 2. Sessions list
            sessions = data.get("sessions", [])
            if isinstance(sessions, list):
                for s in sessions:
                    if not isinstance(s, dict):
                        continue
                    d_str = str(s.get("date", "") or "").strip() or datetime.now().strftime("%Y-%m-%d")
                    summary = str(s.get("summary", "") or "").strip()[:280]
                    if not summary:
                        continue
                    language = str(s.get("language", "") or "").strip()

                    cur = conn.execute(
                        "SELECT id FROM sessions WHERE date = ? AND summary = ?",
                        (d_str, summary),
                    )
                    if cur.fetchone() is None:
                        conn.execute(
                            """
                            INSERT INTO sessions (date, summary, language, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (d_str, summary, language, now),
                        )
                        stats["sessions"] += 1
                    else:
                        stats["skipped"] += 1
    finally:
        conn.close()

    return stats


def migrate_whatsapp_contacts(
    json_path: Path | str, db_path: Path | str | None = None
) -> dict[str, int]:
    """
    Migrates whatsapp_contacts.json to SQLite.
    Idempotent: Running multiple times updates identical records without duplication.
    Does not modify or delete the source JSON.
    """
    path = Path(json_path)
    stats = {"contacts": 0, "skipped": 0}
    if not path.exists():
        return stats

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return stats
    except Exception:
        return stats

    accounts = data.get("accounts", {})
    if not isinstance(accounts, dict):
        return stats

    now = _now_iso()
    conn = db.get_connection(db_path)
    try:
        with conn:
            for account, aliases in accounts.items():
                if not isinstance(account, str) or not isinstance(aliases, dict):
                    continue
                for alias, info in aliases.items():
                    if not isinstance(alias, str) or not isinstance(info, dict):
                        continue
                    chat_id = str(info.get("id", "") or "").strip()
                    name = str(info.get("name", "") or "").strip()
                    confirmed_at = str(info.get("confirmed_at", "") or "").strip() or now
                    if not chat_id or not name:
                        continue

                    cur = conn.execute(
                        "SELECT chat_id, name FROM whatsapp_contacts WHERE account = ? AND alias = ?",
                        (account, alias),
                    )
                    existing = cur.fetchone()

                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO whatsapp_contacts (account, alias, chat_id, name, confirmed_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (account, alias, chat_id, name, confirmed_at),
                        )
                        conn.execute(
                            """
                            INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            ("MIGRATE_INSERT", "whatsapp_contacts", f"{account}/{alias}", None, f"{chat_id}|{name}", now),
                        )
                        stats["contacts"] += 1
                    else:
                        if existing["chat_id"] != chat_id or existing["name"] != name:
                            conn.execute(
                                """
                                UPDATE whatsapp_contacts
                                SET chat_id = ?, name = ?, confirmed_at = ?
                                WHERE account = ? AND alias = ?
                                """,
                                (chat_id, name, confirmed_at, account, alias),
                            )
                            conn.execute(
                                """
                                INSERT INTO memory_audit_log (action, target_table, record_id, old_value, new_value, created_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                ("MIGRATE_UPDATE", "whatsapp_contacts", f"{account}/{alias}", f"{existing['chat_id']}|{existing['name']}", f"{chat_id}|{name}", now),
                            )
                            stats["contacts"] += 1
                        else:
                            stats["skipped"] += 1
    finally:
        conn.close()

    return stats


def migrate_tasks(
    json_path: Path | str, db_path: Path | str | None = None
) -> dict[str, int]:
    """
    Migrates kira_tasks.json to SQLite.
    Idempotent: Running multiple times does not duplicate tasks.
    Does not modify or delete the source JSON.
    """
    path = Path(json_path)
    stats = {"tasks": 0, "skipped": 0}
    if not path.exists():
        return stats

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return stats
    except Exception:
        return stats

    now = _now_iso()
    conn = db.get_connection(db_path)
    try:
        with conn:
            for item in data:
                if not isinstance(item, dict):
                    continue
                task_id = item.get("id")
                text = str(item.get("text", "") or "").strip()
                if not text:
                    continue
                done = 1 if item.get("done") else 0
                created = str(item.get("created", "") or "").strip() or now
                completed = str(item.get("completed", "") or "").strip() or None

                if task_id is not None:
                    cur = conn.execute("SELECT id, text, done FROM tasks WHERE id = ?", (int(task_id),))
                    existing = cur.fetchone()
                else:
                    cur = conn.execute("SELECT id, text, done FROM tasks WHERE text = ? AND created_at = ?", (text, created))
                    existing = cur.fetchone()

                if existing is None:
                    if task_id is not None:
                        conn.execute(
                            """
                            INSERT INTO tasks (id, text, done, created_at, completed_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (int(task_id), text, done, created, completed),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO tasks (text, done, created_at, completed_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (text, done, created, completed),
                        )
                    stats["tasks"] += 1
                else:
                    if existing["text"] != text or existing["done"] != done:
                        conn.execute(
                            """
                            UPDATE tasks SET text = ?, done = ?, completed_at = ?
                            WHERE id = ?
                            """,
                            (text, done, completed, existing["id"]),
                        )
                        stats["tasks"] += 1
                    else:
                        stats["skipped"] += 1
    finally:
        conn.close()

    return stats


def migrate_all(
    memory_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Coordinates migration of long_term.json, whatsapp_contacts.json, and kira_tasks.json
    into the target SQLite database.
    """
    base = Path(memory_dir) if memory_dir else (db.BASE_DIR / "memory")
    db_target = db_path or (base / "kira_memory.db")

    db.init_db(db_target)

    lt_stats = migrate_long_term(base / "long_term.json", db_target)
    wa_stats = migrate_whatsapp_contacts(base / "whatsapp_contacts.json", db_target)
    tk_stats = migrate_tasks(base / "kira_tasks.json", db_target)

    return {
        "long_term": lt_stats,
        "whatsapp_contacts": wa_stats,
        "tasks": tk_stats,
    }
