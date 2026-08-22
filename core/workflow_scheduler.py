"""
core/workflow_scheduler.py — JARVIS Autonomous Workflow Scheduler.

Allows JARVIS to learn and run recurring or one-time workflows.

Workflows are stored in memory/scheduled_workflows.json.

Supported schedule patterns:
    - every N minutes
    - every N hours
    - every N days
    - every day at HH:MM
    - every weekday at HH:MM (weekday = monday/tuesday/etc)
    - once at YYYY-MM-DD HH:MM

No local LLM is required.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
DEFAULT_FILE = MEMORY_DIR / "scheduled_workflows.json"

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def parse_schedule_text(text: str) -> dict:
    """
    Parse a simple natural-language schedule into structured fields.

    Returns:
        {
            "type": "interval" | "daily" | "weekly" | "once",
            "interval_seconds": int | None,
            "time": "HH:MM" | None,
            "weekdays": [0-6] | None,
            "once_at": "YYYY-MM-DD HH:MM" | None,
        }
    """
    text = (text or "").strip().lower()

    # Once at specific date/time
    m = re.search(r"once at\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", text)
    if m:
        return {
            "type": "once",
            "interval_seconds": None,
            "time": None,
            "weekdays": None,
            "once_at": f"{m.group(1)} {m.group(2)}",
        }

    # Every N minutes / hours / days
    m = re.search(r"every\s+(\d+)\s*(minutes|minute|mins|min|hours|hour|days|day)", text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        if unit in ("minutes", "minute", "mins", "min"):
            seconds = amount * 60
        elif unit in ("hours", "hour"):
            seconds = amount * 3600
        else:
            seconds = amount * 86400
        return {
            "type": "interval",
            "interval_seconds": seconds,
            "time": None,
            "weekdays": None,
            "once_at": None,
        }

    # Every day at HH:MM
    m = re.search(r"every day at\s+(\d{1,2}:\d{2})", text)
    if m:
        return {
            "type": "daily",
            "interval_seconds": None,
            "time": m.group(1),
            "weekdays": None,
            "once_at": None,
        }

    # Every weekday at HH:MM (weekday may be plural/comma separated)
    m = re.search(r"every\s+([a-z, ]+?)\s+at\s+(\d{1,2}:\d{2})", text)
    if m:
        day_text = m.group(1).strip()
        weekdays = []
        for name, index in WEEKDAYS.items():
            if name in day_text:
                weekdays.append(index)
        if weekdays:
            return {
                "type": "weekly",
                "interval_seconds": None,
                "time": m.group(2),
                "weekdays": weekdays,
                "once_at": None,
            }

    return {
        "type": "daily",
        "interval_seconds": None,
        "time": "09:00",
        "weekdays": None,
        "once_at": None,
    }


@dataclass
class ScheduledWorkflow:
    id: str
    name: str
    tool_name: str
    params: dict = field(default_factory=dict)
    schedule_type: str = "daily"
    interval_seconds: Optional[int] = None
    time: Optional[str] = None
    weekdays: Optional[list[int]] = None
    once_at: Optional[str] = None
    enabled: bool = True
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tool_name": self.tool_name,
            "params": self.params,
            "schedule_type": self.schedule_type,
            "interval_seconds": self.interval_seconds,
            "time": self.time,
            "weekdays": self.weekdays,
            "once_at": self.once_at,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledWorkflow":
        return cls(**data)


class WorkflowScheduler:
    """Thread-safe persistent workflow scheduler."""

    def __init__(self, path: Path = DEFAULT_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._workflows: dict[str, ScheduledWorkflow] = {}
        self._executor: Optional[Callable[[str, dict], Any]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._load()

    # ------------------------------------------------------------------
    # Executor
    # ------------------------------------------------------------------
    def set_executor(self, executor: Callable[[str, dict], Any]) -> None:
        self._executor = executor

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    wf = ScheduledWorkflow.from_dict(item)
                    self._workflows[wf.id] = wf
        except Exception:
            self._workflows = {}

    def _save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        data = [wf.to_dict() for wf in self._workflows.values()]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def add_workflow(
        self,
        name: str,
        tool_name: str,
        params: Optional[dict] = None,
        schedule: Optional[str] = None,
    ) -> ScheduledWorkflow:
        schedule_info = parse_schedule_text(schedule or "every day at 09:00")

        workflow_id = uuid.uuid4().hex[:8]
        workflow = ScheduledWorkflow(
            id=workflow_id,
            name=name or f"workflow_{workflow_id}",
            tool_name=tool_name or "agent_task",
            params=dict(params or {}),
            schedule_type=schedule_info["type"],
            interval_seconds=schedule_info["interval_seconds"],
            time=schedule_info["time"],
            weekdays=schedule_info["weekdays"],
            once_at=schedule_info["once_at"],
        )
        workflow.next_run = self._compute_next_run(workflow)

        with self._lock:
            self._workflows[workflow.id] = workflow
            self._save()

        return workflow

    def remove_workflow(self, workflow_id: str) -> bool:
        with self._lock:
            if workflow_id in self._workflows:
                del self._workflows[workflow_id]
                self._save()
                return True
        return False

    def list_workflows(self) -> list[dict]:
        with self._lock:
            return [wf.to_dict() for wf in self._workflows.values()]

    def get_workflow(self, workflow_id: str) -> Optional[dict]:
        with self._lock:
            wf = self._workflows.get(workflow_id)
            return wf.to_dict() if wf else None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    def _compute_next_run(self, workflow: ScheduledWorkflow) -> Optional[str]:
        now = datetime.now()

        if workflow.schedule_type == "interval":
            seconds = workflow.interval_seconds or 86400
            return (now + timedelta(seconds=seconds)).isoformat(timespec="seconds")

        if workflow.schedule_type == "daily":
            hour, minute = self._parse_time(workflow.time)
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.isoformat(timespec="seconds")

        if workflow.schedule_type == "weekly":
            hour, minute = self._parse_time(workflow.time)
            weekdays = workflow.weekdays or []
            if not weekdays:
                weekdays = [now.weekday()]

            for offset in range(8):
                candidate_day = now + timedelta(days=offset)
                if candidate_day.weekday() in weekdays:
                    candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if candidate > now:
                        return candidate.isoformat(timespec="seconds")

            # Fallback +7 days first matching
            for offset in range(7, 14):
                candidate_day = now + timedelta(days=offset)
                if candidate_day.weekday() in weekdays:
                    candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    return candidate.isoformat(timespec="seconds")

            return None

        if workflow.schedule_type == "once":
            return workflow.once_at

        return None

    @staticmethod
    def _parse_time(time_str: Optional[str]) -> tuple[int, int]:
        if not time_str:
            return 9, 0
        try:
            hour, minute = time_str.split(":")
            return int(hour), int(minute)
        except Exception:
            return 9, 0

    def get_due_workflows(self) -> list[ScheduledWorkflow]:
        now = datetime.now()
        due = []
        with self._lock:
            for wf in self._workflows.values():
                if not wf.enabled or not wf.next_run:
                    continue
                next_dt = datetime.fromisoformat(wf.next_run)
                if now >= next_dt:
                    due.append(wf)
        return due

    def advance_workflow(self, workflow_id: str) -> None:
        with self._lock:
            wf = self._workflows.get(workflow_id)
            if not wf:
                return

            wf.last_run = datetime.now().isoformat(timespec="seconds")

            if wf.schedule_type == "once":
                wf.enabled = False
                wf.next_run = None
            else:
                wf.next_run = self._compute_next_run(wf)

            self._save()

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WorkflowScheduler")
        self._thread.start()
        print("[Scheduler] ✅ Started")

    def stop(self) -> None:
        self._running = False
        print("[Scheduler] 🛑 Stopped")

    def _loop(self) -> None:
        while self._running:
            try:
                due = self.get_due_workflows()
                for wf in due:
                    if not wf.enabled:
                        continue

                    print(f"[Scheduler] ▶ Running workflow: {wf.name} [{wf.tool_name}]")
                    if self._executor:
                        try:
                            self._executor(wf.tool_name, wf.params)
                        except Exception as e:
                            print(f"[Scheduler] ❌ Workflow '{wf.name}' failed: {e}")

                    self.advance_workflow(wf.id)

            except Exception as e:
                print(f"[Scheduler] ⚠️ Loop error: {e}")

            time.sleep(30)