"""Import products from Excel/CSV — owner only."""
from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import Response

from app.core.deps import DB, OwnerUser
from app.services import importer
from app.services.import_template import build_template

router = APIRouter(prefix="/product-import", tags=["products"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/template")
def template(_: OwnerUser) -> Response:
    return Response(build_template(), media_type=XLSX, headers={
        "Content-Disposition": 'attachment; filename="JD-TechShop-products-template.xlsx"'})


@router.post("")
async def import_products(
    request: Request,
    owner: OwnerUser,
    db: DB,
    file: UploadFile = File(...),
    commit: bool = Query(False, description="false = check and preview only; true = save"),
) -> dict:
    content = await file.read(importer.MAX_BYTES + 1)
    return importer.run_import(db, content, file.filename or "", owner, request, commit=commit)
