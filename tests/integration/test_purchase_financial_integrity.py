from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.modules.inventory.models import Product, ProductBatch
from src.modules.purchases.crud import (
    create_purchase_order,
    create_purchase_return,
    create_supplier_payment,
    get_supplier_balance,
    receive_purchase_order,
)
from src.modules.purchases.models import (
    PurchaseItem,
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnItem,
    Supplier,
    SupplierPayment,
)
from src.modules.purchases.schemas import (
    PurchaseOrderCreate,
    PurchaseReturnCreate,
    SupplierPaymentCreate,
)
from src.modules.tenants.models import Tenant
from src.modules.users.models import User


def _seed_context(db: Session) -> tuple[Tenant, User, Supplier, Product]:
    tenant = Tenant(
        name="Purchase Integrity Pharmacy",
        owner_email="purchase-integrity-owner@example.com",
    )
    db.add(tenant)
    db.flush()
    user = User(
        email=f"purchase-integrity-{tenant.id}@example.com",
        hashed_password="synthetic-hash",
        full_name="Purchase Integrity User",
        role="pharmacist",
        tenant_id=tenant.id,
    )
    supplier = Supplier(name="Purchase Integrity Supplier", tenant_id=tenant.id)
    product = Product(
        name="Purchase Integrity Product",
        price=20,
        total_parts=0,
        tenant_id=tenant.id,
    )
    db.add_all([user, supplier, product])
    db.commit()
    return tenant, user, supplier, product


def _received_order(
    db: Session,
    *,
    tenant_id: int,
    user_id: int,
    supplier_id: int,
    product_id: int,
    quantity: int = 3,
    base_cost: str = "10.00",
    discount_percentage: str = "0",
    batch_number: str = "PURCHASE-INTEGRITY-BATCH",
) -> PurchaseOrder:
    order = create_purchase_order(
        db,
        PurchaseOrderCreate.model_validate(
            {
                "supplier_id": supplier_id,
                "tax_percentage": 0,
                "items": [
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "base_cost": base_cost,
                        "discount_percentage": discount_percentage,
                        "batch_number": batch_number,
                        "expiry_date": "2030-01-01",
                    }
                ],
            }
        ),
        user_id,
        tenant_id,
    )
    return receive_purchase_order(db, order.id, tenant_id, user_id=user_id)


def _return_request(
    order_id: int,
    purchase_item_id: int,
    quantity: int,
    *,
    idempotency_key: str | None = None,
) -> PurchaseReturnCreate:
    return PurchaseReturnCreate.model_validate(
        {
            "purchase_order_id": order_id,
            "idempotency_key": idempotency_key or str(uuid4()),
            "items": [{"purchase_item_id": purchase_item_id, "quantity": quantity}],
        }
    )


@pytest.mark.integration
def test_purchase_totals_are_server_derived_and_receipt_records_exact_batch(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        order = _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=3,
            base_cost="10.01",
            discount_percentage="10",
        )
        item = db.query(PurchaseItem).filter_by(purchase_id=order.id).one()
        batch = db.get(ProductBatch, item.received_batch_id)

        assert order.subtotal == Decimal("30.03")
        assert order.total_discount == Decimal("3.00")
        assert order.tax_value == Decimal("0.00")
        assert order.total_cost == Decimal("27.03")
        assert item.unit_cost == Decimal("9.01")
        assert item.subtotal == Decimal("27.03")
        assert item.discount_value == Decimal("3.00")
        assert batch is not None
        assert batch.quantity == 3
        assert db.get(Product, product.id).total_parts == 3

    engine.dispose()


@pytest.mark.integration
def test_purchase_return_uses_original_item_cost_and_exact_received_batch(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        order = _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=3,
            base_cost="10.01",
            discount_percentage="10",
        )
        item = db.query(PurchaseItem).filter_by(purchase_id=order.id).one()
        purchase_return = create_purchase_return(
            db,
            _return_request(order.id, item.id, 2),
            user.id,
            tenant.id,
        )
        return_item = db.query(PurchaseReturnItem).filter_by(return_id=purchase_return.id).one()

        assert purchase_return.supplier_id == supplier.id
        assert purchase_return.total_refund == Decimal("18.02")
        assert return_item.purchase_item_id == item.id
        assert return_item.batch_id == item.received_batch_id
        assert return_item.product_id == product.id
        assert return_item.unit_refund == Decimal("9.01")
        assert return_item.subtotal == Decimal("18.02")
        assert db.get(ProductBatch, item.received_batch_id).quantity == 1
        assert db.get(Product, product.id).total_parts == 1
        assert get_supplier_balance(db, supplier.id, tenant.id)["current_balance"] == Decimal(
            "9.01"
        )

    engine.dispose()


