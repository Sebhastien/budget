"""Dashboard, variance, and household intelligence reports."""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from budget.db import (
    get_monthly_budget,
    list_accounts,
    list_categories,
    list_calendar_events,
    list_debt_accounts,
    list_paycheck_plans_for_month,
    list_savings_goals,
    list_schedules,
    list_transactions,
)
from budget.models import (
    AccountType,
    CategoryTier,
    DebtAccount,
    Direction,
    TransactionStatus,
)


def _month_range(month: date) -> tuple[date, date]:
    start = month.replace(day=1)
    _, last_day = calendar.monthrange(start.year, start.month)
    end = start.replace(day=last_day)
    return start, end


def _safe_pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def dashboard(month: date | None = None) -> dict[str, Any]:
    if month is None:
        month = date.today().replace(day=1)
    start, end = _month_range(month)
    budget = get_monthly_budget(month)

    # Cash on hand (checking + savings)
    accounts = list_accounts(active_only=True)
    cash = sum(a.current_balance_cents for a in accounts
               if a.account_type in (AccountType.CHECKING, AccountType.SAVINGS, AccountType.CASH))
    credit_balances = sum(a.current_balance_cents for a in accounts if a.account_type == AccountType.CREDIT_CARD)
    invest = sum(a.current_balance_cents for a in accounts if a.account_type == AccountType.INVESTMENT)
    net_worth = sum(a.current_balance_cents for a in accounts if a.include_in_net_worth)

    # Upcoming bills for the next 14 days within the queried month.
    current_month = date.today().replace(day=1)
    upcoming_start = date.today() if month == current_month else start
    upcoming_end = min(upcoming_start + timedelta(days=13), end)
    upcoming_total = 0
    upcoming_items = []
    for sch in list_schedules(active_only=True):
        for nxt in sch.next_expected_dates(upcoming_start - timedelta(days=1), count=6):
            if upcoming_start <= nxt <= upcoming_end:
                amt = sch.amount_cents or sch.amount_min_cents or 0
                upcoming_total += amt
                upcoming_items.append({
                    "name": sch.name,
                    "due_date": nxt.isoformat(),
                    "amount_cents": amt,
                    "autopay": sch.autopay,
                })

    # Debt
    debts = list_debt_accounts(active_only=True)
    total_debt = sum(d.current_balance_cents for d in debts)
    min_payments = sum(d.min_payment_cents for d in debts)

    # Savings goals
    goals = list_savings_goals(active_only=True)
    total_saved = sum(g.saved_cents for g in goals)
    total_target = sum(g.target_cents for g in goals)

    # Calendar events this month
    events = list_calendar_events(start_date=start, end_date=end)
    event_cost = sum(e.expected_cost_cents or 0 for e in events)

    # Actuals vs plan
    txs = list_transactions(start_date=start, end_date=end)
    actual_income = sum(t.amount_cents for t in txs if t.direction == Direction.INCOME and t.status == TransactionStatus.POSTED)
    actual_expense = sum(t.amount_cents for t in txs if t.direction == Direction.EXPENSE and t.status == TransactionStatus.POSTED)

    # Safe-to-spend estimate
    safe_to_spend = 0
    if budget:
        safe_to_spend = budget.remaining_cents() - upcoming_total - event_cost

    # Danger zones: categories over budget
    danger_zones = []
    if budget:
        cats = {c.id: c for c in list_categories()}
        for line in budget.lines:
            if line.planned_cents > 0 and line.spent_cents > line.planned_cents:
                cat = cats.get(line.category_id)
                danger_zones.append({
                    "category": cat.name if cat else "Unknown",
                    "tier": cat.tier.value if cat else "",
                    "planned_cents": line.planned_cents,
                    "spent_cents": line.spent_cents,
                    "over_by_cents": line.spent_cents - line.planned_cents,
                })

    # Paychecks this month
    paychecks = list_paycheck_plans_for_month(month)
    unallocated_total = sum(p.unallocated_cents for p in paychecks)

    return {
        "month": month.isoformat(),
        "cash_on_hand_cents": cash,
        "credit_card_balances_cents": credit_balances,
        "investments_cents": invest,
        "net_worth_cents": net_worth,
        "upcoming_bills_cents": upcoming_total,
        "upcoming_bills": upcoming_items,
        "total_debt_cents": total_debt,
        "min_payments_cents": min_payments,
        "savings_target_cents": total_target,
        "savings_current_cents": total_saved,
        "calendar_event_cost_cents": event_cost,
        "actual_income_cents": actual_income,
        "actual_expense_cents": actual_expense,
        "safe_to_spend_cents": safe_to_spend,
        "danger_zones": danger_zones,
        "paycheck_unallocated_cents": unallocated_total,
        "recommended_next_action": _recommend_action(danger_zones, safe_to_spend, upcoming_total, total_debt),
    }


