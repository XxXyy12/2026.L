"""步骤封装：统一计时、日志、错误截图与暂停检查。

每个可观察动作都通过 step_scope 包裹，保证日志字段完整、暂停粒度为“步骤”。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from core.control import PauseRequested, StopRequested


@contextmanager
def step_scope(
    ctx,
    module: str,
    name: str,
    product_id: str = "",
    detail: str = "",
    screenshot_on_error: bool = True,
) -> Iterator[None]:
    """ctx 需提供 logs/control/session/job/task 与 pause_if_needed()。"""
    started = time.perf_counter()
    ctx.pause_if_needed()
    ensure_browser = getattr(ctx, "ensure_browser", None)
    if callable(ensure_browser):
        ensure_browser()
    ctx.job.current_step = name
    if ctx.task is not None:
        ctx.task.step = name
    ctx.logs.emit(
        f"{detail}（{name}）",
        module=module,
        step=name,
        status="RUNNING",
        product_id=product_id,
    )
    try:
        yield
    except (StopRequested, PauseRequested):
        raise
    except Exception as exc:  # noqa: BLE001 - 需要统一落错误日志
        shot = ""
        if screenshot_on_error:
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                path = Path(ctx.logs.screenshot_dir) / f"error_{module}_{name}_{stamp}.png"
                shot = ctx.session.screenshot(path)
            except Exception:  # 截图失败不能掩盖原始异常
                shot = ""
        ctx.logs.emit(
            f"步骤失败：{detail}",
            module=module,
            step=name,
            status="FAILED",
            level="ERROR",
            product_id=product_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
            screenshot_path=shot,
        )
        raise
    else:
        ctx.logs.emit(
            f"步骤完成：{detail}",
            module=module,
            step=name,
            status="OK",
            product_id=product_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
