"""
core/reset_controller.py — Global hard-reset registry for JARVIS.

Any plugin or module with in-memory state that must be cleared when the
STOP button is pressed can register a reset callback here.

This gives JARVIS one reliable place to reset all volatile features.
"""

from __future__ import annotations

import threading
from typing import Callable


class ResetController:
    """Thread-safe registry of reset callbacks."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialised", False):
            return
        self._initialised = True
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._lock = threading.RLock()

    def register(self, name: str, callback: Callable[[], None]) -> None:
        """Register a callback that will be called on every hard reset."""
        with self._lock:
            self._callbacks[name] = callback

    def unregister(self, name: str) -> None:
        with self._lock:
            self._callbacks.pop(name, None)

    def reset_all(self) -> None:
        """Run every registered reset callback. Failures are isolated."""
        with self._lock:
            callbacks = list(self._callbacks.items())

        for name, callback in callbacks:
            try:
                callback()
            except Exception as e:
                print(f"[ResetController] ⚠️ {name} reset failed: {e}")


# Singleton accessor
reset_controller = ResetController()