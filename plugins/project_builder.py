"""
plugins/project_builder.py — Multi-language autonomous project builder for JARVIS.

Creates a complete project from a natural-language requirement.

It supports Python, JavaScript/TypeScript, Node.js, React, Vue, Go, Rust,
Java, C#, Flutter, and other common software engineering stacks.

The model generates:
    - project structure and source files
    - tests
    - README
    - Dockerfile
    - CI workflow
    - dependency file
    - run/test commands

The builder executes the generated test command and attempts automatic fixes.

No local LLM is used. Code generation uses the central model router.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from core.model_router import ModelRouter


PLUGIN_INFO = {
    "name": "project_builder",
    "description": (
        "Build a complete software project from a requirement. Supports Python, "
        "JavaScript/TypeScript, Node.js, React, Vue, Go, Rust, Java, C#, Flutter, "
        "and other common software engineering stacks. Generates project structure, "
        "source code, tests, README, Dockerfile, CI workflow, and dependency files. "
        "Runs the generated test command and attempts automatic fixes. "
        "Use for 'build a Flask app', 'create a React frontend', 'scaffold a Go API', "
        "or 'build a mobile app with Flutter'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description": {
                "type": "STRING",
                "description": "What the project should do"
            },
            "project_name": {
                "type": "STRING",
                "description": "Optional project folder name"
            },
            "language": {
                "type": "STRING",
                "description": "Optional language/framework, e.g. python, javascript, go, rust, java, csharp, flutter"
            },
            "output_dir": {
                "type": "STRING",
                "description": "Where to create the project. Default: Desktop"
            },
            "max_fix_attempts": {
                "type": "INTEGER",
                "description": "Max automatic fix attempts. Default: 3"
            }
        },
        "required": ["description"]
    }
}


def _extract_code(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"```(?:python|json|yaml|yml|markdown|md|javascript|js|ts|go|rust|java|csharp|dart)?", "", text)
    text = text.strip().rstrip("`").strip()
    return text


def _generate_spec(description: str, project_name: str, language: Optional[str], router: ModelRouter) -> dict:
    """
    Generate a language-aware project plan as JSON.

    The model must return commands.run and commands.test because they differ
    by language and framework.
    """
    language_instruction = (
        f"Use the language/framework: {language}. "
        if language
        else "Infer the best language/framework from the requirement. "
    )

    prompt = f"""
Generate a complete project plan for this requirement:

{description}

Project name: {project_name}
{language_instruction}

Return ONLY valid JSON with this exact structure:

{{
  "language": "python|javascript|typescript|go|rust|java|csharp|dart|etc",
  "project_type": "web|mobile|desktop|cli|library|api",
  "files": [
    {{
      "path": "src/app.py",
      "content": "...",
      "description": "..."
    }}
  ],
  "tests": [
    {{
      "path": "tests/test_app.py",
      "content": "...",
      "description": "..."
    }}
  ],
  "dependencies": [
    "package-or-library-names"
  ],
  "readme": "...",
  "dockerfile": "...",
  "ci_workflow": "...",
  "commands": {{
    "run": "python app.py",
    "test": "pytest -q"
  }}
}}

RULES:
- Use proper project structure for the chosen stack.
- Include the correct dependency file: requirements.txt, package.json, go.mod, Cargo.toml, pom.xml, etc.
- Generate real code, not placeholders.
- Generate meaningful tests.
- commands.test must be the exact command to run the test suite.
- commands.run must be the exact command to run the project.
- Return ONLY JSON.
"""
    response = router.generate(
        prompt=prompt,
        system="You are an expert software engineer. Return only valid JSON.",
        temperature=0.2,
        max_tokens=4000,
    )
    if not response.get("success"):
        raise RuntimeError(response.get("error") or "Model router failed.")

    raw = _extract_code(response["text"])
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise RuntimeError("Generated plan is not valid JSON.")

    spec = json.loads(match.group(0))

    required_keys = ("files", "commands")
    for key in required_keys:
        if key not in spec:
            raise RuntimeError(f"Generated plan missing required key: {key}")

    return spec


def _write_file(base_dir: Path, rel_path: str, content: str) -> None:
    path = base_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_tests(base_dir: Path, test_command: str) -> dict:
    if not test_command:
        return {
            "success": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "No test command provided.",
        }

    try:
        parts = shlex.split(test_command, posix=(sys.platform != "win32"))
    except ValueError:
        parts = test_command.split()

    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(base_dir),
            timeout=180,
        )
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": "Test command timed out.",
        }
    except FileNotFoundError as e:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"Test command not found: {e}",
        }
    except Exception as e:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(e),
        }


def _generate_fix(
    project_name: str,
    description: str,
    language: Optional[str],
    errors: str,
    router: ModelRouter,
) -> dict | None:
    prompt = f"""
