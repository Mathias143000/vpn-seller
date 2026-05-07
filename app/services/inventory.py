from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KeyStatus, OrderStatus
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.plans import PlansRepository
from app.repositories.vpn_keys import VPNKeysRepository
from app.services.transactions import transactional


class InventoryService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        orders_repo: OrdersRepository,
        vpn_keys_repo: VPNKeysRepository,
        plans_repo: PlansRepository,
        audit_logs_repo: AuditLogsRepository,
    ) -> None:
        self._session = session
        self._orders_repo = orders_repo
        self._vpn_keys_repo = vpn_keys_repo
        self._plans_repo = plans_repo
        self._audit_logs_repo = audit_logs_repo

    async def get_inventory_summary(self) -> list[dict]:
        return await self._plans_repo.list_with_stock()

    async def cleanup_expired_reservations(self) -> int:
        now = datetime.now(tz=timezone.utc)
        released = 0
        async with transactional(self._session):
            expired_orders = await self._orders_repo.list_expired_reservations(now)
            for order in expired_orders:
                locked_order = await self._orders_repo.lock_by_id(order.id)
                if locked_order is None:
                    continue
                if locked_order.status not in {OrderStatus.CREATED.value, OrderStatus.PENDING_PAYMENT.value}:
                    continue
                if not locked_order.reservation_expires_at or locked_order.reservation_expires_at >= now:
                    continue
                if locked_order.reserved_key_id:
                    vpn_key = await self._vpn_keys_repo.lock_by_id(locked_order.reserved_key_id)
                    if vpn_key and vpn_key.status == KeyStatus.RESERVED.value and vpn_key.reserved_by_order_id == locked_order.id:
                        await self._vpn_keys_repo.release_reservation(vpn_key)
                        released += 1
                locked_order.reserved_key_id = None
                locked_order.status = OrderStatus.EXPIRED_RESERVATION.value
                locked_order.failure_reason = "Reservation expired before payment confirmation."
                await self._audit_logs_repo.add(
                    actor_user_id=None,
                    entity_type="order",
                    entity_id=str(locked_order.id),
                    action="reservation_expired",
                    payload_json={"order_id": locked_order.id},
                )
        return released
