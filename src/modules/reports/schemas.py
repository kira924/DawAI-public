from datetime import date

from pydantic import BaseModel, Field

from src.core.money import Money


class DashboardSummaryResponse(BaseModel):
    business_date: date
    inventory_date: date
    timezone: str
    gross_sales: Money
    sales_returns: Money
    net_sales: Money
    operating_expenses: Money
    operating_result_before_cogs_and_tax: Money
    total_sales: Money
    total_expenses: Money
    total_returns: Money
    net_revenue: Money = Field(
        deprecated="Use operating_result_before_cogs_and_tax; this value is not net revenue."
    )
    low_stock_count: int
