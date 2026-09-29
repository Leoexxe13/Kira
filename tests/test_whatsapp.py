"""Isolated contract tests. No GUI, network, real profile or personal memory.

The semantic-model boundary is a double: these tests verify the routing contract,
not the language comprehension of a live Gemini model.
"""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from core import local_fastpath
from core.action_loader import ActionRecord, ActionRegistry, _validate
from memory import memory_manager as memory


ROOT = Path(__file__).resolve().parents[1]

# Import the actual action with both browser creation and background thread
# startup disabled. No discovery of unrelated actions or application startup.
playwright = types.ModuleType("playwright")
sync_api = types.ModuleType("playwright.sync_api")
sync_api.sync_playwright = Mock(side_effect=AssertionError("Real browser forbidden"))
with patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}), \
        patch.object(threading.Thread, "start"):
    from actions import whatsapp_web as wa


def router_class():
    """Execute the real router methods without importing Qt/audio/Gemini."""
    tree = ast.parse((ROOT / "main.py").read_text())
    names = {"_route_text_command_v1", "_should_use_groq_v1"}
    methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
               for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"json": json}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "main.py", "exec"), namespace)
    return type("RouterHarness", (), {name: namespace[name] for name in names})


class IsolatedMemory(unittest.TestCase):
    def setUp(self):
        # Even temporary files stay within the user's authorized workspace.
        self.temp = tempfile.TemporaryDirectory(prefix=".test-whatsapp-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "whatsapp_contacts.json"
        for attribute, value in (("WHATSAPP_CONTACTS_PATH", self.path),
                                 ("MEMORY_PATH", self.directory / "long_term.json")):
            mock = patch.object(memory, attribute, value)
            mock.start()
            self.addCleanup(mock.stop)

    def worker(self):
        with patch.object(threading.Thread, "start"):
            worker = wa._WhatsAppWorker()
        worker._ensure_page = Mock(return_value=Mock(url="https://web.whatsapp.com/"))
        worker._bring_front = Mock()
        worker._ensure_ready = Mock(return_value=True)
        worker._account_id = Mock(return_value="account-a@c.us")
        worker._identity_by_id = Mock(side_effect=lambda page, cid: {"id": cid, "name": "Verified"})
        worker._open_id = Mock(return_value={"ok": True})
        worker._active_chat = Mock(return_value={"id": "22@c.us", "name": "Brianny B"})
        worker._chat_rows = Mock(return_value=[
            {"id": "11@c.us", "name": "Brianny A", "aliases": ["Brianny A"], "timestamp": 2},
            {"id": "22@c.us", "name": "Brianny B", "aliases": ["Brianny B"], "timestamp": 1},
        ])
        worker._contact_rows = Mock(return_value=[])
        worker._send = Mock(return_value={"ok": True, "verified": True})
        worker._messages = Mock(return_value=[
            {"id": "m1", "text": "incoming", "fromMe": False, "timestamp": 1},
            {"id": "m2", "text": "outgoing", "fromMe": True, "timestamp": 2},
        ])
        worker._last_audio = Mock(return_value={"id": "audio1", "type": "ptt"})
        worker._play_audio_message = Mock(return_value={"ok": True})
        worker._download_media_payload = Mock(return_value={"data": "fake"})
        worker._transcribe_audio_payload = Mock(return_value={"ok": True, "text": "transcript"})
        worker._stop_audio = Mock(return_value={"ok": True})
        return worker

    def dispatch(self, worker, action="select", **params):
        result = worker._dispatch({"action": action, **params})
        prefix, _, body = result.partition(": ")
        try:
            return prefix, json.loads(body)
        except ValueError:
            return prefix, body

    def learn(self, worker=None, action="select", **params):
        worker = worker or self.worker()
        prefix, body = self.dispatch(worker, action, chat="Brianny", **params)
        self.assertIn(prefix, ("WHATSAPP_UNVERIFIED", "WHATSAPP_VERIFIED_SEARCH"))
        self.assertEqual(len(body["candidates"]), 2)
        self.assertIsNone(memory.get_whatsapp_contact("account-a@c.us", "brianny"))
        prefix, body = self.dispatch(worker, "select" if action == "search" else action,
                                     candidate_index=2, **params)
        self.assertNotEqual(prefix, "WHATSAPP_UNVERIFIED")
        return worker, body


class MemoryTests(IsolatedMemory):
    def test_persists_without_general_memory_or_prompt_changes(self):
        memory.remember("example", "general fact")
        before = memory.MEMORY_PATH.read_bytes()
        memory.remember_whatsapp_contact("a", "alias", "1@c.us", "Person")
        # Recreate the memory module, not merely a cached worker dictionary.
        spec = importlib.util.spec_from_file_location("recreated_memory", memory.__file__)
        restarted = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restarted)
        restarted.WHATSAPP_CONTACTS_PATH = self.path
        self.assertEqual(restarted.get_whatsapp_contact("a", "alias")["id"], "1@c.us")
        self.assertIsNone(restarted.get_whatsapp_contact("b", "alias"))
        self.assertEqual(memory.MEMORY_PATH.read_bytes(), before)
        self.assertNotIn("1@c.us", memory.format_memory_for_prompt(memory.load_memory()))
        self.assertNotIn("1@c.us", memory.search_memory("alias"))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_normalization_does_not_merge_fuzzy_names(self):
        memory.remember_whatsapp_contact("a", wa._norm(" BRÍANNY  "), "1@c.us", "One")
        self.assertIsNotNone(memory.get_whatsapp_contact("a", wa._norm("Brianny")))
        self.assertIsNone(memory.get_whatsapp_contact("a", wa._norm("Brianni")))

    def test_replacement_requires_forget_and_preserves_other_accounts(self):
        for account in ("a", "b"):
            memory.remember_whatsapp_contact(account, "alias", "1@c.us", "One")
        with self.assertRaises(ValueError):
            memory.remember_whatsapp_contact("a", "alias", "2@c.us", "Two")
        self.assertTrue(memory.forget_whatsapp_contact("a", "alias"))
        self.assertFalse(memory.forget_whatsapp_contact("a", "alias"))
        memory.remember_whatsapp_contact("a", "alias", "2@c.us", "Two")
        self.assertEqual(memory.get_whatsapp_contact("a", "alias")["id"], "2@c.us")
        self.assertEqual(memory.get_whatsapp_contact("b", "alias")["id"], "1@c.us")

    def test_corruption_is_not_overwritten(self):
        self.path.write_text("broken")
        with self.assertRaises(ValueError):
            memory.remember_whatsapp_contact("a", "alias", "1@c.us", "One")
        self.assertEqual(self.path.read_text(), "broken")

    def test_atomic_failure_preserves_previous_contents(self):
        memory.remember_whatsapp_contact("a", "alias", "1@c.us", "One")
        before = self.path.read_bytes()
        with patch("os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                memory.forget_whatsapp_contact("a", "alias")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])


class ResolutionTests(IsolatedMemory):
    def test_explicit_verified_choice_learns_original_query(self):
        worker, body = self.learn()
        self.assertEqual(memory.get_whatsapp_contact("account-a@c.us", "brianny")["id"], "22@c.us")
        self.assertIsNone(memory.get_whatsapp_contact("account-a@c.us", "el segundo"))
        self.assertEqual(worker._open_id.call_args.args[1], "22@c.us")
        self.assertIsNone(worker.pending_context())

    def test_explicit_choice_preserves_each_pending_operation(self):
        for action in ("read", "send", "last_incoming", "last_outgoing", "last_audio",
                       "play_audio", "transcribe_audio"):
            with self.subTest(action=action):
                worker, _ = self.learn(action=action, message="Original message")
                self.assertIsNotNone(memory.get_whatsapp_contact("account-a@c.us", "brianny"))
                if action == "send":
                    worker._send.assert_called_once()
                    self.assertEqual(worker._send.call_args.args[1:], ("22@c.us", "Original message"))
                memory.forget_whatsapp_contact("account-a@c.us", "brianny")

    def test_account_identity_requires_a_serialized_user_not_a_device_or_label(self):
        worker, page = self.worker(), Mock()
        for value, expected in (("123@c.us", "123@c.us"), ("456@lid", "456@lid"),
                                ("123:4@c.us", ""), ("Display Name", ""),
                                ("[object Object]", ""), (None, "")):
            with self.subTest(value=value):
                page.evaluate.return_value = value
                self.assertEqual(wa._WhatsAppWorker._account_id(worker, page), expected)

    def test_candidate_id_must_belong_to_pending_list(self):
        worker = self.worker()
        self.dispatch(worker, chat="Brianny")
        prefix, _ = self.dispatch(worker, candidate_id="99@c.us")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._open_id.assert_not_called()
        self.assertFalse(self.path.exists())
        prefix, _ = self.dispatch(worker, candidate_id="22@c.us", chat="el segundo")
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_CHAT_OPEN")
        self.assertIsNotNone(memory.get_whatsapp_contact("account-a@c.us", "brianny"))
        self.assertIsNone(memory.get_whatsapp_contact("account-a@c.us", "el segundo"))

    def test_restart_reuses_identity_without_ranking(self):
        self.learn()
        worker = self.worker()
        worker._resolve_chat = Mock(side_effect=AssertionError("Must use confirmed identity"))
        prefix, _ = self.dispatch(worker, chat=" BRÍANNY ")
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_CHAT_OPEN")
        worker._identity_by_id.assert_called_once()
        worker._open_id.assert_called_once()
        worker._active_chat.assert_called_once()

    def test_search_does_not_learn_and_uses_confirmed_identity_later(self):
        worker, _ = self.learn(action="search")
        worker._resolve_chat = Mock(side_effect=AssertionError("Unexpected ranking"))
        worker._open_id.reset_mock()
        prefix, body = self.dispatch(worker, "search", chat="Brianny")
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_SEARCH")
        self.assertEqual(body["match"]["id"], "22@c.us")
        worker._open_id.assert_not_called()

    def test_automatic_name_match_never_learns(self):
        worker = self.worker()
        prefix, _ = self.dispatch(worker, chat="Brianny B")
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_CHAT_OPEN")
        self.assertFalse(self.path.exists())

    def test_failed_open_or_final_verification_never_learns(self):
        for failure in ("open", "active"):
            with self.subTest(failure=failure):
                worker = self.worker()
                self.dispatch(worker, chat="Brianny")
                if failure == "open":
                    worker._open_id.return_value = {"ok": False}
                else:
                    worker._active_chat.return_value = {"id": "wrong@c.us"}
                prefix, _ = self.dispatch(worker, candidate_index=2)
                self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
                self.assertFalse(self.path.exists())

    def test_expired_selection_cannot_learn(self):
        worker = self.worker()
        self.dispatch(worker, chat="Brianny")
        with patch.object(wa.time, "monotonic", return_value=worker._last_candidates_at + 301):
            self.assertIsNone(worker.pending_context())
            prefix, _ = self.dispatch(worker, candidate_index=2)
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        self.assertFalse(self.path.exists())

    def test_invalid_memory_never_falls_back_or_sends(self):
        self.learn()
        for identity in (None, {"id": "wrong@c.us"}):
            with self.subTest(identity=identity):
                worker = self.worker()
                worker._identity_by_id.side_effect = None
                worker._identity_by_id.return_value = identity
                worker._resolve_chat = Mock(side_effect=AssertionError("Unsafe fallback"))
                prefix, body = self.dispatch(worker, "send", chat="Brianny", message="test")
                self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
                self.assertTrue(body["forget_required"])
                worker._open_id.assert_not_called()
                worker._send.assert_not_called()
        self.assertEqual(memory.get_whatsapp_contact("account-a@c.us", "brianny")["id"], "22@c.us")

    def test_identity_lookup_exception_blocks_without_ranking(self):
        self.learn()
        worker = self.worker()
        worker._identity_by_id.side_effect = RuntimeError("WA-JS unavailable")
        worker._resolve_chat = Mock(side_effect=AssertionError("Unsafe fallback"))
        prefix, _ = self.dispatch(worker, "send", chat="Brianny", message="test")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._send.assert_not_called()

    def test_remembered_identity_still_requires_open_and_send_guards(self):
        self.learn()
        for stage in ("open", "resolved_active", "before_send"):
            with self.subTest(stage=stage):
                worker = self.worker()
                if stage == "open":
                    worker._open_id.return_value = {"ok": False}
                elif stage == "resolved_active":
                    worker._active_chat.return_value = {"id": "wrong@c.us"}
                else:
                    worker._active_chat.side_effect = [{"id": "22@c.us"}, {"id": "wrong@c.us"}]
                prefix, _ = self.dispatch(worker, "send", chat="Brianny", message="test")
                self.assertIn(prefix, ("WHATSAPP_UNVERIFIED", "WHATSAPP_SEND_BLOCKED_WRONG_RECIPIENT"))
                worker._send.assert_not_called()

    def test_forget_allows_new_resolution_and_replacement(self):
        worker, _ = self.learn()
        prefix, body = self.dispatch(worker, "forget_contact", chat="Brianny")
        self.assertEqual(prefix, "WHATSAPP_CONTACT_FORGOTTEN")
        self.assertTrue(body["forgotten"])
        prefix, body = self.dispatch(worker, chat="Brianny")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._active_chat.return_value = {"id": "11@c.us"}
        self.dispatch(worker, candidate_index=1)
        self.assertEqual(memory.get_whatsapp_contact("account-a@c.us", "brianny")["id"], "11@c.us")

    def test_different_account_cannot_reuse_memory_or_pending_choice(self):
        self.learn()
        worker = self.worker()
        worker._account_id.return_value = "account-b@c.us"
        prefix, _ = self.dispatch(worker, chat="Brianny")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._identity_by_id.assert_not_called()
        worker._account_id.return_value = "account-c@c.us"
        prefix, _ = self.dispatch(worker, candidate_index=2)
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._open_id.assert_not_called()

    def test_account_change_during_verification_blocks_learning(self):
        worker = self.worker()
        self.dispatch(worker, chat="Brianny")
        worker._account_id.side_effect = ["account-a@c.us", "account-b@c.us"]
        prefix, _ = self.dispatch(worker, candidate_index=2)
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        self.assertFalse(self.path.exists())

    def test_account_change_before_send_blocks_send(self):
        self.learn()
        worker = self.worker()
        worker._account_id.side_effect = ["account-a@c.us", "account-a@c.us", "account-b@c.us"]
        prefix, _ = self.dispatch(worker, "send", chat="Brianny", message="test")
        self.assertEqual(prefix, "WHATSAPP_SEND_BLOCKED_WRONG_RECIPIENT")
        worker._send.assert_not_called()

    def test_unknown_account_and_broken_store_fail_closed(self):
        worker = self.worker()
        worker._account_id.return_value = ""
        prefix, _ = self.dispatch(worker, "send", chat="Brianny", message="test")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._send.assert_not_called()
        worker._account_id.return_value = "account-a@c.us"
        self.path.write_text("broken")
        prefix, _ = self.dispatch(worker, chat="Brianny")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._open_id.assert_not_called()

    def test_persistence_failure_is_reported_without_claiming_learning(self):
        worker = self.worker()
        self.dispatch(worker, chat="Brianny")
        with patch.object(wa, "remember_whatsapp_contact", side_effect=OSError("disk")):
            prefix, body = self.dispatch(worker, candidate_index=2)
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_CHAT_OPEN")
        self.assertIn("memory_warning", body)
        self.assertFalse(self.path.exists())

    def test_existing_operations_use_common_verified_resolution(self):
        self.learn()
        expected = {
            "select": "WHATSAPP_VERIFIED_CHAT_OPEN", "read": "WHATSAPP_VERIFIED_MESSAGES",
            "last_incoming": "WHATSAPP_VERIFIED_LAST_INCOMING",
            "last_outgoing": "WHATSAPP_VERIFIED_LAST_OUTGOING",
            "last_audio": "WHATSAPP_VERIFIED_LAST_AUDIO", "play_audio": "WHATSAPP_VERIFIED_AUDIO_PLAYING",
            "transcribe_audio": "WHATSAPP_VERIFIED_AUDIO_TRANSCRIPT", "send": "WHATSAPP_VERIFIED_SENT",
        }
        for action, expected_prefix in expected.items():
            with self.subTest(action=action):
                worker = self.worker()
                prefix, body = self.dispatch(worker, action, chat="Brianny", message="test")
                self.assertEqual(prefix, expected_prefix)
                worker._identity_by_id.assert_called_once()
                worker._open_id.assert_called_once()
                self.assertGreaterEqual(worker._active_chat.call_count, 1)
                if action == "last_incoming":
                    self.assertEqual(body["message"]["text"], "incoming")
                if action == "last_outgoing":
                    self.assertEqual(body["message"]["text"], "outgoing")
                if action == "play_audio":
                    worker._transcribe_audio_payload.assert_not_called()
                if action == "send":
                    self.assertEqual(worker._send.call_args.args[1:], ("22@c.us", "test"))

    def test_read_direction_and_active_chat_without_named_target(self):
        worker = self.worker()
        for direction, expected in (("incoming", "incoming"), ("outgoing", "outgoing")):
            _, body = self.dispatch(worker, "read", direction=direction)
            self.assertEqual([m["text"] for m in body["messages"]], [expected])
        worker._open_id.assert_not_called()
        prefix, _ = self.dispatch(worker, "send", message="test")
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_SENT")
        worker._active_chat.return_value = None
        worker._send.reset_mock()
        prefix, _ = self.dispatch(worker, "send", message="test")
        self.assertEqual(prefix, "WHATSAPP_UNVERIFIED")
        worker._send.assert_not_called()

    def test_open_authentication_stop_audio_and_close(self):
        worker = self.worker()
        self.assertEqual(self.dispatch(worker, "open")[0], "WHATSAPP_VERIFIED_OPEN")
        worker._bring_front.assert_called_once()
        worker._bring_front.reset_mock()
        self.dispatch(worker, chat="Brianny")
        worker._bring_front.assert_not_called()
        self.assertIsNotNone(worker.pending_context())
        worker._ensure_ready.return_value = False
        self.assertEqual(self.dispatch(worker, "open")[0], "WHATSAPP_LOGIN_REQUIRED")
        self.assertIsNone(worker.pending_context())
        self.assertEqual(self.dispatch(worker, "read", chat="Brianny")[0], "WHATSAPP_LOGIN_REQUIRED")
        worker._ensure_ready.return_value = True
        self.assertEqual(self.dispatch(worker, "stop_audio")[0], "WHATSAPP_AUDIO_STOPPED")
        self.dispatch(worker, chat="Brianny")
        worker._ctx, worker._pw = Mock(), Mock()
        context, playwright = worker._ctx, worker._pw
        worker._ensure_page.reset_mock()
        prefix, _ = self.dispatch(worker, "close")
        self.assertEqual(prefix, "WHATSAPP_VERIFIED_CLOSED")
        context.close.assert_called_once()
        playwright.stop.assert_called_once()
        worker._ensure_page.assert_not_called()
        self.assertIsNone(worker.pending_context())

    def test_exact_open_guard_uses_wajs_active_id(self):
        worker = self.worker()
        page = Mock()
        worker._ensure_composer_visible = Mock()
        worker._active_chat.return_value = {"id": "wrong@c.us"}
        self.assertFalse(wa._WhatsAppWorker._open_id(worker, page, "22@c.us")["ok"])
        worker._active_chat.return_value = {"id": "22@c.us"}
        self.assertTrue(wa._WhatsAppWorker._open_id(worker, page, "22@c.us")["ok"])

    def test_browser_lifecycle_keeps_dedicated_persistent_chromium(self):
        worker = self.worker()
        worker._ensure_composer_visible = Mock()
        page = Mock()
        page.is_closed.return_value = False
        context = Mock(pages=[page])
        runtime = Mock()
        runtime.chromium.launch_persistent_context.return_value = context
        factory = Mock()
        factory.start.return_value = runtime
        bundle = self.directory / "wajs.js"
        bundle.write_text("// fake")
        profile = self.directory / "profile"
        with patch.object(wa, "_PROFILE", profile), patch.object(wa, "_WAJS", bundle), \
                patch.object(wa, "sync_playwright", return_value=factory):
            self.assertIs(wa._WhatsAppWorker._ensure_page(worker), page)
            self.assertIs(wa._WhatsAppWorker._ensure_page(worker), page)
        runtime.chromium.launch_persistent_context.assert_called_once()
        self.assertEqual(runtime.chromium.launch_persistent_context.call_args.args, (str(profile),))
        self.assertFalse(runtime.chromium.launch_persistent_context.call_args.kwargs["headless"])
        page.goto.assert_called_once_with(wa._URL, wait_until="domcontentloaded", timeout=60000)

    def test_recent_status_and_unread_remain_available(self):
        worker = self.worker()
        worker._chat_rows.return_value[0]["unread"] = 1
        for action, prefix in (("status", "WHATSAPP_VERIFIED_STATUS"),
                               ("recent", "WHATSAPP_VERIFIED_RECENT"),
                               ("last", "WHATSAPP_VERIFIED_LAST"),
                               ("unread", "WHATSAPP_VERIFIED_UNREAD")):
            with self.subTest(action=action):
                actual, body = self.dispatch(worker, action)
                self.assertEqual(actual, prefix)
                if action == "unread":
                    self.assertEqual(len(body["chats"]), 1)

    def test_send_duplicate_guard_unchanged(self):
        worker = self.worker()
        page = Mock()
        page.evaluate.return_value = {"ok": True}
        worker._messages.return_value = [{"fromMe": True, "text": "test"}]
        first = wa._WhatsAppWorker._send(worker, page, "22@c.us", "test")
        second = wa._WhatsAppWorker._send(worker, page, "22@c.us", "test")
        self.assertTrue(first["verified"])
        self.assertTrue(second["duplicate_suppressed"])
        page.evaluate.assert_called_once()


class RoutingTests(IsolatedMemory):
    def registry(self, worker=None):
        handler = Mock(return_value="done")
        record = ActionRecord(name="whatsapp_web", valid=True, handler=handler,
                              pending_context=worker.pending_context if worker else None)
        generic = Mock(side_effect=AssertionError("Generic WhatsApp executor forbidden"))
        records = {"whatsapp_web": record}
        for name in ("open_app", "browser_control", "send_message"):
            records[name] = ActionRecord(name=name, valid=True, handler=generic)
        return ActionRegistry(records, Mock()), handler, generic

    def router(self, registry):
        router = router_class()()
        router._action_registry = registry
        router._on_text_command = Mock()
        router._music_request_v62 = Mock(return_value=None)
        router.ui = Mock()
        router._loop = None
        return router

    def test_fastpath_never_claims_specialized_resource(self):
        examples = ("abre WhatsApp", "inicia WhatsApp", "ve a WhatsApp",
                    "Me gustaría entrar a WhatsApp", "abre WhatsApp en Safari",
                    "abre el chat de Test en WhatsApp", "pon música por WhatsApp")
        with patch.object(local_fastpath, "_run") as execute:
            for text in examples:
                with self.subTest(text=text):
                    self.assertFalse(local_fastpath.handle(text).handled)
            execute.assert_not_called()

    def test_semantic_access_contract_and_language_variations(self):
        registry, handler, generic = self.registry()
        router = self.router(registry)
        # A semantic-session double supplies the same intent for varied language.
        # No phrase matcher is implemented in production or in this double.
        router._on_text_command.side_effect = lambda text: registry.run("whatsapp_web", {"action": "open"})
        for text in ("abre WhatsApp", "inicia WhatsApp", "ve a WhatsApp",
                     "Quisiera acceder a WhatsApp", "Could you bring up WhatsApp?"):
            with self.subTest(text=text):
                router._route_text_command_v1(text)
                self.assertEqual((handler.call_args.kwargs.get("parameters") or handler.call_args.kwargs.get("args")), {"action": "open"})
                self.assertEqual(router._on_text_command.call_args.args[0], text)
        generic.assert_not_called()

    def test_generic_access_redirects_but_embedded_operation_is_not_downgraded(self):
        registry, handler, generic = self.registry()
        registry.run("open_app", {"app_name": "WhatsApp"})
        self.assertEqual((handler.call_args.kwargs.get("parameters") or handler.call_args.kwargs.get("args")), {"action": "open"})
        handler.reset_mock()
        result = registry.run("open_app", {"app_name": "chat de Test en WhatsApp"})
        self.assertTrue(result.startswith("KIRA_ROUTE_BLOCKED"))
        result = registry.run("open_app", {"app_name": "WhatsApp", "action": "send", "message": "test"})
        self.assertTrue(result.startswith("KIRA_ROUTE_BLOCKED"))
        handler.assert_not_called()
        generic.assert_not_called()

    def test_missing_specialized_backend_never_falls_back_to_native(self):
        registry, handler, generic = self.registry()
        del registry._actions["whatsapp_web"]
        self.assertIn("not available", registry.run("open_app", {"app_name": "WhatsApp"}))
        generic.assert_not_called()
        handler.assert_not_called()

    def test_unrelated_native_apps_keep_working(self):
        from actions import open_app
        launcher = Mock(return_value=True)
        with patch.dict(open_app._OS_LAUNCHERS, {open_app._SYSTEM: launcher}):
            self.assertEqual(open_app.open_app({"app_name": "Spotify"}), "Opened Spotify.")
        launcher.assert_called_once()

    def test_generic_guards_allow_whatsapp_in_message_contents(self):
        from core.capabilities import generic_target_owner
        self.assertIsNone(generic_target_owner("send_message", {
            "platform": "telegram", "message_text": "WhatsApp is unavailable"}))

    def test_generic_browser_and_messaging_cannot_claim_whatsapp(self):
        registry, handler, generic = self.registry()
        for name, params in (("browser_control", {"action": "go_to", "url": "https://web.whatsapp.com"}),
                             ("browser_control", {"action": "search", "query": "WhatsApp"}),
                             ("browser_control", {"action": "type", "text": "https://web.whatsapp.com"}),
                             ("send_message", {"platform": "whatsapp", "receiver": "Test"}),
                             ("send_message", {"receiver": "Test"})):
            with self.subTest(tool=name, params=params):
                self.assertTrue(registry.run(name, params).startswith("KIRA_ROUTE_BLOCKED"))
        handler.assert_not_called()
        generic.assert_not_called()

    def test_legacy_messaging_aliases_cannot_reach_native_whatsapp(self):
        with patch.dict(sys.modules, {"pyautogui": types.ModuleType("pyautogui"),
                                      "pyperclip": types.ModuleType("pyperclip")}):
            from actions import send_message
        with patch.object(send_message, "_desktop_send", side_effect=AssertionError("Native WhatsApp")):
            for alias in ("whatsapp", "wp", "wapp"):
                with self.subTest(alias=alias):
                    self.assertTrue(send_message._resolve_platform(alias)("Test", "message").startswith("KIRA_ROUTE_BLOCKED"))
            self.assertTrue(send_message.send_message({"receiver": "Test", "message_text": "test"}).startswith("KIRA_ROUTE_BLOCKED"))

    def test_direct_browser_entry_cannot_navigate_to_whatsapp(self):
        from core.capabilities import generic_target_owner, reserved_result
        tree = ast.parse((ROOT / "actions/browser_control.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "browser_control")
        namespace = {"generic_target_owner": generic_target_owner, "reserved_result": reserved_result}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "browser_control.py", "exec"), namespace)
        result = namespace["browser_control"]({"action": "go_to", "url": "https://web.whatsapp.com"})
        self.assertTrue(result.startswith("KIRA_ROUTE_BLOCKED"))

    def test_direct_native_launcher_is_guarded_even_without_action_parameter(self):
        from actions import open_app
        with patch.dict(open_app._OS_LAUNCHERS, {open_app._SYSTEM: Mock(side_effect=AssertionError("Native launch"))}):
            self.assertTrue(open_app.open_app({"app_name": "WhatsApp"}).startswith("KIRA_ROUTE_BLOCKED"))
            self.assertTrue(open_app.TOOL["handler"]({"app_name": "WhatsApp"}).startswith("KIRA_ROUTE_BLOCKED"))

    def test_specific_semantic_operations_retain_arguments(self):
        registry, handler, _ = self.registry()
        router = self.router(registry)
        for action in ("select", "search", "read", "send", "last_incoming", "last_outgoing",
                       "play_audio", "last_audio", "transcribe_audio", "stop_audio", "close", "forget_contact"):
            with self.subTest(action=action):
                intent = {"action": action, "chat": "Test", "message": "payload"}
                router._on_text_command.side_effect = lambda text: registry.run("whatsapp_web", intent)
                router._route_text_command_v1("Operación contextual en WhatsApp")
                kw = handler.call_args.kwargs
        self.assertEqual(kw.get("parameters") or kw.get("args"), intent)

    def test_pending_state_routes_all_input_with_operation_and_original_query(self):
        worker = self.worker()
        self.dispatch(worker, "send", chat="Brianny", message="Original message")
        registry, _, _ = self.registry(worker)
        router = self.router(registry)
        for text in ("2", "el segundo", "la opción dos", "esa persona", "cambié de idea", "/groq hola"):
            with self.subTest(text=text):
                router._route_text_command_v1(text)
                forwarded = router._on_text_command.call_args.args[0]
                context = json.loads(forwarded.split("\n")[1])["whatsapp_web"]
                self.assertEqual(context["query"], "Brianny")
                self.assertEqual(context["operation"], {"action": "send", "chat": "Brianny", "message": "Original message"})
                self.assertTrue(forwarded.endswith(text))
        router._music_request_v62.assert_not_called()
        # Mutating a caller's snapshot must not change the worker's candidates.
        registry.pending_contexts()["whatsapp_web"]["candidates"].clear()
        self.assertEqual(len(worker.pending_context()["candidates"]), 2)

    def test_normal_routing_preserves_original_free_first_policy(self):
        router = self.router(self.registry()[0])
        for text in ("tengo una idea", "conversemos un rato", "lee el chat de Test"):
            self.assertTrue(router._should_use_groq_v1(text))
        for text in ("abre la terminal", "busca noticias", "mensaje urgente"):
            self.assertFalse(router._should_use_groq_v1(text))
        self.assertFalse(router._should_use_groq_v1("corto"))
        self.assertFalse(router._should_use_groq_v1("/gemini explica esto"))
        self.assertTrue(router._should_use_groq_v1("/groq explica esto"))

    def test_specialized_resource_ownership_precedes_free_first(self):
        router = self.router(self.registry()[0])
        # These are semantic examples only; production routing checks ownership,
        # never these formulations.
        whatsapp_intents = (
            "acceso simple a WhatsApp",
            "lee un chat en WhatsApp",
            "escribe a un contacto por WhatsApp",
            "busca un contacto en WhatsApp",
            "cierra WhatsApp",
        )
        for text in whatsapp_intents:
            with self.subTest(text=text):
                self.assertFalse(router._should_use_groq_v1(text))
        # Ownership also wins over an explicit generic provider request: the
        # specialized tool-capable resolver must receive the resource operation.
        self.assertFalse(router._should_use_groq_v1("/groq busca un contacto en WhatsApp"))
        self.assertTrue(router._should_use_groq_v1("conversemos sin mencionar recursos"))

    def test_pending_context_overrides_normal_provider_selection_by_state(self):
        worker = self.worker()
        self.dispatch(worker, "send", chat="Brianny", message="Original message")
        registry, _, _ = self.registry(worker)
        router = self.router(registry)
        # This input would normally go to Groq under FREE-FIRST; pending state
        # forwards it to the semantic session with the operation context.
        router._route_text_command_v1("conversemos un rato")
        forwarded = router._on_text_command.call_args.args[0]
        self.assertIn("PENDING_TOOL_CONTEXT", forwarded)
        self.assertIn("Brianny", forwarded)
        self.assertIn("Original message", forwarded)
        self.assertTrue(router._should_use_groq_v1("conversemos un rato"),
                        "The provider predicate remains FREE-FIRST; pending state is handled by the route")

    def test_pending_callback_is_discovered_but_not_sent_in_tool_schema(self):
        record = _validate(wa, "whatsapp_web.py")
        self.assertTrue(record.valid)
        self.assertTrue(callable(record.pending_context))
        declarations = ActionRegistry({record.name: record}, Mock()).get_tool_declarations()
        self.assertNotIn("pending_context", declarations[0])


if __name__ == "__main__":
    unittest.main()
