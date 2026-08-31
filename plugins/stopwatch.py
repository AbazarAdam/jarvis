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
_lock = threading.RLock()   # Use RLock to allow nested acquisition in same thread
_ui = None


def _reset_globals():
    """Reset the stopwatch state. Called by the global reset controller."""
    global _start_time, _elapsed
    with _lock:
        _start_time = None
        _elapsed = 0.0
    _sync_ui()


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


def _sync_ui():
    """Send current stopwatch state to the UI window (if active)."""
    global _start_time, _elapsed, _ui
    if _ui is None:
        return

    with _lock:
        if _start_time is not None:
            elapsed = _elapsed + (time.monotonic() - _start_time)
            running = True
        else:
            elapsed = _elapsed
            running = False

    try:
        _ui.update_stopwatch(elapsed, running)
    except Exception:
        pass


def _ui_callback(action: str):
    """Called from the stopwatch window buttons via callback."""
    global _start_time, _elapsed

    with _lock:
        if action == "start":
            if _start_time is None:
                _start_time = time.monotonic()
        elif action == "stop":
            if _start_time is not None:
                _elapsed += time.monotonic() - _start_time
                _start_time = None
        elif action == "reset":
            _start_time = None
            _elapsed = 0.0
        # "status" not used here

    _sync_ui()


def execute(parameters: dict, player=None, speak=None) -> str:
    global _start_time, _elapsed, _ui

    # Store UI reference for later
    if player is not None:
        _ui = player

    action = (parameters or {}).get("action", "").lower().strip()

    if action == "start":
        with _lock:
            if _start_time is not None:
                return "Stopwatch is already running, sir."
            _start_time = time.monotonic()

        # Show window if UI available (outside lock)
        if _ui:
            _ui.show_stopwatch(_ui_callback)

        _sync_ui()
        return "Stopwatch started, sir."

    elif action == "status":
        with _lock:
            if _start_time is None:
                result = f"Stopwatch is stopped at {_format_elapsed(_elapsed)}, sir."
            else:
                current = _elapsed + (time.monotonic() - _start_time)
                result = f"Stopwatch is running: {_format_elapsed(current)}, sir."
        _sync_ui()
        return result

    elif action == "stop":
        with _lock:
            if _start_time is None:
                return f"Stopwatch is already stopped at {_format_elapsed(_elapsed)}, sir."
            _elapsed += time.monotonic() - _start_time
            _start_time = None
        _sync_ui()
        return f"Stopwatch stopped at {_format_elapsed(_elapsed)}, sir."

    elif action == "reset":
        with _lock:
            _start_time = None
            _elapsed = 0.0
        _sync_ui()
        return "Stopwatch reset to zero, sir."

    return "Unknown stopwatch action. Use start, status, stop, or reset."