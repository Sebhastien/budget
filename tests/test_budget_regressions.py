"""Regression tests for review findings."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from budget.budget_engine import create_monthly_budget
from budget.cli import _default_categories
from budget.csv_import import import_csv_file, parse_amount, row_to_transaction
from budget.db import (
    get_paycheck_plan,
    init_db,
    insert_monthly_budget,
    insert_paycheck_plan,
    insert_schedule,
    insert_transaction,
    list_transactions,
    set_db_path,
)
from budget.models import (
    BudgetLineItem,
    Cadence,
    Direction,
    MonthlyBudget,
    PaycheckCommittedItem,
    PaycheckPlan,
    Schedule,
    Transaction,
    to_cents,
)
from budget.paycheck import create_paycheck_plan
from budget.reconciliation import reconcile_month
from budget.recurring import suggest_recurring_streams
from budget.reports import dashboard


def _fresh_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    set_db_path(path)
    init_db()
    return path


def test_paycheck_plan_includes_next_month_bills_in_cross_month_window() -> None:
    _fresh_db()
    bill = Schedule(
        name="Rent",
        payee="Rent",
        direction=Direction.EXPENSE,
        cadence=Cadence.MONTHLY,
        start_date=date(2025, 1, 1),
        amount_cents=to_cents("1200"),
    )
    insert_schedule(bill)
    insert_monthly_budget(MonthlyBudget(month=date(2025, 1, 1), total_income_cents=to_cents("5000")))

    result = create_paycheck_plan(date(2025, 1, 31), dry_run=False)

    assert result["status"] == "created"
    committed = result["plan"]["committed"]
    assert any(item["name"] == "Rent" and item["due_date"] == "2025-02-01" for item in committed)


def test_parse_amount_treats_parentheses_as_negative() -> None:
    assert parse_amount("($50.00)") == -5000

    tx = row_to_transaction(
        {"Date": "2025-05-01", "Description": "Grocery Store", "Amount": "($50.00)"},
        {"date": "Date", "payee": "Description", "amount": "Amount"},
        "sample.csv",
        "batch",
        1,
    )

    assert tx.amount_cents == 5000
    assert tx.direction == Direction.EXPENSE


def test_init_default_categories_do_not_drop_fuel_category() -> None:
    names = [c.name for c in _default_categories()]

    assert len(names) == len(set(names))
    assert "Natural Gas" in names
    assert "Fuel / Gasoline" in names


def test_income_keyword_matching_uses_word_boundaries() -> None:
    tx = row_to_transaction(
        {"Date": "2025-05-01", "Description": "Pinterest", "Amount": "-15.00"},
        {"date": "Date", "payee": "Description", "amount": "Amount"},
        "sample.csv",
        "batch",
        1,
    )

    assert tx.direction == Direction.EXPENSE


def test_recurring_seed_amount_filter_handles_zero_median() -> None:
    txs = [
        Transaction(date=date(2025, 1, 1), payee="Netflix", amount_cents=0, direction=Direction.EXPENSE),
        Transaction(date=date(2025, 2, 1), payee="Netflix", amount_cents=0, direction=Direction.EXPENSE),
        Transaction(date=date(2025, 3, 1), payee="Netflix", amount_cents=0, direction=Direction.EXPENSE),
    ]

    suggestions = suggest_recurring_streams(txs, config={"min_confidence": "none"})

    assert suggestions


def test_dedupe_hash_includes_account_id() -> None:
    tx1 = Transaction(
        account_id="checking",
        date=date(2025, 5, 1),
        payee="Same Merchant",
        amount_cents=1000,
        direction=Direction.EXPENSE,
    )
    tx2 = Transaction(
        account_id="credit-card",
        date=date(2025, 5, 1),
        payee="Same Merchant",
        amount_cents=1000,
        direction=Direction.EXPENSE,
    )

    assert tx1.compute_dedupe_hash() != tx2.compute_dedupe_hash()


def test_dashboard_upcoming_bills_uses_queried_month_window() -> None:
    _fresh_db()
    insert_schedule(Schedule(
        name="Rent",
        payee="Rent",
        direction=Direction.EXPENSE,
        cadence=Cadence.MONTHLY,
        start_date=date(2025, 5, 1),
        amount_cents=to_cents("1200"),
    ))

    dash = dashboard(date(2025, 5, 1))

    assert dash["upcoming_bills_cents"] == 120000
    assert dash["upcoming_bills"][0]["due_date"] == "2025-05-01"


def test_reconciliation_matches_transactions_one_to_one() -> None:
    _fresh_db()
    category_id = "utilities"
    insert_monthly_budget(MonthlyBudget(
        month=date(2025, 5, 1),
        lines=[BudgetLineItem(category_id=category_id, planned_cents=to_cents("200"))],
    ))
    plan = PaycheckPlan(
        month=date(2025, 5, 1),
        paycheck_date=date(2025, 5, 15),
        committed=[
            PaycheckCommittedItem(name="Utility", schedule_id="schedule-1", planned_amount_cents=to_cents("100")),
            PaycheckCommittedItem(name="Utility", schedule_id="schedule-2", planned_amount_cents=to_cents("100")),
        ],
    )
    insert_paycheck_plan(plan)
    insert_transaction(Transaction(
        date=date(2025, 5, 16),
        payee="Utility",
        amount_cents=to_cents("100"),
        direction=Direction.EXPENSE,
        category_id=category_id,
    ))

    reconcile_month(date(2025, 5, 1), dry_run=False)
    reconciled = get_paycheck_plan(plan.id)

    paid = [item for item in reconciled.committed if item.status == "paid"]
    pending = [item for item in reconciled.committed if item.status == "pending"]
    assert len(paid) == 1
    assert len(pending) == 1


def test_column_mapping_is_deterministic_and_priority_ordered() -> None:
    from budget.csv_import import KNOWN_COLUMNS, auto_detect_mapping

    # Aliases must be ordered (lists), not sets, so iteration order is stable.
    assert all(isinstance(aliases, list) for aliases in KNOWN_COLUMNS.values())

    # When several payee aliases are present, the higher-priority one wins,
    # and the result is identical on every call regardless of header order.
    headers = ["Date", "Name", "Description", "Amount"]
    first = auto_detect_mapping(headers)
    for _ in range(5):
        assert auto_detect_mapping(headers) == first
    assert first["payee"] == "Description"  # "description" outranks "name"


def test_historical_average_is_anchored_to_budget_month_not_today() -> None:
    from budget.budget_engine import _historical_avg_for_category

    _fresh_db()
    category_id = "groceries"
    # Spend $300 in each of the three months before May 2025.
    for month, day in ((2, 10), (3, 10), (4, 10)):
        insert_transaction(Transaction(
            date=date(2025, month, day),
            payee="Market",
            amount_cents=to_cents("300"),
            direction=Direction.EXPENSE,
            category_id=category_id,
        ))

    # Anchored to May 2025 the trailing window sees the spend; anchored to a
    # year later (closer to "today") it sees nothing.
    assert _historical_avg_for_category(category_id, date(2025, 5, 1)) == to_cents("300")
    assert _historical_avg_for_category(category_id, date(2026, 6, 1)) == 0


def test_income_keyword_does_not_override_negative_amount() -> None:
    # A signed-negative amount is authoritative even when the payee contains an
    # income keyword: "Interest Charge" / "Refund Fee" are debits.
    mapping = {"date": "Date", "payee": "Description", "amount": "Amount"}
    for payee in ("Interest Charge", "Refund Fee", "Deposit Fee"):
        tx = row_to_transaction(
            {"Date": "2025-05-01", "Description": payee, "Amount": "-30.00"},
            mapping, "sample.csv", "batch", 1,
        )
        assert tx.direction == Direction.EXPENSE, payee

    # A genuine positive deposit is still promoted to income.
    tx = row_to_transaction(
        {"Date": "2025-05-01", "Description": "Payroll Deposit", "Amount": "1500.00"},
        mapping, "sample.csv", "batch", 1,
    )
    assert tx.direction == Direction.INCOME


def test_csv_import_scopes_dedupe_by_account() -> None:
    _fresh_db()
    content = "Date,Description,Amount\n2025-05-01,Same Merchant,-10.00\n"
    fd, path = tempfile.mkstemp(suffix=".csv")
    Path(path).write_text(content)
    csv_path = Path(path)

    r1 = import_csv_file(csv_path, account_id="checking")
    r2 = import_csv_file(csv_path, account_id="credit-card")

    # Identical row, different account → both kept, not deduped as a collision.
    assert r1["imported"] == 1
    assert r2["imported"] == 1
    assert r2["duplicates"] == 0

    txs = list_transactions()
    assert {t.account_id for t in txs} == {"checking", "credit-card"}

    # Re-importing into an account that already has the row still dedupes.
    r3 = import_csv_file(csv_path, account_id="checking")
    assert r3["imported"] == 0
    assert r3["duplicates"] == 1
