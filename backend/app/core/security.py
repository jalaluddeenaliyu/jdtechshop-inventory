"""Password hashing and login tokens.

- Passwords are hashed with bcrypt (slow on purpose, salted). The plain password is never stored.
- After login, the server issues a signed token (JWT) inside an httpOnly cookie.
  The token contains the user's id and their `token_version`; bumping the version
  (password reset, deactivation) instantly invalidates every existing login.
"""
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

ALGORITHM = "HS256"
COOKIE_NAME = "jdt_session"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt ignores anything longer, so we refuse it instead

# Used to spend the same time checking a password when the username does not exist,
# so attackers cannot discover valid usernames by timing the response.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()


def validate_password_strength(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError("Password is too long (maximum 72 bytes).")
    if password.strip() != password:
        raise ValueError("Password must not start or end with spaces.")
    return password


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), (password_hash or _DUMMY_HASH).encode())
    except ValueError:
        return False


def create_access_token(user_id: int, token_version: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
