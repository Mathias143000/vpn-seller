from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from functools import lru_cache

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory, ensure_runtime_compatibility, init_models
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
from app.services.httpx_session import HttpxSession
from app.services.imports.xlsx_export import XlsxExportService
from app.services.imports.hiddify_xlsx_import import HiddifyXlsxImportService
from app.services.imports.xlsx_import import XlsxImportService
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
from app.services.vk_api import VKApiClient
from app.services.vk_bot import VkBotService
from app.services.whatsapp_api import WhatsAppApiClient
from app.services.whatsapp_bot import WhatsAppBotService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ServiceBundle:
    session: AsyncSession
    users_repo: UsersRepository
    plans_repo: PlansRepository
    orders_repo: OrdersRepository
    vpn_keys_repo: VPNKeysRepository
    payments_repo: PaymentsRepository
    payment_events_repo: PaymentEventsRepository
    delivery_jobs_repo: DeliveryJobsRepository
    hiddify_servers_repo: HiddifyServersRepository
    hiddify_usage_snapshots_repo: HiddifyUsageSnapshotsRepository
    import_batches_repo: ImportBatchesRepository
    audit_logs_repo: AuditLogsRepository
    promo_codes_repo: PromoCodesRepository
    shop_settings_repo: ShopSettingsRepository
    users: UsersService
    content: ContentService
    pricing: PricingService
    promos: PromoService
    shop_settings: ShopSettingsService
    plans: PlansService
    inventory: InventoryService
    hiddify: HiddifyService
    hiddify_usage: HiddifyUsageMonitorService
    orders: OrdersService
    issuing: IssuingService
    payments: PaymentService
    delivery: DeliveryService
    communications: CommunicationsService
    xlsx_import: XlsxImportService
    hiddify_xlsx_import: HiddifyXlsxImportService
    xlsx_export: XlsxExportService
    notifications: NotificationService
    subscriptions: SubscriptionAggregatorService


