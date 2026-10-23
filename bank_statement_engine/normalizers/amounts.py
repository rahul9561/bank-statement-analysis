"""Parsing of Indian currency amounts into ``Decimal``.

Handles lakh/crore grouping ("2,58,006.05"), western grouping, rupee markers
("₹", "Rs.", "INR"), trailing Cr/Dr markers, parentheses and leading signs.
Binary floating point is never used.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CURRENCY = re.compile(r"(₹|rs\.?|inr)", re.I)
# The integer part may be omitted (".01" is printed for one-paisa verification credits).
_NUMBER = re.compile(r"^[+-]?((\d{1,3}(,\d{2,3})+|\d+)(\.\d{1,2})?|\.\d{1,2})$")
_SUFFIX = re.compile(r"\s*\(?\s*(cr|dr)\.?\s*\)?\s*$", re.I)
_PLACEHOLDERS = {"", "-", "--", "—", "–", "nil", "na", "n/a", "0", "0.0", "0.00"}

TWO = Decimal("0.01")
#: Integer digits accepted as money (999 lakh crore). Longer digit runs are reference numbers.
MAX_INTEGER_DIGITS = 15


def parse_amount(text: str | None, *, allow_zero: bool = False, require_decimals: bool = False) -> Decimal | None:
    """Parse an unsigned amount; returns ``None`` for blanks and placeholders.

    ``require_decimals`` rejects bare integers, which avoids mistaking serial
    numbers or branch codes for amounts in strict contexts.
    """
    if text is None:
        return None
    raw = _CURRENCY.sub("", str(text)).strip().replace(" ", "")
    raw = _SUFFIX.sub("", raw)
    if raw.lower() in _PLACEHOLDERS and not (allow_zero and raw.startswith("0")):
        return None
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        raw, negative = raw[1:-1], True
    if not _NUMBER.match(raw):
        return None
    if require_decimals and "." not in raw:
        return None
    digits = raw.replace(",", "").lstrip("+-")
    if len(digits.split(".")[0]) > MAX_INTEGER_DIGITS:
        return None  # reference numbers, not money; also beyond Decimal quantize precision
    try:
        value = Decimal(raw.replace(",", ""))
        value = value.quantize(TWO)
    except InvalidOperation:
        return None
    if negative:
        value = -value
    return value


def parse_signed_balance(text: str | None) -> Decimal | None:
    """Parse a balance that may carry a Cr/Dr marker or a sign.

    ``"1,234.50 Dr"`` (overdrawn) -> ``Decimal("-1234.50")``.
    """
    if text is None:
        return None
    s = str(text).strip()
    m = _SUFFIX.search(_CURRENCY.sub("", s))
    marker = m.group(1).lower() if m else None
    value = parse_amount(s, allow_zero=True)
    if value is None:
        return None
    if marker == "dr" and value > 0:
        value = -value
    return value


def is_amount_token(text: str) -> bool:
    return parse_amount(text, allow_zero=True, require_decimals=True) is not None
