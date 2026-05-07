from __future__ import annotations

from sqlalchemy import select

from app.db.models import ShopSetting
from app.repositories.base import BaseRepository


class ShopSettingsRepository(BaseRepository):
    async def get(self, key: str) -> ShopSetting | None:
        return await self.session.scalar(select(ShopSetting).where(ShopSetting.key == key))

    async def set(self, *, key: str, value: str | None) -> ShopSetting:
        setting = await self.get(key)
        if setting is None:
            setting = ShopSetting(key=key, value=value)
            self.session.add(setting)
        else:
            setting.value = value
        await self.session.flush()
        return setting

    async def list_all(self) -> list[ShopSetting]:
        result = await self.session.scalars(select(ShopSetting).order_by(ShopSetting.key.asc()))
        return list(result)
