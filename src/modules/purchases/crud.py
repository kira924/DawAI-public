import hashlib
import json
from decimal import Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.core.business_time import ExpiryStatus
from src.core.money import ZERO_MONEY, quantize_money
from src.modules.finance.ledger import credit, debit, record_financial_transaction
from src.modules.inventory.ledger import record_stock_movement
from src.modules.inventory.models import (
    NearExpiryReceiptConfirmation,
    Product,
    ProductBatch,
)
from src.modules.inventory.policy import validate_incoming_expiry
from src.modules.purchases import models, schemas
from src.modules.shifts.crud import get_active_shift


def _fingerprint(payload: BaseModel) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _advisory_lock_id(namespace: str, tenant_id: int, identity: str | int) -> int:
    digest = hashlib.sha256(f"{namespace}:{tenant_id}:{identity}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _take_advisory_lock(
    db: Session,
    namespace: str,
    tenant_id: int,
    identity: str | int,
) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _advisory_lock_id(namespace, tenant_id, identity)},
    )


def _get_supplier_for_tenant(
    db: Session,
    supplier_id: int,
    tenant_id: int,
    *,
    for_update: bool = False,
):
    query = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id,
        models.Supplier.tenant_id == tenant_id,
    )
    if for_update:
        query = query.with_for_update()
    db_supplier = query.first()
    if not db_supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )
    return db_supplier


def _lock_supplier_account(db: Session, supplier_id: int, tenant_id: int):
    _take_advisory_lock(db, "supplier-account", tenant_id, supplier_id)
    return _get_supplier_for_tenant(db, supplier_id, tenant_id, for_update=True)


