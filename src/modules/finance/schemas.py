from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from src.core.money import Money


class FinancialAccount(str, Enum):
    CASH_DRAWER = "cash_drawer"
    CUSTOMER_RECEIVABLE = "customer_receivable"
    CUSTOMER_CREDIT = "customer_credit"
    SUPPLIER_PAYABLE = "supplier_payable"
    SALES_REVENUE = "sales_revenue"
    SALES_RETURNS = "sales_returns"
    INVENTORY_PURCHASES = "inventory_purchases"
    OPERATING_EXPENSE = "operating_expense"
    EXTERNAL_FUNDS = "external_funds"
    CASH_OVER_SHORT = "cash_over_short"


class EntrySide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class FinancialEventType(str, Enum):
    SALE = "sale"
    SALES_RETURN = "sales_return"
    CUSTOMER_PAYMENT = "customer_payment"
    PURCHASE_RECEIPT = "purchase_receipt"
    PURCHASE_RETURN = "purchase_return"
    SUPPLIER_PAYMENT = "supplier_payment"
    EXPENSE = "expense"
    SHIFT_ADJUSTMENT = "shift_adjustment"


class FinancialSourceType(str, Enum):
    INVOICE = "invoice"
    SALES_RETURN = "sales_return"
    CUSTOMER_PAYMENT = "customer_payment"
    PURCHASE_ORDER = "purchase_order"
    PURCHASE_RETURN = "purchase_return"
    SUPPLIER_PAYMENT = "supplier_payment"
    EXPENSE = "expense"
    SHIFT = "shift"


class FinancialEntryResponse(BaseModel):
    id: int
    account_code: FinancialAccount
    entry_side: EntrySide
    amount: Money

    model_config = ConfigDict(from_attributes=True)


class FinancialTransactionResponse(BaseModel):
    id: int
    event_type: FinancialEventType
    source_type: FinancialSourceType
    source_id: int
    source_key: str
    actor_user_id: int
    shift_id: int | None
    customer_id: int | None
    supplier_id: int | None
    tenant_id: int
    occurred_at: datetime | None
    recorded_at: datetime
    entries: list[FinancialEntryResponse]

    model_config = ConfigDict(from_attributes=True)


class FinancialReconciliationIssue(BaseModel):
    issue_type: str
    entity_id: int | None
    stored_amount: Money
    ledger_amount: Money


class FinancialReconciliationResponse(BaseModel):
    checked_transactions: int
    checked_shifts: int
    checked_customers: int
    checked_suppliers: int
    total_debits: Money
    total_credits: Money
    issue_count: int
    issues_truncated: bool
    issues: list[FinancialReconciliationIssue]
