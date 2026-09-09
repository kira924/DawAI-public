"""prevent duplicate catalog links

Revision ID: 5f2b8c4d7a91
Revises: 3c8d5f1a7b20
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5f2b8c4d7a91"
down_revision: str | Sequence[str] | None = "3c8d5f1a7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM ("
            "SELECT tenant_id, catalog_product_id FROM products "
            "WHERE catalog_product_id IS NOT NULL "
            "GROUP BY tenant_id, catalog_product_id HAVING count(*) > 1"
            ") duplicate_links"
        )
    )
    if duplicate_count:
        raise RuntimeError("Cannot enforce unique tenant catalog links while duplicate links exist")

    op.create_index(
        "uq_products_tenant_catalog_product",
        "products",
        ["tenant_id", "catalog_product_id"],
        unique=True,
        postgresql_where=sa.text("catalog_product_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_products_tenant_catalog_product", table_name="products")
