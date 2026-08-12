from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.models import HiddifyServer, KeyStatus, Order, OrderStatus, PaymentStatus, Plan, User, UserRole, VPNKey
from app.db.session import init_models
from app.runtime import configure_runtime
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.delivery_jobs import DeliveryJobsRepository
from app.repositories.hiddify_servers import HiddifyServersRepository
from app.repositories.hiddify_usage_snapshots import HiddifyUsageSnapshotsRepository
from app.repositories.import_batches import ImportBatchesRepository
from app.repositories.orders import OrdersRepository
from app.repositories.payment_events import PaymentEventsRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.promo_codes import PromoCodesRepository
from app.repositories.shop_settings import ShopSettingsRepository
from app.repositories.users import UsersRepository
from app.repositories.vpn_keys import VPNKeysRepository
from app.services.delivery import DeliveryService
from app.services.communications import CommunicationsService
from app.services.content import ContentService
from app.services.hiddify import HiddifyService
from app.services.hiddify_usage import HiddifyUsageMonitorService
from app.services.imports.xlsx_export import XlsxExportService
from app.services.imports.hiddify_xlsx_import import HiddifyXlsxImportService
from app.services.imports.xlsx_import import XlsxImportService
from app.services.imports.sqlite_import import SqliteImportService
from app.services.inventory import InventoryService
from app.services.issuing import IssuingService
from app.services.notifications import NotificationService
from app.services.orders import OrdersService
from app.services.payments.donate_stream import DonateStreamPaymentProvider
from app.services.payments.fake import FakePaymentProvider
from app.services.payments.service import PaymentService
from app.services.plans import PlansService
from app.services.pricing import PricingService
from app.services.promos import PromoService
from app.services.security import KeyProtector
from app.services.shop_settings import ShopSettingsService
from app.services.subscriptions import SubscriptionAggregatorService
from app.services.users import UsersService

configure_runtime()


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.documents: list[dict] = []
        self.calls: list[dict] = []
        self.failures_remaining = 0

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("Telegram unavailable")
        self.messages.append((chat_id, text))
        self.calls.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

    async def send_document(self, chat_id: int, document, **kwargs) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("Telegram unavailable")
        entry = {"chat_id": chat_id, "document": document, "kwargs": kwargs}
        self.documents.append(entry)
        self.calls.append(entry)


class FakeVkClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.profiles: dict[int, dict] = {}
        self.failures_remaining = 0

    async def send_message(self, *, peer_id: int, message: str, keyboard=None, disable_mentions: bool = True) -> int:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("VK unavailable")
        self.messages.append(
            {
                "peer_id": peer_id,
                "message": message,
                "keyboard": keyboard,
                "disable_mentions": disable_mentions,
            }
        )
        return len(self.messages)

    async def get_user_profile(self, user_id: int) -> dict | None:
        return self.profiles.get(user_id)


class FakeWhatsAppClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.failures_remaining = 0

    async def send_text_message(self, *, to: str, body: str, preview_url: bool = False) -> str:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("WhatsApp unavailable")
        self.messages.append({"type": "text", "to": to, "body": body, "preview_url": preview_url})
        return f"wamid.{len(self.messages)}"

    async def send_buttons(self, *, to: str, body: str, buttons: list[dict]) -> str:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("WhatsApp unavailable")
        self.messages.append({"type": "buttons", "to": to, "body": body, "buttons": buttons})
        return f"wamid.{len(self.messages)}"

    async def send_list(self, *, to: str, body: str, button_text: str, sections: list[dict], header_text: str | None = None) -> str:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("WhatsApp unavailable")
        self.messages.append(
            {
                "type": "list",
                "to": to,
                "body": body,
                "button_text": button_text,
                "sections": sections,
                "header_text": header_text,
            }
        )
        return f"wamid.{len(self.messages)}"

    def verify_signature(self, body: bytes, signature_header: str | None) -> bool:
        return True


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        app_mode="web",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        bot_token="123456:TEST",
        encryption_key="test-secret",
        admin_ids="999001,999002",
        payment_provider="fake",
        delivery_retry_seconds=0,
        delivery_max_attempts=2,
        payment_stale_pending_minutes=1,
        vk_group_id=1,
        vk_group_token="vk-test-token",
        vk_confirmation_token="vk-confirm-token",
        vk_callback_secret="vk-secret",
        whatsapp_phone_number_id="1234567890",
        whatsapp_access_token="wa-test-token",
        whatsapp_verify_token="wa-verify-token",
        whatsapp_app_secret="wa-app-secret",
        content_file=str(tmp_path / "messages.json"),
        plan_pricing_file=str(tmp_path / "pricing.json"),
    )


