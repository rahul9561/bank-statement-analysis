"""Month-by-month aggregation."""

from __future__ import annotations

import calendar
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from ..schemas import Transaction
from .common import ZERO, is_external_income, mean, month_key, total


def analyze_monthly(
    txns: list[Transaction], eod: list[tuple[date, Decimal]], opening: Decimal | None, start: date, end: date
) -> list[dict]:
    months: OrderedDict[str, dict] = OrderedDict()
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months[f"{y:04d}-{m:02d}"] = {"txns": [], "eod": [], "year": y, "month": m}
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for t in txns:
        months.setdefault(month_key(t.date), {"txns": [], "eod": [], "year": t.date.year, "month": t.date.month})["txns"].append(t)
    for d, b in eod:
        if month_key(d) in months:
            months[month_key(d)]["eod"].append((d, b))

    out = []
    prev_closing = opening
    for key, data in months.items():
        ts: list[Transaction] = data["txns"]
        credits = total(t.credit for t in ts)
        debits = total(t.debit for t in ts)
        eod_vals = [b for _, b in data["eod"]]
        first_day = date(data["year"], data["month"], 1)
        last_day = date(data["year"], data["month"], calendar.monthrange(data["year"], data["month"])[1])
        partial = start > first_day or end < last_day
        closing = eod_vals[-1] if eod_vals else prev_closing
        out.append({
            "month": key,
            "label": first_day.strftime("%b %Y"),
            "partial_month": partial,
            "transaction_count": len(ts),
            "credit_count": sum(1 for t in ts if t.credit > 0),
            "debit_count": sum(1 for t in ts if t.debit > 0),
            "total_credits": credits,
            "total_debits": debits,
            "net_cash_flow": credits - debits,
            "external_credits": total(t.credit for t in ts if is_external_income(t)),
            "cash_withdrawals": total(t.debit for t in ts if t.category == "ATM_WITHDRAWAL"),
            "cash_deposits": total(t.credit for t in ts if t.category == "CASH_DEPOSIT"),
            "opening_balance": prev_closing,
            "closing_balance": closing,
            "average_daily_balance": mean(eod_vals),
            "min_eod_balance": min(eod_vals) if eod_vals else None,
            "max_eod_balance": max(eod_vals) if eod_vals else None,
        })
        prev_closing = closing
    return [m for m in out if m["transaction_count"] or m["average_daily_balance"] is not None] or out


def full_months(monthly: list[dict]) -> list[dict]:
    """Months fully inside the statement period (falls back to all months if fewer than 3)."""
    full = [m for m in monthly if not m["partial_month"]]
    return full if len(full) >= 3 else monthly


def monthly_total(monthly: list[dict], field: str) -> Decimal:
    return total(m[field] for m in monthly) if monthly else ZERO
