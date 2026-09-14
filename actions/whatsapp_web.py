from __future__ import annotations

import base64
import json
import os
import queue
import ssl
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from difflib import SequenceMatcher
from pathlib import Path

from playwright.sync_api import sync_playwright

_URL = "https://web.whatsapp.com/"
_PROFILE = Path.home() / ".kira_whatsapp_profile"
_WAJS = Path.home() / "Mark-LIII" / "vendor" / "wppconnect-wa.js"


def _norm(value):
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().strip().split())


def _dump(prefix, payload):
    return prefix + ": " + json.dumps(payload, ensure_ascii=False, default=str)


def _fuzzy_key(value):
    s = _norm(value)
    s = "".join(ch for ch in s if ch.isalnum())
    # Frequent Spanish speech-to-text/contact-name variation:
    # final/intermediate "y" is commonly transcribed as "i".
    s = s.replace("y", "i")
    return s


def _tokens(value):
    return [x for x in _norm(value).split() if x]


def _token_similarity(a, b):
    a, b = _fuzzy_key(a), _fuzzy_key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
        return 0.94
    return SequenceMatcher(None, a, b).ratio()


def _score(wanted, candidate):
    """Conservative contact-name score.

    A misspelling such as Brianni/Brianny should still work, but a two-word
    request such as 'David Jose' must not collapse into a completely different
    active chat. Multi-token requests require each requested name token to be
    represented by a reasonably similar candidate token.
    """
    a, b = _norm(wanted), _norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    ka, kb = _fuzzy_key(a), _fuzzy_key(b)
    if ka and kb and ka == kb:
        return 0.995

    wa, wb = _tokens(a), _tokens(b)
    # Ignore numeric aliases when the user asked by name. Phone numbers are
    # useful only when the user actually dictates a number.
    if not any(ch.isdigit() for ch in a) and b.replace(' ', '').isdigit():
        return 0.0

    if len(wa) >= 2:
        per_token = []
        for wanted_token in wa:
            best = max((_token_similarity(wanted_token, cand) for cand in wb), default=0.0)
            per_token.append(best)
        # Every requested token must be represented. This is the key guard that
        # prevents 'David Jose' from resolving to an unrelated chat.
        if not per_token or min(per_token) < 0.72:
            return 0.0
        coverage = sum(per_token) / len(per_token)
        whole = max(
            SequenceMatcher(None, a, b).ratio(),
            SequenceMatcher(None, ka, kb).ratio() if ka and kb else 0.0,
        )
        return 0.72 * coverage + 0.28 * whole

    # Single names still tolerate small spelling/dictation changes.
    if ka and kb and (ka in kb or kb in ka):
        return 0.95
    raw = SequenceMatcher(None, a, b).ratio()
    fuzzy = SequenceMatcher(None, ka, kb).ratio() if ka and kb else 0.0
    prefix_bonus = 0.0
    if len(ka) >= 4 and len(kb) >= 4 and ka[:4] == kb[:4] and abs(len(ka)-len(kb)) <= 2:
        prefix_bonus = 0.88
    return max(raw, fuzzy, prefix_bonus)


