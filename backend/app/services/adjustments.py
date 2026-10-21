"""Stock adjustments: damaged, missing or found stock, and count corrections.

Anyone may request one (with a reason). Only the owner approves or rejects it, and only an approved
adjustment moves stock — through the ledger, like everything else. The owner can approve their own
request straight away.

Quantity items change by a number (+ found, − damaged/missing). Serial/IMEI items change one unit at
a time by its status: in stock → damaged/missing takes it out (−1); damaged/missing → in stock puts
it back (+1, "found").
"""
from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import now_utc
from app.models import (
    AdjustmentStatus,
    MovementType,
    Product,
    ProductUnit,
    StockAdjustment,
    StockAdjustmentLine,
    TrackingType,
    UnitStatus,
    User,
)
from app.schemas.returns import AdjustmentIn, AdjustmentLineOut, AdjustmentOut, DecisionIn
from app.services import stock as stock_service

ADJUSTABLE = {UnitStatus.IN_STOCK, UnitStatus.DAMAGED, UnitStatus.MISSING}
KIND_TO_MOVEMENT = {"damage": MovementType.DAMAGE, "missing": MovementType.MISSING,
                    "adjustment": MovementType.ADJUSTMENT}


def _unit_label(u: ProductUnit) -> str:
    return f"IMEI {u.imei1}" if u.imei1 else f"serial {u.serial_number}"


def adjustment_out(db: Session, adj: StockAdjustment) -> AdjustmentOut:
    names = dict(db.execute(select(User.id, User.full_name).where(
        User.id.in_([i for i in (adj.requested_by_id, adj.decided_by_id) if i]))).all())
    products = {p.id: p for p in db.scalars(select(Product).where(
        Product.id.in_([line.product_id for line in adj.lines])))}
    units = {u.id: u for u in db.scalars(select(ProductUnit).where(
        ProductUnit.id.in_([line.unit_id for line in adj.lines if line.unit_id])))}
    return AdjustmentOut(
        id=adj.id, status=adj.status, reason=adj.reason,
        requested_by_name=names.get(adj.requested_by_id, "—"), requested_at=adj.requested_at,
        decided_by_name=names.get(adj.decided_by_id), decided_at=adj.decided_at,
        decision_note=adj.decision_note,
        lines=[AdjustmentLineOut(
            product_id=line.product_id, product_name=products[line.product_id].name,
            product_sku=products[line.product_id].sku, unit_id=line.unit_id,
            unit_label=_unit_label(units[line.unit_id]) if line.unit_id else None,
            unit_new_status=line.unit_new_status, quantity_change=line.quantity_change,
            kind=line.kind,
        ) for line in adj.lines],
    )


def _plan(db: Session, data: AdjustmentIn) -> list[StockAdjustmentLine]:
    """Check the request makes sense now, and turn it into lines."""
    lines: list[StockAdjustmentLine] = []
    seen_units: set[int] = set()
    for n, item in enumerate(data.lines, 1):
        product = db.get(Product, item.product_id)
        if product is None:
            raise HTTPException(422, f"Line {n}: that product does not exist.")
        if product.tracking_type == TrackingType.SERVICE:
            raise HTTPException(422, f"Line {n}: {product.name} is a service, so it has no "
                                     "stock to adjust.")
        if product.tracking_type == TrackingType.SERIALIZED:
            if item.unit_id is None:
                raise HTTPException(422, f"Line {n}: {product.name} is tracked by serial/IMEI — pick "
                                         "the unit and its new status.")
            unit = db.get(ProductUnit, item.unit_id)
            if unit is None or unit.product_id != product.id:
                raise HTTPException(422, f"Line {n}: that unit is not a {product.name}.")
            if unit.id in seen_units:
                raise HTTPException(422, f"Line {n}: the same unit is listed twice.")
            seen_units.add(unit.id)
            if unit.status not in ADJUSTABLE:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"Line {n}: {product.name} ({_unit_label(unit)}) is "
                                    f"{unit.status.replace('_', ' ')} and cannot be adjusted.")
            if unit.status == item.unit_new_status:
                raise HTTPException(422, f"Line {n}: that unit is already "
                                         f"{item.unit_new_status.replace('_', ' ')}.")
            going_out = item.unit_new_status in (UnitStatus.DAMAGED, UnitStatus.MISSING)
            coming_in = item.unit_new_status == UnitStatus.IN_STOCK
            if not (going_out and unit.status == UnitStatus.IN_STOCK) and not coming_in:
                raise HTTPException(422, f"Line {n}: change a {unit.status} unit back to in stock first.")
            lines.append(StockAdjustmentLine(
                product_id=product.id, unit_id=unit.id, unit_new_status=item.unit_new_status,
                quantity_change=-1 if going_out else 1,
                kind=item.unit_new_status.replace("damaged", "damage") if going_out else "adjustment",
            ))
        else:
            if item.unit_id is not None:
                raise HTTPException(422, f"Line {n}: {product.name} is counted by quantity — enter "
                                         "how many to add or remove.")
            lines.append(StockAdjustmentLine(product_id=product.id, quantity_change=item.quantity_change,
                                             kind=item.kind))
    return lines


