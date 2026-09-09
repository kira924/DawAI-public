from fastapi import HTTPException, status
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.business_time import utc_now
from src.core.money import ZERO_MONEY, quantize_money
from src.modules.finance.ledger import credit, debit, record_financial_transaction
from src.modules.finance.models import FinancialEntry, FinancialTransaction
from src.modules.shifts import models, schemas


def get_active_shift(db: Session, user_id: int, tenant_id: int, *, for_update: bool = False):
    # Retrieve the currently open shift for the specific user in this tenant
    query = db.query(models.Shift).filter(
        models.Shift.user_id == user_id,
        models.Shift.tenant_id == tenant_id,
        models.Shift.status == "OPEN",
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def open_shift(db: Session, shift: schemas.ShiftCreate, user_id: int, tenant_id: int):
    # Guardrail: Prevent opening a new shift if one is already open
    active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
    if active_shift:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has an open shift. Close it before opening a new one.",
        )

    # Initialize a new shift
    db_shift = models.Shift(
        opening_balance=shift.opening_balance, user_id=user_id, tenant_id=tenant_id
    )
    try:
        db.add(db_shift)
        db.commit()
        db.refresh(db_shift)
        return db_shift
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has an open shift. Close it before opening a new one.",
        ) from error


def close_shift(db: Session, shift_close: schemas.ShiftClose, user_id: int, tenant_id: int):
    # Ensure there is an open shift to close
    active_shift = get_active_shift(db, user_id, tenant_id, for_update=True)
    if not active_shift:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No open shift found for this user."
        )

    cash_delta = (
        db.query(
            func.sum(
                case(
                    (FinancialEntry.entry_side == "debit", FinancialEntry.amount),
                    else_=-FinancialEntry.amount,
                )
            )
        )
        .select_from(FinancialTransaction)
        .join(FinancialEntry)
        .filter(
            FinancialTransaction.shift_id == active_shift.id,
            FinancialTransaction.tenant_id == tenant_id,
            FinancialEntry.account_code == "cash_drawer",
            FinancialTransaction.event_type != "shift_adjustment",
        )
        .scalar()
        or ZERO_MONEY
    )

    expected_balance = quantize_money(active_shift.opening_balance + cash_delta)
    if expected_balance < ZERO_MONEY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Shift cash ledger is negative and requires reconciliation before closing.",
        )

    difference = quantize_money(shift_close.actual_closing_balance - expected_balance)

    # Finalize the shift records
    active_shift.expected_closing_balance = expected_balance
    active_shift.actual_closing_balance = shift_close.actual_closing_balance
    active_shift.difference = difference
    active_shift.end_time = utc_now()
    active_shift.status = "CLOSED"

    if difference > ZERO_MONEY:
        adjustment_lines = [
            debit("cash_drawer", difference),
            credit("cash_over_short", difference),
        ]
    elif difference < ZERO_MONEY:
        shortage = -difference
        adjustment_lines = [
            debit("cash_over_short", shortage),
            credit("cash_drawer", shortage),
        ]
    else:
        adjustment_lines = []

    if adjustment_lines:
        record_financial_transaction(
            db,
            event_type="shift_adjustment",
            source_type="shift",
            source_id=active_shift.id,
            source_key=f"shift-adjustment:{active_shift.id}",
            actor_user_id=user_id,
            shift_id=active_shift.id,
            tenant_id=tenant_id,
            lines=adjustment_lines,
        )

    try:
        db.commit()
        db.refresh(active_shift)
        return active_shift
    except Exception:
        db.rollback()
        raise
