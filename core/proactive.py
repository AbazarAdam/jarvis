"""
core/proactive.py — JARVIS Proactive Intelligence v2.

A modular, quiet, cooldown-gated suggestion engine.

Default: OFF.

When enabled, JARVIS learns from:
    - long-term memory
    - reflection memory
    - recent user activity
    - system health
    - known domains

It speaks only when a genuinely useful suggestion is found, using local TTS.
It never interrupts the live Gemini conversation path.
"""

from __future__ import annotations

import json
import ssl
import socket
import threading
import time
import psutil
from collections import deque, Counter
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
PATTERNS_FILE = MEMORY_DIR / "proactive_patterns.json"


class ProactiveAssistant:
    def __init__(self):
        self.enabled = threading.Event()  # default OFF

        self._running = True
        self._last_triggered = {}
        self._tts_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self.speak_callback = None

        # Recent user commands/tasks, useful for pattern discovery
        self._recent_activities = deque(maxlen=120)

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ProactiveAssistant"
        )
        self._thread.start()
        print("[Proactive] 🧠 Assistant started")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool):
        if enabled:
            self.enabled.set()
            print("[Proactive] ▶ Enabled")
            # Run one immediate evaluation so the user gets feedback
            threading.Thread(target=self._evaluate_suggestions, daemon=True).start()
        else:
            self.enabled.clear()
            print("[Proactive] ⏸ Disabled")

    def stop(self):
        self._running = False
        print("[Proactive] 🛑 Assistant stopped")

    def note_user_activity(self, text: str) -> None:
        """Record a user command/task for later pattern detection."""
        text = (text or "").strip()
        if not text or len(text) < 3:
            return

        with self._activity_lock:
            self._recent_activities.append({
                "text": text,
                "time": time.time(),
            })

        self._detect_and_save_pattern(text)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                if self.enabled.is_set():
                    self._evaluate_suggestions()
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")
            time.sleep(90)  # quiet check interval

    def _evaluate_suggestions(self):
        """Gather candidate suggestions and speak the best one if allowed."""
        suggestions = []

        suggestions.extend(self._check_time_context())
        suggestions.extend(self._check_system_health())
        suggestions.extend(self._check_ssl_expiry())
        suggestions.extend(self._check_recent_failures())
        suggestions.extend(self._check_recurring_tasks())
        suggestions.extend(self._check_disk_space())

        if not suggestions:
            return

        # Sort by priority, highest first
        suggestions.sort(key=lambda s: s.get("priority", 0), reverse=True)

        for suggestion in suggestions[:1]:
            key = suggestion.get("key", "generic")
            cooldown = suggestion.get("cooldown", 7200)
            if self._has_triggered(key, cooldown):
                continue

            self._mark(key)
            self._speak(suggestion.get("text", ""))
            break

    # ------------------------------------------------------------------
    # Suggestion modules
    # ------------------------------------------------------------------
    def _check_time_context(self) -> list[dict]:
        hour = datetime.now().hour
        results = []

        if hour == 8 and not self._has_triggered("morning_brief", 86400):
            results.append({
                "key": "morning_brief",
                "priority": 10,
                "cooldown": 86400,
                "text": "Good morning, sir. Would you like me to prepare your morning brief?",
            })

        elif hour == 13 and not self._has_triggered("midday_check", 86400):
            results.append({
                "key": "midday_check",
                "priority": 4,
                "cooldown": 86400,
                "text": "It's early afternoon, sir. I can summarise your unread emails or start a quick system check if useful.",
            })

        elif hour == 23 and not self._has_triggered("evening", 86400):
            results.append({
                "key": "evening",
                "priority": 8,
                "cooldown": 86400,
                "text": "It's getting late, sir. Consider saving your work and shutting down.",
            })

        return results

    def _check_system_health(self) -> list[dict]:
        results = []

        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > 88 and not self._has_triggered("cpu_high", 600):
                results.append({
                    "key": "cpu_high",
                    "priority": 7,
                    "cooldown": 600,
                    "text": "CPU usage is above 88 percent, sir. Would you like me to list the top consumers?",
                })
        except Exception:
            pass

        try:
            mem = psutil.virtual_memory().percent
            if mem > 88 and not self._has_triggered("mem_high", 600):
                results.append({
                    "key": "mem_high",
                    "priority": 7,
                    "cooldown": 600,
                    "text": "Memory usage is above 88 percent, sir. I can identify the processes using the most memory.",
                })
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
                results.append({
                    "key": "battery_low",
                    "priority": 9,
                    "cooldown": 900,
                    "text": "Battery is below 20 percent and not charging, sir. Please connect the power adapter.",
                })
        except Exception:
            pass

        return results

    def _check_ssl_expiry(self) -> list[dict]:
        """Warn about known domains whose SSL cert expires within 14 days."""
        results = []
        domains = self._known_domains()
        if not domains:
            return []

        for domain in domains:
            days_left = self._ssl_days_left(domain)
            if days_left is None:
                continue

            key = f"ssl_expiry_{domain}"
            if days_left <= 14 and not self._has_triggered(key, 86400):
                results.append({
                    "key": key,
                    "priority": 8,
                    "cooldown": 86400,
                    "text": f"The SSL certificate for {domain} expires in {days_left} days, sir.",
                })

        return results

    def _check_recent_failures(self) -> list[dict]:
        """Suggest fixing a recent failed task from reflection memory."""
        try:
            from core.reflection import ReflectionMemory

            failures = ReflectionMemory().recent_failures(limit=5)
        except Exception:
            return []

        if not failures:
            return []

        failure = failures[-1]
        goal = failure.get("goal", "task")
        error = (failure.get("error", "") or "")[:120]
        text = f"I noticed a previous task failed: {goal}."
        if error:
            text += f" The error was: {error}."
        text += " Would you like me to retry it?"

        return [{
            "key": "recent_failure",
            "priority": 6,
            "cooldown": 3600,
            "text": text,
        }]

    def _check_recurring_tasks(self) -> list[dict]:
        """Detect repeated user commands and suggest automation."""
        with self._activity_lock:
            activities = list(self._recent_activities)

        if len(activities) < 3:
            return []

        # Count exact/lightly normalised commands
        command_counts = Counter()
        command_examples = {}

        for item in activities:
            text = self._normalise_activity(item["text"])
            if len(text) < 4:
                continue
            command_counts[text] += 1
            command_examples[text] = item["text"]

        if not command_counts:
            return []

        most_common_text, count = command_counts.most_common(1)[0]
        if count < 3:
            return []

        if self._has_triggered("recurring_task", 43200):
            return []

        original = command_examples.get(most_common_text, "")
        if not original:
            return []

        return [{
            "key": "recurring_task",
            "priority": 5,
            "cooldown": 43200,
            "text": (
                f"I noticed you often ask me to '{original}'. "
                "Would you like me to learn this as a reusable skill or schedule it?"
            ),
        }]

    def _check_disk_space(self) -> list[dict]:
        results = []
        try:
            usage = psutil.disk_usage(str(Path.home()))
            if usage.percent > 90 and not self._has_triggered("disk_high", 86400):
                results.append({
                    "key": "disk_high",
                    "priority": 7,
                    "cooldown": 86400,
                    "text": "Your main disk is above 90 percent usage, sir. Shall I find the largest files?",
                })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _normalise_activity(self, text: str) -> str:
        return " ".join(text.lower().strip().split())

    def _known_domains(self) -> list[str]:
        """Return domains JARVIS knows about from long-term memory."""
        domains = []
        try:
            from memory.memory_manager import load_memory

            mem = load_memory()
            if not isinstance(mem, dict):
                return []

            projects = mem.get("projects", {}) if isinstance(mem.get("projects"), dict) else {}
            for value in projects.values():
                text = str(value)
                if "." in text and " " not in text:
                    domains.append(text.strip().lower())

            wishes = mem.get("wishes", {}) if isinstance(mem.get("wishes"), dict) else {}
            for value in wishes.values():
                text = str(value)
                if "." in text and " " not in text:
                    domains.append(text.strip().lower())

        except Exception:
            pass

        # De-duplicate and filter obvious non-domains
        unique = []
        for d in domains:
            if "." in d and "/" not in d and "@" not in d:
                unique.append(d)
        return unique[:5]

    def _ssl_days_left(self, domain: str) -> int | None:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((domain, 443), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    expires = cert.get("notAfter")
                    if not expires:
                        return None
                    expiry = datetime.strptime(expires, "%b %d %H:%M:%S %Y %Z")
                    days_left = (expiry - datetime.now()).days
                    return max(0, days_left)
        except Exception:
            return None

    def _detect_and_save_pattern(self, text: str):
        """Incrementally save recurring command patterns to disk."""
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            if not PATTERNS_FILE.exists():
                data = {}
            else:
                data = json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))

            key = self._normalise_activity(text)
            if not key:
                return

            if key not in data:
                data[key] = {
                    "count": 0,
                    "last_text": text,
                    "last_seen": time.time(),
                }

            data[key]["count"] += 1
            data[key]["last_text"] = text
            data[key]["last_seen"] = time.time()

            PATTERNS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _has_triggered(self, key: str, cooldown_seconds: int) -> bool:
        last = self._last_triggered.get(key)
        if last and (time.time() - last) < cooldown_seconds:
            return True
        return False

    def _mark(self, key: str):
        self._last_triggered[key] = time.time()

    def _speak(self, text: str):
        if not self.enabled.is_set():
            return

        # Prefer JARVIS voice if available
        if self.speak_callback:
            try:
                self.speak_callback(text)
                return
            except Exception as e:
                print(f"[Proactive] JARVIS speak callback failed: {e}")

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