class _WhatsAppWorker:
    def __init__(self):
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="KIRA-WhatsApp-WAJS", daemon=True)
        self._thread.start()
        self._pw = None
        self._ctx = None
        self._page = None
        self._active_chat_id = ""
        self._active_chat_name = ""
        self._last_send = ("", "", 0.0)
        self._last_candidates = []
        self._last_candidate_query = ""
        self._last_candidates_at = 0.0
        self._pending_operation = None

    def call(self, params, timeout=90):
        done = queue.Queue(maxsize=1)
        self._q.put((dict(params or {}), done))
        try:
            ok, result = done.get(timeout=timeout)
        except queue.Empty:
            return "WHATSAPP_ERROR: tiempo de espera agotado."
        if ok:
            return result
        return "WHATSAPP_ERROR: " + str(result)

    def _run(self):
        while True:
            params, done = self._q.get()
            try:
                done.put((True, self._dispatch(params)))
            except Exception as e:
                done.put((False, f"{type(e).__name__}: {e}"))

    def _ensure_page(self):
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                pass

        if not _WAJS.exists():
            raise RuntimeError("Falta vendor/wppconnect-wa.js. Vuelve a ejecutar KIRA_WHATSAPP_BETA.py.")

        self._pw = sync_playwright().start()
        _PROFILE.mkdir(parents=True, exist_ok=True)
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(_PROFILE),
            headless=False,
            viewport={"width": 1100, "height": 650},
            bypass_csp=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1160,760",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        pages = list(self._ctx.pages)
        self._page = pages[0] if pages else self._ctx.new_page()
        self._page.goto(_URL, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(1500)
        self._ensure_composer_visible(self._page)
        return self._page

    def _bring_front(self, page):
        try:
            page.bring_to_front()
        except Exception:
            pass

    def _ensure_composer_visible(self, page):
        """UI helper only. WA-JS remains the source of truth."""
        try:
            page.set_viewport_size({"width": 1100, "height": 650})
        except Exception:
            pass

        try:
            return bool(page.evaluate("""() => {
                const main = document.querySelector("#main");
                if (!main) return false;

                const nodes = [...main.querySelectorAll("div")];
                for (const el of nodes) {
                    if (el.scrollHeight > el.clientHeight + 120 && el.clientHeight > 160) {
                        el.scrollTop = el.scrollHeight;
                    }
                }

                const candidates = [
                    main.querySelector('footer [contenteditable="true"]'),
                    main.querySelector('[role="textbox"][contenteditable="true"]'),
                    main.querySelector('[contenteditable="true"][data-tab]'),
                    main.querySelector('footer')
                ].filter(Boolean);

                const target = candidates[0];
                if (!target) return false;

                try { target.scrollIntoView({block: "end", inline: "nearest"}); } catch (e) {}
                try {
                    if (target.getAttribute && target.getAttribute("contenteditable") === "true") {
                        target.focus({preventScroll: true});
                    }
                } catch (e) {}

                const r = target.getBoundingClientRect();
                return r.bottom > 0 && r.top < window.innerHeight &&
                       r.left < window.innerWidth && r.right > 0;
            }"""))
        except Exception:
            return False

    def _inject_wajs(self, page, force=False):
        try:
            ready = page.evaluate("() => !!(window.WPP && WPP.isReady)")
            if ready and not force:
                return True
        except Exception:
            pass

        try:
            page.add_script_tag(path=str(_WAJS))
        except Exception:
            try:
                bundle = _WAJS.read_text(encoding="utf-8", errors="ignore")
                page.evaluate(bundle)
            except Exception as e:
                raise RuntimeError(f"No pude inyectar WA-JS: {e}")

        try:
            page.wait_for_function(
                "() => !!(window.WPP && (WPP.isReady || WPP.isInjected))",
                timeout=30000,
            )
            return True
        except Exception:
            try:
                page.wait_for_function(
                    "() => !!(window.WPP && WPP.chat && WPP.contact && WPP.conn)",
                    timeout=15000,
                )
                return True
            except Exception:
                return False

    def _ready(self, page):
        if not self._inject_wajs(page):
            return False
        try:
            return bool(page.evaluate("""async () => {
                try { return !!(await WPP.conn.isAuthenticated()); }
                catch (e) { return false; }
            }"""))
        except Exception:
            return False

    def _ensure_ready(self, page):
        if self._ready(page):
            return True
        self._bring_front(page)
        for _ in range(10):
            page.wait_for_timeout(1000)
            if self._ready(page):
                return True
        return False

    def _chat_rows(self, page, count=200):
        return page.evaluate("""async (count) => {
            const safe = (x) => { try { return String(x ?? ""); } catch(e) { return ""; } };
            const ser = (id) => { try { return safe(id?._serialized || id); } catch(e) { return safe(id); } };
            const chats = await WPP.chat.list({count});
            return (chats || []).map(c => {
              const contact = c?.contact || {};
              const id = ser(c?.id);
              const names = [
                c?.name, c?.formattedTitle, c?.title,
                contact?.name, contact?.pushname, contact?.formattedName,
                contact?.shortName, contact?.verifiedName, contact?.number
              ].map(safe).filter(Boolean);
              return {
                id,
                name: names[0] || id,
                aliases: [...new Set(names)],
                unread: Number(c?.unreadCount || 0),
                timestamp: Number(c?.t || c?.timestamp || 0),
                isGroup: !!c?.isGroup
              };
            }).filter(x => x.id);
        }""", count)

    def _contact_rows(self, page):
        return page.evaluate("""async () => {
            const safe = (x) => { try { return String(x ?? ""); } catch(e) { return ""; } };
            const ser = (id) => { try { return safe(id?._serialized || id); } catch(e) { return safe(id); } };
            const contacts = await WPP.contact.list({onlyMyContacts: true});
            return (contacts || []).map(c => {
              const id = ser(c?.id);
              const names = [
                c?.name, c?.pushname, c?.formattedName, c?.shortName,
                c?.verifiedName, c?.number
              ].map(safe).filter(Boolean);
              return {
                id,
                name: names[0] || id,
                aliases: [...new Set(names)]
              };
            }).filter(x => x.id);
        }""")

    def _resolve_chat(self, page, wanted):
        wanted = str(wanted or "").strip()
        if not wanted:
            return None, []

        rows = self._chat_rows(page, 250)
        try:
            known = self._contact_rows(page)
        except Exception:
            known = []

        by_id = {r["id"]: r for r in rows}
        for c in known:
            if c["id"] not in by_id:
                rows.append({
                    "id": c["id"], "name": c["name"],
                    "aliases": c["aliases"], "unread": 0, "timestamp": 0
                })
            else:
                merged = list(dict.fromkeys(
                    (by_id[c["id"]].get("aliases") or []) + (c.get("aliases") or [])
                ))
                by_id[c["id"]]["aliases"] = merged

        ranked = []
        for row in rows:
            aliases = row.get("aliases") or [row.get("name", "")]
            scored = [(_score(wanted, x), x) for x in aliases]
            scored.append((_score(wanted, row.get("name", "")), row.get("name", "")))
            s, alias = max(scored, key=lambda x: x[0])
            if s >= 0.50:
                ranked.append((s, row, alias))

        # Evita que el mismo chat aparezca duplicado por chat.list/contact.list.
        dedup = {}
        for s, row, alias in ranked:
            rid = str(row.get("id") or "")
            prev = dedup.get(rid)
            if prev is None or s > prev[0]:
                dedup[rid] = (s, row, alias)

        ranked = list(dedup.values())
        ranked.sort(key=lambda x: (x[0], x[1].get("timestamp", 0)), reverse=True)

        candidates = [
            {
                "name": r.get("name"),
                "id": r.get("id"),
                "score": round(s, 3),
                "matched_alias": alias,
            }
            for s, r, alias in ranked[:5]
        ]
        if not ranked:
            return None, []

        best_score, best, best_alias = ranked[0]
        multi = len(_tokens(wanted)) >= 2
        threshold = 0.82 if multi else 0.76
        if best_score < threshold:
            return None, candidates

        if len(ranked) > 1:
            second = ranked[1][0]
            margin = 0.10 if multi else 0.08
            if (best_score - second) < margin:
                return None, candidates

        best = dict(best)
        best["match_score"] = round(best_score, 3)
        best["matched_alias"] = best_alias
        return best, candidates

    def _remember_candidates(self, query, candidates):
        """Guarda la última lista ambigua para poder decir 'el segundo'."""
        clean = []
        seen = set()
        for item in list(candidates or [])[:5]:
            c = dict(item or {})
            cid = str(c.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)

            raw = cid.split("@", 1)[0]
            if cid.endswith("@c.us") or cid.endswith("@s.whatsapp.net"):
                hint = ("••••" + raw[-4:]) if len(raw) >= 4 else raw
                kind = "contact"
            elif cid.endswith("@g.us"):
                hint = "grupo"
                kind = "group"
            else:
                hint = "chat"
                kind = "chat"

            c["option"] = len(clean) + 1
            c["id_hint"] = hint
            c["kind"] = kind
            clean.append(c)

        self._last_candidates = clean
        self._last_candidate_query = str(query or "").strip()
        self._last_candidates_at = time.monotonic()
        return clean

    def _clear_candidates(self):
        self._last_candidates = []
        self._last_candidate_query = ""
        self._last_candidates_at = 0.0
        self._pending_operation = None

    def _candidate_choice(self, candidate_id="", candidate_index=0):
        """Selecciona solo desde la última lista ambigua, nunca por adivinanza."""
        cached = list(self._last_candidates or [])
        age = time.monotonic() - float(self._last_candidates_at or 0.0)

        if not cached:
            return None, "No hay una lista de coincidencias pendiente."
        # Valid only on [created, created + 300 seconds).
        if age >= 300:
            self._clear_candidates()
            return None, "La lista de coincidencias expiró; vuelve a pedir el contacto."

        cid = str(candidate_id or "").strip()
        if candidate_index and (type(candidate_index) is not int or candidate_index < 1):
            return None, "candidate_index debe ser un entero positivo."
        if cid and candidate_index:
            match = next((c for c in cached if c.get("option") == candidate_index), None)
            if not match or str(match.get("id")) != cid:
                return None, "candidate_id y candidate_index se contradicen."
        if cid:
            for c in cached:
                if str(c.get("id") or "") == cid:
                    return dict(c), ""
            return None, "Ese ID no pertenece a la última lista de coincidencias."

        try:
            idx = int(candidate_index or 0)
        except Exception:
            idx = 0

        if idx:
            for c in cached:
                if int(c.get("option") or 0) == idx:
                    return dict(c), ""
            return None, f"La opción {idx} no existe en la última lista."

        return None, "Falta candidate_index o candidate_id."

    def _resolve_and_open(self, page, wanted="", candidate_id="", candidate_index=0):
        """Resuelve por nombre o por opción exacta de la última lista ambigua."""
        using_choice = bool(str(candidate_id or "").strip() or int(candidate_index or 0))

        if using_choice:
            best, err = self._candidate_choice(candidate_id, candidate_index)
            candidates = list(self._last_candidates or [])
            if not best:
                return None, candidates, {
                    "ok": False,
                    "reason": err,
                    "query": self._last_candidate_query,
                    "candidates": candidates,
                    "selection_supported": True,
                }
        else:
            best, candidates = self._resolve_chat(page, wanted)
            if not best:
                numbered = self._remember_candidates(wanted, candidates)
                return None, numbered, {
                    "ok": False,
                    "reason": "Hay varias coincidencias o ninguna es suficientemente clara. Elige una opción; no repetiré la búsqueda por el mismo nombre.",
                    "query": wanted,
                    "candidates": numbered,
                    "selection_supported": bool(numbered),
                    "selection_instruction": "Usa candidate_index=1,2,3... o candidate_id de ESTA lista.",
                }

        opened = self._open_id(page, best["id"], best.get("name", wanted))
        if not opened.get("ok"):
            return None, list(self._last_candidates or candidates), {
                "ok": False,
                "reason": "WhatsApp abrió un chat distinto o no confirmó el destinatario solicitado.",
                "query": wanted or self._last_candidate_query,
                "expected": {"id": best.get("id"), "name": best.get("name")},
                "active": opened.get("active"),
                "candidates": list(self._last_candidates or candidates),
            }

        active = self._active_chat(page)
        if not active or active.get("id") != best.get("id"):
            return None, list(self._last_candidates or candidates), {
                "ok": False,
                "reason": "Verificación final de destinatario falló; envío/lectura bloqueados.",
                "query": wanted or self._last_candidate_query,
                "expected": {"id": best.get("id"), "name": best.get("name")},
                "active": active,
                "candidates": list(self._last_candidates or candidates),
            }

        selected = dict(best)
        if "match_score" not in selected and selected.get("score") is not None:
            selected["match_score"] = selected.get("score")

        self._clear_candidates()
        return selected, candidates, {
            "ok": True,
            "active": active,
            "selected_by": "candidate" if using_choice else "name",
        }

    def _active_chat(self, page):
        try:
            obj = page.evaluate("""() => {
                const c = WPP.chat.getActiveChat();
                if (!c) return null;
                const safe = (x) => { try { return String(x ?? ""); } catch(e) { return ""; } };
                const ser = (id) => { try { return safe(id?._serialized || id); } catch(e) { return safe(id); } };
                const contact = c?.contact || {};
                const names = [
                  c?.name, c?.formattedTitle, c?.title,
                  contact?.name, contact?.pushname, contact?.formattedName,
                  contact?.shortName, contact?.number
                ].map(safe).filter(Boolean);
                return {id: ser(c?.id), name: names[0] || ser(c?.id)};
            }""")
            if obj and obj.get("id"):
                self._active_chat_id = obj["id"]
                self._active_chat_name = obj.get("name") or obj["id"]
                return obj
        except Exception:
            pass
        return None

    def _open_id(self, page, chat_id, expected_name=""):
        result = page.evaluate("""async (id) => {
            try {
              // "Chatlist" is the documented entry point for an existing chat.
              let ok = await WPP.chat.openChatBottom(id, "Chatlist");
              // A second bottom-open is intentional: it makes WhatsApp move the
              // conversation viewport to the bottom even when it restored an
              // older scroll position.
              await new Promise(r => setTimeout(r, 120));
              ok = (await WPP.chat.openChatBottom(id)) || ok;
              return {called: true, result: !!ok};
            } catch (e) {
              return {called: false, error: String(e?.message || e)};
            }
        }""", chat_id)
        page.wait_for_timeout(450)

        # UI-only fallback: make the bottom composer visible after opening a chat.
        # This does not decide which chat is active; WA-JS still verifies that.
        self._ensure_composer_visible(page)
        page.wait_for_timeout(120)
        self._ensure_composer_visible(page)

        active = self._active_chat(page)
        if active and active.get("id") == chat_id:
            self._active_chat_id = chat_id
            self._active_chat_name = active.get("name") or expected_name or chat_id
            return {
                "ok": True,
                "chat": self._active_chat_name,
                "id": chat_id,
                "verification": "WPP.chat.getActiveChat",
                "open_call": result,
            }
        return {
            "ok": False,
            "chat": expected_name or chat_id,
            "id": chat_id,
            "active": active,
            "open_call": result,
            "reason": "WA-JS no confirmó ese chat como activo.",
        }

    def _messages(self, page, chat_id, count=20):
        return page.evaluate("""async ({id, count}) => {
            const safe = (x) => { try { return String(x ?? ""); } catch(e) { return ""; } };
            const ser = (x) => { try { return safe(x?._serialized || x); } catch(e) { return safe(x); } };
            const msgs = await WPP.chat.getMessages(id, {count});
            return (msgs || []).map(m => {
                const serializedId = ser(m?.id);
                const idFromMe = (typeof m?.id?.fromMe === "boolean") ? m.id.fromMe : null;
                const explicitFromMe = (typeof m?.fromMe === "boolean") ? m.fromMe : null;
                const serializedFromMe = serializedId.startsWith("true_");
                const fromMe = explicitFromMe !== null
                    ? explicitFromMe
                    : (idFromMe !== null ? idFromMe : serializedFromMe);

                return {
                    id: serializedId,
                    text: safe(m?.body || m?.text || m?.caption || ""),
                    fromMe: !!fromMe,
                    direction: fromMe ? "outgoing" : "incoming",
                    from: ser(m?.from),
                    to: ser(m?.to),
                    timestamp: Number(m?.t || m?.timestamp || 0),
                    type: safe(m?.type || ""),
                    mimetype: safe(m?.mimetype || m?.mediaData?.mimetype || ""),
                    duration: Number(m?.duration || m?.mediaData?.duration || 0),
                    isMedia: !!(m?.isMedia || m?.mediaData || m?.mimetype)
                };
            })
            .filter(m => m.text || m.type)
            .sort((a, b) => a.timestamp - b.timestamp);
        }""", {"id": chat_id, "count": int(count)})

    def _is_audio_message(self, msg):
        typ = _norm((msg or {}).get("type", ""))
        mime = _norm((msg or {}).get("mimetype", ""))
        return typ in {"ptt", "audio", "voice"} or mime.startswith("audio/")

    def _last_audio(self, page, chat_id, incoming_only=True):
        msgs = self._messages(page, chat_id, 80)
        audio = [m for m in msgs if self._is_audio_message(m)]
        if incoming_only:
            audio = [m for m in audio if not m.get("fromMe")]
        return audio[-1] if audio else None

    def _play_audio_message(self, page, message_id):
        """Play a WhatsApp media message inside Chromium using the downloaded Blob."""
        return page.evaluate("""async (id) => {
            try {
                if (window.__kiraWhatsAppAudio) {
                    try { window.__kiraWhatsAppAudio.pause(); } catch (e) {}
                    try {
                        if (window.__kiraWhatsAppAudioUrl) URL.revokeObjectURL(window.__kiraWhatsAppAudioUrl);
                    } catch (e) {}
                }
                const blob = await WPP.chat.downloadMedia(id);
                if (!blob) return {ok:false, error:'WA-JS no devolvió el audio.'};
                const url = URL.createObjectURL(blob);
                const audio = new Audio(url);
                window.__kiraWhatsAppAudio = audio;
                window.__kiraWhatsAppAudioUrl = url;
                audio.preload = 'auto';
                audio.volume = 1.0;
                audio.addEventListener('ended', () => {
                    try { URL.revokeObjectURL(url); } catch (e) {}
                    if (window.__kiraWhatsAppAudio === audio) {
                        window.__kiraWhatsAppAudio = null;
                        window.__kiraWhatsAppAudioUrl = null;
                    }
                }, {once:true});
                await audio.play();
                return {
                    ok:true,
                    messageId:id,
                    mime: String(blob.type || ''),
                    size: Number(blob.size || 0),
                    duration: Number.isFinite(audio.duration) ? audio.duration : 0
                };
            } catch (e) {
                return {ok:false, error:String(e?.message || e)};
            }
        }""", message_id)

    def _stop_audio(self, page):
        try:
            return page.evaluate("""() => {
                const audio = window.__kiraWhatsAppAudio;
                if (!audio) return {ok:true, stopped:false};
                try { audio.pause(); audio.currentTime = 0; } catch (e) {}
                try {
                    if (window.__kiraWhatsAppAudioUrl) URL.revokeObjectURL(window.__kiraWhatsAppAudioUrl);
                } catch (e) {}
                window.__kiraWhatsAppAudio = null;
                window.__kiraWhatsAppAudioUrl = null;
                return {ok:true, stopped:true};
            }""")
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _download_media_payload(self, page, message_id):
        return page.evaluate("""async (id) => {
            try {
                const blob = await WPP.chat.downloadMedia(id);
                if (!blob) return {ok:false, error:'WA-JS no devolvió el archivo.'};
                const size = Number(blob.size || 0);
                if (size > 24 * 1024 * 1024) return {ok:false, tooLarge:true, size, mime:String(blob.type || '')};
                const bytes = new Uint8Array(await blob.arrayBuffer());
                let binary = '';
                const step = 0x8000;
                for (let i = 0; i < bytes.length; i += step) {
                    binary += String.fromCharCode(...bytes.subarray(i, i + step));
                }
                return {ok:true, size, mime:String(blob.type || 'audio/ogg'), data:btoa(binary)};
            } catch (e) {
                return {ok:false, error:String(e?.message || e)};
            }
        }""", message_id)

    def _groq_key(self):
        key = os.getenv("GROQ_API_KEY", "").strip()
        if key:
            return key
        try:
            cfg_path = Path.home() / "Mark-LIII" / "config" / "providers.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            g = (cfg.get("providers") or {}).get("groq") or {}
            if not g.get("enabled"):
                return ""
            return str(g.get("api_key") or "").strip()
        except Exception:
            return ""

    def _transcribe_audio_payload(self, payload):
        if not payload or not payload.get("ok"):
            if payload and payload.get("tooLarge"):
                return {"ok": False, "error": "La nota supera el límite seguro de 24 MB para transcripción directa.", "size": payload.get("size", 0)}
            return {"ok": False, "error": (payload or {}).get("error", "No pude descargar el audio.")}

        key = self._groq_key()
        if not key:
            return {"ok": False, "unavailable": True, "error": "Groq no está configurado para transcribir audio."}

        try:
            raw = base64.b64decode(payload.get("data") or "", validate=True)
        except Exception as e:
            return {"ok": False, "error": f"Audio inválido: {e}"}

        if not raw:
            return {"ok": False, "error": "El audio descargado está vacío."}
        if len(raw) > 24 * 1024 * 1024:
            return {"ok": False, "error": "La nota supera 24 MB."}

        mime = str(payload.get("mime") or "audio/ogg").split(";")[0].strip().lower()
        ext_by_mime = {
            "audio/ogg": "ogg", "audio/opus": "ogg", "audio/webm": "webm",
            "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a",
            "audio/x-m4a": "m4a", "audio/wav": "wav", "audio/x-wav": "wav", "audio/flac": "flac",
        }
        ext = ext_by_mime.get(mime, "ogg")
        boundary = "----KIRASTT" + uuid.uuid4().hex
        crlf = b"\r\n"
        body = bytearray()

        def field(name, value):
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8")); body.extend(crlf)

        field("model", "whisper-large-v3-turbo")
        field("response_format", "json")
        field("temperature", "0")
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="file"; filename="kira_whatsapp_audio.{ext}"\r\n'.encode())
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
        body.extend(raw); body.extend(crlf); body.extend(f"--{boundary}--\r\n".encode())

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            data=bytes(body),
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "Accept": "application/json",
                "User-Agent": "KIRA/WhatsApp-STT",
            }, method="POST")
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=75, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            transcript = str(data.get("text") or "").strip()
            if not transcript:
                return {"ok": False, "error": "Groq respondió sin texto."}
            try:
                from core.api_usage import record
                record("groq")
            except Exception:
                pass
            return {"ok": True, "text": transcript, "model": "whisper-large-v3-turbo", "mime": mime, "size": len(raw)}
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = ""
            if e.code == 429:
                return {"ok": False, "quota": True, "error": "Groq alcanzó temporalmente su límite de transcripción. Intenta luego."}
            return {"ok": False, "error": f"Groq STT HTTP {e.code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": f"Groq STT: {type(e).__name__}: {e}"}

    def _send(self, page, chat_id, text):
        now = time.time()
        last_id, last_text, last_ts = self._last_send
        if chat_id == last_id and text == last_text and now - last_ts < 10:
            return {"ok": True, "duplicate_suppressed": True, "verified": False, "verification": "duplicate_guard_unconfirmed"}

        result = page.evaluate("""async ({id, text}) => {
            try {
              const r = await WPP.chat.sendTextMessage(id, text);
              const safe = (x) => { try { return String(x ?? ""); } catch(e) { return ""; } };
              const msgId = safe(r?.id?._serialized || r?.id || r?.key?.id || "");
              return {ok: true, messageId: msgId};
            } catch (e) {
              return {ok: false, error: String(e?.message || e)};
            }
        }""", {"id": chat_id, "text": text})

        if result and result.get("ok"):
            self._last_send = (chat_id, text, now)
            try:
                page.wait_for_timeout(350)
                msgs = self._messages(page, chat_id, 12)
                for m in reversed(msgs):
                    if (result.get("messageId") and m.get("id") == result["messageId"]
                            and m.get("fromMe") and (m.get("text") or "").strip() == text.strip()):
                        result["verified"] = True
                        result["verification"] = "WPP.chat.getMessages"
                        return result
            except Exception:
                pass
            result["verified"] = False
            result["verification"] = "sendTextMessage_resolved"
            return result
        return result or {"ok": False, "error": "sendTextMessage no devolvió resultado."}

    def _dispatch(self, p):
        action = _norm(p.get("action", "open")).replace(" ", "_")
        chat = str(p.get("chat", "") or "").strip()
        message = str(p.get("message", "") or "").strip()
        candidate_id = str(p.get("candidate_id", "") or "").strip()
        candidate_index = p.get("candidate_index", 0)
        direction = _norm(p.get("direction", "all"))
        try:
            limit = max(1, min(int(p.get("limit", 10) or 10), 30))
        except Exception:
            limit = 10

        if action in {"write","escribir","escribe","message","mensaje","mandar","manda",
                      "enviar","enviar_mensaje","send_message","reply","responder","responde","dile"}:
            action = "send"
        elif action in {"abrir_chat","open_chat","seleccionar","select_candidate","choose","elegir","escoger"}:
            action = "select"
        elif action in {"buscar_chat","find","buscar"}:
            action = "search"
        elif action in {"last_incoming","ultimo_recibido","ultimo_de_esa_persona","su_ultimo_mensaje"}:
            action = "last_incoming"
        elif action in {"last_outgoing","ultimo_enviado","mi_ultimo_mensaje"}:
            action = "last_outgoing"
        elif action in {"last_audio","ultimo_audio","ultima_nota","nota_de_voz"}:
            action = "last_audio"
        elif action in {"play_audio","reproducir_audio","pon_audio","escuchar_audio","reproduce_audio"}:
            action = "play_audio"
        elif action in {"transcribe_audio","transcribir_audio","transcribe_nota","que_dijo_audio","que_dice_audio"}:
            action = "transcribe_audio"
        elif action in {"stop_audio","parar_audio","detener_audio","para_audio"}:
            action = "stop_audio"
        elif action in {"close","cerrar","salir","return","volver","back","regresar"}:
            action = "close"

        if action == "close":
            self._clear_candidates()
            # Close ONLY the dedicated WhatsApp Playwright window/context.
            # Never call the generic application/window closer here.
            try:
                if self._ctx is not None:
                    self._ctx.close()
            except Exception:
                pass
            self._page = None
            self._ctx = None
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:
                pass
            self._pw = None
            return _dump("WHATSAPP_VERIFIED_CLOSED", {
                "closed": True,
                "target": "WhatsApp Web Chromium",
                "kira_remains_running": True
            })

        # Validate explicit selection before any browser access or active-chat fallback.
        choosing = "candidate_index" in p or "candidate_id" in p
        if "candidate_index" in p and (type(candidate_index) is not int or not 1 <= candidate_index <= 5):
            return "WHATSAPP_UNVERIFIED: candidate_index debe ser un entero entre 1 y 5."
        if "candidate_id" in p and (not isinstance(p["candidate_id"], str) or not candidate_id):
            return "WHATSAPP_UNVERIFIED: candidate_id inválido."
        if choosing:
            selected, error = self._candidate_choice(candidate_id, candidate_index)
            pending = self._pending_operation
            if not selected or not pending:
                return "WHATSAPP_UNVERIFIED: " + (error or "No hay operación pendiente.")
            if action not in {"select", pending["action"]}:
                return "WHATSAPP_UNVERIFIED: La acción contradice la operación pendiente."
            if chat and _norm(chat) not in {_norm(pending["chat"]), _norm(selected.get("name", ""))}:
                return "WHATSAPP_UNVERIFIED: El destinatario contradice la selección."
            if "message" in p and message != pending["message"]:
                return "WHATSAPP_UNVERIFIED: El mensaje contradice la operación pendiente."
            if "direction" in p and direction != pending["direction"]:
                return "WHATSAPP_UNVERIFIED: La dirección contradice la operación pendiente."
            if "limit" in p and limit != pending["limit"]:
                return "WHATSAPP_UNVERIFIED: El límite contradice la operación pendiente."
            action = "select" if pending["action"] == "search" else pending["action"]
            chat, message = pending["chat"], pending["message"]
            direction, limit = pending["direction"], pending["limit"]
        elif chat:
            # A new named operation invalidates the old list, even if lookup fails.
            self._clear_candidates()
            self._pending_operation = dict(action=action, chat=chat, message=message,
                                           direction=direction, limit=limit)
        elif self._last_candidates and action in {"send", "read", "select", "last_incoming",
                                                  "last_outgoing", "last_audio", "play_audio", "transcribe_audio"}:
            return "WHATSAPP_UNVERIFIED: Hay candidatos pendientes; selecciona una opción explícita."

        page = self._ensure_page()
        self._bring_front(page)

        if action in {"open","abrir","show","mostrar"}:
            if self._ensure_ready(page):
                return _dump("WHATSAPP_VERIFIED_OPEN", {
                    "open": True, "backend": "WA-JS", "logged_in": True, "url": page.url
                })
            return _dump("WHATSAPP_LOGIN_REQUIRED", {
                "open": True, "backend": "WA-JS", "logged_in": False, "url": page.url
            })

        if not self._ensure_ready(page):
            return "WHATSAPP_LOGIN_REQUIRED: WhatsApp Web está abierto, pero WA-JS aún no confirmó una sesión autenticada."

        if action in {"status","estado","is_open","esta_abierto"}:
            active = self._active_chat(page)
            return _dump("WHATSAPP_VERIFIED_STATUS", {
                "open": True, "backend": "WA-JS", "authenticated": True, "active_chat": active
            })

        if action == "stop_audio":
            stopped = self._stop_audio(page)
            return _dump("WHATSAPP_AUDIO_STOPPED", stopped)

        if action in {"last_audio", "play_audio", "transcribe_audio"}:
            if chat or candidate_id or candidate_index:
                best, candidates, verified = self._resolve_and_open(
                    page, chat, candidate_id=candidate_id, candidate_index=candidate_index
                )
                if not best:
                    return _dump("WHATSAPP_UNVERIFIED", verified)
                chat_id = best["id"]
                chat_name = best.get("name", chat)
            else:
                active = self._active_chat(page)
                if not active:
                    return "WHATSAPP_UNVERIFIED: No hay un chat activo confirmado para buscar audio."
                chat_id = active["id"]
                chat_name = active.get("name", chat_id)

            msg = self._last_audio(page, chat_id, incoming_only=True)
            if not msg:
                return _dump("WHATSAPP_VERIFIED_NO_AUDIO", {
                    "chat": chat_name, "id": chat_id,
                    "reason": "No encontré una nota de voz/audio entrante reciente."
                })
            if action == "last_audio":
                return _dump("WHATSAPP_VERIFIED_LAST_AUDIO", {
                    "chat": chat_name, "id": chat_id, "message": msg, "backend": "WA-JS"
                })
            if action == "play_audio":
                played = self._play_audio_message(page, msg.get("id"))
                return _dump("WHATSAPP_VERIFIED_AUDIO_PLAYING" if played.get("ok") else "WHATSAPP_UNVERIFIED", {
                    "chat": chat_name, "id": chat_id, "message": msg, "playback": played, "backend": "WA-JS"
                })

            payload = self._download_media_payload(page, msg.get("id"))
            transcription = self._transcribe_audio_payload(payload)
            if transcription.get("ok"):
                return _dump("WHATSAPP_VERIFIED_AUDIO_TRANSCRIPT", {
                    "chat": chat_name, "id": chat_id, "message": msg,
                    "transcription": transcription, "backend": "WA-JS + Groq Whisper"
                })
            return _dump("WHATSAPP_AUDIO_TRANSCRIPTION_UNAVAILABLE", {
                "chat": chat_name, "id": chat_id, "message": msg,
                "transcription": transcription, "backend": "WA-JS + Groq Whisper"
            })

        if action == "search":
            if not chat:
                return "WHATSAPP_ERROR: Falta el nombre del chat."
            best, candidates = self._resolve_chat(page, chat)
            if best:
                self._clear_candidates()
                numbered = candidates
            else:
                numbered = self._remember_candidates(chat, candidates)
            return _dump("WHATSAPP_VERIFIED_SEARCH", {
                "query": chat,
                "match": best,
                "candidates": numbered,
                "selection_supported": bool(numbered and not best),
                "selection_instruction": (
                    "Si el usuario elige una opción, usa candidate_index con la misma acción pendiente; "
                    "no vuelvas a resolver el mismo nombre."
                    if numbered and not best else ""
                ),
            })

        if action == "select":
            if not chat and not candidate_id and not candidate_index:
                return "WHATSAPP_ERROR: Falta el nombre del chat o una opción pendiente."
            best, candidates, verified = self._resolve_and_open(
                page, chat, candidate_id=candidate_id, candidate_index=candidate_index
            )
            if not best:
                return _dump("WHATSAPP_UNVERIFIED", verified)
            return _dump("WHATSAPP_VERIFIED_CHAT_OPEN", {
                "ok": True,
                "query": chat,
                "chat": best.get("name", chat),
                "id": best.get("id"),
                "match_score": best.get("match_score"),
                "matched_alias": best.get("matched_alias"),
                "verification": "resolve + WPP.chat.getActiveChat exact id",
            })

        if action in {"last_incoming","last_outgoing"}:
            if chat or candidate_id or candidate_index:
                best, candidates, verified = self._resolve_and_open(
                    page, chat, candidate_id=candidate_id, candidate_index=candidate_index
                )
                if not best:
                    return _dump("WHATSAPP_UNVERIFIED", verified)
                chat_id = best["id"]
                chat_name = best.get("name", chat)
            else:
                active = self._active_chat(page)
                if not active:
                    return "WHATSAPP_UNVERIFIED: No hay un chat activo confirmado por WA-JS."
                chat_id = active["id"]
                chat_name = active.get("name", chat_id)

            msgs = self._messages(page, chat_id, 60)
            want_from_me = (action == "last_outgoing")
            filtered = [m for m in msgs if bool(m.get("fromMe")) == want_from_me]
            last_msg = filtered[-1] if filtered else None
            return _dump(
                "WHATSAPP_VERIFIED_LAST_INCOMING" if not want_from_me else "WHATSAPP_VERIFIED_LAST_OUTGOING",
                {
                    "chat": chat_name,
                    "id": chat_id,
                    "message": last_msg,
                    "backend": "WA-JS",
                    "direction": "incoming" if not want_from_me else "outgoing",
                },
            )

        if action in {"read","leer","read_chat","leer_chat"}:
            if chat or candidate_id or candidate_index:
                best, candidates, verified = self._resolve_and_open(
                    page, chat, candidate_id=candidate_id, candidate_index=candidate_index
                )
                if not best:
                    return _dump("WHATSAPP_UNVERIFIED", verified)
                chat_id = best["id"]
                chat_name = best.get("name", chat)
            else:
                active = self._active_chat(page)
                if not active:
                    return "WHATSAPP_UNVERIFIED: No hay un chat activo confirmado por WA-JS."
                chat_id = active["id"]
                chat_name = active.get("name", chat_id)

            msgs = self._messages(page, chat_id, max(limit, 20))
            if direction in {"incoming","entrantes","recibidos","contact","ella","el"}:
                msgs = [m for m in msgs if not m.get("fromMe")]
            elif direction in {"outgoing","salientes","enviados","me","mios"}:
                msgs = [m for m in msgs if m.get("fromMe")]
            return _dump("WHATSAPP_VERIFIED_MESSAGES", {
                "chat": chat_name, "id": chat_id, "messages": msgs[-limit:], "backend": "WA-JS"
            })

        if action == "send":
            if not message:
                return "WHATSAPP_ERROR: Falta el texto del mensaje."

            if chat or candidate_id or candidate_index:
                best, candidates, verified = self._resolve_and_open(
                    page, chat, candidate_id=candidate_id, candidate_index=candidate_index
                )
                if not best:
                    return _dump("WHATSAPP_UNVERIFIED", verified)
                chat_id = best["id"]
                chat_name = best.get("name", chat)
            else:
                active = self._active_chat(page)
                if not active:
                    return "WHATSAPP_UNVERIFIED: No hay un chat activo confirmado para responder."
                chat_id = active["id"]
                chat_name = active.get("name", chat_id)

            # Last millisecond guard: never send if WhatsApp's active id changed.
            active_now = self._active_chat(page)
            if not active_now or active_now.get("id") != chat_id:
                return _dump("WHATSAPP_SEND_BLOCKED_WRONG_RECIPIENT", {
                    "requested": chat or chat_name,
                    "expected": {"id": chat_id, "name": chat_name},
                    "active": active_now,
                    "reason": "El chat activo no coincide exactamente con el destinatario verificado."
                })

            sent = self._send(page, chat_id, message)
            if sent.get("ok"):
                return _dump("WHATSAPP_VERIFIED_SENT" if sent.get("verified") else "WHATSAPP_SEND_REQUESTED", {
                    "chat": chat_name, "id": chat_id, "message": message,
                    "messageId": sent.get("messageId"),
                    "verified": bool(sent.get("verified")),
                    "verification": sent.get("verification"),
                    "duplicate_suppressed": bool(sent.get("duplicate_suppressed")),
                    "backend": "WA-JS"
                })
            return _dump("WHATSAPP_UNVERIFIED", {
                "reason": sent.get("error", "WA-JS no confirmó el envío."),
                "chat": chat_name, "id": chat_id
            })

        if action in {"recent","recientes","chats","lista","last","ultimo","ultima",
                      "last_sender","ultimo_remitente","unread","no_leidos","sin_leer"}:
            rows = self._chat_rows(page, max(30, limit))
            if action in {"unread","no_leidos","sin_leer"}:
                rows = [r for r in rows if int(r.get("unread", 0) or 0) > 0]
                return _dump("WHATSAPP_VERIFIED_UNREAD", {"chats": rows[:limit], "backend": "WA-JS"})
            if action in {"last","ultimo","ultima","last_sender","ultimo_remitente"}:
                return _dump("WHATSAPP_VERIFIED_LAST", {"chat": rows[0] if rows else None, "backend": "WA-JS"})
            return _dump("WHATSAPP_VERIFIED_RECENT", {"chats": rows[:limit], "backend": "WA-JS"})

        return "WHATSAPP_ERROR: acción no reconocida."


