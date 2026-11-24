"""开发期：闲鱼页面探测（登录 / 发布闲置 / 表单字段）。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.browser import BrowserSession  # noqa: E402
from automation.selectors import XIANYU  # noqa: E402
from config import get_config  # noqa: E402
from services.log_service import LogService  # noqa: E402

KEYWORDS = ["登录", "发布闲置", "发布", "出售", "标题", "描述", "价格", "所在地"]


def dump(page, keyword: str, limit: int = 10) -> None:
    script = """
    (keyword) => {
      const out = [];
      for (const el of document.querySelectorAll('a,button,span,div,li,input,textarea')) {
        const text = ((el.innerText || el.value || el.placeholder || '') + '').trim();
        if (!text || text.length > 60) continue;
        if (!text.includes(keyword)) continue;
        if (out.length >= 30) break;
        out.push({tag: el.tagName, type: el.getAttribute('type'), placeholder: el.getAttribute('placeholder'),
                  text, cls: (el.className || '').toString().slice(0, 70),
                  href: el.getAttribute('href') || ''});
      }
      return out;
    }
    """
    try:
        items = page.evaluate(script, keyword)[:limit]
    except Exception:
        return
    if items:
        print(f"\n== {keyword} ==")
        for item in items:
            print(json.dumps(item, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=XIANYU["home_url"])
    parser.add_argument("--click", default="")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--try-login", action="store_true", help="用 .env 账号密码尝试登录闲鱼")
    args = parser.parse_args()

    cfg = get_config()
    logs = LogService(cfg)
    session = BrowserSession(cfg, logs)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(cfg.base_dir) / "logs" / "probe"
    out.mkdir(parents=True, exist_ok=True)

    session.start()
    page = session.page
    try:
        session.goto(args.url)
        page.wait_for_timeout(3000)
        print("TITLE:", page.title())
        print("URL:", page.url)
        print("FRAMES:", [f.url for f in page.frames])
        print("SHOT:", session.screenshot(out / f"xy_home_{stamp}.png", full_page=False))
        (out / f"xy_home_{stamp}.html").write_text(page.content(), encoding="utf-8")

        print("BODY:", page.locator("body").inner_text()[:900].replace("\n", " | "))

        if args.login:
            print("\n>>> 点击登录")
            try:
                session.click_text(["登录", "立即登录"])
            except Exception as exc:  # noqa: BLE001
                print("点击登录失败:", exc)
            page.wait_for_timeout(3000)
            print("LOGIN SHOT:", session.screenshot(out / f"xy_login_{stamp}.png", full_page=False))
            print("LOGIN URL:", page.url)
            print("FRAMES:", [f.url for f in page.frames])
            for frame in page.frames:
                if "login" not in frame.url:
                    continue
                try:
                    print(f"\n--- FRAME {frame.url[:90]} ---")
                    print("TEXT:", frame.locator("body").inner_text()[:600].replace("\n", " | "))
                    inputs = frame.locator("input")
                    print("INPUT count:", inputs.count())
                    for i in range(min(inputs.count(), 12)):
                        el = inputs.nth(i)
                        print(i, el.get_attribute("type"), el.get_attribute("placeholder"), el.is_visible())
                    for item in (frame.locator("a,div,span,button").all()[:0]):
                        pass
                except Exception as exc:  # noqa: BLE001
                    print("frame 解析失败:", exc)

        if args.try_login:
            print("\n>>> 尝试账号密码登录闲鱼")
            frame = next((f for f in page.frames if "mini_login" in f.url), None)
            if frame is None:
                print("未找到登录 iframe")
            else:
                try:
                    # 默认是短信登录，必须先切换到“密码登录”
                    try:
                        frame.get_by_text("密码登录", exact=True).first.click()
                        print("已切换密码登录")
                        page.wait_for_timeout(1200)
                    except Exception as exc:  # noqa: BLE001
                        print("切换密码登录失败:", exc)
                    account = frame.locator('input[type="text"]').first
                    account.fill(cfg.xy_username.value)
                    password = frame.locator('input[type="password"]').first
                    password.fill(cfg.xy_password.value)
                    checkbox = frame.locator('input[type="checkbox"]').first
                    if checkbox.count() and not checkbox.is_checked():
                        checkbox.check()
                    print("FRAME BUTTONS:")
                    buttons = frame.locator("button")
                    for i in range(min(buttons.count(), 8)):
                        print(" BTN", i, buttons.nth(i).inner_text()[:30].replace("\n", " "),
                              buttons.nth(i).is_visible())
                    clicked = False
                    for i in range(buttons.count()):
                        btn = buttons.nth(i)
                        try:
                            if btn.is_visible() and "登录" in btn.inner_text():
                                btn.click()
                                clicked = True
                                print("已点击登录按钮", i)
                                break
                        except Exception:
                            continue
                    if not clicked:
                        frame.get_by_text("登录", exact=True).last.click()
                        print("已按文本点击登录")
                except Exception as exc:  # noqa: BLE001
                    print("登录填写失败:", type(exc).__name__, exc)
                page.wait_for_timeout(6000)
                print("AFTER TRY URL:", page.url)
                print("AFTER TRY SHOT:", session.screenshot(out / f"xy_after_login_{stamp}.png", full_page=False))
                body = page.locator("body").inner_text()[:400]
                print("AFTER TRY BODY:", body.replace("\n", " | ")[:400])
                print("FRAMES:", [f.url for f in page.frames])
                if any(k in body for k in ["发布闲置", "我的闲鱼", "消息"]):
                    session.save_state()
                    print("闲鱼登录成功，登录态已保存")

        if args.click:
            print("\n>>> 点击:", args.click)
            try:
                session.click_text([args.click])
            except Exception as exc:  # noqa: BLE001
                print("点击失败:", exc)
            page.wait_for_timeout(3000)
            print("CLICK URL:", page.url)
            print("CLICK SHOT:", session.screenshot(out / f"xy_click_{stamp}.png", full_page=False))
            (out / f"xy_click_{stamp}.html").write_text(page.content(), encoding="utf-8")
            print("CLICK BODY:", page.locator("body").inner_text()[:1200].replace("\n", " | "))
            print("FRAMES:", [f.url for f in page.frames])

        for keyword in KEYWORDS:
            dump(page, keyword)
    finally:
        session.close(keep_open=False)


if __name__ == "__main__":
    main()
