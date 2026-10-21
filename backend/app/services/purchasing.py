"""Suppliers, purchases and supplier payments.

- A draft purchase changes nothing. Posting it receives the stock in one all-or-nothing step:
  quantity items through one ledger movement each (updating the weighted-average cost),
  serial/IMEI items as new units, each with its own movement and its own landed cost.
- Additional costs (transport, customs…) are spread over the lines by value, so every item's
  landed cost includes its share.
- What the shop owes a supplier is always calculated: posted purchases − payments.
  Payments can never exceed it.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import business_today, now_utc
from app.models import (
    MovementType,
    PaymentMethod,
    Product,
    ProductUnit,
    Purchase,
    PurchaseLine,
    PurchaseLineUnit,
    PurchaseStatus,
    Supplier,
    SupplierPayment,
    SupplierReturn,
    SupplierReturnLine,
    TrackingType,
    UnitStatus,
    User,
)
from app.models.purchasing import PURCHASE_REF_SEQ
from app.schemas.purchasing import (
    PurchaseIn,
    PurchaseLineOut,
    PurchaseOut,
    PurchaseUnitOut,
    SupplierCreate,
    SupplierOut,
    SupplierPaymentIn,
    SupplierPaymentOut,
    StockUnitRef,
    SupplierReturnSummary,
    SupplierUpdate,
)
from app.services import stock as stock_service
from app.services.catalog import like_pattern
from app.services.units import check_identifiers_free

KOBO = Decimal("0.01")
COST = Decimal("0.0001")
ZERO = Decimal("0.00")


# ---------- Suppliers ----------

def supplier_balances(db: Session, supplier_ids: list[int]) -> dict[int, tuple[Decimal, Decimal]]:
    """{supplier_id: (posted purchases less goods returned, payments)} — calculated, never stored.

    The balance (first − second) is what the shop owes; below zero, the supplier owes the shop.
    """
    if not supplier_ids:
        return {}
    bought = dict(db.execute(
        select(Purchase.supplier_id, func.sum(Purchase.total - purchase_credit_expr()))
        .where(Purchase.supplier_id.in_(supplier_ids), Purchase.status == PurchaseStatus.POSTED)
        .group_by(Purchase.supplier_id)
    ).all())
    paid = dict(db.execute(
        select(SupplierPayment.supplier_id, func.sum(SupplierPayment.amount))
        .where(SupplierPayment.supplier_id.in_(supplier_ids), SupplierPayment.reversed.is_(False))
        .group_by(SupplierPayment.supplier_id)
    ).all())
    return {i: (bought.get(i) or ZERO, paid.get(i) or ZERO) for i in supplier_ids}


def supplier_out(s: Supplier, totals: tuple[Decimal, Decimal]) -> SupplierOut:
    bought, paid = totals
    return SupplierOut(
        id=s.id, name=s.name, phone=s.phone, email=s.email, address=s.address, notes=s.notes,
        is_active=s.is_active, purchases_total=bought, paid_total=paid, balance=bought - paid,
        created_at=s.created_at,
    )


def _supplier_name_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    q = select(Supplier.id).where(func.lower(Supplier.name) == name.lower())
    if exclude_id is not None:
        q = q.where(Supplier.id != exclude_id)
    return db.scalar(q) is not None


def create_supplier(db: Session, data: SupplierCreate, actor: User,
                    request: Request | None) -> Supplier:
    if _supplier_name_taken(db, data.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "A supplier with that name exists.")
    supplier = Supplier(**data.model_dump(), is_active=True)
    db.add(supplier)
    db.flush()
    audit.record(db, action="supplier.created", user=actor, entity_type="supplier",
                 entity_id=supplier.id, details={"name": supplier.name}, request=request)
    db.commit()
    db.refresh(supplier)
    return supplier


def update_supplier(db: Session, supplier_id: int, data: SupplierUpdate, actor: User,
                    request: Request | None) -> Supplier:
    supplier = db.get(Supplier, supplier_id, with_for_update=True)
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found.")
    fields = data.model_dump(exclude_unset=True)
    if "name" in fields and _supplier_name_taken(db, fields["name"], exclude_id=supplier.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "A supplier with that name exists.")
    changes: dict = {}
    for field, new in fields.items():
        old = getattr(supplier, field)
        if old != new:
            changes[field] = {"from": old, "to": new}
            setattr(supplier, field, new)
    if not changes:
        db.rollback()
        return supplier
    audit.record(db, action="supplier.updated", user=actor, entity_type="supplier",
                 entity_id=supplier.id, details=changes, request=request)
    db.commit()
    db.refresh(supplier)
    return supplier


def get_supplier(db: Session, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found.")
    return supplier


def list_suppliers(db: Session, *, q: str | None, include_inactive: bool,
                   owing_only: bool) -> list[SupplierOut]:
    query = select(Supplier).order_by(func.lower(Supplier.name))
    if not include_inactive:
        query = query.where(Supplier.is_active.is_(True))
    if q and q.strip():
        pattern = like_pattern(q.strip())
        query = query.where(or_(Supplier.name.ilike(pattern, escape="\\"),
                                Supplier.phone.ilike(pattern, escape="\\")))
    suppliers = list(db.scalars(query))
    totals = supplier_balances(db, [s.id for s in suppliers])
    out = [supplier_out(s, totals[s.id]) for s in suppliers]
    return [s for s in out if s.balance > 0] if owing_only else out


# ---------- Cost allocation ----------

@dataclass
class LineCost:
    line_total: Decimal
    allocated_extra_cost: Decimal
    landed_unit_cost: Decimal


def allocate_costs(lines: list[tuple[int, Decimal]], additional_costs: Decimal) -> list[LineCost]:
    """Spread additional costs over lines in proportion to their value.

    `lines` is [(quantity, unit_cost)]. Shares are rounded to kobo and the last line takes
    the remainder, so the shares always add up to exactly `additional_costs`. If every line
    is free (value 0), the costs are spread by quantity instead.
    """
    totals = [(Decimal(q) * c).quantize(KOBO, ROUND_HALF_UP) for q, c in lines]
    weights = totals if sum(totals) > 0 else [Decimal(q) for q, _ in lines]
    weight_sum = sum(weights)
    shares: list[Decimal] = []
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            shares.append(additional_costs - sum(shares))
        else:
            shares.append((additional_costs * w / weight_sum).quantize(KOBO, ROUND_HALF_UP)
                          if weight_sum else ZERO)
    return [
        LineCost(line_total=t, allocated_extra_cost=share,
                 landed_unit_cost=(c + share / Decimal(q)).quantize(COST, ROUND_HALF_UP))
        for (q, c), t, share in zip(lines, totals, shares, strict=True)
    ]


# ---------- Purchases ----------

def purchase_credit_expr():
    """Correlated SQL: credit for goods returned to the supplier from Purchase.id."""
    return (select(func.coalesce(func.sum(SupplierReturn.credit_total), 0))
            .where(SupplierReturn.purchase_id == Purchase.id).scalar_subquery())


def purchase_credits(db: Session, purchase_ids: list[int]) -> dict[int, Decimal]:
    if not purchase_ids:
        return {}
    return dict(db.execute(
        select(SupplierReturn.purchase_id, func.sum(SupplierReturn.credit_total))
        .where(SupplierReturn.purchase_id.in_(purchase_ids)).group_by(SupplierReturn.purchase_id)
    ).all())


def _amounts_paid(db: Session, purchase_ids: list[int]) -> dict[int, Decimal]:
    if not purchase_ids:
        return {}
    return dict(db.execute(
        select(SupplierPayment.purchase_id, func.sum(SupplierPayment.amount))
        .where(SupplierPayment.purchase_id.in_(purchase_ids), SupplierPayment.reversed.is_(False))
        .group_by(SupplierPayment.purchase_id)
    ).all())


def purchase_out(db: Session, p: Purchase, with_lines: bool = False) -> PurchaseOut:
    paid = _amounts_paid(db, [p.id]).get(p.id) or ZERO
    credit = purchase_credits(db, [p.id]).get(p.id) or ZERO
    names = dict(db.execute(select(User.id, User.full_name).where(
        User.id.in_([i for i in (p.created_by_id, p.posted_by_id) if i]))).all())
    lines = returns = None
    if with_lines:
        returns = []
        for r in db.scalars(select(SupplierReturn).where(SupplierReturn.purchase_id == p.id)
                            .order_by(SupplierReturn.id)):
            line_names = {line.id: line.product.name for line in p.lines}
            unit_labels = dict(db.execute(select(
                ProductUnit.id, func.coalesce(ProductUnit.imei1, ProductUnit.serial_number)).where(
                ProductUnit.id.in_([rl.unit_id for rl in r.lines if rl.unit_id]))).all())
            returns.append(SupplierReturnSummary(
                id=r.id, returned_at=r.returned_at, reason=r.reason, credit_total=r.credit_total,
                recorded_by_name=db.scalar(select(User.full_name).where(User.id == r.recorded_by_id)),
                items=[f"{line_names[rl.purchase_line_id]} ({unit_labels[rl.unit_id]})" if rl.unit_id
                       else f"{line_names[rl.purchase_line_id]} × {rl.quantity}" for rl in r.lines],
            ))
        line_ids = [line.id for line in p.lines]
        returned_qty = dict(db.execute(
            select(SupplierReturnLine.purchase_line_id, func.sum(SupplierReturnLine.quantity))
            .where(SupplierReturnLine.purchase_line_id.in_(line_ids))
            .group_by(SupplierReturnLine.purchase_line_id)).all()) if line_ids else {}
        in_stock: dict[int, list[StockUnitRef]] = {}
        for u in db.scalars(select(ProductUnit).where(
                ProductUnit.purchase_line_id.in_(line_ids), ProductUnit.status == UnitStatus.IN_STOCK)
                .order_by(ProductUnit.id)) if line_ids else []:
            in_stock.setdefault(u.purchase_line_id, []).append(
                StockUnitRef(id=u.id, label=u.imei1 or u.serial_number or f"#{u.id}"))
        lines = [
            PurchaseLineOut(
                id=line.id, product_id=line.product_id, product_name=line.product.name,
                product_sku=line.product.sku, tracking_type=line.product.tracking_type,
                quantity=line.quantity, unit_cost=line.unit_cost, line_total=line.line_total,
                allocated_extra_cost=line.allocated_extra_cost,
                landed_unit_cost=line.landed_unit_cost,
                units=[PurchaseUnitOut.model_validate(u) for u in line.units],
                returned_quantity=int(returned_qty.get(line.id, 0)),
                units_in_stock=in_stock.get(line.id, []),
            )
            for line in p.lines
        ]
    return PurchaseOut(
        id=p.id, reference=p.reference, supplier_id=p.supplier_id, supplier_name=p.supplier.name,
        supplier_invoice_no=p.supplier_invoice_no, purchase_date=p.purchase_date, status=p.status,
        subtotal=p.subtotal, additional_costs=p.additional_costs, total=p.total,
        amount_paid=paid,
        returned_credit=credit,
        balance_due=(p.total - credit - paid) if p.status == PurchaseStatus.POSTED else ZERO,
        reversed_at=p.reversed_at, reversal_reason=p.reversal_reason,
        notes=p.notes, line_count=len(p.lines),
        created_by_name=names.get(p.created_by_id), posted_by_name=names.get(p.posted_by_id),
        posted_at=p.posted_at, created_at=p.created_at, lines=lines, returns=returns,
    )


def _check_header(db: Session, data: PurchaseIn, purchase_id: int | None) -> Supplier:
    supplier = db.get(Supplier, data.supplier_id)
    if supplier is None:
        raise HTTPException(422, "That supplier does not exist.")
    if not supplier.is_active:
        raise HTTPException(422, f"The supplier “{supplier.name}” is turned off.")
    if data.purchase_date > business_today():
        raise HTTPException(422, "The purchase date cannot be in the future.")
    if data.supplier_invoice_no:
        q = select(Purchase.reference).where(
            Purchase.supplier_id == supplier.id,
            func.lower(Purchase.supplier_invoice_no) == data.supplier_invoice_no.lower(),
        )
        if purchase_id is not None:
            q = q.where(Purchase.id != purchase_id)
        existing = db.scalar(q)
        if existing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Invoice {data.supplier_invoice_no} from {supplier.name} is already entered "
                f"as {existing}.",
            )
    return supplier


def _build_lines(db: Session, data: PurchaseIn) -> list[PurchaseLine]:
    """Validate the lines and work out every line's landed cost. Raises on the first problem."""
    candidates = []
    products: dict[int, Product] = {}
    for n, line in enumerate(data.lines, 1):
        product = products.get(line.product_id) or db.get(Product, line.product_id)
        if product is None:
            raise HTTPException(422, f"Line {n}: that product does not exist.")
        if not product.is_active:
            raise HTTPException(422, f"Line {n}: “{product.name}” is turned off.")
        products[product.id] = product
        if product.tracking_type == TrackingType.SERVICE:
            raise HTTPException(422, f"Line {n}: {product.name} is a service, so it isn't bought "
                                     "from suppliers. Record materials as their own products, "
                                     "or as an expense.")
        if product.tracking_type == TrackingType.SERIALIZED:
            if len(line.units) != line.quantity:
                raise HTTPException(
                    422,
                    f"Line {n}: {product.name} is tracked by serial/IMEI, so enter one for each of "
                    f"the {line.quantity} unit(s) ({len(line.units)} entered).",
                )
            for u_n, u in enumerate(line.units, 1):
                candidates.append((f"Line {n}, unit {u_n}: ", u.serial_number, u.imei1, u.imei2))
        elif line.units:
            raise HTTPException(422, f"Line {n}: {product.name} is counted by quantity, "
                                     "so it takes no serial/IMEI numbers.")
    check_identifiers_free(db, candidates)

    costs = allocate_costs([(line.quantity, line.unit_cost) for line in data.lines],
                           data.additional_costs) if data.lines else []
    return [
        PurchaseLine(
            product_id=line.product_id, quantity=line.quantity, unit_cost=line.unit_cost,
            line_total=cost.line_total, allocated_extra_cost=cost.allocated_extra_cost,
            landed_unit_cost=cost.landed_unit_cost,
            units=[PurchaseLineUnit(serial_number=u.serial_number, imei1=u.imei1, imei2=u.imei2,
                                    condition=u.condition) for u in line.units],
        )
        for line, cost in zip(data.lines, costs, strict=True)
    ]


