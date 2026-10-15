"""Dashboard (everyone; cost and profit for the owner only) and reports (owner only).

Every report can be downloaded as CSV with `?format=csv` — the same rows as on screen.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from app.core.clock import business_today
from app.core.deps import DB, CurrentUser, OwnerUser
from app.models import MovementType
from app.schemas.reports import Dashboard, Report
from app.services import reports as service

router = APIRouter(tags=["reports"])

MAX_DAYS = 366

ReportName = Literal["sales", "profit", "best-sellers", "slow-sellers", "stock", "debts", "suppliers",
                     "purchases", "movements", "employees", "corrections", "cash"]


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    d2 = date_to or business_today()
    d1 = date_from or d2
    if d1 > d2:
        raise HTTPException(422, "The start date is after the end date.")
    if (d2 - d1).days >= MAX_DAYS:
        raise HTTPException(422, "Choose a period of at most a year.")
    return d1, d2


@router.get("/dashboard", response_model=Dashboard)
def dashboard(user: CurrentUser, db: DB, date_from: date | None = None,
              date_to: date | None = None) -> Dashboard:
    d1, d2 = _period(date_from, date_to)
    return service.dashboard(db, d1, d2, user)


@router.get("/reports/{name}", response_model=Report)
def report(
    name: ReportName,
    _: OwnerUser,
    db: DB,
    date_from: date | None = None,
    date_to: date | None = None,
    stock: Literal["all", "low", "out"] = "all",
    movement_type: MovementType | None = None,
    format: Literal["json", "csv"] = Query("json", description="csv downloads a spreadsheet file"),
):
    if name in ("sales", "profit", "best-sellers", "slow-sellers", "purchases", "movements",
                "employees", "corrections", "cash") and date_from is None and date_to is None:
        d2 = business_today()
        d1 = d2.replace(day=1)  # default: this month so far
    else:
        d1, d2 = _period(date_from, date_to)
    if (d2 - d1).days >= MAX_DAYS:
        raise HTTPException(422, "Choose a period of at most a year.")
    builders = {
        "sales": lambda: service.report_sales(db, d1, d2),
        "profit": lambda: service.report_profit(db, d1, d2),
        "best-sellers": lambda: service.report_products(db, d1, d2, slow=False),
        "slow-sellers": lambda: service.report_products(db, d1, d2, slow=True),
        "stock": lambda: service.report_stock(db, stock),
        "debts": lambda: service.report_debts(db),
        "suppliers": lambda: service.report_suppliers(db),
        "purchases": lambda: service.report_purchases(db, d1, d2),
        "movements": lambda: service.report_movements(db, d1, d2, movement_type),
        "employees": lambda: service.report_employees(db, d1, d2),
        "corrections": lambda: service.report_corrections(db, d1, d2),
        "cash": lambda: service.report_cash(db, d1, d2),
    }
    result = builders[name]()
    if format == "csv":
        filename = f"jdtechshop-{name}-{d1:%Y%m%d}" + ("" if d1 == d2 else f"-{d2:%Y%m%d}") + ".csv"
        return Response(service.to_csv(result), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return result

