from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.business_time import local_business_date
from src.modules.inventory import models, schemas


def _sellable_stock_subquery(db: Session, tenant_id: int, inventory_date: date):
    return (
        db.query(
            models.ProductBatch.product_id.label("product_id"),
            func.sum(models.ProductBatch.quantity).label("sellable_parts"),
        )
        .filter(
            models.ProductBatch.tenant_id == tenant_id,
            models.ProductBatch.quantity > 0,
            models.ProductBatch.expiry_date >= inventory_date,
        )
        .group_by(models.ProductBatch.product_id)
        .subquery()
    )


def get_replenishment_recommendations(
    db: Session,
    tenant_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
    as_of: date | None = None,
) -> list[schemas.ReplenishmentRecommendationResponse]:
    inventory_date = as_of or local_business_date()
    sellable_stock = _sellable_stock_subquery(db, tenant_id, inventory_date)
    sellable_parts = func.coalesce(sellable_stock.c.sellable_parts, 0)
    suggested_order_parts = models.Product.target_stock_parts - sellable_parts

    rows = (
        db.query(models.Product, sellable_parts.label("sellable_parts"))
        .outerjoin(sellable_stock, sellable_stock.c.product_id == models.Product.id)
        .filter(
            models.Product.tenant_id == tenant_id,
            models.Product.reorder_point_parts.is_not(None),
            models.Product.target_stock_parts.is_not(None),
            sellable_parts <= models.Product.reorder_point_parts,
        )
        .order_by(suggested_order_parts.desc(), models.Product.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    recommendations = []
    for product, current_sellable_parts in rows:
        effective_parts_per_unit = (
            product.parts_per_unit
            if product.is_divisible and product.parts_per_unit and product.parts_per_unit > 1
            else 1
        )
        suggested_parts = product.target_stock_parts - current_sellable_parts
        recommendations.append(
            schemas.ReplenishmentRecommendationResponse(
                product_id=product.id,
                product_name=product.name,
                barcode=product.barcode,
                parts_per_unit=effective_parts_per_unit,
                sellable_parts=current_sellable_parts,
                reorder_point_parts=product.reorder_point_parts,
                target_stock_parts=product.target_stock_parts,
                suggested_order_parts=suggested_parts,
                suggested_full_units=suggested_parts // effective_parts_per_unit,
                suggested_loose_parts=suggested_parts % effective_parts_per_unit,
                recommendation_source=schemas.ReplenishmentRecommendationSource.MANUAL_POLICY,
                inventory_date=inventory_date,
            )
        )
    return recommendations


def count_replenishment_due(
    db: Session,
    tenant_id: int,
    *,
    as_of: date | None = None,
) -> int:
    inventory_date = as_of or local_business_date()
    sellable_stock = _sellable_stock_subquery(db, tenant_id, inventory_date)
    sellable_parts = func.coalesce(sellable_stock.c.sellable_parts, 0)
    return (
        db.query(func.count(models.Product.id))
        .outerjoin(sellable_stock, sellable_stock.c.product_id == models.Product.id)
        .filter(
            models.Product.tenant_id == tenant_id,
            models.Product.reorder_point_parts.is_not(None),
            models.Product.target_stock_parts.is_not(None),
            sellable_parts <= models.Product.reorder_point_parts,
        )
        .scalar()
        or 0
    )


def set_replenishment_policy(
    db: Session,
    product_id: int,
    tenant_id: int,
    policy: schemas.ReplenishmentPolicyUpsert,
) -> schemas.ReplenishmentPolicyResponse | None:
    product = (
        db.query(models.Product)
        .filter(models.Product.id == product_id, models.Product.tenant_id == tenant_id)
        .with_for_update()
        .first()
    )
    if product is None:
        return None

    product.reorder_point_parts = policy.reorder_point_parts
    product.target_stock_parts = policy.target_stock_parts
    db.commit()
    db.refresh(product)
    return schemas.ReplenishmentPolicyResponse(
        product_id=product.id,
        tenant_id=product.tenant_id,
        reorder_point_parts=product.reorder_point_parts,
        target_stock_parts=product.target_stock_parts,
        is_configured=product.reorder_point_parts is not None,
    )
