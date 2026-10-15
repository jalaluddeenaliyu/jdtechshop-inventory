"""Request dependencies: who is logged in, and are they allowed to do this?

Every protected route declares `CurrentUser` or `OwnerUser`. Permission checks happen
here on the server, so calling the API directly cannot bypass them.
"""
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import COOKIE_NAME, decode_access_token
from app.database import get_db
from app.models import Role, User

DB = Annotated[Session, Depends(get_db)]

_NOT_LOGGED_IN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in to continue."
)


def get_current_user(
    db: DB, session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None
) -> User:
    if not session_token:
        raise _NOT_LOGGED_IN
    payload = decode_access_token(session_token)
    if not payload:
        raise _NOT_LOGGED_IN
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise _NOT_LOGGED_IN
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise _NOT_LOGGED_IN
    return user


def require_owner(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can perform this action.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OwnerUser = Annotated[User, Depends(require_owner)]
