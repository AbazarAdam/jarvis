"""
plugins/system_management.py — Full system control for JARVIS.

Actions:
  get_system_status  → CPU, memory, disk, battery, network
  set_wifi           → enable/disable Wi‑Fi
  set_power_plan     → high_performance | balanced | power_saver
  set_jarvis_feature → proactive | remote | mute (on/off)
"""

import platform
import socket
import subprocess
import psutil

PLUGIN_INFO = {
    "name": "system_management",
    "description": (
        "Read system health and control OS features: "
        "get_system_status, set_wifi, set_power_plan, set_jarvis_feature."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "get_system_status | set_wifi | set_power_plan | set_jarvis_feature"
            },
            "enabled": {
                "type": "BOOLEAN",
                "description": "True to enable, False to disable (for set_wifi, set_jarvis_feature)"
            },
            "plan": {
                "type": "STRING",
                "description": "high_performance | balanced | power_saver (for set_power_plan)"
            },
            "feature": {
                "type": "STRING",
                "description": "proactive | remote | mute (for set_jarvis_feature)"
            }
        },
        "required": ["action"]
    }
}


def _run_powershell(script: str, timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "PowerShell timed out."
    except Exception as e:
        return 1, "", str(e)


def _get_system_status() -> str:
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("C:\\")
    disk_free_gb = disk.free / (1024 ** 3)
    disk_total_gb = disk.total / (1024 ** 3)

    battery = psutil.sensors_battery()
    if battery:
        battery_str = f"{battery.percent}% {'(charging)' if battery.power_plugged else '(on battery)'}"
    else:
        battery_str = "No battery (desktop)"

    try:
        import requests
        ip_info = requests.get("https://api.ipify.org?format=json", timeout=5).json()
        public_ip = ip_info.get("ip", "Unknown")
    except Exception:
        public_ip = "Unknown"

    return (
        f"CPU: {cpu}%\n"
        f"Memory: {mem}%\n"
        f"Disk: {disk_free_gb:.1f} GB free of {disk_total_gb:.1f} GB\n"
        f"Battery: {battery_str}\n"
        f"Public IP: {public_ip}"
    )


def _set_wifi(enabled: bool) -> str:
    if platform.system() != "Windows":
        return "Wi‑Fi control is only supported on Windows."

    state = "enable" if enabled else "disable"
    script = f'netsh interface set interface "Wi-Fi" admin={state}'
    code, out, err = _run_powershell(script)
    if code == 0:
        return f"Wi‑Fi has been {state}d, sir."
    return f"Wi‑Fi control failed: {err or out}"


def _set_power_plan(plan: str) -> str:
    if platform.system() != "Windows":
        return "Power plan control is only supported on Windows."

    plan_guids = {
        "high_performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
        "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
    }
    guid = plan_guids.get(plan.lower())
    if not guid:
        return f"Unknown power plan: {plan}. Use high_performance, balanced, or power_saver."

    code, out, err = _run_powershell(f"powercfg /setactive {guid}")
    if code == 0:
        return f"Power plan set to {plan.replace('_', ' ')}, sir."
    return f"Power plan change failed: {err or out}"


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()

    if action == "get_system_status":
        return _get_system_status()

    elif action == "set_wifi":
        enabled = bool(parameters.get("enabled", True))
        return _set_wifi(enabled)

    elif action == "set_power_plan":
        plan = parameters.get("plan", "balanced")
        return _set_power_plan(plan)

    elif action == "set_jarvis_feature":
        feature = parameters.get("feature", "").lower()
        enabled = bool(parameters.get("enabled", True))
        # We'll implement actual toggles in Part 2, using player/UI methods.
        return f"Feature '{feature}' will be handled in Part 2."

    return f"Unknown action: {action}"

def _set_jarvis_feature(player, feature: str, enabled: bool) -> str:
    """Toggle JARVIS internal features: proactive, remote, mute."""
    win = getattr(player, "_win", None)

    # Proactive assistance
    if feature == "proactive":
        if win:
            win._proactive_enabled = enabled
            win._style_proactive_btn()
            win._proactive_toggle_signal.emit(enabled)
            return f"Proactive mode {'enabled' if enabled else 'disabled'}, sir."
        return "Proactive control unavailable."

    # Remote access (ngrok tunnel)
    elif feature == "remote":
        if win:
            current = getattr(win, "_remote_active", False)
            if current != enabled:
                win._toggle_remote_access()
            return f"Remote access {'enabled' if enabled else 'disabled'}, sir."
        return "Remote access control unavailable."

    # Microphone mute
    elif feature == "mute":
        if hasattr(player, "muted"):
            player.muted = enabled
            return f"Microphone {'muted' if enabled else 'unmuted'}, sir."
        return "Mute control unavailable."

    return f"Unknown JARVIS feature: {feature}"


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()

    if action == "get_system_status":
        return _get_system_status()

    elif action == "set_wifi":
        enabled = bool(parameters.get("enabled", True))
        return _set_wifi(enabled)

    elif action == "set_power_plan":
        plan = parameters.get("plan", "balanced")
        return _set_power_plan(plan)

    elif action == "set_jarvis_feature":
        feature = parameters.get("feature", "").lower().strip()
        enabled = bool(parameters.get("enabled", True))
        return _set_jarvis_feature(player, feature, enabled)

    return f"Unknown action: {action}"

