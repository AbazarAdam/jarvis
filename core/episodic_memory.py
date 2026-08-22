"""
core/episodic_memory.py — JARVIS Episodic Memory.

Compresses recent conversation working memory into short episodic summaries
and stores them across sessions.

This gives JARVIS the ability to answer:
    - What did we talk about earlier?
    - What did I ask you last week?
    - Continue from our last conversation.

Uses core/model_router.py for summarisation. If the router fails, a safe
truncated timeline fallback is used.

No local LLM is used.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.model_router import ModelRouter


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
EPISODIC_FILE = MEMORY_DIR / "episodic_summary.json"

MAX_EPISODES = 30


class EpisodicMemory:
    """Thread-safe episodic summary store."""

    def __init__(self, path: Path = EPISODIC_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._episodes: list[dict[str, Any]] = []
        self._router = ModelRouter()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._episodes = data[-MAX_EPISODES:]
        except Exception:
            self._episodes = []

    def _save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._episodes, indent=2),
            encoding="utf-8",
        )

    def add_episode(self, summary: str, turns: int = 0, source: str = "working_memory") -> None:
        """Append one compressed episode."""
        summary = (summary or "").strip()
        if not summary:
            return

        with self._lock:
            episode = {
                "summary": summary,
                "turns": turns,
                "source": source,
                "time": datetime.now().isoformat(timespec="seconds"),
            }

            # Prevent near-duplicate consecutive episodes
            if self._episodes:
                last = self._episodes[-1]
                if self._is_duplicate(last.get("summary", ""), summary):
                    return

            self._episodes.append(episode)
            if len(self._episodes) > MAX_EPISODES:
                self._episodes = self._episodes[-MAX_EPISODES:]
            self._save()

    @staticmethod
    def _is_duplicate(old: str, new: str, threshold: float = 0.85) -> bool:
        set_a = set(old.lower().split())
        set_b = set(new.lower().split())
        if not set_a or not set_b:
            return False
        return (len(set_a & set_b) / len(set_a | set_b)) >= threshold

    def recent_episodes(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            return self._episodes[-limit:]

    def summarise_working_memory(self, working_memory_text: str) -> str:
        """
        Summarise recent working memory into a compact episodic summary.

        Uses the cloud model router. If all models fail, use a safe fallback.
        """
        working_memory_text = (working_memory_text or "").strip()
        if not working_memory_text:
            return ""

        prompt = f"""
Summarise the following conversation into no more than 3 sentences.
Focus on the actual topic, tasks, and user requests.

CONVERSATION:
{working_memory_text[:3000]}

SUMMARY:
"""

        try:
            response = self._router.generate(
                prompt=prompt,
                system=(
                    "You are the episodic memory module of JARVIS. "
                    "Return ONLY a concise factual summary. No greetings."
                ),
                temperature=0.2,
                max_tokens=250,
            )
            if response.get("success") and response.get("text"):
                summary = response["text"].strip()
                if len(summary) > 30:
                    return summary
        except Exception:
            pass

        # Safe fallback: create simple timeline from working memory lines
        lines = []
        for line in working_memory_text.splitlines():
            if line.startswith(("USER:", "JARVIS:")):
                lines.append(line.strip()[:120])
        if not lines:
            return working_memory_text[:300]
        return " | ".join(lines[:6])

    def to_context_text(self, limit: int = 3) -> str:
        """Return a compact episodic memory block for the current prompt."""
        episodes = self.recent_episodes(limit=limit)
        if not episodes:
            return "(no episodic memory yet)"

        lines = ["RECENT CONVERSATION EPISODES:"]
        for ep in episodes:
            summary = ep.get("summary", "").replace("\n", " ")
            time_str = ep.get("time", "unknown")[:16]
            lines.append(f"- [{time_str}] {summary}")

        return "\n".join(lines)