def _supplier_balance_components(
    db: Session,
    supplier_id: int,
    tenant_id: int,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    total_purchases = quantize_money(
        db.query(func.sum(models.PurchaseOrder.total_cost))
        .filter(
            models.PurchaseOrder.supplier_id == supplier_id,
            models.PurchaseOrder.tenant_id == tenant_id,
            models.PurchaseOrder.status == "RECEIVED",
        )
        .scalar()
        or ZERO_MONEY
    )
    total_payments = quantize_money(
        db.query(func.sum(models.SupplierPayment.amount))
        .filter(
            models.SupplierPayment.supplier_id == supplier_id,
            models.SupplierPayment.tenant_id == tenant_id,
        )
        .scalar()
        or ZERO_MONEY
    )
    total_returns = quantize_money(
        db.query(func.sum(models.PurchaseReturn.total_refund))
        .filter(
            models.PurchaseReturn.supplier_id == supplier_id,
            models.PurchaseReturn.tenant_id == tenant_id,
        )
        .scalar()
        or ZERO_MONEY
    )
    current_balance = quantize_money(total_purchases - total_payments - total_returns)
    return total_purchases, total_payments, total_returns, current_balance


def create_supplier(db: Session, supplier: schemas.SupplierCreate, tenant_id: int):
    db_supplier = models.Supplier(
        name=supplier.name,
        contact_info=supplier.contact_info,
        tenant_id=tenant_id,
    )
    db.add(db_supplier)
    db.commit()
    db.refresh(db_supplier)
    return db_supplier


def get_suppliers(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.Supplier)
        .filter(models.Supplier.tenant_id == tenant_id)
        .order_by(models.Supplier.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_supplier_by_id(db: Session, supplier_id: int, tenant_id: int):
    return _get_supplier_for_tenant(db, supplier_id, tenant_id)


def create_purchase_order(
    db: Session,
    order: schemas.PurchaseOrderCreate,
    user_id: int,
    tenant_id: int,
):
    _get_supplier_for_tenant(db, order.supplier_id, tenant_id)

    product_ids = {item.product_id for item in order.items}
    products = (
        db.query(Product).filter(Product.id.in_(product_ids), Product.tenant_id == tenant_id).all()
    )
    if {product.id for product in products} != product_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more products were not found.",
        )

    prepared_items: list[tuple[schemas.PurchaseItemCreate, Decimal, Decimal, Decimal]] = []
    subtotal = ZERO_MONEY
    total_discount = ZERO_MONEY
    for item in order.items:
        base_cost = quantize_money(item.base_cost)
        unit_cost = quantize_money(
            base_cost * (Decimal("100") - item.discount_percentage) / Decimal("100")
        )
        line_subtotal = quantize_money(unit_cost * item.quantity)
        line_discount = quantize_money(base_cost * item.quantity - line_subtotal)
        subtotal += quantize_money(base_cost * item.quantity)
        total_discount += line_discount
        prepared_items.append((item, unit_cost, line_subtotal, line_discount))

    subtotal = quantize_money(subtotal)
    total_discount = quantize_money(total_discount)
    net_subtotal = quantize_money(subtotal - total_discount)
    tax_value = quantize_money(net_subtotal * order.tax_percentage / Decimal("100"))
    total_cost = quantize_money(net_subtotal + tax_value)

    db_order = models.PurchaseOrder(
        supplier_id=order.supplier_id,
        status="PENDING",
        subtotal=subtotal,
        total_discount=total_discount,
        tax_percentage=order.tax_percentage,
        tax_value=tax_value,
        total_cost=total_cost,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    db.add(db_order)

    try:
        db.flush()
        for item, unit_cost, line_subtotal, line_discount in prepared_items:
            db.add(
                models.PurchaseItem(
                    purchase_id=db_order.id,
                    product_id=item.product_id,
                    tenant_id=tenant_id,
                    quantity=item.quantity,
                    base_cost=quantize_money(item.base_cost),
                    discount_percentage=item.discount_percentage,
                    discount_value=line_discount,
                    unit_cost=unit_cost,
                    subtotal=line_subtotal,
                    batch_number=item.batch_number,
                    expiry_date=item.expiry_date,
                )
            )
        db.commit()
        db.refresh(db_order)
        return db_order
    except Exception:
        db.rollback()
        raise


def receive_purchase_order(
    db: Session,
    order_id: int,
    tenant_id: int,
    *,
    user_id: int | None = None,
    actor_role: str = "pharmacist",
    confirm_near_expiry: bool = False,
):
    if user_id is None:
        raise ValueError("A user identity is required to receive a purchase order")
    order_identity = (
        db.query(models.PurchaseOrder.supplier_id)
        .filter(
            models.PurchaseOrder.id == order_id,
            models.PurchaseOrder.tenant_id == tenant_id,
        )
        .first()
    )
    if not order_identity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Purchase order not found",
        )

    try:
        _lock_supplier_account(db, order_identity.supplier_id, tenant_id)
        db_order = (
            db.query(models.PurchaseOrder)
            .filter(
                models.PurchaseOrder.id == order_id,
                models.PurchaseOrder.tenant_id == tenant_id,
            )
            .with_for_update()
            .one()
        )
        if db_order.supplier_id != order_identity.supplier_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Purchase order supplier changed; retry the receipt.",
            )
        if db_order.status == "RECEIVED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order has already been received",
            )
        if db_order.status == "CANCELLED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot receive a cancelled order",
            )

        items = (
            db.query(models.PurchaseItem)
            .filter(
                models.PurchaseItem.purchase_id == db_order.id,
                models.PurchaseItem.tenant_id == tenant_id,
            )
            .order_by(models.PurchaseItem.product_id.asc(), models.PurchaseItem.id.asc())
            .with_for_update()
            .all()
        )
        if not items:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Purchase order has no items.",
            )

        expiry_states: dict[int, ExpiryStatus] = {}
        for item in items:
            if not item.batch_number or not item.expiry_date:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Batch data is required for purchase item ID {item.id}.",
                )
            expiry_states[item.id] = validate_incoming_expiry(
                item.expiry_date,
                confirm_near_expiry=confirm_near_expiry,
                actor_role=actor_role,
            )
        if ExpiryStatus.CRITICAL in expiry_states.values() and user_id is None:
            raise ValueError("Manager identity is required for near-expiry confirmation")

        product_ids = {item.product_id for item in items}
        products = (
            db.query(Product)
            .filter(Product.id.in_(product_ids), Product.tenant_id == tenant_id)
            .order_by(Product.id.asc())
            .with_for_update()
            .all()
        )
        products_by_id = {product.id: product for product in products}
        if set(products_by_id) != product_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more purchase products require reconciliation.",
            )

        batches = (
            db.query(ProductBatch)
            .filter(
                ProductBatch.product_id.in_(product_ids),
                ProductBatch.tenant_id == tenant_id,
            )
            .order_by(ProductBatch.product_id.asc(), ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        batches_by_key = {(batch.product_id, batch.batch_number): batch for batch in batches}
        for product_id, product in products_by_id.items():
            batch_total = sum(batch.quantity for batch in batches if batch.product_id == product_id)
            if product.total_parts != batch_total:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Inventory for product ID {product.id} requires reconciliation.",
                )

        for item in items:
            batch = batches_by_key.get((item.product_id, item.batch_number))
            if batch is None:
                batch = ProductBatch(
                    batch_number=item.batch_number,
                    expiry_date=item.expiry_date,
                    quantity=0,
                    product_id=item.product_id,
                    tenant_id=tenant_id,
                )
                db.add(batch)
                db.flush()
                batches_by_key[(item.product_id, item.batch_number)] = batch
            elif batch.expiry_date != item.expiry_date:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Batch expiry mismatch for purchase item ID {item.id}.",
                )

            batch.quantity += item.quantity
            products_by_id[item.product_id].total_parts += item.quantity
            item.received_batch_id = batch.id
            db.flush()
            record_stock_movement(
                db,
                event_type="purchase_receipt",
                stock_bucket="on_hand",
                quantity_delta=item.quantity,
                source_type="purchase_order",
                source_id=db_order.id,
                source_line_id=item.id,
                source_key=f"purchase-receipt:item:{item.id}:batch:{batch.id}",
                actor_user_id=user_id,
                product_id=item.product_id,
                batch_id=batch.id,
                tenant_id=tenant_id,
            )
            if expiry_states[item.id] == ExpiryStatus.CRITICAL:
                db.add(
                    NearExpiryReceiptConfirmation(
                        source="purchase_receipt",
                        quantity=item.quantity,
                        expiry_date=item.expiry_date,
                        batch_id=batch.id,
                        product_id=item.product_id,
                        purchase_order_id=db_order.id,
                        confirmed_by_user_id=user_id,
                        tenant_id=tenant_id,
                    )
                )

        db_order.status = "RECEIVED"
        if db_order.total_cost > ZERO_MONEY:
            record_financial_transaction(
                db,
                event_type="purchase_receipt",
                source_type="purchase_order",
                source_id=db_order.id,
                source_key=f"purchase-receipt:order:{db_order.id}",
                actor_user_id=user_id,
                supplier_id=db_order.supplier_id,
                tenant_id=tenant_id,
                lines=[
                    debit("inventory_purchases", db_order.total_cost),
                    credit("supplier_payable", db_order.total_cost),
                ],
            )
        db.commit()
        db.refresh(db_order)
        return db_order
    except Exception:
        db.rollback()
        raise


