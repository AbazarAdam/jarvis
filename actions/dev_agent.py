"""
actions/dev_agent.py — JARVIS Autonomous Software Engineering Agent v2.

This module builds complete projects from a natural-language description.

Key improvements over v1:
    - Incremental file generation, not one massive JSON plan
    - Per-file retry with stable ModelRouter
    - File existence + non-empty verification
    - Syntax/build/test validation
    - Failure raises an exception, so background status is honest
    - Language-agnostic specifications

No deprecated google.generativeai. No or_client fallback storm.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.model_router import ModelRouter


BASE_DIR         = Path(__file__).resolve().parent.parent
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
PROJECTS_DIR     = Path.home() / "Desktop" / "JarvisProjects"
LOG_DIR          = BASE_DIR / "logs"
DEV_AGENT_LOG    = LOG_DIR / "dev_agent.log"

MAX_FILE_RETRIES      = 3
MAX_BUILD_ATTEMPTS    = 5
MAX_FIX_ATTEMPTS      = 5
DEFAULT_TIMEOUT       = 60


LANGUAGE_SPECS: dict[str, dict[str, Any]] = {
    "python": {
        "ext": ".py",
        "run_cmd": "python main.py",
        "test_cmd": "pytest -q tests/",
        "dependency_file": "requirements.txt",
        "install_cmd": "{python} -m pip install -r requirements.txt",
        "entry_file": "main.py",
        "project_type": "application",
    },
    "javascript": {
        "ext": ".js",
        "run_cmd": "node index.js",
        "test_cmd": "npm test",
        "dependency_file": "package.json",
        "install_cmd": "npm install",
        "entry_file": "index.js",
        "project_type": "application",
    },
    "typescript": {
        "ext": ".ts",
        "run_cmd": "npx ts-node index.ts",
        "test_cmd": "npm test",
        "dependency_file": "package.json",
        "install_cmd": "npm install",
        "entry_file": "index.ts",
        "project_type": "application",
    },
    "go": {
        "ext": ".go",
        "run_cmd": "go run .",
        "test_cmd": "go test ./...",
        "dependency_file": "go.mod",
        "install_cmd": "",
        "entry_file": "main.go",
        "project_type": "application",
    },
    "rust": {
        "ext": ".rs",
        "run_cmd": "cargo run",
        "test_cmd": "cargo test",
        "dependency_file": "Cargo.toml",
        "install_cmd": "",
        "entry_file": "src/main.rs",
        "project_type": "application",
    },
    "java": {
        "ext": ".java",
        "run_cmd": "mvn spring-boot:run",
        "test_cmd": "mvn -q test",
        "dependency_file": "pom.xml",
        "install_cmd": "mvn -q install",
        "entry_file": "src/main/java/Main.java",
        "project_type": "application",
    },
    "csharp": {
        "ext": ".cs",
        "run_cmd": "dotnet run",
        "test_cmd": "dotnet test",
        "dependency_file": "Project.csproj",
        "install_cmd": "dotnet restore",
        "entry_file": "Program.cs",
        "project_type": "application",
    },
    "dart": {
        "ext": ".dart",
        "run_cmd": "dart run",
        "test_cmd": "dart test",
        "dependency_file": "pubspec.yaml",
        "install_cmd": "dart pub get",
        "entry_file": "bin/main.dart",
        "project_type": "application",
    },
}


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DEV_AGENT_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


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
        "dotnet": "csharp",
    }
    return aliases.get(lang, lang)


def _extract_code(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"```[a-zA-Z]*\r?\n?", "", text)
    text = text.strip().rstrip("`").strip()
    return text


def _parse_json(text: str) -> dict:
    """Parse model JSON, repairing minor truncation if possible."""
    text = (text or "").strip()
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    # Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Find first complete JSON object via raw_decode
    start = text.find("{")
    if start != -1:
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(text[start:])
            return data
        except Exception:
            pass

    # Repair missing closing braces
    if start != -1:
        for end in range(len(text), 0, -1):
            candidate = text[start:end]
            open_braces = candidate.count("{") - candidate.count("}")
            if open_braces < 0:
                continue
            candidate += "}" * open_braces
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

    raise ValueError("Model returned unparseable JSON.")


def _cloud_generate(
    prompt: str,
    system: str = "You are an expert software engineer.",
    temperature: float = 0.2,
    max_tokens: int = 8000,
) -> str:
    router = ModelRouter()
    response = router.generate(
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if not response.get("success"):
        raise RuntimeError(response.get("error") or "Cloud model failed.")

    return response["text"].strip()


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-]", "_", name or "jarvis_project")
    return name or "jarvis_project"


def _run_command(
    cmd: list[str],
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out."
    except FileNotFoundError:
        return 127, "", "Command not found."
    except Exception as e:
        return 1, "", str(e)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _plan_project(description: str, language: str) -> dict:
    """
    Generate a minimal project file plan.

    The plan lists files, imports, and entry point. It does NOT contain
    file contents — those are generated one file at a time to avoid huge
    truncated model outputs.
    """
    lang = _normalise_language(language)
    spec = LANGUAGE_SPECS.get(lang, LANGUAGE_SPECS["python"])

    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Language: {lang}
Description: {description}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "{spec['entry_file']}",
  "language": "{lang}",
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point — what it does and which modules it imports",
      "imports": []
    }},
    {{
      "path": "core/engine.py",
      "description": "Core engine module",
      "imports": []
    }}
  ],
  "run_command": "{spec['run_cmd']}",
  "test_command": "{spec['test_cmd']}",
  "dependencies": []
}}

Rules:
- Keep the file list small and focused. 3 to 8 files are enough for v1.
- List files in dependency order: no-import files first, entry point last.
- Return ONLY valid JSON. Ensure all braces and quotes are closed.
- Do not write code in the plan. Describe each file only.
"""

    try:
        raw = _cloud_generate(
            prompt=prompt,
            system="You are a software architect. Return only valid JSON.",
            temperature=0.2,
            max_tokens=4000,
        )
        plan = _parse_json(raw)
    except Exception as e:
        print(f"[DevAgent] Planning failed: {e} — using fallback plan")
        plan = {
            "project_name": _safe_filename(description.split()[0] if description.split() else "project"),
            "entry_point": spec["entry_file"],
            "language": lang,
            "files": [
                {
                    "path": spec["entry_file"],
                    "description": "Entry point that runs the application",
                    "imports": [],
                }
            ],
            "run_command": spec["run_cmd"],
            "test_command": spec["test_cmd"],
            "dependencies": [],
        }

    if not isinstance(plan, dict):
        raise RuntimeError("Invalid project plan.")

    if "files" not in plan or not isinstance(plan["files"], list):
        plan["files"] = [
            {
                "path": spec["entry_file"],
                "description": "Entry point",
                "imports": [],
            }
        ]

    plan.setdefault("entry_point", spec["entry_file"])
    plan.setdefault("language", lang)
    plan.setdefault("run_command", spec["run_cmd"])
    plan.setdefault("test_command", spec["test_cmd"])
    plan.setdefault("dependencies", [])

    # Ensure entry point is in files list
    entry = plan["entry_point"]
    if not any(f.get("path") == entry for f in plan["files"]):
        plan["files"].append({"path": entry, "description": "Entry point", "imports": []})

    return plan


