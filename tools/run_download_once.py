"""开发期：单店单商品跑通真实下载流程（不经过 Flask）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.browser import BrowserSession  # noqa: E402
from automation.wangshangyuan import WangshangyuanJob  # noqa: E402
from config import get_config  # noqa: E402
from core.control import ControlFlags  # noqa: E402
from core.state import JobRecord, JobState, RunType, now_iso  # noqa: E402
from services.log_service import LogService  # noqa: E402


def main() -> None:
    cfg = get_config()
    cfg.raw["wangshangyuan"]["shop_count"] = 1
    cfg.wangshangyuan._data["shop_count"] = 1  # type: ignore[attr-defined]
    cfg.raw["wangshangyuan"]["product_count"] = 1
    cfg.wangshangyuan._data["product_count"] = 1  # type: ignore[attr-defined]
    cfg.raw["job"]["max_product_total"] = 1
    cfg.job._data["max_product_total"] = 1  # type: ignore[attr-defined]

    logs = LogService(cfg)
    session = BrowserSession(cfg, logs)
    control = ControlFlags()
    job = JobRecord(
        run_id="dev-download",
        run_type=RunType.DOWNLOAD,
        state=JobState.RUNNING,
        simulated=False,
        started_at=now_iso(),
        total=1,
    )
    session.start()
    try:
        WangshangyuanJob(cfg, logs, control, session, job, None).run()
    finally:
        session.close(keep_open=False)
    print(f"RESULT state={job.state.value} success={job.success} failed={job.failed} skipped={job.skipped}")


if __name__ == "__main__":
    main()
