import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import src.main  # noqa: F401
from src.core.database import get_db
from src.core.security import get_password_hash
from src.modules.inventory import control
from src.modules.inventory.crud import create_product
from src.modules.inventory.ledger import reconcile_stock
from src.modules.inventory.models import (
    Product,
    ProductBatch,
    QuarantineDisposition,
    StockCount,
    StockMovement,
)
from src.modules.inventory.schemas import (
    InitialBatchCreate,
    ProductCreate,
    QuarantineCondition,
    QuarantineCreate,
    QuarantineDispositionAction,
    QuarantineDispositionCreate,
    QuarantineReason,
    StockCountCancel,
    StockCountCreate,
    StockCountItemInput,
    StockCountUpdate,
    StockVarianceReason,
)
from src.modules.sales.models import QuarantineItem
from src.modules.tenants.models import Tenant
from src.modules.users.models import User

PASSWORD = "Synthetic-Controlled-Inventory-Password-123"


def _request(
    method: str,
    path: str,
    database_url: str,
    *,
    form_data: dict[str, str] | None = None,
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    engine = create_engine(database_url)

    def override_database() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=src.main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method,
                path,
                data=form_data,
                json=json_body,
                headers=headers,
            )

    src.main.app.dependency_overrides[get_db] = override_database
    try:
        return asyncio.run(request())
    finally:
        src.main.app.dependency_overrides.clear()
        engine.dispose()


def _authorization_header(database_url: str, email: str) -> dict[str, str]:
    response = _request(
        "POST",
        "/api/auth/login",
        database_url,
        form_data={"username": email, "password": PASSWORD},
    )
    assert response.status_code == status.HTTP_200_OK
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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


def _seed_inventory(db: Session) -> tuple[Tenant, User, User, Product, ProductBatch]:
    tenant = Tenant(
        name="Controlled Inventory Pharmacy",
        owner_email="controlled-manager@example.test",
        is_active=False,
    )
    db.add(tenant)
    db.flush()
    manager = User(
        email="controlled-manager@example.test",
        hashed_password=get_password_hash(PASSWORD),
        full_name="Controlled Inventory Manager",
        role="manager",
        tenant_id=tenant.id,
    )
    pharmacist = User(
        email="controlled-pharmacist@example.test",
        hashed_password=get_password_hash(PASSWORD),
        full_name="Controlled Inventory Pharmacist",
        role="pharmacist",
        tenant_id=tenant.id,
    )
    db.add_all([manager, pharmacist])
    db.flush()
    tenant.owner_user_id = manager.id
    tenant.is_active = True
    db.commit()
    product = create_product(
        db,
        ProductCreate(
            name="Controlled Inventory Product",
            price="25.00",
            initial_boxes=5,
            initial_batch=InitialBatchCreate(
                batch_number="CONTROLLED-1",
                expiry_date=date(2030, 1, 31),
            ),
        ),
        tenant.id,
        actor_role="manager",
        actor_user_id=manager.id,
    )
    batch = db.query(ProductBatch).filter_by(product_id=product.id).one()
    return tenant, manager, pharmacist, product, batch


