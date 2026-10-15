"""Application settings, read from environment variables (or backend/.env)."""
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
INSECURE_KEYS = {"", "change-me", "dev-secret-change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    environment: str = "development"  # development | production | test
    database_url: str = "postgresql+psycopg://localhost:5432/jdtechshop"
    test_database_url: str = "postgresql+psycopg://localhost:5432/jdtechshop_test"
    secret_key: str = "dev-secret-change-me"
    access_token_minutes: int = 720
    cookie_secure: bool = False
    business_timezone: str = "Africa/Lagos"

    # Password hashing cost (12 is a good default; tests use a lower value for speed only).
    bcrypt_rounds: int = 12

    # Login protection
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Optional: path to the built frontend (frontend/dist) so one server can serve everything.
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    @model_validator(mode="after")
    def _check_production_safety(self):
        if self.environment == "production":
            if self.secret_key in INSECURE_KEYS or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a long random value in production "
                    "(see backend/.env.example)."
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
