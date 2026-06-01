"""CSV import with column mapping, normalization, provenance, and duplicate detection."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from budget.db import (
    find_transaction_by_dedupe_hash,
    insert_import_batch,
    insert_transaction,
    update_import_batch,
)
from budget.models import Direction, ImportBatch, Transaction


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

# Aliases are ordered by preference: the first alias present in the headers
# wins. Lists (not sets) keep matching deterministic across processes — set
# iteration order varies under hash randomization, which would otherwise let
# the same file map a field to different columns on different runs.
KNOWN_COLUMNS = {
    "date": ["date", "transaction date", "posted date", "date posted", "txn date"],
    "payee": ["payee", "description", "merchant", "counterparty", "name", "memo"],
    "amount": ["amount", "transaction amount", "amount ($)", "amount($)"],
    "debit": ["debit", "debit amount", "debit ($)", "debit($)", "withdrawal"],
    "credit": ["credit", "credit amount", "credit ($)", "credit($)", "deposit"],
    "direction": ["type", "transaction type", "direction"],
}


def auto_detect_mapping(headers: list[str]) -> dict[str, str]:
    """Map CSV headers to canonical fields, honoring alias priority order."""
    mapping: dict[str, str] = {}
    lower_headers = [h.strip().lower() for h in headers]
    for canonical, aliases in KNOWN_COLUMNS.items():
        for alias in aliases:
            if alias in lower_headers:
                mapping[canonical] = headers[lower_headers.index(alias)]
                break
    return mapping


def resolve_mapping(
    headers: list[str],
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    mapping = auto_detect_mapping(headers)
    if overrides:
        for k, v in overrides.items():
            if v in headers:
                mapping[k] = v
    # Validate
    if "date" not in mapping:
        raise ValueError(f"Could not detect date column in {headers}")
    if "amount" not in mapping and ("debit" not in mapping or "credit" not in mapping):
        raise ValueError(f"Could not detect amount columns in {headers}")
    return mapping


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_NORMALIZATION = str.maketrans({
    "’": "'", "–": "-", "—": "-",
})


def normalize_payee(raw: str) -> str:
    s = raw.strip().translate(_NORMALIZATION)
    # Strip common suffixes
    for suffix in (" LLC", " INC", " LTD", " CORP", " CO", " PLC"):
        if s.upper().endswith(suffix):
            s = s[: -len(suffix)].strip()
    # Title-case short names, keep long ones as-is mostly
    if len(s) <= 30:
        s = s.title()
    return s


def parse_amount(raw: str) -> int:
    """Return integer cents from a raw amount string."""
    cleaned = raw.replace("$", "").replace(",", "").strip()
    is_parenthesized_negative = cleaned.startswith("(") and cleaned.endswith(")")
    if is_parenthesized_negative:
        cleaned = cleaned[1:-1].strip()
    try:
        d = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Cannot parse amount: {raw}")
    if is_parenthesized_negative:
        d = -d
    return int((d * 100).to_integral_value())


def _contains_income_keyword(payee: str) -> bool:
    normalized = payee.lower()
    income_keywords = ["payroll", "deposit", "direct dep", "salary", "wage", "refund", "interest", "dividend"]
    for keyword in income_keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        if re.search(pattern, normalized):
            return True
    return False


def parse_date(raw: str) -> date:
    """Parse common date formats."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    # ISO fallback
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    raise ValueError(f"Cannot parse date: {raw}")


# ---------------------------------------------------------------------------
# Row → Transaction
# ---------------------------------------------------------------------------

def row_to_transaction(
    row: dict[str, str],
    mapping: dict[str, str],
    file_name: str,
    batch_id: str,
    row_index: int,
    account_id: str = "default",
) -> Transaction:
    date_raw = row[mapping["date"]]
    payee_raw = row.get(mapping.get("payee", ""), "")

    # Determine amount and direction
    if "amount" in mapping:
        raw_cents = parse_amount(row[mapping["amount"]])
        amount_cents = abs(raw_cents)
        # Default: negative = expense (money leaving), positive = income (money entering)
        # Common bank export convention
        direction = Direction.INCOME if raw_cents > 0 else Direction.EXPENSE
        # Keyword hints only promote *unsigned/positive* amounts to income. A
        # signed-negative amount is authoritative: "Interest Charge" or
        # "Refund Fee" are debits and must stay expenses.
        if raw_cents > 0 and _contains_income_keyword(payee_raw):
            direction = Direction.INCOME
        # Optional explicit direction override
        if "direction" in mapping:
            dir_raw = row[mapping["direction"]].strip().lower()
            if dir_raw in ("debit", "withdrawal", "purchase", "payment", "expense"):
                direction = Direction.EXPENSE
            elif dir_raw in ("credit", "deposit", "income", "refund"):
                direction = Direction.INCOME
    elif "debit" in mapping and "credit" in mapping:
        debit_raw = row[mapping["debit"]].strip()
        credit_raw = row[mapping["credit"]].strip()
        if debit_raw:
            amount_cents = abs(parse_amount(debit_raw))
            direction = Direction.EXPENSE
        elif credit_raw:
            amount_cents = abs(parse_amount(credit_raw))
            direction = Direction.INCOME
        else:
            raise ValueError("Empty debit and credit")
    else:
        raise ValueError("No amount mapping")

    norm_payee = normalize_payee(payee_raw) if payee_raw else f"Row {row_index}"
    tx = Transaction(
        account_id=account_id,
        date=parse_date(date_raw),
        payee=norm_payee,
        description=payee_raw,
        amount_cents=amount_cents,
        direction=direction,
        import_batch_id=batch_id,
        original_csv_row=dict(row),
        normalized_payee=norm_payee.lower().strip(),
    )
    tx.dedupe_hash = tx.compute_dedupe_hash()
    return tx


# ---------------------------------------------------------------------------
# File hash
# ---------------------------------------------------------------------------

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Import orchestration
# ---------------------------------------------------------------------------

def import_csv_file(
    path: Path,
    mapping_overrides: dict[str, str] | None = None,
    dry_run: bool = False,
    account_id: str = "default",
) -> dict[str, Any]:
    fhash = file_hash(path)

    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV has no headers")
        mapping = resolve_mapping(list(reader.fieldnames), mapping_overrides)

    batch = ImportBatch(
        file_name=path.name,
        file_hash=fhash,
        row_count=0,
        status="pending",
        mapping=mapping,
    )

    if not dry_run:
        insert_import_batch(batch)

    imported = 0
    duplicates = 0
    skipped = 0
    transactions: list[Transaction] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        f.seek(0)
        reader = csv.DictReader(f, dialect=dialect)
        for idx, row in enumerate(reader, start=1):
            batch.row_count += 1
            # Skip rows with no date
            if not row.get(mapping["date"], "").strip():
                skipped += 1
                continue
            try:
                tx = row_to_transaction(row, mapping, path.name, batch.id, idx, account_id)
            except ValueError as e:
                skipped += 1
                continue

            # Duplicate detection
            existing = find_transaction_by_dedupe_hash(tx.dedupe_hash)
            if existing:
                duplicates += 1
                continue

            transactions.append(tx)
            if not dry_run:
                insert_transaction(tx)
            imported += 1

    batch.imported_count = imported
    batch.duplicate_count = duplicates
    batch.status = "complete"
    if not dry_run:
        update_import_batch(batch)

    return {
        "batch_id": batch.id,
        "file_name": batch.file_name,
        "file_hash": batch.file_hash,
        "row_count": batch.row_count,
        "imported": imported,
        "duplicates": duplicates,
        "skipped": skipped,
        "mapping": mapping,
        "dry_run": dry_run,
    }
