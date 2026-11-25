"""商品目录规划、meta.json 与 products.json 索引读写。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Config

INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def slugify(text: str, max_length: int = 40) -> str:
    cleaned = INVALID_CHARS.sub("_", (text or "").strip()).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "item")[:max_length]


class ProductStore:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.root: Path = cfg.download_root
        self.index_path: Path = cfg.base_dir / "data" / "products.json"

    # ---------- 目录 ----------
    def product_dir(self, shop_index: int, shop_name: str, product_index: int, title: str,
                    item_id: str) -> Path:
        shop_dir = self.root / f"{shop_index:02d}_{slugify(shop_name, 20)}"
        product_dir = shop_dir / f"{product_index:02d}_{slugify(title, 24)}_{item_id}"
        (product_dir / str(self.cfg.download.raw_subdir)).mkdir(parents=True, exist_ok=True)
        (product_dir / str(self.cfg.download.images_subdir)).mkdir(parents=True, exist_ok=True)
        return product_dir

    # ---------- 索引 ----------
    def load_all(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            with self.index_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict):
            data = data.get("products", [])
        return data if isinstance(data, list) else []

    def pending(self) -> list[dict[str, Any]]:
        return [
            p
            for p in self.load_all()
            if p.get("download_status") in ("SUCCESS", "PARTIAL")
            and p.get("publish_status") in (None, "", "PENDING")
            and p.get("images")
        ]

    def upsert(self, meta: dict[str, Any]) -> None:
        products = self.load_all()
        product_id = str(meta.get("product_id", ""))
        for index, item in enumerate(products):
            if str(item.get("product_id")) == product_id:
                products[index] = {**item, **meta}
                break
        else:
            products.append(meta)
        self._write(products)

    def _write(self, products: list[dict[str, Any]]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "products": products,
        }
        with self.index_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def write_meta(self, product_dir: Path, meta: dict[str, Any]) -> Path:
        path = product_dir / "meta.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        return path

    def update_publish_status(self, product_id: str, status: str, extra: dict[str, Any] | None = None) -> None:
        products = self.load_all()
        for item in products:
            if str(item.get("product_id")) == product_id:
                item["publish_status"] = status
                if extra:
                    item.update(extra)
                break
        else:
            return
        self._write(products)

    def set_publish_status(self, product_id: str, status: str) -> bool:
        """仅改发布状态（用于界面上手动重置/标记），不动其他字段。"""
        products = self.load_all()
        for item in products:
            if str(item.get("product_id")) == product_id:
                item["publish_status"] = status
                break
        else:
            return False
        self._write(products)
        return True

    def delete_product(self, product_id: str) -> tuple[bool, str]:
        """删除商品下载目录与清单记录，返回 (是否成功, 已删除目录或原因)。

        目录必须位于下载根目录内，避免误删外部路径；店铺计划缓存保留，
        下次下载时可复用商品链接重新抓取该商品。
        """
        products = self.load_all()
        target = next((i for i in products if str(i.get("product_id")) == product_id), None)
        if target is None:
            return False, "商品不存在"

        deleted_dir = ""
        dir_value = str(target.get("product_dir") or "")
        if dir_value:
            path = Path(dir_value).resolve()
            root = self.root.resolve()
            if root == path or root in path.parents:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                    deleted_dir = str(path)
                elif path.exists():
                    path.unlink()
                    deleted_dir = str(path)
            else:
                return False, "目录不在下载根目录内，已拒绝删除"

        remaining = [i for i in products if str(i.get("product_id")) != product_id]
        self._write(remaining)
        return True, deleted_dir
