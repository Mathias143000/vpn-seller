from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from app.db.models import AuditLog
from tests.conftest import (
    build_services,
    create_hiddify_server,
    create_user,
    make_hiddify_servers_xlsx,
)


class FakeHiddifyRegistrationService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._next_id = 100

    async def register_server(self, **kwargs):
        self.calls.append(kwargs)
        self._next_id += 1
        return SimpleNamespace(
            id=self._next_id,
            name=kwargs["name"],
            country_name=kwargs["country_name"],
            base_url=kwargs["base_url"],
            admin_proxy_path=kwargs["admin_proxy_path"],
            client_proxy_path=kwargs["client_proxy_path"],
            is_active=kwargs["is_active"],
            panel_version="test-panel",
        )


async def test_hiddify_xlsx_preview_rejects_duplicates_and_existing_server(db, settings, fake_bot):
    services = build_services(db, settings, fake_bot)
    await create_hiddify_server(
        db,
        services["protector"],
        base_url="https://existing.example.com",
        admin_proxy_path="existing-admin",
        client_proxy_path="existing-client",
    )
    content = make_hiddify_servers_xlsx(
        [
            {
                "name": "USA #1",
                "country_name": "USA",
                "base_url": "https://dup.example.com",
                "admin_proxy_path": "admin-one",
                "client_proxy_path": "client-one",
                "api_key": "key-1",
                "is_active": True,
            },
            {
                "name": "USA #2",
                "country_name": "USA",
                "base_url": "https://dup.example.com",
                "admin_proxy_path": "admin-one",
                "client_proxy_path": "client-two",
                "api_key": "key-2",
                "is_active": True,
            },
            {
                "name": "Existing",
                "country_name": "Germany",
                "base_url": "https://existing.example.com",
                "admin_proxy_path": "existing-admin",
                "client_proxy_path": "existing-client",
                "api_key": "key-3",
                "is_active": True,
            },
        ]
    )

    preview = await services["hiddify_xlsx_import"].preview(filename="servers.xlsx", content=content)

    assert preview["rows_valid"] == 0
    assert preview["rows_rejected"] == 3


async def test_hiddify_xlsx_import_registers_servers_and_writes_audit(db, settings, fake_bot):
    user = await create_user(db, telegram_user_id=880001, role="admin")
    fake_hiddify = FakeHiddifyRegistrationService()
    services = build_services(db, settings, fake_bot, hiddify_service=fake_hiddify)
    content = make_hiddify_servers_xlsx(
        [
            {
                "name": "USA #1",
                "country_name": "USA",
                "base_url": "https://usa.example.com",
                "admin_proxy_path": "admin-usa",
                "client_proxy_path": "client-usa",
                "api_key": "real-api-key",
                "is_active": "FALSE",
            },
            {
                "name": "Broken row",
                "country_name": "NL",
                "base_url": "https://nl.example.com",
                "admin_proxy_path": "admin-nl",
                "client_proxy_path": "client-nl",
                "api_key": "",
                "is_active": True,
            },
        ]
    )

    result = await services["hiddify_xlsx_import"].import_file(
        filename="servers.xlsx",
        content=content,
        uploaded_by_user_id=user.id,
    )

    audit_logs = list((await db.scalars(select(AuditLog).order_by(AuditLog.id.asc()))))

    assert result["rows_imported"] == 1
    assert result["rows_rejected"] == 1
    assert fake_hiddify.calls[0]["is_active"] is False
    assert any(
        log.action == "hiddify_xlsx_import_completed"
        and log.actor_user_id == user.id
        for log in audit_logs
    )
