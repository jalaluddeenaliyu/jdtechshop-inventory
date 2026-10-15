from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

ColumnKind = Literal["text", "money", "int", "date", "datetime", "percent"]


class Column(BaseModel):
    key: str
    label: str
    kind: ColumnKind = "text"


class Report(BaseModel):
    """Any report: the same shape is shown on screen, printed and exported as CSV."""

    key: str
    title: str
    subtitle: str  # e.g. the period covered
    columns: list[Column]
    rows: list[dict[str, Any]]
    totals: dict[str, Any] | None = None
    notes: list[str] = []


class MethodAmount(BaseModel):
    method: str
    amount: Decimal


class PeriodFigures(BaseModel):
    """Everything about one period, worked out from the records (see services/reports.py)."""

    date_from: date
    date_to: date
    sales_count: int
    items_sold: int
    gross_sales: Decimal       # completed sales, after line discounts
    discounts: Decimal
    returns: Decimal           # value of items customers brought back
    net_sales: Decimal
    average_sale: Decimal
    credit_given: Decimal      # part of this period's sales not paid at the till
    # owner only (None for sales staff)
    cost_of_goods: Decimal | None = None
    gross_profit: Decimal | None = None
    gross_margin: Decimal | None = None  # percent of net sales
    expenses: Decimal | None = None
    expenses_recorded: bool | None = None
    stock_losses: Decimal | None = None   # damaged / missing stock written off, at cost
    net_profit: Decimal | None = None
    cash_received: Decimal | None = None  # customer payments in the period
    refunds: Decimal | None = None
    received_by_method: list[MethodAmount] | None = None


class DayPoint(BaseModel):
    day: date
    net_sales: Decimal
    gross_profit: Decimal | None = None


class TopProduct(BaseModel):
    product_id: int
    name: str
    quantity: int
    net_sales: Decimal


class StockAlert(BaseModel):
    product_id: int
    name: str
    sku: str
    qty_on_hand: int
    reorder_level: int


class Activity(BaseModel):
    when: str
    who: str | None
    action: str
    detail: str


class Dashboard(BaseModel):
    period: PeriodFigures
    by_day: list[DayPoint]
    best_sellers: list[TopProduct]
    low_stock: list[StockAlert]
    low_stock_count: int
    out_of_stock_count: int
    # owner only: positions right now
    stock_value: Decimal | None = None        # at cost
    retail_value: Decimal | None = None       # at selling price
    customers_owe: Decimal | None = None
    customers_overdue: Decimal | None = None
    owed_to_suppliers: Decimal | None = None
    pending_adjustments: int | None = None
    recent_activity: list[Activity] | None = None
