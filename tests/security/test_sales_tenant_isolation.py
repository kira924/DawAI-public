import asyncio
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import get_password_hash
from src.main import app
from src.modules.customers.models import Customer
from src.modules.inventory.models import Product, ProductBatch
from src.modules.sales.models import Invoice, InvoiceItem, SalesReturn
from src.modules.shifts.models import Shift
from src.modules.tenants.models import Tenant
from src.modules.users.models import User

PASSWORD = "Synthetic-sales-password-123"


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
                method, path, data=form_data, json=json_body, headers=headers
            )

    app.dependency_overrides[get_db] = override_database
    try:
        return asyncio.run(request())
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_sales_tenants(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    password_hash = get_password_hash(PASSWORD)
    with Session(engine) as db:
        tenant_a = Tenant(name="Sales Tenant A", owner_email="sales-a@example.com")
        tenant_b = Tenant(name="Sales Tenant B", owner_email="sales-b@example.com")
        db.add_all([tenant_a, tenant_b])
        db.flush()
        user_a = User(
            email="sales-a@example.com",
            hashed_password=password_hash,
            full_name="Sales User A",
            role="manager",
            tenant_id=tenant_a.id,
        )
        user_b = User(
            email="sales-b@example.com",
            hashed_password=password_hash,
            full_name="Sales User B",
            role="manager",
            tenant_id=tenant_b.id,
        )
        db.add_all([user_a, user_b])
        db.flush()
        tenant_a.owner_user_id = user_a.id
        tenant_a.is_active = True
        tenant_b.owner_user_id = user_b.id
        tenant_b.is_active = True
        shift_a = Shift(user_id=user_a.id, tenant_id=tenant_a.id)
        shift_b = Shift(user_id=user_b.id, tenant_id=tenant_b.id)
        customer_a = Customer(name="Customer A", tenant_id=tenant_a.id)
        customer_b = Customer(name="Customer B", tenant_id=tenant_b.id)
        product_a = Product(name="Product A", price=10, total_parts=10, tenant_id=tenant_a.id)
        product_b = Product(name="Product B", price=10, total_parts=10, tenant_id=tenant_b.id)
        db.add_all([shift_a, shift_b, customer_a, customer_b, product_a, product_b])
        db.flush()
        db.add_all(
            [
                ProductBatch(
                    batch_number="SALES-A",
                    expiry_date=date(2030, 1, 1),
                    quantity=10,
                    product_id=product_a.id,
                    tenant_id=tenant_a.id,
                ),
                ProductBatch(
                    batch_number="SALES-B",
                    expiry_date=date(2030, 1, 1),
                    quantity=10,
                    product_id=product_b.id,
                    tenant_id=tenant_b.id,
                ),
            ]
        )
        invoice_a = Invoice(
            total_amount=10,
            payment_type="cash",
            user_id=user_a.id,
            tenant_id=tenant_a.id,
            shift_id=shift_a.id,
        )
        invoice_b = Invoice(
            total_amount=10,
            payment_type="cash",
            user_id=user_b.id,
            tenant_id=tenant_b.id,
            shift_id=shift_b.id,
        )
        db.add_all([invoice_a, invoice_b])
        db.flush()
        db.add_all(
            [
                InvoiceItem(
                    invoice_id=invoice_a.id,
                    product_id=product_a.id,
                    tenant_id=tenant_a.id,
                    quantity=1,
                    list_unit_price=10,
                    sale_unit_price=10,
                    subtotal=10,
                ),
                InvoiceItem(
                    invoice_id=invoice_b.id,
                    product_id=product_b.id,
                    tenant_id=tenant_b.id,
                    quantity=1,
                    list_unit_price=10,
                    sale_unit_price=10,
                    subtotal=10,
                ),
            ]
        )
        db.commit()
        identifiers = {
            "tenant_a": tenant_a.id,
            "tenant_b": tenant_b.id,
            "customer_a": customer_a.id,
            "customer_b": customer_b.id,
            "product_a": product_a.id,
            "product_b": product_b.id,
            "invoice_a": invoice_a.id,
            "invoice_b": invoice_b.id,
            "invoice_item_a": invoice_a.items[0].id,
            "invoice_item_b": invoice_b.items[0].id,
        }
    engine.dispose()
    return identifiers


def _authorization_header(database_url: str) -> dict[str, str]:
    response = _request(
        "POST",
        "/api/auth/login",
        database_url,
        form_data={"username": "sales-a@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _invoice_payload(product_id: int, customer_id: int | None = None) -> dict[str, object]:
    return {
        "payment_type": "cash",
        "customer_id": customer_id,
        "items": [{"product_id": product_id, "quantity": 1, "sale_unit_price": 10}],
    }


@pytest.mark.integration
@pytest.mark.parametrize("payment_type", ["cash", "credit"])
def test_invoice_rejects_foreign_customer_without_partial_write(
    migrated_postgresql_database_url: str, payment_type: str
) -> None:
    ids = _seed_sales_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)
    payload = _invoice_payload(ids["product_a"], ids["customer_b"])
    payload["payment_type"] = payment_type

    response = _request(
        "POST", "/api/sales/", migrated_postgresql_database_url, json_body=payload, headers=headers
    )

    assert response.status_code == 404
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.query(Invoice).count() == 2
        assert db.get(Product, ids["product_a"]).total_parts == 10
        assert db.get(Customer, ids["customer_b"]).total_debt == 0
    engine.dispose()


@pytest.mark.integration
def test_invoice_rejects_foreign_product_without_partial_write(
    migrated_postgresql_database_url: str,
) -> None:
    ids = _seed_sales_tenants(migrated_postgresql_database_url)
    response = _request(
        "POST",
        "/api/sales/",
        migrated_postgresql_database_url,
        json_body=_invoice_payload(ids["product_b"]),
        headers=_authorization_header(migrated_postgresql_database_url),
    )
    assert response.status_code == 404
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.query(Invoice).count() == 2
        assert db.get(Product, ids["product_b"]).total_parts == 10
    engine.dispose()


@pytest.mark.integration
def test_invoice_reads_exclude_foreign_tenant(
    migrated_postgresql_database_url: str,
) -> None:
    ids = _seed_sales_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)
    listing = _request("GET", "/api/sales/", migrated_postgresql_database_url, headers=headers)
    foreign = _request(
        "GET", f"/api/sales/{ids['invoice_b']}", migrated_postgresql_database_url, headers=headers
    )
    assert listing.status_code == 200
    assert {invoice["id"] for invoice in listing.json()} == {ids["invoice_a"]}
    assert foreign.status_code == 404


@pytest.mark.integration
def test_sales_return_rejects_foreign_invoice_and_product_atomically(
    migrated_postgresql_database_url: str,
) -> None:
    ids = _seed_sales_tenants(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url)
    payloads = [
        {
            "invoice_id": ids["invoice_b"],
            "idempotency_key": "fa8b0bb9-3d69-4cf0-a58d-252b07721111",
            "items": [
                {
                    "invoice_item_id": ids["invoice_item_a"],
                    "quantity": 1,
                    "condition": "unknown",
                }
            ],
        },
        {
            "invoice_id": ids["invoice_a"],
            "idempotency_key": "fa8b0bb9-3d69-4cf0-a58d-252b07722222",
            "items": [
                {
                    "invoice_item_id": ids["invoice_item_b"],
                    "quantity": 1,
                    "condition": "unknown",
                }
            ],
        },
    ]
    for payload in payloads:
        response = _request(
            "POST",
            "/api/sales/returns",
            migrated_postgresql_database_url,
            json_body=payload,
            headers=headers,
        )
        assert response.status_code == 404
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.query(SalesReturn).count() == 0
        assert db.get(Product, ids["product_a"]).total_parts == 10
        assert db.get(Product, ids["product_b"]).total_parts == 10
    engine.dispose()


@pytest.mark.integration
def test_same_tenant_cash_invoice_with_customer_remains_available(
    migrated_postgresql_database_url: str,
) -> None:
    ids = _seed_sales_tenants(migrated_postgresql_database_url)
    response = _request(
        "POST",
        "/api/sales/",
        migrated_postgresql_database_url,
        json_body=_invoice_payload(ids["product_a"], ids["customer_a"]),
        headers=_authorization_header(migrated_postgresql_database_url),
    )
    assert response.status_code == 201
    assert response.json()["tenant_id"] == ids["tenant_a"]
    assert response.json()["customer_id"] == ids["customer_a"]
