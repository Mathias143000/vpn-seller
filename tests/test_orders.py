from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.db.models import OrderFulfillmentMode, OrderStatus
from app.services.exceptions import OutOfStockError
from app.services.hiddify import HiddifyAccessBundle, HiddifyAccessSource, HiddifyCountryOption, HiddifyServerOption
from tests.conftest import build_services, create_available_key, create_user, seed_default_plan


class FakeHiddifyService:
    def __init__(self, *, servers: list[HiddifyServerOption] | None = None) -> None:
        self.servers = servers or []

    async def has_active_servers(self, *, country_name: str | None = None) -> bool:
        return bool(self._filter_servers(country_name))

    async def list_available_servers(self) -> list[HiddifyServerOption]:
        return list(self.servers)

    async def list_available_countries(self) -> list[HiddifyCountryOption]:
        grouped: dict[str, HiddifyCountryOption] = {}
        for server in self.servers:
            existing = grouped.get(server.country_name)
            if existing is None:
                grouped[server.country_name] = HiddifyCountryOption(
                    representative_server_id=server.server_id,
                    country_name=server.country_name,
                    servers_count=1,
                )
            else:
                existing.servers_count += 1
        return [grouped[key] for key in sorted(grouped)]

    async def get_active_server_choice(self, server_id: int):
        for server in self.servers:
            if server.server_id == server_id:
                return SimpleNamespace(id=server_id, name=server.server_name, country_name=server.country_name, is_active=True)
        raise OutOfStockError("Selected Hiddify server is not available")

    async def provision_for_order(self, *, order_id: int, preferred_server_id: int | None = None, **kwargs):
        server = next(server for server in self.servers if server.server_id == preferred_server_id)
        source = HiddifyAccessSource(
            server_id=server.server_id,
            server_name=server.server_name,
            country_name=server.country_name,
            remote_user_uuid=f"remote-{order_id}-{server.server_id}",
            subscription_url=f"https://{server.server_name}.example/sub/{order_id}",
            panel_url=f"https://{server.server_name}.example/panel/{order_id}",
            deeplink_url=f"hiddify://import/https://{server.server_name}.example/sub/{order_id}",
        )
        return HiddifyAccessBundle(
            server_id=source.server_id,
            server_name=source.server_name,
            country_name=source.country_name,
            profile_name=f"profile-{order_id}",
            remote_user_uuid=source.remote_user_uuid,
            subscription_url=source.subscription_url,
            panel_url=source.panel_url,
            deeplink_url=source.deeplink_url,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
            included_countries=(source.country_name,),
            sources=(source,),
        )

    async def provision_superkey_for_order(self, *, order_id: int, **kwargs):
        sources = tuple(
            HiddifyAccessSource(
                server_id=server.server_id,
                server_name=server.server_name,
                country_name=server.country_name,
                remote_user_uuid=f"remote-{order_id}-{server.server_id}",
                subscription_url=f"https://{server.server_name}.example/sub/{order_id}",
                panel_url=f"https://{server.server_name}.example/panel/{order_id}",
                deeplink_url=f"hiddify://import/https://{server.server_name}.example/sub/{order_id}",
            )
            for server in self.servers
        )
        countries = tuple(server.country_name for server in self.servers)
        return HiddifyAccessBundle(
            server_id=None,
            server_name="Superkey Aggregator",
            country_name=", ".join(countries),
            profile_name=f"super-{order_id}",
            remote_user_uuid=f"super-{order_id}",
            subscription_url=f"https://bot.example/subscriptions/super-{order_id}",
            panel_url=None,
            deeplink_url=f"hiddify://import/https://bot.example/subscriptions/super-{order_id}",
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
            kind="superkey",
            included_countries=countries,
            sources=sources,
        )

    def _filter_servers(self, country_name: str | None) -> list[HiddifyServerOption]:
        if country_name is None:
            return list(self.servers)
        return [server for server in self.servers if server.country_name == country_name]


async def test_order_creation_reserves_key_and_creates_payment(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://key-1")

    order_id, payment_url = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)

    order = await services["orders_repo"].get_by_id(order_id)
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT.value
    assert order.reserved_key_id is not None
    assert order.fulfillment_mode == OrderFulfillmentMode.INVENTORY.value
    assert payment_url.startswith("/fake/payments/")


async def test_stock_check_blocks_payment_creation_without_keys(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)

    try:
        await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    except OutOfStockError:
        pass
    else:
        raise AssertionError("OutOfStockError expected")


async def test_order_creation_allows_explicit_hiddify_server_without_local_stock(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=888001)
    hiddify_service = FakeHiddifyService(
        servers=[
            HiddifyServerOption(server_id=7, server_name="nl-1", country_name="Netherlands"),
        ]
    )
    services = build_services(db, settings, fake_bot, hiddify_service=hiddify_service)

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
        preferred_hiddify_server_id=7,
    )

    order = await services["orders_repo"].get_by_id(order_id)
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT.value
    assert order.reserved_key_id is None
    assert order.fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SERVER.value
    assert order.preferred_hiddify_server_id == 7


async def test_order_creation_supports_superkey_mode(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=777001)
    hiddify_service = FakeHiddifyService(
        servers=[
            HiddifyServerOption(server_id=7, server_name="de-1", country_name="Germany"),
            HiddifyServerOption(server_id=8, server_name="nl-1", country_name="Netherlands"),
        ]
    )
    services = build_services(db, settings, fake_bot, hiddify_service=hiddify_service)

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
    )

    order = await services["orders_repo"].get_by_id(order_id)
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT.value
    assert order.reserved_key_id is None
    assert order.fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SUPERKEY.value
    assert order.preferred_hiddify_server_id is None


async def test_catalog_marks_plan_available_with_inventory_servers_and_superkey(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    services = build_services(
        db,
        settings,
        fake_bot,
        hiddify_service=FakeHiddifyService(
            servers=[
                HiddifyServerOption(server_id=1, server_name="de-1", country_name="Germany"),
                HiddifyServerOption(server_id=2, server_name="nl-1", country_name="Netherlands"),
            ]
        ),
    )
    await create_available_key(db, services["protector"], plan.id, "vpn://catalog-key")

    catalog = await services["plans"].get_catalog()

    assert catalog[0]["available_count"] == 1
    assert catalog[0]["inventory_available"] is True
    assert catalog[0]["is_available"] is True
    assert catalog[0]["superkey_available"] is True
    assert catalog[0]["hiddify_server_options"] == [
        {"server_id": 1, "server_name": "de-1", "country_name": "Germany"},
        {"server_id": 2, "server_name": "nl-1", "country_name": "Netherlands"},
    ]
