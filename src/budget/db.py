"""SQLite persistence layer with simple repository pattern."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Generator, TypeVar

from budget.models import (
    Account,
    AccountType,
    AuditEvent,
    CalendarEvent,
    Category,
    CategoryTier,
    DebtAccount,
    HouseholdMember,
    ImportBatch,
    MonthlyBudget,
    PaycheckPlan,
    SavingsGoal,
    Schedule,
    Transaction,
    TransactionStatus,
)

T = TypeVar("T")

DB_PATH: Path | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


def _parse_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _json_load(s: str | None) -> Any:
    return json.loads(s) if s else None


def _json_dump(v: Any) -> str | None:
    return json.dumps(v, default=str) if v is not None else None


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def set_db_path(path: str | Path) -> None:
    global DB_PATH
    DB_PATH = Path(path)


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    if DB_PATH is None:
        raise RuntimeError("DB path not set. Run `budget init` first.")
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS household_members (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    role TEXT,
    email TEXT,
    phone TEXT,
    active INTEGER
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    institution TEXT,
    current_balance_cents INTEGER,
    credit_limit_cents INTEGER,
    interest_rate_percent TEXT,
    debt_account_id TEXT,
    notes TEXT,
    active INTEGER,
    include_in_net_worth INTEGER
);

CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    tier TEXT NOT NULL,
    "group" TEXT,
    monthly_cap_cents INTEGER,
    notes TEXT,
    essential INTEGER,
    sacred INTEGER,
    cut_priority INTEGER,
    member_id TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    account_id TEXT,
    date TEXT NOT NULL,
    payee TEXT NOT NULL,
    description TEXT,
    amount_cents INTEGER NOT NULL,
    direction TEXT NOT NULL,
    category_id TEXT,
    member_id TEXT,
    status TEXT,
    import_batch_id TEXT,
    original_csv_row TEXT,
    dedupe_hash TEXT,
    is_split INTEGER,
    parent_id TEXT,
    reimbursable INTEGER,
    essential_override INTEGER,
    normalized_payee TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_dedupe ON transactions(dedupe_hash);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_batch ON transactions(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_tx_payee ON transactions(normalized_payee);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    event_date TEXT NOT NULL,
    end_date TEXT,
    category_id TEXT,
    expected_cost_cents INTEGER,
    actual_cost_cents INTEGER,
    impact TEXT,
    notes TEXT,
    recurring_event_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_cal_date ON calendar_events(event_date);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    payee TEXT,
    direction TEXT NOT NULL,
    category_id TEXT,
    cadence TEXT NOT NULL,
    cadence_days TEXT,
    amount_cents INTEGER,
    amount_min_cents INTEGER,
    amount_max_cents INTEGER,
    amount_behavior TEXT,
    active INTEGER,
    start_date TEXT,
    end_date TEXT,
    autopay INTEGER,
    payment_method TEXT,
    source TEXT,
    seed_library_key TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS monthly_budgets (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    month TEXT NOT NULL UNIQUE,
    mode TEXT,
    total_income_cents INTEGER,
    primary_expenses_cents INTEGER,
    secondary_expenses_cents INTEGER,
    tertiary_expenses_cents INTEGER,
    savings_targets_cents INTEGER,
    extra_debt_payoff_cents INTEGER,
    lines TEXT,
    notes TEXT,
    finalized INTEGER
);

CREATE TABLE IF NOT EXISTS paycheck_plans (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    month TEXT NOT NULL,
    paycheck_date TEXT NOT NULL,
    next_paycheck_date TEXT,
    committed TEXT,
    reserves TEXT,
    manual_allocations TEXT,
    unallocated_cents INTEGER,
    notes TEXT,
    finalized INTEGER
);

CREATE TABLE IF NOT EXISTS savings_goals (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    target_cents INTEGER,
    saved_cents INTEGER,
    deadline TEXT,
    category_id TEXT,
    priority INTEGER,
    active INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS debt_accounts (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    name TEXT NOT NULL,
    original_balance_cents INTEGER,
    current_balance_cents INTEGER,
    min_payment_cents INTEGER,
    interest_rate_percent TEXT,
    payoff_strategy TEXT,
    payoff_priority INTEGER,
    category_id TEXT,
    active INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    file_name TEXT,
    file_hash TEXT,
    row_count INTEGER,
    imported_count INTEGER,
    duplicate_count INTEGER,
    mapping TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    action TEXT,
    entity_type TEXT,
    entity_id TEXT,
    rollback_id TEXT,
    payload TEXT,
    user_hint TEXT
);
"""


def init_db() -> None:
    if DB_PATH is None:
        raise RuntimeError("DB path not set.")
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def insert_audit_event(event: AuditEvent) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO audit_events
               (id, created_at, updated_at, action, entity_type, entity_id,
                rollback_id, payload, user_hint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id, _iso(event.created_at), _iso(event.updated_at),
                event.action, event.entity_type, event.entity_id,
                event.rollback_id, _json_dump(event.payload), event.user_hint,
            ),
        )


def generate_rollback_id() -> str:
    import uuid
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Household Members
# ---------------------------------------------------------------------------

def insert_household_member(m: HouseholdMember) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO household_members
               (id, created_at, updated_at, name, role, email, phone, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (m.id, _iso(m.created_at), _iso(m.updated_at), m.name, m.role,
             m.email, m.phone, int(m.active)),
        )


def list_household_members(active_only: bool = True) -> list[HouseholdMember]:
    query = "SELECT * FROM household_members"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_household_member(r) for r in rows]


def get_household_member_by_id(mid: str) -> HouseholdMember | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM household_members WHERE id = ? LIMIT 1", (mid,)
        ).fetchone()
    if not row:
        return None
    return _row_to_household_member(row)


def _row_to_household_member(row: sqlite3.Row) -> HouseholdMember:
    return HouseholdMember(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        role=row["role"] or "",
        email=row["email"],
        phone=row["phone"],
        active=bool(row["active"]),
    )


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def insert_account(a: Account) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO accounts
               (id, created_at, updated_at, name, account_type, institution,
                current_balance_cents, credit_limit_cents, interest_rate_percent,
                debt_account_id, notes, active, include_in_net_worth)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a.id, _iso(a.created_at), _iso(a.updated_at), a.name,
             a.account_type.value, a.institution, a.current_balance_cents,
             a.credit_limit_cents,
             str(a.interest_rate_percent) if a.interest_rate_percent else None,
             a.debt_account_id, a.notes, int(a.active), int(a.include_in_net_worth)),
        )


def list_accounts(active_only: bool = True) -> list[Account]:
    query = "SELECT * FROM accounts"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_account(r) for r in rows]


def get_account_by_id(aid: str) -> Account | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ? LIMIT 1", (aid,)
        ).fetchone()
    if not row:
        return None
    return _row_to_account(row)


def update_account(a: Account) -> None:
    a.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE accounts SET
               updated_at=?, name=?, account_type=?, institution=?,
               current_balance_cents=?, credit_limit_cents=?,
               interest_rate_percent=?, debt_account_id=?, notes=?,
               active=?, include_in_net_worth=?
               WHERE id=?""",
            (_iso(a.updated_at), a.name, a.account_type.value, a.institution,
             a.current_balance_cents, a.credit_limit_cents,
             str(a.interest_rate_percent) if a.interest_rate_percent else None,
             a.debt_account_id, a.notes, int(a.active),
             int(a.include_in_net_worth), a.id),
        )


def _row_to_account(row: sqlite3.Row) -> Account:
    from decimal import Decimal
    ir = row["interest_rate_percent"]
    return Account(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        account_type=AccountType(row["account_type"]),
        institution=row["institution"] or "",
        current_balance_cents=row["current_balance_cents"] or 0,
        credit_limit_cents=row["credit_limit_cents"],
        interest_rate_percent=Decimal(ir) if ir else None,
        debt_account_id=row["debt_account_id"],
        notes=row["notes"] or "",
        active=bool(row["active"]),
        include_in_net_worth=bool(row["include_in_net_worth"] if row["include_in_net_worth"] is not None else 1),
    )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def insert_transaction(tx: Transaction) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO transactions
               (id, created_at, updated_at, account_id, date, payee, description,
                amount_cents, direction, category_id, member_id, status,
                import_batch_id, original_csv_row, dedupe_hash, is_split,
                parent_id, reimbursable, essential_override, normalized_payee)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tx.id, _iso(tx.created_at), _iso(tx.updated_at),
                tx.account_id, _iso(tx.date), tx.payee, tx.description,
                tx.amount_cents, tx.direction.value, tx.category_id,
                tx.member_id, tx.status.value, tx.import_batch_id,
                _json_dump(tx.original_csv_row), tx.dedupe_hash,
                int(tx.is_split), tx.parent_id,
                int(tx.reimbursable),
                int(tx.essential_override) if tx.essential_override is not None else None,
                tx.normalized_payee,
            ),
        )


def find_transaction_by_dedupe_hash(dedupe_hash: str) -> Transaction | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE dedupe_hash = ? LIMIT 1",
            (dedupe_hash,),
        ).fetchone()
    if not row:
        return None
    return _row_to_transaction(row)


def list_transactions(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    import_batch_id: str | None = None,
    payee_like: str | None = None,
    category_id: str | None = None,
    member_id: str | None = None,
) -> list[Transaction]:
    query = "SELECT * FROM transactions WHERE 1=1"
    params: list[Any] = []
    if start_date:
        query += " AND date >= ?"
        params.append(_iso(start_date))
    if end_date:
        query += " AND date <= ?"
        params.append(_iso(end_date))
    if import_batch_id:
        query += " AND import_batch_id = ?"
        params.append(import_batch_id)
    if payee_like:
        query += " AND (payee LIKE ? OR normalized_payee LIKE ?)"
        params.extend([f"%{payee_like}%", f"%{payee_like}%"])
    if category_id:
        query += " AND category_id = ?"
        params.append(category_id)
    if member_id:
        query += " AND member_id = ?"
        params.append(member_id)
    query += " ORDER BY date DESC, rowid DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_transaction(r) for r in rows]


def update_transaction(tx: Transaction) -> None:
    tx.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE transactions SET
               updated_at=?, account_id=?, date=?, payee=?, description=?,
               amount_cents=?, direction=?, category_id=?, member_id=?, status=?,
               import_batch_id=?, original_csv_row=?, dedupe_hash=?,
               is_split=?, parent_id=?, reimbursable=?, essential_override=?,
               normalized_payee=?
               WHERE id=?""",
            (
                _iso(tx.updated_at), tx.account_id, _iso(tx.date), tx.payee,
                tx.description, tx.amount_cents, tx.direction.value,
                tx.category_id, tx.member_id, tx.status.value, tx.import_batch_id,
                _json_dump(tx.original_csv_row), tx.dedupe_hash,
                int(tx.is_split), tx.parent_id,
                int(tx.reimbursable),
                int(tx.essential_override) if tx.essential_override is not None else None,
                tx.normalized_payee, tx.id,
            ),
        )


def _row_to_transaction(row: sqlite3.Row) -> Transaction:
    from budget.models import Direction
    eo = row["essential_override"]
    return Transaction(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        account_id=row["account_id"] or "default",
        date=_parse_date(row["date"]) or date.today(),
        payee=row["payee"],
        description=row["description"] or "",
        amount_cents=row["amount_cents"],
        direction=Direction(row["direction"]),
        category_id=row["category_id"],
        member_id=row["member_id"],
        status=TransactionStatus(row["status"] or "posted"),
        import_batch_id=row["import_batch_id"],
        original_csv_row=_json_load(row["original_csv_row"]),
        dedupe_hash=row["dedupe_hash"],
        is_split=bool(row["is_split"]),
        parent_id=row["parent_id"],
        reimbursable=bool(row["reimbursable"]),
        essential_override=bool(eo) if eo is not None else None,
        normalized_payee=row["normalized_payee"] or "",
    )


# ---------------------------------------------------------------------------
# Calendar Events
# ---------------------------------------------------------------------------

def insert_calendar_event(ev: CalendarEvent) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO calendar_events
               (id, created_at, updated_at, name, event_date, end_date,
                category_id, expected_cost_cents, actual_cost_cents,
                impact, notes, recurring_event_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ev.id, _iso(ev.created_at), _iso(ev.updated_at), ev.name,
             _iso(ev.event_date), _iso(ev.end_date), ev.category_id,
             ev.expected_cost_cents, ev.actual_cost_cents, ev.impact,
             ev.notes, ev.recurring_event_id),
        )


def list_calendar_events(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[CalendarEvent]:
    query = "SELECT * FROM calendar_events WHERE 1=1"
    params: list[Any] = []
    if start_date:
        query += " AND event_date >= ?"
        params.append(_iso(start_date))
    if end_date:
        query += " AND event_date <= ?"
        params.append(_iso(end_date))
    query += " ORDER BY event_date"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_calendar_event(r) for r in rows]


