"""
actions/web_search.py

Semantic Web Research and Source Navigation Tool.
Handles web searches, domain-specific searches, fetching news, reading webpage content,
and opening URLs natively in the OS browser.
"""
import json
import re
import sys
import threading
import concurrent.futures
from urllib.parse import quote_plus
from pathlib import Path
from typing import Any

# If bs4 is missing, fallback to basic parsing
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

import requests


# ── Internal Helpers ─────────────────────────────────────────────────────────

def _is_mac() -> bool:
    return sys.platform == "darwin"

def _is_win() -> bool:
    return sys.platform == "win32"

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)["gemini_api_key"]
    except Exception:
        return ""


# ── Web Operations ───────────────────────────────────────────────────────────

def op_search(query: str, domain: str = "", max_results: int = 5) -> dict:
    """Uses DDG for structured search results."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return {"error": "Missing library: pip install duckduckgo-search"}

    # Apply domain restriction to query if present
    search_query = query
    if domain:
        search_query = f"{query} site:{domain}"

    results = []
    try:
        with DDGS() as ddgs:
            # max_results is passed to text() if supported, otherwise manually limited
            generator = ddgs.text(search_query, max_results=max_results)
            for r in generator:
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("href",   ""),
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        return {"error": f"Search failed: {e}", "results": []}

    return {
        "action": "search",
        "query": search_query,
        "count": len(results),
        "results": results
    }

def op_news(query: str, domain: str = "", max_results: int = 5) -> dict:
    """Uses DDG News for structured news results."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return {"error": "Missing library: pip install duckduckgo-search"}

    search_query = query
    if domain:
        search_query = f"{query} site:{domain}"
        
    results = []
    try:
        with DDGS() as ddgs:
            generator = ddgs.news(search_query, max_results=max_results)
            for r in generator:
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("url",    ""),
                    "source":  r.get("source", ""),
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        return {"error": f"News search failed: {e}", "results": []}

    return {
        "action": "news",
        "query": search_query,
        "count": len(results),
        "results": results
    }

def op_read_page(url: str) -> dict:
    """Fetches the URL and extracts text content."""
    if not url.startswith("http"):
        url = "https://" + url

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        html = r.text

        if BeautifulSoup:
            soup = BeautifulSoup(html, "html.parser")
            # Remove scripts and styles
            for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
                script.decompose()
            text = soup.get_text(separator="\n")
            # Clean up whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)
        else:
            # Extremely basic fallback
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()

        # Limit text to avoid blowing up context window (about 8000 chars)
        max_chars = 8000
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars] + "... [TRUNCATED]"
            truncated = True

        return {
            "action": "read_page",
            "url": url,
            "content": text,
            "truncated": truncated
        }

    except Exception as e:
        return {"error": f"Failed to read page: {e}", "url": url}

def op_open_url(url: str) -> dict:
    """Opens a URL in the user's native default browser."""
    import webbrowser
    if not url.startswith("http"):
        url = "https://" + url

    try:
        success = webbrowser.open(url)
        if success:
            return {"action": "open_url", "url": url, "status": "Opened natively in default browser."}
        else:
            return {"error": "Failed to open browser."}
    except Exception as e:
        return {"error": f"Error opening URL: {e}"}


# ── Dispatcher ───────────────────────────────────────────────────────────────

def web_search(parameters: dict, player=None, **kwargs) -> Any:
    """
    Main entry point for web research and navigation.
    Returns structured dicts so they go into operational context.
    """
    params = parameters or {}
    action = params.get("action", "search").lower().strip()
    query  = params.get("query", "").strip()
    domain = params.get("domain", "").strip()
    url    = params.get("url", "").strip()

    # Backwards compatibility: if action is missing but query is present, it's a search.
    # Also support older modes (research, price, compare) by falling back to search.
    if action in ("research", "price", "compare"):
        action = "search"

    if player:
        player.write_log(f"[WebResearch] {action}: {query or url}")
    print(f"[WebResearch] 🔍 action={action!r} query={query!r} domain={domain!r} url={url!r}")

    if action == "search":
        if not query:
            return {"error": "Query is required for search action"}
        return op_search(query, domain=domain)

    elif action == "news":
        if not query:
            return {"error": "Query is required for news action"}
        return op_news(query, domain=domain)

    elif action == "read_page":
        if not url:
            return {"error": "URL is required for read_page action"}
        return op_read_page(url)

    elif action == "open_url":
        if not url:
            return {"error": "URL is required for open_url action"}
        return op_open_url(url)

    else:
        return {"error": f"Unknown action: {action}"}


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "web_search",
    "description": (
        "Semantic Web Research and Source Navigation tool. "
        "Allows you to search the web, read specific pages, and open URLs. "
        "Use 'search' to get structured results (optionally restricted to a domain like 'wikipedia.org' or 'youtube.com'). "
        "Use 'read_page' to extract text from a specific URL. "
        "Use 'open_url' to visually open a site/video for the user in their native browser."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "search | news | read_page | open_url"
            },
            "query": {
                "type": "STRING",
                "description": "Search query or topic (required for search/news)"
            },
            "domain": {
                "type": "STRING",
                "description": "Optional: Restrict search to a specific domain/source (e.g., 'wikipedia.org', 'youtube.com')"
            },
            "url": {
                "type": "STRING",
                "description": "Target URL (required for read_page/open_url)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": web_search,
}
