"""Reconciliation: actual posted transactions update plans and budgets."""

from __future__ import annotations

from datetime import date
from typing import Any

from budget.db import (
    get_monthly_budget,
    list_paycheck_plans_for_month,
    list_transactions,
    update_monthly_budget,
    update_paycheck_plan,
    update_transaction,
)
from budget.models import TransactionStatus


def reconcile_month(month: date, *, dry_run: bool = False) -> dict[str, Any]:
    month = month.replace(day=1)
    budget = get_monthly_budget(month)
    if not budget:
        return {"status": "error", "message": "Monthly budget not found"}

    # Update all unposted transactions in the month to posted
    start = month
    import calendar
    _, last_day = calendar.monthrange(month.year, month.month)
    end = month.replace(day=last_day)

    txs = list_transactions(start_date=start, end_date=end)
    updated_txs = 0
    for tx in txs:
        if tx.status == TransactionStatus.PENDING:
            tx.status = TransactionStatus.POSTED
            if not dry_run:
                update_transaction(tx)
            updated_txs += 1

    # Recalculate budget spent from posted transactions
    cat_spent: dict[str, int] = {}
    for tx in txs:
        if tx.status == TransactionStatus.POSTED and tx.direction.value == "expense" and tx.category_id:
            cat_spent[tx.category_id] = cat_spent.get(tx.category_id, 0) + tx.amount_cents

    for line in budget.lines:
        line.spent_cents = cat_spent.get(line.category_id, 0)

    # Update paycheck plans: match committed items to posted transactions
    paychecks = list_paycheck_plans_for_month(month)
    matched_plans = 0
    matched_tx_ids: set[str] = set()
    for pp in paychecks:
        changed = False
        for item in pp.committed:
            if item.status != "pending":
                continue
            # Match by schedule + approximate amount + date within window.
            # A posted transaction can satisfy at most one committed item.
            for tx in txs:
                if tx.id in matched_tx_ids or tx.status != TransactionStatus.POSTED:
                    continue
                if item.schedule_id and tx.payee and item.name.lower() in tx.payee.lower():
                    if abs(tx.amount_cents - item.planned_amount_cents) <= max(item.planned_amount_cents // 10, 100):
                        item.status = "paid"
                        item.actual_amount_cents = tx.amount_cents
                        matched_tx_ids.add(tx.id)
                        changed = True
                        break
        if changed and not dry_run:
            update_paycheck_plan(pp)
            matched_plans += 1

    if not dry_run:
        update_monthly_budget(budget)

    return {
        "status": "reconciled",
        "month": month.isoformat(),
        "transactions_updated": updated_txs,
        "paychecks_matched": matched_plans,
        "budget_remaining_cents": budget.remaining_cents(),
        "dry_run": dry_run,
    }
