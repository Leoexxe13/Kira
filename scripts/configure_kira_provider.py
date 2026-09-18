#!/usr/bin/env python3
"""Configure KIRA's semantic provider on the local computer.

The Gemini key is entered through getpass and is never printed or passed as a
shell argument. Existing config/api_keys.json fields are preserved.
"""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "api_keys.json"
PROVIDERS = ROOT / "config" / "providers.json"


def save_gemini_key(key: str) -> None:
    key = key.strip()
    if len(key) < 16:
        raise ValueError("La clave parece demasiado corta; no se guardó.")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except (OSError, ValueError):
        data = {}
    data["gemini_api_key"] = key
    CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        CONFIG.chmod(0o600)
    except OSError:
        pass


def configure_gemini_provider() -> None:
    """Update only Gemini's non-secret provider settings in the local file."""
    try:
        data = json.loads(PROVIDERS.read_text(encoding="utf-8")) if PROVIDERS.exists() else {}
    except (OSError, ValueError):
        data = {}
    providers = data.setdefault("providers", {})
    gemini = providers.setdefault("gemini", {})
    gemini.update({"enabled": True, "model": "gemini-2.5-flash", "timeout_seconds": 20})
    PROVIDERS.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Configura el proveedor semántico local de KIRA")
    parser.add_argument("provider", choices=["gemini"], help="proveedor a configurar")
    args = parser.parse_args(argv)
    if args.provider == "gemini":
        print("La clave se escribirá localmente en config/api_keys.json y no se mostrará.")
        key = getpass.getpass("Pega tu Gemini API key (entrada oculta): ")
        save_gemini_key(key)
        configure_gemini_provider()
        print("Gemini quedó configurado. Verifica con:")
        print("  python3 scripts/test_kira_text.py --diagnose")
        print("Si google-genai no está instalado, ejecuta:")
        print("  python3 -m pip install -r requirements.txt")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nConfiguración cancelada.", file=sys.stderr)
        raise SystemExit(130)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
