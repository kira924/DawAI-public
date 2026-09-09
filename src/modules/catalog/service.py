from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.modules.catalog import models
from src.modules.catalog.normalization import build_search_variants
from src.modules.inventory.models import Product


@dataclass(frozen=True, order=True)
class SearchScore:
    rank: int
    secondary_score: float
    product_id: int


def _alias_matches(
    db: Session,
    *,
    alias_model: type[models.CatalogProductAlias] | type[models.ProductAlias],
    product_id_column: object,
    value: str,
    rank_offset: int,
    filters: tuple[object, ...] = (),
    candidate_limit: int = 250,
) -> list[tuple[int, int, float]]:
    normalized_alias = alias_model.normalized_alias
    prefix = normalized_alias.startswith(value, autoescape=True)

    rows = (
        db.query(product_id_column, normalized_alias)
        .filter(*filters, prefix)
        .order_by(normalized_alias.asc(), product_id_column.asc())
        .limit(candidate_limit)
        .all()
    )
    return [
        (
            int(product_id),
            rank_offset if alias == value else rank_offset + 10,
            float(len(alias)),
        )
        for product_id, alias in rows
    ]


def _merge_scores(scores: dict[int, SearchScore], rows: list[tuple[int, int, float]]) -> None:
    for product_id, rank, secondary_score in rows:
        candidate = SearchScore(rank, secondary_score, product_id)
        current = scores.get(product_id)
        if current is None or candidate < current:
            scores[product_id] = candidate


def search_catalog_products(db: Session, query: str, limit: int) -> list[models.CatalogProduct]:
    variants = build_search_variants(query)
    if not variants.original:
        return []

    scores: dict[int, SearchScore] = {}
    if variants.original.isdigit():
        barcode_ids = (
            db.query(models.CatalogProductBarcode.catalog_product_id)
            .filter(models.CatalogProductBarcode.barcode == variants.original)
            .limit(limit)
            .all()
        )
        _merge_scores(scores, [(int(product_id), 0, 1.0) for (product_id,) in barcode_ids])

    original_matches = _alias_matches(
        db,
        alias_model=models.CatalogProductAlias,
        product_id_column=models.CatalogProductAlias.catalog_product_id,
        value=variants.original,
        rank_offset=10,
    )
    _merge_scores(scores, original_matches)
    if not scores and variants.arabic_keyboard:
        _merge_scores(
            scores,
            _alias_matches(
                db,
                alias_model=models.CatalogProductAlias,
                product_id_column=models.CatalogProductAlias.catalog_product_id,
                value=variants.arabic_keyboard,
                rank_offset=15,
            ),
        )

    ordered_ids = [score.product_id for score in sorted(scores.values())[:limit]]
    if not ordered_ids:
        return []
    products = {
        product.id: product
        for product in db.query(models.CatalogProduct)
        .filter(
            models.CatalogProduct.id.in_(ordered_ids), models.CatalogProduct.is_active.is_(True)
        )
        .all()
    }
    barcodes: dict[int, str] = {}
    for product_id, barcode in (
        db.query(
            models.CatalogProductBarcode.catalog_product_id,
            models.CatalogProductBarcode.barcode,
        )
        .filter(models.CatalogProductBarcode.catalog_product_id.in_(ordered_ids))
        .order_by(
            models.CatalogProductBarcode.catalog_product_id.asc(),
            models.CatalogProductBarcode.is_valid_gtin.desc(),
            models.CatalogProductBarcode.barcode.asc(),
        )
        .all()
    ):
        barcodes.setdefault(int(product_id), barcode)
    for product_id, product in products.items():
        product.barcode = barcodes.get(product_id)
    return [products[product_id] for product_id in ordered_ids if product_id in products]


def search_tenant_products(db: Session, tenant_id: int, query: str, limit: int) -> list[Product]:
    variants = build_search_variants(query)
    if not variants.original:
        return []

    scores: dict[int, SearchScore] = {}
    barcode_ids = (
        db.query(Product.id)
        .filter(Product.tenant_id == tenant_id, Product.barcode == query.strip())
        .limit(limit)
        .all()
    )
    _merge_scores(scores, [(int(product_id), 0, 1.0) for (product_id,) in barcode_ids])

    if not scores:
        value = variants.original
        _merge_scores(
            scores,
            _alias_matches(
                db,
                alias_model=models.ProductAlias,
                product_id_column=models.ProductAlias.product_id,
                value=value,
                rank_offset=10,
                filters=(models.ProductAlias.tenant_id == tenant_id,),
            ),
        )
        _merge_scores(
            scores,
            _alias_matches(
                db,
                alias_model=models.CatalogProductAlias,
                product_id_column=Product.id,
                value=value,
                rank_offset=11,
                filters=(
                    Product.tenant_id == tenant_id,
                    Product.catalog_product_id == models.CatalogProductAlias.catalog_product_id,
                ),
            ),
        )
    if not scores and variants.arabic_keyboard:
        value = variants.arabic_keyboard
        _merge_scores(
            scores,
            _alias_matches(
                db,
                alias_model=models.ProductAlias,
                product_id_column=models.ProductAlias.product_id,
                value=value,
                rank_offset=15,
                filters=(models.ProductAlias.tenant_id == tenant_id,),
            ),
        )
        _merge_scores(
            scores,
            _alias_matches(
                db,
                alias_model=models.CatalogProductAlias,
                product_id_column=Product.id,
                value=value,
                rank_offset=16,
                filters=(
                    Product.tenant_id == tenant_id,
                    Product.catalog_product_id == models.CatalogProductAlias.catalog_product_id,
                ),
            ),
        )

    ordered_ids = [score.product_id for score in sorted(scores.values())[:limit]]
    if not ordered_ids:
        return []
    products = {
        product.id: product
        for product in db.query(Product)
        .filter(Product.tenant_id == tenant_id, Product.id.in_(ordered_ids))
        .all()
    }
    return [products[product_id] for product_id in ordered_ids if product_id in products]
