"""Supplier returns and purchase reversals (owner only).

A supplier return sends goods from a posted purchase back. Stock leaves at the landed cost it came
in at (so the weighted average is exactly unwound) and the supplier is credited the invoice price,
which lowers what the shop owes — below zero, the supplier owes the shop.

A purchase reversal cancels a purchase that was entered by mistake. It is only possible while every
item from it is still in the shop and nothing has been paid or returned on it; otherwise use a
supplier return (and, for money, a payment reversal).
"""
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import business_today, now_utc
from app.models import (
    MovementType,
    ProductUnit,
    Purchase,
    PurchaseStatus,
    Supplier,
    SupplierPayment,
    SupplierReturn,
    SupplierReturnLine,
    TrackingType,
    UnitStatus,
    User,
)
from app.schemas.returns import ReasonIn, SupplierReturnIn
from app.services import stock as stock_service

KOBO = Decimal("0.01")
ZERO = Decimal("0.00")


def _lock_purchase(db: Session, purchase_id: int) -> Purchase:
    """Lock the supplier, then the purchase. A return or reversal changes what the shop owes the
    supplier, so it must queue behind (and ahead of) a supplier payment, which locks the same
    supplier row first. In the other order the two deadlock, and a payment could slip in against
    a purchase being reversed. FOR NO KEY UPDATE does not block new purchases from the supplier."""
    supplier_id = db.scalar(select(Purchase.supplier_id).where(Purchase.id == purchase_id))
    if supplier_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found.")
    db.get(Supplier, supplier_id, with_for_update={"key_share": True})
    return db.get(Purchase, purchase_id, with_for_update=True, populate_existing=True)


def _returned_by_line(db: Session, line_ids: list[int]) -> dict[int, int]:
    if not line_ids:
        return {}
    return {lid: int(q) for lid, q in db.execute(
        select(SupplierReturnLine.purchase_line_id, func.sum(SupplierReturnLine.quantity))
        .where(SupplierReturnLine.purchase_line_id.in_(line_ids))
        .group_by(SupplierReturnLine.purchase_line_id)
    )}


def _unit_label(u: ProductUnit) -> str:
    return f"IMEI {u.imei1}" if u.imei1 else f"serial {u.serial_number}"


def record_return(db: Session, purchase_id: int, data: SupplierReturnIn, actor: User,
                  request: Request | None) -> tuple[SupplierReturn, bool]:
    existing = db.scalar(select(SupplierReturn).where(
        SupplierReturn.idempotency_key == data.idempotency_key))
    if existing:
        if existing.purchase_id != purchase_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This return form was already used.")
        return existing, False

    purchase = _lock_purchase(db, purchase_id)
    if purchase.status != PurchaseStatus.POSTED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{purchase.reference} is {purchase.status}: only posted purchases "
                            "can have goods returned.")
    lines = {line.id: line for line in purchase.lines}
    for item in data.lines:
        if item.purchase_line_id not in lines:
            raise HTTPException(422, f"That item is not on {purchase.reference}.")
    products = {pid: stock_service.lock_product(db, pid)
                for pid in sorted({lines[i.purchase_line_id].product_id for i in data.lines})}
    unit_ids = [u for i in data.lines for u in i.unit_ids]
    if len(unit_ids) != len(set(unit_ids)):
        raise HTTPException(422, "The same unit is listed twice.")
    units = {u.id: u for u in db.scalars(
        select(ProductUnit).where(ProductUnit.id.in_(unit_ids)).order_by(ProductUnit.id)
        .with_for_update().execution_options(populate_existing=True))} if unit_ids else {}

    already = _returned_by_line(db, list(lines))
    wanted: dict[int, int] = {}
    leaving: dict[int, int] = {}  # product id → quantity items leaving
    for item in data.lines:
        line = lines[item.purchase_line_id]
        product = products[line.product_id]
        wanted[line.id] = wanted.get(line.id, 0) + item.quantity
        left = line.quantity - already.get(line.id, 0)
        if wanted[line.id] > left:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Only {left} {product.name} from {purchase.reference} can still be "
                                "returned.")
        if product.tracking_type == TrackingType.SERIALIZED:
            if len(item.unit_ids) != item.quantity:
                raise HTTPException(422, f"Pick which {product.name} unit(s) go back to the supplier.")
            for uid in item.unit_ids:
                unit = units.get(uid)
                if unit is None or unit.purchase_line_id != line.id:
                    raise HTTPException(422, f"That unit did not come in on this line of "
                                             f"{purchase.reference}.")
                if unit.status != UnitStatus.IN_STOCK:
                    raise HTTPException(status.HTTP_409_CONFLICT,
                                        f"{product.name} ({_unit_label(unit)}) is not in stock "
                                        f"(it is {unit.status.replace('_', ' ')}).")
        else:
            if item.unit_ids:
                raise HTTPException(422, f"{product.name} is counted by quantity: no units to pick.")
            leaving[product.id] = leaving.get(product.id, 0) + item.quantity
    for pid, qty in leaving.items():
        if qty > products[pid].qty_on_hand:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Only {products[pid].qty_on_hand} {products[pid].name} in stock, "
                                f"so {qty} cannot go back.")

    credit_total = sum(((lines[i.purchase_line_id].unit_cost * i.quantity).quantize(KOBO)
                        for i in data.lines), ZERO)
    if credit_total <= 0:
        raise HTTPException(422, "These items cost nothing, so there is no credit. "
                                 "Use a stock adjustment instead.")
    ret = SupplierReturn(purchase_id=purchase.id, supplier_id=purchase.supplier_id,
                         business_date=business_today(), reason=data.reason,
                         credit_total=credit_total, recorded_by_id=actor.id,
                         idempotency_key=data.idempotency_key)
    db.add(ret)
    try:
        db.flush()
        for item in data.lines:
            line = lines[item.purchase_line_id]
            product = products[line.product_id]
            common = {"movement_type": MovementType.SUPPLIER_RETURN, "user": actor,
                      "reference_type": "supplier_return", "reference_id": ret.id,
                      "reason": data.reason, "revalue": True}
            if item.unit_ids:
                for uid in item.unit_ids:
                    unit = units[uid]
                    db.add(SupplierReturnLine(return_id=ret.id, purchase_line_id=line.id,
                                              unit_id=unit.id, quantity=1,
                                              credit_value=line.unit_cost.quantize(KOBO)))
                    unit.status = UnitStatus.RETURNED_TO_SUPPLIER
                    stock_service.record_movement(db, product, quantity_change=-1, unit_id=unit.id,
                                                  unit_cost=unit.unit_cost, **common)
            else:
                db.add(SupplierReturnLine(return_id=ret.id, purchase_line_id=line.id,
                                          quantity=item.quantity,
                                          credit_value=(line.unit_cost * item.quantity).quantize(KOBO)))
                stock_service.record_movement(db, product, quantity_change=-item.quantity,
                                              unit_cost=line.landed_unit_cost, **common)
        db.flush()
        audit.record(db, action="purchase.returned", user=actor, entity_type="purchase",
                     entity_id=purchase.id,
                     details={"reference": purchase.reference, "return_id": ret.id,
                              "credit": str(credit_total), "reason": data.reason},
                     request=request)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SupplierReturn).where(
            SupplierReturn.idempotency_key == data.idempotency_key))
        if existing:
            return existing, False
        raise
    db.refresh(ret)
    return ret, True


