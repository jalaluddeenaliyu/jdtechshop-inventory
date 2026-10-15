"""Shop time. "Today" is a calendar day in the business time zone (Africa/Lagos), not UTC."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings


def now_utc() -> datetime:
    return datetime.now(UTC)


def business_today() -> date:
    return datetime.now(ZoneInfo(get_settings().business_timezone)).date()
