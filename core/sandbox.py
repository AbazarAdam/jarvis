"""
core/sandbox.py — Safe execution environment for generated or learned code.

This module runs small Python scripts produced by JARVIS in a controlled
subprocess. It performs a static AST safety check before execution and
enforces timeouts and output limits.

The sandbox allows safe computation, network requests, and JSON/string
processing, but it blocks destructive filesystem operations and system
command execution.

Generated code should print its result to stdout. Optional parameters can be
passed as JSON inside sys.argv[1].
"""

from __future__ import annotations

import ast
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
SANDBOX_LOG = LOGS_DIR / "sandbox.log"

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_CHARS = 20_000


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class UnsafeCodeError(Exception):
    """Raised when generated code contains forbidden operations."""


class SandboxTimeoutError(Exception):
    """Raised when sandbox execution exceeds the allowed timeout."""


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SANDBOX_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Static safety checks
# ---------------------------------------------------------------------------
FORBIDDEN_MODULES = {
    "os",
    "subprocess",
    "shutil",
    "ctypes",
    "importlib",
    "builtins",
    "winreg",
    "multiprocessing",
    "threading",
    "socket",
}

FORBIDDEN_CALLS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "system",
    "popen",
}

FORBIDDEN_ATTRIBUTES = {
    ("sys", "exit"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "Popen"),
}


def validate_code(code: str) -> tuple[bool, list[str]]:
    """
    Perform a static safety check on generated Python code.

    Returns:
        (is_safe: bool, violations: list[str])
    """
    violations: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"Syntax error: {e}"]

    class SafetyVisitor(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                module_root = alias.name.split(".")[0]
                if module_root in FORBIDDEN_MODULES:
                    violations.append(f"Forbidden import: {alias.name}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
                violations.append(f"Forbidden import from: {node.module}")
            self.generic_visit(node)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                violations.append(f"Forbidden call: {node.func.id}")

            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                root = None
                current = node.func.value
                while isinstance(current, ast.Attribute):
                    current = current.value
                if isinstance(current, ast.Name):
                    root = current.id
                if (root, attr) in FORBIDDEN_ATTRIBUTES:
                    violations.append(f"Forbidden call: {root}.{attr}")

            self.generic_visit(node)

        def visit_Name(self, node):
            if node.id in ("__file__", "__builtins__"):
                violations.append(f"Forbidden name: {node.id}")
            self.generic_visit(node)

    visitor = SafetyVisitor()
    visitor.visit(tree)

    unique_violations = list(dict.fromkeys(violations))
    return len(unique_violations) == 0, unique_violations


# ---------------------------------------------------------------------------
# Runtime execution
# ---------------------------------------------------------------------------
def run_code(
    code: str,
    timeout: int = DEFAULT_TIMEOUT,
    env: Optional[dict] = None,
    args: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Execute Python code safely in a subprocess.

    Parameters:
        code: Python source code to execute.
        timeout: seconds before the process is killed.
        env: optional environment variables to pass to the subprocess.
        args: optional list of command-line arguments to pass to the script.
              Usually JSON-encoded parameters.

    Returns:
        dict with:
            success: bool
            returncode: int | None
            stdout: str
            stderr: str
            duration: float
    """
    start = time.time()

    safe, violations = validate_code(code)
    if not safe:
        _log(f"BLOCKED unsafe code. Violations: {violations}")
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": "Unsafe code blocked:\n" + "\n".join(violations),
            "duration": round(time.time() - start, 3),
        }

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis_sandbox_", dir=LOGS_DIR))
    script_path = temp_dir / "script.py"

    try:
        script_path.write_text(code, encoding="utf-8")

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        command = [sys.executable, str(script_path)]
        if args:
            command.extend(str(a) for a in args)

        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(temp_dir),
            env=merged_env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        duration = round(time.time() - start, 3)
        stdout = (proc.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (proc.stderr or "")[:MAX_OUTPUT_CHARS]

        _log(
            f"Executed sandbox script. "
            f"returncode={proc.returncode} duration={duration}s "
            f"stdout_len={len(stdout)} stderr_len={len(stderr)}"
        )

        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration": duration,
        }

    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 3)
        _log(f"Sandbox timeout after {timeout}s")
        raise SandboxTimeoutError(f"Sandbox code timed out after {timeout} seconds.")

    except Exception as e:
        duration = round(time.time() - start, 3)
        _log(f"Sandbox execution error: {e}")
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(e),
            "duration": duration,
        }

    finally:
        try:
            script_path.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass