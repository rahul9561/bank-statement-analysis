"""Conversion of raw table rows into canonical ``Transaction`` objects.

Nothing here silently changes amounts. Interpretation steps that are not read
directly from a labelled column (statement order, direction inferred from the
balance) are recorded as flags on the transaction and as validation issues.
"""

from __future__ import annotations

from decimal import Decimal

from ..parsers.base import RawRow
from ..schemas import CREDIT, DEBIT, ZERO, Transaction, ValidationIssue


def _chain_score(rows: list[RawRow], tol: Decimal, swap: bool = False) -> tuple[int, int]:
    ok = total = 0
    for prev, cur in zip(rows, rows[1:]):
        if prev.balance is None or cur.balance is None:
            continue
        debit, credit = (cur.credit, cur.debit) if swap else (cur.debit, cur.credit)
        total += 1
        if abs(prev.balance + (credit or ZERO) - (debit or ZERO) - cur.balance) <= tol:
            ok += 1
    return ok, total


def detect_reverse_order(rows: list[RawRow], tol: Decimal) -> bool:
    if len(rows) < 2:
        return False
    if rows[0].date != rows[-1].date:
        return rows[0].date > rows[-1].date
    forward, _ = _chain_score(rows, tol)
    backward, _ = _chain_score(rows[::-1], tol)
    return backward > forward


def build_transactions(
    rows: list[RawRow],
    tolerance: Decimal,
    *,
    layout_inferred: bool = False,
    keep_source_text: bool = False,
) -> tuple[list[Transaction], list[ValidationIssue], dict]:
    issues: list[ValidationIssue] = []
    info: dict = {"rows_read": len(rows), "rows_without_amount": 0, "reverse_chronological": False,
                  "columns_swapped": False, "direction_inferred": 0}

    usable: list[RawRow] = []
    skipped_pages: list[int] = []
    unparsed = [r for r in rows if any(f.startswith("UNPARSED_") for f in r.flags)]
    if unparsed:
        issues.append(ValidationIssue(
            "UNPARSED_AMOUNT", "ERROR",
            f"{len(unparsed)} row(s) contain text in an amount or balance column that could not be read as a "
            "number; these rows may be missing or incomplete.",
            page=unparsed[0].page, details={"pages": sorted({r.page for r in unparsed})[:20]},
        ))
    for row in rows:
        if row.debit is None and row.credit is None and row.amount is None:
            info["rows_without_amount"] += 1
            skipped_pages.append(row.page)
            continue
        usable.append(row)
    if skipped_pages:
        issues.append(ValidationIssue(
            "ROW_WITHOUT_AMOUNT", "INFO",
            f"{len(skipped_pages)} dated row(s) had no debit or credit amount (e.g. zero-value "
            "verification entries) and were not counted as transactions.",
            page=skipped_pages[0], details={"pages": sorted(set(skipped_pages))[:20]},
        ))

    if detect_reverse_order(usable, tolerance):
        usable.reverse()
        info["reverse_chronological"] = True
        issues.append(ValidationIssue(
            "REVERSE_CHRONOLOGICAL", "INFO",
            "The statement lists transactions newest-first; they were re-ordered oldest-first for analysis.",
        ))

    if layout_inferred:
        as_is, total = _chain_score(usable, tolerance)
        swapped, _ = _chain_score(usable, tolerance, swap=True)
        if total and swapped > as_is and swapped >= 0.9 * total:
            for row in usable:
                row.debit, row.credit = row.credit, row.debit
            info["columns_swapped"] = True
            issues.append(ValidationIssue(
                "INFERRED_COLUMNS_ORIENTED_BY_BALANCE", "INFO",
                "The table header was not readable; the debit/credit columns were identified from "
                "the running balance.",
            ))

    transactions: list[Transaction] = []
    unresolved: list[str] = []
    negative_rows: list[str] = []
    for i, row in enumerate(usable):
        tid = f"T{i + 1:05d}"
        flags = list(row.flags)
        debit = row.debit or ZERO
        credit = row.credit or ZERO
        if row.amount is not None and not debit and not credit:
            if row.drcr == "DR":
                debit = row.amount
            elif row.drcr == "CR":
                credit = row.amount
            else:
                prev = usable[i - 1].balance if i > 0 else None
                if prev is not None and row.balance is not None:
                    if abs(prev + row.amount - row.balance) <= tolerance:
                        credit = row.amount
                    elif abs(prev - row.amount - row.balance) <= tolerance:
                        debit = row.amount
                if debit or credit:
                    flags.append("DIRECTION_INFERRED_FROM_BALANCE")
                    info["direction_inferred"] += 1
                else:
                    debit = row.amount
                    flags.append("DIRECTION_UNKNOWN")
                    unresolved.append(tid)
        if debit < 0 or credit < 0:
            # A negative withdrawal adds money (typically a reversal) and vice versa;
            # the running-balance check confirms or rejects this reading.
            flags.append("NEGATIVE_VALUE_IN_AMOUNT_COLUMN")
            negative_rows.append(tid)
            if debit < 0:
                credit, debit = credit - debit, ZERO
            if credit < 0:
                debit, credit = debit - credit, ZERO
        if debit and credit:
            flags.append("BOTH_DEBIT_AND_CREDIT")
            issues.append(ValidationIssue(
                "BOTH_DEBIT_AND_CREDIT", "WARNING",
                "A row carries both a debit and a credit amount.", transaction_ids=[tid], page=row.page,
            ))
        ttype = CREDIT if credit > debit else DEBIT
        transactions.append(Transaction(
            id=tid,
            date=row.date,
            value_date=row.value_date,
            narration=row.narration,
            reference_number=row.reference,
            transaction_type=ttype,
            debit=debit.quantize(Decimal("0.01")),
            credit=credit.quantize(Decimal("0.01")),
            balance=row.balance,
            source_page=row.page,
            source_text=row.source_text if keep_source_text else None,
            flags=flags,
        ))
    if negative_rows:
        issues.append(ValidationIssue(
            "NEGATIVE_AMOUNT_IN_COLUMN", "INFO",
            f"{len(negative_rows)} row(s) print a negative value in the withdrawal/deposit column (usually a "
            "reversal). A negative withdrawal was read as a credit and a negative deposit as a debit.",
            transaction_ids=negative_rows[:50],
        ))
    if unresolved:
        issues.append(ValidationIssue(
            "DIRECTION_UNKNOWN", "ERROR",
            f"{len(unresolved)} transaction(s) have an amount without a debit/credit indicator.",
            transaction_ids=unresolved[:50],
        ))
    if info["direction_inferred"]:
        issues.append(ValidationIssue(
            "DIRECTION_INFERRED", "INFO",
            f"Debit/credit direction of {info['direction_inferred']} transaction(s) was inferred from "
            "the running balance.",
        ))
    return transactions, issues, info
