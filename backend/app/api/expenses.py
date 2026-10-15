"""Expenses — owner only."""
from datetime import date

from fastapi import APIRouter, Query, Request

from app.core.deps import DB, OwnerUser
from app.schemas.expenses import ExpenseIn, ExpenseOut
from app.schemas.returns import ReasonIn
from app.services import expenses as service

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get("")
def list_expenses(_: OwnerUser, db: DB, date_from: date | None = None, date_to: date | None = None,
                  category: str | None = Query(None, max_length=60), page: int = Query(1, ge=1),
                  page_size: int = Query(25, ge=1, le=200)) -> dict:
    return service.list_expenses(db, date_from=date_from, date_to=date_to, category=category,
                                 page=page, page_size=page_size)


@router.get("/categories", response_model=list[str])
def expense_categories(_: OwnerUser, db: DB) -> list[str]:
    return service.categories(db)


@router.post("", response_model=ExpenseOut, status_code=201)
def record_expense(body: ExpenseIn, request: Request, owner: OwnerUser, db: DB) -> ExpenseOut:
    return service.expense_out(db, service.record_expense(db, body, owner, request))


@router.post("/{expense_id}/reverse", response_model=ExpenseOut)
def reverse_expense(expense_id: int, body: ReasonIn, request: Request, owner: OwnerUser,
                    db: DB) -> ExpenseOut:
    return service.expense_out(db, service.reverse_expense(db, expense_id, body.reason, owner, request))
