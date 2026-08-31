"""
core/swe_core.py — JARVIS Software Engineering Core v2.

This module is the central engine for building real software projects.

It is used by actions/dev_agent.py and plugins/project_builder.py.

It does NOT hardcode a single game or app. Instead it:
  - accepts any project description + optional language
  - plans mandatory modules based on project type
  - generates each file separately to avoid truncated outputs
  - detects dependencies from generated code
  - validates syntax/build where possible
  - reports honest success/failure
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.model_router import ModelRouter


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = Path.home() / "Desktop" / "JarvisProjects"
LOG_DIR = BASE_DIR / "logs"
SWE_LOG = LOG_DIR / "swe_core.log"

MAX_RETRIES = 3
MAX_FIX_ATTEMPTS = 4
BUILD_TIMEOUT = 60


# Global cancellation flag for background SWE tasks
_swe_cancel_event = threading.Event()


def request_swe_cancel():
    """Request cancellation of any running background software generation."""
    _swe_cancel_event.set()


def clear_swe_cancel():
    """Clear the SWE cancellation flag for future tasks."""
    _swe_cancel_event.clear()

LANGUAGE_SPECS: dict[str, dict[str, Any]] = {
    "python": {
        "ext": ".py",
        "run": "python main.py",
        "test": "pytest -q tests/",
        "dep_file": "requirements.txt",
        "entry": "main.py",
        "types": ["application", "game", "web", "api", "cli"],
    },
    "javascript": {
        "ext": ".js",
        "run": "node index.js",
        "test": "npm test",
        "dep_file": "package.json",
        "entry": "index.js",
        "types": ["application", "web", "api", "game"],
    },
    "typescript": {
        "ext": ".ts",
        "run": "npx ts-node index.ts",
        "test": "npm test",
        "dep_file": "package.json",
        "entry": "index.ts",
        "types": ["application", "web", "api"],
    },
    "go": {
        "ext": ".go",
        "run": "go run .",
        "test": "go test ./...",
        "dep_file": "go.mod",
        "entry": "main.go",
        "types": ["application", "api", "cli"],
    },
    "rust": {
        "ext": ".rs",
        "run": "cargo run",
        "test": "cargo test",
        "dep_file": "Cargo.toml",
        "entry": "src/main.rs",
        "types": ["application", "api", "cli"],
    },
    "java": {
        "ext": ".java",
        "run": "mvn spring-boot:run",
        "test": "mvn -q test",
        "dep_file": "pom.xml",
        "entry": "src/main/java/Main.java",
        "types": ["application", "web", "api"],
    },
    "csharp": {
        "ext": ".cs",
        "run": "dotnet run",
        "test": "dotnet test",
        "dep_file": "Project.csproj",
        "entry": "Program.cs",
        "types": ["application", "web", "api"],
    },
}


@dataclass
class ProjectRequirement:
    name: str
    description: str
    language: str
    project_type: str = "application"
    modules: list[str] = field(default_factory=list)
    entry_point: str = "main.py"
    run_command: str = ""
    test_command: str = ""
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "language": self.language,
            "project_type": self.project_type,
            "modules": self.modules,
            "entry_point": self.entry_point,
            "run_command": self.run_command,
            "test_command": self.test_command,
            "dependencies": self.dependencies,
        }


def _log(msg: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(SWE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _normalise_language(language: str | None) -> str:
    lang = (language or "python").lower().strip()
    aliases = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "node": "javascript",
        "golang": "go",
        "rs": "rust",
        "cs": "csharp",
        "c#": "csharp",
    }
    return aliases.get(lang, lang)

def _provider_available() -> bool:
    """Return True if at least one cloud AI provider can answer a simple prompt."""
    try:
        from core.model_router import ModelRouter

        r = ModelRouter().generate(
            prompt="Reply with OK",
            system="Reply with OK only.",
            temperature=0,
            max_tokens=10,
        )
        return bool(r.get("success") and r.get("text", "").strip())
    except Exception:
        return False

def _detect_project_type(description: str) -> str:
    text = description.lower()
    if any(k in text for k in ("game", "shooter", "player", "enemy", "level", "pygame")):
        return "game"
    if any(k in text for k in ("web", "api", "rest", "backend", "frontend", "server")):
        return "web"
    if any(k in text for k in ("mobile", "android", "ios", "flutter")):
        return "mobile"
    if any(k in text for k in ("cli", "command", "terminal")):
        return "cli"
    return "application"

# ---------------------------------------------------------------------------
# Planning helpers
# ---------------------------------------------------------------------------
def _mandatory_modules_for_type(project_type: str, language: str) -> list[dict]:
    """Return a list of required modules for a given project type.

    These are not hardcoded code files — they are structural requirements
    that ensure the generated project is complete and not a skeleton.
    """
    base = {
        "app": {
            "path": "main.py" if language == "python" else (
                "index.js" if language in ("javascript", "typescript") else "main.go"
            ),
            "description": "Entry point that wires everything together",
            "imports": [],
        }
    }

    if project_type == "game":
        modules = [
            {"path": "core/config.py", "description": "Game settings and constants", "imports": []},
            {"path": "core/entity.py", "description": "Base entity class with position, health, update, render", "imports": []},
            {"path": "core/player.py", "description": "Player character with movement and shooting", "imports": ["core.entity"]},
            {"path": "core/enemy.py", "description": "Enemy character with movement and AI", "imports": ["core.entity"]},
            {"path": "core/bullet.py", "description": "Bullet projectile logic", "imports": ["core.entity"]},
            {"path": "core/engine.py", "description": "Game loop, collision detection, rendering", "imports": ["core.player", "core.enemy", "core.bullet", "core.config"]},
            {"path": "main.py", "description": "Entry point that starts the game", "imports": ["core.engine"]},
        ]
    elif project_type == "web":
        modules = [
            {"path": "app.py", "description": "Main web application entry point", "imports": []},
            {"path": "routes.py", "description": "Route handlers", "imports": ["app"]},
            {"path": "models.py", "description": "Data models", "imports": []},
            {"path": "templates/index.html", "description": "Basic template", "imports": []},
            {"path": "main.py", "description": "Run the web server", "imports": ["app"]},
        ]
    elif project_type == "api":
        modules = [
            {"path": "app.py", "description": "API application factory", "imports": []},
            {"path": "routes.py", "description": "API endpoints", "imports": ["app"]},
            {"path": "models.py", "description": "Data schemas", "imports": []},
            {"path": "main.py", "description": "Start the API server", "imports": ["app"]},
        ]
    else:
        modules = [
            {"path": "core/engine.py", "description": "Core business logic", "imports": []},
            {"path": "utils/helpers.py", "description": "Helper utilities", "imports": []},
            {"path": "main.py", "description": "Entry point", "imports": ["core.engine", "utils.helpers"]},
        ]

    # Ensure unique and language-specific entry point
    return modules


def _plan_from_ai(requirement: ProjectRequirement) -> list[dict]:
    """Use cloud model to refine plan, but only if available. Falls back to mandatory modules."""
    lang = requirement.language
    spec = LANGUAGE_SPECS.get(lang, LANGUAGE_SPECS["python"])

    prompt = f"""
