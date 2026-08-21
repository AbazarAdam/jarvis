"""
core/poc_executor.py — Safe read-only proof-of-concept execution for JARVIS.

This module executes AI-generated or attack-chain PoC commands in a controlled
way. It only allows read-only, non-interactive networking tools.

It never executes shell pipelines, redirections, or destructive commands.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from core.safety import validate_command_tokens
from core.proxy_manager import get_tool_env


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
POC_LOG = LOGS_DIR / "poc_executor.log"

ALLOWED_EXECUTABLES = {
    "curl", "curl.exe",
    "nuclei", "nuclei.exe",
    "nmap", "nmap.exe",
    "httpx", "httpx.exe",
    "subfinder", "subfinder.exe",
    "dnsx", "dnsx.exe",
    "katana", "katana.exe",
}

FORBIDDEN_SUBSTRINGS = [
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
    "--upload",
    "-T",
    "--data-binary",
    ">",
    "<",
    "|",
    "&",
    ";",
    "`",
    "$(",
]

EVIDENCE_PATTERNS = [
    "root:x:0:0",
    "200 OK",
    "403 Forbidden",
    "401 Unauthorized",
    "Vulnerable",
    "CVE-",
]


def _log(msg: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(POC_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _parse_command(command: str) -> list[str]:
    """Parse a command string into argv without shell metacharacters."""
    if sys.platform == "win32":
        return shlex.split(command, posix=False)
    return shlex.split(command)


def command_is_safe(command: str | list[str]) -> tuple[bool, str]:
    """
    Validate that a command is safe to execute as a read-only PoC.

    Returns:
        (safe: bool, reason: str)
    """
    if isinstance(command, list):
        parts = [str(c) for c in command]
        joined = " ".join(parts)
        if not parts:
            return False, "Empty command."
        exe = Path(parts[0]).name.lower()
    else:
        text = str(command).strip()
        if not text:
            return False, "Empty command."
        joined = text
        try:
            parts = _parse_command(text)
        except Exception:
            return False, "Could not parse command."
        if not parts:
            return False, "Could not parse command."
        exe = Path(parts[0]).name.lower()

    if exe not in ALLOWED_EXECUTABLES:
        return False, f"Executable not allowed: {exe}"

    for token in FORBIDDEN_SUBSTRINGS:
        if token.lower() in joined.lower():
            return False, f"Forbidden token: {token}"

    safe, reason = validate_command_tokens(joined)
    if not safe:
        return False, reason

    return True, "Command is safe for PoC execution."


def _extract_evidence(stdout: str, stderr: str) -> str:
    """Return a short evidence preview if known vulnerability markers appear."""
    combined = f"{stdout}\n{stderr}"[:2000]
    for pattern in EVIDENCE_PATTERNS:
        if pattern.lower() in combined.lower():
            return pattern
    return combined[:300]


def execute_poc(command: str | list[str], timeout: int = 20) -> dict[str, Any]:
    """
    Execute a single read-only PoC command safely.

    Returns:
        {
            "command": str,
            "safe": bool,
            "reason": str,
            "returncode": int | None,
            "stdout": str,
            "stderr": str,
            "evidence": str,
            "duration": float,
        }
    """
    start = time.time()

    safe, reason = command_is_safe(command)
    if not safe:
        _log(f"BLOCKED unsafe PoC: {command} | {reason}")
        return {
            "command": str(command),
            "safe": False,
            "reason": reason,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "evidence": "",
            "duration": round(time.time() - start, 3),
        }

    if isinstance(command, str):
        parts = _parse_command(command)
    else:
        parts = [str(c) for c in command]

    env = None
    proxy_env = get_tool_env()
    if proxy_env:
        env = os.environ.copy()
        env.update(proxy_env)

    try:
        proc = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(BASE_DIR),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        duration = round(time.time() - start, 3)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        evidence = _extract_evidence(stdout, stderr)

        _log(
            f"PoC executed. returncode={proc.returncode} "
            f"duration={duration}s stdout_len={len(stdout)} stderr_len={len(stderr)}"
        )

        return {
            "command": str(command),
            "safe": True,
            "reason": "",
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "evidence": evidence,
            "duration": duration,
        }

    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 3)
        _log(f"PoC timeout after {timeout}s: {command}")
        return {
            "command": str(command),
            "safe": True,
            "reason": "timed out",
            "returncode": None,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds.",
            "evidence": "",
            "duration": duration,
        }

    except Exception as e:
        duration = round(time.time() - start, 3)
        _log(f"PoC execution error: {e}")
        return {
            "command": str(command),
            "safe": True,
            "reason": str(e),
            "returncode": None,
            "stdout": "",
            "stderr": str(e),
            "evidence": "",
            "duration": duration,
        }


def execute_many(commands: list[str], timeout: int = 20) -> list[dict[str, Any]]:
    """Execute multiple PoC commands and return their structured results."""
    results = []
    for cmd in commands:
        results.append(execute_poc(cmd, timeout=timeout))
    return results