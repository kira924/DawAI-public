import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.modules.customers.crud import create_customer_payment
from src.modules.customers.schemas import CustomerPaymentCreate
from src.modules.expenses.crud import create_expense
from src.modules.expenses.models import Expense
from src.modules.expenses.schemas import ExpenseCreate
from src.modules.finance.ledger import reconcile_financial_ledger
from src.modules.finance.models import FinancialEntry, FinancialTransaction
from src.modules.inventory.crud import create_product
from src.modules.inventory.schemas import ProductCreate
from src.modules.purchases.crud import (
    create_purchase_order,
    create_purchase_return,
    create_supplier_payment,
    receive_purchase_order,
)
from src.modules.purchases.schemas import (
    PurchaseOrderCreate,
    PurchaseReturnCreate,
    SupplierPaymentCreate,
)
from src.modules.sales.crud import create_invoice, create_sales_return
from src.modules.sales.models import Invoice
from src.modules.sales.schemas import InvoiceCreate, SalesReturnCreate
from src.modules.shifts.crud import close_shift
from src.modules.shifts.schemas import ShiftClose
from tests.factories import PharmacyFactory


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


def _seed_financial_context(db: Session, suffix: str = "main") -> dict[str, object]:
    factory = PharmacyFactory(db, f"financial-{suffix}")
    tenant, user = factory.owned_tenant(
        name=f"Financial Ledger Pharmacy {suffix}",
        owner_email=f"financial-owner-{suffix}@example.test",
        owner_full_name="Financial Ledger Manager",
        hashed_password="synthetic-hash",
    )
    shift = factory.shift(user, opening_balance=Decimal("100.00"))
    customer = factory.customer(
        tenant,
        name="Ledger Customer",
        credit_limit=Decimal("500.00"),
    )
    supplier = factory.supplier(tenant, name="Ledger Supplier")
    db.commit()
    return {
        "tenant": tenant,
        "user": user,
        "shift": shift,
        "customer": customer,
        "supplier": supplier,
    }


