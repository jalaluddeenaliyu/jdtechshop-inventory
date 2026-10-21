"""Point of sale.

A sale is one all-or-nothing transaction: lines, stock movements, unit status and payments
are saved together or not at all. Rules enforced here (and backed by the database):

- Prices come from the product, never from the till. Discounts are per line, and sales staff
  cannot exceed the owner's limit (Settings → staff discount %).
- Product rows are locked in id order, then unit rows: two cashiers can never sell the same
  phone or the last charger twice, and two sales never deadlock.
- Each line freezes its cost at the moment of sale (the unit's own cost for phones/laptops,
  the weighted average for accessories), so later cost changes never alter past profit.
- Payments never exceed the total. Paying less makes a credit sale, allowed only for
  registered customers (never walk-ins), and for staff only when Settings allow it.
- One idempotency key per cart: submitting the same cart twice records one sale.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.config import get_settings
from app.core.clock import business_today
from app.models import (
    BusinessSettings,
    Customer,
    CustomerPayment,
    MovementType,
    PaymentAllocation,
    PaymentMethod,
    Product,
    ProductUnit,
    Sale,
    SaleLine,
    SaleReturn,
    SaleStatus,
    TrackingType,
    UnitStatus,
    User,
)
from app.schemas.sales import (
    PosProduct,
    PosResult,
    PosUnit,
    SaleIn,
    SaleLineOut,
    SaleOut,
    SalePaymentOut,
    SaleReturnSummary,
)
from app.services import stock as stock_service
from app.services.catalog import like_pattern, list_products
from app.services.customers import walk_in_customer
from app.services.debts import SaleMoney, sale_balance_expr, sale_money

KOBO = Decimal("0.01")
COST = Decimal("0.0001")
ZERO = Decimal("0.00")

UNIT_STATUS_WORDS = {
    UnitStatus.SOLD: "already sold",
    UnitStatus.RESERVED: "reserved",
    UnitStatus.RETURNED_TO_SUPPLIER: "returned to the supplier",
    UnitStatus.DAMAGED: "marked damaged",
    UnitStatus.MISSING: "marked missing",
}


def _naira(amount: Decimal) -> str:
    return f"₦{amount:,.2f}"


def _unit_label(u: ProductUnit) -> str:
    return f"IMEI {u.imei1}" if u.imei1 else f"serial {u.serial_number}"


def next_receipt_no(db: Session, day: date | None = None) -> tuple[str, date]:
    """JDT-YYMMDD-NNNN, numbered per shop day. The counter row is locked until commit, and a
    failed sale rolls its number back, so receipts have no gaps and no duplicates.
    A sale entered for an earlier day gets the next number of that day."""
    today = day or business_today()
    n = db.scalar(text(
        "INSERT INTO receipt_counters (day, last_number) VALUES (:d, 1) "
        "ON CONFLICT (day) DO UPDATE SET last_number = receipt_counters.last_number + 1 "
        "RETURNING last_number"
    ), {"d": today})
    return f"JDT-{today:%y%m%d}-{n:04d}", today


EARLIEST_SALE_DATE = date(2000, 1, 1)


def _check_sale_date(sale_date: date | None, actor: User) -> date:
    """The shop day the sale belongs to. Only the owner may enter a sale for an earlier day."""
    today = business_today()
    if sale_date is None or sale_date == today:
        return today
    if sale_date > today:
        raise HTTPException(422, "The sale date cannot be in the future.")
    if sale_date < EARLIEST_SALE_DATE:
        raise HTTPException(422, "That sale date is too far in the past.")
    if not actor.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the owner can record a sale for an earlier date.")
    return sale_date


def _on_day(day: date) -> datetime:
    """The same clock time as now, on an earlier shop day (Africa/Lagos)."""
    tz = ZoneInfo(get_settings().business_timezone)
    return datetime.combine(day, datetime.now(tz).time().replace(microsecond=0), tzinfo=tz)


def _replay(db: Session, key: str) -> Sale | None:
    return db.scalar(select(Sale).where(Sale.idempotency_key == key))


def create_sale(db: Session, data: SaleIn, actor: User,
                request: Request | None) -> tuple[Sale, bool]:
    """Returns (sale, created). The same cart sent twice returns the first sale."""
    existing = _replay(db, data.idempotency_key)
    if existing:
        return existing, False

    settings = db.get(BusinessSettings, 1)
    if data.customer_id is None:
        customer = walk_in_customer(db)
    else:
        customer = db.get(Customer, data.customer_id)
        if customer is None or not customer.is_active:
            raise HTTPException(422, "That customer does not exist or is turned off.")

    unit_ids = [line.unit_id for line in data.lines if line.unit_id is not None]
    if len(unit_ids) != len(set(unit_ids)):
        raise HTTPException(422, "The same phone/laptop is in the cart twice.")

    # Lock products (id order), then units (id order): the same order in every sale.
    products: dict[int, Product] = {}
    for pid in sorted({line.product_id for line in data.lines}):
        product = db.get(Product, pid, with_for_update=True, populate_existing=True)
        if product is None:
            raise HTTPException(422, "A product in the cart no longer exists.")
        products[pid] = product
    units: dict[int, ProductUnit] = {
        u.id: u for u in db.scalars(
            select(ProductUnit).where(ProductUnit.id.in_(unit_ids))
            .order_by(ProductUnit.id).with_for_update().execution_options(populate_existing=True)
        )
    } if unit_ids else {}

    limit_pct = settings.staff_max_discount_percent
    priced = []  # (line_in, product, unit, unit_price, gross)
    for n, line in enumerate(data.lines, 1):
        product = products[line.product_id]
        if not product.is_active:
            raise HTTPException(422, f"Line {n}: “{product.name}” is turned off and cannot be sold.")
        unit = None
        if product.tracking_type == TrackingType.SERIALIZED:
            if line.unit_id is None:
                raise HTTPException(422, f"Line {n}: choose which {product.name} you are selling "
                                         "(scan or pick its IMEI/serial).")
            if line.quantity != 1:
                raise HTTPException(422, f"Line {n}: each {product.name} is sold as its own line.")
            unit = units.get(line.unit_id)
            if unit is None or unit.product_id != product.id:
                raise HTTPException(422, f"Line {n}: that unit is not a {product.name}.")
            if unit.status != UnitStatus.IN_STOCK:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Line {n}: this {product.name} ({_unit_label(unit)}) is "
                    f"{UNIT_STATUS_WORDS.get(unit.status, unit.status)}. Remove it from the cart.",
                )
        elif line.unit_id is not None:
            raise HTTPException(422, f"Line {n}: {product.name} is counted by quantity, "
                                     "so no unit is picked.")

        price = product.selling_price
        if product.tracking_type == TrackingType.SERVICE and price == 0:
            # "Price set at the till": the cashier types what this job costs.
            if line.unit_price is None:
                raise HTTPException(422, f"Line {n}: enter the price for {product.name}.")
            price = line.unit_price
        elif line.unit_price is not None and line.unit_price != price:
            raise HTTPException(422, f"Line {n}: the price of {product.name} is fixed at "
                                     f"{_naira(price)}. Use a discount to reduce it.")
        gross = (Decimal(line.quantity) * price).quantize(KOBO)
        if line.discount > gross:
            raise HTTPException(422, f"Line {n}: the discount is more than the price "
                                     f"({_naira(gross)}).")
        if not actor.is_owner and line.discount > 0:
            allowed = (gross * limit_pct / 100).quantize(KOBO, ROUND_HALF_UP)
            if line.discount > allowed:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Line {n}: a discount of {_naira(line.discount)} on {product.name} is more "
                    f"than sales staff may give ({limit_pct.normalize()}% = {_naira(allowed)}). "
                    "Ask the owner to make this sale.",
                )
        priced.append((line, product, unit, price, gross))

    # Enough stock of each quantity item (the ledger checks again when it moves the stock).
    wanted: dict[int, int] = {}
    for line, product, unit, *_ in priced:
        if unit is None and product.tracking_type != TrackingType.SERVICE:
            wanted[product.id] = wanted.get(product.id, 0) + line.quantity
    for pid, n in wanted.items():
        product = products[pid]
        if n > product.qty_on_hand:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Not enough stock of {product.name}: {product.qty_on_hand} "
                                f"available, {n} needed.")

    subtotal = sum((gross for *_, gross in priced), ZERO)
    discount_total = sum((line.discount for line, *_ in priced), ZERO)
    total = subtotal - discount_total

    methods: dict[int, PaymentMethod] = {}
    for p in data.payments:
        method = methods.get(p.method_id) or db.get(PaymentMethod, p.method_id)
        if method is None or not method.is_active:
            raise HTTPException(422, "Choose an active payment method for each payment.")
        methods[method.id] = method
    paid = sum((p.amount for p in data.payments), ZERO)
    if paid > total:
        raise HTTPException(
            422,
            f"Payments add up to {_naira(paid)}, more than the total of {_naira(total)}. Enter only "
            "what goes towards this sale (give change from the cash).",
        )
    sale_day = _check_sale_date(data.sale_date, actor)
    if data.due_date is not None and data.due_date < sale_day:
        raise HTTPException(422, "The due date cannot be before the sale date."
                            if sale_day < business_today() else "The due date cannot be in the past.")
    if paid < total:
        if customer.is_walk_in:
            raise HTTPException(
                422,
                f"{_naira(total - paid)} is unpaid. Walk-in customers must pay in full — choose or "
                "add the customer to sell on credit.",
            )
        if not actor.is_owner and not settings.allow_staff_credit_sales:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Sales staff cannot sell on credit (Settings). Ask the owner.")

    backdated = sale_day < business_today()
    receipt_no, today = next_receipt_no(db, sale_day)
    sale = Sale(
        receipt_no=receipt_no, customer_id=customer.id, sold_by_id=actor.id, business_date=today,
        **({"sold_at": _on_day(sale_day)} if backdated else {}),
        status=SaleStatus.COMPLETED, subtotal=subtotal, discount_total=discount_total,
        total=total, amount_paid_at_sale=paid, due_date=data.due_date if paid < total else None,
        notes=data.notes, idempotency_key=data.idempotency_key,
    )
    db.add(sale)
    try:
        db.flush()
        for line, product, unit, price, gross in priced:
            unit_cost = unit.unit_cost if unit else product.avg_cost
            sale_line = SaleLine(
                sale_id=sale.id, product_id=product.id, unit_id=unit.id if unit else None,
                quantity=line.quantity, unit_price=price, discount=line.discount,
                line_total=gross - line.discount, unit_cost_snapshot=unit_cost,
                cost_total_snapshot=(Decimal(line.quantity) * unit_cost).quantize(COST),
            )
            db.add(sale_line)
            db.flush()
            if product.tracking_type != TrackingType.SERVICE:  # services have no stock
                stock_service.record_movement(
                    db, product, quantity_change=-line.quantity, movement_type=MovementType.SALE,
                    user=actor, unit_cost=unit_cost, unit_id=unit.id if unit else None,
                    reference_type="sale", reference_id=sale.id,
                )
            if unit:
                unit.status = UnitStatus.SOLD
                unit.sale_line_id = sale_line.id
        for p in data.payments:
            db.add(CustomerPayment(
                customer_id=customer.id, sale_id=sale.id, amount=p.amount, method_id=p.method_id,
                business_date=today, reference=p.reference, recorded_by_id=actor.id,
            ))
        db.flush()
        audit.record(db, action="sale.completed", user=actor, entity_type="sale",
                     entity_id=sale.id,
                     details={"receipt_no": receipt_no, "total": str(total), "paid": str(paid),
                              "customer": customer.name,
                              "items": sum(line.quantity for line, *_ in priced),
                              "discount": str(discount_total),
                              **({"sale_date": sale_day.isoformat(),
                                  "entered_late": True} if backdated else {})},
                     request=request)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _replay(db, data.idempotency_key)  # same cart sent twice at the same moment
        if existing:
            return existing, False
        raise
    db.refresh(sale)
    return sale, True


# ---------- Reading sales ----------

def sale_out(db: Session, sale: Sale, show_cost: bool, detail: bool = False,
             money: SaleMoney | None = None) -> SaleOut:
    if money is None:
        money = sale_money(db, [sale.id])[sale.id]
    seller = db.scalar(select(User.full_name).where(User.id == sale.sold_by_id))
    cost = sum((line.cost_total_snapshot for line in sale.lines), Decimal("0"))
    sale_returns = list(db.scalars(select(SaleReturn).where(SaleReturn.sale_id == sale.id)
                                   .order_by(SaleReturn.id)))
    returned_cost = sum((rl.cost_total for r in sale_returns for rl in r.lines), Decimal("0"))
    lines = payments = returns = None
    if detail:
        units = {u.id: u for u in db.scalars(select(ProductUnit).where(
            ProductUnit.id.in_([line.unit_id for line in sale.lines if line.unit_id])))}
        returned_qty = {}
        for r in sale_returns:
            for rl in r.lines:
                returned_qty[rl.sale_line_id] = returned_qty.get(rl.sale_line_id, 0) + rl.quantity
        lines = []
        for line in sale.lines:
            u = units.get(line.unit_id) if line.unit_id else None
            lines.append(SaleLineOut(
                id=line.id, product_id=line.product_id, product_name=line.product.name,
                product_sku=line.product.sku, tracking_type=line.product.tracking_type,
                unit_id=line.unit_id, serial_number=u.serial_number if u else None,
                imei1=u.imei1 if u else None, imei2=u.imei2 if u else None,
                condition=u.condition if u else line.product.condition,
                quantity=line.quantity, unit_price=line.unit_price, discount=line.discount,
                line_total=line.line_total,
                unit_cost_snapshot=line.unit_cost_snapshot if show_cost else None,
                cost_total_snapshot=line.cost_total_snapshot if show_cost else None,
                returned_quantity=returned_qty.get(line.id, 0),
            ))
        # Payments taken at the till, then the parts of later payments that paid this sale.
        at_till = db.execute(
            select(CustomerPayment, PaymentMethod.name, CustomerPayment.amount)
            .join(PaymentMethod, PaymentMethod.id == CustomerPayment.method_id)
            .where(CustomerPayment.sale_id == sale.id, CustomerPayment.reversed.is_(False))
        ).all()
        later = db.execute(
            select(CustomerPayment, PaymentMethod.name, PaymentAllocation.amount)
            .join(PaymentAllocation, PaymentAllocation.payment_id == CustomerPayment.id)
            .join(PaymentMethod, PaymentMethod.id == CustomerPayment.method_id)
            .where(PaymentAllocation.sale_id == sale.id, CustomerPayment.reversed.is_(False))
        ).all()
        payments = [SalePaymentOut(id=p.id, method_name=name, amount=amount,
                                   reference=p.reference, received_at=p.received_at)
                    for p, name, amount in sorted([*at_till, *later], key=lambda r: r[0].id)]
        recorders = dict(db.execute(select(User.id, User.full_name).where(
            User.id.in_([r.recorded_by_id for r in sale_returns]))).all())
        returns = [SaleReturnSummary(
            id=r.id, returned_at=r.returned_at, reason=r.reason, value_total=r.value_total,
            refund_amount=r.refund_amount, recorded_by_name=recorders.get(r.recorded_by_id),
            items=[(rl.sale_line_id, rl.quantity, rl.restocked) for rl in r.lines],
        ) for r in sale_returns]
    return SaleOut(
        id=sale.id, receipt_no=sale.receipt_no, customer_id=sale.customer_id,
        customer_name=sale.customer.name, customer_phone=sale.customer.phone,
        is_walk_in=sale.customer.is_walk_in, sold_by_name=seller or "—", sold_at=sale.sold_at,
        business_date=sale.business_date, status=sale.status, subtotal=sale.subtotal,
        discount_total=sale.discount_total, total=sale.total, amount_paid=money.paid,
        returned_total=money.returned, balance_due=money.balance, due_date=sale.due_date,
        notes=sale.notes, reversed_at=sale.reversed_at, reversal_reason=sale.reversal_reason,
        reversed_by_name=db.scalar(select(User.full_name).where(User.id == sale.reversed_by_id))
        if sale.reversed_by_id else None,
        reversal_refund=sale.reversal_refund, returns=returns,
        item_count=sum(line.quantity for line in sale.lines),
        profit=(Decimal("0.00") if sale.status == SaleStatus.REVERSED
                else (sale.total - money.returned - cost + returned_cost).quantize(KOBO))
        if show_cost else None,
        lines=lines, payments=payments,
    )


def get_sale(db: Session, sale_id: int) -> Sale:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found.")
    return sale


def list_sales(db: Session, *, date_from: date | None, date_to: date | None,
               customer_id: int | None,
               sold_by_id: int | None, q: str | None, unpaid_only: bool, page: int,
               page_size: int, show_cost: bool) -> dict:
    conditions = []
    if date_from:
        conditions.append(Sale.business_date >= date_from)
    if date_to:
        conditions.append(Sale.business_date <= date_to)
    if customer_id is not None:
        conditions.append(Sale.customer_id == customer_id)
    if sold_by_id is not None:
        conditions.append(Sale.sold_by_id == sold_by_id)
    if q and q.strip():
        pattern = like_pattern(q.strip())
        conditions.append(or_(Sale.receipt_no.ilike(pattern, escape="\\"),
                              Sale.customer_id.in_(select(Customer.id).where(
                                  Customer.name.ilike(pattern, escape="\\")))))
    if unpaid_only:
        conditions.append(sale_balance_expr() > 0)
    total = db.scalar(select(func.count()).select_from(Sale).where(*conditions)) or 0
    sales = list(db.scalars(
        select(Sale).where(*conditions).order_by(Sale.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    money = sale_money(db, [s.id for s in sales])
    return {"items": [sale_out(db, s, show_cost, money=money[s.id]) for s in sales],
            "total": total, "page": page, "page_size": page_size}


# ---------- Till search ----------

def pos_search(db: Session, q: str, limit: int = 12) -> list[PosResult]:
    """What the cashier typed or scanned. A serial/IMEI finds that exact phone (in stock);
    anything else finds products by name, SKU, barcode or model."""
    q = q.strip()
    if not q:
        return []
    results: list[PosResult] = []
    compact = q.replace(" ", "").replace("-", "")
    unit_q = (select(ProductUnit).join(Product, Product.id == ProductUnit.product_id)
              .where(ProductUnit.status == UnitStatus.IN_STOCK, Product.is_active.is_(True)))
    exact = db.scalars(unit_q.where(or_(
        ProductUnit.imei1 == compact, ProductUnit.imei2 == compact,
        ProductUnit.serial_number == compact.upper(),
    ))).all()
    partial = [] if exact or len(compact) < 5 else db.scalars(unit_q.where(or_(
        *(col.ilike(like_pattern(compact), escape="\\") for col in
          (ProductUnit.imei1, ProductUnit.imei2, ProductUnit.serial_number))
    )).order_by(ProductUnit.id).limit(5)).all()
    for u in [*exact, *partial]:
        results.append(PosResult(kind="unit", product=_pos_product(u.product), unit=PosUnit(
            id=u.id, serial_number=u.serial_number, imei1=u.imei1, imei2=u.imei2,
            condition=u.condition)))
    if exact:
        return results  # a scanned phone: nothing else to choose
    products, _, _ = list_products(db, q=q, page_size=limit)
    # An exact SKU or barcode match goes first (scanned barcode → Enter adds it).
    key = q.upper()
    products.sort(key=lambda p: 0 if key in (p.sku, p.barcode) else 1)
    results += [PosResult(kind="product", product=_pos_product(p)) for p in products]
    return results[:limit]


def _pos_product(p: Product) -> PosProduct:
    return PosProduct(id=p.id, name=p.name, sku=p.sku, barcode=p.barcode,
                      tracking_type=p.tracking_type, condition=p.condition,
                      selling_price=p.selling_price, qty_on_hand=p.qty_on_hand)
