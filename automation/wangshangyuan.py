"""网商园采集下载任务：登录 → 金牌档口前 5 店 → 30日新品/男装 → 前 3 商品 → 一键下载 → 解压落盘。"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from automation.base import BaseJob
from automation.selectors import WSY
from core.errors import AppError, ErrorCode, NeedHumanError
from core.state import TaskRecord, TaskState
from services.unzip_service import UnzipService
from services.product_store import ProductStore
from services.login_guard import LoginGuard
from services.shop_plan_store import ShopPlanStore

LOGIN_URL = "https://www.wsy.com/member/login.htm"
GOLD_STALL_URL = "https://www.wsy.com/new/goldStall"


class WangshangyuanJob(BaseJob):
    module = "wsy"

    def __init__(self, cfg, logs, control, session, job, manager) -> None:
        super().__init__(cfg, logs, control, session, job, manager)
        self.store = ProductStore(cfg)
        self.unzip = UnzipService(cfg)
        self.guard = LoginGuard(cfg, "wsy", cfg.wsy_username.value)
        self.plans = ShopPlanStore(cfg)
        self.login_count = 0
        self.counters = {"success": 0, "failed": 0, "skipped": 0}
        self.shop_failures = 0
        self.relogin_attempts = 0
        self.processed = 0

    # ---------- 主流程 ----------
    def run(self) -> None:
        with self.step("open_home", "打开网商园首页"):
            self.session.goto(str(self.cfg.wangshangyuan.base_url))

        self.ensure_login()

        shops = self.collect_gold_stalls()
        if not shops:
            raise AppError(ErrorCode.E_SELECTOR, "金牌档口页未找到店铺卡片", "请检查页面结构或网络")

        per_shop = int(self.cfg.wangshangyuan.product_count)
        batch_size = max(1, int(self.cfg.wangshangyuan.get("shop_batch_size", 2)))
        plan_items: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
        skipped_shops = 0

        for shop_index, shop in enumerate(shops, start=1):
            if len(plan_items) >= batch_size:
                break
            self.pause_if_needed()
            cached = self.plans.get(shop["url"])
            if cached:
                products = list(cached.get("products", []))[:per_shop]
            else:
                try:
                    products = self.collect_shop_products(shop, shop_index)
                except NeedHumanError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单店失败不影响后续
                    self.shop_failures += 1
                    self.logs.emit(
                        f"店铺采集失败：{shop['name']}",
                        module=self.module,
                        step="shop_failed",
                        status="FAILED",
                        level="ERROR",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    continue
                self.plans.put(shop["url"], shop["name"], products)

            pending = [p for p in products if not self.find_existing(p["item_id"])]
            if not pending:
                skipped_shops += 1
                self.logs.emit(
                    f"店铺 {shop['name']} 的商品均已下载，跳过访问",
                    module=self.module,
                    step="shop_skip",
                    status="SKIPPED",
                )
                continue
            plan_items.append((shop_index, shop, pending[:per_shop]))

        limit = int(self.cfg.job.max_product_total)
        total = min(sum(len(items) for _, _, items in plan_items), limit)
        self.job.total = total
        self.job.message = (
            f"本批 {len(plan_items)} 家店铺，共 {total} 个待下载商品"
            f"（跳过已下载商品；店铺批次上限 {batch_size}）"
        )
        self.logs.emit(self.job.message, module=self.module, step="plan", status="RUNNING")

        for shop_index, shop, products in plan_items:
            for product_index, product in enumerate(products, start=1):
                if self.processed >= total:
                    break
                self.pause_if_needed()
                self.processed += 1
                item_id = product["item_id"]
                task = TaskRecord(
                    product_id=f"S{shop_index:02d}-P{product_index:02d}-{item_id}",
                    shop_name=shop["name"],
                    title=product.get("title", ""),
                )
                with self.task_scope(task):
                    self.download_one(shop, shop_index, product, product_index, task)
                self._count(task)
                self.idle_wait(
                    random.uniform(
                        float(self.cfg.timing.between_products_min),
                        float(self.cfg.timing.between_products_max),
                    )
                )
            if self.processed < total:
                pause = random.uniform(
                    float(self.cfg.wangshangyuan.between_shops_min),
                    float(self.cfg.wangshangyuan.between_shops_max),
                )
                self.logs.emit(
                    f"店铺之间休整 {pause:.0f} 秒，降低登录/访问频率",
                    module=self.module,
                    step="between_shops",
                    status="INFO",
                )
                self.idle_wait(pause)

        self.job.done = self.counters["success"] + self.counters["failed"] + self.counters["skipped"]
        self.job.success = self.counters["success"]
        self.job.failed = self.counters["failed"]
        self.job.skipped = self.counters["skipped"]
        if self.shop_failures:
            self.job.message = (
                f"完成 {self.job.done}/{self.job.total}，{self.shop_failures} 家店铺采集失败（详见日志）"
            )
        self.logs.emit(
            f"采集结束：成功 {self.counters['success']}，失败 {self.counters['failed']}，跳过 {self.counters['skipped']}"
            + (f"，店铺失败 {self.shop_failures}" if self.shop_failures else ""),
            module=self.module,
            step="summary",
            status="OK",
        )

    def _count(self, task: TaskRecord) -> None:
        if task.state is TaskState.SUCCESS:
            self.counters["success"] += 1
        elif task.state is TaskState.SKIPPED:
            self.counters["skipped"] += 1
        elif task.state in (TaskState.FAILED, TaskState.STOPPED):
            self.counters["failed"] += 1
        self.job.done = self.counters["success"] + self.counters["failed"] + self.counters["skipped"]
        self.job.success = self.counters["success"]
        self.job.failed = self.counters["failed"]
        self.job.skipped = self.counters["skipped"]

    # ---------- 登录 ----------
    def is_logged_in(self) -> bool:
        try:
            body = self.session.page.locator("body").inner_text(timeout=5000)
        except Exception:  # noqa: BLE001
            return False
        return "退出" in body

    def ensure_login(self) -> None:
        max_per_run = int(self.cfg.login.max_per_run)
        # 手动登录模式：先在首页判断登录态，未登录直接去登录页，不先去金牌档口
        if bool(self.cfg.wangshangyuan.get("manual_login", False)):
            with self.step("login_check", "检测网商园登录态"):
                logged = self.is_logged_in()
            if logged:
                self.logs.emit("网商园已登录，跳过登录", module=self.module, step="login", status="OK")
                self.session.save_state()
                return
            self.logs.emit(
                "首页未检测到登录态，进入登录页等待人工登录",
                module=self.module,
                step="login",
                status="WAITING_HUMAN",
                level="WARNING",
            )
            self.check_login_allowed(max_per_run)
            self.manual_login()
            self.guard.mark_login()
            self.login_count += 1
            return

        with self.step("login_check", "检测网商园登录态"):
            logged = self.is_logged_in()
        if logged:
            self.logs.emit("网商园已登录", module=self.module, step="login", status="OK")
            self.session.save_state()
            return

        self.check_login_allowed(max_per_run)
        self.perform_login()
        if not self.is_logged_in():
            self.require_human(
                ErrorCode.E_LOGIN.value,
                "网商园自动登录未成功，请在弹出的浏览器中手动登录",
                "登录完成后回到控制台点击“已处理，继续”",
            )
            if not self.is_logged_in():
                raise NeedHumanError(ErrorCode.E_LOGIN, "人工登录后仍未检测到登录态")
        self.guard.mark_login()
        self.login_count += 1
        self.session.save_state()

    def check_login_allowed(self, max_per_run: int) -> None:
        if self.login_count >= max_per_run:
            raise NeedHumanError(
                ErrorCode.E_LOGIN,
                f"本次任务已登录过 {self.login_count} 次，为避免账号被标记，已停止继续登录",
                "请结束本次任务，冷却后再运行，或更换账号/网络",
            )
        if not self.guard.can_login():
            raise NeedHumanError(
                ErrorCode.E_LOGIN,
                f"登录冷却中，剩余约 {self.guard.remaining_text()}，已停止以避免频繁登录",
                "请等待冷却结束再运行；频繁重登会导致账号被站点标记",
            )

    def manual_login(self) -> None:
        """打开登录页，由用户手动登录（持久化 Edge 目录会保留登录态）。"""
        self.session.goto(LOGIN_URL)
        self.session.page.wait_for_timeout(1500)
        self.require_human(
            ErrorCode.E_LOGIN.value,
            "请手动登录网商园（浏览器已打开登录页）",
            "在浏览器中完成账号密码登录后，回到控制台点击“已处理，继续”，系统会自动校验金牌档口页",
        )
        # 用金牌档口页验证登录是否真正生效（首页“退出”可能是假登录态）
        self.session.goto(GOLD_STALL_URL)
        self.session.page.wait_for_timeout(2000)
        if self.is_login_page():
            self.require_human(
                ErrorCode.E_LOGIN.value,
                "登录后访问金牌档口页仍被重定向到登录页",
                "请重新登录并耐心等待页面加载，确认金牌档口页可见后再点击“已处理，继续”",
            )
            self.session.goto(GOLD_STALL_URL)
            self.session.page.wait_for_timeout(2000)
            if self.is_login_page():
                raise NeedHumanError(ErrorCode.E_LOGIN, "人工登录后仍被跳转登录页")
        self.session.save_state()
        self.logs.emit("人工登录完成，登录态已保存", module=self.module, step="login", status="OK")

    def is_login_page(self) -> bool:
        page = self.session.page
        try:
            if "member/login" in page.url:
                return True
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:  # noqa: BLE001
            return False
        return "供应商请点此登录" in body

    def recover_if_logged_out(self, reopen) -> bool:
        """店铺页被跳回登录页时不再自动重登，直接停止，避免频繁登录被标记。"""
        if not self.is_login_page():
            return False
        shot = self.session.screenshot()
        self.logs.emit(
            "店铺页被重定向到登录页，已停止自动重登（保护账号）",
            module=self.module,
            step="risk",
            status="WAITING_HUMAN",
            level="WARNING",
            screenshot_path=shot,
        )
        raise NeedHumanError(
            ErrorCode.E_RISK,
            f"网商园店铺页被跳转登录（登录冷却剩余约 {self.guard.remaining_text()}），已停止以避免频繁登录",
            "请冷却 10–30 分钟或更换账号/网络后再运行；不要连续重试登录",
        )

    def perform_login(self) -> None:
        with self.step("open_login", "打开发网商园登录页"):
            self.session.goto(LOGIN_URL)
        with self.step("fill_login", "填写账号密码"):
            page = self.session.page
            username = page.locator(WSY["username_inputs"][0]).first
            if not username.is_visible():
                username = page.locator('input[placeholder*="账号"]').first
            self.session.wait_visible(username)
            username.click()
            username.fill(self.cfg.wsy_username.value)
            self.session.delay(0.5)
            password = page.locator(WSY["password_inputs"][0]).first
            self.session.wait_visible(password)
            password.click()
            password.fill(self.cfg.wsy_password.value)
            self.session.delay(0.5)
        with self.step("submit_login", "提交登录"):
            page.locator('input[type="submit"]').first.click()
            page.wait_for_load_state("domcontentloaded")
            self.session.delay(1.0)
            # 登录后回首页停留，模拟人工并让站点写入完整登录态
            self.session.goto(str(self.cfg.wangshangyuan.base_url))
            page.wait_for_timeout(2000)

    # ---------- 金牌档口 ----------
    def collect_gold_stalls(self) -> list[dict[str, Any]]:
        with self.step("open_gold_stall", "打开金牌档口页"):
            self.session.goto(GOLD_STALL_URL)
            self.session.page.wait_for_timeout(1800)
            # 首页“退出”可能是假登录态，金牌档口页被跳登录时直接停止，不自动重登
            self.recover_if_logged_out(lambda: None)
        shops: list[dict[str, Any]] = []
        cards = self.session.page.locator(".stall-item")
        try:
            total = cards.count()
        except Exception:  # noqa: BLE001
            total = 0
        self.logs.emit(
            f"金牌档口卡片数：{total}",
            module=self.module,
            step="gold_stall_count",
            status="OK",
        )
        limit = min(int(self.cfg.wangshangyuan.shop_count), total)
        for index in range(limit):
            card = cards.nth(index)
            try:
                link = card.locator(".s-name a").first
                name = link.inner_text().strip()
                href = link.evaluate("el => el.href")
            except Exception as exc:  # noqa: BLE001
                self.logs.emit(
                    f"店铺卡片解析失败（第 {index + 1} 个）",
                    module=self.module,
                    step="gold_stall_parse",
                    status="WARNING",
                    level="WARNING",
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            if not href:
                continue
            shops.append({"name": name or f"店铺{index + 1}", "url": href})
            self.logs.emit(
                f"金牌档口第 {index + 1} 家：{name}",
                module=self.module,
                step="gold_stall",
                status="OK",
                file_path=href,
            )
        self.session.page.screenshot(
            path=str(Path(self.logs.screenshot_dir) / "gold_stall.png"), full_page=False
        )
        return shops

    # ---------- 店铺内商品 ----------
    def collect_shop_products(self, shop: dict[str, Any], shop_index: int) -> list[dict[str, Any]]:
        shop_id = shop["url"].rstrip("/").split("/")[-1]
        search_url = f"https://www.wsy.com/store/p/{shop_id}/search.htm?d=30"
        with self.step("open_shop", f"打开店铺：{shop['name']}"):
            self.session.goto(shop["url"])
            self.session.page.wait_for_timeout(1500)
            self.recover_if_logged_out(lambda: self.session.goto(shop["url"]))

        with self.step("filter_30d", "进入 30 日新品"):
            link = self.session.page.locator('a[href*="search.htm?d=30"]').first
            if link.count() and link.is_visible():
                link.click()
                self.session.page.wait_for_load_state("domcontentloaded")
                self.session.delay()
                self.recover_if_logged_out(lambda: self.session.goto(search_url))
            else:
                # 兜底：按店铺 ID 直接拼接 30 日新品搜索页
                self.session.goto(search_url)

        with self.step("filter_menswear", "筛选男装"):
            page = self.session.page
            sections = page.locator(".type_section")
            clicked = False
            for index in range(min(sections.count(), 8)):
                section = sections.nth(index)
                try:
                    if "男装" in section.inner_text():
                        all_link = section.locator('a[href*="pid=30"]').first
                        if all_link.count() and all_link.is_visible():
                            all_link.click()
                            page.wait_for_load_state("domcontentloaded")
                            self.session.delay()
                            clicked = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not clicked:
                self.logs.emit(
                    "未找到“男装”分类筛选项，按当前 30 日新品列表继续",
                    module=self.module,
                    step="filter_menswear",
                    status="WARNING",
                    level="WARNING",
                )

        with self.step("list_products", "读取商品列表"):
            page = self.session.page
            self.recover_if_logged_out(lambda: self.session.goto(search_url))
            if self.is_login_page():
                raise NeedHumanError(
                    ErrorCode.E_RISK,
                    "无法进入店铺 30 日新品页（持续跳转登录）",
                    "请手动登录后点击“已处理，继续”；必要时等待几分钟再试",
                )
            anchors = page.locator('.tit a[href*="/item.htm?id="]')
            if anchors.count() == 0:
                anchors = page.locator('a[href*="/item.htm?id="]')
            products: list[dict[str, Any]] = []
            seen: set[str] = set()
            for index in range(anchors.count()):
                anchor = anchors.nth(index)
                try:
                    href = anchor.get_attribute("href") or ""
                    text = anchor.inner_text().strip()
                    if not href or not text:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                item_id_match = re.search(r"id=(\d+)", href)
                if not item_id_match or item_id_match.group(1) in seen:
                    continue
                seen.add(item_id_match.group(1))
                products.append(
                    {
                        "item_id": item_id_match.group(1),
                        "title": text,
                        "url": href if href.startswith("http") else f"https://www.wsy.com{href}",
                    }
                )
                if len(products) >= int(self.cfg.wangshangyuan.product_count):
                    break
            if not products:
                try:
                    body_text = page.locator("body").inner_text(timeout=3000)[:1500]
                except Exception:  # noqa: BLE001
                    body_text = ""
                if "暂无" in body_text or "没有找到" in body_text:
                    self.logs.emit(
                        f"店铺 {shop['name']} 的 30 日新品为空，跳过该店铺",
                        module=self.module,
                        step="list_products",
                        status="SKIPPED",
                        level="WARNING",
                    )
                    return []
                raise AppError(ErrorCode.E_SELECTOR, "30 日新品页未找到商品卡片")
            self.logs.emit(
                f"店铺 {shop['name']} 取到 {len(products)} 个商品",
                module=self.module,
                step="list_products",
                status="OK",
            )
            return products

    # ---------- 单商品下载 ----------
    def download_one(self, shop: dict[str, Any], shop_index: int, product: dict[str, Any],
                     product_index: int, task: TaskRecord) -> None:
        existing = self.find_existing(product["item_id"])
        if existing and bool(self.cfg.job.skip_existing_success):
            task.state = TaskState.SKIPPED
            self.logs.emit(
                "该商品已成功下载过，跳过",
                module=self.module,
                step="skip_existing",
                status="SKIPPED",
                product_id=task.product_id,
                file_path=str(existing.get("product_dir", "")),
            )
            return

        with self.step("open_product", f"打开商品：{product['title']}"):
            self.session.goto(product["url"])
            self.session.page.wait_for_timeout(1200)
            self.recover_if_logged_out(lambda: self.session.goto(product["url"]))

        title, price = self.read_product_info(product["title"])
        item_id = product["item_id"]
        product_id = f"S{shop_index:02d}-P{product_index:02d}-{item_id}"
        product_dir = self.store.product_dir(shop_index, shop["name"], product_index, title, item_id)
        raw_dir = product_dir / str(self.cfg.download.raw_subdir)
        images_dir = product_dir / str(self.cfg.download.images_subdir)

        archive_path = self.download_archive(product_id, raw_dir)
        images: list[Path] = []
        status = "SUCCESS"
        with self.step("extract", "解压图片", product_id=product_id):
            try:
                images = self.unzip.extract(archive_path, images_dir)
            except AppError as exc:
                status = "PARTIAL"
                self.logs.emit(
                    f"解压失败：{exc.message}",
                    module=self.module,
                    step="extract",
                    status="WARNING",
                    level="WARNING",
                    product_id=product_id,
                    file_path=str(archive_path),
                )
        if not images:
            status = "PARTIAL"
            self.logs.emit(
                "未解压出图片，标记为 PARTIAL，发布阶段将跳过",
                module=self.module,
                step="extract",
                status="WARNING",
                level="WARNING",
                product_id=product_id,
                screenshot_path=str(images_dir),
            )

        meta = {
            "product_id": product_id,
            "item_id": item_id,
            "shop_name": shop["name"],
            "shop_url": shop["url"],
            "title": title,
            "price": price,
            "source_url": product["url"],
            "raw_file": str(archive_path.relative_to(product_dir)),
            "product_dir": str(product_dir),
            "images": [str(p.relative_to(product_dir)) for p in images],
            "image_count": len(images),
            "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            "download_status": status,
            "publish_status": "PENDING",
        }
        with self.step("write_meta", "写入 meta.json 并更新清单", product_id=product_id):
            self.store.write_meta(product_dir, meta)
            self.store.upsert(meta)
        self.logs.emit(
            f"下载完成：{title}（{len(images)} 张图片）",
            module=self.module,
            step="done",
            status="SUCCESS",
            product_id=product_id,
            file_path=str(product_dir),
        )

    def read_product_info(self, fallback_title: str) -> tuple[str, float | None]:
        page = self.session.page
        title = fallback_title
        try:
            block = page.locator(".item-mb").first.inner_text(timeout=3000)
            first_line = block.splitlines()[0].strip() if block else ""
            if first_line:
                title = first_line
        except Exception:  # noqa: BLE001
            cleaned = page.title().split(" -")[0].strip()
            if cleaned:
                title = cleaned

        price: float | None = None
        try:
            text = page.locator(".p_price").first.inner_text(timeout=3000)
            match = re.search(r"(\d+(?:\.\d+)?)", text)
            if match:
                price = float(match.group(1))
        except Exception:  # noqa: BLE001
            try:
                body = page.locator("body").inner_text(timeout=3000)
                match = re.search(r"批价\s*¥\s*(\d+(?:\.\d+)?)", body)
                if match:
                    price = float(match.group(1))
            except Exception:  # noqa: BLE001
                price = None
        return title, price

    def download_archive(self, product_id: str, raw_dir: Path) -> Path:
        with self.step("click_download", "点击一键下载", product_id=product_id):
            page = self.session.page
            button = self._find_download_button()
            self.session.wait_clickable(button)
            button.click()
            self.session.delay(0.8)

        with self.step("wait_modal", "等待下载弹窗", product_id=product_id):
            context = self._find_download_context(timeout_sec=20)
            if context is None:
                frames = [f.url for f in page.frames]
                self.logs.emit(
                    "一键下载弹窗未出现",
                    module=self.module,
                    step="wait_modal",
                    status="WARNING",
                    level="WARNING",
                    product_id=product_id,
                    error=f"frames={frames}",
                )
                raise AppError(ErrorCode.E_DOWNLOAD, "一键下载弹窗未出现", "检查页面是否改版或账号权限")

        with self.step("click_download_row", "点击“当前的详情图,主图,颜色图”下载", product_id=product_id):
            row = context.get_by_text(WSY["detail_row_text"], exact=False).first
            row.wait_for(state="visible", timeout=15000)
            row_li = row.locator("xpath=ancestor::li[1]")
            link = row_li.get_by_text("下载", exact=True).first
            link.wait_for(state="visible", timeout=10000)
            try:
                with page.expect_download(timeout=int(self.cfg.timing.download_timeout) * 1000) as info:
                    link.click()
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    ErrorCode.E_DOWNLOAD,
                    "未捕获到压缩包下载",
                    "确认下载按钮是否触发新窗口或需要登录",
                ) from exc
            download = info.value
            filename = download.suggested_filename or f"{product_id}.zip"
            safe_name = re.sub(r'[\\/:*?"<>|]+', "_", filename)
            target = raw_dir / safe_name
            download.save_as(str(target))
            size = target.stat().st_size
            self.logs.emit(
                f"压缩包已保存：{filename}（{size} 字节）",
                module=self.module,
                step="save_archive",
                status="OK",
                product_id=product_id,
                file_path=str(target),
            )
            if size <= 0:
                raise AppError(ErrorCode.E_DOWNLOAD, "下载文件为空")
            return target

    def _find_download_button(self):
        """优先点击商品操作区的“一键下载”，避免误点顶部快捷入口。"""
        page = self.session.page
        candidates = [
            page.locator("a.J_pic_upload", has_text="一键下载"),
            page.locator(".item-btn").get_by_text("一键下载", exact=True),
            page.get_by_text("一键下载", exact=True),
        ]
        for candidate in candidates:
            try:
                if candidate.count() > 0 and candidate.first.is_visible():
                    return candidate.first
            except Exception:  # noqa: BLE001
                continue
        raise AppError(ErrorCode.E_SELECTOR, "未找到“一键下载”按钮")

    def _find_download_context(self, timeout_sec: int):
        """弹窗可能在 iframe（downloads.htm）内，也可能直接在主页面。"""
        page = self.session.page
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            for frame in page.frames:
                if "downloads.htm" in frame.url:
                    return frame
            for context in [page, *page.frames]:
                try:
                    if context.get_by_text(WSY["detail_row_text"], exact=False).count() > 0:
                        return context
                except Exception:  # noqa: BLE001
                    continue
            time.sleep(0.4)
        return None

    # ---------- 已有记录 ----------
    def find_existing(self, item_id: str) -> dict[str, Any] | None:
        for item in self.store.load_all():
            if str(item.get("item_id")) == item_id and item.get("download_status") in ("SUCCESS", "PARTIAL"):
                return item
        return None
