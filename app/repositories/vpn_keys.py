from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.db.models import KeyStatus, VPNKey
from app.repositories.base import BaseRepository


class VPNKeysRepository(BaseRepository):
    async def count_available(self, plan_id: int) -> int:
        query = select(func.count(VPNKey.id)).where(
            VPNKey.plan_id == plan_id,
            VPNKey.status == KeyStatus.AVAILABLE.value,
        )
        return int((await self.session.scalar(query)) or 0)

    async def get_by_id(self, key_id: int) -> VPNKey | None:
        return await self.session.get(VPNKey, key_id)

    async def get_by_fingerprint(self, key_fingerprint: str) -> VPNKey | None:
        query = select(VPNKey).where(VPNKey.key_fingerprint == key_fingerprint)
        return await self.session.scalar(query)

    async def get_by_external_ref(self, external_ref: str) -> VPNKey | None:
        query = select(VPNKey).where(VPNKey.external_ref == external_ref)
        return await self.session.scalar(query)

    async def lock_by_id(self, key_id: int) -> VPNKey | None:
        query = select(VPNKey).where(VPNKey.id == key_id).with_for_update()
        return await self.session.scalar(query)

    async def reserve_available_key(self, *, plan_id: int, order_id: int) -> VPNKey | None:
        query = (
            select(VPNKey)
            .where(
                VPNKey.plan_id == plan_id,
                VPNKey.status == KeyStatus.AVAILABLE.value,
            )
            .order_by(VPNKey.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        vpn_key = await self.session.scalar(query)
        if vpn_key is None:
            return None
        vpn_key.status = KeyStatus.RESERVED.value
        vpn_key.reserved_by_order_id = order_id
        await self.session.flush()
        return vpn_key

    async def get_next_available_for_plan(self, plan_id: int) -> VPNKey | None:
        query = (
            select(VPNKey)
            .where(
                VPNKey.plan_id == plan_id,
                VPNKey.status == KeyStatus.AVAILABLE.value,
            )
            .order_by(VPNKey.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return await self.session.scalar(query)

    async def release_reservation(self, vpn_key: VPNKey) -> VPNKey:
        vpn_key.status = KeyStatus.AVAILABLE.value
        vpn_key.reserved_by_order_id = None
        await self.session.flush()
        return vpn_key

    async def issue_key(self, *, vpn_key: VPNKey, user_id: int, issued_at: datetime) -> VPNKey:
        vpn_key.status = KeyStatus.ISSUED.value
        vpn_key.issued_to_user_id = user_id
        vpn_key.reserved_by_order_id = None
        vpn_key.issued_at = issued_at
        await self.session.flush()
        return vpn_key

    async def mark_broken(self, vpn_key: VPNKey) -> VPNKey:
        vpn_key.status = KeyStatus.BROKEN.value
        await self.session.flush()
        return vpn_key

    async def create_generated_issued_key(
        self,
        *,
        plan_id: int,
        key_value_encrypted: str,
        key_fingerprint: str,
        user_id: int,
        issued_at: datetime,
        expires_at: datetime | None,
        external_ref: str | None,
        comment: str | None,
    ) -> VPNKey:
        existing = await self.session.scalar(
            select(VPNKey).where(VPNKey.key_fingerprint == key_fingerprint).with_for_update()
        )
        if existing is not None:
            existing.status = KeyStatus.ISSUED.value
            existing.issued_to_user_id = user_id
            existing.issued_at = issued_at
            existing.expires_at = expires_at
            existing.external_ref = external_ref
            existing.comment = comment
            await self.session.flush()
            return existing

        vpn_key = VPNKey(
            plan_id=plan_id,
            key_value_encrypted=key_value_encrypted,
            key_fingerprint=key_fingerprint,
            status=KeyStatus.ISSUED.value,
            issued_to_user_id=user_id,
            issued_at=issued_at,
            expires_at=expires_at,
            external_ref=external_ref,
            comment=comment,
        )
        self.session.add(vpn_key)
        await self.session.flush()
        return vpn_key
