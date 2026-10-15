"""Categories and brands. Everyone can read them; only the owner can change them."""
from fastapi import APIRouter, Request

from app.core.deps import DB, CurrentUser, OwnerUser
from app.models import Brand
from app.schemas.catalog import (
    BrandCreate,
    BrandOut,
    BrandUpdate,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
)
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalogue"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(_: CurrentUser, db: DB, include_inactive: bool = False) -> list[CategoryOut]:
    return [catalog_service.category_out(c)
            for c in catalog_service.list_categories(db, include_inactive)]


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryCreate, request: Request, owner: OwnerUser,
                    db: DB) -> CategoryOut:
    return catalog_service.category_out(catalog_service.create_category(db, body, owner, request))


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, body: CategoryUpdate, request: Request, owner: OwnerUser,
                    db: DB) -> CategoryOut:
    return catalog_service.category_out(
        catalog_service.update_category(db, category_id, body, owner, request)
    )


@router.get("/brands", response_model=list[BrandOut])
def list_brands(_: CurrentUser, db: DB, include_inactive: bool = False) -> list[Brand]:
    return catalog_service.list_brands(db, include_inactive)


@router.post("/brands", response_model=BrandOut, status_code=201)
def create_brand(body: BrandCreate, request: Request, owner: OwnerUser, db: DB) -> Brand:
    return catalog_service.create_brand(db, body, owner, request)


@router.patch("/brands/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: int, body: BrandUpdate, request: Request, owner: OwnerUser,
                 db: DB) -> Brand:
    return catalog_service.update_brand(db, brand_id, body, owner, request)
