"""
plugins/skill_runner.py — Learned skill execution for JARVIS.

This plugin gives JARVIS access to the new self-learning skill store.

Actions:
    list     — list saved skills
    status   — show one skill's metadata
    run      — execute a saved skill safely
    create   — learn and save a new skill from a natural language task

All execution happens inside core/sandbox.py. New skills are validated and
de-duplicated before they become active.
"""

from __future__ import annotations

from typing import Any

from core.skill_store import SkillStore
from core.skill_validator import SkillValidator
from core.skill_synthesizer import synthesize_skill


PLUGIN_INFO = {
    "name": "skill_runner",
    "description": (
        "Manage and execute learned JARVIS skills. "
        "Use this when no existing tool matches the user's request. "
        "Actions: list, status, run, create."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list | status | run | create"
            },
            "name": {
                "type": "STRING",
                "description": "Skill name"
            },
            "description": {
                "type": "STRING",
                "description": "Natural language description of the new skill"
            },
            "params": {
                "type": "OBJECT",
                "description": "JSON parameters to pass to the skill"
            }
        },
        "required": ["action"]
    }
}


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()
    name = (parameters or {}).get("name", "").strip()

    store = SkillStore()
    validator = SkillValidator(store)

    if action == "list":
        skills = store.list_skills(include_all=True)
        if not skills:
            return "No learned skills yet, sir."
        lines = []
        for s in skills:
            lines.append(
                f"- {s.get('name')} [{s.get('status')}] "
                f"success={s.get('success_count', 0)} "
                f"fail={s.get('fail_count', 0)}"
            )
        return "Learned skills:\n" + "\n".join(lines)

    elif action == "status":
        if not name:
            return "Please provide a skill name, sir."
        meta = store.get_skill(name)
        if not meta:
            return f"No skill named '{name}', sir."
        return (
            f"Skill: {meta.get('name')}\n"
            f"Status: {meta.get('status')}\n"
            f"Description: {meta.get('description')}\n"
            f"Success: {meta.get('success_count')}\n"
            f"Failures: {meta.get('fail_count')}\n"
            f"Confidence: {meta.get('confidence')}"
        )

    elif action == "run":
        if not name:
            return "Please provide a skill name, sir."

        params = parameters.get("params")
        result = validator.execute_skill(name, params)

        if not result.get("success"):
            return f"Skill '{name}' failed: {result.get('run_result', {}).get('stderr', 'Unknown error')}"

        output = (result.get("run_result", {}).get("stdout") or "").strip()

        # If the skill description says it saves/writes a file, save stdout to desktop automatically.
        meta = result.get("skill", {})
        description = str(meta.get("description", "")).lower()
        should_save = (
            parameters.get("save") is True
            or "save" in description
            or "write" in description
            or "file" in description
        )

        if should_save and output:
            output_name = parameters.get("output_name") or f"{name}.txt"
            try:
                from actions.file_controller import file_controller as fc

                fc(
                    parameters={
                        "action": "write",
                        "path": "desktop",
                        "name": output_name,
                        "content": output,
                    },
                    player=None,
                )
                return f"Saved result to desktop/{output_name}, sir."
            except Exception as e:
                return f"Skill ran but could not save result: {e}"

        return output or "Skill executed."

    elif action == "create":
        description = (parameters or {}).get("description", "").strip()
        if not description:
            return "Please provide a description of the new skill, sir."

        if speak:
            speak("Learning a new skill for you now, sir. This may take a few moments.")

        # Always provide a safe test parameter so the generated skill has
        # input and is forced to produce output during validation.
        test_params = parameters.get("params") or {"text": "test"}

        result = synthesize_skill(
            name=name or "learned_skill",
            description=description,
            parameters=parameters.get("params") or {},
            risk_level=2,
            side_effects=["generated_code"],
            keywords=None,
            test_params=test_params,
            max_attempts=3,
            speak=None,
        )

        if result.get("success"):
            skill = result.get("skill", {})
            if result.get("created"):
                if speak:
                    speak(f"New skill '{skill.get('name')}' learned and saved, sir.")
                return f"New skill '{skill.get('name')}' learned and saved, sir."
            if speak:
                speak(f"Similar skill already exists: {skill.get('name')}, sir.")
            return f"Similar skill already exists: {skill.get('name')}, sir."

        if speak:
            speak("I could not create that skill after several attempts, sir.")
        return (
            "I could not create that skill after several attempts, sir. "
            f"Last error: {result.get('last_error', 'Unknown')}"
        )

    return f"Unknown skill_runner action: {action}"