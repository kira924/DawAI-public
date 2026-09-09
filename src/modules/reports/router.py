from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.modules.auth.authorization import require_reports_access
from src.modules.auth.dependencies import get_current_user
from src.modules.reports import crud, schemas
from src.modules.users import schemas as user_schemas

router = APIRouter(
    prefix="/reports",
    tags=["Reports & Analytics"],
    dependencies=[Depends(require_reports_access)],
)


@router.get("/dashboard", response_model=schemas.DashboardSummaryResponse)
def read_dashboard_summary(
    business_date: date | None = Query(
        default=None,
        description="Tenant-local calendar date; defaults to today in Africa/Cairo.",
    ),
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Fetch the real-time financial summary for the tenant
    return crud.get_daily_dashboard_summary(
        db=db,
        tenant_id=current_user.tenant_id,
        requested_business_date=business_date,
    )
