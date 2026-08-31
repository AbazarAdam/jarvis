"""
plugins/undo_plugin.py — JARVIS Undo / Rollback Plugin.

Lets JARVIS undo recent file changes using core/action_history.py.
"""

from core.action_history import undo_last, undo_file, list_history, clear_history


PLUGIN_INFO = {
    "name": "undo",
    "description": (
        "Undo recent file changes or restore a file to a previous state. "
        "Use for 'undo', 'undo last change', 'restore file X', 'list recent changes'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "undo | undo_file | list | clear"},
            "file": {"type": "STRING", "description": "Optional file path for undo_file"},
            "limit": {"type": "INTEGER", "description": "Number of history entries to list (default 10)"},
        },
        "required": ["action"],
    },
}


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()
    file_path = (parameters or {}).get("file", "").strip()
    limit = int((parameters or {}).get("limit", 10))

    if action == "undo":
        result = undo_last()
        if result.get("success"):
            if speak:
                speak(result.get("message", "Undone, sir."))
            return result.get("message", "Undone.")
        return result.get("reason", "Undo failed.")

    elif action == "undo_file":
        if not file_path:
            return "Please provide a file path to restore, sir."
        result = undo_file(file_path)
        if result.get("success"):
            if speak:
                speak(result.get("message", "Restored, sir."))
            return result.get("message", "Restored.")
        return result.get("reason", "Undo failed.")

    elif action == "list":
        history = list_history(limit)
        if not history:
            return "No recent changes recorded, sir."
        lines = []
        for h in history:
            lines.append(f"- {h.get('time')} | {h.get('action')} | {h.get('file')}")
        return "Recent changes:\n" + "\n".join(lines)

    elif action == "clear":
        clear_history()
        return "Action history cleared, sir."

    return f"Unknown undo action: {action}"