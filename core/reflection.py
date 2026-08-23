"""
core/reflection.py — JARVIS Reflection Memory v2.

Records task outcomes, errors, fixes, and skill experiences.

v2 adds:
    - error type classification
    - error fingerprinting for fast lookup of similar past failures
    - fix success tracking
    - structured event types
    - context field

This memory is used by:
    - agent/error_handler.py
    - core/self_heal.py
    - core/skill_store.py
    - core/self_evaluation.py
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
REFLECTION_FILE = MEMORY_DIR / "reflection_log.json"

MAX_EVENTS = 120


class ReflectionMemory:
    """Thread-safe reflection log for JARVIS experiences."""

    def __init__(self, path: Path = REFLECTION_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def classify_error(error: str) -> str:
        """Return a stable error category for a raw error string."""
        text = (error or "").lower()

        if any(k in text for k in ("rate limit", "quota", "429", "resource_exhausted")):
            return "rate_limit"
        if any(k in text for k in ("timeout", "timed out", "connection", "network", "socket")):
            return "network"
        if any(k in text for k in ("permission denied", "access denied", "unauthorized", "401", "403")):
            return "auth"
        if any(k in text for k in ("file not found", "no such file", "filenotfounderror")):
            return "filesystem"
        if any(k in text for k in ("syntaxerror", "nameerror", "typeerror", "attributeerror", "importerror", "modulenotfounderror")):
            return "code"
        if any(k in text for k in ("unknown tool", "unsupported", "not configured")):
            return "tool"
        if any(k in text for k in ("blocked", "safety", "forbidden")):
            return "safety"
        return "unknown"

    @staticmethod
    def fingerprint(error: str) -> str:
        """Generate a stable short fingerprint for an error."""
        text = (error or "").strip().lower()
        if not text:
            return "empty"
        return hashlib.md5(text[:1500].encode("utf-8", errors="replace")).hexdigest()[:10]

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
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
        Backward-compatible record method.

        Creates a structured reflection event.
        """
        event = {
            "event_id": f"ev_{int(datetime.now().timestamp() * 1000)}",
            "type": "task",
            "goal": goal or "",
            "result": (result or "")[:500],
            "outcome": outcome,
            "error": (error or "")[:500],
            "error_type": self.classify_error(error) if error else "",
            "error_fingerprint": self.fingerprint(error) if error else "",
            "fix_used": (fix or "")[:300],
            "fix_success": outcome == "success" and bool(fix),
            "context": "",
            "time": datetime.now().isoformat(timespec="seconds"),
        }

        with self._lock:
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._events = self._events[-MAX_EVENTS:]
            self._save()

    def record_event(
        self,
        goal: str,
        outcome: str = "success",
        event_type: str = "task",
        result: str = "",
        error: str = "",
        fix: str = "",
        tool: str = "",
        context: str = "",
    ) -> None:
        """
        Record a richer experience event with context.
        """
        event = {
            "event_id": f"ev_{int(datetime.now().timestamp() * 1000)}",
            "type": event_type,
            "goal": goal or "",
            "result": (result or "")[:500],
            "outcome": outcome,
            "error": (error or "")[:500],
            "error_type": self.classify_error(error) if error else "",
            "error_fingerprint": self.fingerprint(error) if error else "",
            "fix_used": (fix or "")[:300],
            "fix_success": outcome == "success" and bool(fix),
            "tool": tool or "",
            "context": (context or "")[:300],
            "time": datetime.now().isoformat(timespec="seconds"),
        }

        with self._lock:
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._events = self._events[-MAX_EVENTS:]
            self._save()

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    def recent_successes(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._events if e.get("outcome") == "success"]
        return items[-limit:]

    def recent_failures(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._events if e.get("outcome") == "failure"]
        return items[-limit:]

    def find_similar_error(self, error_text: str, limit: int = 3) -> list[dict[str, Any]]:
        """
        Return past events with the same error fingerprint.

        This lets error_handler know if a similar problem was fixed before.
        """
        fp = self.fingerprint(error_text)
        with self._lock:
            matches = [
                e for e in self._events
                if e.get("error_fingerprint") == fp and e.get("error_fingerprint")
            ]
        return matches[-limit:]

    def get_latest_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return self._events[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._events = []
            self._save()

    # ------------------------------------------------------------------
    # Context formatting
    # ------------------------------------------------------------------
    def to_context_text(self, limit: int = 8) -> str:
        """Return a compact reflection context block."""
        with self._lock:
            recent = self._events[-limit:]

        if not recent:
            return "(no reflection memory yet)"

        lines = ["RECENT TASK MEMORY:"]
        for e in recent:
            outcome = e.get("outcome", "?")
            goal = (e.get("goal") or "")[:80]
            error_type = e.get("error_type") or ""
            fix = (e.get("fix_used") or "")[:80]

            if outcome == "success":
                lines.append(f"✔ {goal}")
            elif outcome == "partial":
                lines.append(f"◑ {goal}")
            else:
                lines.append(f"✘ {goal}")
                if error_type:
                    lines.append(f"  Type: {error_type}")
                if fix:
                    lines.append(f"  Fix: {fix}")

        return "\n".join(lines)