You are a software architect. Create a file plan for this project:

Requirement: {requirement.description}
Language: {lang}
Project type: {requirement.project_type}

Return ONLY JSON:
{{
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point",
      "imports": []
    }}
  ],
  "dependencies": ["package-name"],
  "run_command": "{spec['run']}",
  "test_command": "{spec['test']}"
}}

Rules:
- Include all necessary files, but keep it focused.
- Ensure entry point is present.
- For {requirement.project_type}, include modules relevant to the type.
- Return ONLY valid JSON.
"""

    try:
        from core.model_router import ModelRouter

        response = ModelRouter().generate(
            prompt=prompt,
            system="You are a software architect. Return only valid JSON.",
            temperature=0.2,
            max_tokens=5000,
        )
        if response.get("success"):
            raw = response["text"].strip()
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            data = json.loads(raw)
            if isinstance(data, dict) and "files" in data:
                return data
    except Exception as e:
        print(f"[SWE] AI planning failed, using mandatory modules: {e}")

    # Fallback: mandatory modules
    return {
        "files": _mandatory_modules_for_type(requirement.project_type, lang),
        "dependencies": [],
        "run_command": spec["run"],
        "test_command": spec["test"],
    }


def _extract_dependencies_from_code(code: str, language: str, project_modules: set[str] | None = None) -> list[str]:
    """Extract external dependencies, ignoring project-local modules."""
    deps = set()
    project_modules = project_modules or set()

    if language == "python":
        patterns = [
            r"^\s*import\s+([a-zA-Z0-9_]+)",
            r"^\s*from\s+([a-zA-Z0-9_]+)\s+import",
        ]
        for pat in patterns:
            for m in re.finditer(pat, code, re.MULTILINE):
                mod = m.group(1)
                # Skip standard library
                if mod in (
                    "sys", "os", "json", "re", "typing", "pathlib", "datetime",
                    "time", "math", "random", "collections", "itertools", "functools",
                    "abc", "dataclasses", "enum", "subprocess", "shutil", "tempfile",
                    "logging",
                ):
                    continue
                # Skip local project modules
                if mod in project_modules:
                    continue
                deps.add(mod)

        mapping = {
            "pygame": "pygame",
            "flask": "flask",
            "requests": "requests",
            "numpy": "numpy",
            "pandas": "pandas",
            "dotenv": "python-dotenv",
            "yaml": "pyyaml",
            "cv2": "opencv-python",
            "pytest": "pytest",
        }
        deps = {mapping.get(d, d) for d in deps}

    return sorted(deps)


# ---------------------------------------------------------------------------
# Symbol contract validation
# ---------------------------------------------------------------------------
def _extract_public_symbols(code: str) -> set[str]:
    """Extract top-level class/function/constant names from Python code."""
    try:
        import ast
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    symbols = set()

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                symbols.add(node.target.id)

    return symbols


def _extract_project_imports(test_code: str) -> list[tuple[str, str]]:
    """
    Extract imports from project modules in test code.

    Returns list of (module, imported_name)
    Example:
        from game import Game -> ("game", "Game")
        import player      -> ("player", None)
    """
    try:
        import ast
        tree = ast.parse(test_code)
    except SyntaxError:
        return []

    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, None))

    return imports

# ---------------------------------------------------------------------------
# Symbol contract validation
# ---------------------------------------------------------------------------
def _validate_symbol_contracts(
    project_dir: Path,
    file_codes: dict[str, str],
    requirement: ProjectRequirement,
) -> list[str]:
    """
    Check that test imports match source symbols.

    Returns list of human-readable contract errors.
    """
    if requirement.language != "python":
        return []

    errors = []

    for path, code in file_codes.items():
        if not path.endswith(".py"):
            continue

        if path.startswith("tests/") or path.startswith("test_"):
            continue

        module_name = Path(path).stem
        expected_symbols = _extract_public_symbols(code)

        # Find test files that import from this module
        for test_path, test_code in file_codes.items():
            if not test_path.endswith(".py"):
                continue
            if not (test_path.startswith("tests/") or test_path.startswith("test_")):
                continue

            imports = _extract_project_imports(test_code)
            for mod, imported_name in imports:
                if mod != module_name and mod != f"{module_name}":
                    continue
                if imported_name and imported_name not in expected_symbols:
                    errors.append(
                        f"Contract violation: test '{test_path}' imports "
                        f"'{imported_name}' from '{path}' but '{imported_name}' is not defined there."
                    )

    return errors


def _write_project_files(project_dir: Path, files: list[dict], requirement: ProjectRequirement, speak=None) -> dict[str, str]:
    """Generate each file individually and return code mapping."""
    file_codes: dict[str, str] = {}

    for file_info in files:
        path = file_info.get("path", "")
        desc = file_info.get("description", "")
        imports = file_info.get("imports", [])
        if not path:
            continue

        if _swe_cancel_event.is_set():
            print(f"[SWE] ❌ Cancelled during file generation: {path}")
            break

        prompt = f"""Write complete, functional code for this file.

