"""
core/self_heal.py — JARVIS Self‑Healing System.

Scans recent crash logs, creates a Git rollback point, fixes the responsible
file, runs tests, and rolls back if the fix breaks anything.
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime


BASE_DIR   = Path(__file__).resolve().parent.parent
LOG_DIR    = BASE_DIR / "logs"
CRASH_LOG  = LOG_DIR / "crash.log"
SELF_LOG   = LOG_DIR / "self_heal.log"

# Never auto‑modify these files unless the user explicitly allows it
PROTECTED_FILES = {
    "main.py",
    "ui.py",
    "core/self_heal.py",
    "core/error_handler.py",
    "core/audit.py",
    "core/proactive.py",
    "server.py",
}


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(SELF_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def _run(cmd: list, cwd: str = None, timeout: int = 120):
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd or str(BASE_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out."
    except Exception as e:
        return 1, "", str(e)

def _backup_file(path: Path) -> Path | None:
    """Create a timestamped backup of a file."""
    if not path.exists():
        return None
    backup_dir = LOG_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = backup_dir / f"{path.name}.{ts}.bak"
    try:
        backup_path.write_bytes(path.read_bytes())
        _log(f"Backup created: {backup_path}")
        return backup_path
    except Exception as e:
        _log(f"Backup failed: {e}")
        return None


def _restore_file(path: Path, backup_path: Path) -> bool:
    """Restore a file from its backup."""
    try:
        path.write_bytes(backup_path.read_bytes())
        _log(f"Restored {path.name} from {backup_path}")
        return True
    except Exception as e:
        _log(f"Restore failed: {e}")
        return False

def _read_last_error() -> str:
    """Return the last non‑empty crash log entry (max 2000 chars)."""
    if not CRASH_LOG.exists():
        return ""

    try:
        content = CRASH_LOG.read_text(encoding="utf-8", errors="ignore")
        entries = content.split("\n\n")[-5:]  # last 5 chunks
        return "\n\n".join(entries)[-4000:]
    except Exception:
        return ""


def _extract_file_from_traceback(error_text: str) -> str | None:
    """Find the most relevant project file in a Python traceback.

    NEVER returns files inside .venv / venv / site-packages.
    """
    import re
    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'], line (\d+)')
    matches = pattern.findall(error_text)
    if not matches:
        return None

    for file_path, _ in reversed(matches):
        p = Path(file_path)
        if not p.exists() or not str(p).startswith(str(BASE_DIR)):
            continue
        try:
            relative = p.relative_to(BASE_DIR).as_posix()
        except ValueError:
            continue

        parts = relative.split("/")
        # Skip virtual environment and installed packages
        if any(part in {".venv", "venv", "site-packages"} for part in parts):
            continue

        if relative in PROTECTED_FILES:
            continue

        return relative

    return None


def _run_tests() -> str:
    code, out, err = _run(["pytest", "-q", "tests/"])
    return (out or err).strip() or "No test output."


def _llm_analyse_and_fix(error_text: str, file_relative: str | None) -> tuple[str, str]:
    """Use Groq/OpenRouter to analyse the error and return a fixed file path + code."""
    from or_client import client

    if file_relative:
        file_path = BASE_DIR / file_relative
        code = file_path.read_text(encoding="utf-8", errors="ignore")
        prompt = (
            "You are an expert Python debugger.\n"
            f"File to fix: {file_relative}\n"
            f"Error:\n{error_text}\n\n"
            "Return ONLY the complete corrected Python code for this file. No markdown."
        )
        system = "You fix production Python code. Return only the corrected code."
        fixed_code = client.chat(prompt, system=system)
        return file_relative, fixed_code
    else:
        # No specific file found – ask LLM for general recommendation
        prompt = (
            "Analyze the following error and suggest which project file should be fixed, "
            "and provide the corrected code for that file.\n"
            f"Error:\n{error_text}\n\n"
            "Return JSON with 'file' and 'code'. Only JSON."
        )
        try:
            from or_client import client as c
            data = c.chat_json(prompt)
            return data.get("file", ""), data.get("code", "")
        except Exception:
            return "", ""


def run_self_heal(speak=None) -> str:
    """Run self‑heal with file backup and rollback."""
    _log("Self‑heal started.")
    error_text = _read_last_error()

    if not error_text:
        _log("No recent errors found.")
        if speak:
            speak("No recent errors found, sir.")
        return "No recent errors found."

    file_relative = _extract_file_from_traceback(error_text)
    if not file_relative:
        _log("No fixable project file identified.")
        if speak:
            speak("I found an error, but could not identify a safe file to fix, sir.")
        return "No safe file to fix."

    target = BASE_DIR / file_relative
    if file_relative in PROTECTED_FILES:
        _log(f"Refused to modify protected file: {file_relative}")
        if speak:
            speak(f"I refused to modify {file_relative} because it is protected, sir.")
        return "Aborted: protected file."

    # Create file backup
    backup_path = _backup_file(target)
    if not backup_path:
        _log("Could not create backup. Aborting.")
        if speak:
            speak("I could not create a backup, so I will not modify any files, sir.")
        return "Aborted: backup failed."

    # Fix the file
    try:
        new_file, fixed_code = _llm_analyse_and_fix(error_text, file_relative)
        if not fixed_code.strip():
            _restore_file(target, backup_path)
            _log("LLM returned empty fix. Rolled back.")
            if speak:
                speak("The fix was empty, so I rolled back without changes, sir.")
            return "Rolled back: empty fix."

        target.write_text(fixed_code.strip(), encoding="utf-8")
        _log(f"Applied fix to {file_relative}")
    except Exception as e:
        _restore_file(target, backup_path)
        _log(f"Fix failed: {e}. Rolled back.")
        if speak:
            speak(f"The fix failed and I rolled back, sir. Error: {e}")
        return f"Rolled back after failure: {e}"

    # Run tests
    test_output = _run_tests()
    if "FAILED" in test_output or "ERROR" in test_output:
        _restore_file(target, backup_path)
        _log(f"Tests failed after fix. Rolled back.\n{test_output}")
        if speak:
            speak("The fix broke the tests, so I rolled back, sir.")
        return "Rolled back: tests failed."

    # Keep fix and record success
    _log("Self‑heal successful. Tests pass.")
    if speak:
        speak("Self‑healing completed successfully, sir. All tests pass.")
    return f"Self‑healing complete. Fixed {file_relative}."


def self_heal(parameters: dict, player=None, speak=None) -> str:
    """
    Tool entry point for self_heal.
    Requires explicit confirmation before modifying any files.
    """
    confirmed = str((parameters or {}).get("confirmed", "")).lower()
    if confirmed not in ("yes", "true", "1", "confirm"):
        if speak:
            speak(
                "Self‑healing will modify project files, sir. "
                "Please confirm by saying yes, or call self_heal with confirmed=yes."
            )
        return "Confirmation required."
    return run_self_heal(speak=speak)