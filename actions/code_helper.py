"""
actions/code_helper.py — Advanced Software Engineering Assistant for JARVIS.

Actions:
  write          → Generate clean, well-commented code
  edit           → Natural-language edit OR line-aware patch
  explain        → Explain what code does
  run            → Execute a script
  build          → Write → Run → Fix loop (max attempts)
  optimize       → Optimise performance / readability / best practices
  analyze        → Static analysis (ruff / flake8 / mypy / eslint)
  generate_tests → Generate pytest / jest test file
  test           → Run tests
  self_fix       → Run tests, fix errors automatically, rollback if needed
  git_status     → Show Git status
  git_diff       → Show Git diff
  git_commit     → Stage and commit changes
  screen_debug   → Screenshot + Gemini Vision analysis + optional fix
  auto           → Detect intent automatically
"""

import subprocess
import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
DESKTOP         = Path.home() / "Desktop"
LOG_DIR         = BASE_DIR / "logs"
LOG_FILE        = LOG_DIR / "code_helper.log"
MAX_BUILD_ATTEMPTS = 3

LANGUAGE_EXT = {
    "python": ".py", "py": ".py",
    "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts",
    "html": ".html", "css": ".css",
    "java": ".java", "cpp": ".cpp", "c": ".c",
    "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
    "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
}


