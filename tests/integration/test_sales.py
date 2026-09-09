from datetime import date

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.modules.inventory.crud import create_product
from src.modules.inventory.models import Product, ProductBatch
from src.modules.inventory.schemas import ProductCreate
from src.modules.sales.crud import create_invoice
from src.modules.sales.models import Invoice, InvoiceItem
from src.modules.sales.schemas import InvoiceCreate
from src.modules.shifts.models import Shift
from src.modules.tenants.models import Tenant
from src.modules.users.models import User
from tests.factories import PharmacyFactory


def _seed_tenant_user_and_shift(db: Session) -> tuple[Tenant, User, Shift]:
    factory = PharmacyFactory(db, "sales")
    tenant, _owner = factory.owned_tenant(
        name="Synthetic Pharmacy",
        owner_email="owner@example.test",
        owner_full_name="Synthetic Owner",
    )
    user = factory.user(
        tenant,
        email="cashier@example.test",
        hashed_password="not-a-real-password-hash",
        full_name="Synthetic Cashier",
        role="pharmacist",
    )
    shift = factory.shift(user)
    db.commit()
    return tenant, user, shift


@pytest.mark.integration
def test_invoice_creation_preserves_product_batch_invariant(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant, user, _ = _seed_tenant_user_and_shift(db)
        product = create_product(
            db,
            ProductCreate(
                name="Synthetic Medicine",
                price=30,
                part_price=10,
                is_divisible=True,
                parts_per_unit=3,
                initial_boxes=2,
                initial_parts=1,
                initial_batch={
                    "batch_number": "SYNTHETIC-BATCH-1",
                    "expiry_date": date(2030, 1, 1),
                },
            ),
            tenant.id,
            actor_user_id=user.id,
        )

        invoice = create_invoice(
            db,
            InvoiceCreate(
                payment_type="cash",
                items=[{"product_id": product.id, "quantity": 2, "sale_unit_price": 10}],
            ),
            user.id,
            tenant.id,
        )

        db.expire_all()
        stored_product = db.get(Product, product.id)
        stored_batch = db.query(ProductBatch).filter_by(product_id=product.id).one()
        assert invoice.payment_type == "cash"
        assert invoice.total_amount == 20
        assert stored_product is not None
        assert stored_product.total_parts == 5
        assert stored_batch.quantity == 5
        assert db.scalar(func.count(InvoiceItem.id)) == 1

    engine.dispose()


@pytest.mark.integration
def test_invoice_rejects_stock_mismatch_without_partial_write(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant, user, shift = _seed_tenant_user_and_shift(db)
        product = Product(name="Mismatched Product", price=10, total_parts=5, tenant_id=tenant.id)
        db.add(product)
        db.flush()
        db.add(
            ProductBatch(
                batch_number="SYNTHETIC-MISMATCH",
                expiry_date=date(2030, 1, 1),
                quantity=4,
                product_id=product.id,
                tenant_id=tenant.id,
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            create_invoice(
                db,
                InvoiceCreate(
                    payment_type="cash",
                    items=[
                        {
                            "product_id": product.id,
                            "quantity": 1,
                            "sale_unit_price": 10,
                        }
                    ],
                ),
                user.id,
                tenant.id,
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(Invoice.id)) == 0
        assert db.get(Product, product.id).total_parts == 5
        assert db.query(ProductBatch).filter_by(product_id=product.id).one().quantity == 4
        assert db.get(Shift, shift.id).status == "OPEN"

    engine.dispose()
