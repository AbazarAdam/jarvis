"""
actions/dev_agent.py — Advanced Full‑SDLC Project Builder for JARVIS.

This module enables JARVIS to:
  - analyse requirements and plan a complete project
  - scaffold files and directories
  - install dependencies
  - write tests
  - generate CI/CD configs and Dockerfile
  - initialise Git and commit
  - run / debug / auto‑fix the project
  - record project state for background status

It is designed to be called in the background, so JARVIS remains responsive.
"""

import subprocess
import sys
import json
import re
import time
import shutil
from pathlib import Path
from datetime import datetime


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
PROJECTS_DIR     = Path.home() / "Desktop" / "JarvisProjects"
LOG_DIR          = BASE_DIR / "logs"
DEV_AGENT_LOG    = LOG_DIR / "dev_agent.log"

MAX_FIX_ATTEMPTS = 5
MODEL_PLANNER    = "gemini-2.5-flash"
MODEL_WRITER     = "gemini-2.5-flash"

LANGUAGE_EXT = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "html": ".html",
    "css": ".css",
    "java": ".java",
    "go": ".go",
    "rust": ".rs",
}


def _log(message: str) -> None:
    """Append an entry to the dev_agent log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DEV_AGENT_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_model(model_name: str):
    """Compatibility stub. Direct Gemini SDK usage has been removed."""
    raise NotImplementedError("Use _llm_chat or _cloud_generate instead.")


def _cloud_generate(
    prompt: str,
    system: str = "You are a senior software architect. Return only requested output.",
    max_tokens: int = 3000,
) -> str:
    """Generate text using the stable central ModelRouter."""
    from core.model_router import ModelRouter

    response = ModelRouter().generate(
        prompt=prompt,
        system=system,
        temperature=0.2,
        max_tokens=max_tokens,
    )

    if not response.get("success"):
        raise RuntimeError(response.get("error") or "Cloud model failed.")

    return response["text"].strip()


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg


class RateLimitError(Exception):
    pass


def _parse_traceback(output: str, project_files: list[str]) -> tuple[str | None, int | None]:
    """Find the first project file mentioned in a Python traceback."""
    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)

    for raw_path, line_str in reversed(matches):
        raw_name = Path(raw_path).name
        for pf in project_files:
            if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                return pf, int(line_str)

    return None, None


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
    )):
        return "runtime_error"

    return "none"


def _has_error(output: str, run_command: str) -> bool:
    low = output.lower()

    if "timed out" in low:
        return False

    if not output.strip():
        return False

    return _classify_error(output) != "none"


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
      "imports": ["utils.helpers", "core.engine"]
    }},
    {{
      "path": "utils/helpers.py",
      "description": "Helper utilities — what functions it exposes",
      "imports": []
    }}
  ],
  "run_command": "python main.py",
  "dependencies": ["requests"],
  "tests": ["test_example.py"]
}}

Critical rules:
1. List files in DEPENDENCY ORDER — files with no imports come first, entry point comes last.
2. The "imports" field must list every other project module this file imports (dot-notation).
3. Keep it minimal — only files truly needed.
4. Entry point must be in the files list.
5. Use relative paths only.
6. Standard library modules do NOT go in "dependencies".

JSON:"""

    try:
        raw = _strip_fences(_cloud_generate(prompt))
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw: {raw[:300]}")
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise


