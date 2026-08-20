"""
core/skill_validator.py — Validates and executes JARVIS learned skills.

A skill is only promoted to active after it proves useful. This module:
  1. Saves candidate skills through SkillStore.
  2. Runs them inside the sandbox.
  3. Records success/failure and promotes/deprecates accordingly.
  4. Executes active skills safely on demand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from core.sandbox import run_code
from core.skill_store import SkillStore


BASE_DIR = Path(__file__).resolve().parent.parent


class SkillValidator:
    def __init__(self, store: Optional[SkillStore] = None):
        self.store = store or SkillStore()

    def validate_candidate(
        self,
        name: str,
        code: str,
        description: str,
        parameters: dict | None = None,
        risk_level: int = 2,
        side_effects: list[str] | None = None,
        keywords: list[str] | None = None,
        test_params: Any | None = None,
    ) -> dict:
        """
        Save a new candidate skill and run one validation attempt.

        Returns a structured result with sandbox output.
        """
        save_result = self.store.save_skill(
            name=name,
            code=code,
            description=description,
            parameters=parameters,
            risk_level=risk_level,
            side_effects=side_effects,
            keywords=keywords,
        )

        if not save_result.get("created"):
            return {
                "success": False,
                "stage": "save",
                "reason": save_result.get("reason"),
                "save_result": save_result,
            }

        skill_name = save_result["skill"]["name"]
        run_result = self._run_skill_code(code, test_params)

        if run_result.get("success"):
            self.store.record_success(skill_name, run_result.get("stdout", ""))
        else:
            self.store.record_failure(skill_name, run_result.get("stderr", ""))

        return {
            "success": run_result.get("success", False),
            "stage": "validate",
            "save_result": save_result,
            "run_result": run_result,
        }

    def execute_skill(self, name: str, params: Any | None = None) -> dict:
        """Execute an existing skill with optional JSON parameters."""
        meta = self.store.get_skill(name)
        if not meta:
            return {
                "success": False,
                "reason": "skill_not_found",
            }

        code = self.store.load_code(name)
        if code is None:
            return {
                "success": False,
                "reason": "skill_code_missing",
            }

        run_result = self._run_skill_code(code, params)

        if run_result.get("success"):
            self.store.record_success(name, run_result.get("stdout", ""))
        else:
            self.store.record_failure(name, run_result.get("stderr", ""))

        return {
            "success": run_result.get("success", False),
            "skill": meta,
            "run_result": run_result,
        }

    @staticmethod
    def _run_skill_code(code: str, params: Any) -> dict:
        args = None
        if params is not None:
            args = [json.dumps(params, ensure_ascii=True)]
        return run_code(code, args=args, timeout=30)