@pytest.mark.integration
def test_all_money_paths_write_balanced_reconcilable_transactions(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        context = _seed_financial_context(db)
        tenant = context["tenant"]
        user = context["user"]
        customer = context["customer"]
        supplier = context["supplier"]

        product = create_product(
            db,
            ProductCreate.model_validate(
                {
                    "name": "Financial Ledger Product",
                    "price": "20.00",
                    "initial_boxes": 10,
                    "initial_batch": {
                        "batch_number": "FINANCIAL-OPENING",
                        "expiry_date": "2030-01-01",
                    },
                }
            ),
            tenant.id,
            actor_role=user.role,
            actor_user_id=user.id,
        )
        purchase = create_purchase_order(
            db,
            PurchaseOrderCreate.model_validate(
                {
                    "supplier_id": supplier.id,
                    "items": [
                        {
                            "product_id": product.id,
                            "quantity": 10,
                            "base_cost": "5.00",
                            "batch_number": "FINANCIAL-PURCHASE",
                            "expiry_date": "2031-01-01",
                        }
                    ],
                }
            ),
            user.id,
            tenant.id,
        )
        receive_purchase_order(db, purchase.id, tenant.id, user_id=user.id)

        create_supplier_payment(
            db,
            SupplierPaymentCreate.model_validate(
                {
                    "supplier_id": supplier.id,
                    "amount": "10.00",
                    "payment_channel": "external",
                    "idempotency_key": str(uuid4()),
                }
            ),
            user.id,
            tenant.id,
        )
        create_supplier_payment(
            db,
            SupplierPaymentCreate.model_validate(
                {
                    "supplier_id": supplier.id,
                    "amount": "5.00",
                    "payment_channel": "cash",
                    "idempotency_key": str(uuid4()),
                }
            ),
            user.id,
            tenant.id,
        )

        cash_invoice = create_invoice(
            db,
            InvoiceCreate.model_validate(
                {
                    "payment_type": "cash",
                    "items": [
                        {"product_id": product.id, "quantity": 1, "sale_unit_price": "20.00"}
                    ],
                }
            ),
            user.id,
            tenant.id,
        )
        credit_invoice = create_invoice(
            db,
            InvoiceCreate.model_validate(
                {
                    "payment_type": "credit",
                    "customer_id": customer.id,
                    "items": [
                        {"product_id": product.id, "quantity": 2, "sale_unit_price": "20.00"}
                    ],
                }
            ),
            user.id,
            tenant.id,
        )
        create_customer_payment(
            db,
            CustomerPaymentCreate(
                customer_id=customer.id,
                amount=Decimal("10.00"),
            ),
            user.id,
            tenant.id,
        )
        create_sales_return(
            db,
            SalesReturnCreate.model_validate(
                {
                    "invoice_id": credit_invoice.id,
                    "idempotency_key": str(uuid4()),
                    "items": [
                        {
                            "invoice_item_id": credit_invoice.items[0].id,
                            "batch_id": credit_invoice.items[0].batch_allocations[0].batch_id,
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
                    "invoice_id": cash_invoice.id,
                    "idempotency_key": str(uuid4()),
                    "items": [
                        {
                            "invoice_item_id": cash_invoice.items[0].id,
                            "batch_id": cash_invoice.items[0].batch_allocations[0].batch_id,
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
        create_expense(
            db,
            ExpenseCreate(amount=Decimal("5.00"), description="Synthetic drawer expense"),
            user.id,
            tenant.id,
        )
        create_purchase_return(
            db,
            PurchaseReturnCreate.model_validate(
                {
                    "purchase_order_id": purchase.id,
                    "idempotency_key": str(uuid4()),
                    "items": [{"purchase_item_id": purchase.items[0].id, "quantity": 1}],
                }
            ),
            user.id,
            tenant.id,
        )
        closed_shift = close_shift(
            db,
            ShiftClose(actual_closing_balance=Decimal("98.00")),
            user.id,
            tenant.id,
        )

        assert closed_shift.expected_closing_balance == Decimal("100.00")
        assert closed_shift.difference == Decimal("-2.00")
        assert customer.total_debt == Decimal("10.00")
        transactions = (
            db.query(FinancialTransaction).filter(FinancialTransaction.tenant_id == tenant.id).all()
        )
        assert {transaction.event_type for transaction in transactions} == {
            "sale",
            "sales_return",
            "customer_payment",
            "purchase_receipt",
            "purchase_return",
            "supplier_payment",
            "expense",
            "shift_adjustment",
        }
        for transaction in transactions:
            debit_total = sum(
                entry.amount for entry in transaction.entries if entry.entry_side == "debit"
            )
            credit_total = sum(
                entry.amount for entry in transaction.entries if entry.entry_side == "credit"
            )
            assert debit_total == credit_total

        report = reconcile_financial_ledger(db, tenant.id)
        assert report.issue_count == 0
        assert report.total_debits == report.total_credits

    engine.dispose()


@pytest.mark.integration
def test_reconciliation_detects_a_source_written_without_its_transaction(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        context = _seed_financial_context(db, "missing-source")
        tenant = context["tenant"]
        user = context["user"]
        shift = context["shift"]
        expense = Expense(
            amount=Decimal("7.00"),
            description="Direct source write",
            shift_id=shift.id,
            user_id=user.id,
            tenant_id=tenant.id,
        )
        db.add(expense)
        db.commit()

        report = reconcile_financial_ledger(db, tenant.id)

        assert report.issue_count == 1
        assert report.issues[0].issue_type == "missing_expense_transaction"
        assert report.issues[0].entity_id == expense.id
        assert report.issues[0].stored_amount == Decimal("7.00")
        assert report.issues[0].ledger_amount == Decimal("0.00")

    engine.dispose()


@pytest.mark.integration
def test_customer_payment_and_expense_replays_are_idempotent(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        context = _seed_financial_context(db, "idempotency")
        tenant = context["tenant"]
        user = context["user"]
        customer = context["customer"]
        customer.total_debt = Decimal("20.00")
        db.add(
            Invoice(
                total_amount=Decimal("20.00"),
                outstanding_amount=Decimal("20.00"),
                payment_type="credit",
                customer_id=customer.id,
                user_id=user.id,
                tenant_id=tenant.id,
            )
        )
        db.commit()

        payment_request = CustomerPaymentCreate(
            idempotency_key=uuid4(),
            customer_id=customer.id,
            amount=Decimal("10.00"),
        )
        payment = create_customer_payment(db, payment_request, user.id, tenant.id)
        assert create_customer_payment(db, payment_request, user.id, tenant.id).id == payment.id

        expense_request = ExpenseCreate(
            idempotency_key=uuid4(),
            amount=Decimal("3.00"),
            description="Idempotent expense",
        )
        expense = create_expense(db, expense_request, user.id, tenant.id)
        assert create_expense(db, expense_request, user.id, tenant.id).id == expense.id
        assert (
            db.query(FinancialTransaction)
            .filter(FinancialTransaction.tenant_id == tenant.id)
            .count()
            == 2
        )

        with pytest.raises(HTTPException) as conflict:
            create_expense(
                db,
                expense_request.model_copy(update={"amount": Decimal("4.00")}),
                user.id,
                tenant.id,
            )
        assert conflict.value.status_code == status.HTTP_409_CONFLICT

    engine.dispose()


@pytest.mark.integration
def test_concurrent_expense_replay_creates_one_cash_transaction(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        context = _seed_financial_context(db, "concurrent-expense")
        tenant_id = context["tenant"].id
        user_id = context["user"].id

    request = ExpenseCreate(
        idempotency_key=uuid4(),
        amount=Decimal("4.00"),
        description="Concurrent expense",
    )

    def submit() -> int:
        with Session(engine) as worker_db:
            return create_expense(worker_db, request, user_id, tenant_id).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        expense_ids = list(executor.map(lambda _: submit(), range(2)))

    assert expense_ids[0] == expense_ids[1]
    with Session(engine) as db:
        assert (
            db.query(FinancialTransaction)
            .filter(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.event_type == "expense",
            )
            .count()
            == 1
        )
    engine.dispose()


@pytest.mark.integration
def test_database_rejects_forged_unbalanced_and_mutated_journals(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        context = _seed_financial_context(db, "constraints")
        tenant = context["tenant"]
        user = context["user"]
        shift = context["shift"]

        forged = FinancialTransaction(
            event_type="expense",
            source_type="expense",
            source_id=999999,
            source_key="expense:999999",
            actor_user_id=user.id,
            shift_id=shift.id,
            tenant_id=tenant.id,
        )
        db.add(forged)
        db.flush()
        db.add_all(
            [
                FinancialEntry(
                    transaction_id=forged.id,
                    account_code="operating_expense",
                    entry_side="debit",
                    amount=Decimal("10.00"),
                    tenant_id=tenant.id,
                ),
                FinancialEntry(
                    transaction_id=forged.id,
                    account_code="cash_drawer",
                    entry_side="credit",
                    amount=Decimal("10.00"),
                    tenant_id=tenant.id,
                ),
            ]
        )
        with pytest.raises(DBAPIError, match="source context is invalid"):
            db.commit()
        db.rollback()

        expense = create_expense(
            db,
            ExpenseCreate(amount=Decimal("2.00"), description="Append-only probe"),
            user.id,
            tenant.id,
        )
        transaction = (
            db.query(FinancialTransaction)
            .filter(FinancialTransaction.source_key == f"expense:{expense.id}")
            .one()
        )
        transaction.entries[0].amount = Decimal("3.00")
        with pytest.raises(DBAPIError, match="append-only"):
            db.commit()
        db.rollback()

    engine.dispose()


@pytest.mark.integration
def test_financial_migration_fails_closed_on_shift_cash_drift(
    empty_postgresql_database_url: str,
) -> None:
    baseline = _run_alembic(empty_postgresql_database_url, "upgrade", "3a7c9e1f5b82")
    assert baseline.returncode == 0, baseline.stderr
    engine = create_engine(empty_postgresql_database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Financial Drift Pharmacy', 'financial-drift@example.test', FALSE) "
                "RETURNING id"
            )
        )
        user_id = connection.scalar(
            text(
                "INSERT INTO users (email, hashed_password, full_name, role, tenant_id) "
                "VALUES ('financial-drift-user@example.test', 'synthetic-hash', "
                "'Financial Drift User', 'manager', :tenant_id) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO shifts (status, start_time, end_time, opening_balance, "
                "expected_closing_balance, actual_closing_balance, difference, user_id, tenant_id) "
                "VALUES ('CLOSED', now(), now(), 10, 99, 99, 0, :user_id, :tenant_id)"
            ),
            {"user_id": user_id, "tenant_id": tenant_id},
        )
    engine.dispose()

    failed = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert failed.returncode != 0
    assert "shift cash differs" in failed.stderr

    engine = create_engine(empty_postgresql_database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "3a7c9e1f5b82"
    engine.dispose()


@pytest.mark.integration
def test_financial_migration_round_trip_and_metadata_drift(
    empty_postgresql_database_url: str,
) -> None:
    upgraded = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    downgraded = _run_alembic(
        empty_postgresql_database_url,
        "downgrade",
        "3a7c9e1f5b82",
    )
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded_again = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded_again.returncode == 0, upgraded_again.stderr
    drift = _run_alembic(empty_postgresql_database_url, "check")
    assert drift.returncode == 0, drift.stderr
