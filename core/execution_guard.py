"""
core/execution_guard.py — Pre‑execution safety and conflict gate for JARVIS.

This module connects:
    - core/cortex.py       → conflict resolution + capability selection
    - core/safety.py       → path/command/risk safety checks
    - core/model_router.py → stable LLM fallback for future decisions

Before ANY tool executes, main.py should call:

    decision = preflight_tool_call(name, parameters, tool_declarations, plugin_declarations)

If `decision["allowed"]` is False, main.py must not execute the tool.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.cortex import analyse_tool_call
from core.safety import (
    ensure_path_allowed,
    validate_command_tokens,
    require_confirmation,
)
from core.model_router import ModelRouter


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
GUARD_LOG = LOGS_DIR / "execution_guard.log"


def _log(msg: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Additional tool-specific preflight
# ---------------------------------------------------------------------------
def _validate_tool_specific_paths(name: str, parameters: dict) -> tuple[bool, str]:
    """
    If a tool accepts a filesystem path, ensure it is allowed before execution.
    """
    path_keys = ("path", "file_path", "output_path", "destination")
    for key in path_keys:
        value = parameters.get(key)
        if not value or not isinstance(value, str):
            continue

        # Skip obvious shortcuts / URLs
        value_lower = value.lower()
        if value_lower in ("desktop", "downloads", "documents", "home", "."):
            continue
        if value_lower.startswith(("http://", "https://")):
            continue

        try:
            ensure_path_allowed(value, "write")
        except Exception as e:
            return False, f"Unsafe path for '{key}': {e}"

    return True, "Path checks passed."


def _validate_tool_specific_commands(name: str, parameters: dict) -> tuple[bool, str]:
    """
    If a tool accepts a command/argument list, block obvious destructive tokens.
    """
    for key in ("command", "cmd", "args", "task"):
        value = parameters.get(key)
        if value is None:
            continue

        if isinstance(value, list):
            safe, reason = validate_command_tokens(value)
        elif isinstance(value, str):
            safe, reason = validate_command_tokens(value)
        else:
            continue

        if not safe:
            return False, reason

    return True, "Command checks passed."


# ---------------------------------------------------------------------------
# Main preflight API
# ---------------------------------------------------------------------------
def preflight_tool_call(
    name: str,
    parameters: dict | None,
    tool_declarations: list[dict],
    plugin_declarations: list[dict],
    skills: list[dict] | None = None,
) -> dict:
    """
    Decide whether a tool call should execute.

    Returns:
        {
            "allowed": bool,
            "risk_level": int,
            "reason": str,
            "valid_parameters": bool,
            "parameter_errors": list[str],
        }
    """
    params = dict(parameters or {})

    # 1. Basic Cortex decision
    decision = analyse_tool_call(
        name=name,
        parameters=params,
        tool_declarations=tool_declarations,
        plugin_declarations=plugin_declarations,
        skills=skills,
    )

    if not decision.get("allowed"):
        _log(f"BLOCKED {name}: {decision.get('reason')}")
        return decision

    # 2. Tool-specific path checks
    path_ok, path_reason = _validate_tool_specific_paths(name, params)
    if not path_ok:
        _log(f"BLOCKED {name}: {path_reason}")
        return {
            "allowed": False,
            "risk_level": decision.get("risk_level", 3),
            "reason": path_reason,
            "valid_parameters": decision.get("valid_parameters", False),
            "parameter_errors": decision.get("parameter_errors", []),
        }

    # 3. Tool-specific command checks
    cmd_ok, cmd_reason = _validate_tool_specific_commands(name, params)
    if not cmd_ok:
        _log(f"BLOCKED {name}: {cmd_reason}")
        return {
            "allowed": False,
            "risk_level": decision.get("risk_level", 3),
            "reason": cmd_reason,
            "valid_parameters": decision.get("valid_parameters", False),
            "parameter_errors": decision.get("parameter_errors", []),
        }

    _log(f"ALLOWED {name} risk={decision.get('risk_level')}")
    return decision


# ---------------------------------------------------------------------------
# Skill fallback helper
# ---------------------------------------------------------------------------
def skill_fallback_decision(name: str, parameters: dict, skills: list[dict]) -> dict:
    """
    If Cortex cannot map a tool name, try to match a learned skill.

    Returns:
        {
            "use_skill": bool,
            "skill_name": str | None,
            "reason": str,
        }
    """
    skills = skills or []
    if not skills:
        return {"use_skill": False, "skill_name": None, "reason": "No learned skills."}

    name_tokens = set(str(name).lower().split("_"))
    for skill in skills:
        skill_name = str(skill.get("name", "")).lower()
        skill_tokens = set(skill_name.split("_"))
        if name_tokens & skill_tokens:
            return {
                "use_skill": True,
                "skill_name": skill.get("name"),
                "reason": "Matched learned skill by name.",
            }

    return {"use_skill": False, "skill_name": None, "reason": "No matching skill."}