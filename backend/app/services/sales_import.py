"""Import old sales from another app (e.g. Vyapar) — history only.

The file is an Excel workbook with three sheets:
  * "Sales": one row per sale — Date, invoice no., Customer, Phone, Discount, Sale total,
    Paid, Paid by, Still owed, Note.
  * "Sale items": one row per item — invoice no., Product name, Quantity, Price each.
  * "Products" (optional): Product name, Type (Accessory / Phone / Service), Selling price,
    Cost price each.

Each sale is recorded on its own date with its items, payments and anything still owed, so
reports by day/month and customer debts are right. Stock is NOT touched (the shop enters
today's stock separately), and missing products and customers are created. Like the product
import, it is checked first (preview) and then saved all at once, or not at all. Each old
invoice becomes receipt "VY-<number>", so the same file can't be imported twice.
"""
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, status
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.models import (
    Customer,
    CustomerPayment,
    PaymentMethod,
    Product,
    Sale,
    SaleLine,
    SaleStatus,
    TrackingType,
    User,
)
from app.schemas.catalog import ProductCreate
from app.schemas.customers import normalize_phone
from app.services.catalog import create_product
from app.services.customers import walk_in_customer

MAX_BYTES = 5 * 1024 * 1024
MAX_SALES = 5000
KOBO = Decimal("0.01")
COST = Decimal("0.0001")
PREFIX = "VY-"
WALK_IN_NAMES = {"", "unknown", "walk-in", "walk in", "walk-in customer", "cash", "customer"}
UNKNOWN_OWING = "Unknown (old records)"
TYPES = {"accessory": TrackingType.QUANTITY, "quantity": TrackingType.QUANTITY,
         "phone": TrackingType.SERIALIZED, "phone/laptop": TrackingType.SERIALIZED,
         "laptop": TrackingType.SERIALIZED, "serial/imei": TrackingType.SERIALIZED,
         "service": TrackingType.SERVICE}


def _key(text: str) -> str:
    return " ".join(str(text).split()).lower()


def _head(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _sheet(wb, *names):
    for ws in wb.worksheets:
        if _head(ws.title) in names:
            return ws
    return None


def _columns(ws, wanted: dict[str, tuple[str, ...]], sheet: str) -> tuple[int, dict[str, int]]:
    """Find the header row (within the first 10) and the column of each wanted field."""
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), start=1):
        found = {}
        for c, cell in enumerate(row):
            h = _head(cell)
            for key, starts in wanted.items():
                if key not in found and any(h.startswith(s) for s in starts):
                    found[key] = c
                    break
        if "invoice" in found or "name" in found:
            missing = [k for k in wanted if k not in found and not k.startswith("opt_")]
            if missing:
                raise HTTPException(422, f"The “{sheet}” sheet has no column for: "
                                         f"{', '.join(m.replace('_', ' ') for m in missing)}.")
            return r, found
    raise HTTPException(422, f"Couldn't find the column names on the “{sheet}” sheet.")


def _money(v: Any) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v)).quantize(KOBO, ROUND_HALF_UP)
    try:
        return Decimal(re.sub(r"[₦,\s]", "", str(v)).replace("NGN", "")).quantize(KOBO, ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"“{v}” is not an amount")


def _date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"“{s}” is not a date (use DD/MM/YYYY)")


