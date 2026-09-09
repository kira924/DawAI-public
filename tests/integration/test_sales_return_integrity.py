from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.modules.customers.crud import create_customer_payment
from src.modules.customers.models import Customer, CustomerPaymentAllocation
from src.modules.customers.schemas import CustomerPaymentCreate
from src.modules.inventory.models import Product, ProductBatch
from src.modules.sales.crud import create_invoice, create_sales_return
from src.modules.sales.models import (
    Invoice,
    InvoiceItemBatchAllocation,
    QuarantineItem,
    SalesReturn,
    SalesReturnItem,
)
from src.modules.sales.schemas import InvoiceCreate, SalesReturnCreate
from src.modules.shifts.crud import close_shift
from src.modules.shifts.models import Shift
from src.modules.shifts.schemas import ShiftClose
from src.modules.tenants.models import Tenant
from src.modules.users.models import User


def _seed_context(db: Session) -> tuple[Tenant, User, User, Shift, Shift, Customer]:
    tenant = Tenant(
        name="Return Integrity Pharmacy",
        owner_email=f"owner-{uuid4()}@example.test",
    )
    db.add(tenant)
    db.flush()

    manager = User(
        email=f"manager-{uuid4()}@example.test",
        hashed_password="not-a-real-password-hash",
        full_name="Synthetic Manager",
        role="manager",
        tenant_id=tenant.id,
    )
    pharmacist = User(
        email=f"pharmacist-{uuid4()}@example.test",
        hashed_password="not-a-real-password-hash",
        full_name="Synthetic Pharmacist",
        role="pharmacist",
        tenant_id=tenant.id,
    )
    db.add_all([manager, pharmacist])
    db.flush()

    manager_shift = Shift(opening_balance=0, user_id=manager.id, tenant_id=tenant.id)
    pharmacist_shift = Shift(opening_balance=0, user_id=pharmacist.id, tenant_id=tenant.id)
    customer = Customer(
        name="Synthetic Customer",
        credit_limit=Decimal("1000.00"),
        tenant_id=tenant.id,
    )
    db.add_all([manager_shift, pharmacist_shift, customer])
    db.commit()
    return tenant, manager, pharmacist, manager_shift, pharmacist_shift, customer


def _seed_product(
    db: Session,
    tenant_id: int,
    *,
    name: str,
    price: str,
    batch_quantities: tuple[int, ...],
    return_policy: str = "standard",
) -> tuple[Product, list[ProductBatch]]:
    product = Product(
        name=name,
        price=Decimal(price),
        total_parts=sum(batch_quantities),
        return_policy=return_policy,
        tenant_id=tenant_id,
    )
    db.add(product)
    db.flush()
    batches = [
        ProductBatch(
            batch_number=f"{name.upper().replace(' ', '-')}-{index}",
            expiry_date=date(2035, index, 1),
            quantity=quantity,
            product_id=product.id,
            tenant_id=tenant_id,
        )
        for index, quantity in enumerate(batch_quantities, start=1)
    ]
    db.add_all(batches)
    db.commit()
    return product, batches


def _invoice(
    db: Session,
    *,
    product_id: int,
    quantity: int,
    sale_unit_price: str,
    payment_type: str,
    customer_id: int | None,
    user_id: int,
    tenant_id: int,
) -> Invoice:
    return create_invoice(
        db,
        InvoiceCreate(
            payment_type=payment_type,
            customer_id=customer_id,
            items=[
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "sale_unit_price": Decimal(sale_unit_price),
                }
            ],
        ),
        user_id,
        tenant_id,
    )


def _return_request(
    *,
    invoice_id: int,
    invoice_item_id: int,
    quantity: int,
    condition: str,
    batch_id: int | None = None,
    idempotency_key: UUID | None = None,
    destination: str = "customer_credit",
) -> SalesReturnCreate:
    return SalesReturnCreate(
        invoice_id=invoice_id,
        idempotency_key=idempotency_key or uuid4(),
        paid_portion_destination=destination,
        items=[
            {
                "invoice_item_id": invoice_item_id,
                "batch_id": batch_id,
                "quantity": quantity,
                "condition": condition,
            }
        ],
    )


