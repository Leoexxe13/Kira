"""Resource ownership, not natural-language intent parsing.

Gemini resolves intent using the tool schemas. These boundaries prevent generic
executors from claiming resources owned by a specialized capability.
"""
import re


SPECIALIZED_RESOURCES = {
    "whatsapp_web": ("whatsapp", "whatsapp web", "web.whatsapp.com"),
}


def resource_owner(value, exact=False):
    text = " ".join(str(value or "").casefold().split())
    for owner, names in SPECIALIZED_RESOURCES.items():
        for name in names:
            if (text == name if exact else re.search(
                    r"(?<!\w)" + re.escape(name) + r"(?!\w)", text)):
                return owner
    return None


def generic_target_owner(tool, parameters):
    """Inspect target fields, never message contents or a list of user phrases."""
    fields = {
        "open_app": ("app_name",),
        "browser_control": ("url", "browser", "target", "query", "text", "description"),
        "send_message": ("platform",),
    }.get(tool, ())
    for field in fields:
        value = parameters.get(field, "whatsapp" if tool == "send_message" else "")
        owner = resource_owner(value)
        if owner:
            return owner
    return None


def reserved_result(owner):
    return (f"KIRA_ROUTE_BLOCKED: Recurso reservado para {owner}. "
            "Usa esa herramienta conservando la operación completa solicitada.")
