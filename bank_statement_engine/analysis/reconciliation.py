"""Financial validation and reconciliation.

Checks performed (each recorded as a ``ValidationCheck``):

* running balance: ``previous balance + credit - debit == balance`` for every link;
* opening balance (printed or derived) links to the first transaction;
* ``opening + total credits - total debits == closing balance``;
* printed summary totals and counts agree with extracted transactions;
* dates lie inside the printed statement period and are in order;
* duplicate-looking transactions (reported, never removed);
* extraction gaps (table pages without rows, unreadable pages).

Same-day ordering: some banks print a day's transactions in an order that differs
from posting order (the printed balances only chain in another order). When a
consistent posting order exists for that day, the day's transactions are
re-sequenced, each is flagged ``REORDERED_WITHIN_DAY`` and an issue is recorded.
Amounts and balances are never altered.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..config import AnalysisConfig
from ..schemas import (
    FAILED,
    REQUIRES_REVIEW,
    VERIFIED,
    ZERO,
    StatementMetadata,
    Transaction,
    ValidationCheck,
    ValidationIssue,
    ValidationReport,
)

MAX_LISTED_BREAKS = 50
MAX_REORDER_GROUP = 40


def _links(prev: Transaction, cur: Transaction, tol: Decimal) -> bool | None:
    if prev.balance is None or cur.balance is None:
        return None
    return abs(prev.balance + cur.credit - cur.debit - cur.balance) <= tol


def _order_day(start: Decimal, group: list[Transaction], tol: Decimal) -> list[Transaction] | None:
    """Depth-first search for an order in which ``group`` chains from ``start``."""
    if len(group) > MAX_REORDER_GROUP or any(t.balance is None for t in group):
        return None
    path: list[Transaction] = []
    used = [False] * len(group)

    def dfs(balance: Decimal) -> bool:
        if len(path) == len(group):
            return True
        tried: set[tuple] = set()
        for i, t in enumerate(group):
            if used[i]:
                continue
            key = (t.debit, t.credit, t.balance)
            if key in tried:
                continue
            tried.add(key)
            if abs(balance + t.credit - t.debit - t.balance) <= tol:
                used[i] = True
                path.append(t)
                if dfs(t.balance):
                    return True
                path.pop()
                used[i] = False
        return False

    return list(path) if dfs(start) else None


def resequence_same_day(txns: list[Transaction], opening: Decimal | None, tol: Decimal) -> list[list[str]]:
    """Re-sequence same-day groups whose printed order breaks the chain. Returns changed groups."""
    changed: list[list[str]] = []
    i = 0
    while i < len(txns):
        j = i
        while j + 1 < len(txns) and txns[j + 1].date == txns[i].date:
            j += 1
        group = txns[i:j + 1]
        before = txns[i - 1].balance if i > 0 else opening
        after = txns[j + 1] if j + 1 < len(txns) else None
        broken = any(_links(a, b, tol) is False for a, b in zip(group, group[1:]))
        if before is not None and group[0].balance is not None:
            broken = broken or abs(before + group[0].credit - group[0].debit - group[0].balance) > tol
        if after is not None:
            broken = broken or _links(group[-1], after, tol) is False
        if broken and len(group) > 1 and before is not None:
            ordered = _order_day(before, group, tol)
            if ordered is not None and (after is None or _links(ordered[-1], after, tol) is not False):
                if [t.id for t in ordered] != [t.id for t in group]:
                    txns[i:j + 1] = ordered
                    for t in ordered:
                        t.flags.append("REORDERED_WITHIN_DAY")
                    changed.append([t.id for t in ordered])
        i = j + 1
    return changed


def reconcile(
    txns: list[Transaction],
    meta: StatementMetadata,
    config: AnalysisConfig,
    *,
    sections: list | None = None,
    extraction: dict | None = None,
    pre_issues: list[ValidationIssue] | None = None,
) -> ValidationReport:
    tol = config.balance_tolerance
    report = ValidationReport()
    for issue in pre_issues or []:
        report.add(issue)
    extraction = extraction or {}
    printed = meta.printed_summary
    sections = sections or []
    multi_section = len(sections) > 1
    first_section_end = sections[1][0] if multi_section else None
    last_section_start = sections[-1][0] if multi_section else None

    if not txns:
        report.status = FAILED
        return report

    # ---- opening balance (printed in the first section, else derived)
    opening_printed = printed.opening_balance
    if opening_printed is not None and multi_section and printed.pages.get("opening_balance", 0) >= first_section_end:
        opening_printed = None
    first = txns[0]

    # ---- same-day ordering, then chain
    changed = resequence_same_day(txns, opening_printed if opening_printed is not None else (
        first.balance - first.credit + first.debit if first.balance is not None else None), tol)
    if changed:
        report.add(ValidationIssue(
            "SAME_DAY_ORDER_ADJUSTED", "INFO",
            f"On {len(changed)} day(s) the statement prints transactions in an order that differs from "
            "posting order; they were re-sequenced within the day so that the printed balances chain.",
            transaction_ids=[tid for group in changed[:10] for tid in group],
        ))
    first = txns[0]

    links = breaks = 0
    break_issues: list[ValidationIssue] = []
    missing_balance = [t.id for t in txns if t.balance is None]
    for prev, cur in zip(txns, txns[1:]):
        ok = _links(prev, cur, tol)
        if ok is None:
            continue
        links += 1
        if not ok:
            breaks += 1
            if len(break_issues) < MAX_LISTED_BREAKS:
                expected = prev.balance + cur.credit - cur.debit
                break_issues.append(ValidationIssue(
                    "BALANCE_CHAIN_BREAK", "ERROR",
                    "Running balance does not follow from the previous balance and this transaction.",
                    transaction_ids=[prev.id, cur.id], page=cur.source_page,
                    details={"expected_balance": expected, "printed_balance": cur.balance,
                             "difference": cur.balance - expected},
                ))
    report.balance_links_checked = links
    report.balance_chain_breaks = breaks
    for issue in break_issues:
        report.add(issue)
    if breaks > len(break_issues):
        report.add(ValidationIssue(
            "BALANCE_CHAIN_BREAK", "ERROR",
            f"{breaks - len(break_issues)} further balance-chain break(s) not listed individually.",
        ))
    report.checks.append(ValidationCheck(
        "running_balance_chain", (breaks == 0) if links else None, expected=0, actual=breaks,
        message=f"{links - breaks} of {links} consecutive balance links are consistent.",
    ))
    if missing_balance:
        report.add(ValidationIssue(
            "MISSING_BALANCE", "WARNING",
            f"{len(missing_balance)} transaction(s) have no running balance and cannot be chain-checked.",
            transaction_ids=missing_balance[:50],
        ))

    # ---- opening
    derived_opening = first.balance - first.credit + first.debit if first.balance is not None else None
    if opening_printed is not None:
        report.opening_balance, report.opening_balance_source = opening_printed, "statement"
        if derived_opening is not None:
            ok = abs(opening_printed - derived_opening) <= tol
            report.checks.append(ValidationCheck(
                "opening_balance_links_first_transaction", ok, expected=opening_printed, actual=derived_opening,
                message="Printed opening balance plus the first transaction equals its printed balance."
                if ok else "Printed opening balance does not link to the first transaction.",
            ))
            if not ok:
                report.add(ValidationIssue(
                    "OPENING_BALANCE_MISMATCH", "ERROR",
                    "The printed opening balance does not agree with the first transaction's balance; "
                    "transactions may be missing at the start of the statement.",
                    transaction_ids=[first.id], page=first.source_page,
                    details={"printed": opening_printed, "derived": derived_opening},
                ))
    elif derived_opening is not None:
        report.opening_balance, report.opening_balance_source = derived_opening, "derived"

    # ---- closing
    last_balance = next((t.balance for t in reversed(txns) if t.balance is not None), None)
    closing_printed = printed.closing_balance
    if closing_printed is not None and multi_section and printed.pages.get("closing_balance", 0) < last_section_start:
        closing_printed = None
    if closing_printed is not None:
        report.closing_balance, report.closing_balance_source = closing_printed, "statement"
        if last_balance is not None:
            ok = abs(closing_printed - last_balance) <= tol
            report.checks.append(ValidationCheck(
                "closing_balance_matches_last_transaction", ok, expected=closing_printed, actual=last_balance,
            ))
            if not ok:
                report.add(ValidationIssue(
                    "CLOSING_BALANCE_MISMATCH", "ERROR",
                    "The printed closing balance differs from the last transaction's balance; "
                    "transactions may be missing at the end of the statement.",
                    details={"printed": closing_printed, "last_transaction_balance": last_balance},
                ))
    elif last_balance is not None:
        report.closing_balance, report.closing_balance_source = last_balance, "last_transaction"

    total_debits = sum((t.debit for t in txns), ZERO)
    total_credits = sum((t.credit for t in txns), ZERO)
    if report.opening_balance is not None and report.closing_balance is not None:
        computed = report.opening_balance + total_credits - total_debits
        report.computed_closing_balance = computed
        ok = abs(computed - report.closing_balance) <= tol
        report.checks.append(ValidationCheck(
            "opening_plus_credits_minus_debits_equals_closing", ok,
            expected=report.closing_balance, actual=computed,
        ))
        if not ok:
            report.add(ValidationIssue(
                "TOTALS_DO_NOT_RECONCILE", "ERROR",
                "Opening balance plus total credits minus total debits does not equal the closing balance.",
                details={"computed_closing": computed, "closing": report.closing_balance,
                         "difference": report.closing_balance - computed},
            ))

    # ---- printed totals and counts
    comparisons = [
        ("printed_total_debits", printed.total_debits, total_debits),
        ("printed_total_credits", printed.total_credits, total_credits),
        ("printed_debit_count", printed.debit_count, sum(1 for t in txns if t.debit > 0)),
        ("printed_credit_count", printed.credit_count, sum(1 for t in txns if t.credit > 0)),
    ]
    if multi_section and any(c[1] is not None for c in comparisons):
        report.add(ValidationIssue(
            "MULTIPLE_STATEMENT_PERIODS", "INFO",
            f"The file contains {len(sections)} statement periods; printed period totals were not "
            "compared with whole-file totals. Balance continuity across periods was checked.",
            details={"periods": [[p[0], p[1]] for _, p in sections]},
        ))
    else:
        for name, expected, actual in comparisons:
            if expected is None:
                continue
            ok = (abs(expected - actual) <= tol) if isinstance(expected, Decimal) else expected == actual
            report.checks.append(ValidationCheck(name, ok, expected=expected, actual=actual))
            if not ok:
                report.add(ValidationIssue(
                    "PRINTED_SUMMARY_MISMATCH", "ERROR",
                    f"The statement's printed {name.replace('printed_', '').replace('_', ' ')} "
                    "does not match the extracted transactions.",
                    details={"printed": expected, "extracted": actual},
                ))

    # ---- dates
    if meta.period_start and meta.period_end:
        outside = [t.id for t in txns if not (meta.period_start <= t.date <= meta.period_end)]
        if outside:
            report.add(ValidationIssue(
                "DATE_OUTSIDE_PERIOD", "WARNING",
                f"{len(outside)} transaction(s) are dated outside the printed statement period.",
                transaction_ids=outside[:50],
            ))
    backwards = [cur.id for prev, cur in zip(txns, txns[1:]) if cur.date < prev.date]
    if backwards:
        report.add(ValidationIssue(
            "DATES_OUT_OF_ORDER", "WARNING",
            f"{len(backwards)} transaction(s) are dated earlier than the transaction before them.",
            transaction_ids=backwards[:50],
        ))

    # ---- duplicate-looking transactions (never removed)
    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for t in txns:
        groups[(t.date, t.debit, t.credit, t.narration_normalized or t.narration.upper())].append(t)
    dupes = [g for g in groups.values() if len(g) > 1]
    if dupes:
        same_balance = [g for g in dupes if len({t.balance for t in g}) < len(g)]
        # A row read twice would break the balance chain; with a fully consistent chain the repeats are genuine.
        chain_proves_genuine = links > 0 and breaks == 0 and not missing_balance
        suspicious = bool(same_balance) and not chain_proves_genuine
        report.add(ValidationIssue(
            "POSSIBLE_DUPLICATES", "WARNING" if suspicious else "INFO",
            f"{len(dupes)} group(s) of transactions share date, amount and narration. They were kept: "
            + ("some also share the same printed balance, which may indicate a repeated row."
               if suspicious else "the running balance confirms each is a separate transaction."),
            transaction_ids=[t.id for g in dupes[:10] for t in g],
        ))
        for g in dupes:
            for t in g:
                t.flags.append("DUPLICATE_LOOKING")

    # ---- extraction gaps
    gap_pages = extraction.get("table_pages_without_rows") or []
    if gap_pages:
        report.add(ValidationIssue(
            "PAGES_WITHOUT_TRANSACTIONS", "WARNING",
            f"{len(gap_pages)} page(s) inside the transaction range yielded no transactions.",
            details={"pages": gap_pages[:20]},
        ))
    unreadable = extraction.get("unreadable_pages") or []
    if unreadable:
        # A page with content we could not read means completeness cannot be established.
        report.add(ValidationIssue(
            "UNREADABLE_PAGES", "ERROR",
            f"{len(unreadable)} page(s) contain content without extractable text (e.g. scanned pages with "
            "OCR unavailable); transactions on them may be missing.",
            details={"pages": unreadable[:20]},
        ))
    damaged = extraction.get("damaged_pages") or []
    if damaged:
        report.add(ValidationIssue(
            "DAMAGED_PAGES", "ERROR",
            f"{len(damaged)} page(s) have damaged content that could not be fully decoded; transactions on "
            "them may be missing or incomplete.",
            details={"pages": damaged[:20]},
        ))
    blank = [p for p in (extraction.get("blank_pages") or []) if p not in damaged]
    if blank:
        report.add(ValidationIssue("BLANK_PAGES", "INFO", f"{len(blank)} blank page(s) were skipped.",
                                   details={"pages": blank[:20]}))
    shortfall = extraction.get("row_census_shortfall") or {}
    if shortfall:
        missing = sum(c[0] - c[1] for c in shortfall.values())
        report.add(ValidationIssue(
            "POSSIBLE_MISSED_ROWS", "ERROR",
            f"An independent scan found {missing} more transaction-like line(s) (date and amount) than were "
            "parsed; some transactions may be missing.",
            page=min(shortfall), details={"pages": {str(p): {"lines": c[0], "parsed": c[1]}
                                                    for p, c in sorted(shortfall.items())[:20]}},
        ))

    # ---- boundaries: without printed opening/closing balances, rows missing at the very start or
    # end of the statement cannot be detected from the balance chain.
    if report.opening_balance_source != "statement":
        report.add(ValidationIssue(
            "OPENING_BALANCE_NOT_PRINTED", "INFO",
            "No printed opening balance was found; the opening balance is derived from the first transaction, "
            "so rows missing before it would not be detectable.",
        ))
    if report.closing_balance_source != "statement":
        report.add(ValidationIssue(
            "CLOSING_BALANCE_NOT_PRINTED", "INFO",
            "No printed closing balance was found; rows missing after the last extracted transaction would not "
            "be detectable from the balances.",
        ))
    report.completeness = (
        "CONFIRMED_BY_PRINTED_BALANCES"
        if report.opening_balance_source == "statement" and report.closing_balance_source == "statement"
        else "CHAIN_ONLY"
    )

    # ---- status
    has_errors = any(i.severity == "ERROR" for i in report.issues)
    chain_ok = links > 0 and breaks == 0 and not missing_balance
    closing_ok = all(c.passed is not False for c in report.checks)
    report.reconciled = chain_ok and closing_ok
    if links and breaks / links > config.max_chain_break_ratio_for_review:
        report.status = FAILED
    elif report.reconciled and not has_errors:
        report.status = VERIFIED
    else:
        report.status = REQUIRES_REVIEW
    if not links:
        report.add(ValidationIssue(
            "NO_RUNNING_BALANCE", "WARNING",
            "The statement has no running balance column; transactions could not be reconciled.",
        ))
    return report
