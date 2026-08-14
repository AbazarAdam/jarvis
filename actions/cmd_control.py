"""
actions/cmd_control.py — Execute system commands for agent tasks.
"""

import subprocess


def cmd_control(parameters: dict, player=None, speak=None) -> str:
    task = (parameters or {}).get("task", "").strip()
    if not task:
        return "No command specified."

    try:
        proc = subprocess.run(
            task,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = proc.stdout.strip() or proc.stderr.strip()
        return output[:2000] if output else "Command completed with no output."
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except Exception as e:
        return f"Command failed: {e}"