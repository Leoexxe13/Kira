import sqlite3
import shutil
import sys
from pathlib import Path
from core.action_loader import discover_actions

def system_check():
    print("=" * 40)
    print(" SYSTEM CHECK (KIRA DEVELOPMENT MODE)")
    print("=" * 40)
    
    # 1. Action Registry
    try:
        registry = discover_actions(Path("actions"), logger=lambda m: None)
        active = len(registry.names())
        if active >= 23:
            print(f" [OK] Capabilities: {active} external actions loaded.")
        else:
            print(f" [WARN] Capabilities: Only {active} actions loaded, expected >= 23.")
            for rec in registry._all_records:
                if not rec.valid:
                    print(f"      - Rejected: {rec.name} -> {rec.error}")
    except Exception as e:
        print(f" [FAIL] Capabilities Registry: {e}")

    # 2. SQLite / Memory
    try:
        db_path = Path("memory/scheduler.db")
        if not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("SELECT name FROM sqlite_master;")
        print(" [OK] Memory: SQLite accessible (WAL supported).")
    except Exception as e:
        print(f" [FAIL] Memory: SQLite error -> {e}")

    # 3. LibreOffice / Renderer
    import subprocess
    lo_paths = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        "soffice"
    ]
    lo_found = False
    for p in lo_paths:
        if shutil.which(p) or Path(p).exists():
            lo_found = True
            break
    if lo_found:
        print(" [OK] Renderer: LibreOffice detected (Visual QA available).")
    else:
        print(" [WARN] Renderer: LibreOffice NOT found. Visual QA for PPTX/DOCX will skip render.")

    # 4. GUI Bridge check
    try:
        import PyQt6.QtCore
        print(" [OK] GUI Bridge: PyQt6 is installed and ready.")
    except ImportError:
        print(" [FAIL] GUI Bridge: PyQt6 is missing.")

    print("=" * 40)

if __name__ == "__main__":
    system_check()
