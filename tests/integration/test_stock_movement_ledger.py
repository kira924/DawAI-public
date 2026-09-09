import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.modules.inventory.crud import create_product
from src.modules.inventory.ledger import reconcile_stock
from src.modules.inventory.models import Product, ProductBatch, StockMovement
from src.modules.inventory.schemas import ProductCreate
from src.modules.purchases.crud import (
    create_purchase_order,
    create_purchase_return,
    receive_purchase_order,
)
from src.modules.purchases.models import Supplier
from src.modules.purchases.schemas import PurchaseOrderCreate, PurchaseReturnCreate
from src.modules.sales.crud import create_invoice, create_sales_return
from src.modules.sales.models import Invoice, QuarantineItem
from src.modules.sales.schemas import InvoiceCreate, SalesReturnCreate
from src.modules.shifts.models import Shift
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


def _seed_operator(db: Session, suffix: str = "primary") -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Stock Ledger Pharmacy {suffix}",
        owner_email=f"stock-ledger-owner-{suffix}@example.test",
    )
    db.add(tenant)
    db.flush()
    user = User(
        email=f"stock-ledger-user-{suffix}@example.test",
        hashed_password="synthetic-hash",
        full_name="Stock Ledger Operator",
        role="manager",
        tenant_id=tenant.id,
    )
    db.add(user)
    db.flush()
    tenant.owner_user_id = user.id
    db.commit()
    return tenant, user


