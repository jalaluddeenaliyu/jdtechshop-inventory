"""Audit trail helper.

`record()` adds the audit entry to the *same* database session as the business change,
so both are saved together or neither is.
"""
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User

# Never let these keys reach the audit log.
_REDACT = {"password", "password_hash", "new_password", "current_password", "token"}


def _clean(details: dict[str, Any] | None) -> dict[str, Any] | None:
    if details is None:
        return None
    out: dict[str, Any] = {}
    for key, value in details.items():
        if key in _REDACT:
            continue
        out[key] = _clean(value) if isinstance(value, dict) else (
            value if isinstance(value, (str, int, float, bool, list, type(None))) else str(value)
        )
    return out


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def record(
    db: Session,
    *,
    action: str,
    user: User | None,
    entity_type: str | None = None,
    entity_id: Any = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=_clean(details),
        ip_address=client_ip(request),
    )
    db.add(entry)
    return entry
