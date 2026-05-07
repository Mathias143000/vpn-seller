from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeliveryJobStatus, KeyStatus, OrderFulfillmentMode, OrderStatus, PlanProvisioningMode
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.delivery_jobs import DeliveryJobsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.plans import PlansRepository
from app.repositories.users import UsersRepository
from app.repositories.vpn_keys import VPNKeysRepository
from app.services.exceptions import InvalidStateError, NotFoundError, OutOfStockError, ProvisioningError
from app.services.hiddify import HiddifyAccessBundle, HiddifyService
from app.services.security import KeyProtector
from app.services.transactions import transactional


class IssuingService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        orders_repo: OrdersRepository,
        vpn_keys_repo: VPNKeysRepository,
        plans_repo: PlansRepository,
        users_repo: UsersRepository,
        delivery_jobs_repo: DeliveryJobsRepository,
        audit_logs_repo: AuditLogsRepository,
        hiddify: HiddifyService,
        key_protector: KeyProtector,
    ) -> None:
        self._session = session
        self._orders_repo = orders_repo
        self._vpn_keys_repo = vpn_keys_repo
        self._plans_repo = plans_repo
        self._users_repo = users_repo
        self._delivery_jobs_repo = delivery_jobs_repo
        self._audit_logs_repo = audit_logs_repo
        self._hiddify = hiddify
        self._key_protector = key_protector

    async def issue_key_for_paid_order(self, order_id: int) -> tuple[str, int | None]:
        now = datetime.now(tz=timezone.utc)
        hiddify_context: dict | None = None
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError(f"Order {order_id} not found")
            if order.status == OrderStatus.ISSUED.value:
                return order.status, order.issued_key_id
            if order.status not in {OrderStatus.PAID.value, OrderStatus.PAID_BUT_NOT_ISSUED.value}:
                raise InvalidStateError(f"Order {order_id} is not ready for issuing")

            plan = await self._plans_repo.get_by_id(order.plan_id)
            user = await self._users_repo.get_by_id(order.user_id)
            if plan is None or user is None:
                raise NotFoundError("Order dependencies not found")

            fulfillment_mode = self._resolve_order_fulfillment_mode(
                order_fulfillment_mode=order.fulfillment_mode,
                plan_provisioning_mode=plan.provisioning_mode,
            )
            if fulfillment_mode == OrderFulfillmentMode.INVENTORY.value:
                return await self._issue_from_inventory(
                    order_id=order.id,
                    user_id=order.user_id,
                    now=now,
                    audit_action="key_issued",
                )

            hiddify_context = {
                "order_id": order.id,
                "user": user,
                "plan_id": plan.id,
                "plan_name": plan.name,
                "duration_days": plan.duration_days,
                "amount_value": order.amount_value,
                "amount_currency": order.amount_currency,
                "preferred_server_id": order.preferred_hiddify_server_id,
                "strict_server_selection": fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SERVER.value
                and order.preferred_hiddify_server_id is not None,
                "is_superkey": fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
                "is_mtproxy": fulfillment_mode == OrderFulfillmentMode.MTPROXY.value,
            }

        if hiddify_context is None:
            raise InvalidStateError(f"Order {order_id} could not be issued")

        try:
            if hiddify_context["is_mtproxy"]:
                access = await self._hiddify.provision_mtproxy_for_order(
                    order_id=hiddify_context["order_id"],
                    user=hiddify_context["user"],
                    plan_name=hiddify_context["plan_name"],
                    duration_days=hiddify_context["duration_days"],
                    amount_value=hiddify_context["amount_value"],
                    amount_currency=hiddify_context["amount_currency"],
                    preferred_server_id=hiddify_context["preferred_server_id"],
                )
            elif hiddify_context["is_superkey"]:
                access = await self._hiddify.provision_superkey_for_order(
                    order_id=hiddify_context["order_id"],
                    user=hiddify_context["user"],
                    plan_name=hiddify_context["plan_name"],
                    duration_days=hiddify_context["duration_days"],
                    amount_value=hiddify_context["amount_value"],
                    amount_currency=hiddify_context["amount_currency"],
                )
            else:
                access = await self._hiddify.provision_for_order(
                    order_id=hiddify_context["order_id"],
                    user=hiddify_context["user"],
                    plan_name=hiddify_context["plan_name"],
                    duration_days=hiddify_context["duration_days"],
                    amount_value=hiddify_context["amount_value"],
                    amount_currency=hiddify_context["amount_currency"],
                    preferred_server_id=hiddify_context["preferred_server_id"],
                )
            return await self._finalize_hiddify_issue(
                order_id=hiddify_context["order_id"],
                plan_id=hiddify_context["plan_id"],
                user_id=hiddify_context["user"].id,
                access=access,
                now=now,
            )
        except ProvisioningError:
            if hiddify_context["is_mtproxy"]:
                return await self._mark_paid_but_not_issued(
                    order_id=hiddify_context["order_id"],
                    reason="Payment confirmed, but MTProxy could not be issued on the least-loaded server.",
                    action="issue_failed_mtproxy_unavailable",
                )
            if hiddify_context["is_superkey"]:
                return await self._mark_paid_but_not_issued(
                    order_id=hiddify_context["order_id"],
                    reason="Payment confirmed, but the superkey could not be assembled from all active servers.",
                    action="issue_failed_superkey_unavailable",
                )
            if hiddify_context["strict_server_selection"]:
                return await self._mark_paid_but_not_issued(
                    order_id=hiddify_context["order_id"],
                    reason="Payment confirmed, but the selected server is unavailable for issuing.",
                    action="issue_failed_selected_server_unavailable",
                )
            return await self._issue_from_inventory(
                order_id=hiddify_context["order_id"],
                user_id=hiddify_context["user"].id,
                now=now,
            )

    async def replace_access_for_issued_order(self, *, order_id: int, actor_user_id: int | None) -> int:
        now = datetime.now(tz=timezone.utc)
        replacement_context: dict | None = None
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError("Order not found")
            if order.status != OrderStatus.ISSUED.value or order.issued_key_id is None:
                raise InvalidStateError("Only issued orders can receive a replacement key")

            current_key = await self._vpn_keys_repo.lock_by_id(order.issued_key_id)
            if current_key is None:
                raise NotFoundError("Current issued key not found")

            plan = await self._plans_repo.get_by_id(order.plan_id)
            user = await self._users_repo.get_by_id(order.user_id)
            if plan is None or user is None:
                raise NotFoundError("Order dependencies not found")

            fulfillment_mode = self._resolve_order_fulfillment_mode(
                order_fulfillment_mode=order.fulfillment_mode,
                plan_provisioning_mode=plan.provisioning_mode,
            )
            if fulfillment_mode == OrderFulfillmentMode.INVENTORY.value:
                replacement_key = await self._vpn_keys_repo.get_next_available_for_plan(order.plan_id)
                if replacement_key is None:
                    raise OutOfStockError("No replacement keys are available")

                await self._vpn_keys_repo.issue_key(vpn_key=replacement_key, user_id=order.user_id, issued_at=now)
                await self._vpn_keys_repo.mark_broken(current_key)
                _, replacement_key_id = await self._finalize_order_issue(
                    order=order,
                    vpn_key_id=replacement_key.id,
                    audit_action="replacement_issued",
                    audit_payload={
                        "order_id": order.id,
                        "old_vpn_key_id": current_key.id,
                        "vpn_key_id": replacement_key.id,
                    },
                    actor_user_id=actor_user_id,
                )
                return replacement_key_id

            replacement_context = {
                "order_id": order.id,
                "old_key_id": current_key.id,
                "previous_server_id": order.preferred_hiddify_server_id,
                "user": user,
                "plan_id": plan.id,
                "plan_name": plan.name,
                "duration_days": plan.duration_days,
                "amount_value": order.amount_value,
                "amount_currency": order.amount_currency,
                "is_superkey": fulfillment_mode == OrderFulfillmentMode.HIDDIFY_SUPERKEY.value,
                "is_mtproxy": fulfillment_mode == OrderFulfillmentMode.MTPROXY.value,
            }

        if replacement_context is None:
            raise InvalidStateError(f"Order {order_id} could not receive a replacement key")

        issue_tag = self._replacement_issue_tag(old_key_id=replacement_context["old_key_id"], now=now)
        try:
            access = await self._provision_replacement_access(replacement_context, issue_tag=issue_tag)
        except Exception as exc:
            await self._audit_replacement_failed(
                order_id=replacement_context["order_id"],
                old_key_id=replacement_context["old_key_id"],
                actor_user_id=actor_user_id,
                error_message=str(exc),
            )
            if isinstance(exc, ProvisioningError):
                raise
            raise ProvisioningError(f"Replacement provisioning failed: {exc}") from exc

        _, replacement_key_id = await self._finalize_hiddify_issue(
            order_id=replacement_context["order_id"],
            plan_id=replacement_context["plan_id"],
            user_id=replacement_context["user"].id,
            access=access,
            now=now,
            replacement_key_id=replacement_context["old_key_id"],
            previous_server_id=replacement_context["previous_server_id"],
            actor_user_id=actor_user_id,
        )
        if replacement_key_id is None:
            raise InvalidStateError(f"Order {order_id} replacement did not produce a key")
        return replacement_key_id

    async def _provision_replacement_access(self, context: dict, *, issue_tag: str) -> HiddifyAccessBundle:
        if context["is_mtproxy"]:
            return await self._hiddify.provision_mtproxy_for_order(
                order_id=context["order_id"],
                user=context["user"],
                plan_name=context["plan_name"],
                duration_days=context["duration_days"],
                amount_value=context["amount_value"],
                amount_currency=context["amount_currency"],
                preferred_server_id=None,
                avoid_server_id=context["previous_server_id"],
                issue_tag=issue_tag,
            )
        if context["is_superkey"]:
            return await self._hiddify.provision_superkey_for_order(
                order_id=context["order_id"],
                user=context["user"],
                plan_name=context["plan_name"],
                duration_days=context["duration_days"],
                amount_value=context["amount_value"],
                amount_currency=context["amount_currency"],
                issue_tag=issue_tag,
            )
        return await self._hiddify.provision_for_order(
            order_id=context["order_id"],
            user=context["user"],
            plan_name=context["plan_name"],
            duration_days=context["duration_days"],
            amount_value=context["amount_value"],
            amount_currency=context["amount_currency"],
            preferred_server_id=None,
            avoid_server_id=context["previous_server_id"],
            issue_tag=issue_tag,
        )

    async def _audit_replacement_failed(
        self,
        *,
        order_id: int,
        old_key_id: int,
        actor_user_id: int | None,
        error_message: str,
    ) -> None:
        async with transactional(self._session):
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="order",
                entity_id=str(order_id),
                action="replacement_failed",
                payload_json={"order_id": order_id, "old_vpn_key_id": old_key_id, "error": error_message},
            )

    async def _issue_from_inventory(
        self,
        *,
        order_id: int,
        user_id: int,
        now: datetime,
        audit_action: str = "key_issued",
    ) -> tuple[str, int | None]:
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError(f"Order {order_id} not found")
            if order.status == OrderStatus.ISSUED.value:
                return order.status, order.issued_key_id

            vpn_key = None
            if order.reserved_key_id:
                reserved_key = await self._vpn_keys_repo.lock_by_id(order.reserved_key_id)
                if (
                    reserved_key is not None
                    and reserved_key.status == KeyStatus.RESERVED.value
                    and reserved_key.reserved_by_order_id == order.id
                ):
                    vpn_key = reserved_key

            if vpn_key is None:
                vpn_key = await self._vpn_keys_repo.get_next_available_for_plan(order.plan_id)

            if vpn_key is None:
                order.status = OrderStatus.PAID_BUT_NOT_ISSUED.value
                order.failure_reason = "Payment confirmed, but no stock or Hiddify capacity is available for issuing."
                await self._audit_logs_repo.add(
                    actor_user_id=None,
                    entity_type="order",
                    entity_id=str(order.id),
                    action="issue_failed_out_of_stock",
                    payload_json={"order_id": order.id},
                )
                return order.status, None

            await self._vpn_keys_repo.issue_key(vpn_key=vpn_key, user_id=user_id, issued_at=now)
            return await self._finalize_order_issue(order=order, vpn_key_id=vpn_key.id, audit_action=audit_action)

    async def _finalize_hiddify_issue(
        self,
        *,
        order_id: int,
        plan_id: int,
        user_id: int,
        access: HiddifyAccessBundle,
        now: datetime,
        replacement_key_id: int | None = None,
        previous_server_id: int | None = None,
        actor_user_id: int | None = None,
    ) -> tuple[str, int | None]:
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError(f"Order {order_id} not found")
            is_replacement = replacement_key_id is not None
            current_key = None
            if order.status == OrderStatus.ISSUED.value and not is_replacement:
                return order.status, order.issued_key_id
            if is_replacement:
                if order.status != OrderStatus.ISSUED.value or order.issued_key_id != replacement_key_id:
                    raise InvalidStateError(f"Order {order_id} was changed before replacement could be finalized")
                current_key = await self._vpn_keys_repo.lock_by_id(replacement_key_id)
                if current_key is None:
                    raise NotFoundError("Current issued key not found")
            elif order.status not in {OrderStatus.PAID.value, OrderStatus.PAID_BUT_NOT_ISSUED.value}:
                raise InvalidStateError(f"Order {order_id} is not ready for Hiddify issue")

            payload = self._hiddify.serialize_access_payload(access)
            if access.kind == "superkey":
                external_ref = f"hiddify-superkey:{access.remote_user_uuid}"
                comment = f"Superkey countries: {', '.join(access.included_countries)}"
                audit_action = "hiddify_superkey_issued"
            elif access.kind == "mtproxy":
                external_ref = f"mtproxy:{access.server_id}:{access.remote_user_uuid}"
                comment = f"MTProxy server: {access.server_name} ({access.country_name})"
                audit_action = "mtproxy_issued"
                if access.server_id is not None:
                    order.preferred_hiddify_server_id = access.server_id
            else:
                external_ref = f"hiddify:{access.server_id}:{access.remote_user_uuid}"
                comment = f"Hiddify server: {access.server_name} ({access.country_name})"
                audit_action = "hiddify_key_issued"
                if access.server_id is not None:
                    order.preferred_hiddify_server_id = access.server_id
            if is_replacement:
                audit_action = {
                    "superkey": "hiddify_superkey_replacement_issued",
                    "mtproxy": "mtproxy_replacement_issued",
                }.get(access.kind, "hiddify_server_replacement_issued")
            vpn_key = await self._vpn_keys_repo.create_generated_issued_key(
                plan_id=plan_id,
                key_value_encrypted=self._key_protector.encrypt(payload),
                key_fingerprint=self._key_protector.fingerprint(access.subscription_url),
                user_id=user_id,
                issued_at=now,
                expires_at=access.expires_at,
                external_ref=external_ref,
                comment=comment,
            )
            if is_replacement:
                if vpn_key.id == replacement_key_id:
                    raise ProvisioningError("Replacement returned the existing access instead of a new key.")
                await self._vpn_keys_repo.mark_broken(current_key)
            return await self._finalize_order_issue(
                order=order,
                vpn_key_id=vpn_key.id,
                audit_action=audit_action,
                audit_payload={
                    "order_id": order.id,
                    "vpn_key_id": vpn_key.id,
                    "old_vpn_key_id": replacement_key_id,
                    "delivery_kind": access.kind,
                    "server_id": access.server_id,
                    "previous_server_id": previous_server_id,
                    "remote_user_uuid": access.remote_user_uuid,
                    "countries": list(access.included_countries),
                },
                actor_user_id=actor_user_id,
            )

    async def _finalize_order_issue(
        self,
        *,
        order,
        vpn_key_id: int,
        audit_action: str,
        audit_payload: dict | None = None,
        actor_user_id: int | None = None,
    ) -> tuple[str, int]:
        order.issued_key_id = vpn_key_id
        order.reserved_key_id = None
        order.status = OrderStatus.ISSUED.value
        order.delivery_status = DeliveryJobStatus.PENDING.value
        order.failure_reason = None

        await self._delivery_jobs_repo.enqueue(
            order_id=order.id,
            user_id=order.user_id,
            job_type="deliver_issued_key",
            payload_json={"order_id": order.id, "issued_key_id": vpn_key_id},
            dedupe_key=f"issue:{order.id}:{vpn_key_id}",
        )
        await self._audit_logs_repo.add(
            actor_user_id=actor_user_id,
            entity_type="order",
            entity_id=str(order.id),
            action=audit_action,
            payload_json=audit_payload or {"order_id": order.id, "vpn_key_id": vpn_key_id},
        )
        return order.status, vpn_key_id

    @staticmethod
    def _replacement_issue_tag(*, old_key_id: int, now: datetime) -> str:
        return f"replacement-{old_key_id}-{int(now.timestamp() * 1_000_000)}"

    @staticmethod
    def _resolve_order_fulfillment_mode(*, order_fulfillment_mode: str | None, plan_provisioning_mode: str) -> str:
        if order_fulfillment_mode and order_fulfillment_mode != OrderFulfillmentMode.AUTO.value:
            return order_fulfillment_mode
        if plan_provisioning_mode == PlanProvisioningMode.MTPROXY.value:
            return OrderFulfillmentMode.MTPROXY.value
        if plan_provisioning_mode == PlanProvisioningMode.INVENTORY.value:
            return OrderFulfillmentMode.INVENTORY.value
        return OrderFulfillmentMode.HIDDIFY_SERVER.value

    async def _mark_paid_but_not_issued(self, *, order_id: int, reason: str, action: str) -> tuple[str, None]:
        async with transactional(self._session):
            order = await self._orders_repo.lock_by_id(order_id)
            if order is None:
                raise NotFoundError(f"Order {order_id} not found")
            if order.status == OrderStatus.ISSUED.value:
                return order.status, order.issued_key_id
            order.status = OrderStatus.PAID_BUT_NOT_ISSUED.value
            order.failure_reason = reason
            await self._audit_logs_repo.add(
                actor_user_id=None,
                entity_type="order",
                entity_id=str(order.id),
                action=action,
                payload_json={"order_id": order.id, "reason": reason},
            )
            return order.status, None
