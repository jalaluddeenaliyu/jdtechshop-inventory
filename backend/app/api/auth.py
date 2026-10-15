from fastapi import APIRouter, Request, Response

from app.config import get_settings
from app.core.deps import DB, CurrentUser
from app.core.security import COOKIE_NAME, create_access_token
from app.models import User
from app.schemas.user import ChangePasswordRequest, LoginRequest, UserOut
from app.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_access_token(user.id, user.token_version),
        max_age=s.access_token_minutes * 60,
        httponly=True,          # JavaScript cannot read it
        secure=s.cookie_secure,  # HTTPS-only in production
        samesite="lax",         # not sent on cross-site form posts
        path="/api",
    )


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, response: Response, db: DB) -> User:
    user = user_service.authenticate(db, body.username, body.password, request)
    _set_session_cookie(response, user)
    return user


@router.post("/logout", status_code=204)
def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/api")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.post("/change-password", response_model=UserOut)
def change_password(body: ChangePasswordRequest, request: Request, response: Response,
                    user: CurrentUser, db: DB) -> User:
    user = user_service.change_own_password(db, user, body.current_password, body.new_password,
                                            request)
    _set_session_cookie(response, user)  # keep this device logged in; others are logged out
    return user