Project: {requirement.description}
Language: {requirement.language}
File path: {path}
Purpose: {desc}
Imports from project: {', '.join(imports) if imports else 'none'}

Existing project files:
{chr(10).join(f'- {p}' for p in file_codes.keys())}

Dependency context:
{chr(10).join(f'--- {fp} ---\n{code[:1500]}' for fp, code in file_codes.items())}

Rules:
- Output ONLY raw code. No markdown, no explanation.
- Write complete, runnable code.
- No placeholders, no TODO, no pass stubs.
- Match imports exactly.
"""

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                from core.model_router import ModelRouter

                response = ModelRouter().generate(
                    prompt=prompt,
                    system=f"You are an expert {requirement.language} developer. Return only code.",
                    temperature=0.2,
                    max_tokens=8000,
                )
                if not response.get("success"):
                    raise RuntimeError(response.get("error") or "Model failed")

                code = response["text"].strip()
                code = re.sub(r"```[a-zA-Z]*\r?\n?", "", code).strip().rstrip("`").strip()

                if len(code.strip()) < 20:
                    raise RuntimeError("Generated code is too short.")

                full_path = project_dir / path

                # Basic syntax check for Python files
                if path.endswith(".py"):
                    try:
                        compile(code, str(full_path), "exec")
                    except SyntaxError as e:
                        raise RuntimeError(f"Syntax error in generated code: {e}")
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(code, encoding="utf-8")
                file_codes[path] = code
                print(f"[SWE] ✅ Written: {path} ({len(code)} chars)")
                if speak:
                    speak(f"Generated {path}, sir.")
                time.sleep(0.3)
                break

            except Exception as e:
                print(f"[SWE] ⚠️ Failed {path} attempt {attempt}: {e}")
                if attempt == MAX_RETRIES:
                    # Do not abort entire build; record failure and continue
                    file_codes[path] = f"# Failed to generate: {path}"
                time.sleep(1)

    return file_codes

def _repair_failing_files(
    project_dir: Path,
    test_output: str,
    file_codes: dict[str, str],
    requirement: ProjectRequirement,
) -> bool:
    """
    Identify failing project files from pytest output and ask the model to fix them.
    Returns True if at least one file was repaired.
    """
    # Find project file paths in the test output
    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'], line \d+')
    failing_paths = set()
    for m in pattern.finditer(test_output):
        raw = m.group(1)
        try:
            rel = Path(raw).relative_to(project_dir).as_posix()
            failing_paths.add(rel)
        except Exception:
            pass

    if not failing_paths:
        # Fallback: include all non-test Python files in project
        failing_paths = {
            p for p in file_codes
            if p.endswith(".py") and not p.startswith("tests/")
        }

    repaired_any = False

    for rel_path in list(failing_paths)[:6]:
        current_code = file_codes.get(rel_path)
        if not current_code:
            continue

        prompt = f"""Fix this Python file so the tests pass.

