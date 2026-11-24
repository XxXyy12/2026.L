"""DeepSeek 描述生成：闲鱼口语风格，失败时模板兜底（标记来源）。"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from config import Config
from services.log_service import LogService


class DescriptorService:
    def __init__(self, cfg: Config, logs: LogService) -> None:
        self.cfg = cfg
        self.logs = logs
        self.api_key = cfg.deepseek_api_key.value
        self.base_url = cfg.deepseek_base_url.rstrip("/")
        self.model = cfg.deepseek_model

    def generate(self, meta: dict[str, Any]) -> tuple[str, str]:
        """返回 (描述文本, 来源 deepseek|template)。"""
        limit = int(self.cfg.xianyu.desc_max_len)
        if bool(self.cfg.deepseek.enabled) and self.api_key:
            try:
                text = self._call_api(meta)
                if text:
                    return self._finalize(text, limit), "deepseek"
            except Exception as exc:  # noqa: BLE001 - 失败必须兜底
                self.logs.emit(
                    "DeepSeek 调用失败，使用模板兜底",
                    module="descriptor",
                    step="generate",
                    status="WARNING",
                    level="WARNING",
                    product_id=str(meta.get("product_id", "")),
                    error=f"{type(exc).__name__}: {exc}",
                )
        return self._finalize(self._template(meta), limit), "template"

    def _call_api(self, meta: dict[str, Any]) -> str:
        title = str(meta.get("title", ""))
        price = meta.get("price")
        style = "、".join(str(k) for k in self.cfg.deepseek.style_keywords)
        forbidden = "、".join(str(k) for k in self.cfg.deepseek.forbidden_keywords)
        prompt = (
            "你是闲鱼卖家，请为下面的服装写一段闲鱼风格的商品描述。\n"
            f"商品标题：{title}\n"
            f"原进货价：{price} 元\n"
            "要求：\n"
            f"1. 风格：{style}；口语化、真实感受、分点短句；\n"
            f"2. 全文不超过 {int(self.cfg.xianyu.desc_max_len)} 字；\n"
            "3. 不要出现微信/QQ/二维码/外站联系方式，不要使用绝对化极限词，不要虚构品牌；\n"
            "4. 不要写价格，不要写“全新未拆”等未经确认的信息；\n"
            "5. 直接输出描述正文，不要标题、不要解释。\n"
            f"禁止出现：{forbidden}。"
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(self.cfg.deepseek.temperature),
            "max_tokens": int(self.cfg.deepseek.max_tokens),
            "stream": False,
        }
        url = f"{self.base_url}/chat/completions"
        last_error: Exception | None = None
        for _ in range(max(1, int(self.cfg.deepseek.retry))):
            try:
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    data=json.dumps(payload),
                    timeout=float(self.cfg.deepseek.timeout_sec),
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return str(content).strip()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        assert last_error is not None
        raise last_error

    def _finalize(self, text: str, limit: int) -> str:
        cleaned = re.sub(r"\n{3,}", "\n\n", text or "").strip()
        for word in self.cfg.deepseek.forbidden_keywords:
            cleaned = cleaned.replace(str(word), "")
        return cleaned[:limit]

    def _template(self, meta: dict[str, Any]) -> str:
        template = str(self.cfg.deepseek.template)
        return template.format(
            title=str(meta.get("title", "")),
            fabric="实拍见图",
            fit="以实拍为准",
        )
