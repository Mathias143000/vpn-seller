from __future__ import annotations

from app.config import Settings
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.shop_settings import ShopSettingsRepository


class ShopSettingsService:
    DONATE_STREAM_URL = "donate_stream_url"
    SUPPORT_USERNAME = "support_username"
    SUPPORT_URL = "support_url"

    def __init__(
        self,
        *,
        settings: Settings,
        shop_settings_repo: ShopSettingsRepository,
        audit_logs_repo: AuditLogsRepository,
    ) -> None:
        self._settings = settings
        self._shop_settings_repo = shop_settings_repo
        self._audit_logs_repo = audit_logs_repo

    async def get_value(self, key: str, fallback: str | None = None) -> str | None:
        setting = await self._shop_settings_repo.get(key)
        if setting is None or setting.value is None or not setting.value.strip():
            return fallback
        return setting.value.strip()

    async def get_donate_stream_url(self) -> str:
        return (
            await self.get_value(self.DONATE_STREAM_URL, self._settings.donate_stream_url)
            or ""
        )

    async def get_support_username(self) -> str | None:
        return await self.get_value(self.SUPPORT_USERNAME, self._settings.support_username)

    async def get_support_url(self) -> str | None:
        return await self.get_value(self.SUPPORT_URL, self._settings.support_url)

    async def set_value(self, *, key: str, value: str | None, actor_user_id: int | None) -> None:
        normalized = value.strip() if isinstance(value, str) and value.strip() else None
        await self._shop_settings_repo.set(key=key, value=normalized)
        await self._audit_logs_repo.add(
            actor_user_id=actor_user_id,
            entity_type="shop_setting",
            entity_id=key,
            action="shop_setting_updated",
            payload_json={"key": key, "is_set": normalized is not None},
        )

    async def summary(self) -> dict[str, str | None]:
        return {
            self.DONATE_STREAM_URL: await self.get_donate_stream_url(),
            self.SUPPORT_USERNAME: await self.get_support_username(),
            self.SUPPORT_URL: await self.get_support_url(),
        }
