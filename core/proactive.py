"""
core/proactive.py — JARVIS Proactive Assistance Engine
Checks system health and time context, then speaks a suggestion when triggered.
"""

import threading
import time
import psutil
from datetime import datetime


class ProactiveAssistant:
    def __init__(self, speak_callback, ui=None):
        self.speak_callback = speak_callback
        self.ui = ui
        self._running = True
        self._last_triggered = {}

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ProactiveAssistant"
        )
        self._thread.start()
        print("[Proactive] 🧠 Assistant started")

    def _loop(self):
        while self._running:
            try:
                self._check_time_context()
                self._check_system_health()
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")
            time.sleep(120)  # check every 2 minutes

    # ------------------------------------------------------------------
    # Time‑based suggestions
    # ------------------------------------------------------------------
    def _check_time_context(self):
        hour = datetime.now().hour

        # Morning brief suggestion at 8 AM (once per day)
        if hour == 8 and not self._has_triggered("morning_brief", 86400):
            self._mark("morning_brief")
            self._speak(
                "Good morning, sir. Would you like me to prepare your morning brief?"
            )

        # Evening wind‑down at 11 PM (once per day)
        elif hour == 23 and not self._has_triggered("evening", 86400):
            self._mark("evening")
            self._speak(
                "It's getting late, sir. Consider saving your work and shutting down."
            )

    # ------------------------------------------------------------------
    # System health alerts
    # ------------------------------------------------------------------
    def _check_system_health(self):
        # High CPU
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

        # High memory
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

        # Low battery (laptops only)
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
    # Helpers
    # ------------------------------------------------------------------
    def _speak(self, text: str):
        """Forward a proactive message to Jarvis's voice output."""
        try:
            if self.speak_callback:
                self.speak_callback(text)
        except Exception as e:
            print(f"[Proactive] ⚠️ Speak error: {e}")

    def _has_triggered(self, key: str, cooldown_seconds: int) -> bool:
        last = self._last_triggered.get(key)
        if last and (time.time() - last) < cooldown_seconds:
            return True
        return False

    def _mark(self, key: str):
        self._last_triggered[key] = time.time()

    def stop(self):
        self._running = False
        print("[Proactive] 🛑 Assistant stopped")