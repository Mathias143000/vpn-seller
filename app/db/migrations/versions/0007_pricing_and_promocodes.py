"""Add pricing metadata and promo codes."""

from alembic import op
import sqlalchemy as sa

revision = "0007_pricing_and_promocodes"
down_revision = "0006_users_whatsapp_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("discount_type", sa.String(length=16), nullable=False),
        sa.Column("discount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"])

    op.add_column("users", sa.Column("active_promo_code", sa.String(length=64), nullable=True))
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("original_amount_value", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(
            sa.Column("discount_amount_value", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
        )
        batch_op.add_column(
            sa.Column(
                "promo_code_id",
                sa.Integer(),
                sa.ForeignKey("promo_codes.id", name="fk_orders_promo_code_id"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("promo_code", sa.String(length=64), nullable=True))

    op.create_table(
        "promo_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("promo_code_id", sa.Integer(), sa.ForeignKey("promo_codes.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("discount_amount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_redemptions_code_user"),
        sa.UniqueConstraint("order_id", name="uq_promo_redemptions_order_id"),
    )


def downgrade() -> None:
    op.drop_table("promo_redemptions")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("promo_code")
        batch_op.drop_column("promo_code_id")
        batch_op.drop_column("discount_amount_value")
        batch_op.drop_column("original_amount_value")
    op.drop_column("users", "active_promo_code")
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")
