"""登录频率保护：记录最近一次登录时间，冷却期内拒绝再次登录。

用于避免短时间频繁登录导致账号被站点标记。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from config import Config


class LoginGuard:
    def __init__(self, cfg: Config, platform: str = "wsy", account: str = "") -> None:
        self.cfg = cfg
        self.platform = platform
        self.path: Path = cfg.base_dir / "data" / "login_state.json"
        self.cooldown = int(cfg.login.cooldown_minutes)
        digest = hashlib.sha1((account or "anonymous").encode("utf-8")).hexdigest()[:10]
        self.key = f"{platform}:{digest}"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def mark_login(self) -> None:
        data = self._load()
        data[self.key] = {
            "last_login_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save(data)

    def remaining_seconds(self) -> int:
        data = self._load().get(self.key) or {}
        raw = data.get("last_login_at")
        if not raw:
            return 0
        try:
            last = datetime.fromisoformat(str(raw))
        except ValueError:
            return 0
        deadline = last + timedelta(minutes=self.cooldown)
        return max(0, int((deadline - datetime.now()).total_seconds()))

    def can_login(self) -> bool:
        return self.remaining_seconds() <= 0

    def remaining_text(self) -> str:
        seconds = self.remaining_seconds()
        if seconds <= 0:
            return "0 分钟"
        minutes = seconds // 60 + (1 if seconds % 60 else 0)
        return f"{minutes} 分钟"
