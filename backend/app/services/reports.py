"""Dashboard figures and reports — every number is a query over the records, never stored.

Periods are shop days (Africa/Lagos), inclusive. Definitions (docs/ARCHITECTURE.md, rules 10–11):

- Gross sales   = totals of completed sales in the period (line discounts already taken off).
                  Reversed sales are left out entirely.
- Returns       = value of items customers brought back in the period.
- Net sales     = gross sales − returns.
- Cost of goods = cost frozen on the period's sale lines − cost of the period's returned items.
- Gross profit  = net sales − cost of goods.
- Stock losses  = damaged / missing stock written off in the period, at its cost.
- Net profit    = gross profit − expenses − stock losses. Labelled "before expenses" when no
                  expense was recorded for the period.
- Cash is not revenue: payments received and refunds are reported separately, by method.
"""
import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.clock import business_today
from app.models import (
    AdjustmentStatus,
    AuditLog,
    BusinessSettings,
    Category,
    Customer,
    CustomerPayment,
    Expense,
    MovementType,
    PaymentMethod,
    Product,
    ProductUnit,
    Purchase,
    PurchaseStatus,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    StockAdjustment,
    StockMovement,
    Supplier,
    SupplierPayment,
    SupplierReturn,
    TrackingType,
    User,
)
from app.models.units import ON_HAND_STATUSES
from app.schemas.reports import (
    Activity,
    Column,
    Dashboard,
    DayPoint,
    MethodAmount,
    PeriodFigures,
    Report,
    StockAlert,
    TopProduct,
)
from app.services import quick_sales
from app.services.debts import customer_balances, sale_balance_expr
from app.services.purchasing import purchase_credit_expr, supplier_balances

KOBO = Decimal("0.01")
ZERO = Decimal("0.00")
LOSS_TYPES = (MovementType.DAMAGE, MovementType.MISSING)


def _d(value) -> Decimal:
    return Decimal(value or 0).quantize(KOBO, ROUND_HALF_UP)


def shop_day(column):
    """SQL: the shop day (Africa/Lagos) of a timestamp column."""
    return cast(func.timezone(get_settings().business_timezone, column), Date)


def _period_text(d1: date, d2: date) -> str:
    fmt = "%d %b %Y"
    return d1.strftime(fmt) if d1 == d2 else f"{d1.strftime(fmt)} – {d2.strftime(fmt)}"


# ---------- One period ----------

def period_figures(db: Session, d1: date, d2: date, show_cost: bool,
                   seller_id: int | None = None) -> PeriodFigures:
    sale_in = and_(Sale.status == SaleStatus.COMPLETED, Sale.business_date.between(d1, d2))
    if seller_id is not None:
        sale_in = and_(sale_in, Sale.sold_by_id == seller_id)
    count, gross, discounts, credit = db.execute(select(
        func.count(Sale.id), func.coalesce(func.sum(Sale.total), 0),
        func.coalesce(func.sum(Sale.discount_total), 0),
        func.coalesce(func.sum(Sale.total - Sale.amount_paid_at_sale), 0),
    ).where(sale_in)).one()
    items, cost = db.execute(select(
        func.coalesce(func.sum(SaleLine.quantity), 0),
        func.coalesce(func.sum(SaleLine.cost_total_snapshot), 0),
    ).join(Sale, Sale.id == SaleLine.sale_id).where(sale_in)).one()

    ret_in = SaleReturn.business_date.between(d1, d2)
    if seller_id is not None:
        ret_in = and_(ret_in, SaleReturn.sale_id.in_(select(Sale.id).where(Sale.sold_by_id == seller_id)))
    returns, refunds = db.execute(select(
        func.coalesce(func.sum(SaleReturn.value_total), 0),
        func.coalesce(func.sum(SaleReturn.refund_amount), 0),
    ).where(ret_in)).one()
    returned_qty, returned_cost = db.execute(select(
        func.coalesce(func.sum(SaleReturnLine.quantity), 0),
        func.coalesce(func.sum(SaleReturnLine.cost_total), 0),
    ).join(SaleReturn, SaleReturn.id == SaleReturnLine.return_id).where(ret_in)).one()

    net = _d(gross) - _d(returns)
    figures = PeriodFigures(
        date_from=d1, date_to=d2, sales_count=count, items_sold=int(items) - int(returned_qty),
        gross_sales=_d(gross), discounts=_d(discounts), returns=_d(returns), net_sales=net,
        average_sale=_d(Decimal(gross) / count) if count else ZERO, credit_given=_d(credit),
    )
    if not show_cost:
        return figures

    cogs = _d(Decimal(cost) - Decimal(returned_cost))
    gross_profit = net - cogs
    expense_total, expense_count = db.execute(select(
        func.coalesce(func.sum(Expense.amount), 0), func.count(Expense.id),
    ).where(Expense.spent_on.between(d1, d2), Expense.reversed.is_(False))).one()
    losses = db.scalar(select(func.coalesce(func.sum(
        -StockMovement.quantity_change * func.coalesce(StockMovement.unit_cost, 0)), 0))
        .where(StockMovement.movement_type.in_(LOSS_TYPES),
               shop_day(StockMovement.created_at).between(d1, d2)))
    reversal_refunds = db.scalar(select(func.coalesce(func.sum(Sale.reversal_refund), 0)).where(
        Sale.status == SaleStatus.REVERSED, shop_day(Sale.reversed_at).between(d1, d2)))

    received = db.execute(
        select(PaymentMethod.name, func.sum(CustomerPayment.amount))
        .join(PaymentMethod, PaymentMethod.id == CustomerPayment.method_id)
        .where(CustomerPayment.business_date.between(d1, d2), CustomerPayment.reversed.is_(False))
        .group_by(PaymentMethod.name).order_by(PaymentMethod.name)
    ).all()
    figures.cost_of_goods = cogs
    figures.gross_profit = gross_profit
    figures.gross_margin = (gross_profit / net * 100).quantize(Decimal("0.1")) if net else None
    figures.expenses = _d(expense_total)
    figures.expenses_recorded = expense_count > 0
    figures.stock_losses = _d(losses)
    figures.net_profit = gross_profit - _d(expense_total) - _d(losses)
    figures.cash_received = _d(sum((amt for _, amt in received), Decimal(0)))
    figures.refunds = _d(Decimal(refunds) + Decimal(reversal_refunds))
    figures.received_by_method = [MethodAmount(method=m, amount=_d(a)) for m, a in received]
    return figures


