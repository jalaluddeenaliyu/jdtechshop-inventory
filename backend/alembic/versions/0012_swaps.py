"""swap deals: a customer's old phone taken in as part payment at the till

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

OLD_TYPES = ("'opening', 'purchase', 'sale', 'customer_return', 'supplier_return', 'damage', "
             "'missing', 'adjustment', 'reversal'")


def upgrade() -> None:
    # The swap value is recorded as a payment of this built-in kind, so balances and debts work
    # as for any payment. It is never money: it is turned off (so no cash screen offers it) and
    # left out of every cash figure.
    op.add_column("payment_methods", sa.Column("is_trade_in", sa.Boolean(), nullable=False,
                                               server_default=sa.false()))
    op.create_index("uq_payment_methods_single_trade_in", "payment_methods", ["is_trade_in"],
                    unique=True, postgresql_where=sa.text("is_trade_in"))
    op.execute("INSERT INTO payment_methods (name, is_active, sort_order, is_trade_in) "
               "VALUES ('Swap (old phone)', false, 999, true)")

    op.drop_constraint("type_valid", "stock_movements", type_="check")
    op.create_check_constraint("type_valid", "stock_movements",
                               f"movement_type IN ({OLD_TYPES}, 'trade_in')")

    op.create_table(
        "sale_trade_ins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False, unique=True),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("product_units.id"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("customer_payments.id"),
                  nullable=False, unique=True),
        sa.Column("value", sa.Numeric(14, 2), nullable=False),
        # Kept here too, so the receipt still shows the phone if the swap is undone.
        sa.Column("product_name", sa.String(160), nullable=False),
        sa.Column("imei1", sa.String(15)),
        sa.Column("imei2", sa.String(15)),
        sa.Column("serial_number", sa.String(64)),
        sa.Column("condition", sa.String(20), nullable=False),
        sa.Column("notes", sa.String(255)),
        # A phone the shop sold before, coming back: what to restore if the swap is undone.
        sa.Column("previous_sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id")),
        sa.Column("previous_unit_cost", sa.Numeric(14, 4)),
        sa.Column("previous_condition", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("value > 0", name="value_positive"),
        sa.CheckConstraint("condition IN ('new', 'used', 'refurbished')", name="condition_valid"),
    )
    op.create_index("ix_sale_trade_ins_unit_id", "sale_trade_ins", ["unit_id"])
    op.execute("CREATE TRIGGER sale_trade_ins_permanent BEFORE UPDATE OR DELETE ON sale_trade_ins "
               "FOR EACH ROW EXECUTE FUNCTION sales_block_changes()")


def downgrade() -> None:
    op.execute("DROP TRIGGER sale_trade_ins_permanent ON sale_trade_ins")
    op.drop_table("sale_trade_ins")
    # Older versions don't know swaps: their stock-ins read as opening stock.
    op.execute("ALTER TABLE stock_movements DISABLE TRIGGER USER")
    op.execute("UPDATE stock_movements SET movement_type = 'opening' WHERE movement_type = 'trade_in'")
    op.execute("ALTER TABLE stock_movements ENABLE TRIGGER USER")
    op.drop_constraint("type_valid", "stock_movements", type_="check")
    op.create_check_constraint("type_valid", "stock_movements", f"movement_type IN ({OLD_TYPES})")
    op.execute("UPDATE payment_methods SET name = name || ' (built in)' WHERE is_trade_in")
    op.drop_index("uq_payment_methods_single_trade_in", table_name="payment_methods")
    op.drop_column("payment_methods", "is_trade_in")
