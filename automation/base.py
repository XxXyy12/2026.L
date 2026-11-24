"""业务 Job 基类：暂停等待、步骤日志、人工接管。

Job 在 AutomationWorker 线程内运行，直接更新 JobRecord（UI 只读快照）。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from config import Config
from core.control import PauseRequested, StopRequested
from core.errors import ErrorCode, NeedHumanError
from core.state import JobRecord, JobState, TaskRecord
from services.log_service import LogService
from automation.browser import BrowserSession
from automation.steps import step_scope


class BaseJob:
    module = "job"

    def __init__(self, cfg: Config, logs: LogService, control, session: BrowserSession,
                 job: JobRecord, manager) -> None:
        self.cfg = cfg
        self.logs = logs
        self.control = control
        self.session = session
        self.job = job
        self.manager = manager
        self.task: TaskRecord | None = None
        self.browser_restarts = 0

    # ---------- 暂停 ----------
    def pause_if_needed(self) -> None:
        while True:
            try:
                self.control.checkpoint()
                return
            except PauseRequested:
                if self.job.state != JobState.PAUSED:
                    self.job.state = JobState.PAUSED
                    self.logs.emit("任务已暂停", module=self.module, step="pause", status="PAUSED")
                while self.control.paused:
                    if self.control.stop_requested:
                        raise StopRequested()
                    time.sleep(0.2)
                self.job.state = JobState.RUNNING
                self.logs.emit("任务继续运行", module=self.module, step="resume", status="RUNNING")

    def step(self, name: str, detail: str = "", product_id: str = "", module: str | None = None):
        return step_scope(
            self,
            module or self.module,
            name,
            product_id=product_id or (self.task.product_id if self.task else ""),
            detail=detail or name,
        )

    def idle_wait(self, seconds: float) -> None:
        """可中断的间隔等待：暂停时挂起，停止时立即抛出，避免长等待期间无法响应。"""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            self.pause_if_needed()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.5, remaining))

    # ---------- 浏览器崩溃恢复 ----------
    def ensure_browser(self) -> None:
        """步骤边界检查浏览器；已关闭则自动重启一次，仍不可用则转人工。"""
        if self.session is None or self.session.is_alive():
            return
        if self.browser_restarts < 1:
            self.browser_restarts += 1
            self.logs.emit(
                "检测到浏览器已关闭，尝试自动重启（本次任务仅一次）",
                module=self.module,
                step="browser_restart",
                status="WARNING",
                level="WARNING",
            )
            try:
                self.session.restart()
            except Exception as exc:  # noqa: BLE001 - 重启失败走人工
                self.logs.emit(
                    "浏览器自动重启失败",
                    module=self.module,
                    step="browser_restart",
                    status="FAILED",
                    level="ERROR",
                    error=f"{type(exc).__name__}: {exc}",
                )
            if self.session.is_alive():
                self.logs.emit(
                    "浏览器已自动重启，继续执行（登录态依赖持久化配置目录）",
                    module=self.module,
                    step="browser_restart",
                    status="OK",
                )
                return
        self.require_human(
            ErrorCode.E_BROWSER.value,
            "浏览器已关闭且自动恢复失败",
            "请手动恢复/打开浏览器窗口，确认页面可用后回到控制台点击“已处理，继续”",
        )
        if not self.session.is_alive():
            raise NeedHumanError(ErrorCode.E_BROWSER, "浏览器仍不可用，已停止任务")

    # ---------- 人工接管 ----------
    def require_human(self, code: str, message: str, hint: str = "") -> None:
        shot = ""
        try:
            self.session.screenshot()
        except Exception:  # noqa: BLE001
            shot = ""
        self.logs.emit(
            message,
            module=self.module,
            step="need_human",
            status="WAITING_HUMAN",
            level="WARNING",
            error=code,
            screenshot_path=shot,
        )
        self.job.state = JobState.WAITING_HUMAN
        self.job.human_reason = message
        self.job.human_hint = hint
        self.control.wait_for_human(message, hint, on_wait=lambda reason, tip: None)
        self.job.state = JobState.RUNNING
        self.job.human_reason = ""
        self.job.human_hint = ""
        self.logs.emit("人工处理完成，继续执行", module=self.module, step="resume_after_human", status="RUNNING")

    # ---------- 任务记录 ----------
    @contextmanager
    def task_scope(self, task: TaskRecord) -> Iterator[TaskRecord]:
        from core.state import TaskState, now_iso

        self.task = task
        task.state = TaskState.RUNNING
        task.started_at = now_iso()
        self.job.current_product_id = task.product_id
        self.job.current_shop = task.shop_name
        self.logs.emit(
            "开始处理商品",
            module=self.module,
            step="product_start",
            status="RUNNING",
            product_id=task.product_id,
        )
        try:
            yield task
        except (StopRequested, PauseRequested):
            task.state = TaskState.STOPPED
            task.finished_at = now_iso()
            raise
        except Exception as exc:  # noqa: BLE001
            task.state = TaskState.FAILED
            task.finished_at = now_iso()
            task.error = f"{type(exc).__name__}: {exc}"
            self.logs.emit(
                "商品处理失败",
                module=self.module,
                step="product_failed",
                status="FAILED",
                level="ERROR",
                product_id=task.product_id,
                error=task.error,
            )
            if isinstance(exc, NeedHumanError):
                raise
        else:
            if task.state is TaskState.RUNNING:
                task.state = TaskState.SUCCESS
            task.finished_at = now_iso()
        finally:
            self.task = None
            self.job.done = self.job.success + self.job.failed + self.job.skipped
            self.job.current_product_id = ""
