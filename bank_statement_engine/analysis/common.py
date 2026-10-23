"""Small Decimal helpers shared by the analysis modules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
TWO = Decimal("0.01")
HUNDRED = Decimal("100")

#: Credit categories that are not income: own money moving or returning, or borrowed money.
NON_INCOME = frozenset({"SELF_TRANSFER", "REFUND_REVERSAL", "LOAN_DISBURSAL", "INVESTMENT_INCOME"})


def is_external_income(t) -> bool:
    """Credit counted as external inflow: not own money, not a refund/loan/redemption, and not an
    instrument that was later returned unpaid (a bounced cheque deposit is not income)."""
    return (t.credit > 0 and not t.is_self_transfer and t.category not in NON_INCOME
            and "RETURNED_UNPAID" not in t.flags)


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def total(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)


def pct(part: Decimal, whole: Decimal) -> Decimal | None:
    if not whole:
        return None
    return q2(Decimal(part) * HUNDRED / Decimal(whole))


def mean(values: list[Decimal]) -> Decimal | None:
    return q2(total(values) / len(values)) if values else None


def median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else q2((s[mid - 1] + s[mid]) / 2)


def cv_pct(values: list[Decimal]) -> Decimal | None:
    """Coefficient of variation (population std / mean) in percent."""
    if len(values) < 2:
        return None
    m = total(values) / len(values)
    if not m:
        return None
    var = sum(((v - m) ** 2 for v in values), Decimal(0)) / len(values)
    return q2(var.sqrt() / abs(m) * HUNDRED)


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def months_between(start: date, end: date) -> Decimal:
    """Approximate months covered (inclusive day count / 30.44), at least one decimal place."""
    days = (end - start).days + 1
    return (Decimal(days) / Decimal("30.44")).quantize(Decimal("0.1"))


def txn_ref(t) -> dict:
    """Compact evidence reference to a transaction (no narration, to minimise personal data)."""
    return {"transaction_id": t.id, "date": t.date, "amount": t.amount, "type": t.transaction_type}


_KEY_NOISE = re.compile(r"[\d/\-*:.,@_#()]+")


def group_key(t) -> str:
    """Grouping key for a counterparty: extracted name, else the narration's leading words."""
    if t.counterparty:
        return t.counterparty
    words = [w for w in _KEY_NOISE.sub(" ", t.narration_normalized or "").split() if len(w) > 1]
    return " ".join(words[:4]) or "UNSPECIFIED"


def amount_clusters(txns: list, tolerance_pct: Decimal) -> list[list]:
    """Group transactions whose amounts lie within ``tolerance_pct`` of the cluster's first amount."""
    clusters: list[list] = []
    for t in sorted(txns, key=lambda t: t.amount):
        if clusters:
            base = clusters[-1][0].amount
            if base and abs(t.amount - base) * HUNDRED / base <= tolerance_pct:
                clusters[-1].append(t)
                continue
        clusters.append([t])
    return clusters


def recurrence(txns: list) -> dict:
    """Summary of how a set of transactions recurs across months."""
    months = sorted({month_key(t.date) for t in txns})
    amounts = [t.amount for t in txns]
    days = sorted(t.date.day for t in txns)
    return {
        "occurrences": len(txns),
        "months": months,
        "month_count": len(months),
        "total": total(amounts),
        "median_amount": median(amounts),
        "min_amount": min(amounts),
        "max_amount": max(amounts),
        "amount_cv_pct": cv_pct(amounts),
        "typical_day_of_month": days[len(days) // 2],
        "first_date": min(t.date for t in txns),
        "last_date": max(t.date for t in txns),
    }
