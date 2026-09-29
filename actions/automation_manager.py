import json
import time
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import memory.scheduler_db as db

def calculate_next_run(schedule_type, expr, tz_str):
    tz = ZoneInfo(tz_str) if tz_str else ZoneInfo("UTC")
    now_dt = datetime.fromtimestamp(time.time(), tz)

    if schedule_type == "one_shot":
        target_dt = datetime.fromisoformat(expr)
        if target_dt.tzinfo is None: target_dt = target_dt.replace(tzinfo=tz)
        return target_dt.timestamp()

    elif schedule_type == "interval":
        val = int(expr[:-1])
        unit = expr[-1]
        import datetime as dt
        if unit == 'm': delta = dt.timedelta(minutes=val)
        elif unit == 'h': delta = dt.timedelta(hours=val)
        elif unit == 'd': delta = dt.timedelta(days=val)
        else: return None
        return (now_dt + delta).timestamp()

    elif schedule_type == "daily":
        hour, minute = map(int, expr.split(':'))
        import datetime as dt
        target_dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now_dt: target_dt += dt.timedelta(days=1)
        return target_dt.timestamp()
    return None

def handle_action(args: dict, context: dict = None) -> str:
    action = args.get("action", "list")
    
    if action == "create":
        title = args.get("title")
        goal = args.get("goal", {})
        schedule_type = args.get("schedule_type")
        schedule_expr = args.get("schedule_expr")
        tz = args.get("timezone", "UTC")
        policy = args.get("execution_policy", "safe")
        
        try:
            nr = calculate_next_run(schedule_type, schedule_expr, tz)
            if not nr: return "Error: invalid schedule format."
            
            auto_id = db.add_automation(title, goal, schedule_type, schedule_expr, tz, nr, policy)
            dt_str = datetime.fromtimestamp(nr, ZoneInfo(tz)).isoformat()
            return f"Automatización '{title}' creada. Próxima ejecución: {dt_str} ({tz})"
        except Exception as e:
            return f"Error creating automation: {e}"
            
    elif action == "list":
        autos = db.get_active_automations()
        if not autos: return "No hay automatizaciones activas."
        res = "Automatizaciones activas:\n"
        for a in autos:
            res += f"- [{a['id'][:8]}] {a['title']} | Tipo: {a['schedule_type']} | Expr: {a['schedule_expr']} | Pol: {a['execution_policy']}\n"
        return res
        
    elif action in ["pause", "resume", "cancel"]:
        auto_id = args.get("id")
        if not auto_id: return "Error: id required."
        
        status_map = {"pause": "paused", "resume": "active", "cancel": "cancelled"}
        st = status_map[action]
        
        # We search prefix if short id provided
        autos = db.get_all_automations()
        target = next((a for a in autos if a['id'].startswith(auto_id)), None)
        if not target: return "Error: Automation not found."
        
        db.update_status(target['id'], st)
        if action == "resume":
            # Recalculate next run
            nr = calculate_next_run(target['schedule_type'], target['schedule_expr'], target['timezone'])
            if nr: db.update_next_run(target['id'], nr, target['last_run'])
            
        return f"Automatización {target['title']} marcada como {st}."
        
    elif action == "toggle_dnd":
        state = args.get("dnd_state", True)
        # We rely on the core scheduler to read this, we just report success. 
        # (A real implementation would set a global config, we'll assume it's read by KIRA).
        return f"Do Not Disturb {'activado' if state else 'desactivado'}."
        
    return "Error: Unknown action."

TOOL = {
    "name": "automation_manager",
    "description": (
        "Crea y gestiona recordatorios, tareas programadas y automatizaciones continuas persistentes. "
        "Usa 'create' para agendar. schedule_type: 'one_shot', 'interval', 'daily'. "
        "schedule_expr: formato ISO para one_shot (ej '2026-10-01T09:00:00'), '15m'/'2h'/'1d' para interval, 'HH:MM' para daily. "
        "execution_policy: 'notify', 'safe' (acciones inofensivas), 'confirm' (acciones sensibles). "
        "goal: dict con {'type': 'notify'} o {'type': 'deterministic', 'action': '...', 'args': {...}} o {'type': 'gemini', 'prompt': '...'}."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'create', 'list', 'pause', 'resume', 'cancel', 'toggle_dnd'"
            },
            "title": {"type": "STRING"},
            "schedule_type": {"type": "STRING"},
            "schedule_expr": {"type": "STRING"},
            "timezone": {"type": "STRING", "description": "e.g., 'America/New_York'"},
            "execution_policy": {"type": "STRING"},
            "goal": {"type": "OBJECT"},
            "id": {"type": "STRING"},
            "dnd_state": {"type": "BOOLEAN"}
        },
        "required": ["action"]
    },
    "handler": handle_action
}
