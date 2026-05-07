from __future__ import annotations

import json

from app.config import Settings
from app.db.models import OrderStatus, UserChannel
from app.services.payments.donate_stream import DonateStreamPaymentProvider
from app.services.vk_bot import VkBotService
from tests.conftest import build_services, create_available_key, seed_default_plan


async def test_vk_bot_returns_confirmation_token(db, settings, fake_bot, fake_vk):
    services = build_services(db, settings, fake_bot, fake_vk=fake_vk)
    vk_bot = VkBotService(settings, fake_vk, services["notification"])

    result = await vk_bot.handle_event({"type": "confirmation", "group_id": settings.vk_group_id}, services)

    assert result == settings.vk_confirmation_token


async def test_vk_bot_creates_vk_user_and_order(db, settings, fake_bot, fake_vk):
    plan = await seed_default_plan(db, settings)
    services = build_services(db, settings, fake_bot, fake_vk=fake_vk)
    await create_available_key(db, services["protector"], plan.id, "vpn://vk-order")
    fake_vk.profiles[555001] = {
        "first_name": "Yulia",
        "last_name": "VK",
        "screen_name": "vkbuyer",
    }
    vk_bot = VkBotService(settings, fake_vk, services["notification"])

    result = await vk_bot.handle_event(
        {
            "type": "message_new",
            "group_id": settings.vk_group_id,
            "object": {
                "message": {
                    "from_id": 555001,
                    "peer_id": 555001,
                    "text": "",
                    "payload": json.dumps(
                        {
                            "command": "confirm_buy",
                            "plan_id": plan.id,
                            "mode": "inventory",
                        }
                    ),
                }
            },
        },
        services,
    )

    user = await services["users_repo"].get_by_vk_user_id(555001)
    orders = await services["orders"].list_user_orders(user.id)

    assert result == "ok"
    assert user is not None
    assert user.delivery_channel == UserChannel.VK.value
    assert orders
    assert orders[0].status == OrderStatus.PENDING_PAYMENT.value
    assert any(message["peer_id"] == 555001 for message in fake_vk.messages)
    assert fake_bot.messages


async def test_manual_confirmation_delivers_to_vk_customer(db, settings, fake_bot, fake_vk):
    plan = await seed_default_plan(db, settings)
    donate_settings = Settings(**{**settings.model_dump(), "payment_provider": "donate_stream"})
    services = build_services(
        db,
        donate_settings,
        fake_bot,
        payment_provider=DonateStreamPaymentProvider(donate_settings),
        fake_vk=fake_vk,
    )
    fake_vk.profiles[777123] = {
        "first_name": "Vika",
        "last_name": "Buyer",
        "screen_name": "vkclient",
    }
    user = await services["users"].ensure_from_vk(vk_user_id=777123, username="vkclient", full_name="Vika Buyer")
    await db.commit()
    await create_available_key(db, services["protector"], plan.id, "vpn://vk-delivery")

    order_id, payment_url = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order_status, issued_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    delivered = await services["delivery"].process_pending_jobs()

    assert payment_url == donate_settings.donate_stream_url
    assert order_status == OrderStatus.ISSUED.value
    assert issued_key_id is not None
    assert delivered == 1
    assert any(message["peer_id"] == 777123 and "vpn://vk-delivery" in message["message"] for message in fake_vk.messages)
    assert any(donate_settings.setup_guide_url in message["message"] for message in fake_vk.messages)
