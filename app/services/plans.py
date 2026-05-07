from __future__ import annotations

from app.config import Settings
from app.db.models import PlanProvisioningMode
from app.repositories.plans import PlansRepository
from app.services.hiddify import HiddifyService
from app.services.pricing import PricingService


class PlansService:
    def __init__(
        self,
        plans_repo: PlansRepository,
        settings: Settings,
        hiddify: HiddifyService,
        pricing: PricingService,
    ) -> None:
        self._plans_repo = plans_repo
        self._settings = settings
        self._hiddify = hiddify
        self._pricing = pricing

    async def seed_defaults(self) -> None:
        await self._plans_repo.seed_defaults(
            low_stock_threshold=self._settings.default_low_stock_threshold,
            plan_overrides=self._pricing.load_plan_overrides(),
            apply_updates=self._settings.apply_plan_pricing_on_startup,
        )

    async def get_catalog(self) -> list[dict]:
        rows = await self._plans_repo.list_with_stock()
        countries = await self._hiddify.list_available_countries()
        servers = await self._hiddify.list_available_servers()
        has_hiddify = bool(servers)
        superkey_available = len(servers) >= 2
        return [
            {
                "id": row["plan"].id,
                "code": row["plan"].code,
                "name": row["plan"].name,
                "duration_days": row["plan"].duration_days,
                "price_value": row["plan"].price_value,
                "inventory_price_value": self._pricing.price_for_plan(row["plan"], "inventory"),
                "mtproxy_price_value": self._pricing.price_for_plan(row["plan"], "mtproxy"),
                "server_price_value": self._pricing.price_for_plan(row["plan"], "hiddify_server"),
                "superkey_price_value": self._pricing.price_for_plan(row["plan"], "hiddify_superkey"),
                "price_currency": row["plan"].price_currency,
                "description": row["plan"].description,
                "available_count": row["available_count"],
                "reserved_count": row["reserved_count"],
                "issued_count": row["issued_count"],
                "broken_count": row["broken_count"],
                "provisioning_mode": row["plan"].provisioning_mode,
                "inventory_available": row["available_count"] > 0
                and row["plan"].provisioning_mode != PlanProvisioningMode.MTPROXY.value,
                "mtproxy_available": has_hiddify
                if row["plan"].provisioning_mode == PlanProvisioningMode.MTPROXY.value
                else False,
                "mtproxy_server_count": len(servers)
                if row["plan"].provisioning_mode == PlanProvisioningMode.MTPROXY.value
                else 0,
                "hiddify_server_options": [
                    {
                        "server_id": option.server_id,
                        "server_name": option.server_name,
                        "country_name": option.country_name,
                    }
                    for option in servers
                    if row["plan"].provisioning_mode != PlanProvisioningMode.MTPROXY.value
                ],
                "hiddify_countries": [
                    {
                        "server_id": option.representative_server_id,
                        "country_name": option.country_name,
                        "servers_count": option.servers_count,
                    }
                    for option in countries
                ],
                "superkey_available": self._is_superkey_available(
                    provisioning_mode=row["plan"].provisioning_mode,
                    has_hiddify=has_hiddify,
                    superkey_available=superkey_available,
                ),
                "is_available": self._is_plan_available(
                    provisioning_mode=row["plan"].provisioning_mode,
                    available_count=row["available_count"],
                    has_hiddify=has_hiddify,
                ),
            }
            for row in rows
            if row["plan"].is_active
        ]

    @staticmethod
    def _is_plan_available(*, provisioning_mode: str, available_count: int, has_hiddify: bool) -> bool:
        if provisioning_mode == PlanProvisioningMode.INVENTORY.value:
            return available_count > 0
        if provisioning_mode == PlanProvisioningMode.MTPROXY.value:
            return has_hiddify
        if provisioning_mode == PlanProvisioningMode.HIDDIFY.value:
            return has_hiddify
        return available_count > 0 or has_hiddify

    @staticmethod
    def _is_superkey_available(*, provisioning_mode: str, has_hiddify: bool, superkey_available: bool) -> bool:
        if provisioning_mode in {PlanProvisioningMode.INVENTORY.value, PlanProvisioningMode.MTPROXY.value}:
            return False
        return has_hiddify and superkey_available
