"""
core/safety.py — Central safety boundary for JARVIS.

Every tool, generated script, self‑heal action, and learned skill must pass
through this module before executing.

Safety levels:
    0  read‑only, always safe
    1  write inside JARVIS workspace
    2  write inside user Desktop / Downloads / Documents
    3  modify JARVIS project files or config
    4  system / delete / restart / shutdown

Default policy:
    - Levels 0‑2 are allowed with logging.
    - Level 3 requires explicit confirmation.
    - Level 4 requires confirmed="yes" from the user.
"""

from __future__ import annotations

import os
import sys
import platform
from pathlib import Path
from datetime import datetime
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
SAFETY_LOG = LOGS_DIR / "safety.log"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class SafetyError(Exception):
    """Raised when an action violates the safety policy."""


class PathNotAllowedError(SafetyError):
    """Raised when a filesystem path is outside allowed areas."""


class UnsafeCommandError(SafetyError):
    """Raised when a command contains forbidden operations."""


# ---------------------------------------------------------------------------
# Safe / unsafe paths
# ---------------------------------------------------------------------------
def _user_desktop() -> Path:
    """Return the real Desktop path, even if OneDrive / custom shell folder."""
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop

    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            )
            value, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            if value:
                return Path(value)
        except Exception:
            pass

    return desktop


def allowed_write_roots() -> list[Path]:
    """Return paths where JARVIS may write without triggering high risk."""
    roots = [
        BASE_DIR,
        Path.home() / "Downloads",
        Path.home() / "Documents",
        _user_desktop(),
        LOGS_DIR,
        BASE_DIR / "memory" / "skills",
    ]
    return roots


FORBIDDEN_DIRS = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("C:/Windows/System32"),
    Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows",
]


SPECIAL_READONLY_FILES = {
    BASE_DIR / "config" / "api_keys.json",
    BASE_DIR / "memory" / "long_term.json",
}


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SAFETY_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Filesystem safety
# ---------------------------------------------------------------------------
def normalise_path(path: str | Path) -> Path:
    """Return an absolute, resolved Path."""
    return Path(path).expanduser().resolve()


def classify_path(path: str | Path, action: str = "write") -> tuple[bool, int, str]:
    """
    Determine whether a path is allowed for a given action.

    Returns:
        (allowed: bool, risk_level: int, reason: str)
    """
    action = action.lower()
    p = normalise_path(path)

    # Read operations are always allowed unless path is explicitly forbidden
    if action in ("read", "list", "find", "exists", "stat"):
        if any(str(p).lower().startswith(str(f).lower()) for f in FORBIDDEN_DIRS):
            return False, 0, "Path is inside a forbidden system directory."
        return True, 0, "Read operation is allowed."

    if action in ("write", "create", "delete", "move", "rename", "copy", "extract"):
        # Special read-only files
        if p in SPECIAL_READONLY_FILES:
            return False, 3, "This file is read‑only for generated code and tools."

        # Forbid system directories
        if any(str(p).lower().startswith(str(f).lower()) for f in FORBIDDEN_DIRS):
            return False, 4, "Path is inside a forbidden system directory."

        # Check allowed write roots
        roots = allowed_write_roots()
        for root in roots:
            try:
                if str(p).lower().startswith(str(root).lower()):
                    # Deleting project files is high risk
                    if action == "delete" and str(p).lower().startswith(str(BASE_DIR).lower()):
                        return True, 3, "Deleting JARVIS project files is high risk."
                    return True, 2, "Path is within an allowed user workspace."
            except Exception:
                continue

        return False, 3, "Path is outside allowed user workspace."

    if action == "execute":
        if any(str(p).lower().startswith(str(f).lower()) for f in FORBIDDEN_DIRS):
            return False, 4, "Executables inside system directories are forbidden."
        return True, 2, "Execution is conditionally allowed."

    return False, 3, "Unknown action type."


def ensure_path_allowed(path: str | Path, action: str = "write") -> Path:
    """Raise PathNotAllowedError if the path is not allowed, otherwise return it."""
    p = normalise_path(path)
    allowed, risk, reason = classify_path(p, action)
    if not allowed:
        _log(f"BLOCKED action={action} path={p} reason={reason}")
        raise PathNotAllowedError(reason)
    if risk >= 3:
        _log(f"WARN action={action} path={p} risk={risk} reason={reason}")
    return p


# ---------------------------------------------------------------------------
# Command safety
# ---------------------------------------------------------------------------
FORBIDDEN_COMMAND_TOKENS = [
    "rm -rf",
    "rmdir /s",
    "del /f",
    "format",
    "shutdown",
    "reg delete",
    "regedit",
    "diskpart",
    "takeown",
    "icacls",
    "powershell.exe -command remove-item",
    "powershell.exe -command delete",
]


def validate_command_tokens(command: list[str] | str) -> tuple[bool, str]:
    """
    Check a command for obviously destructive system operations.

    Returns:
        (safe: bool, reason: str)
    """
    if isinstance(command, list):
        joined = " ".join(str(c) for c in command).lower()
    else:
        joined = str(command).lower()

    for token in FORBIDDEN_COMMAND_TOKENS:
        if token in joined:
            _log(f"BLOCKED command token: {token}")
            return False, f"Forbidden command pattern: {token}"

    return True, "Command looks safe."


def require_confirmation(risk_level: int, confirmed: Any = None) -> bool:
    """Return True if the action may proceed, False if confirmation is required."""
    if risk_level < 3:
        return True

    confirmed_str = str(confirmed or "").lower().strip()
    if confirmed_str in ("yes", "true", "1", "confirm"):
        _log("HIGH RISK action authorised by user.")
        return True

    return False