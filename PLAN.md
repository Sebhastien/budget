# Household Budgeting CLI — Implementation Plan & Chunk Map

## Overview
A deterministic, automation-safe household budgeting CLI. Monthly budget is the primary planning object; paycheck planning is the execution layer on top. Built around one principle: **help make better household decisions**, not just track spending.

## Tech Stack
- Python 3.13, Click CLI, Pydantic v2, SQLite local-first
- Integer cents for all money in JSON
- Every command supports `--json`; mutating commands support `--dry-run`
- Every mutation writes an AuditEvent with rollback ID

---

## Chunk A: Foundation ✅ (COMPLETE)
**Goal:** Safely import transactions and suggest recurring items.

- [x] Project scaffolding, Pydantic models, SQLite DB layer, `budget init`
- [x] CSV import with column mapping, normalization, provenance tracking, duplicate detection
- [x] Recurring suggestion engine with seed library + pattern detection
- [x] Seed library covering housing, utilities, debt, insurance, subscriptions, income
- [x] Confidence scoring, cadence inference, amount behavior classification
- [x] All JSON amounts in integer cents
- [x] Audit events + rollback IDs

**Files:** `models.py`, `db.py`, `csv_import.py`, `recurring.py`, `cli.py` (init/import/recurring)

---

## Chunk B: Core Planning Engine ✅ (COMPLETE)
**Goal:** Turn schedules + history into a monthly plan and paycheck plans.

- [x] Extended data model: Accounts, CalendarEvent, HouseholdMember, BudgetMode, sacred/cut-first flags
- [x] `budget recurring suggest` wired to CLI
- [x] `budget schedules list / confirm` (including semi-monthly 15th/last-day support)
- [x] Monthly budget engine: recurring schedules populate planned amounts (with cadence-aware multiplication)
- [x] Paycheck planning engine: assigns obligations to paycheck windows
- [x] `budget budget create/show`
- [x] `budget paycheck plan/edit`

**Files:** `budget_engine.py`, `paycheck.py`, `cli.py` (schedules/budget/paycheck)

---

## Chunk C: Execution & Reconciliation ✅ (COMPLETE)
**Goal:** Close the loop between plan and reality.

- [x] Reconciliation: posted transactions update paycheck execution status + monthly balances
- [x] Manual allocation support for one-off decisions (extra debt, savings, temporary boosts)
- [x] `budget reconcile`
- [x] `budget paycheck edit` for manual entries
- [x] Deduct manual allocations from monthly budget when category is specified

**Files:** `reconciliation.py`, `cli.py`

---

## Chunk D: Household Intelligence ✅ (COMPLETE — MVP Level)
**Goal:** Answer "Are we okay?" and recommend next actions.

- [x] `budget report dashboard` — cash, upcoming bills, debt, net worth, safe-to-spend, next action
- [x] `budget report month` — full variance analysis + month-over-month changes
- [x] `budget report debt` — payoff projection with extra payment scenarios
- [x] Danger zone detection (categories over budget)
- [x] Decision engine basics: safe-to-spend calculation, bill buffer check, recommended next action
- [x] Budget modes supported in model and CLI (normal, tighten, emergency, debt_attack, land_saving, new_baby, travel, holiday)
- [x] Sacred vs cut-first category flags

**Files:** `reports.py`, `cli.py`

---

## Chunk E: Data Management & Export ✅ (COMPLETE)
**Goal:** Household entities and scriptable output.

- [x] `budget member add/list`
- [x] `budget account add/list`
- [x] `budget categories` list
- [x] `budget export --json` (transactions, schedules, accounts, debts, goals)
- [x] Local-first SQLite storage

**Files:** `cli.py`, `db.py`

---

