#!/usr/bin/env python3
"""Manual text harness for KIRA stage 2.

Examples:
  python scripts/test_kira_text.py --dry-run --text "Encuentra el último PDF de Descargas"
  python scripts/test_kira_text.py --db /tmp/kira-stage2.db --interactive
  python scripts/test_kira_text.py --state --db /tmp/kira-stage2.db
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.text_dispatcher import TextDispatcher


def result_payload(result):
    return {
        "state": result.state,
        "ok": result.ok,
        "executed": bool(result.results),
        "dry_run_plan": result.state == "planned",
        "goal": result.goal,
        "needs_clarification": result.needs_clarification,
        "text": result.text,
        "plan": result.plan,
        "results": [item.as_dict() for item in result.results],
    }


def build_parser():
    parser = argparse.ArgumentParser(description="KIRA stage 2 text dispatcher harness")
    parser.add_argument("--text", help="one request to dispatch")
    parser.add_argument("--interactive", action="store_true", help="read sequential requests until EOF or exit")
    parser.add_argument("--dry-run", action="store_true", help="show the validated plan without executing tools")
    parser.add_argument("--state", action="store_true", help="print current context and provider state")
    parser.add_argument("--diagnose", action="store_true", help="check provider readiness without exposing keys")
    parser.add_argument("--probe", action="store_true", help="make one minimal provider request and report readiness")
    parser.add_argument("--db", type=Path, help="SQLite memory path")
    parser.add_argument("--principal", help="trusted local principal; never supplied by the model")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    dispatcher = TextDispatcher(
        base_dir=ROOT,
        memory_path=args.db,
        principal=args.principal,
        logger=lambda message: print(f"[runtime] {message}", file=sys.stderr),
    )
    if args.state:
        print(json.dumps(dispatcher.state(), ensure_ascii=False, indent=2, default=str))
    if args.diagnose:
        diagnosis = dispatcher.provider.diagnose() if hasattr(dispatcher.provider, "diagnose") else {"error": "provider has no diagnostics"}
        print(json.dumps(diagnosis, ensure_ascii=False, indent=2, default=str))
    if args.probe:
        if hasattr(dispatcher.provider, "interpret_request"):
            result = dispatcher.provider.interpret_request(
                "Devuelve exactamente {\"ok\":true}.", {}, [],
                "Responde únicamente con JSON válido y no llames herramientas.",
            )
            print(json.dumps({
                "ok": result.ok, "provider": result.provider, "model": result.model,
                "latency_ms": result.latency_ms, "error": result.error,
            }, ensure_ascii=False, indent=2, default=str))
        else:
            print(json.dumps({"ok": False, "error": "provider has no probe method"}, indent=2))
    if args.text:
        print(json.dumps(result_payload(dispatcher.dispatch(args.text, source="cli", dry_run=args.dry_run)), ensure_ascii=False, indent=2, default=str))
    if args.interactive or not args.text and not args.state and not args.diagnose and not args.probe:
        print("KIRA stage 2. Escribe una petición; usa 'salir' para terminar.", file=sys.stderr)
        while True:
            try:
                text = input("kira> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                break
            if text.casefold() in {"salir", "exit", "quit"}:
                break
            if not text:
                continue
            result = dispatcher.dispatch(text, source="cli", dry_run=args.dry_run)
            print(json.dumps(result_payload(result), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
