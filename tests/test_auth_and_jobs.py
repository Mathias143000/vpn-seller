from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import DeliveryJobStatus, Order, OrderStatus, User
from app.services.users import UsersService
from tests.conftest import build_services, create_available_key, create_user, seed_default_plan


async def test_admin_authorization(db, settings, fake_bot):
    await create_user(db, telegram_user_id=111, role="user")
    admin = await create_user(db, telegram_user_id=999001, role="superadmin")
    services = build_services(db, settings, fake_bot)
    regular = await services["users_repo"].get_by_telegram_user_id(111)

    assert UsersService.is_admin(admin) is True
    assert UsersService.is_admin(regular) is False


async def test_reservation_expiration_job_releases_key(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    vpn_key = await create_available_key(db, services["protector"], plan.id, "vpn://reserved")
    order = Order(
        user_id=user.id,
        plan_id=plan.id,
        status=OrderStatus.PENDING_PAYMENT.value,
        amount_value=plan.price_value,
        amount_currency=plan.price_currency,
        payment_provider="fake",
        reserved_key_id=vpn_key.id,
        reservation_expires_at=datetime.now(tz=timezone.utc) - timedelta(minutes=20),
    )
    db.add(order)
    await db.commit()
    vpn_key.status = "reserved"
    vpn_key.reserved_by_order_id = order.id
    await db.commit()

    released = await services["inventory"].cleanup_expired_reservations()

    refreshed_order = await services["orders_repo"].get_by_id(order.id)
    refreshed_key = await services["vpn_keys_repo"].get_by_id(vpn_key.id)
    assert released == 1
    assert refreshed_order.status == OrderStatus.EXPIRED_RESERVATION.value
    assert refreshed_key.status == "available"


async def test_delivery_jobs_retry_and_complete(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://delivery")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)
    await services["payments"].handle_webhook(
        {
            "provider_payment_id": order.provider_payment_id,
            "order_id": order.id,
            "amount_value": str(order.amount_value),
            "amount_currency": order.amount_currency,
            "status": "succeeded",
            "provider_event_id": "delivery-event",
        }
    )

    fake_bot.failures_remaining = 1
    first_run = await services["delivery"].process_pending_jobs()
    second_run = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order.id)
    assert first_run == 0
    assert second_run == 1
    assert refreshed.delivery_status == DeliveryJobStatus.DELIVERED.value


async def test_delivery_job_with_invalid_encryption_marks_failed(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    user = await create_user(db)
    services = build_services(db, settings, fake_bot)
    await create_available_key(db, services["protector"], plan.id, "vpn://invalid-encryption")
    order_id, _ = await services["orders"].create_order_with_payment(user_id=user.id, plan_id=plan.id)
    order = await services["orders_repo"].get_by_id(order_id)
    await services["payments"].handle_webhook(
        {
            "provider_payment_id": order.provider_payment_id,
            "order_id": order.id,
            "amount_value": str(order.amount_value),
            "amount_currency": order.amount_currency,
            "status": "succeeded",
            "provider_event_id": "invalid-encryption-event",
        }
    )

    issued_key = await services["vpn_keys_repo"].get_by_id(order.issued_key_id)
    issued_key.key_value_encrypted = "definitely-not-a-valid-token"
    await db.commit()

    delivered = await services["delivery"].process_pending_jobs()

    refreshed = await services["orders_repo"].get_by_id(order.id)
    assert delivered == 0
    assert refreshed.delivery_status == DeliveryJobStatus.FAILED.value


async def test_broadcast_reaches_only_customers(db, settings, fake_bot):
    plan = await seed_default_plan(db, settings)
    customer = await create_user(db, telegram_user_id=7001)
    outsider = await create_user(db, telegram_user_id=7002)
    services = build_services(db, settings, fake_bot)

    order = Order(
        user_id=customer.id,
        plan_id=plan.id,
        status=OrderStatus.CREATED.value,
        amount_value=plan.price_value,
        amount_currency=plan.price_currency,
        payment_provider="fake",
    )
    db.add(order)
    await db.commit()

    fake_bot.messages.clear()
    fake_bot.calls.clear()
    result = await services["communications"].broadcast_to_customers(
        text="Тестовое уведомление для клиентов",
        actor_user_id=None,
    )

    assert result["total"] == 1
    assert result["sent"] == 1
    assert any(
        chat_id == customer.telegram_user_id and "Тестовое уведомление для клиентов" in text
        for chat_id, text in fake_bot.messages
    )
    assert all(chat_id != outsider.telegram_user_id for chat_id, _ in fake_bot.messages)
