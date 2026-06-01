"""Core acceptance tests for the budgeting CLI."""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from budget.csv_import import import_csv_file
from budget.db import (
    find_transaction_by_dedupe_hash,
    get_monthly_budget,
    get_paycheck_plan,
    init_db,
    list_schedules,
    list_transactions,
    set_db_path,
)
from budget.models import (
    Account,
    AccountType,
    BudgetMode,
    CategoryTier,
    DebtAccount,
    Direction,
    HouseholdMember,
    SavingsGoal,
    Schedule,
    to_cents,
)
from budget.budget_engine import create_monthly_budget, get_budget_variance
from budget.paycheck import create_paycheck_plan, add_manual_allocation
from budget.reconciliation import reconcile_month
from budget.reports import dashboard, month_over_month, debt_payoff_projection
from budget.recurring import suggest_recurring_streams


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    set_db_path(path)
    init_db()
    return path


def _sample_csv() -> Path:
    content = """Date,Description,Amount
2025-05-01,Payroll Deposit,5000.00
2025-05-01,Mortgage Payment,-1200.00
2025-05-03,Electric Company,-85.50
2025-05-05,Whole Foods,-142.30
2025-05-07,Netflix Subscription,-15.99
2025-05-10,Gas Station,-45.00
2025-05-12,Whole Foods,-198.10
2025-05-15,Payroll Deposit,5000.00
2025-05-15,Car Loan Payment,-350.00
2025-05-18,Electric Company,-88.20
2025-05-20,Whole Foods,-165.40
2025-05-22,Gas Station,-38.50
2025-06-01,Payroll Deposit,5000.00
2025-06-01,Mortgage Payment,-1200.00
"""
    fd, path = tempfile.mkstemp(suffix=".csv")
    Path(path).write_text(content)
    return Path(path)


# ---------------------------------------------------------------------------
# CSV Import & Dedupe
# ---------------------------------------------------------------------------

def test_csv_import_and_dedupe() -> None:
    db = _fresh_db()
    csv = _sample_csv()
    r1 = import_csv_file(csv)
    assert r1["imported"] == 14
    assert r1["duplicates"] == 0

    # Re-import must not inflate totals
    r2 = import_csv_file(csv)
    assert r2["imported"] == 0
    assert r2["duplicates"] == 14

    txs = list_transactions()
    assert len(txs) == 14
    income = [t for t in txs if t.direction == Direction.INCOME]
    expense = [t for t in txs if t.direction == Direction.EXPENSE]
    assert len(income) == 3  # three payroll deposits
    assert len(expense) == 11

    # All have dedupe hashes
    for t in txs:
        assert t.dedupe_hash
        assert find_transaction_by_dedupe_hash(t.dedupe_hash)


# ---------------------------------------------------------------------------
# Recurring Suggestions
# ---------------------------------------------------------------------------

def test_recurring_suggestions() -> None:
    db = _fresh_db()
    csv = _sample_csv()
    import_csv_file(csv)
    txs = list_transactions()
    suggestions = suggest_recurring_streams(txs)
    assert len(suggestions) >= 3

    payees = {s.payee for s in suggestions}
    assert "Payroll" in payees or "Payroll Deposit" in payees
    assert "Mortgage" in payees or "Mortgage Payment" in payees
    assert "Electricity" in payees or "Electric Company" in payees

    # Classifications exist
    for s in suggestions:
        assert s.confidence.value in ("high", "medium", "low", "none")
        assert s.reason_codes


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def test_schedule_semi_monthly() -> None:
    db = _fresh_db()
    sch = Schedule(
        name="Payroll",
        payee="Employer",
        direction=Direction.INCOME,
        cadence=Schedule.model_fields["cadence"].annotation("semi_monthly"),
        cadence_days=[15, 0],
        amount_cents=to_cents("5000"),
        source="manual",
    )
    from budget.db import insert_schedule
    insert_schedule(sch)
    loaded = list_schedules()[0]
    assert loaded.cadence.value == "semi_monthly"
    assert loaded.cadence_days == [15, 0]
    dates = loaded.next_expected_dates(date(2025, 5, 1), count=4)
    assert len(dates) == 4
    assert dates[0] == date(2025, 5, 15)
    assert dates[1] == date(2025, 5, 31)
    assert dates[2] == date(2025, 6, 15)
    assert dates[3] == date(2025, 6, 30)


