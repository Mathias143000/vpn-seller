"""Add Hiddify servers and plan provisioning mode."""

from alembic import op
import sqlalchemy as sa

revision = "0002_hiddify_servers"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("provisioning_mode", sa.String(length=32), nullable=False, server_default=sa.text("'auto'")),
    )

    op.create_table(
        "hiddify_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("admin_proxy_path", sa.String(length=128), nullable=False),
        sa.Column("client_proxy_path", sa.String(length=128), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("panel_version", sa.String(length=64), nullable=True),
        sa.Column("last_health_status", sa.String(length=32), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("last_healthcheck_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("base_url", "admin_proxy_path", name="uq_hiddify_servers_base_admin_path"),
    )
    op.create_index(
        "ix_hiddify_servers_is_active_last_used_at",
        "hiddify_servers",
        ["is_active", "last_used_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hiddify_servers_is_active_last_used_at", table_name="hiddify_servers")
    op.drop_table("hiddify_servers")
    op.drop_column("plans", "provisioning_mode")
