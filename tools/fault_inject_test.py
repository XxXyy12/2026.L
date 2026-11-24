"""开发期：故障注入测试（断网 / 隐藏元素 / 模拟风控页）。

只验证异常路径是否按预期记录（FAILED 日志 + 错误截图）与转人工，
不访问真实站点、不修改业务代码。

用法：.venv\\Scripts\\python.exe tools\\fault_inject_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.browser import BrowserSession  # noqa: E402
from automation.steps import step_scope  # noqa: E402
from automation.wangshangyuan import WangshangyuanJob  # noqa: E402
from config import get_config  # noqa: E402
from core.control import ControlFlags  # noqa: E402
from core.errors import ErrorCode, NeedHumanError  # noqa: E402
from core.state import JobRecord, JobState, RunType, now_iso  # noqa: E402
from services.log_service import LogService  # noqa: E402


class StepContext:
    """给 step_scope 用的最小上下文。"""

    def __init__(self, cfg, logs, control, session, job) -> None:
        self.cfg = cfg
        self.logs = logs
        self.control = control
        self.session = session
        self.job = job
        self.task = None

    def pause_if_needed(self) -> None:
        self.control.checkpoint()

    def ensure_browser(self) -> None:
        if not self.session.is_alive():
            self.session.restart()

    def step(self, name: str, detail: str = ""):
        return step_scope(self, "fault", name, detail=detail)


def failed_log(logs: LogService, cursor: int, step_name: str) -> tuple[bool, str]:
    records = logs.since(cursor)["logs"]
    for record in records:
        if record["step"] == step_name and record["status"] == "FAILED":
            shot = str(record.get("screenshot_path") or "")
            if shot and Path(shot).exists():
                return True, f"FAILED 日志 + 截图 {Path(shot).name}"
            return False, "FAILED 日志存在但缺少截图"
    return False, "未找到 FAILED 日志"


def main() -> None:
    cfg = get_config()
    logs = LogService(cfg)
    control = ControlFlags()
    job = JobRecord(
        run_id="fault-inject",
        run_type=RunType.DOWNLOAD,
        state=JobState.RUNNING,
        simulated=False,
        started_at=now_iso(),
    )
    session = BrowserSession(cfg, logs)
    ctx = StepContext(cfg, logs, control, session, job)
    results: list[tuple[str, bool, str]] = []

    session.start()
    try:
        # 1) 断网：不可解析域名
        cursor = logs.latest_cursor()
        try:
            with ctx.step("fault_network", "断网注入：打开不可解析域名"):
                session.goto("http://fault-inject.invalid/")
            results.append(("断网", False, "未抛出异常"))
        except Exception as exc:  # noqa: BLE001
            ok, detail = failed_log(logs, cursor, "fault_network")
            results.append(("断网", ok, f"{type(exc).__name__} | {detail}"))

        # 2) 隐藏元素：display:none 等待可见
        session.page.set_content("<div id='hidden' style='display:none'>隐藏元素</div>")
        cursor = logs.latest_cursor()
        try:
            with ctx.step("fault_hidden", "隐藏元素注入：等待不可见元素"):
                session.wait_visible(session.page.locator("#hidden"), timeout_ms=800)
            results.append(("隐藏元素", False, "未抛出异常"))
        except Exception as exc:  # noqa: BLE001
            ok, detail = failed_log(logs, cursor, "fault_hidden")
            results.append(("隐藏元素", ok, f"{type(exc).__name__} | {detail}"))

        # 3) 模拟风控/验证码页：出现“供应商请点此登录”时应转人工并停止
        session.page.set_content("<html><body>供应商请点此登录</body></html>")
        wsy = WangshangyuanJob(cfg, logs, control, session, job, None)
        detected = wsy.is_login_page()
        if not detected:
            results.append(("模拟风控页", False, "未识别为登录/风控页"))
        else:
            try:
                wsy.recover_if_logged_out(lambda: None)
                results.append(("模拟风控页", False, "未转人工"))
            except NeedHumanError as exc:
                ok = exc.code is ErrorCode.E_RISK
                results.append(("模拟风控页", ok, f"{exc.code.value} | {exc.message}"))
    finally:
        session.close(keep_open=False)

    print("\n=== 故障注入结果 ===")
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if any(not ok for _, ok, _ in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
