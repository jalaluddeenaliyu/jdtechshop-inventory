from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

WALK_IN_NAME = "Walk-in customer"


class Customer(TimestampMixin, Base):
    """A shop customer. Never deleted (sales and debts point at it); turned off instead.

    Exactly one row is the built-in walk-in customer, used for anonymous sales. A database
    trigger keeps that row unchanged and refuses deletes of any customer.
    """

    __tablename__ = "customers"
    __table_args__ = (
        # Phones are stored normalised (0803… or +44…), so one number = one customer.
        CheckConstraint("phone IS NULL OR phone ~ '^(0[0-9]{10}|\\+[0-9]{8,15})$'", name="phone_format"),
        # Only one walk-in customer can exist.
        Index("uq_customers_single_walk_in", "is_walk_in", unique=True,
              postgresql_where=text("is_walk_in")),
        Index("ix_customers_name_lower", text("lower(name)")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_walk_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
