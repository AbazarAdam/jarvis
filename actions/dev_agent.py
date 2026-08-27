"""
actions/dev_agent.py — JARVIS Software Engineering Agent.

This is now a thin wrapper around core/swe_core.py.

All project generation, file planning, dependency detection, syntax checks,
and run validation happen in core/swe_core.py.
"""

from core.swe_core import (
    ProjectRequirement,
    generate_software_project,
    _normalise_language,
    _detect_project_type,
    LANGUAGE_SPECS,
)


def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p = parameters or {}
    description = p.get("description", "").strip()
    language = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()

    if not description:
        return "Please describe the project you want me to build, sir."

    lang = _normalise_language(language)
    spec = LANGUAGE_SPECS.get(lang, LANGUAGE_SPECS["python"])
    project_type = _detect_project_type(description)

    if not project_name:
        project_name = description.strip().lower().replace(" ", "_")[:40]
        project_name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in project_name)

    requirement = ProjectRequirement(
        name=project_name,
        description=description,
        language=lang,
        project_type=project_type,
        modules=[],
        entry_point=spec["entry"],
        run_command=spec["run"],
        test_command=spec["test"],
        dependencies=[],
    )

    if speak:
        speak(f"Building project {project_name} now, sir.")

    try:
        return generate_software_project(requirement, speak=speak, player=player)
    except Exception as e:
        msg = f"Project generation failed: {e}"
        if speak:
            speak(msg, sir=False)
        return msg