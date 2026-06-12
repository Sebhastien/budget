"""CLI entry point and commands."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import click

import budget.db as _db
from budget.db import (
    generate_rollback_id,
    init_db,
    insert_audit_event,
    insert_category,
    insert_household_member,
    insert_schedule,
    list_categories,
    list_household_members,
    list_schedules,
    set_db_path,
)
from budget.models import AuditEvent, Category, CategoryTier, HouseholdMember


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _out_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


def _require_db() -> None:
    if _db.DB_PATH is None or not _db.DB_PATH.exists():
        click.echo("Database not initialized. Run `budget init` first.", err=True)
        sys.exit(1)


def _audit(action: str, entity_type: str, entity_id: str, payload: dict[str, Any], hint: str = "") -> str:
    rollback_id = generate_rollback_id()
    event = AuditEvent(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        rollback_id=rollback_id,
        payload=payload,
        user_hint=hint,
    )
    insert_audit_event(event)
    return rollback_id


def _default_categories() -> list[Category]:
    return [
        # Income
        Category(name="Payroll", tier=CategoryTier.INCOME, group="Income", sacred=True),
        Category(name="Other Income", tier=CategoryTier.INCOME, group="Income"),
        # Primary
        Category(name="Mortgage / Rent", tier=CategoryTier.PRIMARY, group="Housing", sacred=True),
        Category(name="HOA", tier=CategoryTier.PRIMARY, group="Housing"),
        Category(name="Electricity", tier=CategoryTier.PRIMARY, group="Utilities"),
        Category(name="Water", tier=CategoryTier.PRIMARY, group="Utilities"),
        Category(name="Natural Gas", tier=CategoryTier.PRIMARY, group="Utilities"),
        Category(name="Trash", tier=CategoryTier.PRIMARY, group="Utilities"),
        Category(name="Internet", tier=CategoryTier.PRIMARY, group="Utilities"),
        Category(name="Cell Phone", tier=CategoryTier.PRIMARY, group="Utilities"),
        Category(name="Auto Insurance", tier=CategoryTier.PRIMARY, group="Insurance"),
        Category(name="Health Insurance", tier=CategoryTier.PRIMARY, group="Insurance"),
        Category(name="Home Insurance", tier=CategoryTier.PRIMARY, group="Insurance"),
        Category(name="Renters Insurance", tier=CategoryTier.PRIMARY, group="Insurance"),
        Category(name="Life Insurance", tier=CategoryTier.PRIMARY, group="Insurance"),
        Category(name="Car Loan", tier=CategoryTier.PRIMARY, group="Debt"),
        Category(name="Student Loan", tier=CategoryTier.PRIMARY, group="Debt"),
        Category(name="Credit Card Minimum", tier=CategoryTier.PRIMARY, group="Debt"),
        # Secondary
        Category(name="Groceries", tier=CategoryTier.SECONDARY, group="Living", sacred=True),
        Category(name="Fuel / Gasoline", tier=CategoryTier.SECONDARY, group="Living"),
        Category(name="Shopping", tier=CategoryTier.SECONDARY, group="Living"),
        # Tertiary
        Category(name="Entertainment", tier=CategoryTier.TERTIARY, group="Discretionary", cut_priority=10),
        Category(name="Streaming", tier=CategoryTier.TERTIARY, group="Discretionary", cut_priority=5),
        Category(name="Dining Out", tier=CategoryTier.TERTIARY, group="Discretionary", cut_priority=8),
        Category(name="Gym", tier=CategoryTier.TERTIARY, group="Discretionary", cut_priority=3),
        Category(name="Subscriptions", tier=CategoryTier.TERTIARY, group="Discretionary", cut_priority=6),
        # Savings / Debt payoff
        Category(name="Emergency Fund", tier=CategoryTier.SAVINGS, group="Savings", sacred=True),
        Category(name="Retirement", tier=CategoryTier.SAVINGS, group="Savings"),
        Category(name="Extra Debt Payoff", tier=CategoryTier.DEBT_PAYOFF, group="Debt", sacred=True),
        Category(name="Property / Land Fund", tier=CategoryTier.SAVINGS, group="Savings", sacred=True),
        Category(name="Transfer", tier=CategoryTier.TRANSFER, group="Transfer"),
        # Optional household-context categories useful for many families
        Category(name="Bulk Grocery / Warehouse", tier=CategoryTier.SECONDARY, group="Living", sacred=True),
        Category(name="Fresh Groceries", tier=CategoryTier.SECONDARY, group="Living", sacred=True),
        Category(name="Prepared / Frozen Meals", tier=CategoryTier.SECONDARY, group="Living"),
        Category(name="Baby / Kids", tier=CategoryTier.PRIMARY, group="Kids", sacred=True),
        Category(name="Health / Fitness / Supplements", tier=CategoryTier.SECONDARY, group="Health", sacred=True),
        Category(name="Family Events / Birthdays", tier=CategoryTier.TERTIARY, group="Discretionary"),
        Category(name="Travel / Family Support Fund", tier=CategoryTier.SAVINGS, group="Savings"),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
@click.option("--data-dir", type=click.Path(), default=None, envvar="BUDGET_DATA_DIR")
@click.pass_context
def main(ctx: click.Context, data_dir: str | None) -> None:
    """Household budgeting CLI. Monthly budget first; paycheck plans follow."""
    if data_dir is None:
        data_dir = os.path.expanduser("~/.budget")
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    db_file = path / "budget.db"
    set_db_path(db_file)
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = str(path)
    ctx.obj["db_path"] = str(db_file)


@main.command()
@click.option("--json", "json_mode", is_flag=True, help="Output JSON only.")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def init(ctx: click.Context, json_mode: bool, dry_run: bool) -> None:
    """Initialize the budget database and seed default categories."""
    if dry_run:
        payload = {
            "status": "dry_run",
            "db_path": str(_db.DB_PATH),
            "would_seed_categories": len(_default_categories()),
            "rollback_id": None,
        }
        if json_mode:
            _out_json(payload)
        else:
            click.echo(f"Would initialize database at {_db.DB_PATH}")
            click.echo(f"Would seed {payload['would_seed_categories']} default categories.")
        return

    init_db()
    seeded = 0
    existing_names = {c.name for c in list_categories()}
    for cat in _default_categories():
        if cat.name not in existing_names:
            insert_category(cat)
            existing_names.add(cat.name)
            seeded += 1
    # Seed default household member
    members = list_household_members(active_only=True)
    if not members:
        insert_household_member(
            HouseholdMember(name="Primary", role="primary")
        )
    rollback_id = _audit("init", "system", "system", {"seeded_categories": seeded})
    if json_mode:
        _out_json({
            "status": "initialized",
            "db_path": str(_db.DB_PATH),
            "seeded_categories": seeded,
            "rollback_id": rollback_id,
        })
    else:
        click.echo(f"Initialized database at {_db.DB_PATH}")
        click.echo(f"Seeded {seeded} default categories.")
        click.echo(f"Rollback ID: {rollback_id}")


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@main.command("import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("--map-date", default=None, help="Override date column name.")
@click.option("--map-payee", default=None, help="Override payee/description column name.")
@click.option("--map-amount", default=None, help="Override amount column name.")
@click.option("--map-debit", default=None, help="Override debit column name.")
@click.option("--map-credit", default=None, help="Override credit column name.")
@click.option("--account-id", default="default", help="Account these rows belong to (scopes dedupe).")
def import_csv(
    file: str,
    json_mode: bool,
    dry_run: bool,
    map_date: str | None,
    map_payee: str | None,
    map_amount: str | None,
    map_debit: str | None,
    map_credit: str | None,
    account_id: str,
) -> None:
    _require_db()
    from budget.csv_import import import_csv_file

    overrides: dict[str, str] = {}
    if map_date:
        overrides["date"] = map_date
    if map_payee:
        overrides["payee"] = map_payee
    if map_amount:
        overrides["amount"] = map_amount
    if map_debit:
        overrides["debit"] = map_debit
    if map_credit:
        overrides["credit"] = map_credit

    result = import_csv_file(Path(file), mapping_overrides=overrides or None, dry_run=dry_run, account_id=account_id)
    rollback_id = None if dry_run else _audit("import", "import_batch", result["batch_id"], result)
    result["rollback_id"] = rollback_id
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Imported {result['imported']} rows from {result['file_name']}")
        click.echo(f"Duplicates skipped: {result['duplicates']}")
        click.echo(f"Skipped: {result['skipped']}")
        click.echo(f"Rollback ID: {rollback_id}")


# ---------------------------------------------------------------------------
# Recurring suggestions
# ---------------------------------------------------------------------------

@main.group("recurring")
def recurring() -> None:
    """Recurring item detection and management."""
    pass


@recurring.command("suggest")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--start-date", type=str, default=None)
@click.option("--end-date", type=str, default=None)
def recurring_suggest(json_mode: bool, start_date: str | None, end_date: str | None) -> None:
    _require_db()
    from budget.recurring import suggest_recurring_streams
    from budget.db import list_transactions

    s = date.fromisoformat(start_date) if start_date else date.today().replace(day=1)
    e = date.fromisoformat(end_date) if end_date else date.today()
    txs = list_transactions(start_date=s, end_date=e)
    suggestions = suggest_recurring_streams(txs)
    payload = [s.model_dump(mode="json") for s in suggestions]
    if json_mode:
        _out_json({"suggestions": payload})
    else:
        click.echo(f"Found {len(suggestions)} recurring suggestion(s)")
        for sug in suggestions:
            flag = "✓" if sug.confidence.value == "high" else "?"
            amt = sug.amount_cents or (sug.amount_max_cents or 0)
            click.echo(f"  {flag} {sug.payee} | {sug.cadence.value if sug.cadence else '?'} | ${amt/100:.2f} | {sug.confidence.value}")


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

@main.group("schedules")
def schedules() -> None:
    """Confirmed recurring schedules."""
    pass


@schedules.command("list")
@click.option("--json", "json_mode", is_flag=True)
def schedules_list(json_mode: bool) -> None:
    _require_db()
    items = list_schedules(active_only=True)
    payload = [s.model_dump(mode="json") for s in items]
    if json_mode:
        _out_json({"schedules": payload})
    else:
        click.echo(f"Active schedules: {len(items)}")
        for s in items:
            amt = s.amount_cents or (s.amount_max_cents or 0)
            click.echo(f"  {s.name} | {s.cadence.value} | ${amt/100:.2f} | {s.direction.value}")


@schedules.command("confirm")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("--payee", required=True)
@click.option("--direction", type=click.Choice(["income", "expense"]), default="expense")
@click.option("--cadence", type=click.Choice(["weekly", "biweekly", "semi_monthly", "monthly", "quarterly", "annual"]), default="monthly")
@click.option("--amount", type=str, default=None)
@click.option("--category-id", default=None)
@click.option("--autopay", is_flag=True)
@click.option("--start-date", type=str, default=None)
def schedules_confirm(
    json_mode: bool,
    dry_run: bool,
    payee: str,
    direction: str,
    cadence: str,
    amount: str | None,
    category_id: str | None,
    autopay: bool,
    start_date: str | None,
) -> None:
    _require_db()
    from budget.models import Schedule, Direction, Cadence, AmountBehavior
    from budget.db import insert_schedule

    amt_cents = None
    if amount:
        from budget.models import to_cents
        amt_cents = to_cents(amount)
    sch = Schedule(
        name=payee,
        payee=payee,
        direction=Direction(direction),
        category_id=category_id,
        cadence=Cadence(cadence),
        amount_cents=amt_cents,
        amount_behavior=AmountBehavior.FIXED if amt_cents else AmountBehavior.VARIABLE,
        autopay=autopay,
        start_date=date.fromisoformat(start_date) if start_date else None,
        source="manual",
    )
    if cadence == "semi_monthly":
        sch.cadence_days = [15, 0]
    if not dry_run:
        insert_schedule(sch)
    rollback_id = None if dry_run else _audit("schedule_confirm", "schedule", sch.id, sch.model_dump(mode="json"))
    if json_mode:
        _out_json({"status": "confirmed", "schedule": sch.model_dump(mode="json"), "rollback_id": rollback_id})
    else:
        click.echo(f"Confirmed schedule: {sch.name}")
        click.echo(f"Rollback ID: {rollback_id}")


# ---------------------------------------------------------------------------
# Monthly Budget
# ---------------------------------------------------------------------------

@main.group("budget")
def budget_cmd() -> None:
    """Monthly budget planning."""
    pass


@budget_cmd.command("create")
@click.option("--month", type=str, required=True, help="YYYY-MM")
@click.option("--mode", type=click.Choice(["normal", "tighten", "emergency", "debt_attack", "land_saving", "new_baby", "travel", "holiday"]), default="normal")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def budget_create(month: str, mode: str, json_mode: bool, dry_run: bool) -> None:
    _require_db()
    from budget.budget_engine import create_monthly_budget
    from budget.models import BudgetMode

    target_month = date.fromisoformat(f"{month}-01")
    result = create_monthly_budget(target_month, mode=BudgetMode(mode), dry_run=dry_run)
    if result["status"] == "exists":
        click.echo("Budget already exists for this month.", err=True)
        sys.exit(1)
    budget_id = result["budget"]["id"]
    rollback_id = None if dry_run else _audit("budget_create", "monthly_budget", budget_id, result)
    result["rollback_id"] = rollback_id
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Created monthly budget for {month}")
        click.echo(f"Rollback ID: {rollback_id}")


@budget_cmd.command("show")
@click.option("--month", type=str, required=True, help="YYYY-MM")
@click.option("--json", "json_mode", is_flag=True)
def budget_show(month: str, json_mode: bool) -> None:
    _require_db()
    from budget.budget_engine import get_budget_variance

    target_month = date.fromisoformat(f"{month}-01")
    result = get_budget_variance(target_month)
    if result["status"] == "not_found":
        click.echo("Budget not found.", err=True)
        sys.exit(1)
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Budget for {month} ({result['mode']})")
        click.echo(f"  Income:      ${result['total_income_cents']/100:.2f}")
        click.echo(f"  Primary:     ${result['primary_expenses_cents']/100:.2f}")
        click.echo(f"  Secondary:   ${result['secondary_expenses_cents']/100:.2f}")
        click.echo(f"  Tertiary:    ${result['tertiary_expenses_cents']/100:.2f}")
        click.echo(f"  Savings:     ${result['savings_targets_cents']/100:.2f}")
        click.echo(f"  Debt payoff: ${result['extra_debt_payoff_cents']/100:.2f}")
        click.echo(f"  Remaining:   ${result['remaining_cents']/100:.2f}")
        click.echo("Lines:")
        for line in result["lines"]:
            click.echo(f"  {line['category_name']}: planned=${line['planned_cents']/100:.2f} spent=${line['spent_cents']/100:.2f} var=${line['variance_cents']/100:.2f}")


# ---------------------------------------------------------------------------
# Paycheck
# ---------------------------------------------------------------------------

@main.group("paycheck")
def paycheck() -> None:
    """Paycheck planning."""
    pass


@paycheck.command("plan")
@click.option("--date", "plan_date_str", type=str, required=True, help="YYYY-MM-DD")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def paycheck_plan(plan_date_str: str, json_mode: bool, dry_run: bool) -> None:
    _require_db()
    from budget.paycheck import create_paycheck_plan
    from datetime import date

    plan_date = date.fromisoformat(plan_date_str)
    result = create_paycheck_plan(plan_date, dry_run=dry_run)
    if result["status"] == "error":
        click.echo(result["message"], err=True)
        sys.exit(1)
    plan_id = result["plan"]["id"]
    rollback_id = None if dry_run else _audit("paycheck_plan", "paycheck_plan", plan_id, result)
    result["rollback_id"] = rollback_id
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Created paycheck plan for {plan_date}")
        click.echo(f"Next paycheck: {result['plan']['next_paycheck_date']}")
        click.echo(f"Unallocated: ${result['plan']['unallocated_cents']/100:.2f}")
        click.echo(f"Rollback ID: {rollback_id}")


@paycheck.command("edit")
@click.option("--plan-id", type=str, required=True)
@click.option("--name", type=str, required=True)
@click.option("--amount", type=str, required=True)
@click.option("--direction", type=click.Choice(["income", "expense"]), default="expense")
@click.option("--category-id", default=None)
@click.option("--notes", default="")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def paycheck_edit(
    plan_id: str,
    name: str,
    amount: str,
    direction: str,
    category_id: str | None,
    notes: str,
    json_mode: bool,
    dry_run: bool,
) -> None:
    _require_db()
    from budget.paycheck import add_manual_allocation
    from budget.models import to_cents

    result = add_manual_allocation(
        plan_id, name, to_cents(amount), direction, category_id, notes, dry_run=dry_run,
    )
    if result["status"] == "error":
        click.echo(result["message"], err=True)
        sys.exit(1)
    rollback_id = None if dry_run else _audit("paycheck_edit", "paycheck_plan", plan_id, result)
    result["rollback_id"] = rollback_id
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Added manual allocation to paycheck plan: {name}")
        click.echo(f"Rollback ID: {rollback_id}")


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@main.command()
@click.option("--month", type=str, required=True, help="YYYY-MM")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def reconcile(month: str, json_mode: bool, dry_run: bool) -> None:
    _require_db()
    from budget.reconciliation import reconcile_month

    target_month = date.fromisoformat(f"{month}-01")
    result = reconcile_month(target_month, dry_run=dry_run)
    rollback_id = None if dry_run else _audit("reconcile", "month", month, result)
    result["rollback_id"] = rollback_id
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Reconciled {month}")
        click.echo(f"Transactions updated: {result['transactions_updated']}")
        click.echo(f"Paychecks matched: {result['paychecks_matched']}")
        click.echo(f"Remaining: ${result['budget_remaining_cents']/100:.2f}")
        click.echo(f"Rollback ID: {rollback_id}")


# ---------------------------------------------------------------------------
# Debt accounts
# ---------------------------------------------------------------------------

@main.group("debt")
def debt() -> None:
    """Debt account management."""
    pass


@debt.command("add")
@click.option("--name", required=True)
@click.option("--balance", type=str, required=True)
@click.option("--min-payment", type=str, required=True)
@click.option("--apr", type=str, default=None, help="Annual percentage rate, e.g. 22.99")
@click.option("--strategy", type=click.Choice(["avalanche", "snowball", "custom"]), default="avalanche")
@click.option("--priority", type=int, default=0, help="Custom payoff priority; lower comes first.")
@click.option("--category-id", default=None)
@click.option("--notes", default="")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def debt_add(
    name: str,
    balance: str,
    min_payment: str,
    apr: str | None,
    strategy: str,
    priority: int,
    category_id: str | None,
    notes: str,
    json_mode: bool,
    dry_run: bool,
) -> None:
    _require_db()
    from budget.db import insert_debt_account
    from budget.models import DebtAccount, to_cents

    balance_cents = to_cents(balance)
    debt_account = DebtAccount(
        name=name,
        original_balance_cents=balance_cents,
        current_balance_cents=balance_cents,
        min_payment_cents=to_cents(min_payment),
        interest_rate_percent=Decimal(apr) if apr else None,
        payoff_strategy=cast(Literal["avalanche", "snowball", "custom"], strategy),
        payoff_priority=priority,
        category_id=category_id,
        notes=notes,
    )
    if not dry_run:
        insert_debt_account(debt_account)
    rollback_id = None if dry_run else _audit("debt_add", "debt_account", debt_account.id, debt_account.model_dump(mode="json"))
    payload = {"debt": debt_account.model_dump(mode="json"), "rollback_id": rollback_id}
    if json_mode:
        _out_json(payload)
    else:
        click.echo(f"Added debt: {name}")
        click.echo(f"Rollback ID: {rollback_id}")


@debt.command("list")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--all", "show_all", is_flag=True, help="Include inactive debts.")
def debt_list(json_mode: bool, show_all: bool) -> None:
    _require_db()
    from budget.db import list_debt_accounts

    debts = list_debt_accounts(active_only=not show_all)
    if json_mode:
        _out_json({"debts": [d.model_dump(mode="json") for d in debts]})
    else:
        click.echo(f"Debt accounts: {len(debts)}")
        for d in debts:
            apr = f"{d.interest_rate_percent}%" if d.interest_rate_percent is not None else "n/a"
            status = "active" if d.active else "inactive"
            click.echo(
                f"  {d.id} | {d.name} | ${d.current_balance_cents/100:.2f} | "
                f"min ${d.min_payment_cents/100:.2f} | {apr} | {d.payoff_strategy} | {status}"
            )


@debt.command("update")
@click.option("--id", "debt_id", required=True)
@click.option("--name", default=None)
@click.option("--balance", type=str, default=None)
@click.option("--original-balance", type=str, default=None)
@click.option("--min-payment", type=str, default=None)
@click.option("--apr", type=str, default=None)
@click.option("--strategy", type=click.Choice(["avalanche", "snowball", "custom"]), default=None)
@click.option("--priority", type=int, default=None)
@click.option("--category-id", default=None)
@click.option("--notes", default=None)
@click.option("--active/--inactive", default=None)
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def debt_update(
    debt_id: str,
    name: str | None,
    balance: str | None,
    original_balance: str | None,
    min_payment: str | None,
    apr: str | None,
    strategy: str | None,
    priority: int | None,
    category_id: str | None,
    notes: str | None,
    active: bool | None,
    json_mode: bool,
    dry_run: bool,
) -> None:
    _require_db()
    from budget.db import get_debt_account_by_id, update_debt_account
    from budget.models import to_cents

    debt_account = get_debt_account_by_id(debt_id)
    if debt_account is None:
        click.echo("Debt account not found.", err=True)
        sys.exit(1)

    if name is not None:
        debt_account.name = name
    if balance is not None:
        debt_account.current_balance_cents = to_cents(balance)
    if original_balance is not None:
        debt_account.original_balance_cents = to_cents(original_balance)
    if min_payment is not None:
        debt_account.min_payment_cents = to_cents(min_payment)
    if apr is not None:
        debt_account.interest_rate_percent = Decimal(apr)
    if strategy is not None:
        debt_account.payoff_strategy = cast(Literal["avalanche", "snowball", "custom"], strategy)
    if priority is not None:
        debt_account.payoff_priority = priority
    if category_id is not None:
        debt_account.category_id = category_id
    if notes is not None:
        debt_account.notes = notes
    if active is not None:
        debt_account.active = active

    if not dry_run:
        update_debt_account(debt_account)
    rollback_id = None if dry_run else _audit("debt_update", "debt_account", debt_account.id, debt_account.model_dump(mode="json"))
    payload = {"debt": debt_account.model_dump(mode="json"), "rollback_id": rollback_id}
    if json_mode:
        _out_json(payload)
    else:
        click.echo(f"Updated debt: {debt_account.name}")
        click.echo(f"Rollback ID: {rollback_id}")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@main.group("report")
def report() -> None:
    """Household intelligence reports."""
    pass


@report.command("month")
@click.option("--month", type=str, required=True, help="YYYY-MM")
@click.option("--json", "json_mode", is_flag=True)
def report_month(month: str, json_mode: bool) -> None:
    _require_db()
    from budget.reports import dashboard, month_over_month

    target_month = date.fromisoformat(f"{month}-01")
    dash = dashboard(target_month)
    mom = month_over_month(target_month)
    payload = {
        "dashboard": dash,
        "month_over_month": mom,
    }
    if json_mode:
        _out_json(payload)
    else:
        click.echo(f"=== Dashboard for {month} ===")
        click.echo(f"Cash on hand:      ${dash['cash_on_hand_cents']/100:.2f}")
        click.echo(f"Credit balances:   ${dash['credit_card_balances_cents']/100:.2f}")
        click.echo(f"Net worth:         ${dash['net_worth_cents']/100:.2f}")
        click.echo(f"Upcoming bills:    ${dash['upcoming_bills_cents']/100:.2f}")
        click.echo(f"Safe to spend:     ${dash['safe_to_spend_cents']/100:.2f}")
        click.echo(f"Savings rate:      {_safe_pct(dash['savings_current_cents'], dash['savings_target_cents'])}%")
        click.echo(f"Actual income:     ${dash['actual_income_cents']/100:.2f}")
        click.echo(f"Actual expense:    ${dash['actual_expense_cents']/100:.2f}")
        click.echo(f"Next action:       {dash['recommended_next_action']}")
        if dash["danger_zones"]:
            click.echo("Danger zones:")
            for dz in dash["danger_zones"]:
                click.echo(f"  {dz['category']}: over by ${dz['over_by_cents']/100:.2f}")
        click.echo(f"\n=== Month-over-month ===")
        click.echo(f"Total change: ${mom['total_change_cents']/100:.2f}")
        if mom["biggest_leaks"]:
            click.echo("Biggest leaks:")
            for leak in mom["biggest_leaks"][:3]:
                click.echo(f"  {leak['category_name']}: +${leak['delta_cents']/100:.2f}")


def _safe_pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


@report.command("dashboard")
@click.option("--json", "json_mode", is_flag=True)
def report_dashboard(json_mode: bool) -> None:
    _require_db()
    from budget.reports import dashboard
    dash = dashboard()
    if json_mode:
        _out_json(dash)
    else:
        click.echo(f"Cash on hand:      ${dash['cash_on_hand_cents']/100:.2f}")
        click.echo(f"Net worth:         ${dash['net_worth_cents']/100:.2f}")
        click.echo(f"Safe to spend:     ${dash['safe_to_spend_cents']/100:.2f}")
        click.echo(f"Upcoming bills:    ${dash['upcoming_bills_cents']/100:.2f}")
        click.echo(f"Next action:       {dash['recommended_next_action']}")
        if dash["danger_zones"]:
            click.echo("Danger zones:")
            for dz in dash["danger_zones"]:
                click.echo(f"  {dz['category']}: over by ${dz['over_by_cents']/100:.2f}")


@report.command("debt")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--extra-monthly", type=str, default="0", help="Extra monthly payment in dollars")
@click.option("--strategy", type=click.Choice(["avalanche", "snowball", "custom"]), default="avalanche")
def report_debt(json_mode: bool, extra_monthly: str, strategy: str) -> None:
    _require_db()
    from budget.reports import debt_payoff_projection
    from budget.models import to_cents
    result = debt_payoff_projection(to_cents(extra_monthly), strategy=strategy)
    if json_mode:
        _out_json(result)
    else:
        click.echo(f"Strategy: {result['strategy']}")
        click.echo(f"Total debt:         ${result['total_debt_cents']/100:.2f}")
        click.echo(f"Min payments:       ${result['min_payments_cents']/100:.2f}")
        click.echo(f"Extra monthly:      ${result['extra_monthly_cents']/100:.2f}")
        click.echo(f"Monthly budget:     ${result['monthly_payment_budget_cents']/100:.2f}")
        click.echo(f"Est. months to payoff: {result['estimated_months_to_payoff']}")
        click.echo(f"Debt-free date:     {result['debt_free_date']}")
        click.echo(f"Est. interest:      ${result['total_interest_cents']/100:.2f}")
        click.echo(f"Interest saved:     ${result['interest_saved_cents']/100:.2f}")
        if result["payoff_order"]:
            click.echo("Payoff order:")
            for item in result["payoff_order"]:
                click.echo(f"  {item['name']}: payoff month {item['payoff_month']}")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@main.command()
@click.option("--json", "json_mode", is_flag=True)
def export(json_mode: bool) -> None:
    _require_db()
    from budget.db import list_transactions, list_schedules, list_debt_accounts, list_savings_goals, list_accounts

    payload = {
        "transactions": [t.model_dump(mode="json") for t in list_transactions()],
        "schedules": [s.model_dump(mode="json") for s in list_schedules(active_only=False)],
        "debt_accounts": [d.model_dump(mode="json") for d in list_debt_accounts(active_only=False)],
        "savings_goals": [g.model_dump(mode="json") for g in list_savings_goals(active_only=False)],
        "accounts": [a.model_dump(mode="json") for a in list_accounts(active_only=False)],
    }
    if json_mode:
        _out_json(payload)
    else:
        click.echo(f"Exported {len(payload['transactions'])} transactions, {len(payload['schedules'])} schedules, etc.")


# ---------------------------------------------------------------------------
# Household data management
# ---------------------------------------------------------------------------

@main.group("member")
def member() -> None:
    """Household member management."""
    pass


@member.command("add")
@click.option("--name", required=True)
@click.option("--role", default="")
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def member_add(name: str, role: str, json_mode: bool, dry_run: bool) -> None:
    _require_db()
    from budget.models import HouseholdMember
    m = HouseholdMember(name=name, role=role)
    if not dry_run:
        insert_household_member(m)
    rollback_id = None if dry_run else _audit("member_add", "household_member", m.id, m.model_dump(mode="json"))
    if json_mode:
        _out_json({"member": m.model_dump(mode="json"), "rollback_id": rollback_id})
    else:
        click.echo(f"Added member: {name}")


@member.command("list")
@click.option("--json", "json_mode", is_flag=True)
def member_list(json_mode: bool) -> None:
    _require_db()
    members = list_household_members()
    if json_mode:
        _out_json({"members": [m.model_dump(mode="json") for m in members]})
    else:
        for m in members:
            click.echo(f"  {m.name} ({m.role})")


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

@main.command("categories")
@click.option("--json", "json_mode", is_flag=True)
def categories_list(json_mode: bool) -> None:
    _require_db()
    cats = list_categories()
    if json_mode:
        _out_json({"categories": [c.model_dump(mode="json") for c in cats]})
    else:
        for c in cats:
            click.echo(f"  {c.id} | {c.name} | {c.tier.value} | group={c.group}")


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

@main.group("account")
def account() -> None:
    """Account (cash, credit, savings) management."""
    pass


@account.command("add")
@click.option("--name", required=True)
@click.option("--type", "acc_type", type=click.Choice(["checking", "savings", "credit_card", "cash", "investment", "loan"]), required=True)
@click.option("--balance", type=str, default="0")
@click.option("--credit-limit", type=str, default=None)
@click.option("--json", "json_mode", is_flag=True)
@click.option("--dry-run", is_flag=True)
def account_add(name: str, acc_type: str, balance: str, credit_limit: str | None, json_mode: bool, dry_run: bool) -> None:
    _require_db()
    from budget.models import Account, AccountType, to_cents
    from budget.db import insert_account
    a = Account(
        name=name,
        account_type=AccountType(acc_type),
        current_balance_cents=to_cents(balance),
        credit_limit_cents=to_cents(credit_limit) if credit_limit else None,
    )
    if not dry_run:
        insert_account(a)
    rollback_id = None if dry_run else _audit("account_add", "account", a.id, a.model_dump(mode="json"))
    if json_mode:
        _out_json({"account": a.model_dump(mode="json"), "rollback_id": rollback_id})
    else:
        click.echo(f"Added account: {name}")


@account.command("list")
@click.option("--json", "json_mode", is_flag=True)
def account_list(json_mode: bool) -> None:
    _require_db()
    from budget.db import list_accounts
    accounts = list_accounts()
    if json_mode:
        _out_json({"accounts": [a.model_dump(mode="json") for a in accounts]})
    else:
        for a in accounts:
            click.echo(f"  {a.name} ({a.account_type.value}): ${a.current_balance_cents/100:.2f}")


if __name__ == "__main__":
    main()
