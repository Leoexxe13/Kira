"""
test_local_explorer.py — Tests for the local_explorer structured filesystem tool.

Uses real directories inside the user's home so _is_safe() accepts them.
No phrase-based routing is tested — only the mechanical behaviour of each operation.
"""
import json
import os
import sys
import time
import unittest
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.local_explorer import (
    local_explorer, op_list, op_search, op_recent, op_info, op_space,
    TOOL, _norm_ext, _resolve,
)

# Use a temp dir inside the KIRA-CODEX workspace. The sandbox can write here
# and it's still under Path.home() so _is_safe() accepts it.
_TEST_BASE = Path(__file__).resolve().parent.parent / "tests" / ".tmp_local_explorer"


def _make_fixture(tmp: Path) -> dict:
    """Create a nested file tree. Returns notable paths."""
    paths: dict = {}
    (tmp / "readme.txt").write_text("hello")
    (tmp / "report.pdf").write_bytes(b"PDF" * 100)
    (tmp / "image.JPG").write_bytes(b"\xff\xd8" * 50)
    paths["txt"]  = tmp / "readme.txt"
    paths["pdf"]  = tmp / "report.pdf"
    paths["jpg"]  = tmp / "image.JPG"

    sub = tmp / "sub folder"
    sub.mkdir()
    (sub / "notes.txt").write_text("sub notes")
    (sub / "data.csv").write_text("a,b,c")
    paths["sub"]     = sub
    paths["sub_txt"] = sub / "notes.txt"
    paths["sub_csv"] = sub / "data.csv"

    deep = tmp / "deep" / "α-unicode"
    deep.mkdir(parents=True)
    (deep / "script.py").write_text("print(1)")
    paths["deep_py"] = deep / "script.py"

    return paths


