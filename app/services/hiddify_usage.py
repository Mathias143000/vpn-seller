from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.hiddify_servers import HiddifyServersRepository
from app.repositories.hiddify_usage_snapshots import HiddifyUsageSnapshotsRepository
from app.services.hiddify import HiddifyServerLoad, HiddifyService
from app.services.notifications import NotificationService
from app.services.transactions import transactional


@dataclass(slots=True)
class LocationServerUsageState:
    server_id: int
    server_name: str
    country_name: str
    active_users_percent: float | None
    monthly_average_total_usage_gb: float | None
    monthly_snapshots_count: int


@dataclass(slots=True)
class HiddifyLocationCapacityStatus:
    country_name: str
    servers_count: int
    active_users_threshold_percent: float
    usage_threshold_gb: float
    active_users_status: str
    usage_status: str
    active_users_min_percent: float | None
    active_users_max_percent: float | None
    usage_min_gb: float | None
    usage_max_gb: float | None
    active_hot_servers_count: int
    usage_hot_servers_count: int
    unknown_active_servers_count: int
    unknown_usage_servers_count: int
    snapshots_count: int
    capacity_needed: bool


class HiddifyUsageMonitorService:
    LOCATION_ACTIVE_USERS_ALERT_ACTION = "hiddify_location_active_users_threshold_alert"
    LOCATION_USAGE_ALERT_ACTION = "hiddify_location_average_usage_threshold_alert"
    COUNTRY_ALIASES = {
        "de": "germany",
        "deu": "germany",
        "germany": "germany",
        "германия": "germany",
        "nl": "netherlands",
        "nld": "netherlands",
        "netherlands": "netherlands",
        "нидерланды": "netherlands",
        "us": "usa",
        "usa": "usa",
        "united states": "usa",
        "united states of america": "usa",
        "сша": "usa",
    }

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings,
        hiddify: HiddifyService,
        hiddify_servers_repo: HiddifyServersRepository,
        usage_snapshots_repo: HiddifyUsageSnapshotsRepository,
        audit_logs_repo: AuditLogsRepository,
        notification_service: NotificationService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._hiddify = hiddify
        self._hiddify_servers_repo = hiddify_servers_repo
        self._usage_snapshots_repo = usage_snapshots_repo
        self._audit_logs_repo = audit_logs_repo
        self._notification_service = notification_service

    async def collect_due_snapshots(self, *, now: datetime | None = None) -> list[HiddifyServerLoad]:
        sampled_at = now or datetime.now(tz=timezone.utc)
        sampled_at = self._ensure_aware(sampled_at)
        interval_minutes = max(int(getattr(self._settings, "hiddify_usage_snapshot_interval_minutes", 60)), 1)
        due_before = sampled_at - timedelta(minutes=interval_minutes)

        async with transactional(self._session):
            servers = await self._hiddify_servers_repo.list_active()
            latest_by_server_id = await self._usage_snapshots_repo.latest_by_server_ids([server.id for server in servers])

        due_server_ids = [
            server.id
            for server in servers
            if server.id not in latest_by_server_id
            or self._ensure_aware(latest_by_server_id[server.id].sampled_at) <= due_before
        ]

        collected: list[HiddifyServerLoad] = []
        for server_id in due_server_ids:
            loads = await self._hiddify.collect_usage_snapshots(server_id=server_id, now=sampled_at)
            for load in loads:
                collected.append(load)
        if collected:
            await self._send_location_alerts_if_needed(server_ids=[server.id for server in servers], now=sampled_at)
        return collected

    async def collect_snapshots_now(self, *, now: datetime | None = None) -> list[HiddifyServerLoad]:
        sampled_at = now or datetime.now(tz=timezone.utc)
        sampled_at = self._ensure_aware(sampled_at)
        loads = await self._hiddify.collect_usage_snapshots(now=sampled_at)
        if loads:
            await self._send_location_alerts_if_needed(server_ids=[load.server_id for load in loads], now=sampled_at)
        return loads

    async def _send_location_alerts_if_needed(self, *, server_ids: list[int], now: datetime) -> None:
        states_by_country = await self._build_location_usage_states(server_ids=server_ids, now=now)
        for country_name, states in states_by_country.items():
            await self._send_location_active_users_alert_if_needed(country_name=country_name, states=states, now=now)
            await self._send_location_usage_alert_if_needed(country_name=country_name, states=states, now=now)

    def build_location_capacity_status(self, loads: list[HiddifyServerLoad]) -> list[HiddifyLocationCapacityStatus]:
        states_by_country: dict[str, list[LocationServerUsageState]] = {}
        for load in loads:
            if not load.is_active:
                continue
            state = LocationServerUsageState(
                server_id=load.server_id,
                server_name=load.server_name,
                country_name=load.country_name,
                active_users_percent=load.active_users_percent,
                monthly_average_total_usage_gb=load.monthly_average_total_usage_gb,
                monthly_snapshots_count=load.monthly_snapshots_count,
            )
            states_by_country.setdefault(load.country_name, []).append(state)
        return self._build_capacity_statuses(states_by_country)

    async def _build_location_usage_states(
        self,
        *,
        server_ids: list[int],
        now: datetime,
    ) -> dict[str, list[LocationServerUsageState]]:
        if not server_ids:
            return {}
        window_days = max(int(getattr(self._settings, "hiddify_usage_monthly_window_days", 30)), 1)
        since = now - timedelta(days=window_days)
        async with transactional(self._session):
            servers = await self._hiddify_servers_repo.list_active()
            latest_by_server_id = await self._usage_snapshots_repo.latest_by_server_ids(server_ids)
            summaries = await self._usage_snapshots_repo.monthly_summary_by_server_ids(server_ids=server_ids, since=since)

        states_by_country: dict[str, list[LocationServerUsageState]] = {}
        for server in servers:
            if server.id not in server_ids:
                continue
            latest_snapshot = latest_by_server_id.get(server.id)
            summary = summaries.get(server.id)
            state = LocationServerUsageState(
                server_id=server.id,
                server_name=server.name,
                country_name=server.country_name,
                active_users_percent=float(latest_snapshot.active_users_percent)
                if latest_snapshot is not None and latest_snapshot.active_users_percent is not None
                else None,
                monthly_average_total_usage_gb=summary.average_total_current_usage_gb if summary is not None else None,
                monthly_snapshots_count=summary.snapshots_count if summary is not None else 0,
            )
            states_by_country.setdefault(server.country_name, []).append(state)
        return states_by_country

    def _build_capacity_statuses(
        self,
        states_by_country: dict[str, list[LocationServerUsageState]],
    ) -> list[HiddifyLocationCapacityStatus]:
        statuses: list[HiddifyLocationCapacityStatus] = []
        for country_name, states in sorted(states_by_country.items(), key=lambda item: item[0].lower()):
            active_threshold = self._country_threshold(
                country_name=country_name,
                default_value=float(getattr(self._settings, "hiddify_active_users_alert_percent", 0.0) or 0.0),
                raw_overrides=str(getattr(self._settings, "hiddify_active_users_alert_percent_by_country", "") or ""),
            )
            usage_threshold = self._country_threshold(
                country_name=country_name,
                default_value=float(getattr(self._settings, "hiddify_average_monthly_usage_alert_gb", 0.0) or 0.0),
                raw_overrides=str(getattr(self._settings, "hiddify_average_monthly_usage_alert_gb_by_country", "") or ""),
            )
            active_values = [state.active_users_percent for state in states]
            usage_values = [state.monthly_average_total_usage_gb for state in states]
            active_status = self._capacity_state(values=active_values, threshold=active_threshold)
            usage_status = self._capacity_state(values=usage_values, threshold=usage_threshold)
            known_active_values = [value for value in active_values if value is not None]
            known_usage_values = [value for value in usage_values if value is not None]
            statuses.append(
                HiddifyLocationCapacityStatus(
                    country_name=country_name,
                    servers_count=len(states),
                    active_users_threshold_percent=active_threshold,
                    usage_threshold_gb=usage_threshold,
                    active_users_status=active_status,
                    usage_status=usage_status,
                    active_users_min_percent=min(known_active_values) if known_active_values else None,
                    active_users_max_percent=max(known_active_values) if known_active_values else None,
                    usage_min_gb=min(known_usage_values) if known_usage_values else None,
                    usage_max_gb=max(known_usage_values) if known_usage_values else None,
                    active_hot_servers_count=self._hot_count(values=active_values, threshold=active_threshold),
                    usage_hot_servers_count=self._hot_count(values=usage_values, threshold=usage_threshold),
                    unknown_active_servers_count=sum(1 for value in active_values if value is None),
                    unknown_usage_servers_count=sum(1 for value in usage_values if value is None),
                    snapshots_count=sum(state.monthly_snapshots_count for state in states),
                    capacity_needed=active_status == "needs_capacity" or usage_status == "needs_capacity",
                )
            )
        return statuses

    async def _send_location_active_users_alert_if_needed(
        self,
        *,
        country_name: str,
        states: list[LocationServerUsageState],
        now: datetime,
    ) -> None:
        threshold = self._country_threshold(
            country_name=country_name,
            default_value=float(getattr(self._settings, "hiddify_active_users_alert_percent", 0.0) or 0.0),
            raw_overrides=str(getattr(self._settings, "hiddify_active_users_alert_percent_by_country", "") or ""),
        )
        if threshold <= 0 or not states:
            return
        values = [state.active_users_percent for state in states]
        if any(value is None or value < threshold for value in values):
            return
        if await self._alert_recently_sent(
            action=self.LOCATION_ACTIVE_USERS_ALERT_ACTION,
            entity_type="hiddify_location",
            entity_id=self._country_key(country_name),
            now=now,
        ):
            return

        message = (
            "Hiddify: every active server in a location is near active-user capacity.\n"
            f"Location: {country_name}\n"
            f"Threshold: {threshold:.1f}% active users.\n"
            f"Servers: {self._format_active_location_servers(states)}\n"
            "Action: add one more server for this location."
        )
        await self._send_and_record_alert(
            action=self.LOCATION_ACTIVE_USERS_ALERT_ACTION,
            entity_type="hiddify_location",
            entity_id=self._country_key(country_name),
            message=message,
            payload={
                "country_name": country_name,
                "threshold_percent": threshold,
                "servers": [
                    {
                        "server_id": state.server_id,
                        "server_name": state.server_name,
                        "active_users_percent": state.active_users_percent,
                    }
                    for state in states
                ],
            },
        )

    async def _send_location_usage_alert_if_needed(
        self,
        *,
        country_name: str,
        states: list[LocationServerUsageState],
        now: datetime,
    ) -> None:
        threshold = self._country_threshold(
            country_name=country_name,
            default_value=float(getattr(self._settings, "hiddify_average_monthly_usage_alert_gb", 0.0) or 0.0),
            raw_overrides=str(getattr(self._settings, "hiddify_average_monthly_usage_alert_gb_by_country", "") or ""),
        )
        if threshold <= 0 or not states:
            return
        values = [state.monthly_average_total_usage_gb for state in states]
        if any(value is None or value < threshold for value in values):
            return
        if await self._alert_recently_sent(
            action=self.LOCATION_USAGE_ALERT_ACTION,
            entity_type="hiddify_location",
            entity_id=self._country_key(country_name),
            now=now,
        ):
            return

        message = (
            "Hiddify: every active server in a location is near traffic capacity.\n"
            f"Location: {country_name}\n"
            f"Threshold: {threshold:.2f} GB average total usage.\n"
            f"Servers: {self._format_usage_location_servers(states)}\n"
            "Action: add one more server for this location."
        )
        await self._send_and_record_alert(
            action=self.LOCATION_USAGE_ALERT_ACTION,
            entity_type="hiddify_location",
            entity_id=self._country_key(country_name),
            message=message,
            payload={
                "country_name": country_name,
                "threshold_gb": threshold,
                "servers": [
                    {
                        "server_id": state.server_id,
                        "server_name": state.server_name,
                        "monthly_average_total_usage_gb": state.monthly_average_total_usage_gb,
                        "monthly_snapshots_count": state.monthly_snapshots_count,
                    }
                    for state in states
                ],
            },
        )

    async def _alert_recently_sent(self, *, action: str, entity_type: str, entity_id: str, now: datetime) -> bool:
        cooldown_minutes = max(int(getattr(self._settings, "hiddify_alert_cooldown_minutes", 360)), 1)
        since = now - timedelta(minutes=cooldown_minutes)
        async with transactional(self._session):
            return await self._audit_logs_repo.exists_recent(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                since=since,
            )

    async def _send_and_record_alert(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str,
        message: str,
        payload: dict,
    ) -> None:
        await self._notification_service.send_admin_alert(message)
        async with transactional(self._session):
            await self._audit_logs_repo.add(
                actor_user_id=None,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                payload_json=payload,
            )

    def _country_threshold(self, *, country_name: str, default_value: float, raw_overrides: str) -> float:
        overrides = self._parse_country_thresholds(raw_overrides)
        return overrides.get(self._country_key(country_name), default_value)

    @classmethod
    def _capacity_state(cls, *, values: list[float | None], threshold: float) -> str:
        if threshold <= 0:
            return "disabled"
        if not values or all(value is None for value in values):
            return "unknown"
        hot_count = cls._hot_count(values=values, threshold=threshold)
        unknown_count = sum(1 for value in values if value is None)
        if hot_count == len(values) and unknown_count == 0:
            return "needs_capacity"
        if hot_count > 0:
            return "watch"
        if unknown_count > 0:
            return "unknown"
        return "ok"

    @staticmethod
    def _hot_count(*, values: list[float | None], threshold: float) -> int:
        if threshold <= 0:
            return 0
        return sum(1 for value in values if value is not None and value >= threshold)

    @classmethod
    def _parse_country_thresholds(cls, raw_value: str) -> dict[str, float]:
        raw_value = raw_value.strip()
        if not raw_value:
            return {}
        if raw_value.startswith("{"):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                return {}
            if not isinstance(parsed, dict):
                return {}
            items = parsed.items()
        else:
            pairs: list[tuple[str, str]] = []
            for chunk in raw_value.split(","):
                if "=" in chunk:
                    key, value = chunk.split("=", maxsplit=1)
                elif ":" in chunk:
                    key, value = chunk.split(":", maxsplit=1)
                else:
                    continue
                pairs.append((key, value))
            items = pairs

        thresholds: dict[str, float] = {}
        for country_name, value in items:
            try:
                thresholds[cls._country_key(str(country_name))] = float(value)
            except (TypeError, ValueError):
                continue
        return thresholds

    @classmethod
    def _country_key(cls, country_name: str) -> str:
        normalized = " ".join((country_name or "").strip().lower().split())
        return cls.COUNTRY_ALIASES.get(normalized, normalized)

    @staticmethod
    def _format_active_location_servers(states: list[LocationServerUsageState]) -> str:
        return "; ".join(
            f"{state.server_name} {state.active_users_percent:.1f}%"
            for state in states
            if state.active_users_percent is not None
        )

    @staticmethod
    def _format_usage_location_servers(states: list[LocationServerUsageState]) -> str:
        return "; ".join(
            f"{state.server_name} {state.monthly_average_total_usage_gb:.2f} GB/{state.monthly_snapshots_count} samples"
            for state in states
            if state.monthly_average_total_usage_gb is not None
        )

    @staticmethod
    def _ensure_aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
