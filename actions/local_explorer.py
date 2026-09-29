"""
local_explorer.py — Structured local filesystem exploration tool for KIRA.

Returns structured JSON-serializable dicts so that Gemini can reference and
chain results without requiring any phrase-based routing. Results are
automatically absorbed by the operational context system.

Strictly read-only: no deletion, no movement, no writes.
"""
from __future__ import annotations

import json
import os
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_OS = platform.system()

# ── Safety limits ─────────────────────────────────────────────────────────────
_MAX_RESULTS   = 200   # absolute ceiling on result rows
_MAX_DIRS_WALK = 2000  # directories walked per call to keep latency sane
_TIMEOUT_SECS  = 10    # wall-clock limit; graceful truncation after this

# Directories that are almost never useful in a generic search and would make
# a recursive walk extremely slow.
_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv", ".env",
    "Library", "System", "proc", "sys", "dev",
    ".Trash", ".Spotlight-V100", ".fseventsd", ".TemporaryItems",
}

_HOME = Path.home()

# ── Path resolution (mirrors file_controller to avoid duplication) ─────────────

def _resolve(raw: str) -> Path:
    """Resolve shortcut names and ~ to absolute paths."""
    raw = str(raw or "").strip()
    shortcuts = {
        "desktop":    _home_dir("Desktop"),
        "escritorio": _home_dir("Desktop"),
        "downloads":  _home_dir("Downloads"),
        "descargas":  _home_dir("Downloads"),
        "documents":  _home_dir("Documents"),
        "documentos": _home_dir("Documents"),
        "pictures":   _home_dir("Pictures"),
        "imagenes":   _home_dir("Pictures"),
        "imágenes":   _home_dir("Pictures"),
        "music":      _home_dir("Music"),
        "música":     _home_dir("Music"),
        "musica":     _home_dir("Music"),
        "videos":     _home_dir("Videos"),
        "home":       _HOME,
        "inicio":     _HOME,
        "~":          _HOME,
    }
    if not raw:
        return _HOME
    low = raw.lower()
    if low in shortcuts:
        return shortcuts[low]
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    parts = Path(raw).parts
    if parts and str(parts[0]).lower() in shortcuts:
        base = shortcuts[str(parts[0]).lower()]
        return base.joinpath(*parts[1:]) if len(parts) > 1 else base
    return _HOME / raw