def _row_to_calendar_event(row: sqlite3.Row) -> CalendarEvent:
    return CalendarEvent(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        event_date=_parse_date(row["event_date"]) or date.today(),
        end_date=_parse_date(row["end_date"]),
        category_id=row["category_id"],
        expected_cost_cents=row["expected_cost_cents"],
        actual_cost_cents=row["actual_cost_cents"],
        impact=row["impact"] or "expense",
        notes=row["notes"] or "",
        recurring_event_id=row["recurring_event_id"],
    )


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def insert_category(cat: Category) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO categories
               (id, created_at, updated_at, name, tier, "group",
                monthly_cap_cents, notes, essential, sacred, cut_priority, member_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cat.id, _iso(cat.created_at), _iso(cat.updated_at),
                cat.name, cat.tier.value, cat.group,
                cat.monthly_cap_cents, cat.notes,
                int(cat.essential), int(cat.sacred), cat.cut_priority,
                cat.member_id,
            ),
        )


def list_categories() -> list[Category]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return [_row_to_category(r) for r in rows]


def get_category_by_id(cat_id: str) -> Category | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ? LIMIT 1", (cat_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_category(row)


def update_category(cat: Category) -> None:
    cat.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE categories SET
               updated_at=?, name=?, tier=?, "group"=?,
               monthly_cap_cents=?, notes=?, essential=?, sacred=?,
               cut_priority=?, member_id=?
               WHERE id=?""",
            (_iso(cat.updated_at), cat.name, cat.tier.value, cat.group,
             cat.monthly_cap_cents, cat.notes, int(cat.essential),
             int(cat.sacred), cat.cut_priority, cat.member_id, cat.id),
        )


def _row_to_category(row: sqlite3.Row) -> Category:
    return Category(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        tier=CategoryTier(row["tier"]),
        group=row["group"] or "",
        monthly_cap_cents=row["monthly_cap_cents"],
        notes=row["notes"] or "",
        essential=bool(row["essential"]),
        sacred=bool(row["sacred"]),
        cut_priority=row["cut_priority"] or 0,
        member_id=row["member_id"],
    )


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def insert_schedule(sch: Schedule) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO schedules
               (id, created_at, updated_at, name, payee, direction, category_id,
                cadence, cadence_days, amount_cents, amount_min_cents,
                amount_max_cents, amount_behavior, active, start_date, end_date,
                autopay, payment_method, source, seed_library_key, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sch.id, _iso(sch.created_at), _iso(sch.updated_at),
                sch.name, sch.payee, sch.direction.value, sch.category_id,
                sch.cadence.value, _json_dump(sch.cadence_days),
                sch.amount_cents, sch.amount_min_cents, sch.amount_max_cents,
                sch.amount_behavior.value, int(sch.active),
                _iso(sch.start_date), _iso(sch.end_date),
                int(sch.autopay), sch.payment_method,
                sch.source, sch.seed_library_key, sch.reason,
            ),
        )


def list_schedules(active_only: bool = True) -> list[Schedule]:
    query = "SELECT * FROM schedules"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_schedule(r) for r in rows]


def get_schedule_by_id(sch_id: str) -> Schedule | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE id = ? LIMIT 1", (sch_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_schedule(row)


def update_schedule(sch: Schedule) -> None:
    sch.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE schedules SET
               updated_at=?, name=?, payee=?, direction=?, category_id=?,
               cadence=?, cadence_days=?, amount_cents=?, amount_min_cents=?,
               amount_max_cents=?, amount_behavior=?, active=?, start_date=?,
               end_date=?, autopay=?, payment_method=?, source=?,
               seed_library_key=?, reason=?
               WHERE id=?""",
            (
                _iso(sch.updated_at), sch.name, sch.payee, sch.direction.value,
                sch.category_id, sch.cadence.value, _json_dump(sch.cadence_days),
                sch.amount_cents, sch.amount_min_cents, sch.amount_max_cents,
                sch.amount_behavior.value, int(sch.active),
                _iso(sch.start_date), _iso(sch.end_date),
                int(sch.autopay), sch.payment_method,
                sch.source, sch.seed_library_key, sch.reason, sch.id,
            ),
        )


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    from budget.models import Cadence, AmountBehavior, Direction
    cadence_days = _json_load(row["cadence_days"])
    return Schedule(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        payee=row["payee"] or "",
        direction=Direction(row["direction"]),
        category_id=row["category_id"],
        cadence=Cadence(row["cadence"]),
        cadence_days=cadence_days if cadence_days else [],
        amount_cents=row["amount_cents"],
        amount_min_cents=row["amount_min_cents"],
        amount_max_cents=row["amount_max_cents"],
        amount_behavior=AmountBehavior(row["amount_behavior"] or "fixed"),
        active=bool(row["active"]),
        start_date=_parse_date(row["start_date"]),
        end_date=_parse_date(row["end_date"]),
        autopay=bool(row["autopay"]),
        payment_method=row["payment_method"] or "",
        source=row["source"] or "manual",
        seed_library_key=row["seed_library_key"],
        reason=row["reason"] or "",
    )