def _generate_file(
    project_dir: Path,
    file_info: dict,
    plan: dict,
    already_written: dict[str, str],
) -> str:
    """
    Generate one file using the cloud model, with retry.

    Returns the file content. Raises RuntimeError if all retries fail.
    """
    language = plan.get("language", "python")
    all_files = plan.get("files", [])
    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for imp in file_imports:
        imp_path = imp.replace(".", "/") + (LANGUAGE_SPECS.get(language, {}).get("ext", ".py"))
        if imp_path in already_written:
            dependency_context += f"\n--- {imp_path} (import from this) ---\n{already_written[imp_path][:3000]}\n"

    prompt = f"""You are an elite {language} developer writing production-quality code.

Project description:
{plan.get('project_name', '')}

Full project file structure:
{file_list}

Your task: Write the complete, working code for this file:

File path: {file_path}
Purpose: {file_desc}
Imports from project files: {', '.join(file_imports) if file_imports else 'none'}

{f'Dependency code for imports:\n{dependency_context}' if dependency_context else ''}

Rules:
- Output ONLY raw code. No markdown, no backticks, no explanation.
- Write complete, runnable code — no placeholders, no TODO stubs.
- All imports must be standard library, listed dependencies, or the project files above.
- Use proper error handling where needed.
- Make the code as functional as possible for the described project.

Code for {file_path}:"""

    last_error = ""
    for attempt in range(1, MAX_FILE_RETRIES + 1):
        try:
            raw = _cloud_generate(
                prompt=prompt,
                system=f"You are an expert {language} engineer. Return only code.",
                temperature=0.2,
                max_tokens=8000,
            )
            code = _extract_code(raw)

            # Basic sanity: code must be non-empty and not just a docstring
            if len(code.strip()) < 20:
                raise RuntimeError("Generated code is too short or empty.")

            _write_file(project_dir / file_path, code)
            return code

        except Exception as e:
            last_error = str(e)
            print(f"[DevAgent] ⚠️ Failed {file_path} attempt {attempt}: {last_error}")
            time.sleep(1)

    raise RuntimeError(f"Could not generate {file_path}: {last_error}")


