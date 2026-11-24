"""配置加载与校验：config.yaml（非敏感） + .env（敏感）。

规则：
1. 敏感值只从环境变量/.env 读取，绝不出现在代码与日志中；
2. 启动时校验关键项，失败直接拒绝启动并给出明确错误；
3. 对外只暴露 public_view()，敏感值仅返回“是否已配置”。
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent

# 浏览器安装到项目内，避免写入系统目录（受限环境无法写 AppData）
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BASE_DIR / "data" / "ms-playwright"))


class ConfigError(Exception):
    """配置文件缺失或非法。"""


class Secret:
    """敏感值包装：repr/str 永远不泄露内容。"""

    __slots__ = ("_value",)

    def __init__(self, value: str | None = "") -> None:
        self._value = (value or "").strip()

    @property
    def value(self) -> str:
        return self._value

    @property
    def configured(self) -> bool:
        return bool(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(***)" if self._value else "Secret(unset)"

    __str__ = __repr__


class Section:
    """只读字典视图，支持 section.key 访问，嵌套自动包装。"""

    __slots__ = ("_data", "_name")

    def __init__(self, data: dict[str, Any], name: str) -> None:
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        try:
            value = self._data[item]
        except KeyError as exc:
            raise AttributeError(f"缺少配置项 {self._name}.{item}") from exc
        if isinstance(value, dict):
            return Section(value, f"{self._name}.{item}")
        return value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)


class Config:
    def __init__(self, yaml_path: Path | None = None, env_path: Path | None = None) -> None:
        self.base_dir = BASE_DIR
        env_file = env_path or (BASE_DIR / ".env")
        load_dotenv(env_file, override=False)

        path = yaml_path or (BASE_DIR / "config.yaml")
        if not path.exists():
            raise ConfigError(f"缺少配置文件：{path}")
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ConfigError("config.yaml 顶层必须是映射结构")

        self.raw: dict[str, Any] = raw
        self._lock = threading.Lock()
        for name, value in raw.items():
            if not isinstance(value, dict):
                raise ConfigError(f"config.yaml 中 {name} 必须是映射结构")
            setattr(self, name, Section(value, name))

        # 敏感项：只来自环境变量
        self.wsy_username = Secret(os.getenv("WSY_USERNAME"))
        self.wsy_password = Secret(os.getenv("WSY_PASSWORD"))
        # 备用账号：config.yaml 中 wangshangyuan.account_index=2 时启用
        account_index = int(raw.get("wangshangyuan", {}).get("account_index", 1) or 1)
        if account_index == 2:
            alt_user = os.getenv("WSY_USERNAME_2")
            alt_password = os.getenv("WSY_PASSWORD_2")
            if alt_user and alt_password:
                self.wsy_username = Secret(alt_user)
                self.wsy_password = Secret(alt_password)
        self.xy_username = Secret(os.getenv("XY_USERNAME"))
        self.xy_password = Secret(os.getenv("XY_PASSWORD"))
        self.deepseek_api_key = Secret(os.getenv("DEEPSEEK_API_KEY"))
        self.deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        self._validate()
        self.download_root = self._resolve(self.download.root_dir)
        self._ensure_dirs()

    # ---------- 校验与路径 ----------
    def _validate(self) -> None:
        t = self.timing
        if not (0.2 <= float(t.delay_min) < float(t.delay_max) <= 60):
            raise ConfigError("timing.delay_min/delay_max 需满足 0.2 <= min < max <= 60")
        if not (1 <= int(self.wangshangyuan.shop_count) <= 20):
            raise ConfigError("wangshangyuan.shop_count 取值 1~20")
        if not (1 <= int(self.wangshangyuan.product_count) <= 10):
            raise ConfigError("wangshangyuan.product_count 取值 1~10")
        if self.pricing.markup_type not in ("percent", "fixed"):
            raise ConfigError("pricing.markup_type 只能是 percent 或 fixed")
        if int(self.pricing.markup_value) < 0:
            raise ConfigError("pricing.markup_value 不能为负")
        if not (1 <= int(self.xianyu.max_images_upload) <= 20):
            raise ConfigError("xianyu.max_images_upload 取值 1~20")
        if int(self.xianyu.desc_max_len) <= 0 or int(self.xianyu.desc_max_len) > 5000:
            raise ConfigError("xianyu.desc_max_len 取值 1~5000")
        total = int(self.wangshangyuan.shop_count) * int(self.wangshangyuan.product_count)
        if int(self.job.max_product_total) > total:
            raise ConfigError("job.max_product_total 不得超过 店铺数×商品数")
        if self.mode.headless:
            # 允许启动，但明确警告：需求要求可见浏览器
            pass

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.base_dir / path)

    def _ensure_dirs(self) -> None:
        for directory in (
            self.download_root,
            self.base_dir / "data",
            self.base_dir / "data" / "reports",
            self.base_dir / "data" / "browser_state",
            (self.base_dir / self.logging.screenshot_dir),
            (self.base_dir / self.logging.file).parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # ---------- 运行时开关 ----------
    def set_test_mode(self, enabled: bool) -> None:
        with self._lock:
            self.raw["mode"]["test_mode"] = bool(enabled)
            self.mode._data["test_mode"] = bool(enabled)  # type: ignore[attr-defined]

    @property
    def test_mode(self) -> bool:
        return bool(self.mode.test_mode)

    def path_of(self, key: str) -> Path:
        """按 logging.file 之类的相对路径取绝对路径。"""
        return self._resolve(str(key))

    # ---------- 对外展示 ----------
    def public_view(self) -> dict[str, Any]:
        return {
            "test_mode": self.test_mode,
            "simulated": bool(self.mode.simulated),
            "headless": bool(self.mode.headless),
            "shop_count": int(self.wangshangyuan.shop_count),
            "product_count": int(self.wangshangyuan.product_count),
            "max_product_total": int(self.job.max_product_total),
            "delay_min": float(self.timing.delay_min),
            "delay_max": float(self.timing.delay_max),
            "pricing": {
                "markup_type": self.pricing.markup_type,
                "markup_value": self.pricing.markup_value,
                "min_markup_amount": self.pricing.min_markup_amount,
                "min_price": self.pricing.min_price,
                "round_mode": self.pricing.round_mode,
            },
            "xianyu": {
                "max_images_upload": int(self.xianyu.max_images_upload),
                "title_max_len": int(self.xianyu.title_max_len),
                "desc_max_len": int(self.xianyu.desc_max_len),
                "title_source": self.xianyu.title_source,
                "spec_mode": self.xianyu.spec_mode,
                "location_pick": self.xianyu.location_pick,
            },
            "download_root": str(self.download_root),
            "credentials": {
                "wangshangyuan": self.wsy_username.configured and self.wsy_password.configured,
                "xianyu": self.xy_username.configured and self.xy_password.configured,
                "deepseek": self.deepseek_api_key.configured,
            },
        }

    def __repr__(self) -> str:
        return f"Config(base_dir={self.base_dir}, test_mode={self.test_mode})"


_config: Config | None = None
_config_lock = threading.Lock()


def get_config() -> Config:
    global _config
    with _config_lock:
        if _config is None:
            _config = Config()
        return _config
