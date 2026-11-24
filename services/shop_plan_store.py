"""店铺商品计划缓存：避免每次运行都重新访问店铺列表页。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Config


class ShopPlanStore:
    def __init__(self, cfg: Config) -> None:
        self.path: Path = cfg.base_dir / "data" / "shop_plans.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, shop_url: str) -> dict[str, Any] | None:
        return self._load().get(shop_url)

    def put(self, shop_url: str, shop_name: str, products: list[dict[str, Any]]) -> None:
        data = self._load()
        data[shop_url] = {
            "shop_name": shop_name,
            "products": products,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
