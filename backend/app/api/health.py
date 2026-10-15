from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.core.deps import DB

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: DB) -> JSONResponse:
    """Public check that the API and database are reachable. Reveals no business data."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"status": "ok" if db_ok else "degraded",
                 "database": "ok" if db_ok else "unreachable",
                 "version": __version__},
    )