def _apply(purchase: Purchase, data: PurchaseIn, lines: list[PurchaseLine]) -> None:
    purchase.supplier_id = data.supplier_id
    purchase.supplier_invoice_no = data.supplier_invoice_no
    purchase.purchase_date = data.purchase_date
    purchase.additional_costs = data.additional_costs
    purchase.notes = data.notes
    purchase.lines = lines
    purchase.subtotal = sum((line.line_total for line in lines), ZERO)
    purchase.total = purchase.subtotal + purchase.additional_costs


def create_draft(db: Session, data: PurchaseIn, actor: User,
                 request: Request | None) -> tuple[Purchase, bool]:
    """Returns (purchase, created). A repeated idempotency key returns the first draft."""
    if data.idempotency_key:
        existing = db.scalar(select(Purchase).where(Purchase.idempotency_key == data.idempotency_key))
        if existing:
            return existing, False
    _check_header(db, data, None)
    lines = _build_lines(db, data)
    number = db.scalar(select(PURCHASE_REF_SEQ.next_value()))
    purchase = Purchase(reference=f"PO-{data.purchase_date.year}-{number:05d}",
                        status=PurchaseStatus.DRAFT, idempotency_key=data.idempotency_key,
                        created_by_id=actor.id)
    _apply(purchase, data, lines)
    db.add(purchase)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if data.idempotency_key:
            existing = db.scalar(select(Purchase).where(
                Purchase.idempotency_key == data.idempotency_key))
            if existing:
                return existing, False
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This invoice was entered by someone else a moment ago. Nothing was saved.")
    audit.record(db, action="purchase.created", user=actor, entity_type="purchase",
                 entity_id=purchase.id,
                 details={"reference": purchase.reference, "supplier": purchase.supplier_id,
                          "total": str(purchase.total), "lines": len(lines)},
                 request=request)
    db.commit()
    db.refresh(purchase)
    return purchase, True


