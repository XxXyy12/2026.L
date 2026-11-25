"""任务状态机与运行时记录：/api/status 的唯一数据来源。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    WAITING_HUMAN = "WAITING_HUMAN"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunType(str, Enum):
    DOWNLOAD = "download"
    PUBLISH = "publish"


class TaskState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    STOPPED = "STOPPED"


@dataclass
class TaskRecord:
    product_id: str
    shop_name: str = ""
    title: str = ""
    state: TaskState = TaskState.PENDING
    step: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "shop_name": self.shop_name,
            "title": self.title,
            "state": self.state.value,
            "step": self.step,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass
class JobRecord:
    run_id: str
    run_type: RunType
    state: JobState = JobState.IDLE
    test_mode: bool = True
    simulated: bool = True
    started_at: str | None = None
    finished_at: str | None = None
    current_step: str = ""
    current_product_id: str = ""
    current_shop: str = ""
    human_reason: str = ""
    human_hint: str = ""
    total: int = 0
    done: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    message: str = ""
    tasks: list[TaskRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_type": self.run_type.value,
            "state": self.state.value,
            "test_mode": self.test_mode,
            "simulated": self.simulated,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "current_step": self.current_step,
            "current_product": self.current_product_id,
            "current_shop": self.current_shop,
            "human_reason": self.human_reason,
            "human_hint": self.human_hint,
            "total": self.total,
            "done": self.done,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "message": self.message,
            "tasks": [t.to_dict() for t in self.tasks],
        }


class JobRegistry:
    """线程安全的任务快照容器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: JobRecord | None = None

    def set(self, job: JobRecord | None) -> None:
        with self._lock:
            self._job = job

    def get(self) -> JobRecord | None:
        with self._lock:
            return self._job

    def snapshot(self) -> dict[str, Any] | None:
        job = self.get()
        return job.to_dict() if job else None
