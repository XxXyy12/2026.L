"""运行控制信号：暂停 / 继续 / 停止 / 等待人工接管。

worker 在每个步骤边界调用 checkpoint()，因此暂停与停止都以“步骤”为粒度生效。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class StopRequested(Exception):
    """收到停止指令。"""


class PauseRequested(Exception):
    """收到暂停指令。"""


class ControlFlags:
    def __init__(self) -> None:
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._human_waiting = threading.Event()
        self._human_done = threading.Event()
        self._lock = threading.Lock()
        self.human_reason = ""
        self.human_hint = ""

    # ---------- 指令 ----------
    def request_pause(self) -> None:
        self._pause.set()

    def request_resume(self) -> None:
        self._pause.clear()

    def request_stop(self) -> None:
        self._stop.set()
        self._human_done.set()  # 唤醒正在等待人工的流程

    def mark_human_done(self) -> None:
        with self._lock:
            self._human_done.set()

    def reset(self) -> None:
        with self._lock:
            self._pause.clear()
            self._stop.clear()
            self._human_waiting.clear()
            self._human_done.clear()
            self.human_reason = ""
            self.human_hint = ""

    # ---------- 状态 ----------
    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def human_waiting(self) -> bool:
        return self._human_waiting.is_set()

    def checkpoint(self) -> None:
        if self._stop.is_set():
            raise StopRequested()
        if self._pause.is_set():
            raise PauseRequested()

    # ---------- 人工接管 ----------
    def wait_for_human(
        self,
        reason: str,
        hint: str,
        on_wait: Callable[[str, str], None],
        timeout_sec: int = 0,
    ) -> None:
        """阻塞等待用户点击“已处理，继续”。

        on_wait(reason, hint) 由调用方负责把 Job 状态切到 WAITING_HUMAN。
        timeout_sec=0 表示无限等待；等待期间收到停止指令会抛 StopRequested。
        """
        with self._lock:
            self.human_reason = reason
            self.human_hint = hint
            self._human_done.clear()
            self._human_waiting.set()
        on_wait(reason, hint)

        deadline = None if timeout_sec <= 0 else time.time() + timeout_sec
        try:
            while not self._human_done.wait(0.3):
                if self._stop.is_set():
                    raise StopRequested()
                if deadline is not None and time.time() > deadline:
                    raise TimeoutError("人工处理等待超时")
        finally:
            with self._lock:
                self._human_waiting.clear()
                self._human_done.clear()
                self.human_reason = ""
                self.human_hint = ""
