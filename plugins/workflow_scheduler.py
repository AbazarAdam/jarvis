"""
plugins/workflow_scheduler.py — JARVIS Autonomous Workflow Scheduler Interface.

Allows JARVIS to create, list, remove, run, and manage scheduled workflows.

Uses core/workflow_scheduler.py under the hood.

Workflows execute existing JARVIS tools/plugins/agent tasks automatically.
"""

from __future__ import annotations

from typing import Any, Optional

from core.workflow_scheduler import WorkflowScheduler, parse_schedule_text


PLUGIN_INFO = {
    "name": "workflow_scheduler",
    "description": (
        "Create, list, run, or remove scheduled/recurring workflows. "
        "Use for requests like 'every Sunday at 10 AM run a security scan', "
        "'every 30 minutes check the news', or 'schedule a project report at 5 PM'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "create | list | run | remove | status"
            },
            "name": {
                "type": "STRING",
                "description": "Workflow name"
            },
            "tool_name": {
                "type": "STRING",
                "description": "Tool/plugin name to run, e.g. security_mode, news, agent_task"
            },
            "params": {
                "type": "OBJECT",
                "description": "Parameters to pass to the tool/plugin"
            },
            "schedule": {
                "type": "STRING",
                "description": "Natural schedule, e.g. 'every day at 09:00', 'every 30 minutes', 'every Sunday at 10:00'"
            },
            "workflow_id": {
                "type": "STRING",
                "description": "Workflow ID for remove/run/status"
            }
        },
        "required": ["action"]
    }
}


_scheduler: Optional[WorkflowScheduler] = None


def get_scheduler() -> WorkflowScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = WorkflowScheduler()
    return _scheduler


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()
    scheduler = get_scheduler()

    if action == "create":
        name = (parameters or {}).get("name", "").strip()
        tool_name = (parameters or {}).get("tool_name", "").strip()
        params = (parameters or {}).get("params") or {}
        schedule = (parameters or {}).get("schedule", "").strip()

        if not tool_name:
            return "Please provide a tool_name to schedule, sir."
        if not schedule:
            return "Please provide a schedule, sir."

        workflow = scheduler.add_workflow(
            name=name or f"{tool_name}_workflow",
            tool_name=tool_name,
            params=params,
            schedule=schedule,
        )

        return (
            f"Scheduled workflow '{workflow.name}' created, sir. "
            f"Next run: {workflow.next_run}."
        )

    elif action == "list":
        workflows = scheduler.list_workflows()
        if not workflows:
            return "No scheduled workflows yet, sir."

        lines = []
        for wf in workflows:
            status = "enabled" if wf.get("enabled") else "disabled"
            lines.append(
                f"- {wf.get('name')} [{wf.get('id')}] | {wf.get('tool_name')} | "
                f"{status} | next: {wf.get('next_run') or 'not scheduled'}"
            )
        return "Scheduled workflows:\n" + "\n".join(lines)

    elif action == "run":
        workflow_id = (parameters or {}).get("workflow_id", "").strip()
        if not workflow_id:
            return "Please provide a workflow_id to run now, sir."

        wf = scheduler.get_workflow(workflow_id)
        if not wf:
            return f"No workflow found with ID '{workflow_id}', sir."

        tool_name = wf.get("tool_name", "")
        params = wf.get("params", {})

        try:
            from plugins.workflow_scheduler import run_tool_by_name
            result = run_tool_by_name(tool_name, params, player, speak)
            return f"Workflow '{wf.get('name')}' executed. Result: {str(result)[:200]}"
        except Exception as e:
            return f"Workflow '{wf.get('name')}' failed: {e}"

    elif action == "remove":
        workflow_id = (parameters or {}).get("workflow_id", "").strip()
        if not workflow_id:
            return "Please provide a workflow_id to remove, sir."

        if scheduler.remove_workflow(workflow_id):
            return f"Workflow '{workflow_id}' removed, sir."
        return f"No workflow found with ID '{workflow_id}', sir."

    elif action == "status":
        workflow_id = (parameters or {}).get("workflow_id", "").strip()
        if not workflow_id:
            return "Please provide a workflow_id to check, sir."

        wf = scheduler.get_workflow(workflow_id)
        if not wf:
            return f"No workflow found with ID '{workflow_id}', sir."
        return (
            f"Workflow: {wf.get('name')}\n"
            f"Tool: {wf.get('tool_name')}\n"
            f"Schedule type: {wf.get('schedule_type')}\n"
            f"Enabled: {wf.get('enabled')}\n"
            f"Last run: {wf.get('last_run') or 'never'}\n"
            f"Next run: {wf.get('next_run') or 'not scheduled'}"
        )

    return f"Unknown workflow_scheduler action: {action}"


def run_tool_by_name(tool_name: str, params: dict, player=None, speak=None) -> Any:
    """
    Dispatch a tool/plugin by name for scheduled execution.

    This is intentionally centralised so the scheduler does not need to
    import every possible module directly.
    """
    tool_name = (tool_name or "").strip()
    params = dict(params or {})

    # Plugins first
    if tool_name == "news":
        from plugins.news_plugin import execute as news_execute
        return news_execute(params, player=player, speak=speak)

    if tool_name == "stopwatch":
        from plugins.stopwatch import execute as stopwatch_execute
        return stopwatch_execute(params, player=player, speak=speak)

    if tool_name == "project_builder":
        from plugins.project_builder import execute as builder_execute
        return builder_execute(params, player=player, speak=speak)

    if tool_name == "codebase_insight":
        from plugins.codebase_plugin import execute as codebase_execute
        return codebase_execute(params, player=player, speak=speak)

    if tool_name == "skill_runner":
        from plugins.skill_runner import execute as skill_runner_execute
        return skill_runner_execute(params, player=player, speak=speak)

    if tool_name == "workflow_scheduler":
        return execute(params, player=player, speak=speak)

    # Core actions
    if tool_name == "security_mode":
        from actions.security_mode import security_mode
        return security_mode(params, player=player, speak=speak)

    if tool_name == "morning_brief":
        from actions.morning_brief import morning_brief
        return morning_brief(params, player=player, speak=speak)

    if tool_name == "agent_task":
        from agent.task_queue import get_queue, TaskPriority

        def _run_agent(parameters, player=None):
            priority_map = {
                "low": TaskPriority.LOW,
                "normal": TaskPriority.NORMAL,
                "high": TaskPriority.HIGH,
            }
            priority = priority_map.get(
                str(parameters.get("priority", "normal")).lower(),
                TaskPriority.NORMAL,
            )
            task_id = get_queue().submit(
                goal=parameters.get("goal", ""),
                priority=priority,
                speak=None,
            )
            import time as _time
            while True:
                status = get_queue().get_status(task_id)
                if status and status["status"] in ("completed", "failed", "cancelled"):
                    break
                _time.sleep(2)
            final = get_queue().get_status(task_id)
            return final.get("result") or final.get("error") or "Done."

        return _run_agent(params, player)

    if tool_name == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(params, player=player)

    if tool_name == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(params, player=player)

    if tool_name == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(params, player=player)

    if tool_name == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(params, player=player, speak=speak)

    if tool_name == "web_search":
        from actions.web_search import web_search
        return web_search(params, player=player)

    raise RuntimeError(f"Scheduler cannot dispatch unknown tool: {tool_name}")