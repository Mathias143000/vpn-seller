from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HiddifyServer, ServerHealthStatus, User, UserChannel
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.hiddify_servers import HiddifyServersRepository
from app.repositories.hiddify_usage_snapshots import HiddifyUsageSnapshotsRepository
from app.services.exceptions import NotFoundError, ProvisioningError
from app.services.security import KeyProtector
from app.services.transactions import transactional


@dataclass(slots=True)
class HiddifyAccessSource:
    server_id: int
    server_name: str
    country_name: str
    remote_user_uuid: str
    subscription_url: str
    panel_url: str
    deeplink_url: str


@dataclass(slots=True)
class HiddifyAccessBundle:
    server_id: int | None
    server_name: str
    country_name: str
    profile_name: str
    remote_user_uuid: str
    subscription_url: str
    panel_url: str | None
    deeplink_url: str
    expires_at: datetime
    kind: str = "single"
    included_countries: tuple[str, ...] = ()
    sources: tuple[HiddifyAccessSource, ...] = ()
    mtproxy_links: tuple[str, ...] = ()


@dataclass(slots=True)
class HiddifyConnectionInfo:
    panel_version: str | None
    checked_at: datetime


@dataclass(slots=True)
class HiddifyCountryOption:
    representative_server_id: int
    country_name: str
    servers_count: int


@dataclass(slots=True)
class HiddifyServerOption:
    server_id: int
    server_name: str
    country_name: str


@dataclass(slots=True)
class HiddifyServerLoad:
    server_id: int
    server_name: str
    country_name: str
    is_active: bool
    health_status: str
    total_users_count: int | None
    active_users_count: int | None
    active_users_percent: float | None
    checked_at: datetime | None
    last_used_at: datetime | None
    last_error: str | None
    mtproxy_available: bool
    total_current_usage_gb: float | None = None
    average_current_usage_gb: float | None = None
    average_monthly_usage_gb: float | None = None
    usage_sample_users_count: int = 0
    monthly_average_active_users_percent: float | None = None
    monthly_average_total_usage_gb: float | None = None
    monthly_average_user_usage_gb: float | None = None
    monthly_snapshots_count: int = 0
    monthly_latest_sampled_at: datetime | None = None
    selected_for_mtproxy: bool = False


