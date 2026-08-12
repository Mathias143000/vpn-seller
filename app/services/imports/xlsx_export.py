from __future__ import annotations

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, Plan, User, VPNKey
from app.repositories.audit_logs import AuditLogsRepository
from app.services.security import KeyProtector
from app.services.transactions import transactional


class XlsxExportService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        key_protector: KeyProtector,
        audit_logs_repo: AuditLogsRepository,
    ) -> None:
        self._session = session
        self._key_protector = key_protector
        self._audit_logs_repo = audit_logs_repo

    async def export_inventory(
        self,
        *,
        status: str | None = None,
        plan_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        actor_user_id: int | None = None,
    ) -> bytes:
        async with transactional(self._session):
            query = (
                select(VPNKey, Plan, User, Order)
                .join(Plan, Plan.id == VPNKey.plan_id)
                .outerjoin(User, User.id == VPNKey.issued_to_user_id)
                .outerjoin(Order, Order.issued_key_id == VPNKey.id)
            )
            if status:
                query = query.where(VPNKey.status == status)
            if plan_code:
                query = query.where(Plan.code == plan_code)
            if created_from:
                query = query.where(VPNKey.created_at >= created_from)
            if created_to:
                query = query.where(VPNKey.created_at <= created_to)

            rows = (await self._session.execute(query)).all()
            content = self._build_inventory_workbook(rows)
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="export",
                entity_id="inventory",
                action="xlsx_inventory_exported",
                payload_json={
                    "rows_exported": len(rows),
                    "filters": {
                        "status": status,
                        "plan_code": plan_code,
                        "created_from": created_from.isoformat() if created_from else None,
                        "created_to": created_to.isoformat() if created_to else None,
                    },
                },
            )
        return content

    async def export_orders(
        self,
        *,
        status: str | None = None,
        actor_user_id: int | None = None,
    ) -> bytes:
        async with transactional(self._session):
            query = (
                select(Order, Plan, User, VPNKey)
                .join(Plan, Plan.id == Order.plan_id)
                .join(User, User.id == Order.user_id)
                .outerjoin(VPNKey, VPNKey.id == Order.issued_key_id)
                .order_by(Order.created_at.desc())
            )
            if status:
                query = query.where(Order.status == status)

            rows = (await self._session.execute(query)).all()
            content = self._build_orders_workbook(rows)
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="export",
                entity_id="orders",
                action="xlsx_orders_exported",
                payload_json={
                    "rows_exported": len(rows),
                    "filters": {"status": status},
                },
            )
        return content

    def _build_inventory_workbook(self, rows) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "keys"
        sheet.append(
            [
                "id",
                "plan_code",
                "status",
                "key_value",
                "external_ref",
                "issued_to_user",
                "order_id",
                "issued_at",
                "created_at",
                "key_type",
            ]
        )
        for vpn_key, plan, user, order in rows:
            sheet.append(
                [
                    vpn_key.id,
                    plan.code,
                    vpn_key.status,
                    self._key_protector.decrypt(vpn_key.key_value_encrypted),
                    vpn_key.external_ref,
                    (
                        user.whatsapp_phone
                        if user and user.whatsapp_phone
                        else (user.vk_user_id if user and user.vk_user_id else (user.telegram_user_id if user else None))
                    ),
                    order.id if order else None,
                    vpn_key.issued_at.isoformat() if vpn_key.issued_at else None,
                    vpn_key.created_at.isoformat() if vpn_key.created_at else None,
                    vpn_key.key_type,
                ]
            )

        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def _build_orders_workbook(self, rows) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "orders"
        sheet.append(
            [
                "id",
                "user_id",
                "username",
                "plan_code",
                "status",
                "payment_provider",
                "provider_payment_id",
                "amount_value",
                "amount_currency",
                "reserved_key_id",
                "issued_key_id",
                "issued_key_external_ref",
                "delivery_status",
                "delivery_attempts",
                "failure_reason",
                "created_at",
                "updated_at",
                "delivery_channel",
                "telegram_user_id",
                "vk_user_id",
                "whatsapp_phone",
            ]
        )
        for order, plan, user, vpn_key in rows:
            sheet.append(
                [
                    order.id,
                    order.user_id,
                    user.username,
                    plan.code,
                    order.status,
                    order.payment_provider,
                    order.provider_payment_id,
                    str(order.amount_value),
                    order.amount_currency,
                    order.reserved_key_id,
                    order.issued_key_id,
                    vpn_key.external_ref if vpn_key else None,
                    order.delivery_status,
                    order.delivery_attempts,
                    order.failure_reason,
                    order.created_at.isoformat() if order.created_at else None,
                    order.updated_at.isoformat() if order.updated_at else None,
                    user.delivery_channel,
                    user.telegram_user_id,
                    user.vk_user_id,
                    user.whatsapp_phone,
                ]
            )

        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
