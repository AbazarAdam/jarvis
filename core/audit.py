"""
core/audit.py — Global audit logger for JARVIS.
Logs every tool invocation with timestamp, tool name, parameters, and result.
"""

from datetime import datetime
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
AUDIT_FILE = LOG_DIR / "audit.log"


def log_action(tool_name: str, parameters: dict, result: str = "", status: str = "executed") -> None:
    """Append an entry to the audit log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params_str = str(parameters)[:500]
    result_str = str(result)[:500]
    line = (
        f"[{timestamp}] tool={tool_name} status={status}\n"
        f"  params: {params_str}\n"
        f"  result: {result_str}\n"
        f"{'-'*60}\n"
    )
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(line)