_WORKER = _WhatsAppWorker()


def whatsapp_web(parameters: dict, response=None, player=None, session_memory=None):
    try:
        return _WORKER.call(parameters or {}, 90)
    except Exception as e:
        return f"WHATSAPP_ERROR: {type(e).__name__}: {e}"


TOOL = {
    "name": "whatsapp_web",
    "description": (
        "ÚNICA herramienta para WhatsApp Web. Backend WA-JS dentro de Chromium: "
        "resuelve chats/contactos, conoce el chat activo, abre, lee y envía sin depender "
        "de selectores DOM del encabezado o del cuadro de mensaje. "
        "Para una orden de envío usa UNA sola llamada action=send. "
        "Antes de enviar verifica el destinatario por id exacto. Ante ambigüedad espera la elección explícita del usuario; "
        "action=select con candidate_index/candidate_id continúa la operación y texto pendientes durante menos de 300 segundos. "
        "No inventes una selección ni inicies envíos sin petición del usuario. WHATSAPP_SEND_REQUESTED no confirma el envío. "
        "También puede reproducir y, solo cuando se pide, transcribir notas de voz recibidas."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "open | close | status | search | select | read | last_incoming | last_outgoing | last_audio | play_audio | transcribe_audio | stop_audio | send | recent | last | unread"},
            "chat": {"type": "STRING", "description": "Nombre/apodo del contacto o chat"},
            "candidate_index": {"type": "INTEGER", "description": "Opción 1-5 de la ÚLTIMA lista ambigua. Úsala cuando el usuario diga primero/segundo/tercero."},
            "candidate_id": {"type": "STRING", "description": "ID exacto de una opción de la ÚLTIMA lista ambigua. Nunca inventarlo."},
            "message": {"type": "STRING", "description": "Texto a enviar; solo send"},
            "direction": {"type": "STRING", "description": "all | incoming | outgoing. Para mensajes de la otra persona usa incoming; para los tuyos outgoing."},
            "limit": {"type": "INTEGER", "description": "1-30"},
        },
        "required": ["action"],
    },
    "handler": whatsapp_web,
}
