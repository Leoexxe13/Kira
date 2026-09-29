"""
actions/mac_orchestrator.py

Semantic macOS Control and Application Orchestrator.
Handles apps, windows, clipboard, and system info using macOS native APIs (AppleScript, pbcopy/pbpaste, open).
"""
import sys
import os
import subprocess
import json
import time
from core import confirm as confirm_gate

# Use psutil for system info if available
try:
    import psutil
except ImportError:
    psutil = None

_IS_MAC = (sys.platform == "darwin")

def _run_applescript(script: str) -> dict:
    """Runs AppleScript and returns a structured response handling permissions."""
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            err = res.stderr.strip()
            # Handle common macOS permission errors
            if "Not authorized to send Apple events" in err or "-1743" in err or "-54" in err:
                return {
                    "success": False,
                    "error": "Permission Denied: KIRA needs Accessibility and Automation permissions in System Settings > Privacy & Security.",
                    "raw_error": err
                }
            return {"success": False, "error": err}
        return {"success": True, "output": res.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "AppleScript execution timed out."}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── App Management ────────────────────────────────────────────────────────────

def op_list_apps() -> dict:
    script = '''
    tell application "System Events"
        set appList to name of every application process whose visible is true
        return appList
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    apps = [a.strip() for a in res["output"].split(",") if a.strip()]
    return {"action": "list_apps", "success": True, "apps": apps}

def op_get_active_app() -> dict:
    script = '''
    tell application "System Events"
        set activeApp to name of first application process whose frontmost is true
        return activeApp
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    return {"action": "get_active_app", "success": True, "active_app": res["output"]}

def op_open_app(app_name: str) -> dict:
    try:
        subprocess.run(["open", "-a", app_name], check=True, capture_output=True, timeout=10)
        time.sleep(1) # Give it time to open
        return {"action": "open_app", "success": True, "app": app_name}
    except subprocess.CalledProcessError as e:
        return {"action": "open_app", "success": False, "error": e.stderr.decode().strip() or "App not found"}

def op_close_app(app_name: str) -> dict:
    if confirm_gate.pending_title():
        return {"action": "close_app", "success": False, "error": "A confirmation is already waiting on screen."}

    def _do_close():
        script = f'tell application "{app_name}" to quit'
        res = _run_applescript(script)
        if not res["success"]: return res
        return {"action": "close_app", "success": True, "app": app_name}

    return confirm_gate.request(
        key="close_app",
        title=f"Close application {app_name}",
        detail=f"This will quit {app_name}. Any unsaved data might trigger a prompt or be lost.",
        run=_do_close
    )

def op_focus_app(app_name: str) -> dict:
    script = f'''
    tell application "{app_name}" to activate
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    return {"action": "focus_app", "success": True, "app": app_name}

def op_open_file(app_name: str, file_path: str) -> dict:
    try:
        if app_name:
            subprocess.run(["open", "-a", app_name, file_path], check=True, capture_output=True, timeout=10)
        else:
            subprocess.run(["open", file_path], check=True, capture_output=True, timeout=10)
        return {"action": "open_file", "success": True, "app": app_name or "default", "file": file_path}
    except subprocess.CalledProcessError as e:
        return {"action": "open_file", "success": False, "error": e.stderr.decode().strip()}

# ── Window Management ──────────────────────────────────────────────────────────

def op_list_windows(app_name: str) -> dict:
    script = f'''
    tell application "System Events"
        tell application process "{app_name}"
            set windowList to name of every window
            return windowList
        end tell
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    windows = [w.strip() for w in res["output"].split(",") if w.strip()]
    return {"action": "list_windows", "success": True, "app": app_name, "windows": windows}

def op_get_active_window(app_name: str) -> dict:
    script = f'''
    tell application "System Events"
        tell application process "{app_name}"
            set activeWin to name of first window
            return activeWin
        end tell
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    return {"action": "get_active_window", "success": True, "app": app_name, "active_window": res["output"]}

def op_focus_window(app_name: str, window_name: str) -> dict:
    script = f'''
    tell application "System Events"
        tell application process "{app_name}"
            set frontmost to true
            tell window "{window_name}"
                perform action "AXRaise"
            end tell
        end tell
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    return {"action": "focus_window", "success": True, "app": app_name, "window": window_name}

def op_close_window(app_name: str, window_name: str) -> dict:
    if confirm_gate.pending_title():
        return {"action": "close_window", "success": False, "error": "A confirmation is already waiting on screen."}

    def _do_close():
        script = f'''
        tell application "System Events"
            tell application process "{app_name}"
                tell window "{window_name}"
                    click button 1
                end tell
            end tell
        end tell
        '''
        res = _run_applescript(script)
        if not res["success"]: return res
        return {"action": "close_window", "success": True, "app": app_name, "window": window_name}
        
    return confirm_gate.request(
        key="close_window",
        title=f"Close window in {app_name}",
        detail=f"This will close the window '{window_name}'.",
        run=_do_close
    )

