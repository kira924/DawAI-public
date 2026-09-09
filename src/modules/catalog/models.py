from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from src.core.database import Base
from src.core.money import MONEY_TYPE


class CatalogProduct(Base):
    __tablename__ = "catalog_products"
    __table_args__ = (
        CheckConstraint(
            "length(btrim(display_name)) > 0",
            name="ck_catalog_products_display_name_present",
        ),
        CheckConstraint(
            "reference_price IS NULL OR reference_price >= 0",
            name="ck_catalog_products_reference_price_nonnegative",
        ),
        CheckConstraint(
            "units_per_package IS NULL OR units_per_package > 0",
            name="ck_catalog_products_units_positive",
        ),
        CheckConstraint(
            "package_size IS NULL OR package_size > 0",
            name="ck_catalog_products_package_size_positive",
        ),
        CheckConstraint(
            "json_typeof(data_quality_flags) = 'array'",
            name="ck_catalog_products_quality_flags_array",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    canonical_key = Column(String(96), nullable=False, unique=True)
    display_name = Column(String(200), nullable=False)
    name_en = Column(String(200), nullable=True)
    name_ar = Column(String(200), nullable=True)
    active_ingredients = Column(Text, nullable=True)
    manufacturer = Column(String(200), nullable=True)
    reference_price = Column(MONEY_TYPE, nullable=True)
    units_per_package = Column(Integer, nullable=True)
    package_size = Column(Integer, nullable=True)
    package_unit = Column(String(50), nullable=True)
    dosage_form = Column(String(100), nullable=True)
    therapeutic_category = Column(String(250), nullable=True)
    data_quality_flags = Column(JSON, nullable=False, default=list, server_default="[]")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    needs_review = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class CatalogProductSource(Base):
    __tablename__ = "catalog_product_sources"
    __table_args__ = (
        UniqueConstraint("source_name", "source_record_id", name="uq_catalog_sources_name_record"),
        Index("ix_catalog_sources_product_id", "catalog_product_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    catalog_product_id = Column(
        BigInteger, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    source_name = Column(String(48), nullable=False)
    source_record_id = Column(String(64), nullable=False)
    source_checksum = Column(String(64), nullable=False)
    source_updated_on = Column(Date, nullable=True)
    source_data = Column(JSON, nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CatalogProductAlias(Base):
    __tablename__ = "catalog_product_aliases"
    __table_args__ = (
        CheckConstraint("language IN ('ar', 'en', 'und')", name="ck_catalog_aliases_language"),
        UniqueConstraint(
            "catalog_product_id",
            "normalized_alias",
            "alias_type",
            "source_name",
            name="uq_catalog_aliases_product_value_type_source",
        ),
        Index("ix_catalog_aliases_normalized", "normalized_alias"),
        Index(
            "ix_catalog_aliases_normalized_prefix",
            "normalized_alias",
            "catalog_product_id",
            postgresql_ops={"normalized_alias": "text_pattern_ops"},
        ),
        Index(
            "ix_catalog_aliases_normalized_trgm",
            "normalized_alias",
            postgresql_using="gin",
            postgresql_ops={"normalized_alias": "gin_trgm_ops"},
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    catalog_product_id = Column(
        BigInteger, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    alias = Column(String(250), nullable=False)
    normalized_alias = Column(String(250), nullable=False)
    language = Column(String(8), nullable=False, default="und", server_default="und")
    alias_type = Column(String(32), nullable=False)
    source_name = Column(String(48), nullable=False)


class CatalogProductBarcode(Base):
    __tablename__ = "catalog_product_barcodes"
    __table_args__ = (
        UniqueConstraint(
            "catalog_product_id", "barcode", name="uq_catalog_barcodes_product_barcode"
        ),
        Index("ix_catalog_barcodes_barcode", "barcode"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    catalog_product_id = Column(
        BigInteger, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    barcode = Column(String(64), nullable=False)
    source_name = Column(String(48), nullable=False)
    is_valid_gtin = Column(Boolean, nullable=False, default=False, server_default="false")


class CatalogDrugInteraction(Base):
    __tablename__ = "catalog_drug_interactions"
    __table_args__ = (
        UniqueConstraint(
            "source_name", "source_record_id", name="uq_catalog_interactions_source_record"
        ),
        Index("ix_catalog_interactions_ingredient_a", "normalized_ingredient_a"),
        Index("ix_catalog_interactions_ingredient_b", "normalized_ingredient_b"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_name = Column(String(48), nullable=False)
    source_record_id = Column(String(64), nullable=False)
    ingredient_a = Column(String(200), nullable=False)
    ingredient_b = Column(String(200), nullable=False)
    normalized_ingredient_a = Column(String(200), nullable=False)
    normalized_ingredient_b = Column(String(200), nullable=False)
    details = Column(Text, nullable=False)
    advice = Column(Text, nullable=True)
    clinical_status = Column(
        String(32), nullable=False, default="legacy_unverified", server_default="legacy_unverified"
    )
    source_checksum = Column(String(64), nullable=False)
    imported_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProductAlias(Base):
    __tablename__ = "product_aliases"
    __table_args__ = (
        CheckConstraint("language IN ('ar', 'en', 'und')", name="ck_product_aliases_language"),
        UniqueConstraint(
            "product_id",
            "tenant_id",
            "normalized_alias",
            "alias_type",
            name="uq_product_aliases_product_tenant_value_type",
        ),
        ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_product_aliases_product_same_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_product_aliases_tenant_normalized", "tenant_id", "normalized_alias"),
        Index(
            "ix_product_aliases_tenant_normalized_prefix",
            "tenant_id",
            "normalized_alias",
            "product_id",
            postgresql_ops={"normalized_alias": "text_pattern_ops"},
        ),
        Index(
            "ix_product_aliases_normalized_trgm",
            "normalized_alias",
            postgresql_using="gin",
            postgresql_ops={"normalized_alias": "gin_trgm_ops"},
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String(250), nullable=False)
    normalized_alias = Column(String(250), nullable=False)
    language = Column(String(8), nullable=False, default="und", server_default="und")
    alias_type = Column(String(32), nullable=False)
    source_name = Column(String(48), nullable=False, default="manual", server_default="manual")
