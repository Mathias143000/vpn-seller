from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.keyboards.admin import build_hiddify_load_actions
from tests.conftest import build_services, create_hiddify_server


async def test_usage_snapshot_collection_persists_monthly_summary(db, settings, fake_bot, monkeypatch):
    services = build_services(db, settings, fake_bot)
    server = await create_hiddify_server(
        db,
        services["protector"],
        name="usage-de",
        country_name="Germany",
        base_url="https://usage-de.example.com",
        admin_proxy_path="admin-usage-de",
        client_proxy_path="client-usage-de",
    )
    hiddify = services["hiddify"]
    sampled_at = datetime.now(tz=timezone.utc).replace(microsecond=0)

    async def fake_list_remote_users(**kwargs):
        return [
            {"enable": True, "current_usage_GB": 12},
            {"enable": False, "current_usage_GB": 6},
        ]

    monkeypatch.setattr(hiddify, "_list_remote_users", fake_list_remote_users)

    loads = await hiddify.collect_usage_snapshots(server_id=server.id, now=sampled_at)
    assert len(loads) == 1
    assert loads[0].active_users_percent == 50
    assert loads[0].monthly_snapshots_count == 1
    assert loads[0].monthly_average_user_usage_gb == 9
    assert loads[0].monthly_average_total_usage_gb == 18

    snapshot = await services["hiddify_usage_snapshots_repo"].latest_for_server(server.id)
    assert snapshot is not None
    stored_sampled_at = snapshot.sampled_at
    if stored_sampled_at.tzinfo is None:
        stored_sampled_at = stored_sampled_at.replace(tzinfo=timezone.utc)
    assert stored_sampled_at == sampled_at
    assert snapshot.active_users_count == 1
    assert snapshot.total_users_count == 2
    assert float(snapshot.active_users_percent) == 50
    assert float(snapshot.total_current_usage_gb) == 18
    assert float(snapshot.average_user_usage_gb) == 9

    load_card = await hiddify.list_server_load()
    by_id = {item.server_id: item for item in load_card}
    assert by_id[server.id].monthly_snapshots_count == 1
    assert by_id[server.id].monthly_average_user_usage_gb == 9
    assert by_id[server.id].monthly_average_active_users_percent == 50


async def test_usage_monitor_collects_due_snapshots_and_deduplicates_alerts(db, settings, fake_bot, monkeypatch):
    settings.hiddify_usage_snapshot_interval_minutes = 60
    settings.hiddify_active_users_alert_percent = 80
    settings.hiddify_average_monthly_usage_alert_gb = 15
    settings.hiddify_active_users_alert_percent_by_country = ""
    settings.hiddify_average_monthly_usage_alert_gb_by_country = ""
    settings.hiddify_alert_cooldown_minutes = 360
    services = build_services(db, settings, fake_bot)
    server = await create_hiddify_server(
        db,
        services["protector"],
        name="usage-alert",
        country_name="Netherlands",
        base_url="https://usage-alert.example.com",
        admin_proxy_path="admin-usage-alert",
        client_proxy_path="client-usage-alert",
    )
    hiddify = services["hiddify"]
    sampled_at = datetime.now(tz=timezone.utc).replace(microsecond=0)

    async def fake_list_remote_users(**kwargs):
        return [
            {"enable": True, "current_usage_GB": 12},
            {"enable": True, "current_usage_GB": 8},
        ]

    monkeypatch.setattr(hiddify, "_list_remote_users", fake_list_remote_users)

    first_batch = await services["hiddify_usage"].collect_due_snapshots(now=sampled_at)
    assert [item.server_id for item in first_batch] == [server.id]
    alert_messages = [message for _, message in fake_bot.messages if message.startswith("[ALERT]")]
    assert len(alert_messages) == 4
    assert any("active-user capacity" in message for message in alert_messages)
    assert any("traffic capacity" in message for message in alert_messages)

    second_batch = await services["hiddify_usage"].collect_due_snapshots(now=sampled_at + timedelta(minutes=61))
    assert [item.server_id for item in second_batch] == [server.id]
    assert len([message for _, message in fake_bot.messages if message.startswith("[ALERT]")]) == 4

    snapshots = await services["hiddify_usage_snapshots_repo"].list_for_server_since(
        server_id=server.id,
        since=sampled_at - timedelta(minutes=1),
    )
    assert len(snapshots) == 2


async def test_usage_monitor_collect_snapshots_now_ignores_interval_and_deduplicates_alerts(
    db,
    settings,
    fake_bot,
    monkeypatch,
):
    settings.hiddify_usage_snapshot_interval_minutes = 60
    settings.hiddify_active_users_alert_percent = 80
    settings.hiddify_average_monthly_usage_alert_gb = 15
    settings.hiddify_active_users_alert_percent_by_country = ""
    settings.hiddify_average_monthly_usage_alert_gb_by_country = ""
    settings.hiddify_alert_cooldown_minutes = 360
    services = build_services(db, settings, fake_bot)
    server = await create_hiddify_server(
        db,
        services["protector"],
        name="usage-manual",
        country_name="Netherlands",
        base_url="https://usage-manual.example.com",
        admin_proxy_path="admin-usage-manual",
        client_proxy_path="client-usage-manual",
    )
    hiddify = services["hiddify"]
    sampled_at = datetime.now(tz=timezone.utc).replace(microsecond=0)

    async def fake_list_remote_users(**kwargs):
        return [
            {"enable": True, "current_usage_GB": 12},
            {"enable": True, "current_usage_GB": 8},
        ]

    monkeypatch.setattr(hiddify, "_list_remote_users", fake_list_remote_users)

    first_batch = await services["hiddify_usage"].collect_snapshots_now(now=sampled_at)
    second_batch = await services["hiddify_usage"].collect_snapshots_now(now=sampled_at + timedelta(minutes=1))

    assert [item.server_id for item in first_batch] == [server.id]
    assert [item.server_id for item in second_batch] == [server.id]
    assert len([message for _, message in fake_bot.messages if message.startswith("[ALERT]")]) == 4

    snapshots = await services["hiddify_usage_snapshots_repo"].list_for_server_since(
        server_id=server.id,
        since=sampled_at - timedelta(minutes=1),
    )
    assert len(snapshots) == 2


