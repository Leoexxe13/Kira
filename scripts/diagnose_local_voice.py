#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    report = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "say": bool(shutil.which("say")),
        "sounddevice": False,
        "input_devices": 0,
        "webrtcvad": False,
        "whisper_binary": False,
        "whisper_model": False,
        "ready": False,
        "errors": [],
    }
    try:
        import webrtcvad  # noqa: F401
        report["webrtcvad"] = True
    except Exception as exc:
        report["errors"].append(f"webrtcvad:{type(exc).__name__}")
    try:
        import sounddevice as sd
        report["sounddevice"] = True
        devices = sd.query_devices()
        report["input_devices"] = sum(1 for item in devices if int(item.get("max_input_channels", 0)) > 0)
    except Exception as exc:
        report["errors"].append(f"sounddevice:{type(exc).__name__}")
    try:
        from core.local_voice_runtime import load_local_voice_config
        _cfg, binary, model = load_local_voice_config(ROOT)
        report["whisper_binary"] = binary.exists() and binary.is_file()
        report["whisper_model"] = model.exists() and model.is_file()
        report["binary_path"] = str(binary)
        report["model_path"] = str(model)
    except Exception as exc:
        report["errors"].append(f"config:{type(exc).__name__}")
    report["ready"] = all((
        report["platform"] == "Darwin", report["say"], report["sounddevice"],
        report["input_devices"] > 0, report["webrtcvad"], report["whisper_binary"],
        report["whisper_model"],
    ))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ready"]:
        print("\nLa voz local no está lista. Ejecuta: bash scripts/setup_local_voice_macos.sh")
        return 1
    print("\nLa voz local está lista. KIRA usará el dispatcher común para cada turno.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
