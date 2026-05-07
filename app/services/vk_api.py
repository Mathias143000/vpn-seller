from __future__ import annotations

import json
import secrets
from typing import Any

import httpx

from app.config import Settings


class VKApiClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = "https://api.vk.ru/method/"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    @property
    def enabled(self) -> bool:
        return self._settings.vk_enabled

    async def close(self) -> None:
        await self._client.aclose()

    async def send_message(
        self,
        *,
        peer_id: int,
        message: str,
        keyboard: dict[str, Any] | None = None,
        disable_mentions: bool = True,
    ) -> int:
        params: dict[str, Any] = {
            "peer_id": peer_id,
            "message": message,
            "random_id": secrets.randbelow(2_147_483_647),
        }
        if keyboard is not None:
            params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
        if disable_mentions:
            params["disable_mentions"] = 1
        response = await self.call("messages.send", params)
        if isinstance(response, int):
            return response
        return int(response[0]["message_id"]) if isinstance(response, list) and response else 0

    async def get_user_profile(self, user_id: int) -> dict[str, Any] | None:
        response = await self.call("users.get", {"user_ids": user_id, "fields": "screen_name"})
        if isinstance(response, list) and response:
            item = response[0]
            if isinstance(item, dict):
                return item
        return None

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        if not self.enabled:
            raise RuntimeError("VK integration is not configured.")

        payload = {
            **params,
            "access_token": self._settings.vk_group_token.get_secret_value(),
            "v": self._settings.vk_api_version,
        }
        response = await self._client.post(f"{self._base_url}{method}", data=payload)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            error = data["error"]
            raise RuntimeError(f"VK API error {error.get('error_code')}: {error.get('error_msg')}")
        return data.get("response")
