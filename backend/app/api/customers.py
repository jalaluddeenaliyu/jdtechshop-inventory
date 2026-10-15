"""Customers — owner and sales staff (only the owner can turn a customer off)."""
from fastapi import APIRouter, Query, Request

from app.core.deps import DB, CurrentUser
from app.schemas.customers import CustomerCreate, CustomerOut, CustomerUpdate
from app.schemas.settings import Page
from app.services import customers as service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=Page[CustomerOut])
def list_customers(
    _: CurrentUser,
    db: DB,
    q: str | None = Query(None, max_length=100, description="Name, phone or email"),
    include_inactive: bool = False,
    include_walk_in: bool = False,
    owing_only: bool = Query(False, description="Only customers who owe something"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    customers, total = service.list_customers(db, q=q, include_inactive=include_inactive,
                                              include_walk_in=include_walk_in, page=page,
                                              page_size=page_size, owing_only=owing_only)
    return {"items": service.customers_out(db, customers), "total": total, "page": page,
            "page_size": page_size}


@router.get("/walk-in", response_model=CustomerOut)
def walk_in_customer(_: CurrentUser, db: DB) -> CustomerOut:
    return service.customers_out(db, [service.walk_in_customer(db)])[0]


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, _: CurrentUser, db: DB) -> CustomerOut:
    return service.customers_out(db, [service.get_customer(db, customer_id)])[0]


@router.post("", response_model=CustomerOut, status_code=201)
def create_customer(body: CustomerCreate, request: Request, user: CurrentUser,
                    db: DB) -> CustomerOut:
    return service.customers_out(db, [service.create_customer(db, body, user, request)])[0]


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: int, body: CustomerUpdate, request: Request, user: CurrentUser,
                    db: DB) -> CustomerOut:
    customer = service.update_customer(db, customer_id, body, user, request)
    return service.customers_out(db, [customer])[0]