@pytest.mark.integration
def test_sealed_return_restocks_original_batch_and_replays_after_shift_close(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant, _, pharmacist, _, _, _ = _seed_context(db)
        product, batches = _seed_product(
            db,
            tenant.id,
            name="Sealed Product",
            price="25.00",
            batch_quantities=(5,),
        )
        invoice = _invoice(
            db,
            product_id=product.id,
            quantity=3,
            sale_unit_price="19.99",
            payment_type="cash",
            customer_id=None,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        invoice_item_id = invoice.items[0].id
        allocation = db.query(InvoiceItemBatchAllocation).one()
        key = uuid4()
        request = _return_request(
            invoice_id=invoice.id,
            invoice_item_id=invoice_item_id,
            batch_id=allocation.batch_id,
            quantity=1,
            condition="sealed",
            idempotency_key=key,
        )

        result = create_sales_return(db, request, pharmacist.id, tenant.id, "pharmacist")

        assert result.total_refund == Decimal("19.99")
        assert result.cash_refund == Decimal("19.99")
        assert result.debt_reduction == Decimal("0.00")
        assert result.credit_balance_increase == Decimal("0.00")
        assert db.get(Product, product.id).total_parts == 3
        assert db.get(ProductBatch, batches[0].id).quantity == 3
        assert result.items[0].disposition == "restock"

        close_shift(
            db,
            ShiftClose(actual_closing_balance=0),
            pharmacist.id,
            tenant.id,
        )

        replay = create_sales_return(db, request, pharmacist.id, tenant.id, "pharmacist")
        assert replay.id == result.id
        assert db.scalar(func.count(SalesReturn.id)) == 1

        conflicting_request = _return_request(
            invoice_id=invoice.id,
            invoice_item_id=invoice_item_id,
            quantity=1,
            condition="unknown",
            idempotency_key=key,
        )
        with pytest.raises(HTTPException) as exc_info:
            create_sales_return(
                db,
                conflicting_request,
                pharmacist.id,
                tenant.id,
                "pharmacist",
            )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(SalesReturn.id)) == 1

    engine.dispose()


@pytest.mark.integration
def test_quarantine_does_not_increase_available_stock_and_over_return_is_atomic(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant, _, pharmacist, _, _, _ = _seed_context(db)
        product, batches = _seed_product(
            db,
            tenant.id,
            name="Quarantine Product",
            price="10.00",
            batch_quantities=(3,),
        )
        invoice = _invoice(
            db,
            product_id=product.id,
            quantity=3,
            sale_unit_price="8.50",
            payment_type="cash",
            customer_id=None,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        invoice_item_id = invoice.items[0].id
        allocation = db.query(InvoiceItemBatchAllocation).one()

        first_return = create_sales_return(
            db,
            _return_request(
                invoice_id=invoice.id,
                invoice_item_id=invoice_item_id,
                batch_id=allocation.batch_id,
                quantity=1,
                condition="opened_or_damaged",
            ),
            pharmacist.id,
            tenant.id,
            "pharmacist",
        )

        assert first_return.total_refund == Decimal("8.50")
        assert first_return.items[0].disposition == "quarantine"
        assert db.get(Product, product.id).total_parts == 0
        assert db.get(ProductBatch, batches[0].id).quantity == 0
        quarantine = db.query(QuarantineItem).one()
        assert quarantine.quantity == 1
        assert quarantine.batch_id == batches[0].id

        invalid_request = SalesReturnCreate(
            invoice_id=invoice.id,
            idempotency_key=uuid4(),
            items=[
                {
                    "invoice_item_id": invoice_item_id,
                    "batch_id": allocation.batch_id,
                    "quantity": 1,
                    "condition": "opened_or_damaged",
                },
                {
                    "invoice_item_id": invoice_item_id,
                    "quantity": 2,
                    "condition": "unknown",
                },
            ],
        )
        with pytest.raises(HTTPException) as exc_info:
            create_sales_return(
                db,
                invalid_request,
                pharmacist.id,
                tenant.id,
                "pharmacist",
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(SalesReturn.id)) == 1
        assert db.scalar(func.count(SalesReturnItem.id)) == 1
        assert db.scalar(func.count(QuarantineItem.id)) == 1
        assert db.get(Product, product.id).total_parts == 0

    engine.dispose()


@pytest.mark.integration
def test_return_policy_and_fourteen_day_window_are_enforced(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant, _, pharmacist, _, _, _ = _seed_context(db)
        non_returnable, _ = _seed_product(
            db,
            tenant.id,
            name="Cold Chain Product",
            price="40.00",
            batch_quantities=(1,),
            return_policy="non_returnable",
        )
        restricted_invoice = _invoice(
            db,
            product_id=non_returnable.id,
            quantity=1,
            sale_unit_price="40.00",
            payment_type="cash",
            customer_id=None,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        with pytest.raises(HTTPException) as exc_info:
            create_sales_return(
                db,
                _return_request(
                    invoice_id=restricted_invoice.id,
                    invoice_item_id=restricted_invoice.items[0].id,
                    quantity=1,
                    condition="unknown",
                ),
                pharmacist.id,
                tenant.id,
                "pharmacist",
            )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT

        unopened_only, _ = _seed_product(
            db,
            tenant.id,
            name="Sterile Product",
            price="30.00",
            batch_quantities=(1,),
            return_policy="unopened_only",
        )
        unopened_invoice = _invoice(
            db,
            product_id=unopened_only.id,
            quantity=1,
            sale_unit_price="30.00",
            payment_type="cash",
            customer_id=None,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        with pytest.raises(HTTPException) as exc_info:
            create_sales_return(
                db,
                _return_request(
                    invoice_id=unopened_invoice.id,
                    invoice_item_id=unopened_invoice.items[0].id,
                    quantity=1,
                    condition="opened_or_damaged",
                ),
                pharmacist.id,
                tenant.id,
                "pharmacist",
            )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT

        old_product, _ = _seed_product(
            db,
            tenant.id,
            name="Expired Window Product",
            price="20.00",
            batch_quantities=(1,),
        )
        old_invoice = _invoice(
            db,
            product_id=old_product.id,
            quantity=1,
            sale_unit_price="20.00",
            payment_type="cash",
            customer_id=None,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        old_invoice.created_at = datetime.now(timezone.utc) - timedelta(days=15)
        db.commit()
        with pytest.raises(HTTPException) as exc_info:
            create_sales_return(
                db,
                _return_request(
                    invoice_id=old_invoice.id,
                    invoice_item_id=old_invoice.items[0].id,
                    quantity=1,
                    condition="unknown",
                ),
                pharmacist.id,
                tenant.id,
                "pharmacist",
            )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(SalesReturn.id)) == 0

    engine.dispose()


@pytest.mark.integration
def test_credit_returns_reduce_debt_then_create_credit_or_manager_cash_refund(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)

    with Session(engine) as db:
        tenant, manager, pharmacist, _, _, customer = _seed_context(db)
        product, _ = _seed_product(
            db,
            tenant.id,
            name="Credit Product",
            price="50.00",
            batch_quantities=(4,),
        )
        invoice = _invoice(
            db,
            product_id=product.id,
            quantity=2,
            sale_unit_price="50.00",
            payment_type="credit",
            customer_id=customer.id,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )

        debt_return = create_sales_return(
            db,
            _return_request(
                invoice_id=invoice.id,
                invoice_item_id=invoice.items[0].id,
                quantity=1,
                condition="unknown",
            ),
            pharmacist.id,
            tenant.id,
            "pharmacist",
        )
        assert debt_return.debt_reduction == Decimal("50.00")
        assert debt_return.cash_refund == Decimal("0.00")
        assert db.get(Customer, customer.id).total_debt == Decimal("50.00")
        assert db.get(Invoice, invoice.id).outstanding_amount == Decimal("50.00")

        payment = create_customer_payment(
            db,
            CustomerPaymentCreate(customer_id=customer.id, amount=Decimal("50.00")),
            pharmacist.id,
            tenant.id,
        )
        allocation = db.query(CustomerPaymentAllocation).filter_by(payment_id=payment.id).one()
        assert allocation.invoice_id == invoice.id
        assert allocation.amount == Decimal("50.00")

        credit_return = create_sales_return(
            db,
            _return_request(
                invoice_id=invoice.id,
                invoice_item_id=invoice.items[0].id,
                quantity=1,
                condition="unknown",
            ),
            pharmacist.id,
            tenant.id,
            "pharmacist",
        )
        assert credit_return.credit_balance_increase == Decimal("50.00")
        assert credit_return.cash_refund == Decimal("0.00")
        assert db.get(Customer, customer.id).credit_balance == Decimal("50.00")

        cash_invoice = _invoice(
            db,
            product_id=product.id,
            quantity=1,
            sale_unit_price="30.00",
            payment_type="credit",
            customer_id=customer.id,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        create_customer_payment(
            db,
            CustomerPaymentCreate(customer_id=customer.id, amount=Decimal("30.00")),
            pharmacist.id,
            tenant.id,
        )
        cash_request = _return_request(
            invoice_id=cash_invoice.id,
            invoice_item_id=cash_invoice.items[0].id,
            quantity=1,
            condition="unknown",
            destination="cash",
        )
        with pytest.raises(HTTPException) as exc_info:
            create_sales_return(
                db,
                cash_request,
                pharmacist.id,
                tenant.id,
                "pharmacist",
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

        manager_cash_return = create_sales_return(
            db,
            cash_request,
            manager.id,
            tenant.id,
            "manager",
        )
        assert manager_cash_return.cash_refund == Decimal("30.00")
        assert manager_cash_return.credit_balance_increase == Decimal("0.00")
        assert db.get(Customer, customer.id).credit_balance == Decimal("50.00")

    engine.dispose()


@pytest.mark.integration
def test_concurrent_returns_cannot_exceed_quantity_sold(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, _, pharmacist, _, _, _ = _seed_context(db)
        product, _ = _seed_product(
            db,
            tenant.id,
            name="Concurrent Product",
            price="15.00",
            batch_quantities=(1,),
        )
        invoice = _invoice(
            db,
            product_id=product.id,
            quantity=1,
            sale_unit_price="15.00",
            payment_type="cash",
            customer_id=None,
            user_id=pharmacist.id,
            tenant_id=tenant.id,
        )
        invoice_id = invoice.id
        invoice_item_id = invoice.items[0].id
        product_id = product.id
        tenant_id = tenant.id
        pharmacist_id = pharmacist.id

    def attempt_return() -> int:
        with Session(engine) as worker_db:
            try:
                create_sales_return(
                    worker_db,
                    _return_request(
                        invoice_id=invoice_id,
                        invoice_item_id=invoice_item_id,
                        quantity=1,
                        condition="unknown",
                    ),
                    pharmacist_id,
                    tenant_id,
                    "pharmacist",
                )
                return status.HTTP_201_CREATED
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _: attempt_return(), range(2)))

    assert results == [status.HTTP_201_CREATED, status.HTTP_409_CONFLICT]
    with Session(engine) as db:
        assert db.scalar(func.count(SalesReturn.id)) == 1
        assert db.scalar(func.count(SalesReturnItem.id)) == 1
        assert db.scalar(func.count(QuarantineItem.id)) == 1
        assert db.get(Product, product_id).total_parts == 0

    engine.dispose()
