from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import validate_password_strength

RoleName = Literal["owner", "staff"]
USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,49}$"


def _norm_username(v: str) -> str:
    return v.strip().lower()


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if len(v) < 2:
        raise ValueError("Full name must have at least 2 characters.")
    return v


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)

    _u = field_validator("username")(_norm_username)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str

    _p = field_validator("new_password")(validate_password_strength)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    username: str
    role: RoleName
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    full_name: str = Field(max_length=120)
    username: str = Field(max_length=50)
    password: str
    role: RoleName = "staff"

    _n = field_validator("full_name")(_clean_name)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        import re

        v = _norm_username(v)
        if not re.match(USERNAME_PATTERN, v):
            raise ValueError(
                "Username must be 3–50 characters: letters, numbers, dot, dash or underscore."
            )
        return v

    _p = field_validator("password")(validate_password_strength)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    role: RoleName | None = None
    is_active: bool | None = None

    @field_validator("full_name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


class PasswordReset(BaseModel):
    new_password: str

    _p = field_validator("new_password")(validate_password_strength)