def _lock_purchase(db: Session, purchase_id: int) -> Purchase:
    purchase = db.get(Purchase, purchase_id, with_for_update=True, populate_existing=True)
    if purchase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found.")
    return purchase


def _must_be_draft(purchase: Purchase) -> None:
    if purchase.status != PurchaseStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{purchase.reference} is already {purchase.status} and can no longer "
                            "be changed.")


def update_draft(db: Session, purchase_id: int, data: PurchaseIn, actor: User,
                 request: Request | None) -> Purchase:
    purchase = _lock_purchase(db, purchase_id)
    _must_be_draft(purchase)
    _check_header(db, data, purchase.id)
    lines = _build_lines(db, data)
    before = {"total": str(purchase.total), "lines": len(purchase.lines)}
    purchase.lines.clear()
    db.flush()  # remove old lines before adding the new ones
    _apply(purchase, data, lines)
    db.flush()
    audit.record(db, action="purchase.updated", user=actor, entity_type="purchase",
                 entity_id=purchase.id,
                 details={"reference": purchase.reference,
                          "total": {"from": before["total"], "to": str(purchase.total)},
                          "lines": {"from": before["lines"], "to": len(lines)}},
                 request=request)
    db.commit()
    db.refresh(purchase)
    return purchase


def delete_draft(db: Session, purchase_id: int, actor: User, request: Request | None) -> None:
    """Discard a draft. It never touched stock or balances. Posted purchases cannot be deleted."""
    purchase = _lock_purchase(db, purchase_id)
    _must_be_draft(purchase)
    audit.record(db, action="purchase.deleted", user=actor, entity_type="purchase",
                 entity_id=purchase.id,
                 details={"reference": purchase.reference, "total": str(purchase.total)},
                 request=request)
    db.delete(purchase)
    db.commit()


