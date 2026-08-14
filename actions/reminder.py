"""
actions/reminder.py — JARVIS Persistent Alarm and Timer System

Supports:
  - timers via seconds / minutes
  - alarms via date / time (including "today", "tomorrow", "now+1min")

Alarms are created as Windows scheduled tasks, so they keep working even
after JARVIS is closed.
"""

import subprocess
import uuid
from pathlib import Path
from datetime import datetime, timedelta


BASE_DIR     = Path(__file__).resolve().parent.parent
REMINDER_DIR = BASE_DIR / "logs" / "reminders"


def _parse_reminder(params: dict) -> datetime:
    """Determine trigger time from parameters."""
    now = datetime.now()

    # 1) Timer
    seconds = params.get("seconds")
    minutes = params.get("minutes")
    if seconds is not None or minutes is not None:
        try:
            secs = int(seconds or 0) + int(minutes or 0) * 60
            return now + timedelta(seconds=secs)
        except Exception:
            pass

    # 2) Alarm
    date_str = str(params.get("date", "")).strip().lower()
    time_str = str(params.get("time", "")).strip()

    if not time_str:
        raise ValueError("Provide either seconds/minutes for a timer, or date and time for an alarm.")

    # Determine date
    if date_str in ("", "today", "[today]"):
        d = now.date()
    elif date_str in ("tomorrow", "[tomorrow]"):
        d = now.date() + timedelta(days=1)
    else:
        # Try common date formats
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                d = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Invalid date: {date_str}")

    # Determine time
    time_clean = time_str.replace("[", "").replace("]", "")
    time_clean = time_clean.replace("now+", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            t = datetime.strptime(time_clean, fmt).time()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Invalid time: {time_str}")

    trigger = datetime.combine(d, t)

    # If the trigger is already past, use tomorrow
    if trigger <= now:
        trigger += timedelta(days=1)

    return trigger


def _create_alert_script(message: str, reminder_id: str) -> Path:
    """Write a PowerShell script that shows a popup and plays an alarm sound."""
    REMINDER_DIR.mkdir(parents=True, exist_ok=True)
    script = REMINDER_DIR / f"alert_{reminder_id}.ps1"

    safe_message = message.replace("'", "''")

    script_content = f"""
Add-Type -AssemblyName System.Windows.Forms
$alarm = [System.Media.SoundPlayer]::new("C:\\Windows\\Media\\Alarm01.wav")
$alarm.PlayLooping()

[System.Windows.Forms.MessageBox]::Show('{safe_message}', 'JARVIS Reminder', 0, 64)

$alarm.Stop()
"""

    script.write_text(script_content, encoding="utf-8")
    return script


def _create_scheduled_task(trigger_time: datetime, message: str) -> str:
    """Create a Windows scheduled task via PowerShell for a specific time."""
    reminder_id = uuid.uuid4().hex[:8]
    script_path = _create_alert_script(message, reminder_id)

    task_name = f"JARVIS Reminder {reminder_id}"

    iso_time = trigger_time.strftime("%Y-%m-%dT%H:%M:%S")

    ps_command = f"""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-ExecutionPolicy Bypass -WindowStyle Hidden -File "{script_path}"'
$trigger = New-ScheduledTaskTrigger -Once -At '{iso_time}'
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Force
"""

    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ps_command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if proc.returncode == 0:
            return (
                f"Reminder set for {trigger_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(Task: {task_name}). It will work even if JARVIS is closed."
            )
        return f"Reminder creation failed: {proc.stderr.strip() or proc.stdout.strip()}"
    except Exception as e:
        return f"Reminder creation failed: {e}"


def reminder(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """
    Set an alarm or timer.

    parameters:
        message   : string, the reminder message
        seconds   : int, countdown seconds (for timer)
        minutes   : int, countdown minutes (for timer)
        date      : string, date YYYY-MM-DD or "today" / "tomorrow"
        time      : string, time HH:MM 24h or HH:MM AM/PM
    """
    params  = parameters or {}
    message = params.get("message", "Time's up!")

    try:
        trigger_time = _parse_reminder(params)
    except Exception as e:
        return str(e)

    result = _create_scheduled_task(trigger_time, message)

    if player:
        player.write_log(result)

    return result