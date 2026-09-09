from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.pagination import AuditPageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import require_reports_access
from src.modules.auth.dependencies import get_current_user
from src.modules.finance import ledger, schemas
from src.modules.users.schemas import UserResponse

router = APIRouter(
    prefix="/finance",
    tags=["Financial & Cash Ledger"],
    dependencies=[Depends(require_reports_access)],
)


@router.get("/transactions", response_model=list[schemas.FinancialTransactionResponse])
def read_financial_transactions(
    account_code: schemas.FinancialAccount | None = None,
    shift_id: int | None = Query(default=None, gt=0),
    customer_id: int | None = Query(default=None, gt=0),
    supplier_id: int | None = Query(default=None, gt=0),
    skip: PageOffset = 0,
    limit: AuditPageLimit = 100,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    return ledger.get_financial_transactions(
        db,
        current_user.tenant_id,
        account_code=account_code.value if account_code else None,
        shift_id=shift_id,
        customer_id=customer_id,
        supplier_id=supplier_id,
        skip=skip,
        limit=limit,
    )


@router.get("/reconciliation", response_model=schemas.FinancialReconciliationResponse)
def read_financial_reconciliation(
    issue_limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    return ledger.reconcile_financial_ledger(
        db,
        current_user.tenant_id,
        issue_limit=issue_limit,
    )
