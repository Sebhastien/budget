"""Paycheck planning engine."""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from budget.db import (
    get_monthly_budget,
    get_paycheck_plan,
    insert_paycheck_plan,
    list_categories,
    list_schedules,
    update_monthly_budget,
    update_paycheck_plan,
)
from budget.models import (
    BudgetLineItem,
    Cadence,
    CategoryTier,
    MonthlyBudget,
    PaycheckCommittedItem,
    PaycheckManualAllocation,
    PaycheckPlan,
    PaycheckReserve,
    Schedule,
)


def _month_end(month: date) -> date:
    _, last_day = calendar.monthrange(month.year, month.month)
    return month.replace(day=last_day)


def _find_next_paycheck_date(after: date, cadence: Cadence = Cadence.SEMI_MONTHLY) -> date:
    """Find the next standard paycheck date (15th or last day)."""
    y, m = after.year, after.month
    _, last_day = calendar.monthrange(y, m)
    candidates = [date(y, m, 15), date(y, m, last_day)]
    # Next month too
    nm = m + 1 if m < 12 else 1
    ny = y if m < 12 else y + 1
    _, last_day_nm = calendar.monthrange(ny, nm)
    candidates += [date(ny, nm, 15), date(ny, nm, last_day_nm)]
    valid = [c for c in candidates if c > after]
    return min(valid) if valid else after


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return months


def _schedule_amount(sch: Schedule) -> int:
    return sch.amount_cents or sch.amount_min_cents or 0


def _schedules_due_between(start: date, end: date) -> list[tuple[Schedule, date, int]]:
    """Return schedules with due dates and expected amounts in a window."""
    results = []
    months = _months_between(start, end)
    for sch in list_schedules(active_only=True):
        if sch.cadence == Cadence.MONTHLY:
            due_day = sch.start_date.day if sch.start_date else 1
            for year, month in months:
                _, last_day = calendar.monthrange(year, month)
                due = date(year, month, min(due_day, last_day))
                if start < due <= end:
                    results.append((sch, due, _schedule_amount(sch)))
        elif sch.cadence == Cadence.SEMI_MONTHLY:
            days = sch.cadence_days or [15, 0]
            for year, month in months:
                _, last_day = calendar.monthrange(year, month)
                for d in days:
                    due = date(year, month, last_day if d == 0 else min(d, last_day))
                    if start < due <= end:
                        results.append((sch, due, _schedule_amount(sch)))
        elif sch.cadence in (Cadence.BIWEEKLY, Cadence.WEEKLY):
            # Generate all occurrences in the window.
            for nxt in sch.next_expected_dates(start, count=6):
                if nxt > end:
                    break
                if start < nxt:
                    results.append((sch, nxt, _schedule_amount(sch)))
    return results


def create_paycheck_plan(
    paycheck_date: date,
    monthly_budget: MonthlyBudget | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    month = paycheck_date.replace(day=1)
    if monthly_budget is None:
        monthly_budget = get_monthly_budget(month)
    if monthly_budget is None:
        return {"status": "error", "message": "Monthly budget not found"}

    next_pay = _find_next_paycheck_date(paycheck_date)
    due_items = _schedules_due_between(paycheck_date, next_pay)

    committed: list[PaycheckCommittedItem] = []
    total_committed = 0

    for sch, due, amt in due_items:
        committed.append(PaycheckCommittedItem(
            name=sch.name,
            category_id=sch.category_id,
            schedule_id=sch.id,
            due_date=due,
            planned_amount_cents=amt,
        ))
        total_committed += amt

    # Recommended reserves only for controllable monthly categories.
    # Primary fixed bills stay in committed items; income is never reserved.
    categories = {c.id: c for c in list_categories()}
    reserves: list[PaycheckReserve] = []
    total_reserve = 0
    for line in monthly_budget.lines:
        cat = categories.get(line.category_id)
        if not cat or cat.tier not in (CategoryTier.SECONDARY, CategoryTier.TERTIARY):
            continue
        remaining_for_category = max(line.planned_cents - line.spent_cents, 0)
        reserve = remaining_for_category // 2
        if reserve > 0:
            reserves.append(PaycheckReserve(
                category_id=line.category_id,
                reserve_cents=reserve,
            ))
            total_reserve += reserve

    plan = PaycheckPlan(
        month=month,
        paycheck_date=paycheck_date,
        next_paycheck_date=next_pay,
        committed=committed,
        reserves=reserves,
        manual_allocations=[],
        unallocated_cents=monthly_budget.remaining_cents() - total_committed - total_reserve,
    )

    if not dry_run:
        insert_paycheck_plan(plan)

    return {
        "status": "created",
        "plan": plan.model_dump(mode="json"),
        "dry_run": dry_run,
    }


def add_manual_allocation(
    plan_id: str,
    name: str,
    amount_cents: int,
    direction: str = "expense",
    category_id: str | None = None,
    notes: str = "",
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = get_paycheck_plan(plan_id)
    if not plan:
        return {"status": "error", "message": "Paycheck plan not found"}

    from budget.models import Direction
    alloc = PaycheckManualAllocation(
        name=name,
        category_id=category_id,
        amount_cents=amount_cents,
        direction=Direction(direction),
        notes=notes,
    )

    plan.manual_allocations.append(alloc)

    # Update unallocated
    sign = -1 if alloc.direction == Direction.EXPENSE else 1
    plan.unallocated_cents += sign * amount_cents

    # Update monthly budget remaining
    budget = get_monthly_budget(plan.month)
    if budget:
        if alloc.direction == Direction.EXPENSE:
            # Deduct from the appropriate budget bucket if category matches
            for line in budget.lines:
                if line.category_id == category_id:
                    line.spent_cents += amount_cents
                    break
            else:
                # Uncategorized manual spend reduces remaining broadly
                pass
        else:
            # Income addition
            budget.total_income_cents += amount_cents
        if not dry_run:
            update_monthly_budget(budget)

    if not dry_run:
        update_paycheck_plan(plan)

    return {
        "status": "added",
        "plan": plan.model_dump(mode="json"),
        "dry_run": dry_run,
    }
