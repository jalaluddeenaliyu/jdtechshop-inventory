"""User accounts and login rules."""
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.core.security import hash_password, verify_password
from app.models import Role, User
from app.schemas.user import UserCreate, UserUpdate

BAD_LOGIN = "Incorrect username or password."


def _now() -> datetime:
    return datetime.now(UTC)


def authenticate(db: Session, username: str, password: str, request: Request | None) -> User:
    """Check a username/password. Locks the account after repeated failures."""
    s = get_settings()
    user = db.scalar(select(User).where(User.username == username).with_for_update())

    if user is None:
        verify_password(password, None)  # equalise response time
        audit.record(db, action="auth.login_failed", user=None,
                     details={"username": username, "reason": "unknown_user"}, request=request)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    if user.locked_until and user.locked_until > _now():
        minutes = max(1, int((user.locked_until - _now()).total_seconds() // 60) + 1)
        db.rollback()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed attempts. Try again in about {minutes} minute(s), "
            "or ask the owner to reset your password.",
        )

    if not verify_password(password, user.password_hash) or not user.is_active:
        reason = "inactive" if user.is_active is False else "bad_password"
        if reason == "bad_password":
            user.failed_login_count += 1
            if user.failed_login_count >= s.max_failed_logins:
                user.locked_until = _now() + timedelta(minutes=s.lockout_minutes)
                user.failed_login_count = 0
                reason = "locked"
        audit.record(db, action="auth.login_failed", user=user, entity_type="user",
                     entity_id=user.id, details={"reason": reason}, request=request)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    audit.record(db, action="auth.login", user=user, entity_type="user", entity_id=user.id,
                 request=request)
    db.commit()
    return user


def create_user(db: Session, data: UserCreate, actor: User | None, request: Request | None) -> User:
    if db.scalar(select(User.id).where(User.username == data.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken.")
    user = User(
        full_name=data.full_name,
        username=data.username,
        password_hash=hash_password(data.password),
        role=data.role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    audit.record(db, action="user.created", user=actor, entity_type="user", entity_id=user.id,
                 details={"username": user.username, "role": user.role}, request=request)
    db.commit()
    return user


def _ensure_another_active_owner(db: Session, excluding: User) -> None:
    owners = db.scalars(
        select(User).where(User.role == Role.OWNER, User.is_active.is_(True)).with_for_update()
    ).all()
    if not any(o.id != excluding.id for o in owners):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The shop must always have at least one active owner account.",
        )


def update_user(db: Session, user_id: int, data: UserUpdate, actor: User,
                request: Request | None) -> User:
    user = db.get(User, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    changes: dict[str, dict] = {}
    losing_owner_power = (
        user.role == Role.OWNER and user.is_active
        and ((data.role is not None and data.role != Role.OWNER) or data.is_active is False)
    )
    if losing_owner_power:
        if user.id == actor.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You cannot remove your own owner access or deactivate yourself.",
            )
        _ensure_another_active_owner(db, excluding=user)

    for field in ("full_name", "role", "is_active"):
        new = getattr(data, field)
        if new is not None and new != getattr(user, field):
            changes[field] = {"from": getattr(user, field), "to": new}
            setattr(user, field, new)

    if not changes:
        db.rollback()
        return user

    # Role or access changes take effect immediately: log the user out everywhere.
    if "role" in changes or "is_active" in changes:
        user.token_version += 1

    audit.record(db, action="user.updated", user=actor, entity_type="user", entity_id=user.id,
                 details=changes, request=request)
    db.commit()
    return user


def reset_password(db: Session, user_id: int, new_password: str, actor: User,
                   request: Request | None) -> User:
    user = db.get(User, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    user.failed_login_count = 0
    user.locked_until = None
    audit.record(db, action="user.password_reset", user=actor, entity_type="user",
                 entity_id=user.id, request=request)
    db.commit()
    return user


def change_own_password(db: Session, user: User, current: str, new: str,
                        request: Request | None) -> User:
    if not verify_password(current, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect.")
    if current == new:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "New password must be different from the current one.")
    user.password_hash = hash_password(new)
    user.token_version += 1
    audit.record(db, action="user.password_changed", user=user, entity_type="user",
                 entity_id=user.id, request=request)
    db.commit()
    return user


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0
