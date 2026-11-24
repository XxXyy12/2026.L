"""价格规则：默认在原价基础上 +10%，最低加价 1 元，向上取到 0.1。"""

from __future__ import annotations

import math
from decimal import Decimal

from config import Config


class PriceRule:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.pricing = cfg.pricing

    def apply(self, original: float | None) -> float:
        if original is None or original <= 0:
            return float(self.pricing.min_price)

        base = Decimal(str(original))
        markup_type = str(self.pricing.markup_type)
        markup_value = Decimal(str(self.pricing.markup_value))
        if markup_type == "percent":
            price = base * (Decimal("1") + markup_value / Decimal("100"))
        else:
            price = base + markup_value

        price = max(price, base + Decimal(str(self.pricing.min_markup_amount)))
        price = max(price, Decimal(str(self.pricing.min_price)))

        mode = str(self.pricing.round_mode)
        if mode == "ceil_to_1":
            value = math.ceil(price)
        elif mode == "ceil_to_0_1":
            value = math.ceil(price * 10) / 10
        elif mode == "end_with_9":
            value = math.ceil(price) - 0.1
        else:
            value = float(price)
        return round(float(value), 2)
