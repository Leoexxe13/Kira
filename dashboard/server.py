"""
dashboard/server.py — KIRA Local Remote Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import string
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
MAX_UPLOAD_MB = 500


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"



UPLOADS_DIR = _make_uploads_dir()

# Persistent mobile pairing + local HTTPS identity.
DEVICE_STORE = BASE_DIR / "config" / "dashboard_devices.json"
CERT_DIR     = BASE_DIR / "config" / "certs"
CA_KEY       = CERT_DIR / "kira_local_ca.key"
CA_PEM       = CERT_DIR / "kira_local_ca.pem"
CA_CER       = CERT_DIR / "kira-local-ca.cer"
SERVER_KEY   = CERT_DIR / "jarvis.key"   # keep legacy filenames for compatibility
SERVER_CERT  = CERT_DIR / "jarvis.crt"
SETUP_PORT   = PORT + 1


def _device_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _load_device_store() -> dict:
    try:
        if DEVICE_STORE.exists():
            data = json.loads(DEVICE_STORE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_device_store(data: dict) -> None:
    DEVICE_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICE_STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, DEVICE_STORE)
    try:
        os.chmod(DEVICE_STORE, 0o600)
    except Exception:
        pass


def _ensure_tls_material(ip: str) -> bool:
    """Create one local CA and renew the LAN server certificate for the current IP."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

        CERT_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.utcnow()

        if CA_KEY.exists() and CA_PEM.exists():
            ca_key = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
            ca_cert = x509.load_pem_x509_certificate(CA_PEM.read_bytes())
        else:
            ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KIRA Local CA")])
            ca_cert = (
                x509.CertificateBuilder()
                .subject_name(ca_name)
                .issuer_name(ca_name)
                .public_key(ca_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True, content_commitment=False, key_encipherment=False,
                        data_encipherment=False, key_agreement=False, key_cert_sign=True,
                        crl_sign=True, encipher_only=False, decipher_only=False,
                    ),
                    critical=True,
                )
                .sign(ca_key, hashes.SHA256())
            )
            CA_KEY.write_bytes(
                ca_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
            CA_PEM.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
            try:
                os.chmod(CA_KEY, 0o600)
            except Exception:
                pass

        # iPhone profile download prefers DER .cer.
        CA_CER.write_bytes(ca_cert.public_bytes(serialization.Encoding.DER))

        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        host = socket.gethostname()
        san = [x509.DNSName(host), x509.DNSName(host + ".local")]
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except Exception:
            pass

        leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KIRA Remote")])
        leaf = (
            x509.CertificateBuilder()
            .subject_name(leaf_name)
            .issuer_name(ca_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        SERVER_KEY.write_bytes(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        SERVER_CERT.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        try:
            os.chmod(SERVER_KEY, 0o600)
        except Exception:
            pass
        return True
    except Exception as exc:
        print(f"[Dashboard] HTTPS setup unavailable: {exc}")
        return False


def _start_setup_server(ip: str) -> None:
    """Plain-HTTP one-time iPhone setup portal used only to install KIRA's local CA."""
    if not CA_CER.exists():
        return

    dashboard_url = f"https://{ip}:{PORT}"
    ca_bytes = CA_CER.read_bytes()
    html = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KIRA · Vincular iPhone</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#050505;color:#eee;
margin:0;padding:28px;line-height:1.5}}main{{max-width:620px;margin:auto}}h1{{letter-spacing:.18em}}
.card{{border:1px solid #333;border-radius:16px;padding:18px;margin:16px 0;background:#0d0d0d}}
a,button{{display:inline-block;background:#fff;color:#000;text-decoration:none;border:0;border-radius:10px;
padding:12px 16px;font-weight:700;margin:6px 4px 6px 0}}code{{color:#bbb}}ol{{padding-left:22px}}
.small{{color:#999;font-size:13px}}
</style></head><body><main>
<h1>KIRA</h1><div class="card">
<strong>Configuración única para usar el micrófono del iPhone</strong>
<ol>
<li>Toca <b>Instalar certificado KIRA</b>.</li>
<li>En iPhone: Ajustes → General → VPN y gestión de dispositivos → instala <b>KIRA Local CA</b>.</li>
<li>Luego: Ajustes → General → Información → Ajustes de confianza de certificados → activa confianza total para <b>KIRA Local CA</b>.</li>
<li>Vuelve aquí y toca <b>Abrir KIRA</b>.</li>
</ol>
<a href="/kira-local-ca.cer">Instalar certificado KIRA</a>
<a href="{dashboard_url}">Abrir KIRA</a>
<p class="small">Esto solo confía en el certificado local creado por tu Mac para KIRA. La clave privada de la CA permanece en tu Mac.</p>
</div></main></body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/kira-local-ca.cer":
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                self.send_header("Content-Disposition", 'attachment; filename="kira-local-ca.cer"')
                self.send_header("Content-Length", str(len(ca_bytes)))
                self.end_headers()
                self.wfile.write(ca_bytes)
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", SETUP_PORT), Handler)
    except OSError:
        return
    print(f"[Dashboard] iPhone first-time setup: http://{ip}:{SETUP_PORT}")
    threading.Thread(target=httpd.serve_forever, daemon=True, name="kira-mobile-setup").start()

def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"KIRA Dashboard Port {port}"
        prog_rule  = "KIRA Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until JARVIS is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._ip                          = _local_ip()
        _ensure_tls_material(self._ip)
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = _load_device_store()  # sha256(device_token) → session
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self.app                          = self._build_app()

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        return SERVER_KEY.exists() and SERVER_CERT.exists()

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """First-time mobile setup portal when HTTPS is active; dashboard URL otherwise."""
        if self._ssl_enabled():
            return f"http://{self._ip}:{SETUP_PORT}"
        return f"http://{self._ip}:{PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    def _pair_device(self, session_key: str) -> str:
        dev_tok = secrets.token_urlsafe(32)
        digest = _device_hash(dev_tok)
        self._device_sessions[digest] = {
            "session_key": session_key,
            "created_at": int(time.time()),
            "last_seen": int(time.time()),
        }
        _save_device_store(self._device_sessions)
        return dev_tok

    def _device_session(self, dev_tok: str) -> dict | None:
        digest = _device_hash(dev_tok)
        item = self._device_sessions.get(digest)
        if not isinstance(item, dict):
            return None
        item["last_seen"] = int(time.time())
        self._device_sessions[digest] = item
        _save_device_store(self._device_sessions)
        return item

    def _unpair_device(self, dev_tok: str) -> bool:
        digest = _device_hash(dev_tok)
        removed = self._device_sessions.pop(digest, None) is not None
        if removed:
            _save_device_store(self._device_sessions)
        return removed

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                session_key = secrets.token_urlsafe(32)  # long AES key; PIN is pairing only
                tok = secrets.token_urlsafe(32)
                dev_tok = self._pair_device(session_key)
                self._tokens.add(tok)
                self._token_keys[tok] = session_key
                self._aes_key(session_key)
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "KIRA Remote connected and this phone is paired."}
                ))
                return JSONResponse({
                    "ok": True, "token": tok, "key": session_key, "device_token": dev_tok
                })
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in KIRA to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            session_key = secrets.token_urlsafe(32)
            tok     = secrets.token_urlsafe(32)
            dev_tok = self._pair_device(session_key)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "KIRA Remote connected via QR code and phone paired."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{session_key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to KIRA…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            item = self._device_session(dev_tok) if dev_tok else None
            if not item:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = str(item.get("session_key") or "")
            if not session_key:
                return JSONResponse({"ok": False}, status_code=401)
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Paired iPhone reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            _save_device_store(self._device_sessions)
            return JSONResponse({"ok": True, "revoked": count})

        @app.post("/api/unpair-device")
        async def unpair_device(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                body = {}
            dev_tok = str(body.get("device_token") or "").strip()
            if not dev_tok:
                return JSONResponse({"ok": False, "error": "Missing device token"}, status_code=400)
            return JSONResponse({"ok": self._unpair_device(dev_tok)})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Legacy name retained; port 8001 is now the iPhone trust/setup portal."""
        return

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _ensure_network_access, PORT)

        use_ssl = self._ssl_enabled()
        if use_ssl:
            loop.run_in_executor(None, _ensure_network_access, SETUP_PORT)
            _start_setup_server(self._ip)

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(SERVER_KEY), "ssl_certfile": str(SERVER_CERT)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        if use_ssl:
            print(f"[Dashboard] iPhone trust/setup: http://{self._ip}:{SETUP_PORT}")
        print("[Dashboard] Press 'Remote Control' in KIRA UI to pair or reconnect.")
        await uvicorn.Server(cfg).serve()
