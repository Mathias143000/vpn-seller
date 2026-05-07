"""Add WhatsApp user channel fields."""

from alembic import op
import sqlalchemy as sa

revision = "0006_users_whatsapp_channel"
down_revision = "0005_users_vk_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("whatsapp_phone", sa.String(length=32), nullable=True))
    op.create_index("ix_users_whatsapp_phone", "users", ["whatsapp_phone"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_whatsapp_phone", table_name="users")
    op.drop_column("users", "whatsapp_phone")