class HiddifyService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings,
        hiddify_servers_repo: HiddifyServersRepository,
        usage_snapshots_repo: HiddifyUsageSnapshotsRepository,
        audit_logs_repo: AuditLogsRepository,
        key_protector: KeyProtector,
    ) -> None:
        self._session = session
        self._settings = settings
        self._hiddify_servers_repo = hiddify_servers_repo
        self._usage_snapshots_repo = usage_snapshots_repo
        self._audit_logs_repo = audit_logs_repo
        self._key_protector = key_protector

    async def has_active_servers(self, *, country_name: str | None = None) -> bool:
        return await self._hiddify_servers_repo.has_active_server(country_name=country_name)

    async def list_servers(self) -> list[HiddifyServer]:
        return await self._hiddify_servers_repo.list_all()

    async def list_available_servers(self) -> list[HiddifyServerOption]:
        servers = await self._hiddify_servers_repo.list_active()
        return [
            HiddifyServerOption(
                server_id=server.id,
                server_name=server.name,
                country_name=self._normalize_country_name(server.country_name),
            )
            for server in servers
        ]

    async def list_available_countries(self) -> list[HiddifyCountryOption]:
        by_country: dict[str, HiddifyCountryOption] = {}
        for server in await self._hiddify_servers_repo.list_active():
            country_name = self._normalize_country_name(server.country_name)
            existing = by_country.get(country_name)
            if existing is None:
                by_country[country_name] = HiddifyCountryOption(
                    representative_server_id=server.id,
                    country_name=country_name,
                    servers_count=1,
                )
            else:
                existing.servers_count += 1
        return [by_country[key] for key in sorted(by_country)]

    async def has_superkey_capacity(self) -> bool:
        return len(await self.list_available_servers()) >= 2

    async def get_server(self, server_id: int) -> HiddifyServer:
        server = await self._hiddify_servers_repo.get_by_id(server_id)
        if server is None:
            raise NotFoundError("Hiddify server not found.")
        return server

    async def get_active_server_choice(self, server_id: int) -> HiddifyServer:
        server = await self.get_server(server_id)
        if not server.is_active:
            raise NotFoundError("Selected Hiddify server is not active right now.")
        return server

    async def list_server_load(self) -> list[HiddifyServerLoad]:
        snapshots: list[HiddifyServerLoad] = []
        async with transactional(self._session):
            servers = await self._hiddify_servers_repo.list_all()
        for server in servers:
            if not server.is_active:
                snapshots.append(
                    HiddifyServerLoad(
                        server_id=server.id,
                        server_name=server.name,
                        country_name=self._normalize_country_name(server.country_name),
                        is_active=server.is_active,
                        health_status=server.last_health_status,
                        total_users_count=None,
                        active_users_count=None,
                        active_users_percent=None,
                        checked_at=server.last_healthcheck_at,
                        last_used_at=server.last_used_at,
                        last_error=server.last_error,
                        mtproxy_available=False,
                    )
                )
                continue
            snapshots.append(await self._measure_server_load(server))

        await self._attach_monthly_usage_summaries(snapshots)
        candidates = [snapshot for snapshot in snapshots if snapshot.active_users_count is not None]
        if candidates:
            fallback_time = datetime.min.replace(tzinfo=timezone.utc)
            selected = min(
                candidates,
                key=lambda item: (item.active_users_count or 0, item.last_used_at or fallback_time, item.server_id),
            )
            selected.selected_for_mtproxy = True
        return snapshots

    async def collect_usage_snapshots(
        self,
        *,
        server_id: int | None = None,
        now: datetime | None = None,
    ) -> list[HiddifyServerLoad]:
        sampled_at = now or datetime.now(tz=timezone.utc)
        if sampled_at.tzinfo is None:
            sampled_at = sampled_at.replace(tzinfo=timezone.utc)
        async with transactional(self._session):
            if server_id is not None:
                server = await self.get_server(server_id)
                servers = [server] if server.is_active else []
            else:
                servers = await self._hiddify_servers_repo.list_active()

        snapshots: list[HiddifyServerLoad] = []
        for server in servers:
            load = await self._measure_server_load(server, checked_at=sampled_at)
            async with transactional(self._session):
                await self._usage_snapshots_repo.create(
                    server_id=load.server_id,
                    sampled_at=sampled_at,
                    total_users_count=load.total_users_count,
                    active_users_count=load.active_users_count,
                    active_users_percent=load.active_users_percent,
                    total_current_usage_gb=load.total_current_usage_gb,
                    average_user_usage_gb=load.average_current_usage_gb,
                    usage_sample_users_count=load.usage_sample_users_count,
                    health_status=load.health_status,
                    error_message=load.last_error,
                )
            snapshots.append(load)

        await self._attach_monthly_usage_summaries(snapshots, now=sampled_at)
        return snapshots

    async def select_least_loaded_server_by_active_users(self, *, avoid_server_id: int | None = None) -> HiddifyServer:
        async with transactional(self._session):
            servers = await self._hiddify_servers_repo.list_active()
        if not servers:
            raise ProvisioningError("No active servers are available.")

        measured: list[tuple[int, datetime, int, HiddifyServer]] = []
        fallback_time = datetime.min.replace(tzinfo=timezone.utc)
        for server in servers:
            snapshot = await self._measure_server_load(server)
            if snapshot.active_users_count is not None:
                measured.append((snapshot.active_users_count, server.last_used_at or fallback_time, server.id, server))

        if not measured:
            raise ProvisioningError("Could not measure active users on any server.")
        if avoid_server_id is not None and len(measured) > 1:
            alternatives = [item for item in measured if item[2] != avoid_server_id]
            if alternatives:
                measured = alternatives
        return min(measured, key=lambda item: (item[0], item[1], item[2]))[3]

    async def _attach_monthly_usage_summaries(
        self,
        snapshots: list[HiddifyServerLoad],
        *,
        now: datetime | None = None,
    ) -> None:
        if not snapshots:
            return
        window_days = max(int(getattr(self._settings, "hiddify_usage_monthly_window_days", 30)), 1)
        checked_at = now or datetime.now(tz=timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        since = checked_at - timedelta(days=window_days)
        async with transactional(self._session):
            summaries = await self._usage_snapshots_repo.monthly_summary_by_server_ids(
                server_ids=[snapshot.server_id for snapshot in snapshots],
                since=since,
            )
        for snapshot in snapshots:
            summary = summaries.get(snapshot.server_id)
            if summary is None:
                continue
            snapshot.monthly_average_active_users_percent = summary.average_active_users_percent
            snapshot.monthly_average_total_usage_gb = summary.average_total_current_usage_gb
            snapshot.monthly_average_user_usage_gb = summary.average_user_usage_gb
            snapshot.monthly_snapshots_count = summary.snapshots_count
            snapshot.monthly_latest_sampled_at = summary.latest_sampled_at

    async def register_server(
        self,
        *,
        name: str,
        country_name: str,
        base_url: str,
        admin_proxy_path: str,
        client_proxy_path: str,
        api_key: str,
        actor_user_id: int | None,
        is_active: bool = True,
    ) -> HiddifyServer:
        normalized_base = base_url.rstrip("/")
        normalized_admin_path = admin_proxy_path.strip("/")
        normalized_client_path = client_proxy_path.strip("/")

        connection = await self._check_connection(
            base_url=normalized_base,
            admin_proxy_path=normalized_admin_path,
            api_key=api_key,
        )
        encrypted_key = self._key_protector.encrypt(api_key)
        async with transactional(self._session):
            server = await self._hiddify_servers_repo.create(
                name=name.strip(),
                country_name=self._normalize_country_name(country_name),
                base_url=normalized_base,
                admin_proxy_path=normalized_admin_path,
                client_proxy_path=normalized_client_path,
                api_key_encrypted=encrypted_key,
                is_active=is_active,
                panel_version=connection.panel_version,
                last_health_status=ServerHealthStatus.HEALTHY.value,
                last_healthcheck_at=connection.checked_at,
            )
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="hiddify_server",
                entity_id=str(server.id),
                action="hiddify_server_added",
                payload_json={
                    "name": server.name,
                    "country_name": server.country_name,
                    "base_url": server.base_url,
                    "admin_proxy_path": server.admin_proxy_path,
                    "client_proxy_path": server.client_proxy_path,
                    "is_active": server.is_active,
                    "panel_version": server.panel_version,
                },
            )
        return server

    async def toggle_server(self, *, server_id: int, actor_user_id: int | None) -> HiddifyServer:
        async with transactional(self._session):
            server = await self._hiddify_servers_repo.lock_by_id(server_id)
            if server is None:
                raise NotFoundError("Hiddify server not found.")
            await self._hiddify_servers_repo.set_active(server, is_active=not server.is_active)
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="hiddify_server",
                entity_id=str(server.id),
                action="hiddify_server_toggled",
                payload_json={"is_active": server.is_active},
            )
            return server

    async def refresh_server(self, *, server_id: int, actor_user_id: int | None) -> HiddifyServer:
        server = await self._hiddify_servers_repo.get_by_id(server_id)
        if server is None:
            raise NotFoundError("Hiddify server not found.")
        api_key = self._key_protector.decrypt(server.api_key_encrypted)
        checked_at = datetime.now(tz=timezone.utc)
        try:
            connection = await self._check_connection(
                base_url=server.base_url,
                admin_proxy_path=server.admin_proxy_path,
                api_key=api_key,
            )
            status = ServerHealthStatus.HEALTHY.value
            error_message = None
            panel_version = connection.panel_version
            checked_at = connection.checked_at
        except Exception as exc:
            status = ServerHealthStatus.UNHEALTHY.value
            error_message = str(exc)
            panel_version = server.panel_version

        async with transactional(self._session):
            locked_server = await self._hiddify_servers_repo.lock_by_id(server_id)
            if locked_server is None:
                raise NotFoundError("Hiddify server not found.")
            await self._hiddify_servers_repo.set_health(
                locked_server,
                status=status,
                checked_at=checked_at,
                panel_version=panel_version,
                error_message=error_message,
            )
            await self._audit_logs_repo.add(
                actor_user_id=actor_user_id,
                entity_type="hiddify_server",
                entity_id=str(locked_server.id),
                action="hiddify_server_checked",
                payload_json={"status": status, "error": error_message, "panel_version": panel_version},
            )
            return locked_server

    async def provision_for_order(
        self,
        *,
        order_id: int,
        user: User,
        plan_name: str,
        duration_days: int,
        amount_value: Decimal | str,
        amount_currency: str,
        preferred_server_id: int | None = None,
        avoid_server_id: int | None = None,
        issue_tag: str | None = None,
    ) -> HiddifyAccessBundle:
        servers = await self._select_servers_for_issue(
            preferred_server_id=preferred_server_id,
            avoid_server_id=avoid_server_id,
        )
        if not servers:
            raise ProvisioningError("No active Hiddify servers are available.")

        order_hint = self._build_order_hint(order_id, issue_tag=issue_tag)
        errors: list[str] = []

        for server in servers:
            try:
                access_source = await self._provision_single_on_server(
                    server=server,
                    order_id=order_id,
                    order_hint=order_hint,
                    user=user,
                    plan_name=plan_name,
                    duration_days=duration_days,
                    amount_value=amount_value,
                    amount_currency=amount_currency,
                    issue_tag=issue_tag,
                )
                return HiddifyAccessBundle(
                    server_id=access_source.server_id,
                    server_name=access_source.server_name,
                    country_name=access_source.country_name,
                    profile_name=self._build_remote_name(order_hint=order_hint, user=user, plan_name=plan_name),
                    remote_user_uuid=access_source.remote_user_uuid,
                    subscription_url=access_source.subscription_url,
                    panel_url=access_source.panel_url,
                    deeplink_url=access_source.deeplink_url,
                    expires_at=datetime.now(tz=timezone.utc).replace(microsecond=0) + timedelta(days=duration_days),
                    included_countries=(access_source.country_name,),
                    sources=(access_source,),
                )
            except Exception as exc:
                errors.append(f"{server.name}: {exc}")
                await self._mark_server_unhealthy(server.id, str(exc))

        raise ProvisioningError("Failed to provision Hiddify access: " + "; ".join(errors))

    async def provision_mtproxy_for_order(
        self,
        *,
        order_id: int,
        user: User,
        plan_name: str,
        duration_days: int,
        amount_value: Decimal | str,
        amount_currency: str,
        preferred_server_id: int | None = None,
        avoid_server_id: int | None = None,
        issue_tag: str | None = None,
    ) -> HiddifyAccessBundle:
        server = (
            await self.get_active_server_choice(preferred_server_id)
            if preferred_server_id is not None
            else await self.select_least_loaded_server_by_active_users(avoid_server_id=avoid_server_id)
        )
        order_hint = self._build_order_hint(order_id, issue_tag=issue_tag)
        mtproxy_plan_name = f"{plan_name} MTProto"
        try:
            access_source = await self._provision_single_on_server(
                server=server,
                order_id=order_id,
                order_hint=order_hint,
                user=user,
                plan_name=mtproxy_plan_name,
                duration_days=duration_days,
                amount_value=amount_value,
                amount_currency=amount_currency,
                issue_tag=issue_tag,
            )
            mtproxy_links = await self._fetch_mtproxy_links(
                server=server,
                remote_user_uuid=access_source.remote_user_uuid,
            )
        except Exception as exc:
            await self._mark_server_unhealthy(server.id, str(exc))
            if isinstance(exc, ProvisioningError):
                raise
            raise ProvisioningError(f"Failed to provision MTProxy access on {server.name}: {exc}") from exc
        profile_name = self._build_remote_name(order_hint=order_hint, user=user, plan_name=mtproxy_plan_name)
        first_link = mtproxy_links[0]
        return HiddifyAccessBundle(
            server_id=access_source.server_id,
            server_name=access_source.server_name,
            country_name=access_source.country_name,
            profile_name=profile_name,
            remote_user_uuid=access_source.remote_user_uuid,
            subscription_url=first_link,
            panel_url=access_source.panel_url,
            deeplink_url=first_link,
            expires_at=datetime.now(tz=timezone.utc).replace(microsecond=0) + timedelta(days=duration_days),
            kind="mtproxy",
            included_countries=(access_source.country_name,),
            sources=(access_source,),
            mtproxy_links=tuple(mtproxy_links),
        )

    async def provision_superkey_for_order(
        self,
        *,
        order_id: int,
        user: User,
        plan_name: str,
        duration_days: int,
        amount_value: Decimal | str,
        amount_currency: str,
        issue_tag: str | None = None,
    ) -> HiddifyAccessBundle:
        selected_servers = await self._select_servers_for_superkey()
        if len(selected_servers) < 2:
            raise ProvisioningError("Superkey requires at least two active servers.")

        order_hint = self._build_order_hint(order_id, issue_tag=issue_tag)
        superkey_plan_name = f"{plan_name} Superkey"
        profile_name = self._build_remote_name(order_hint=order_hint, user=user, plan_name=superkey_plan_name)
        sources: list[HiddifyAccessSource] = []
        errors: list[str] = []

        for server in selected_servers:
            try:
                sources.append(
                    await self._provision_single_on_server(
                        server=server,
                        order_id=order_id,
                        order_hint=order_hint,
                        user=user,
                        plan_name=superkey_plan_name,
                        duration_days=duration_days,
                        amount_value=amount_value,
                        amount_currency=amount_currency,
                        issue_tag=issue_tag,
                    )
                )
            except Exception as exc:
                errors.append(f"{server.country_name}/{server.name}: {exc}")
                await self._mark_server_unhealthy(server.id, str(exc))

        if errors:
            raise ProvisioningError("Failed to build superkey: " + "; ".join(errors))

        countries = tuple(dict.fromkeys(source.country_name for source in sources))
        aggregate_token = self._build_superkey_token(order_id, issue_tag=issue_tag)
        aggregate_subscription_url = self._build_aggregate_subscription_url(aggregate_token)
        return HiddifyAccessBundle(
            server_id=None,
            server_name="Superkey Aggregator",
            country_name=", ".join(countries),
            profile_name=profile_name,
            remote_user_uuid=aggregate_token,
            subscription_url=aggregate_subscription_url,
            panel_url=None,
            deeplink_url=self._build_deeplink(aggregate_subscription_url, profile_name=profile_name),
            expires_at=datetime.now(tz=timezone.utc).replace(microsecond=0) + timedelta(days=duration_days),
            kind="superkey",
            included_countries=countries,
            sources=tuple(sources),
        )

    def serialize_access_payload(self, bundle: HiddifyAccessBundle) -> str:
        delivery_kind = "hiddify"
        if bundle.kind == "superkey":
            delivery_kind = "hiddify_superkey"
        elif bundle.kind == "mtproxy":
            delivery_kind = "mtproxy"
        payload = {
            "delivery_kind": delivery_kind,
            "profile_name": bundle.profile_name,
            "subscription_url": bundle.subscription_url,
            "panel_url": bundle.panel_url,
            "deeplink_url": bundle.deeplink_url,
            "included_countries": list(bundle.included_countries or (bundle.country_name,)),
            "sources": [asdict(source) for source in bundle.sources],
            "mtproxy_links": list(bundle.mtproxy_links),
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _select_servers_for_issue(
        self,
        *,
        preferred_server_id: int | None,
        avoid_server_id: int | None = None,
    ) -> list[HiddifyServer]:
        if preferred_server_id is None:
            return self._prefer_alternatives(
                await self._hiddify_servers_repo.list_active(),
                avoid_server_id=avoid_server_id,
            )

        preferred = await self.get_active_server_choice(preferred_server_id)
        same_country = await self._hiddify_servers_repo.list_active(country_name=preferred.country_name)
        ordered = [preferred]
        ordered.extend(server for server in same_country if server.id != preferred.id)
        return ordered

    async def _select_servers_for_superkey(self) -> list[HiddifyServer]:
        return await self._hiddify_servers_repo.list_active()

    async def _provision_single_on_server(
        self,
        *,
        server: HiddifyServer,
        order_id: int,
        order_hint: str,
        user: User,
        plan_name: str,
        duration_days: int,
        amount_value: Decimal | str,
        amount_currency: str,
        issue_tag: str | None = None,
    ) -> HiddifyAccessSource:
        api_key = self._key_protector.decrypt(server.api_key_encrypted)
        remote_user = await self._ensure_remote_user(
            server=server,
            api_key=api_key,
            order_id=order_id,
            order_hint=order_hint,
            user=user,
            plan_name=plan_name,
            duration_days=duration_days,
            amount_value=amount_value,
            amount_currency=amount_currency,
            issue_tag=issue_tag,
        )
        remote_user_uuid = str(remote_user.get("uuid") or "").strip()
        if not remote_user_uuid:
            raise ProvisioningError("Hiddify did not return a user UUID.")

        profile_name = str(
            remote_user.get("name")
            or self._build_remote_name(order_hint=order_hint, user=user, plan_name=plan_name)
        ).strip()
        subscription_url = self._build_subscription_url(server, remote_user_uuid)
        panel_url = self._build_panel_url(server, remote_user_uuid, profile_name=profile_name)
        deeplink_url = self._build_deeplink(subscription_url, profile_name=profile_name)

        now = datetime.now(tz=timezone.utc)
        async with transactional(self._session):
            locked_server = await self._hiddify_servers_repo.lock_by_id(server.id)
            if locked_server is None:
                raise NotFoundError("Hiddify server disappeared during provisioning.")
            await self._hiddify_servers_repo.set_health(
                locked_server,
                status=ServerHealthStatus.HEALTHY.value,
                checked_at=now,
                error_message=None,
            )
            await self._hiddify_servers_repo.touch_used(locked_server, used_at=now)

        return HiddifyAccessSource(
            server_id=server.id,
            server_name=server.name,
            country_name=self._normalize_country_name(server.country_name),
            remote_user_uuid=remote_user_uuid,
            subscription_url=subscription_url,
            panel_url=panel_url,
            deeplink_url=deeplink_url,
        )

    async def _measure_server_load(self, server: HiddifyServer, *, checked_at: datetime | None = None) -> HiddifyServerLoad:
        checked_at = checked_at or datetime.now(tz=timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        try:
            api_key = self._key_protector.decrypt(server.api_key_encrypted)
            async with self._client() as client:
                users = await self._list_remote_users(client=client, server=server, api_key=api_key)
            active_users_count = sum(1 for item in users if self._remote_user_counts_as_active(item))
            total_users_count = len(users)
            active_users_percent = (active_users_count / total_users_count * 100) if total_users_count else 0.0
            usage_stats = self._build_remote_usage_stats(users, checked_at=checked_at)
            await self._mark_server_healthy(server.id, checked_at=checked_at)
            return HiddifyServerLoad(
                server_id=server.id,
                server_name=server.name,
                country_name=self._normalize_country_name(server.country_name),
                is_active=server.is_active,
                health_status=ServerHealthStatus.HEALTHY.value,
                total_users_count=total_users_count,
                active_users_count=active_users_count,
                active_users_percent=round(active_users_percent, 1),
                checked_at=checked_at,
                last_used_at=server.last_used_at,
                last_error=None,
                mtproxy_available=True,
                total_current_usage_gb=usage_stats["total_current_usage_gb"],
                average_current_usage_gb=usage_stats["average_current_usage_gb"],
                average_monthly_usage_gb=usage_stats["average_monthly_usage_gb"],
                usage_sample_users_count=usage_stats["usage_sample_users_count"],
            )
        except Exception as exc:
            error_message = str(exc)
            await self._mark_server_unhealthy(server.id, error_message, checked_at=checked_at)
            return HiddifyServerLoad(
                server_id=server.id,
                server_name=server.name,
                country_name=self._normalize_country_name(server.country_name),
                is_active=server.is_active,
                health_status=ServerHealthStatus.UNHEALTHY.value,
                total_users_count=None,
                active_users_count=None,
                active_users_percent=None,
                checked_at=checked_at,
                last_used_at=server.last_used_at,
                last_error=error_message,
                mtproxy_available=False,
            )

    async def _mark_server_healthy(self, server_id: int, *, checked_at: datetime) -> None:
        async with transactional(self._session):
            locked_server = await self._hiddify_servers_repo.lock_by_id(server_id)
            if locked_server is None:
                return
            await self._hiddify_servers_repo.set_health(
                locked_server,
                status=ServerHealthStatus.HEALTHY.value,
                checked_at=checked_at,
                error_message=None,
            )

    async def _mark_server_unhealthy(
        self,
        server_id: int,
        error_message: str,
        *,
        checked_at: datetime | None = None,
    ) -> None:
        async with transactional(self._session):
            locked_server = await self._hiddify_servers_repo.lock_by_id(server_id)
            if locked_server is None:
                return
            await self._hiddify_servers_repo.set_health(
                locked_server,
                status=ServerHealthStatus.UNHEALTHY.value,
                checked_at=checked_at or datetime.now(tz=timezone.utc),
                error_message=error_message,
            )

    async def _check_connection(self, *, base_url: str, admin_proxy_path: str, api_key: str) -> HiddifyConnectionInfo:
        async with self._client() as client:
            await self._request_json(
                client=client,
                method="GET",
                url=self._panel_api_url(base_url, admin_proxy_path, "panel/ping/"),
                api_key=api_key,
            )
            panel_info = await self._request_json(
                client=client,
                method="GET",
                url=self._panel_api_url(base_url, admin_proxy_path, "panel/info/"),
                api_key=api_key,
            )
        return HiddifyConnectionInfo(
            panel_version=panel_info.get("version"),
            checked_at=datetime.now(tz=timezone.utc),
        )

    async def _ensure_remote_user(
        self,
        *,
        server: HiddifyServer,
        api_key: str,
        order_id: int,
        order_hint: str,
        user: User,
        plan_name: str,
        duration_days: int,
        amount_value: Decimal | str,
        amount_currency: str,
        issue_tag: str | None = None,
    ) -> dict[str, Any]:
        issue_marker = self._issue_marker(order_id, issue_tag=issue_tag)
        payload = {
            "name": self._build_remote_name(order_hint=order_hint, user=user, plan_name=plan_name),
            "package_days": duration_days,
            "usage_limit_GB": 1000,
            "mode": "no_reset",
            "enable": True,
            "start_date": date.today().isoformat(),
            "comment": (
                f"vpn-seller order access; {issue_marker}; user={self._user_marker(user)}; "
                f"plan={plan_name}; amount={amount_value} {amount_currency}"
            ),
        }
        if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.TELEGRAM.value:
            payload["telegram_id"] = user.telegram_user_id

        async with self._client() as client:
            existing = await self._find_remote_user(
                client=client,
                server=server,
                api_key=api_key,
                order_id=order_id,
                order_hint=order_hint,
                issue_tag=issue_tag,
                telegram_user_id=user.telegram_user_id
                if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.TELEGRAM.value
                else None,
            )
            if existing is not None:
                return existing

            created = await self._request_json(
                client=client,
                method="POST",
                url=self._server_api_url(server, "admin/user/"),
                api_key=api_key,
                json_payload=payload,
            )
            created_uuid = str(created.get("uuid") or "").strip()
            if created_uuid:
                return created

            existing = await self._find_remote_user(
                client=client,
                server=server,
                api_key=api_key,
                order_id=order_id,
                order_hint=order_hint,
                issue_tag=issue_tag,
                telegram_user_id=user.telegram_user_id
                if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.TELEGRAM.value
                else None,
            )
            if existing is not None:
                return existing

        raise ProvisioningError("Hiddify created a user, but the user record could not be resolved.")

    async def _find_remote_user(
        self,
        *,
        client: httpx.AsyncClient,
        server: HiddifyServer,
        api_key: str,
        order_id: int,
        order_hint: str,
        issue_tag: str | None,
        telegram_user_id: int | None,
    ) -> dict[str, Any] | None:
        users = await self._list_remote_users(client=client, server=server, api_key=api_key)
        order_marker = self._issue_marker(order_id, issue_tag=issue_tag)
        for remote_user in users:
            comment = str(remote_user.get("comment") or "")
            if order_marker in comment:
                return remote_user
        if telegram_user_id is None:
            return None
        for remote_user in users:
            name = str(remote_user.get("name") or "")
            remote_telegram_id = remote_user.get("telegram_id")
            if str(remote_telegram_id) == str(telegram_user_id) and name.endswith(order_hint):
                return remote_user
        return None

    async def _list_remote_users(
        self,
        *,
        client: httpx.AsyncClient,
        server: HiddifyServer,
        api_key: str,
    ) -> list[dict[str, Any]]:
        response = await client.get(
            self._server_api_url(server, "admin/user/"),
            headers={"Hiddify-API-Key": api_key},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ProvisioningError("Hiddify returned an unexpected user list payload.")
        return [item for item in payload if isinstance(item, dict)]

    async def _count_active_remote_users(self, server: HiddifyServer) -> int:
        api_key = self._key_protector.decrypt(server.api_key_encrypted)
        async with self._client() as client:
            users = await self._list_remote_users(client=client, server=server, api_key=api_key)
        return sum(1 for item in users if self._remote_user_counts_as_active(item))

    @classmethod
    def _build_remote_usage_stats(cls, users: list[dict[str, Any]], *, checked_at: datetime) -> dict[str, float | int | None]:
        total_current_usage_gb = 0.0
        current_values: list[float] = []
        monthly_values: list[float] = []
        for remote_user in users:
            usage_gb = cls._extract_usage_gb(remote_user)
            if usage_gb is None:
                continue
            total_current_usage_gb += usage_gb
            current_values.append(usage_gb)
            monthly_values.append(cls._estimate_monthly_usage_gb(remote_user, usage_gb=usage_gb, checked_at=checked_at))

        if not current_values:
            return {
                "total_current_usage_gb": None,
                "average_current_usage_gb": None,
                "average_monthly_usage_gb": None,
                "usage_sample_users_count": 0,
            }
        return {
            "total_current_usage_gb": round(total_current_usage_gb, 2),
            "average_current_usage_gb": round(sum(current_values) / len(current_values), 2),
            "average_monthly_usage_gb": round(sum(monthly_values) / len(monthly_values), 2),
            "usage_sample_users_count": len(current_values),
        }

    @classmethod
    def _estimate_monthly_usage_gb(
        cls,
        remote_user: dict[str, Any],
        *,
        usage_gb: float,
        checked_at: datetime,
    ) -> float:
        mode = str(remote_user.get("mode") or "").lower()
        if "month" in mode:
            return usage_gb

        start_at = cls._parse_remote_datetime(
            remote_user.get("start_date")
            or remote_user.get("created_at")
            or remote_user.get("created")
            or remote_user.get("added_at")
        )
        if start_at is None:
            return usage_gb

        elapsed_days = max((checked_at - start_at).total_seconds() / 86400, 1.0)
        elapsed_months = max(elapsed_days / 30.0, 1.0)
        return usage_gb / elapsed_months

    @staticmethod
    def _extract_usage_gb(remote_user: dict[str, Any]) -> float | None:
        gb_keys = (
            "current_usage_GB",
            "current_usage_gb",
            "usage_GB",
            "usage_gb",
            "used_GB",
            "used_gb",
            "used_traffic_GB",
            "used_traffic_gb",
        )
        for key in gb_keys:
            parsed = HiddifyService._parse_float(remote_user.get(key))
            if parsed is not None:
                return max(parsed, 0.0)

        byte_keys = (
            "current_usage_bytes",
            "usage_bytes",
            "used_bytes",
            "used_traffic_bytes",
            "transfer_bytes",
        )
        for key in byte_keys:
            parsed = HiddifyService._parse_float(remote_user.get(key))
            if parsed is not None:
                return max(parsed, 0.0) / 1024**3

        upload = HiddifyService._parse_float(remote_user.get("upload_bytes"))
        download = HiddifyService._parse_float(remote_user.get("download_bytes"))
        if upload is not None or download is not None:
            return max((upload or 0.0) + (download or 0.0), 0.0) / 1024**3
        return None

    async def _fetch_mtproxy_links(self, *, server: HiddifyServer, remote_user_uuid: str) -> list[str]:
        async with self._client() as client:
            response = await client.get(
                self._user_api_url(server, "mtproxies/"),
                headers={"Hiddify-API-Key": remote_user_uuid},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ProvisioningError("Hiddify returned an unexpected MTProxy payload.")
        links = [
            str(item.get("link") or "").strip()
            for item in payload
            if isinstance(item, dict) and str(item.get("link") or "").strip()
        ]
        if not links:
            raise ProvisioningError("Hiddify did not return MTProxy links for the issued user.")
        return links

    @staticmethod
    def _remote_user_counts_as_active(remote_user: dict[str, Any]) -> bool:
        if remote_user.get("enable") is False:
            return False
        if remote_user.get("is_active") is False:
            return False
        return True

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip().replace(",", ".")
            if not normalized:
                return None
            try:
                return float(normalized)
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_remote_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        elif isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    @staticmethod
    def _user_marker(user: User) -> str:
        if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.WHATSAPP.value and user.whatsapp_phone:
            return f"whatsapp:{user.whatsapp_phone}"
        if getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.VK.value and user.vk_user_id:
            return f"vk:{user.vk_user_id}"
        return f"telegram:{user.telegram_user_id}"

    async def _request_json(
        self,
        *,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        api_key: str,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await client.request(
            method,
            url,
            headers={"Hiddify-API-Key": api_key},
            json=json_payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ProvisioningError("Hiddify returned an unexpected payload.")
        return data

    @staticmethod
    def _panel_api_url(base_url: str, admin_proxy_path: str, suffix: str) -> str:
        return f"{base_url.rstrip('/')}/{admin_proxy_path.strip('/')}/api/v2/{suffix.lstrip('/')}"

    def _server_api_url(self, server: HiddifyServer, suffix: str) -> str:
        return self._panel_api_url(server.base_url, server.admin_proxy_path, suffix)

    def _user_api_url(self, server: HiddifyServer, suffix: str) -> str:
        return self._panel_api_url(server.base_url, server.client_proxy_path, f"user/{suffix.lstrip('/')}")

    @staticmethod
    def _build_subscription_url(server: HiddifyServer, remote_user_uuid: str) -> str:
        return f"{server.base_url.rstrip('/')}/{server.client_proxy_path.strip('/')}/{remote_user_uuid}/sub/"

    @staticmethod
    def _build_panel_url(server: HiddifyServer, remote_user_uuid: str, *, profile_name: str | None = None) -> str:
        base_url = f"{server.base_url.rstrip('/')}/{server.client_proxy_path.strip('/')}/{remote_user_uuid}/"
        fragment = HiddifyService._build_profile_fragment(profile_name)
        return f"{base_url}#{fragment}" if fragment else base_url

    @staticmethod
    def _build_deeplink(subscription_url: str, *, profile_name: str | None = None) -> str:
        deeplink = f"hiddify://import/{subscription_url}"
        fragment = HiddifyService._build_profile_fragment(profile_name)
        return f"{deeplink}#{fragment}" if fragment else deeplink

    def _build_superkey_token(self, order_id: int, *, issue_tag: str | None = None) -> str:
        source = f"hiddify-superkey:{order_id}:{self._settings.app_base_url}"
        if issue_tag:
            source = f"{source}:{issue_tag}"
        digest = self._key_protector.fingerprint(source)
        return digest[:32]

    def _build_aggregate_subscription_url(self, token: str) -> str:
        return f"{self._settings.app_base_url.rstrip('/')}/subscriptions/{token}"

    @staticmethod
    def _build_remote_name(*, order_hint: str, user: User, plan_name: str) -> str:
        if user.username:
            username = user.username
        elif getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.WHATSAPP.value and user.whatsapp_phone:
            username = f"wa{user.whatsapp_phone[-8:]}"
        elif getattr(user, "delivery_channel", UserChannel.TELEGRAM.value) == UserChannel.VK.value and user.vk_user_id:
            username = f"vk{user.vk_user_id}"
        else:
            username = f"user{abs(user.telegram_user_id)}"
        return f"{username} {plan_name} {order_hint}"[:64]

    @staticmethod
    def _build_order_hint(order_id: int, *, issue_tag: str | None = None) -> str:
        source = f"vpn-seller:hiddify:{order_id}"
        if issue_tag:
            source = f"{source}:{issue_tag}"
        return str(uuid5(NAMESPACE_URL, source)).split("-")[0]

    @staticmethod
    def _issue_marker(order_id: int, *, issue_tag: str | None = None) -> str:
        marker = f"order_id={order_id}"
        if issue_tag:
            marker = f"{marker}; issue_tag={issue_tag}"
        return marker

    @staticmethod
    def _prefer_alternatives(servers: list[HiddifyServer], *, avoid_server_id: int | None) -> list[HiddifyServer]:
        if avoid_server_id is None or len(servers) <= 1:
            return servers
        alternatives = [server for server in servers if server.id != avoid_server_id]
        avoided = [server for server in servers if server.id == avoid_server_id]
        return alternatives + avoided

    @staticmethod
    def _build_profile_fragment(profile_name: str | None) -> str:
        return (profile_name or "").strip().replace(" ", "-")

    @staticmethod
    def _normalize_country_name(country_name: str | None) -> str:
        normalized = (country_name or "").strip()
        return normalized or "Без страны"