@pytest.mark.integration
def test_all_current_stock_paths_write_one_reconcilable_ledger(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user = _seed_operator(db)
        db.add(Shift(opening_balance=0, user_id=user.id, tenant_id=tenant.id))
        db.commit()

        product = create_product(
            db,
            ProductCreate.model_validate(
                {
                    "name": "Ledger Product",
                    "price": "20.00",
                    "initial_boxes": 5,
                    "initial_batch": {
                        "batch_number": "LEDGER-OPENING",
                        "expiry_date": "2030-01-01",
                    },
                }
            ),
            tenant.id,
            actor_role=user.role,
            actor_user_id=user.id,
        )
        opening_batch = db.query(ProductBatch).filter(ProductBatch.product_id == product.id).one()

        purchase = create_purchase_order(
            db,
            PurchaseOrderCreate.model_validate(
                {
                    "supplier_id": _create_supplier(db, tenant.id),
                    "items": [
                        {
                            "product_id": product.id,
                            "quantity": 3,
                            "base_cost": "10.00",
                            "discount_percentage": "0",
                            "batch_number": "LEDGER-PURCHASE",
                            "expiry_date": "2031-01-01",
                        }
                    ],
                }
            ),
            user.id,
            tenant.id,
        )
        receive_purchase_order(db, purchase.id, tenant.id, user_id=user.id)
        purchase_item = purchase.items[0]

        invoice_request = InvoiceCreate.model_validate(
            {
                "idempotency_key": str(uuid4()),
                "payment_type": "cash",
                "items": [
                    {
                        "product_id": product.id,
                        "quantity": 2,
                        "sale_unit_price": "20.00",
                    }
                ],
            }
        )
        invoice = create_invoice(
            db,
            invoice_request,
            user.id,
            tenant.id,
        )
        replay = create_invoice(db, invoice_request, user.id, tenant.id)
        assert replay.id == invoice.id
        conflicting_request = invoice_request.model_copy(deep=True)
        conflicting_request.items[0].quantity = 3
        with pytest.raises(HTTPException) as conflict:
            create_invoice(db, conflicting_request, user.id, tenant.id)
        assert conflict.value.status_code == status.HTTP_409_CONFLICT
        invoice_item = invoice.items[0]

        create_sales_return(
            db,
            SalesReturnCreate.model_validate(
                {
                    "invoice_id": invoice.id,
                    "idempotency_key": str(uuid4()),
                    "items": [
                        {
                            "invoice_item_id": invoice_item.id,
                            "batch_id": opening_batch.id,
                            "quantity": 1,
                            "condition": "sealed",
                        }
                    ],
                }
            ),
            user.id,
            tenant.id,
            user.role,
        )
        create_sales_return(
            db,
            SalesReturnCreate.model_validate(
                {
                    "invoice_id": invoice.id,
                    "idempotency_key": str(uuid4()),
                    "items": [
                        {
                            "invoice_item_id": invoice_item.id,
                            "quantity": 1,
                            "condition": "opened_or_damaged",
                        }
                    ],
                }
            ),
            user.id,
            tenant.id,
            user.role,
        )
        create_purchase_return(
            db,
            PurchaseReturnCreate.model_validate(
                {
                    "purchase_order_id": purchase.id,
                    "idempotency_key": str(uuid4()),
                    "items": [{"purchase_item_id": purchase_item.id, "quantity": 1}],
                }
            ),
            user.id,
            tenant.id,
        )

        movements = (
            db.query(StockMovement)
            .filter(StockMovement.tenant_id == tenant.id)
            .order_by(StockMovement.id.asc())
            .all()
        )
        assert [(movement.event_type, movement.quantity_delta) for movement in movements] == [
            ("opening_stock", 5),
            ("purchase_receipt", 3),
            ("sale", -2),
            ("sales_return_restock", 1),
            ("sales_return_quarantine", 1),
            ("purchase_return", -1),
        ]
        assert len({movement.source_key for movement in movements}) == len(movements)
        assert all(movement.actor_user_id == user.id for movement in movements)
        assert db.get(Product, product.id).total_parts == 6
        assert db.query(QuarantineItem).filter_by(product_id=product.id).one().quantity == 1

        report = reconcile_stock(db, tenant.id)
        assert report.checked_products == 1
        assert report.checked_batches == 2
        assert report.issue_count == 0
        assert report.issues == []

    engine.dispose()


def _create_supplier(db: Session, tenant_id: int) -> int:
    supplier = Supplier(name="Stock Ledger Supplier", tenant_id=tenant_id)
    db.add(supplier)
    db.commit()
    return supplier.id


@pytest.mark.integration
def test_stock_movement_constraints_enforce_tenant_context_and_append_only_history(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant_a, user_a = _seed_operator(db, "a")
        tenant_b, _ = _seed_operator(db, "b")
        product = Product(name="Constraint Product", price=10, total_parts=1, tenant_id=tenant_a.id)
        db.add(product)
        db.flush()
        batch = ProductBatch(
            batch_number="CONSTRAINT-BATCH",
            expiry_date=date(2030, 1, 1),
            quantity=1,
            product_id=product.id,
            tenant_id=tenant_a.id,
        )
        db.add(batch)
        db.flush()
        movement = StockMovement(
            event_type="opening_stock",
            stock_bucket="on_hand",
            quantity_delta=1,
            source_type="product",
            source_id=product.id,
            source_line_id=batch.id,
            source_key=(f"opening-stock:product:{product.id}:batch:{batch.id}"),
            actor_user_id=user_a.id,
            product_id=product.id,
            batch_id=batch.id,
            tenant_id=tenant_a.id,
        )
        db.add(movement)
        db.commit()

        db.add(
            Invoice(
                idempotency_key="",
                request_fingerprint="0" * 64,
                total_amount=10,
                outstanding_amount=0,
                payment_type="cash",
                user_id=user_a.id,
                tenant_id=tenant_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            Invoice(
                idempotency_key=str(uuid4()),
                request_fingerprint="0" * 63,
                total_amount=10,
                outstanding_amount=0,
                payment_type="cash",
                user_id=user_a.id,
                tenant_id=tenant_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            StockMovement(
                event_type="opening_stock",
                stock_bucket="on_hand",
                quantity_delta=1,
                source_type="product",
                source_id=product.id,
                source_line_id=batch.id,
                source_key="",
                actor_user_id=user_a.id,
                product_id=product.id,
                batch_id=batch.id,
                tenant_id=tenant_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            StockMovement(
                event_type="opening_stock",
                stock_bucket="on_hand",
                quantity_delta=1,
                source_type="product",
                source_id=product.id,
                source_line_id=batch.id,
                source_key="forged-opening-key",
                actor_user_id=user_a.id,
                product_id=product.id,
                batch_id=batch.id,
                tenant_id=tenant_a.id,
            )
        )
        with pytest.raises(DBAPIError, match="source context is invalid"):
            db.commit()
        db.rollback()

        db.add(
            StockMovement(
                event_type="migration_on_hand_balance",
                stock_bucket="on_hand",
                quantity_delta=1,
                source_type="migration",
                source_id=None,
                source_line_id=None,
                source_key="forged-migration-baseline",
                actor_user_id=None,
                product_id=product.id,
                batch_id=batch.id,
                tenant_id=tenant_a.id,
            )
        )
        with pytest.raises(DBAPIError, match="initialization-only"):
            db.commit()
        db.rollback()

        movement.quantity_delta = 2
        with pytest.raises(DBAPIError, match="append-only"):
            db.commit()
        db.rollback()

        db.delete(db.get(StockMovement, movement.id))
        with pytest.raises(DBAPIError, match="append-only"):
            db.commit()
        db.rollback()

        db.add(
            StockMovement(
                event_type="opening_stock",
                stock_bucket="on_hand",
                quantity_delta=1,
                source_type="product",
                source_id=product.id,
                source_line_id=batch.id,
                source_key="constraint-cross-tenant",
                actor_user_id=user_a.id,
                product_id=product.id,
                batch_id=batch.id,
                tenant_id=tenant_b.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    engine.dispose()


@pytest.mark.integration
def test_invoice_idempotency_serializes_concurrent_stock_mutations(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user = _seed_operator(db, "concurrent")
        db.add(Shift(opening_balance=0, user_id=user.id, tenant_id=tenant.id))
        db.commit()
        product = create_product(
            db,
            ProductCreate.model_validate(
                {
                    "name": "Concurrent Ledger Product",
                    "price": "10.00",
                    "initial_boxes": 2,
                    "initial_batch": {
                        "batch_number": "CONCURRENT-LEDGER",
                        "expiry_date": "2030-01-01",
                    },
                }
            ),
            tenant.id,
            actor_role=user.role,
            actor_user_id=user.id,
        )
        tenant_id = tenant.id
        user_id = user.id
        product_id = product.id

    request = InvoiceCreate.model_validate(
        {
            "idempotency_key": str(uuid4()),
            "payment_type": "cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1,
                    "sale_unit_price": "10.00",
                }
            ],
        }
    )

    def submit() -> int:
        with Session(engine) as worker_db:
            return create_invoice(worker_db, request, user_id, tenant_id).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        invoice_ids = list(executor.map(lambda _: submit(), range(2)))

    assert invoice_ids[0] == invoice_ids[1]
    with Session(engine) as db:
        assert db.get(Product, product_id).total_parts == 1
        assert (
            db.query(StockMovement)
            .filter(
                StockMovement.tenant_id == tenant_id,
                StockMovement.event_type == "sale",
            )
            .count()
            == 1
        )
        assert reconcile_stock(db, tenant_id).issue_count == 0

    engine.dispose()


@pytest.mark.integration
def test_reconciliation_reports_direct_balance_tampering(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, user = _seed_operator(db, "reconciliation")
        product = create_product(
            db,
            ProductCreate.model_validate(
                {
                    "name": "Reconciliation Product",
                    "price": "10.00",
                    "initial_boxes": 2,
                    "initial_batch": {
                        "batch_number": "RECONCILIATION-BATCH",
                        "expiry_date": "2030-01-01",
                    },
                }
            ),
            tenant.id,
            actor_role=user.role,
            actor_user_id=user.id,
        )
        batch = db.query(ProductBatch).filter(ProductBatch.product_id == product.id).one()
        batch.quantity = 1
        db.commit()

        report = reconcile_stock(db, tenant.id)
        assert report.issue_count == 2
        assert {issue.issue_type for issue in report.issues} == {
            "batch_ledger_mismatch",
            "product_batch_mismatch",
        }

    engine.dispose()


@pytest.mark.integration
def test_stock_ledger_migration_backfills_current_balance_and_fails_closed_on_drift(
    empty_postgresql_database_url: str,
) -> None:
    baseline = _run_alembic(empty_postgresql_database_url, "upgrade", "5f2b8c4d7a91")
    assert baseline.returncode == 0, baseline.stderr
    engine = create_engine(empty_postgresql_database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Ledger Migration Pharmacy', 'ledger-migration@example.test', FALSE) "
                "RETURNING id"
            )
        )
        product_id = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) "
                "VALUES ('Ledger Migration Product', 'MEDICINE', 10, 4, :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        batch_id = connection.scalar(
            text(
                "INSERT INTO product_batches "
                "(batch_number, expiry_date, quantity, product_id, tenant_id) "
                "VALUES ('LEDGER-MIGRATION', DATE '2030-01-01', 3, :product_id, :tenant_id) "
                "RETURNING id"
            ),
            {"product_id": product_id, "tenant_id": tenant_id},
        )

    failed = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert failed.returncode != 0
    assert "product and batch balances differ" in failed.stderr

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE products SET total_parts = 3 WHERE id = :product_id"),
            {"product_id": product_id},
        )
    upgraded = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    with engine.connect() as connection:
        movement = connection.execute(
            text("SELECT event_type, stock_bucket, quantity_delta, batch_id FROM stock_movements")
        ).one()
        assert movement == ("migration_on_hand_balance", "on_hand", 3, batch_id)

    engine.dispose()


@pytest.mark.integration
def test_stock_ledger_migration_round_trip(empty_postgresql_database_url: str) -> None:
    upgraded = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    downgraded = _run_alembic(
        empty_postgresql_database_url,
        "downgrade",
        "5f2b8c4d7a91",
    )
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded_again = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded_again.returncode == 0, upgraded_again.stderr
    drift = _run_alembic(empty_postgresql_database_url, "check")
    assert drift.returncode == 0, drift.stderr
