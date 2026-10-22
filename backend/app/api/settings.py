"""Business settings, payment methods and the audit log viewer."""
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core import audit
from app.core.deps import DB, CurrentUser, OwnerUser
from app.models import AuditLog, BusinessSettings, PaymentMethod, User
from app.schemas.settings import (
    AuditLogOut,
    BusinessSettingsOut,
    BusinessSettingsUpdate,
    Page,
    PaymentMethodCreate,
    PaymentMethodOut,
    PaymentMethodUpdate,
)

router = APIRouter(tags=["settings"])


# ---------- Business settings ----------

@router.get("/settings", response_model=BusinessSettingsOut)
def get_business_settings(_: CurrentUser, db: DB) -> BusinessSettings:
    row = db.get(BusinessSettings, 1)
    if row is None:  # should never happen: created by the first migration
        raise HTTPException(500, "Business settings are missing. Run the database migrations.")
    return row


@router.put("/settings", response_model=BusinessSettingsOut)
def update_business_settings(body: BusinessSettingsUpdate, request: Request, owner: OwnerUser,
                             db: DB) -> BusinessSettings:
    row = db.get(BusinessSettings, 1, with_for_update=True)
    changes = {}
    for field, new in body.model_dump().items():
        old = getattr(row, field)
        if old != new:
            changes[field] = {"from": str(old) if old is not None else None,
                              "to": str(new) if new is not None else None}
            setattr(row, field, new)
    if changes:
        row.updated_by_id = owner.id
        audit.record(db, action="settings.updated", user=owner, entity_type="business_settings",
                     entity_id=1, details=changes, request=request)
        db.commit()
        db.refresh(row)  # return exactly what the database stored
    return row


# ---------- Payment methods ----------

@router.get("/payment-methods", response_model=list[PaymentMethodOut])
def list_payment_methods(_: CurrentUser, db: DB,
                         include_inactive: bool = False) -> list[PaymentMethod]:
    q = (select(PaymentMethod).where(PaymentMethod.is_trade_in.is_(False))  # swaps: built in, hidden
         .order_by(PaymentMethod.sort_order, PaymentMethod.name))
    if not include_inactive:
        q = q.where(PaymentMethod.is_active.is_(True))
    return list(db.scalars(q))


@router.post("/payment-methods", response_model=PaymentMethodOut, status_code=201)
def create_payment_method(body: PaymentMethodCreate, request: Request, owner: OwnerUser,
                          db: DB) -> PaymentMethod:
    pm = PaymentMethod(name=body.name, sort_order=body.sort_order, is_active=True)
    db.add(pm)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A payment method with that name exists.")
    audit.record(db, action="payment_method.created", user=owner, entity_type="payment_method",
                 entity_id=pm.id, details={"name": pm.name}, request=request)
    db.commit()
    return pm


@router.patch("/payment-methods/{pm_id}", response_model=PaymentMethodOut)
def update_payment_method(pm_id: int, body: PaymentMethodUpdate, request: Request,
                          owner: OwnerUser, db: DB) -> PaymentMethod:
    pm = db.get(PaymentMethod, pm_id, with_for_update=True)
    if pm is None or pm.is_trade_in:
        raise HTTPException(404, "Payment method not found.")
    changes = {}
    for field, new in body.model_dump(exclude_none=True).items():
        if field == "name":
            new = " ".join(new.split())
        if getattr(pm, field) != new:
            changes[field] = {"from": getattr(pm, field), "to": new}
            setattr(pm, field, new)
    if changes:
        if pm.is_active is False:
            active_left = db.scalar(select(func.count()).select_from(PaymentMethod).where(
                PaymentMethod.is_active.is_(True), PaymentMethod.id != pm.id))
            if not active_left:
                db.rollback()
                raise HTTPException(409, "At least one payment method must stay active.")
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "A payment method with that name exists.")
        audit.record(db, action="payment_method.updated", user=owner, entity_type="payment_method",
                     entity_id=pm.id, details=changes, request=request)
        db.commit()
    return pm


# ---------- Audit log ----------

@router.get("/audit-logs", response_model=Page[AuditLogOut])
def list_audit_logs(
    _: OwnerUser,
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    user_id: int | None = None,
) -> dict:
    q = select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.user_id)
    count_q = select(func.count()).select_from(AuditLog)
    if action:
        q = q.where(AuditLog.action.startswith(action))
        count_q = count_q.where(AuditLog.action.startswith(action))
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
        count_q = count_q.where(AuditLog.user_id == user_id)
    rows = db.execute(
        q.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = []
    for log, name in rows:
        item = AuditLogOut.model_validate(log)
        item.user_name = name
        items.append(item)
    return {"items": items, "total": db.scalar(count_q) or 0, "page": page, "page_size": page_size}