def _by_day(db: Session, d1: date, d2: date, show_cost: bool) -> list[DayPoint]:
    sale_in = and_(Sale.status == SaleStatus.COMPLETED, Sale.business_date.between(d1, d2))
    sales = dict(db.execute(select(Sale.business_date, func.sum(Sale.total)).where(sale_in)
                            .group_by(Sale.business_date)).all())
    cost = dict(db.execute(select(Sale.business_date, func.sum(SaleLine.cost_total_snapshot))
                           .join(Sale, Sale.id == SaleLine.sale_id).where(sale_in)
                           .group_by(Sale.business_date)).all())
    returns = dict(db.execute(select(SaleReturn.business_date, func.sum(SaleReturn.value_total))
                              .where(SaleReturn.business_date.between(d1, d2))
                              .group_by(SaleReturn.business_date)).all())
    returned_cost = dict(db.execute(
        select(SaleReturn.business_date, func.sum(SaleReturnLine.cost_total))
        .join(SaleReturn, SaleReturn.id == SaleReturnLine.return_id)
        .where(SaleReturn.business_date.between(d1, d2)).group_by(SaleReturn.business_date)).all())
    out, day = [], d1
    while day <= d2:
        net = _d(sales.get(day)) - _d(returns.get(day))
        profit = net - _d(Decimal(cost.get(day) or 0) - Decimal(returned_cost.get(day) or 0))
        out.append(DayPoint(day=day, net_sales=net, gross_profit=profit if show_cost else None))
        day += timedelta(days=1)
    return out


def _product_sales(db: Session, d1: date, d2: date) -> dict[int, dict]:
    """{product_id: {qty, net_sales, cost}} for the period, net of returns."""
    out: dict[int, dict] = defaultdict(lambda: {"qty": 0, "net_sales": ZERO, "cost": Decimal(0)})
    for pid, qty, revenue, cost in db.execute(
        select(SaleLine.product_id, func.sum(SaleLine.quantity), func.sum(SaleLine.line_total),
               func.sum(SaleLine.cost_total_snapshot))
        .join(Sale, Sale.id == SaleLine.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.business_date.between(d1, d2))
        .group_by(SaleLine.product_id)
    ):
        out[pid]["qty"] += int(qty)
        out[pid]["net_sales"] += _d(revenue)
        out[pid]["cost"] += Decimal(cost)
    for pid, qty, value, cost in db.execute(
        select(SaleLine.product_id, func.sum(SaleReturnLine.quantity), func.sum(SaleReturnLine.value),
               func.sum(SaleReturnLine.cost_total))
        .join(SaleLine, SaleLine.id == SaleReturnLine.sale_line_id)
        .join(SaleReturn, SaleReturn.id == SaleReturnLine.return_id)
        .where(SaleReturn.business_date.between(d1, d2))
        .group_by(SaleLine.product_id)
    ):
        out[pid]["qty"] -= int(qty)
        out[pid]["net_sales"] -= _d(value)
        out[pid]["cost"] -= Decimal(cost)
    return out


