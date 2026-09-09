"""add canonical medicine catalog and search aliases

Revision ID: 1a6c9e4b2f70
Revises: 9f1c2a7d4e60
Create Date: 2026-09-05
"""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1a6c9e4b2f70"
down_revision: str | Sequence[str] | None = "9f1c2a7d4e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
ARABIC_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ـ": "",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
    }
)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().translate(ARABIC_FOLD)
    normalized = ARABIC_MARKS.sub("", normalized)
    searchable = "".join(character if character.isalnum() else " " for character in normalized)
    return " ".join(searchable.split())


def _backfill_product_aliases() -> None:
    connection = op.get_bind()
    products = connection.execute(sa.text("SELECT id, tenant_id, name FROM products")).mappings()
    rows = []
    for product in products:
        alias = product["name"]
        normalized = _normalize(alias)
        if not normalized:
            raise RuntimeError(f"Product {product['id']} has no searchable name")
        if any("\u0600" <= character <= "\u06ff" for character in alias):
            language = "ar"
        elif any("a" <= character.casefold() <= "z" for character in alias):
            language = "en"
        else:
            language = "und"
        rows.append(
            {
                "product_id": product["id"],
                "tenant_id": product["tenant_id"],
                "alias": alias,
                "normalized_alias": normalized,
                "language": language,
            }
        )
    if rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO product_aliases
                    (product_id, tenant_id, alias, normalized_alias, language,
                     alias_type, source_name)
                VALUES
                    (:product_id, :tenant_id, :alias, :normalized_alias, :language,
                     'primary_name', 'migration_backfill')
                """
            ),
            rows,
        )


def _assert_safe_downgrade() -> None:
    connection = op.get_bind()
    catalog_rows = connection.scalar(sa.text("SELECT COUNT(*) FROM catalog_products"))
    linked_products = connection.scalar(
        sa.text("SELECT COUNT(*) FROM products WHERE catalog_product_id IS NOT NULL")
    )
    non_reconstructible_aliases = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM product_aliases
            WHERE alias_type <> 'primary_name'
               OR source_name NOT IN ('manual', 'migration_backfill')
            """
        )
    )
    if catalog_rows or linked_products or non_reconstructible_aliases:
        raise RuntimeError(
            "Downgrade would discard catalog/search data: "
            f"catalog-products={catalog_rows}, linked-products={linked_products}, "
            f"non-reconstructible-aliases={non_reconstructible_aliases}"
        )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "catalog_products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("canonical_key", sa.String(length=96), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=True),
        sa.Column("name_ar", sa.String(length=200), nullable=True),
        sa.Column("active_ingredients", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.String(length=200), nullable=True),
        sa.Column("reference_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("units_per_package", sa.Integer(), nullable=True),
        sa.Column("package_size", sa.Integer(), nullable=True),
        sa.Column("package_unit", sa.String(length=50), nullable=True),
        sa.Column("dosage_form", sa.String(length=100), nullable=True),
        sa.Column("therapeutic_category", sa.String(length=250), nullable=True),
        sa.Column(
            "data_quality_flags",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("needs_review", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(display_name)) > 0",
            name="ck_catalog_products_display_name_present",
        ),
        sa.CheckConstraint(
            "reference_price IS NULL OR reference_price >= 0",
            name="ck_catalog_products_reference_price_nonnegative",
        ),
        sa.CheckConstraint(
            "units_per_package IS NULL OR units_per_package > 0",
            name="ck_catalog_products_units_positive",
        ),
        sa.CheckConstraint(
            "package_size IS NULL OR package_size > 0",
            name="ck_catalog_products_package_size_positive",
        ),
        sa.CheckConstraint(
            "json_typeof(data_quality_flags) = 'array'",
            name="ck_catalog_products_quality_flags_array",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key"),
    )
    op.create_table(
        "catalog_product_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("catalog_product_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=48), nullable=False),
        sa.Column("source_record_id", sa.String(length=64), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_updated_on", sa.Date(), nullable=True),
        sa.Column("source_data", sa.JSON(), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["catalog_product_id"], ["catalog_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_name", "source_record_id", name="uq_catalog_sources_name_record"
        ),
    )
    op.create_index(
        "ix_catalog_sources_product_id", "catalog_product_sources", ["catalog_product_id"]
    )
    op.create_table(
        "catalog_product_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("catalog_product_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.String(length=250), nullable=False),
        sa.Column("normalized_alias", sa.String(length=250), nullable=False),
        sa.Column("language", sa.String(length=8), server_default="und", nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=48), nullable=False),
        sa.CheckConstraint("language IN ('ar', 'en', 'und')", name="ck_catalog_aliases_language"),
        sa.ForeignKeyConstraint(
            ["catalog_product_id"], ["catalog_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_product_id",
            "normalized_alias",
            "alias_type",
            "source_name",
            name="uq_catalog_aliases_product_value_type_source",
        ),
    )
    op.create_index(
        "ix_catalog_aliases_normalized", "catalog_product_aliases", ["normalized_alias"]
    )
    op.create_index(
        "ix_catalog_aliases_normalized_trgm",
        "catalog_product_aliases",
        ["normalized_alias"],
        postgresql_using="gin",
        postgresql_ops={"normalized_alias": "gin_trgm_ops"},
    )
    op.create_table(
        "catalog_product_barcodes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("catalog_product_id", sa.BigInteger(), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=48), nullable=False),
        sa.Column("is_valid_gtin", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_product_id"], ["catalog_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_product_id", "barcode", name="uq_catalog_barcodes_product_barcode"
        ),
    )
    op.create_index("ix_catalog_barcodes_barcode", "catalog_product_barcodes", ["barcode"])
    op.create_table(
        "catalog_drug_interactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_name", sa.String(length=48), nullable=False),
        sa.Column("source_record_id", sa.String(length=64), nullable=False),
        sa.Column("ingredient_a", sa.String(length=200), nullable=False),
        sa.Column("ingredient_b", sa.String(length=200), nullable=False),
        sa.Column("normalized_ingredient_a", sa.String(length=200), nullable=False),
        sa.Column("normalized_ingredient_b", sa.String(length=200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("advice", sa.Text(), nullable=True),
        sa.Column(
            "clinical_status",
            sa.String(length=32),
            server_default="legacy_unverified",
            nullable=False,
        ),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_name", "source_record_id", name="uq_catalog_interactions_source_record"
        ),
    )
    op.create_index(
        "ix_catalog_interactions_ingredient_a",
        "catalog_drug_interactions",
        ["normalized_ingredient_a"],
    )
    op.create_index(
        "ix_catalog_interactions_ingredient_b",
        "catalog_drug_interactions",
        ["normalized_ingredient_b"],
    )

    op.add_column("products", sa.Column("catalog_product_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_products_catalog_product_id",
        "products",
        "catalog_products",
        ["catalog_product_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_products_catalog_product_id", "products", ["catalog_product_id"])

    op.create_table(
        "product_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=250), nullable=False),
        sa.Column("normalized_alias", sa.String(length=250), nullable=False),
        sa.Column("language", sa.String(length=8), server_default="und", nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=48), server_default="manual", nullable=False),
        sa.CheckConstraint("language IN ('ar', 'en', 'und')", name="ck_product_aliases_language"),
        sa.ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_product_aliases_product_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id",
            "tenant_id",
            "normalized_alias",
            "alias_type",
            name="uq_product_aliases_product_tenant_value_type",
        ),
    )
    op.create_index(
        "ix_product_aliases_tenant_normalized",
        "product_aliases",
        ["tenant_id", "normalized_alias"],
    )
    op.create_index(
        "ix_product_aliases_normalized_trgm",
        "product_aliases",
        ["normalized_alias"],
        postgresql_using="gin",
        postgresql_ops={"normalized_alias": "gin_trgm_ops"},
    )
    _backfill_product_aliases()


