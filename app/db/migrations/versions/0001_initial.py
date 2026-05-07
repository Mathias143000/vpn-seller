"""Initial schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=False)

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("price_currency", sa.String(length=8), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_imported", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("amount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount_currency", sa.String(length=8), nullable=False),
        sa.Column("payment_provider", sa.String(length=64), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=True),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("reserved_key_id", sa.Integer(), nullable=True),
        sa.Column("issued_key_id", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("delivery_status", sa.String(length=64), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_orders_provider_payment_id", "orders", ["provider_payment_id"], unique=False)
    op.create_index("ix_orders_user_id_created_at", "orders", ["user_id", "created_at"], unique=False)

    op.create_table(
        "vpn_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("key_value_encrypted", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reserved_by_order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("issued_to_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("imported_batch_id", sa.Integer(), sa.ForeignKey("import_batches.id"), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key_fingerprint"),
        sa.UniqueConstraint("reserved_by_order_id"),
    )
    op.create_index("ix_vpn_keys_plan_id_status", "vpn_keys", ["plan_id", "status"], unique=False)

    with op.batch_alter_table("orders") as batch_op:
        batch_op.create_foreign_key("fk_orders_reserved_key_id", "vpn_keys", ["reserved_key_id"], ["id"])
        batch_op.create_foreign_key("fk_orders_issued_key_id", "vpn_keys", ["issued_key_id"], ["id"])
        batch_op.create_unique_constraint("uq_orders_reserved_key_id", ["reserved_key_id"])
        batch_op.create_unique_constraint("uq_orders_issued_key_id", ["issued_key_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("amount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount_currency", sa.String(length=8), nullable=False),
        sa.Column("provider_metadata_json", sa.JSON(), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_payment_id"),
    )

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_events_provider_event_id"),
    )
    op.create_index(
        "ix_payment_events_provider_payment_id_received_at",
        "payment_events",
        ["provider_payment_id", "received_at"],
        unique=False,
    )

    op.create_table(
        "delivery_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_delivery_jobs_dedupe_key"),
    )
    op.create_index("ix_delivery_jobs_status_next_retry_at", "delivery_jobs", ["status", "next_retry_at"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_index("ix_delivery_jobs_status_next_retry_at", table_name="delivery_jobs")
    op.drop_table("delivery_jobs")
    op.drop_index("ix_payment_events_provider_payment_id_received_at", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_table("payments")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("uq_orders_issued_key_id", type_="unique")
        batch_op.drop_constraint("uq_orders_reserved_key_id", type_="unique")
        batch_op.drop_constraint("fk_orders_issued_key_id", type_="foreignkey")
        batch_op.drop_constraint("fk_orders_reserved_key_id", type_="foreignkey")
    op.drop_index("ix_vpn_keys_plan_id_status", table_name="vpn_keys")
    op.drop_table("vpn_keys")
    op.drop_index("ix_orders_user_id_created_at", table_name="orders")
    op.drop_index("ix_orders_provider_payment_id", table_name="orders")
    op.drop_table("orders")
    op.drop_table("import_batches")
    op.drop_table("plans")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
