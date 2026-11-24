"""闲鱼发布（当前默认测试模式）：登录 → 发布闲鱼页 → 上传 4 图 → 描述 → 价格 → 测试截图。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from automation.base import BaseJob
from automation.selectors import XIANYU
from automation.xianyu_login import XianyuLogin
from core.errors import AppError, ErrorCode
from core.state import TaskRecord, TaskState
from services.descriptor import DescriptorService
from services.pricing import PriceRule
from services.product_store import ProductStore


class XianyuPublishJob(BaseJob):
    module = "xy"

    def __init__(self, cfg, logs, control, session, job, manager) -> None:
        super().__init__(cfg, logs, control, session, job, manager)
        self.store = ProductStore(cfg)
        self.login = XianyuLogin(cfg, logs, session)
        self.pricing = PriceRule(cfg)
        self.descriptor = DescriptorService(cfg, logs)
        self.counters = {"success": 0, "failed": 0, "skipped": 0}

    # ---------- 主流程 ----------
    def run(self) -> None:
        self.login.ensure_logged_in(
            lambda code, message, hint: self.require_human(code, message, hint)
        )

        products = self.store.pending()
        if not products:
            self.job.total = 0
            self.job.message = "没有待发布商品，请先执行“开始下载图片”"
            self.logs.emit(
                self.job.message,
                module=self.module,
                step="precheck",
                status="WARNING",
                level="WARNING",
            )
            return

        products = products[: int(self.cfg.job.max_product_total)]
        self.job.total = len(products)
        self.logs.emit(
            f"待发布商品 {len(products)} 个，模式：{'测试模式（截图）' if self.cfg.test_mode else '真实发布'}",
            module=self.module,
            step="plan",
            status="RUNNING",
        )

        for meta in products:
            self.pause_if_needed()
            task = TaskRecord(
                product_id=str(meta.get("product_id", "")),
                shop_name=str(meta.get("shop_name", "")),
                title=str(meta.get("title", "")),
            )
            with self.task_scope(task):
                self.publish_one(meta)
            self.count(task)

        self.job.done = sum(self.counters.values())
        self.job.success = self.counters["success"]
        self.job.failed = self.counters["failed"]
        self.job.skipped = self.counters["skipped"]
        self.logs.emit(
            f"发布流程结束：成功 {self.counters['success']}，失败 {self.counters['failed']}",
            module=self.module,
            step="summary",
            status="OK",
        )

    def count(self, task: TaskRecord) -> None:
        if task.state is TaskState.SUCCESS:
            self.counters["success"] += 1
        elif task.state is TaskState.SKIPPED:
            self.counters["skipped"] += 1
        else:
            self.counters["failed"] += 1
        self.job.done = sum(self.counters.values())
        self.job.success = self.counters["success"]
        self.job.failed = self.counters["failed"]
        self.job.skipped = self.counters["skipped"]

    # ---------- 单商品 ----------
    def publish_one(self, meta: dict) -> None:
        product_dir = Path(str(meta.get("product_dir", "")))
        if not product_dir.exists():
            raise AppError(ErrorCode.E_IO, f"商品目录不存在：{product_dir}")

        product_id = str(meta.get("product_id", ""))
        with self.step("open_publish", "打开发布闲置页", product_id=product_id):
            self.session.goto(str(self.cfg.xianyu.publish_url))
            self.session.page.wait_for_timeout(2500)

        self.dump_page_structure(product_id)

        images = self.pick_images(meta, product_dir)
        with self.step("upload_images", f"上传 {len(images)} 张图片", product_id=product_id):
            self.upload_images(images)

        with self.step("set_condition", "成色设为全新", product_id=product_id):
            self.set_condition_new()

        title = str(meta.get("title", ""))
        desc_body, source = self.descriptor.generate(meta)
        description = self.build_description(title, desc_body)
        with self.step("fill_description", "填写宝贝描述", product_id=product_id):
            self.fill_description(description)

        price = self.pricing.apply(meta.get("price"))
        with self.step("fill_price", f"填写价格 {price}", product_id=product_id):
            self.fill_price(price)

        with self.step("select_location", "选择所在地（第一个）", product_id=product_id):
            self.select_first_location()

        preview = product_dir / "publish_preview.png"
        with self.step("pre_submit", "保存发布前截图", product_id=product_id):
            shot = self.session.screenshot(preview, full_page=True)
            (product_dir / "description.txt").write_text(description, encoding="utf-8")
            result = {
                "product_id": product_id,
                "mode": "test" if self.cfg.test_mode else "real",
                "submitted": False,
                "title": title,
                "price": price,
                "description_source": source,
                "screenshot": shot,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            submitted = False
            result_shot = ""
            if not self.cfg.test_mode:
                clicked = self.click_submit()
                if clicked:
                    submitted = self.confirm_publish(int(self.cfg.xianyu.wait_publish_success_timeout))
                try:
                    result_shot = self.session.screenshot(
                        product_dir / "publish_result.png", full_page=True
                    )
                except Exception:  # noqa: BLE001 - 结果截图失败不影响记录
                    result_shot = ""
                result["submitted"] = submitted
                if result_shot:
                    result["result_screenshot"] = result_shot
            (product_dir / "publish_result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            publish_status = (
                "TESTED" if self.cfg.test_mode else ("SUBMITTED" if submitted else "FAILED")
            )
            update_fields = {
                "publish_mode": result["mode"],
                "publish_price": price,
                "publish_at": result["at"],
                "publish_screenshot": shot,
                "description_source": source,
                "submitted": result["submitted"],
            }
            if result_shot:
                update_fields["publish_result_screenshot"] = result_shot
            # 只回写发布字段，避免覆盖商品原价等下载阶段数据
            self.store.update_publish_status(product_id, publish_status, update_fields)
            if self.cfg.test_mode:
                message = "测试模式：已填写并截图，未点击发布"
                level = "INFO"
            elif submitted:
                message = "真实发布已确认成功"
                level = "INFO"
            else:
                message = "真实发布未确认成功（未找到按钮或未出现成功标识），请人工核对"
                level = "WARNING"
            self.logs.emit(
                message,
                module=self.module,
                step="pre_submit",
                status="SUCCESS" if self.cfg.test_mode or submitted else "FAILED",
                level=level,
                product_id=product_id,
                file_path=str(preview),
            )

        with self.step("back_home", "返回闲鱼首页，准备下一件", product_id=product_id):
            self.session.goto(str(self.cfg.xianyu.home_url))
            self.session.page.wait_for_timeout(1500)

    # ---------- 具体动作 ----------
    def pick_images(self, meta: dict, product_dir: Path) -> list[Path]:
        limit = int(self.cfg.xianyu.max_images_upload)
        files: list[Path] = []
        for rel in meta.get("images", []):
            path = product_dir / str(rel)
            if path.exists():
                files.append(path)
            if len(files) >= limit:
                break
        if not files:
            raise AppError(ErrorCode.E_IO, "商品目录中没有可用图片")
        return files

    def upload_images(self, images: list[Path]) -> None:
        page = self.session.page
        file_input = page.locator(XIANYU["upload_inputs"][0])
        if file_input.count() > 0:
            file_input.first.set_input_files([str(p) for p in images])
        else:
            with page.expect_file_chooser(timeout=15000) as chooser_info:
                page.get_by_text("添加首图", exact=False).first.click()
            chooser_info.value.set_files([str(p) for p in images])
        page.wait_for_timeout(4000)

    def fill_description(self, description: str) -> None:
        page = self.session.page
        textarea = page.locator("textarea")
        if textarea.count() > 0:
            area = textarea.first
            self.session.wait_visible(area)
            area.click()
            area.fill(description)
        else:
            editable = page.locator("div[contenteditable='true']").first
            self.session.wait_visible(editable)
            editable.click()
            page.keyboard.insert_text(description)
        self.session.delay(0.6)

    def build_description(self, title: str, body: str) -> str:
        limit = int(self.cfg.xianyu.desc_max_len)
        text = f"{title}\n\n{body}".strip()
        return text[:limit]

    def fill_price(self, price: float) -> None:
        page = self.session.page
        value = f"{price:.2f}"
        for label_text in XIANYU["price_label_texts"]:
            try:
                label = page.get_by_text(label_text, exact=True).first
                if label.count() and label.is_visible():
                    target = label.locator("xpath=following::input[1]")
                    if target.count() and target.first.is_visible():
                        target.first.click()
                        target.first.fill(value)
                        self.logs.emit(
                            f"价格已填写（{label_text} 后第一个输入框）",
                            module=self.module,
                            step="fill_price",
                            status="OK",
                        )
                        return
            except Exception:  # noqa: BLE001
                continue
        # 兜底：占位符含 0.00 或 ¥ 的输入框
        candidates = page.locator('input[placeholder*="0.00"], input[placeholder*="¥"]')
        if candidates.count() > 0:
            candidates.first.click()
            candidates.first.fill(value)
            return
        raise AppError(ErrorCode.E_SELECTOR, "未找到价格输入框")

    def select_first_location(self) -> None:
        page = self.session.page
        item = self._form_item(XIANYU["location_label_text"])
        if item is None:
            self.logs.emit(
                "当前发布页未找到所在地字段",
                module=self.module,
                step="select_location",
                status="WARNING",
                level="WARNING",
            )
            return
        try:
            text = item.inner_text().replace(XIANYU["location_label_text"], "").strip()
            if text and XIANYU["placeholder_text"] not in text:
                self.logs.emit(
                    f"所在地使用页面默认值：{text[:40]}",
                    module=self.module,
                    step="select_location",
                    status="OK",
                )
                return
            control = item.locator(".ant-form-item-control").first
            control.click(timeout=3000)
            page.wait_for_timeout(1200)
            options = page.locator(XIANYU["select_option_selector"] + ", .ant-modal:visible li")
            if options.count() > 0:
                options.first.click()
                page.wait_for_timeout(800)
                self.logs.emit(
                    "所在地已选择第一个候选",
                    module=self.module,
                    step="select_location",
                    status="OK",
                )
            else:
                self.logs.emit(
                    "所在地下拉无候选，保留页面默认值",
                    module=self.module,
                    step="select_location",
                    status="WARNING",
                    level="WARNING",
                )
        except Exception as exc:  # noqa: BLE001
            self.logs.emit(
                "所在地处理失败，保留页面默认值",
                module=self.module,
                step="select_location",
                status="WARNING",
                level="WARNING",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _form_item(self, label_text: str):
        """按字段标签定位 Ant Design 表单项容器。"""
        page = self.session.page
        label = page.get_by_text(label_text, exact=True).first
        if label.count() == 0:
            return None
        for selector in XIANYU["form_item_selectors"]:
            scope = page.locator(selector).filter(has=label)
            if scope.count() > 0:
                return scope.first
        return None

    def set_condition_new(self) -> None:
        """闲鱼会根据图片自动填“成色”（可能是空值），统一改为“全新”。"""
        page = self.session.page
        label_text = XIANYU["condition_label_text"]
        new_option = XIANYU["condition_new_option"]
        item = self._form_item(label_text)
        if item is None:
            self.logs.emit(
                "未找到成色字段（页面可能未识别属性），保留当前值",
                module=self.module,
                step="set_condition",
                status="WARNING",
                level="WARNING",
            )
            return
        try:
            current = item.inner_text().replace(label_text, "").strip()
            if new_option in current:
                self.logs.emit(
                    "成色已是全新",
                    module=self.module,
                    step="set_condition",
                    status="OK",
                )
                return
            trigger = item.locator(XIANYU["select_trigger_selector"]).first
            if trigger.count() == 0:
                self.logs.emit(
                    "成色下拉未找到，保留当前值",
                    module=self.module,
                    step="set_condition",
                    status="WARNING",
                    level="WARNING",
                )
                return
            trigger.click()
            page.wait_for_timeout(600)
            options = page.locator(XIANYU["select_option_selector"])
            for index in range(options.count()):
                option = options.nth(index)
                if new_option in option.inner_text():
                    option.click()
                    page.wait_for_timeout(400)
                    self.logs.emit(
                        "成色已改为全新",
                        module=self.module,
                        step="set_condition",
                        status="OK",
                    )
                    return
            page.keyboard.press("Escape")
            self.logs.emit(
                "成色下拉未出现“全新”选项，保留当前值",
                module=self.module,
                step="set_condition",
                status="WARNING",
                level="WARNING",
            )
        except Exception as exc:  # noqa: BLE001
            self.logs.emit(
                "成色设置失败，保留当前值",
                module=self.module,
                step="set_condition",
                status="WARNING",
                level="WARNING",
                error=f"{type(exc).__name__}: {exc}",
            )

    def click_submit(self) -> bool:
        page = self.session.page
        for text in XIANYU["submit_texts"]:
            try:
                button = page.get_by_text(text, exact=True).last
                if button.count() and button.is_visible():
                    button.click()
                    page.wait_for_timeout(5000)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def confirm_publish(self, timeout_sec: int) -> bool:
        """等待发布成功标识或离开发布页；超时视为未确认成功。"""
        page = self.session.page
        deadline = time.time() + max(5, int(timeout_sec))
        while time.time() < deadline:
            for marker in XIANYU["publish_success_markers"]:
                try:
                    loc = page.get_by_text(marker, exact=False)
                    if loc.count() and loc.first.is_visible():
                        return True
                except Exception:  # noqa: BLE001 - 元素抖动时继续等待
                    continue
            try:
                if "/publish" not in page.url:
                    return True
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(1000)
        return False

    def dump_page_structure(self, product_id: str) -> None:
        """记录发布页结构，便于页面改版后快速校准选择器。"""
        page = self.session.page
        try:
            info = page.evaluate(
                """
                () => ({
                  inputs: Array.from(document.querySelectorAll('input')).slice(0, 20)
                    .map(e => ({type: e.type, placeholder: e.placeholder})),
                  textareas: Array.from(document.querySelectorAll('textarea'))
                    .map(e => ({placeholder: e.placeholder, maxLength: e.maxLength})),
                  buttons: Array.from(document.querySelectorAll('button')).slice(0, 15)
                    .map(e => (e.innerText || '').trim().slice(0, 20))
                })
                """
            )
            self.logs.emit(
                f"发布页结构：{json.dumps(info, ensure_ascii=False)[:800]}",
                module=self.module,
                step="dump",
                status="INFO",
                product_id=product_id,
                screenshot_path=self.session.screenshot(full_page=False),
            )
        except Exception as exc:  # noqa: BLE001
            self.logs.emit(
                "发布页结构导出失败",
                module=self.module,
                step="dump",
                status="WARNING",
                level="WARNING",
                product_id=product_id,
                error=f"{type(exc).__name__}: {exc}",
            )