def op_minimize_window(app_name: str, window_name: str) -> dict:
    script = f'''
    tell application "System Events"
        tell application process "{app_name}"
            tell window "{window_name}"
                click button 3
            end tell
        end tell
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    return {"action": "minimize_window", "success": True, "app": app_name, "window": window_name}

def op_maximize_window(app_name: str, window_name: str) -> dict:
    script = f'''
    tell application "System Events"
        tell application process "{app_name}"
            tell window "{window_name}"
                click button 2
            end tell
        end tell
    end tell
    '''
    res = _run_applescript(script)
    if not res["success"]: return res
    return {"action": "maximize_window", "success": True, "app": app_name, "window": window_name}

# ── Clipboard ─────────────────────────────────────────────────────────────────

def op_clipboard_read() -> dict:
    try:
        res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
        return {"action": "clipboard_read", "success": True, "content": res.stdout}
    except Exception as e:
        return {"action": "clipboard_read", "success": False, "error": str(e)}

def op_clipboard_write(text: str) -> dict:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=2)
        return {"action": "clipboard_write", "success": True, "bytes_written": len(text.encode("utf-8"))}
    except Exception as e:
        return {"action": "clipboard_write", "success": False, "error": str(e)}

# ── System Info ───────────────────────────────────────────────────────────────

def op_system_info() -> dict:
    if not psutil:
        return {"action": "system_info", "success": False, "error": "psutil not installed."}
    
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    uptime_secs = time.time() - psutil.boot_time()
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)

    battery_percent = None
    battery_plugged = None
    if hasattr(psutil, "sensors_battery"):
        bat = psutil.sensors_battery()
        if bat:
            battery_percent = bat.percent
            battery_plugged = bat.power_plugged

    return {
        "action": "system_info",
        "success": True,
        "metrics": {
            "cpu_percent": round(cpu, 1),
            "ram_percent": round(ram.percent, 1),
            "ram_used_gb": round(ram.used / 1024**3, 1),
            "ram_total_gb": round(ram.total / 1024**3, 1),
            "disk_percent": round(disk.percent, 1),
            "disk_free_gb": round(disk.free / 1024**3, 1),
            "uptime": f"{uptime_h}h {uptime_m}m",
            "process_count": len(psutil.pids()),
            "battery_percent": battery_percent,
            "battery_plugged": battery_plugged
        }
    }

# ── Tool Dispatcher ──────────────────────────────────────────────────────────

def mac_orchestrator(parameters: dict, player=None, **kwargs) -> dict:
    if not _IS_MAC:
        return {"success": False, "error": "mac_orchestrator is only supported on macOS."}

    params = parameters or {}
    action = params.get("action", "").lower().strip()
    app_name = params.get("app_name", "").strip()
    window_name = params.get("window_name", "").strip()
    file_path = params.get("file_path", "").strip()
    text = params.get("text", "")

    if player:
        player.write_log(f"[MacOrchestrator] {action}")
    print(f"[MacOrchestrator] 🖥️ action={action!r} app={app_name!r} window={window_name!r}")

    # Apps
    if action == "list_apps": return op_list_apps()
    if action == "get_active_app": return op_get_active_app()
    if action == "open_app":
        if not app_name: return {"success": False, "error": "app_name required"}
        return op_open_app(app_name)
    if action == "close_app":
        if not app_name: return {"success": False, "error": "app_name required"}
        return op_close_app(app_name)
    if action == "focus_app":
        if not app_name: return {"success": False, "error": "app_name required"}
        return op_focus_app(app_name)
    if action == "open_file":
        if not file_path: return {"success": False, "error": "file_path required"}
        return op_open_file(app_name, file_path)
    
    # Windows
    if action == "list_windows":
        if not app_name: return {"success": False, "error": "app_name required"}
        return op_list_windows(app_name)
    if action == "get_active_window":
        if not app_name: return {"success": False, "error": "app_name required"}
        return op_get_active_window(app_name)
    if action == "focus_window":
        if not app_name or not window_name: return {"success": False, "error": "app_name and window_name required"}
        return op_focus_window(app_name, window_name)
    if action == "close_window":
        if not app_name or not window_name: return {"success": False, "error": "app_name and window_name required"}
        return op_close_window(app_name, window_name)
    if action == "minimize_window":
        if not app_name or not window_name: return {"success": False, "error": "app_name and window_name required"}
        return op_minimize_window(app_name, window_name)
    if action == "maximize_window":
        if not app_name or not window_name: return {"success": False, "error": "app_name and window_name required"}
        return op_maximize_window(app_name, window_name)

    # Clipboard
    if action == "clipboard_read": return op_clipboard_read()
    if action == "clipboard_write": return op_clipboard_write(text)

    # System Info
    if action == "system_info": return op_system_info()

    return {"success": False, "error": f"Unknown action: {action}"}


TOOL = {
    "name": "mac_orchestrator",
    "description": (
        "macOS Application, Window, and System Orchestrator. "
        "Allows you to list open applications, open/close/focus apps, manage specific app windows (list, close, minimize, maximize), "
        "read/write clipboard, open files with specific apps, and get structured system metrics (CPU, RAM, Battery)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list_apps | get_active_app | open_app | close_app | focus_app | open_file | list_windows | get_active_window | focus_window | close_window | minimize_window | maximize_window | clipboard_read | clipboard_write | system_info"
            },
            "app_name": {
                "type": "STRING",
                "description": "Exact name of the application process (e.g. 'Google Chrome', 'Safari'). Required for app and window operations."
            },
            "window_name": {
                "type": "STRING",
                "description": "Exact title of the window. Required for specific window operations."
            },
            "file_path": {
                "type": "STRING",
                "description": "Absolute path to the file. Required for open_file."
            },
            "text": {
                "type": "STRING",
                "description": "Text content. Required for clipboard_write."
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": mac_orchestrator,
}
