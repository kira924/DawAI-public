import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.business_time import is_sellable_expiry, local_business_date
from src.modules.inventory import models, schemas
from src.modules.inventory.ledger import record_stock_movement
from src.modules.sales.models import QuarantineItem
from src.modules.users.schemas import UserRole


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _lock_id(namespace: str, tenant_id: int, identity: str | int) -> int:
    digest = hashlib.sha256(f"{namespace}:{tenant_id}:{identity}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _take_lock(db: Session, namespace: str, tenant_id: int, identity: str | int) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _lock_id(namespace, tenant_id, identity)},
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _role_value(user_role: str | UserRole) -> str:
    return user_role.value if isinstance(user_role, UserRole) else user_role


def _require_manager(user_role: str | UserRole) -> None:
    if _role_value(user_role) != UserRole.MANAGER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager permission is required.",
        )


def _require_count_owner_or_manager(
    stock_count: models.StockCount,
    *,
    user_id: int,
    user_role: str | UserRole,
) -> None:
    if (
        stock_count.created_by_user_id != user_id
        and _role_value(user_role) != UserRole.MANAGER.value
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the count creator or a manager can change this stock count.",
        )


def _validate_full_count_scope(
    db: Session,
    stock_count: models.StockCount,
    tenant_id: int,
) -> None:
    if stock_count.count_type != schemas.StockCountType.FULL.value:
        return
    counted_batch_ids = {item.batch_id for item in stock_count.items}
    current_batch_ids = {
        batch_id
        for (batch_id,) in db.query(models.ProductBatch.id)
        .filter(
            models.ProductBatch.tenant_id == tenant_id,
            models.ProductBatch.quantity > 0,
        )
        .all()
    }
    if counted_batch_ids != current_batch_ids:
        raise _conflict("The full stock-count scope changed. Create a fresh stock count.")


