"""
actions/reminder.py — JARVIS Alarm and Timer System
Supports one-time alarms (date/time) and countdown timers (seconds/minutes).
Plays an alarm sound and shows a popup notification until acknowledged.
"""

import ctypes
import threading
import time
import winsound
from datetime import datetime, timedelta
from pathlib import Path


def _parse_reminder(params: dict) -> datetime:
    """Determine trigger time from parameters."""
    # Timer: seconds or minutes
    seconds = params.get("seconds")
    minutes = params.get("minutes")
    if seconds is not None or minutes is not None:
        try:
            secs = int(seconds or 0) + int(minutes or 0) * 60
            return datetime.now() + timedelta(seconds=secs)
        except Exception:
            pass

    # Alarm: date and time
    date_str = params.get("date", "")
    time_str = params.get("time", "")
    if not date_str or not time_str:
        raise ValueError("Provide either seconds/minutes for a timer, or date and time for an alarm.")

    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
        except Exception as e:
            raise ValueError(f"Invalid date/time format: {e}")


def _play_alarm_and_notify(message: str):
    """Show a popup and play a proper Windows alarm sound until dismissed."""
    sound_paths = [
        Path(r"C:\Windows\Media\Alarm01.wav"),
        Path(r"C:\Windows\Media\Alarm02.wav"),
        Path(r"C:\Windows\Media\Alarm03.wav"),
        Path(r"C:\Windows\Media\Alarm04.wav"),
    ]
    sound_file = next((p for p in sound_paths if p.exists()), None)

    if sound_file:
        winsound.PlaySound(
            str(sound_file),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP
        )
    else:
        # Fallback: repeated beep
        for _ in range(20):
            winsound.Beep(1000, 300)
            time.sleep(0.3)
        return

    # Popup that stops the alarm when dismissed
    try:
        MB_OK = 0x0
        MB_ICONINFORMATION = 0x40
        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "JARVIS Reminder",
            MB_OK | MB_ICONINFORMATION,
        )
    finally:
        winsound.PlaySound(None, winsound.SND_PURGE)


def _worker(trigger_time: datetime, message: str):
    """Wait until trigger time, then alert."""
    now = datetime.now()
    if trigger_time > now:
        time.sleep((trigger_time - now).total_seconds())
    _play_alarm_and_notify(message)


def reminder(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """
    Set an alarm or timer.

    parameters:
        message   : string, the reminder message
        seconds   : int, countdown seconds (for timer)
        minutes   : int, countdown minutes (for timer)
        date      : string, date YYYY-MM-DD (for alarm)
        time      : string, time HH:MM (for alarm)
    """
    params = parameters or {}
    message = params.get("message", "Time's up!")
    try:
        trigger_time = _parse_reminder(params)
    except Exception as e:
        return str(e)

    if trigger_time <= datetime.now():
        trigger_time = datetime.now() + timedelta(seconds=1)

    threading.Thread(
        target=_worker,
        args=(trigger_time, message),
        daemon=True,
    ).start()

    if player:
        player.write_log(f"Reminder set for {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}")

    return f"Reminder set for {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}."