def _invoice(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s if s and s.lower() != "total" else None


@dataclass
class Item:
    row: int
    name: str
    qty: int
    price: Decimal


@dataclass
class OldSale:
    row: int
    invoice: str
    day: date
    customer: str
    phone: str | None
    discount: Decimal
    total: Decimal
    paid: Decimal
    paid_by: str | None
    owed: Decimal
    note: str | None
    items: list[Item] = field(default_factory=list)


def _read(content: bytes) -> tuple[list[OldSale], dict[str, dict], list[dict]]:
    if len(content) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "That file is too big (5 MB at most).")
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception:
        raise HTTPException(422, "Couldn't open that file. Upload the Excel workbook (.xlsx) of old sales.")
    sales_ws = _sheet(wb, "sales")
    items_ws = _sheet(wb, "sale items", "items")
    if sales_ws is None or items_ws is None:
        raise HTTPException(422, "The workbook needs a “Sales” sheet and a “Sale items” sheet.")
    problems: list[dict] = []

    head, col = _columns(sales_ws, {
        "date": ("date",), "invoice": ("vyapar invoice", "invoice", "receipt"), "customer": ("customer", "party"),
        "opt_phone": ("phone",), "opt_discount": ("discount",), "total": ("sale total", "total amount"),
        "paid": ("paid ", "paid"), "opt_paid_by": ("paid by", "payment type", "payment"),
        "owed": ("still owed", "balance", "owed"), "opt_note": ("note", "description")}, "Sales")
    # "Paid by" also starts with "paid": make sure "paid" is the amount column.
    if "opt_paid_by" in col and col.get("paid") == col["opt_paid_by"]:
        raise HTTPException(422, "The “Sales” sheet needs separate “Paid” and “Paid by” columns.")
    sales: dict[str, OldSale] = {}
    for r, row in enumerate(sales_ws.iter_rows(min_row=head + 1, values_only=True), start=head + 1):
        get = lambda k: row[col[k]] if k in col and col[k] < len(row) else None  # noqa: E731
        inv = _invoice(get("invoice"))
        if inv is None or not any(v is not None for v in row):
            continue
        try:
            s = OldSale(row=r, invoice=inv, day=_date(get("date")),
                        customer=" ".join(str(get("customer") or "").split()),
                        phone=str(get("opt_phone")).strip() if get("opt_phone") else None,
                        discount=_money(get("opt_discount")) or Decimal("0.00"),
                        total=_money(get("total")), paid=_money(get("paid")) or Decimal("0.00"),
                        paid_by=(str(get("opt_paid_by")).strip() or None) if get("opt_paid_by") else None,
                        owed=_money(get("owed")) or Decimal("0.00"),
                        note=(str(get("opt_note")).strip() or None) if get("opt_note") else None)
        except ValueError as e:
            problems.append({"where": f"Sales row {r}", "message": str(e)})
            continue
        if s.total is None:
            problems.append({"where": f"Sales row {r}", "message": "Sale total is empty"})
            continue
        if inv in sales:
            problems.append({"where": f"Sales row {r}", "message": f"Invoice {inv} appears twice"})
            continue
        sales[inv] = s
    if len(sales) > MAX_SALES:
        raise HTTPException(422, f"Too many sales in one file ({len(sales)}); at most {MAX_SALES}.")

    head, col = _columns(items_ws, {"invoice": ("vyapar invoice", "invoice", "receipt"), "name": ("product", "item"),
                                    "qty": ("quantity", "qty"), "price": ("price",)}, "Sale items")
    for r, row in enumerate(items_ws.iter_rows(min_row=head + 1, values_only=True), start=head + 1):
        get = lambda k: row[col[k]] if col[k] < len(row) else None  # noqa: E731
        inv = _invoice(get("invoice"))
        if inv is None:
            continue
        where = f"Sale items row {r}"
        name = " ".join(str(get("name") or "").split())
        try:
            qty_d = _money(get("qty"))
            price = _money(get("price"))
        except ValueError as e:
            problems.append({"where": where, "message": str(e)})
            continue
        if not name:
            problems.append({"where": where, "message": "Product name is empty"})
        elif qty_d is None or qty_d <= 0 or qty_d != qty_d.to_integral_value():
            problems.append({"where": where, "message": f"Quantity must be a whole number above 0 ({name})"})
        elif price is None or price < 0:
            problems.append({"where": where, "message": f"Price is missing ({name})"})
        elif inv not in sales:
            problems.append({"where": where, "message": f"Invoice {inv} is not on the Sales sheet"})
        else:
            sales[inv].items.append(Item(r, name, int(qty_d), price))

    products: dict[str, dict] = {}
    prod_ws = _sheet(wb, "products")
    if prod_ws is not None:
        head, col = _columns(prod_ws, {"name": ("product name", "product", "item"), "opt_type": ("type", "counted by"),
                                       "opt_price": ("selling price", "price"), "opt_cost": ("cost",)}, "Products")
        for r, row in enumerate(prod_ws.iter_rows(min_row=head + 1, values_only=True), start=head + 1):
            get = lambda k: row[col[k]] if k in col and col[k] < len(row) else None  # noqa: E731
            name = " ".join(str(get("name") or "").split())
            if not name or name.lower() == "total":
                continue
            t = _key(get("opt_type") or "accessory")
            if t not in TYPES:
                problems.append({"where": f"Products row {r}",
                                 "message": f"Type “{get('opt_type')}” — use Accessory, Phone or Service ({name})"})
                continue
            try:
                products[_key(name)] = {"name": name, "type": TYPES[t], "price": _money(get("opt_price")),
                                        "cost": _money(get("opt_cost"))}
            except ValueError as e:
                problems.append({"where": f"Products row {r}", "message": f"{e} ({name})"})
    return list(sales.values()), products, problems