def downgrade() -> None:
    _assert_safe_downgrade()
    op.drop_index("ix_product_aliases_normalized_trgm", table_name="product_aliases")
    op.drop_index("ix_product_aliases_tenant_normalized", table_name="product_aliases")
    op.drop_table("product_aliases")
    op.drop_index("ix_products_catalog_product_id", table_name="products")
    op.drop_constraint("fk_products_catalog_product_id", "products", type_="foreignkey")
    op.drop_column("products", "catalog_product_id")
    op.drop_index("ix_catalog_interactions_ingredient_b", table_name="catalog_drug_interactions")
    op.drop_index("ix_catalog_interactions_ingredient_a", table_name="catalog_drug_interactions")
    op.drop_table("catalog_drug_interactions")
    op.drop_index("ix_catalog_barcodes_barcode", table_name="catalog_product_barcodes")
    op.drop_table("catalog_product_barcodes")
    op.drop_index("ix_catalog_aliases_normalized_trgm", table_name="catalog_product_aliases")
    op.drop_index("ix_catalog_aliases_normalized", table_name="catalog_product_aliases")
    op.drop_table("catalog_product_aliases")
    op.drop_index("ix_catalog_sources_product_id", table_name="catalog_product_sources")
    op.drop_table("catalog_product_sources")
    op.drop_table("catalog_products")
