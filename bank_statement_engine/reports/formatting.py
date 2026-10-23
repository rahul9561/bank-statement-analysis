"""Display formatting (Indian number grouping, compact lakh/crore, dates)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

NA = "Not available"


def _group_indian(integer: str) -> str:
    if len(integer) <= 3:
        return integer
    head, tail = integer[:-3], integer[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(value, symbol: str = "₹", decimals: int = 2, na: str = NA) -> str:
    """``Decimal('1234567.5')`` -> ``'₹12,34,567.50'``."""
    if value is None:
        return na
    d = Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    text = f"{abs(d):.{decimals}f}"
    integer, _, frac = text.partition(".")
    return f"{sign}{symbol}{_group_indian(integer)}" + (f".{frac}" if decimals else "")


def inr0(value, symbol: str = "₹", na: str = NA) -> str:
    return inr(value, symbol, 0, na)


def inr_compact(value, symbol: str = "₹", na: str = NA) -> str:
    """Compact Indian notation: ₹9.11 L, ₹1.25 Cr; small values in full rupees."""
    if value is None:
        return na
    d = Decimal(str(value))
    a = abs(d)
    sign = "-" if d < 0 else ""
    if a >= Decimal("10000000"):
        return f"{sign}{symbol}{(a / Decimal('10000000')).quantize(Decimal('0.01'))} Cr"
    if a >= Decimal("100000"):
        return f"{sign}{symbol}{(a / Decimal('100000')).quantize(Decimal('0.01'))} L"
    return inr0(d, symbol)


def pct(value, na: str = NA, decimals: int = 1) -> str:
    if value is None:
        return na
    return f"{Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)}%"


def fdate(value, na: str = NA) -> str:
    if value is None:
        return na
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return value.strftime("%d-%m-%Y")


def fdatetime(value: datetime, label: str = "") -> str:
    return value.strftime("%d %B %Y, %I:%M %p") + (f" {label}" if label else "")


def title_case(code: str | None) -> str:
    return (code or "").replace("_", " ").title()