@pytest.mark.integration
def test_controlled_inventory_http_workflow_enforces_roles_and_actor_evidence(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, _product, batch = _seed_inventory(db)
        tenant_id = tenant.id
        manager_id = manager.id
        pharmacist_id = pharmacist.id
        batch_id = batch.id
        manager_email = manager.email
        pharmacist_email = pharmacist.email
    engine.dispose()

    pharmacist_headers = _authorization_header(migrated_postgresql_database_url, pharmacist_email)
    manager_headers = _authorization_header(migrated_postgresql_database_url, manager_email)
    created = _request(
        "POST",
        "/api/products/stock-counts",
        migrated_postgresql_database_url,
        json_body={
            "count_type": "cycle",
            "items": [{"batch_id": batch_id, "counted_quantity": 4, "variance_reason": "loss"}],
        },
        headers=pharmacist_headers,
    )
    assert created.status_code == status.HTTP_201_CREATED
    count_id = created.json()["id"]

    updated = _request(
        "PATCH",
        f"/api/products/stock-counts/{count_id}",
        migrated_postgresql_database_url,
        json_body={
            "notes": "Second physical count",
            "items": [{"batch_id": batch_id, "counted_quantity": 3, "variance_reason": "loss"}],
        },
        headers=pharmacist_headers,
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["items"][0]["counted_quantity"] == 3

    submitted = _request(
        "POST",
        f"/api/products/stock-counts/{count_id}/submit",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    assert submitted.status_code == status.HTTP_200_OK
    assert submitted.json()["submitted_by_user_id"] == pharmacist_id

    denied = _request(
        "POST",
        f"/api/products/stock-counts/{count_id}/apply",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    applied = _request(
        "POST",
        f"/api/products/stock-counts/{count_id}/apply",
        migrated_postgresql_database_url,
        headers=manager_headers,
    )
    assert applied.status_code == status.HTTP_200_OK
    assert applied.json()["status"] == "applied"
    assert applied.json()["applied_by_user_id"] == manager_id
    assert applied.json()["tenant_id"] == tenant_id


@pytest.mark.integration
def test_stock_count_draft_can_be_cancelled_but_final_evidence_is_immutable(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, _product, batch = _seed_inventory(db)
        stock_count = control.create_stock_count(
            db,
            StockCountCreate(items=[StockCountItemInput(batch_id=batch.id, counted_quantity=5)]),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        updated = control.update_stock_count(
            db,
            stock_count.id,
            StockCountUpdate(
                notes="Verified shelf count",
                items=[StockCountItemInput(batch_id=batch.id, counted_quantity=5)],
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
            user_role=pharmacist.role,
        )
        assert updated.notes == "Verified shelf count"
        cancelled = control.cancel_stock_count(
            db,
            stock_count.id,
            StockCountCancel(reason="Duplicate count session"),
            tenant_id=tenant.id,
            user_id=manager.id,
            user_role=manager.role,
        )
        assert cancelled.status == "cancelled"
        assert cancelled.cancelled_by_user_id == manager.id

        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "UPDATE stock_count_items SET counted_quantity = 4 WHERE stock_count_id = :id"
                ),
                {"id": stock_count.id},
            )
            db.commit()
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM stock_counts WHERE id = :id"), {"id": stock_count.id})
            db.commit()
        db.rollback()

        unsafe = control.create_stock_count(
            db,
            StockCountCreate(
                items=[
                    StockCountItemInput(
                        batch_id=batch.id,
                        counted_quantity=4,
                        variance_reason=StockVarianceReason.LOSS,
                    )
                ]
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        db.execute(
            text(
                "UPDATE stock_counts SET status = 'submitted', submitted_at = now(), "
                "submitted_by_user_id = :manager_id WHERE id = :count_id"
            ),
            {"manager_id": manager.id, "count_id": unsafe.id},
        )
        db.commit()
        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "UPDATE stock_counts SET status = 'applied', applied_at = now(), "
                    "applied_by_user_id = :manager_id WHERE id = :count_id"
                ),
                {"manager_id": manager.id, "count_id": unsafe.id},
            )
            db.commit()
        db.rollback()
        assert db.get(StockCount, unsafe.id).status == "submitted"

    engine.dispose()


@pytest.mark.integration
def test_stock_count_applies_audited_variance_and_rejects_stale_snapshot(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, product, batch = _seed_inventory(db)
        payload = StockCountCreate(
            idempotency_key=uuid4(),
            items=[
                StockCountItemInput(
                    batch_id=batch.id,
                    counted_quantity=7,
                    variance_reason=StockVarianceReason.FOUND_STOCK,
                )
            ],
        )
        stock_count = control.create_stock_count(
            db, payload, tenant_id=tenant.id, user_id=pharmacist.id
        )
        replay = control.create_stock_count(db, payload, tenant_id=tenant.id, user_id=pharmacist.id)
        assert replay.id == stock_count.id

        control.submit_stock_count(
            db,
            stock_count.id,
            tenant_id=tenant.id,
            user_id=pharmacist.id,
            user_role=pharmacist.role,
        )
        applied = control.apply_stock_count(
            db,
            stock_count.id,
            tenant_id=tenant.id,
            user_id=manager.id,
            user_role=manager.role,
        )
        assert applied.status == "applied"
        assert db.get(ProductBatch, batch.id).quantity == 7
        assert db.get(Product, product.id).total_parts == 7
        movement = db.query(StockMovement).filter_by(event_type="stock_adjustment").one()
        assert movement.quantity_delta == 2
        assert movement.source_line_id == stock_count.items[0].id
        assert reconcile_stock(db, tenant.id).issue_count == 0

        stale = control.create_stock_count(
            db,
            StockCountCreate(
                items=[
                    StockCountItemInput(
                        batch_id=batch.id,
                        counted_quantity=6,
                        variance_reason=StockVarianceReason.LOSS,
                    )
                ]
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        newer = control.create_stock_count(
            db,
            StockCountCreate(
                items=[
                    StockCountItemInput(
                        batch_id=batch.id,
                        counted_quantity=8,
                        variance_reason=StockVarianceReason.FOUND_STOCK,
                    )
                ]
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        control.submit_stock_count(
            db,
            newer.id,
            tenant_id=tenant.id,
            user_id=pharmacist.id,
            user_role=pharmacist.role,
        )
        control.apply_stock_count(
            db,
            newer.id,
            tenant_id=tenant.id,
            user_id=manager.id,
            user_role=manager.role,
        )
        with pytest.raises(HTTPException) as exc_info:
            control.submit_stock_count(
                db,
                stale.id,
                tenant_id=tenant.id,
                user_id=pharmacist.id,
                user_role=pharmacist.role,
            )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert db.get(ProductBatch, batch.id).quantity == 8

    engine.dispose()


@pytest.mark.integration
def test_quarantine_disposition_preserves_buckets_and_release_policy(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, product, batch = _seed_inventory(db)
        quarantined = control.create_quarantine_item(
            db,
            QuarantineCreate(
                batch_id=batch.id,
                quantity=2,
                condition=QuarantineCondition.OPENED_OR_DAMAGED,
                reason=QuarantineReason.DAMAGE,
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        assert db.get(ProductBatch, batch.id).quantity == 3
        assert quarantined.quantity == 2

        disposed = control.create_quarantine_disposition(
            db,
            quarantined.id,
            QuarantineDispositionCreate(
                action=QuarantineDispositionAction.DISPOSE,
                quantity=1,
                reason="Damaged package approved for disposal",
            ),
            tenant_id=tenant.id,
            user_id=manager.id,
            user_role=manager.role,
        )
        assert disposed.action == "dispose"
        assert db.get(QuarantineItem, quarantined.id).quantity == 1
        with pytest.raises(HTTPException) as pharmacist_disposition:
            control.create_quarantine_disposition(
                db,
                quarantined.id,
                QuarantineDispositionCreate(
                    action=QuarantineDispositionAction.DISPOSE,
                    quantity=1,
                    reason="Unauthorized disposal attempt",
                ),
                tenant_id=tenant.id,
                user_id=pharmacist.id,
                user_role=pharmacist.role,
            )
        assert pharmacist_disposition.value.status_code == status.HTTP_403_FORBIDDEN
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE quarantine_dispositions SET reason = 'rewritten' WHERE id = :id"),
                {"id": disposed.id},
            )
            db.commit()
        db.rollback()
        db.add(
            QuarantineDisposition(
                quarantine_item_id=quarantined.id,
                action="dispose",
                quantity=1,
                reason="Direct pharmacist bypass attempt",
                idempotency_key=str(uuid4()),
                request_fingerprint="a" * 64,
                performed_by_user_id=pharmacist.id,
                product_id=quarantined.product_id,
                batch_id=quarantined.batch_id,
                tenant_id=tenant.id,
            )
        )
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        db.add(
            QuarantineDisposition(
                quarantine_item_id=quarantined.id,
                action="dispose",
                quantity=1,
                reason="Incomplete direct manager write",
                idempotency_key=str(uuid4()),
                request_fingerprint="b" * 64,
                performed_by_user_id=manager.id,
                product_id=quarantined.product_id,
                batch_id=quarantined.batch_id,
                tenant_id=tenant.id,
            )
        )
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        with pytest.raises(HTTPException) as opened_release:
            control.create_quarantine_disposition(
                db,
                quarantined.id,
                QuarantineDispositionCreate(
                    action=QuarantineDispositionAction.RELEASE,
                    quantity=1,
                    reason="Unsafe release attempt",
                ),
                tenant_id=tenant.id,
                user_id=manager.id,
                user_role=manager.role,
            )
        assert opened_release.value.status_code == status.HTTP_409_CONFLICT

        sealed = control.create_quarantine_item(
            db,
            QuarantineCreate(
                batch_id=batch.id,
                quantity=1,
                condition=QuarantineCondition.SEALED,
                reason=QuarantineReason.RECALL,
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        control.create_quarantine_disposition(
            db,
            sealed.id,
            QuarantineDispositionCreate(
                action=QuarantineDispositionAction.RELEASE,
                quantity=1,
                reason="Recall was withdrawn",
            ),
            tenant_id=tenant.id,
            user_id=manager.id,
            user_role=manager.role,
        )
        assert db.get(QuarantineItem, sealed.id).status == "resolved"
        assert db.get(ProductBatch, batch.id).quantity == 3
        assert reconcile_stock(db, tenant.id).issue_count == 0
        assert (
            db.query(func.sum(StockMovement.quantity_delta))
            .filter(StockMovement.stock_bucket == "quarantine")
            .scalar()
            == 1
        )

    engine.dispose()


@pytest.mark.integration
def test_concurrent_quarantine_dispositions_cannot_exceed_pending_quantity(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, _product, batch = _seed_inventory(db)
        quarantined = control.create_quarantine_item(
            db,
            QuarantineCreate(
                batch_id=batch.id,
                quantity=1,
                condition=QuarantineCondition.OPENED_OR_DAMAGED,
                reason=QuarantineReason.DAMAGE,
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        quarantine_id = quarantined.id
        tenant_id = tenant.id
        manager_id = manager.id

    def dispose() -> int:
        with Session(engine) as worker_db:
            try:
                control.create_quarantine_disposition(
                    worker_db,
                    quarantine_id,
                    QuarantineDispositionCreate(
                        action=QuarantineDispositionAction.DISPOSE,
                        quantity=1,
                        reason="Concurrent disposal approval",
                    ),
                    tenant_id=tenant_id,
                    user_id=manager_id,
                    user_role="manager",
                )
                return status.HTTP_201_CREATED
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _: dispose(), range(2)))

    assert results == [status.HTTP_201_CREATED, status.HTTP_409_CONFLICT]
    with Session(engine) as db:
        assert db.get(QuarantineItem, quarantine_id).quantity == 0
        assert db.query(QuarantineDisposition).count() == 1
        assert reconcile_stock(db, tenant_id).issue_count == 0
    engine.dispose()


@pytest.mark.integration
def test_expired_quarantine_cannot_be_released_and_direct_mutation_is_blocked(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, _product, batch = _seed_inventory(db)
        batch.expiry_date = date(2020, 1, 31)
        db.commit()
        quarantined = control.create_quarantine_item(
            db,
            QuarantineCreate(
                batch_id=batch.id,
                quantity=1,
                condition=QuarantineCondition.SEALED,
                reason=QuarantineReason.EXPIRED_BATCH,
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        with pytest.raises(HTTPException) as release:
            control.create_quarantine_disposition(
                db,
                quarantined.id,
                QuarantineDispositionCreate(
                    action=QuarantineDispositionAction.RELEASE,
                    quantity=1,
                    reason="Expired stock must stay unavailable",
                ),
                tenant_id=tenant.id,
                user_id=manager.id,
                user_role=manager.role,
            )
        assert release.value.status_code == status.HTTP_409_CONFLICT

        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE quarantine_items SET quantity = 0 WHERE id = :item_id"),
                {"item_id": quarantined.id},
            )
            db.commit()
        db.rollback()
        assert db.get(QuarantineItem, quarantined.id).quantity == 1

    engine.dispose()


@pytest.mark.integration
def test_controlled_inventory_is_tenant_scoped_and_idempotency_conflicts_are_atomic(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, _manager, pharmacist, _product, batch = _seed_inventory(db)
        foreign_tenant = Tenant(
            name="Foreign Controlled Pharmacy",
            owner_email="foreign-controlled@example.test",
            is_active=False,
        )
        db.add(foreign_tenant)
        db.commit()

        idempotency_key = uuid4()
        original = StockCountCreate(
            idempotency_key=idempotency_key,
            items=[StockCountItemInput(batch_id=batch.id, counted_quantity=5)],
        )
        stock_count = control.create_stock_count(
            db, original, tenant_id=tenant.id, user_id=pharmacist.id
        )
        conflicting = StockCountCreate(
            idempotency_key=idempotency_key,
            items=[
                StockCountItemInput(
                    batch_id=batch.id,
                    counted_quantity=4,
                    variance_reason=StockVarianceReason.LOSS,
                )
            ],
        )
        with pytest.raises(HTTPException) as conflict:
            control.create_stock_count(db, conflicting, tenant_id=tenant.id, user_id=pharmacist.id)
        assert conflict.value.status_code == status.HTTP_409_CONFLICT
        assert db.query(StockMovement).filter_by(event_type="stock_adjustment").count() == 0

        with pytest.raises(HTTPException) as foreign_count:
            control.submit_stock_count(
                db,
                stock_count.id,
                tenant_id=foreign_tenant.id,
                user_id=pharmacist.id,
                user_role=pharmacist.role,
            )
        assert foreign_count.value.status_code == status.HTTP_404_NOT_FOUND
        with pytest.raises(HTTPException) as foreign_quarantine:
            control.create_quarantine_item(
                db,
                QuarantineCreate(
                    batch_id=batch.id,
                    quantity=1,
                    condition=QuarantineCondition.SEALED,
                    reason=QuarantineReason.RECALL,
                ),
                tenant_id=foreign_tenant.id,
                user_id=pharmacist.id,
            )
        assert foreign_quarantine.value.status_code == status.HTTP_404_NOT_FOUND
        assert db.get(ProductBatch, batch.id).quantity == 5

    engine.dispose()


@pytest.mark.integration
def test_concurrent_stock_count_approval_applies_one_adjustment(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant, manager, pharmacist, product, batch = _seed_inventory(db)
        stock_count = control.create_stock_count(
            db,
            StockCountCreate(
                items=[
                    StockCountItemInput(
                        batch_id=batch.id,
                        counted_quantity=4,
                        variance_reason=StockVarianceReason.LOSS,
                    )
                ]
            ),
            tenant_id=tenant.id,
            user_id=pharmacist.id,
        )
        control.submit_stock_count(
            db,
            stock_count.id,
            tenant_id=tenant.id,
            user_id=pharmacist.id,
            user_role=pharmacist.role,
        )
        count_id = stock_count.id
        tenant_id = tenant.id
        manager_id = manager.id
        product_id = product.id
        batch_id = batch.id

    def approve() -> int:
        with Session(engine) as worker_db:
            return control.apply_stock_count(
                worker_db,
                count_id,
                tenant_id=tenant_id,
                user_id=manager_id,
                user_role="manager",
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: approve(), range(2)))

    assert results == [count_id, count_id]
    with Session(engine) as db:
        assert db.get(Product, product_id).total_parts == 4
        assert db.get(ProductBatch, batch_id).quantity == 4
        assert db.query(StockMovement).filter_by(event_type="stock_adjustment").count() == 1
        assert reconcile_stock(db, tenant_id).issue_count == 0
    engine.dispose()


@pytest.mark.integration
def test_controlled_inventory_migration_round_trip(
    empty_postgresql_database_url: str,
) -> None:
    upgraded = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    downgraded = _run_alembic(empty_postgresql_database_url, "downgrade", "6e1a9d3c4b72")
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded_again = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded_again.returncode == 0, upgraded_again.stderr
    drift = _run_alembic(empty_postgresql_database_url, "check")
    assert drift.returncode == 0, drift.stderr


@pytest.mark.integration
def test_controlled_inventory_migration_preserves_legacy_quarantine(
    empty_postgresql_database_url: str,
) -> None:
    baseline = _run_alembic(empty_postgresql_database_url, "upgrade", "6e1a9d3c4b72")
    assert baseline.returncode == 0, baseline.stderr
    engine = create_engine(empty_postgresql_database_url)
    with engine.begin() as connection:
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenants (name, owner_email, is_active) "
                "VALUES ('Legacy Quarantine Pharmacy', 'legacy-quarantine@example.test', FALSE) "
                "RETURNING id"
            )
        )
        user_id = connection.scalar(
            text(
                "INSERT INTO users "
                "(email, hashed_password, full_name, role, tenant_id, is_active) "
                "VALUES ('legacy-quarantine@example.test', 'synthetic-hash', "
                "'Legacy Quarantine User', 'pharmacist', :tenant_id, TRUE) RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        shift_id = connection.scalar(
            text(
                "INSERT INTO shifts "
                "(status, opening_balance, expected_closing_balance, user_id, tenant_id) "
                "VALUES ('OPEN', 0, 0, :user_id, :tenant_id) RETURNING id"
            ),
            {"user_id": user_id, "tenant_id": tenant_id},
        )
        product_id = connection.scalar(
            text(
                "INSERT INTO products (name, category, price, total_parts, tenant_id) "
                "VALUES ('Legacy Quarantine Product', 'MEDICINE', 10, 0, :tenant_id) "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id},
        )
        invoice_id = connection.scalar(
            text(
                "INSERT INTO invoices "
                "(idempotency_key, request_fingerprint, total_amount, outstanding_amount, "
                "payment_type, user_id, tenant_id, shift_id) VALUES "
                "('legacy-quarantine-invoice', repeat('1', 64), 10, 0, 'cash', "
                ":user_id, :tenant_id, :shift_id) RETURNING id"
            ),
            {"user_id": user_id, "tenant_id": tenant_id, "shift_id": shift_id},
        )
        invoice_item_id = connection.scalar(
            text(
                "INSERT INTO invoice_items "
                "(invoice_id, product_id, tenant_id, quantity, list_unit_price, "
                "sale_unit_price, subtotal) VALUES "
                "(:invoice_id, :product_id, :tenant_id, 1, 10, 10, 10) RETURNING id"
            ),
            {
                "invoice_id": invoice_id,
                "product_id": product_id,
                "tenant_id": tenant_id,
            },
        )
        return_id = connection.scalar(
            text(
                "INSERT INTO sales_returns "
                "(idempotency_key, request_fingerprint, total_refund, cash_refund, "
                "debt_reduction, credit_balance_increase, invoice_id, shift_id, user_id, "
                "tenant_id) VALUES ('legacy-quarantine-return', repeat('2', 64), "
                "10, 10, 0, 0, :invoice_id, :shift_id, :user_id, :tenant_id) RETURNING id"
            ),
            {
                "invoice_id": invoice_id,
                "shift_id": shift_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        )
        return_item_id = connection.scalar(
            text(
                "INSERT INTO sales_return_items "
                "(sales_return_id, invoice_id, invoice_item_id, product_id, tenant_id, "
                "quantity, condition, disposition, unit_refund, refund_amount) VALUES "
                "(:return_id, :invoice_id, :invoice_item_id, :product_id, :tenant_id, "
                "1, 'unknown', 'quarantine', 10, 10) RETURNING id"
            ),
            {
                "return_id": return_id,
                "invoice_id": invoice_id,
                "invoice_item_id": invoice_item_id,
                "product_id": product_id,
                "tenant_id": tenant_id,
            },
        )
        quarantine_id = connection.scalar(
            text(
                "INSERT INTO quarantine_items "
                "(sales_return_item_id, product_id, quantity, condition, reason, tenant_id) "
                "VALUES (:return_item_id, :product_id, 1, 'unknown', 'return_condition', "
                ":tenant_id) RETURNING id"
            ),
            {
                "return_item_id": return_item_id,
                "product_id": product_id,
                "tenant_id": tenant_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO stock_movements "
                "(event_type, stock_bucket, quantity_delta, source_type, source_id, "
                "source_line_id, source_key, actor_user_id, product_id, tenant_id) VALUES "
                "('sales_return_quarantine', 'quarantine', 1, 'sales_return', :return_id, "
                ":return_item_id, 'sales-return-quarantine:item:' || :return_item_id, "
                ":user_id, :product_id, :tenant_id)"
            ),
            {
                "return_id": return_id,
                "return_item_id": return_item_id,
                "user_id": user_id,
                "product_id": product_id,
                "tenant_id": tenant_id,
            },
        )

    upgraded = _run_alembic(empty_postgresql_database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT quantity, initial_quantity, status, idempotency_key, "
                "created_by_user_id FROM quarantine_items WHERE id = :quarantine_id"
            ),
            {"quarantine_id": quarantine_id},
        ).one()
        assert row == (1, 1, "pending", f"sales-return-item:{return_item_id}", user_id)
        assert (
            connection.scalar(
                text("SELECT count(*) FROM stock_movements WHERE stock_bucket = 'quarantine'")
            )
            == 1
        )
    engine.dispose()
