"""JD TechShop API — application entry point.

Run in development:  uvicorn app.main:app --reload   (from the backend/ folder)
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app import __version__
from app.api import api_router
from app.config import get_settings

log = logging.getLogger("jdtechshop")
settings = get_settings()

CLIENT_HEADER = "X-JDT-Client"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

app = FastAPI(
    title="JD TechShop API",
    version=__version__,
    # Interactive API docs are handy in development but hidden in production.
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF protection: state-changing API calls must carry a custom header.
    # Browsers do not let other websites add custom headers to requests sent to us,
    # so a malicious page cannot trick a logged-in browser into, e.g., recording a sale.
    if request.url.path.startswith("/api/") and request.method in UNSAFE_METHODS:
        if request.headers.get(CLIENT_HEADER) != "web":
            return JSONResponse(status_code=403,
                                content={"detail": "Request blocked: missing client header."})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # Turn Pydantic's technical errors into readable, per-field messages.
    errors = []
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")]
        msg = str(e.get("msg", "Invalid value")).removeprefix("Value error, ")
        errors.append({"field": ".".join(loc) or None, "message": msg})
    first = errors[0]["message"] if errors else "Invalid input."
    return JSONResponse(status_code=422, content={"detail": first, "errors": errors})


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_: Request, exc: IntegrityError):
    log.warning("Integrity error: %s", exc.orig)
    return JSONResponse(
        status_code=409,
        content={"detail": "This change conflicts with existing records (duplicate or invalid "
                           "reference). Nothing was saved."},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on the server. Nothing was saved. "
                           "Please try again or contact the administrator."},
    )


app.include_router(api_router)


# Serve the built frontend (frontend/dist) if present, so the shop can run one server.
_dist = settings.frontend_dist
if _dist.is_dir() and (_dist / "index.html").exists():
    if (_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})
        candidate = (_dist / full_path).resolve()
        if full_path and candidate.is_file() and _dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