## AI Agent Vision Coverage Map

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| 1 | Household financial dashboard | ✅ MVP | `report dashboard` — cash, bills, debt, net worth, safe-to-spend, next action |
| 2 | Income tracking (variable) | ✅ MVP | Via transaction categories + direction; conservative vs actual shown in reports |
| 3 | Bills / recurring obligations | ✅ MVP | Seed library + schedules + autopay flag; `report dashboard` shows upcoming bills |
| 4 | Spending categories | ✅ MVP | Generic seeded categories with tiers (primary/secondary/tertiary), sacred/cut-first flags |
| 5 | Budget plan by month | ✅ MVP | `budget budget create/show` with planned vs spent variance per line |
| 6 | Transaction tracking | ✅ MVP | Full provenance, dedupe, member_id, reimbursable, essential_override, split support fields |
| 7 | Account integration / manual import | ✅ MVP | CSV import with flexible mapping + dedupe; manual entry via CLI |
| 8 | Debt management | ✅ MVP | DebtAccount model + `report debt` payoff projection (avalanche/snowball) |
| 9 | Savings goals / sinking funds | ✅ MVP | SavingsGoal model with target, saved, deadline, priority |
| 10 | Grocery / meal budget integration | 🔄 Partial | Generic grocery subcategories exist (bulk/warehouse, fresh groceries, prepared/frozen meals); meal logic is future |
| 11 | Calendar-aware budgeting | 🔄 Partial | CalendarEvent model + DB exists; CLI not yet exposed but can be added |
| 12 | AI assistant layer | 📋 Future | CLI reports provide the data foundation; natural language layer is future |
| 13 | Decision engine | ✅ MVP | Dashboard ranks decisions: cut first, pay debt, build buffer |
| 14 | Family member / household roles | ✅ MVP | `member` commands; member_id on transactions |
| 15 | Alerts and briefings | 🔄 Partial | Dashboard danger zones + next action; scheduled alerts are future |
| 16 | Budget philosophy settings | ✅ MVP | Sacred/cut-first flags on categories; budget modes on monthly plan |
| 17 | Monthly closeout | ✅ MVP | `reconcile` + `report month` provide actuals vs plan, variance, notes |
| 18 | Forecasting | 🔄 Partial | `report debt` projects payoff; `report dashboard` shows safe-to-spend |
| 19 | Privacy and data safety | ✅ MVP | Local SQLite, JSON export, audit log, no third-party sharing |
| 20 | Data model essentials | ✅ MVP | Accounts, transactions, categories, budgets, schedules, debts, goals, events, members |
| 21 | Rules and automations | ✅ MVP | Seed library auto-matching + recurring pattern detection |
| 22 | Reports that matter | ✅ MVP | Dashboard, month-over-month, debt projection, variance by category |
| 23 | "What changed?" analysis | ✅ MVP | `report month` includes biggest leaks, wins, category changes, delta % |
| 24 | Budget modes | ✅ MVP | Normal, tighten, emergency, debt_attack, land_saving, new_baby, travel, holiday |
| 25 | MVP feature set | ✅ MVP | Import, categories, budget, bills, goals, debt tracker, dashboard, reports, export |

---

## Acceptance Criteria Checklist

From the original spec:

- [x] Import CSVs and safely normalize/deduplicate without inflating totals on re-import
- [x] Recurring suggestion helper identifies obvious recurring income/expenses using seed library + fallback heuristics
- [x] Confirmed recurring schedules can populate monthly budget and paycheck planner
- [x] Create monthly budget as the main plan for the household
- [x] Generate paycheck plans for 15th/last-day workflows by assigning due items to paycheck windows
- [x] Review paycheck draft, add manual allocations, reconcile results back into the month cleanly
- [x] All commands support `--json`
- [x] Mutating commands support `--dry-run`
- [x] JSON amounts are integer cents
- [x] Every mutation generates an audit event and rollback identifier

---

## Next Chunks (Post-MVP)

**Chunk F: Rules & Automations**
- Merchant → category auto-assignment rules
- Autopay detection from descriptions
- Subscription price-change alerts

**Chunk G: Calendar Integration**
- `budget event add/list`
- Calendar events feed into monthly budget as planned line items
- Seasonal expense warnings

**Chunk H: Advanced Intelligence**
- "What changed?" natural language summary
- Grocery cost-per-serving tracking
- Meal plan cost estimator

**Chunk I: Bank Sync (Optional)**
- Plaid / MX / Teller integration layer
- OAuth token management

---

## How to Run

```bash
# Install dependencies
uv venv
uv pip install -e .
uv pip install pytest

# Initialize
PYTHONPATH=src uv run budget init

# Import transactions
PYTHONPATH=src uv run budget import path/to/transactions.csv

# Suggest recurring items
PYTHONPATH=src uv run budget recurring suggest

# Confirm schedules
PYTHONPATH=src uv run budget schedules confirm --payee "Mortgage" --direction expense --cadence monthly --amount 1200 --category-id <uuid>

# Create monthly budget
PYTHONPATH=src uv run budget budget create --month 2025-05

# Show budget
PYTHONPATH=src uv run budget budget show --month 2025-05

# Create paycheck plan
PYTHONPATH=src uv run budget paycheck plan --date 2025-05-15

# Add manual allocation
PYTHONPATH=src uv run budget paycheck edit --plan-id <uuid> --name "Extra Debt" --amount 500 --direction expense --category-id <uuid>

# Reconcile
PYTHONPATH=src uv run budget reconcile --month 2025-05

# Dashboard
PYTHONPATH=src uv run budget report dashboard

# Month report
PYTHONPATH=src uv run budget report month --month 2025-05

# Export
PYTHONPATH=src uv run budget export --json

# Tests
PYTHONPATH=src uv run python -m pytest tests/test_budget.py -v
```

**Note:** `PYTHONPATH=src` is needed because `uv run` creates an isolated environment context. You can also activate the venv directly: `source .venv/bin/activate && budget init`.
