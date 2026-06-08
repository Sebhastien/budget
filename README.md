# budget

A personal/household budgeting CLI for savings and debt optimization. Import
bank CSVs, detect recurring streams, plan monthly budgets and paychecks,
reconcile against actuals, and produce dashboard and debt-payoff reports — all
in a local SQLite database. Money is stored as integer cents throughout, so
there are no floating-point rounding surprises.

## Requirements

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Install

```bash
uv sync                     # create .venv and install deps
uv run budget --help        # run the CLI

# or, with pip:
pip install -e .
budget --help
```

Data lives in `~/.budget/budget.db` by default. Override with `--data-dir` or the
`BUDGET_DATA_DIR` environment variable. The database is never committed.

## Quick start

```bash
budget init                              # create the DB and seed categories
budget account add --name Checking --type checking --balance 5000
budget import statement.csv --account-id checking
budget recurring suggest                 # detect recurring bills/income
budget budget create --month 2025-06     # build a monthly budget
budget paycheck plan --date 2025-06-15   # plan a paycheck
budget reconcile --month 2025-06         # mark actuals, recompute spent
budget report month --month 2025-06      # dashboard + month-over-month
```

Every mutating command supports `--dry-run` (preview without writing) and
`--json` (machine-readable output), and records an audit event with a rollback
ID.

## Commands

| Command | Description |
| --- | --- |
| `init` | Initialize the database and seed default categories. |
| `import FILE` | Import a bank CSV. Auto-detects columns; override with `--map-date/-payee/-amount/-debit/-credit`. `--account-id` scopes dedupe. |
| `recurring suggest` | Detect recurring income/expense streams (seed library + pattern detection). |
| `schedules list` / `schedules confirm` | List or confirm recurring schedules. |
| `budget create` / `budget show` | Create or display a monthly budget (`--month YYYY-MM`). Modes: normal, tighten, emergency, debt_attack, land_saving, new_baby, travel, holiday. |
| `paycheck plan` / `paycheck edit` | Plan a paycheck and add manual allocations. |
| `reconcile` | Post pending transactions and recompute budget/paycheck actuals for a month. |
| `report month` / `report dashboard` / `report debt` | Reports: dashboard + month-over-month, current dashboard, debt-payoff projection. |
| `account add` / `account list` | Manage cash/credit/savings/investment/loan accounts. |
| `member add` / `member list` | Manage household members. |
| `categories` | List categories. |
| `export` | Export all data as JSON. |

## CSV import

Column headers are auto-detected against a prioritized alias list (e.g. `Date`,
`Description`/`Payee`, `Amount`, or split `Debit`/`Credit`). Amounts may be signed
(`-50.00`) or parenthesized (`($50.00)`); both are treated as expenses. Rows are
deduplicated by a hash of account + date + payee + amount + direction +
description, so re-importing the same file is safe.

## Example Output

### `budget report dashboard`

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BUDGET DASHBOARD — June 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ACCOUNTS
  Checking (Chase)         $  3,842.17
  High-Yield Savings       $ 12,500.00
  Visa Signature (credit)  $   -924.55  [utilization: 18%]
  Car Loan (Toyota)        $ -8,214.00
  ────────────────────────────────────
  Net Worth                $  7,203.62

SPENDING THIS MONTH  (Jun 1–18, 18 days in)
  Groceries          $  384.12  / $  600.00   ████████░░░░░  64%
  Dining Out         $  127.40  / $  200.00   ███████░░░░░░  64%
  Gas & Transport    $   89.00  / $  150.00   ██████░░░░░░░  59%
  Utilities          $  143.00  / $  150.00   █████████████  95%  ⚠
  Subscriptions      $   62.97  / $   75.00   ████████████░  84%
  Healthcare         $    0.00  / $  100.00   ░░░░░░░░░░░░░   0%
  Clothing           $   54.99  / $  100.00   ███████░░░░░░  55%
  Misc / Other       $   31.20  / $  100.00   ████░░░░░░░░░  31%
  ────────────────────────────────────────────────────────────
  Total Spent        $  892.68  / $1,475.00                  61%

BUDGET PROGRESS
  Budgeted income this month:   $5,400.00
  Fixed expenses (rent, loan):  $1,950.00
  Variable budget remaining:    $2,557.32
  Unallocated surplus:          $  107.32
  Savings target (20%):         $1,080.00  ✓ on track

UPCOMING (next 7 days)
  Jun 20  Rent — AutoPay         $1,200.00
  Jun 22  Internet (Xfinity)     $   59.99
  Jun 25  Car loan payment       $  312.00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### `budget report debt`

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  DEBT PAYOFF PROJECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CURRENT DEBTS
  Visa Signature    APR 22.99%   Balance   $924.55   Min  $ 25.00
  Car Loan          APR  6.49%   Balance $8,214.00   Min  $312.00
  ──────────────────────────────────────────────────
  Total Debt                              $9,138.55

STRATEGY: Avalanche (highest APR first)
  Monthly payment budget:   $650.00
  Extra toward Visa:        $313.00  (above minimums)

PAYOFF TIMELINE
  Visa Signature
    Months to payoff:     3  (Sep 2026)
    Total interest paid:  $ 31.42
    Payoff date:          2026-09

  Car Loan  (after Visa cleared — full $650 redirected)
    Months to payoff:    14  (Nov 2027)  ← accelerated from 27 months
    Total interest paid: $412.18
    Payoff date:         2027-11

SUMMARY
  Total interest (avalanche):   $  443.60
  Total interest (minimums):    $1,128.74
  Interest saved:               $  685.14
  Debt-free date:               November 2027

  Tip: Adding $50/month would move debt-free date to August 2027,
       saving an additional $89.21 in interest.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Development

This project was built with AI assistance (Claude by Anthropic). AI-assisted development is embraced here as a productivity tool — the important thing is that the code is well-tested, readable, and correct.

Want to contribute? See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, coding conventions, and PR guidelines. AI-assisted contributions are welcome.

### Layout

```
src/budget/
  models.py          # pydantic data model + money helpers
  db.py              # SQLite persistence (repository functions)
  csv_import.py      # CSV parsing, column mapping, dedupe
  recurring.py       # recurring-stream detection (seed library + patterns)
  budget_engine.py   # monthly budget construction and variance
  paycheck.py        # paycheck planning
  reconciliation.py  # match actuals to plans
  reports.py         # dashboard, month-over-month, debt projection
  cli.py             # Click entry point
tests/               # pytest suite incl. regression tests
```

```bash
uv run pytest          # run the test suite
```
