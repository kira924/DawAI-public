from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.modules.inventory.models import Product, ProductBatch, StockMovement
from src.modules.sales.models import QuarantineItem


@dataclass(frozen=True)
class StockReconciliationIssue:
    issue_type: str
    product_id: int
    batch_id: int | None
    stored_quantity: int
    ledger_quantity: int


@dataclass(frozen=True)
class StockReconciliationReport:
    checked_products: int
    checked_batches: int
    issue_count: int
    issues_truncated: bool
    issues: list[StockReconciliationIssue]


def record_stock_movement(
    db: Session,
    *,
    event_type: str,
    stock_bucket: str,
    quantity_delta: int,
    source_type: str,
    source_key: str,
    product_id: int,
    tenant_id: int,
    batch_id: int | None,
    actor_user_id: int | None,
    source_id: int | None = None,
    source_line_id: int | None = None,
) -> StockMovement:
    movement = StockMovement(
        event_type=event_type,
        stock_bucket=stock_bucket,
        quantity_delta=quantity_delta,
        source_type=source_type,
        source_id=source_id,
        source_line_id=source_line_id,
        source_key=source_key,
        actor_user_id=actor_user_id,
        product_id=product_id,
        batch_id=batch_id,
        tenant_id=tenant_id,
    )
    db.add(movement)
    db.flush()
    return movement


def get_stock_movements(
    db: Session,
    tenant_id: int,
    *,
    product_id: int | None = None,
    batch_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[StockMovement]:
    query = db.query(StockMovement).filter(StockMovement.tenant_id == tenant_id)
    if product_id is not None:
        query = query.filter(StockMovement.product_id == product_id)
    if batch_id is not None:
        query = query.filter(StockMovement.batch_id == batch_id)
    return (
        query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def reconcile_stock(
    db: Session,
    tenant_id: int,
    *,
    issue_limit: int = 100,
) -> StockReconciliationReport:
    products = (
        db.query(Product.id, Product.total_parts)
        .filter(Product.tenant_id == tenant_id)
        .order_by(Product.id.asc())
        .all()
    )
    batches = (
        db.query(ProductBatch.id, ProductBatch.product_id, ProductBatch.quantity)
        .filter(ProductBatch.tenant_id == tenant_id)
        .order_by(ProductBatch.id.asc())
        .all()
    )
    on_hand_by_batch = {
        batch_id: quantity or 0
        for batch_id, quantity in (
            db.query(StockMovement.batch_id, func.sum(StockMovement.quantity_delta))
            .filter(
                StockMovement.tenant_id == tenant_id,
                StockMovement.stock_bucket == "on_hand",
            )
            .group_by(StockMovement.batch_id)
            .all()
        )
    }
    on_hand_by_product = {
        product_id: quantity or 0
        for product_id, quantity in (
            db.query(StockMovement.product_id, func.sum(StockMovement.quantity_delta))
            .filter(
                StockMovement.tenant_id == tenant_id,
                StockMovement.stock_bucket == "on_hand",
            )
            .group_by(StockMovement.product_id)
            .all()
        )
    }
    quarantine_by_product = {
        product_id: quantity or 0
        for product_id, quantity in (
            db.query(QuarantineItem.product_id, func.sum(QuarantineItem.quantity))
            .filter(QuarantineItem.tenant_id == tenant_id)
            .group_by(QuarantineItem.product_id)
            .all()
        )
    }
    quarantine_ledger_by_product = {
        product_id: quantity or 0
        for product_id, quantity in (
            db.query(StockMovement.product_id, func.sum(StockMovement.quantity_delta))
            .filter(
                StockMovement.tenant_id == tenant_id,
                StockMovement.stock_bucket == "quarantine",
            )
            .group_by(StockMovement.product_id)
            .all()
        )
    }
    quarantine_by_batch = {
        (product_id, batch_id): quantity or 0
        for product_id, batch_id, quantity in (
            db.query(
                QuarantineItem.product_id,
                QuarantineItem.batch_id,
                func.sum(QuarantineItem.quantity),
            )
            .filter(QuarantineItem.tenant_id == tenant_id)
            .group_by(QuarantineItem.product_id, QuarantineItem.batch_id)
            .all()
        )
    }
    quarantine_ledger_by_batch = {
        (product_id, batch_id): quantity or 0
        for product_id, batch_id, quantity in (
            db.query(
                StockMovement.product_id,
                StockMovement.batch_id,
                func.sum(StockMovement.quantity_delta),
            )
            .filter(
                StockMovement.tenant_id == tenant_id,
                StockMovement.stock_bucket == "quarantine",
            )
            .group_by(StockMovement.product_id, StockMovement.batch_id)
            .all()
        )
    }

    issues: list[StockReconciliationIssue] = []
    batch_total_by_product: dict[int, int] = {}
    for batch_id, product_id, quantity in batches:
        batch_total_by_product[product_id] = batch_total_by_product.get(product_id, 0) + quantity
        ledger_quantity = on_hand_by_batch.get(batch_id, 0)
        if quantity != ledger_quantity:
            issues.append(
                StockReconciliationIssue(
                    issue_type="batch_ledger_mismatch",
                    product_id=product_id,
                    batch_id=batch_id,
                    stored_quantity=quantity,
                    ledger_quantity=ledger_quantity,
                )
            )

    for product_id, total_parts in products:
        batch_quantity = batch_total_by_product.get(product_id, 0)
        if total_parts != batch_quantity:
            issues.append(
                StockReconciliationIssue(
                    issue_type="product_batch_mismatch",
                    product_id=product_id,
                    batch_id=None,
                    stored_quantity=total_parts,
                    ledger_quantity=batch_quantity,
                )
            )
        ledger_quantity = on_hand_by_product.get(product_id, 0)
        if total_parts != ledger_quantity:
            issues.append(
                StockReconciliationIssue(
                    issue_type="product_ledger_mismatch",
                    product_id=product_id,
                    batch_id=None,
                    stored_quantity=total_parts,
                    ledger_quantity=ledger_quantity,
                )
            )

        quarantined_quantity = quarantine_by_product.get(product_id, 0)
        quarantine_ledger_quantity = quarantine_ledger_by_product.get(product_id, 0)
        if quarantined_quantity != quarantine_ledger_quantity:
            issues.append(
                StockReconciliationIssue(
                    issue_type="quarantine_ledger_mismatch",
                    product_id=product_id,
                    batch_id=None,
                    stored_quantity=quarantined_quantity,
                    ledger_quantity=quarantine_ledger_quantity,
                )
            )

    for product_id, batch_id in sorted(
        set(quarantine_by_batch) | set(quarantine_ledger_by_batch),
        key=lambda identity: (identity[0], identity[1] is None, identity[1] or 0),
    ):
        quarantined_quantity = quarantine_by_batch.get((product_id, batch_id), 0)
        ledger_quantity = quarantine_ledger_by_batch.get((product_id, batch_id), 0)
        if quarantined_quantity != ledger_quantity:
            issues.append(
                StockReconciliationIssue(
                    issue_type="quarantine_batch_ledger_mismatch",
                    product_id=product_id,
                    batch_id=batch_id,
                    stored_quantity=quarantined_quantity,
                    ledger_quantity=ledger_quantity,
                )
            )

    issue_count = len(issues)
    return StockReconciliationReport(
        checked_products=len(products),
        checked_batches=len(batches),
        issue_count=issue_count,
        issues_truncated=issue_count > issue_limit,
        issues=issues[:issue_limit],
    )
