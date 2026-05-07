from __future__ import annotations

import asyncio
from dataclasses import asdict
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.models import AuditLog, KeyStatus, Order, OrderFulfillmentMode, OrderStatus
from app.db.session import init_models
from app.services.exceptions import ProvisioningError
from app.services.hiddify import HiddifyAccessBundle, HiddifyAccessSource, HiddifyCountryOption, HiddifyServerOption
from app.services.payments.base import PaymentProvider
from app.services.payments.donate_stream import DonateStreamPaymentProvider
from tests.conftest import build_services, create_available_key, create_hiddify_server, create_user, seed_default_plan


async def test_successful_payment_webhook_issues_key_and_queues_delivery(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://issued-1")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)

    result = await services["payments"].handle_webhook(
        {
            "provider_payment_id": order.provider_payment_id,
            "order_id": order.id,
            "amount_value": str(order.amount_value),
            "amount_currency": order.amount_currency,
            "status": "succeeded",
        }
    )

    assert result["duplicate"] is False
    refreshed = await services["orders_repo"].get_by_id(order_id)
    assert refreshed.status == OrderStatus.ISSUED.value
    assert refreshed.issued_key_id is not None
    jobs = await services["delivery_jobs_repo"].claim_due_jobs(now=datetime.now(tz=timezone.utc))
    assert len(jobs) == 1


async def test_payment_webhook_accepts_equivalent_decimal_amount(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://issued-decimal-equivalent")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)

    amount_without_cents = str(int(order.amount_value))
    result = await services["payments"].handle_webhook(
        {
            "provider_payment_id": order.provider_payment_id,
            "order_id": order.id,
            "amount_value": amount_without_cents,
            "amount_currency": order.amount_currency,
            "status": "succeeded",
        }
    )

    refreshed = await services["orders_repo"].get_by_id(order_id)
    assert result["duplicate"] is False
    assert refreshed.status == OrderStatus.ISSUED.value


async def test_repeated_payment_webhook_is_idempotent(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://issued-2")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)
    payload = {
        "provider_payment_id": order.provider_payment_id,
        "order_id": order.id,
        "amount_value": str(order.amount_value),
        "amount_currency": order.amount_currency,
        "status": "succeeded",
        "provider_event_id": "evt-1",
    }

    first = await services["payments"].handle_webhook(payload)
    second = await services["payments"].handle_webhook(payload)

    assert first["duplicate"] is False
    assert second["duplicate"] is True


async def test_manual_donate_stream_confirmation_issues_inventory_key_and_sends_delivery(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=424242)
    donate_settings = Settings(**{**settings.model_dump(), "payment_provider": "donate_stream"})
    services = build_services(
        db,
        donate_settings,
        fake_bot,
        payment_provider=DonateStreamPaymentProvider(donate_settings),
    )
    await create_available_key(db, services["protector"], plan.id, "vpn://manual-confirm")

    order_id, payment_url = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)

    assert payment_url == donate_settings.donate_stream_url
    assert any("ручной проверки оплаты" in text for _, text in fake_bot.messages)

    order_status, issued_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    delivered = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order_id)
    assert order_status == OrderStatus.ISSUED.value
    assert issued_key_id is not None
    assert refreshed.status == OrderStatus.ISSUED.value
    assert delivered == 1
    assert any(chat_id == user.telegram_user_id and "твой ключ" in text.lower() for chat_id, text in fake_bot.messages)
    assert any(doc["chat_id"] == user.telegram_user_id for doc in fake_bot.documents)


async def test_donate_stream_url_can_be_configured_from_admin_settings(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=424244)
    donate_settings = Settings(
        **{
            **settings.model_dump(),
            "payment_provider": "donate_stream",
            "donate_stream_url": "",
        }
    )
    services = build_services(
        db,
        donate_settings,
        fake_bot,
        payment_provider=DonateStreamPaymentProvider(donate_settings),
    )
    await services["shop_settings"].set_value(
        key="donate_stream_url",
        value="https://donate.example/pay",
        actor_user_id=None,
    )
    await create_available_key(db, services["protector"], plan.id, "vpn://admin-configured-donate-url")

    _, payment_url = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)

    assert payment_url == "https://donate.example/pay"


