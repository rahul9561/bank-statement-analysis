"""Standard Chartered Bank (India) account statement parser.

Layout, as printed on every page of the statement::

    <holder name>                     BRANCH          : <branch>
                                      STATEMENT DATE  : 01 Mar 2026 To 26 Nov 2026
                                      ACCOUNT TYPE / ACCOUNT NO / NOMINEE ...
    BRANCH ADDRESS : ... IFSC: SCBL0XXXXXX  MICR CODE: ...
    Date | Value Date | Description | Cheque | Deposit | Withdrawal | Balance
         Balance Brought Forward                                      <balance>
    Mar 02  Mar 02  UPI/.../                       8,000.00          714,425.61
                    <narration continues on following lines>
    ...
    Total                                  <deposits>  <withdrawals>
    Page n of m

Facts the generic parser cannot handle, and how they are treated here:

* dates are printed **without a year** (``Mar 02``): the year is taken from the
  printed statement period, choosing the candidate nearest the previous row so a
  December → January boundary moves to the next year;
* the posting **Date is printed only when it changes** (and on each page's first
  row): a blank Date cell repeats the posting date of the row above. The Value
  Date is printed on every row;
* **Deposit is left of Withdrawal**: amount columns are taken from the header
  positions (amounts are right-aligned, so they are assigned by their right edge);
  the column order is never assumed;
* each page starts with ``Balance Brought Forward`` (page 1: the opening
  balance) and the last page ends with a ``Total`` row (printed totals). Some
  pages omit the brought-forward row.

Only the layout is assumed — no transaction data. ``detect`` returns ``False``
for any Standard Chartered document whose table header or period differs, so
those fall back to the generic parser.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from ..extractors.base import PageContent, Word
from ..normalizers.amounts import is_amount_token, parse_signed_balance
from ..normalizers.dates import MONTHS, find_statement_period, parse_date
from . import layout as L
from .base import ParsedStatement, RawRow
from .generic import GenericTableParser, join_lines

_MONTH_DAY = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})$", re.I)
_PAGE_FOOTER = re.compile(r"^page\s+\d+\s+of\s+\d+\b", re.I)
_DISCLAIMER = re.compile(r"^bank\s+deposits\s+are\s+covered", re.I)
_BROUGHT_FORWARD = re.compile(r"\bbalance\s+brought\s+forward\b", re.I)

#: Header label -> column kind. Every key must be present on the header line.
_AMOUNT_HEADERS = {"deposit": L.CREDIT, "withdrawal": L.DEBIT, "balance": L.BALANCE}
_TEXT_HEADERS = ("date", "value", "description", "cheque")


@dataclass(frozen=True)
class _Columns:
    """Column boundaries (x, PDF points) derived from one page's header line."""

    header_top: float
    header_bottom: float
    date_right: float       # Date | Value Date
    value_right: float      # Value Date | Description (dates end here)
    text_right: float       # Description | Cheque
    ref_right: float        # Cheque | first amount column (by right edge)
    amounts: tuple[tuple[str, float], ...]  # (kind, right boundary) left to right

    def amount_kind(self, word: Word) -> str | None:
        """Column of a word right of the narration, by its right edge (amounts are right-aligned)."""
        if word.x1 <= self.ref_right:
            return L.REF
        for kind, right in self.amounts:
            if word.x1 <= right:
                return kind
        return self.amounts[-1][0]