def _stock_rows(db: Session) -> list[dict]:
    """Every active product with its stock now, valued at cost and at selling price."""
    default_reorder = db.scalar(select(BusinessSettings.default_reorder_level)) or 0
    unit_value = dict(db.execute(
        select(ProductUnit.product_id, func.sum(ProductUnit.unit_cost))
        .where(ProductUnit.status.in_(ON_HAND_STATUSES)).group_by(ProductUnit.product_id)).all())
    cats = dict(db.execute(select(Category.id, Category.name)).all())
    rows = []
    for p in db.scalars(select(Product).where(Product.is_active.is_(True),
                                              Product.tracking_type != TrackingType.SERVICE)
                        .order_by(func.lower(Product.name))):
        level = p.reorder_level if p.reorder_level is not None else default_reorder
        if p.tracking_type == TrackingType.SERIALIZED:
            value = _d(unit_value.get(p.id))
        else:
            value = _d(Decimal(p.qty_on_hand) * p.avg_cost)
        rows.append({
            "product_id": p.id, "sku": p.sku, "product": p.name,
            "category": cats.get(p.category_id, "—"),
            "tracking": "Serial / IMEI" if p.tracking_type == TrackingType.SERIALIZED else "Quantity",
            "qty_on_hand": p.qty_on_hand, "reorder_level": level,
            "status": "Out of stock" if p.qty_on_hand == 0 else "Low" if p.qty_on_hand <= level else "OK",
            "avg_cost": _d(value / p.qty_on_hand) if p.qty_on_hand else ZERO,
            "stock_value": value, "selling_price": p.selling_price,
            "retail_value": _d(Decimal(p.qty_on_hand) * p.selling_price),
        })
    return rows


# ---------- Dashboard ----------

ACTION_WORDS = {
    "sale.completed": "Sale", "sale.returned": "Return", "sale.reversed": "Sale reversed",
    "purchase.posted": "Purchase posted", "purchase.returned": "Returned to supplier",
    "purchase.reversed": "Purchase reversed", "customer_payment.recorded": "Payment received",
    "supplier_payment.recorded": "Supplier paid", "stock.opening": "Opening stock",
    "stock.opening_units": "Opening units", "stock_adjustment.requested": "Adjustment requested",
    "stock_adjustment.approved": "Adjustment approved", "expense.recorded": "Expense",
    "product.created": "New product", "customer.created": "New customer",
    "sale.quick_reviewed": "Quick sale reviewed",
}


def dashboard(db: Session, d1: date, d2: date, viewer: User) -> Dashboard:
    owner = viewer.is_owner
    stock = _stock_rows(db)
    low = [r for r in stock if r["status"] != "OK"]
    low.sort(key=lambda r: (r["qty_on_hand"], r["product"].lower()))
    products = {p.id: p.name for p in db.scalars(select(Product))}
    best = sorted(((pid, v) for pid, v in _product_sales(db, d1, d2).items() if v["qty"] > 0),
                  key=lambda kv: (-kv[1]["qty"], -kv[1]["net_sales"]))[:5]
    out = Dashboard(
        period=period_figures(db, d1, d2, show_cost=owner),
        by_day=_by_day(db, d1, d2, owner) if (d2 - d1).days <= 92 else [],
        best_sellers=[TopProduct(product_id=pid, name=products.get(pid, "?"), quantity=v["qty"],
                                 net_sales=v["net_sales"]) for pid, v in best],
        low_stock=[StockAlert(product_id=r["product_id"], name=r["product"], sku=r["sku"],
                              qty_on_hand=r["qty_on_hand"], reorder_level=r["reorder_level"])
                   for r in low[:8]],
        low_stock_count=sum(1 for r in stock if r["status"] == "Low"),
        out_of_stock_count=sum(1 for r in stock if r["status"] == "Out of stock"),
    )
    if not owner:
        return out
    out.stock_value = sum((r["stock_value"] for r in stock), ZERO)
    out.retail_value = sum((r["retail_value"] for r in stock), ZERO)
    customer_ids = list(db.scalars(select(Customer.id)))
    balances = customer_balances(db, customer_ids).values()
    out.customers_owe = sum((b.owed for b in balances), ZERO)
    out.customers_overdue = sum((b.overdue for b in balances), ZERO)
    supplier_ids = list(db.scalars(select(Supplier.id)))
    out.owed_to_suppliers = sum((max(ZERO, bought - paid) for bought, paid in
                                 supplier_balances(db, supplier_ids).values()), ZERO)
    out.quick_sales_to_review = quick_sales.pending_count(db)
    out.pending_adjustments = db.scalar(select(func.count()).select_from(StockAdjustment)
                                        .where(StockAdjustment.status == AdjustmentStatus.PENDING))
    rows = db.execute(select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.user_id)
                      .where(AuditLog.action.in_(ACTION_WORDS)).order_by(AuditLog.id.desc())
                      .limit(8)).all()
    out.recent_activity = [Activity(
        when=log.created_at.isoformat(), who=name, action=ACTION_WORDS[log.action],
        detail=_activity_detail(log.action, log.details or {}),
    ) for log, name in rows]
    return out


def _activity_detail(action: str, d: dict) -> str:
    if action == "sale.quick_reviewed":
        return f"{d.get('receipt_no')} · {d.get('item')} → {d.get('product') or 'cost entered'}"
    if action == "sale.completed":
        return f"{d.get('receipt_no')} · ₦{Decimal(d.get('total', 0)):,.2f}"
    if action in ("sale.returned", "sale.reversed"):
        return f"{d.get('receipt_no')} · {d.get('reason', '')}"
    if action.startswith("purchase."):
        return f"{d.get('reference')}" + (f" · ₦{Decimal(d['total']):,.2f}" if d.get("total") else "")
    if "amount" in d:
        return f"₦{Decimal(d['amount']):,.2f}" + (f" · {d['customer']}" if d.get("customer") else
                                                 f" · {d['supplier']}" if d.get("supplier") else
                                                 f" · {d['category']}" if d.get("category") else "")
    return str(d.get("name") or d.get("reason") or "")