def create_adjustment(db: Session, data: AdjustmentIn, actor: User,
                      request: Request | None) -> StockAdjustment:
    if data.approve_now and not actor.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can approve adjustments.")
    adj = StockAdjustment(status=AdjustmentStatus.PENDING, reason=data.reason,
                          requested_by_id=actor.id, lines=_plan(db, data))
    db.add(adj)
    db.flush()
    audit.record(db, action="stock_adjustment.requested", user=actor, entity_type="stock_adjustment",
                 entity_id=adj.id, details={"reason": data.reason, "lines": len(adj.lines)},
                 request=request)
    if data.approve_now:
        return _apply(db, adj, actor, None, request)
    db.commit()
    db.refresh(adj)
    return adj


def _lock_pending(db: Session, adjustment_id: int) -> StockAdjustment:
    adj = db.get(StockAdjustment, adjustment_id, with_for_update=True, populate_existing=True)
    if adj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Adjustment not found.")
    if adj.status != AdjustmentStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This adjustment was already {adj.status}.")
    return adj


def _apply(db: Session, adj: StockAdjustment, actor: User, note: str | None,
           request: Request | None) -> StockAdjustment:
    products = {pid: stock_service.lock_product(db, pid)
                for pid in sorted({line.product_id for line in adj.lines})}
    unit_ids = [line.unit_id for line in adj.lines if line.unit_id]
    units = {u.id: u for u in db.scalars(
        select(ProductUnit).where(ProductUnit.id.in_(unit_ids)).order_by(ProductUnit.id)
        .with_for_update().execution_options(populate_existing=True))} if unit_ids else {}
    for line in adj.lines:
        product = products[line.product_id]
        if line.unit_id:
            unit = units[line.unit_id]
            expected = UnitStatus.IN_STOCK if line.quantity_change < 0 else None
            if (expected and unit.status != expected) or unit.status not in ADJUSTABLE \
                    or unit.status == line.unit_new_status:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"{product.name} ({_unit_label(unit)}) is now "
                                    f"{unit.status.replace('_', ' ')}, so this adjustment no longer "
                                    "fits. Reject it and request a new one.")
            unit.status = line.unit_new_status
        stock_service.record_movement(
            db, product, quantity_change=line.quantity_change,
            movement_type=KIND_TO_MOVEMENT[line.kind], user=actor,
            unit_id=line.unit_id, reference_type="adjustment", reference_id=adj.id, reason=adj.reason,
        )  # refuses to take stock below zero
    adj.status = AdjustmentStatus.APPROVED
    adj.decided_by_id = actor.id
    adj.decided_at = now_utc()
    adj.decision_note = note
    db.flush()
    audit.record(db, action="stock_adjustment.approved", user=actor, entity_type="stock_adjustment",
                 entity_id=adj.id,
                 details={"reason": adj.reason,
                          "changes": [f"{products[line.product_id].name}: {line.quantity_change:+d}"
                                      for line in adj.lines]},
                 request=request)
    db.commit()
    db.refresh(adj)
    return adj


def approve(db: Session, adjustment_id: int, data: DecisionIn, actor: User,
            request: Request | None) -> StockAdjustment:
    return _apply(db, _lock_pending(db, adjustment_id), actor, data.note, request)


def reject(db: Session, adjustment_id: int, data: DecisionIn, actor: User,
           request: Request | None) -> StockAdjustment:
    adj = _lock_pending(db, adjustment_id)
    adj.status = AdjustmentStatus.REJECTED
    adj.decided_by_id = actor.id
    adj.decided_at = now_utc()
    adj.decision_note = data.note
    db.flush()
    audit.record(db, action="stock_adjustment.rejected", user=actor, entity_type="stock_adjustment",
                 entity_id=adj.id, details={"reason": adj.reason, "note": data.note}, request=request)
    db.commit()
    db.refresh(adj)
    return adj


def list_adjustments(db: Session, *, viewer: User, status_: str | None, page: int,
                     page_size: int) -> dict:
    """The owner sees every adjustment; sales staff see their own requests."""
    conditions = []
    if not viewer.is_owner:
        conditions.append(StockAdjustment.requested_by_id == viewer.id)
    if status_:
        conditions.append(StockAdjustment.status == status_)
    total = db.scalar(select(func.count()).select_from(StockAdjustment).where(*conditions)) or 0
    items = db.scalars(select(StockAdjustment).where(*conditions)
                       .order_by(StockAdjustment.id.desc())
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [adjustment_out(db, a) for a in items], "total": total, "page": page,
            "page_size": page_size}