# ---------------------------------------------------------------------------
# Monthly Budgets
# ---------------------------------------------------------------------------

def insert_monthly_budget(b: MonthlyBudget) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO monthly_budgets
               (id, created_at, updated_at, month, mode, total_income_cents,
                primary_expenses_cents, secondary_expenses_cents,
                tertiary_expenses_cents, savings_targets_cents,
                extra_debt_payoff_cents, lines, notes, finalized)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                b.id, _iso(b.created_at), _iso(b.updated_at),
                _iso(b.month), b.mode.value, b.total_income_cents,
                b.primary_expenses_cents, b.secondary_expenses_cents,
                b.tertiary_expenses_cents, b.savings_targets_cents,
                b.extra_debt_payoff_cents,
                _json_dump([li.model_dump(mode="json") for li in b.lines]),
                b.notes, int(b.finalized),
            ),
        )


def get_monthly_budget(month: date) -> MonthlyBudget | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM monthly_budgets WHERE month = ? LIMIT 1",
            (_iso(month.replace(day=1)),),
        ).fetchone()
    if not row:
        return None
    return _row_to_monthly_budget(row)


def update_monthly_budget(b: MonthlyBudget) -> None:
    b.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE monthly_budgets SET
               updated_at=?, mode=?, total_income_cents=?, primary_expenses_cents=?,
               secondary_expenses_cents=?, tertiary_expenses_cents=?,
               savings_targets_cents=?, extra_debt_payoff_cents=?,
               lines=?, notes=?, finalized=?
               WHERE id=?""",
            (
                _iso(b.updated_at), b.mode.value, b.total_income_cents,
                b.primary_expenses_cents, b.secondary_expenses_cents,
                b.tertiary_expenses_cents, b.savings_targets_cents,
                b.extra_debt_payoff_cents,
                _json_dump([li.model_dump(mode="json") for li in b.lines]),
                b.notes, int(b.finalized), b.id,
            ),
        )


def _row_to_monthly_budget(row: sqlite3.Row) -> MonthlyBudget:
    from budget.models import BudgetLineItem, BudgetMode
    lines_raw = _json_load(row["lines"]) or []
    return MonthlyBudget(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        month=_parse_date(row["month"]) or date.today().replace(day=1),
        mode=BudgetMode(row["mode"] or "normal"),
        total_income_cents=row["total_income_cents"] or 0,
        primary_expenses_cents=row["primary_expenses_cents"] or 0,
        secondary_expenses_cents=row["secondary_expenses_cents"] or 0,
        tertiary_expenses_cents=row["tertiary_expenses_cents"] or 0,
        savings_targets_cents=row["savings_targets_cents"] or 0,
        extra_debt_payoff_cents=row["extra_debt_payoff_cents"] or 0,
        lines=[BudgetLineItem(**li) for li in lines_raw],
        notes=row["notes"] or "",
        finalized=bool(row["finalized"]),
    )


# ---------------------------------------------------------------------------
# Paycheck Plans
# ---------------------------------------------------------------------------

def insert_paycheck_plan(pp: PaycheckPlan) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO paycheck_plans
               (id, created_at, updated_at, month, paycheck_date,
                next_paycheck_date, committed, reserves, manual_allocations,
                unallocated_cents, notes, finalized)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pp.id, _iso(pp.created_at), _iso(pp.updated_at),
                _iso(pp.month), _iso(pp.paycheck_date),
                _iso(pp.next_paycheck_date),
                _json_dump([c.model_dump(mode="json") for c in pp.committed]),
                _json_dump([r.model_dump(mode="json") for r in pp.reserves]),
                _json_dump([m.model_dump(mode="json") for m in pp.manual_allocations]),
                pp.unallocated_cents, pp.notes, int(pp.finalized),
            ),
        )


def get_paycheck_plan(plan_id: str) -> PaycheckPlan | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM paycheck_plans WHERE id = ? LIMIT 1", (plan_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_paycheck_plan(row)


def list_paycheck_plans_for_month(month: date) -> list[PaycheckPlan]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM paycheck_plans WHERE month = ? ORDER BY paycheck_date",
            (_iso(month.replace(day=1)),),
        ).fetchall()
    return [_row_to_paycheck_plan(r) for r in rows]


def update_paycheck_plan(pp: PaycheckPlan) -> None:
    pp.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE paycheck_plans SET
               updated_at=?, month=?, paycheck_date=?, next_paycheck_date=?,
               committed=?, reserves=?, manual_allocations=?,
               unallocated_cents=?, notes=?, finalized=?
               WHERE id=?""",
            (
                _iso(pp.updated_at), _iso(pp.month), _iso(pp.paycheck_date),
                _iso(pp.next_paycheck_date),
                _json_dump([c.model_dump(mode="json") for c in pp.committed]),
                _json_dump([r.model_dump(mode="json") for r in pp.reserves]),
                _json_dump([m.model_dump(mode="json") for m in pp.manual_allocations]),
                pp.unallocated_cents, pp.notes, int(pp.finalized), pp.id,
            ),
        )