def _spread(discount: Decimal, amounts: list[Decimal]) -> list[Decimal]:
    """Share a sale-level discount over its lines in proportion (kobo-exact, never above a line)."""
    gross = sum(amounts)
    if not discount or not gross:
        return [Decimal("0.00")] * len(amounts)
    shares = [(discount * a / gross).quantize(KOBO, ROUND_HALF_UP) for a in amounts]
    shares[-1] += discount - sum(shares)
    return shares


def run_import(db: Session, content: bytes, actor: User, request: Request | None, commit: bool) -> dict:
    sales, sheet_products, problems = _read(content)

    # --- check each sale
    for s in sales:
        where = f"Invoice {s.invoice} (Sales row {s.row})"
        if not s.items:
            problems.append({"where": where, "message": "has no items on the Sale items sheet"})
            continue
        gross = sum(Decimal(i.qty) * i.price for i in s.items)
        if gross - s.discount != s.total:
            problems.append({"where": where, "message":
                             f"items add up to ₦{gross:,.2f}, less ₦{s.discount:,.2f} discount, but the sale "
                             f"total is ₦{s.total:,.2f}"})
        if s.paid + s.owed != s.total:
            problems.append({"where": where, "message":
                             f"paid ₦{s.paid:,.2f} + still owed ₦{s.owed:,.2f} isn't the total ₦{s.total:,.2f}"})
        if s.paid < 0 or s.owed < 0 or s.discount < 0:
            problems.append({"where": where, "message": "amounts can't be negative"})
    already = set(db.scalars(select(Sale.receipt_no).where(
        Sale.receipt_no.in_([PREFIX + s.invoice for s in sales]))))
    if already:
        nums = sorted((r.removeprefix(PREFIX) for r in already), key=lambda n: (len(n), n))
        problems.append({"where": "This file", "message":
                         f"{len(already)} of these sales were already imported (invoice {', '.join(nums[:10])}"
                         f"{'…' if len(nums) > 10 else ''}). Nothing is imported twice."})
    for s in sales:
        if len(PREFIX + s.invoice) > 20:
            problems.append({"where": f"Invoice {s.invoice}", "message": "invoice number is too long"})

    # --- payment methods by name
    methods = {_key(m.name): m for m in db.scalars(select(PaymentMethod).where(
        PaymentMethod.is_active.is_(True), PaymentMethod.is_trade_in.is_(False)))}
    aliases = {"bank": "bank transfer", "transfer": "bank transfer", "pos": "pos terminal"}
    for s in sales:
        if s.paid > 0:
            k = _key(s.paid_by or "")
            k = k if k in methods else aliases.get(k, k)
            if k not in methods:
                problems.append({"where": f"Invoice {s.invoice}", "message":
                                 f"paid by “{s.paid_by or '(empty)'}” — use one of your payment methods: "
                                 f"{', '.join(m.name for m in methods.values())}"})
            else:
                s.paid_by = methods[k].name

    # --- products: existing ones by name, the rest are created
    existing = {}
    for p in db.scalars(select(Product).where(Product.is_quick_sale.is_(False)).order_by(Product.is_active.desc(), Product.id)):
        existing.setdefault(_key(p.name), p)
    names: dict[str, str] = {}
    last_price: dict[str, tuple[date, Decimal]] = {}
    for s in sales:
        for i in s.items:
            k = _key(i.name)
            names.setdefault(k, i.name)
            if k not in last_price or s.day >= last_price[k][0]:
                last_price[k] = (s.day, i.price)
    new_products = sorted((names[k] for k in names if k not in existing), key=str.lower)
    costs_missing = sorted(names[k] for k in names
                           if not (sheet_products.get(k, {}).get("cost") or (k in existing and existing[k].avg_cost)))

    # --- customers
    def customer_key(s: OldSale) -> str:
        if _key(s.customer) in WALK_IN_NAMES:
            return "" if s.owed == 0 else _key(UNKNOWN_OWING)
        return _key(s.customer)
    known = {_key(c.name): c for c in db.scalars(select(Customer).where(Customer.is_walk_in.is_(False))
                                                 .order_by(Customer.is_active.desc(), Customer.id))}
    by_phone = {c.phone: c for c in known.values() if c.phone}
    new_customers = sorted({(UNKNOWN_OWING if customer_key(s) == _key(UNKNOWN_OWING) else s.customer)
                            for s in sales if customer_key(s) and customer_key(s) not in known
                            and not (normalize_phone(s.phone) in by_phone if s.phone else False)}, key=str.lower)

    owing = defaultdict(Decimal)
    for s in sales:
        if s.owed > 0:
            owing[UNKNOWN_OWING if customer_key(s) == _key(UNKNOWN_OWING) else s.customer] += s.owed
    days = [s.day for s in sales]
    summary = {
        "sales": len(sales), "items": sum(i.qty for s in sales for i in s.items),
        "date_from": min(days).isoformat() if days else None, "date_to": max(days).isoformat() if days else None,
        "total": str(sum((s.total for s in sales), Decimal("0.00"))),
        "paid": str(sum((s.paid for s in sales), Decimal("0.00"))),
        "owed": str(sum((s.owed for s in sales), Decimal("0.00"))),
        "products_used": len(names), "new_products": new_products,
        "products_without_cost": len(costs_missing),
        "new_customers": new_customers,
        "customers_owing": [{"name": n, "owed": str(v)} for n, v in sorted(owing.items(), key=lambda kv: -kv[1])],
    }
    result = {"saved": False, "summary": summary, "problems": problems}
    if problems or not commit or not sales:
        return result

    # --- save everything in one transaction
    tz = ZoneInfo(get_settings().business_timezone)
    walk_in = walk_in_customer(db)
    product_of: dict[str, Product] = dict(existing)
    for k in names:
        if k in product_of:
            continue
        info = sheet_products.get(k, {})
        tracking = info.get("type") or TrackingType.QUANTITY
        price = info.get("price") if info.get("price") is not None else last_price[k][1]
        product_of[k] = create_product(db, ProductCreate(
            name=names[k], tracking_type=tracking, selling_price=price,
            service_cost=info.get("cost") if tracking == TrackingType.SERVICE else None),
            actor, request, commit=False)

    customer_of: dict[str, Customer] = {}

    def customer_for(s: OldSale) -> Customer:
        k = customer_key(s)
        if not k:
            return walk_in
        phone = normalize_phone(s.phone) if s.phone else None
        if k in customer_of:
            return customer_of[k]
        c = known.get(k) or (by_phone.get(phone) if phone else None)
        if c is None:
            c = Customer(name=UNKNOWN_OWING if k == _key(UNKNOWN_OWING) else s.customer[:120],
                         phone=phone, is_walk_in=False, is_active=True, created_by_id=actor.id,
                         notes="Added from imported old sales")
            db.add(c)
            db.flush()
            if phone:
                by_phone[phone] = c
        customer_of[k] = c
        return c

    for s in sorted(sales, key=lambda s: (s.day, s.invoice)):
        customer = customer_for(s)
        amounts = [Decimal(i.qty) * i.price for i in s.items]
        note = f"Imported from old records (invoice {s.invoice})" + (f". {s.note}" if s.note else "")
        sale = Sale(receipt_no=PREFIX + s.invoice, customer_id=customer.id, sold_by_id=actor.id,
                    sold_at=datetime.combine(s.day, time(12, 0), tzinfo=tz), business_date=s.day,
                    status=SaleStatus.COMPLETED, subtotal=sum(amounts), discount_total=s.discount,
                    total=s.total, amount_paid_at_sale=s.paid, due_date=None, notes=note[:500],
                    idempotency_key=f"import-{PREFIX}{s.invoice}"[:64], imported=True)
        db.add(sale)
        db.flush()
        for item, amount, disc in zip(s.items, amounts, _spread(s.discount, amounts)):
            k = _key(item.name)
            product = product_of[k]
            cost = sheet_products.get(k, {}).get("cost")
            if cost is None:
                cost = product.avg_cost or Decimal("0")
            db.add(SaleLine(sale_id=sale.id, product_id=product.id, unit_id=None, quantity=item.qty,
                            unit_price=item.price, discount=disc, line_total=amount - disc,
                            unit_cost_snapshot=Decimal(cost).quantize(COST),
                            cost_total_snapshot=(Decimal(item.qty) * Decimal(cost)).quantize(COST)))
        if s.paid > 0:
            db.add(CustomerPayment(customer_id=customer.id, sale_id=sale.id, amount=s.paid,
                                   method_id=methods[_key(s.paid_by)].id, business_date=s.day,
                                   recorded_by_id=actor.id))
    db.flush()
    audit.record(db, action="sales.imported", user=actor, entity_type="sale", entity_id=None,
                 details={"sales": len(sales), "total": summary["total"], "owed": summary["owed"],
                          "date_from": summary["date_from"], "date_to": summary["date_to"],
                          "new_products": len(new_products), "new_customers": len(new_customers)},
                 request=request)
    db.commit()
    result["saved"] = True
    return result


def imported_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Sale).where(Sale.imported.is_(True))) or 0