def _recommend_action(danger_zones: list[dict], safe_to_spend: int, upcoming: int, total_debt: int) -> str:
    if danger_zones:
        return f"Cut non-sacred spending. {len(danger_zones)} category(s) over budget."
    if safe_to_spend < 0:
        return "Deficit detected. Review discretionary and delay non-essential purchases."
    if upcoming > safe_to_spend:
        return "Upcoming bills exceed safe-to-spend. Hold discretionary until bills clear."
    if total_debt > 0 and safe_to_spend > 50000:  # $500
        return "Bills covered with buffer. Consider extra debt payment or savings transfer."
    return "On track. Monitor weekly."


def month_over_month(current_month: date) -> dict[str, Any]:
    current_month = current_month.replace(day=1)
    import datetime
    prev_month = current_month - datetime.timedelta(days=1)
    prev_month = prev_month.replace(day=1)

    cur_budget = get_monthly_budget(current_month)
    prev_budget = get_monthly_budget(prev_month)

    cur_start, cur_end = _month_range(current_month)
    prev_start, prev_end = _month_range(prev_month)

    cur_txs = list_transactions(start_date=cur_start, end_date=cur_end)
    prev_txs = list_transactions(start_date=prev_start, end_date=prev_end)

    def _aggregate(txs: list[Any]) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for t in txs:
            if t.direction == Direction.EXPENSE and t.category_id:
                out[t.category_id] += t.amount_cents
        return dict(out)

    cur_cat = _aggregate(cur_txs)
    prev_cat = _aggregate(prev_txs)

    cats = {c.id: c for c in list_categories()}
    changes = []
    all_cats = set(cur_cat.keys()) | set(prev_cat.keys())
    for cid in all_cats:
        cur = cur_cat.get(cid, 0)
        prev = prev_cat.get(cid, 0)
        if prev == 0 and cur == 0:
            continue
        delta = cur - prev
        pct = round((delta / prev) * 100, 1) if prev else (100.0 if cur else 0.0)
        cat = cats.get(cid)
        changes.append({
            "category_id": cid,
            "category_name": cat.name if cat else "Unknown",
            "previous_cents": prev,
            "current_cents": cur,
            "delta_cents": delta,
            "delta_percent": pct,
            "tier": cat.tier.value if cat else "",
        })

    changes.sort(key=lambda x: abs(x["delta_cents"]), reverse=True)

    # Biggest leaks
    leaks = [c for c in changes if c["delta_cents"] > 0]
    wins = [c for c in changes if c["delta_cents"] < 0]

    return {
        "current_month": current_month.isoformat(),
        "previous_month": prev_month.isoformat(),
        "total_change_cents": sum(c["delta_cents"] for c in changes),
        "biggest_leaks": leaks[:5],
        "biggest_wins": wins[:5],
        "category_changes": changes,
        "structural_or_temporary": "Review recurring vs one-time by checking transaction counts.",
    }


def debt_payoff_projection(extra_monthly_cents: int = 0) -> dict[str, Any]:
    debts = list_debt_accounts(active_only=True)
    total = sum(d.current_balance_cents for d in debts)
    min_total = sum(d.min_payment_cents for d in debts)

    # Simple projection: months = balance / (min + extra)
    months = 0
    if total > 0 and (min_total + extra_monthly_cents) > 0:
        months = total // (min_total + extra_monthly_cents)

    return {
        "total_debt_cents": total,
        "min_payments_cents": min_total,
        "extra_monthly_cents": extra_monthly_cents,
        "estimated_months_to_payoff": months,
        "debts": [d.model_dump(mode="json") for d in debts],
    }