# ---------------------------------------------------------------------------
# Monthly Budget
# ---------------------------------------------------------------------------

def test_monthly_budget_creation() -> None:
    db = _fresh_db()
    result = create_monthly_budget(date(2025, 5, 1), mode=BudgetMode.NORMAL, dry_run=False)
    assert result["status"] == "created"
    b = get_monthly_budget(date(2025, 5, 1))
    assert b is not None
    assert b.month == date(2025, 5, 1)
    assert b.mode == BudgetMode.NORMAL


def test_budget_variance() -> None:
    db = _fresh_db()
    create_monthly_budget(date(2025, 5, 1), dry_run=False)
    var = get_budget_variance(date(2025, 5, 1))
    assert var["status"] == "ok"
    assert "lines" in var


# ---------------------------------------------------------------------------
# Paycheck Plan
# ---------------------------------------------------------------------------

def test_paycheck_plan() -> None:
    db = _fresh_db()
    create_monthly_budget(date(2025, 5, 1), dry_run=False)
    result = create_paycheck_plan(date(2025, 5, 15), dry_run=False)
    assert result["status"] == "created"
    plan = get_paycheck_plan(result["plan"]["id"])
    assert plan is not None
    assert plan.paycheck_date == date(2025, 5, 15)
    assert plan.next_paycheck_date is not None


def test_manual_allocation_updates_budget() -> None:
    db = _fresh_db()
    create_monthly_budget(date(2025, 5, 1), dry_run=False)
    pr = create_paycheck_plan(date(2025, 5, 15), dry_run=False)
    plan_id = pr["plan"]["id"]
    result = add_manual_allocation(plan_id, "Extra debt", to_cents("1000"), "expense", dry_run=False)
    assert result["status"] == "added"
    plan = get_paycheck_plan(plan_id)
    assert len(plan.manual_allocations) == 1


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def test_reconcile() -> None:
    db = _fresh_db()
    csv = _sample_csv()
    import_csv_file(csv)
    create_monthly_budget(date(2025, 5, 1), dry_run=False)
    result = reconcile_month(date(2025, 5, 1), dry_run=False)
    assert result["status"] == "reconciled"
    assert result["transactions_updated"] >= 0


# ---------------------------------------------------------------------------
# Dashboard & Reports
# ---------------------------------------------------------------------------

def test_dashboard() -> None:
    db = _fresh_db()
    from budget.db import insert_account
    insert_account(Account(name="Checking", account_type=AccountType.CHECKING, current_balance_cents=to_cents("5000")))
    dash = dashboard(date(2025, 5, 1))
    assert dash["cash_on_hand_cents"] == 500000
    assert "recommended_next_action" in dash


def test_month_over_month() -> None:
    db = _fresh_db()
    mom = month_over_month(date(2025, 5, 1))
    assert mom["current_month"] == "2025-05-01"


def test_debt_projection() -> None:
    db = _fresh_db()
    from budget.db import insert_debt_account
    insert_debt_account(DebtAccount(
        name="Car Loan",
        original_balance_cents=to_cents("15000"),
        current_balance_cents=to_cents("8000"),
        min_payment_cents=to_cents("350"),
        interest_rate_percent=Decimal("4.5"),
    ))
    proj = debt_payoff_projection(extra_monthly_cents=to_cents("200"))
    assert proj["total_debt_cents"] == 800000
    assert proj["estimated_months_to_payoff"] > 0