def _llm_chat(
    prompt: str,
    system: str = "You are an expert software engineer. Return only the requested output.",
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Use the stable central ModelRouter for all code generation."""
    from core.model_router import ModelRouter

    response = ModelRouter().generate(
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    if not response.get("success"):
        raise RuntimeError(response.get("error") or "Cloud model failed.")

    return response["text"].strip()


def _write_file(
    file_info: dict,
    project_description: str,
    all_files: list[dict],
    language: str,
    project_dir: Path,
    already_written: dict[str, str],
) -> str:
    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for dep_dotted in file_imports:
        dep_path = dep_dotted.replace(".", "/") + ".py"
        if dep_path in already_written:
            code_snippet = already_written[dep_path][:2000]
            dependency_context += f"\n\n--- {dep_path} (import from this) ---\n{code_snippet}"

    lang_rules = ""
    if language.lower() == "python":
        lang_rules = """
Python-specific rules:
- Use type hints for all function signatures.
- Add docstrings for all public functions and classes.
- Use if __name__ == "__main__": guard in the entry point.
- Match import paths exactly to the project file structure.
- Create __init__.py files where needed for packages.
"""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        lang_rules = """
JS/TS-specific rules:
- Use ES modules (import/export), not CommonJS (require).
- Add JSDoc comments for all exported functions.
- Handle promise rejections with try/catch in async functions.
"""

    prompt = f"""You are a senior {language} developer writing production-quality code for a real project.

Project goal: {project_description}

Complete project file structure (in dependency order):
{file_list}

{f"Dependencies this file must import from other project files:{dependency_context}" if dependency_context else ""}

Your task: Write the complete, working code for: {file_path}
Purpose of this file: {file_desc}
{f"This file imports from: {', '.join(file_imports)}" if file_imports else "This file has no project-internal imports."}

{lang_rules}

General rules:
- Output ONLY raw code. Absolutely no explanation, no markdown, no triple backticks.
- Write COMPLETE, RUNNABLE code — no placeholders, no "# TODO", no "pass" stubs.
- Every import must either be from the standard library, listed dependencies, or the project files shown above.
- Match import paths EXACTLY to the file paths in the project structure.
- Use proper error handling (try/except) where I/O or network calls are made.
- The code must work correctly when the project entry point is run from the project root directory.

Code for {file_path}:"""

    code = _strip_fences(_llm_chat(prompt))
    full_path = project_dir / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(code, encoding="utf-8")

    print(f"[DevAgent] ✅ Written: {file_path} ({len(code)} chars)")
    return code


def _install_dependencies(dependencies: list[str], project_dir: Path) -> str:
    if not dependencies:
        return "No external dependencies."

    to_install = []
    for dep in dependencies:
        pkg_name = re.split(r"[>=<!]", dep)[0].strip()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", pkg_name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            to_install.append(dep)
        else:
            print(f"[DevAgent] ✓ Already installed: {pkg_name}")

    if not to_install:
        return f"All dependencies already installed: {', '.join(dependencies)}"

    print(f"[DevAgent] 📦 Installing: {to_install}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + to_install,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=180, cwd=str(project_dir),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            return f"Installed: {', '.join(to_install)}"
        return f"Install warning (non-fatal): {result.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return "Dependency install timed out (non-fatal)."
    except Exception as e:
        return f"Install error (non-fatal): {e}"


def _git_init(project_dir: Path, project_name: str) -> str:
    try:
        if (project_dir / ".git").exists():
            return "Git already initialised."

        code, out, err = _run_command(["git", "init"], cwd=str(project_dir))
        if code != 0:
            return f"git init failed: {err}"

        # Create .gitignore
        gitignore = project_dir / ".gitignore"
        gitignore.write_text(
            "__pycache__/\n*.pyc\n.venv/\n.env\n*.log\n.DS_Store\nnode_modules/\n",
            encoding="utf-8",
        )

        # Initial commit
        _run_command(["git", "add", "-A"], cwd=str(project_dir))
        code, out, err = _run_command(
            ["git", "commit", "-m", f"Initial commit for {project_name}"],
            cwd=str(project_dir),
        )
        if code == 0:
            return "Git initialised and initial commit created."
        return f"Git initialised, but commit failed: {err}"
    except Exception as e:
        return f"Git initialisation error: {e}"


def _generate_ci_config(project_dir: Path, language: str, test_command: str) -> str:
    workflow_dir = project_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)

    if language.lower() == "python":
        ci = f"""name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: {test_command}
"""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        ci = f"""name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install
      - run: {test_command}
"""
    else:
        ci = f"""name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: {test_command}
"""

    (workflow_dir / "ci.yml").write_text(ci, encoding="utf-8")
    return f"CI workflow generated at {workflow_dir / 'ci.yml'}"


def _generate_dockerfile(project_dir: Path, language: str, run_command: str) -> str:
    dockerfile = project_dir / "Dockerfile"

    if language.lower() == "python":
        content = f"""FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["{run_command.split()[0]}", "{' '.join(run_command.split()[1:]) if len(run_command.split()) > 1 else '.'}"]
"""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        content = f"""FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

CMD ["npm", "start"]
"""
    else:
        content = f"""FROM ubuntu:latest

WORKDIR /app

COPY . .

CMD ["{run_command.split()[0]}", "{' '.join(run_command.split()[1:]) if len(run_command.split()) > 1 else '.'}"]
"""

    dockerfile.write_text(content, encoding="utf-8")
    return f"Dockerfile generated at {dockerfile}"


def _generate_readme(project_dir: Path, project_name: str, description: str, run_command: str) -> str:
    readme = project_dir / "README.md"
    readme.write_text(
        f"# {project_name}\n\n{description}\n\n"
        "## Setup\n\n"
        "```bash\n"
        "pip install -r requirements.txt\n"
        "```\n\n"
        "## Run\n\n"
        f"```bash\n{run_command}\n```\n",
        encoding="utf-8",
    )
    return f"README generated at {readme}"

def _run_command(cmd: list, cwd: str = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a command safely and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out."
    except FileNotFoundError:
        return 127, "", "Command not found."
    except Exception as e:
        return 1, "", str(e)


def _open_vscode(project_dir: Path) -> bool:
    vscode_candidates = [
        "code",
        rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    ]
    for cmd in vscode_candidates:
        try:
            subprocess.Popen(
                [cmd, str(project_dir)],
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            print(f"[DevAgent] 💻 VSCode opened: {project_dir}")
            return True
        except Exception:
            continue
    return False


def _run_project(run_command: str, project_dir: Path, timeout: int = 30) -> str:
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

        combined_parts = []
        if stdout:
            combined_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            combined_parts.append(f"STDERR:\n{stderr}")

        return "\n\n".join(combined_parts) if combined_parts else "Ran with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s — long-running app (server/GUI) is likely working."
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"


def _try_auto_install(error_output: str, project_dir: Path) -> bool:
    """Try to auto-install missing packages detected in ModuleNotFoundError."""
    pattern = re.compile(
        r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.IGNORECASE
    )
    match = pattern.search(error_output)
    if not match:
        return False

    pkg = match.group(1).replace("_", "-").split(".")[0]
    print(f"[DevAgent] 🔧 Auto-installing missing package: {pkg}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=str(project_dir),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.returncode == 0
    except Exception:
        return False


def _fix_files(
    error_output: str,
    project_description: str,
    all_files: list[dict],
    file_codes: dict[str, str],
    language: str,
    project_dir: Path,
    entry_point: str,
) -> dict[str, str]:
    """Use the LLM to fix the files responsible for the current error."""
    error_file, error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    files_to_fix: list[str] = []

    if error_file:
        files_to_fix.append(error_file)
        if error_type == "import_error":
            for fi in all_files:
                if error_file.replace("/", ".").replace(".py", "") in fi.get("imports", []):
                    p = fi["path"]
                    if p not in files_to_fix:
                        files_to_fix.append(p)
    else:
        files_to_fix.append(entry_point)

    updated_codes: dict[str, str] = {}

    for fix_path in files_to_fix:
        current_code = file_codes.get(fix_path, "")

        other_ctx = ""
        for fp, code in file_codes.items():
            if fp != fix_path and code:
                snippet = code[:1500] + ("..." if len(code) > 1500 else "")
                other_ctx += f"\n--- {fp} ---\n{snippet}\n"

        line_hint = ""
        if error_line and fix_path == error_file:
            line_hint = f"\nError appears to be near line {error_line} in this file."

        prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project goal: {project_description}

All project files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

Other files for context (read-only — fix only the target file):
{other_ctx[:3500]}

File to fix: {fix_path}{line_hint}
Error type: {error_type}

Error output:
{error_output[:2500]}

Current (broken) code:
{current_code}

Rules:
- Output ONLY the complete fixed code. No explanation, no markdown, no backticks.
- Fix ALL errors visible in the error output.
- Keep all existing correct logic — do not remove working features.
- Ensure import paths match the actual project file structure exactly.
- Do NOT introduce new bugs or remove error handling.

Fixed code for {fix_path}:"""

        try:
            fixed = _strip_fences(_llm_chat(prompt))
            full_path = project_dir / fix_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(fixed, encoding="utf-8")

            updated_codes[fix_path] = fixed
            print(f"[DevAgent] 🔧 Fixed: {fix_path}")
        except RateLimitError:
            raise
        except Exception as e:
            print(f"[DevAgent] ⚠️ Could not fix {fix_path}: {e}")

    return updated_codes


def _write_tests(project_dir: Path, all_files: list[dict], entry_point: str) -> str:
    """Generate a simple pytest file for Python projects."""
    if not any(f["path"].endswith(".py") for f in all_files):
        return "Test generation skipped for non-Python project."

    test_dir = project_dir / "tests"
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / f"test_{Path(entry_point).stem}.py"

    prompt = f"""Write a basic pytest test file for the entry point of this project.

Entry point: {entry_point}
Files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

Return ONLY valid Python pytest code, no markdown.
Include at least:
- One test that imports the entry point module.
- One test that checks a core function or class exists.
- One test that validates a simple behaviour (e.g., function returns expected value)."""

    test_code = _strip_fences(_llm_chat(prompt))
    test_file.write_text(test_code, encoding="utf-8")
    print(f"[DevAgent] 🧪 Tests written: {test_file}")
    return f"Test file generated at {test_file}"


def _generate_requirements_file(project_dir: Path, dependencies: list[str]) -> str:
    if not dependencies:
        (project_dir / "requirements.txt").write_text("", encoding="utf-8")
        return "No dependencies — empty requirements.txt created."

    content = "\n".join(dependencies)
    (project_dir / "requirements.txt").write_text(content, encoding="utf-8")
    return f"requirements.txt generated with {len(dependencies)} dependencies."

def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
) -> str:
    """Full SDLC pipeline: plan -> scaffold -> write -> test -> run -> fix -> deliver."""

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        _log(msg)
        if player:
            player.write_log(f"[DevAgent] {msg}")

    log("Planning project structure...")
    try:
        plan = _plan_project(description, language)
    except RateLimitError:
        msg = "Rate limit reached, sir. Please try again in a moment."
        if speak:
            speak(msg)
        return msg
    except ValueError as e:
        msg = f"Planning failed: {e}"
        if speak:
            speak(msg)
        return msg

    proj_name = project_name or plan.get("project_name", "jarvis_project")
    proj_name = re.sub(r"[^\w\-]", "_", proj_name)
    project_dir = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    files = plan.get("files", [])
    entry_point = plan.get("entry_point", "main.py")
    run_command = plan.get("run_command", f"python {entry_point}")
    dependencies = plan.get("dependencies", [])
    tests = plan.get("tests", [])

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point}")

    # Sort files by dependency order
    def _dep_sort_key(fi: dict) -> int:
        return len(fi.get("imports", []))

    sorted_files = sorted(files, key=_dep_sort_key)

    file_codes: dict[str, str] = {}

    for file_info in sorted_files:
        file_path = file_info.get("path", "")
        if not file_path:
            continue

        log(f"Writing {file_path}...")
        for attempt in range(2):
            try:
                code = _write_file(
                    file_info=file_info,
                    project_description=description,
                    all_files=files,
                    language=language,
                    project_dir=project_dir,
                    already_written=file_codes,
                )
                file_codes[file_path] = code
                time.sleep(0.4)
                break
            except RateLimitError:
                if attempt == 0:
                    log("Rate limit — waiting 20s...")
                    time.sleep(20)
                else:
                    log(f"Rate limit retry failed for {file_path}, skipping.")
            except Exception as e:
                log(f"Failed to write {file_path}: {e}")
                break

    if not file_codes:
        msg = "I could not write any project files, sir."
        if speak:
            speak(msg)
        return msg

    # Generate requirements.txt
    req_result = _generate_requirements_file(project_dir, dependencies)
    log(req_result)

    # Install dependencies
    install_result = _install_dependencies(dependencies, project_dir)
    log(install_result)

    # Generate tests if Python
    if language.lower() == "python":
        test_result = _write_tests(project_dir, files, entry_point)
        log(test_result)

    # Generate CI/CD config
    ci_result = _generate_ci_config(project_dir, language, "pytest -q tests/")
    log(ci_result)

    # Generate Dockerfile
    docker_result = _generate_dockerfile(project_dir, language, run_command)
    log(docker_result)

    # Generate README
    readme_result = _generate_readme(project_dir, proj_name, description, run_command)
    log(readme_result)

    # Git init + initial commit
    git_result = _git_init(project_dir, proj_name)
    log(git_result)

    # Open VSCode
    _open_vscode(project_dir)

    # Run -> fix -> rerun loop
    last_output = ""
    auto_installs = 0

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        log(f"Running project (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")
        last_output = _run_project(run_command, project_dir, timeout)
        log(f"Output preview: {last_output[:150]}")

        if not _has_error(last_output, run_command):
            msg = (
                f"Project '{proj_name}' is working, sir. "
                f"Built in {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to: {project_dir}"
            )
            if speak:
                speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        if attempt == MAX_FIX_ATTEMPTS:
            break

        error_type = _classify_error(last_output)
        if error_type == "dependency_error" and auto_installs < 3:
            installed = _try_auto_install(last_output, project_dir)
            if installed:
                auto_installs += 1
                log("Missing dependency installed, retrying...")
                time.sleep(1)
                continue

        log(f"Fixing errors (type: {error_type})...")
        try:
            updated = _fix_files(
                error_output=last_output,
                project_description=description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
            )
            file_codes.update(updated)
            time.sleep(1)
        except RateLimitError:
            msg = "Rate limit reached during fix. Project saved, check it manually in VSCode."
            if speak:
                speak(msg)
            return msg
        except Exception as e:
            log(f"Fix step failed: {e}")

    msg = (
        f"I couldn't fully fix '{proj_name}' after {MAX_FIX_ATTEMPTS} attempts, sir. "
        f"Project is saved at {project_dir} — open it in VSCode and check manually."
    )
    if speak:
        speak(msg)
    return f"{msg}\n\nLast error:\n{last_output[:600]}"


def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    parameters:
        description   — what the project should do
        language      — programming language (default: python)
        project_name  — optional folder name
        timeout       — run timeout in seconds (default: 30)
    """
    p = parameters or {}
    description = p.get("description", "").strip()
    language = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()
    timeout = int(p.get("timeout", 30))

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

