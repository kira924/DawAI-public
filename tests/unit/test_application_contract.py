import asyncio

import httpx

from src.main import app


def _get(path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_root_endpoint_is_available() -> None:
    response = _get("/")

    assert response.status_code == 200
    assert "message" in response.json()


def test_openapi_document_can_be_generated() -> None:
    document = app.openapi()

    assert document["openapi"]
    assert document["paths"]
    assert "barcode" in document["components"]["schemas"]["CatalogProductResponse"]["properties"]


def test_expected_domain_routes_are_registered() -> None:
    paths = set(app.openapi()["paths"])

    assert "/api/v1/auth/login" in paths
    assert any(path.startswith("/api/v1/products") for path in paths)
    assert "/api/v1/products/stock-movements" in paths
    assert "/api/v1/products/stock-reconciliation" in paths
    assert "/api/v1/products/stock-counts" in paths
    assert "/api/v1/products/stock-counts/{count_id}" in paths
    assert "/api/v1/products/stock-counts/{count_id}/submit" in paths
    assert "/api/v1/products/stock-counts/{count_id}/apply" in paths
    assert "/api/v1/products/stock-counts/{count_id}/cancel" in paths
    assert "/api/v1/products/quarantine" in paths
    assert "/api/v1/products/quarantine/{quarantine_item_id}/dispositions" in paths
    assert "/api/v1/catalog/products/search" in paths
    assert any(path.startswith("/api/v1/sales") for path in paths)
    assert any(path.startswith("/api/v1/purchases") for path in paths)