def _home_dir(name: str) -> Path:
    """Return a well-known user directory, using XDG vars on Linux."""
    if _OS == "Linux":
        env_key = f"XDG_{name.upper()}_DIR"
        xdg = os.environ.get(env_key, "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return _HOME / name


def _is_safe(p: Path) -> bool:
    """Only allow paths inside the user's home directory."""
    try:
        resolved = p.resolve()
        return resolved == _HOME.resolve() or resolved.is_relative_to(_HOME.resolve())
    except Exception:
        return False


def _norm_ext(ext: str) -> str:
    """Normalise extension: ensure leading dot, lowercase."""
    ext = (ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return ext


def _file_record(p: Path) -> dict:
    """Build a structured record for a single filesystem entry."""
    try:
        stat = p.stat()
        return {
            "name":        p.name,
            "path":        str(p),
            "kind":        "folder" if p.is_dir() else "file",
            "extension":   p.suffix.lower() if p.is_file() else "",
            "size_bytes":  stat.st_size if p.is_file() else None,
            "size_human":  _fmt_size(stat.st_size) if p.is_file() else None,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "modified_ts": stat.st_mtime,
        }
    except PermissionError:
        return {"name": p.name, "path": str(p), "kind": "unknown", "error": "permission_denied"}
    except Exception as e:
        return {"name": p.name, "path": str(p), "kind": "unknown", "error": str(e)}


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _err(code: str, detail: str) -> dict:
    return {"error": code, "detail": detail, "results": [], "count": 0}


# ── Core operations ────────────────────────────────────────────────────────────

def op_list(path: str, show_hidden: bool = False, sort_by: str = "name",
            limit: int = 100, folders_only: bool = False,
            files_only: bool = False) -> dict:
    """List contents of a single directory. Non-recursive."""
    target = _resolve(path)
    if not _is_safe(target):
        return _err("access_denied", str(target))
    if not target.exists():
        return _err("path_not_found", str(target))
    if not target.is_dir():
        return _err("not_a_directory", str(target))

    items = []
    try:
        for entry in target.iterdir():
            if not show_hidden and entry.name.startswith("."):
                continue
            if folders_only and not entry.is_dir():
                continue
            if files_only and not entry.is_file():
                continue
            items.append(_file_record(entry))
    except PermissionError:
        return _err("permission_denied", str(target))

    # Sort
    key_fn = {
        "name":     lambda r: r["name"].lower(),
        "date":     lambda r: r.get("modified_ts", 0),
        "modified": lambda r: r.get("modified_ts", 0),
        "size":     lambda r: r.get("size_bytes") or 0,
    }.get(sort_by.lower(), lambda r: r["name"].lower())

    reverse = sort_by.lower() in ("date", "modified", "size")
    items.sort(key=key_fn, reverse=reverse)
    items = items[:limit]

    return {
        "action":  "list",
        "path":    str(target),
        "count":   len(items),
        "results": items,
    }


def op_search(path: str = "home", name: str = "", extension: str = "",
              recursive: bool = True, limit: int = 50,
              sort_by: str = "name", folders_only: bool = False,
              files_only: bool = False, count_only: bool = False) -> dict:
    """Search for files/folders. Respects safety limits and timeout."""
    search_root = _resolve(path)
    if not _is_safe(search_root):
        return _err("access_denied", str(search_root))
    if not search_root.exists():
        return _err("path_not_found", str(search_root))

    ext_filter = _norm_ext(extension)
    name_lower = (name or "").lower()
    limit = min(limit, _MAX_RESULTS)

    results: list[dict] = []
    dirs_walked = 0
    deadline = time.monotonic() + _TIMEOUT_SECS
    truncated = False

    def _should_skip_dir(d: Path) -> bool:
        return d.name in _SKIP_DIRS or d.name.startswith(".")

    def _walk(current: Path):
        nonlocal dirs_walked, truncated
        if time.monotonic() > deadline:
            truncated = True
            return
        if not _is_safe(current):
            return
        try:
            entries = list(current.iterdir())
        except PermissionError:
            return
        except Exception:
            return

        for entry in entries:
            if time.monotonic() > deadline:
                truncated = True
                return
            if len(results) >= limit:
                truncated = True
                return
            try:
                is_dir  = entry.is_dir()
                is_file = entry.is_file()

                # Kind filter
                if folders_only and not is_dir:
                    continue
                if files_only and not is_file:
                    continue

                # Name filter
                if name_lower and name_lower not in entry.name.lower():
                    if is_dir:
                        # Still recurse — the matching file might be inside
                        pass
                    else:
                        continue

                # Extension filter (files only)
                if ext_filter and is_file:
                    if entry.suffix.lower() != ext_filter:
                        continue
                # When extension filter is active, skip directories from results
                if ext_filter and is_dir:
                    pass  # still recurse below, but don't add to results
                else:
                    # Add to results if it matches all filters
                    matches = True
                    if name_lower and name_lower not in entry.name.lower():
                        matches = False
                    if matches:
                        results.append(_file_record(entry))

                # Recurse into subdirectories
                if recursive and is_dir and not _should_skip_dir(entry):
                    dirs_walked += 1
                    if dirs_walked > _MAX_DIRS_WALK:
                        truncated = True
                        return
                    _walk(entry)

            except Exception:
                continue

    _walk(search_root)

    if not results:
        return {
            "action":    "search",
            "query":     {"path": str(search_root), "name": name, "extension": extension},
            "count":     0,
            "results":   [],
            "truncated": False,
        }

    # Sort
    key_fn = {
        "name":     lambda r: r["name"].lower(),
        "date":     lambda r: r.get("modified_ts", 0),
        "modified": lambda r: r.get("modified_ts", 0),
        "size":     lambda r: r.get("size_bytes") or 0,
    }.get(sort_by.lower(), lambda r: r["name"].lower())

    reverse = sort_by.lower() in ("date", "modified", "size")
    results.sort(key=key_fn, reverse=reverse)

    if count_only:
        return {
            "action":    "search",
            "query":     {"path": str(search_root), "name": name, "extension": extension},
            "count":     len(results),
            "results":   [],
            "truncated": truncated,
        }

    return {
        "action":    "search",
        "query":     {"path": str(search_root), "name": name, "extension": extension},
        "count":     len(results),
        "results":   results,
        "truncated": truncated,
    }


def op_recent(path: str = "home", limit: int = 20,
              extension: str = "", days: int = 30) -> dict:
    """Return recently modified files, newest first."""
    root = _resolve(path)
    if not _is_safe(root):
        return _err("access_denied", str(root))
    if not root.exists():
        return _err("path_not_found", str(root))

    ext_filter = _norm_ext(extension)
    cutoff = time.time() - days * 86400
    limit  = min(limit, _MAX_RESULTS)

    items: list[dict] = []
    deadline = time.monotonic() + _TIMEOUT_SECS
    truncated = False

    def _walk(current: Path):
        nonlocal truncated
        if time.monotonic() > deadline:
            truncated = True
            return
        try:
            for entry in current.iterdir():
                if time.monotonic() > deadline:
                    truncated = True
                    return
                if entry.name.startswith("."):
                    continue
                if entry.is_file():
                    if ext_filter and entry.suffix.lower() != ext_filter:
                        continue
                    try:
                        mtime = entry.stat().st_mtime
                        if mtime >= cutoff:
                            items.append(_file_record(entry))
                    except Exception:
                        pass
                elif entry.is_dir() and entry.name not in _SKIP_DIRS:
                    _walk(entry)
        except PermissionError:
            return

    _walk(root)
    items.sort(key=lambda r: r.get("modified_ts", 0), reverse=True)

    return {
        "action":    "recent",
        "path":      str(root),
        "days":      days,
        "count":     len(items[:limit]),
        "results":   items[:limit],
        "truncated": truncated,
    }


def op_info(path: str) -> dict:
    """Return detailed metadata about a single file or folder."""
    target = _resolve(path)
    if not _is_safe(target):
        return _err("access_denied", str(target))
    if not target.exists():
        return _err("path_not_found", str(target))

    record = _file_record(target)
    if target.is_dir():
        try:
            children = list(target.iterdir())
            record["child_count"] = len(children)
        except PermissionError:
            record["child_count"] = None
    return {"action": "info", "count": 1, "results": [record]}


def op_space(path: str) -> dict:
    """Return total size consumed by all files under a path."""
    root = _resolve(path)
    if not _is_safe(root):
        return _err("access_denied", str(root))
    if not root.exists():
        return _err("path_not_found", str(root))

    total = 0
    file_count = 0
    deadline = time.monotonic() + _TIMEOUT_SECS
    truncated = False
    try:
        for entry in root.rglob("*"):
            if time.monotonic() > deadline:
                truncated = True
                break
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                    file_count += 1
                except Exception:
                    pass
    except PermissionError:
        pass

    return {
        "action":     "space",
        "path":       str(root),
        "total_bytes": total,
        "total_human": _fmt_size(total),
        "file_count":  file_count,
        "truncated":   truncated,
    }


def op_open(path: str) -> dict:
    """Open a file, folder, or URL using the OS default handler."""
    import subprocess
    target = _resolve(path)

    # Allow URLs passed as-is (not resolved through home)
    raw = str(path or "").strip()
    is_url = raw.startswith("http://") or raw.startswith("https://") or raw.startswith("ftp://")

    if not is_url:
        if not _is_safe(target):
            return _err("access_denied", str(target))
        if not target.exists():
            return _err("path_not_found", str(target))

    try:
        if _OS == "Darwin":
            subprocess.Popen(["open", raw if is_url else str(target)])
        elif _OS == "Windows":
            os.startfile(raw if is_url else str(target))  # type: ignore
        else:
            subprocess.Popen(["xdg-open", raw if is_url else str(target)])

        label = raw if is_url else target.name
        return {
            "action":  "open",
            "opened":  raw if is_url else str(target),
            "name":    label,
            "success": True,
        }
    except Exception as e:
        return _err("open_failed", str(e))


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def local_explorer(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> Any:
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if player:
        player.write_log(f"[local_explorer] {action} {params.get('path','')}")

    try:
        if action == "list":
            result = op_list(
                path=params.get("path", "home"),
                show_hidden=bool(params.get("show_hidden", False)),
                sort_by=params.get("sort_by", "name"),
                limit=min(int(params.get("limit", 100)), _MAX_RESULTS),
                folders_only=bool(params.get("folders_only", False)),
                files_only=bool(params.get("files_only", False)),
            )

        elif action == "search":
            result = op_search(
                path=params.get("path", "home"),
                name=params.get("name", ""),
                extension=params.get("extension", ""),
                recursive=bool(params.get("recursive", True)),
                limit=min(int(params.get("limit", 50)), _MAX_RESULTS),
                sort_by=params.get("sort_by", "name"),
                folders_only=bool(params.get("folders_only", False)),
                files_only=bool(params.get("files_only", False)),
                count_only=bool(params.get("count_only", False)),
            )

        elif action == "recent":
            result = op_recent(
                path=params.get("path", "home"),
                limit=min(int(params.get("limit", 20)), _MAX_RESULTS),
                extension=params.get("extension", ""),
                days=int(params.get("days", 30)),
            )

        elif action == "info":
            result = op_info(path=params.get("path", ""))

        elif action == "space":
            result = op_space(path=params.get("path", "home"))

        elif action == "open":
            result = op_open(path=params.get("path", ""))

        else:
            result = {"error": "unknown_action", "detail": action, "results": [], "count": 0}

    except Exception as e:
        result = {"error": "unexpected", "detail": str(e), "results": [], "count": 0}

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "local_explorer",
    "description": (
        "Read-only structured exploration of the local filesystem. "
        "Use this tool to list directory contents, search for files or folders by name or extension, "
        "get recently modified files, retrieve metadata about a specific path, calculate space usage, "
        "or open a file/folder/URL with the OS default handler. "
        "Results are structured JSON objects Gemini can reference in follow-up tool calls. "
        "This tool never writes, deletes, or moves anything. "
        "Accepts shortcuts: desktop, downloads, documents, pictures, music, videos, home."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list | search | recent | info | space | open",
            },
            "path": {
                "type": "STRING",
                "description": (
                    "Target path or shortcut (desktop, downloads, documents, pictures, music, videos, home). "
                    "Accepts ~, absolute paths, or relative. For 'open', accepts URLs too."
                ),
            },
            "name": {
                "type": "STRING",
                "description": "For search: substring to match in file/folder names (case-insensitive).",
            },
            "extension": {
                "type": "STRING",
                "description": "For search/recent: file extension to filter by, e.g. '.pdf' or 'pdf'.",
            },
            "recursive": {
                "type": "BOOLEAN",
                "description": "For search: whether to search subdirectories (default true).",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of results to return (default 50, max 200).",
            },
            "sort_by": {
                "type": "STRING",
                "description": "Sort order: name | date | size (default: name).",
            },
            "folders_only": {
                "type": "BOOLEAN",
                "description": "If true, only return directories.",
            },
            "files_only": {
                "type": "BOOLEAN",
                "description": "If true, only return files (not directories).",
            },
            "count_only": {
                "type": "BOOLEAN",
                "description": "For search: if true, return only the count, not the full list.",
            },
            "days": {
                "type": "INTEGER",
                "description": "For recent: how many days back to consider (default 30).",
            },
            "show_hidden": {
                "type": "BOOLEAN",
                "description": "For list: include hidden files/folders (default false).",
            },
        },
        "required": ["action"],
    },
    "handler": local_explorer,
}