def create_stock_count(
    db: Session,
    payload: schemas.StockCountCreate,
    *,
    tenant_id: int,
    user_id: int,
) -> models.StockCount:
    idempotency_key = str(payload.idempotency_key)
    request_fingerprint = _fingerprint(
        {
            "actor_user_id": user_id,
            "payload": payload.model_dump(mode="json", exclude={"idempotency_key"}),
        }
    )
    try:
        _take_lock(db, "stock-count", tenant_id, idempotency_key)
        existing = (
            db.query(models.StockCount)
            .filter(
                models.StockCount.tenant_id == tenant_id,
                models.StockCount.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise _conflict("Idempotency key was already used with a different request.")
            db.commit()
            return existing

        requested_batch_ids = {item.batch_id for item in payload.items}
        batches = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.tenant_id == tenant_id,
                models.ProductBatch.id.in_(requested_batch_ids),
            )
            .order_by(models.ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        if {batch.id for batch in batches} != requested_batch_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")

        if payload.count_type == schemas.StockCountType.FULL:
            current_batch_ids = {
                batch_id
                for (batch_id,) in db.query(models.ProductBatch.id)
                .filter(
                    models.ProductBatch.tenant_id == tenant_id,
                    models.ProductBatch.quantity > 0,
                )
                .all()
            }
            if requested_batch_ids != current_batch_ids:
                raise _conflict("A full stock count must include every batch with physical stock.")

        items_by_batch = {item.batch_id: item for item in payload.items}
        stock_count = models.StockCount(
            count_type=payload.count_type.value,
            status="draft",
            notes=payload.notes,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            created_by_user_id=user_id,
            tenant_id=tenant_id,
        )
        db.add(stock_count)
        db.flush()
        for batch in batches:
            item = items_by_batch[batch.id]
            variance = item.counted_quantity - batch.quantity
            if variance == 0 and item.variance_reason is not None:
                raise _conflict("A matching count must not include a variance reason.")
            if variance != 0 and item.variance_reason is None:
                raise _conflict("Every stock variance requires a reason.")
            db.add(
                models.StockCountItem(
                    stock_count_id=stock_count.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    expected_quantity=batch.quantity,
                    counted_quantity=item.counted_quantity,
                    variance_reason=(
                        item.variance_reason.value if item.variance_reason is not None else None
                    ),
                    tenant_id=tenant_id,
                )
            )
        db.commit()
        db.refresh(stock_count)
        return stock_count
    except Exception:
        db.rollback()
        raise


def get_stock_counts(
    db: Session,
    tenant_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[models.StockCount]:
    return (
        db.query(models.StockCount)
        .filter(models.StockCount.tenant_id == tenant_id)
        .order_by(models.StockCount.created_at.desc(), models.StockCount.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def update_stock_count(
    db: Session,
    count_id: int,
    payload: schemas.StockCountUpdate,
    *,
    tenant_id: int,
    user_id: int,
    user_role: str | UserRole,
) -> models.StockCount:
    try:
        stock_count = (
            db.query(models.StockCount)
            .filter(models.StockCount.id == count_id, models.StockCount.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if stock_count is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Stock count not found."
            )
        _require_count_owner_or_manager(stock_count, user_id=user_id, user_role=user_role)
        if stock_count.status != "draft":
            raise _conflict("Only a draft stock count can be edited.")

        items_by_batch = {item.batch_id: item for item in stock_count.items}
        payload_by_batch = {item.batch_id: item for item in payload.items}
        if set(payload_by_batch) != set(items_by_batch):
            raise _conflict("A draft edit cannot change the original batch scope.")

        batches = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.tenant_id == tenant_id,
                models.ProductBatch.id.in_(items_by_batch),
            )
            .order_by(models.ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        quantities = {batch.id: batch.quantity for batch in batches}
        if len(quantities) != len(items_by_batch) or any(
            quantities.get(batch_id) != item.expected_quantity
            for batch_id, item in items_by_batch.items()
        ):
            raise _conflict("Stock changed after counting. Create a fresh stock count.")

        for batch_id, item in items_by_batch.items():
            update = payload_by_batch[batch_id]
            variance = update.counted_quantity - item.expected_quantity
            if variance == 0 and update.variance_reason is not None:
                raise _conflict("A matching count must not include a variance reason.")
            if variance != 0 and update.variance_reason is None:
                raise _conflict("Every stock variance requires a reason.")
            item.counted_quantity = update.counted_quantity
            item.variance_reason = (
                update.variance_reason.value if update.variance_reason is not None else None
            )
        stock_count.notes = payload.notes
        db.commit()
        db.refresh(stock_count)
        return stock_count
    except Exception:
        db.rollback()
        raise


def submit_stock_count(
    db: Session,
    count_id: int,
    *,
    tenant_id: int,
    user_id: int,
    user_role: str | UserRole,
) -> models.StockCount:
    try:
        stock_count = (
            db.query(models.StockCount)
            .filter(models.StockCount.id == count_id, models.StockCount.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if stock_count is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Stock count not found."
            )
        _require_count_owner_or_manager(stock_count, user_id=user_id, user_role=user_role)
        if stock_count.status == "submitted":
            db.commit()
            return stock_count
        if stock_count.status != "draft":
            raise _conflict("Only a draft stock count can be submitted.")

        batch_ids = [item.batch_id for item in stock_count.items]
        batches = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.tenant_id == tenant_id,
                models.ProductBatch.id.in_(batch_ids),
            )
            .order_by(models.ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        quantities = {batch.id: batch.quantity for batch in batches}
        _validate_full_count_scope(db, stock_count, tenant_id)
        if len(quantities) != len(batch_ids) or any(
            quantities.get(item.batch_id) != item.expected_quantity for item in stock_count.items
        ):
            raise _conflict("Stock changed after counting. Create a fresh stock count.")

        stock_count.status = "submitted"
        stock_count.submitted_by_user_id = user_id
        stock_count.submitted_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(stock_count)
        return stock_count
    except Exception:
        db.rollback()
        raise


def cancel_stock_count(
    db: Session,
    count_id: int,
    payload: schemas.StockCountCancel,
    *,
    tenant_id: int,
    user_id: int,
    user_role: str | UserRole,
) -> models.StockCount:
    try:
        stock_count = (
            db.query(models.StockCount)
            .filter(models.StockCount.id == count_id, models.StockCount.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if stock_count is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Stock count not found."
            )
        _require_count_owner_or_manager(stock_count, user_id=user_id, user_role=user_role)
        if stock_count.status == "cancelled":
            db.commit()
            return stock_count
        if stock_count.status not in {"draft", "submitted"}:
            raise _conflict("Only a draft or submitted stock count can be cancelled.")

        stock_count.status = "cancelled"
        stock_count.cancelled_by_user_id = user_id
        stock_count.cancelled_at = datetime.now(timezone.utc)
        stock_count.cancellation_reason = payload.reason.strip()
        db.commit()
        db.refresh(stock_count)
        return stock_count
    except Exception:
        db.rollback()
        raise


def apply_stock_count(
    db: Session,
    count_id: int,
    *,
    tenant_id: int,
    user_id: int,
    user_role: str | UserRole,
) -> models.StockCount:
    _require_manager(user_role)
    try:
        stock_count = (
            db.query(models.StockCount)
            .filter(models.StockCount.id == count_id, models.StockCount.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if stock_count is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Stock count not found."
            )
        if stock_count.status == "applied":
            db.commit()
            return stock_count
        if stock_count.status != "submitted":
            raise _conflict("Only a submitted stock count can be applied.")

        batch_ids = [item.batch_id for item in stock_count.items]
        product_ids = {item.product_id for item in stock_count.items}
        products = (
            db.query(models.Product)
            .filter(
                models.Product.tenant_id == tenant_id,
                models.Product.id.in_(product_ids),
            )
            .order_by(models.Product.id.asc())
            .with_for_update()
            .all()
        )
        batches = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.tenant_id == tenant_id,
                models.ProductBatch.id.in_(batch_ids),
            )
            .order_by(models.ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        batches_by_id = {batch.id: batch for batch in batches}
        products_by_id = {product.id: product for product in products}
        _validate_full_count_scope(db, stock_count, tenant_id)
        if len(batches_by_id) != len(batch_ids) or len(products_by_id) != len(product_ids):
            raise _conflict("The counted stock context no longer exists.")
        if any(
            batches_by_id[item.batch_id].quantity != item.expected_quantity
            for item in stock_count.items
        ):
            raise _conflict("Stock changed after submission. Create a fresh stock count.")

        applied_at = datetime.now(timezone.utc)
        stock_count.status = "applied"
        stock_count.applied_by_user_id = user_id
        stock_count.applied_at = applied_at
        db.flush()
        for item in stock_count.items:
            variance = item.counted_quantity - item.expected_quantity
            if variance == 0:
                continue
            batch = batches_by_id[item.batch_id]
            product = products_by_id[item.product_id]
            batch.quantity = item.counted_quantity
            product.total_parts += variance
            if product.total_parts < 0:
                raise _conflict("The stock count would make the product balance negative.")
            record_stock_movement(
                db,
                event_type="stock_adjustment",
                stock_bucket="on_hand",
                quantity_delta=variance,
                source_type="stock_count",
                source_id=stock_count.id,
                source_line_id=item.id,
                source_key=f"stock-count:item:{item.id}",
                actor_user_id=user_id,
                product_id=item.product_id,
                batch_id=item.batch_id,
                tenant_id=tenant_id,
            )
        db.commit()
        db.refresh(stock_count)
        return stock_count
    except Exception:
        db.rollback()
        raise


def create_quarantine_item(
    db: Session,
    payload: schemas.QuarantineCreate,
    *,
    tenant_id: int,
    user_id: int,
) -> QuarantineItem:
    idempotency_key = str(payload.idempotency_key)
    request_fingerprint = _fingerprint(
        {
            "actor_user_id": user_id,
            "payload": payload.model_dump(mode="json", exclude={"idempotency_key"}),
        }
    )
    try:
        _take_lock(db, "quarantine-item", tenant_id, idempotency_key)
        existing = (
            db.query(QuarantineItem)
            .filter(
                QuarantineItem.tenant_id == tenant_id,
                QuarantineItem.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise _conflict("Idempotency key was already used with a different request.")
            db.commit()
            return existing

        batch_context = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.id == payload.batch_id,
                models.ProductBatch.tenant_id == tenant_id,
            )
            .first()
        )
        if batch_context is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")
        product = (
            db.query(models.Product)
            .filter(
                models.Product.id == batch_context.product_id,
                models.Product.tenant_id == tenant_id,
            )
            .with_for_update()
            .one()
        )
        batch = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.id == payload.batch_id,
                models.ProductBatch.product_id == product.id,
                models.ProductBatch.tenant_id == tenant_id,
            )
            .with_for_update()
            .one()
        )
        is_expired = not is_sellable_expiry(batch.expiry_date, as_of=local_business_date())
        if is_expired and payload.reason != schemas.QuarantineReason.EXPIRED_BATCH:
            raise _conflict("Expired stock must use the expired_batch quarantine reason.")
        if not is_expired and payload.reason == schemas.QuarantineReason.EXPIRED_BATCH:
            raise _conflict("A non-expired batch cannot use the expired_batch quarantine reason.")
        if payload.reason == schemas.QuarantineReason.EXPIRED_BATCH and (
            payload.condition != schemas.QuarantineCondition.SEALED
        ):
            raise _conflict("Expired batch quarantine requires sealed condition.")
        if batch.quantity < payload.quantity or product.total_parts < payload.quantity:
            raise _conflict("Quarantine quantity exceeds physical on-hand stock.")

        quarantine_item = QuarantineItem(
            sales_return_item_id=None,
            product_id=product.id,
            batch_id=batch.id,
            quantity=payload.quantity,
            initial_quantity=payload.quantity,
            condition=payload.condition.value,
            reason=payload.reason.value,
            status="pending",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            created_by_user_id=user_id,
            tenant_id=tenant_id,
        )
        db.add(quarantine_item)
        db.flush()
        batch.quantity -= payload.quantity
        product.total_parts -= payload.quantity
        record_stock_movement(
            db,
            event_type="quarantine_transfer_out",
            stock_bucket="on_hand",
            quantity_delta=-payload.quantity,
            source_type="quarantine_item",
            source_id=quarantine_item.id,
            source_line_id=quarantine_item.id,
            source_key=f"quarantine-item:{quarantine_item.id}:on-hand-out",
            actor_user_id=user_id,
            product_id=product.id,
            batch_id=batch.id,
            tenant_id=tenant_id,
        )
        record_stock_movement(
            db,
            event_type="quarantine_transfer_in",
            stock_bucket="quarantine",
            quantity_delta=payload.quantity,
            source_type="quarantine_item",
            source_id=quarantine_item.id,
            source_line_id=quarantine_item.id,
            source_key=f"quarantine-item:{quarantine_item.id}:quarantine-in",
            actor_user_id=user_id,
            product_id=product.id,
            batch_id=batch.id,
            tenant_id=tenant_id,
        )
        db.commit()
        db.refresh(quarantine_item)
        return quarantine_item
    except Exception:
        db.rollback()
        raise


def get_quarantine_items(
    db: Session,
    tenant_id: int,
    *,
    include_resolved: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> list[QuarantineItem]:
    query = db.query(QuarantineItem).filter(QuarantineItem.tenant_id == tenant_id)
    if not include_resolved:
        query = query.filter(QuarantineItem.status == "pending")
    return (
        query.order_by(QuarantineItem.created_at.desc(), QuarantineItem.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_quarantine_disposition(
    db: Session,
    quarantine_item_id: int,
    payload: schemas.QuarantineDispositionCreate,
    *,
    tenant_id: int,
    user_id: int,
    user_role: str | UserRole,
) -> models.QuarantineDisposition:
    _require_manager(user_role)
    idempotency_key = str(payload.idempotency_key)
    request_fingerprint = _fingerprint(
        {
            "actor_user_id": user_id,
            "quarantine_item_id": quarantine_item_id,
            "payload": payload.model_dump(mode="json", exclude={"idempotency_key"}),
        }
    )
    try:
        _take_lock(db, "quarantine-disposition", tenant_id, idempotency_key)
        existing = (
            db.query(models.QuarantineDisposition)
            .filter(
                models.QuarantineDisposition.tenant_id == tenant_id,
                models.QuarantineDisposition.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise _conflict("Idempotency key was already used with a different request.")
            db.commit()
            return existing

        item = (
            db.query(QuarantineItem)
            .filter(QuarantineItem.id == quarantine_item_id, QuarantineItem.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Quarantine item not found."
            )
        if item.batch_id is None:
            raise _conflict("Disposition requires an exact source batch.")
        if item.quantity < payload.quantity:
            raise _conflict("Disposition quantity exceeds the pending quarantine quantity.")

        product = (
            db.query(models.Product)
            .filter(models.Product.id == item.product_id, models.Product.tenant_id == tenant_id)
            .with_for_update()
            .one()
        )
        batch = (
            db.query(models.ProductBatch)
            .filter(
                models.ProductBatch.id == item.batch_id,
                models.ProductBatch.product_id == item.product_id,
                models.ProductBatch.tenant_id == tenant_id,
            )
            .with_for_update()
            .one()
        )
        if payload.action == schemas.QuarantineDispositionAction.RELEASE:
            if item.condition != schemas.QuarantineCondition.SEALED.value:
                raise _conflict("Only sealed quarantined stock can be released.")
            if not is_sellable_expiry(batch.expiry_date, as_of=local_business_date()):
                raise _conflict("Expired stock cannot be released from quarantine.")

        disposition = models.QuarantineDisposition(
            quarantine_item_id=item.id,
            action=payload.action.value,
            quantity=payload.quantity,
            reason=payload.reason.strip(),
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            performed_by_user_id=user_id,
            product_id=item.product_id,
            batch_id=batch.id,
            tenant_id=tenant_id,
        )
        db.add(disposition)
        db.flush()
        item.quantity -= payload.quantity
        item.status = "resolved" if item.quantity == 0 else "pending"

        if payload.action == schemas.QuarantineDispositionAction.RELEASE:
            batch.quantity += payload.quantity
            product.total_parts += payload.quantity
            event_type = "quarantine_release_out"
        else:
            event_type = "quarantine_disposition"
        record_stock_movement(
            db,
            event_type=event_type,
            stock_bucket="quarantine",
            quantity_delta=-payload.quantity,
            source_type="quarantine_disposition",
            source_id=disposition.id,
            source_line_id=item.id,
            source_key=f"quarantine-disposition:{disposition.id}:quarantine-out",
            actor_user_id=user_id,
            product_id=item.product_id,
            batch_id=batch.id,
            tenant_id=tenant_id,
        )
        if payload.action == schemas.QuarantineDispositionAction.RELEASE:
            record_stock_movement(
                db,
                event_type="quarantine_release_in",
                stock_bucket="on_hand",
                quantity_delta=payload.quantity,
                source_type="quarantine_disposition",
                source_id=disposition.id,
                source_line_id=item.id,
                source_key=f"quarantine-disposition:{disposition.id}:on-hand-in",
                actor_user_id=user_id,
                product_id=item.product_id,
                batch_id=batch.id,
                tenant_id=tenant_id,
            )
        db.commit()
        db.refresh(disposition)
        return disposition
    except Exception:
        db.rollback()
        raise
