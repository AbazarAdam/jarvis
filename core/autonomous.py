"""
core/autonomous.py — Goal -> plan -> execute for JARVIS autonomous task.

This module provides:
    generate_plan(goal, max_steps) -> list[dict]
    execute_workflow(goal, tool_registry, ...) -> str

The planner uses ModelRouter to translate a high-level goal into a strict
JSON workflow. The executor runs each step via a caller-provided registry
of tool functions, respects cancellation, and reports honest success/failure.
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional

from core.model_router import ModelRouter


MAX_TOTAL_SECONDS = 300  # hard wall-clock cap for a whole workflow


AVAILABLE_TOOLS = """
Available tools (use ONLY these exact names):

- web_search: Search the web.
  parameters: {"query": "string", "mode": "search|compare", "items": ["..."], "aspect": "string"}
- file_controller: File operations.
  parameters: {"action": "list|create_file|create_folder|read|write|find|info", "path": "string", "content": "string", "name": "string"}
- file_processor: Process an uploaded/known file (summarize, extract, convert, etc).
  parameters: {"file_path": "string", "action": "string", "instruction": "string"}
- computer_control: Type, click, hotkey, screenshot, wait.
  parameters: {"action": "type|click|hotkey|press|screenshot|wait", "text": "string", "key": "string", "keys": "string", "seconds": number}
- browser_control: Control the web browser.
  parameters: {"action": "go_to|search|click|type|scroll|press", "url": "string", "query": "string", "text": "string", "key": "string"}
- open_app: Launch an application.
  parameters: {"app_name": "string"}
- reminder: Set a timer or alarm.
  parameters: {"message": "string", "seconds": int, "minutes": int}
- tell_time: Get current date/time.
  parameters: {}
- desktop_control: Wallpaper, organize desktop, list, stats.
  parameters: {"action": "wallpaper|organize|list|stats"}
- code_helper: Write/read/edit/run/explain code files.
  parameters: {"action": "write|read|edit|run|explain", "description": "string", "file_path": "string", "language": "string", "output_path": "string"}
- news: Latest cybersecurity/AI/software news.
  parameters: {"category": "cybersecurity|ai|software", "count": int}
- morning_brief: Generate morning brief.
  parameters: {"save": bool, "speak": bool}
"""


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from arbitrary text."""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def generate_plan(goal: str, max_steps: int = 10, context: str = "") -> list[dict]:
    """Ask ModelRouter to produce a strict JSON workflow for the given goal."""
    prompt = f"""You are a task planner for an AI assistant.

Goal: {goal}

{context}

{AVAILABLE_TOOLS}

Produce a step-by-step plan with AT MOST {max_steps} steps.
- Each step must call exactly ONE tool from the list above.
- Use the exact tool name and the exact parameter schema.
- Keep steps minimal — do not add unnecessary steps.
- If the goal is a single action, return a single step.
- Never invent tool names or parameters.
- Preserve any file paths, URLs, or literal strings from the goal verbatim.

IMPORTANT — chaining between steps:
If a later step needs the output of an earlier step, use a placeholder
instead of inventing content:
  $stepN   -> the result of step N (e.g. "$step1")
  $prev    -> the result of the immediately preceding step
Example — save step 1's search results to a file:
  {{"step": 2, "tool": "file_controller",
    "parameters": {{"action": "create_file",
                    "path": "Desktop/report.txt",
                    "content": "$step1"}}}}
Never guess or fabricate the content that a previous step will produce.
Always use a placeholder so the executor can substitute the real value.

Return ONLY this JSON shape, no markdown, no prose:

{{
  "steps": [
    {{
      "step": 1,
      "tool": "web_search",
      "parameters": {{"query": "example"}},
      "reasoning": "brief reason"
    }}
  ]
}}
"""

    try:
        response = ModelRouter().generate(
            prompt=prompt,
            system="Return ONLY valid JSON. No markdown, no explanation.",
            temperature=0.2,
            max_tokens=1500,
        )
    except Exception as e:
        print(f"[Autonomous] ModelRouter failed during planning: {e}")
        return []

    if not response.get("success"):
        print(f"[Autonomous] No provider available for planning: {response.get('error')}")
        return []

    data = _extract_json(response.get("text", ""))
    if not data or "steps" not in data or not isinstance(data["steps"], list):
        print("[Autonomous] Planner returned invalid shape.")
        return []

    clean: list[dict] = []
    for i, s in enumerate(data["steps"][:max_steps]):
        if not isinstance(s, dict):
            continue
        tool = str(s.get("tool", "")).strip()
        params = s.get("parameters", {})
        if not tool or not isinstance(params, dict):
            continue
        clean.append({
            "step": i + 1,
            "tool": tool,
            "parameters": params,
            "reasoning": str(s.get("reasoning", ""))[:200],
        })

    return clean


