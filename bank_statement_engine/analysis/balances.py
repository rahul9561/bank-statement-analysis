"""Balance behaviour: end-of-day series, average daily balance, low-balance days.

The end-of-day (EOD) balance of a day is the printed balance after the day's last
transaction; days without transactions carry the previous EOD forward. Days
before the first transaction use the opening balance. ADB is the mean EOD over
calendar days and is reported as reliable only when the balance chain is verified.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..config import AnalysisConfig
from ..schemas import VERIFIED, Transaction, ValidationReport
from .common import mean, pct, q2


def eod_series(txns: list[Transaction], opening: Decimal | None, start: date, end: date) -> list[tuple[date, Decimal]]:
    by_day: dict[date, Decimal] = {}
    for t in txns:
        if t.balance is not None:
            by_day[t.date] = t.balance
    if not by_day:
        return []
    series: list[tuple[date, Decimal]] = []
    current = opening
    day = start
    while day <= end:
        if day in by_day:
            current = by_day[day]
        if current is not None:
            series.append((day, current))
        day += timedelta(days=1)
    return series


def analyze_balances(
    txns: list[Transaction], validation: ValidationReport, config: AnalysisConfig, start: date, end: date
) -> dict:
    series = eod_series(txns, validation.opening_balance, start, end)
    observed = [t.balance for t in txns if t.balance is not None]
    if not series:
        return {"available": False, "reason": "No running balances were extracted."}
    values = [b for _, b in series]
    low1, low2 = config.low_balance_threshold, config.low_balance_threshold_secondary
    below1 = [d for d, b in series if b < low1]
    below2 = [d for d, b in series if b < low2]
    negative = [d for d, b in series if b < 0]
    min_day = min(series, key=lambda s: s[1])
    max_day = max(series, key=lambda s: s[1])
    reliable = validation.status == VERIFIED
    return {
        "available": True,
        "reliable": reliable,
        "basis": "Printed running balances (end of day), carried forward on days without transactions.",
        "reliability_note": None if reliable else
        "The statement did not fully reconcile; balance statistics are estimates from printed balances.",
        "days_tracked": len(series),
        "days_with_transactions": len({t.date for t in txns}),
        "average_daily_balance": mean(values),
        "min_eod_balance": min_day[1],
        "min_eod_date": min_day[0],
        "max_eod_balance": max_day[1],
        "max_eod_date": max_day[0],
        "min_observed_balance": min(observed) if observed else None,
        "max_observed_balance": max(observed) if observed else None,
        "low_balance_threshold": low1,
        "days_below_low_threshold": len(below1),
        "pct_days_below_low_threshold": pct(Decimal(len(below1)), Decimal(len(series))),
        "secondary_threshold": low2,
        "days_below_secondary_threshold": len(below2),
        "pct_days_below_secondary_threshold": pct(Decimal(len(below2)), Decimal(len(series))),
        "negative_balance_days": len(negative),
        "first_negative_date": negative[0] if negative else None,
        "eod_series": [{"date": d, "balance": q2(b)} for d, b in series],
    }
