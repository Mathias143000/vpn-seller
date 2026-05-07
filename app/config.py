from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_mode: Literal["web", "polling"] = "web"
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    bot_token: SecretStr = SecretStr("change-me")
    bot_webhook_secret: str = "telegram-webhook-secret"
    admin_ids: str = ""
    support_username: str | None = None
    support_url: str | None = None
    vk_group_id: int | None = None
    vk_group_token: SecretStr = SecretStr("")
    vk_confirmation_token: str | None = None
    vk_callback_secret: str | None = None
    vk_api_version: str = "5.199"
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_api_version: str = "v23.0"

    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    encryption_key: SecretStr = SecretStr("change-me")
    default_low_stock_threshold: int = 5
    reservation_ttl_minutes: int = 15

    payment_provider: Literal["fake", "donate_stream"] = "donate_stream"
    payment_reconciliation_minutes: int = 15
    payment_stale_pending_minutes: int = 30
    delivery_retry_seconds: int = 30
    delivery_max_attempts: int = 5
    donate_stream_url: str = "https://lk.donate.stream/"
    content_file: str = "content/messages.json"
    plan_pricing_file: str = "content/pricing.json"
    apply_plan_pricing_on_startup: bool = True
    min_order_amount: int = 1
    server_markup_percent: int = 0
    superkey_markup_percent: int = 50
    hiddify_usage_snapshot_interval_minutes: int = 60
    hiddify_usage_monthly_window_days: int = 30
    hiddify_active_users_alert_percent: float = 85.0
    hiddify_average_monthly_usage_alert_gb: float = 800.0
    hiddify_active_users_alert_percent_by_country: str = ""
    hiddify_average_monthly_usage_alert_gb_by_country: str = ""
    hiddify_alert_cooldown_minutes: int = 1440

    @property
    def parsed_admin_ids(self) -> list[int]:
        if not self.admin_ids.strip():
            return []
        return [int(item.strip()) for item in self.admin_ids.split(",") if item.strip()]

    @property
    def telegram_webhook_path(self) -> str:
        return f"/telegram/webhook/{self.bot_webhook_secret}"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.app_base_url.rstrip('/')}{self.telegram_webhook_path}"

    @property
    def vk_enabled(self) -> bool:
        return bool(self.vk_group_id and self.vk_group_token.get_secret_value().strip())

    @property
    def vk_callback_path(self) -> str:
        return "/vk/callback"

    @property
    def vk_callback_url(self) -> str:
        return f"{self.app_base_url.rstrip('/')}{self.vk_callback_path}"

    @property
    def setup_guide_url(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/files/setup-guide"

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(
            (self.whatsapp_phone_number_id or "").strip()
            and self.whatsapp_access_token.get_secret_value().strip()
        )

    @property
    def whatsapp_verify_enabled(self) -> bool:
        return bool((self.whatsapp_verify_token or "").strip())

    @property
    def whatsapp_webhook_path(self) -> str:
        return "/whatsapp/webhook"

    @property
    def whatsapp_webhook_url(self) -> str:
        return f"{self.app_base_url.rstrip('/')}{self.whatsapp_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