# ---------- Reports ----------

def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    return (part / whole * 100).quantize(Decimal("0.1")) if whole else None


def report_sales(db: Session, d1: date, d2: date) -> Report:
    rows, totals = [], defaultdict(Decimal)
    sale_in = and_(Sale.status == SaleStatus.COMPLETED, Sale.business_date.between(d1, d2))
    counts = dict(db.execute(select(Sale.business_date, func.count(Sale.id)).where(sale_in)
                             .group_by(Sale.business_date)).all())
    day = d1
    while day <= d2:
        f = period_figures(db, day, day, show_cost=True) if counts.get(day) or _any_return(db, day) else None
        if f:
            row = {"day": day, "sales": f.sales_count, "items": f.items_sold, "gross_sales": f.gross_sales,
                   "discounts": f.discounts, "returns": f.returns, "net_sales": f.net_sales,
                   "cost_of_goods": f.cost_of_goods, "gross_profit": f.gross_profit,
                   "margin": f.gross_margin}
            rows.append(row)
            for k in ("sales", "items", "gross_sales", "discounts", "returns", "net_sales",
                      "cost_of_goods", "gross_profit"):
                totals[k] += Decimal(row[k])
        day += timedelta(days=1)
    totals_out = {k: (int(v) if k in ("sales", "items") else _d(v)) for k, v in totals.items()}
    if rows:
        totals_out["margin"] = _pct(totals["gross_profit"], totals["net_sales"])
    return Report(
        key="sales", title="Sales", subtitle=_period_text(d1, d2),
        columns=[Column(key="day", label="Day", kind="date"), Column(key="sales", label="Sales", kind="int"),
                 Column(key="items", label="Items", kind="int"),
                 Column(key="gross_sales", label="Gross sales", kind="money"),
                 Column(key="discounts", label="Discounts", kind="money"),
                 Column(key="returns", label="Returns", kind="money"),
                 Column(key="net_sales", label="Net sales", kind="money"),
                 Column(key="cost_of_goods", label="Cost of goods", kind="money"),
                 Column(key="gross_profit", label="Gross profit", kind="money"),
                 Column(key="margin", label="Margin", kind="percent")],
        rows=rows, totals=totals_out or None,
        notes=["Gross sales are after line discounts. Reversed sales are not counted."],
    )


def _any_return(db: Session, day: date) -> bool:
    return db.scalar(select(SaleReturn.id).where(SaleReturn.business_date == day).limit(1)) is not None


def report_profit(db: Session, d1: date, d2: date) -> Report:
    f = period_figures(db, d1, d2, show_cost=True)
    by_category = db.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.spent_on.between(d1, d2), Expense.reversed.is_(False))
        .group_by(Expense.category).order_by(func.sum(Expense.amount).desc())).all()
    rows = [
        {"line": "Gross sales (after discounts)", "amount": f.gross_sales},
        {"line": "  less items returned", "amount": -f.returns},
        {"line": "Net sales", "amount": f.net_sales},
        {"line": "  less cost of goods sold", "amount": -f.cost_of_goods},
        {"line": "Gross profit", "amount": f.gross_profit},
        *[{"line": f"  less {cat}", "amount": -_d(amt)} for cat, amt in by_category],
        {"line": "  less stock losses (damaged / missing)", "amount": -f.stock_losses},
        {"line": "Net profit" if f.expenses_recorded else "Profit before expenses (no expenses recorded)",
         "amount": f.net_profit},
    ]
    return Report(
        key="profit", title="Profit and loss", subtitle=_period_text(d1, d2),
        columns=[Column(key="line", label=""), Column(key="amount", label="Amount", kind="money")],
        rows=rows,
        notes=[f"Discounts given: ₦{f.discounts:,.2f}. Gross margin: "
               f"{f.gross_margin if f.gross_margin is not None else '—'}%.",
               "Cost of goods uses the cost frozen on each sale (unit cost for phones, average cost "
               "for accessories), so later cost changes never alter past profit."],
    )


