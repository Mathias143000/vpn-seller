"""Add VK user channel fields."""

from alembic import op
import sqlalchemy as sa

revision = "0005_users_vk_channel"
down_revision = "0004_order_fulfillment_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("vk_user_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column("delivery_channel", sa.String(length=16), nullable=False, server_default=sa.text("'telegram'")),
    )
    op.create_index("ix_users_vk_user_id", "users", ["vk_user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_vk_user_id", table_name="users")
    op.drop_column("users", "delivery_channel")
    op.drop_column("users", "vk_user_id")