def _looks_like_failure(text: str) -> bool:
    """Heuristic: does a tool result string look like a failure?"""
    low = (text or "").lower().strip()
    if not low:
        return True
    markers = (
        "error", "failed", "could not", "unable to",
        "not found", "exception", "search failed",
        "no results", "not available", "timed out",
    )
    return any(m in low for m in markers)


def _substitute_placeholders(params, results_by_step: dict[int, str]):
    """Recursively substitute $stepN and $prev placeholders in parameters."""
    import copy

    def _sub(value):
        if not isinstance(value, str):
            return value
        s = value
        if "$prev" in s and results_by_step:
            last_n = max(results_by_step.keys())
            s = s.replace("$prev", results_by_step[last_n])
        s = re.sub(
            r"\$step(\d+)",
            lambda m: results_by_step.get(int(m.group(1)), ""),
            s,
        )
        return s

    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return _sub(node)

    return _walk(copy.deepcopy(params))


def _summarize_step(step: dict, result: str) -> str:
    text = str(result or "").replace("\n", " ")
    if len(text) > 300:
        text = text[:297] + "..."
    return f"Step {step.get('step')} [{step.get('tool')}]: {text}"


def execute_workflow(
    goal: str,
    tool_registry: dict[str, Callable[[dict], str]],
    speak: Optional[Callable[[str], None]] = None,
    stop_event=None,
    max_steps: int = 10,
) -> str:
    """Plan and execute an autonomous workflow for the given goal."""
    start_time = time.time()
    plan = generate_plan(goal, max_steps=max_steps)
    if not plan:
        return "I could not generate a plan, sir. AI provider may be unavailable."

    results: list[dict] = []
    context_lines: list[str] = []
    results_by_step: dict[int, str] = {}

    for step in plan:
        if time.time() - start_time > MAX_TOTAL_SECONDS:
            return "Workflow exceeded time limit, sir. Partial results:\n" + "\n".join(context_lines)

        if stop_event and stop_event.is_set():
            return "Workflow cancelled, sir. Partial results:\n" + "\n".join(context_lines)

        step_num = step["step"]
        tool_name = step["tool"]
        raw_params = dict(step.get("parameters", {}))
        params = _substitute_placeholders(raw_params, results_by_step)

        tool_fn = tool_registry.get(tool_name)
        if not tool_fn:
            results.append({"step": step_num, "tool": tool_name, "success": False,
                            "error": f"Tool '{tool_name}' not permitted for autonomous use."})
            context_lines.append(f"Step {step_num} [{tool_name}]: FAILED — not permitted")
            continue

        try:
            print(f"[Autonomous] Step {step_num}: {tool_name} {params}")
            result = tool_fn(params)
            result_str = str(result or "")
            success = not _looks_like_failure(result_str)
            results.append({"step": step_num, "tool": tool_name, "success": success,
                            "result": result_str[:500]})
            context_lines.append(_summarize_step(step, result_str))
            if success:
                results_by_step[step_num] = result_str
        except Exception as e:
            err = str(e)
            print(f"[Autonomous] Step {step_num} failed: {err}")
            results.append({"step": step_num, "tool": tool_name, "success": False,
                            "error": err[:500]})
            context_lines.append(f"Step {step_num} [{tool_name}]: FAILED — {err[:150]}")

    ok = sum(1 for r in results if r.get("success"))
    total = len(results)

    lines = [f"Autonomous workflow complete: {ok}/{total} steps succeeded.", ""]
    for r in results:
        marker = "OK" if r.get("success") else "FAIL"
        detail = (r.get("result") or r.get("error") or "")[:200]
        lines.append(f"[{marker}] Step {r['step']} [{r['tool']}] — {detail}")
    return "\n".join(lines)