class _WithFixture(unittest.TestCase):
    """Mixin: creates a fresh tmp dir under home for every test class."""
    @classmethod
    def setUpClass(cls):
        _TEST_BASE.mkdir(parents=True, exist_ok=True)
        cls.tmp = _TEST_BASE / cls.__name__
        if cls.tmp.exists():
            shutil.rmtree(cls.tmp)
        cls.tmp.mkdir(parents=True)
        cls.paths = _make_fixture(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


# ── _norm_ext ─────────────────────────────────────────────────────────────────

class TestNormExt(unittest.TestCase):
    def test_without_dot(self):     self.assertEqual(_norm_ext("pdf"),   ".pdf")
    def test_with_dot(self):        self.assertEqual(_norm_ext(".pdf"),  ".pdf")
    def test_uppercase(self):       self.assertEqual(_norm_ext("PDF"),   ".pdf")
    def test_empty(self):           self.assertEqual(_norm_ext(""),      "")
    def test_with_leading_space(self): self.assertEqual(_norm_ext(" pdf "), ".pdf")


# ── op_list ───────────────────────────────────────────────────────────────────

class TestOpList(_WithFixture):
    def test_list_basic(self):
        result = op_list(str(self.tmp))
        self.assertEqual(result["action"], "list")
        self.assertGreater(result["count"], 0)
        self.assertIn("readme.txt", [r["name"] for r in result["results"]])

    def test_list_structured_fields(self):
        result = op_list(str(self.tmp))
        for r in result["results"]:
            self.assertIn("name", r)
            self.assertIn("path", r)
            self.assertIn("kind", r)

    def test_list_files_only(self):
        result = op_list(str(self.tmp), files_only=True)
        for r in result["results"]:
            self.assertEqual(r["kind"], "file")

    def test_list_folders_only(self):
        result = op_list(str(self.tmp), folders_only=True)
        for r in result["results"]:
            self.assertEqual(r["kind"], "folder")

    def test_list_sort_by_name(self):
        result = op_list(str(self.tmp), sort_by="name")
        names = [r["name"].lower() for r in result["results"]]
        self.assertEqual(names, sorted(names))

    def test_list_sort_by_size(self):
        result = op_list(str(self.tmp), sort_by="size", files_only=True)
        sizes = [r.get("size_bytes") or 0 for r in result["results"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_list_limit(self):
        result = op_list(str(self.tmp), limit=2)
        self.assertLessEqual(result["count"], 2)

    def test_list_path_not_found(self):
        result = op_list(str(_TEST_BASE / "DOES_NOT_EXIST_XYZ"))
        self.assertIn("error", result)

    def test_list_not_a_directory(self):
        result = op_list(str(self.paths["txt"]))
        self.assertIn("error", result)

    def test_list_path_with_spaces(self):
        result = op_list(str(self.paths["sub"]))
        self.assertEqual(result["action"], "list")
        self.assertIn("notes.txt", [r["name"] for r in result["results"]])


# ── op_search ─────────────────────────────────────────────────────────────────

class TestOpSearch(_WithFixture):
    def test_search_by_name(self):
        result = op_search(str(self.tmp), name="readme")
        self.assertGreater(result["count"], 0)
        self.assertTrue(any("readme" in r["name"].lower() for r in result["results"]))

    def test_search_by_extension_with_dot(self):
        result = op_search(str(self.tmp), extension=".txt")
        self.assertGreater(result["count"], 0)
        for r in result["results"]:
            self.assertEqual(r["extension"], ".txt")

    def test_search_by_extension_without_dot(self):
        result = op_search(str(self.tmp), extension="txt")
        self.assertGreater(result["count"], 0)
        for r in result["results"]:
            self.assertEqual(r["extension"], ".txt")

    def test_search_case_insensitive_extension(self):
        # image.JPG should be found by ".jpg"
        result = op_search(str(self.tmp), extension=".jpg")
        self.assertGreater(result["count"], 0)
        self.assertIn("image.JPG", [r["name"] for r in result["results"]])

    def test_search_case_insensitive_name(self):
        result = op_search(str(self.tmp), name="README")
        self.assertGreater(result["count"], 0)

    def test_search_recursive_finds_deep(self):
        result = op_search(str(self.tmp), name="script", recursive=True)
        self.assertGreater(result["count"], 0)
        found_paths = [r["path"] for r in result["results"]]
        self.assertTrue(any("script.py" in p for p in found_paths))

    def test_search_nonrecursive_misses_deep(self):
        result = op_search(str(self.tmp), name="script", recursive=False)
        self.assertEqual(result["count"], 0)

    def test_search_limit(self):
        result = op_search(str(self.tmp), extension=".txt", limit=1)
        self.assertLessEqual(result["count"], 1)

    def test_search_count_only(self):
        result = op_search(str(self.tmp), extension=".txt", count_only=True)
        self.assertGreater(result["count"], 0)
        self.assertEqual(result["results"], [])

    def test_search_no_results(self):
        result = op_search(str(self.tmp), name="XXXX_DOES_NOT_EXIST")
        self.assertEqual(result["count"], 0)

    def test_search_path_not_found(self):
        result = op_search(str(_TEST_BASE / "MISSING"))
        self.assertIn("error", result)

    def test_search_unicode_path(self):
        result = op_search(str(self.tmp), name="script", recursive=True)
        found_paths = [r["path"] for r in result["results"]]
        self.assertTrue(any("α-unicode" in p for p in found_paths))

    def test_search_path_with_spaces(self):
        result = op_search(str(self.paths["sub"]))
        self.assertGreater(result["count"], 0)

    def test_search_sort_by_name(self):
        result = op_search(str(self.tmp), extension=".txt", sort_by="name")
        names = [r["name"].lower() for r in result["results"]]
        self.assertEqual(names, sorted(names))

    def test_search_multiple_results(self):
        result = op_search(str(self.tmp), extension=".txt")
        self.assertGreaterEqual(result["count"], 2)

    def test_search_structured_result_fields(self):
        result = op_search(str(self.tmp), name="readme")
        for r in result["results"]:
            for field in ("name", "path", "kind", "extension", "size_bytes", "modified_at"):
                self.assertIn(field, r)


# ── op_recent ─────────────────────────────────────────────────────────────────

class TestOpRecent(_WithFixture):
    def test_recent_returns_files(self):
        result = op_recent(str(self.tmp), days=365)
        self.assertGreater(result["count"], 0)

    def test_recent_sorted_newest_first(self):
        result = op_recent(str(self.tmp), days=365)
        ts = [r.get("modified_ts", 0) for r in result["results"]]
        self.assertEqual(ts, sorted(ts, reverse=True))

    def test_recent_with_extension_filter(self):
        result = op_recent(str(self.tmp), extension=".txt", days=365)
        for r in result["results"]:
            self.assertEqual(r["extension"], ".txt")

    def test_recent_limit(self):
        result = op_recent(str(self.tmp), limit=2, days=365)
        self.assertLessEqual(result["count"], 2)

    def test_recent_zero_days_returns_nothing(self):
        result = op_recent(str(self.tmp), days=0)
        self.assertEqual(result["count"], 0)


# ── op_info ───────────────────────────────────────────────────────────────────

class TestOpInfo(_WithFixture):
    def test_info_file(self):
        result = op_info(str(self.paths["txt"]))
        self.assertEqual(result["count"], 1)
        r = result["results"][0]
        self.assertEqual(r["name"], "readme.txt")
        self.assertEqual(r["kind"], "file")
        self.assertIn("size_bytes", r)
        self.assertIn("modified_at", r)

    def test_info_folder(self):
        result = op_info(str(self.paths["sub"]))
        r = result["results"][0]
        self.assertEqual(r["kind"], "folder")
        self.assertIn("child_count", r)
        self.assertEqual(r["child_count"], 2)

    def test_info_not_found(self):
        result = op_info(str(_TEST_BASE / "NO_SUCH_FILE.txt"))
        self.assertIn("error", result)


# ── op_space ──────────────────────────────────────────────────────────────────

class TestOpSpace(_WithFixture):
    def test_space_returns_total(self):
        result = op_space(str(self.tmp))
        self.assertIn("total_bytes", result)
        self.assertGreater(result["total_bytes"], 0)
        self.assertIn("total_human", result)
        self.assertIn("file_count", result)

    def test_space_not_found(self):
        result = op_space(str(_TEST_BASE / "MISSING"))
        self.assertIn("error", result)


# ── Dispatcher ────────────────────────────────────────────────────────────────

class TestDispatcher(_WithFixture):
    def test_unknown_action_returns_error(self):
        result = local_explorer({"action": "DELETE_EVERYTHING"})
        self.assertIn("error", result)

    def test_invalid_path_no_crash(self):
        result = local_explorer({"action": "list", "path": str(_TEST_BASE / "MISSING")})
        self.assertIn("error", result)

    def test_list_via_dispatcher(self):
        result = local_explorer({"action": "list", "path": str(self.tmp)})
        self.assertEqual(result["action"], "list")
        self.assertGreater(result["count"], 0)

    def test_search_via_dispatcher(self):
        result = local_explorer({"action": "search", "path": str(self.tmp), "extension": "txt"})
        self.assertGreater(result["count"], 0)

    def test_info_via_dispatcher(self):
        result = local_explorer({"action": "info", "path": str(self.paths["txt"])})
        self.assertEqual(result["count"], 1)

    def test_recent_via_dispatcher(self):
        result = local_explorer({"action": "recent", "path": str(self.tmp), "days": 365})
        self.assertGreater(result["count"], 0)

    def test_space_via_dispatcher(self):
        result = local_explorer({"action": "space", "path": str(self.tmp)})
        self.assertIn("total_bytes", result)

    def test_none_params_no_crash(self):
        result = local_explorer(None)
        self.assertIn("error", result)


# ── Tool declaration ──────────────────────────────────────────────────────────

class TestToolDeclaration(unittest.TestCase):
    def test_tool_has_required_fields(self):
        for field in ("name", "description", "parameters", "handler"):
            self.assertIn(field, TOOL)

    def test_tool_name(self):
        self.assertEqual(TOOL["name"], "local_explorer")

    def test_tool_required_params(self):
        self.assertIn("action", TOOL["parameters"].get("required", []))

    def test_tool_has_action_param(self):
        props = TOOL["parameters"]["properties"]
        for p in ("action", "path", "name", "extension", "recursive", "sort_by", "limit"):
            self.assertIn(p, props)


# ── Capabilities registry integration ────────────────────────────────────────

class TestCapabilitiesRegistry(unittest.TestCase):
    def _registry(self):
        from core.action_loader import discover_actions
        return discover_actions(
            actions_dir=Path(__file__).resolve().parent.parent / "actions",
            reserved_names=set(),
        )

    def test_auto_discovery_in_registry(self):
        self.assertIn("local_explorer", self._registry().names())

    def test_tool_declarations_include_local_explorer(self):
        decl_names = [d["name"] for d in self._registry().get_tool_declarations()]
        self.assertIn("local_explorer", decl_names)

    def test_new_tool_appears_without_gui_edits(self):
        # Just having the file in actions/ is enough
        self.assertGreater(len(self._registry().names()), 10)


# ── Operational context integration ──────────────────────────────────────────

class TestOperationalContext(unittest.TestCase):
    def setUp(self):
        self.mock_ui = MagicMock()
        self.patcher = patch("main.WakeWordDetector", autospec=True)
        self.patcher.start()
        from main import JarvisLive
        from collections import deque
        self.jarvis = JarvisLive(self.mock_ui)
        self.jarvis._loop = MagicMock()
        # Initialize lazily-created attribute that _execute_tool sets
        self.jarvis._op_context = deque(maxlen=5)

    def tearDown(self):
        self.patcher.stop()

    def test_structured_result_absorbed(self):
        fake_result = json.dumps({
            "action": "search", "count": 2,
            "results": [
                {"name": "test.pdf", "path": "/home/user/Downloads/test.pdf", "kind": "file"},
            ]
        })[:500]
        self.jarvis._op_context.append({
            "tool":           "local_explorer",
            "args":           {"action": "search", "extension": ".pdf"},
            "result_snippet": fake_result,
        })
        self.assertEqual(len(self.jarvis._op_context), 1)
        self.assertEqual(self.jarvis._op_context[0]["tool"], "local_explorer")

    def test_failed_result_does_not_replace_valid(self):
        self.jarvis._op_context.append({
            "tool":           "local_explorer",
            "args":           {"action": "list"},
            "result_snippet": "valid",
        })
        error = {"error": "path_not_found", "results": [], "count": 0}
        # Simulate main.py behaviour: only append on success
        if "error" not in error:
            self.jarvis._op_context.append({"tool": "local_explorer", "args": {}, "result_snippet": "bad"})
        self.assertEqual(len(self.jarvis._op_context), 1)
        self.assertEqual(self.jarvis._op_context[0]["result_snippet"], "valid")


# ── Security boundaries ───────────────────────────────────────────────────────

class TestSecurity(unittest.TestCase):
    def test_access_outside_home_denied_list(self):
        self.assertIn("error", op_list("/etc"))

    def test_access_outside_home_denied_search(self):
        self.assertIn("error", op_search("/etc"))

    def test_access_outside_home_denied_info(self):
        self.assertIn("error", op_info("/etc/hosts"))


if __name__ == "__main__":
    unittest.main()
