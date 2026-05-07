from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.models import HiddifyServerUsageSnapshot
from app.repositories.base import BaseRepository


@dataclass(slots=True)
class HiddifyUsageMonthlySummary:
    server_id: int
    snapshots_count: int
    average_active_users_percent: float | None
    average_total_current_usage_gb: float | None
    average_user_usage_gb: float | None
    latest_sampled_at: datetime | None


class HiddifyUsageSnapshotsRepository(BaseRepository):
    async def create(
        self,
        *,
        server_id: int,
        sampled_at: datetime,
        total_users_count: int | None,
        active_users_count: int | None,
        active_users_percent: float | None,
        total_current_usage_gb: float | None,
        average_user_usage_gb: float | None,
        usage_sample_users_count: int,
        health_status: str,
        error_message: str | None,
    ) -> HiddifyServerUsageSnapshot:
        snapshot = HiddifyServerUsageSnapshot(
            server_id=server_id,
            sampled_at=sampled_at,
            total_users_count=total_users_count,
            active_users_count=active_users_count,
            active_users_percent=self._decimal_or_none(active_users_percent),
            total_current_usage_gb=self._decimal_or_none(total_current_usage_gb),
            average_user_usage_gb=self._decimal_or_none(average_user_usage_gb),
            usage_sample_users_count=usage_sample_users_count,
            health_status=health_status,
            error_message=error_message,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def latest_for_server(self, server_id: int) -> HiddifyServerUsageSnapshot | None:
        query = (
            select(HiddifyServerUsageSnapshot)
            .where(HiddifyServerUsageSnapshot.server_id == server_id)
            .order_by(HiddifyServerUsageSnapshot.sampled_at.desc(), HiddifyServerUsageSnapshot.id.desc())
            .limit(1)
        )
        return await self.session.scalar(query)

    async def latest_by_server_ids(self, server_ids: list[int]) -> dict[int, HiddifyServerUsageSnapshot]:
        if not server_ids:
            return {}
        query = (
            select(HiddifyServerUsageSnapshot)
            .where(HiddifyServerUsageSnapshot.server_id.in_(server_ids))
            .order_by(
                HiddifyServerUsageSnapshot.server_id.asc(),
                HiddifyServerUsageSnapshot.sampled_at.desc(),
                HiddifyServerUsageSnapshot.id.desc(),
            )
        )
        result = await self.session.scalars(query)
        latest: dict[int, HiddifyServerUsageSnapshot] = {}
        for snapshot in result:
            latest.setdefault(snapshot.server_id, snapshot)
        return latest

    async def list_for_server_since(self, *, server_id: int, since: datetime) -> list[HiddifyServerUsageSnapshot]:
        query = (
            select(HiddifyServerUsageSnapshot)
            .where(
                HiddifyServerUsageSnapshot.server_id == server_id,
                HiddifyServerUsageSnapshot.sampled_at >= since,
            )
            .order_by(HiddifyServerUsageSnapshot.sampled_at.asc(), HiddifyServerUsageSnapshot.id.asc())
        )
        result = await self.session.scalars(query)
        return list(result)

    async def monthly_summary_by_server_ids(
        self,
        *,
        server_ids: list[int],
        since: datetime,
    ) -> dict[int, HiddifyUsageMonthlySummary]:
        if not server_ids:
            return {}
        query = (
            select(HiddifyServerUsageSnapshot)
            .where(
                HiddifyServerUsageSnapshot.server_id.in_(server_ids),
                HiddifyServerUsageSnapshot.sampled_at >= since,
            )
            .order_by(HiddifyServerUsageSnapshot.server_id.asc(), HiddifyServerUsageSnapshot.sampled_at.asc())
        )
        result = await self.session.scalars(query)
        grouped: dict[int, list[HiddifyServerUsageSnapshot]] = {}
        for snapshot in result:
            grouped.setdefault(snapshot.server_id, []).append(snapshot)

        return {
            server_id: self._build_summary(server_id=server_id, snapshots=snapshots)
            for server_id, snapshots in grouped.items()
        }

    @classmethod
    def _build_summary(
        cls,
        *,
        server_id: int,
        snapshots: list[HiddifyServerUsageSnapshot],
    ) -> HiddifyUsageMonthlySummary:
        active_percents = [cls._float_or_none(item.active_users_percent) for item in snapshots]
        total_usage = [cls._float_or_none(item.total_current_usage_gb) for item in snapshots]
        average_user_usage = [cls._float_or_none(item.average_user_usage_gb) for item in snapshots]
        return HiddifyUsageMonthlySummary(
            server_id=server_id,
            snapshots_count=len(snapshots),
            average_active_users_percent=cls._average([item for item in active_percents if item is not None]),
            average_total_current_usage_gb=cls._average([item for item in total_usage if item is not None]),
            average_user_usage_gb=cls._average([item for item in average_user_usage if item is not None]),
            latest_sampled_at=max((item.sampled_at for item in snapshots), default=None),
        )

    @staticmethod
    def _average(values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    @staticmethod
    def _decimal_or_none(value: float | int | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(round(float(value), 2)))

    @staticmethod
    def _float_or_none(value) -> float | None:
        if value is None:
            return None
        return float(value)
