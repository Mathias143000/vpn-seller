from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import DeliveryJobStatus, Order, OrderFulfillmentMode, Plan, PlanProvisioningMode, User, VPNKey
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.delivery_jobs import DeliveryJobsRepository
from app.repositories.orders import OrdersRepository
from app.services.notifications import NotificationService
from app.services.security import KeyProtector
from app.services.transactions import transactional


class DeliveryService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        delivery_jobs_repo: DeliveryJobsRepository,
        orders_repo: OrdersRepository,
        audit_logs_repo: AuditLogsRepository,
        notification_service: NotificationService,
        key_protector: KeyProtector,
    ) -> None:
        self._session = session
        self._settings = settings
        self._delivery_jobs_repo = delivery_jobs_repo
        self._orders_repo = orders_repo
        self._audit_logs_repo = audit_logs_repo
        self._notification_service = notification_service
        self._key_protector = key_protector

    async def process_pending_jobs(self) -> int:
        now = datetime.now(tz=timezone.utc)
        async with transactional(self._session):
            jobs = await self._delivery_jobs_repo.claim_due_jobs(now=now)

        delivered = 0
        for job in jobs:
            job_id = job.id
            order_id = job.order_id
            attempts_count = job.attempts_count
            try:
                async with transactional(self._session):
                    order, user, plan, vpn_key = await self._load_delivery_payload(order_id)
                    key_value = self._key_protector.decrypt(vpn_key.key_value_encrypted)
                    external_ref = vpn_key.external_ref or ""
                    delivery_kind = self._detect_delivery_kind(order=order, plan=plan, external_ref=external_ref)
                    access_payload = (
                        self._parse_hiddify_payload(key_value)
                        if delivery_kind in {"hiddify", "mtproxy"}
                        else {}
                    )
                    if delivery_kind == "mtproxy" and access_payload:
                        mtproxy_links = access_payload.get("mtproxy_links") or [access_payload.get("subscription_url")]
                        key_value = "\n".join(str(link) for link in mtproxy_links if link)
                await self._notification_service.send_key_delivery(
                    user=user,
                    plan_name=plan.name,
                    key_value=key_value,
                    expires_at=vpn_key.expires_at,
                    delivery_kind=access_payload.get("delivery_kind", delivery_kind),
                    subscription_url=access_payload.get("subscription_url"),
                    panel_url=access_payload.get("panel_url"),
                    deeplink_url=access_payload.get("deeplink_url"),
                    included_countries=access_payload.get("included_countries"),
                )
                if delivery_kind != "mtproxy":
                    await self._notification_service.send_setup_guide(user)
                async with transactional(self._session):
                    locked_job = await self._delivery_jobs_repo.lock_by_id(job_id)
                    locked_order = await self._orders_repo.lock_by_id(order_id)
                    if locked_job is None:
                        continue
                    await self._delivery_jobs_repo.mark_delivered(locked_job, delivered_at=datetime.now(tz=timezone.utc))
                    if locked_order:
                        locked_order.delivery_status = DeliveryJobStatus.DELIVERED.value
                        locked_order.delivery_attempts += 1
                    await self._audit_logs_repo.add(
                        actor_user_id=None,
                        entity_type="delivery_job",
                        entity_id=str(job_id),
                        action="delivery_succeeded",
                        payload_json={"order_id": order_id, "delivery_job_id": job_id},
                    )
                delivered += 1
            except Exception as exc:
                async with transactional(self._session):
                    locked_job = await self._delivery_jobs_repo.lock_by_id(job_id)
                    locked_order = await self._orders_repo.lock_by_id(order_id)
                    if locked_job is None:
                        continue
                    reached_max_attempts = attempts_count + 1 >= self._settings.delivery_max_attempts
                    permanent_failure = isinstance(exc, InvalidToken)
                    if reached_max_attempts or permanent_failure:
                        await self._delivery_jobs_repo.mark_failed(locked_job, error_message=str(exc))
                        if locked_order:
                            locked_order.delivery_status = DeliveryJobStatus.FAILED.value
                            locked_order.delivery_attempts += 1
                    else:
                        await self._delivery_jobs_repo.mark_retry(
                            locked_job,
                            next_retry_at=datetime.now(tz=timezone.utc)
                            + timedelta(seconds=self._settings.delivery_retry_seconds),
                            error_message=str(exc),
                        )
                        if locked_order:
                            locked_order.delivery_status = DeliveryJobStatus.RETRY.value
                            locked_order.delivery_attempts += 1
                    await self._audit_logs_repo.add(
                        actor_user_id=None,
                        entity_type="delivery_job",
                        entity_id=str(job_id),
                        action="delivery_failed",
                        payload_json={"order_id": order_id, "delivery_job_id": job_id, "error": str(exc)},
                    )
        return delivered

    async def _load_delivery_payload(self, order_id: int) -> tuple[Order, User, Plan, VPNKey]:
        query = (
            select(Order, User, Plan, VPNKey)
            .join(User, User.id == Order.user_id)
            .join(Plan, Plan.id == Order.plan_id)
            .join(VPNKey, VPNKey.id == Order.issued_key_id)
            .where(Order.id == order_id)
        )
        row = (await self._session.execute(query)).one()
        return row[0], row[1], row[2], row[3]

    @staticmethod
    def _parse_hiddify_payload(payload: str) -> dict[str, Any]:
        try:
            parsed_json = json.loads(payload)
        except json.JSONDecodeError:
            parsed_json = None
        if isinstance(parsed_json, dict):
            return parsed_json

        parsed: dict[str, Any] = {}
        for line in payload.splitlines():
            if ":" not in line:
                continue
            label, value = line.split(":", maxsplit=1)
            normalized = label.strip().lower()
            if normalized == "hiddify import":
                parsed["deeplink_url"] = value.strip()
            elif normalized == "subscription":
                parsed["subscription_url"] = value.strip()
            elif normalized == "panel":
                parsed["panel_url"] = value.strip()
        return parsed

    @staticmethod
    def _detect_delivery_kind(*, order: Order, plan: Plan, external_ref: str) -> str:
        if external_ref.startswith("hiddify:") or external_ref.startswith("hiddify-superkey:"):
            return "hiddify"
        if external_ref.startswith("mtproxy:"):
            return "mtproxy"
        if (
            order.fulfillment_mode == OrderFulfillmentMode.MTPROXY.value
            or plan.provisioning_mode == PlanProvisioningMode.MTPROXY.value
        ):
            return "mtproxy"
        return "inventory"
