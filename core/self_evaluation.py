"""
core/self_evaluation.py — JARVIS Self-Evaluation Loop.

After a significant task, JARVIS evaluates his own output using the cloud
model router.

It produces:
    - usefulness_score: 0-100
    - verdict: excellent | good | acceptable | poor
    - issue: short description if result was weak
    - recommended_fix: next improvement action
    - update_skill: true/false
    - skill_update_hint: concise note for future skill updates

This evaluation is stored in reflection memory so JARVIS learns from it.

No local LLM is used.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from core.model_router import ModelRouter
from core.reflection import ReflectionMemory


class SelfEvaluator:
    """Cloud-powered self-evaluation for JARVIS task outcomes."""

    def __init__(self):
        self.router = ModelRouter()

    def evaluate(
        self,
        task: str,
        params: Optional[dict] = None,
        result: str = "",
        outcome: str = "success",
        error: str = "",
    ) -> dict[str, Any]:
        """
        Evaluate a completed task.

        Returns a structured evaluation dict.
        """
        task = (task or "").strip()
        result = (result or "").strip()
        error = (error or "").strip()

        if not task:
            return self._fallback("Unknown task")

        prompt = f"""
You are the JARVIS self-evaluation module.

Task: {task}
Parameters: {json.dumps(params or {}, ensure_ascii=True)[:500] if params else '{}'}
Outcome: {outcome}
Error: {error[:300] or 'none'}
Result: {result[:1200] or 'none'}

Evaluate the result.

Return ONLY valid JSON with exactly these keys:
{{
  "usefulness_score": 0,
  "verdict": "excellent | good | acceptable | poor",
  "issue": "",
  "recommended_fix": "",
  "update_skill": false,
  "skill_update_hint": ""
}}

Rules:
- If outcome is failure, usefulness_score must be 0 or low.
- If result contains clear useful output, usefulness_score should be 70-100.
- update_skill should be true only when a reusable skill should be created/updated.
- Be objective and concise.
"""

        try:
            response = self.router.generate(
                prompt=prompt,
                system=(
                    "You are the self-evaluation engine of JARVIS. "
                    "Return only valid JSON. No explanations."
                ),
                temperature=0.1,
                max_tokens=350,
            )
            if response.get("success") and response.get("text"):
                text = response["text"].strip()
                data = self._parse_json(text)
                if data:
                    return self._normalise(data, task, outcome, error)

        except Exception as e:
            print(f"[SelfEval] ⚠️ Evaluation failed: {e}")

        return self._fallback(task, outcome=outcome, error=error)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return None
        return None

    def _normalise(self, data: dict, task: str, outcome: str, error: str) -> dict:
        try:
            score = int(data.get("usefulness_score", 0))
        except Exception:
            score = 0
        score = max(0, min(100, score))

        if outcome == "failure":
            score = min(score, 20)

        return {
            "task": task,
            "outcome": outcome,
            "usefulness_score": score,
            "verdict": str(data.get("verdict", "poor")).lower(),
            "issue": str(data.get("issue", "")).strip(),
            "recommended_fix": str(data.get("recommended_fix", "")).strip(),
            "update_skill": bool(data.get("update_skill", False)),
            "skill_update_hint": str(data.get("skill_update_hint", "")).strip(),
            "error": error or "",
        }

    def _fallback(self, task: str, outcome: str = "success", error: str = "") -> dict:
        if outcome == "failure":
            return {
                "task": task,
                "outcome": outcome,
                "usefulness_score": 10,
                "verdict": "poor",
                "issue": error or "Task failed.",
                "recommended_fix": "Retry the task or review the error.",
                "update_skill": False,
                "skill_update_hint": "",
                "error": error,
            }

        return {
            "task": task,
            "outcome": outcome,
            "usefulness_score": 70,
            "verdict": "acceptable",
            "issue": "",
            "recommended_fix": "",
            "update_skill": False,
            "skill_update_hint": "",
            "error": "",
        }

    def record_evaluation(self, evaluation: dict) -> None:
        """Save the evaluation to reflection memory."""
        try:
            memory = ReflectionMemory()
            memory.record(
                goal=evaluation.get("task", ""),
                result=evaluation.get("verdict", ""),
                outcome=evaluation.get("outcome", "success"),
                error=evaluation.get("error", ""),
                fix=evaluation.get("recommended_fix", ""),
                tool="self_evaluator",
            )
        except Exception as e:
            print(f"[SelfEval] ⚠️ Record failed: {e}")