Project: {project_name}
Description: {description}
Language: {language or 'inferred'}

Tests failed with:

{errors}

Generate a NEW complete project plan JSON that fixes the issue. Return only JSON.
"""
    try:
        return _generate_spec(prompt, project_name, language, router)
    except Exception:
        return None


def execute(parameters: dict, player=None, speak=None) -> str:
    description = (parameters or {}).get("description", "").strip()
    if not description:
        return "Please provide a project description, sir."

    project_name = (parameters or {}).get("project_name", "").strip()
    if not project_name:
        project_name = re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")[:40]

    language = (parameters or {}).get("language", "").strip() or None

    output_dir = (parameters or {}).get("output_dir", "").strip()
    base_output = Path(output_dir) if output_dir else Path.home() / "Desktop"
    base_output.mkdir(parents=True, exist_ok=True)

    project_dir = base_output / project_name
    max_fix_attempts = int((parameters or {}).get("max_fix_attempts", 3))

    if speak:
        speak(f"Building project {project_name} now, sir.")

    router = ModelRouter()

    try:
        spec = _generate_spec(description, project_name, language, router)
    except Exception as e:
        return f"Could not generate project plan: {e}"

    generated_language = spec.get("language", language or "unknown")
    run_command = spec.get("commands", {}).get("run", "")
    test_command = spec.get("commands", {}).get("test", "")

    # Write all planned files
    for file_entry in spec.get("files", []):
        _write_file(project_dir, file_entry.get("path", ""), file_entry.get("content", ""))

    for test_entry in spec.get("tests", []):
        _write_file(project_dir, test_entry.get("path", ""), test_entry.get("content", ""))

    _write_file(project_dir, "README.md", spec.get("readme", ""))
    _write_file(project_dir, "Dockerfile", spec.get("dockerfile", ""))

    deps = spec.get("dependencies", [])
    if deps:
        if generated_language in ("python",):
            _write_file(project_dir, "requirements.txt", "\n".join(deps))
        elif generated_language in ("javascript", "typescript", "node"):
            _write_file(project_dir, "package.json", "{}")
        elif generated_language == "go":
            _write_file(project_dir, "go.mod", "\n".join(deps))
        elif generated_language == "rust":
            _write_file(project_dir, "Cargo.toml", "\n".join(deps))
        elif generated_language == "java":
            _write_file(project_dir, "pom.xml", "\n".join(deps))
        elif generated_language in ("csharp", "dotnet"):
            _write_file(project_dir, f"{project_name}.csproj", "\n".join(deps))

    _write_file(project_dir, ".github/workflows/ci.yml", spec.get("ci_workflow", ""))

    # Run tests and attempt fixes
    test_result = _run_tests(project_dir, test_command)

    attempts = 0
    while not test_result["success"] and attempts < max_fix_attempts:
        attempts += 1
        if speak:
            speak(f"Test failed. Attempting fix {attempts}, sir.")

        fixed_spec = _generate_fix(
            project_name,
            description,
            language,
            test_result["stderr"] or test_result["stdout"],
            router,
        )
        if not fixed_spec:
            break

        for file_entry in fixed_spec.get("files", []):
            _write_file(project_dir, file_entry.get("path", ""), file_entry.get("content", ""))
        for test_entry in fixed_spec.get("tests", []):
            _write_file(project_dir, test_entry.get("path", ""), test_entry.get("content", ""))

        test_command = fixed_spec.get("commands", {}).get("test", test_command)
        test_result = _run_tests(project_dir, test_command)

    status = "passed" if test_result["success"] else "failed"
    summary = (
        f"Project '{project_name}' created at {project_dir}, sir. "
        f"Language: {generated_language}. "
        f"Tests {status} after {attempts} fix attempt(s)."
    )

    if not test_result["success"]:
        summary += f"\nLast error: {(test_result['stderr'] or test_result['stdout'])[:200]}"

    return summary