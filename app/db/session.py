from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.models import Base


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    path = database_url.split("///", maxsplit=1)[-1]
    if path.startswith("./"):
        path = path[2:]
    if not path or path == ":memory:":
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_engine(settings: Settings) -> AsyncEngine:
    _ensure_sqlite_parent(settings.database_url)
    return create_async_engine(settings.database_url, future=True, echo=False)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def ensure_runtime_compatibility(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        if engine.url.drivername.startswith("sqlite"):
            await _ensure_sqlite_column(
                connection,
                table_name="users",
                column_name="vk_user_id",
                ddl="ALTER TABLE users ADD COLUMN vk_user_id BIGINT",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="users",
                column_name="whatsapp_phone",
                ddl="ALTER TABLE users ADD COLUMN whatsapp_phone VARCHAR(32)",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="users",
                column_name="active_promo_code",
                ddl="ALTER TABLE users ADD COLUMN active_promo_code VARCHAR(64)",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="users",
                column_name="delivery_channel",
                ddl="ALTER TABLE users ADD COLUMN delivery_channel VARCHAR(16) NOT NULL DEFAULT 'telegram'",
                backfill_sql="UPDATE users SET delivery_channel = 'telegram' WHERE delivery_channel IS NULL",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="plans",
                column_name="provisioning_mode",
                ddl="ALTER TABLE plans ADD COLUMN provisioning_mode VARCHAR(32) NOT NULL DEFAULT 'auto'",
                backfill_sql="UPDATE plans SET provisioning_mode = 'auto' WHERE provisioning_mode IS NULL",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="hiddify_servers",
                column_name="country_name",
                ddl="ALTER TABLE hiddify_servers ADD COLUMN country_name VARCHAR(128) NOT NULL DEFAULT 'Без страны'",
                backfill_sql="UPDATE hiddify_servers SET country_name = 'Без страны' WHERE country_name IS NULL",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="orders",
                column_name="preferred_hiddify_server_id",
                ddl="ALTER TABLE orders ADD COLUMN preferred_hiddify_server_id INTEGER",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="orders",
                column_name="fulfillment_mode",
                ddl="ALTER TABLE orders ADD COLUMN fulfillment_mode VARCHAR(64) NOT NULL DEFAULT 'auto'",
                backfill_sql="UPDATE orders SET fulfillment_mode = 'auto' WHERE fulfillment_mode IS NULL",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="orders",
                column_name="original_amount_value",
                ddl="ALTER TABLE orders ADD COLUMN original_amount_value NUMERIC(10, 2)",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="orders",
                column_name="discount_amount_value",
                ddl="ALTER TABLE orders ADD COLUMN discount_amount_value NUMERIC(10, 2) NOT NULL DEFAULT 0",
                backfill_sql="UPDATE orders SET discount_amount_value = 0 WHERE discount_amount_value IS NULL",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="orders",
                column_name="promo_code_id",
                ddl="ALTER TABLE orders ADD COLUMN promo_code_id INTEGER",
            )
            await _ensure_sqlite_column(
                connection,
                table_name="orders",
                column_name="promo_code",
                ddl="ALTER TABLE orders ADD COLUMN promo_code VARCHAR(64)",
            )
            await connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_user_id ON users(vk_user_id) WHERE vk_user_id IS NOT NULL"
            )
            await connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_whatsapp_phone ON users(whatsapp_phone) WHERE whatsapp_phone IS NOT NULL"
            )
            await _ensure_hiddify_usage_snapshots_table(connection, sqlite=True)
            await _ensure_shop_settings_table(connection, sqlite=True)
            return

        await _ensure_column_if_missing(
            connection,
            table_name="users",
            column_name="vk_user_id",
            ddl="ALTER TABLE users ADD COLUMN vk_user_id BIGINT",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="users",
            column_name="whatsapp_phone",
            ddl="ALTER TABLE users ADD COLUMN whatsapp_phone VARCHAR(32)",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="users",
            column_name="active_promo_code",
            ddl="ALTER TABLE users ADD COLUMN active_promo_code VARCHAR(64)",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="users",
            column_name="delivery_channel",
            ddl="ALTER TABLE users ADD COLUMN delivery_channel VARCHAR(16) NOT NULL DEFAULT 'telegram'",
            backfill_sql="UPDATE users SET delivery_channel = 'telegram' WHERE delivery_channel IS NULL",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="plans",
            column_name="provisioning_mode",
            ddl="ALTER TABLE plans ADD COLUMN provisioning_mode VARCHAR(32) NOT NULL DEFAULT 'auto'",
            backfill_sql="UPDATE plans SET provisioning_mode = 'auto' WHERE provisioning_mode IS NULL",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="hiddify_servers",
            column_name="country_name",
            ddl="ALTER TABLE hiddify_servers ADD COLUMN country_name VARCHAR(128) NOT NULL DEFAULT 'Без страны'",
            backfill_sql="UPDATE hiddify_servers SET country_name = 'Без страны' WHERE country_name IS NULL",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="orders",
            column_name="preferred_hiddify_server_id",
            ddl="ALTER TABLE orders ADD COLUMN preferred_hiddify_server_id INTEGER REFERENCES hiddify_servers (id)",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="orders",
            column_name="fulfillment_mode",
            ddl="ALTER TABLE orders ADD COLUMN fulfillment_mode VARCHAR(64) NOT NULL DEFAULT 'auto'",
            backfill_sql="UPDATE orders SET fulfillment_mode = 'auto' WHERE fulfillment_mode IS NULL",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="orders",
            column_name="original_amount_value",
            ddl="ALTER TABLE orders ADD COLUMN original_amount_value NUMERIC(10, 2)",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="orders",
            column_name="discount_amount_value",
            ddl="ALTER TABLE orders ADD COLUMN discount_amount_value NUMERIC(10, 2) NOT NULL DEFAULT 0",
            backfill_sql="UPDATE orders SET discount_amount_value = 0 WHERE discount_amount_value IS NULL",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="orders",
            column_name="promo_code_id",
            ddl="ALTER TABLE orders ADD COLUMN promo_code_id INTEGER REFERENCES promo_codes (id)",
        )
        await _ensure_column_if_missing(
            connection,
            table_name="orders",
            column_name="promo_code",
            ddl="ALTER TABLE orders ADD COLUMN promo_code VARCHAR(64)",
        )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_user_id ON users(vk_user_id) WHERE vk_user_id IS NOT NULL"
        )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_whatsapp_phone ON users(whatsapp_phone) WHERE whatsapp_phone IS NOT NULL"
        )
        await _ensure_hiddify_usage_snapshots_table(connection, sqlite=False)
        await _ensure_shop_settings_table(connection, sqlite=False)


