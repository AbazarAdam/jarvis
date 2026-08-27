"""
plugins/project_builder.py — JARVIS Project Builder plugin.

This is a thin wrapper around core/swe_core.py so JARVIS builds real
projects using the same software engineering engine as dev_agent.
"""

from core.swe_core import (
    ProjectRequirement,
    generate_software_project,
    _normalise_language,
    _detect_project_type,
    LANGUAGE_SPECS,
)


PLUGIN_INFO = {
    "name": "project_builder",
    "description": (
        "Build a complete software project from a natural-language requirement. "
        "Supports Python, JavaScript/TypeScript, Go, Rust, Java, C#, and more. "
        "Generates project structure, source code, tests, README, Dockerfile, "
        "CI workflow, and dependency files. Runs tests and attempts automatic fixes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description": {"type": "STRING", "description": "What the project should do"},
            "project_name": {"type": "STRING", "description": "Optional project folder name"},
            "language": {"type": "STRING", "description": "Optional language/framework"},
            "output_dir": {"type": "STRING", "description": "Optional output directory. Default: Desktop/JarvisProjects"},
        },
        "required": ["description"],
    },
}


def execute(parameters: dict, player=None, speak=None) -> str:
    description = (parameters or {}).get("description", "").strip()
    language = (parameters or {}).get("language", "python").strip()
    project_name = (parameters or {}).get("project_name", "").strip()

    if not description:
        return "Please provide a project description, sir."

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

    return generate_software_project(requirement, speak=speak, player=player)