def _log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def _load_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _clean_code(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _llm_chat(
    prompt: str,
    system: str = "You are an expert software engineer. Return only code or requested text.",
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Use the stable central ModelRouter instead of deprecated or_client."""
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


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext = LANGUAGE_EXT.get((language or "python").lower(), ".py")
    if output_path:
        p = Path(output_path)
        return p if p.is_absolute() else DESKTOP / p
    return DESKTOP / f"jarvis_code{ext}"


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    p = Path(file_path)
    if not p.exists():
        return "", f"File not found: {file_path}"
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:
        return "", f"Could not read file: {e}"


def _save_file(path: Path, content: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Saved to: {path}"
    except Exception as e:
        return f"Could not save: {e}"


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview   = "\n".join(all_lines[:lines])
    suffix    = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


def _has_error(output: str) -> bool:
    error_signals = [
        "error", "exception", "traceback", "syntaxerror", "nameerror",
        "typeerror", "stderr", "failed", "crash",
    ]
    return any(s in output.lower() for s in error_signals)


def _run_file(path: Path, args: list, timeout: int) -> str:
    interpreters = {
        ".py":  [sys.executable],
        ".js":  ["node"],
        ".ts":  ["ts-node"],
        ".sh":  ["bash"],
        ".ps1": ["powershell", "-File"],
        ".rb":  ["ruby"],
        ".php": ["php"],
    }
    interp = interpreters.get(path.suffix.lower())
    if not interp:
        return f"No interpreter for {path.suffix}."

    try:
        result = subprocess.run(
            interp + [str(path)] + (args or []),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(path.parent)
        )
        output = result.stdout.strip()
        error  = result.stderr.strip()
        parts  = []
        if output: parts.append(f"Output:\n{output}")
        if error:  parts.append(f"Stderr:\n{error}")
        return "\n\n".join(parts) if parts else "Executed with no output."
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    except FileNotFoundError:
        return f"Interpreter not found: {interp[0]}."
    except Exception as e:
        return f"Execution error: {e}"

# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

def _write_action(description, language, output_path, player=None) -> str:
    if not description:
        return "Please describe what you want me to write, sir."
    _log(f"Write requested: {description[:200]}")
    if player:
        player.write_log("[Code] Writing code...")

    lang   = language or "python"
    prompt = f"""You are an expert {lang} developer.
Write clean, working, well-commented {lang} code for the description below.
Return ONLY the code — no markdown, no explanation, no backticks.

Description: {description}
"""
    try:
        code = _llm_chat(prompt, system="You are an expert programmer. Return only code.")
        code = _clean_code(code)
        path = _resolve_save_path(output_path, lang)
        _save_file(path, code)
        _log(f"Wrote code to {path}")
        return f"Code written. Saved to: {path}\n\nPreview:\n{_preview(code)}"
    except Exception as e:
        return f"Could not generate code: {e}"


def _line_edit(path: Path, line_start: int, line_end: int, new_content: str) -> str:
    content = _read_file(str(path))[0]
    lines = content.splitlines()
    start = max(1, int(line_start)) - 1
    end = min(len(lines), int(line_end))
    new_lines = new_content.splitlines()
    updated = lines[:start] + new_lines + lines[end:]
    _save_file(path, "\n".join(updated))
    _log(f"Line edit {line_start}-{line_end} on {path}")
    return f"Edited {path} lines {line_start}-{line_end}."


def _edit_action(file_path, instruction, line_start=None, line_end=None, new_content=None, player=None) -> str:
    if not file_path:
        return "Please provide a file path to edit, sir."
    if not instruction and not (line_start and line_end and new_content):
        return "Please describe what change to make, sir."

    content, err = _read_file(file_path)
    if err:
        return err

    # Line-aware patch
    if line_start is not None and line_end is not None and new_content is not None:
        _log(f"Line edit {line_start}-{line_end} on {file_path}")
        if player:
            player.write_log("[Code] Editing file (line-aware)...")
        return _line_edit(Path(file_path), line_start, line_end, new_content)

    # Natural-language edit
    if player:
        player.write_log("[Code] Editing file...")

    prompt = f"""You are an expert code editor.
Apply the following change to the code below.
Return ONLY the complete updated code — no markdown, no backticks.

Change: {instruction}

Original code:
{content}

Updated code:"""
    try:
        edited = _llm_chat(prompt, system="You are an expert code editor. Return only code.")
        edited = _clean_code(edited)
    except Exception as e:
        return f"Could not edit code: {e}"

    status = _save_file(Path(file_path), edited)
    _log(f"Edited file: {file_path}")
    return f"File edited. {status}\n\nPreview:\n{_preview(edited)}"


def _explain_action(file_path, code, player=None) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain, sir."

    if player:
        player.write_log("[Code] Analyzing code...")

    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""
    try:
        return _llm_chat(prompt, system="You are a helpful code tutor.")
    except Exception as e:
        return f"Could not explain code: {e}"


def _run_action(file_path, args, timeout, player=None) -> str:
    if not file_path:
        return "Please provide a file path to run, sir."
    p = Path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"
    if player:
        player.write_log(f"[Code] Running {p.name}...")
    _log(f"Running {file_path}")
    return _run_file(p, args, timeout)


def _optimize_action(file_path, code, language, output_path, player=None) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to optimize, sir."

    if player:
        player.write_log("[Code] Optimizing code...")

    lang = language or "python"
    prompt = f"""You are an expert {lang} developer and code reviewer.
Optimize the following code for:
1. Performance — eliminate unnecessary operations, use efficient data structures
2. Readability — clear variable names, proper formatting, logical structure
3. Best practices — modern {lang} patterns, error handling, type hints if applicable
4. Remove dead code, redundant comments, and unnecessary complexity

Return ONLY the optimized code — no explanation, no markdown, no backticks.

Original code:
{code[:6000]}

Optimized code:"""
    try:
        optimized = _llm_chat(prompt, system="You are an expert code optimizer. Return only code.")
        optimized = _clean_code(optimized)
    except Exception as e:
        return f"Could not optimize code: {e}"

    save_path = Path(file_path) if file_path else _resolve_save_path(output_path, lang)
    status = _save_file(save_path, optimized)
    _log(f"Optimized {save_path}")

    original_lines  = len(code.splitlines())
    optimized_lines = len(optimized.splitlines())
    diff = original_lines - optimized_lines

    return (
        f"Code optimized. {status}\n"
        f"Lines: {original_lines} → {optimized_lines} "
        f"({'−' if diff > 0 else '+'}{abs(diff)} lines)\n\n"
        f"Preview:\n{_preview(optimized)}"
    )



def _build_action(description, language, output_path, args, timeout, player=None, speak=None) -> str:
    if not description:
        return "Please describe what you want me to build, sir."

    if player:
        player.write_log("[Code] Build started...")
    _log(f"Build started: {description[:200]}")

    lang = language or "python"

    try:
        code, path = _write_action(description, lang, output_path, player).split("\n", 1)[1].split("\n\n")[0]
        code = _read_file(str(path))[0]
    except Exception:
        # fallback if write_action returned error string
        return _write_action(description, lang, output_path, player)

    last_output = ""
    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        if player:
            player.write_log(f"[Code] Attempt {attempt}...")

        last_output = _run_file(path, args, timeout)

        if not _has_error(last_output):
            msg = f"Build complete, sir. Working after {attempt} attempt(s). Saved to {path}."
            if speak:
                speak(msg)
            _log(f"Build complete: {path}")
            return f"{msg}\n\nOutput:\n{last_output}"

        if player:
            player.write_log(f"[Code] Fixing (attempt {attempt})...")

        try:
            prompt = f"""The code below failed with the following error. Fix it.
Return ONLY the corrected code — no markdown.

Original goal: {description}
Error:
{last_output[:2000]}

Broken code:
{code}

Fixed code:"""
            fixed = _llm_chat(prompt, system="You are an expert debugger. Return only code.")
            fixed = _clean_code(fixed)
            _save_file(path, fixed)
            code = fixed
        except Exception as e:
            msg = f"Could not fix code on attempt {attempt}: {e}"
            if speak:
                speak(msg)
            return msg

    msg = f"Unable to build after {MAX_BUILD_ATTEMPTS} attempts. Last error: {last_output[:200]}"
    if speak:
        speak(msg)
    return f"{msg}\n\nLast code saved to: {path}"


# ---------------------------------------------------------------------------
# Static analysis / tests / git
# ---------------------------------------------------------------------------

def _static_analysis(path: Path) -> str:
    lang = path.suffix.lower()
    results = []
    if lang == ".py":
        for tool in ["ruff", "flake8", "mypy"]:
            code, out, err = _run_command([tool, str(path)], timeout=60)
            if code == 127:
                results.append(f"{tool}: not installed")
            else:
                results.append(f"--- {tool} ---\n{(out or err).strip()[:2000]}")
    elif lang in (".js", ".jsx", ".ts", ".tsx"):
        code, out, err = _run_command(["npx", "eslint", str(path)], timeout=60)
        results.append("--- eslint ---\n" + (out or err).strip()[:2000])
    else:
        results.append(f"No static analysis configured for {lang}.")
    return "\n".join(results)


def _run_command(cmd: list, timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out."
    except FileNotFoundError:
        return 127, "", "Command not found."


def _generate_tests(path: Path) -> str:
    lang = path.suffix.lower()
    if lang == ".py":
        content = _read_file(str(path))[0]
        prompt = f"""
Write a pytest test file for the following Python module.
Include tests for every public function.
Return ONLY valid Python code, no markdown fences.

Module:
```python
{content[:4000]}
```
"""
        try:
            test_code = _llm_chat(prompt, system="You are a senior Python developer. Return only Python code.")
            test_path = path.parent / f"test_{path.stem}.py"
            _save_file(test_path, _clean_code(test_code))
            _log(f"Generated tests: {test_path}")
            return f"Generated test file: {test_path}"
        except Exception as e:
            return f"Test generation failed: {e}"
    return f"Test generation not configured for {lang}."


def _run_tests(path: Path) -> str:
    lang = path.suffix.lower()
    if lang == ".py":
        test_path = path.parent / f"test_{path.stem}.py"
        if test_path.exists():
            cmd = ["pytest", str(test_path)]
        else:
            cmd = ["pytest", str(path)]
        return _run_command(cmd, timeout=120)[1] or _run_command(cmd, timeout=120)[2]
    return "Test runner not configured for this file type."


def _git_status() -> str:
    code, out, err = _run_command(["git", "status", "--short"], timeout=10)
    return (out or err).strip() or "No changes."


def _git_diff() -> str:
    code, out, err = _run_command(["git", "diff"], timeout=10)
    return (out or err).strip() or "No diff."


def _git_commit(message: str) -> str:
    code, out, err = _run_command(["git", "add", "-A"], timeout=10)
    if code != 0:
        return f"git add failed: {err}"
    code, out, err = _run_command(["git", "commit", "-m", message], timeout=30)
    if code == 0:
        return f"Committed: {message}"
    return f"git commit failed: {err}"



def _self_fix(path: Path, max_attempts: int = 3) -> str:
    original = _read_file(str(path))[0]
    for attempt in range(1, max_attempts + 1):
        # Ensure tests exist
        test_path = path.parent / f"test_{path.stem}.py"
        if path.suffix == ".py" and not test_path.exists():
            _generate_tests(path)

        test_output = _run_tests(path)
        if "FAILED" not in test_output and "ERROR" not in test_output:
            _log(f"Self-fix success after {attempt} attempts for {path}")
            return f"All tests pass after {attempt} attempt(s)."

        try:
            prompt = (
                "The following Python file has failing tests.\n"
                f"Test output:\n{test_output[:2000]}\n\n"
                "Current code:\n"
                "```python\n"
                f"{original[:4000]}\n"
                "```\n\n"
                "Fix the code so the tests pass. Return the complete corrected Python code only, no markdown."
            )
            fixed = _llm_chat(prompt, system="You are an expert Python developer. Return only fixed code.")
            _save_file(path, _clean_code(fixed))
            _log(f"Self-fix attempt {attempt} applied to {path}")
        except Exception as e:
            _save_file(path, original)
            _log(f"Self-fix attempt {attempt} failed: {e}")
            return f"Self-fix failed at attempt {attempt}: {e}"

    _save_file(path, original)
    _log(f"Self-fix failed after {max_attempts} attempts, rolled back {path}")
    return f"Self-fix failed after {max_attempts} attempts. Rolled back."

def _screen_debug_action(description: str, file_path: str, player=None, speak=None) -> str:
    """
    Capture a screenshot, analyse it with vision if available, and optionally
    return a debugging suggestion.

    This is a safe fallback implementation. It delegates vision analysis to
    JARVIS's existing screen processor when possible.
    """
    if not description:
        return "Please describe the issue you are seeing, sir."

    if speak:
        speak("Analysing the current screen for debugging, sir.")

    try:
        from actions.screen_processor import screen_process

        result = screen_process(
            parameters={"angle": "screen", "text": description},
            player=player,
            session_memory=None,
        )
        return result or "Screen analysis completed, sir."
    except Exception as e:
        return (
            "I could not run the screen debugger automatically, sir. "
            f"Reason: {e}. Please provide the file path or error text manually."
        )

def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p = parameters or {}
    action = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    language = p.get("language", "python").strip()
    output_path = p.get("output_path", "").strip()
    file_path = p.get("file_path", "").strip()
    code = p.get("code", "").strip()
    args = p.get("args", [])
    timeout = int(p.get("timeout", 30))
    line_start = p.get("line_start")
    line_end = p.get("line_end")
    new_content = p.get("new_content", "")
    attempts = int(p.get("attempts", 3))
    message = p.get("message", "JARVIS code change")

    path = Path(file_path).expanduser() if file_path else None

    if action == "auto":
        desc = description.lower()
        if any(k in desc for k in ["screen", "ekranda", "why am i getting", "what's wrong"]):
            action = "screen_debug"
        elif path and path.exists() and "edit" in desc:
            action = "edit"
        elif path and path.exists() and "run" in desc:
            action = "run"
        elif "build" in desc:
            action = "build"
        elif "explain" in desc:
            action = "explain"
        elif "optimize" in desc:
            action = "optimize"
        else:
            action = "write"

    if action == "write":
        return _write_action(description, language, output_path, player)
    elif action == "edit":
        return _edit_action(
            file_path,
            description or p.get("instruction", ""),
            line_start,
            line_end,
            new_content,
            player,
        )
    elif action == "explain":
        return _explain_action(file_path, code, player)
    elif action == "run":
        return _run_action(file_path, args, timeout, player)
    elif action == "build":
        return _build_action(description, language, output_path, args, timeout, player, speak)
    elif action == "optimize":
        return _optimize_action(file_path, code, language, output_path, player)
    elif action == "analyze":
        return _static_analysis(path) if path else "file_path required."
    elif action == "generate_tests":
        return _generate_tests(path) if path else "file_path required."
    elif action == "test":
        return _run_tests(path) if path else "file_path required."
    elif action == "git_status":
        return _git_status()
    elif action == "git_diff":
        return _git_diff()
    elif action == "git_commit":
        return _git_commit(message)
    elif action == "self_fix":
        return _self_fix(path, attempts) if path else "file_path required."
    elif action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)
    else:
        return f"Unknown action: '{action}'"