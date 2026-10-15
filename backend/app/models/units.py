from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.catalog import Product


class UnitStatus(StrEnum):
    IN_STOCK = "in_stock"
    RESERVED = "reserved"
    SOLD = "sold"
    RETURNED_TO_SUPPLIER = "returned_to_supplier"
    DAMAGED = "damaged"
    MISSING = "missing"
    VOIDED = "voided"  # came in on a purchase that was later reversed


# Statuses that count towards the product's quantity on hand.
ON_HAND_STATUSES = (UnitStatus.IN_STOCK, UnitStatus.RESERVED)

_STATUS_SQL = ", ".join(f"'{s.value}'" for s in UnitStatus)


class ProductUnit(TimestampMixin, Base):
    """One physical phone / laptop / tablet, identified by its serial number and/or IMEIs.

    Every IMEI (either slot) is also written to `imei_registry` by a database trigger, whose
    primary key makes an IMEI unique across all units and both slots.
    """

    __tablename__ = "product_units"
    __table_args__ = (
        CheckConstraint("serial_number IS NOT NULL OR imei1 IS NOT NULL", name="has_identifier"),
        CheckConstraint("imei2 IS NULL OR imei1 IS NOT NULL", name="imei2_needs_imei1"),
        CheckConstraint("imei1 IS NULL OR imei2 IS NULL OR imei1 <> imei2", name="imeis_differ"),
        CheckConstraint("imei1 IS NULL OR imei1 ~ '^[0-9]{15}$'", name="imei1_format"),
        CheckConstraint("imei2 IS NULL OR imei2 ~ '^[0-9]{15}$'", name="imei2_format"),
        CheckConstraint("serial_number IS NULL OR serial_number = upper(serial_number)",
                        name="serial_uppercase"),
        CheckConstraint("condition IN ('new', 'used', 'refurbished')", name="condition_valid"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonneg"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    serial_number: Mapped[str | None] = mapped_column(String(50), unique=True)
    imei1: Mapped[str | None] = mapped_column(String(15), unique=True)
    imei2: Mapped[str | None] = mapped_column(String(15), unique=True)
    condition: Mapped[str] = mapped_column(String(20), nullable=False)
    # What this particular unit cost the shop. Profit on its sale uses exactly this figure.
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=UnitStatus.IN_STOCK,
                                        index=True)
    notes: Mapped[str | None] = mapped_column(String(255))
    # The purchase line it arrived on (empty for opening stock).
    purchase_line_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_lines.id"),
                                                         index=True)
    # The sale line it was sold on (while sold).
    # (use_alter: units and sale lines point at each other, so this key is added afterwards.)
    sale_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("sale_lines.id", use_alter=True), index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    product: Mapped[Product] = relationship(lazy="selectin")


class ImeiRegistry(Base):
    """Every IMEI in use, from either slot. Maintained only by a database trigger."""

    __tablename__ = "imei_registry"
    __table_args__ = (CheckConstraint("slot IN (1, 2)", name="slot_valid"),)

    imei: Mapped[str] = mapped_column(String(15), primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