async def test_payment_issues_mtproxy_on_least_loaded_active_server(db, settings, fake_bot, monkeypatch):
    services = build_services(db, settings, fake_bot)
    await services["plans"].seed_defaults()
    await db.commit()
    plan = await services["plans_repo"].get_by_code("mtproxy_30")
    user = await create_user(db, telegram_user_id=424243)
    busy_server = await create_hiddify_server(
        db,
        services["protector"],
        name="busy",
        country_name="Germany",
        base_url="https://busy.example.com",
        admin_proxy_path="admin-busy",
        client_proxy_path="client-busy",
    )
    quiet_server = await create_hiddify_server(
        db,
        services["protector"],
        name="quiet",
        country_name="Netherlands",
        base_url="https://quiet.example.com",
        admin_proxy_path="admin-quiet",
        client_proxy_path="client-quiet",
    )
    hiddify_service = services["hiddify"]

    async def fake_list_remote_users(**kwargs):
        server = kwargs["server"]
        if server.id == busy_server.id:
            return [{"enable": True}, {"enable": True}, {"enable": True}]
        if server.id == quiet_server.id:
            return [{"enable": True}]
        return []

    async def fake_ensure_remote_user(**kwargs):
        return {"uuid": f"remote-{kwargs['server'].id}", "name": "mtproxy-test"}

    async def fake_fetch_mtproxy_links(**kwargs):
        return [f"tg://proxy?server={kwargs['server'].name}.example.com&port=443&secret=abcdef"]

    monkeypatch.setattr(hiddify_service, "_list_remote_users", fake_list_remote_users)
    monkeypatch.setattr(hiddify_service, "_ensure_remote_user", fake_ensure_remote_user)
    monkeypatch.setattr(hiddify_service, "_fetch_mtproxy_links", fake_fetch_mtproxy_links)

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.MTPROXY.value,
    )
    order = await services["orders_repo"].get_by_id(order_id)

    await services["payments"].handle_webhook(
        {
            "provider_payment_id": order.provider_payment_id,
            "order_id": order.id,
            "amount_value": str(order.amount_value),
            "amount_currency": order.amount_currency,
            "status": "succeeded",
        }
    )
    delivered = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order_id)
    issued_key = await services["vpn_keys_repo"].get_by_id(refreshed.issued_key_id)
    assert refreshed.status == OrderStatus.ISSUED.value
    assert refreshed.preferred_hiddify_server_id == quiet_server.id
    assert issued_key.external_ref.startswith(f"mtproxy:{quiet_server.id}:")
    assert delivered == 1
    assert any(chat_id == user.telegram_user_id and "tg://proxy" in text for chat_id, text in fake_bot.messages)
    assert not any(doc["chat_id"] == user.telegram_user_id for doc in fake_bot.documents)


