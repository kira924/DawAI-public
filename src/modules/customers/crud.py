import hashlib
import json

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.money import ZERO_MONEY, quantize_money
from src.modules.customers import models, schemas
from src.modules.finance.ledger import credit, debit, record_financial_transaction
from src.modules.sales.models import Invoice
from src.modules.shifts.crud import get_active_shift


# --- Customer Logic ---
def create_customer(db: Session, customer: schemas.CustomerCreate, tenant_id: int):
    db_customer = models.Customer(
        name=customer.name,
        phone=customer.phone,
        credit_limit=customer.credit_limit,
        tenant_id=tenant_id,
    )
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer


def get_customers(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    return (
        db.query(models.Customer)
        .filter(models.Customer.tenant_id == tenant_id)
        .order_by(models.Customer.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_customer_by_id(db: Session, customer_id: int, tenant_id: int):
    return (
        db.query(models.Customer)
        .filter(models.Customer.id == customer_id, models.Customer.tenant_id == tenant_id)
        .first()
    )


# --- Customer Payment Logic ---
def create_customer_payment(
    db: Session, payment: schemas.CustomerPaymentCreate, user_id: int, tenant_id: int
):
    try:
        return _create_customer_payment(db, payment, user_id, tenant_id)
    except Exception:
        db.rollback()
        raise


def _create_customer_payment(
    db: Session, payment: schemas.CustomerPaymentCreate, user_id: int, tenant_id: int
):
    idempotency_key = str(payment.idempotency_key)
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "actor_user_id": user_id,
                "payment": payment.model_dump(mode="json", exclude={"idempotency_key"}),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    lock_digest = hashlib.sha256(
        f"customer-payment:{tenant_id}:{idempotency_key}".encode()
    ).digest()
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": int.from_bytes(lock_digest[:8], byteorder="big", signed=True)},
    )
    existing_payment = (
        db.query(models.CustomerPayment)
        .filter(
            models.CustomerPayment.tenant_id == tenant_id,
            models.CustomerPayment.idempotency_key == idempotency_key,
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

    # 1. Check for an active shift to receive the money
    active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
    if not active_shift:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot accept payment. You must open a shift first.",
        )

    # 2. Verify customer exists and lock its debt ledger
    customer = (
        db.query(models.Customer)
        .filter(models.Customer.id == payment.customer_id, models.Customer.tenant_id == tenant_id)
        .with_for_update()
        .first()
    )
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")

    payment_amount = quantize_money(payment.amount)
    if payment_amount > customer.total_debt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment cannot exceed the customer's outstanding debt.",
        )

    outstanding_invoices = (
        db.query(Invoice)
        .filter(
            Invoice.customer_id == customer.id,
            Invoice.tenant_id == tenant_id,
            Invoice.payment_type == "credit",
            Invoice.outstanding_amount > ZERO_MONEY,
        )
        .order_by(Invoice.created_at.asc(), Invoice.id.asc())
        .with_for_update()
        .all()
    )
    outstanding_total = quantize_money(
        sum((invoice.outstanding_amount for invoice in outstanding_invoices), ZERO_MONEY)
    )
    if outstanding_total != customer.total_debt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer debt ledger requires reconciliation before accepting a payment.",
        )

    # 3. Create the payment record
    db_payment = models.CustomerPayment(
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        amount=payment_amount,
        customer_id=customer.id,
        shift_id=active_shift.id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    db.add(db_payment)
    db.flush()

    remaining = payment_amount
    for invoice in outstanding_invoices:
        if remaining == ZERO_MONEY:
            break
        allocated = min(invoice.outstanding_amount, remaining)
        invoice.outstanding_amount = quantize_money(invoice.outstanding_amount - allocated)
        remaining = quantize_money(remaining - allocated)
        db.add(
            models.CustomerPaymentAllocation(
                payment_id=db_payment.id,
                invoice_id=invoice.id,
                customer_id=customer.id,
                amount=allocated,
                tenant_id=tenant_id,
            )
        )

    if remaining != ZERO_MONEY:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment allocation failed because the debt ledger is incomplete.",
        )

    # 4. Decrease the customer's total debt
    customer.total_debt = quantize_money(customer.total_debt - payment_amount)

    record_financial_transaction(
        db,
        event_type="customer_payment",
        source_type="customer_payment",
        source_id=db_payment.id,
        source_key=f"customer-payment:{db_payment.id}",
        actor_user_id=user_id,
        shift_id=active_shift.id,
        customer_id=customer.id,
        tenant_id=tenant_id,
        lines=[
            debit("cash_drawer", payment_amount),
            credit("customer_receivable", payment_amount),
        ],
    )

    db.commit()
    db.refresh(db_payment)
    return db_payment
