"""
plugins/stopwatch.py — Interactive stopwatch for JARVIS.

Tracks elapsed time and can be started, checked, stopped, or reset.
The stopwatch runs safely in-memory without long-running loops.
"""

from __future__ import annotations

import threading
import time

from core.reset_controller import reset_controller


PLUGIN_INFO = {
    "name": "stopwatch",
    "description": (
        "Start, check, stop, or reset a stopwatch-style timer. "
        "Use for 'start a stopwatch', 'how long has the stopwatch been running', "
        "'stop the stopwatch', or 'reset the stopwatch'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | status | stop | reset"
            }
        },
        "required": ["action"]
    }
}


_start_time: float | None = None
_elapsed: float = 0.0
_lock = threading.Lock()


def _reset_globals():
    """Reset the stopwatch state. Called by the global reset controller."""
    global _start_time, _elapsed
    with _lock:
        _start_time = None
        _elapsed = 0.0


reset_controller.register("stopwatch", _reset_globals)

def _format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not parts:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")

    if len(parts) > 1:
        return " and ".join(parts)
    return parts[0]


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()

    global _start_time, _elapsed

    with _lock:
        if action == "start":
            if _start_time is not None:
                return "Stopwatch is already running, sir."
            _start_time = time.monotonic()
            return "Stopwatch started, sir."

        elif action == "status":
            if _start_time is None:
                return f"Stopwatch is stopped at {_format_elapsed(_elapsed)}, sir."
            current = _elapsed + (time.monotonic() - _start_time)
            return f"Stopwatch is running: {_format_elapsed(current)}, sir."

        elif action == "stop":
            if _start_time is None:
                return f"Stopwatch is already stopped at {_format_elapsed(_elapsed)}, sir."
            _elapsed += time.monotonic() - _start_time
            _start_time = None
            return f"Stopwatch stopped at {_format_elapsed(_elapsed)}, sir."

        elif action == "reset":
            _start_time = None
            _elapsed = 0.0
            return "Stopwatch reset to zero, sir."

    return "Unknown stopwatch action. Use start, status, stop, or reset."