import pytest
from pydantic import ValidationError

from src.modules.inventory.schemas import ReplenishmentPolicyUpsert


@pytest.mark.parametrize(
    ("reorder_point", "target"),
    [(None, 10), (5, None), (-1, 10), (5, 5), (6, 5)],
)
def test_replenishment_policy_rejects_incomplete_or_invalid_values(
    reorder_point: int | None,
    target: int | None,
) -> None:
    with pytest.raises(ValidationError):
        ReplenishmentPolicyUpsert(
            reorder_point_parts=reorder_point,
            target_stock_parts=target,
        )


def test_replenishment_policy_accepts_configuration_and_explicit_clear() -> None:
    configured = ReplenishmentPolicyUpsert(reorder_point_parts=5, target_stock_parts=12)
    cleared = ReplenishmentPolicyUpsert(reorder_point_parts=None, target_stock_parts=None)

    assert configured.model_dump() == {
        "reorder_point_parts": 5,
        "target_stock_parts": 12,
    }
    assert cleared.model_dump() == {
        "reorder_point_parts": None,
        "target_stock_parts": None,
    }
