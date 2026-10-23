"""Parsing of the date formats found on Indian bank statements.

Indian statements are day-first. Supported forms include::

    21/09/2025  21-09-2025  21.09.2025  21/09/25  21-Sep-2025  21 Sep 2025
    21-SEP-25   21Sep2025   2025-09-21 (ISO)      Sep 21, 2025
"""

from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)"), "dmy"),
    (re.compile(r"(?<!\d)(\d{1,2})[\s\-/.]*" + _MON + r"[\s\-/.,]*(\d{4}|\d{2})(?!\d)", re.I), "dMy"),
    (re.compile(_MON + r"\s+(\d{1,2}),?\s+(\d{4})(?!\d)", re.I), "Mdy"),
]


def _year(text: str) -> int:
    y = int(text)
    if len(text) == 2:
        y += 2000 if y < 70 else 1900
    return y


def _build(kind: str, groups: tuple[str, ...]) -> date | None:
    try:
        if kind == "ymd":
            return date(int(groups[0]), int(groups[1]), int(groups[2]))
        if kind == "dmy":
            return date(_year(groups[2]), int(groups[1]), int(groups[0]))
        if kind == "dMy":
            return date(_year(groups[2]), MONTHS[groups[1].lower()[:3]], int(groups[0]))
        if kind == "Mdy":
            return date(int(groups[2]), MONTHS[groups[0].lower()[:3]], int(groups[1]))
    except (ValueError, KeyError):
        return None
    return None


def parse_date(text: str | None, *, strict: bool = True) -> date | None:
    """Parse a date from ``text``.

    With ``strict=True`` the date must make up the whole string (ignoring
    surrounding whitespace and punctuation); otherwise the first date found is returned.
    """
    if not text:
        return None
    candidate = text.strip().strip(",;:|")
    for pattern, kind in _PATTERNS:
        m = pattern.fullmatch(candidate) if strict else pattern.search(candidate)
        if m:
            d = _build(kind, m.groups())
            if d is not None and 1900 <= d.year <= 2100:
                return d
    return None


def find_dates(text: str) -> list[tuple[date, int, int]]:
    """Return every date in ``text`` as ``(date, start, end)`` ordered by position."""
    found: list[tuple[date, int, int]] = []
    taken: list[tuple[int, int]] = []
    for pattern, kind in _PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            d = _build(kind, m.groups())
            if d is not None and 1900 <= d.year <= 2100:
                found.append((d, m.start(), m.end()))
                taken.append((m.start(), m.end()))
    return sorted(found, key=lambda x: x[1])


_PERIOD_HINT = re.compile(r"(period|from|statement\s+for|between)", re.I)
_PERIOD_SEP = re.compile(r"^[\s:]*(?:to|till|until|-|–|—|and)[\s:]*$", re.I)


def find_statement_period(text: str) -> tuple[date, date] | None:
    """Find a ``<date> to <date>`` statement period in header text."""
    dates = find_dates(text)
    best: tuple[date, date] | None = None
    for (d1, s1, e1), (d2, s2, _e2) in zip(dates, dates[1:]):
        between = text[e1:s2]
        if len(between) > 12 or not _PERIOD_SEP.match(between) or d2 < d1:
            continue
        context = text[max(0, s1 - 80):s1]
        if _PERIOD_HINT.search(context):
            return d1, d2
        if best is None:
            best = (d1, d2)
    return best
