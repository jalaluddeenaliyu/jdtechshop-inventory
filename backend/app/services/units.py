"""Serialized units: individual phones / laptops identified by serial number and IMEIs.

Each unit enters stock with its own ledger movement (+1, linked to the unit), so the
product's quantity is always the number of its units on hand. The database enforces that,
and its IMEI registry makes every IMEI unique across all units and both IMEI slots.
"""
from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.models import (
    ImeiRegistry,
    MovementType,
    Product,
    ProductUnit,
    StockMovement,
    TrackingType,
    UnitStatus,
    User,
)
from app.schemas.units import OpeningUnitsIn, UnitOut, UnitUpdate, check_identifier_rules
from app.services import stock as stock_service
from app.services.catalog import like_pattern

# (row label, serial, imei1, imei2) — row label is "Row 3: " for a batch, "" for one unit.
Candidate = tuple[str, str | None, str | None, str | None]


def unit_out(u: ProductUnit, show_cost: bool) -> UnitOut:
    return UnitOut(
        id=u.id, product_id=u.product_id, product_name=u.product.name, product_sku=u.product.sku,
        serial_number=u.serial_number, imei1=u.imei1, imei2=u.imei2, condition=u.condition,
        unit_cost=u.unit_cost if show_cost else None, status=u.status, notes=u.notes,
        created_at=u.created_at, updated_at=u.updated_at,
    )


def _describe(unit: ProductUnit) -> str:
    return f"“{unit.product.name}” (unit #{unit.id})"


def check_identifiers_free(db: Session, candidates: list[Candidate],
                           exclude_unit_id: int | None = None) -> None:
    """Refuse duplicates within the batch and against every unit already registered."""
    seen_serials: dict[str, str] = {}
    seen_imeis: dict[str, str] = {}
    for row, serial, imei1, imei2 in candidates:
        if serial is not None:
            if serial in seen_serials:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"{row}serial number {serial} is entered twice "
                                    f"({seen_serials[serial].strip(': ') or 'above'}).")
            seen_serials[serial] = row
        for imei in (imei1, imei2):
            if imei is None:
                continue
            if imei in seen_imeis:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"{row}IMEI {imei} is entered twice "
                                    f"({seen_imeis[imei].strip(': ') or 'above'}).")
            seen_imeis[imei] = row

    if seen_serials:
        q = select(ProductUnit).where(ProductUnit.serial_number.in_(seen_serials))
        if exclude_unit_id is not None:
            q = q.where(ProductUnit.id != exclude_unit_id)
        for unit in db.scalars(q):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{seen_serials[unit.serial_number]}serial number "
                                f"{unit.serial_number} is already registered to {_describe(unit)}.")
    if seen_imeis:
        q = (select(ImeiRegistry.imei, ProductUnit)
             .join(ProductUnit, ProductUnit.id == ImeiRegistry.unit_id)
             .where(ImeiRegistry.imei.in_(seen_imeis)))
        if exclude_unit_id is not None:
            q = q.where(ProductUnit.id != exclude_unit_id)
        for imei, unit in db.execute(q):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{seen_imeis[imei]}IMEI {imei} is already registered to "
                                f"{_describe(unit)}.")


_RACE = HTTPException(
    status.HTTP_409_CONFLICT,
    "One of these serial/IMEI numbers was registered by someone else a moment ago. "
    "Nothing was saved — check the numbers and try again.",
)


def add_opening_units(db: Session, product_id: int, data: OpeningUnitsIn, actor: User,
                      request: Request | None) -> tuple[Product, list[ProductUnit]]:
    """Register phones/laptops the shop already had before it started using the system.

    Allowed in several batches, but only until the product's first purchase, sale or
    adjustment — after that, stock arrives through purchases.
    """
    product = stock_service.lock_product(db, product_id)
    if product.tracking_type != TrackingType.SERIALIZED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This product is counted by quantity, not by serial/IMEI. "
                            "Use “Opening stock” to enter how many you have.")
    if not product.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This product is turned off. Turn it on before adding stock.")
    if db.scalar(select(StockMovement.id).where(
        StockMovement.product_id == product.id,
        StockMovement.movement_type != MovementType.OPENING,
    ).limit(1)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This product already has purchases, sales or adjustments, so opening stock is "
            "closed. New units arrive through purchases.",
        )

    check_identifiers_free(db, [
        (f"Row {i}: ", u.serial_number, u.imei1, u.imei2) for i, u in enumerate(data.units, 1)
    ])

    units: list[ProductUnit] = []
    try:
        for u in data.units:
            unit = ProductUnit(product_id=product.id, serial_number=u.serial_number, imei1=u.imei1,
                               imei2=u.imei2, condition=u.condition, unit_cost=u.unit_cost,
                               notes=u.notes, status=UnitStatus.IN_STOCK, created_by_id=actor.id)
            db.add(unit)
            db.flush()
            stock_service.record_movement(db, product, quantity_change=1,
                                          movement_type=MovementType.OPENING, user=actor,
                                          unit_cost=u.unit_cost, unit_id=unit.id, reason=data.note)
            units.append(unit)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _RACE

    audit.record(db, action="stock.opening_units", user=actor, entity_type="product",
                 entity_id=product.id,
                 details={"count": len(units), "unit_ids": [u.id for u in units],
                          "total_cost": str(sum(u.unit_cost for u in units))},
                 request=request)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _RACE
    db.refresh(product)
    return product, units


def update_unit(db: Session, unit_id: int, data: UnitUpdate, actor: User,
                request: Request | None) -> ProductUnit:
    """Correct a unit's serial/IMEIs, condition or notes. Every change is audited."""
    unit = db.get(ProductUnit, unit_id, with_for_update=True)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found.")
    fields = data.model_dump(exclude_unset=True)
    merged = {k: fields.get(k, getattr(unit, k)) for k in ("serial_number", "imei1", "imei2")}
    try:
        check_identifier_rules(merged["serial_number"], merged["imei1"], merged["imei2"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    check_identifiers_free(db, [("", merged["serial_number"], merged["imei1"], merged["imei2"])],
                           exclude_unit_id=unit.id)

    changes: dict = {}
    for field, new in fields.items():
        old = getattr(unit, field)
        if old != new:
            changes[field] = {"from": old, "to": new}
            setattr(unit, field, new)
    if not changes:
        db.rollback()
        return unit
    audit.record(db, action="unit.updated", user=actor, entity_type="product_unit",
                 entity_id=unit.id, details=changes, request=request)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _RACE
    db.refresh(unit)
    return unit


def get_unit(db: Session, unit_id: int) -> ProductUnit:
    unit = db.get(ProductUnit, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unit not found.")
    return unit


def list_units(db: Session, *, q: str | None = None, product_id: int | None = None,
               status_: str | None = None, page: int = 1,
               page_size: int = 50) -> tuple[list[ProductUnit], int]:
    """Newest first. `q` matches any part of a serial number or either IMEI."""
    conditions = []
    if q and q.strip():
        pattern = like_pattern(q.strip().replace(" ", ""))
        conditions.append(or_(*(col.ilike(pattern, escape="\\") for col in
                                (ProductUnit.serial_number, ProductUnit.imei1, ProductUnit.imei2))))
    if product_id is not None:
        conditions.append(ProductUnit.product_id == product_id)
    if status_ is not None:
        conditions.append(ProductUnit.status == status_)
    total = db.scalar(select(func.count()).select_from(ProductUnit).where(*conditions)) or 0
    units = db.scalars(
        select(ProductUnit).where(*conditions).order_by(ProductUnit.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(units), total
