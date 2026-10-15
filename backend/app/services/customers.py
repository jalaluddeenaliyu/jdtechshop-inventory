"""Customers. Staff may add and edit them (they meet customers at the till); only the owner
turns them off. Nobody can change the built-in walk-in customer, and nobody is ever deleted.
"""
import re

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.models import Customer, User
from app.schemas.customers import CustomerCreate, CustomerOut, CustomerUpdate
from app.services.catalog import like_pattern
from app.services.debts import customer_balances, owing_customer_ids


def display_phone(phone: str) -> str:
    """08031234567 → 0803 123 4567 (other numbers as stored)."""
    m = re.fullmatch(r"(0\d{3})(\d{3})(\d{4})", phone)
    return " ".join(m.groups()) if m else phone


def _phone_taken(db: Session, phone: str | None, exclude_id: int | None = None) -> None:
    if phone is None:
        return
    q = select(Customer.name).where(Customer.phone == phone)
    if exclude_id is not None:
        q = q.where(Customer.id != exclude_id)
    owner_name = db.scalar(q)
    if owner_name:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The phone number {display_phone(phone)} already belongs to "
                            f"{owner_name}.")


def _duplicate_phone() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT,
                         "That phone number was saved for another customer a moment ago.")


def create_customer(db: Session, data: CustomerCreate, actor: User,
                    request: Request | None) -> Customer:
    _phone_taken(db, data.phone)
    customer = Customer(**data.model_dump(), is_walk_in=False, is_active=True,
                        created_by_id=actor.id)
    db.add(customer)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _duplicate_phone()
    audit.record(db, action="customer.created", user=actor, entity_type="customer",
                 entity_id=customer.id, details={"name": customer.name, "phone": customer.phone},
                 request=request)
    db.commit()
    db.refresh(customer)
    return customer


def update_customer(db: Session, customer_id: int, data: CustomerUpdate, actor: User,
                    request: Request | None) -> Customer:
    customer = db.get(Customer, customer_id, with_for_update=True)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")
    if customer.is_walk_in:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The walk-in customer is built in and cannot be changed.")
    fields = data.model_dump(exclude_unset=True)
    if "is_active" in fields and fields["is_active"] != customer.is_active and not actor.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the owner can turn customers off or on.")
    if "phone" in fields:
        _phone_taken(db, fields["phone"], exclude_id=customer.id)

    changes: dict = {}
    for field, new in fields.items():
        old = getattr(customer, field)
        if old != new:
            changes[field] = {"from": old, "to": new}
            setattr(customer, field, new)
    if not changes:
        db.rollback()
        return customer
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _duplicate_phone()
    audit.record(db, action="customer.updated", user=actor, entity_type="customer",
                 entity_id=customer.id, details=changes, request=request)
    db.commit()
    db.refresh(customer)
    return customer


def get_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")
    return customer


def walk_in_customer(db: Session) -> Customer:
    customer = db.scalar(select(Customer).where(Customer.is_walk_in.is_(True)))
    if customer is None:  # created by migration 0005; should never happen
        raise HTTPException(500, "The walk-in customer is missing. Run the database migrations.")
    return customer


def customers_out(db: Session, customers: list[Customer]) -> list[CustomerOut]:
    """Customers with what they owe, worked out from their sales and payments."""
    balances = customer_balances(db, [c.id for c in customers])
    out = []
    for c in customers:
        item = CustomerOut.model_validate(c)
        item.owed, item.overdue = balances[c.id].owed, balances[c.id].overdue
        out.append(item)
    return out


def list_customers(db: Session, *, q: str | None, include_inactive: bool, include_walk_in: bool,
                   page: int, page_size: int,
                   owing_only: bool = False) -> tuple[list[Customer], int]:
    """Search by any part of the name, email or phone number (in any common format)."""
    conditions = []
    if not include_inactive:
        conditions.append(Customer.is_active.is_(True))
    if not include_walk_in:
        conditions.append(Customer.is_walk_in.is_(False))
    if owing_only:
        conditions.append(Customer.id.in_(owing_customer_ids()))
    if q and q.strip():
        text_match = [col.ilike(like_pattern(q.strip()), escape="\\")
                      for col in (Customer.name, Customer.email)]
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 4:
            if digits.startswith("234") and len(digits) > 3:
                digits = "0" + digits[3:]
            text_match.append(Customer.phone.contains(digits.lstrip("0") or digits))
        conditions.append(or_(*text_match))
    total = db.scalar(select(func.count()).select_from(Customer).where(*conditions)) or 0
    customers = db.scalars(
        select(Customer).where(*conditions)
        # The walk-in customer (when included) comes first, then everyone A–Z.
        .order_by(Customer.is_walk_in.desc(), func.lower(Customer.name), Customer.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(customers), total
