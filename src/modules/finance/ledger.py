from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, text
from sqlalchemy.orm import Session, joinedload

from src.core.money import ZERO_MONEY, quantize_money
from src.modules.customers.models import Customer
from src.modules.finance.models import FinancialEntry, FinancialTransaction
from src.modules.purchases.models import PurchaseOrder, PurchaseReturn, Supplier, SupplierPayment
from src.modules.shifts.models import Shift


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    entry_side: str
    amount: Decimal


@dataclass(frozen=True)
class FinancialReconciliationIssue:
    issue_type: str
    entity_id: int | None
    stored_amount: Decimal
    ledger_amount: Decimal


@dataclass(frozen=True)
class FinancialReconciliationReport:
    checked_transactions: int
    checked_shifts: int
    checked_customers: int
    checked_suppliers: int
    total_debits: Decimal
    total_credits: Decimal
    issue_count: int
    issues_truncated: bool
    issues: list[FinancialReconciliationIssue]


def debit(account_code: str, amount: Decimal) -> JournalLine:
    return JournalLine(account_code, "debit", quantize_money(amount))


def credit(account_code: str, amount: Decimal) -> JournalLine:
    return JournalLine(account_code, "credit", quantize_money(amount))


def record_financial_transaction(
    db: Session,
    *,
    event_type: str,
    source_type: str,
    source_id: int,
    source_key: str,
    actor_user_id: int,
    tenant_id: int,
    lines: list[JournalLine],
    shift_id: int | None = None,
    customer_id: int | None = None,
    supplier_id: int | None = None,
) -> FinancialTransaction:
    normalized = [line for line in lines if line.amount != ZERO_MONEY]
    debits = quantize_money(
        sum((line.amount for line in normalized if line.entry_side == "debit"), ZERO_MONEY)
    )
    credits = quantize_money(
        sum((line.amount for line in normalized if line.entry_side == "credit"), ZERO_MONEY)
    )
    if not normalized or debits != credits:
        raise ValueError("Financial transaction entries must be non-empty and balanced")

    transaction = FinancialTransaction(
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        source_key=source_key,
        actor_user_id=actor_user_id,
        shift_id=shift_id,
        customer_id=customer_id,
        supplier_id=supplier_id,
        tenant_id=tenant_id,
    )
    db.add(transaction)
    db.flush()
    for line in normalized:
        db.add(
            FinancialEntry(
                transaction_id=transaction.id,
                account_code=line.account_code,
                entry_side=line.entry_side,
                amount=line.amount,
                tenant_id=tenant_id,
            )
        )
    db.flush()
    return transaction


