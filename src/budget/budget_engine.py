"""Monthly budget engine: combine schedules, historical spending, and user overrides."""

from __future__ import annotations

from datetime import date
from typing import Any

from budget.db import (
    get_monthly_budget,
    insert_monthly_budget,
    list_categories,
    list_schedules,
    list_transactions,
    update_monthly_budget,
)
from budget.models import (
    BudgetLineItem,
    BudgetMode,
    CategoryTier,
    MonthlyBudget,
    Schedule,
)


def _month_start_end(month: date) -> tuple[date, date]:
    start = month.replace(day=1)
    import calendar
    _, last_day = calendar.monthrange(start.year, start.month)
    end = start.replace(day=last_day)
    return start, end


def _historical_avg_for_category(category_id: str, as_of: date, months: int = 3) -> int:
    """Average monthly spending for a category over the months preceding ``as_of``.

    Anchored to the budget month, not ``date.today()``, so budgets built for past
    or future months average the correct trailing window.
    """
    import calendar
    anchor = as_of.replace(day=1)
    total = 0
    count = 0
    for i in range(1, months + 1):
        y, mth = anchor.year, anchor.month - i
        while mth <= 0:
            mth += 12
            y -= 1
        start = date(y, mth, 1)
        _, last_day = calendar.monthrange(y, mth)
        end = date(y, mth, last_day)
        txs = list_transactions(
            start_date=start, end_date=end, category_id=category_id
        )
        spent = sum(t.amount_cents for t in txs if t.direction.value == "expense")
        total += spent
        count += 1
    return total // count if count else 0


def _scheduled_amount_for_month(sch: Schedule, month: date) -> int | None:
    """Return expected amount for a schedule in a given month."""
    base = sch.amount_cents
    if base is None and sch.amount_min_cents is not None and sch.amount_max_cents is not None:
        base = (sch.amount_min_cents + sch.amount_max_cents) // 2
    if base is None:
        return None
    # Multiply by expected occurrences in month
    if sch.cadence.value == "semi_monthly":
        return base * 2
    if sch.cadence.value == "biweekly":
        return base * 2  # roughly 2 per month
    if sch.cadence.value == "weekly":
        return base * 4
    return base


def create_monthly_budget(
    month: date,
    mode: BudgetMode = BudgetMode.NORMAL,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    month = month.replace(day=1)
    existing = get_monthly_budget(month)
    if existing:
        return {"status": "exists", "budget": existing.model_dump(mode="json")}

    categories = {c.id: c for c in list_categories()}
    schedules = list_schedules(active_only=True)
    start, end = _month_start_end(month)

    lines: list[BudgetLineItem] = []
    total_income = 0
    primary = 0
    secondary = 0
    tertiary = 0
    savings = 0
    debt_payoff = 0

    # Initialize lines from categories
    for cat in categories.values():
        planned = 0
        # Apply schedule amounts for primary categories
        if cat.tier == CategoryTier.PRIMARY:
            for sch in schedules:
                if sch.category_id == cat.id:
                    amt = _scheduled_amount_for_month(sch, month)
                    if amt:
                        planned += amt
            if planned == 0:
                # Fallback to historical average for controllable primaries? No, use cap if set
                planned = cat.monthly_cap_cents or 0
        elif cat.tier in (CategoryTier.SECONDARY, CategoryTier.TERTIARY):
            planned = cat.monthly_cap_cents or _historical_avg_for_category(cat.id, month)
        elif cat.tier == CategoryTier.SAVINGS:
            planned = cat.monthly_cap_cents or 0
        elif cat.tier == CategoryTier.DEBT_PAYOFF:
            planned = cat.monthly_cap_cents or 0
        elif cat.tier == CategoryTier.INCOME:
            for sch in schedules:
                if sch.category_id == cat.id:
                    amt = _scheduled_amount_for_month(sch, month)
                    if amt:
                        planned += amt

        if planned > 0 or cat.tier in (CategoryTier.PRIMARY, CategoryTier.INCOME):
            line = BudgetLineItem(
                category_id=cat.id,
                planned_cents=planned,
            )
            lines.append(line)
            if cat.tier == CategoryTier.INCOME:
                total_income += planned
            elif cat.tier == CategoryTier.PRIMARY:
                primary += planned
            elif cat.tier == CategoryTier.SECONDARY:
                secondary += planned
            elif cat.tier == CategoryTier.TERTIARY:
                tertiary += planned
            elif cat.tier == CategoryTier.SAVINGS:
                savings += planned
            elif cat.tier == CategoryTier.DEBT_PAYOFF:
                debt_payoff += planned

    budget = MonthlyBudget(
        month=month,
        mode=mode,
        total_income_cents=total_income,
        primary_expenses_cents=primary,
        secondary_expenses_cents=secondary,
        tertiary_expenses_cents=tertiary,
        savings_targets_cents=savings,
        extra_debt_payoff_cents=debt_payoff,
        lines=lines,
    )

    if not dry_run:
        insert_monthly_budget(budget)

    return {
        "status": "created",
        "budget": budget.model_dump(mode="json"),
        "dry_run": dry_run,
    }


def recalc_monthly_budget(month: date) -> dict[str, Any]:
    """Recalculate spent amounts from actual transactions."""
    month = month.replace(day=1)
    budget = get_monthly_budget(month)
    if not budget:
        return {"status": "not_found", "month": month.isoformat()}

    start, end = _month_start_end(month)
    txs = list_transactions(start_date=start, end_date=end)

    cat_spent: dict[str, int] = {}
    for tx in txs:
        if tx.direction.value == "expense" and tx.category_id:
            cat_spent[tx.category_id] = cat_spent.get(tx.category_id, 0) + tx.amount_cents

    for line in budget.lines:
        line.spent_cents = cat_spent.get(line.category_id, 0)

    update_monthly_budget(budget)
    return {"status": "recalculated", "budget": budget.model_dump(mode="json")}


def get_budget_variance(month: date) -> dict[str, Any]:
    month = month.replace(day=1)
    budget = get_monthly_budget(month)
    if not budget:
        return {"status": "not_found"}

    categories = {c.id: c for c in list_categories()}
    variance_lines = []
    for line in budget.lines:
        cat = categories.get(line.category_id)
        variance_lines.append({
            "category_id": line.category_id,
            "category_name": cat.name if cat else "Unknown",
            "tier": cat.tier.value if cat else "",
            "planned_cents": line.planned_cents,
            "spent_cents": line.spent_cents,
            "variance_cents": line.planned_cents - line.spent_cents,
            "notes": line.notes,
        })

    return {
        "status": "ok",
        "month": budget.month.isoformat(),
        "mode": budget.mode.value,
        "total_income_cents": budget.total_income_cents,
        "primary_expenses_cents": budget.primary_expenses_cents,
        "secondary_expenses_cents": budget.secondary_expenses_cents,
        "tertiary_expenses_cents": budget.tertiary_expenses_cents,
        "savings_targets_cents": budget.savings_targets_cents,
        "extra_debt_payoff_cents": budget.extra_debt_payoff_cents,
        "remaining_cents": budget.remaining_cents(),
        "lines": variance_lines,
    }