async def test_admin_replacement_reissues_mtproxy_on_alternate_server(db, settings, fake_bot, monkeypatch):
    services = build_services(db, settings, fake_bot)
    await services["plans"].seed_defaults()
    await db.commit()
    plan = await services["plans_repo"].get_by_code("mtproxy_30")
    user = await create_user(db, telegram_user_id=424244)
    busy_server = await create_hiddify_server(
        db,
        services["protector"],
        name="busy",
        country_name="Germany",
        base_url="https://busy-replace.example.com",
        admin_proxy_path="admin-busy-replace",
        client_proxy_path="client-busy-replace",
    )
    quiet_server = await create_hiddify_server(
        db,
        services["protector"],
        name="quiet",
        country_name="Netherlands",
        base_url="https://quiet-replace.example.com",
        admin_proxy_path="admin-quiet-replace",
        client_proxy_path="client-quiet-replace",
    )
    hiddify_service = services["hiddify"]

    async def fake_list_remote_users(**kwargs):
        server = kwargs["server"]
        if server.id == busy_server.id:
            return [{"enable": True}, {"enable": True}, {"enable": True}]
        if server.id == quiet_server.id:
            return [{"enable": True}]
        return []

    async def fake_ensure_remote_user(**kwargs):
        return {
            "uuid": f"remote-{kwargs['server'].id}-{kwargs['order_hint']}",
            "name": f"mtproxy-{kwargs['server'].name}",
        }

    async def fake_fetch_mtproxy_links(**kwargs):
        return [
            "tg://proxy?"
            f"server={kwargs['server'].name}.example.com&port=443&secret={kwargs['remote_user_uuid']}"
        ]

    monkeypatch.setattr(hiddify_service, "_list_remote_users", fake_list_remote_users)
    monkeypatch.setattr(hiddify_service, "_ensure_remote_user", fake_ensure_remote_user)
    monkeypatch.setattr(hiddify_service, "_fetch_mtproxy_links", fake_fetch_mtproxy_links)

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.MTPROXY.value,
    )
    order = await services["orders_repo"].get_by_id(order_id)
    await services["payments"].handle_webhook(
        {
            "provider_payment_id": order.provider_payment_id,
            "order_id": order.id,
            "amount_value": str(order.amount_value),
            "amount_currency": order.amount_currency,
            "status": "succeeded",
        }
    )
    await services["delivery"].process_pending_jobs()

    issued_order = await services["orders_repo"].get_by_id(order_id)
    old_key_id = issued_order.issued_key_id
    old_key = await services["vpn_keys_repo"].get_by_id(old_key_id)
    assert issued_order.preferred_hiddify_server_id == quiet_server.id
    assert old_key.external_ref.startswith(f"mtproxy:{quiet_server.id}:")

    new_key_id = await services["issuing"].replace_access_for_issued_order(order_id=order_id, actor_user_id=999001)
    delivered = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order_id)
    old_key = await services["vpn_keys_repo"].get_by_id(old_key_id)
    new_key = await services["vpn_keys_repo"].get_by_id(new_key_id)
    audit_log = await db.scalar(select(AuditLog).where(AuditLog.action == "mtproxy_replacement_issued"))

    assert delivered == 1
    assert refreshed.status == OrderStatus.ISSUED.value
    assert refreshed.issued_key_id == new_key_id
    assert refreshed.preferred_hiddify_server_id == busy_server.id
    assert old_key.status == KeyStatus.BROKEN.value
    assert new_key.external_ref.startswith(f"mtproxy:{busy_server.id}:")
    assert audit_log.actor_user_id == 999001
    assert audit_log.payload_json["old_vpn_key_id"] == old_key_id
    assert any("busy.example.com" in text for _, text in fake_bot.messages)


class SuccessProvider(PaymentProvider):
    provider_name = "fake"

    async def create_payment(self, *, order_id, amount_value, amount_currency, description, payment_url=None):
        raise NotImplementedError

    async def parse_webhook(self, payload: dict):
        raise NotImplementedError

    async def get_payment_status(self, provider_payment_id: str) -> str:
        return "succeeded"


class FakeHiddifyService:
    def __init__(self, *, servers: list[HiddifyServerOption] | None = None, should_fail: bool = False) -> None:
        self.servers = servers or []
        self.should_fail = should_fail

    async def has_active_servers(self, *, country_name: str | None = None) -> bool:
        if country_name is None:
            return bool(self.servers)
        return any(server.country_name == country_name for server in self.servers)

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
                return type("ChosenServer", (), {"id": server_id, "name": server.server_name, "country_name": server.country_name, "is_active": True})()
        raise ProvisioningError("Selected server is unavailable")

    async def provision_for_order(self, *, order_id: int, preferred_server_id: int | None = None, **kwargs):
        if self.should_fail and preferred_server_id is not None:
            raise ProvisioningError("Selected server is temporarily unavailable")
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
        if self.should_fail:
            raise ProvisioningError("Superkey provisioning failed")
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

    def serialize_access_payload(self, bundle: HiddifyAccessBundle) -> str:
        import json

        return json.dumps(
            {
                "delivery_kind": "hiddify_superkey" if bundle.kind == "superkey" else "hiddify",
                "profile_name": bundle.profile_name,
                "subscription_url": bundle.subscription_url,
                "panel_url": bundle.panel_url,
                "deeplink_url": bundle.deeplink_url,
                "included_countries": list(bundle.included_countries),
                "sources": [asdict(source) for source in bundle.sources],
            }
        )


