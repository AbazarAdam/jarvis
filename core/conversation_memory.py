"""
core/conversation_memory.py — JARVIS Working Memory.

Provides a thread-safe rolling buffer for the current conversation.

It keeps:
    - The last N user/JARVIS turns (working memory)
    - A compact session checkpoint stored on disk, so context survives a hard reset

Duration:
    Working memory keeps the last 10 turns.
    Session checkpoint stores a short summary and the last important action.

No local LLM is used. Summarisation happens in context_manager.py.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
SESSION_CHECKPOINT = MEMORY_DIR / "session_summary.json"


class WorkingMemory:
    """Rolling buffer for recent conversation turns."""

    def __init__(self, max_turns: int = 10):
        self.max_turns = max_turns
        self._turns: deque[dict[str, Any]] = deque(maxlen=max_turns)
        self._lock = threading.RLock()
        self._last_user_text = ""
        self._last_jarvis_text = ""

    def add_user(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._turns.append({
                "role": "user",
                "content": text,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
            self._last_user_text = text

    def add_jarvis(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._turns.append({
                "role": "jarvis",
                "content": text,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
            self._last_jarvis_text = text

    def get_recent_turns(self, limit: Optional[int] = None) -> list[dict[str, str]]:
        with self._lock:
            items = list(self._turns)
        if limit is not None:
            items = items[-limit:]
        return [
            {"role": t.get("role", ""), "content": t.get("content", "")}
            for t in items
        ]

    def get_last_user_text(self) -> str:
        with self._lock:
            return self._last_user_text

    def get_last_jarvis_text(self) -> str:
        with self._lock:
            return self._last_jarvis_text

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._last_user_text = ""
            self._last_jarvis_text = ""

    def to_context_text(self, limit: int = 6) -> str:
        """Return a compact readable context block for the system prompt."""
        turns = self.get_recent_turns(limit=limit)
        if not turns:
            return "(no recent conversation)"

        lines = ["RECENT CONVERSATION:"]
        for t in turns:
            role = "USER" if t["role"] == "user" else "JARVIS"
            content = t["content"].strip().replace("\n", " ")
            if len(content) > 300:
                content = content[:297] + "..."
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def save_checkpoint(self, summary: str = "", last_task: str = "") -> None:
        """Persist a small session checkpoint to disk."""
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "summary": summary or "",
                "last_task": last_task or "",
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "recent_turns_count": len(self.get_recent_turns()),
            }
            SESSION_CHECKPOINT.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"[Memory] Cannot save session checkpoint: {e}")

    def load_checkpoint(self) -> dict[str, str]:
        if not SESSION_CHECKPOINT.exists():
            return {"summary": "", "last_task": ""}
        try:
            data = json.loads(SESSION_CHECKPOINT.read_text(encoding="utf-8"))
            return {
                "summary": data.get("summary", ""),
                "last_task": data.get("last_task", ""),
            }
        except Exception:
            return {"summary": "", "last_task": ""}