"""开发期：打开闲鱼密码登录并等待人工完成滑块，成功后导出发布页结构。

用法：
  .venv\\Scripts\\python.exe tools/login_xianyu_once.py
浏览器会保持打开，请在 300 秒内完成滑块并点击登录。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.browser import BrowserSession  # noqa: E402
from config import get_config  # noqa: E402
from services.log_service import LogService  # noqa: E402

WAIT_SECONDS = 1200


def login_frame(page):
    return next((f for f in page.frames if "mini_login" in f.url), None)


def logged_in(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=4000)
    except Exception:  # noqa: BLE001
        return False
    if "先登录，再继续使用" in body:
        return False
    return "发布闲置" in body and "登录" not in body.split("搜索")[0][:20]


def main() -> None:
    cfg = get_config()
    logs = LogService(cfg)
    session = BrowserSession(cfg, logs)
    out = Path(cfg.base_dir) / "logs" / "probe"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    session.start()
    page = session.page
    try:
        session.goto("https://www.goofish.com/")
        page.wait_for_timeout(2500)

        frame = None
        for _ in range(20):
            frame = login_frame(page)
            if frame is not None:
                break
            time.sleep(0.5)

        if frame is None:
            try:
                page.get_by_text("登录", exact=True).first.click()
                page.wait_for_timeout(1500)
            except Exception as exc:  # noqa: BLE001
                print("打开登录弹窗失败:", exc)
            frame = login_frame(page)

        if frame is not None:
            try:
                frame.get_by_text("密码登录", exact=True).first.click()
                page.wait_for_timeout(1000)
                frame.locator('input[type="text"]').first.fill(cfg.xy_username.value)
                frame.locator('input[type="password"]').first.fill(cfg.xy_password.value)
                checkbox = frame.locator('input[type="checkbox"]').first
                if checkbox.count() and not checkbox.is_checked():
                    checkbox.check()
                print("READY: 账号密码已填写", flush=True)
            except Exception as exc:  # noqa: BLE001
                print("填写失败:", type(exc).__name__, exc)

        print("READY: 请在浏览器中点击“登录”并完成滑块（等待 %d 秒）" % WAIT_SECONDS, flush=True)
        print("SHOT:", session.screenshot(out / f"xy_login_ready_{stamp}.png", full_page=False))

        deadline = time.time() + WAIT_SECONDS
        success = False
        while time.time() < deadline:
            if logged_in(page):
                success = True
                break
            time.sleep(2)

        if not success:
            print("LOGIN_TIMEOUT")
            print("SHOT:", session.screenshot(out / f"xy_login_timeout_{stamp}.png", full_page=False))
            return

        print("LOGIN_OK")
        session.save_state()
        print("SHOT:", session.screenshot(out / f"xy_logged_in_{stamp}.png", full_page=False))

        # 打开发布闲置
        try:
            page.get_by_text("发布闲置", exact=True).first.click()
            page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            print("点击发布闲置失败:", exc)
        print("PUBLISH URL:", page.url)
        print("PUBLISH SHOT:", session.screenshot(out / f"xy_publish_{stamp}.png", full_page=False))
        (out / f"xy_publish_{stamp}.html").write_text(page.content(), encoding="utf-8")
        print("FRAMES:", [f.url for f in page.frames])

        info = page.evaluate(
            """
            () => ({
              inputs: Array.from(document.querySelectorAll('input')).slice(0, 30)
                .map(e => ({type: e.type, placeholder: e.placeholder, cls: (e.className || '').toString().slice(0, 60)})),
              textareas: Array.from(document.querySelectorAll('textarea')).slice(0, 10)
                .map(e => ({placeholder: e.placeholder, cls: (e.className || '').toString().slice(0, 60)})),
              editables: Array.from(document.querySelectorAll('[contenteditable="true"]')).slice(0, 10)
                .map(e => ({cls: (e.className || '').toString().slice(0, 60)})),
              buttons: Array.from(document.querySelectorAll('button')).slice(0, 20)
                .map(e => ({text: (e.innerText || '').trim().slice(0, 30), cls: (e.className || '').toString().slice(0, 50)}))
            })
            """
        )
        print("PUBLISH INFO:", json.dumps(info, ensure_ascii=False)[:3000])
    finally:
        session.close(keep_open=False)


if __name__ == "__main__":
    main()
