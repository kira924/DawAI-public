import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.core.business_time import business_day_utc_bounds, local_business_date
from src.modules.expenses.models import Expense
from src.modules.finance.ledger import credit, debit, record_financial_transaction
from src.modules.inventory.crud import create_product, get_product
from src.modules.inventory.models import (
    NearExpiryReceiptConfirmation,
    Product,
    ProductBatch,
)
from src.modules.inventory.schemas import ProductCreate
from src.modules.purchases.crud import create_purchase_order, receive_purchase_order
from src.modules.purchases.models import PurchaseOrder, Supplier
from src.modules.purchases.schemas import PurchaseOrderCreate
from src.modules.reports.crud import get_daily_dashboard_summary
from src.modules.sales.crud import create_invoice, create_sales_return
from src.modules.sales.models import (
    Invoice,
    InvoiceItemBatchAllocation,
    QuarantineItem,
)
from src.modules.sales.schemas import InvoiceCreate, SalesReturnCreate
from src.modules.shifts.crud import close_shift, open_shift
from src.modules.shifts.models import Shift
from src.modules.shifts.schemas import ShiftClose, ShiftCreate
from src.modules.tenants.models import Tenant
from src.modules.users.models import User


def _run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def _seed_users(db: Session) -> tuple[Tenant, User, User]:
    tenant = Tenant(name="Expiry Test Pharmacy", owner_email="expiry-owner@example.com")
    db.add(tenant)
    db.flush()
    manager = User(
        email=f"expiry-manager-{tenant.id}@example.com",
        hashed_password="synthetic-hash",
        full_name="Expiry Manager",
        role="manager",
        tenant_id=tenant.id,
    )
    pharmacist = User(
        email=f"expiry-pharmacist-{tenant.id}@example.com",
        hashed_password="synthetic-hash",
        full_name="Expiry Pharmacist",
        role="pharmacist",
        tenant_id=tenant.id,
    )
    db.add_all([manager, pharmacist])
    db.commit()
    return tenant, manager, pharmacist


