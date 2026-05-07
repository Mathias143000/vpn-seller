from __future__ import annotations

from app.config import Settings
from app.db.models import OrderStatus, UserChannel
from app.services.payments.donate_stream import DonateStreamPaymentProvider
from app.services.whatsapp_bot import WhatsAppBotService
from tests.conftest import build_services, create_available_key, seed_default_plan


async def test_whatsapp_bot_creates_user_and_order(db, settings, fake_bot, fake_whatsapp):
    plan = await seed_default_plan(db, settings)
    services = build_services(db, settings, fake_bot, fake_whatsapp=fake_whatsapp)
    await create_available_key(db, services["protector"], plan.id, "vpn://wa-order")
    wa_bot = WhatsAppBotService(settings, fake_whatsapp, services["notification"])

    await wa_bot.handle_webhook(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"profile": {"name": "WA Buyer"}, "wa_id": "79001234567"}],
                                "messages": [
                                    {
                                        "from": "79001234567",
                                        "id": "wamid.1",
                                        "type": "interactive",
                                        "interactive": {"button_reply": {"id": f"confirm_buy:{plan.id}:inventory", "title": "Оформить"}},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        },
        services,
    )

    user = await services["users_repo"].get_by_whatsapp_phone("79001234567")
    orders = await services["orders"].list_user_orders(user.id)

    assert user is not None
    assert user.delivery_channel == UserChannel.WHATSAPP.value
    assert orders
    assert orders[0].status == OrderStatus.PENDING_PAYMENT.value
    assert any(message["to"] == "79001234567" for message in fake_whatsapp.messages)


async def test_manual_confirmation_delivers_to_whatsapp_customer(db, settings, fake_bot, fake_whatsapp):
    plan = await seed_default_plan(db, settings)
    donate_settings = Settings(**{**settings.model_dump(), "payment_provider": "donate_stream"})
    services = build_services(
        db,
        donate_settings,
        fake_bot,
        payment_provider=DonateStreamPaymentProvider(donate_settings),
        fake_whatsapp=fake_whatsapp,
    )
    user = await services["users"].ensure_from_whatsapp(
        whatsapp_phone="79007654321",
        username=None,
        full_name="WA Customer",
    )
    await db.commit()
    await create_available_key(db, services["protector"], plan.id, "vpn://wa-delivery")

    order_id, payment_url = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order_status, issued_key_id = await services["payments"].confirm_manual_payment(order_id=order_id, actor_user_id=1)
    delivered = await services["delivery"].process_pending_jobs()

    assert payment_url == donate_settings.donate_stream_url
    assert order_status == OrderStatus.ISSUED.value
    assert issued_key_id is not None
    assert delivered == 1
    assert any(message["to"] == "79007654321" and "vpn://wa-delivery" in message["body"] for message in fake_whatsapp.messages)
    assert any(donate_settings.setup_guide_url in message["body"] for message in fake_whatsapp.messages)