_POST_RACE = HTTPException(
    status.HTTP_409_CONFLICT,
    "A serial/IMEI number on this purchase was registered by someone else a moment ago. "
    "Nothing was posted — check the units and try again.",
)


def post_purchase(db: Session, purchase_id: int, actor: User,
                  request: Request | None) -> Purchase:
    """Receive the stock. All-or-nothing; posting an already-posted purchase changes nothing."""
    purchase = _lock_purchase(db, purchase_id)
    if purchase.status == PurchaseStatus.POSTED:
        return purchase  # double click / retry: the first post already did the work
    _must_be_draft(purchase)
    if not purchase.lines:
        raise HTTPException(422, "Add at least one product before posting.")
    if not purchase.supplier.is_active:
        raise HTTPException(422, f"The supplier “{purchase.supplier.name}” is turned off.")

    # Re-check everything: products may have changed since the draft was saved.
    candidates = []
    for n, line in enumerate(purchase.lines, 1):
        product = line.product
        if not product.is_active:
            raise HTTPException(422, f"Line {n}: “{product.name}” is turned off.")
        serialized = product.tracking_type == TrackingType.SERIALIZED
        if serialized and len(line.units) != line.quantity:
            raise HTTPException(422, f"Line {n}: {product.name} needs a serial/IMEI for each of "
                                     f"its {line.quantity} unit(s).")
        if not serialized and line.units:
            raise HTTPException(422, f"Line {n}: {product.name} is counted by quantity, "
                                     "so it takes no serial/IMEI numbers.")
        candidates += [(f"Line {n}, unit {i}: ", u.serial_number, u.imei1, u.imei2)
                       for i, u in enumerate(line.units, 1)]
    check_identifiers_free(db, candidates)

    # Lock every product once, in id order, so two postings can never deadlock.
    locked = {pid: stock_service.lock_product(db, pid)
              for pid in sorted({line.product_id for line in purchase.lines})}
    try:
        for line in purchase.lines:
            product = locked[line.product_id]
            common = {"movement_type": MovementType.PURCHASE, "user": actor,
                      "unit_cost": line.landed_unit_cost, "reference_type": "purchase",
                      "reference_id": purchase.id}
            if product.tracking_type == TrackingType.SERIALIZED:
                for u in line.units:
                    unit = ProductUnit(product_id=product.id, serial_number=u.serial_number,
                                       imei1=u.imei1, imei2=u.imei2, condition=u.condition,
                                       unit_cost=line.landed_unit_cost, status=UnitStatus.IN_STOCK,
                                       purchase_line_id=line.id, created_by_id=actor.id)
                    db.add(unit)
                    db.flush()
                    stock_service.record_movement(db, product, quantity_change=1,
                                                  unit_id=unit.id, **common)
            else:
                stock_service.record_movement(db, product, quantity_change=line.quantity, **common)
        db.flush()
        purchase.status = PurchaseStatus.POSTED
        purchase.posted_at = now_utc()
        purchase.posted_by_id = actor.id
        audit.record(db, action="purchase.posted", user=actor, entity_type="purchase",
                     entity_id=purchase.id,
                     details={"reference": purchase.reference, "total": str(purchase.total),
                              "supplier": purchase.supplier.name,
                              "items": sum(line.quantity for line in purchase.lines)},
                     request=request)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _POST_RACE
    db.refresh(purchase)
    return purchase


