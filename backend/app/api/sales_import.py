"""Import old sales from another app (history only) — owner only."""
from fastapi import APIRouter, File, Query, Request, UploadFile

from app.core.deps import DB, OwnerUser
from app.services import sales_import

router = APIRouter(prefix="/sales-import", tags=["sales"])


@router.post("")
async def import_sales(
    request: Request,
    owner: OwnerUser,
    db: DB,
    file: UploadFile = File(...),
    commit: bool = Query(False, description="false = check and preview only; true = save"),
) -> dict:
    content = await file.read(sales_import.MAX_BYTES + 1)
    return sales_import.run_import(db, content, owner, request, commit=commit)
