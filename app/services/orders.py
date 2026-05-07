from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import DeliveryJobStatus, KeyStatus, OrderFulfillmentMode, OrderStatus, PlanProvisioningMode
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.delivery_jobs import DeliveryJobsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.users import UsersRepository
from app.repositories.vpn_keys import VPNKeysRepository
from app.services.exceptions import InvalidStateError, NotFoundError, OutOfStockError
from app.services.hiddify import HiddifyService
from app.services.notifications import NotificationService
from app.services.payments.base import PaymentProvider
from app.services.pricing import PricingService
from app.services.promos import PromoService
from app.services.shop_settings import ShopSettingsService
from app.services.transactions import transactional


class OrdersService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        plans_repo: PlansRepository,
        orders_repo: OrdersRepository,
        vpn_keys_repo: VPNKeysRepository,
        users_repo: UsersRepository,
        payments_repo: PaymentsRepository,
        delivery_jobs_repo: DeliveryJobsRepository,
        audit_logs_repo: AuditLogsRepository,
        notification_service: NotificationService,
        payment_provider: PaymentProvider,
        hiddify: HiddifyService,
        pricing: PricingService,
        promos: PromoService,
        shop_settings: ShopSettingsService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._plans_repo = plans_repo
        self._orders_repo = orders_repo
        self._vpn_keys_repo = vpn_keys_repo
        self._users_repo = users_repo
        self._payments_repo = payments_repo
        self._delivery_jobs_repo = delivery_jobs_repo
        self._audit_logs_repo = audit_logs_repo
        self._notification_service = notification_service
        self._payment_provider = payment_provider
        self._hiddify = hiddify
        self._pricing = pricing
        self._promos = promos
        self._shop_settings = shop_settings

    async def create_order_with_payment(
        self,
        *,
        user_id: int,
        plan_id: int,
        requested_fulfillment_mode: str | None = None,
        preferred_hiddify_server_id: int | None = None,
        promo_code: str | None = None,
    ) -> tuple[int, str]:
        selected_server = None
        promo = None
        promo_preview = None
        async with transactional(self._session):
            plan = await self._plans_repo.get_by_id(plan_id)
            if plan is None or not plan.is_active:
                raise NotFoundError("Plan not found")

            available_count = await self._vpn_keys_repo.count_available(plan_id)
            uses_remote_servers = plan.provisioning_mode not in {
                PlanProvisioningMode.INVENTORY.value,
            }
            available_servers = await self._hiddify.list_available_servers() if uses_remote_servers else []
            fulfillment_mode, selected_server = await self._resolve_fulfillment_mode(
                provisioning_mode=plan.provisioning_mode,
                available_count=available_count,
                available_servers=available_servers,
                requested_fulfillment_mode=requested_fulfillment_mode,
                preferred_hiddify_server_id=preferred_hiddify_server_id,
            )
            uses_hiddify = fulfillment_mode in {
                OrderFulfillmentMode.MTPROXY.value,
                OrderFulfillmentMode.HIDDIFY_SERVER.value,
                OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
            }
            original_amount = self._pricing.price_for_plan(plan, fulfillment_mode)
            customer = await self._users_repo.get_by_id(user_id)
            effective_promo_code = promo_code or (customer.active_promo_code if customer else None)
            amount_value = original_amount
            discount_amount = None
            if effective_promo_code:
                promo = await self._promos.get_valid_promo(code=effective_promo_code, user_id=user_id)
                promo_preview = self._promos.preview(amount=original_amount, promo=promo)
                amount_value = promo_preview.final_amount
                discount_amount = promo_preview.discount_amount

            order = await self._orders_repo.create(
                user_id=user_id,
                plan_id=plan.id,
                amount_value=amount_value,
                amount_currency=plan.price_currency,
                original_amount_value=original_amount,
                discount_amount_value=discount_amount,
                promo_code_id=promo.id if promo else None,
                promo_code=promo.code if promo else None,
                payment_provider=self._payment_provider.provider_name,
                fulfillment_mode=fulfillment_mode,
                preferred_hiddify_server_id=selected_server.id if selected_server else None,
            )
            if promo is not None:
                promo_preview = await self._promos.redeem_for_order(
                    promo=promo,
                    user_id=user_id,
                    order_id=order.id,
                    original_amount=original_amount,
                )
                if customer is not None:
                    await self._users_repo.set_active_promo_code(user=customer, promo_code=None)
            if uses_hiddify:
                order.reserved_key_id = None
                order.reservation_expires_at = None
            else:
                reserved_key = await self._vpn_keys_repo.reserve_available_key(plan_id=plan.id, order_id=order.id)
                if reserved_key is None:
                    raise OutOfStockError("No keys available for reservation")
                order.reserved_key_id = reserved_key.id
                order.reservation_expires_at = datetime.now(tz=timezone.utc) + timedelta(
                    minutes=self._settings.reservation_ttl_minutes
                )

            await self._audit_logs_repo.add(
                actor_user_id=user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="order_created",
                payload_json={
                    "order_id": order.id,
                    "plan_id": plan.id,
                    "provisioning_mode": plan.provisioning_mode,
                    "fulfillment_mode": fulfillment_mode,
                    "preferred_hiddify_server_id": selected_server.id if selected_server else None,
                    "preferred_country_name": getattr(selected_server, "country_name", None),
                    "preferred_server_name": getattr(selected_server, "name", None),
                    "original_amount_value": str(original_amount),
                    "amount_value": str(amount_value),
                    "promo_code": promo.code if promo else None,
                    "discount_amount_value": str(promo_preview.discount_amount) if promo_preview else None,
                },
            )

        try:
            payment_url_override = None
            if self._payment_provider.provider_name == "donate_stream":
                payment_url_override = await self._shop_settings.get_donate_stream_url()
            payment_result = await self._payment_provider.create_payment(
                order_id=order.id,
                amount_value=order.amount_value,
                amount_currency=order.amount_currency,
                description=f"{plan.name} ({plan.duration_days} days)",
                payment_url=payment_url_override,
            )
        except Exception as exc:
            async with transactional(self._session):
                locked_order = await self._orders_repo.lock_by_id(order.id)
                if locked_order and locked_order.reserved_key_id:
                    vpn_key = await self._vpn_keys_repo.lock_by_id(locked_order.reserved_key_id)
                    if vpn_key and vpn_key.status == KeyStatus.RESERVED.value and vpn_key.reserved_by_order_id == locked_order.id:
                        await self._vpn_keys_repo.release_reservation(vpn_key)
                if locked_order:
                    locked_order.reserved_key_id = None
                    locked_order.status = OrderStatus.PAYMENT_FAILED.value
                    locked_order.failure_reason = f"Payment provider error: {exc}"
                await self._audit_logs_repo.add(
                    actor_user_id=user_id,
                    entity_type="order",
                    entity_id=str(order.id),
                    action="payment_create_failed",
                    payload_json={"order_id": order.id, "error": str(exc)},
                )
            raise

        async with transactional(self._session):
            locked_order = await self._orders_repo.lock_by_id(order.id)
            if locked_order is None:
                raise NotFoundError("Order disappeared during payment creation")
            locked_order.provider_payment_id = payment_result.provider_payment_id
            locked_order.payment_url = payment_result.payment_url
            locked_order.status = OrderStatus.PENDING_PAYMENT.value
            await self._payments_repo.upsert(
                order_id=locked_order.id,
                provider=self._payment_provider.provider_name,
                provider_payment_id=payment_result.provider_payment_id,
                amount_value=locked_order.amount_value,
                amount_currency=locked_order.amount_currency,
                status="pending",
                raw_payload_json={},
                provider_metadata_json=payment_result.provider_metadata,
                idempotency_key=payment_result.idempotency_key,
            )
            await self._audit_logs_repo.add(
                actor_user_id=user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="payment_created",
                payload_json={
                    "order_id": order.id,
                    "provider_payment_id": payment_result.provider_payment_id,
                    "payment_provider": self._payment_provider.provider_name,
                },
            )

        customer = await self._users_repo.get_by_id(user_id)
        if customer is not None:
            await self._notification_service.send_admin_payment_review_request(
                order_id=order.id,
                plan_name=plan.name,
                amount_value=order.amount_value,
                amount_currency=plan.price_currency,
                customer_user=customer,
                customer_username=customer.username,
                payment_url=payment_result.payment_url,
                fulfillment_mode=fulfillment_mode,
                preferred_server_name=getattr(selected_server, "name", None),
                preferred_country_name=getattr(selected_server, "country_name", None),
            )
        return order.id, payment_result.payment_url

    async def _resolve_fulfillment_mode(
        self,
        *,
        provisioning_mode: str,
        available_count: int,
        available_servers: list,
        requested_fulfillment_mode: str | None,
        preferred_hiddify_server_id: int | None,
    ) -> tuple[str, object | None]:
        if requested_fulfillment_mode is None:
            if preferred_hiddify_server_id is not None:
                requested_fulfillment_mode = OrderFulfillmentMode.HIDDIFY_SERVER.value
            elif provisioning_mode == PlanProvisioningMode.MTPROXY.value:
                requested_fulfillment_mode = OrderFulfillmentMode.MTPROXY.value
            elif provisioning_mode == PlanProvisioningMode.INVENTORY.value:
                requested_fulfillment_mode = OrderFulfillmentMode.INVENTORY.value
            elif provisioning_mode == PlanProvisioningMode.HIDDIFY.value:
                requested_fulfillment_mode = OrderFulfillmentMode.HIDDIFY_SERVER.value
            elif available_count > 0:
                requested_fulfillment_mode = OrderFulfillmentMode.INVENTORY.value
            else:
                requested_fulfillment_mode = OrderFulfillmentMode.HIDDIFY_SERVER.value

        if (
            provisioning_mode == PlanProvisioningMode.MTPROXY.value
            and requested_fulfillment_mode == OrderFulfillmentMode.INVENTORY.value
        ):
            requested_fulfillment_mode = OrderFulfillmentMode.MTPROXY.value

        allows_inventory = provisioning_mode != PlanProvisioningMode.HIDDIFY.value
        allows_hiddify = provisioning_mode not in {
            PlanProvisioningMode.INVENTORY.value,
            PlanProvisioningMode.MTPROXY.value,
        }

        if requested_fulfillment_mode == OrderFulfillmentMode.MTPROXY.value:
            if provisioning_mode != PlanProvisioningMode.MTPROXY.value:
                raise InvalidStateError("This plan is not an MTProxy product.")
            if not available_servers:
                raise OutOfStockError("No active servers are available for MTProxy issuing.")
            return requested_fulfillment_mode, None

        if requested_fulfillment_mode == OrderFulfillmentMode.INVENTORY.value:
            if not allows_inventory:
                raise InvalidStateError("This plan is issued only through Hiddify servers.")
            if available_count <= 0:
                raise OutOfStockError("No prepared keys are available for this plan.")
            return requested_fulfillment_mode, None

        if requested_fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SERVER.value:
            if not allows_hiddify:
                raise InvalidStateError("This plan does not support server issuance.")
            if not available_servers:
                raise OutOfStockError("No active servers are available for this plan.")
            if preferred_hiddify_server_id is None:
                if len(available_servers) == 1:
                    selected_server = await self._hiddify.get_active_server_choice(available_servers[0].server_id)
                    return requested_fulfillment_mode, selected_server
                raise InvalidStateError("Choose a server before creating the order.")
            selected_server = await self._hiddify.get_active_server_choice(preferred_hiddify_server_id)
            return requested_fulfillment_mode, selected_server

        if requested_fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SUPERKEY.value:
            if not allows_hiddify:
                raise InvalidStateError("This plan does not support server issuance.")
            if len(available_servers) < 2:
                raise OutOfStockError("Superkey needs at least two active servers.")
            return requested_fulfillment_mode, None

        raise InvalidStateError("Unsupported fulfillment mode.")

    async def list_user_orders(self, user_id: int):
        return await self._orders_repo.list_by_user(user_id)

    async def search_orders(self, query_text: str):
        return await self._orders_repo.search(query_text)

    async def cancel_unpaid_order(self, *, order_id: int, actor_user_id: int | None) -> None:
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError("Order not found")
            if order.status not in {OrderStatus.CREATED.value, OrderStatus.PENDING_PAYMENT.value, OrderStatus.PAYMENT_FAILED.value}:
                raise InvalidStateError("Only unpaid orders can be canceled")
            if order.reserved_key_id:
                vpn_key = await self._vpn_keys_repo.lock_by_id(order.reserved_key_id)
                if vpn_key and vpn_key.status == KeyStatus.RESERVED.value and vpn_key.reserved_by_order_id == order.id:
                    await self._vpn_keys_repo.release_reservation(vpn_key)
            order.reserved_key_id = None
            order.status = OrderStatus.CANCELED.value
            order.failure_reason = "Order canceled manually."
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="order_canceled",
                payload_json={"order_id": order.id},
            )

    async def mark_refunded(self, *, order_id: int, actor_user_id: int | None) -> None:
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError("Order not found")
            if order.status not in {OrderStatus.ISSUED.value, OrderStatus.PAID.value, OrderStatus.PAID_BUT_NOT_ISSUED.value}:
                raise InvalidStateError("Order cannot be marked as refunded")
            order.status = OrderStatus.REFUNDED.value
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="order_refunded",
                payload_json={"order_id": order.id},
            )

    async def enqueue_resend(self, *, order_id: int, actor_user_id: int | None) -> None:
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError("Order not found")
            if order.status != OrderStatus.ISSUED.value or order.issued_key_id is None:
                raise InvalidStateError("Only issued orders can be resent")
            order.delivery_status = DeliveryJobStatus.PENDING.value
            await self._delivery_jobs_repo.enqueue(
                order_id=order.id,
                user_id=order.user_id,
                job_type="deliver_issued_key",
                payload_json={"order_id": order.id, "issued_key_id": order.issued_key_id},
                dedupe_key=f"resend:{order.id}:{uuid4().hex}",
            )
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="order_resend_queued",
                payload_json={"order_id": order.id},
            )

    async def replace_issued_key(self, *, order_id: int, actor_user_id: int | None) -> int:
        async with transactional(self._session):
            now = datetime.now(tz=timezone.utc)
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError("Order not found")
            if order.status != OrderStatus.ISSUED.value or order.issued_key_id is None:
                raise InvalidStateError("Only issued orders can receive a replacement key")

            current_key = await self._vpn_keys_repo.lock_by_id(order.issued_key_id)
            if current_key is None:
                raise NotFoundError("Current issued key not found")
            await self._vpn_keys_repo.mark_broken(current_key)

            replacement_key = await self._vpn_keys_repo.get_next_available_for_plan(order.plan_id)
            if replacement_key is None:
                raise OutOfStockError("No replacement keys are available")

            await self._vpn_keys_repo.issue_key(vpn_key=replacement_key, user_id=order.user_id, issued_at=now)
            order.issued_key_id = replacement_key.id
            order.delivery_status = DeliveryJobStatus.PENDING.value
            await self._delivery_jobs_repo.enqueue(
                order_id=order.id,
                user_id=order.user_id,
                job_type="deliver_issued_key",
                payload_json={"order_id": order.id, "issued_key_id": replacement_key.id},
                dedupe_key=f"replacement:{order.id}:{replacement_key.id}",
            )
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="replacement_issued",
                payload_json={"order_id": order.id, "vpn_key_id": replacement_key.id},
            )
            return replacement_key.id