@pytest.mark.integration
def test_purchase_return_is_idempotent_and_rejects_cumulative_excess(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        order = _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=2,
        )
        item = db.query(PurchaseItem).filter_by(purchase_id=order.id).one()
        idempotency_key = str(uuid4())
        request = _return_request(order.id, item.id, 1, idempotency_key=idempotency_key)

        first = create_purchase_return(db, request, user.id, tenant.id)
        replay = create_purchase_return(db, request, user.id, tenant.id)
        assert replay.id == first.id
        assert db.scalar(func.count(PurchaseReturn.id)) == 1
        assert db.get(Product, product.id).total_parts == 1

        with pytest.raises(HTTPException) as conflict:
            create_purchase_return(
                db,
                _return_request(order.id, item.id, 2, idempotency_key=idempotency_key),
                user.id,
                tenant.id,
            )
        assert conflict.value.status_code == status.HTTP_409_CONFLICT

        with pytest.raises(HTTPException) as excess:
            create_purchase_return(
                db,
                _return_request(order.id, item.id, 2),
                user.id,
                tenant.id,
            )
        assert excess.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(PurchaseReturn.id)) == 1
        assert db.get(Product, product.id).total_parts == 1

    engine.dispose()


@pytest.mark.integration
def test_purchase_return_cannot_take_stock_from_another_batch(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        order = _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=1,
        )
        item = db.query(PurchaseItem).filter_by(purchase_id=order.id).one()
        original_batch = db.get(ProductBatch, item.received_batch_id)
        original_batch.quantity = 0
        db.add(
            ProductBatch(
                batch_number="UNRELATED-BATCH",
                expiry_date=date(2031, 1, 1),
                quantity=5,
                product_id=product.id,
                tenant_id=tenant.id,
            )
        )
        product.total_parts = 5
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            create_purchase_return(
                db,
                _return_request(order.id, item.id, 1),
                user.id,
                tenant.id,
            )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.get(Product, product.id).total_parts == 5
        assert db.scalar(func.count(PurchaseReturn.id)) == 0

    engine.dispose()


@pytest.mark.integration
def test_supplier_payments_are_idempotent_and_cannot_exceed_payable_balance(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=3,
        )
        idempotency_key = str(uuid4())
        request = SupplierPaymentCreate.model_validate(
            {
                "supplier_id": supplier.id,
                "amount": "20.00",
                "payment_channel": "external",
                "idempotency_key": idempotency_key,
            }
        )
        first = create_supplier_payment(db, request, user.id, tenant.id)
        replay = create_supplier_payment(db, request, user.id, tenant.id)

        assert replay.id == first.id
        assert db.scalar(func.count(SupplierPayment.id)) == 1
        assert get_supplier_balance(db, supplier.id, tenant.id)["current_balance"] == Decimal(
            "10.00"
        )

        with pytest.raises(HTTPException) as conflict:
            create_supplier_payment(
                db,
                SupplierPaymentCreate.model_validate(
                    {
                        "supplier_id": supplier.id,
                        "amount": "19.00",
                        "payment_channel": "external",
                        "idempotency_key": idempotency_key,
                    }
                ),
                user.id,
                tenant.id,
            )
        assert conflict.value.status_code == status.HTTP_409_CONFLICT

        with pytest.raises(HTTPException) as overpayment:
            create_supplier_payment(
                db,
                SupplierPaymentCreate.model_validate(
                    {
                        "supplier_id": supplier.id,
                        "amount": "10.01",
                        "payment_channel": "external",
                        "idempotency_key": str(uuid4()),
                    }
                ),
                user.id,
                tenant.id,
            )
        assert overpayment.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(SupplierPayment.id)) == 1

    engine.dispose()


@pytest.mark.integration
def test_concurrent_purchase_returns_cannot_exceed_received_quantity(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        order = _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=1,
        )
        item = db.query(PurchaseItem).filter_by(purchase_id=order.id).one()
        tenant_id = tenant.id
        user_id = user.id
        order_id = order.id
        item_id = item.id
        product_id = product.id

    def attempt_return() -> int:
        with Session(engine) as worker_db:
            try:
                create_purchase_return(
                    worker_db,
                    _return_request(order_id, item_id, 1),
                    user_id,
                    tenant_id,
                )
                return status.HTTP_201_CREATED
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _: attempt_return(), range(2)))

    assert results == [status.HTTP_201_CREATED, status.HTTP_409_CONFLICT]
    with Session(engine) as db:
        assert db.scalar(func.count(PurchaseReturn.id)) == 1
        assert db.scalar(func.count(PurchaseReturnItem.id)) == 1
        assert db.get(Product, product_id).total_parts == 0

    engine.dispose()


@pytest.mark.integration
def test_concurrent_supplier_payments_cannot_overpay(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user, supplier, product = _seed_context(db)
        _received_order(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            supplier_id=supplier.id,
            product_id=product.id,
            quantity=1,
        )
        tenant_id = tenant.id
        user_id = user.id
        supplier_id = supplier.id

    def attempt_payment() -> int:
        with Session(engine) as worker_db:
            try:
                create_supplier_payment(
                    worker_db,
                    SupplierPaymentCreate.model_validate(
                        {
                            "supplier_id": supplier_id,
                            "amount": "7.00",
                            "payment_channel": "external",
                            "idempotency_key": str(uuid4()),
                        }
                    ),
                    user_id,
                    tenant_id,
                )
                return status.HTTP_201_CREATED
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _: attempt_payment(), range(2)))

    assert results == [status.HTTP_201_CREATED, status.HTTP_409_CONFLICT]
    with Session(engine) as db:
        assert db.scalar(func.count(SupplierPayment.id)) == 1
        assert get_supplier_balance(db, supplier_id, tenant_id)["current_balance"] == Decimal(
            "3.00"
        )

    engine.dispose()