def report_products(db: Session, d1: date, d2: date, slow: bool) -> Report:
    sales = _product_sales(db, d1, d2)
    stock = {r["product_id"]: r for r in _stock_rows(db)}
    rows = []
    for pid, r in stock.items():
        s = sales.get(pid, {"qty": 0, "net_sales": ZERO, "cost": Decimal(0)})
        if not slow and s["qty"] <= 0 and s["net_sales"] == 0:
            continue
        profit = s["net_sales"] - _d(s["cost"])
        rows.append({"product": r["product"], "sku": r["sku"], "category": r["category"],
                     "qty_sold": s["qty"], "net_sales": s["net_sales"], "cost_of_goods": _d(s["cost"]),
                     "gross_profit": profit, "margin": _pct(profit, s["net_sales"]),
                     "in_stock": r["qty_on_hand"], "stock_value": r["stock_value"]})
    if slow:  # slow sellers: in stock, sold least
        rows = [r for r in rows if r["in_stock"] > 0]
        rows.sort(key=lambda r: (r["qty_sold"], -r["stock_value"]))
    else:
        rows.sort(key=lambda r: (-r["qty_sold"], -r["net_sales"]))
    return Report(
        key="slow-sellers" if slow else "best-sellers",
        title="Slow sellers" if slow else "Best sellers", subtitle=_period_text(d1, d2),
        columns=[Column(key="product", label="Product"), Column(key="sku", label="SKU"),
                 Column(key="category", label="Category"), Column(key="qty_sold", label="Sold", kind="int"),
                 Column(key="net_sales", label="Net sales", kind="money"),
                 Column(key="cost_of_goods", label="Cost", kind="money"),
                 Column(key="gross_profit", label="Gross profit", kind="money"),
                 Column(key="margin", label="Margin", kind="percent"),
                 Column(key="in_stock", label="In stock", kind="int"),
                 Column(key="stock_value", label="Stock value", kind="money")],
        rows=rows,
        totals={"qty_sold": sum(r["qty_sold"] for r in rows),
                "net_sales": sum((r["net_sales"] for r in rows), ZERO),
                "cost_of_goods": sum((r["cost_of_goods"] for r in rows), ZERO),
                "gross_profit": sum((r["gross_profit"] for r in rows), ZERO),
                "stock_value": sum((r["stock_value"] for r in rows), ZERO)} if rows else None,
        notes=["Slow sellers: products in stock that sold least in the period (money tied up)."] if slow
        else ["Quantities and sales are after returns."],
    )


def report_stock(db: Session, which: str) -> Report:
    rows = _stock_rows(db)
    if which == "low":
        rows = [r for r in rows if r["status"] != "OK"]
    elif which == "out":
        rows = [r for r in rows if r["status"] == "Out of stock"]
    today = business_today()
    return Report(
        key="stock", title={"low": "Low and out of stock", "out": "Out of stock"}.get(which, "Stock and valuation"),
        subtitle=f"As at {today:%d %b %Y}",
        columns=[Column(key="sku", label="SKU"), Column(key="product", label="Product"),
                 Column(key="category", label="Category"), Column(key="tracking", label="Tracking"),
                 Column(key="qty_on_hand", label="In stock", kind="int"),
                 Column(key="reorder_level", label="Low at", kind="int"), Column(key="status", label="Status"),
                 Column(key="avg_cost", label="Cost each", kind="money"),
                 Column(key="stock_value", label="Value at cost", kind="money"),
                 Column(key="selling_price", label="Price", kind="money"),
                 Column(key="retail_value", label="Value at price", kind="money")],
        rows=rows,
        totals={"qty_on_hand": sum(r["qty_on_hand"] for r in rows),
                "stock_value": sum((r["stock_value"] for r in rows), ZERO),
                "retail_value": sum((r["retail_value"] for r in rows), ZERO)} if rows else None,
        notes=["Phones and laptops are valued at each unit's own cost; accessories at weighted-average cost."],
    )


def report_debts(db: Session) -> Report:
    customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.is_walk_in.is_(False)))}
    balances = customer_balances(db, list(customers))
    oldest = dict(db.execute(select(Sale.customer_id, func.min(Sale.business_date)).where(
        Sale.status == SaleStatus.COMPLETED, sale_balance_expr() > 0).group_by(Sale.customer_id)).all())
    last_paid = dict(db.execute(select(CustomerPayment.customer_id, func.max(CustomerPayment.business_date))
                                .where(CustomerPayment.reversed.is_(False))
                                .group_by(CustomerPayment.customer_id)).all())
    rows = [{"customer": c.name, "phone": c.phone or "", "owed": balances[c.id].owed,
             "overdue": balances[c.id].overdue, "oldest_unpaid": oldest.get(c.id),
             "last_payment": last_paid.get(c.id)}
            for c in customers.values() if balances[c.id].owed > 0]
    rows.sort(key=lambda r: -r["owed"])
    return Report(
        key="debts", title="Customer debts", subtitle=f"As at {business_today():%d %b %Y}",
        columns=[Column(key="customer", label="Customer"), Column(key="phone", label="Phone"),
                 Column(key="owed", label="Owes", kind="money"), Column(key="overdue", label="Overdue", kind="money"),
                 Column(key="oldest_unpaid", label="Oldest unpaid sale", kind="date"),
                 Column(key="last_payment", label="Last payment", kind="date")],
        rows=rows,
        totals={"owed": sum((r["owed"] for r in rows), ZERO),
                "overdue": sum((r["overdue"] for r in rows), ZERO)} if rows else None,
    )


