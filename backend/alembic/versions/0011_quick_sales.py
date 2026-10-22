"""quick sales: sell an item that is not in the catalogue yet, review it later

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# The only columns a review may change, once, on a quick-sale line.
REVIEW_COLUMNS = ("product_id", "unit_id", "unit_cost_snapshot", "cost_total_snapshot",
                  "reviewed_at", "reviewed_by_id")


def upgrade() -> None:
    # One built-in product that quick-sale lines point to until they are reviewed.
    op.add_column("products", sa.Column("is_quick_sale", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.create_index("uq_products_single_quick_sale", "products", ["is_quick_sale"], unique=True,
                    postgresql_where=sa.text("is_quick_sale"))
    op.execute(
        "INSERT INTO products (sku, name, tracking_type, selling_price, avg_cost, qty_on_hand, "
        "is_active, is_quick_sale, description) VALUES ('QUICK-SALE', 'Quick sale item', "
        "'service', 0, 0, 0, true, true, 'Built in: items sold before they were added to the "
        "catalogue. Each one is reviewed later.')"
    )

    op.add_column("sale_lines", sa.Column("quick_name", sa.String(160), nullable=True))
    op.add_column("sale_lines", sa.Column("needs_review", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
    op.add_column("sale_lines", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sale_lines", sa.Column("reviewed_by_id", sa.Integer(),
                                          sa.ForeignKey("users.id", name="fk_sale_lines_reviewed_by_id_users"),
                                          nullable=True))
    op.create_check_constraint("quick_line_named", "sale_lines",
                               "needs_review = (quick_name IS NOT NULL)")
    op.create_check_constraint("review_only_quick_lines", "sale_lines",
                               "reviewed_at IS NULL OR needs_review")
    op.create_index("ix_sale_lines_review_pending", "sale_lines", ["id"],
                    postgresql_where=sa.text("needs_review AND reviewed_at IS NULL"))

    # Sale lines stay permanent, except a single review of a quick-sale line.
    cols = ", ".join(f"'{c}'" for c in REVIEW_COLUMNS)
    op.execute(
        f"""
        CREATE FUNCTION sale_lines_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'sale_lines rows cannot be changed or deleted (DELETE blocked)';
            END IF;
            IF NOT OLD.needs_review OR OLD.reviewed_at IS NOT NULL OR NEW.reviewed_at IS NULL
               OR (to_jsonb(NEW) - ARRAY[{cols}]) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY[{cols}]) THEN
                RAISE EXCEPTION 'sale_lines rows cannot be changed or deleted (only one review of a quick-sale item is allowed)';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER sale_lines_permanent ON sale_lines")
    op.execute("CREATE TRIGGER sale_lines_review_only BEFORE UPDATE OR DELETE ON sale_lines "
               "FOR EACH ROW EXECUTE FUNCTION sale_lines_guard()")


def downgrade() -> None:
    op.execute("DROP TRIGGER sale_lines_review_only ON sale_lines")
    op.execute("DROP FUNCTION sale_lines_guard()")
    op.execute("CREATE TRIGGER sale_lines_permanent BEFORE UPDATE OR DELETE ON sale_lines "
               "FOR EACH ROW EXECUTE FUNCTION sales_block_changes()")
    op.drop_index("ix_sale_lines_review_pending", table_name="sale_lines")
    op.drop_constraint("review_only_quick_lines", "sale_lines", type_="check")
    op.drop_constraint("quick_line_named", "sale_lines", type_="check")
    op.drop_constraint("fk_sale_lines_reviewed_by_id_users", "sale_lines", type_="foreignkey")
    for c in ("reviewed_by_id", "reviewed_at", "needs_review", "quick_name"):
        op.drop_column("sale_lines", c)
    # The built-in product stays as an ordinary (turned off) service so old sales keep their lines.
    op.execute("UPDATE products SET is_active = false WHERE is_quick_sale")
    op.drop_index("uq_products_single_quick_sale", table_name="products")
    op.drop_column("products", "is_quick_sale")
