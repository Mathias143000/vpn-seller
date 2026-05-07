"""Add Hiddify server country and preferred server on orders."""

from alembic import op
import sqlalchemy as sa

revision = "0003_hiddify_server_country_and_order_preference"
down_revision = "0002_hiddify_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hiddify_servers",
        sa.Column("country_name", sa.String(length=128), nullable=False, server_default=sa.text("'Без страны'")),
    )
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "preferred_hiddify_server_id",
                sa.Integer(),
                sa.ForeignKey("hiddify_servers.id", name="fk_orders_preferred_hiddify_server_id"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("preferred_hiddify_server_id")
    op.drop_column("hiddify_servers", "country_name")