@pytest.fixture()
async def session_factory(settings: Settings):
    engine = create_async_engine(settings.database_url, future=True)
    await init_models(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def build_services(
    session: AsyncSession,
    settings: Settings,
    fake_bot: FakeBot,
    payment_provider=None,
    hiddify_service=None,
    fake_vk: FakeVkClient | None = None,
    fake_whatsapp: FakeWhatsAppClient | None = None,
):
    users_repo = UsersRepository(session)
    plans_repo = PlansRepository(session)
    orders_repo = OrdersRepository(session)
    vpn_keys_repo = VPNKeysRepository(session)
    payments_repo = PaymentsRepository(session)
    payment_events_repo = PaymentEventsRepository(session)
    delivery_jobs_repo = DeliveryJobsRepository(session)
    hiddify_servers_repo = HiddifyServersRepository(session)
    hiddify_usage_snapshots_repo = HiddifyUsageSnapshotsRepository(session)
    import_batches_repo = ImportBatchesRepository(session)
    audit_logs_repo = AuditLogsRepository(session)
    promo_codes_repo = PromoCodesRepository(session)
    shop_settings_repo = ShopSettingsRepository(session)

    users_service = UsersService(users_repo, settings.parsed_admin_ids)
    key_protector = KeyProtector(settings.encryption_key.get_secret_value())
    content_service = ContentService(settings)
    pricing_service = PricingService(settings)
    hiddify_service = hiddify_service or HiddifyService(
        session=session,
        settings=settings,
        hiddify_servers_repo=hiddify_servers_repo,
        usage_snapshots_repo=hiddify_usage_snapshots_repo,
        audit_logs_repo=audit_logs_repo,
        key_protector=key_protector,
    )
    plans_service = PlansService(plans_repo, settings, hiddify_service, pricing_service)
    notification_service = NotificationService(fake_bot, settings, fake_vk, fake_whatsapp, content_service)
    hiddify_usage_service = HiddifyUsageMonitorService(
        session=session,
        settings=settings,
        hiddify=hiddify_service,
        hiddify_servers_repo=hiddify_servers_repo,
        usage_snapshots_repo=hiddify_usage_snapshots_repo,
        audit_logs_repo=audit_logs_repo,
        notification_service=notification_service,
    )
    promo_service = PromoService(
        session=session,
        promo_codes_repo=promo_codes_repo,
        users_repo=users_repo,
        audit_logs_repo=audit_logs_repo,
        min_order_amount=settings.min_order_amount,
    )
    shop_settings_service = ShopSettingsService(
        settings=settings,
        shop_settings_repo=shop_settings_repo,
        audit_logs_repo=audit_logs_repo,
    )
    issuing_service = IssuingService(
        session=session,
        orders_repo=orders_repo,
        vpn_keys_repo=vpn_keys_repo,
        plans_repo=plans_repo,
        users_repo=users_repo,
        delivery_jobs_repo=delivery_jobs_repo,
        audit_logs_repo=audit_logs_repo,
        hiddify=hiddify_service,
        key_protector=key_protector,
    )
    inventory_service = InventoryService(
        session=session,
        orders_repo=orders_repo,
        vpn_keys_repo=vpn_keys_repo,
        plans_repo=plans_repo,
        audit_logs_repo=audit_logs_repo,
    )
    provider = payment_provider or (
        DonateStreamPaymentProvider(settings)
        if settings.payment_provider == "donate_stream"
        else FakePaymentProvider()
    )
    orders_service = OrdersService(
        session=session,
        settings=settings,
        plans_repo=plans_repo,
        orders_repo=orders_repo,
        vpn_keys_repo=vpn_keys_repo,
        users_repo=users_repo,
        payments_repo=payments_repo,
        delivery_jobs_repo=delivery_jobs_repo,
        audit_logs_repo=audit_logs_repo,
        notification_service=notification_service,
        payment_provider=provider,
        hiddify=hiddify_service,
        pricing=pricing_service,
        promos=promo_service,
        shop_settings=shop_settings_service,
    )
    payments_service = PaymentService(
        session=session,
        provider=provider,
        orders_repo=orders_repo,
        payments_repo=payments_repo,
        payment_events_repo=payment_events_repo,
        vpn_keys_repo=vpn_keys_repo,
        audit_logs_repo=audit_logs_repo,
        issuing_service=issuing_service,
        notification_service=notification_service,
        stale_pending_minutes=settings.payment_stale_pending_minutes,
    )
    delivery_service = DeliveryService(
        session=session,
        settings=settings,
        delivery_jobs_repo=delivery_jobs_repo,
        orders_repo=orders_repo,
        audit_logs_repo=audit_logs_repo,
        notification_service=notification_service,
        key_protector=key_protector,
    )
    communications_service = CommunicationsService(
        session=session,
        users_repo=users_repo,
        audit_logs_repo=audit_logs_repo,
        notification_service=notification_service,
    )
    xlsx_import = XlsxImportService(
        session=session,
        import_batches_repo=import_batches_repo,
        audit_logs_repo=audit_logs_repo,
        key_protector=key_protector,
    )
    sqlite_import = SqliteImportService(
        session=session,
        import_batches_repo=import_batches_repo,
        audit_logs_repo=audit_logs_repo,
        key_protector=key_protector,
    )
    hiddify_xlsx_import = HiddifyXlsxImportService(
        session=session,
        hiddify=hiddify_service,
        audit_logs_repo=audit_logs_repo,
    )
    xlsx_export = XlsxExportService(
        session=session,
        key_protector=key_protector,
        audit_logs_repo=audit_logs_repo,
    )
    subscriptions = SubscriptionAggregatorService(
        session=session,
        vpn_keys_repo=vpn_keys_repo,
        key_protector=key_protector,
    )
    return {
        "session": session,
        "users_repo": users_repo,
        "plans_repo": plans_repo,
        "orders_repo": orders_repo,
        "vpn_keys_repo": vpn_keys_repo,
        "payments_repo": payments_repo,
        "payment_events_repo": payment_events_repo,
        "delivery_jobs_repo": delivery_jobs_repo,
        "hiddify_servers_repo": hiddify_servers_repo,
        "hiddify_usage_snapshots_repo": hiddify_usage_snapshots_repo,
        "audit_logs_repo": audit_logs_repo,
        "promo_codes_repo": promo_codes_repo,
        "shop_settings_repo": shop_settings_repo,
        "users": users_service,
        "content": content_service,
        "pricing": pricing_service,
        "promos": promo_service,
        "shop_settings": shop_settings_service,
        "plans": plans_service,
        "inventory": inventory_service,
        "hiddify": hiddify_service,
        "hiddify_usage": hiddify_usage_service,
        "issuing": issuing_service,
        "orders": orders_service,
        "payments": payments_service,
        "delivery": delivery_service,
        "communications": communications_service,
        "xlsx_import": xlsx_import,
        "sqlite_import": sqlite_import,
        "hiddify_xlsx_import": hiddify_xlsx_import,
        "xlsx_export": xlsx_export,
        "subscriptions": subscriptions,
        "notification": notification_service,
        "notifications": notification_service,
        "protector": key_protector,
    }


@pytest.fixture()
def fake_bot() -> FakeBot:
    return FakeBot()


@pytest.fixture()
def fake_vk() -> FakeVkClient:
    return FakeVkClient()


@pytest.fixture()
def fake_whatsapp() -> FakeWhatsAppClient:
    return FakeWhatsAppClient()


@pytest.fixture()
async def db(session_factory):
    async with session_factory() as session:
        yield session


async def seed_default_plan(session: AsyncSession, settings: Settings) -> Plan:
    services = build_services(session, settings, FakeBot())
    await services["plans"].seed_defaults()
    await session.commit()
    return await services["plans_repo"].get_by_code("plan_30")


async def create_user(session: AsyncSession, telegram_user_id: int = 12345, role: str = UserRole.USER.value) -> User:
    user = User(
        telegram_user_id=telegram_user_id,
        username=f"user{telegram_user_id}",
        full_name=f"User {telegram_user_id}",
        role=role,
    )
    session.add(user)
    await session.commit()
    return user


async def create_available_key(session: AsyncSession, protector: KeyProtector, plan_id: int, key_value: str) -> VPNKey:
    vpn_key = VPNKey(
        plan_id=plan_id,
        key_value_encrypted=protector.encrypt(key_value),
        key_fingerprint=protector.fingerprint(key_value),
        status=KeyStatus.AVAILABLE.value,
    )
    session.add(vpn_key)
    await session.commit()
    return vpn_key


async def create_hiddify_server(
    session: AsyncSession,
    protector: KeyProtector,
    *,
    name: str = "Primary Hiddify",
    country_name: str = "Germany",
    base_url: str = "https://panel.example.com",
    admin_proxy_path: str = "admin-secret",
    client_proxy_path: str = "client-secret",
    is_active: bool = True,
) -> HiddifyServer:
    server = HiddifyServer(
        name=name,
        country_name=country_name,
        base_url=base_url,
        admin_proxy_path=admin_proxy_path,
        client_proxy_path=client_proxy_path,
        api_key_encrypted=protector.encrypt("test-hiddify-api-key"),
        is_active=is_active,
        last_health_status="healthy" if is_active else "unknown",
    )
    session.add(server)
    await session.commit()
    return server


def make_xlsx(rows: list[dict]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "keys"
    sheet.append(["plan_code", "key_value", "external_ref", "comment", "expires_at"])
    for row in rows:
        sheet.append(
            [
                row.get("plan_code"),
                row.get("key_value"),
                row.get("external_ref"),
                row.get("comment"),
                row.get("expires_at"),
            ]
        )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_hiddify_servers_xlsx(rows: list[dict]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "servers"
    sheet.append(
        [
            "name",
            "country_name",
            "base_url",
            "admin_proxy_path",
            "client_proxy_path",
            "api_key",
            "is_active",
        ]
    )
    for row in rows:
        sheet.append(
            [
                row.get("name"),
                row.get("country_name"),
                row.get("base_url"),
                row.get("admin_proxy_path"),
                row.get("client_proxy_path"),
                row.get("api_key"),
                row.get("is_active"),
            ]
        )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