async def test_reconciliation_recovers_paid_but_not_issued(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://reconcile-1")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)
    order.status = OrderStatus.PAID_BUT_NOT_ISSUED.value
    await db.commit()
    services = build_services(db, settings, fake_bot, payment_provider=SuccessProvider())

    processed = await services["payments"].reconcile()

    refreshed = await services["orders_repo"].get_by_id(order_id)
    assert processed == 1
    assert refreshed.status == OrderStatus.ISSUED.value


async def test_manual_confirmation_can_issue_hiddify_access(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=987654)
    services = build_services(
        db,
        settings,
        fake_bot,
        hiddify_service=FakeHiddifyService(
            servers=[HiddifyServerOption(server_id=11, server_name="de-1", country_name="Germany")]
        ),
    )

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
        preferred_hiddify_server_id=11,
    )

    order_status, issued_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    delivered = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order_id)
    issued_key = await services["vpn_keys_repo"].get_by_id(issued_key_id)

    assert order_status == OrderStatus.ISSUED.value
    assert refreshed.status == OrderStatus.ISSUED.value
    assert issued_key_id is not None
    assert issued_key is not None
    assert issued_key.external_ref.startswith("hiddify:")
    assert delivered == 1
    assert any(
        chat_id == user.telegram_user_id and "Ссылка-подписка" in text and "суперключ" not in text.lower()
        for chat_id, text in fake_bot.messages
    )


async def test_manual_confirmation_can_issue_superkey_access(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=654987)
    services = build_services(
        db,
        settings,
        fake_bot,
        hiddify_service=FakeHiddifyService(
            servers=[
                HiddifyServerOption(server_id=11, server_name="de-1", country_name="Germany"),
                HiddifyServerOption(server_id=12, server_name="nl-1", country_name="Netherlands"),
            ]
        ),
    )

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
    )

    order_status, issued_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    delivered = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order_id)
    issued_key = await services["vpn_keys_repo"].get_by_id(issued_key_id)

    assert order_status == OrderStatus.ISSUED.value
    assert refreshed.status == OrderStatus.ISSUED.value
    assert issued_key is not None
    assert issued_key.external_ref.startswith("hiddify-superkey:")
    assert delivered == 1
    assert any(
        chat_id == user.telegram_user_id and "суперключ" in text.lower() and "Страны внутри" in text
        for chat_id, text in fake_bot.messages
    )


async def test_selected_hiddify_server_does_not_fallback_to_inventory(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=765432)
    services = build_services(
        db,
        settings,
        fake_bot,
        hiddify_service=FakeHiddifyService(
            servers=[HiddifyServerOption(server_id=11, server_name="nl-1", country_name="Netherlands")],
            should_fail=True,
        ),
    )
    await create_available_key(db, services["protector"], plan.id, "vpn://inventory-fallback-source")

    order_id, _ = await services["orders"].create_order_with_payment(
        user_id=user.id,
        plan_id=plan.id,
        requested_fulfillment_mode=OrderFulfillmentMode.HIDDIFY_SERVER.value,
        preferred_hiddify_server_id=11,
    )

    order_status, issued_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    refreshed = await services["orders_repo"].get_by_id(order_id)

    assert order_status == OrderStatus.PAID_BUT_NOT_ISSUED.value
    assert refreshed.status == OrderStatus.PAID_BUT_NOT_ISSUED.value
    assert issued_key_id is None
    assert refreshed.issued_key_id is None
    assert await services["vpn_keys_repo"].count_available(plan.id) == 1


