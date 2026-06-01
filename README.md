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

## Development

```bash
uv run pytest          # run the test suite
```

Layout:

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
