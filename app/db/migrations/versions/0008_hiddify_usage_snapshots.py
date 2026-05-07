"""Add Hiddify server usage snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "0008_hiddify_usage_snapshots"
down_revision = "0007_pricing_and_promocodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hiddify_server_usage_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("hiddify_servers.id"), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("total_users_count", sa.Integer(), nullable=True),
        sa.Column("active_users_count", sa.Integer(), nullable=True),
        sa.Column("active_users_percent", sa.Numeric(6, 2), nullable=True),
        sa.Column("total_current_usage_gb", sa.Numeric(14, 2), nullable=True),
        sa.Column("average_user_usage_gb", sa.Numeric(14, 2), nullable=True),
        sa.Column("usage_sample_users_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("health_status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_hiddify_usage_snapshots_server_sampled_at",
        "hiddify_server_usage_snapshots",
        ["server_id", "sampled_at"],
    )
    op.create_index(
        "ix_hiddify_usage_snapshots_sampled_at",
        "hiddify_server_usage_snapshots",
        ["sampled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hiddify_usage_snapshots_sampled_at", table_name="hiddify_server_usage_snapshots")
    op.drop_index("ix_hiddify_usage_snapshots_server_sampled_at", table_name="hiddify_server_usage_snapshots")
    op.drop_table("hiddify_server_usage_snapshots")