def get_purchase(db: Session, purchase_id: int) -> Purchase:
    purchase = db.get(Purchase, purchase_id)
    if purchase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found.")
    return purchase


def list_purchases(db: Session, *, status_: str | None, supplier_id: int | None, q: str | None,
                   unpaid_only: bool, page: int, page_size: int) -> dict:
    conditions = []
    if status_:
        conditions.append(Purchase.status == status_)
    if supplier_id is not None:
        conditions.append(Purchase.supplier_id == supplier_id)
    if q and q.strip():
        pattern = like_pattern(q.strip())
        conditions.append(or_(Purchase.reference.ilike(pattern, escape="\\"),
                              Purchase.supplier_invoice_no.ilike(pattern, escape="\\")))
    if unpaid_only:
        paid = (select(func.coalesce(func.sum(SupplierPayment.amount), 0))
                .where(SupplierPayment.purchase_id == Purchase.id,
                       SupplierPayment.reversed.is_(False))
                .scalar_subquery())
        conditions += [Purchase.status == PurchaseStatus.POSTED,
                       Purchase.total - purchase_credit_expr() > paid]
    total = db.scalar(select(func.count()).select_from(Purchase).where(*conditions)) or 0
    purchases = db.scalars(
        select(Purchase).where(*conditions)
        .order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [purchase_out(db, p) for p in purchases], "total": total, "page": page,
            "page_size": page_size}


