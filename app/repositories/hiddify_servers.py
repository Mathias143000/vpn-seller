from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, nullsfirst, select

from app.db.models import HiddifyServer, ServerHealthStatus
from app.repositories.base import BaseRepository


class HiddifyServersRepository(BaseRepository):
    async def create(
        self,
        *,
        name: str,
        country_name: str,
        base_url: str,
        admin_proxy_path: str,
        client_proxy_path: str,
        api_key_encrypted: str,
        is_active: bool = True,
        panel_version: str | None = None,
        last_health_status: str = ServerHealthStatus.UNKNOWN.value,
        last_healthcheck_at: datetime | None = None,
        last_error: str | None = None,
    ) -> HiddifyServer:
        server = HiddifyServer(
            name=name,
            country_name=country_name,
            base_url=base_url,
            admin_proxy_path=admin_proxy_path,
            client_proxy_path=client_proxy_path,
            api_key_encrypted=api_key_encrypted,
            is_active=is_active,
            panel_version=panel_version,
            last_health_status=last_health_status,
            last_healthcheck_at=last_healthcheck_at,
            last_error=last_error,
        )
        self.session.add(server)
        await self.session.flush()
        return server

    async def get_by_id(self, server_id: int) -> HiddifyServer | None:
        return await self.session.get(HiddifyServer, server_id)

    async def lock_by_id(self, server_id: int) -> HiddifyServer | None:
        query = select(HiddifyServer).where(HiddifyServer.id == server_id).with_for_update()
        return await self.session.scalar(query)

    async def list_all(self) -> list[HiddifyServer]:
        result = await self.session.scalars(select(HiddifyServer).order_by(HiddifyServer.name.asc(), HiddifyServer.id.asc()))
        return list(result)

    async def list_active(self, *, country_name: str | None = None) -> list[HiddifyServer]:
        query = select(HiddifyServer).where(HiddifyServer.is_active.is_(True))
        if country_name:
            query = query.where(HiddifyServer.country_name == country_name)
        result = await self.session.scalars(query.order_by(HiddifyServer.country_name.asc(), HiddifyServer.id.asc()))
        return list(result)

    async def has_active_server(self, *, country_name: str | None = None) -> bool:
        query = select(func.count(HiddifyServer.id)).where(HiddifyServer.is_active.is_(True))
        if country_name:
            query = query.where(HiddifyServer.country_name == country_name)
        return bool((await self.session.scalar(query)) or 0)

    async def claim_next_active(self) -> HiddifyServer | None:
        query = (
            select(HiddifyServer)
            .where(HiddifyServer.is_active.is_(True))
            .order_by(nullsfirst(HiddifyServer.last_used_at.asc()), HiddifyServer.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return await self.session.scalar(query)

    async def set_health(
        self,
        server: HiddifyServer,
        *,
        status: str,
        checked_at: datetime,
        panel_version: str | None = None,
        error_message: str | None = None,
    ) -> HiddifyServer:
        server.last_health_status = status
        server.last_healthcheck_at = checked_at
        server.last_error = error_message
        if panel_version is not None:
            server.panel_version = panel_version
        await self.session.flush()
        return server

    async def set_active(self, server: HiddifyServer, *, is_active: bool) -> HiddifyServer:
        server.is_active = is_active
        await self.session.flush()
        return server

    async def touch_used(self, server: HiddifyServer, *, used_at: datetime) -> HiddifyServer:
        server.last_used_at = used_at
        await self.session.flush()
        return server
