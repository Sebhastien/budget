"""Recurring suggestion engine with seed library + pattern detection."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from budget.models import (
    AmountBehavior,
    Cadence,
    CategoryTier,
    Confidence,
    Direction,
    RecurringSuggestion,
    Schedule,
    SuggestionType,
    Transaction,
)


# ---------------------------------------------------------------------------
# Seed library
# ---------------------------------------------------------------------------

class SeedItem:
    def __init__(
        self,
        key: str,
        name: str,
        keywords: list[str],
        direction: Direction,
        cadence: Cadence,
        category_group: str,
        category_tier: CategoryTier,
        amount_behavior: AmountBehavior = AmountBehavior.FIXED,
    ):
        self.key = key
        self.name = name
        self.keywords = [k.lower() for k in keywords]
        self.direction = direction
        self.cadence = cadence
        self.category_group = category_group
        self.category_tier = category_tier
        self.amount_behavior = amount_behavior


SEED_LIBRARY: list[SeedItem] = [
    # Housing
    SeedItem("mortgage", "Mortgage", ["mortgage", "home loan", "lending"], Direction.EXPENSE, Cadence.MONTHLY, "Housing", CategoryTier.PRIMARY),
    SeedItem("rent", "Rent", ["rent", "rent payment", "landlord"], Direction.EXPENSE, Cadence.MONTHLY, "Housing", CategoryTier.PRIMARY),
    SeedItem("hoa", "HOA Dues", ["hoa", "homeowners", "association"], Direction.EXPENSE, Cadence.MONTHLY, "Housing", CategoryTier.PRIMARY),
    # Utilities
    SeedItem("electric", "Electricity", ["electric", "power", "energy", "edison", "duke energy", "pg&e", "pge", "peco"], Direction.EXPENSE, Cadence.MONTHLY, "Utilities", CategoryTier.PRIMARY),
    SeedItem("water", "Water", ["water", "sewer", "utilities water"], Direction.EXPENSE, Cadence.MONTHLY, "Utilities", CategoryTier.PRIMARY),
    SeedItem("gas", "Natural Gas", ["gas utility", "nicor", "atmos", "people gas", "dominion gas"], Direction.EXPENSE, Cadence.MONTHLY, "Utilities", CategoryTier.PRIMARY, AmountBehavior.VARIABLE),
    SeedItem("trash", "Trash", ["trash", "waste", "garbage", "disposal"], Direction.EXPENSE, Cadence.MONTHLY, "Utilities", CategoryTier.PRIMARY),
    SeedItem("internet", "Internet", ["internet", "broadband", "fiber", "comcast", "xfinity", "spectrum", "att", "at&t", "verizon fios", "cox"], Direction.EXPENSE, Cadence.MONTHLY, "Utilities", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("cell", "Cell Phone", ["cell", "mobile", "wireless", "verizon wireless", "tmobile", "t-mobile", "att wireless", "mint mobile", "cricket"], Direction.EXPENSE, Cadence.MONTHLY, "Utilities", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    # Debt
    SeedItem("car_loan", "Car Loan", ["car loan", "auto loan", "vehicle loan", "capital one auto", "ally auto"], Direction.EXPENSE, Cadence.MONTHLY, "Debt", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("student_loan", "Student Loan", ["student loan", "navient", "nelnet", "mohela", "fedloan", "great lakes"], Direction.EXPENSE, Cadence.MONTHLY, "Debt", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("credit_card_min", "Credit Card Minimum", ["credit card payment", "card payment", "autopay cc", "min payment"], Direction.EXPENSE, Cadence.MONTHLY, "Debt", CategoryTier.PRIMARY, AmountBehavior.VARIABLE),
    # Insurance
    SeedItem("auto_insurance", "Auto Insurance", ["auto insurance", "car insurance", "geico", "progressive", "state farm", "allstate", "usaa", "liability"], Direction.EXPENSE, Cadence.MONTHLY, "Insurance", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("health_insurance", "Health Insurance", ["health insurance", "medical insurance", "blue cross", "aetna", "cigna", "unitedhealth", "humana", "kaiser"], Direction.EXPENSE, Cadence.MONTHLY, "Insurance", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("home_insurance", "Home Insurance", ["home insurance", "property insurance", "homeowners ins"], Direction.EXPENSE, Cadence.MONTHLY, "Insurance", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("renters_insurance", "Renters Insurance", ["renters insurance", "rental insurance"], Direction.EXPENSE, Cadence.MONTHLY, "Insurance", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    SeedItem("life_insurance", "Life Insurance", ["life insurance", "term life", "whole life"], Direction.EXPENSE, Cadence.MONTHLY, "Insurance", CategoryTier.PRIMARY, AmountBehavior.FIXED),
    # Subscriptions
    SeedItem("streaming", "Streaming", ["netflix", "hulu", "disney+", "disney plus", "hbo max", "max", "peacock", "paramount+", "apple tv", "youtube premium", "spotify", "amazon prime"], Direction.EXPENSE, Cadence.MONTHLY, "Discretionary", CategoryTier.TERTIARY, AmountBehavior.FIXED),
    SeedItem("gym", "Gym Membership", ["gym", "fitness", "yoga studio", "crossfit", "planet fitness", "la fitness", "equinox"], Direction.EXPENSE, Cadence.MONTHLY, "Discretionary", CategoryTier.TERTIARY, AmountBehavior.FIXED),
    SeedItem("saas", "App / SaaS Subscription", ["notion", "dropbox", "icloud", "google one", "adobe", "microsoft 365", "office 365", "todoist", "1password", "lastpass"], Direction.EXPENSE, Cadence.MONTHLY, "Discretionary", CategoryTier.TERTIARY, AmountBehavior.FIXED),
    # Income
    SeedItem("payroll", "Payroll", ["payroll", "direct deposit", "salary", "wage", "paycheck"], Direction.INCOME, Cadence.SEMI_MONTHLY, "Income", CategoryTier.INCOME, AmountBehavior.FIXED),
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _normalize_payee(p: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", p.lower())
    return " ".join(s.split())


def _match_score(tx: Transaction, seed: SeedItem) -> float:
    norm = _normalize_payee(tx.payee)
    scores = []
    for kw in seed.keywords:
        if kw in norm:
            # Longer keyword matches = higher confidence
            scores.append(len(kw) / max(len(norm), 1))
    if not scores:
        return 0.0
    if tx.direction != seed.direction:
        return 0.0
    return max(scores)


def _infer_cadence(dates: list[date]) -> tuple[Cadence | None, list[str]]:
    reasons: list[str] = []
    if len(dates) < 2:
        return None, ["insufficient_date_history"]
    deltas = sorted({(dates[i] - dates[i - 1]).days for i in range(1, len(dates))})
    if not deltas:
        return None, ["insufficient_date_history"]
    avg_delta = statistics.mean(deltas)
    min_delta = min(deltas)
    max_delta = max(deltas)
    reasons.append(f"avg_interval_days:{avg_delta:.0f}")

    # Monthly-ish
    if 27 <= avg_delta <= 32 and max_delta - min_delta <= 5:
        return Cadence.MONTHLY, reasons + ["monthly_interval_pattern"]
    # Biweekly-ish
    if 12 <= avg_delta <= 16 and max_delta - min_delta <= 4:
        return Cadence.BIWEEKLY, reasons + ["biweekly_interval_pattern"]
    # Weekly-ish
    if 6 <= avg_delta <= 8 and max_delta - min_delta <= 2:
        return Cadence.WEEKLY, reasons + ["weekly_interval_pattern"]
    # Semi-monthly (two per month, ~15 days apart)
    if len(dates) >= 4:
        month_counts: dict[tuple[int, int], int] = defaultdict(int)
        for d in dates:
            month_counts[(d.year, d.month)] += 1
        if all(c == 2 for c in month_counts.values()):
            intra_month_deltas = []
            for y, m in month_counts:
                month_dates = sorted([d for d in dates if d.year == y and d.month == m])
                if len(month_dates) == 2:
                    intra_month_deltas.append((month_dates[1] - month_dates[0]).days)
            if intra_month_deltas and all(12 <= d <= 18 for d in intra_month_deltas):
                return Cadence.SEMI_MONTHLY, reasons + ["semi_monthly_pattern"]
    # Quarterly-ish
    if 85 <= avg_delta <= 100:
        return Cadence.QUARTERLY, reasons + ["quarterly_interval_pattern"]
    # Annual-ish
    if 355 <= avg_delta <= 375:
        return Cadence.ANNUAL, reasons + ["annual_interval_pattern"]

    return None, reasons + ["irregular_interval_pattern"]


def _amount_behavior(amounts: list[int]) -> tuple[AmountBehavior, int | None, int | None, int | None, list[str]]:
    reasons: list[str] = []
    if len(amounts) == 1:
        return AmountBehavior.FIXED, amounts[0], None, None, reasons + ["single_amount"]
    mean_amt = statistics.mean(amounts)
    stdev_amt = statistics.stdev(amounts) if len(amounts) > 1 else 0
    cv = stdev_amt / mean_amt if mean_amt else 0
    min_a, max_a = min(amounts), max(amounts)
    reasons.append(f"amount_cv:{cv:.2f}")
    if cv < 0.02:
        return AmountBehavior.FIXED, int(mean_amt), None, None, reasons + ["amount_stable"]
    elif cv < 0.15:
        return AmountBehavior.RANGE, None, min_a, max_a, reasons + ["amount_variable_low_cv"]
    else:
        return AmountBehavior.VARIABLE, None, min_a, max_a, reasons + ["amount_variable_high_cv"]


def _next_expected_date(dates: list[date], cadence: Cadence | None) -> date | None:
    if not dates:
        return None
    last = max(dates)
    if cadence == Cadence.MONTHLY:
        # Next month same day
        y, m = last.year, last.month
        if m == 12:
            return date(y + 1, 1, min(last.day, 31))
        else:
            import calendar
            _, max_day = calendar.monthrange(y, m + 1)
            return date(y, m + 1, min(last.day, max_day))
    if cadence == Cadence.BIWEEKLY:
        return last + timedelta(days=14)
    if cadence == Cadence.WEEKLY:
        return last + timedelta(days=7)
    if cadence == Cadence.SEMI_MONTHLY:
        # Guess based on last date
        if last.day <= 15:
            import calendar
            _, max_day = calendar.monthrange(last.year, last.month)
            return date(last.year, last.month, max_day)
        else:
            if last.month == 12:
                return date(last.year + 1, 1, 15)
            return date(last.year, last.month + 1, 15)
    if cadence == Cadence.QUARTERLY:
        m = last.month + 3
        y = last.year
        if m > 12:
            m -= 12
            y += 1
        import calendar
        _, max_day = calendar.monthrange(y, m)
        return date(y, m, min(last.day, max_day))
    if cadence == Cadence.ANNUAL:
        import calendar
        _, max_day = calendar.monthrange(last.year + 1, last.month)
        return date(last.year + 1, last.month, min(last.day, max_day))
    return last + timedelta(days=30)


def _confidence_from_evidence(
    match_score: float,
    tx_count: int,
    cadence: Cadence | None,
    behavior: AmountBehavior,
) -> Confidence:
    if match_score >= 0.5 and tx_count >= 4 and cadence is not None and behavior in (AmountBehavior.FIXED, AmountBehavior.RANGE):
        return Confidence.HIGH
    if match_score >= 0.3 and tx_count >= 3 and cadence is not None:
        return Confidence.MEDIUM
    if tx_count >= 3 and cadence is not None:
        return Confidence.MEDIUM
    if tx_count >= 2:
        return Confidence.LOW
    return Confidence.NONE


def _suggestion_type(cadence: Cadence | None, behavior: AmountBehavior) -> SuggestionType:
    if cadence is None:
        return SuggestionType.NOT_RECURRING
    if behavior == AmountBehavior.FIXED:
        return SuggestionType.FIXED_RECURRING
    if behavior == AmountBehavior.RANGE:
        return SuggestionType.VARIABLE_RECURRING
    return SuggestionType.PROBABLE_RECURRING


# ---------------------------------------------------------------------------
# Main helper
# ---------------------------------------------------------------------------

def suggest_recurring_streams(
    transactions: list[Transaction],
    seed_library: list[SeedItem] | None = None,
    config: dict[str, Any] | None = None,
) -> list[RecurringSuggestion]:
    """Suggest recurring income and expense streams from transactions."""
    if seed_library is None:
        seed_library = SEED_LIBRARY
    cfg = config or {}
    min_confidence = cfg.get("min_confidence", "low")
    min_transactions = cfg.get("min_transactions", 2)
    confidence_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    min_confidence_rank = confidence_order.get(str(min_confidence), 1)

    # Step 1: Seed matching
    matched_tx_ids: set[str] = set()
    suggestions: list[RecurringSuggestion] = []

    for seed in seed_library:
        scored_txs = [(tx, _match_score(tx, seed)) for tx in transactions]
        matched = [(tx, s) for tx, s in scored_txs if s > 0]
        if not matched:
            continue
        matched = sorted(matched, key=lambda x: x[1], reverse=True)
        # Take top matches that share similar amounts (within 25% of median)
        amounts = [tx.amount_cents for tx, _ in matched]
        if amounts:
            med = statistics.median(amounts)
            # filter to amounts within 25% of median; zero-dollar authorizations cannot use ratios
            if med != 0:
                filtered = [(tx, s) for tx, s in matched if 0.75 <= tx.amount_cents / med <= 1.25]
                if len(filtered) >= min_transactions:
                    matched = filtered
        tx_ids = [tx.id for tx, _ in matched]
        matched_tx_ids.update(tx_ids)
        dates = sorted([tx.date for tx, _ in matched])
        amounts = [tx.amount_cents for tx, _ in matched]
        cadence, cadence_reasons = _infer_cadence(dates)
        behavior, amt, min_a, max_a, amt_reasons = _amount_behavior(amounts)
        confidence = _confidence_from_evidence(
            max(s for _, s in matched), len(matched), cadence, behavior
        )
        if confidence_order[confidence.value] < min_confidence_rank:
            continue
        suggestion = RecurringSuggestion(
            type=_suggestion_type(cadence, behavior),
            confidence=confidence,
            direction=seed.direction,
            payee=seed.name,
            category_group=seed.category_group,
            category_tier=seed.category_tier,
            cadence=cadence or Cadence.MONTHLY,
            amount_cents=amt,
            amount_min_cents=min_a,
            amount_max_cents=max_a,
            amount_behavior=behavior,
            next_expected_date=_next_expected_date(dates, cadence),
            matched_transaction_ids=tx_ids,
            reason_codes=["seed_library_match", seed.key] + cadence_reasons + amt_reasons,
            seed_library_key=seed.key,
        )
        suggestions.append(suggestion)

    # Step 2: Pattern detection on unmatched transactions
    unmatched = [tx for tx in transactions if tx.id not in matched_tx_ids]
    clusters: dict[tuple[str, Direction], list[Transaction]] = defaultdict(list)
    for tx in unmatched:
        norm = _normalize_payee(tx.payee)
        clusters[(norm, tx.direction)].append(tx)

    for (norm_key, direction), txs in clusters.items():
        if len(txs) < min_transactions:
            continue
        txs = sorted(txs, key=lambda t: t.date)
        dates = [t.date for t in txs]
        amounts = [t.amount_cents for t in txs]
        cadence, cadence_reasons = _infer_cadence(dates)
        if cadence is None and len(txs) < 4:
            continue
        behavior, amt, min_a, max_a, amt_reasons = _amount_behavior(amounts)
        confidence = _confidence_from_evidence(0.0, len(txs), cadence, behavior)
        if confidence_order[confidence.value] < min_confidence_rank:
            continue
        # Derive a display name
        payee_name = txs[0].payee if txs else norm_key.title()
        # Try to infer category tier from payee heuristics
        tier = None
        group = ""
        if direction == Direction.INCOME:
            tier = CategoryTier.INCOME
            group = "Income"
        suggestion = RecurringSuggestion(
            type=_suggestion_type(cadence, behavior),
            confidence=confidence,
            direction=direction,
            payee=payee_name,
            category_group=group,
            category_tier=tier,
            cadence=cadence or Cadence.MONTHLY,
            amount_cents=amt,
            amount_min_cents=min_a,
            amount_max_cents=max_a,
            amount_behavior=behavior,
            next_expected_date=_next_expected_date(dates, cadence),
            matched_transaction_ids=[t.id for t in txs],
            reason_codes=["pattern_detection"] + cadence_reasons + amt_reasons,
        )
        suggestions.append(suggestion)

    # Sort: high confidence first, then by payee
    sort_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    suggestions.sort(key=lambda s: (sort_order.get(s.confidence.value, 99), s.payee))
    return suggestions
