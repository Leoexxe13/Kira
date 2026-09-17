import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TASK_FILE = BASE_DIR / "memory" / "kira_tasks.json"

def _load():
    try:
        data = json.loads(TASK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save(tasks):
    TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASK_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

def kira_tasks(parameters: dict, response=None, player=None, session_memory=None) -> str:
    action = str(parameters.get("action", "list")).lower().strip()
    text = str(parameters.get("text", "")).strip()
    task_id = parameters.get("id")
    tasks = _load()

    if action == "list":
        pending = [t for t in tasks if not t.get("done")]
        if not pending:
            return "No tienes pendientes registrados."
        lines = [f"{i+1}. {t.get('text','')}" for i, t in enumerate(pending)]
        return "Tienes pendiente: " + "; ".join(lines)

    if action == "add":
        if not text:
            return "Necesito saber qué pendiente quieres guardar."
        next_id = max([int(t.get("id", 0)) for t in tasks] + [0]) + 1
        tasks.append({
            "id": next_id,
            "text": text,
            "done": False,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save(tasks)
        return f"Listo. Guardé como pendiente: {text}"

    if action == "update":
        if not text:
            return "No pude actualizar la tarea: falta el texto."
        for task in tasks:
            if str(task.get('id')) == str(task_id):
                task['text'] = text
                _save(tasks)
                return "Tarea actualizada: " + text
        return "No encontré ese pendiente."

    if action in ("complete", "delete"):
        try:
            wanted = int(task_id)
        except Exception:
            wanted = None

        if wanted is None and text:
            low = text.lower()
            matches = [t for t in tasks if low in str(t.get("text", "")).lower()]
            if len(matches) == 1:
                wanted = int(matches[0].get("id", 0))

        if wanted is None:
            return "No pude identificar cuál pendiente quieres modificar."

        found = False
        if action == "complete":
            result = ""
            for t in tasks:
                if int(t.get("id", 0)) == wanted:
                    t["done"] = True
                    t["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    found = True
                    result = f"Marcado como hecho: {t.get('text','')}"
                    break
        else:
            new_tasks = []
            result = ""
            for t in tasks:
                if int(t.get("id", 0)) == wanted:
                    found = True
                    result = f"Eliminé el pendiente: {t.get('text','')}"
                else:
                    new_tasks.append(t)
            tasks = new_tasks

        if not found:
            return "No encontré ese pendiente."
        _save(tasks)
        return result

    return "Acción de pendientes no reconocida."

TOOL = {
    "name": "kira_tasks",
    "description": (
        "Gestiona la lista REAL y persistente de pendientes del usuario. "
        "Úsala SIEMPRE cuando el usuario diga que tiene algo pendiente, que tiene que hacer algo, "
        "pida guardar una tarea, pregunte qué tiene pendiente o qué le falta, "
        "o quiera marcar o eliminar un pendiente. "
        "Cuando action=list, responde naturalmente y lee la respuesta en voz."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | add | update | complete | delete"},
            "text": {"type": "STRING", "description": "Texto de la tarea o parte del texto para identificarla."},
            "id": {"type": "INTEGER", "description": "ID de la tarea cuando se conoce."}
        },
        "required": ["action"]
    },
    "handler": kira_tasks,
}