def report_suppliers(db: Session) -> Report:
    suppliers = list(db.scalars(select(Supplier).order_by(func.lower(Supplier.name))))
    balances = supplier_balances(db, [s.id for s in suppliers])
    rows = []
    for s in suppliers:
        bought, paid = balances[s.id]
        if bought == 0 and paid == 0:
            continue
        rows.append({"supplier": s.name, "phone": s.phone or "", "bought": _d(bought), "paid": _d(paid),
                     "balance": _d(bought - paid)})
    return Report(
        key="suppliers", title="Supplier balances", subtitle=f"As at {business_today():%d %b %Y}",
        columns=[Column(key="supplier", label="Supplier"), Column(key="phone", label="Phone"),
                 Column(key="bought", label="Bought (less returns)", kind="money"),
                 Column(key="paid", label="Paid", kind="money"), Column(key="balance", label="You owe", kind="money")],
        rows=rows,
        totals={k: sum((r[k] for r in rows), ZERO) for k in ("bought", "paid", "balance")} if rows else None,
        notes=["A negative balance means the supplier owes the shop (credit from returns)."],
    )


def report_purchases(db: Session, d1: date, d2: date) -> Report:
    paid = (select(func.coalesce(func.sum(SupplierPayment.amount), 0))
            .where(SupplierPayment.purchase_id == Purchase.id, SupplierPayment.reversed.is_(False))
            .scalar_subquery())
    credit = purchase_credit_expr()
    rows = []
    for p, sup, cr, pd in db.execute(
        select(Purchase, Supplier.name, credit, paid).join(Supplier, Supplier.id == Purchase.supplier_id)
        .where(Purchase.status != PurchaseStatus.DRAFT, Purchase.purchase_date.between(d1, d2))
        .order_by(Purchase.purchase_date, Purchase.id)
    ):
        posted = p.status == PurchaseStatus.POSTED
        rows.append({"day": p.purchase_date, "reference": p.reference, "supplier": sup,
                     "invoice": p.supplier_invoice_no or "", "status": p.status.capitalize(),
                     "total": p.total, "returned": _d(cr), "paid": _d(pd),
                     "balance": _d(p.total - Decimal(cr) - Decimal(pd)) if posted else ZERO})
    live = [r for r in rows if r["status"] == "Posted"]
    return Report(
        key="purchases", title="Purchases", subtitle=_period_text(d1, d2),
        columns=[Column(key="day", label="Date", kind="date"), Column(key="reference", label="Reference"),
                 Column(key="supplier", label="Supplier"), Column(key="invoice", label="Invoice"),
                 Column(key="status", label="Status"), Column(key="total", label="Total", kind="money"),
                 Column(key="returned", label="Returned", kind="money"), Column(key="paid", label="Paid", kind="money"),
                 Column(key="balance", label="Still owed", kind="money")],
        rows=rows,
        totals={k: sum((r[k] for r in live), ZERO) for k in ("total", "returned", "paid", "balance")} if live else None,
        notes=["Totals leave out reversed purchases."],
    )


MOVEMENT_WORDS = {"opening": "Opening stock", "purchase": "Purchase", "sale": "Sale",
                  "customer_return": "Customer return", "supplier_return": "Return to supplier",
                  "damage": "Damaged", "missing": "Missing", "adjustment": "Adjustment", "reversal": "Reversal"}


def report_movements(db: Session, d1: date, d2: date, movement_type: str | None) -> Report:
    q = (select(StockMovement, Product.name, Product.sku, User.full_name,
                func.coalesce(ProductUnit.imei1, ProductUnit.serial_number))
         .join(Product, Product.id == StockMovement.product_id)
         .outerjoin(User, User.id == StockMovement.user_id)
         .outerjoin(ProductUnit, ProductUnit.id == StockMovement.unit_id)
         .where(shop_day(StockMovement.created_at).between(d1, d2)))
    if movement_type:
        q = q.where(StockMovement.movement_type == movement_type)
    rows = []
    for m, name, sku, who, unit in db.execute(q.order_by(StockMovement.id).limit(5000)):
        rows.append({"when": m.created_at, "product": name, "sku": sku, "unit": unit or "",
                     "type": MOVEMENT_WORDS.get(m.movement_type, m.movement_type),
                     "change": m.quantity_change, "balance_after": m.balance_after,
                     "unit_cost": _d(m.unit_cost), "value": _d(Decimal(m.quantity_change) * (m.unit_cost or 0)),
                     "reference": f"{m.reference_type} #{m.reference_id}" if m.reference_type else "",
                     "by": who or "", "reason": m.reason or ""})
    return Report(
        key="movements", title="Stock movements", subtitle=_period_text(d1, d2),
        columns=[Column(key="when", label="When", kind="datetime"), Column(key="product", label="Product"),
                 Column(key="sku", label="SKU"), Column(key="unit", label="IMEI / serial"),
                 Column(key="type", label="Movement"), Column(key="change", label="Change", kind="int"),
                 Column(key="balance_after", label="Stock after", kind="int"),
                 Column(key="unit_cost", label="Cost each", kind="money"), Column(key="value", label="Value", kind="money"),
                 Column(key="reference", label="Reference"), Column(key="by", label="By"),
                 Column(key="reason", label="Reason")],
        rows=rows, notes=["At most 5,000 movements are listed; narrow the dates for more."] if len(rows) == 5000 else [],
    )


