from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func, select

from app.db.models import KeyStatus, Plan, PlanProvisioningMode, VPNKey
from app.repositories.base import BaseRepository


DEFAULT_PLANS = [
    {
        "code": "plan_30",
        "name": "30 дней",
        "duration_days": 30,
        "price_value": Decimal("299.00"),
        "price_currency": "RUB",
        "description": "Базовый тариф на 30 дней.",
    },
    {
        "code": "plan_90",
        "name": "90 дней",
        "duration_days": 90,
        "price_value": Decimal("799.00"),
        "price_currency": "RUB",
        "description": "Оптимальный тариф на 90 дней.",
    },
    {
        "code": "plan_180",
        "name": "180 дней",
        "duration_days": 180,
        "price_value": Decimal("1499.00"),
        "price_currency": "RUB",
        "description": "Полугодовой тариф с лучшей ценой за день.",
    },
    {
        "code": "plan_365",
        "name": "365 дней",
        "duration_days": 365,
        "price_value": Decimal("2799.00"),
        "price_currency": "RUB",
        "description": "Годовой тариф для постоянного использования.",
    },
    {
        "code": "mtproxy_30",
        "name": "MTProxy 30 дней",
        "duration_days": 30,
        "price_value": Decimal("99.00"),
        "price_currency": "RUB",
        "description": "Недорогой MTProxy-доступ из подготовленной базы серверов.",
        "provisioning_mode": PlanProvisioningMode.MTPROXY.value,
    },
]


class PlansRepository(BaseRepository):
    async def list_active(self) -> list[Plan]:
        result = await self.session.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.duration_days))
        return list(result)

    async def get_by_id(self, plan_id: int) -> Plan | None:
        return await self.session.get(Plan, plan_id)

    async def get_by_code(self, code: str) -> Plan | None:
        return await self.session.scalar(select(Plan).where(Plan.code == code))

    async def seed_defaults(
        self,
        low_stock_threshold: int,
        plan_overrides: list[dict] | None = None,
        apply_updates: bool = False,
    ) -> None:
        source_plans = self._merge_plan_overrides(plan_overrides)
        existing_by_code = {plan.code: plan for plan in await self.list_active()}
        for item in source_plans:
            existing = existing_by_code.get(item["code"])
            if existing is not None:
                if apply_updates:
                    existing.name = item["name"]
                    existing.duration_days = item["duration_days"]
                    existing.price_value = item["price_value"]
                    existing.price_currency = item["price_currency"]
                    existing.description = item.get("description")
                    existing.provisioning_mode = item.get("provisioning_mode", PlanProvisioningMode.AUTO.value)
                continue
            self.session.add(
                Plan(
                    **item,
                    low_stock_threshold=low_stock_threshold,
                )
            )
        await self.session.flush()

    @staticmethod
    def _merge_plan_overrides(plan_overrides: list[dict] | None) -> list[dict]:
        if not plan_overrides:
            return DEFAULT_PLANS
        by_code = {item["code"]: dict(item) for item in DEFAULT_PLANS}
        for override in plan_overrides:
            code = str(override.get("code", "")).strip()
            if not code:
                continue
            base = by_code.get(code, {"code": code})
            merged = {**base, **override}
            if "price_value" in merged:
                merged["price_value"] = Decimal(str(merged["price_value"]))
            if "duration_days" in merged:
                merged["duration_days"] = int(merged["duration_days"])
            merged.setdefault("price_currency", "RUB")
            merged.setdefault("description", "")
            merged.setdefault("name", code)
            merged.setdefault("provisioning_mode", PlanProvisioningMode.AUTO.value)
            by_code[code] = merged
        return list(by_code.values())

    async def list_with_stock(self) -> list[dict]:
        query = (
            select(
                Plan,
                func.sum(case((VPNKey.status == KeyStatus.AVAILABLE.value, 1), else_=0)).label("available_count"),
                func.sum(case((VPNKey.status == KeyStatus.RESERVED.value, 1), else_=0)).label("reserved_count"),
                func.sum(case((VPNKey.status == KeyStatus.ISSUED.value, 1), else_=0)).label("issued_count"),
                func.sum(case((VPNKey.status == KeyStatus.BROKEN.value, 1), else_=0)).label("broken_count"),
            )
            .outerjoin(VPNKey, VPNKey.plan_id == Plan.id)
            .group_by(Plan.id)
            .order_by(Plan.duration_days)
        )
        rows = await self.session.execute(query)
        return [
            {
                "plan": plan,
                "available_count": int(available_count or 0),
                "reserved_count": int(reserved_count or 0),
                "issued_count": int(issued_count or 0),
                "broken_count": int(broken_count or 0),
            }
            for plan, available_count, reserved_count, issued_count, broken_count in rows.all()
        ]
