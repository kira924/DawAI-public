import asyncio

import httpx
from fastapi import FastAPI

from src.api.errors import install_exception_handlers
from src.api.middleware import ApiContractMiddleware
from src.main import app


def _request(
    application: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: object | None = None,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=application,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers, json=json)

    return asyncio.run(send())


def test_openapi_exposes_v1_and_hides_legacy_aliases() -> None:
    document = app.openapi()
    paths = set(document["paths"])

    domain_paths = {path for path in paths if path.startswith("/api/")}
    assert domain_paths
    assert all(path.startswith("/api/v1/") for path in domain_paths)
    assert "/api/v1/auth/login" in paths
    assert "/api/auth/login" not in paths
    assert (
        document["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"]["password"][
            "tokenUrl"
        ]
        == "/api/v1/auth/login"
    )


def test_openapi_documents_the_v1_error_contract_and_bounded_pagination() -> None:
    document = app.openapi()
    operation = document["paths"]["/api/v1/products/"]["get"]

    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiErrorResponse"
    }
    assert set(operation["responses"]["422"]["headers"]) == {
        "X-API-Version",
        "X-Request-ID",
    }
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["skip"]["schema"]["minimum"] == 0
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100


def test_every_v1_collection_endpoint_has_a_bounded_limit() -> None:
    document = app.openapi()
    collection_paths = []

    for path, path_item in document["paths"].items():
        if not path.startswith("/api/v1/") or "get" not in path_item:
            continue
        operation = path_item["get"]
        schema = (
            operation.get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if schema.get("type") != "array":
            continue

        collection_paths.append(path)
        parameters = {parameter["name"]: parameter for parameter in operation.get("parameters", [])}
        limit_schema = parameters["limit"]["schema"]
        assert limit_schema["minimum"] == 1, path
        assert limit_schema["maximum"] <= 500, path

    assert collection_paths


def test_v1_public_enums_match_the_canonical_domain_values() -> None:
    schemas = app.openapi()["components"]["schemas"]
    expected_enums = {
        "TokenType": ["bearer"],
        "SupportAuditEventType": [
            "grant_created",
            "grant_revoked",
            "access_denied",
            "request_started",
        ],
        "ExpiryStatus": ["expired", "critical", "warning", "valid"],
        "StockMovementEventType": [
            "migration_on_hand_balance",
            "migration_quarantine_balance",
            "opening_stock",
            "purchase_receipt",
            "sale",
            "sales_return_restock",
            "sales_return_quarantine",
            "purchase_return",
            "stock_adjustment",
            "quarantine_transfer_out",
            "quarantine_transfer_in",
            "quarantine_disposition",
            "quarantine_release_out",
            "quarantine_release_in",
        ],
        "StockBucket": ["on_hand", "quarantine"],
        "StockMovementSourceType": [
            "migration",
            "product",
            "purchase_order",
            "invoice",
            "sales_return",
            "purchase_return",
            "stock_count",
            "quarantine_item",
            "quarantine_disposition",
        ],
        "QuarantineItemReason": [
            "expired_batch",
            "return_condition",
            "damage",
            "recall",
            "quality_concern",
            "other",
        ],
        "QuarantineItemStatus": ["pending", "resolved"],
        "ReplenishmentRecommendationSource": ["manual_policy"],
        "RecordedReturnCondition": ["sealed", "opened_or_damaged", "unknown", "legacy"],
        "ReturnDisposition": ["restock", "quarantine", "legacy"],
        "PurchaseOrderStatus": ["PENDING", "RECEIVED", "CANCELLED"],
        "ShiftStatus": ["OPEN", "CLOSED"],
        "FinancialEventType": [
            "sale",
            "sales_return",
            "customer_payment",
            "purchase_receipt",
            "purchase_return",
            "supplier_payment",
            "expense",
            "shift_adjustment",
        ],
        "FinancialSourceType": [
            "invoice",
            "sales_return",
            "customer_payment",
            "purchase_order",
            "purchase_return",
            "supplier_payment",
            "expense",
            "shift",
        ],
    }

    for name, expected_values in expected_enums.items():
        assert schemas[name]["enum"] == expected_values


def test_v1_json_request_models_reject_unknown_fields() -> None:
    document = app.openapi()
    request_schema_names = set()

    for path, path_item in document["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for operation in path_item.values():
            for media_type in operation.get("requestBody", {}).get("content", {}).values():
                reference = media_type.get("schema", {}).get("$ref")
                if reference and "application/json" in operation["requestBody"]["content"]:
                    request_schema_names.add(reference.rsplit("/", 1)[-1])

    assert request_schema_names
    for name in request_schema_names:
        assert document["components"]["schemas"][name]["additionalProperties"] is False, name


def test_replenishment_and_explicit_dashboard_metrics_are_public_contracts() -> None:
    document = app.openapi()

    assert "get" in document["paths"]["/api/v1/products/replenishment"]
    assert "put" in document["paths"]["/api/v1/products/{product_id}/replenishment-policy"]

    dashboard = document["components"]["schemas"]["DashboardSummaryResponse"]["properties"]
    assert "inventory_date" in dashboard
    assert {
        "gross_sales",
        "sales_returns",
        "net_sales",
        "operating_expenses",
        "operating_result_before_cogs_and_tax",
    }.issubset(dashboard)
    assert dashboard["net_revenue"]["deprecated"] is True


def test_v1_errors_use_the_stable_envelope_and_request_id() -> None:
    response = _request(
        app,
        "POST",
        "/api/v1/auth/refresh",
        headers={"X-Request-ID": "contract-test-123"},
        json={},
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "contract-test-123"
    assert response.headers["X-API-Version"] == "1"
    assert "Deprecation" not in response.headers
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "request_id": "contract-test-123",
            "details": [
                {
                    "field": "body.refresh_token",
                    "message": "Field required",
                    "code": "missing",
                }
            ],
        }
    }


def test_legacy_alias_preserves_detail_and_is_marked_deprecated() -> None:
    response = _request(app, "POST", "/api/auth/refresh", json={})

    assert response.status_code == 422
    assert response.headers["X-API-Version"] == "1"
    assert response.headers["Deprecation"] == "true"
    assert "detail" in response.json()
    assert "error" not in response.json()


def test_legacy_validation_errors_do_not_echo_sensitive_input() -> None:
    rejected_password = "too-short"
    response = _request(
        app,
        "POST",
        "/api/auth/password-reset/confirm",
        json={"token": "t" * 32, "new_password": rejected_password},
    )

    assert response.status_code == 422
    assert "detail" in response.json()
    assert rejected_password not in response.text
    assert set(response.json()["detail"][0]) == {"type", "loc", "msg"}


def test_v1_http_errors_use_the_envelope_and_preserve_auth_headers() -> None:
    response = _request(
        app,
        "GET",
        "/api/v1/products/",
        headers={"X-Request-ID": "unauthenticated-request"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"] == "unauthenticated-request"
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Not authenticated",
            "request_id": "unauthenticated-request",
        }
    }


def test_invalid_client_request_id_is_replaced() -> None:
    response = _request(app, "GET", "/", headers={"X-Request-ID": "invalid request id"})

    request_id = response.headers["X-Request-ID"]
    assert request_id != "invalid request id"
    assert len(request_id) == 36


def test_unhandled_v1_errors_do_not_disclose_internal_details() -> None:
    isolated_app = FastAPI()
    isolated_app.add_middleware(ApiContractMiddleware)
    install_exception_handlers(isolated_app)

    async def explode() -> None:
        raise RuntimeError("sensitive internal diagnostic")

    isolated_app.add_api_route("/api/v1/explode", explode)
    response = _request(
        isolated_app,
        "GET",
        "/api/v1/explode",
        raise_app_exceptions=False,
    )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"]
    assert response.json()["error"]["code"] == "internal_error"
    assert "sensitive internal diagnostic" not in response.text
