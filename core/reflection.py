"""
core/reflection.py — JARVIS Reflection Memory.

After important tasks, JARVIS records what was attempted, what succeeded,
and what failed. This gives JARVIS experience it can reuse later.

This module stores lightweight structured events in:

    memory/reflection_log.json

It does not use a local LLM. The memory is compact and suitable for prompt
context injection.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
REFLECTION_FILE = MEMORY_DIR / "reflection_log.json"

MAX_EVENTS = 100


class ReflectionMemory:
    """Thread-safe reflection log for JARVIS experiences."""

    def __init__(self, path: Path = REFLECTION_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._events = data[-MAX_EVENTS:]
        except Exception:
            self._events = []

    def _save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._events, indent=2),
            encoding="utf-8",
        )

    def record(
        self,
        goal: str,
        result: str,
        outcome: str = "success",
        error: str = "",
        fix: str = "",
        tool: str = "",
    ) -> None:
        """
        Record one reflection event.

        outcome: success | failure | partial
        """
        with self._lock:
            event = {
                "goal": goal or "",
                "result": (result or "")[:500],
                "outcome": outcome,
                "error": (error or "")[:500],
                "fix": (fix or "")[:500],
                "tool": tool or "",
                "time": datetime.now().isoformat(timespec="seconds"),
            }
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._events = self._events[-MAX_EVENTS:]
            self._save()

    def recent_successes(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._events if e.get("outcome") == "success"]
        return items[-limit:]

    def recent_failures(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._events if e.get("outcome") == "failure"]
        return items[-limit:]

    def to_context_text(self, limit: int = 8) -> str:
        """Return a compact reflection context block."""
        with self._lock:
            recent = self._events[-limit:]

        if not recent:
            return "(no reflection memory yet)"

        lines = ["RECENT TASK MEMORY:"]
        for e in recent:
            outcome = e.get("outcome", "?")
            goal = e.get("goal", "")[:80]
            fix = e.get("fix", "")[:80]
            if outcome == "success":
                lines.append(f"✔ {goal}")
            elif outcome == "partial":
                lines.append(f"◑ {goal}")
            else:
                lines.append(f"✘ {goal}")
                if fix:
                    lines.append(f"  Fix: {fix}")

        return "\n".join(lines)