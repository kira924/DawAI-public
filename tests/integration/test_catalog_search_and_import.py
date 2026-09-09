from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.modules.catalog import models as catalog_models
from src.modules.catalog import schemas as catalog_schemas
from src.modules.catalog.service import search_catalog_products, search_tenant_products
from src.modules.catalog.titan_import import (
    AliasRecord,
    BarcodeRecord,
    CatalogEntry,
    InteractionEntry,
    SourceRecord,
    TitanImportPlan,
    apply_titan_import,
)
from src.modules.customers.models import Customer  # noqa: F401
from src.modules.inventory import crud as inventory_crud
from src.modules.inventory import schemas as inventory_schemas
from src.modules.inventory.models import Product
from src.modules.shifts.models import Shift  # noqa: F401
from src.modules.tenants.models import Tenant
from src.modules.users.models import User  # noqa: F401


def _catalog_product(
    db: Session,
    *,
    canonical_key: str,
    name_en: str,
    name_ar: str,
    barcode: str | None = None,
) -> catalog_models.CatalogProduct:
    product = catalog_models.CatalogProduct(
        canonical_key=canonical_key,
        display_name=name_en,
        name_en=name_en,
        name_ar=name_ar,
        reference_price=Decimal("50.00"),
        data_quality_flags=[],
    )
    db.add(product)
    db.flush()
    for alias, language in ((name_en, "en"), (name_ar, "ar")):
        db.add(
            catalog_models.CatalogProductAlias(
                catalog_product_id=product.id,
                alias=alias,
                normalized_alias=alias.casefold(),
                language=language,
                alias_type="trade_name",
                source_name="synthetic",
            )
        )
    if barcode:
        db.add(
            catalog_models.CatalogProductBarcode(
                catalog_product_id=product.id,
                barcode=barcode,
                source_name="synthetic",
                is_valid_gtin=True,
            )
        )
    return product


@pytest.mark.integration
def test_ranked_search_supports_arabic_english_keyboard_and_tenant_isolation(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant_a = Tenant(
            name="Search Tenant A",
            owner_email="search-a@example.test",
            is_active=False,
        )
        tenant_b = Tenant(
            name="Search Tenant B",
            owner_email="search-b@example.test",
            is_active=False,
        )
        db.add_all([tenant_a, tenant_b])
        db.flush()

        exact_catalog = _catalog_product(
            db,
            canonical_key="synthetic:panadol",
            name_en="Panadol",
            name_ar="بانادول",
            barcode="4006381333931",
        )
        _catalog_product(
            db,
            canonical_key="synthetic:panadol-extra",
            name_en="Panadol Extra",
            name_ar="بانادول اكسترا",
        )
        local_product = Product(
            name="Panadol",
            barcode="4006381333931",
            category="MEDICINE",
            price=Decimal("50.00"),
            total_parts=0,
            is_divisible=True,
            parts_per_unit=2,
            catalog_product_id=exact_catalog.id,
            tenant_id=tenant_a.id,
        )
        foreign_product = Product(
            name="Panadol",
            category="MEDICINE",
            price=Decimal("50.00"),
            total_parts=0,
            is_divisible=False,
            parts_per_unit=1,
            tenant_id=tenant_b.id,
        )
        db.add_all([local_product, foreign_product])
        db.flush()
        for product in (local_product, foreign_product):
            db.add(
                catalog_models.ProductAlias(
                    product_id=product.id,
                    tenant_id=product.tenant_id,
                    alias="Panadol",
                    normalized_alias="panadol",
                    language="en",
                    alias_type="primary_name",
                    source_name="synthetic",
                )
            )
        db.commit()

        catalog_results = search_catalog_products(db, "panadol", 20)
        assert [product.id for product in catalog_results][:2] == [
            exact_catalog.id,
            exact_catalog.id + 1,
        ]
        assert catalog_results[0].barcode == "4006381333931"
        serialized = catalog_schemas.CatalogProductResponse.model_validate(
            catalog_results[0]
        ).model_dump()
        assert serialized["barcode"] == "4006381333931"
        assert [product.id for product in search_tenant_products(db, tenant_a.id, "pan", 20)] == [
            local_product.id
        ]
        assert [
            product.id for product in search_tenant_products(db, tenant_a.id, "fhkh],g", 20)
        ] == [local_product.id]
        assert [
            product.id for product in search_tenant_products(db, tenant_a.id, "4006381333931", 20)
        ] == [local_product.id]
        assert foreign_product.id not in {
            product.id for product in search_tenant_products(db, tenant_a.id, "panadol", 20)
        }
    engine.dispose()


@pytest.mark.integration
def test_tenant_can_link_a_catalog_product_only_once(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant = Tenant(
            name="Catalog Link Tenant",
            owner_email="catalog-link@example.test",
            is_active=False,
        )
        db.add(tenant)
        db.flush()
        catalog_product = _catalog_product(
            db,
            canonical_key="synthetic:catalog-link",
            name_en="Linked Medicine",
            name_ar="دواء مرتبط",
        )
        db.commit()

        product_input = inventory_schemas.ProductCreate(
            name="Linked Medicine",
            catalog_product_id=catalog_product.id,
            price=Decimal("25.00"),
        )
        first = inventory_crud.create_product(db, product_input, tenant.id)
        assert first.catalog_product_id == catalog_product.id

        with pytest.raises(inventory_crud.CatalogProductAlreadyLinkedError):
            inventory_crud.create_product(db, product_input, tenant.id)

        db.add(
            Product(
                name="Direct Duplicate",
                category="MEDICINE",
                price=Decimal("25.00"),
                total_parts=0,
                is_divisible=False,
                parts_per_unit=1,
                catalog_product_id=catalog_product.id,
                tenant_id=tenant.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    engine.dispose()


@pytest.mark.integration
def test_catalog_import_is_idempotent(migrated_postgresql_database_url: str) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    entry = CatalogEntry(
        canonical_key="synthetic:1",
        display_name="Synthetic Medicine",
        name_en="Synthetic Medicine",
        name_ar="دواء تجريبي",
        reference_price=Decimal("10.00"),
        data_quality_flags=[],
        sources=[SourceRecord("synthetic", "1", "A" * 64)],
        aliases=[AliasRecord("Synthetic Medicine", "en", "trade_name", "synthetic")],
        barcodes=[BarcodeRecord("4006381333931", "synthetic", True)],
    )
    plan = TitanImportPlan(
        products=[entry],
        interactions=[
            InteractionEntry(
                record_id="1",
                ingredient_a="Ingredient A",
                ingredient_b="Ingredient B",
                details="Synthetic interaction",
                advice="Monitor",
                checksum="B" * 64,
            )
        ],
        stats={},
        file_checksums={"synthetic": "C" * 64, "titan_ddi": "D" * 64},
    )

    with Session(engine) as db:
        first = apply_titan_import(db, plan)
        second = apply_titan_import(db, plan)

        assert first == second
        assert second == {
            "catalog_products": 1,
            "catalog_sources": 1,
            "catalog_aliases": 1,
            "catalog_barcodes": 1,
            "catalog_interactions": 1,
        }
    engine.dispose()