async def _ensure_sqlite_column(
    connection: AsyncConnection,
    *,
    table_name: str,
    column_name: str,
    ddl: str,
    backfill_sql: str | None = None,
) -> None:
    columns_result = await connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in columns_result.fetchall()}
    if column_name in existing_columns:
        return
    await connection.exec_driver_sql(ddl)
    if backfill_sql is not None:
        await connection.exec_driver_sql(backfill_sql)


async def _ensure_column_if_missing(
    connection: AsyncConnection,
    *,
    table_name: str,
    column_name: str,
    ddl: str,
    backfill_sql: str | None = None,
) -> None:
    existing_columns = await connection.run_sync(
        lambda sync_connection: {column["name"] for column in inspect(sync_connection).get_columns(table_name)}
    )
    if column_name in existing_columns:
        return
    await connection.exec_driver_sql(ddl)
    if backfill_sql is not None:
        await connection.exec_driver_sql(backfill_sql)


async def _ensure_hiddify_usage_snapshots_table(connection: AsyncConnection, *, sqlite: bool) -> None:
    id_column = "id INTEGER PRIMARY KEY" if sqlite else "id SERIAL PRIMARY KEY"
    sampled_default = "CURRENT_TIMESTAMP" if sqlite else "NOW()"
    sampled_type = "TIMESTAMP" if sqlite else "TIMESTAMP WITH TIME ZONE"
    await connection.exec_driver_sql(
        f"""
        CREATE TABLE IF NOT EXISTS hiddify_server_usage_snapshots (
            {id_column},
            server_id INTEGER NOT NULL,
            sampled_at {sampled_type} NOT NULL DEFAULT {sampled_default},
            total_users_count INTEGER,
            active_users_count INTEGER,
            active_users_percent NUMERIC(6, 2),
            total_current_usage_gb NUMERIC(14, 2),
            average_user_usage_gb NUMERIC(14, 2),
            usage_sample_users_count INTEGER NOT NULL DEFAULT 0,
            health_status VARCHAR(32) NOT NULL,
            error_message TEXT,
            FOREIGN KEY(server_id) REFERENCES hiddify_servers (id)
        )
        """
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_hiddify_usage_snapshots_server_sampled_at "
        "ON hiddify_server_usage_snapshots(server_id, sampled_at)"
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_hiddify_usage_snapshots_sampled_at "
        "ON hiddify_server_usage_snapshots(sampled_at)"
    )


async def _ensure_shop_settings_table(connection: AsyncConnection, *, sqlite: bool) -> None:
    id_column = "id INTEGER PRIMARY KEY" if sqlite else "id SERIAL PRIMARY KEY"
    timestamp_type = "TIMESTAMP" if sqlite else "TIMESTAMP WITH TIME ZONE"
    timestamp_default = "CURRENT_TIMESTAMP" if sqlite else "NOW()"
    await connection.exec_driver_sql(
        f"""
        CREATE TABLE IF NOT EXISTS shop_settings (
            {id_column},
            key VARCHAR(128) NOT NULL UNIQUE,
            value TEXT,
            created_at {timestamp_type} NOT NULL DEFAULT {timestamp_default},
            updated_at {timestamp_type} NOT NULL DEFAULT {timestamp_default}
        )
        """
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_shop_settings_key ON shop_settings(key)"
    )


async def session_dependency(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
