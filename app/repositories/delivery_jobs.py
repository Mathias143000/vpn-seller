from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from app.db.models import DeliveryJob, DeliveryJobStatus
from app.repositories.base import BaseRepository


class DeliveryJobsRepository(BaseRepository):
    async def get_by_id(self, job_id: int) -> DeliveryJob | None:
        return await self.session.get(DeliveryJob, job_id)

    async def lock_by_id(self, job_id: int) -> DeliveryJob | None:
        query = select(DeliveryJob).where(DeliveryJob.id == job_id).with_for_update()
        return await self.session.scalar(query)

    async def enqueue(
        self,
        *,
        order_id: int,
        user_id: int,
        job_type: str,
        payload_json: dict,
        dedupe_key: str,
    ) -> DeliveryJob:
        delivery_job = DeliveryJob(
            order_id=order_id,
            user_id=user_id,
            job_type=job_type,
            payload_json=payload_json,
            dedupe_key=dedupe_key,
            status=DeliveryJobStatus.PENDING.value,
        )
        self.session.add(delivery_job)
        await self.session.flush()
        return delivery_job

    async def claim_due_jobs(self, *, now: datetime, limit: int = 20) -> list[DeliveryJob]:
        query = (
            select(DeliveryJob)
            .where(
                DeliveryJob.status.in_([DeliveryJobStatus.PENDING.value, DeliveryJobStatus.RETRY.value]),
                or_(DeliveryJob.next_retry_at.is_(None), DeliveryJob.next_retry_at <= now),
            )
            .order_by(DeliveryJob.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.scalars(query)
        jobs = list(result)
        for job in jobs:
            job.status = DeliveryJobStatus.PROCESSING.value
            job.locked_at = now
        await self.session.flush()
        return jobs

    async def mark_delivered(self, job: DeliveryJob, *, delivered_at: datetime) -> DeliveryJob:
        job.status = DeliveryJobStatus.DELIVERED.value
        job.delivered_at = delivered_at
        job.last_error = None
        await self.session.flush()
        return job

    async def mark_retry(self, job: DeliveryJob, *, next_retry_at: datetime, error_message: str) -> DeliveryJob:
        job.status = DeliveryJobStatus.RETRY.value
        job.last_error = error_message
        job.attempts_count += 1
        job.next_retry_at = next_retry_at
        await self.session.flush()
        return job

    async def mark_failed(self, job: DeliveryJob, *, error_message: str) -> DeliveryJob:
        job.status = DeliveryJobStatus.FAILED.value
        job.last_error = error_message
        job.attempts_count += 1
        await self.session.flush()
        return job
