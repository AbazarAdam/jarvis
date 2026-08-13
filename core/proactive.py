"""
core/proactive.py — JARVIS Proactive Assistance Engine
Runs in the background and speaks system/time alerts via local TTS.
Can be toggled on/off from the UI.
"""

import threading
import time
import psutil
from datetime import datetime


class ProactiveAssistant:
    def __init__(self):
        self.enabled = threading.Event()
        self.enabled.set()   # default ON

        self._running = True
        self._last_triggered = {}
        self._tts_lock = threading.Lock()

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ProactiveAssistant"
        )
        self._thread.start()
        print("[Proactive] 🧠 Assistant started")

    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool):
        if enabled:
            self.enabled.set()
            print("[Proactive] ▶ Enabled")
        else:
            self.enabled.clear()
            print("[Proactive] ⏸ Disabled")

    def stop(self):
        self._running = False
        print("[Proactive] 🛑 Assistant stopped")

    # ------------------------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                if self.enabled.is_set():
                    self._check_time_context()
                    self._check_system_health()
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")
            time.sleep(120)   # check every 2 minutes

    # ------------------------------------------------------------------
    def _speak(self, text: str):
        """Speak locally using pyttsx3 — does NOT touch the Gemini model."""
        if not self.enabled.is_set():
            return

        try:
            import pyttsx3
        except ImportError:
            print(f"[Proactive] (no local TTS) {text}")
            return

        def _run():
            try:
                with self._tts_lock:
                    engine = pyttsx3.init()
                    engine.say(text)
                    engine.runAndWait()
            except Exception as e:
                print(f"[Proactive] TTS error: {e}")

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    def _check_time_context(self):
        hour = datetime.now().hour

        if hour == 8 and not self._has_triggered("morning_brief", 86400):
            self._mark("morning_brief")
            self._speak(
                "Good morning, sir. Would you like me to prepare your morning brief?"
            )

        elif hour == 23 and not self._has_triggered("evening", 86400):
            self._mark("evening")
            self._speak(
                "It's getting late, sir. Consider saving your work and shutting down."
            )

    # ------------------------------------------------------------------
    def _check_system_health(self):
        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > 90 and not self._has_triggered("cpu_high", 300):
                self._mark("cpu_high")
                self._speak(
                    "CPU usage is above 90 percent, sir. "
                    "Would you like me to list the top resource consumers?"
                )
        except Exception:
            pass

        try:
            mem = psutil.virtual_memory().percent
            if mem > 90 and not self._has_triggered("mem_high", 300):
                self._mark("mem_high")
                self._speak(
                    "Memory usage is above 90 percent, sir. "
                    "I can identify the processes using the most memory if you wish."
                )
        except Exception:
            pass

        try:
            battery = psutil.sensors_battery()
            if (
                battery
                and battery.percent < 20
                and not battery.power_plugged
                and not self._has_triggered("battery_low", 900)
            ):
                self._mark("battery_low")
                self._speak(
                    "Battery is below 20 percent and not charging, sir. "
                    "Please connect the power adapter."
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _has_triggered(self, key: str, cooldown_seconds: int) -> bool:
        last = self._last_triggered.get(key)
        if last and (time.time() - last) < cooldown_seconds:
            return True
        return False

    def _mark(self, key: str):
        self._last_triggered[key] = time.time()