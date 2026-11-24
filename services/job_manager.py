"""任务管理：HTTP 线程与唯一后台 worker 线程之间的桥梁。

当前阶段 worker 为 **模拟执行**（不启动浏览器），用于联调界面、状态机、
暂停/继续/停止与日志；阶段 3 起将替换为真实 Playwright 流程，接口保持不变。
"""

from __future__ import annotations

import queue
import random
import threading
import time
import uuid
from datetime import datetime

from config import Config
from core.control import ControlFlags, PauseRequested, StopRequested
from core.state import JobRecord, JobRegistry, JobState, RunType, TaskRecord, TaskState, now_iso
from services.log_service import LogService
from services.product_store import ProductStore
from services.report_service import write_run_report

ACTIVE_STATES = {
    JobState.RUNNING,
    JobState.PAUSING,
    JobState.PAUSED,
    JobState.WAITING_HUMAN,
    JobState.STOPPING,
}


class JobRejected(Exception):
    """当前状态不允许该操作。"""


class JobManager:
    def __init__(self, cfg: Config, logs: LogService, registry: JobRegistry) -> None:
        self.cfg = cfg
        self.logs = logs
        self.registry = registry
        self.control = ControlFlags()
        self.product_store = ProductStore(cfg)
        self._queue: queue.Queue[tuple[str, RunType | None]] = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._loop, name="automation-worker", daemon=True)
        self._worker.start()

    # ---------- 对外指令 ----------
    def start_job(self, run_type: RunType) -> JobRecord:
        with self._lock:
            current = self.registry.get()
            if current and current.state in ACTIVE_STATES:
                raise JobRejected(f"任务正在运行（{current.state.value}），请先等待或停止")
            self.control.reset()
            job = JobRecord(
                run_id=datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
                run_type=run_type,
                state=JobState.RUNNING,
                test_mode=self.cfg.test_mode,
                simulated=bool(self.cfg.mode.simulated),
                started_at=now_iso(),
                total=int(self.cfg.job.max_product_total),
                message="任务已提交",
            )
            self.registry.set(job)

        self.logs.emit(
            f"收到任务：{'开始下载图片' if run_type is RunType.DOWNLOAD else '开始发布服装'}"
            f"（{'测试模式' if self.cfg.test_mode else '真实模式'}，"
            f"{'模拟执行' if job.simulated else '真实浏览器'}）",
            module="job",
            step="start",
            status="STARTED",
        )
        self._queue.put(("run", run_type))
        return job

    def pause(self) -> JobState:
        job = self._require_active()
        self.control.request_pause()
        if job.state == JobState.RUNNING:
            job.state = JobState.PAUSING
        self.logs.emit("收到暂停指令，将在当前步骤结束后暂停", module="job", step="pause", status="PAUSING")
        return job.state

    def resume(self) -> JobState:
        job = self._require_active()
        self.control.request_resume()
        if job.state == JobState.PAUSING:
            job.state = JobState.RUNNING
        self.logs.emit("收到继续指令", module="job", step="resume", status="RUNNING")
        return job.state

    def stop(self) -> JobState:
        job = self.registry.get()
        if not job or job.state not in ACTIVE_STATES:
            raise JobRejected("当前没有可停止的任务")
        self.control.request_stop()
        job.state = JobState.STOPPING
        self.logs.emit("收到停止指令，不再开始新商品", module="job", step="stop", status="STOPPING")
        return job.state

    def human_done(self) -> None:
        self.control.mark_human_done()
        self.logs.emit("用户确认人工处理完成，等待流程继续", module="job", step="human_done", status="RUNNING")

    def shutdown(self) -> None:
        if self.registry.get() and self.registry.get().state in ACTIVE_STATES:
            try:
                self.stop()
            except JobRejected:
                pass

    # ---------- 内部工具 ----------
    def _require_active(self) -> JobRecord:
        job = self.registry.get()
        if not job or job.state not in ACTIVE_STATES:
            raise JobRejected("当前没有运行中的任务")
        return job

    def _loop(self) -> None:
        while True:
            command, payload = self._queue.get()
            if command == "run" and payload is not None:
                self._execute(payload)

    def _execute(self, run_type: RunType) -> None:
        job = self.registry.get()
        if job is None:
            return
        if job.simulated:
            self._execute_simulated(run_type)
        else:
            self._execute_real(run_type)

    def _execute_real(self, run_type: RunType) -> None:
        job = self.registry.get()
        if job is None:
            return
        session = None
        try:
            job.state = JobState.RUNNING
            job.total = int(self.cfg.job.max_product_total)
            self.logs.emit("启动真实浏览器自动化", module="job", step="run", status="RUNNING")

            from automation.browser import BrowserSession

            session = BrowserSession(self.cfg, self.logs)
            session.start()
            if run_type is RunType.DOWNLOAD:
                from automation.wangshangyuan import WangshangyuanJob

                business = WangshangyuanJob(self.cfg, self.logs, self.control, session, job, self)
            else:
                from automation.xianyu_publish import XianyuPublishJob

                business = XianyuPublishJob(self.cfg, self.logs, self.control, session, job, self)
            business.run()

            job.state = JobState.COMPLETED
            job.message = "任务完成"
            self.logs.emit("任务完成", module="job", step="run", status="COMPLETED")
        except StopRequested:
            job.state = JobState.STOPPED
            job.message = f"已停止：不再开始新商品（已完成 {job.done}/{job.total}）"
            self.logs.emit(job.message, module="job", step="stop", status="STOPPED", level="WARNING")
        except PauseRequested:
            job.state = JobState.PAUSED
        except Exception as exc:  # noqa: BLE001 - 顶层兜底，避免 worker 线程退出
            job.state = JobState.FAILED
            job.message = f"任务异常终止：{type(exc).__name__}"
            self.logs.emit(
                "任务异常终止",
                module="job",
                step="run",
                status="FAILED",
                level="ERROR",
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            job.finished_at = now_iso()
            job.current_step = ""
            job.current_shop = ""
            job.current_product_id = ""
            if session is not None:
                session.close(keep_open=bool(self.cfg.mode.keep_browser_open))
            self._write_report(job)

    def _write_report(self, job: JobRecord) -> None:
        """任务结束后落盘运行报告；写失败只告警，不影响任务状态。"""
        try:
            path = write_run_report(self.cfg, job)
            self.logs.emit(
                "运行报告已写入",
                module="job",
                step="report",
                status="OK",
                file_path=str(path),
            )
        except Exception as exc:  # noqa: BLE001 - 报告失败不能覆盖任务结果
            self.logs.emit(
                "运行报告写入失败",
                module="job",
                step="report",
                status="FAILED",
                level="WARNING",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _execute_simulated(self, run_type: RunType) -> None:
        job = self.registry.get()
        if job is None:
            return
        try:
            job.state = JobState.RUNNING
            items = self._download_items() if run_type is RunType.DOWNLOAD else self._publish_items()
            job.total = len(items)
            if job.simulated:
                job.message = "模拟运行：未操作浏览器，所有成功状态仅用于界面联调"
            self.logs.emit(
                f"任务开始，共 {job.total} 个商品",
                module="job",
                step="run",
                status="RUNNING",
            )

            for index, item in enumerate(items, start=1):
                self._checkpoint(job)
                task = TaskRecord(
                    product_id=item["product_id"],
                    shop_name=item.get("shop_name", ""),
                    title=item.get("title", ""),
                    state=TaskState.RUNNING,
                    started_at=now_iso(),
                )
                job.tasks.append(task)
                job.current_product_id = task.product_id
                job.current_shop = task.shop_name
                self.logs.emit(
                    f"开始处理第 {index}/{job.total} 个商品",
                    module="job",
                    step="product_start",
                    status="RUNNING",
                    product_id=task.product_id,
                )
                try:
                    steps = self._download_steps() if run_type is RunType.DOWNLOAD else self._publish_steps()
                    for step_name, message in steps:
                        self._simulated_step(job, task, run_type, step_name, message)
                    task.state = TaskState.SUCCESS
                    task.finished_at = now_iso()
                    job.success += 1
                except StopRequested:
                    task.state = TaskState.STOPPED
                    task.finished_at = now_iso()
                    raise
                finally:
                    job.done = job.success + job.failed + job.skipped
                    job.current_product_id = ""

        except StopRequested:
            job.state = JobState.STOPPED
            job.message = f"已停止：不再开始新商品（已完成 {job.done}/{job.total}）"
            self.logs.emit(job.message, module="job", step="stop", status="STOPPED", level="WARNING")
        except PauseRequested:
            # 暂停在 _checkpoint 内部消化，理论上不会到这里
            job.state = JobState.PAUSED
        except Exception as exc:  # 兜底：不让 worker 线程退出
            job.state = JobState.FAILED
            job.message = f"任务异常终止：{type(exc).__name__}"
            self.logs.emit(
                "任务异常终止",
                module="job",
                step="run",
                status="FAILED",
                level="ERROR",
                error=f"{type(exc).__name__}: {exc}",
            )
        else:
            job.state = JobState.COMPLETED
            job.message = "任务完成"
            self.logs.emit("任务完成", module="job", step="run", status="COMPLETED")
        finally:
            job.finished_at = now_iso()
            job.current_step = ""
            job.current_shop = ""
            self._write_report(job)

    def _checkpoint(self, job: JobRecord) -> None:
        while True:
            try:
                self.control.checkpoint()
                return
            except PauseRequested:
                if job.state != JobState.PAUSED:
                    job.state = JobState.PAUSED
                    self.logs.emit("任务已暂停", module="job", step="pause", status="PAUSED")
                while self.control.paused:
                    if self.control.stop_requested:
                        raise StopRequested()
                    time.sleep(0.2)
                job.state = JobState.RUNNING
                self.logs.emit("任务继续运行", module="job", step="resume", status="RUNNING")

    def _simulated_step(
        self,
        job: JobRecord,
        task: TaskRecord,
        run_type: RunType,
        step_name: str,
        message: str,
    ) -> None:
        self._checkpoint(job)
        job.current_step = step_name
        task.step = step_name
        started = time.perf_counter()
        time.sleep(random.uniform(0.10, 0.28))
        duration = int((time.perf_counter() - started) * 1000)
        self.logs.emit(
            message,
            module="sim.download" if run_type is RunType.DOWNLOAD else "sim.publish",
            step=step_name,
            status="SIMULATED",
            product_id=task.product_id,
            duration_ms=duration,
        )

    # ---------- 模拟数据 ----------
    def _download_items(self) -> list[dict[str, str]]:
        shops = int(self.cfg.wangshangyuan.shop_count)
        products = int(self.cfg.wangshangyuan.product_count)
        items: list[dict[str, str]] = []
        for s in range(1, shops + 1):
            for p in range(1, products + 1):
                items.append(
                    {
                        "product_id": f"S{s:02d}-P{p:02d}",
                        "shop_name": f"金牌档口{s:02d}（模拟）",
                        "title": f"30日新品男装 商品{p:02d}（模拟）",
                    }
                )
        return items[: int(self.cfg.job.max_product_total)]

    def _publish_items(self) -> list[dict[str, str]]:
        pending = self.product_store.pending()
        if pending:
            return [
                {
                    "product_id": str(p.get("product_id", "")),
                    "shop_name": str(p.get("shop_name", "")),
                    "title": str(p.get("title", "")),
                }
                for p in pending
            ][: int(self.cfg.job.max_product_total)]
        self.logs.emit(
            "未找到已下载商品清单 data/products.json，本次使用占位商品仅用于界面联调",
            module="job",
            step="publish_precheck",
            status="WARNING",
            level="WARNING",
        )
        return self._download_items()

    def _download_steps(self) -> list[tuple[str, str]]:
        return [
            ("open_shop", "打开店铺（模拟）"),
            ("filter_new_30d", "筛选 30 日新品（模拟）"),
            ("filter_menswear", "筛选男装（模拟）"),
            ("open_product", "进入商品详情（模拟）"),
            ("click_one_click_download", "点击一键下载（模拟）"),
            ("download_archive", "保存压缩包到 raw/（模拟）"),
            ("extract_images", "解压图片到 images/（模拟）"),
        ]

    def _publish_steps(self) -> list[tuple[str, str]]:
        return [
            ("login_check", "检测闲鱼登录态（模拟）"),
            ("open_publish_page", "打开发布闲置页（模拟）"),
            ("upload_images", "上传 4 张图片（模拟）"),
            ("fill_title", "填写标题（原标题）（模拟）"),
            ("fill_description", "生成并填写描述（模拟）"),
            ("fill_price", "按加价规则填写价格（模拟）"),
            ("select_location", "选择第一个附近地址（模拟）"),
            ("pre_submit_screenshot", "测试模式：保存发布前截图（模拟）"),
            ("back_home", "返回首页继续下一件（模拟）"),
        ]
