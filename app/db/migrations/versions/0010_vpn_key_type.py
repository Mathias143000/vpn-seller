"""Add explicit VPN key type."""

from alembic import op
import sqlalchemy as sa

revision = "0010_vpn_key_type"
down_revision = "0009_shop_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vpn_keys",
        sa.Column("key_type", sa.String(length=32), nullable=False, server_default="unknown"),
    )


def downgrade() -> None:
    op.drop_column("vpn_keys", "key_type")
