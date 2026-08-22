import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision
from core.execution_guard import preflight_tool_call
from core.skill_store import SkillStore


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _call_cloud_llm(messages: list[dict], max_tokens: int = 1800) -> str:
    """Use the central ModelRouter instead of deprecated google.generativeai."""
    from core.model_router import ModelRouter

    response = ModelRouter().chat(
        messages=messages,
        temperature=0.2,
        max_tokens=max_tokens,
    )

    if not response.get("success"):
        raise RuntimeError(response.get("error") or "Cloud model failed.")

    return response["text"].strip()

def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    if speak:
        speak("Writing custom code for this task, sir.")

    home = Path.home()
    desktop = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    prompt = (
        "You are an expert Python developer. "
        "Write clean, complete, working Python code. "
        "Use standard library + common packages. "
        "Install missing packages with subprocess + pip if needed. "
        "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
        f"SYSTEM PATHS:\n"
        f"  Desktop   = r'{desktop}'\n"
        f"  Downloads = r'{downloads}'\n"
        f"  Documents = r'{documents}'\n"
        f"  Home      = r'{home}'\n\n"
        f"Write Python code to accomplish this task:\n\n{description}"
    )

    messages = [
        {"role": "system", "content": "You are an elite software engineer. Return only Python code."},
        {"role": "user", "content": prompt},
    ]

    code = _call_cloud_llm(messages, max_tokens=3000)
    code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_path = f.name

    print(f"[Executor] 🐍 Running generated code: {tmp_path}")

    result = subprocess.run(
        [sys.executable, tmp_path],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path.home()),
    )

    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    output = result.stdout.strip()
    error = result.stderr.strip()

    if result.returncode == 0 and output:
        return output
    if result.returncode == 0:
        return "Task completed successfully."
    if error:
        raise RuntimeError(f"Code error: {error[:400]}")
    return "Completed."

def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)

    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        # Gather all substantial results
        all_results = [
            v for v in step_results.values()
            if v and len(v) > 100 and v not in ("Done.", "Completed.")
        ]
        if all_results:
            combined = all_results[-1]
            # Block writing useless disclaimers
            if any(phrase in combined for phrase in [
                "I cannot", "I will not", "unauthorized", "I am unable"
            ]):
                params["content"] = "Research could not be completed — the requested action was blocked."
            else:
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] 💉 Injected content (len={len(translated)})")

    return params


def _detect_language(text: str) -> str:
    try:
        messages = [
            {"role": "system", "content": "Reply with ONLY the language name in English."},
            {"role": "user", "content": f"What language is this text written in?\n\nText: {text[:200]}"},
        ]
        return _call_cloud_llm(messages, max_tokens=20).strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )

        messages = [
            {"role": "system", "content": "You are a professional translator. Return only the translated text."},
            {"role": "user", "content": prompt},
        ]

        translated = _call_cloud_llm(messages, max_tokens=3000)
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content
    


def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:

    # ------------------------------------------------------------------
    # CORTEX + SAFETY PREFLIGHT — same gate as main.py
    # ------------------------------------------------------------------
    if tool != "skill_runner":
        try:
            from main import TOOL_DECLARATIONS, PLUGIN_DECLARATIONS

            try:
                learned_skills = SkillStore().list_skills(include_all=False)
            except Exception:
                learned_skills = []

            guard_decision = preflight_tool_call(
                name=tool,
                parameters=parameters,
                tool_declarations=TOOL_DECLARATIONS,
                plugin_declarations=PLUGIN_DECLARATIONS,
                skills=learned_skills,
            )

            if not guard_decision.get("allowed"):
                reason = guard_decision.get("reason", "Blocked by execution guard.")
                print(f"[Executor] 🚫 Blocked {tool}: {reason}")
                return f"Blocked by execution guard: {reason}"
        except Exception as guard_err:
            print(f"[Executor] ⚠️ Execution guard error: {guard_err}")

    if tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=parameters, player=None) or "Done."

    elif tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=parameters, player=None) or "Done."
    
    elif tool == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=parameters, player=None) or "Done."

    elif tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=parameters, player=None) or "Done."

    elif tool == "cmd_control":
        from actions.cmd_control import cmd_control
        return cmd_control(parameters=parameters, player=None) or "Done."

    elif tool == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "dev_agent":
        from actions.dev_agent import dev_agent
        return dev_agent(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "screen_process":
        from actions.screen_processor import screen_process
        screen_process(parameters=parameters, player=None)
        return "Screen captured and analyzed."

    elif tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=parameters, player=None) or "Done."


    elif tool == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=parameters, player=None) or "Done."

    elif tool == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=parameters, player=None) or "Done."

    elif tool == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=parameters, player=None) or "Done."

    elif tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)


    elif tool == "file_processor":
        from actions.file_processor import file_processor as _file_processor
        # file_processor expects a 'file_path' and returns a string
        result = _file_processor(parameters=parameters, player=None, speak=speak)
        return result or "Done."

    elif tool == "cmd_control":
        from actions.cmd_control import cmd_control
        return cmd_control(parameters=parameters, player=None) or "Done."

    elif tool == "skill_runner":
        from plugins.skill_runner import execute as skill_runner_execute
        return skill_runner_execute(parameters=parameters, player=None, speak=speak) or "Done."

    else:
        print(f"[Executor] ⚠️ Unknown tool '{tool}' — falling back to generated_code")
        return _run_generated_code(f"Accomplish this task: {parameters}", speak=speak)

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
    ) -> str:
        print(f"\n[Executor] 🎯 Goal: {goal}")

        replan_attempts = 0
        completed_steps = []
        step_results    = {} 
        plan            = create_plan(goal)

        while True:
            steps = plan.get("steps", [])

            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return "Task cancelled."

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})

                params = _inject_context(params, tool, step_results, goal=goal)

                print(f"\n[Executor] ▶️ Step {step_num}: [{tool}] {desc}")

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak)
                        step_results[step_num] = result 
                        completed_steps.append(step)
                        print(f"[Executor] ✅ Step {step_num} done: {str(result)[:100]}")
                        step_ok = True
                        break

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] ❌ Step {step_num} attempt {attempt} failed: {error_msg}")

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] ⏭️ Skipping step {step_num}")
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else: 
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach, sir.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak
                                    )
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except Exception as fix_err:
                                    print(f"[Executor] ⚠️ Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach, sir.")

            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        try:
            steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
            prompt = (
                f'User goal: "{goal}"\n'
                f"Completed steps:\n{steps_str}\n\n"
                "Write ONE short, natural sentence confirming completion. "
                "Address the user as 'sir'. Do NOT use generic phrases like 'Done, sir.' or 'Thank you, sir.' every time. "
                "Vary the wording: e.g., 'The report is on your desktop.', 'All finished, sir.', 'Here you go, sir.' "
                "Be warm but concise."
            )

            messages = [
                {"role": "system", "content": "You are JARVIS. Summarise completion naturally."},
                {"role": "user", "content": prompt},
            ]

            summary = _call_cloud_llm(messages, max_tokens=120)
            if summary:
                if speak:
                    speak(summary)
                return summary
        except Exception:
            pass

        if speak:
            speak(fallback)
        return fallback