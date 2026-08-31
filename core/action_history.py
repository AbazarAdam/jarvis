"""
core/action_history.py — JARVIS Action History & Rollback System.

Records every file modification/deletion/creation so JARVIS can undo
changes safely. Stores original content in logs/action_history/.

This is the foundation of JARVIS self‑protection.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
HISTORY_DIR = LOGS_DIR / "action_history"
HISTORY_FILE = HISTORY_DIR / "history.json"

_lock = threading.RLock()


def _ensure_dirs() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_history() -> list[dict[str, Any]]:
    _ensure_dirs()
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_history(history: list[dict[str, Any]]) -> None:
    _ensure_dirs()
    try:
        HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except OSError:
        pass


def record_action(
    action: str,
    file_path: str,
    original_content: Optional[str] = None,
    new_content: Optional[str] = None,
    extra: Optional[dict] = None,
) -> str:
    """
    Record an action and create a backup if needed.

    Returns action_id.
    """
    action_id = uuid.uuid4().hex[:12]
    ts = datetime.now().isoformat(timespec="seconds")

    with _lock:
        history = _load_history()

        backup_path = None
        file_p = Path(file_path) if file_path else None

        if action in ("edit", "delete", "write", "create") and file_p is not None:
            if file_p.exists():
                backup_dir = HISTORY_DIR / action_id
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = str(backup_dir / file_p.name)
                shutil.copy2(file_p, backup_path)

        event = {
            "id": action_id,
            "time": ts,
            "action": action,
            "file": str(file_path),
            "original_content": original_content,
            "new_content": new_content,
            "backup_path": backup_path,
            "extra": extra or {},
        }

        history.append(event)
        # Keep only last 50 actions
        history = history[-50:]
        _save_history(history)

        return action_id


def undo_last() -> dict:
    """
    Undo the most recent action if possible.

    Returns status dict.
    """
    with _lock:
        history = _load_history()
        if not history:
            return {"success": False, "reason": "No actions to undo."}

        event = history.pop()
        _save_history(history)

        file_path = event.get("file")
        action = event.get("action")
        backup_path = event.get("backup_path")

        if not file_path or not Path(file_path).exists():
            # If the file doesn't exist now, we may need to restore from backup
            if backup_path and Path(backup_path).exists() and action in ("delete", "edit", "write", "create"):
                Path(file_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, file_path)
                return {"success": True, "message": f"Restored {file_path}"}

        if backup_path and Path(backup_path).exists():
            # Restore original from backup
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, file_path)
            return {"success": True, "message": f"Restored {file_path}"}

        return {"success": False, "reason": "No backup available to undo."}


def undo_file(file_path: str) -> dict:
    """
    Find the most recent action for a specific file and undo it.

    Returns status dict.
    """
    with _lock:
        history = _load_history()
        for i in range(len(history) - 1, -1, -1):
            event = history[i]
            if event.get("file") == file_path:
                # Remove this event and restore backup
                event = history.pop(i)
                _save_history(history)

                backup_path = event.get("backup_path")
                if backup_path and Path(backup_path).exists():
                    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, file_path)
                    return {"success": True, "message": f"Restored {file_path}"}
                else:
                    return {"success": False, "reason": f"No backup for {file_path}"}

        return {"success": False, "reason": f"No history for {file_path}"}


def list_history(limit: int = 10) -> list[dict[str, Any]]:
    with _lock:
        history = _load_history()
        return history[-limit:]


def clear_history() -> None:
    with _lock:
        _save_history([])
        # Optionally remove backup dirs
        try:
            for child in HISTORY_DIR.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
        except Exception:
            pass