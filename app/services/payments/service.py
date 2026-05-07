from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KeyStatus, OrderStatus, PaymentStatus, ProcessingStatus
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.payment_events import PaymentEventsRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.vpn_keys import VPNKeysRepository
from app.services.exceptions import InvalidStateError, NotFoundError, PaymentMismatchError
from app.services.issuing import IssuingService
from app.services.notifications import NotificationService
from app.services.payments.base import PaymentProvider
from app.services.transactions import transactional


class PaymentService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        provider: PaymentProvider,
        orders_repo: OrdersRepository,
        payments_repo: PaymentsRepository,
        payment_events_repo: PaymentEventsRepository,
        vpn_keys_repo: VPNKeysRepository,
        audit_logs_repo: AuditLogsRepository,
        issuing_service: IssuingService,
        notification_service: NotificationService,
        stale_pending_minutes: int,
    ) -> None:
        self._session = session
        self._provider = provider
        self._orders_repo = orders_repo
        self._payments_repo = payments_repo
        self._payment_events_repo = payment_events_repo
        self._vpn_keys_repo = vpn_keys_repo
        self._audit_logs_repo = audit_logs_repo
        self._issuing_service = issuing_service
        self._notification_service = notification_service
        self._stale_pending_minutes = stale_pending_minutes

    async def handle_webhook(self, payload: dict) -> dict:
        event = await self._provider.parse_webhook(payload)
        async with transactional(self._session):
            payment_event, inserted = await self._payment_events_repo.record_event(
                provider=event.provider,
                provider_event_id=event.provider_event_id,
                provider_payment_id=event.provider_payment_id,
                event_type=event.event_type,
                raw_payload_json=event.raw_payload,
            )
            if not inserted:
                if payment_event:
                    await self._payment_events_repo.mark_processed(
                        payment_event,
                        status=ProcessingStatus.DUPLICATE.value,
                        processed_at=datetime.now(tz=timezone.utc),
                    )
                return {"duplicate": True, "provider_event_id": event.provider_event_id}

            order = None
            if event.order_id is not None:
                order = await self._orders_repo.get_by_id(event.order_id)
            if order is None:
                order = await self._orders_repo.get_by_provider_payment_id(event.provider_payment_id)
            if order is None:
                if payment_event:
                    await self._payment_events_repo.mark_processed(
                        payment_event,
                        status=ProcessingStatus.FAILED.value,
                        processed_at=datetime.now(tz=timezone.utc),
                    )
                raise NotFoundError("Order not found for webhook event")

            if not _amounts_match(order.amount_value, event.amount_value) or (
                order.amount_currency.upper() != event.amount_currency.upper()
            ):
                if payment_event:
                    await self._payment_events_repo.mark_processed(
                        payment_event,
                        status=ProcessingStatus.FAILED.value,
                        processed_at=datetime.now(tz=timezone.utc),
                    )
                await self._audit_logs_repo.add(
                    actor_user_id=None,
                    entity_type="order",
                    entity_id=str(order.id),
                    action="payment_mismatch",
                    payload_json={
                        "order_id": order.id,
                        "expected_amount": str(order.amount_value),
                        "received_amount": str(event.amount_value),
                    },
                )
                raise PaymentMismatchError("Webhook amount or currency mismatch")

            await self._payments_repo.upsert(
                order_id=order.id,
                provider=event.provider,
                provider_payment_id=event.provider_payment_id,
                amount_value=event.amount_value,
                amount_currency=event.amount_currency,
                status=event.status,
                raw_payload_json=event.raw_payload,
                provider_metadata_json=event.provider_metadata,
                paid_at=event.paid_at,
            )

            if event.status == PaymentStatus.SUCCEEDED.value and order.status in {
                OrderStatus.CREATED.value,
                OrderStatus.PENDING_PAYMENT.value,
                OrderStatus.PAYMENT_FAILED.value,
            }:
                order.status = OrderStatus.PAID.value
                order.failure_reason = None
            elif event.status == PaymentStatus.CANCELED.value and order.status in {
                OrderStatus.CREATED.value,
                OrderStatus.PENDING_PAYMENT.value,
            }:
                await self._release_reservation(order)
                order.status = OrderStatus.CANCELED.value
            elif event.status in {PaymentStatus.FAILED.value, PaymentStatus.REFUNDED.value} and order.status in {
                OrderStatus.CREATED.value,
                OrderStatus.PENDING_PAYMENT.value,
            }:
                await self._release_reservation(order)
                order.status = OrderStatus.PAYMENT_FAILED.value

            await self._audit_logs_repo.add(
                actor_user_id=None,
                entity_type="payment_event",
                entity_id=str(payment_event.id if payment_event else event.provider_event_id),
                action="payment_event_processed",
                payload_json={
                    "order_id": order.id,
                    "provider_payment_id": event.provider_payment_id,
                    "status": event.status,
                },
            )
            if payment_event:
                await self._payment_events_repo.mark_processed(
                    payment_event,
                    status=ProcessingStatus.PROCESSED.value,
                    processed_at=datetime.now(tz=timezone.utc),
                )
            order_id = order.id

        if event.status == PaymentStatus.SUCCEEDED.value:
            order_status, _ = await self._issuing_service.issue_key_for_paid_order(order_id)
            if order_status == OrderStatus.PAID_BUT_NOT_ISSUED.value:
                await self._notification_service.send_admin_alert(
                    f"Оплата подтверждена, но ключ не выдан. Order #{order_id}"
                )
        return {"duplicate": False, "order_id": order_id, "status": event.status}

    async def reconcile(self) -> int:
        now = datetime.now(tz=timezone.utc)
        processed = 0
        async with transactional(self._session):
            stale_orders = await self._orders_repo.list_pending_for_reconciliation(
                now=now,
                older_than_minutes=self._stale_pending_minutes,
            )
            stale_orders.extend(await self._orders_repo.list_paid_but_not_issued())

        seen_ids: set[int] = set()
        for order in stale_orders:
            if order.id in seen_ids or not order.provider_payment_id:
                continue
            seen_ids.add(order.id)
            provider_status = await self._resolve_payment_status(order)
            async with transactional(self._session):
                locked_order = await self._orders_repo.lock_by_id(order.id)
                if locked_order is None:
                    continue
                if provider_status == PaymentStatus.SUCCEEDED.value:
                    if locked_order.status == OrderStatus.PENDING_PAYMENT.value:
                        locked_order.status = OrderStatus.PAID.value
                        processed += 1
                    elif locked_order.status == OrderStatus.PAID_BUT_NOT_ISSUED.value:
                        processed += 1
                elif provider_status in {PaymentStatus.CANCELED.value, PaymentStatus.FAILED.value}:
                    if locked_order.status == OrderStatus.PENDING_PAYMENT.value:
                        await self._release_reservation(locked_order)
                        locked_order.status = OrderStatus.PAYMENT_FAILED.value
                        processed += 1
            if provider_status == PaymentStatus.SUCCEEDED.value:
                status, _ = await self._issuing_service.issue_key_for_paid_order(order.id)
                if status == OrderStatus.PAID_BUT_NOT_ISSUED.value:
                    await self._notification_service.send_admin_alert(
                        f"Reconciliation обнаружил оплаченный, но не выданный заказ #{order.id}"
                    )
        return processed

    async def confirm_manual_payment(self, *, order_id: int, actor_user_id: int | None) -> tuple[str, int | None]:
        now = datetime.now(tz=timezone.utc)
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError("Order not found")
            if order.status == OrderStatus.ISSUED.value:
                return order.status, order.issued_key_id
            if order.status in {OrderStatus.CANCELED.value, OrderStatus.REFUNDED.value, OrderStatus.EXPIRED_RESERVATION.value}:
                raise InvalidStateError("Order is not eligible for manual payment confirmation")

            order.status = OrderStatus.PAID.value
            order.failure_reason = None
            await self._payments_repo.upsert(
                order_id=order.id,
                provider=order.payment_provider,
                provider_payment_id=order.provider_payment_id or f"manual-{order.id}",
                amount_value=order.amount_value,
                amount_currency=order.amount_currency,
                status=PaymentStatus.SUCCEEDED.value,
                raw_payload_json={"manual_confirmation": True, "actor_user_id": actor_user_id},
                provider_metadata_json={"manual_confirmation": True},
                paid_at=now,
            )
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="order",
                entity_id=str(order.id),
                action="payment_confirmed_manually",
                payload_json={"order_id": order.id},
            )

        order_status, issued_key_id = await self._issuing_service.issue_key_for_paid_order(order_id)
        if order_status == OrderStatus.PAID_BUT_NOT_ISSUED.value:
            await self._notification_service.send_admin_alert(
                f"Оплата по order #{order_id} подтверждена вручную, но ключ не выдан: нет доступного ключа."
            )
        return order_status, issued_key_id

    async def _resolve_payment_status(self, order) -> str:
        if order.payment_provider == "donate_stream":
            payment = await self._payments_repo.get_latest_by_order_id(order.id)
            if payment is not None:
                return payment.status
            return PaymentStatus.PENDING.value
        return await self._provider.get_payment_status(order.provider_payment_id)

    async def _release_reservation(self, order) -> None:
        if not order.reserved_key_id:
            return
        vpn_key = await self._vpn_keys_repo.lock_by_id(order.reserved_key_id)
        if vpn_key and vpn_key.status == KeyStatus.RESERVED.value and vpn_key.reserved_by_order_id == order.id:
            await self._vpn_keys_repo.release_reservation(vpn_key)
        order.reserved_key_id = None
        order.reservation_expires_at = None


def _amounts_match(expected, received) -> bool:
    try:
        return Decimal(str(expected)) == Decimal(str(received))
    except (InvalidOperation, ValueError):
        return False
