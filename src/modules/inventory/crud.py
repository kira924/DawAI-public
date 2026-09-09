from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.business_time import ExpiryStatus, local_business_date
from src.modules.catalog.models import CatalogProduct, ProductAlias
from src.modules.catalog.normalization import normalize_search_text
from src.modules.inventory import ledger, models, schemas
from src.modules.inventory.policy import validate_incoming_expiry
from src.modules.sales.models import QuarantineItem


class CatalogProductNotFoundError(ValueError):
    pass


class CatalogProductAlreadyLinkedError(ValueError):
    pass


def _is_duplicate_catalog_link(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == "uq_products_tenant_catalog_product"


def _alias_language(value: str) -> str:
    if any("\u0600" <= character <= "\u06ff" for character in value):
        return "ar"
    if any("a" <= character.casefold() <= "z" for character in value):
        return "en"
    return "und"


def _validate_catalog_product(
    db: Session,
    catalog_product_id: int | None,
    tenant_id: int,
    *,
    exclude_product_id: int | None = None,
) -> None:
    if catalog_product_id is None:
        return
    exists = (
        db.query(CatalogProduct.id)
        .filter(CatalogProduct.id == catalog_product_id, CatalogProduct.is_active.is_(True))
        .first()
    )
    if exists is None:
        raise CatalogProductNotFoundError("Catalog product not found")

    linked_query = db.query(models.Product.id).filter(
        models.Product.tenant_id == tenant_id,
        models.Product.catalog_product_id == catalog_product_id,
    )
    if exclude_product_id is not None:
        linked_query = linked_query.filter(models.Product.id != exclude_product_id)
    if linked_query.first() is not None:
        raise CatalogProductAlreadyLinkedError(
            "Catalog product is already linked to this pharmacy inventory"
        )


def _sync_primary_alias(db: Session, product: models.Product) -> None:
    normalized = normalize_search_text(product.name)
    if not normalized:
        raise ValueError("Product name must contain searchable characters")
    alias = (
        db.query(ProductAlias)
        .filter(
            ProductAlias.product_id == product.id,
            ProductAlias.tenant_id == product.tenant_id,
            ProductAlias.alias_type == "primary_name",
        )
        .first()
    )
    if alias is None:
        alias = ProductAlias(
            product_id=product.id,
            tenant_id=product.tenant_id,
            alias_type="primary_name",
            source_name="manual",
        )
        db.add(alias)
    alias.alias = product.name
    alias.normalized_alias = normalized
    alias.language = _alias_language(product.name)


def attach_stock_breakdown(db: Session, products: list[models.Product]) -> None:
    if not products:
        return

    product_ids = [product.id for product in products]
    today = local_business_date()
    batch_totals = {
        product_id: (batch_parts or 0, sellable_parts or 0, expired_parts or 0)
        for product_id, batch_parts, sellable_parts, expired_parts in (
            db.query(
                models.ProductBatch.product_id,
                func.sum(models.ProductBatch.quantity),
                func.sum(
                    case(
                        (models.ProductBatch.expiry_date >= today, models.ProductBatch.quantity),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (models.ProductBatch.expiry_date < today, models.ProductBatch.quantity),
                        else_=0,
                    )
                ),
            )
            .filter(
                models.ProductBatch.product_id.in_(product_ids),
                models.ProductBatch.tenant_id == products[0].tenant_id,
            )
            .group_by(models.ProductBatch.product_id)
            .all()
        )
    }
    quarantine_totals = {
        product_id: quantity or 0
        for product_id, quantity in (
            db.query(QuarantineItem.product_id, func.sum(QuarantineItem.quantity))
            .filter(
                QuarantineItem.product_id.in_(product_ids),
                QuarantineItem.tenant_id == products[0].tenant_id,
            )
            .group_by(QuarantineItem.product_id)
            .all()
        )
    }

    for product in products:
        batch_parts, sellable_parts, expired_parts = batch_totals.get(product.id, (0, 0, 0))
        product.sellable_parts = sellable_parts
        product.expired_parts = expired_parts
        product.quarantined_parts = quarantine_totals.get(product.id, 0)
        product.batch_parts = batch_parts


def create_product(
    db: Session,
    product: schemas.ProductCreate,
    tenant_id: int,
    actor_role: str = "pharmacist",
    actor_user_id: int | None = None,
):
    _validate_catalog_product(db, product.catalog_product_id, tenant_id)

    # 1. Calculate the total absolute parts
    # Ensure parts_per_unit is at least 1 to avoid division by zero later
    safe_parts_per_unit = (
        product.parts_per_unit if product.is_divisible and product.parts_per_unit > 0 else 1
    )

    calculated_total_parts = (product.initial_boxes * safe_parts_per_unit) + product.initial_parts

    # 2. Convert schema to dict and remove the temporary input fields
    product_data = product.model_dump(
        exclude={"initial_boxes", "initial_parts", "initial_batch", "confirm_near_expiry"}
    )

    # 3. Inject the calculated total_parts and the tenant_id safely
    db_product = models.Product(
        **product_data, total_parts=calculated_total_parts, tenant_id=tenant_id
    )

    try:
        db.add(db_product)
        db.flush()
        _sync_primary_alias(db, db_product)

        if calculated_total_parts > 0:
            if product.initial_batch is None:
                raise ValueError("Opening stock requires batch details")
            expiry_state = validate_incoming_expiry(
                product.initial_batch.expiry_date,
                confirm_near_expiry=product.confirm_near_expiry,
                actor_role=actor_role,
            )
            db_batch = models.ProductBatch(
                batch_number=product.initial_batch.batch_number,
                expiry_date=product.initial_batch.expiry_date,
                quantity=calculated_total_parts,
                product_id=db_product.id,
                tenant_id=tenant_id,
            )
            db.add(db_batch)
            db.flush()
            ledger.record_stock_movement(
                db,
                event_type="opening_stock",
                stock_bucket="on_hand",
                quantity_delta=calculated_total_parts,
                source_type="product",
                source_id=db_product.id,
                source_line_id=db_batch.id,
                source_key=f"opening-stock:product:{db_product.id}:batch:{db_batch.id}",
                actor_user_id=actor_user_id,
                product_id=db_product.id,
                batch_id=db_batch.id,
                tenant_id=tenant_id,
            )
            if expiry_state == ExpiryStatus.CRITICAL:
                if actor_user_id is None:
                    raise ValueError("Manager identity is required for near-expiry confirmation")
                db.add(
                    models.NearExpiryReceiptConfirmation(
                        source="opening_stock",
                        quantity=calculated_total_parts,
                        expiry_date=db_batch.expiry_date,
                        batch_id=db_batch.id,
                        product_id=db_product.id,
                        purchase_order_id=None,
                        confirmed_by_user_id=actor_user_id,
                        tenant_id=tenant_id,
                    )
                )

        db.commit()
        db.refresh(db_product)
        attach_stock_breakdown(db, [db_product])
        return db_product
    except IntegrityError as exc:
        db.rollback()
        if _is_duplicate_catalog_link(exc):
            raise CatalogProductAlreadyLinkedError(
                "Catalog product is already linked to this pharmacy inventory"
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


def get_products(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    products = (
        db.query(models.Product)
        .filter(models.Product.tenant_id == tenant_id)
        .order_by(models.Product.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    attach_stock_breakdown(db, products)
    return products


def get_product(db: Session, product_id: int, tenant_id: int):
    product = (
        db.query(models.Product)
        .filter(models.Product.id == product_id, models.Product.tenant_id == tenant_id)
        .first()
    )
    if product is not None:
        attach_stock_breakdown(db, [product])
    return product


def update_product(db: Session, db_product: models.Product, product_update: schemas.ProductUpdate):
    update_data = product_update.model_dump(exclude_unset=True)
    if "catalog_product_id" in update_data:
        _validate_catalog_product(
            db,
            update_data["catalog_product_id"],
            db_product.tenant_id,
            exclude_product_id=db_product.id,
        )

    try:
        for key, value in update_data.items():
            setattr(db_product, key, value)
        if "name" in update_data:
            _sync_primary_alias(db, db_product)

        db.commit()
        db.refresh(db_product)
        attach_stock_breakdown(db, [db_product])
        return db_product
    except IntegrityError as exc:
        db.rollback()
        if _is_duplicate_catalog_link(exc):
            raise CatalogProductAlreadyLinkedError(
                "Catalog product is already linked to this pharmacy inventory"
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


def get_product_batches(
    db: Session, tenant_id: int, product_id: Optional[int] = None, skip: int = 0, limit: int = 100
):
    query = db.query(models.ProductBatch).filter(models.ProductBatch.tenant_id == tenant_id)

    # If the frontend passes a specific product_id, filter by it
    if product_id:
        query = query.filter(models.ProductBatch.product_id == product_id)

    # Order by expiry date so the oldest batches appear first (FEFO: First Expire, First Out)
    return (
        query.order_by(models.ProductBatch.expiry_date.asc(), models.ProductBatch.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
