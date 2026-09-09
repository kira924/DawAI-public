from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core.config import settings

NEAR_EXPIRY_WARNING_DAYS = 90
NEAR_EXPIRY_CRITICAL_DAYS = 30


class ExpiryStatus(str, Enum):
    EXPIRED = "expired"
    CRITICAL = "critical"
    WARNING = "warning"
    VALID = "valid"


def business_timezone(timezone_name: str | None = None) -> ZoneInfo:
    name = timezone_name or settings.BUSINESS_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown business timezone: {name}") from error


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_business_date(
    at: datetime | None = None,
    *,
    timezone_name: str | None = None,
) -> date:
    instant = at or utc_now()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Business-time conversion requires a timezone-aware datetime")
    return instant.astimezone(business_timezone(timezone_name)).date()


def business_day_utc_bounds(
    business_date: date,
    *,
    timezone_name: str | None = None,
) -> tuple[datetime, datetime]:
    zone = business_timezone(timezone_name)
    local_start = datetime.combine(business_date, time.min, tzinfo=zone)
    local_end = datetime.combine(business_date + timedelta(days=1), time.min, tzinfo=zone)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def normalize_month_expiry(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def normalize_expiry_input(value: object) -> object:
    if isinstance(value, str):
        normalized = value.strip()
        if len(normalized) == 7 and normalized[4] == "-":
            year_text, month_text = normalized.split("-", maxsplit=1)
            if year_text.isdigit() and month_text.isdigit():
                return normalize_month_expiry(int(year_text), int(month_text))
    return value


def days_until_expiry(expiry_date: date, *, as_of: date) -> int:
    return (expiry_date - as_of).days


def classify_expiry(expiry_date: date, *, as_of: date) -> ExpiryStatus:
    remaining_days = days_until_expiry(expiry_date, as_of=as_of)
    if remaining_days < 0:
        return ExpiryStatus.EXPIRED
    if remaining_days <= NEAR_EXPIRY_CRITICAL_DAYS:
        return ExpiryStatus.CRITICAL
    if remaining_days <= NEAR_EXPIRY_WARNING_DAYS:
        return ExpiryStatus.WARNING
    return ExpiryStatus.VALID


def is_sellable_expiry(expiry_date: date, *, as_of: date) -> bool:
    return expiry_date >= as_of
