from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx

from tests.conftest import build_services, create_user, seed_default_plan


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


async def test_superkey_subscription_aggregator_merges_unique_links(db, settings, fake_bot, monkeypatch):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db, telegram_user_id=111222)
    services = build_services(db, settings, fake_bot)

    token = "abc123super"
    payload = {
        "delivery_kind": "hiddify_superkey",
        "subscription_url": f"https://bot.example/subscriptions/{token}",
        "deeplink_url": f"hiddify://import/https://bot.example/subscriptions/{token}",
        "included_countries": ["Germany", "Netherlands"],
        "sources": [
            {"subscription_url": "https://one.example/sub"},
            {"subscription_url": "https://two.example/sub"},
        ],
    }
    vpn_key = await services["vpn_keys_repo"].create_generated_issued_key(
        plan_id=plan.id,
        key_value_encrypted=services["protector"].encrypt(json.dumps(payload)),
        key_fingerprint=services["protector"].fingerprint(payload["subscription_url"]),
        user_id=user.id,
        issued_at=datetime.now(tz=timezone.utc),
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
        external_ref=f"hiddify-superkey:{token}",
        comment="superkey",
    )
    assert vpn_key.id is not None

    async def fake_get(self, url: str, *args, **kwargs):
        if url == "https://one.example/sub":
            return FakeResponse(base64.b64encode(b"vmess://one\nvmess://shared").decode("utf-8"))
        if url == "https://two.example/sub":
            return FakeResponse("vmess://shared\nvmess://two")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    merged_payload = await services["subscriptions"].build_subscription_payload(token)
    merged_text = base64.b64decode(merged_payload).decode("utf-8")

    assert merged_text.splitlines() == ["vmess://one", "vmess://shared", "vmess://two"]
