from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.vpn_keys import VPNKeysRepository
from app.services.exceptions import NotFoundError, ProvisioningError
from app.services.security import KeyProtector


class SubscriptionAggregatorService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        vpn_keys_repo: VPNKeysRepository,
        key_protector: KeyProtector,
    ) -> None:
        self._session = session
        self._vpn_keys_repo = vpn_keys_repo
        self._key_protector = key_protector

    async def build_subscription_payload(self, token: str) -> bytes:
        payload = await self._load_superkey_payload(token)
        merged_lines: list[str] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            for source in payload["sources"]:
                response = await client.get(source["subscription_url"])
                response.raise_for_status()
                for line in self._extract_subscription_lines(response.text):
                    if line not in seen:
                        seen.add(line)
                        merged_lines.append(line)

        if not merged_lines:
            raise ProvisioningError("The superkey does not contain any active subscriptions.")

        merged_text = "\n".join(merged_lines)
        return base64.b64encode(merged_text.encode("utf-8"))

    async def _load_superkey_payload(self, token: str) -> dict:
        vpn_key = await self._vpn_keys_repo.get_by_external_ref(f"hiddify-superkey:{token}")
        if vpn_key is None:
            raise NotFoundError("Superkey subscription not found.")
        if vpn_key.expires_at and vpn_key.expires_at < datetime.now(tz=timezone.utc):
            raise NotFoundError("Superkey subscription has expired.")

        decrypted = self._key_protector.decrypt(vpn_key.key_value_encrypted)
        try:
            payload = json.loads(decrypted)
        except json.JSONDecodeError as exc:
            raise NotFoundError("Stored superkey payload is invalid.") from exc

        if payload.get("delivery_kind") != "hiddify_superkey":
            raise NotFoundError("This subscription token is not a superkey.")
        if not isinstance(payload.get("sources"), list) or not payload["sources"]:
            raise NotFoundError("This superkey has no source subscriptions.")
        return payload

    @staticmethod
    def _extract_subscription_lines(raw_payload: str) -> list[str]:
        payload = raw_payload.strip()
        if not payload:
            return []

        decoded = SubscriptionAggregatorService._try_base64_decode(payload)
        text = decoded if decoded is not None else payload
        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _try_base64_decode(value: str) -> str | None:
        compact = "".join(value.split())
        if not compact or "://" in compact:
            return None

        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                padding = "=" * (-len(compact) % 4)
                decoded = decoder((compact + padding).encode("utf-8"))
                text = decoded.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                continue
            normalized = text.strip()
            if normalized and ("://" in normalized or "\n" in normalized or normalized.startswith("proxies:")):
                return text
        return None