def _row_to_paycheck_plan(row: sqlite3.Row) -> PaycheckPlan:
    from budget.models import PaycheckCommittedItem, PaycheckReserve, PaycheckManualAllocation
    committed = _json_load(row["committed"]) or []
    reserves = _json_load(row["reserves"]) or []
    manual = _json_load(row["manual_allocations"]) or []
    return PaycheckPlan(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        month=_parse_date(row["month"]) or date.today().replace(day=1),
        paycheck_date=_parse_date(row["paycheck_date"]) or date.today(),
        next_paycheck_date=_parse_date(row["next_paycheck_date"]),
        committed=[PaycheckCommittedItem(**c) for c in committed],
        reserves=[PaycheckReserve(**r) for r in reserves],
        manual_allocations=[PaycheckManualAllocation(**m) for m in manual],
        unallocated_cents=row["unallocated_cents"] or 0,
        notes=row["notes"] or "",
        finalized=bool(row["finalized"]),
    )


# ---------------------------------------------------------------------------
# Import Batches
# ---------------------------------------------------------------------------

def insert_import_batch(batch: ImportBatch) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO import_batches
               (id, created_at, updated_at, file_name, file_hash, row_count,
                imported_count, duplicate_count, mapping, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                batch.id, _iso(batch.created_at), _iso(batch.updated_at),
                batch.file_name, batch.file_hash, batch.row_count,
                batch.imported_count, batch.duplicate_count,
                _json_dump(batch.mapping), batch.status,
            ),
        )