# ---------- Supplier payments ----------

def payment_out(db: Session, pay: SupplierPayment) -> SupplierPaymentOut:
    supplier = db.get(Supplier, pay.supplier_id)
    method = db.get(PaymentMethod, pay.method_id)
    reference = db.scalar(select(Purchase.reference).where(Purchase.id == pay.purchase_id)) \
        if pay.purchase_id else None
    recorder = db.scalar(select(User.full_name).where(User.id == pay.recorded_by_id)) \
        if pay.recorded_by_id else None
    return SupplierPaymentOut(
        id=pay.id, supplier_id=pay.supplier_id, supplier_name=supplier.name,
        purchase_id=pay.purchase_id, purchase_reference=reference, amount=pay.amount,
        method_id=pay.method_id, method_name=method.name, paid_on=pay.paid_on,
        reference=pay.reference, notes=pay.notes, recorded_by_name=recorder,
        reversed=pay.reversed, reversal_reason=pay.reversal_reason, created_at=pay.created_at,
    )


def _replay(db: Session, key: str, data: SupplierPaymentIn) -> SupplierPayment | None:
    existing = db.scalar(select(SupplierPayment).where(SupplierPayment.idempotency_key == key))
    if existing is None:
        return None
    if existing.supplier_id != data.supplier_id or existing.amount != data.amount:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This payment form was already used for a different payment. "
                            "Reload the page and try again.")
    return existing