Project requirement: {requirement.description}
Language: python
File: {rel_path}

Test output:
{test_output[:2500]}

Current code:
{current_code}

Return ONLY the complete fixed Python code. No markdown, no explanation.
"""

        for attempt in range(1, 3):
            try:
                from core.model_router import ModelRouter

                response = ModelRouter().generate(
                    prompt=prompt,
                    system="You are an expert Python debugger. Return only fixed code.",
                    temperature=0.2,
                    max_tokens=8000,
                )
                if not response.get("success"):
                    continue

                fixed = response["text"].strip()
                fixed = re.sub(r"```[a-zA-Z]*\r?\n?", "", fixed).strip().rstrip("`").strip()
                if len(fixed) < 20:
                    continue

                full_path = project_dir / rel_path
                full_path.write_text(fixed, encoding="utf-8")
                file_codes[rel_path] = fixed
                print(f"[SWE] 🔧 Repaired: {rel_path}")
                repaired_any = True
                break
            except Exception as e:
                print(f"[SWE] ⚠️ Repair attempt {attempt} failed for {rel_path}: {e}")

        time.sleep(0.5)

    return repaired_any

def _repair_contract_violations(
    project_dir: Path,
    file_codes: dict[str, str],
    requirement: ProjectRequirement,
    errors: list[str],
) -> bool:
    """Attempt to repair broken symbol contracts using the cloud model."""
    if not errors:
        return True

    repaired = False

    # Simplify: parse first 3 errors and ask model to fix all related files.
    error_text = "\n".join(errors[:5])
    files_to_fix = set()

    for err in errors:
        m = re.search(r"test '([^']+)' imports '([^']+)' from '([^']+)'", err)
        if m:
            test_path, imported_name, source_path = m.groups()
            files_to_fix.add(source_path)
            files_to_fix.add(test_path)

    for rel_path in list(files_to_fix)[:6]:
        current_code = file_codes.get(rel_path)
        if not current_code:
            continue

        prompt = f"""Fix the public API contract between these files.

