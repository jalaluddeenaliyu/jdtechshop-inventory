from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TrackingType(StrEnum):
    SERIALIZED = "serialized"  # phones, laptops: every unit has its own serial / IMEI
    QUANTITY = "quantity"      # accessories: counted, not individually identified
    SERVICE = "service"        # sticker cutting, printing, repairs: sold, but no stock


class Condition(StrEnum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"


# Auto-generated SKUs (JDT-00001, JDT-00002 …) when the owner leaves the SKU blank.
PRODUCT_SKU_SEQ = Sequence("product_sku_seq", metadata=Base.metadata)


class Category(TimestampMixin, Base):
    """Product category. May have one level of subcategories (e.g. Accessories → Chargers)."""

    __tablename__ = "categories"
    __table_args__ = (
        Index("uq_categories_name_ci", text("lower(name)"), unique=True),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    parent: Mapped["Category | None"] = relationship(remote_side=[id], lazy="selectin")


class Brand(TimestampMixin, Base):
    __tablename__ = "brands"
    __table_args__ = (Index("uq_brands_name_ci", text("lower(name)"), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Product(TimestampMixin, Base):
    """A catalogue item.

    `qty_on_hand` and `avg_cost` are maintained only by the stock service, inside the same
    transaction as the stock movement that explains the change. A deferred database trigger
    refuses to commit if `qty_on_hand` ever differs from the sum of the product's movements.
    """

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("tracking_type IN ('serialized', 'quantity', 'service')",
                        name="tracking_type_valid"),
        CheckConstraint("tracking_type <> 'service' OR qty_on_hand = 0",
                        name="service_has_no_stock"),
        CheckConstraint(
            "condition IS NULL OR condition IN ('new', 'used', 'refurbished')",
            name="condition_valid",
        ),
        CheckConstraint("sku = upper(sku)", name="sku_uppercase"),
        CheckConstraint("selling_price >= 0", name="price_nonneg"),
        CheckConstraint("avg_cost >= 0", name="avg_cost_nonneg"),
        CheckConstraint("qty_on_hand >= 0", name="qty_nonneg"),
        CheckConstraint("reorder_level IS NULL OR reorder_level >= 0", name="reorder_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(120))
    specifications: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    tracking_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Quantity items only; serialized units carry their own condition.
    condition: Mapped[str | None] = mapped_column(String(20))
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                   default=Decimal("0"))
    # Weighted-average cost, 4 decimal places so repeated averaging stays accurate.
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("0"))
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # None means "use the shop's default reorder level" from business settings.
    reorder_level: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    category: Mapped[Category | None] = relationship(lazy="selectin")
    brand: Mapped[Brand | None] = relationship(lazy="selectin")
