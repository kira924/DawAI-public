from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.modules.inventory import replenishment, schemas
from tests.factories import PharmacyFactory


@pytest.mark.integration
def test_replenishment_uses_sellable_stock_and_isolates_tenants(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    inventory_date = date(2026, 9, 9)

    with Session(engine) as db:
        factory = PharmacyFactory(db, "replenishment")
        tenant, _ = factory.owned_tenant()
        foreign_tenant, _ = factory.owned_tenant()
        due = factory.product(
            tenant,
            name="Due Product",
            total_parts=53,
            reorder_point_parts=4,
            target_stock_parts=16,
            is_divisible=True,
            parts_per_unit=10,
        )
        factory.batch(due, batch_number="VALID", expiry_date=inventory_date, quantity=3)
        factory.batch(
            due,
            batch_number="EXPIRED",
            expiry_date=date(2026, 9, 8),
            quantity=50,
        )
        healthy = factory.product(
            tenant,
            total_parts=5,
            reorder_point_parts=4,
            target_stock_parts=10,
        )
        factory.batch(healthy, quantity=5)
        factory.product(tenant)
        foreign_due = factory.product(
            foreign_tenant,
            reorder_point_parts=1,
            target_stock_parts=5,
        )
        db.commit()

        recommendations = replenishment.get_replenishment_recommendations(
            db,
            tenant.id,
            as_of=inventory_date,
        )

        assert [item.product_id for item in recommendations] == [due.id]
        assert recommendations[0].model_dump() == {
            "product_id": due.id,
            "product_name": "Due Product",
            "barcode": None,
            "parts_per_unit": 10,
            "sellable_parts": 3,
            "reorder_point_parts": 4,
            "target_stock_parts": 16,
            "suggested_order_parts": 13,
            "suggested_full_units": 1,
            "suggested_loose_parts": 3,
            "recommendation_source": schemas.ReplenishmentRecommendationSource.MANUAL_POLICY,
            "inventory_date": inventory_date,
        }
        assert replenishment.count_replenishment_due(db, tenant.id, as_of=inventory_date) == 1
        assert foreign_due.id not in {item.product_id for item in recommendations}

    engine.dispose()


@pytest.mark.integration
def test_replenishment_policy_is_tenant_scoped_and_database_constrained(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        factory = PharmacyFactory(db, "replenishment-policy")
        tenant, _ = factory.owned_tenant()
        foreign_tenant, _ = factory.owned_tenant()
        product = factory.product(tenant)
        db.commit()

        configured = replenishment.set_replenishment_policy(
            db,
            product.id,
            tenant.id,
            schemas.ReplenishmentPolicyUpsert(
                reorder_point_parts=5,
                target_stock_parts=12,
            ),
        )
        assert configured is not None and configured.is_configured
        assert (
            replenishment.set_replenishment_policy(
                db,
                product.id,
                foreign_tenant.id,
                schemas.ReplenishmentPolicyUpsert(
                    reorder_point_parts=1,
                    target_stock_parts=2,
                ),
            )
            is None
        )

        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "UPDATE products SET target_stock_parts = NULL, reorder_point_parts = 2 "
                    "WHERE id = :product_id"
                ),
                {"product_id": product.id},
            )
            db.commit()
        db.rollback()

        cleared = replenishment.set_replenishment_policy(
            db,
            product.id,
            tenant.id,
            schemas.ReplenishmentPolicyUpsert(
                reorder_point_parts=None,
                target_stock_parts=None,
            ),
        )
        assert cleared is not None and not cleared.is_configured

    engine.dispose()
