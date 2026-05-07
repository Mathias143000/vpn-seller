from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db.models import OrderFulfillmentMode, Plan

MONEY_QUANT = Decimal("0.01")


class PricingService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def price_for_plan(self, plan: Plan, fulfillment_mode: str | None = None) -> Decimal:
        amount = Decimal(str(plan.price_value))
        markup = Decimal("0")
        if fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SUPERKEY.value:
            markup = Decimal(str(self._settings.superkey_markup_percent))
        elif fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SERVER.value:
            markup = Decimal(str(self._settings.server_markup_percent))
        if markup:
            amount = amount * (Decimal("1") + markup / Decimal("100"))
        return quantize_money(max(amount, Decimal(str(self._settings.min_order_amount))))

    def load_plan_overrides(self) -> list[dict[str, Any]] | None:
        path = Path(self._settings.plan_pricing_file)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        plans = payload.get("plans")
        if not isinstance(plans, list):
            return None
        return plans


def quantize_money(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
