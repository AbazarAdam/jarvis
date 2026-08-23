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


def _repair_json(raw: str) -> dict:
    """Try hard to parse model output as JSON, repairing simple truncation."""
    start = raw.find("{")
    if start == -1:
        raise RuntimeError("No JSON object found.")

    decoder = json.JSONDecoder()

    # Try normal parse first
    try:
        return decoder.raw_decode(raw[start:])[0]
    except json.JSONDecodeError:
        pass

    # Repair missing closing braces by trying progressively shorter cuts.
    # This handles truncated output where the model ran out of tokens.
    text = raw[start:]
    for end in range(len(text), start, -1):
        candidate = text[:end]
        open_braces = candidate.count("{") - candidate.count("}")
        if open_braces < 0:
            continue
        candidate += "}" * open_braces

        try:
            return json.loads(candidate)
        except Exception:
            continue

    raise RuntimeError("Could not repair generated JSON.")

def _default_run_command(language: str) -> str:
    lang = (language or "").lower()
    if lang in ("python", "py"):
        return "python main.py"
    if lang in ("javascript", "js", "node"):
        return "node index.js"
    if lang in ("typescript", "ts"):
        return "npx ts-node index.ts"
    if lang in ("go",):
        return "go run ."
    if lang in ("rust",):
        return "cargo run"
    if lang in ("java",):
        return "java -jar target/app.jar"
    if lang in ("csharp", "dotnet"):
        return "dotnet run"
    if lang in ("dart", "flutter"):
        return "flutter run"
    return "python main.py"


def _default_test_command(language: str) -> str:
    lang = (language or "").lower()
    if lang in ("python", "py"):
        return "pytest -q tests/"
    if lang in ("javascript", "js", "typescript", "ts"):
        return "npm test"
    if lang in ("go",):
        return "go test ./..."
    if lang in ("rust",):
        return "cargo test"
    if lang in ("java",):
        return "mvn test"
    if lang in ("csharp", "dotnet"):
        return "dotnet test"
    if lang in ("dart", "flutter"):
        return "flutter test"
    return "pytest -q tests/"


def _fallback_plan(description: str, project_name: str, language: str) -> dict:
    """Return a minimal valid plan when the model output is unusable."""
    lang = (language or "python").lower()
    if lang in ("python", "py"):
        main_code = (
            "import pygame\n"
            "import sys\n\n"
            "def main():\n"
            "    pygame.init()\n"
            "    screen = pygame.display.set_mode((800, 600))\n"
            "    pygame.display.set_caption('" + project_name + "')\n"
            "    clock = pygame.time.Clock()\n"
            "    running = True\n"
            "    while running:\n"
            "        for event in pygame.event.get():\n"
            "            if event.type == pygame.QUIT:\n"
            "                running = False\n"
            "        screen.fill((0, 0, 0))\n"
            "        pygame.display.flip()\n"
            "        clock.tick(60)\n"
            "    pygame.quit()\n"
            "    sys.exit(0)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        files = [
            {"path": "main.py", "content": main_code, "description": "Game loop using pygame"},
            {"path": "README.md", "content": f"# {project_name}\n\n{description}\n", "description": "Project readme"},
        ]
        run_cmd = "python main.py"
        test_cmd = "pytest -q tests/"
    else:
        files = [
            {"path": "README.md", "content": f"# {project_name}\n\n{description}\n", "description": "Project readme"},
        ]
        run_cmd = _default_run_command(lang)
        test_cmd = _default_test_command(lang)

    return {
        "project_name": project_name,
        "language": lang,
        "files": files,
        "tests": [],
        "commands": {"run": run_cmd, "test": test_cmd},
    }


def _plan_project(description: str, language: str) -> dict:
    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Language: {language}
Description: {description}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "main.py",
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point — what it does and which modules it imports",
      "imports": []
    }}
  ],
  "run_command": "python main.py",
  "dependencies": [],
  "tests": []
}}

Critical:
- Return ONLY valid JSON.
- Keep it minimal.
- If you cannot generate a complex game, still return a valid minimal pygame project.
- Do NOT truncate the JSON. Ensure all braces are closed.
"""

    try:
        raw = _cloud_generate(
            prompt,
            system="You are a software architect. Return only valid JSON.",
            max_tokens=6000,
        )
        raw = _strip_fences(raw)
    except Exception as e:
        # Use fallback on any model failure
        print(f"[DevAgent] Planner model failed: {e} — using fallback plan")
        return _fallback_project_plan(description, language)

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try repair missing closing braces
    start = raw.find("{")
    if start != -1:
        text = raw[start:]
        for end in range(len(text), 0, -1):
            candidate = text[:end]
            open_braces = candidate.count("{") - candidate.count("}")
            if open_braces < 0:
                continue
            candidate += "}" * open_braces
            try:
                data = json.loads(candidate)
                if isinstance(data, dict) and "files" in data:
                    return data
            except Exception:
                continue

    print("[DevAgent] Could not repair planner JSON — using fallback plan")
    return _fallback_project_plan(description, language)


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