async def test_repeated_manual_confirmation_is_idempotent(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=432123)
    donate_settings = Settings(**{**settings.model_dump(), "payment_provider": "donate_stream"})
    services = build_services(
        db,
        donate_settings,
        fake_bot,
        payment_provider=DonateStreamPaymentProvider(donate_settings),
    )
    await create_available_key(db, services["protector"], plan.id, "vpn://manual-idempotent")

    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    first_status, first_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    first_delivered = await services["delivery"].process_pending_jobs()
    second_status, second_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    second_delivered = await services["delivery"].process_pending_jobs()

    assert first_status == OrderStatus.ISSUED.value
    assert second_status == OrderStatus.ISSUED.value
    assert second_key_id == first_key_id
    assert first_delivered == 1
    assert second_delivered == 0


async def test_manual_donate_stream_reconciliation_recovers_paid_but_not_issued(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=543210)
    donate_settings = Settings(**{**settings.model_dump(), "payment_provider": "donate_stream"})
    services = build_services(
        db,
        donate_settings,
        fake_bot,
        payment_provider=DonateStreamPaymentProvider(donate_settings),
    )

    await create_available_key(db, services["protector"], plan.id, "vpn://manual-reconcile-source")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)
    reserved_key = await services["vpn_keys_repo"].get_by_id(order.reserved_key_id)
    reserved_key.status = "broken"
    await db.commit()
    order_status, _ = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    refreshed = await services["orders_repo"].get_by_id(order_id)

    assert order_status == OrderStatus.PAID_BUT_NOT_ISSUED.value
    assert refreshed.status == OrderStatus.PAID_BUT_NOT_ISSUED.value

    await create_available_key(db, services["protector"], plan.id, "vpn://recovered-after-manual")

    processed = await services["payments"].reconcile()

    recovered = await services["orders_repo"].get_by_id(order_id)
    assert processed == 1
    assert recovered.status == OrderStatus.ISSUED.value


@pytest.mark.asyncio
async def test_atomic_key_issuing_under_concurrency_postgres(settings, fake_bot):
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is not configured")

    pg_settings = Settings(**{**settings.model_dump(), "database_url": dsn})
    engine = create_async_engine(pg_settings.database_url, future=True)
    await init_models(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            services = build_services(session, pg_settings, fake_bot)
            plan = await seed_default_plan(session, pg_settings)
            user_one = await create_user(session, telegram_user_id=5001)
            user_two = await create_user(session, telegram_user_id=5002)
            await create_available_key(session, services["protector"], plan.id, "vpn://race")
            first_order = Order(
                user_id=user_one.id,
                plan_id=plan.id,
                status=OrderStatus.PAID.value,
                amount_value=plan.price_value,
                amount_currency=plan.price_currency,
                payment_provider="fake",
                fulfillment_mode=OrderFulfillmentMode.INVENTORY.value,
            )
            second_order = Order(
                user_id=user_two.id,
                plan_id=plan.id,
                status=OrderStatus.PAID.value,
                amount_value=plan.price_value,
                amount_currency=plan.price_currency,
                payment_provider="fake",
                fulfillment_mode=OrderFulfillmentMode.INVENTORY.value,
            )
            session.add_all([first_order, second_order])
            await session.commit()

        async def issue_one(order_id: int) -> str:
            async with factory() as local_session:
                local_services = build_services(local_session, pg_settings, fake_bot)
                status, _ = await local_services["issuing"].issue_key_for_paid_order(order_id)
                return status

        statuses = await asyncio.gather(issue_one(first_order.id), issue_one(second_order.id))
        assert sorted(statuses) == sorted([OrderStatus.ISSUED.value, OrderStatus.PAID_BUT_NOT_ISSUED.value])
    finally:
        await engine.dispose()
