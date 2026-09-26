import json
import time
from pathlib import Path

from memory import db

BASE_DIR = Path(__file__).resolve().parent.parent
TASK_FILE = BASE_DIR / "memory" / "kira_tasks.json"


def _get_db_path() -> Path:
    if TASK_FILE != BASE_DIR / "memory" / "kira_tasks.json":
        return TASK_FILE.parent / "kira_memory.db"
    return BASE_DIR / "memory" / "kira_memory.db"


def _load() -> list[dict]:
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        rows = db.list_tasks(include_done=True, db_path=db_path)
        return [
            {
                "id": r["id"],
                "text": r["text"],
                "done": bool(r["done"]),
                "created": r["created_at"],
                "completed": r["completed_at"] or "",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[Tasks] ⚠️ SQLite load error: {e}")
        return []


def _save(tasks: list[dict]) -> None:
    # Kept for backward compatibility if invoked directly
    if not isinstance(tasks, list):
        return
    db_path = _get_db_path()
    try:
        db.init_db(db_path)
        for t in tasks:
            t_id = t.get("id")
            text = str(t.get("text", "")).strip()
            if not text:
                continue
            done = 1 if t.get("done") else 0
            created = str(t.get("created", "")).strip() or time.strftime("%Y-%m-%d %H:%M:%S")
            completed = str(t.get("completed", "")).strip() or None
            conn = db.get_connection(db_path)
            try:
                with conn:
                    if t_id is not None:
                        conn.execute(
                            """
                            INSERT INTO tasks (id, text, done, created_at, completed_at)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                text = excluded.text,
                                done = excluded.done,
                                created_at = excluded.created_at,
                                completed_at = excluded.completed_at
                            """,
                            (int(t_id), text, done, created, completed),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO tasks (text, done, created_at, completed_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (text, done, created, completed),
                        )
            finally:
                conn.close()

        if TASK_FILE != BASE_DIR / "memory" / "kira_tasks.json":
            TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
            TASK_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Tasks] ⚠️ SQLite save error: {e}")


def kira_tasks(parameters: dict, response=None, player=None, session_memory=None) -> str:
    action = str(parameters.get("action", "list")).lower().strip()
    text = str(parameters.get("text", "")).strip()
    task_id = parameters.get("id")
    db_path = _get_db_path()
    db.init_db(db_path)
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
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db.add_task(text, created_at=now, db_path=db_path)
        if TASK_FILE != BASE_DIR / "memory" / "kira_tasks.json":
            _save(_load())
        return f"Listo. Guardé como pendiente: {text}"

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

        target_task = None
        for t in tasks:
            if int(t.get("id", 0)) == wanted:
                target_task = t
                break

        if not target_task:
            return "No encontré ese pendiente."

        if action == "complete":
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            db.complete_task(wanted, completed_at=now, db_path=db_path)
            if TASK_FILE != BASE_DIR / "memory" / "kira_tasks.json":
                _save(_load())
            return f"Marcado como hecho: {target_task.get('text','')}"
        else:
            db.delete_task(wanted, db_path=db_path)
            if TASK_FILE != BASE_DIR / "memory" / "kira_tasks.json":
                _save(_load())
            return f"Eliminé el pendiente: {target_task.get('text','')}"

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
            "action": {"type": "STRING", "description": "list | add | complete | delete"},
            "text": {"type": "STRING", "description": "Texto de la tarea o parte del texto para identificarla."},
            "id": {"type": "INTEGER", "description": "ID de la tarea cuando se conoce."}
        },
        "required": ["action"]
    },
    "handler": kira_tasks,
}
