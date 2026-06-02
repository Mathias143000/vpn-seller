"""Add fulfillment mode to orders."""

from alembic import op
import sqlalchemy as sa

revision = "0004_order_fulfillment_mode"
down_revision = "0003_hiddify_server_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("fulfillment_mode", sa.String(length=64), nullable=False, server_default=sa.text("'auto'")),
    )


def downgrade() -> None:
    op.drop_column("orders", "fulfillment_mode")
