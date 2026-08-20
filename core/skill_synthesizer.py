"""
core/skill_synthesizer.py — Turn new natural-language tasks into reusable skills.

This module is the first real self-learning component in JARVIS.

Flow:
  1. User asks for a task JARVIS has no existing skill for.
  2. JARVIS generates a small Python script using Gemini.
  3. The script is statically validated.
  4. It is executed safely inside core/sandbox.py.
  5. It is saved as a reusable skill only if it succeeds.
  6. If it fails, JARVIS analyses the error and retries.

No new plugin or manual code is needed from the user.
"""

from __future__ import annotations

import json
import re
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from core.sandbox import run_code, validate_code
from core.skill_store import SkillStore


BASE_DIR = Path(__file__).resolve().parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _extract_code(text: str) -> str:
    """Strip markdown fences and return clean Python code."""
    text = (text or "").strip()
    text = re.sub(r"```(?:python)?", "", text)
    text = text.strip().rstrip("`").strip()
    return text


def _build_prompt(description: str, previous_error: str | None = None) -> str:
    prompt = f"""
You are the JARVIS Skill Synthesizer.

Generate ONE standalone Python script that solves the following task:

TASK:
{description}

STRICT REQUIREMENTS:
- Use only Python standard library, json, sys, and optionally `requests` for read-only network requests.
- NEVER write to disk, never use open(), os, subprocess, shutil, input, eval, exec, __import__.
- NEVER execute system commands.
- If parameters are needed, read them from sys.argv[1] as JSON.
- The script MUST end with a TOP-LEVEL print() statement that outputs the final result.
- Do NOT only define functions. After any function definitions, call the required function and print its output.
- Do NOT print explanations, disclaimers, or usage examples.
- Return ONLY Python code. No markdown.

Example of correct structure for a task "reverse text":

import sys, json
params = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {{}}
text = params.get("text", "")
print(text[::-1])

Return Python code now.
"""
    if previous_error:
        prompt += f"""

PREVIOUS ATTEMPT FAILED WITH THIS ERROR:
{previous_error}

Fix the code and return only the corrected Python script.
"""
    return prompt


def _generate_code(description: str, previous_error: str | None = None) -> str:
    """Generate code using Gemini cloud API only."""
    from google import genai

    prompt = _build_prompt(description, previous_error)
    client = genai.Client(
        api_key=_get_api_key(),
        http_options={"api_version": "v1beta"},
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    # Gather text from all response parts. AFC can split text across parts.
    parts_text = []
    for part in getattr(response, "parts", []):
        if getattr(part, "text", None):
            parts_text.append(part.text)
    if not parts_text and getattr(response, "text", None):
        parts_text.append(response.text)

    raw = "\n".join(parts_text).strip()
    code = _extract_code(raw)
    if not code:
        raise RuntimeError("Gemini returned no code.")
    if "print(" not in code:
        raise RuntimeError("Generated code has no print statement. Make sure the script prints the final result.")
    return code


# ---------------------------------------------------------------------------
# Skill synthesis
# ---------------------------------------------------------------------------
def synthesize_skill(
    name: str,
    description: str,
    parameters: Optional[dict] = None,
    risk_level: int = 2,
    side_effects: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    test_params: Any | None = None,
    max_attempts: int = 3,
    speak: Optional[Callable] = None,
) -> dict:
    """
    Create, test, and save a reusable skill from a natural language task.

    Only successful scripts are saved.

    Returns:
        dict with success, skill, run_result, reason, attempt_count
    """
    store = SkillStore()

    # De-duplicate before generating anything
    existing = store.find_similar_skill(description)
    if existing:
        return {
            "success": True,
            "created": False,
            "skill": existing,
            "reason": "similar_skill_exists",
        }

    previous_error = None

    for attempt in range(1, max_attempts + 1):
        try:

            code = _generate_code(description, previous_error)

            safe, violations = validate_code(code)
            if not safe:
                violation_text = "; ".join(violations)
                previous_error = f"Unsafe code blocked. Violations: {violation_text}"
                continue

            # Test before saving
            run_result = run_code(
                code,
                args=[json.dumps(test_params, ensure_ascii=True)] if test_params is not None else None,
                timeout=30,
            )

            if run_result.get("success") and run_result.get("stdout", "").strip():
                save_result = store.save_skill(
                    name=name,
                    code=code,
                    description=description,
                    parameters=parameters,
                    risk_level=risk_level,
                    side_effects=side_effects or [],
                    keywords=keywords or [],
                )

                if save_result.get("created") or save_result.get("reason") == "already_exists":
                    store.record_success(name)

                return {
                    "success": True,
                    "created": True,
                    "skill": save_result.get("skill"),
                    "run_result": run_result,
                    "reason": "skill_saved",
                    "attempt_count": attempt,
                }

            previous_error = run_result.get("stderr") or (
                "The script ran but printed no output. "
                "Make sure the script calls its main function and uses print() to output the result."
            )

        except Exception as e:
            previous_error = str(e)
            print(f"[SkillSynthesizer] ⚠️ Attempt {attempt} failed: {e}")
            traceback.print_exc()

    return {
        "success": False,
        "created": False,
        "skill": None,
        "reason": "failed_after_retries",
        "attempt_count": max_attempts,
        "last_error": previous_error,
    }