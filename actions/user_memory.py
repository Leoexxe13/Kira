"""Explicit persistent-memory tool bound to the trusted dispatch owner."""
from __future__ import annotations

from memory.sqlite_memory import active_memory


def run(parameters: dict, **_context):
    memory = active_memory()
    action = str(parameters.get("action", "recall")).strip().lower()
    if action == "recall":
        rows = memory.recall(parameters.get("query", ""), limit=8)
        data = [row.as_dict() for row in rows]
        text = "\n".join(f"{row['topic']}: {row['content']}" for row in data) or "No encontré recuerdos coincidentes."
        return {"state": "verified", "verified": True, "text": text, "data": data}
    if action in {"remember", "update"}:
        row = memory.remember(
            parameters["category"], parameters["topic"], parameters["content"],
            source=parameters.get("source", "user_explicit"),
            confidence=parameters.get("confidence", 1.0),
        )
        return {"state": "verified", "verified": True, "text": "Recuerdo guardado.", "data": row.as_dict()}
    if action == "forget":
        deleted = memory.forget(parameters["category"], parameters["topic"])
        return {"state": "verified", "verified": True,
                "text": "Recuerdo olvidado." if deleted else "No encontré ese recuerdo.",
                "data": {"deleted": deleted}}
    return {"state": "failed", "verified": False, "text": "Acción de memoria no reconocida.", "error": "unsupported action"}


TOOL = {
    "name": "user_memory",
    "description": (
        "Consulta, guarda, actualiza u olvida hechos personales explícitamente confirmados. "
        "Nunca almacena credenciales y no acepta user_id."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["recall", "remember", "update", "forget"]},
            "category": {"type": "STRING", "enum": ["identity", "preferences", "projects", "relationships", "wishes", "notes"]},
            "topic": {"type": "STRING"},
            "content": {"type": "STRING"},
            "query": {"type": "STRING"},
            "source": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["action"],
    },
    "handler": run,
}
