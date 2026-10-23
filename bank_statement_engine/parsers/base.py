"""Parser interface and intermediate data structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..extractors.base import PageContent, Word


@dataclass
class RawRow:
    """One transaction row as read from the page, before normalisation."""

    date: date
    page: int
    narration: str = ""
    value_date: date | None = None
    reference: str | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    #: Unsigned amount from a single "Amount" column (with ``drcr`` giving direction).
    amount: Decimal | None = None
    drcr: str | None = None
    balance: Decimal | None = None
    extra: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    source_text: str = ""


@dataclass
class ParsedStatement:
    rows: list[RawRow]
    parser: str
    page_count: int = 0
    #: Words outside the transaction table, for the first pages and the last pages only.
    context_words: dict[int, list[Word]] = field(default_factory=dict)
    header_found: bool = False
    layout_inferred: bool = False
    layout_columns: list[str] = field(default_factory=list)
    pages_with_rows: list[int] = field(default_factory=list)
    table_pages_without_rows: list[int] = field(default_factory=list)
    row_modes: dict[str, int] = field(default_factory=dict)
    #: page -> [transaction-like lines found by a layout-free scan, rows actually parsed]
    row_census: dict[int, list[int]] = field(default_factory=dict)
    #: Statement period printed on each page (where found). Files that concatenate
    #: several statements have more than one distinct period.
    page_periods: dict[int, tuple[date, date]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def sections(self) -> list[tuple[int, tuple[date, date]]]:
        """``(first_page, period)`` for each distinct consecutive statement period."""
        out: list[tuple[int, tuple[date, date]]] = []
        for page in sorted(self.page_periods):
            period = self.page_periods[page]
            if not out or out[-1][1] != period:
                out.append((page, period))
        return out


class StatementParser(ABC):
    """Base class for statement parsers.

    Bank-specific parsers subclass this (usually via ``GenericTableParser``)
    and declare the bank codes they handle. ``detect`` must return ``False``
    for layouts it does not recognise so the registry can fall back safely.
    """

    name = "base"
    bank_codes: tuple[str, ...] = ()

    @classmethod
    def detect(cls, first_page: PageContent, bank_code: str | None) -> bool:
        return bool(bank_code) and bank_code in cls.bank_codes

    @abstractmethod
    def parse(self, pages: Iterable[PageContent]) -> ParsedStatement:
        """Consume pages (once) and return the raw rows."""
