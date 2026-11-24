"""开发期页面探测工具（不参与业务运行）。

用途：打开网商园真实页面，截图并导出关键 DOM 信息，用于校准 selectors.py。
用法：
  .venv\\Scripts\\python.exe tools\\probe_wsy.py
  .venv\\Scripts\\python.exe tools\\probe_wsy.py --login
  .venv\\Scripts\\python.exe tools\\probe_wsy.py --after "https://www.wsy.com/ranking.htm"
  .venv\\Scripts\\python.exe tools\\probe_wsy.py --after "https://www.wsy.com/item.htm?id=xxx" --click "一键下载"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.browser import BrowserSession  # noqa: E402
from automation.selectors import WSY  # noqa: E402
from config import get_config  # noqa: E402
from services.log_service import LogService  # noqa: E402

KEYWORDS = ["登录", "金牌", "档口", "30日", "男装", "一键下载", "下载", "当前的详情图", "发布闲置"]


def dump_products(page, limit: int = 8) -> None:
    script = """
    (limit) => Array.from(document.querySelectorAll('a[href*="/item.htm?id="]'))
      .slice(0, limit)
      .map(a => ({text: (a.innerText || '').trim().slice(0, 60),
                  href: a.getAttribute('href') || '',
                  cls: (a.className || '').toString().slice(0, 60),
                  parent: (a.parentElement && a.parentElement.className || '').toString().slice(0, 60)}))
    """
    try:
        items = page.evaluate(script, limit)
    except Exception as exc:  # noqa: BLE001
        print("商品链接提取失败:", exc)
        return
    print("PRODUCT LINKS:", len(items))
    for item in items:
        print(json.dumps(item, ensure_ascii=False))


def dump_categories(page) -> None:
    try:
        sections = page.locator(".type_section")
        print("CATEGORY type_section count:", sections.count())
        for i in range(min(sections.count(), 6)):
            el = sections.nth(i)
            text = el.inner_text()[:60].replace("\n", " ")
            print("SECTION", i, text)
            if "男装" in text:
                print("  HTML:", el.evaluate("el => el.outerHTML")[:1800])
    except Exception as exc:  # noqa: BLE001
        print("分类提取失败:", exc)


def dump_deal(page) -> None:
    script = """
    () => ({
      h: Array.from(document.querySelectorAll('h1,h2')).slice(0, 3)
           .map(e => (e.innerText || '').trim().slice(0, 80)),
      price: Array.from(document.querySelectorAll('[class*="price"]')).slice(0, 8)
           .map(e => ({cls: (e.className || '').toString().slice(0, 50),
                       text: (e.innerText || '').trim().slice(0, 30)}))
    })
    """
    try:
        info = page.evaluate(script)
    except Exception as exc:  # noqa: BLE001
        print("标题/价格提取失败:", exc)
        return
    print("DEAL:", json.dumps(info, ensure_ascii=False)[:800])


def dump_download_modal(page) -> None:
    frame = next((f for f in page.frames if "downloads.htm" in f.url), None)
    print("MODAL FRAME:", frame.url if frame else None)
    if frame is None:
        print("FRAMES:", [f.url for f in page.frames])
        return
    try:
        print("MODAL TEXT:", frame.locator("body").inner_text()[:500].replace("\n", " | "))
    except Exception:
        pass
    row = frame.get_by_text("当前的详情图", exact=False)
    print("MODAL ROW count:", row.count())
    if row.count() == 0:
        return
    for level in range(1, 4):
        js = (
            "el => { let n = el; for (let i = 0; i < %d; i++) { if (!n) break; n = n.parentElement; } "
            "return n ? n.outerHTML : ''; }" % level
        )
        try:
            print(f"ANC{level}:", row.first.evaluate(js)[:1000])
        except Exception as exc:  # noqa: BLE001
            print(f"ANC{level} 失败:", exc)


def dump_candidates(page, keyword: str, limit: int = 12) -> list[dict]:
    script = """
    (keyword) => {
      const out = [];
      const nodes = document.querySelectorAll('a,button,span,div,li');
      for (const el of nodes) {
        const text = (el.innerText || '').trim();
        if (!text || text.length > 60) continue;
        if (!text.includes(keyword)) continue;
        if (out.length >= 40) break;
        out.push({tag: el.tagName, text, cls: (el.className || '').toString().slice(0, 80),
                  href: el.getAttribute('href') || '', id: el.id || ''});
      }
      return out;
    }
    """
    try:
        return page.evaluate(script, keyword)[:limit]
    except Exception:
        return []


def do_login(session: BrowserSession, cfg, page, out_dir: Path, stamp: str) -> bool:
    print("\n>>> 登录流程")
    # 若不在登录页，尝试点击登录入口
    if "login" not in page.url:
        try:
            session.click_text(WSY["login_entry_texts"])
            page.wait_for_timeout(1500)
        except Exception as exc:  # noqa: BLE001
            print("点击登录入口失败:", exc)
    print("LOGIN URL:", page.url)
    print("LOGIN SHOT:", session.screenshot(out_dir / f"wsy_login_{stamp}.png", full_page=False))

    inputs = page.locator("input")
    print("INPUT count:", inputs.count())
    for i in range(min(inputs.count(), 14)):
        el = inputs.nth(i)
        try:
            print(i, el.get_attribute("type"), el.get_attribute("placeholder"), el.is_visible())
        except Exception:
            continue

    filled = False
    for sel in WSY["username_inputs"]:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.fill(cfg.wsy_username.value)
            print("已填写账号，selector=", sel)
            filled = True
            break
    for sel in WSY["password_inputs"]:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.fill(cfg.wsy_password.value)
            print("已填写密码，selector=", sel)
            break
    if not filled:
        print("未找到账号输入框，可能已登录或页面结构不同")

    submitted = False
    submit = page.locator('input[type="submit"]')
    if submit.count() > 0 and submit.first.is_visible():
        submit.first.click()
        submitted = True
        print("已点击 input[type=submit]")
    if not submitted:
        try:
            session.click_text(WSY["login_submit_texts"])
        except Exception as exc:  # noqa: BLE001
            print("点击提交失败:", exc)

    page.wait_for_timeout(4000)
    print("URL after login:", page.url)
    print("SHOT:", session.screenshot(out_dir / f"wsy_after_login_{stamp}.png", full_page=False))
    body = page.locator("body").inner_text()[:600]
    print("BODY:", body.replace("\n", " | ")[:600])
    logged = "退出" in body
    if logged:
        session.save_state()
        print("登录成功，登录态已保存")
    else:
        print("未检测到登录成功标识（退出）")
    return logged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="执行登录")
    parser.add_argument("--url", default=WSY["home_url"], help="首页地址")
    parser.add_argument("--after", default="", help="登录成功后继续打开的地址")
    parser.add_argument("--click", default="", help="打开 after 后再点击的文案")
    parser.add_argument("--download", action="store_true", help="尝试真实下载弹窗中的目标压缩包")
    args = parser.parse_args()

    cfg = get_config()
    logs = LogService(cfg)
    session = BrowserSession(cfg, logs)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(cfg.base_dir) / "logs" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)

    session.start()
    page = session.page
    try:
        session.goto(args.url)
        page.wait_for_timeout(2000)
        print("TITLE:", page.title())
        print("URL:", page.url)
        print("SCREENSHOT:", session.screenshot(out_dir / f"wsy_home_{stamp}.png", full_page=False))
        (out_dir / f"wsy_home_{stamp}.html").write_text(page.content(), encoding="utf-8")
        dump_products(page)
        dump_categories(page)
        dump_deal(page)
        for keyword in KEYWORDS:
            items = dump_candidates(page, keyword)
            if items:
                print(f"\n== {keyword} ==")
                for item in items[:6]:
                    print(json.dumps(item, ensure_ascii=False))

        if args.login:
            do_login(session, cfg, page, out_dir, stamp)

        if args.after:
            print("\n>>> 打开:", args.after)
            session.goto(args.after)
            page.wait_for_timeout(2000)
            print("AFTER URL:", page.url)
            print("AFTER SHOT:", session.screenshot(out_dir / f"wsy_after_{stamp}.png", full_page=False))
            (out_dir / f"wsy_after_{stamp}.html").write_text(page.content(), encoding="utf-8")
            dump_products(page)
            dump_categories(page)
            print("FRAMES:", [f.url for f in page.frames])
            for keyword in KEYWORDS:
                items = dump_candidates(page, keyword)
                if items:
                    print(f"\n== AFTER {keyword} ==")
                    for item in items[:6]:
                        print(json.dumps(item, ensure_ascii=False))

        if args.click:
            print("\n>>> 点击:", args.click)
            try:
                session.click_text([args.click])
            except Exception as exc:  # noqa: BLE001
                print("点击失败:", exc)
            page.wait_for_timeout(2500)
            print("CLICK SHOT:", session.screenshot(out_dir / f"wsy_click_{stamp}.png", full_page=False))
            print("FRAMES after click:", [f.url for f in page.frames])
            dump_download_modal(page)

            if args.download:
                frame = next((f for f in page.frames if "downloads.htm" in f.url), None)
                if frame is not None:
                    row = frame.get_by_text("当前的详情图", exact=False).first
                    row_li = row.locator("xpath=ancestor::li[1]")
                    link = row_li.get_by_text("下载", exact=True).first
                    print("DOWNLOAD LINK count:", link.count())
                    try:
                        with page.expect_download(timeout=180000) as info:
                            link.click()
                        download = info.value
                        target = out_dir / f"real_download_{stamp}.zip"
                        download.save_as(str(target))
                        print("DOWNLOAD OK:", download.suggested_filename, target.stat().st_size, target)
                    except Exception as exc:  # noqa: BLE001
                        print("DOWNLOAD FAILED:", type(exc).__name__, exc)
            (out_dir / f"wsy_click_{stamp}.html").write_text(page.content(), encoding="utf-8")
            for frame in page.frames:
                try:
                    text = frame.locator("body").inner_text()
                except Exception:
                    continue
                if any(k in text for k in ["当前的详情图", "图片下载", "下载"]):
                    print(f"\nFRAME {frame.url} 文本片段:", text.replace("\n", " | ")[:1200])
                    row = frame.get_by_text("当前的详情图,主图,颜色图:", exact=False)
                    print("  ROW count:", row.count())
                    if row.count() > 0:
                        print("  GRAND:", row.first.evaluate("el => el.parentElement.parentElement.outerHTML")[:1200])
    finally:
        session.close(keep_open=False)


if __name__ == "__main__":
    main()
