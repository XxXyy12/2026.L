"""日志服务：内存环形缓冲（供界面增量拉取） + 文本日志 + JSONL 结构化日志 + 脱敏。

每条记录字段固定：seq/ts/level/module/step/status/product_id/message/
duration_ms/error/file_path/screenshot_path。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Config

_KEY_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|cookie|authorization|secret)\b\s*[:=]\s*([^\s,;]+)"
)
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")


class LogService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._seq = 0
        self._buffer: deque[dict[str, Any]] = deque(maxlen=int(cfg.logging.buffer_size))

        self._text_path = cfg.path_of(str(cfg.logging.file))
        self._jsonl_path = cfg.path_of(str(cfg.logging.structured_file))
        self._screenshot_dir = cfg.path_of(str(cfg.logging.screenshot_dir))
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)

        self._secret_values = [
            s.value
            for s in (
                cfg.wsy_username,
                cfg.wsy_password,
                cfg.xy_username,
                cfg.xy_password,
                cfg.deepseek_api_key,
            )
            if s.value
        ]

        self._logger = logging.getLogger("app")
        self._logger.setLevel(getattr(logging, str(cfg.logging.level).upper(), logging.INFO))
        self._logger.propagate = False
        if not self._logger.handlers:
            formatter = logging.Formatter("%(message)s")
            if bool(cfg.logging.console):
                console = logging.StreamHandler()
                console.setFormatter(formatter)
                self._logger.addHandler(console)
            file_handler = logging.FileHandler(self._text_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            self._logger.addHandler(file_handler)

    # ---------- 脱敏 ----------
    def mask(self, text: str) -> str:
        if not text:
            return ""
        masked = text
        for value in self._secret_values:
            if value and len(value) >= 3:
                masked = masked.replace(value, "***")
        masked = _KEY_RE.sub(lambda m: f"{m.group(1)}=***", masked)
        masked = _PHONE_RE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", masked)
        return masked

    # ---------- 写入 ----------
    def emit(
        self,
        message: str,
        *,
        level: str = "INFO",
        module: str = "app",
        step: str = "",
        status: str = "INFO",
        product_id: str = "",
        duration_ms: int | None = None,
        error: str = "",
        file_path: str = "",
        screenshot_path: str = "",
    ) -> dict[str, Any]:
        level = level.upper()
        record: dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "module": module,
            "step": step,
            "status": status,
            "product_id": product_id,
            "message": self.mask(message),
            "duration_ms": duration_ms,
            "error": self.mask(error),
            "file_path": self.mask(file_path),
            "screenshot_path": self.mask(screenshot_path),
        }
        with self._lock:
            self._seq += 1
            record["seq"] = self._seq
            self._buffer.append(record)

        line = (
            f"{record['ts']} [{level:<5}] {module}.{step or '-'} [{status}]"
            f" {('product=' + product_id) if product_id else ''} {record['message']}"
        )
        if duration_ms is not None:
            line += f" ({duration_ms}ms)"
        if record["error"]:
            line += f" error={record['error']}"
        if record["file_path"]:
            line += f" file={record['file_path']}"
        if record["screenshot_path"]:
            line += f" shot={record['screenshot_path']}"

        self._logger.log(getattr(logging, level, logging.INFO), line)
        try:
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:  # 日志文件不可写不应打断业务
            self._logger.warning("JSONL 日志写入失败: %s", exc)
        return record

    # ---------- 查询 ----------
    def latest_cursor(self) -> int:
        with self._lock:
            return self._seq

    def since(self, cursor: int, limit: int = 300) -> dict[str, Any]:
        with self._lock:
            items = [r for r in self._buffer if r["seq"] > cursor]
            items = items[-limit:]
            next_cursor = items[-1]["seq"] if items else self._seq
        return {"logs": items, "cursor": next_cursor}

    @property
    def screenshot_dir(self) -> Path:
        return self._screenshot_dir