def reverse_purchase(db: Session, purchase_id: int, data: ReasonIn, actor: User,
                     request: Request | None) -> Purchase:
    """Undo a posted purchase entered by mistake. Reversing twice changes nothing."""
    purchase = _lock_purchase(db, purchase_id)
    if purchase.status == PurchaseStatus.REVERSED:
        return purchase
    if purchase.status != PurchaseStatus.POSTED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{purchase.reference} is a draft: discard it instead.")
    if db.scalar(select(SupplierPayment.id).where(SupplierPayment.purchase_id == purchase.id,
                                                  SupplierPayment.reversed.is_(False)).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Payments were recorded against {purchase.reference}. Reverse those "
                            "payments first.")
    if db.scalar(select(SupplierReturn.id).where(SupplierReturn.purchase_id == purchase.id).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Goods from {purchase.reference} were already returned to the supplier. "
                            "Return the rest instead of reversing.")

    products = {pid: stock_service.lock_product(db, pid)
                for pid in sorted({line.product_id for line in purchase.lines})}
    units = list(db.scalars(
        select(ProductUnit).where(ProductUnit.purchase_line_id.in_([line.id for line in purchase.lines]))
        .order_by(ProductUnit.id).with_for_update().execution_options(populate_existing=True)))
    gone = [u for u in units if u.status != UnitStatus.IN_STOCK]
    if gone:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{purchase.reference} cannot be reversed: {products[gone[0].product_id].name} "
                            f"({_unit_label(gone[0])}) is {gone[0].status.replace('_', ' ')}.")
    needed: dict[int, int] = {}
    for line in purchase.lines:
        if products[line.product_id].tracking_type == TrackingType.QUANTITY:
            needed[line.product_id] = needed.get(line.product_id, 0) + line.quantity
    for pid, qty in needed.items():
        if qty > products[pid].qty_on_hand:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{purchase.reference} cannot be reversed: only "
                                f"{products[pid].qty_on_hand} of the {qty} {products[pid].name} are "
                                "still in stock.")

    by_line = {}
    for u in units:
        by_line.setdefault(u.purchase_line_id, []).append(u)
    for line in purchase.lines:
        product = products[line.product_id]
        common = {"movement_type": MovementType.REVERSAL, "user": actor, "reference_type": "purchase",
                  "reference_id": purchase.id, "reason": data.reason, "revalue": True,
                  "unit_cost": line.landed_unit_cost}
        if product.tracking_type == TrackingType.SERIALIZED:
            for unit in by_line.get(line.id, []):
                unit.status = UnitStatus.VOIDED
                stock_service.record_movement(db, product, quantity_change=-1, unit_id=unit.id, **common)
        else:
            stock_service.record_movement(db, product, quantity_change=-line.quantity, **common)
    purchase.status = PurchaseStatus.REVERSED
    purchase.reversed_at = now_utc()
    purchase.reversed_by_id = actor.id
    purchase.reversal_reason = data.reason
    db.flush()
    audit.record(db, action="purchase.reversed", user=actor, entity_type="purchase",
                 entity_id=purchase.id,
                 details={"reference": purchase.reference, "total": str(purchase.total),
                          "reason": data.reason},
                 request=request)
    db.commit()
    db.refresh(purchase)
    return purchase