@pytest.mark.integration
def test_sales_exclude_expired_batches_and_keep_same_day_stock_sellable(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    today = local_business_date()

    with Session(engine) as db:
        tenant, _, pharmacist = _seed_users(db)
        shift = Shift(opening_balance=0, user_id=pharmacist.id, tenant_id=tenant.id)
        product = Product(
            name="Expiry Allocation Product",
            price=10,
            total_parts=10,
            tenant_id=tenant.id,
        )
        db.add_all([shift, product])
        db.flush()
        expired_batch = ProductBatch(
            batch_number="EXPIRED",
            expiry_date=today - timedelta(days=1),
            quantity=5,
            product_id=product.id,
            tenant_id=tenant.id,
        )
        same_day_batch = ProductBatch(
            batch_number="SAME-DAY",
            expiry_date=today,
            quantity=2,
            product_id=product.id,
            tenant_id=tenant.id,
        )
        future_batch = ProductBatch(
            batch_number="FUTURE",
            expiry_date=today + timedelta(days=90),
            quantity=3,
            product_id=product.id,
            tenant_id=tenant.id,
        )
        db.add_all([expired_batch, same_day_batch, future_batch])
        db.commit()

        invoice = create_invoice(
            db,
            InvoiceCreate.model_validate(
                {
                    "payment_type": "cash",
                    "items": [
                        {"product_id": product.id, "quantity": 4, "sale_unit_price": "10.00"}
                    ],
                }
            ),
            pharmacist.id,
            tenant.id,
        )

        allocations = (
            db.query(InvoiceItemBatchAllocation)
            .filter(InvoiceItemBatchAllocation.invoice_id == invoice.id)
            .order_by(InvoiceItemBatchAllocation.id.asc())
            .all()
        )
        assert [(row.batch_id, row.quantity) for row in allocations] == [
            (same_day_batch.id, 2),
            (future_batch.id, 2),
        ]
        db.refresh(expired_batch)
        db.refresh(future_batch)
        assert expired_batch.quantity == 5
        assert future_batch.quantity == 1

        stock = get_product(db, product.id, tenant.id)
        assert stock is not None
        assert stock.total_parts == 6
        assert stock.sellable_parts == 1
        assert stock.expired_parts == 5

        with pytest.raises(HTTPException) as exc_info:
            create_invoice(
                db,
                InvoiceCreate.model_validate(
                    {
                        "payment_type": "cash",
                        "items": [
                            {
                                "product_id": product.id,
                                "quantity": 2,
                                "sale_unit_price": "10.00",
                            }
                        ],
                    }
                ),
                pharmacist.id,
                tenant.id,
            )
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert db.scalar(func.count(Invoice.id)) == 1

    engine.dispose()


@pytest.mark.integration
def test_purchase_receipt_requires_manager_confirmation_and_records_evidence(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    today = local_business_date()

    with Session(engine) as db:
        tenant, manager, pharmacist = _seed_users(db)
        supplier = Supplier(name="Expiry Supplier", tenant_id=tenant.id)
        product = Product(name="Receipt Product", price=15, total_parts=0, tenant_id=tenant.id)
        db.add_all([supplier, product])
        db.commit()
        order = create_purchase_order(
            db,
            PurchaseOrderCreate.model_validate(
                {
                    "supplier_id": supplier.id,
                    "items": [
                        {
                            "product_id": product.id,
                            "quantity": 4,
                            "base_cost": "8.00",
                            "batch_number": "NEAR-EXPIRY",
                            "expiry_date": (today + timedelta(days=15)).isoformat(),
                        }
                    ],
                }
            ),
            manager.id,
            tenant.id,
        )

        with pytest.raises(HTTPException) as missing_confirmation:
            receive_purchase_order(
                db, order.id, tenant.id, user_id=manager.id, actor_role="manager"
            )
        assert missing_confirmation.value.status_code == status.HTTP_409_CONFLICT

        with pytest.raises(HTTPException) as pharmacist_confirmation:
            receive_purchase_order(
                db,
                order.id,
                tenant.id,
                user_id=pharmacist.id,
                actor_role="pharmacist",
                confirm_near_expiry=True,
            )
        assert pharmacist_confirmation.value.status_code == status.HTTP_403_FORBIDDEN

        received = receive_purchase_order(
            db,
            order.id,
            tenant.id,
            user_id=manager.id,
            actor_role="manager",
            confirm_near_expiry=True,
        )
        confirmation = db.query(NearExpiryReceiptConfirmation).one()
        assert received.status == "RECEIVED"
        assert confirmation.source == "purchase_receipt"
        assert confirmation.confirmed_by_user_id == manager.id
        assert confirmation.purchase_order_id == order.id
        assert confirmation.quantity == 4

        expired_order = create_purchase_order(
            db,
            PurchaseOrderCreate.model_validate(
                {
                    "supplier_id": supplier.id,
                    "items": [
                        {
                            "product_id": product.id,
                            "quantity": 1,
                            "base_cost": "8.00",
                            "batch_number": "ALREADY-EXPIRED",
                            "expiry_date": (today - timedelta(days=1)).isoformat(),
                        }
                    ],
                }
            ),
            manager.id,
            tenant.id,
        )
        with pytest.raises(HTTPException) as expired_receipt:
            receive_purchase_order(
                db,
                expired_order.id,
                tenant.id,
                user_id=manager.id,
                actor_role="manager",
                confirm_near_expiry=True,
            )
        assert expired_receipt.value.status_code == status.HTTP_409_CONFLICT
        assert db.get(PurchaseOrder, expired_order.id).status == "PENDING"

    engine.dispose()


@pytest.mark.integration
def test_opening_stock_enforces_expiry_and_audits_manager_confirmation(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    today = local_business_date()

    with Session(engine) as db:
        tenant, manager, pharmacist = _seed_users(db)

        def product_payload(expiry_date: date, *, confirm: bool) -> ProductCreate:
            return ProductCreate.model_validate(
                {
                    "name": f"Opening Stock {expiry_date.isoformat()} {confirm}",
                    "price": "10.00",
                    "initial_boxes": 2,
                    "initial_batch": {
                        "batch_number": f"OPENING-{expiry_date.isoformat()}-{confirm}",
                        "expiry_date": expiry_date.isoformat(),
                    },
                    "confirm_near_expiry": confirm,
                }
            )

        with pytest.raises(HTTPException) as missing_confirmation:
            create_product(
                db,
                product_payload(today + timedelta(days=30), confirm=False),
                tenant.id,
                actor_role="manager",
                actor_user_id=manager.id,
            )
        assert missing_confirmation.value.status_code == status.HTTP_409_CONFLICT

        with pytest.raises(HTTPException) as pharmacist_confirmation:
            create_product(
                db,
                product_payload(today + timedelta(days=30), confirm=True),
                tenant.id,
                actor_role="pharmacist",
                actor_user_id=pharmacist.id,
            )
        assert pharmacist_confirmation.value.status_code == status.HTTP_403_FORBIDDEN

        product = create_product(
            db,
            product_payload(today + timedelta(days=30), confirm=True),
            tenant.id,
            actor_role="manager",
            actor_user_id=manager.id,
        )
        confirmation = db.query(NearExpiryReceiptConfirmation).one()
        assert product.sellable_parts == 2
        assert confirmation.source == "opening_stock"
        assert confirmation.confirmed_by_user_id == manager.id
        assert confirmation.purchase_order_id is None

        with pytest.raises(HTTPException) as expired_stock:
            create_product(
                db,
                product_payload(today - timedelta(days=1), confirm=True),
                tenant.id,
                actor_role="manager",
                actor_user_id=manager.id,
            )
        assert expired_stock.value.status_code == status.HTTP_409_CONFLICT
        assert db.scalar(func.count(Product.id)) == 1

    engine.dispose()


@pytest.mark.integration
def test_sealed_return_to_now_expired_batch_is_quarantined(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    today = local_business_date()

    with Session(engine) as db:
        tenant, _, pharmacist = _seed_users(db)
        shift = Shift(opening_balance=0, user_id=pharmacist.id, tenant_id=tenant.id)
        product = Product(name="Return Product", price=12, total_parts=2, tenant_id=tenant.id)
        db.add_all([shift, product])
        db.flush()
        batch = ProductBatch(
            batch_number="RETURN-BATCH",
            expiry_date=today + timedelta(days=1),
            quantity=2,
            product_id=product.id,
            tenant_id=tenant.id,
        )
        db.add(batch)
        db.commit()
        invoice = create_invoice(
            db,
            InvoiceCreate.model_validate(
                {"items": [{"product_id": product.id, "quantity": 1, "sale_unit_price": "12.00"}]}
            ),
            pharmacist.id,
            tenant.id,
        )
        invoice_item = invoice.items[0]
        batch.expiry_date = today - timedelta(days=1)
        db.commit()

        sales_return = create_sales_return(
            db,
            SalesReturnCreate.model_validate(
                {
                    "invoice_id": invoice.id,
                    "idempotency_key": str(uuid4()),
                    "items": [
                        {
                            "invoice_item_id": invoice_item.id,
                            "batch_id": batch.id,
                            "quantity": 1,
                            "condition": "sealed",
                        }
                    ],
                }
            ),
            pharmacist.id,
            tenant.id,
            "pharmacist",
        )

        db.refresh(product)
        db.refresh(batch)
        quarantine = db.query(QuarantineItem).one()
        assert sales_return.items[0].disposition == "quarantine"
        assert product.total_parts == 1
        assert batch.quantity == 1
        assert quarantine.condition == "sealed"
        assert quarantine.reason == "expired_batch"
        assert quarantine.quantity == 1

    engine.dispose()


@pytest.mark.integration
def test_dashboard_uses_cairo_half_open_bounds_and_sellable_stock(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    report_date = date(2026, 9, 4)
    day_start, day_end = business_day_utc_bounds(report_date)

    with Session(engine) as db:
        tenant, manager, _ = _seed_users(db)
        shift = Shift(opening_balance=0, user_id=manager.id, tenant_id=tenant.id)
        expired_product = Product(
            name="Expired Low Stock",
            price=10,
            total_parts=100,
            reorder_point_parts=9,
            target_stock_parts=20,
            tenant_id=tenant.id,
        )
        stocked_product = Product(
            name="Sellable Stock",
            price=10,
            total_parts=20,
            tenant_id=tenant.id,
        )
        db.add_all([shift, expired_product, stocked_product])
        db.flush()
        db.add_all(
            [
                ProductBatch(
                    batch_number="REPORT-EXPIRED",
                    expiry_date=report_date - timedelta(days=1),
                    quantity=100,
                    product_id=expired_product.id,
                    tenant_id=tenant.id,
                ),
                ProductBatch(
                    batch_number="REPORT-VALID",
                    expiry_date=report_date,
                    quantity=20,
                    product_id=stocked_product.id,
                    tenant_id=tenant.id,
                ),
            ]
        )
        for occurred_at, amount in [
            (day_start - timedelta(microseconds=1), 100),
            (day_start, 10),
            (day_end - timedelta(microseconds=1), 20),
            (day_end, 100),
        ]:
            db.add(
                Invoice(
                    total_amount=amount,
                    outstanding_amount=0,
                    payment_type="cash",
                    created_at=occurred_at,
                    user_id=manager.id,
                    tenant_id=tenant.id,
                    shift_id=shift.id,
                )
            )
        db.add(
            Expense(
                amount=5,
                description="In-day expense",
                created_at=day_start + timedelta(hours=1),
                shift_id=shift.id,
                user_id=manager.id,
                tenant_id=tenant.id,
            )
        )
        db.commit()

        inventory_now = day_end + timedelta(hours=12)
        summary = get_daily_dashboard_summary(db, tenant.id, report_date, now=inventory_now)
        assert summary == {
            "business_date": report_date,
            "inventory_date": date(2026, 9, 5),
            "timezone": "Africa/Cairo",
            "gross_sales": Decimal("30.00"),
            "sales_returns": Decimal("0.00"),
            "net_sales": Decimal("30.00"),
            "operating_expenses": Decimal("5.00"),
            "operating_result_before_cogs_and_tax": Decimal("25.00"),
            "total_sales": Decimal("30.00"),
            "total_expenses": Decimal("5.00"),
            "total_returns": Decimal("0.00"),
            "net_revenue": Decimal("25.00"),
            "low_stock_count": 1,
        }

    engine.dispose()


@pytest.mark.integration
def test_shift_close_includes_transactions_across_cairo_midnight(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    midnight, _ = business_day_utc_bounds(date(2026, 9, 4))

    with Session(engine) as db:
        tenant, manager, _ = _seed_users(db)
        shift = Shift(
            opening_balance=10,
            start_time=midnight - timedelta(hours=1),
            user_id=manager.id,
            tenant_id=tenant.id,
        )
        db.add(shift)
        db.flush()
        invoices = [
            Invoice(
                total_amount=20,
                outstanding_amount=0,
                payment_type="cash",
                created_at=midnight - timedelta(minutes=1),
                user_id=manager.id,
                tenant_id=tenant.id,
                shift_id=shift.id,
            ),
            Invoice(
                total_amount=30,
                outstanding_amount=0,
                payment_type="cash",
                created_at=midnight + timedelta(minutes=1),
                user_id=manager.id,
                tenant_id=tenant.id,
                shift_id=shift.id,
            ),
        ]
        db.add_all(invoices)
        db.flush()
        for invoice in invoices:
            record_financial_transaction(
                db,
                event_type="sale",
                source_type="invoice",
                source_id=invoice.id,
                source_key=f"invoice:{invoice.id}",
                actor_user_id=manager.id,
                shift_id=shift.id,
                tenant_id=tenant.id,
                lines=[
                    debit("cash_drawer", invoice.total_amount),
                    credit("sales_revenue", invoice.total_amount),
                ],
            )
        db.commit()

        closed = close_shift(
            db,
            ShiftClose(actual_closing_balance=60),
            manager.id,
            tenant.id,
        )
        assert closed.expected_closing_balance == Decimal("60.00")
        assert closed.actual_closing_balance == Decimal("60.00")
        assert closed.difference == Decimal("0.00")
        assert closed.status == "CLOSED"

    engine.dispose()


@pytest.mark.integration
def test_concurrent_shift_open_allows_only_one_active_shift(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, _ = _seed_users(db)
        tenant_id = tenant.id
        manager_id = manager.id

    def attempt_open() -> str:
        with Session(engine) as session:
            try:
                open_shift(session, ShiftCreate(opening_balance=0), manager_id, tenant_id)
                return "opened"
            except HTTPException as error:
                assert error.status_code == status.HTTP_409_CONFLICT
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt_open(), range(2)))

    assert sorted(outcomes) == ["conflict", "opened"]
    with Session(engine) as db:
        assert (
            db.query(Shift)
            .filter(
                Shift.user_id == manager_id,
                Shift.tenant_id == tenant_id,
                Shift.status == "OPEN",
            )
            .count()
            == 1
        )

    engine.dispose()


@pytest.mark.integration
def test_shift_close_serializes_against_concurrent_sale(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    today = local_business_date()
    with Session(engine) as db:
        tenant, manager, _ = _seed_users(db)
        shift = Shift(opening_balance=0, user_id=manager.id, tenant_id=tenant.id)
        product = Product(name="Closing Race Product", price=10, total_parts=1, tenant_id=tenant.id)
        db.add_all([shift, product])
        db.flush()
        db.add(
            ProductBatch(
                batch_number="CLOSING-RACE",
                expiry_date=today + timedelta(days=365),
                quantity=1,
                product_id=product.id,
                tenant_id=tenant.id,
            )
        )
        db.commit()
        shift_id = shift.id
        product_id = product.id
        tenant_id = tenant.id
        manager_id = manager.id

    start = Barrier(2)

    def sell() -> str:
        with Session(engine) as session:
            start.wait()
            try:
                create_invoice(
                    session,
                    InvoiceCreate.model_validate(
                        {
                            "items": [
                                {
                                    "product_id": product_id,
                                    "quantity": 1,
                                    "sale_unit_price": "10.00",
                                }
                            ]
                        }
                    ),
                    manager_id,
                    tenant_id,
                )
                return "sold"
            except HTTPException as error:
                assert error.status_code == status.HTTP_400_BAD_REQUEST
                return "closed"

    def close() -> str:
        with Session(engine) as session:
            start.wait()
            close_shift(
                session,
                ShiftClose(actual_closing_balance=10),
                manager_id,
                tenant_id,
            )
            return "closed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        sale_future = executor.submit(sell)
        close_future = executor.submit(close)
        assert close_future.result() == "closed"
        assert sale_future.result() in {"sold", "closed"}

    with Session(engine) as db:
        invoice_total = (
            db.query(func.coalesce(func.sum(Invoice.total_amount), 0))
            .filter(Invoice.shift_id == shift_id)
            .scalar()
        )
        stored_shift = db.get(Shift, shift_id)
        assert stored_shift is not None
        assert stored_shift.status == "CLOSED"
        assert stored_shift.expected_closing_balance == invoice_total
        assert db.query(Shift).filter(Shift.id == shift_id, Shift.status == "OPEN").count() == 0

    engine.dispose()


@pytest.mark.integration
def test_expiry_and_shift_migration_objects_exist(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    inspector = inspect(engine)

    assert "near_expiry_receipt_confirmations" in inspector.get_table_names()
    assert "ix_product_batches_tenant_product_expiry_positive" in {
        index["name"] for index in inspector.get_indexes("product_batches")
    }
    assert "uq_shifts_user_open" in {index["name"] for index in inspector.get_indexes("shifts")}
    assert "ck_shifts_lifecycle_consistent" in {
        constraint["name"] for constraint in inspector.get_check_constraints("shifts")
    }

    engine.dispose()


@pytest.mark.integration
def test_database_rejects_cross_tenant_confirmation_and_invalid_shift_lifecycle(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    today = local_business_date()
    with Session(engine) as db:
        tenant_a, manager_a, _ = _seed_users(db)
        tenant_b, manager_b, _ = _seed_users(db)
        product = Product(name="Constraint Product", price=10, total_parts=1, tenant_id=tenant_a.id)
        shift = Shift(opening_balance=0, user_id=manager_a.id, tenant_id=tenant_a.id)
        db.add_all([product, shift])
        db.flush()
        batch = ProductBatch(
            batch_number="CONSTRAINT-BATCH",
            expiry_date=today + timedelta(days=10),
            quantity=1,
            product_id=product.id,
            tenant_id=tenant_a.id,
        )
        db.add(batch)
        db.commit()

        db.add(
            NearExpiryReceiptConfirmation(
                source="opening_stock",
                quantity=1,
                expiry_date=batch.expiry_date,
                batch_id=batch.id,
                product_id=product.id,
                purchase_order_id=None,
                confirmed_by_user_id=manager_b.id,
                tenant_id=tenant_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        stored_shift = db.get(Shift, shift.id)
        assert stored_shift is not None
        stored_shift.status = "CLOSED"
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        assert tenant_b.id != tenant_a.id

    engine.dispose()


@pytest.mark.integration
def test_expiry_and_shift_migration_round_trip(
    empty_postgresql_database_url: str,
) -> None:
    assert _run_alembic(empty_postgresql_database_url, "upgrade", "head").returncode == 0
    assert (
        _run_alembic(
            empty_postgresql_database_url,
            "downgrade",
            "4e8a1c7b2d90",
        ).returncode
        == 0
    )
    assert _run_alembic(empty_postgresql_database_url, "upgrade", "head").returncode == 0
    drift_check = _run_alembic(empty_postgresql_database_url, "check")
    assert drift_check.returncode == 0, drift_check.stderr


@pytest.mark.integration
def test_expiry_and_shift_migration_fails_closed_on_duplicate_open_shifts(
    empty_postgresql_database_url: str,
) -> None:
    baseline = _run_alembic(empty_postgresql_database_url, "upgrade", "4e8a1c7b2d90")
    assert baseline.returncode == 0, baseline.stderr
    engine = create_engine(empty_postgresql_database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, subscription_plan, is_active) "
                "VALUES ('Duplicate Shift Pharmacy', 'duplicate-shift@example.com', 'Free', FALSE) "
                "RETURNING id"
            )
        )
        user_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('duplicate-shift-user@example.com', 'synthetic-hash', "
                "'Duplicate Shift User', 'manager', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO shifts "
                "(status, start_time, opening_balance, expected_closing_balance, user_id, tenant_id) "
                "VALUES "
                "('OPEN', now(), 0, 0, :user_id, :tenant_id), "
                "('OPEN', now(), 0, 0, :user_id, :tenant_id)"
            ),
            {"user_id": user_id, "tenant_id": tenant_id},
        )

    result = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert result.returncode != 0
    assert "duplicate-open=1" in result.stderr
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "4e8a1c7b2d90"
    engine.dispose()
