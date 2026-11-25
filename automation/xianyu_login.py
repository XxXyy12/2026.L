"""闲鱼登录：自动填账号密码 → 人工完成滑块 → 校验并保存登录态。"""

from __future__ import annotations

import time
from typing import Callable

from automation.browser import BrowserSession
from automation.selectors import XIANYU
from config import Config
from core.errors import ErrorCode, NeedHumanError
from services.log_service import LogService


class XianyuLogin:
    def __init__(self, cfg: Config, logs: LogService, session: BrowserSession) -> None:
        self.cfg = cfg
        self.logs = logs
        self.session = session

    # ---------- 检测 ----------
    @property
    def page(self):
        return self.session.page

    def has_login_frame(self) -> bool:
        try:
            return any("mini_login" in f.url for f in self.page.frames)
        except Exception:  # noqa: BLE001
            return False

    def is_logged_in(self) -> bool:
        try:
            body = self.page.locator("body").inner_text(timeout=5000)
        except Exception:  # noqa: BLE001
            return False
        if "先登录，再继续使用" in body:
            return False
        if self.has_login_frame():
            return False
        return ("发闲置" in body) or ("发布闲置" in body)

    # ---------- 登录 ----------
    def ensure_logged_in(self, require_human: Callable[[str, str, str], None]) -> None:
        with self.session_step("open_home", "打开发闲鱼首页"):
            self.session.goto(str(self.cfg.xianyu.home_url))
            self.page.wait_for_timeout(2500)

        if self.is_logged_in():
            self.logs.emit("闲鱼已登录", module="xy.login", step="check", status="OK")
            self.session.save_state()
            return

        self.open_login_modal()
        self.fill_credentials()

        require_human(
            ErrorCode.E_CAPTCHA.value,
            "闲鱼登录需要人工完成滑块验证",
            "请在浏览器中点击“登录”并拖动滑块；登录成功后回到本页面点击“已处理，继续”",
        )

        for attempt in range(2):
            self.page.wait_for_timeout(2500)
            if self.is_logged_in():
                self.logs.emit("闲鱼登录成功，保存登录态", module="xy.login", step="check", status="OK")
                self.session.save_state()
                return
            shot = self.session.screenshot()
            self.logs.emit(
                "仍未检测到闲鱼登录态",
                module="xy.login",
                step="check",
                status="WARNING",
                level="WARNING",
                screenshot_path=shot,
            )
            if attempt == 0:
                require_human(
                    ErrorCode.E_LOGIN.value,
                    "闲鱼登录未完成（滑块可能失败）",
                    "请重试滑块并确认已进入登录后的首页，然后点击“已处理，继续”",
                )
                self.fill_credentials()  # 重试时重新填一次
            else:
                raise NeedHumanError(ErrorCode.E_LOGIN, "闲鱼登录失败，请手动完成后重试任务")

    def open_login_modal(self) -> None:
        for _ in range(12):
            if self.has_login_frame():
                return
            time.sleep(0.5)
        try:
            self.page.get_by_text("登录", exact=True).first.click()
            self.page.wait_for_timeout(1500)
        except Exception as exc:  # noqa: BLE001
            self.logs.emit(
                "点击闲鱼登录入口失败",
                module="xy.login",
                step="open_modal",
                status="WARNING",
                level="WARNING",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _login_frame(self):
        for _ in range(20):
            frame = next((f for f in self.page.frames if "mini_login" in f.url), None)
            if frame is not None:
                return frame
            time.sleep(0.5)
        return None

    def fill_credentials(self) -> None:
        frame = self._login_frame()
        if frame is None:
            self.logs.emit(
                "未找到闲鱼登录 iframe",
                module="xy.login",
                step="fill",
                status="WARNING",
                level="WARNING",
            )
            return
        try:
            # 默认短信登录，必须切到密码登录
            frame.get_by_text(XIANYU["password_tab_text"], exact=True).first.click(timeout=5000)
            self.page.wait_for_timeout(800)
            account = frame.locator('input[type="text"]').first
            account.fill(self.cfg.xy_username.value)
            password = frame.locator('input[type="password"]').first
            password.fill(self.cfg.xy_password.value)
            checkbox = frame.locator('input[type="checkbox"]').first
            if checkbox.count() and not checkbox.is_checked():
                checkbox.check()
            self.logs.emit(
                "已填写闲鱼账号密码，等待人工滑块",
                module="xy.login",
                step="fill",
                status="OK",
                screenshot_path=self.session.screenshot(),
            )
        except Exception as exc:  # noqa: BLE001
            self.logs.emit(
                "填写闲鱼登录表单失败",
                module="xy.login",
                step="fill",
                status="WARNING",
                level="WARNING",
                error=f"{type(exc).__name__}: {exc}",
            )

    # ---------- 简单步骤日志（登录模块独立于 BaseJob） ----------
    def session_step(self, step: str, detail: str):
        from contextlib import contextmanager

        @contextmanager
        def _scope():
            started = time.perf_counter()
            self.logs.emit(detail, module="xy.login", step=step, status="RUNNING")
            try:
                yield
            except Exception as exc:  # noqa: BLE001
                self.logs.emit(
                    f"{detail} 失败",
                    module="xy.login",
                    step=step,
                    status="FAILED",
                    level="ERROR",
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                raise
            else:
                self.logs.emit(
                    f"{detail} 完成",
                    module="xy.login",
                    step=step,
                    status="OK",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )

        return _scope()