def test_hiddify_load_actions_expose_manual_snapshot_collection():
    markup = build_hiddify_load_actions()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert "admin:hiddify:snapshots:collect" in callbacks


async def test_usage_monitor_does_not_alert_location_when_one_server_has_capacity(db, settings, fake_bot, monkeypatch):
    settings.hiddify_usage_snapshot_interval_minutes = 60
    settings.hiddify_active_users_alert_percent = 80
    settings.hiddify_average_monthly_usage_alert_gb = 15
    settings.hiddify_active_users_alert_percent_by_country = ""
    settings.hiddify_average_monthly_usage_alert_gb_by_country = ""
    services = build_services(db, settings, fake_bot)
    hot = await create_hiddify_server(
        db,
        services["protector"],
        name="de-hot",
        country_name="Germany",
        base_url="https://de-hot.example.com",
        admin_proxy_path="admin-de-hot",
        client_proxy_path="client-de-hot",
    )
    spare = await create_hiddify_server(
        db,
        services["protector"],
        name="de-spare",
        country_name="Germany",
        base_url="https://de-spare.example.com",
        admin_proxy_path="admin-de-spare",
        client_proxy_path="client-de-spare",
    )
    hiddify = services["hiddify"]
    sampled_at = datetime.now(tz=timezone.utc).replace(microsecond=0)

    async def fake_list_remote_users(**kwargs):
        if kwargs["server"].id == hot.id:
            return [
                {"enable": True, "current_usage_GB": 12},
                {"enable": True, "current_usage_GB": 8},
            ]
        if kwargs["server"].id == spare.id:
            return [
                {"enable": True, "current_usage_GB": 6},
                {"enable": False, "current_usage_GB": 2},
            ]
        raise AssertionError("Unexpected server")

    monkeypatch.setattr(hiddify, "_list_remote_users", fake_list_remote_users)

    first_batch = await services["hiddify_usage"].collect_due_snapshots(now=sampled_at)
    assert {item.server_id for item in first_batch} == {hot.id, spare.id}
    assert [message for _, message in fake_bot.messages if message.startswith("[ALERT]")] == []
    capacity_statuses = services["hiddify_usage"].build_location_capacity_status(first_batch)
    assert len(capacity_statuses) == 1
    assert capacity_statuses[0].country_name == "Germany"
    assert capacity_statuses[0].capacity_needed is False
    assert capacity_statuses[0].active_users_status == "watch"
    assert capacity_statuses[0].usage_status == "watch"


async def test_usage_monitor_uses_country_threshold_overrides(db, settings, fake_bot, monkeypatch):
    settings.hiddify_usage_snapshot_interval_minutes = 60
    settings.hiddify_active_users_alert_percent = 95
    settings.hiddify_average_monthly_usage_alert_gb = 1000
    settings.hiddify_active_users_alert_percent_by_country = "DE=70"
    settings.hiddify_average_monthly_usage_alert_gb_by_country = "DE=15"
    services = build_services(db, settings, fake_bot)
    first = await create_hiddify_server(
        db,
        services["protector"],
        name="de-1",
        country_name="Germany",
        base_url="https://de-1.example.com",
        admin_proxy_path="admin-de-1",
        client_proxy_path="client-de-1",
    )
    second = await create_hiddify_server(
        db,
        services["protector"],
        name="de-2",
        country_name="Germany",
        base_url="https://de-2.example.com",
        admin_proxy_path="admin-de-2",
        client_proxy_path="client-de-2",
    )
    hiddify = services["hiddify"]
    sampled_at = datetime.now(tz=timezone.utc).replace(microsecond=0)

    async def fake_list_remote_users(**kwargs):
        return [
            {"enable": True, "current_usage_GB": 12},
            {"enable": True, "current_usage_GB": 8},
        ]

    monkeypatch.setattr(hiddify, "_list_remote_users", fake_list_remote_users)

    first_batch = await services["hiddify_usage"].collect_due_snapshots(now=sampled_at)
    assert {item.server_id for item in first_batch} == {first.id, second.id}
    alert_messages = [message for _, message in fake_bot.messages if message.startswith("[ALERT]")]
    assert len(alert_messages) == 4
    assert all("Location: Germany" in message for message in alert_messages)
    capacity_statuses = services["hiddify_usage"].build_location_capacity_status(first_batch)
    assert len(capacity_statuses) == 1
    assert capacity_statuses[0].country_name == "Germany"
    assert capacity_statuses[0].capacity_needed is True
    assert capacity_statuses[0].active_users_status == "needs_capacity"
    assert capacity_statuses[0].usage_status == "needs_capacity"
