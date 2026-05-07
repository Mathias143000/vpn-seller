from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.db.models import AuditLog
from app.repositories.base import BaseRepository


class AuditLogsRepository(BaseRepository):
    async def add(
        self,
        *,
        actor_user_id: int | None,
        entity_type: str,
        entity_id: str,
        action: str,
        payload_json: dict,
        correlation_id: str | None = None,
    ) -> AuditLog:
        audit_log = AuditLog(
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            payload_json=payload_json,
        )
        self.session.add(audit_log)
        await self.session.flush()
        return audit_log

    async def exists_recent(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        since: datetime,
    ) -> bool:
        query = select(func.count(AuditLog.id)).where(
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
            AuditLog.action == action,
            AuditLog.created_at >= since,
        )
        return bool((await self.session.scalar(query)) or 0)