def record_payment(db: Session, data: SupplierPaymentIn, actor: User,
                   request: Request | None) -> tuple[SupplierPayment, bool]:
    """Returns (payment, created). Re-sending the same form returns the first payment."""
    existing = _replay(db, data.idempotency_key, data)
    if existing:
        return existing, False

    # Lock the supplier so two payments cannot both pass the "not more than owed" check.
    supplier = db.get(Supplier, data.supplier_id, with_for_update=True)
    if supplier is None:
        raise HTTPException(422, "That supplier does not exist.")
    method = db.get(PaymentMethod, data.method_id)
    if method is None or not method.is_active:
        raise HTTPException(422, "Choose an active payment method.")
    paid_on = data.paid_on or business_today()
    if paid_on > business_today():
        raise HTTPException(422, "The payment date cannot be in the future.")

    bought, paid = supplier_balances(db, [supplier.id])[supplier.id]
    owed = bought - paid
    if data.purchase_id is not None:
        purchase = db.get(Purchase, data.purchase_id)
        if purchase is None or purchase.supplier_id != supplier.id:
            raise HTTPException(422, f"That purchase is not from {supplier.name}.")
        if purchase.status != PurchaseStatus.POSTED:
            raise HTTPException(422, f"{purchase.reference} is not posted, so nothing is owed on it yet.")
        due = (purchase.total - (purchase_credits(db, [purchase.id]).get(purchase.id) or ZERO)
               - (_amounts_paid(db, [purchase.id]).get(purchase.id) or ZERO))
        owed = min(owed, due)
        what = f"on {purchase.reference}"
    else:
        what = f"to {supplier.name}"
    if data.amount > owed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"That is more than the shop owes {what} (₦{owed:,.2f}). Overpayments are not allowed.",
        )

    payment = SupplierPayment(
        supplier_id=supplier.id, purchase_id=data.purchase_id, amount=data.amount,
        method_id=method.id, paid_on=paid_on, reference=data.reference, notes=data.notes,
        recorded_by_id=actor.id, idempotency_key=data.idempotency_key, reversed=False,
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:  # the same form submitted twice at the same moment
        db.rollback()
        existing = _replay(db, data.idempotency_key, data)
        if existing:
            return existing, False
        raise
    audit.record(db, action="supplier_payment.recorded", user=actor, entity_type="supplier_payment",
                 entity_id=payment.id,
                 details={"supplier": supplier.name, "amount": str(payment.amount),
                          "method": method.name, "purchase_id": payment.purchase_id},
                 request=request)
    db.commit()
    db.refresh(payment)
    return payment, True


def list_payments(db: Session, *, supplier_id: int | None, purchase_id: int | None,
                  page: int, page_size: int) -> dict:
    conditions = []
    if supplier_id is not None:
        conditions.append(SupplierPayment.supplier_id == supplier_id)
    if purchase_id is not None:
        conditions.append(SupplierPayment.purchase_id == purchase_id)
    total = db.scalar(select(func.count()).select_from(SupplierPayment).where(*conditions)) or 0
    payments = db.scalars(
        select(SupplierPayment).where(*conditions)
        .order_by(SupplierPayment.paid_on.desc(), SupplierPayment.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [payment_out(db, p) for p in payments], "total": total, "page": page,
            "page_size": page_size}



def reverse_payment(db: Session, payment_id: int, reason: str, actor: User,
                    request: Request | None) -> SupplierPayment:
    """Undo a supplier payment recorded by mistake (owner). The amount is owed again."""
    payment = db.get(SupplierPayment, payment_id, with_for_update=True, populate_existing=True)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found.")
    if payment.reversed:
        return payment
    payment.reversed = True
    payment.reversed_at = now_utc()
    payment.reversed_by_id = actor.id
    payment.reversal_reason = reason
    db.flush()
    audit.record(db, action="supplier_payment.reversed", user=actor,
                 entity_type="supplier_payment", entity_id=payment.id,
                 details={"amount": str(payment.amount), "supplier_id": payment.supplier_id,
                          "reason": reason},
                 request=request)
    db.commit()
    db.refresh(payment)
    return payment
