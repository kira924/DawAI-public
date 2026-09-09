from datetime import date, datetime, timedelta, timezone

import pytest

from src.core.business_time import (
    ExpiryStatus,
    business_day_utc_bounds,
    business_timezone,
    classify_expiry,
    local_business_date,
    normalize_month_expiry,
)
from src.main import app
from src.modules.inventory.schemas import InitialBatchCreate
from src.modules.purchases.schemas import PurchaseItemCreate


def test_cairo_business_date_changes_at_local_midnight() -> None:
    before_midnight = datetime(2026, 9, 3, 20, 59, 59, tzinfo=timezone.utc)
    at_midnight = datetime(2026, 9, 3, 21, 0, 0, tzinfo=timezone.utc)

    assert local_business_date(before_midnight) == date(2026, 9, 3)
    assert local_business_date(at_midnight) == date(2026, 9, 4)


def test_business_day_bounds_are_half_open_and_dst_aware() -> None:
    cairo = business_timezone()
    normal_date = date(2026, 4, 23)
    normal_start, normal_end = business_day_utc_bounds(normal_date)

    assert normal_start.astimezone(cairo).date() == normal_date
    assert normal_start.astimezone(cairo).time().isoformat() == "00:00:00"
    assert normal_end - normal_start == timedelta(hours=24)

    dst_start_date = date(2026, 4, 24)
    start, end = business_day_utc_bounds(dst_start_date)

    assert start.astimezone(cairo).date() == dst_start_date
    assert start.astimezone(cairo).time().isoformat() == "01:00:00"
    assert end.astimezone(cairo).date() == dst_start_date + timedelta(days=1)
    assert end.astimezone(cairo).time().isoformat() == "00:00:00"
    assert end - start == timedelta(hours=23)


@pytest.mark.parametrize(
    ("remaining_days", "expected"),
    [
        (-1, ExpiryStatus.EXPIRED),
        (0, ExpiryStatus.CRITICAL),
        (30, ExpiryStatus.CRITICAL),
        (31, ExpiryStatus.WARNING),
        (90, ExpiryStatus.WARNING),
        (91, ExpiryStatus.VALID),
    ],
)
def test_expiry_classification_boundaries(
    remaining_days: int,
    expected: ExpiryStatus,
) -> None:
    today = date(2026, 9, 4)
    assert classify_expiry(today + timedelta(days=remaining_days), as_of=today) == expected


def test_month_only_expiry_normalizes_to_month_end() -> None:
    assert normalize_month_expiry(2028, 2) == date(2028, 2, 29)
    assert normalize_month_expiry(2027, 2) == date(2027, 2, 28)


def test_month_only_expiry_is_normalized_at_write_boundaries() -> None:
    initial_batch = InitialBatchCreate(batch_number="OPENING", expiry_date="2028-02")  # type: ignore[arg-type]
    purchase_item = PurchaseItemCreate(
        product_id=1,
        quantity=1,
        base_cost="10.00",
        batch_number="PURCHASE",
        expiry_date="2027-02",  # type: ignore[arg-type]
    )

    assert initial_batch.expiry_date == date(2028, 2, 29)
    assert purchase_item.expiry_date == date(2027, 2, 28)


def test_business_time_rejects_naive_datetime_and_unknown_zone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        local_business_date(datetime(2026, 9, 4, 12, 0))

    with pytest.raises(ValueError, match="Unknown business timezone"):
        business_timezone("Not/A-Timezone")


def test_openapi_exposes_business_date_and_near_expiry_confirmation() -> None:
    document = app.openapi()
    dashboard = document["paths"]["/api/v1/reports/dashboard"]["get"]
    receive = document["paths"]["/api/v1/purchases/orders/{order_id}/receive"]["post"]
    product_schema = document["components"]["schemas"]["ProductResponse"]

    assert "business_date" in {parameter["name"] for parameter in dashboard["parameters"]}
    assert receive["requestBody"].get("required", False) is False
    assert "sellable_parts" in product_schema["properties"]
    assert "expired_parts" in product_schema["properties"]
