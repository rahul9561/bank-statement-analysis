"""Generic table parser for Indian bank statements.

Works from word geometry only (see ``layout.py``) so it applies to any statement
whose transaction table has a recognisable header with a date column, debit and
credit (or amount) columns and, ideally, a running balance.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable

from ..extractors.base import PageContent, Word
from ..normalizers.amounts import parse_amount, parse_signed_balance
from ..normalizers.dates import find_statement_period, parse_date
from . import layout as L
from .base import ParsedStatement, RawRow, StatementParser

_CONTEXT_HEAD_PAGES = 2
_CONTEXT_TAIL_PAGES = 2
_AMOUNT_PLACEHOLDERS = {"-", "--", "—", "–", "0", "0.0", "0.00", ".00", "nil", "na", "n/a"}
_DRCR = re.compile(r"\b(cr|dr)\b\.?", re.I)


def join_lines(parts: list[str]) -> str:
    """Join wrapped narration lines; avoid inserting spaces inside split tokens."""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and (out[-1] in "/-@:" or part[0] in "/-@"):
            out += part
        elif out:
            out += " " + part
        else:
            out = part
    return out


class GenericTableParser(StatementParser):
    name = "generic"

    def __init__(self, keep_source_text: bool = False):
        self.keep_source_text = keep_source_text

    # Hook for bank-specific subclasses: adjust a row after generic extraction.
    def postprocess_row(self, row: RawRow) -> RawRow:
        return row

    def parse(self, pages: Iterable[PageContent]) -> ParsedStatement:
        result = ParsedStatement(rows=[], parser=self.name)
        layout: L.TableLayout | None = None
        tail: deque[tuple[int, list[Word]]] = deque(maxlen=_CONTEXT_TAIL_PAGES)
        last_row: RawRow | None = None
        last_layout: L.TableLayout | None = None
        calibration_cache: dict[tuple, L.TableLayout] = {}

        for page in pages:
            result.page_count += 1
            lines = L.cluster_lines(page.words)
            # Layout-independent census of transaction-like lines, compared with parsed rows below.
            result.row_census[page.number] = [L.count_candidate_rows(lines), 0]
            rows_before = len(result.rows)
            found = L.find_header(lines, page.width)
            if found is not None:
                key = tuple(found.describe())
                calibrated, exact = L.calibrate_numeric_columns(found, lines)
                if exact:
                    calibration_cache[key] = calibrated
                elif key in calibration_cache:
                    cached = calibration_cache[key].scaled(page.width)
                    calibrated = L.TableLayout(cached.columns, found.header_top, found.header_bottom, page.width)
                found = calibrated
                layout = found
                result.header_found = True
                if not result.layout_columns:
                    result.layout_columns = found.describe()
                page_layout, top = found, found.header_bottom + 0.1
            elif layout is not None:
                page_layout, top = layout.scaled(page.width), 0.0
            else:
                inferred = L.infer_layout(lines, page.width)
                if inferred is None:
                    self._keep_context(result, tail, page.number, page.words)
                    self._record_period(result, page.number, page.words)
                    continue
                layout = inferred
                result.layout_inferred = True
                result.layout_columns = inferred.describe()
                result.warnings.append(
                    "No textual table header was found; columns were inferred from the data layout."
                )
                page_layout, top = inferred, inferred.header_top

            geoms, leading, mode = L.segment_rows(page, lines, page_layout, top)
            result.row_modes[mode] = result.row_modes.get(mode, 0) + 1
            used: set[int] = set()
            if leading and last_row is not None and last_layout is not None:
                extra = self._narration_from_lines(leading, page_layout)
                if extra:
                    last_row.narration = join_lines([last_row.narration, extra])
                    last_row.flags.append("NARRATION_CONTINUED_ACROSS_PAGE")
                used.update(id(w) for l in leading for w in l.words)
            for geom in geoms:
                row = self._build_row(geom, page_layout)
                used.update(id(w) for w in geom.words)
                if row is not None:
                    row = self.postprocess_row(row)
                    result.rows.append(row)
                    last_row = row
            last_layout = page_layout
            result.row_census[page.number][1] = len(result.rows) - rows_before
            if geoms:
                result.pages_with_rows.append(page.number)
            else:
                result.table_pages_without_rows.append(page.number)

            header_band = (found.header_top - 0.5, found.header_bottom + 0.5) if found else None
            context = [
                w for w in page.words
                if id(w) not in used and not (header_band and header_band[0] <= w.yc <= header_band[1])
            ]
            self._keep_context(result, tail, page.number, context)
            self._record_period(result, page.number, context)

        for number, words in tail:
            result.context_words[number] = words
        # Pages after the last page with transactions are trailing summaries, not gaps.
        if result.pages_with_rows:
            last = result.pages_with_rows[-1]
            result.table_pages_without_rows = [p for p in result.table_pages_without_rows if p < last]
        # Trailer pages (notices, glossary) that end one statement period right before the next
        # period starts are not gaps in a file that concatenates several statements.
        section_starts = {page for page, _ in result.sections()[1:]}
        result.table_pages_without_rows = [p for p in result.table_pages_without_rows if p + 1 not in section_starts]
        return result

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _keep_context(result: ParsedStatement, tail: deque, number: int, words: list[Word]) -> None:
        if number <= _CONTEXT_HEAD_PAGES:
            result.context_words[number] = words
        else:
            tail.append((number, words))

    @staticmethod
    def _record_period(result: ParsedStatement, number: int, words: list[Word]) -> None:
        if not words:
            return
        period = find_statement_period(L.context_text(words))
        if period is not None:
            result.page_periods[number] = period

    @staticmethod
    def _narration_from_lines(lines: list[L.Line], layout: L.TableLayout) -> str:
        parts = []
        for line in lines:
            words = [w.text for w in line.words if (c := layout.column_for(w)) and c.kind == L.NARRATION]
            parts.append(" ".join(words))
        return join_lines(parts)

    def _build_row(self, geom: L.RowGeometry, layout: L.TableLayout) -> RawRow | None:
        txn_date = L.anchor_date(geom.anchor, layout)
        if txn_date is None:
            return None
        cells: dict[str, list[list[str]]] = {}
        labels: dict[str, str] = {}
        for line in geom.lines:
            per_line: dict[str, list[str]] = {}
            for w in line.words:
                col = layout.column_for(w)
                kind = col.kind if col else L.OTHER
                key = kind if kind != L.OTHER else f"other:{col.label if col else '?'}"
                labels[key] = col.label if col else "?"
                per_line.setdefault(key, []).append(w.text)
            for key, words in per_line.items():
                cells.setdefault(key, []).append(words)

        def text(kind: str) -> str:
            return " ".join(" ".join(ws) for ws in cells.get(kind, []))

        row = RawRow(date=txn_date, page=geom.page)
        row.narration = join_lines([" ".join(ws) for ws in cells.get(L.NARRATION, [])])
        vd = text(L.VALUE_DATE)
        row.value_date = parse_date(vd) or (parse_date(vd.split(" ")[0]) if vd else None)
        ref = text(L.REF).strip()
        row.reference = ref if ref and ref not in {"-", "--"} else None

        row.debit = self._amount(text(L.DEBIT), row, "DEBIT")
        row.credit = self._amount(text(L.CREDIT), row, "CREDIT")
        if layout.has(L.AMOUNT):
            amount_text = text(L.AMOUNT)
            row.amount = self._amount(amount_text, row, "AMOUNT")
            marker = _DRCR.search(text(L.DRCR) or amount_text)
            row.drcr = marker.group(1).upper() if marker else None
        row.balance = self._balance(text(L.BALANCE), row)

        for key, label in labels.items():
            if key.startswith("other:"):
                value = text(key).strip()
                if value:
                    row.extra[label] = value
        if not row.narration:
            # Layout without a narration column: keep any free text from other columns.
            row.narration = " ".join(v for v in row.extra.values() if not parse_amount(v, allow_zero=True))
        if self.keep_source_text:
            row.source_text = " | ".join(l.text for l in geom.lines)
        if row.debit is None and row.credit is None and row.amount is None:
            row.flags.append("NO_AMOUNT")
        return row

    @staticmethod
    def _amount(text: str, row: RawRow, label: str):
        text = text.strip()
        if not text:
            return None
        value = parse_amount(text)
        if value is not None:
            return value
        tokens = [parse_amount(t) for t in text.split()]
        amounts = [t for t in tokens if t is not None]
        if len(amounts) > 1:
            row.flags.append(f"MULTIPLE_VALUES_IN_{label}")
        if not amounts and text.lower() not in _AMOUNT_PLACEHOLDERS:
            # Something is printed in the amount column but could not be read: never treat as blank.
            row.flags.append(f"UNPARSED_{label}")
        return amounts[0] if amounts else None

    @staticmethod
    def _balance(text: str, row: RawRow):
        text = text.strip()
        if not text:
            return None
        value = parse_signed_balance(text)
        if value is not None:
            return value
        # e.g. "₹ 31,644.64" split oddly, or trailing column noise.
        for i in range(len(text.split()), 0, -1):
            candidate = " ".join(text.split()[:i])
            value = parse_signed_balance(candidate)
            if value is not None:
                return value
        row.flags.append("UNPARSED_BALANCE")
        return None