class AppContainer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.engine = create_engine(self.settings)
        self.session_factory = create_session_factory(self.engine)
        self.key_protector = KeyProtector(self.settings.encryption_key.get_secret_value())
        self.bot = Bot(
            token=self.settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=HttpxSession(),
        )
        self.vk_client = VKApiClient(self.settings)
        self.whatsapp_client = WhatsAppApiClient(self.settings)
        self._payment_provider = (
            DonateStreamPaymentProvider(self.settings)
            if self.settings.payment_provider == "donate_stream"
            else FakePaymentProvider()
        )
        self._dispatcher: Dispatcher | None = None
        self._background_task: asyncio.Task | None = None

    @property
    def dispatcher(self) -> Dispatcher:
        if self._dispatcher is None:
            from app.bot import create_dispatcher

            self._dispatcher = create_dispatcher(self)
        return self._dispatcher

    def build_services(self, session: AsyncSession) -> ServiceBundle:
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

        users_service = UsersService(users_repo, self.settings.parsed_admin_ids)
        content_service = ContentService(self.settings)
        pricing_service = PricingService(self.settings)
        hiddify_service = HiddifyService(
            session=session,
            settings=self.settings,
            hiddify_servers_repo=hiddify_servers_repo,
            usage_snapshots_repo=hiddify_usage_snapshots_repo,
            audit_logs_repo=audit_logs_repo,
            key_protector=self.key_protector,
        )
        plans_service = PlansService(plans_repo, self.settings, hiddify_service, pricing_service)
        notification_service = NotificationService(self.bot, self.settings, self.vk_client, self.whatsapp_client, content_service)
        hiddify_usage_service = HiddifyUsageMonitorService(
            session=session,
            settings=self.settings,
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
            min_order_amount=self.settings.min_order_amount,
        )
        shop_settings_service = ShopSettingsService(
            settings=self.settings,
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
            key_protector=self.key_protector,
        )
        inventory_service = InventoryService(
            session=session,
            orders_repo=orders_repo,
            vpn_keys_repo=vpn_keys_repo,
            plans_repo=plans_repo,
            audit_logs_repo=audit_logs_repo,
        )
        orders_service = OrdersService(
            session=session,
            settings=self.settings,
            plans_repo=plans_repo,
            orders_repo=orders_repo,
            vpn_keys_repo=vpn_keys_repo,
            users_repo=users_repo,
            payments_repo=payments_repo,
            delivery_jobs_repo=delivery_jobs_repo,
            audit_logs_repo=audit_logs_repo,
            notification_service=notification_service,
            payment_provider=self._payment_provider,
            hiddify=hiddify_service,
            pricing=pricing_service,
            promos=promo_service,
            shop_settings=shop_settings_service,
        )
        payments_service = PaymentService(
            session=session,
            provider=self._payment_provider,
            orders_repo=orders_repo,
            payments_repo=payments_repo,
            payment_events_repo=payment_events_repo,
            vpn_keys_repo=vpn_keys_repo,
            audit_logs_repo=audit_logs_repo,
            issuing_service=issuing_service,
            notification_service=notification_service,
            stale_pending_minutes=self.settings.payment_stale_pending_minutes,
        )
        delivery_service = DeliveryService(
            session=session,
            settings=self.settings,
            delivery_jobs_repo=delivery_jobs_repo,
            orders_repo=orders_repo,
            audit_logs_repo=audit_logs_repo,
            notification_service=notification_service,
            key_protector=self.key_protector,
        )
        communications_service = CommunicationsService(
            session=session,
            users_repo=users_repo,
            audit_logs_repo=audit_logs_repo,
            notification_service=notification_service,
        )
        xlsx_import_service = XlsxImportService(
            session=session,
            import_batches_repo=import_batches_repo,
            audit_logs_repo=audit_logs_repo,
            key_protector=self.key_protector,
        )
        hiddify_xlsx_import_service = HiddifyXlsxImportService(
            session=session,
            hiddify=hiddify_service,
            audit_logs_repo=audit_logs_repo,
        )
        xlsx_export_service = XlsxExportService(
            session=session,
            key_protector=self.key_protector,
            audit_logs_repo=audit_logs_repo,
        )
        subscriptions_service = SubscriptionAggregatorService(
            session=session,
            vpn_keys_repo=vpn_keys_repo,
            key_protector=self.key_protector,
        )

        return ServiceBundle(
            session=session,
            users_repo=users_repo,
            plans_repo=plans_repo,
            orders_repo=orders_repo,
            vpn_keys_repo=vpn_keys_repo,
            payments_repo=payments_repo,
            payment_events_repo=payment_events_repo,
            delivery_jobs_repo=delivery_jobs_repo,
            hiddify_servers_repo=hiddify_servers_repo,
            hiddify_usage_snapshots_repo=hiddify_usage_snapshots_repo,
            import_batches_repo=import_batches_repo,
            audit_logs_repo=audit_logs_repo,
            promo_codes_repo=promo_codes_repo,
            shop_settings_repo=shop_settings_repo,
            users=users_service,
            content=content_service,
            pricing=pricing_service,
            promos=promo_service,
            shop_settings=shop_settings_service,
            plans=plans_service,
            inventory=inventory_service,
            hiddify=hiddify_service,
            hiddify_usage=hiddify_usage_service,
            orders=orders_service,
            issuing=issuing_service,
            payments=payments_service,
            delivery=delivery_service,
            communications=communications_service,
            xlsx_import=xlsx_import_service,
            hiddify_xlsx_import=hiddify_xlsx_import_service,
            xlsx_export=xlsx_export_service,
            notifications=notification_service,
            subscriptions=subscriptions_service,
        )

    async def startup(self) -> None:
        await init_models(self.engine)
        await ensure_runtime_compatibility(self.engine)
        async with self.engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
        async with self.session_factory() as session:
            services = self.build_services(session)
            async with session.begin():
                await services.plans.seed_defaults()
        if self.settings.app_mode == "web" and self._background_task is None:
            self._background_task = asyncio.create_task(self._background_loop(), name="background-workers")

    async def shutdown(self) -> None:
        if self._background_task is not None:
            self._background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._background_task
        await self.bot.session.close()
        await self.vk_client.close()
        await self.whatsapp_client.close()
        await self.engine.dispose()

    async def _background_loop(self) -> None:
        while True:
            try:
                async with self.session_factory() as session:
                    services = self.build_services(session)
                    await services.inventory.cleanup_expired_reservations()
                    await services.payments.reconcile()
                    await services.delivery.process_pending_jobs()
                    await services.hiddify_usage.collect_due_snapshots()
            except Exception:
                logger.exception("Background workers failed")
            await asyncio.sleep(15)

    async def run_polling(self) -> None:
        await self.startup()
        await self.bot.delete_webhook(drop_pending_updates=False)
        try:
            await self.dispatcher.start_polling(self.bot)
        finally:
            await self.shutdown()

    @property
    def vk_bot(self) -> VkBotService:
        return VkBotService(
            self.settings,
            self.vk_client,
            NotificationService(self.bot, self.settings, self.vk_client, self.whatsapp_client, ContentService(self.settings)),
        )

    @property
    def whatsapp_bot(self) -> WhatsAppBotService:
        return WhatsAppBotService(
            self.settings,
            self.whatsapp_client,
            NotificationService(self.bot, self.settings, self.vk_client, self.whatsapp_client, ContentService(self.settings)),
        )


@lru_cache(maxsize=1)
def get_container() -> AppContainer:
    return AppContainer()
