from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from app.config import Settings


class WhatsAppApiClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = f"https://graph.facebook.com/{settings.whatsapp_api_version.strip('/')}"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))

    @property
    def enabled(self) -> bool:
        return self._settings.whatsapp_enabled

    async def close(self) -> None:
        await self._client.aclose()

    def verify_signature(self, body: bytes, signature_header: str | None) -> bool:
        app_secret = self._settings.whatsapp_app_secret.get_secret_value().strip()
        if not app_secret:
            return True
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature_header.split("=", maxsplit=1)[1], expected)

    async def send_text_message(self, *, to: str, body: str, preview_url: bool = False) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": preview_url},
        }
        response = await self._post_messages(payload)
        return self._extract_message_id(response)

    async def send_buttons(
        self,
        *,
        to: str,
        body: str,
        buttons: list[dict[str, str]],
    ) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": button["id"],
                                "title": button["title"][:20],
                            },
                        }
                        for button in buttons[:3]
                    ]
                },
            },
        }
        response = await self._post_messages(payload)
        return self._extract_message_id(response)

    async def send_list(
        self,
        *,
        to: str,
        body: str,
        button_text: str,
        sections: list[dict[str, Any]],
        header_text: str | None = None,
    ) -> str:
        interactive: dict[str, Any] = {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_text[:20],
                "sections": sections,
            },
        }
        if header_text:
            interactive["header"] = {"type": "text", "text": header_text[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        response = await self._post_messages(payload)
        return self._extract_message_id(response)

    async def _post_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("WhatsApp integration is not configured.")
        response = await self._client.post(
            f"{self._base_url}/{self._settings.whatsapp_phone_number_id}/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._settings.whatsapp_access_token.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            error = data["error"]
            raise RuntimeError(f"WhatsApp API error {error.get('code')}: {error.get('message')}")
        return data

    @staticmethod
    def _extract_message_id(payload: dict[str, Any]) -> str:
        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict):
                return str(first.get("id") or "")
        return ""