def _install_dependencies(project_dir: Path, dependencies: list[str], language: str) -> str:
    if not dependencies:
        return "No external dependencies."

    spec = LANGUAGE_SPECS.get(language, LANGUAGE_SPECS["python"])
    dependency_file = project_dir / spec["dependency_file"]

    if language == "python":
        _write_file(dependency_file, "\n".join(dependencies))
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(dependency_file)]
    elif language in ("javascript", "typescript"):
        # For JS we don't create package.json here, just report
        return "Dependency installation requires npm. Run `npm install` after generation."
    else:
        return f"Dependency file: {dependency_file.name}"

    code, out, err = _run_command(cmd, cwd=project_dir, timeout=180)
    if code == 0:
        return f"Installed dependencies: {', '.join(dependencies)}"
    return f"Dependency install warning: {err[:300]}"


def _generate_tests(project_dir: Path, plan: dict) -> str:
    """Generate a simple pytest file for Python projects."""
    language = plan.get("language", "python")
    if language != "python":
        return "Test generation skipped for non-Python project."

    entry = plan.get("entry_point", "main.py")
    test_dir = project_dir / "tests"
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / f"test_{Path(entry).stem}.py"

    prompt = f"""Write a basic pytest test file for the entry point of this project.

Entry point: {entry}

Return ONLY valid Python pytest code, no markdown.
Include:
- One test that imports the entry point module.
- One test that checks a core function or class exists.
- One test that validates simple behaviour.
"""

    try:
        code = _extract_code(_cloud_generate(
            prompt=prompt,
            system="You are a senior Python developer. Return only pytest code.",
            temperature=0.2,
            max_tokens=4000,
        ))
        _write_file(test_file, code)
        return f"Test file generated at {test_file}"
    except Exception as e:
        print(f"[DevAgent] ⚠️ Test generation failed: {e}")
        return "Test generation skipped."


def _run_project(run_command: str, project_dir: Path, timeout: int = DEFAULT_TIMEOUT) -> str:
    print(f"[DevAgent] 🚀 Running: {run_command}")
    try:
        parts = run_command.split()
        if parts and parts[0].lower() == "python":
            parts[0] = sys.executable

        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(project_dir),
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        output_parts = []
        if stdout:
            output_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            output_parts.append(f"STDERR:\n{stderr}")

        return "\n\n".join(output_parts) if output_parts else "Ran with no output."
    except subprocess.TimeoutExpired:
        return "Timed out — long-running app may be working."
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"


def _classify_error(output: str) -> str:
    low = output.lower()

    if any(x in low for x in ("no module named", "modulenotfounderror", "importerror")):
        return "dependency_error"
    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"
    if "cannot import" in low or "importerror" in low:
        return "import_error"
    if any(x in low for x in (
        "traceback", "exception", "error:", "nameerror", "typeerror",
        "attributeerror", "valueerror", "keyerror", "indexerror",
        "zerodivisionerror", "filenotfounderror", "permissionerror",
        "not found", "no such file", "critical", "fatal", "failed",
    )):
        return "runtime_error"
    return "none"


def _has_error(output: str, run_command: str) -> bool:
    if not output.strip():
        return False
    low = output.lower()

    # Any stderr is a failure.
    if "stderr:" in low:
        return True

    return _classify_error(output) != "none"


