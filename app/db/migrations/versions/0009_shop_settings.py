"""Add shop settings."""

from alembic import op
import sqlalchemy as sa

revision = "0009_shop_settings"
down_revision = "0008_hiddify_usage_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_shop_settings_key", "shop_settings", ["key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_shop_settings_key", table_name="shop_settings")
    op.drop_table("shop_settings")
