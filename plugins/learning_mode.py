"""
Learning mode plugin for JARVIS.
Create, list, run, and delete safe command shortcuts.
"""

import json
from pathlib import Path

PLUGIN_INFO = {
    "name": "learning_mode",
    "description": (
        "Create, list, run, and delete command shortcuts. "
        "Use for: 'create shortcut named X for Y', 'list shortcuts', "
        "'run shortcut X', 'delete shortcut X'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "create_shortcut | list_shortcuts | run_shortcut | delete_shortcut"
            },
            "name": {
                "type": "STRING",
                "description": "Shortcut name (for create/run/delete)"
            },
            "command": {
                "type": "STRING",
                "description": "The exact command text to store (for create)"
            }
        },
        "required": ["action"]
    }
}


def _shortcuts_file() -> Path:
    return Path(__file__).resolve().parent.parent / "memory" / "shortcuts.json"


def _load_shortcuts() -> dict:
    path = _shortcuts_file()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_shortcuts(data: dict) -> None:
    path = _shortcuts_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_safe_command(command: str) -> bool:
    """Block dangerous commands from being stored as shortcuts."""
    dangerous = [
        "restart", "shutdown", "delete", "format", "lock",
        "uninstall", "erase", "wipe", "remove all", "drop",
        "truncate", "rm -rf", "taskkill"
    ]
    lower = command.lower()
    return not any(word in lower for word in dangerous)


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()
    name = (parameters or {}).get("name", "").strip()
    command = (parameters or {}).get("command", "").strip()

    shortcuts = _load_shortcuts()

    if action == "create_shortcut":
        if not name or not command:
            return "Provide both a name and a command, sir."
        if not _is_safe_command(command):
            return "This command is not safe to store as a shortcut, sir."
        shortcuts[name.lower()] = command
        _save_shortcuts(shortcuts)
        return f"Shortcut '{name}' created successfully, sir."

    elif action == "list_shortcuts":
        if not shortcuts:
            return "No shortcuts yet, sir. Create one with 'create shortcut named X for Y'."
        lines = [f"{n}: {c}" for n, c in shortcuts.items()]
        return "Your shortcuts:\n" + "\n".join(lines)

    elif action == "run_shortcut":
        if not name:
            return "Provide a shortcut name, sir."
        stored = shortcuts.get(name.lower())
        if not stored:
            return f"No shortcut named '{name}', sir. Use list_shortcuts to see available."
        return stored

    elif action == "delete_shortcut":
        if not name:
            return "Provide a shortcut name, sir."
        if name.lower() not in shortcuts:
            return f"No shortcut named '{name}', sir."
        del shortcuts[name.lower()]
        _save_shortcuts(shortcuts)
        return f"Shortcut '{name}' deleted, sir."

    return f"Unknown action for learning_mode: {action}"