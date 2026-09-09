import asyncio
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from src.core.database import get_db
from src.main import app
from src.modules.customers.schemas import CustomerPaymentCreate
from src.modules.expenses.schemas import ExpenseCreate
from src.modules.inventory.schemas import ProductCreate
from src.modules.purchases.schemas import PurchaseItemCreate
from src.modules.sales.schemas import InvoiceCreate, InvoiceItemCreate, PaymentType
from src.modules.users.schemas import UserCreate, UserRole


def _get(path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_openapi_uses_configured_project_title() -> None:
    assert app.title == "DawAI"


def test_invoice_contract_contains_fields_used_by_creation_logic() -> None:
    assert {"payment_type", "customer_id"}.issubset(InvoiceCreate.model_fields)


def test_health_endpoint_uses_the_database_and_reports_readiness() -> None:
    class HealthyDatabase:
        def execute(self, statement: object) -> object:
            return statement

    app.dependency_overrides[get_db] = HealthyDatabase
    try:
        response = _get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_health_endpoint_hides_database_failure_details() -> None:
    class UnhealthyDatabase:
        def execute(self, statement: object) -> object:
            raise RuntimeError("sensitive database details")

    app.dependency_overrides[get_db] = UnhealthyDatabase
    try:
        response = _get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": "unavailable"}
    assert "sensitive" not in response.text


def test_liveness_does_not_require_database_access() -> None:
    response = _get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_credit_invoice_requires_a_customer() -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate.model_validate(
            {
                "payment_type": PaymentType.CREDIT,
                "items": [{"product_id": 1, "quantity": 1, "sale_unit_price": 10}],
            }
        )


def test_invoice_payment_type_is_case_insensitive() -> None:
    invoice = InvoiceCreate.model_validate(
        {
            "payment_type": "CREDIT",
            "customer_id": 1,
            "items": [{"product_id": 1, "quantity": 1, "sale_unit_price": 10}],
        }
    )

    assert invoice.payment_type == PaymentType.CREDIT


def test_opening_stock_requires_batch_details() -> None:
    payload = {
        "name": "Synthetic product",
        "price": 10,
        "initial_boxes": 1,
    }

    with pytest.raises(ValidationError):
        ProductCreate.model_validate(payload)

    product = ProductCreate.model_validate(
        {
            **payload,
            "initial_batch": {
                "batch_number": "SYNTHETIC-BATCH-1",
                "expiry_date": date(2030, 1, 1),
            },
        }
    )
    assert product.initial_batch is not None


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (ExpenseCreate, {"amount": -1, "description": "Invalid"}),
        (CustomerPaymentCreate, {"customer_id": 1, "amount": -1}),
    ],
)
def test_negative_domain_values_are_rejected(schema: type, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_negative_invoice_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceItemCreate.model_validate({"product_id": 1, "quantity": -1, "sale_unit_price": 10})


def test_non_positive_purchase_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PurchaseItemCreate.model_validate(
            {
                "product_id": 1,
                "quantity": 0,
                "base_cost": 10,
                "batch_number": "SYNTHETIC-BATCH",
                "expiry_date": "2030-01-01",
            }
        )


def test_user_role_taxonomy_is_enforced() -> None:
    user = UserCreate.model_validate(
        {
            "email": "synthetic@example.com",
            "full_name": "Synthetic User",
            "tenant_id": 1,
            "password": "test-only-password",
        }
    )
    assert user.role == UserRole.PHARMACIST

    with pytest.raises(ValidationError):
        UserCreate.model_validate(
            {
                "email": "invalid@example.com",
                "full_name": "Invalid User",
                "role": "admin",
                "tenant_id": 1,
                "password": "test-only-password",
            }
        )
