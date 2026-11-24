"""Playwright 浏览器封装：可见浏览器、显式等待、随机间隔、截图、登录态复用。

约束：
- 只在 AutomationWorker 线程内创建与使用，不得跨线程共享；
- 默认 headless=False，用户可实时看到每一步操作。
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Config
from services.log_service import LogService


class BrowserSession:
    def __init__(self, cfg: Config, logs: LogService) -> None:
        self.cfg = cfg
        self.logs = logs
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._state_path = cfg.path_of(str(cfg.browser.storage_state_path))

    # ---------- 生命周期 ----------
    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        if self._browser is not None:
            return
        channel = str(self.cfg.browser.get("channel", "") or "")
        user_data_dir = str(self.cfg.browser.get("user_data_dir", "") or "")
        engine_label = channel or str(self.cfg.browser.engine)
        self.logs.emit(
            f"启动浏览器：{engine_label}，headless={bool(self.cfg.mode.headless)}",
            module="browser",
            step="start",
            status="INFO",
        )
        self._pw = sync_playwright().start()
        launch_args = ["--disable-gpu", "--disable-gpu-shader-disk-cache"]

        if channel or user_data_dir:
            # 使用本机 Edge + 持久化用户目录：保留真实浏览器指纹与登录态
            profile_dir = self._profile_path(user_data_dir)
            profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel or None,
                headless=bool(self.cfg.mode.headless),
                slow_mo=int(self.cfg.browser.slow_mo_ms),
                args=launch_args,
                viewport={
                    "width": int(self.cfg.browser.viewport.width),
                    "height": int(self.cfg.browser.viewport.height),
                },
                locale=str(self.cfg.browser.locale),
                timezone_id=str(self.cfg.browser.timezone),
                accept_downloads=bool(self.cfg.browser.accept_downloads),
            )
            self._browser = self._context.browser
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._import_cookies()
            self.logs.emit(
                f"使用持久化配置目录：{profile_dir}",
                module="browser",
                step="start",
                status="INFO",
                file_path=str(profile_dir),
            )
        else:
            self._browser = self._pw.chromium.launch(
                headless=bool(self.cfg.mode.headless),
                slow_mo=int(self.cfg.browser.slow_mo_ms),
                args=launch_args,
            )
            context_args: dict[str, Any] = {
                "viewport": {
                    "width": int(self.cfg.browser.viewport.width),
                    "height": int(self.cfg.browser.viewport.height),
                },
                "locale": str(self.cfg.browser.locale),
                "timezone_id": str(self.cfg.browser.timezone),
                "accept_downloads": bool(self.cfg.browser.accept_downloads),
            }
            if self._state_path.exists():
                context_args["storage_state"] = str(self._state_path)
                self.logs.emit(
                    f"复用登录态：{self._state_path}",
                    module="browser",
                    step="start",
                    status="INFO",
                    file_path=str(self._state_path),
                )
            self._context = self._browser.new_context(**context_args)
            self._page = self._context.new_page()

        self._context.set_default_timeout(int(self.cfg.timing.element_timeout) * 1000)
        self._context.set_default_navigation_timeout(int(self.cfg.timing.page_load_timeout) * 1000)

    def _profile_path(self, value: str) -> Path:
        path = Path(value or "data/edge-profile")
        return path if path.is_absolute() else (Path(self.cfg.base_dir) / path)

    def _import_cookies(self) -> None:
        """把 storage_state 中的 Cookie 导入持久化配置，兼容旧登录态。"""
        if not self._state_path.exists() or self._context is None:
            return
        try:
            import json

            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            cookies = data.get("cookies") or []
            if cookies:
                self._context.add_cookies(cookies)
                self.logs.emit(
                    f"已导入 {len(cookies)} 条历史 Cookie",
                    module="browser",
                    step="start",
                    status="INFO",
                )
        except Exception as exc:  # noqa: BLE001 - 旧状态不兼容时忽略
            self.logs.emit(
                "导入历史 Cookie 失败（已忽略）",
                module="browser",
                step="start",
                status="WARNING",
                level="WARNING",
                error=f"{type(exc).__name__}: {exc}",
            )

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._page

    @property
    def context(self):
        if self._context is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._context

    def close(self, keep_open: bool = False) -> None:
        if keep_open:
            self.logs.emit("任务结束，浏览器保持打开供人工检查", module="browser", step="close", status="INFO")
            return
        for action in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            try:
                if action is not None:
                    action()
            except Exception as exc:  # noqa: BLE001 - 关闭阶段异常不应影响任务收尾
                self.logs.emit(
                    "关闭浏览器资源时出现异常（已忽略）",
                    module="browser",
                    step="close",
                    status="WARNING",
                    level="WARNING",
                    error=f"{type(exc).__name__}: {exc}",
                )
        self._context = None
        self._browser = None
        self._pw = None
        self._page = None

    # ---------- 崩溃检测与重启 ----------
    def is_alive(self) -> bool:
        """浏览器与当前页面是否仍可用（窗口被关闭或进程崩溃时返回 False）。"""
        if self._context is None or self._page is None:
            return False
        try:
            if self._page.is_closed():
                return False
            _ = self._context.pages  # 访问即校验连接是否仍然有效
            return True
        except Exception:  # noqa: BLE001 - 任何底层异常都视为不可用
            return False

    def restart(self) -> None:
        """释放残留资源后重新启动浏览器（用于崩溃后的自动恢复）。"""
        self.close(keep_open=False)
        self.start()

    # ---------- 通用动作 ----------
    def delay(self, factor: float = 1.0) -> None:
        low = float(self.cfg.timing.delay_min)
        high = float(self.cfg.timing.delay_max)
        time.sleep(random.uniform(low, high) * factor)

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.page.goto(url, wait_until=wait_until)
        self.delay()
        self.logs.emit(f"打开页面：{url}", module="browser", step="goto", status="OK", file_path=url)

    def wait_visible(self, locator, timeout_ms: int | None = None):
        timeout = timeout_ms or int(self.cfg.timing.element_timeout) * 1000
        target = locator.first
        target.wait_for(state="visible", timeout=timeout)
        return target

    def wait_clickable(self, locator, timeout_ms: int | None = None):
        timeout = timeout_ms or int(self.cfg.timing.element_timeout) * 1000
        target = locator.first
        target.wait_for(state="visible", timeout=timeout)
        target.wait_for(state="attached", timeout=timeout)
        return target

    def click_text(self, texts: list[str], exact: bool = False, timeout_ms: int | None = None):
        """按可见文案点击第一个命中的元素（用于文案稳定的入口）。"""
        timeout = timeout_ms or int(self.cfg.timing.element_timeout) * 1000
        deadline = time.time() + timeout / 1000
        last_error: Exception | None = None
        while time.time() < deadline:
            for text in texts:
                try:
                    locator = self.page.get_by_text(text, exact=exact)
                    if locator.count() > 0:
                        target = locator.first
                        if target.is_visible():
                            target.click()
                            self.delay()
                            return target
                except Exception as exc:  # noqa: BLE001 - 页面元素随时变化
                    last_error = exc
            time.sleep(0.25)
        raise TimeoutError(f"未找到可点击文案：{texts}（{last_error}）")

    def screenshot(self, path: str | Path | None = None, full_page: bool = True) -> str:
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = Path(self.logs.screenshot_dir) / f"browser_{stamp}.png"
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(out), full_page=full_page)
        return str(out)

    def save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self.context.storage_state(path=str(self._state_path))
        self.logs.emit(
            "登录态已保存",
            module="browser",
            step="save_state",
            status="OK",
            file_path=str(self._state_path),
        )
