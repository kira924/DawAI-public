from datetime import date

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.modules.inventory.models import Product, ProductBatch
from src.modules.purchases.crud import receive_purchase_order
from src.modules.purchases.models import PurchaseItem, PurchaseOrder, Supplier
from src.modules.tenants.models import Tenant
from src.modules.users.models import User


@pytest.mark.integration
def test_receiving_requires_batch_data_and_updates_both_stock_balances(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant = Tenant(name="Synthetic Pharmacy", owner_email="owner@example.test")
        db.add(tenant)
        db.flush()
        user = User(
            email="buyer@example.test",
            hashed_password="not-a-real-password-hash",
            full_name="Synthetic Buyer",
            role="pharmacist",
            tenant_id=tenant.id,
        )
        supplier = Supplier(name="Synthetic Supplier", tenant_id=tenant.id)
        product = Product(name="Synthetic Product", price=10, total_parts=0, tenant_id=tenant.id)
        db.add_all([user, supplier, product])
        db.flush()
        order = PurchaseOrder(
            status="PENDING",
            subtotal=50,
            total_discount=0,
            total_cost=50,
            supplier_id=supplier.id,
            user_id=user.id,
            tenant_id=tenant.id,
        )
        db.add(order)
        db.flush()
        item = PurchaseItem(
            purchase_id=order.id,
            product_id=product.id,
            tenant_id=tenant.id,
            quantity=5,
            base_cost=10,
            unit_cost=10,
            subtotal=50,
        )
        db.add(item)
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            receive_purchase_order(db, order.id, tenant.id, user_id=user.id)

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.get(Product, product.id).total_parts == 0
        assert db.get(PurchaseOrder, order.id).status == "PENDING"
        assert db.query(ProductBatch).filter_by(product_id=product.id).count() == 0

        stored_item = db.get(PurchaseItem, item.id)
        stored_item.batch_number = "SYNTHETIC-BATCH-1"
        stored_item.expiry_date = date(2030, 1, 1)
        db.commit()

        received_order = receive_purchase_order(db, order.id, tenant.id, user_id=user.id)

        stored_product = db.get(Product, product.id)
        stored_batch = db.query(ProductBatch).filter_by(product_id=product.id).one()
        assert received_order.status == "RECEIVED"
        assert stored_product.total_parts == 5
        assert stored_batch.quantity == 5
        assert db.get(PurchaseItem, item.id).received_batch_id == stored_batch.id

    engine.dispose()