def report_employees(db: Session, d1: date, d2: date) -> Report:
    rows = []
    for u in db.scalars(select(User).order_by(User.full_name)):
        f = period_figures(db, d1, d2, show_cost=True, seller_id=u.id)
        if not f.sales_count and not f.returns:
            continue
        # Cost of goods for this seller's sales, less cost of returns of their sales.
        cost = db.scalar(select(func.coalesce(func.sum(SaleLine.cost_total_snapshot), 0))
                         .join(Sale, Sale.id == SaleLine.sale_id)
                         .where(Sale.status == SaleStatus.COMPLETED, Sale.business_date.between(d1, d2),
                                Sale.sold_by_id == u.id))
        returned_cost = db.scalar(select(func.coalesce(func.sum(SaleReturnLine.cost_total), 0))
                                  .join(SaleReturn, SaleReturn.id == SaleReturnLine.return_id)
                                  .join(Sale, Sale.id == SaleReturn.sale_id)
                                  .where(SaleReturn.business_date.between(d1, d2), Sale.sold_by_id == u.id))
        profit = f.net_sales - _d(Decimal(cost) - Decimal(returned_cost))
        rows.append({"employee": u.full_name, "role": "Owner" if u.is_owner else "Sales staff",
                     "sales": f.sales_count, "items": f.items_sold, "gross_sales": f.gross_sales,
                     "discounts": f.discounts, "returns": f.returns, "net_sales": f.net_sales,
                     "average_sale": f.average_sale, "gross_profit": profit})
    return Report(
        key="employees", title="Sales by employee", subtitle=_period_text(d1, d2),
        columns=[Column(key="employee", label="Employee"), Column(key="role", label="Role"),
                 Column(key="sales", label="Sales", kind="int"), Column(key="items", label="Items", kind="int"),
                 Column(key="gross_sales", label="Gross sales", kind="money"),
                 Column(key="discounts", label="Discounts given", kind="money"),
                 Column(key="returns", label="Returns", kind="money"),
                 Column(key="net_sales", label="Net sales", kind="money"),
                 Column(key="average_sale", label="Average sale", kind="money"),
                 Column(key="gross_profit", label="Gross profit", kind="money")],
        rows=rows,
        totals={"sales": sum(r["sales"] for r in rows), "items": sum(r["items"] for r in rows),
                **{k: sum((r[k] for r in rows), ZERO) for k in
                   ("gross_sales", "discounts", "returns", "net_sales", "gross_profit")}} if rows else None,
        notes=["Returns count against the employee who made the sale."],
    )


def report_corrections(db: Session, d1: date, d2: date) -> Report:
    """Discounts, returns and reversals in the period — everything that reduced a figure."""
    names = dict(db.execute(select(User.id, User.full_name)).all())
    rows = []
    for line, s, pname in db.execute(
        select(SaleLine, Sale, Product.name).join(Sale, Sale.id == SaleLine.sale_id)
        .join(Product, Product.id == SaleLine.product_id)
        .where(SaleLine.discount > 0, Sale.business_date.between(d1, d2))
    ):
        rows.append({"when": s.sold_at, "kind": "Discount", "reference": s.receipt_no,
                     "detail": f"{pname} × {line.quantity} (price {line.quantity * line.unit_price:,.2f})",
                     "amount": line.discount, "by": names.get(s.sold_by_id, ""),
                     "reason": "Sale reversed later" if s.status == SaleStatus.REVERSED else ""})
    for r, receipt in db.execute(select(SaleReturn, Sale.receipt_no).join(Sale, Sale.id == SaleReturn.sale_id)
                                 .where(SaleReturn.business_date.between(d1, d2))):
        rows.append({"when": r.returned_at, "kind": "Customer return", "reference": receipt,
                     "detail": f"Refunded {r.refund_amount:,.2f}" if r.refund_amount else "Reduced debt",
                     "amount": r.value_total, "by": names.get(r.recorded_by_id, ""), "reason": r.reason})
    for r, ref in db.execute(select(SupplierReturn, Purchase.reference)
                             .join(Purchase, Purchase.id == SupplierReturn.purchase_id)
                             .where(SupplierReturn.business_date.between(d1, d2))):
        rows.append({"when": r.returned_at, "kind": "Return to supplier", "reference": ref,
                     "detail": "Supplier credit", "amount": r.credit_total,
                     "by": names.get(r.recorded_by_id, ""), "reason": r.reason})
    reversals = [
        (Sale, "Sale reversed", lambda s: s.receipt_no, lambda s: s.total),
        (Purchase, "Purchase reversed", lambda p: p.reference, lambda p: p.total),
        (CustomerPayment, "Customer payment reversed", lambda p: f"Payment #{p.id}", lambda p: p.amount),
        (SupplierPayment, "Supplier payment reversed", lambda p: f"Payment #{p.id}", lambda p: p.amount),
        (Expense, "Expense reversed", lambda e: e.category, lambda e: e.amount),
    ]
    for model, kind, ref, amount in reversals:
        for obj in db.scalars(select(model).where(model.reversed_at.is_not(None),
                                                  shop_day(model.reversed_at).between(d1, d2))):
            rows.append({"when": obj.reversed_at, "kind": kind, "reference": ref(obj), "detail": "",
                         "amount": amount(obj), "by": names.get(obj.reversed_by_id, ""),
                         "reason": obj.reversal_reason or ""})
    rows.sort(key=lambda r: r["when"])
    by_kind: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        by_kind[r["kind"]] += r["amount"]
    return Report(
        key="corrections", title="Discounts, returns and reversals", subtitle=_period_text(d1, d2),
        columns=[Column(key="when", label="When", kind="datetime"), Column(key="kind", label="What"),
                 Column(key="reference", label="Reference"), Column(key="detail", label="Detail"),
                 Column(key="amount", label="Amount", kind="money"), Column(key="by", label="By"),
                 Column(key="reason", label="Reason")],
        rows=rows,
        notes=[f"{k}: ₦{v:,.2f} ({sum(1 for r in rows if r['kind'] == k)})" for k, v in by_kind.items()],
    )


