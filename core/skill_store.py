"""
core/skill_store.py — Persistent, de-duplicated skill storage for JARVIS.

Skills are small, validated Python scripts stored inside:

    memory/skills/<skill_name>/skill.py
    memory/skills/<skill_name>/skill.json

Only skills that pass validation and prove useful are promoted to active.
This module prevents slop by refusing to create near-duplicate skills and by
deprecating skills that fail repeatedly.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.sandbox import validate_code
from core.safety import ensure_path_allowed


BASE_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BASE_DIR / "memory" / "skills"

_VALID_NAME = re.compile(r"[^a-z0-9_]+")


class SkillStore:
    """Thread-safe filesystem store for learned skills."""

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    def _normalise_name(self, name: str) -> str:
        name = (name or "").strip().lower()
        name = _VALID_NAME.sub("_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        if not name:
            name = "skill_" + uuid.uuid4().hex[:8]
        return name[:60]

    def _skill_dir(self, name: str) -> Path:
        return self.skills_dir / self._normalise_name(name)

    def _code_path(self, name: str) -> Path:
        return self._skill_dir(name) / "skill.py"

    def _meta_path(self, name: str) -> Path:
        return self._skill_dir(name) / "skill.json"

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    def _load_meta(self, name: str) -> dict | None:
        path = self._meta_path(name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_meta(self, name: str, meta: dict) -> None:
        path = self._meta_path(name)
        ensure_path_allowed(path, "write")
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Similarity / de-duplication
    # ------------------------------------------------------------------
    @staticmethod
    def _token_set(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        set_a = SkillStore._token_set(a)
        set_b = SkillStore._token_set(b)
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    def find_similar_skill(self, description: str, threshold: float = 0.85) -> dict | None:
        """Return an existing skill if a strongly similar one already exists."""
        best = None
        best_score = 0.0

        for skill in self.list_skills(include_all=True):
            existing_desc = skill.get("description", "")
            score = self._similarity(description, existing_desc)
            if score > best_score:
                best_score = score
                best = skill

        if best and best_score >= threshold:
            best["similarity"] = round(best_score, 3)
            return best
        return None

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------
    def save_skill(
        self,
        name: str,
        code: str,
        description: str,
        parameters: dict | None = None,
        risk_level: int = 2,
        side_effects: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> dict:
        """
        Create a candidate skill if safe and not duplicate.

        Returns a structured result dictionary.
        """
        with self._lock:
            normalised_name = self._normalise_name(name)

            # 1. De-duplication
            existing = self.find_similar_skill(description)
            if existing:
                return {
                    "created": False,
                    "skill": existing,
                    "reason": "similar_skill_exists",
                }

            # 2. Static safety validation
            safe, violations = validate_code(code)
            if not safe:
                return {
                    "created": False,
                    "skill": None,
                    "reason": "unsafe_code",
                    "violations": violations,
                }

            # 3. Ensure the skill directory is allowed
            skill_dir = self._skill_dir(normalised_name)
            if skill_dir.exists():
                return {
                    "created": False,
                    "skill": self._load_meta(normalised_name),
                    "reason": "already_exists",
                }

            ensure_path_allowed(skill_dir, "write")
            skill_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "name": normalised_name,
                "description": description,
                "status": "candidate",
                "success_count": 0,
                "fail_count": 0,
                "confidence": 0.4,
                "risk_level": risk_level,
                "side_effects": side_effects or [],
                "keywords": keywords or [],
                "parameters": parameters or {},
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "last_used": None,
            }

            self._code_path(normalised_name).write_text(code, encoding="utf-8")
            self._write_meta(normalised_name, meta)

            return {
                "created": True,
                "skill": meta,
                "reason": "created_candidate",
            }

    def get_skill(self, name: str) -> dict | None:
        return self._load_meta(name)

    def load_code(self, name: str) -> str | None:
        path = self._code_path(name)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def list_skills(self, include_all: bool = True) -> list[dict]:
        """Return metadata for all skills, optionally only active ones."""
        if not self.skills_dir.exists():
            return []
        result = []
        for item in sorted(self.skills_dir.iterdir()):
            if not item.is_dir():
                continue
            meta = self._load_meta(item.name)
            if not meta:
                continue
            if not include_all and meta.get("status") != "active":
                continue
            result.append(meta)
        return result

    # ------------------------------------------------------------------
    # Lifecycle updates
    # ------------------------------------------------------------------
    def record_success(self, name: str, result_summary: str | None = None) -> None:
        with self._lock:
            meta = self._load_meta(name)
            if not meta:
                return
            meta["success_count"] = int(meta.get("success_count", 0)) + 1
            meta["last_used"] = datetime.now().isoformat(timespec="seconds")
            meta["confidence"] = min(
                1.0,
                0.4 + (0.30 * meta["success_count"]) - (0.25 * meta["fail_count"]),
            )
            if meta["success_count"] >= 2 and meta["fail_count"] == 0 and meta["confidence"] >= 0.75:
                meta["status"] = "active"
            self._write_meta(name, meta)

    def record_failure(self, name: str, error: str | None = None) -> None:
        with self._lock:
            meta = self._load_meta(name)
            if not meta:
                return
            meta["fail_count"] = int(meta.get("fail_count", 0)) + 1
            meta["last_used"] = datetime.now().isoformat(timespec="seconds")
            meta["confidence"] = max(
                0.0,
                0.4 + (0.30 * meta["success_count"]) - (0.25 * meta["fail_count"]),
            )
            if meta["fail_count"] >= 3:
                meta["status"] = "deprecated"
            self._write_meta(name, meta)

    def set_status(self, name: str, status: str) -> None:
        with self._lock:
            meta = self._load_meta(name)
            if not meta:
                return
            meta["status"] = status
            self._write_meta(name, meta)

    def delete_skill(self, name: str, confirmed: str = "no") -> dict:
        """Delete a skill only after explicit confirmation."""
        if str(confirmed).lower() not in ("yes", "true", "1", "confirm"):
            return {"deleted": False, "reason": "confirmation_required"}

        with self._lock:
            skill_dir = self._skill_dir(name)
            if not skill_dir.exists():
                return {"deleted": False, "reason": "not_found"}

            ensure_path_allowed(skill_dir, "delete")
            for child in skill_dir.iterdir():
                child.unlink(missing_ok=True)
            skill_dir.rmdir()
            return {"deleted": True, "reason": "deleted"}