"""Canonical data model for the budgeting CLI."""

from __future__ import annotations

import calendar
import enum
import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

def to_cents(d: Decimal | float | int | str) -> int:
    """Convert a monetary value to integer cents."""
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        d = Decimal(d)
    if isinstance(d, float):
        d = Decimal(str(d))
    return int((d * 100).to_integral_value())


def from_cents(cents: int) -> Decimal:
    """Convert integer cents back to Decimal."""
    return Decimal(cents) / 100


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp for persisted entities."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Direction(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class Cadence(str, enum.Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMI_MONTHLY = "semi_monthly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class CategoryTier(str, enum.Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    SAVINGS = "savings"
    DEBT_PAYOFF = "debt_payoff"
    INCOME = "income"
    TRANSFER = "transfer"


class AmountBehavior(str, enum.Enum):
    FIXED = "fixed"
    VARIABLE = "variable"
    RANGE = "range"


class TransactionStatus(str, enum.Enum):
    POSTED = "posted"
    PENDING = "pending"
    SCHEDULED = "scheduled"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class SuggestionType(str, enum.Enum):
    FIXED_RECURRING = "fixed_recurring"
    VARIABLE_RECURRING = "variable_recurring"
    PROBABLE_RECURRING = "probable_recurring"
    NOT_RECURRING = "not_recurring"


class BudgetMode(str, enum.Enum):
    NORMAL = "normal"
    TIGHTEN = "tighten"
    EMERGENCY = "emergency"
    DEBT_ATTACK = "debt_attack"
    LAND_SAVING = "land_saving"
    NEW_BABY = "new_baby"
    TRAVEL = "travel"
    HOLIDAY = "holiday"


class AccountType(str, enum.Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"
    LOAN = "loan"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Entity(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Household Member
# ---------------------------------------------------------------------------

class HouseholdMember(Entity):
    name: str
    role: str = ""           # e.g., "primary", "partner", "shared", "kid"
    email: str | None = None
    phone: str | None = None
    active: bool = True


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

class Account(Entity):
    name: str
    account_type: AccountType
    institution: str = ""
    current_balance_cents: int = 0
    credit_limit_cents: int | None = None
    interest_rate_percent: Decimal | None = None
    # For debt accounts tracked separately, link them
    debt_account_id: str | None = None
    notes: str = ""
    active: bool = True
    include_in_net_worth: bool = True

    @field_validator("current_balance_cents", "credit_limit_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return to_cents(v)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

class Transaction(Entity):
    account_id: str = "default"
    date: date
    payee: str
    description: str = ""
    amount_cents: int
    direction: Direction
    category_id: str | None = None
    member_id: str | None = None
    status: TransactionStatus = TransactionStatus.POSTED
    # Provenance / import tracking
    import_batch_id: str | None = None
    original_csv_row: dict[str, str] | None = None
    dedupe_hash: str | None = None
    # Household intelligence
    is_split: bool = False
    parent_id: str | None = None
    reimbursable: bool = False
    essential_override: bool | None = None   # None = infer from category
    # Merchant normalization
    normalized_payee: str = ""

    @field_validator("amount_cents", mode="before")
    @classmethod
    def _coerce_amount(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)

    def compute_dedupe_hash(self) -> str:
        payload = json.dumps({
            "account_id": self.account_id,
            "date": self.date.isoformat(),
            "payee": self.payee.strip().lower(),
            "amount_cents": self.amount_cents,
            "direction": self.direction.value,
            "description": self.description.strip().lower(),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

class Category(Entity):
    name: str
    tier: CategoryTier
    group: str = ""           # e.g., "Housing", "Utilities"
    monthly_cap_cents: int | None = None
    notes: str = ""
    # Household philosophy
    essential: bool = True
    sacred: bool = False       # Never cut first
    cut_priority: int = 0      # Higher = cut first
    member_id: str | None = None

    @field_validator("monthly_cap_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return to_cents(v)


# ---------------------------------------------------------------------------
# Calendar Event
# ---------------------------------------------------------------------------

class CalendarEvent(Entity):
    name: str
    event_date: date
    end_date: date | None = None
    category_id: str | None = None
    expected_cost_cents: int | None = None
    actual_cost_cents: int | None = None
    impact: Literal["income", "expense", "both", "none"] = "expense"
    notes: str = ""
    recurring_event_id: str | None = None

    @field_validator("expected_cost_cents", "actual_cost_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return to_cents(v)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

class Schedule(Entity):
    name: str
    payee: str = ""
    direction: Direction
    category_id: str | None = None
    cadence: Cadence
    cadence_days: list[int] = Field(default_factory=list)
    amount_cents: int | None = None
    amount_min_cents: int | None = None
    amount_max_cents: int | None = None
    amount_behavior: AmountBehavior = AmountBehavior.FIXED
    active: bool = True
    start_date: date | None = None
    end_date: date | None = None
    autopay: bool = False
    payment_method: str = ""
    source: Literal["seed_library", "pattern_detection", "manual"] = "manual"
    seed_library_key: str | None = None
    reason: str = ""

    @field_validator("amount_cents", "amount_min_cents", "amount_max_cents", mode="before")
    @classmethod
    def _coerce_amounts(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return to_cents(v)

    def next_expected_dates(self, after: date, count: int = 3) -> list[date]:
        dates: list[date] = []
        current = after
        while len(dates) < count:
            nxt = self._next_date(current)
            if nxt is None:
                break
            dates.append(nxt)
            current = nxt
        return dates

    def _next_date(self, after: date) -> date | None:
        year, month = after.year, after.month
        candidates: list[date] = []

        if self.cadence == Cadence.MONTHLY:
            day = self.start_date.day if self.start_date else after.day
            for y, m in [(year, month), (year + (month // 12), (month % 12) + 1)]:
                _, max_day = calendar.monthrange(y, m)
                candidates.append(date(y, m, min(day, max_day)))
        elif self.cadence == Cadence.SEMI_MONTHLY:
            days = self.cadence_days or [15, 0]
            for y, m in [(year, month), (year + (month // 12), (month % 12) + 1)]:
                for d in days:
                    if d == 0:
                        _, max_day = calendar.monthrange(y, m)
                        candidates.append(date(y, m, max_day))
                    else:
                        candidates.append(date(y, m, d))
        elif self.cadence == Cadence.BIWEEKLY:
            start = self.start_date or after
            delta = (after - start).days
            steps = (delta // 14) + 1
            candidates.append(start + timedelta(days=steps * 14))
        elif self.cadence == Cadence.WEEKLY:
            candidates.append(after + timedelta(days=7))
        elif self.cadence == Cadence.QUARTERLY:
            q_month = month + 3
            q_year = year
            if q_month > 12:
                q_month -= 12
                q_year += 1
            _, max_day = calendar.monthrange(q_year, q_month)
            candidates.append(date(q_year, q_month, min(after.day, max_day)))
        elif self.cadence == Cadence.ANNUAL:
            _, max_day = calendar.monthrange(year + 1, month)
            candidates.append(date(year + 1, month, min(after.day, max_day)))

        valid = [c for c in candidates if c > after]
        if not valid:
            return None
        return min(valid)


# ---------------------------------------------------------------------------
# Monthly Budget
# ---------------------------------------------------------------------------

class BudgetLineItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    category_id: str
    planned_cents: int
    spent_cents: int = 0
    notes: str = ""
    member_id: str | None = None

    @field_validator("planned_cents", "spent_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)


class MonthlyBudget(Entity):
    month: date
    mode: BudgetMode = BudgetMode.NORMAL
    total_income_cents: int = 0
    primary_expenses_cents: int = 0
    secondary_expenses_cents: int = 0
    tertiary_expenses_cents: int = 0
    savings_targets_cents: int = 0
    extra_debt_payoff_cents: int = 0
    lines: list[BudgetLineItem] = Field(default_factory=list)
    notes: str = ""
    finalized: bool = False

    @field_validator("month", mode="before")
    @classmethod
    def _normalize_month(cls, v: Any) -> date:
        if isinstance(v, str):
            v = date.fromisoformat(v)
        return v.replace(day=1)

    @field_validator("total_income_cents", "primary_expenses_cents",
                     "secondary_expenses_cents", "tertiary_expenses_cents",
                     "savings_targets_cents", "extra_debt_payoff_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)

    def remaining_cents(self) -> int:
        planned_out = self.primary_expenses_cents + self.secondary_expenses_cents + self.tertiary_expenses_cents + self.savings_targets_cents + self.extra_debt_payoff_cents
        return self.total_income_cents - planned_out


# ---------------------------------------------------------------------------
# Paycheck Plan
# ---------------------------------------------------------------------------

class PaycheckCommittedItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    category_id: str | None = None
    schedule_id: str | None = None
    due_date: date | None = None
    planned_amount_cents: int
    actual_amount_cents: int | None = None
    status: Literal["pending", "paid", "skipped"] = "pending"
    notes: str = ""

    @field_validator("planned_amount_cents", "actual_amount_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return to_cents(v)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: Any) -> str:
        return str(v) if v is not None else "pending"

    @field_validator("due_date", mode="before")
    @classmethod
    def _coerce_date(cls, v: Any) -> date | None:
        if v is None:
            return None
        if isinstance(v, date):
            return v
        return date.fromisoformat(str(v))


class PaycheckReserve(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    category_id: str
    reserve_cents: int
    spent_cents: int = 0
    notes: str = ""

    @field_validator("reserve_cents", "spent_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)


class PaycheckManualAllocation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    category_id: str | None = None
    amount_cents: int
    direction: Direction = Direction.EXPENSE
    notes: str = ""

    @field_validator("amount_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)

    @field_validator("direction", mode="before")
    @classmethod
    def _coerce_direction(cls, v: Any) -> Direction:
        if isinstance(v, Direction):
            return v
        return Direction(str(v))


class PaycheckPlan(Entity):
    month: date
    paycheck_date: date
    next_paycheck_date: date | None = None
    committed: list[PaycheckCommittedItem] = Field(default_factory=list)
    reserves: list[PaycheckReserve] = Field(default_factory=list)
    manual_allocations: list[PaycheckManualAllocation] = Field(default_factory=list)
    unallocated_cents: int = 0
    notes: str = ""
    finalized: bool = False

    @field_validator("month", "paycheck_date", "next_paycheck_date", mode="before")
    @classmethod
    def _normalize_dates(cls, v: Any) -> date | None:
        if v is None:
            return None
        if isinstance(v, str):
            v = date.fromisoformat(v)
        return v

    @field_validator("unallocated_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)


# ---------------------------------------------------------------------------
# Goals & Debt Accounts
# ---------------------------------------------------------------------------

class SavingsGoal(Entity):
    name: str
    target_cents: int
    saved_cents: int = 0
    deadline: date | None = None
    category_id: str | None = None
    priority: int = 0
    active: bool = True
    notes: str = ""

    @field_validator("target_cents", "saved_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)


class DebtAccount(Entity):
    name: str
    original_balance_cents: int
    current_balance_cents: int
    min_payment_cents: int
    interest_rate_percent: Decimal | None = None
    payoff_strategy: Literal["avalanche", "snowball", "custom"] = "avalanche"
    payoff_priority: int = 0
    category_id: str | None = None
    active: bool = True
    notes: str = ""

    @field_validator("original_balance_cents", "current_balance_cents", "min_payment_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        return to_cents(v)


# ---------------------------------------------------------------------------
# Import Batch
# ---------------------------------------------------------------------------

class ImportBatch(Entity):
    file_name: str
    file_hash: str
    row_count: int
    imported_count: int = 0
    duplicate_count: int = 0
    mapping: dict[str, str] | None = None
    status: Literal["pending", "complete", "failed"] = "pending"


# ---------------------------------------------------------------------------
# Audit Event
# ---------------------------------------------------------------------------

class AuditEvent(Entity):
    action: str
    entity_type: str
    entity_id: str
    rollback_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    user_hint: str = ""


# ---------------------------------------------------------------------------
# Recurring Suggestion
# ---------------------------------------------------------------------------

class RecurringSuggestion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: SuggestionType
    confidence: Confidence
    direction: Direction
    payee: str
    category_group: str = ""
    category_tier: CategoryTier | None = None
    cadence: Cadence | None = None
    amount_cents: int | None = None
    amount_min_cents: int | None = None
    amount_max_cents: int | None = None
    amount_behavior: AmountBehavior = AmountBehavior.FIXED
    next_expected_date: date | None = None
    matched_transaction_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    seed_library_key: str | None = None
    status: Literal["pending", "confirmed", "edited", "rejected"] = "pending"
    edited_schedule: Schedule | None = None

    @field_validator("amount_cents", "amount_min_cents", "amount_max_cents", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return to_cents(v)