def _find_columns(lines: list[L.Line], h: float) -> _Columns | None:
    for idx, line in enumerate(lines):
        first: dict[str, Word] = {}
        for w in line.words:
            first.setdefault(w.text.strip(":").lower(), w)
        if not all(k in first for k in (*_TEXT_HEADERS, *_AMOUNT_HEADERS)):
            continue
        d, v, desc, chq = (first[k] for k in _TEXT_HEADERS)
        amount_hdrs = sorted(((first[k], kind) for k, kind in _AMOUNT_HEADERS.items()), key=lambda p: p[0].x0)
        if not (d.x1 < v.x0 < desc.x0 < chq.x0 < amount_hdrs[0][0].x0) or amount_hdrs[-1][1] != L.BALANCE:
            return None
        bounds = []
        for (w, kind), (nxt, _) in zip(amount_hdrs, amount_hdrs[1:]):
            bounds.append((kind, (w.x1 + nxt.x0) / 2))
        bounds.append((amount_hdrs[-1][1], float("inf")))
        bottom = line.bottom
        # "Value Date" wraps: "Date" sits on the next line under "Value".
        if idx + 1 < len(lines):
            nxt_line = lines[idx + 1]
            if nxt_line.top - bottom < h and all(w.text.lower() in {"date", "no", "no."} for w in nxt_line.words):
                bottom = nxt_line.bottom
        return _Columns(
            header_top=line.top,
            header_bottom=bottom,
            date_right=(d.x1 + v.x0) / 2,
            value_right=v.x1 + 0.75 * h,
            text_right=chq.x0 - 0.75 * h,
            ref_right=(chq.x1 + amount_hdrs[0][0].x0) / 2,
            amounts=tuple(bounds),
        )
    return None


def _month_day(text: str) -> tuple[int, int] | None:
    m = _MONTH_DAY.match(text.strip())
    if not m:
        return None
    month, day = MONTHS[m.group(1).lower()[:3]], int(m.group(2))
    return (month, day) if 1 <= day <= 31 else None


def resolve_year(month: int, day: int, reference: date, period: tuple[date, date]) -> date | None:
    """The calendar date for a year-less ``month/day`` nearest ``reference``.

    Candidates span the statement period's years (plus one either side, for value
    dates just outside the period); the nearest to ``reference`` wins, which
    keeps rows in order across a December → January boundary.
    """
    best: date | None = None
    for year in range(period[0].year - 1, period[1].year + 2):
        try:
            candidate = date(year, month, day)
        except ValueError:  # e.g. 29 Feb in a non-leap year
            continue
        if best is None or abs((candidate - reference).days) < abs((best - reference).days):
            best = candidate
    return best


