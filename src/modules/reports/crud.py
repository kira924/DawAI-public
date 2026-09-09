from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.business_time import business_day_utc_bounds, local_business_date
from src.core.config import settings
from src.core.money import ZERO_MONEY, quantize_money
from src.modules.expenses.models import Expense
from src.modules.inventory.replenishment import count_replenishment_due
from src.modules.sales.models import Invoice, SalesReturn


def get_daily_dashboard_summary(
    db: Session,
    tenant_id: int,
    requested_business_date: date | None = None,
    *,
    now: datetime | None = None,
):
    report_date = requested_business_date or local_business_date(now)
    inventory_date = local_business_date(now)
    day_start, day_end = business_day_utc_bounds(report_date)

    # Calculate Total Sales for today
    total_sales = (
        db.query(func.sum(Invoice.total_amount))
        .filter(
            Invoice.tenant_id == tenant_id,
            Invoice.created_at >= day_start,
            Invoice.created_at < day_end,
        )
        .scalar()
        or ZERO_MONEY
    )

    # Calculate Total Expenses for today
    total_expenses = (
        db.query(func.sum(Expense.amount))
        .filter(
            Expense.tenant_id == tenant_id,
            Expense.created_at >= day_start,
            Expense.created_at < day_end,
        )
        .scalar()
        or ZERO_MONEY
    )

    # Calculate Total Returns for today
    total_returns = (
        db.query(func.sum(SalesReturn.total_refund))
        .filter(
            SalesReturn.tenant_id == tenant_id,
            SalesReturn.created_at >= day_start,
            SalesReturn.created_at < day_end,
        )
        .scalar()
        or ZERO_MONEY
    )

    low_stock_count = count_replenishment_due(db, tenant_id, as_of=inventory_date)

    net_sales = quantize_money(total_sales - total_returns)
    operating_result = quantize_money(net_sales - total_expenses)

    return {
        "business_date": report_date,
        "inventory_date": inventory_date,
        "timezone": settings.BUSINESS_TIMEZONE,
        "gross_sales": total_sales,
        "sales_returns": total_returns,
        "net_sales": net_sales,
        "operating_expenses": total_expenses,
        "operating_result_before_cogs_and_tax": operating_result,
        "total_sales": total_sales,
        "total_expenses": total_expenses,
        "total_returns": total_returns,
        "net_revenue": operating_result,
        "low_stock_count": low_stock_count,
    }