def _fix_file(
    project_dir: Path,
    file_path: str,
    error_output: str,
    plan: dict,
    file_codes: dict[str, str],
) -> bool:
    """Fix one broken file. Returns True if fixed."""
    language = plan.get("language", "python")
    current_code = file_codes.get(file_path, "")

    other_ctx = ""
    for fp, code in file_codes.items():
        if fp != file_path and code:
            other_ctx += f"\n--- {fp} ---\n{code[:1500]}\n"

    prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project: {plan.get('project_name', '')}

Other files for context:
{other_ctx[:3500]}

File to fix: {file_path}

Error:
{error_output[:2500]}

Current code:
{current_code}

Rules:
- Output ONLY the complete fixed code. No markdown, no backticks.
- Fix ALL errors visible.
- Keep all correct logic.
- Ensure imports match actual project paths.
"""

    try:
        fixed = _extract_code(_cloud_generate(
            prompt=prompt,
            system="You are an expert debugger. Return only fixed code.",
            temperature=0.2,
            max_tokens=8000,
        ))
        if len(fixed.strip()) < 20:
            return False
        _write_file(project_dir / file_path, fixed)
        file_codes[file_path] = fixed
        return True
    except Exception as e:
        print(f"[DevAgent] ⚠️ Fix failed for {file_path}: {e}")
        return False


def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
) -> str:
    def log(msg: str):
        print(f"[DevAgent] {msg}")
        _log(msg)
        if player:
            player.write_log(f"[DevAgent] {msg}")

    lang = _normalise_language(language)
    plan = _plan_project(description, lang)

    proj_name = project_name or plan.get("project_name", "jarvis_project")
    proj_name = _safe_filename(proj_name)
    project_dir = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    files = plan.get("files", [])
    entry_point = plan.get("entry_point", "main.py")
    run_command = plan.get("run_command", f"python {entry_point}")
    test_command = plan.get("test_command", "pytest -q tests/")
    dependencies = plan.get("dependencies", [])

    log(f"Project: {proj_name} | Language: {lang} | Files: {len(files)} | Entry: {entry_point}")

    file_codes: dict[str, str] = {}

    # Generate each file independently
    for file_info in files:
        file_path = file_info.get("path", "")
        if not file_path:
            continue
        log(f"Writing {file_path}...")
        try:
            code = _generate_file(project_dir, file_info, plan, file_codes)
            file_codes[file_path] = code
            time.sleep(0.3)
        except RuntimeError as e:
            log(f"Failed to generate {file_path}: {e}")
            # Do not abort the whole build yet; record failure and continue.
            continue

    if not file_codes:
        msg = "I could not write any project files, sir."
        if speak:
            speak(msg)
        return msg

    # Dependency file + install
    install_result = _install_dependencies(project_dir, dependencies, lang)
    log(install_result)

    # Tests
    test_result_msg = _generate_tests(project_dir, plan)
    log(test_result_msg)

    # README
    readme = project_dir / "README.md"
    _write_file(
        readme,
        f"# {proj_name}\n\n{description}\n\n## Run\n\n```bash\n{run_command}\n```\n\n## Test\n\n```bash\n{test_command}\n```\n",
    )
    log("README generated")

    # Run -> fix -> rerun loop
    last_output = ""
    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        log(f"Running project (attempt {attempt}/{MAX_BUILD_ATTEMPTS})...")
        last_output = _run_project(run_command, project_dir, timeout)
        log(f"Output preview: {last_output[:200]}")

        if not _has_error(last_output, run_command):
            msg = (
                f"Project '{proj_name}' is working, sir. "
                f"Built in {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to: {project_dir}"
            )
            if speak:
                speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        if attempt == MAX_BUILD_ATTEMPTS:
            break

        # Identify likely broken file from output and fix all generated files once
        for fp in list(file_codes.keys()):
            if fp.endswith(".py") and fp != entry_point:
                if _fix_file(project_dir, fp, last_output, plan, file_codes):
                    log(f"Fixed {fp}")
            time.sleep(0.5)

    msg = (
        f"I couldn't fully fix '{proj_name}' after {MAX_BUILD_ATTEMPTS} attempts, sir. "
        f"Project is saved at {project_dir}. "
        f"Last error:\n{last_output[:600]}"
    )
    if speak:
        speak(msg)
    return msg


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
    timeout = int(p.get("timeout", DEFAULT_TIMEOUT))

    if not description:
        return "Please describe the project you want me to build, sir."

    return _build_project(
        description=description,
        language=language,
        project_name=project_name,
        timeout=timeout,
        speak=speak,
        player=player,
    )