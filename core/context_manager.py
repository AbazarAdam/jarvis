"""
core/context_manager.py — JARVIS Context Assembler.

Builds the complete system context for every conversation turn.

It combines:
    - Current date and time
    - Long-term user memory from memory/long_term.json
    - Recent working memory from core/conversation_memory.py
    - Persisted session checkpoint (survives hard reset)
    - Active learned skills from core/skill_store.py

This gives JARVIS coherent context without overloading the live model.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from core.conversation_memory import WorkingMemory
from core.skill_store import SkillStore


def _format_time() -> str:
    now = datetime.now()
    return now.strftime("%A, %B %d, %Y — %I:%M %p")


def _format_long_term_memory() -> str:
    """Load and format long-term user memory if available."""
    try:
        from memory.memory_manager import load_memory, format_memory_for_prompt

        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        return mem_str or "(no long-term user facts saved yet)"
    except Exception:
        return "(long-term memory unavailable)"


def _format_skills() -> str:
    """List active learned skills."""
    try:
        skills = SkillStore().list_skills(include_all=False)
    except Exception:
        skills = []

    if not skills:
        return "(no active learned skills)"

    lines = []
    for skill in skills[:10]:
        name = skill.get("name", "unknown")
        desc = skill.get("description", "")
        confidence = skill.get("confidence", 0)
        if desc:
            lines.append(f"- {name}: {desc} (confidence={confidence:.2f})")
        else:
            lines.append(f"- {name} (confidence={confidence:.2f})")
    return "\n".join(lines)


def build_context(
    working_memory: Optional[WorkingMemory] = None,
    extra_instructions: Optional[str] = None,
) -> dict[str, str]:
    """
    Build the complete JARVIS context.

    Returns:
        {
            "time": str,
            "long_term_memory": str,
            "working_memory": str,
            "session_checkpoint": str,
            "skills": str,
            "extra_instructions": str,
            "combined": str,
        }
    """
    time_str = _format_time()
    long_term_str = _format_long_term_memory()
    skills_str = _format_skills()

    if working_memory is None:
        working_memory = WorkingMemory()

    working_str = working_memory.to_context_text(limit=8)
    checkpoint = working_memory.load_checkpoint()

    checkpoint_lines = []
    if checkpoint.get("last_task"):
        checkpoint_lines.append(f"Last task: {checkpoint['last_task']}")
    if checkpoint.get("summary"):
        checkpoint_lines.append(f"Session summary: {checkpoint['summary']}")
    checkpoint_str = "\n".join(checkpoint_lines) if checkpoint_lines else "(no saved session checkpoint)"

    extra_str = extra_instructions or "(no extra instructions)"

    combined_parts = [
        "[CURRENT DATE & TIME]",
        f"Right now it is: {time_str}",
        "",
        "[LONG-TERM USER MEMORY]",
        long_term_str,
        "",
        "[SESSION CHECKPOINT]",
        checkpoint_str,
        "",
        "[ACTIVE LEARNED SKILLS]",
        skills_str,
        "",
        "[RECENT CONVERSATION]",
        working_str,
        "",
        "[SPECIAL INSTRUCTIONS]",
        extra_str,
    ]

    return {
        "time": time_str,
        "long_term_memory": long_term_str,
        "working_memory": working_str,
        "session_checkpoint": checkpoint_str,
        "skills": skills_str,
        "extra_instructions": extra_str,
        "combined": "\n".join(combined_parts),
    }


def update_checkpoint(
    working_memory: WorkingMemory,
    summary: str = "",
    last_task: str = "",
) -> None:
    """Convenience wrapper to persist the current session checkpoint."""
    working_memory.save_checkpoint(summary=summary, last_task=last_task)