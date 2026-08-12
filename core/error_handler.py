"""
core/error_handler.py — Global crash recovery for JARVIS.
Catches unhandled exceptions in Python threads and the main runtime,
logs them, and speaks a friendly message instead of crashing.
"""

import sys
import traceback
import threading
from datetime import datetime
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "crash.log"

# Callbacks set by main.py
_speak_callback = None
_write_log_callback = None


def setup(speak=None, write_log=None):
    """Install global hooks. Call once at startup."""
    global _speak_callback, _write_log_callback
    _speak_callback = speak
    _write_log_callback = write_log

    # Hook for unhandled exceptions in the main thread
    sys.excepthook = _python_excepthook

    # Wrap all new threads so they don't die silently
    _patch_threading()


def _patch_threading():
    """Make every new thread safe by wrapping its run() method."""
    orig_init = threading.Thread.__init__

    def safe_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        orig_run = self.run

        def safe_run():
            try:
                orig_run()
            except Exception:
                _log_and_speak()

        self.run = safe_run

    threading.Thread.__init__ = safe_init


def _log_and_speak():
    """Log the traceback and try to inform the user."""
    tb = traceback.format_exc()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{timestamp}] UNHANDLED EXCEPTION\n{tb}\n"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg)

    # Show in the UI log
    if _write_log_callback:
        try:
            _write_log_callback("ERR: Internal error. See logs/crash.log")
        except Exception:
            pass

    # Speak to the user
    if _speak_callback:
        try:
            _speak_callback(
                "I encountered an internal error, sir. "
                "Please check the logs for details."
            )
        except Exception:
            pass


def _python_excepthook(exc_type, exc_value, exc_tb):
    """sys.excepthook replacement — called on unhandled exceptions."""
    traceback.print_exception(exc_type, exc_value, exc_tb)
    _log_and_speak()