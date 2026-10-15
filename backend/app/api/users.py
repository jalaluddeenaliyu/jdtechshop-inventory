"""User management — owner only."""
from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import DB, OwnerUser
from app.models import User
from app.schemas.user import PasswordReset, UserCreate, UserOut, UserUpdate
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(_: OwnerUser, db: DB) -> list[User]:
    return list(db.scalars(select(User).order_by(User.is_active.desc(), User.full_name)))


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, owner: OwnerUser, db: DB) -> User:
    return user_service.create_user(db, body, owner, request)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, owner: OwnerUser, db: DB) -> User:
    return user_service.update_user(db, user_id, body, owner, request)


@router.post("/{user_id}/reset-password", response_model=UserOut)
def reset_password(user_id: int, body: PasswordReset, request: Request, owner: OwnerUser,
                   db: DB) -> User:
    return user_service.reset_password(db, user_id, body.new_password, owner, request)
