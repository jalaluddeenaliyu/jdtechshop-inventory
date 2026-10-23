"""imported sales: old sales brought in from another app (history only, no stock)

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("imported", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.execute("ALTER TABLE sales DISABLE TRIGGER USER")
    op.drop_column("sales", "imported")
    op.execute("ALTER TABLE sales ENABLE TRIGGER USER")
