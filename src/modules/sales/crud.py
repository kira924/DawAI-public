import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.core.business_time import is_sellable_expiry, local_business_date
from src.core.money import ZERO_MONEY, quantize_money
from src.modules.customers.models import Customer
from src.modules.finance.ledger import credit, debit, record_financial_transaction
from src.modules.inventory.ledger import record_stock_movement
from src.modules.inventory.models import Product, ProductBatch
from src.modules.sales import models, schemas
from src.modules.shifts.crud import get_active_shift

RETURN_WINDOW = timedelta(days=14)


def _return_fingerprint(return_data: schemas.SalesReturnCreate) -> str:
    canonical = json.dumps(
        return_data.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _invoice_fingerprint(invoice: schemas.InvoiceCreate, user_id: int) -> str:
    canonical = json.dumps(
        {
            "actor_user_id": user_id,
            "invoice": invoice.model_dump(mode="json", exclude={"idempotency_key"}),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _idempotency_lock_id(tenant_id: int, idempotency_key: str) -> int:
    digest = hashlib.sha256(f"{tenant_id}:{idempotency_key}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _list_unit_price(product: Product) -> Decimal:
    if product.is_divisible:
        if product.part_price is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Product ID {product.id} requires a configured part price.",
            )
        return quantize_money(product.part_price)
    return quantize_money(product.price)


def create_invoice(db: Session, invoice: schemas.InvoiceCreate, user_id: int, tenant_id: int):
    idempotency_key = str(invoice.idempotency_key)
    request_fingerprint = _invoice_fingerprint(invoice, user_id)

    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _idempotency_lock_id(tenant_id, f"invoice:{idempotency_key}")},
        )
        existing_invoice = (
            db.query(models.Invoice)
            .filter(
                models.Invoice.tenant_id == tenant_id,
                models.Invoice.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing_invoice:
            if existing_invoice.request_fingerprint != request_fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key was already used with a different request.",
                )
            db.commit()
            return existing_invoice

        active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
        if not active_shift:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot complete sale. You must open a shift first.",
            )

        customer = None
        if invoice.customer_id is not None:
            customer = (
                db.query(Customer)
                .filter(Customer.id == invoice.customer_id, Customer.tenant_id == tenant_id)
                .with_for_update()
                .first()
            )
            if not customer:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Customer not found.",
                )

        if invoice.payment_type == schemas.PaymentType.CREDIT and customer is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customer ID is required for credit sales.",
            )

        requested_product_ids = {item.product_id for item in invoice.items}
        products = (
            db.query(Product)
            .filter(Product.id.in_(requested_product_ids), Product.tenant_id == tenant_id)
            .order_by(Product.id.asc())
            .with_for_update()
            .all()
        )
        products_by_id = {product.id: product for product in products}
        if set(products_by_id) != requested_product_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more products were not found.",
            )

        prepared_items: list[tuple[schemas.InvoiceItemCreate, Product, Decimal, Decimal]] = []
        total_amount = ZERO_MONEY
        for item in invoice.items:
            product = products_by_id[item.product_id]

            list_unit_price = _list_unit_price(product)
            sale_unit_price = quantize_money(item.sale_unit_price)
            if sale_unit_price > list_unit_price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Sale price cannot exceed the list price for product ID {product.id}.",
                )
            subtotal = quantize_money(sale_unit_price * item.quantity)
            prepared_items.append((item, product, list_unit_price, subtotal))
            total_amount += subtotal

        total_amount = quantize_money(total_amount)
        if total_amount <= ZERO_MONEY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invoice total must be greater than zero.",
            )

        if customer is not None and invoice.payment_type == schemas.PaymentType.CREDIT:
            new_debt = quantize_money(customer.total_debt + total_amount)
            if customer.credit_limit > ZERO_MONEY and new_debt > customer.credit_limit:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Credit limit exceeded. Customer can only borrow up to {customer.credit_limit}",
                )
        else:
            new_debt = None

        db_invoice = models.Invoice(
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            total_amount=total_amount,
            outstanding_amount=(
                total_amount if invoice.payment_type == schemas.PaymentType.CREDIT else ZERO_MONEY
            ),
            payment_type=invoice.payment_type.value,
            customer_id=invoice.customer_id,
            user_id=user_id,
            tenant_id=tenant_id,
            shift_id=active_shift.id,
        )
        db.add(db_invoice)
        db.flush()

        sale_business_date = local_business_date()
        for item, product, list_unit_price, subtotal in prepared_items:
            all_batches = (
                db.query(ProductBatch)
                .filter(
                    ProductBatch.product_id == product.id,
                    ProductBatch.tenant_id == tenant_id,
                )
                .order_by(ProductBatch.expiry_date.asc(), ProductBatch.id.asc())
                .with_for_update()
                .all()
            )
            physical_batch_parts = sum(batch.quantity for batch in all_batches)
            if product.total_parts != physical_batch_parts:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Inventory for product ID {product.id} requires reconciliation.",
                )
            batches = [
                batch
                for batch in all_batches
                if batch.quantity > 0
                and is_sellable_expiry(batch.expiry_date, as_of=sale_business_date)
            ]
            available_batch_parts = sum(batch.quantity for batch in batches)
            if available_batch_parts < item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Insufficient stock for product: {product.name}. "
                        f"Available: {available_batch_parts}"
                    ),
                )

            db_item = models.InvoiceItem(
                invoice_id=db_invoice.id,
                product_id=product.id,
                tenant_id=tenant_id,
                quantity=item.quantity,
                list_unit_price=list_unit_price,
                sale_unit_price=quantize_money(item.sale_unit_price),
                subtotal=subtotal,
            )
            db.add(db_item)
            db.flush()

            remaining = item.quantity
            for batch in batches:
                if remaining == 0:
                    break
                allocated = min(batch.quantity, remaining)
                batch.quantity -= allocated
                remaining -= allocated
                db_allocation = models.InvoiceItemBatchAllocation(
                    invoice_item_id=db_item.id,
                    invoice_id=db_invoice.id,
                    batch_id=batch.id,
                    product_id=product.id,
                    tenant_id=tenant_id,
                    quantity=allocated,
                )
                db.add(db_allocation)
                db.flush()
                record_stock_movement(
                    db,
                    event_type="sale",
                    stock_bucket="on_hand",
                    quantity_delta=-allocated,
                    source_type="invoice",
                    source_id=db_invoice.id,
                    source_line_id=db_allocation.id,
                    source_key=f"sale:allocation:{db_allocation.id}:batch:{batch.id}",
                    actor_user_id=user_id,
                    product_id=product.id,
                    batch_id=batch.id,
                    tenant_id=tenant_id,
                )

            if remaining != 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Inventory allocation failed.",
                )
            product.total_parts -= item.quantity

        if customer is not None and new_debt is not None:
            customer.total_debt = new_debt

        payment_account = (
            "customer_receivable"
            if invoice.payment_type == schemas.PaymentType.CREDIT
            else "cash_drawer"
        )
        record_financial_transaction(
            db,
            event_type="sale",
            source_type="invoice",
            source_id=db_invoice.id,
            source_key=f"invoice:{db_invoice.id}",
            actor_user_id=user_id,
            shift_id=active_shift.id if payment_account == "cash_drawer" else None,
            customer_id=customer.id if customer is not None else None,
            tenant_id=tenant_id,
            lines=[
                debit(payment_account, total_amount),
                credit("sales_revenue", total_amount),
            ],
        )

        db.commit()
        db.refresh(db_invoice)
        return db_invoice
    except Exception:
        db.rollback()
        raise


