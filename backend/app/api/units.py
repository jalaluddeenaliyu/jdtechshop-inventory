"""Serialized units (phones, laptops). Anyone can look units up; only the owner adds or edits them.

Staff never see what a unit cost.
"""
from fastapi import APIRouter, Query, Request

from app.core.deps import DB, CurrentUser, OwnerUser
from app.schemas.settings import Page
from app.schemas.units import OpeningUnitsIn, OpeningUnitsResult, UnitOut, UnitStatusName, UnitUpdate
from app.services import catalog as catalog_service
from app.services import units as unit_service

router = APIRouter(tags=["units"])


@router.get("/units", response_model=Page[UnitOut])
def list_units(
    user: CurrentUser,
    db: DB,
    q: str | None = Query(None, max_length=60, description="Any part of a serial number or IMEI"),
    product_id: int | None = None,
    status: UnitStatusName | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    units, total = unit_service.list_units(db, q=q, product_id=product_id, status_=status,
                                           page=page, page_size=page_size)
    return {"items": [unit_service.unit_out(u, show_cost=user.is_owner) for u in units],
            "total": total, "page": page, "page_size": page_size}


@router.get("/units/{unit_id}", response_model=UnitOut)
def get_unit(unit_id: int, user: CurrentUser, db: DB) -> UnitOut:
    return unit_service.unit_out(unit_service.get_unit(db, unit_id), show_cost=user.is_owner)


@router.patch("/units/{unit_id}", response_model=UnitOut)
def update_unit(unit_id: int, body: UnitUpdate, request: Request, owner: OwnerUser,
                db: DB) -> UnitOut:
    return unit_service.unit_out(unit_service.update_unit(db, unit_id, body, owner, request), True)


@router.post("/products/{product_id}/units/opening", response_model=OpeningUnitsResult,
             status_code=201)
def add_opening_units(product_id: int, body: OpeningUnitsIn, request: Request, owner: OwnerUser,
                      db: DB) -> OpeningUnitsResult:
    product, units = unit_service.add_opening_units(db, product_id, body, owner, request)
    return OpeningUnitsResult(
        product=catalog_service.product_out_one(db, product, True),
        units=[unit_service.unit_out(u, True) for u in units],
    )
