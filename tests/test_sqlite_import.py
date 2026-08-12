from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.db.models import AuditLog, VPNKey
from tests.conftest import build_services, create_user, seed_default_plan


def make_typed_bundle(tmp_path: Path, rows: list[tuple]) -> bytes:
    path = tmp_path / "typed.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE export_meta (format_version TEXT NOT NULL, exported_at TEXT NOT NULL, source TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO export_meta VALUES ('golden-vpn.typed-keys.v1', '2026-08-12T00:00:00+00:00', 'test')"
    )
    connection.execute(
        """
        CREATE TABLE typed_keys (
          key_type TEXT NOT NULL,
          label TEXT NOT NULL,
          key_status TEXT NOT NULL,
          config_text TEXT NOT NULL,
          plan_code TEXT,
          external_ref TEXT,
          comment TEXT,
          expires_at TEXT
        )
        """
    )
    connection.executemany("INSERT INTO typed_keys VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()
    return path.read_bytes()


async def test_typed_sqlite_preview_and_import_preserve_type_and_status(db, settings, fake_bot, tmp_path):
    await seed_default_plan(db, settings)
    admin = await create_user(db, telegram_user_id=991001, role="admin")
    services = build_services(db, settings, fake_bot)
    content = make_typed_bundle(
        tmp_path,
        [
            (
                "awg",
                "AWG-NL-stock-0001",
                "available",
                "[Interface]\nPrivateKey = secret-one",
                "plan_30",
                "golden-vpn:NL:AWG-NL-stock-0001",
                "AWG stock",
                None,
            ),
            (
                "trojan",
                "TROJAN-NL-existing",
                "issued",
                "trojan://secret-two@example.invalid",
                "plan_30",
                "golden-vpn:NL:TROJAN-NL-existing",
                "Existing Trojan",
                "2026-12-31T00:00:00+00:00",
            ),
            (
                "vless",
                "VLESS-SE-existing",
                "issued",
                "vless://secret-three@example.invalid",
                "plan_30",
                "legacy:SE:VLESS-SE-existing",
                "Existing VLESS",
                None,
            ),
            (
                "ikev2",
                "IKEV2-SE-existing",
                "issued",
                "IKEv2 client bundle\nusername: test",
                "plan_30",
                "legacy:SE:IKEV2-SE-existing",
                "Existing IKEv2",
                None,
            ),
        ],
    )

    preview = await services["sqlite_import"].preview(filename="typed.sqlite", content=content)
    assert preview["rows_valid"] == 4
    assert preview["types"] == {"awg": 1, "trojan": 1, "vless": 1, "ikev2": 1}
    assert preview["statuses"] == {"available": 1, "issued": 3}

    result = await services["sqlite_import"].import_file(
        filename="typed.sqlite", content=content, uploaded_by_user_id=admin.id
    )
    keys = list(await db.scalars(select(VPNKey).order_by(VPNKey.id)))
    assert result["rows_imported"] == 4
    assert [(key.key_type, key.status) for key in keys] == [
        ("awg", "available"),
        ("trojan", "issued"),
        ("vless", "issued"),
        ("ikev2", "issued"),
    ]
    assert services["protector"].decrypt(keys[0].key_value_encrypted).startswith("[Interface]")
    audit_logs = list(await db.scalars(select(AuditLog)))
    assert any(log.action == "sqlite_typed_keys_import_completed" for log in audit_logs)


async def test_typed_sqlite_rejects_unknown_type_and_duplicate(db, settings, fake_bot, tmp_path):
    await seed_default_plan(db, settings)
    services = build_services(db, settings, fake_bot)
    content = make_typed_bundle(
        tmp_path,
        [
            ("unknown", "one", "available", "same-key", "plan_30", None, None, None),
            ("awg", "two", "available", "same-key", "plan_30", None, None, None),
        ],
    )
    preview = await services["sqlite_import"].preview(filename="typed.sqlite", content=content)
    assert preview["rows_valid"] == 0
    assert preview["rows_rejected"] == 2
