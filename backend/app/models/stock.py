from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MovementType(StrEnum):
    OPENING = "opening"
    PURCHASE = "purchase"
    SALE = "sale"
    CUSTOMER_RETURN = "customer_return"
    SUPPLIER_RETURN = "supplier_return"
    DAMAGE = "damage"
    MISSING = "missing"
    ADJUSTMENT = "adjustment"
    REVERSAL = "reversal"


_TYPES_SQL = ", ".join(f"'{t.value}'" for t in MovementType)


class StockMovement(Base):
    """Append-only stock ledger. A database trigger blocks edits and deletes.

    Mistakes are corrected with a new, opposite movement — never by changing history.
    """

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity_change <> 0", name="nonzero"),
        CheckConstraint("balance_after >= 0", name="balance_nonneg"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_nonneg"),
        CheckConstraint(f"movement_type IN ({_TYPES_SQL})", name="type_valid"),
        # A serialized unit always moves one at a time.
        CheckConstraint("unit_id IS NULL OR quantity_change IN (1, -1)", name="unit_moves_one"),
        Index("ix_stock_movements_product_id_id", "product_id", "id"),
        Index("ix_stock_movements_reference", "reference_type", "reference_id"),
        Index("ix_stock_movements_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    # The phone/laptop that moved, for serial/IMEI-tracked products.
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"), index=True)
    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)  # + in, − out
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Cost per unit of this movement: the receiving cost for stock in, the average cost for stock out.
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # The business document that caused it (e.g. "purchase", 12). Empty for opening stock.
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