class StandardCharteredParser(GenericTableParser):
    name = "standard_chartered"
    bank_codes = ("SCB",)

    @classmethod
    def detect(cls, first_page: PageContent, bank_code: str | None) -> bool:
        if bank_code not in cls.bank_codes:
            return False
        lines = L.cluster_lines(first_page.words)
        cols = _find_columns(lines, L.median_word_height(first_page.words))
        if cols is None:
            return False
        above = [w for w in first_page.words if w.yc < cols.header_top]
        return find_statement_period(L.context_text(above)) is not None

    def parse(self, pages: Iterable[PageContent]) -> ParsedStatement:
        result = ParsedStatement(rows=[], parser=self.name)
        tail: deque[tuple[int, list[Word]]] = deque(maxlen=2)
        period: tuple[date, date] | None = None
        cols: _Columns | None = None
        last_row: RawRow | None = None
        posting_date: date | None = None  # last printed posting date (carried over blank Date cells)

        for page in pages:
            result.page_count += 1
            h = L.median_word_height(page.words)
            lines = L.cluster_lines(page.words)
            found = _find_columns(lines, h)
            if found is not None:
                cols = found
                result.header_found = True
            if cols is None:
                result.row_census[page.number] = [0, 0]
                self._keep_context(result, tail, page.number, page.words)
                self._record_period(result, page.number, page.words)
                continue
            if not result.layout_columns:
                result.layout_columns = [L.DATE, L.VALUE_DATE, L.NARRATION, L.REF, *(k for k, _ in cols.amounts)]

            above = [w for w in page.words if w.yc < cols.header_top]
            page_period = find_statement_period(L.context_text(above))
            if page_period is not None:
                period = page_period
                result.page_periods[page.number] = page_period
                if not result.branch:
                    result.branch = _branch(above)

            body: list[L.Line] = []
            footer_top = float("inf")
            for line in lines:
                if line.top <= cols.header_bottom + 0.1:
                    continue
                if _PAGE_FOOTER.match(line.text) or _DISCLAIMER.match(line.text):
                    footer_top = min(footer_top, line.top)
                if line.top < footer_top:
                    body.append(line)

            used: set[int] = set()
            groups: list[list[L.Line]] = []
            leading: list[L.Line] = []
            census = 0
            for line in body:
                kinds = self._classify(line, cols)
                has_date = bool(kinds.get(L.DATE) or kinds.get(L.VALUE_DATE))
                amount_words = [w for k in (L.DEBIT, L.CREDIT, L.BALANCE) for w in kinds.get(k, [])
                                if is_amount_token(w.text)]
                if not has_date and _BROUGHT_FORWARD.search(line.text):
                    self._brought_forward(result, page.number, kinds, last_row)
                    continue  # stays in context words (printed figure)
                if not has_date and line.words[0].text.lower().strip(":") == "total" and amount_words:
                    self._totals(result, page.number, kinds)
                    continue
                if amount_words:
                    census += 1
                if has_date or amount_words:
                    groups.append([line])
                elif groups:
                    groups[-1].append(line)
                else:
                    leading.append(line)

            result.row_modes["first_line_anchor"] = result.row_modes.get("first_line_anchor", 0) + 1
            if leading:
                extra = join_lines([" ".join(w.text for w in self._classify(l, cols).get(L.NARRATION, []))
                                    for l in leading])
                if last_row is not None and extra:
                    last_row.narration = join_lines([last_row.narration, extra])
                    if "NARRATION_CONTINUED_ACROSS_PAGE" not in last_row.flags:
                        last_row.flags.append("NARRATION_CONTINUED_ACROSS_PAGE")
                    used.update(id(w) for l in leading for w in l.words)
                elif extra:
                    result.warnings.append(
                        f"Page {page.number}: text above the first transaction could not be attached to a row."
                    )

            rows_before = len(result.rows)
            for group in groups:
                row, date_printed = self._build(group, cols, page.number, period, posting_date, result)
                if row is None:
                    continue
                used.update(id(w) for l in group for w in l.words)
                if date_printed or posting_date is None:
                    posting_date = row.date
                row = self.postprocess_row(row)
                result.rows.append(row)
                last_row = row

            result.row_census[page.number] = [census, len(result.rows) - rows_before]
            if len(result.rows) > rows_before:
                result.pages_with_rows.append(page.number)
            else:
                result.table_pages_without_rows.append(page.number)

            header_band = (cols.header_top - 0.5, cols.header_bottom + 0.5) if found else None
            context = [w for w in page.words
                       if id(w) not in used and not (header_band and header_band[0] <= w.yc <= header_band[1])]
            self._keep_context(result, tail, page.number, context)

        for number, words in tail:
            result.context_words[number] = words
        if result.pages_with_rows:
            last = result.pages_with_rows[-1]
            result.table_pages_without_rows = [p for p in result.table_pages_without_rows if p < last]
        return result

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _classify(line: L.Line, cols: _Columns) -> dict[str, list[Word]]:
        out: dict[str, list[Word]] = {}
        for w in line.words:
            if w.x1 <= cols.value_right:
                kind = L.DATE if w.xc < cols.date_right else L.VALUE_DATE
            elif w.x0 < cols.text_right:
                kind = L.NARRATION
            else:
                kind = cols.amount_kind(w)
            out.setdefault(kind, []).append(w)
        return out

    def _build(
        self,
        group: list[L.Line],
        cols: _Columns,
        page: int,
        period: tuple[date, date] | None,
        posting_date: date | None,
        result: ParsedStatement,
    ) -> tuple[RawRow | None, bool]:
        """Build one row; also return whether its posting date was printed (not carried)."""
        anchor = self._classify(group[0], cols)
        cells: dict[str, list[list[str]]] = {}
        for line in group:
            for kind, words in self._classify(line, cols).items():
                cells.setdefault(kind, []).append([w.text for w in words])

        def text(kind: str) -> str:
            return " ".join(" ".join(ws) for ws in cells.get(kind, []))

        date_text = " ".join(w.text for w in anchor.get(L.DATE, []))
        value_text = " ".join(w.text for w in anchor.get(L.VALUE_DATE, []))
        reference = posting_date or (period[0] if period else None)
        printed = self._date(date_text, reference, period)
        flags: list[str] = []
        if printed is not None:
            txn_date = printed
        elif posting_date is not None:
            txn_date = posting_date  # blank Date cell: same posting date as the row above
        else:
            txn_date = None
        value_date = self._date(value_text, txn_date or reference, period)
        if txn_date is None and value_date is not None:
            txn_date = value_date
            flags.append("DATE_FROM_VALUE_DATE")
        if date_text and printed is None:
            flags.append("UNPARSED_DATE")
        if value_text and value_date is None:
            flags.append("UNPARSED_VALUE_DATE")
        if txn_date is None:
            result.warnings.append(
                f"Page {page}: a row with amounts but no readable date was not converted: "
                f"{' | '.join(l.text for l in group)[:120]}"
            )
            return None, False

        row = RawRow(date=txn_date, page=page, value_date=value_date, flags=flags)
        row.narration = join_lines([" ".join(ws) for ws in cells.get(L.NARRATION, [])])
        ref = text(L.REF).strip()
        row.reference = ref if ref and ref not in {"-", "--"} else None
        row.credit = self._amount(text(L.CREDIT), row, "CREDIT")
        row.debit = self._amount(text(L.DEBIT), row, "DEBIT")
        row.balance = self._balance(text(L.BALANCE), row)
        if row.debit is None and row.credit is None:
            row.flags.append("NO_AMOUNT")
        if self.keep_source_text:
            row.source_text = " | ".join(l.text for l in group)
        return row, printed is not None

    @staticmethod
    def _date(text: str, reference: date | None, period: tuple[date, date] | None) -> date | None:
        text = text.strip()
        if not text:
            return None
        full = parse_date(text)
        if full is not None:
            return full
        md = _month_day(text)
        if md is None or reference is None or period is None:
            return None
        return resolve_year(md[0], md[1], reference, period)

    @staticmethod
    def _brought_forward(result: ParsedStatement, page: int, kinds: dict[str, list[Word]], last_row: RawRow | None):
        value = parse_signed_balance(" ".join(w.text for w in kinds.get(L.BALANCE, [])))
        if value is None:
            result.warnings.append(f"Page {page}: the balance brought forward could not be read.")
            return
        if last_row is None:
            result.printed_figures.setdefault("opening_balance", (value, page))
        elif last_row.balance is not None and last_row.balance != value:
            result.warnings.append(
                f"Page {page}: balance brought forward {value} differs from the previous row's balance "
                f"{last_row.balance}."
            )

    @staticmethod
    def _totals(result: ParsedStatement, page: int, kinds: dict[str, list[Word]]) -> None:
        for kind, name in ((L.CREDIT, "total_credits"), (L.DEBIT, "total_debits")):
            value = parse_signed_balance(" ".join(w.text for w in kinds.get(kind, [])))
            if value is not None:
                result.printed_figures[name] = (value, page)


def _branch(words: list[Word]) -> str | None:
    """Value of the ``BRANCH : <name>`` label in the page header (not ``BRANCH ADDRESS``)."""
    for line in L.cluster_lines(words):
        ws = line.words
        for i, w in enumerate(ws[:-2]):
            if w.text.upper() == "BRANCH" and ws[i + 1].text == ":":
                value = " ".join(x.text for x in ws[i + 2:]).strip()
                return value or None
    return None