def update_import_batch(batch: ImportBatch) -> None:
    batch.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE import_batches SET
               updated_at=?, file_name=?, file_hash=?, row_count=?,
               imported_count=?, duplicate_count=?, mapping=?, status=?
               WHERE id=?""",
            (
                _iso(batch.updated_at), batch.file_name, batch.file_hash,
                batch.row_count, batch.imported_count, batch.duplicate_count,
                _json_dump(batch.mapping), batch.status, batch.id,
            ),
        )


def get_import_batch_by_hash(file_hash: str) -> ImportBatch | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM import_batches WHERE file_hash = ? LIMIT 1",
            (file_hash,),
        ).fetchone()
    if not row:
        return None
    return _row_to_import_batch(row)


def _row_to_import_batch(row: sqlite3.Row) -> ImportBatch:
    return ImportBatch(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        file_name=row["file_name"],
        file_hash=row["file_hash"],
        row_count=row["row_count"],
        imported_count=row["imported_count"] or 0,
        duplicate_count=row["duplicate_count"] or 0,
        mapping=_json_load(row["mapping"]),
        status=row["status"] or "pending",
    )


# ---------------------------------------------------------------------------
# Debt & Goals
# ---------------------------------------------------------------------------

def insert_debt_account(da: DebtAccount) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO debt_accounts
               (id, created_at, updated_at, name, original_balance_cents,
                current_balance_cents, min_payment_cents, interest_rate_percent,
                payoff_strategy, payoff_priority, category_id, active, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                da.id, _iso(da.created_at), _iso(da.updated_at),
                da.name, da.original_balance_cents, da.current_balance_cents,
                da.min_payment_cents,
                str(da.interest_rate_percent) if da.interest_rate_percent else None,
                da.payoff_strategy, da.payoff_priority, da.category_id,
                int(da.active), da.notes,
            ),
        )


def list_debt_accounts(active_only: bool = True) -> list[DebtAccount]:
    query = "SELECT * FROM debt_accounts"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY payoff_priority, name"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_debt_account(r) for r in rows]


def get_debt_account_by_id(did: str) -> DebtAccount | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM debt_accounts WHERE id = ? LIMIT 1", (did,)
        ).fetchone()
    if not row:
        return None
    return _row_to_debt_account(row)


def update_debt_account(da: DebtAccount) -> None:
    da.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE debt_accounts SET
               updated_at=?, name=?, original_balance_cents=?,
               current_balance_cents=?, min_payment_cents=?,
               interest_rate_percent=?, payoff_strategy=?, payoff_priority=?,
               category_id=?, active=?, notes=?
               WHERE id=?""",
            (
                _iso(da.updated_at), da.name, da.original_balance_cents,
                da.current_balance_cents, da.min_payment_cents,
                str(da.interest_rate_percent) if da.interest_rate_percent else None,
                da.payoff_strategy, da.payoff_priority, da.category_id,
                int(da.active), da.notes, da.id,
            ),
        )


def _row_to_debt_account(row: sqlite3.Row) -> DebtAccount:
    from decimal import Decimal
    ir = row["interest_rate_percent"]
    return DebtAccount(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        original_balance_cents=row["original_balance_cents"] or 0,
        current_balance_cents=row["current_balance_cents"] or 0,
        min_payment_cents=row["min_payment_cents"] or 0,
        interest_rate_percent=Decimal(ir) if ir else None,
        payoff_strategy=row["payoff_strategy"] or "avalanche",
        payoff_priority=row["payoff_priority"] or 0,
        category_id=row["category_id"],
        active=bool(row["active"]),
        notes=row["notes"] or "",
    )


def insert_savings_goal(sg: SavingsGoal) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO savings_goals
               (id, created_at, updated_at, name, target_cents, saved_cents,
                deadline, category_id, priority, active, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sg.id, _iso(sg.created_at), _iso(sg.updated_at),
                sg.name, sg.target_cents, sg.saved_cents,
                _iso(sg.deadline), sg.category_id, sg.priority,
                int(sg.active), sg.notes,
            ),
        )


def list_savings_goals(active_only: bool = True) -> list[SavingsGoal]:
    query = "SELECT * FROM savings_goals"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY priority DESC, name"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_savings_goal(r) for r in rows]


def get_savings_goal_by_id(gid: str) -> SavingsGoal | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM savings_goals WHERE id = ? LIMIT 1", (gid,)
        ).fetchone()
    if not row:
        return None
    return _row_to_savings_goal(row)


def update_savings_goal(sg: SavingsGoal) -> None:
    sg.updated_at = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE savings_goals SET
               updated_at=?, name=?, target_cents=?, saved_cents=?,
               deadline=?, category_id=?, priority=?, active=?, notes=?
               WHERE id=?""",
            (
                _iso(sg.updated_at), sg.name, sg.target_cents, sg.saved_cents,
                _iso(sg.deadline), sg.category_id, sg.priority,
                int(sg.active), sg.notes, sg.id,
            ),
        )


def _row_to_savings_goal(row: sqlite3.Row) -> SavingsGoal:
    return SavingsGoal(
        id=row["id"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
        name=row["name"],
        target_cents=row["target_cents"] or 0,
        saved_cents=row["saved_cents"] or 0,
        deadline=_parse_date(row["deadline"]),
        category_id=row["category_id"],
        priority=row["priority"] or 0,
        active=bool(row["active"]),
        notes=row["notes"] or "",
    )