def cancel_purchase_order(db: Session, order_id: int, tenant_id: int):
    db_order = (
        db.query(models.PurchaseOrder)
        .filter(
            models.PurchaseOrder.id == order_id,
            models.PurchaseOrder.tenant_id == tenant_id,
        )
        .with_for_update()
        .first()
    )
    if not db_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Purchase order not found",
        )
    if db_order.status == "RECEIVED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel a received order. A purchase return is required.",
        )
    if db_order.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order is already cancelled.",
        )

    try:
        db_order.status = "CANCELLED"
        db.commit()
        db.refresh(db_order)
        return db_order
    except Exception:
        db.rollback()
        raise


def get_purchase_orders(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.PurchaseOrder)
        .filter(models.PurchaseOrder.tenant_id == tenant_id)
        .order_by(models.PurchaseOrder.created_at.desc(), models.PurchaseOrder.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_supplier_payment(
    db: Session,
    payment: schemas.SupplierPaymentCreate,
    user_id: int,
    tenant_id: int,
):
    idempotency_key = str(payment.idempotency_key)
    request_fingerprint = _fingerprint(payment)

    try:
        _take_advisory_lock(db, "supplier-payment", tenant_id, idempotency_key)
        existing_payment = (
            db.query(models.SupplierPayment)
            .filter(
                models.SupplierPayment.tenant_id == tenant_id,
                models.SupplierPayment.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing_payment:
            if existing_payment.request_fingerprint != request_fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key was already used with a different request.",
                )
            db.commit()
            return existing_payment

        _lock_supplier_account(db, payment.supplier_id, tenant_id)
        *_, current_balance = _supplier_balance_components(
            db,
            payment.supplier_id,
            tenant_id,
        )
        amount = quantize_money(payment.amount)
        if current_balance <= ZERO_MONEY:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Supplier has no payable balance.",
            )
        if amount > current_balance:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment exceeds the supplier payable balance.",
            )

        active_shift = None
        if payment.payment_channel == schemas.SupplierPaymentChannel.CASH:
            active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
            if not active_shift:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A cash supplier payment requires an active shift.",
                )

        db_payment = models.SupplierPayment(
            supplier_id=payment.supplier_id,
            amount=amount,
            payment_channel=payment.payment_channel.value,
            shift_id=active_shift.id if active_shift else None,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            reference_number=payment.reference_number,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        db.add(db_payment)
        db.flush()
        funding_account = (
            "cash_drawer"
            if payment.payment_channel == schemas.SupplierPaymentChannel.CASH
            else "external_funds"
        )
        record_financial_transaction(
            db,
            event_type="supplier_payment",
            source_type="supplier_payment",
            source_id=db_payment.id,
            source_key=f"supplier-payment:{db_payment.id}",
            actor_user_id=user_id,
            shift_id=active_shift.id if active_shift else None,
            supplier_id=payment.supplier_id,
            tenant_id=tenant_id,
            lines=[
                debit("supplier_payable", amount),
                credit(funding_account, amount),
            ],
        )
        db.commit()
        db.refresh(db_payment)
        return db_payment
    except Exception:
        db.rollback()
        raise


def create_purchase_return(
    db: Session,
    return_data: schemas.PurchaseReturnCreate,
    user_id: int,
    tenant_id: int,
):
    idempotency_key = str(return_data.idempotency_key)
    request_fingerprint = _fingerprint(return_data)

    try:
        _take_advisory_lock(db, "purchase-return", tenant_id, idempotency_key)
        existing_return = (
            db.query(models.PurchaseReturn)
            .filter(
                models.PurchaseReturn.tenant_id == tenant_id,
                models.PurchaseReturn.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing_return:
            if existing_return.request_fingerprint != request_fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key was already used with a different request.",
                )
            db.commit()
            return existing_return

        order_identity = (
            db.query(models.PurchaseOrder.supplier_id)
            .filter(
                models.PurchaseOrder.id == return_data.purchase_order_id,
                models.PurchaseOrder.tenant_id == tenant_id,
            )
            .first()
        )
        if not order_identity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Original purchase order not found.",
            )

        _lock_supplier_account(db, order_identity.supplier_id, tenant_id)
        db_order = (
            db.query(models.PurchaseOrder)
            .filter(
                models.PurchaseOrder.id == return_data.purchase_order_id,
                models.PurchaseOrder.tenant_id == tenant_id,
            )
            .with_for_update()
            .one()
        )
        if db_order.supplier_id != order_identity.supplier_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Purchase order supplier changed; retry the return.",
            )
        if db_order.status != "RECEIVED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only received purchase orders can be returned.",
            )

        requested_item_ids = {item.purchase_item_id for item in return_data.items}
        purchase_items = (
            db.query(models.PurchaseItem)
            .filter(
                models.PurchaseItem.id.in_(requested_item_ids),
                models.PurchaseItem.purchase_id == db_order.id,
                models.PurchaseItem.tenant_id == tenant_id,
            )
            .order_by(models.PurchaseItem.product_id.asc(), models.PurchaseItem.id.asc())
            .with_for_update()
            .all()
        )
        purchase_items_by_id = {item.id: item for item in purchase_items}
        if set(purchase_items_by_id) != requested_item_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase item not found on the original order.",
            )
        if any(item.received_batch_id is None for item in purchase_items):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more purchase items require batch reconciliation.",
            )

        prior_returns = (
            db.query(
                models.PurchaseReturnItem.purchase_item_id,
                func.sum(models.PurchaseReturnItem.quantity),
            )
            .join(
                models.PurchaseReturn,
                models.PurchaseReturn.id == models.PurchaseReturnItem.return_id,
            )
            .filter(
                models.PurchaseReturn.purchase_order_id == db_order.id,
                models.PurchaseReturn.tenant_id == tenant_id,
            )
            .group_by(models.PurchaseReturnItem.purchase_item_id)
            .all()
        )
        returned_by_item = {
            purchase_item_id: quantity for purchase_item_id, quantity in prior_returns
        }

        product_ids = {item.product_id for item in purchase_items}
        products = (
            db.query(Product)
            .filter(Product.id.in_(product_ids), Product.tenant_id == tenant_id)
            .order_by(Product.id.asc())
            .with_for_update()
            .all()
        )
        products_by_id = {product.id: product for product in products}
        if set(products_by_id) != product_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more purchase products require reconciliation.",
            )

        batches = (
            db.query(ProductBatch)
            .filter(
                ProductBatch.product_id.in_(product_ids),
                ProductBatch.tenant_id == tenant_id,
            )
            .order_by(ProductBatch.product_id.asc(), ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        batches_by_id = {batch.id: batch for batch in batches}
        for product_id, product in products_by_id.items():
            batch_total = sum(batch.quantity for batch in batches if batch.product_id == product_id)
            if product.total_parts != batch_total:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Inventory for product ID {product.id} requires reconciliation.",
                )

        prepared: list[
            tuple[
                schemas.PurchaseReturnItemCreate,
                models.PurchaseItem,
                Product,
                ProductBatch,
                Decimal,
                Decimal,
            ]
        ] = []
        total_refund = ZERO_MONEY
        for return_item in return_data.items:
            purchase_item = purchase_items_by_id[return_item.purchase_item_id]
            already_returned = returned_by_item.get(purchase_item.id, 0)
            if already_returned + return_item.quantity > purchase_item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cumulative return quantity exceeds the quantity received.",
                )
            returned_by_item[purchase_item.id] = already_returned + return_item.quantity

            batch = batches_by_id.get(purchase_item.received_batch_id)
            if batch is None or batch.product_id != purchase_item.product_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The original received batch requires reconciliation.",
                )
            product = products_by_id[purchase_item.product_id]
            if batch.quantity < return_item.quantity or product.total_parts < return_item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The original batch does not contain enough available stock.",
                )

            unit_refund = quantize_money(
                purchase_item.unit_cost
                * (Decimal("100") + db_order.tax_percentage)
                / Decimal("100")
            )
            line_refund = quantize_money(unit_refund * return_item.quantity)
            total_refund += line_refund
            prepared.append((return_item, purchase_item, product, batch, unit_refund, line_refund))

        total_refund = quantize_money(total_refund)
        db_return = models.PurchaseReturn(
            purchase_order_id=db_order.id,
            supplier_id=db_order.supplier_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            total_refund=total_refund,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        db.add(db_return)
        db.flush()

        for return_item, purchase_item, product, batch, unit_refund, line_refund in prepared:
            db_return_item = models.PurchaseReturnItem(
                return_id=db_return.id,
                purchase_order_id=db_order.id,
                purchase_item_id=purchase_item.id,
                batch_id=batch.id,
                product_id=product.id,
                tenant_id=tenant_id,
                quantity=return_item.quantity,
                unit_refund=unit_refund,
                subtotal=line_refund,
            )
            db.add(db_return_item)
            db.flush()
            batch.quantity -= return_item.quantity
            product.total_parts -= return_item.quantity
            record_stock_movement(
                db,
                event_type="purchase_return",
                stock_bucket="on_hand",
                quantity_delta=-return_item.quantity,
                source_type="purchase_return",
                source_id=db_return.id,
                source_line_id=db_return_item.id,
                source_key=f"purchase-return:item:{db_return_item.id}:batch:{batch.id}",
                actor_user_id=user_id,
                product_id=product.id,
                batch_id=batch.id,
                tenant_id=tenant_id,
            )

        if total_refund > ZERO_MONEY:
            record_financial_transaction(
                db,
                event_type="purchase_return",
                source_type="purchase_return",
                source_id=db_return.id,
                source_key=f"purchase-return:{db_return.id}",
                actor_user_id=user_id,
                supplier_id=db_order.supplier_id,
                tenant_id=tenant_id,
                lines=[
                    debit("supplier_payable", total_refund),
                    credit("inventory_purchases", total_refund),
                ],
            )

        db.commit()
        db.refresh(db_return)
        return db_return
    except Exception:
        db.rollback()
        raise


def get_supplier_balance(db: Session, supplier_id: int, tenant_id: int):
    db_supplier = _get_supplier_for_tenant(db, supplier_id, tenant_id)
    total_purchases, total_payments, total_returns, current_balance = _supplier_balance_components(
        db, supplier_id, tenant_id
    )
    return {
        "supplier_id": db_supplier.id,
        "supplier_name": db_supplier.name,
        "total_purchases": total_purchases,
        "total_payments": total_payments,
        "total_returns": total_returns,
        "current_balance": current_balance,
    }


def update_supplier(
    db: Session,
    supplier_id: int,
    supplier_update: schemas.SupplierUpdate,
    tenant_id: int,
):
    db_supplier = get_supplier_by_id(db, supplier_id, tenant_id)
    for key, value in supplier_update.model_dump(exclude_unset=True).items():
        setattr(db_supplier, key, value)
    db.commit()
    db.refresh(db_supplier)
    return db_supplier


def get_purchase_order_by_id(db: Session, order_id: int, tenant_id: int):
    db_order = (
        db.query(models.PurchaseOrder)
        .filter(
            models.PurchaseOrder.id == order_id,
            models.PurchaseOrder.tenant_id == tenant_id,
        )
        .first()
    )
    if not db_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order with ID {order_id} not found.",
        )
    return db_order


def get_supplier_payments(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.SupplierPayment)
        .filter(models.SupplierPayment.tenant_id == tenant_id)
        .order_by(models.SupplierPayment.payment_date.desc(), models.SupplierPayment.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_purchase_returns(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.PurchaseReturn)
        .filter(models.PurchaseReturn.tenant_id == tenant_id)
        .order_by(models.PurchaseReturn.created_at.desc(), models.PurchaseReturn.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
