# Contributing to budget

Thank you for your interest in contributing! This document covers how to get set up, the project conventions, and how to submit changes.

AI-assisted contributions are genuinely welcome — if you used Claude, Copilot, or another AI tool to help write your code or tests, that's great. Just review the output carefully and make sure it follows the conventions below before submitting.

---

## Setting up the dev environment

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
git clone <repo-url>
cd budget

# Install all dependencies (including dev/test extras) into a local .venv
uv sync

# Verify everything works
uv run pytest
```

That's it — no separate virtualenv activation needed. Prefix any command with `uv run` to use the project's environment.

---

## Running tests

```bash
# Run the full test suite
uv run pytest

# Run a specific file or test
uv run pytest tests/test_budget.py
uv run pytest tests/test_budget.py::test_import_deduplication

# Run with verbose output
uv run pytest -v

# Run and show print output (useful for debugging)
uv run pytest -s
```

Tests live in `tests/`. Regression tests are in `tests/test_budget_regressions.py`. Sample CSV fixtures are in `tests/sample_transactions.csv`.

---

## Coding conventions

### Money is always integer cents

**Never use floats for money.** All monetary values are stored and manipulated as integer cents (`int`). For example, $12.50 is stored as `1250`. The `models.py` module has helper functions for converting to/from display strings.

```python
# Good
amount_cents: int = 1250   # $12.50

# Bad
amount: float = 12.50      # floating-point rounding errors accumulate
```

### Commit messages — Conventional Commits

Please use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<optional scope>): <short description>

[optional body]

[optional footer(s)]
```

Common types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `style`, `perf`.

Examples:
```
feat(reports): add net-worth trend to dashboard report
fix(csv-import): handle parenthesized negative amounts with spaces
chore: bump dev dependencies
docs: add example output to README
test(reconciliation): cover partial-match edge case
```

### Style

- Python 3.11+, type-annotated throughout.
- Follow the existing code style (no separate linter config yet — match the surrounding code).
- Keep functions small and focused; prefer pure functions where possible.
- Do not import optional heavy libraries at module level.

---

## Pull request guidelines

- **Small, focused PRs** — one logical change per PR makes review easier and keeps the git history clean.
- **Tests required** — every new feature or bug fix should come with a test. If you fix a regression, add it to `test_budget_regressions.py`.
- **No floating-point money** — see above. PRs that introduce `float` for monetary values will not be merged.
- **Update docs** — if you add or change a command, update the relevant section of the README.
- **Dry-run/JSON flags** — new mutating commands should support `--dry-run` and `--json`, consistent with the rest of the CLI.

### PR checklist

- [ ] `uv run pytest` passes with no new failures
- [ ] Conventional commit message(s)
- [ ] New/changed behavior is tested
- [ ] README updated if commands changed
- [ ] No floats used for money

---

## Project layout

```
src/budget/
  models.py          # Pydantic data model + money helpers
  db.py              # SQLite persistence (repository functions)
  csv_import.py      # CSV parsing, column mapping, dedupe
  recurring.py       # Recurring-stream detection
  budget_engine.py   # Monthly budget construction and variance
  paycheck.py        # Paycheck planning
  reconciliation.py  # Match actuals to plans
  reports.py         # Dashboard, month-over-month, debt projection
  cli.py             # Click entry point
tests/
  test_budget.py
  test_budget_regressions.py
  sample_transactions.csv
```

---

## Questions?

Open an issue or start a discussion. Contributions of all sizes are welcome — typo fixes, new features, and everything in between.
