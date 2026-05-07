from __future__ import annotations

from datetime import date, timedelta

from app.db.models import ServerHealthStatus
from tests.conftest import build_services, create_hiddify_server, create_user


async def test_hiddify_provision_uses_actual_remote_user_uuid_for_links(db, settings, fake_bot, monkeypatch):
    services = build_services(db, settings, fake_bot)
    server = await create_hiddify_server(
        db,
        services["protector"],
        name="USA",
        country_name="США",
        base_url="https://panel.example.com",
        admin_proxy_path="admin-secret",
        client_proxy_path="client-secret",
    )
    user = await create_user(db, telegram_user_id=700001)
    hiddify_service = services["hiddify"]

    async def fake_ensure_remote_user(**kwargs):
        return {
            "uuid": "843d2ac9-d13e-43da-9b95-5d0ee4bf33bb",
            "name": "MSuprema13 30-дней 75c0db06",
        }

    monkeypatch.setattr(hiddify_service, "_ensure_remote_user", fake_ensure_remote_user)

    access = await hiddify_service.provision_for_order(
        order_id=1,
        user=user,
        plan_name="30-дней",
        duration_days=30,
        amount_value="299.00",
        amount_currency="RUB",
        preferred_server_id=server.id,
    )

    assert access.remote_user_uuid == "843d2ac9-d13e-43da-9b95-5d0ee4bf33bb"
    assert access.subscription_url == "https://panel.example.com/client-secret/843d2ac9-d13e-43da-9b95-5d0ee4bf33bb/sub/"
    assert access.panel_url == "https://panel.example.com/client-secret/843d2ac9-d13e-43da-9b95-5d0ee4bf33bb/#MSuprema13-30-дней-75c0db06"
    assert access.deeplink_url == (
        "hiddify://import/https://panel.example.com/client-secret/843d2ac9-d13e-43da-9b95-5d0ee4bf33bb/sub/"
        "#MSuprema13-30-дней-75c0db06"
    )


async def test_superkey_provisions_every_active_server_not_only_one_per_country(db, settings, fake_bot, monkeypatch):
    services = build_services(db, settings, fake_bot)
    first = await create_hiddify_server(
        db,
        services["protector"],
        name="de-1",
        country_name="Germany",
        base_url="https://de1.example.com",
        admin_proxy_path="admin-de-1",
        client_proxy_path="client-de-1",
    )
    second = await create_hiddify_server(
        db,
        services["protector"],
        name="de-2",
        country_name="Germany",
        base_url="https://de2.example.com",
        admin_proxy_path="admin-de-2",
        client_proxy_path="client-de-2",
    )
    third = await create_hiddify_server(
        db,
        services["protector"],
        name="nl-1",
        country_name="Netherlands",
        base_url="https://nl1.example.com",
        admin_proxy_path="admin-nl-1",
        client_proxy_path="client-nl-1",
    )
    user = await create_user(db, telegram_user_id=700002)
    hiddify_service = services["hiddify"]

    async def fake_ensure_remote_user(**kwargs):
        return {"uuid": f"remote-{kwargs['server'].id}", "name": f"profile-{kwargs['server'].id}"}

    monkeypatch.setattr(hiddify_service, "_ensure_remote_user", fake_ensure_remote_user)

    access = await hiddify_service.provision_superkey_for_order(
        order_id=2,
        user=user,
        plan_name="30-дней",
        duration_days=30,
        amount_value="299.00",
        amount_currency="RUB",
    )

    assert [source.server_id for source in access.sources] == [first.id, second.id, third.id]
    assert access.included_countries == ("Germany", "Netherlands")


async def test_server_load_snapshot_marks_least_loaded_mtproxy_candidate(db, settings, fake_bot, monkeypatch):
    services = build_services(db, settings, fake_bot)
    busy = await create_hiddify_server(
        db,
        services["protector"],
        name="busy",
        country_name="Germany",
        base_url="https://busy.example.com",
        admin_proxy_path="admin-busy",
        client_proxy_path="client-busy",
    )
    quiet = await create_hiddify_server(
        db,
        services["protector"],
        name="quiet",
        country_name="Netherlands",
        base_url="https://quiet.example.com",
        admin_proxy_path="admin-quiet",
        client_proxy_path="client-quiet",
    )
    inactive = await create_hiddify_server(
        db,
        services["protector"],
        name="inactive",
        country_name="Finland",
        base_url="https://inactive.example.com",
        admin_proxy_path="admin-inactive",
        client_proxy_path="client-inactive",
        is_active=False,
    )
    hiddify_service = services["hiddify"]
    two_months_ago = (date.today() - timedelta(days=60)).isoformat()
    one_month_ago = (date.today() - timedelta(days=30)).isoformat()

    async def fake_list_remote_users(**kwargs):
        server = kwargs["server"]
        if server.id == busy.id:
            return [
                {"enable": True, "current_usage_GB": 60, "start_date": two_months_ago},
                {"enable": True, "current_usage_GB": "30", "start_date": one_month_ago},
                {"enable": True, "current_usage_GB": 0},
            ]
        if server.id == quiet.id:
            return [
                {"enable": True, "current_usage_GB": 10},
                {"enable": False, "current_usage_GB": 0},
            ]
        raise AssertionError("Inactive servers must not be queried for live load")

    monkeypatch.setattr(hiddify_service, "_list_remote_users", fake_list_remote_users)

    snapshots = await hiddify_service.list_server_load()
    by_id = {snapshot.server_id: snapshot for snapshot in snapshots}

    assert by_id[busy.id].active_users_count == 3
    assert by_id[busy.id].total_users_count == 3
    assert by_id[busy.id].active_users_percent == 100
    assert by_id[busy.id].total_current_usage_gb == 90
    assert 19 <= by_id[busy.id].average_monthly_usage_gb <= 20
    assert by_id[busy.id].usage_sample_users_count == 3
    assert by_id[busy.id].mtproxy_available is True
    assert by_id[quiet.id].active_users_count == 1
    assert by_id[quiet.id].total_users_count == 2
    assert by_id[quiet.id].active_users_percent == 50
    assert by_id[quiet.id].average_monthly_usage_gb == 5
    assert by_id[quiet.id].selected_for_mtproxy is True
    assert by_id[inactive.id].active_users_count is None
    assert by_id[inactive.id].mtproxy_available is False

    refreshed_quiet = await services["hiddify_servers_repo"].get_by_id(quiet.id)
    assert refreshed_quiet.last_health_status == ServerHealthStatus.HEALTHY.value