def get_invoices(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.Invoice)
        .filter(models.Invoice.tenant_id == tenant_id)
        .order_by(models.Invoice.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_invoice_by_id(db: Session, invoice_id: int, tenant_id: int):
    return (
        db.query(models.Invoice)
        .filter(models.Invoice.id == invoice_id, models.Invoice.tenant_id == tenant_id)
        .first()
    )


def create_sales_return(
    db: Session,
    return_data: schemas.SalesReturnCreate,
    user_id: int,
    tenant_id: int,
    user_role: str,
):
    idempotency_key = str(return_data.idempotency_key)
    request_fingerprint = _return_fingerprint(return_data)

    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _idempotency_lock_id(tenant_id, idempotency_key)},
        )
        existing_return = (
            db.query(models.SalesReturn)
            .filter(
                models.SalesReturn.tenant_id == tenant_id,
                models.SalesReturn.idempotency_key == idempotency_key,
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

        active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
        if not active_shift:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot process return. You must open a shift first.",
            )

        invoice_identity = (
            db.query(models.Invoice.payment_type, models.Invoice.customer_id)
            .filter(
                models.Invoice.id == return_data.invoice_id,
                models.Invoice.tenant_id == tenant_id,
            )
            .first()
        )
        if not invoice_identity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Original invoice not found.",
            )

        customer = None
        if invoice_identity.payment_type == schemas.PaymentType.CREDIT.value:
            if invoice_identity.customer_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Credit invoice has no customer and requires reconciliation.",
                )
            customer = (
                db.query(Customer)
                .filter(
                    Customer.id == invoice_identity.customer_id,
                    Customer.tenant_id == tenant_id,
                )
                .with_for_update()
                .one()
            )

        db_invoice = (
            db.query(models.Invoice)
            .filter(
                models.Invoice.id == return_data.invoice_id,
                models.Invoice.tenant_id == tenant_id,
            )
            .with_for_update()
            .one()
        )
        if (
            db_invoice.payment_type != invoice_identity.payment_type
            or db_invoice.customer_id != invoice_identity.customer_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invoice payment context changed; retry the return.",
            )

        created_at = db_invoice.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created_at > RETURN_WINDOW:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The 14-day return window has expired.",
            )

        requested_item_ids = {item.invoice_item_id for item in return_data.items}
        invoice_items = (
            db.query(models.InvoiceItem)
            .filter(
                models.InvoiceItem.invoice_id == db_invoice.id,
                models.InvoiceItem.tenant_id == tenant_id,
                models.InvoiceItem.id.in_(requested_item_ids),
            )
            .with_for_update()
            .all()
        )
        invoice_items_by_id = {item.id: item for item in invoice_items}
        if set(invoice_items_by_id) != requested_item_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invoice item not found on the original invoice.",
            )

        prior_returns = (
            db.query(
                models.SalesReturnItem.invoice_item_id,
                models.SalesReturnItem.batch_id,
                func.sum(models.SalesReturnItem.quantity),
            )
            .join(
                models.SalesReturn,
                models.SalesReturn.id == models.SalesReturnItem.sales_return_id,
            )
            .filter(
                models.SalesReturn.invoice_id == db_invoice.id,
                models.SalesReturn.tenant_id == tenant_id,
            )
            .group_by(models.SalesReturnItem.invoice_item_id, models.SalesReturnItem.batch_id)
            .all()
        )
        returned_by_item: dict[int, int] = {}
        returned_by_batch: dict[tuple[int, int], int] = {}
        for invoice_item_id, batch_id, quantity in prior_returns:
            returned_by_item[invoice_item_id] = returned_by_item.get(invoice_item_id, 0) + quantity
            if batch_id is not None:
                returned_by_batch[(invoice_item_id, batch_id)] = quantity

        product_ids = {item.product_id for item in invoice_items}
        locked_product_rows = (
            db.query(Product)
            .filter(Product.id.in_(product_ids), Product.tenant_id == tenant_id)
            .order_by(Product.id.asc())
            .with_for_update()
            .all()
        )
        locked_products = {product.id: product for product in locked_product_rows}
        if set(locked_products) != product_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more sold products require reconciliation.",
            )
        locked_batch_rows = (
            db.query(ProductBatch)
            .filter(
                ProductBatch.product_id.in_(product_ids),
                ProductBatch.tenant_id == tenant_id,
            )
            .order_by(ProductBatch.product_id.asc(), ProductBatch.id.asc())
            .with_for_update()
            .all()
        )
        locked_batches_by_product: dict[int, dict[int, ProductBatch]] = {
            product_id: {} for product_id in product_ids
        }
        for batch in locked_batch_rows:
            locked_batches_by_product[batch.product_id][batch.id] = batch
        for product_id, product in locked_products.items():
            if product.total_parts != sum(
                batch.quantity for batch in locked_batches_by_product[product_id].values()
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Inventory for product ID {product.id} requires reconciliation.",
                )

        prepared: list[
            tuple[
                schemas.SalesReturnItemCreate,
                models.InvoiceItem,
                Product,
                ProductBatch | None,
                str,
                Decimal,
                Decimal,
            ]
        ] = []
        total_refund = ZERO_MONEY

        for return_item in return_data.items:
            invoice_item = invoice_items_by_id[return_item.invoice_item_id]
            already_returned = returned_by_item.get(invoice_item.id, 0)
            if already_returned + return_item.quantity > invoice_item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cumulative return quantity exceeds the quantity sold.",
                )
            returned_by_item[invoice_item.id] = already_returned + return_item.quantity

            product = locked_products[invoice_item.product_id]

            if product.return_policy == "non_returnable":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Product ID {product.id} is non-returnable.",
                )
            if (
                product.return_policy == "unopened_only"
                and return_item.condition != schemas.ReturnCondition.SEALED
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Product ID {product.id} can only be returned sealed.",
                )

            batch = None
            if return_item.batch_id is not None:
                allocation = (
                    db.query(models.InvoiceItemBatchAllocation)
                    .filter(
                        models.InvoiceItemBatchAllocation.invoice_item_id == invoice_item.id,
                        models.InvoiceItemBatchAllocation.batch_id == return_item.batch_id,
                        models.InvoiceItemBatchAllocation.tenant_id == tenant_id,
                    )
                    .with_for_update()
                    .first()
                )
                if not allocation:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="The selected batch was not allocated to the original invoice item.",
                    )
                returned_from_batch = returned_by_batch.get(
                    (invoice_item.id, return_item.batch_id), 0
                )
                if returned_from_batch + return_item.quantity > allocation.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Cumulative return quantity exceeds the quantity sold from this batch.",
                    )
                batch = locked_batches_by_product[product.id].get(return_item.batch_id)
                if batch is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="The original product batch no longer exists.",
                    )

            disposition = "quarantine"
            if (
                return_item.condition == schemas.ReturnCondition.SEALED
                and batch is not None
                and is_sellable_expiry(batch.expiry_date, as_of=local_business_date())
            ):
                disposition = "restock"
            unit_refund = quantize_money(invoice_item.sale_unit_price)
            refund_amount = quantize_money(unit_refund * return_item.quantity)
            total_refund += refund_amount
            prepared.append(
                (
                    return_item,
                    invoice_item,
                    product,
                    batch,
                    disposition,
                    unit_refund,
                    refund_amount,
                )
            )

        total_refund = quantize_money(total_refund)
        cash_refund = ZERO_MONEY
        debt_reduction = ZERO_MONEY
        credit_balance_increase = ZERO_MONEY
        if db_invoice.payment_type == schemas.PaymentType.CASH.value:
            cash_refund = total_refund
        else:
            if customer is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Credit invoice customer requires reconciliation.",
                )
            debt_reduction = min(total_refund, db_invoice.outstanding_amount)
            paid_portion = quantize_money(total_refund - debt_reduction)
            if customer.total_debt < debt_reduction:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Customer debt requires reconciliation before this return.",
                )
            if paid_portion > ZERO_MONEY:
                if return_data.paid_portion_destination == schemas.PaidPortionDestination.CASH:
                    if user_role != "manager":
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Manager approval is required for a cash refund of paid credit.",
                        )
                    cash_refund = paid_portion
                else:
                    credit_balance_increase = paid_portion

        db_return = models.SalesReturn(
            invoice_id=db_invoice.id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            total_refund=total_refund,
            cash_refund=cash_refund,
            debt_reduction=debt_reduction,
            credit_balance_increase=credit_balance_increase,
            shift_id=active_shift.id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        db.add(db_return)
        db.flush()

        for (
            return_item,
            invoice_item,
            product,
            batch,
            disposition,
            unit_refund,
            refund_amount,
        ) in prepared:
            db_item = models.SalesReturnItem(
                sales_return_id=db_return.id,
                invoice_id=db_invoice.id,
                invoice_item_id=invoice_item.id,
                batch_id=return_item.batch_id,
                product_id=product.id,
                tenant_id=tenant_id,
                quantity=return_item.quantity,
                condition=return_item.condition.value,
                disposition=disposition,
                unit_refund=unit_refund,
                refund_amount=refund_amount,
            )
            db.add(db_item)
            db.flush()
            if disposition == "restock":
                if batch is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="A sealed return requires its original batch.",
                    )
                batch.quantity += return_item.quantity
                product.total_parts += return_item.quantity
                record_stock_movement(
                    db,
                    event_type="sales_return_restock",
                    stock_bucket="on_hand",
                    quantity_delta=return_item.quantity,
                    source_type="sales_return",
                    source_id=db_return.id,
                    source_line_id=db_item.id,
                    source_key=f"sales-return-restock:item:{db_item.id}:batch:{batch.id}",
                    actor_user_id=user_id,
                    product_id=product.id,
                    batch_id=batch.id,
                    tenant_id=tenant_id,
                )
            else:
                quarantine_item = models.QuarantineItem(
                    sales_return_item_id=db_item.id,
                    product_id=product.id,
                    batch_id=return_item.batch_id,
                    quantity=return_item.quantity,
                    initial_quantity=return_item.quantity,
                    condition=return_item.condition.value,
                    reason=(
                        "expired_batch"
                        if return_item.condition == schemas.ReturnCondition.SEALED
                        else "return_condition"
                    ),
                    status="pending",
                    idempotency_key=f"sales-return-item:{db_item.id}",
                    request_fingerprint=request_fingerprint,
                    created_by_user_id=user_id,
                    tenant_id=tenant_id,
                )
                db.add(quarantine_item)
                db.flush()
                record_stock_movement(
                    db,
                    event_type="sales_return_quarantine",
                    stock_bucket="quarantine",
                    quantity_delta=return_item.quantity,
                    source_type="sales_return",
                    source_id=db_return.id,
                    source_line_id=db_item.id,
                    source_key=f"sales-return-quarantine:item:{db_item.id}",
                    actor_user_id=user_id,
                    product_id=product.id,
                    batch_id=return_item.batch_id,
                    tenant_id=tenant_id,
                )

        if customer is not None:
            customer.total_debt = quantize_money(customer.total_debt - debt_reduction)
            customer.credit_balance = quantize_money(
                customer.credit_balance + credit_balance_increase
            )
            db_invoice.outstanding_amount = quantize_money(
                db_invoice.outstanding_amount - debt_reduction
            )

        if total_refund > ZERO_MONEY:
            return_lines = [debit("sales_returns", total_refund)]
            if cash_refund > ZERO_MONEY:
                return_lines.append(credit("cash_drawer", cash_refund))
            if debt_reduction > ZERO_MONEY:
                return_lines.append(credit("customer_receivable", debt_reduction))
            if credit_balance_increase > ZERO_MONEY:
                return_lines.append(credit("customer_credit", credit_balance_increase))
            record_financial_transaction(
                db,
                event_type="sales_return",
                source_type="sales_return",
                source_id=db_return.id,
                source_key=f"sales-return:{db_return.id}",
                actor_user_id=user_id,
                shift_id=active_shift.id if cash_refund > ZERO_MONEY else None,
                customer_id=customer.id if customer is not None else None,
                tenant_id=tenant_id,
                lines=return_lines,
            )

        db.commit()
        db.refresh(db_return)
        return db_return
    except Exception:
        db.rollback()
        raise
