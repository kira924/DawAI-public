import hashlib
import json

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.modules.expenses import models, schemas
from src.modules.finance.ledger import credit, debit, record_financial_transaction
from src.modules.shifts.crud import get_active_shift


def create_expense(db: Session, expense: schemas.ExpenseCreate, user_id: int, tenant_id: int):
    idempotency_key = str(expense.idempotency_key)
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "actor_user_id": user_id,
                "expense": expense.model_dump(mode="json", exclude={"idempotency_key"}),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    lock_digest = hashlib.sha256(f"expense:{tenant_id}:{idempotency_key}".encode()).digest()
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": int.from_bytes(lock_digest[:8], byteorder="big", signed=True)},
        )
        existing_expense = (
            db.query(models.Expense)
            .filter(
                models.Expense.tenant_id == tenant_id,
                models.Expense.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing_expense:
            if existing_expense.request_fingerprint != request_fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key was already used with a different request.",
                )
            db.commit()
            return existing_expense

        active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
        if not active_shift:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot record expense. You must open a shift first.",
            )

        db_expense = models.Expense(
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            amount=expense.amount,
            description=expense.description,
            shift_id=active_shift.id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        db.add(db_expense)
        db.flush()
        record_financial_transaction(
            db,
            event_type="expense",
            source_type="expense",
            source_id=db_expense.id,
            source_key=f"expense:{db_expense.id}",
            actor_user_id=user_id,
            shift_id=active_shift.id,
            tenant_id=tenant_id,
            lines=[
                debit("operating_expense", expense.amount),
                credit("cash_drawer", expense.amount),
            ],
        )
        db.commit()
        db.refresh(db_expense)
        return db_expense
    except Exception:
        db.rollback()
        raise


def get_expenses(db: Session, tenant_id: int, skip: int = 0, limit: int = 100):
    # Fetch all expenses for the current tenant
    return (
        db.query(models.Expense)
        .filter(models.Expense.tenant_id == tenant_id)
        .order_by(models.Expense.created_at.desc(), models.Expense.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_expense_by_id(db: Session, expense_id: int, tenant_id: int):
    # Fetch a specific expense by ID for the current tenant
    return (
        db.query(models.Expense)
        .filter(models.Expense.id == expense_id, models.Expense.tenant_id == tenant_id)
        .first()
    )
