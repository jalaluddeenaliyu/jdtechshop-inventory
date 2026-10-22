"""Quick sales waiting for the owner's review (items sold before they were in the catalogue)."""
from fastapi import APIRouter, Request

from app.core.deps import DB, OwnerUser
from app.services import quick_sales as service

router = APIRouter(tags=["sales"])


@router.get("/quick-sales", response_model=list[service.QuickLineOut])
def list_quick_sales(_: OwnerUser, db: DB, reviewed: bool = False) -> list[service.QuickLineOut]:
    return service.list_quick_lines(db, reviewed=reviewed)


@router.post("/quick-sales/{line_id}/review", response_model=service.QuickLineOut)
def review_quick_sale(line_id: int, body: service.QuickReviewIn, request: Request,
                      user: OwnerUser, db: DB) -> service.QuickLineOut:
    return service.review_line(db, line_id, body, user, request)
