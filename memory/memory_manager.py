import json
import re
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys

from memory import db


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 380

MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
PROMPT_MAX_PER_CATEGORY = 6


def _get_db_path() -> Path:
    if MEMORY_PATH != BASE_DIR / "memory" / "long_term.json":
        return MEMORY_PATH.parent / "kira_memory.db"
    return BASE_DIR / "memory" / "kira_memory.db"


def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }


def load_memory() -> dict:
    """Load memory facts and sessions from SQLite backend."""
    base = _empty_memory()
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        facts = db.get_all_facts(db_path=db_path)
        for r in facts:
            cat = r["category"]
            key = r["key"]
            val = r["value"]
            upd = r["updated_at"]
            if cat not in base:
                base[cat] = {}
            base[cat][key] = {"value": val, "updated": upd}

        # Sessions in FIFO order (oldest to newest)
        sessions = []
        for s in reversed(db.list_sessions(limit=_SESSION_MAX, db_path=db_path)):
            entry = {"date": s["date"], "summary": s["summary"]}
            if s.get("language"):
                entry["language"] = s["language"]
            sessions.append(entry)
        base["sessions"] = sessions
        return base
    except Exception as e:
        print(f"[Memory] ⚠️ SQLite load error: {e}")
        return _empty_memory()


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def save_memory(memory: dict) -> None:
    """Persist facts to SQLite."""
    if not isinstance(memory, dict):
        return
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        now_str = datetime.now().strftime("%Y-%m-%d")
        for cat, items in memory.items():
            if cat == "sessions":
                continue
            if not isinstance(items, dict):
                continue
            for key, entry in items.items():
                val = _entry_value(entry)
                upd = (entry.get("updated") if isinstance(entry, dict) else None) or now_str
                db.set_fact(cat, key, val, updated_at=upd, db_path=db_path)

        # In testing environments where MEMORY_PATH is mocked to a temp path, sync test file
        if MEMORY_PATH != BASE_DIR / "memory" / "long_term.json":
            with _lock:
                MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                MEMORY_PATH.write_text(
                    json.dumps(memory, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
    except Exception as e:
        print(f"[Memory] ⚠️ SQLite save error: {e}")


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def update_memory(memory_update: dict) -> dict:
    """Update facts in SQLite backend."""
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        now_str = datetime.now().strftime("%Y-%m-%d")
        changed_keys = []
        for cat, items in memory_update.items():
            if cat == "sessions":
                continue
            if not isinstance(items, dict):
                continue
            for key, value in items.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                new_val = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
                upd = (value.get("updated") if isinstance(value, dict) else None) or now_str
                if db.set_fact(cat, key, new_val, updated_at=upd, db_path=db_path):
                    changed_keys.append(f"{cat}/{key}")

        if changed_keys:
            print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")

        if MEMORY_PATH != BASE_DIR / "memory" / "long_term.json":
            mem = load_memory()
            with _lock:
                MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                MEMORY_PATH.write_text(
                    json.dumps(mem, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
    except Exception as e:
        print(f"[Memory] ⚠️ SQLite update error: {e}")

    return load_memory()


def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def _search_memory_fallback(query: str, limit: int = 8) -> str:
    """Original lexical scoring fallback for empty queries or environments without FTS5."""
    memory = load_memory()
    words  = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    if not rows:
        return (f"Nothing stored about '{query}'." if query
                else "I have not stored anything about this person yet.")

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    return head + "\n" + "\n".join(lines) + more


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts and relevant sessions matching `query`. Backs recall_memory."""
    q = (query or "").strip()
    if not q:
        return _search_memory_fallback("", limit=limit)

    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        fts_results = db.search_memory_fts(q, limit=max(1, limit), db_path=db_path)
        if fts_results:
            lines = []
            seen = set()
            for r in fts_results:
                identifier = (r["category"], r["key"], r["value"])
                if identifier in seen:
                    continue
                seen.add(identifier)
                if r.get("source") == "session":
                    lines.append(f"session/{r['key']}: {r['value']}")
                else:
                    lines.append(f"{r['category']}/{_pretty(r['key'])}: {r['value']}")
                if len(lines) >= limit:
                    break

            head = f"Stored facts matching '{q}':"
            return head + "\n" + "\n".join(lines)
    except Exception as e:
        print(f"[Memory] ⚠️ FTS search_memory fallback triggered: {e}")

    # Fallback to lexical substring scoring if FTS5 yields no results or is unavailable
    return _search_memory_fallback(q, limit=limit)



def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel."""
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        facts = db.get_all_facts(db_path=db_path)
        rows = []
        for r in facts:
            rows.append({
                "category": r["category"],
                "key":      r["key"],
                "value":    r["value"],
                "updated":  r["updated_at"] or "",
            })
        rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
        return rows
    except Exception as e:
        print(f"[Memory] ⚠️ all_entries_for_ui error: {e}")
        return []


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        if db.delete_fact(category, key, db_path=db_path):
            if MEMORY_PATH != BASE_DIR / "memory" / "long_term.json":
                mem = load_memory()
                with _lock:
                    MEMORY_PATH.write_text(
                        json.dumps(mem, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
            return f"Forgotten: {category}/{key}"
        return f"Not found: {category}/{key}"
    except Exception as e:
        return f"Error: {e}"


forget_memory = forget


# ── Session memory ─────────────────────────────────────────────────────────────

_SESSION_MAX = 3


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a session summary to SQLite."""
    summary = (summary or "").strip()
    if not summary:
        return
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        db.add_session(summary=summary, language=language, max_keep=_SESSION_MAX, db_path=db_path)
        d_str = datetime.now().strftime("%Y-%m-%d")
        print(f"[Memory] 📝 Session saved ({d_str}): {summary[:60]}…")
    except Exception as e:
        print(f"[Memory] ⚠️ save_session_summary error: {e}")


def pop_last_session() -> dict | None:
    """Return AND remove the most recent session entry from SQLite."""
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        row = db.pop_last_session(db_path=db_path)
        if not row:
            return None
        entry = {
            "date": row["date"],
            "summary": row["summary"],
        }
        if row.get("language"):
            entry["language"] = row["language"]
        return entry
    except Exception as e:
        print(f"[Memory] ⚠️ pop_last_session error: {e}")
        return None


# Technical resolution memory for WhatsApp (preserved intact)
WHATSAPP_CONTACTS_PATH = BASE_DIR / "memory" / "whatsapp_contacts.json"


def _read_whatsapp_contacts() -> dict:
    if not WHATSAPP_CONTACTS_PATH.exists():
        return {"version": 1, "accounts": {}}
    data = json.loads(WHATSAPP_CONTACTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
        raise ValueError("Invalid WhatsApp resolution memory")
    for account, aliases in data["accounts"].items():
        if not isinstance(account, str) or not account or not isinstance(aliases, dict):
            raise ValueError("Invalid WhatsApp account memory")
        for alias, entry in aliases.items():
            if (not isinstance(alias, str) or not alias or not isinstance(entry, dict)
                    or any(not isinstance(entry.get(k), str) or not entry[k]
                           for k in ("id", "name", "confirmed_at"))):
                raise ValueError("Invalid WhatsApp contact memory")
    return data


def _write_whatsapp_contacts(data: dict) -> None:
    import os
    import tempfile
    WHATSAPP_CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".whatsapp_contacts.", suffix=".tmp",
                                     dir=WHATSAPP_CONTACTS_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, WHATSAPP_CONTACTS_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_whatsapp_contact(account: str, alias: str) -> dict | None:
    if not account or not alias:
        return None
    with _lock:
        entry = _read_whatsapp_contacts()["accounts"].get(account, {}).get(alias)
        return dict(entry) if entry else None


def remember_whatsapp_contact(account: str, alias: str, chat_id: str, name: str) -> None:
    """Called only after an explicit candidate choice and exact-ID verification."""
    if not all(isinstance(v, str) and v.strip() for v in (account, alias, chat_id, name)):
        raise ValueError("WhatsApp resolution requires account, alias and identity")
    with _lock:
        data = _read_whatsapp_contacts()
        aliases = data["accounts"].setdefault(account, {})
        previous = aliases.get(alias)
        if previous and previous["id"] != chat_id:
            raise ValueError("Forget the previous association before replacing it")
        aliases[alias] = {"id": chat_id, "name": name,
                          "confirmed_at": datetime.now().isoformat(timespec="seconds")}
        _write_whatsapp_contacts(data)


def forget_whatsapp_contact(account: str, alias: str) -> bool:
    if not account or not alias:
        raise ValueError("WhatsApp account and alias required")
    with _lock:
        data = _read_whatsapp_contacts()
        aliases = data["accounts"].get(account, {})
        if alias not in aliases:
            return False
        del aliases[alias]
        if not aliases:
            data["accounts"].pop(account, None)
        _write_whatsapp_contacts(data)
        return True
