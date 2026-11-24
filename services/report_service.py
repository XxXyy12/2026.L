"""运行报告：把一次任务的结果落盘到 `data/reports/report_<时间戳>.json`。

报告只包含任务状态与商品结果，不含账号、密码、Cookie 等敏感信息。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Config
from core.state import JobRecord


def build_report(job: JobRecord) -> dict[str, Any]:
    return {
        "run_id": job.run_id,
        "run_type": job.run_type.value,
        "state": job.state.value,
        "test_mode": job.test_mode,
        "simulated": job.simulated,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "total": job.total,
        "done": job.done,
        "success": job.success,
        "failed": job.failed,
        "skipped": job.skipped,
        "message": job.message,
        "tasks": [task.to_dict() for task in job.tasks],
    }


def write_run_report(cfg: Config, job: JobRecord) -> Path:
    """写入本次任务报告，返回报告路径。同名文件已存在时追加 run_id 避免覆盖。"""
    out_dir = cfg.base_dir / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"report_{stamp}.json"
    if path.exists():
        path = out_dir / f"report_{stamp}_{job.run_id}.json"
    path.write_text(json.dumps(build_report(job), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