Project requirement: {requirement.description}
Language: python

File to fix: {rel_path}

Contract errors:
{error_text}

Current file:
{current_code}

Rules:
- If this is a source file, add the missing public class/function so tests can import it.
- If this is a test file, adjust the import to match the actual public symbol in the source file.
- Keep the rest of the code functional.
- Return ONLY the complete fixed Python code. No markdown, no explanation.
"""

        try:
            from core.model_router import ModelRouter

            response = ModelRouter().generate(
                prompt=prompt,
                system="You are an expert software engineer. Return only fixed Python code.",
                temperature=0.2,
                max_tokens=8000,
            )
            if response.get("success"):
                fixed = response["text"].strip()
                fixed = re.sub(r"```[a-zA-Z]*\r?\n?", "", fixed).strip().rstrip("`").strip()
                if len(fixed) >= 20:
                    full_path = project_dir / rel_path
                    full_path.write_text(fixed, encoding="utf-8")
                    file_codes[rel_path] = fixed
                    print(f"[SWE] 🔧 Repaired contract in: {rel_path}")
                    repaired = True
        except Exception as e:
            print(f"[SWE] ⚠️ Contract repair failed for {rel_path}: {e}")

        time.sleep(0.5)

    return repaired

def generate_software_project(requirement: ProjectRequirement, speak=None, player=None) -> str:
    """Full software generation pipeline."""

    # Preflight: refuse to start if no AI provider is available.
    if not _provider_available():
        msg = (
            "No AI provider is currently available, sir. "
            "Free API quotas may be exhausted. Please wait and try again."
        )
        print(f"[SWE] ❌ {msg}")
        return msg

    if _swe_cancel_event.is_set():
        print("[SWE] ❌ Cancelled before start.")
        return "Cancelled by user, sir."

    project_dir = PROJECTS_DIR / requirement.name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Plan
    plan_data = _plan_from_ai(requirement)
    files = plan_data.get("files", _mandatory_modules_for_type(requirement.project_type, requirement.language))
    dependencies = list(plan_data.get("dependencies", []))

    print(f"[SWE] Building '{requirement.name}' ({requirement.language}) with {len(files)} files")

    # Generate files
    file_codes = _write_project_files(project_dir, files, requirement, speak)


    # Extract dependencies from Python code, ignoring project-local modules
    if requirement.language == "python":
        project_modules = {Path(f).stem for f in file_codes.keys()}
        all_code = "\n".join(file_codes.values())
        deps_from_code = _extract_dependencies_from_code(
            all_code,
            "python",
            project_modules=project_modules,
        )
        dependencies = sorted(set(dependencies).union(deps_from_code))

    # Write dependency file
    dep_file = LANGUAGE_SPECS[requirement.language]["dep_file"]
    if requirement.language == "python" and dependencies:
        (project_dir / dep_file).write_text("\n".join(dependencies), encoding="utf-8")
        print(f"[SWE] Dependencies: {', '.join(dependencies)}")
    elif requirement.language in ("javascript", "typescript"):
        (project_dir / dep_file).write_text("{}", encoding="utf-8")

    # Add pytest config to force pygame dummy video driver
    if requirement.language == "python":
        conftest_path = project_dir / "tests" / "conftest.py"
        conftest_path.parent.mkdir(parents=True, exist_ok=True)
        conftest_path.write_text(
            "import os\n"
            "os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')\n",
            encoding="utf-8",
        )
        print("[SWE] Generated tests/conftest.py")

    # Write README
    readme = project_dir / "README.md"
    readme.write_text(
        f"# {requirement.name}\n\n{requirement.description}\n\n## Run\n\n```bash\n{requirement.run_command}\n```\n\n## Test\n\n```bash\n{requirement.test_command}\n```\n",
        encoding="utf-8",
    )
    print(f"[SWE] README generated")

    # Validation: syntax check for Python
    if requirement.language == "python":
        syntax_errors = []
        for path in file_codes:
            if path.endswith(".py"):
                full_path = project_dir / path
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(full_path)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    syntax_errors.append(path)
                    print(f"[SWE] ❌ Syntax error in {path}")
        if syntax_errors:
            return f"Project generated with syntax errors in: {', '.join(syntax_errors)}"

    # ------------------------------------------------------------------
    # Validation & auto-fix loop
    # ------------------------------------------------------------------
    if requirement.language == "python":
        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            contract_errors = _validate_symbol_contracts(
                project_dir,
                file_codes,
                requirement,
            )

            if contract_errors:
                print(f"[SWE] ❌ Contract violations:")
                for err in contract_errors[:10]:
                    print(f"  - {err}")

                # Attempt repair of contract violations
                repaired = _repair_contract_violations(
                    project_dir,
                    file_codes,
                    requirement,
                    contract_errors,
                )
                if not repaired:
                    return (
                        f"Project generated but public API contracts are broken, sir. "
                        f"Project is at {project_dir}. "
                        f"First issue: {contract_errors[0]}"
                    )

            print(f"[SWE] Running tests attempt {attempt}...")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "tests/"],
                capture_output=True,
                text=True,
                cwd=str(project_dir),
                timeout=BUILD_TIMEOUT,
            )

            if result.returncode == 0:
                print("[SWE] ✅ Tests passed")
                break

            print(f"[SWE] ❌ Tests failed. Attempting repair...")

            repaired = False
            try:
                repaired = _repair_failing_files(
                    project_dir=project_dir,
                    test_output=(result.stdout or "") + "\n" + (result.stderr or ""),
                    file_codes=file_codes,
                    requirement=requirement,
                )
            except Exception as e:
                print(f"[SWE] ⚠️ Repair failed: {e}")

            if not repaired:
                return (
                    f"Project generated but tests failed after {attempt} attempt(s), sir. "
                    f"Project is at {project_dir}. "
                    f"Last error: {(result.stdout or result.stderr)[:500]}"
                )

        else:
            return (
                f"Project generated but tests still failed after {MAX_FIX_ATTEMPTS} repair attempts, sir. "
                f"Project is at {project_dir}. "
                f"Please inspect the code manually."
            )
    return f"Project '{requirement.name}' generated successfully at {project_dir}. See README for run instructions."

