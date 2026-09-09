import asyncio
from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import get_password_hash
from src.main import app
from src.modules.inventory.models import Product
from src.modules.purchases.models import (
    PurchaseItem,
    PurchaseOrder,
    PurchaseReturn,
    Supplier,
    SupplierPayment,
)
from src.modules.tenants.models import Tenant
from src.modules.users.models import User

PASSWORD = "Synthetic-purchase-password-123"


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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method,
                path,
                data=form_data,
                json=json_body,
                headers=headers,
            )

    app.dependency_overrides[get_db] = override_database
    try:
        return asyncio.run(request())
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_purchase_tenants(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    password_hash = get_password_hash(PASSWORD)

    with Session(engine) as db:
        tenant_a = Tenant(
            name="Synthetic Purchase Pharmacy A",
            owner_email="purchase-owner-a@example.com",
            is_active=False,
        )
        tenant_b = Tenant(
            name="Synthetic Purchase Pharmacy B",
            owner_email="purchase-owner-b@example.com",
            is_active=False,
        )
        db.add_all([tenant_a, tenant_b])
        db.flush()

        owner_a = User(
            email="purchase-owner-a@example.com",
            hashed_password=password_hash,
            full_name="Synthetic Purchase Owner A",
            role="manager",
            tenant_id=tenant_a.id,
        )
        owner_b = User(
            email="purchase-owner-b@example.com",
            hashed_password=password_hash,
            full_name="Synthetic Purchase Owner B",
            role="manager",
            tenant_id=tenant_b.id,
        )
        db.add_all([owner_a, owner_b])
        db.flush()

        tenant_a.owner_user_id = owner_a.id
        tenant_a.is_active = True
        tenant_b.owner_user_id = owner_b.id
        tenant_b.is_active = True

        supplier_a = Supplier(name="Synthetic Supplier A", tenant_id=tenant_a.id)
        alternate_supplier_a = Supplier(
            name="Synthetic Alternate Supplier A",
            tenant_id=tenant_a.id,
        )
        supplier_b = Supplier(name="Synthetic Supplier B", tenant_id=tenant_b.id)
        product_a = Product(
            name="Synthetic Product A",
            price=10,
            total_parts=0,
            tenant_id=tenant_a.id,
        )
        product_b = Product(
            name="Synthetic Product B",
            price=10,
            total_parts=0,
            tenant_id=tenant_b.id,
        )
        db.add_all([supplier_a, alternate_supplier_a, supplier_b, product_a, product_b])
        db.flush()

        order_a = PurchaseOrder(
            status="PENDING",
            subtotal=10,
            total_discount=0,
            total_cost=10,
            supplier_id=supplier_a.id,
            user_id=owner_a.id,
            tenant_id=tenant_a.id,
        )
        order_b = PurchaseOrder(
            status="PENDING",
            subtotal=10,
            total_discount=0,
            total_cost=10,
            supplier_id=supplier_b.id,
            user_id=owner_b.id,
            tenant_id=tenant_b.id,
        )
        db.add_all([order_a, order_b])
        db.flush()
        item_a = PurchaseItem(
            purchase_id=order_a.id,
            product_id=product_a.id,
            tenant_id=tenant_a.id,
            quantity=1,
            base_cost=10,
            unit_cost=10,
            subtotal=10,
            batch_number="TENANT-A-BATCH",
            expiry_date=date(2030, 1, 1),
        )
        item_b = PurchaseItem(
            purchase_id=order_b.id,
            product_id=product_b.id,
            tenant_id=tenant_b.id,
            quantity=1,
            base_cost=10,
            unit_cost=10,
            subtotal=10,
            batch_number="TENANT-B-BATCH",
            expiry_date=date(2030, 1, 1),
        )
        db.add_all([item_a, item_b])
        db.flush()
        foreign_payment = SupplierPayment(
            supplier_id=supplier_b.id,
            amount=5,
            idempotency_key=str(uuid4()),
            request_fingerprint="a" * 64,
            user_id=owner_b.id,
            tenant_id=tenant_b.id,
        )
        foreign_return = PurchaseReturn(
            supplier_id=supplier_b.id,
            purchase_order_id=order_b.id,
            total_refund=5,
            idempotency_key=str(uuid4()),
            request_fingerprint="b" * 64,
            user_id=owner_b.id,
            tenant_id=tenant_b.id,
        )
        db.add_all([foreign_payment, foreign_return])
        db.commit()

        identifiers = {
            "tenant_a": tenant_a.id,
            "tenant_b": tenant_b.id,
            "supplier_a": supplier_a.id,
            "alternate_supplier_a": alternate_supplier_a.id,
            "supplier_b": supplier_b.id,
            "product_a": product_a.id,
            "product_b": product_b.id,
            "order_a": order_a.id,
            "order_b": order_b.id,
            "item_a": item_a.id,
            "item_b": item_b.id,
        }

    engine.dispose()
    return identifiers


def _authorization_header(database_url: str) -> dict[str, str]:
    response = _request(
        "POST",
        "/api/auth/login",
        database_url,
        form_data={
            "username": "purchase-owner-a@example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _purchase_order_payload(supplier_id: int, product_id: int) -> dict[str, object]:
    return {
        "supplier_id": supplier_id,
        "tax_percentage": 0,
        "items": [
            {
                "product_id": product_id,
                "quantity": 1,
                "base_cost": 10,
                "discount_percentage": 0,
                "batch_number": f"BATCH-{product_id}",
                "expiry_date": "2030-01-01",
            }
        ],
    }


def _purchase_return_payload(
    purchase_order_id: int,
    purchase_item_id: int,
) -> dict[str, object]:
    return {
        "purchase_order_id": purchase_order_id,
        "idempotency_key": str(uuid4()),
        "items": [
            {
                "purchase_item_id": purchase_item_id,
                "quantity": 1,
            }
        ],
    }


@pytest.mark.integration
def test_purchase_order_rejects_foreign_supplier_without_partial_write(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    response = _request(
        "POST",
        "/api/purchases/orders/",
        migrated_postgresql_database_url,
        json_body=_purchase_order_payload(
            identifiers["supplier_b"],
            identifiers["product_a"],
        ),
        headers=headers,
    )

    assert response.status_code == 404
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.query(PurchaseOrder).count() == 2
        assert db.query(PurchaseItem).count() == 2
    engine.dispose()


@pytest.mark.integration
def test_purchase_order_rejects_foreign_product_without_partial_write(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    response = _request(
        "POST",
        "/api/purchases/orders/",
        migrated_postgresql_database_url,
        json_body=_purchase_order_payload(
            identifiers["supplier_a"],
            identifiers["product_b"],
        ),
        headers=headers,
    )

    assert response.status_code == 404
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.query(PurchaseOrder).count() == 2
        assert db.query(PurchaseItem).count() == 2
    engine.dispose()


@pytest.mark.integration
def test_supplier_payment_rejects_foreign_supplier_without_partial_write(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    response = _request(
        "POST",
        "/api/purchases/payments/",
        migrated_postgresql_database_url,
        json_body={
            "supplier_id": identifiers["supplier_b"],
            "amount": 10,
            "payment_channel": "external",
            "idempotency_key": str(uuid4()),
        },
        headers=headers,
    )

    assert response.status_code == 404
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert (
            db.query(SupplierPayment)
            .filter(SupplierPayment.tenant_id == identifiers["tenant_a"])
            .count()
            == 0
        )
    engine.dispose()


@pytest.mark.integration
def test_purchase_return_rejects_foreign_relationships_without_stock_mutation(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    payloads = [
        _purchase_return_payload(
            identifiers["order_b"],
            identifiers["item_b"],
        ),
        _purchase_return_payload(
            identifiers["order_b"],
            identifiers["item_a"],
        ),
    ]
    for payload in payloads:
        response = _request(
            "POST",
            "/api/purchases/returns/",
            migrated_postgresql_database_url,
            json_body=payload,
            headers=headers,
        )
        assert response.status_code == 404

    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.get(Product, identifiers["product_a"]).total_parts == 0
        assert (
            db.query(PurchaseReturn)
            .filter(PurchaseReturn.tenant_id == identifiers["tenant_a"])
            .count()
            == 0
        )
    engine.dispose()


@pytest.mark.integration
def test_purchase_return_rejects_item_from_another_order_without_stock_mutation(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    response = _request(
        "POST",
        "/api/purchases/returns/",
        migrated_postgresql_database_url,
        json_body=_purchase_return_payload(
            identifiers["order_a"],
            identifiers["item_b"],
        ),
        headers=headers,
    )

    assert response.status_code == 409
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.get(Product, identifiers["product_a"]).total_parts == 0
        assert (
            db.query(PurchaseReturn)
            .filter(PurchaseReturn.tenant_id == identifiers["tenant_a"])
            .count()
            == 0
        )
    engine.dispose()


@pytest.mark.integration
def test_foreign_purchase_orders_are_not_visible_or_mutable(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    for method, suffix in (
        ("GET", ""),
        ("POST", "/receive"),
        ("POST", "/cancel"),
    ):
        response = _request(
            method,
            f"/api/purchases/orders/{identifiers['order_b']}{suffix}",
            migrated_postgresql_database_url,
            headers=headers,
        )
        assert response.status_code == 404


@pytest.mark.integration
def test_purchase_reads_exclude_foreign_tenant_resources(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    suppliers = _request(
        "GET",
        "/api/purchases/suppliers/",
        migrated_postgresql_database_url,
        headers=headers,
    )
    orders = _request(
        "GET",
        "/api/purchases/orders/",
        migrated_postgresql_database_url,
        headers=headers,
    )
    payments = _request(
        "GET",
        "/api/purchases/payments/",
        migrated_postgresql_database_url,
        headers=headers,
    )
    returns = _request(
        "GET",
        "/api/purchases/returns/",
        migrated_postgresql_database_url,
        headers=headers,
    )

    assert suppliers.status_code == 200
    assert {supplier["id"] for supplier in suppliers.json()} == {
        identifiers["supplier_a"],
        identifiers["alternate_supplier_a"],
    }
    assert orders.status_code == 200
    assert {order["id"] for order in orders.json()} == {identifiers["order_a"]}
    assert payments.status_code == 200
    assert payments.json() == []
    assert returns.status_code == 200
    assert returns.json() == []


@pytest.mark.integration
def test_foreign_suppliers_are_not_visible_or_mutable(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    requests = [
        ("GET", f"/api/purchases/suppliers/{identifiers['supplier_b']}", None),
        (
            "PATCH",
            f"/api/purchases/suppliers/{identifiers['supplier_b']}",
            {"name": "Unauthorized Supplier Rename"},
        ),
        (
            "GET",
            f"/api/purchases/suppliers/{identifiers['supplier_b']}/balance",
            None,
        ),
    ]
    for method, path, payload in requests:
        response = _request(
            method,
            path,
            migrated_postgresql_database_url,
            json_body=payload,
            headers=headers,
        )
        assert response.status_code == 404


@pytest.mark.integration
def test_same_tenant_purchase_order_and_payment_remain_available(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_purchase_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)

    order_response = _request(
        "POST",
        "/api/purchases/orders/",
        migrated_postgresql_database_url,
        json_body=_purchase_order_payload(
            identifiers["supplier_a"],
            identifiers["product_a"],
        ),
        headers=headers,
    )
    receive_response = _request(
        "POST",
        f"/api/purchases/orders/{order_response.json()['id']}/receive",
        migrated_postgresql_database_url,
        headers=headers,
    )
    payment_response = _request(
        "POST",
        "/api/purchases/payments/",
        migrated_postgresql_database_url,
        json_body={
            "supplier_id": identifiers["supplier_a"],
            "amount": 10,
            "payment_channel": "external",
            "idempotency_key": str(uuid4()),
        },
        headers=headers,
    )

    assert order_response.status_code == 201
    assert receive_response.status_code == 200
    assert payment_response.status_code == 201
    assert order_response.json()["tenant_id"] == identifiers["tenant_a"]
    assert payment_response.json()["tenant_id"] == identifiers["tenant_a"]