def get_financial_transactions(
    db: Session,
    tenant_id: int,
    *,
    account_code: str | None = None,
    shift_id: int | None = None,
    customer_id: int | None = None,
    supplier_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[FinancialTransaction]:
    query = db.query(FinancialTransaction).filter(FinancialTransaction.tenant_id == tenant_id)
    if account_code is not None:
        query = query.join(FinancialEntry).filter(FinancialEntry.account_code == account_code)
    if shift_id is not None:
        query = query.filter(FinancialTransaction.shift_id == shift_id)
    if customer_id is not None:
        query = query.filter(FinancialTransaction.customer_id == customer_id)
    if supplier_id is not None:
        query = query.filter(FinancialTransaction.supplier_id == supplier_id)
    return (
        query.options(joinedload(FinancialTransaction.entries))
        .order_by(FinancialTransaction.recorded_at.desc(), FinancialTransaction.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def _account_balance_expression(account_code: str):
    return func.sum(
        case(
            (FinancialEntry.entry_side == "debit", FinancialEntry.amount),
            else_=-FinancialEntry.amount,
        )
    ).filter(FinancialEntry.account_code == account_code)


def reconcile_financial_ledger(
    db: Session,
    tenant_id: int,
    *,
    issue_limit: int = 100,
) -> FinancialReconciliationReport:
    totals = (
        db.query(
            func.coalesce(
                func.sum(
                    case((FinancialEntry.entry_side == "debit", FinancialEntry.amount), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((FinancialEntry.entry_side == "credit", FinancialEntry.amount), else_=0)
                ),
                0,
            ),
        )
        .filter(FinancialEntry.tenant_id == tenant_id)
        .one()
    )
    total_debits = quantize_money(totals[0])
    total_credits = quantize_money(totals[1])
    issues: list[FinancialReconciliationIssue] = []
    if total_debits != total_credits:
        issues.append(
            FinancialReconciliationIssue(
                "journal_unbalanced",
                None,
                total_debits,
                total_credits,
            )
        )

    missing_source_rows = db.execute(
        text(
            """
            SELECT 'missing_sale_transaction', i.id, i.total_amount
            FROM invoices i
            WHERE i.tenant_id = :tenant_id
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = i.tenant_id
                    AND ft.event_type = 'sale'
                    AND ft.source_id = i.id
              )
            UNION ALL
            SELECT 'missing_sales_return_transaction', sr.id, sr.total_refund
            FROM sales_returns sr
            WHERE sr.tenant_id = :tenant_id AND sr.total_refund > 0
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = sr.tenant_id
                    AND ft.event_type = 'sales_return'
                    AND ft.source_id = sr.id
              )
            UNION ALL
            SELECT 'missing_customer_payment_transaction', cp.id, cp.amount
            FROM customer_payments cp
            WHERE cp.tenant_id = :tenant_id
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = cp.tenant_id
                    AND ft.event_type = 'customer_payment'
                    AND ft.source_id = cp.id
              )
            UNION ALL
            SELECT 'missing_purchase_receipt_transaction', po.id, po.total_cost
            FROM purchase_orders po
            WHERE po.tenant_id = :tenant_id AND po.status = 'RECEIVED' AND po.total_cost > 0
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = po.tenant_id
                    AND ft.event_type = 'purchase_receipt'
                    AND ft.source_id = po.id
              )
            UNION ALL
            SELECT 'missing_purchase_return_transaction', pr.id, pr.total_refund
            FROM purchase_returns pr
            WHERE pr.tenant_id = :tenant_id AND pr.total_refund > 0
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = pr.tenant_id
                    AND ft.event_type = 'purchase_return'
                    AND ft.source_id = pr.id
              )
            UNION ALL
            SELECT 'missing_supplier_payment_transaction', sp.id, sp.amount
            FROM supplier_payments sp
            WHERE sp.tenant_id = :tenant_id
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = sp.tenant_id
                    AND ft.event_type = 'supplier_payment'
                    AND ft.source_id = sp.id
              )
            UNION ALL
            SELECT 'missing_expense_transaction', e.id, e.amount
            FROM expenses e
            WHERE e.tenant_id = :tenant_id
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = e.tenant_id
                    AND ft.event_type = 'expense'
                    AND ft.source_id = e.id
              )
            UNION ALL
            SELECT 'missing_shift_adjustment_transaction', s.id, abs(s.difference)
            FROM shifts s
            WHERE s.tenant_id = :tenant_id AND s.status = 'CLOSED' AND s.difference <> 0
              AND NOT EXISTS (
                  SELECT 1 FROM financial_transactions ft
                  WHERE ft.tenant_id = s.tenant_id
                    AND ft.event_type = 'shift_adjustment'
                    AND ft.source_id = s.id
              )
            ORDER BY 1, 2
            """
        ),
        {"tenant_id": tenant_id},
    ).all()
    issues.extend(
        FinancialReconciliationIssue(
            issue_type,
            entity_id,
            quantize_money(source_amount),
            ZERO_MONEY,
        )
        for issue_type, entity_id, source_amount in missing_source_rows
    )

    customers = db.query(Customer).filter(Customer.tenant_id == tenant_id).all()
    receivable_by_customer = {
        customer_id: quantize_money(amount or ZERO_MONEY)
        for customer_id, amount in (
            db.query(
                FinancialTransaction.customer_id, _account_balance_expression("customer_receivable")
            )
            .join(FinancialEntry)
            .filter(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.customer_id.is_not(None),
            )
            .group_by(FinancialTransaction.customer_id)
            .all()
        )
    }
    credit_by_customer = {
        customer_id: quantize_money(-(amount or ZERO_MONEY))
        for customer_id, amount in (
            db.query(
                FinancialTransaction.customer_id, _account_balance_expression("customer_credit")
            )
            .join(FinancialEntry)
            .filter(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.customer_id.is_not(None),
            )
            .group_by(FinancialTransaction.customer_id)
            .all()
        )
    }
    for customer in customers:
        receivable = receivable_by_customer.get(customer.id, ZERO_MONEY)
        if receivable != customer.total_debt:
            issues.append(
                FinancialReconciliationIssue(
                    "customer_debt_mismatch", customer.id, customer.total_debt, receivable
                )
            )
        customer_credit = credit_by_customer.get(customer.id, ZERO_MONEY)
        if customer_credit != customer.credit_balance:
            issues.append(
                FinancialReconciliationIssue(
                    "customer_credit_mismatch",
                    customer.id,
                    customer.credit_balance,
                    customer_credit,
                )
            )

    suppliers = db.query(Supplier).filter(Supplier.tenant_id == tenant_id).all()
    payable_by_supplier = {
        supplier_id: quantize_money(-(amount or ZERO_MONEY))
        for supplier_id, amount in (
            db.query(
                FinancialTransaction.supplier_id, _account_balance_expression("supplier_payable")
            )
            .join(FinancialEntry)
            .filter(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.supplier_id.is_not(None),
            )
            .group_by(FinancialTransaction.supplier_id)
            .all()
        )
    }
    for supplier in suppliers:
        total_purchases = (
            db.query(func.sum(PurchaseOrder.total_cost))
            .filter(
                PurchaseOrder.supplier_id == supplier.id,
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.status == "RECEIVED",
            )
            .scalar()
            or ZERO_MONEY
        )
        total_payments = (
            db.query(func.sum(SupplierPayment.amount))
            .filter(
                SupplierPayment.supplier_id == supplier.id,
                SupplierPayment.tenant_id == tenant_id,
            )
            .scalar()
            or ZERO_MONEY
        )
        total_returns = (
            db.query(func.sum(PurchaseReturn.total_refund))
            .filter(
                PurchaseReturn.supplier_id == supplier.id,
                PurchaseReturn.tenant_id == tenant_id,
            )
            .scalar()
            or ZERO_MONEY
        )
        stored_balance = quantize_money(total_purchases - total_payments - total_returns)
        payable = payable_by_supplier.get(supplier.id, ZERO_MONEY)
        if payable != stored_balance:
            issues.append(
                FinancialReconciliationIssue(
                    "supplier_payable_mismatch", supplier.id, stored_balance, payable
                )
            )

    shifts = db.query(Shift).filter(Shift.tenant_id == tenant_id).all()
    for shift in shifts:
        operational_delta = (
            db.query(_account_balance_expression("cash_drawer"))
            .select_from(FinancialTransaction)
            .join(FinancialEntry)
            .filter(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.shift_id == shift.id,
                FinancialTransaction.event_type != "shift_adjustment",
            )
            .scalar()
            or ZERO_MONEY
        )
        expected = quantize_money(shift.opening_balance + operational_delta)
        if shift.status == "CLOSED" and expected != shift.expected_closing_balance:
            issues.append(
                FinancialReconciliationIssue(
                    "shift_expected_cash_mismatch",
                    shift.id,
                    shift.expected_closing_balance,
                    expected,
                )
            )
        if shift.status == "CLOSED" and shift.actual_closing_balance is not None:
            full_cash_delta = (
                db.query(_account_balance_expression("cash_drawer"))
                .select_from(FinancialTransaction)
                .join(FinancialEntry)
                .filter(
                    FinancialTransaction.tenant_id == tenant_id,
                    FinancialTransaction.shift_id == shift.id,
                )
                .scalar()
                or ZERO_MONEY
            )
            actual = quantize_money(shift.opening_balance + full_cash_delta)
            if actual != shift.actual_closing_balance:
                issues.append(
                    FinancialReconciliationIssue(
                        "shift_actual_cash_mismatch",
                        shift.id,
                        shift.actual_closing_balance,
                        actual,
                    )
                )

    issue_count = len(issues)
    return FinancialReconciliationReport(
        checked_transactions=db.query(FinancialTransaction)
        .filter(FinancialTransaction.tenant_id == tenant_id)
        .count(),
        checked_shifts=len(shifts),
        checked_customers=len(customers),
        checked_suppliers=len(suppliers),
        total_debits=total_debits,
        total_credits=total_credits,
        issue_count=issue_count,
        issues_truncated=issue_count > issue_limit,
        issues=issues[:issue_limit],
    )
