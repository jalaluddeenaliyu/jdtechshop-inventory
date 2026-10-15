"""Shop expenses (owner only). They turn gross profit into net profit. Permanent; a mistake is
reversed (once, with a reason), never edited or deleted."""
from datetime import date

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import business_today, now_utc
from app.models import Expense, PaymentMethod, User
from app.schemas.expenses import COMMON_CATEGORIES, ExpenseIn, ExpenseOut


def expense_out(db: Session, e: Expense) -> ExpenseOut:
    method = db.get(PaymentMethod, e.method_id) if e.method_id else None
    return ExpenseOut(
        id=e.id, category=e.category, amount=e.amount, spent_on=e.spent_on,
        method_name=method.name if method else None, note=e.note,
        recorded_by_name=db.scalar(select(User.full_name).where(User.id == e.recorded_by_id)),
        created_at=e.created_at, reversed=e.reversed, reversal_reason=e.reversal_reason,
    )


def record_expense(db: Session, data: ExpenseIn, actor: User, request: Request | None) -> Expense:
    spent_on = data.spent_on or business_today()
    if spent_on > business_today():
        raise HTTPException(422, "The date cannot be in the future.")
    if data.method_id is not None:
        method = db.get(PaymentMethod, data.method_id)
        if method is None or not method.is_active:
            raise HTTPException(422, "Choose an active payment method.")
    expense = Expense(category=data.category, amount=data.amount, spent_on=spent_on,
                      method_id=data.method_id, note=data.note, recorded_by_id=actor.id)
    db.add(expense)
    db.flush()
    audit.record(db, action="expense.recorded", user=actor, entity_type="expense", entity_id=expense.id,
                 details={"category": expense.category, "amount": str(expense.amount),
                          "spent_on": str(spent_on)},
                 request=request)
    db.commit()
    db.refresh(expense)
    return expense


def reverse_expense(db: Session, expense_id: int, reason: str, actor: User,
                    request: Request | None) -> Expense:
    expense = db.get(Expense, expense_id, with_for_update=True)
    if expense is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense not found.")
    if expense.reversed:
        return expense
    expense.reversed = True
    expense.reversed_at = now_utc()
    expense.reversed_by_id = actor.id
    expense.reversal_reason = reason
    db.flush()
    audit.record(db, action="expense.reversed", user=actor, entity_type="expense", entity_id=expense.id,
                 details={"amount": str(expense.amount), "reason": reason}, request=request)
    db.commit()
    db.refresh(expense)
    return expense


def list_expenses(db: Session, *, date_from: date | None, date_to: date | None,
                  category: str | None, page: int, page_size: int) -> dict:
    conditions = []
    if date_from:
        conditions.append(Expense.spent_on >= date_from)
    if date_to:
        conditions.append(Expense.spent_on <= date_to)
    if category:
        conditions.append(func.lower(Expense.category) == category.lower())
    total = db.scalar(select(func.count()).select_from(Expense).where(*conditions)) or 0
    amount = db.scalar(select(func.coalesce(func.sum(Expense.amount), 0))
                       .where(*conditions, Expense.reversed.is_(False)))
    rows = db.scalars(select(Expense).where(*conditions)
                      .order_by(Expense.spent_on.desc(), Expense.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [expense_out(db, e) for e in rows], "total": total, "page": page,
            "page_size": page_size, "amount_total": str(amount)}


def categories(db: Session) -> list[str]:
    used = list(db.scalars(select(Expense.category).distinct().order_by(Expense.category)))
    return used + [c for c in COMMON_CATEGORIES if c.lower() not in {u.lower() for u in used}]
