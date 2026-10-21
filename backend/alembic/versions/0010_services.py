"""services: products that are sold but have no stock (sticker cutting, printing, repairs)

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("tracking_type_valid", "products", type_="check")
    op.create_check_constraint("tracking_type_valid", "products",
                               "tracking_type IN ('serialized', 'quantity', 'service')")
    # A service never holds stock.
    op.create_check_constraint("service_has_no_stock", "products",
                               "tracking_type <> 'service' OR qty_on_hand = 0")


def downgrade() -> None:
    # Older versions don't know services: keep them as quantity products with no stock.
    op.execute("UPDATE products SET tracking_type = 'quantity', condition = 'new' "
               "WHERE tracking_type = 'service'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")  # run deferred checks before altering the table
    op.drop_constraint("service_has_no_stock", "products", type_="check")
    op.drop_constraint("tracking_type_valid", "products", type_="check")
    op.create_check_constraint("tracking_type_valid", "products",
                               "tracking_type IN ('serialized', 'quantity')")