def report_cash(db: Session, d1: date, d2: date) -> Report:
    """Cash-up: money in and out by payment method (revenue is not cash)."""
    methods = dict(db.execute(select(PaymentMethod.id, PaymentMethod.name)).all())
    flows: dict[int | None, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for mid, amt in db.execute(select(CustomerPayment.method_id, func.sum(CustomerPayment.amount))
                               .where(CustomerPayment.business_date.between(d1, d2),
                                      CustomerPayment.reversed.is_(False))
                               .group_by(CustomerPayment.method_id)):
        flows[mid]["received"] += amt
    for mid, amt in db.execute(select(SaleReturn.refund_method_id, func.sum(SaleReturn.refund_amount))
                               .where(SaleReturn.business_date.between(d1, d2), SaleReturn.refund_amount > 0)
                               .group_by(SaleReturn.refund_method_id)):
        flows[mid]["refunds"] += amt
    for mid, amt in db.execute(select(Sale.reversal_refund_method_id, func.sum(Sale.reversal_refund))
                               .where(Sale.reversal_refund > 0, shop_day(Sale.reversed_at).between(d1, d2))
                               .group_by(Sale.reversal_refund_method_id)):
        flows[mid]["refunds"] += amt
    for mid, amt in db.execute(select(SupplierPayment.method_id, func.sum(SupplierPayment.amount))
                               .where(SupplierPayment.paid_on.between(d1, d2), SupplierPayment.reversed.is_(False))
                               .group_by(SupplierPayment.method_id)):
        flows[mid]["suppliers"] += amt
    for mid, amt in db.execute(select(Expense.method_id, func.sum(Expense.amount))
                               .where(Expense.spent_on.between(d1, d2), Expense.reversed.is_(False))
                               .group_by(Expense.method_id)):
        flows[mid]["expenses"] += amt
    rows = []
    for mid, f in sorted(flows.items(), key=lambda kv: methods.get(kv[0], "~")):
        net = f["received"] - f["refunds"] - f["suppliers"] - f["expenses"]
        rows.append({"method": methods.get(mid, "Not stated"), "received": _d(f["received"]),
                     "refunds": _d(f["refunds"]), "suppliers": _d(f["suppliers"]),
                     "expenses": _d(f["expenses"]), "net": _d(net)})
    return Report(
        key="cash", title="Money in and out", subtitle=_period_text(d1, d2),
        columns=[Column(key="method", label="Method"), Column(key="received", label="Received from customers", kind="money"),
                 Column(key="refunds", label="Refunded", kind="money"),
                 Column(key="suppliers", label="Paid to suppliers", kind="money"),
                 Column(key="expenses", label="Expenses", kind="money"), Column(key="net", label="Net", kind="money")],
        rows=rows,
        totals={k: sum((r[k] for r in rows), ZERO) for k in
                ("received", "refunds", "suppliers", "expenses", "net")} if rows else None,
        notes=["Credit sales are revenue on the day of sale but appear here only when paid."],
    )


# ---------- CSV ----------

def to_csv(report: Report) -> str:
    """Plain CSV a spreadsheet opens directly: numbers without ₦ or commas, ISO dates."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([report.title, report.subtitle])
    w.writerow([c.label for c in report.columns])
    for row in report.rows:
        w.writerow([_csv_value(row.get(c.key)) for c in report.columns])
    if report.totals:
        w.writerow([("Total" if i == 0 else _csv_value(report.totals.get(c.key, "")))
                    for i, c in enumerate(report.columns)])
    for note in report.notes:
        w.writerow([note])
    return "﻿" + buf.getvalue()  # BOM so Excel reads ₦ and accents correctly


def _csv_value(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "astimezone"):
        return v.astimezone(ZoneInfo(get_settings().business_timezone)).strftime("%Y-%m-%d %H:%M")
    return str(v)

