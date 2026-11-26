"""Standard Chartered Bank (India) statement parser.

Synthetic statements reproduce the Standard Chartered layout (year-less ``Mon DD``
dates, posting date printed only when it changes, Deposit left of Withdrawal,
per-page "Balance Brought Forward", closing "Total" row) with fictional data only.
The real sample in ``input/`` is checked with aggregate, non-identifying assertions.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest
from reportlab.pdfgen.canvas import Canvas

from conftest import INPUT_DIR
from helpers import money

from bank_statement_engine.extractors.base import PageContent, Word
from bank_statement_engine.parsers.generic import GenericTableParser
from bank_statement_engine.parsers.registry import registry
from bank_statement_engine.parsers.standard_chartered import StandardCharteredParser, resolve_year

D = Decimal
PAGE_W, PAGE_H = 612, 792
FONT = 7
SC_SAMPLE = INPUT_DIR / "sc.bank.pdf"


# ---------------------------------------------------------------- synthetic statement


@dataclass
class Txn:
    posting: str | None  # printed Date cell ("Dec 20") or None when blank (same as above)
    value: str           # printed Value Date cell
    narration: list[str]
    debit: Decimal = D("0")
    credit: Decimal = D("0")
    cheque: str = ""
    #: Narration lines printed at the top of the *next* page (row split by a page break).
    spill: list[str] = field(default_factory=list)


OPENING = D("50000.00")
PERIOD = "20 Dec 2025 To 10 Jan 2026"

PAGES: list[list[Txn]] = [
    [
        Txn("Dec 20", "Dec 20", ["UPI/100000000001/", "FICTIONAL GROCERS/grocer@okbank/", "PAYMENT FROM APP/"],
            debit=D("1200.00")),
        Txn(None, "Dec 20", ["NEFT/N0000001/EXAMPLE EMPLOYER PVT", "LTD/SALARY DEC"], credit=D("25000.00")),
        Txn("Dec 22", "Dec 21", ["CHQ PAID/TEST CLEARING"], debit=D("3000.00"), cheque="000123"),
        Txn(None, "Dec 22", ["UPI/100000000002/SAMPLE CAFE/"], debit=D("10.50"),
            spill=["cafe@okbank/COFFEE/"]),
    ],
    [
        Txn("Dec 31", "Dec 31", ["INTEREST CREDIT"], credit=D("12.34")),
        Txn(None, "Dec 31", ["IMPS/200000000001/DEMO", "TRADERS/INVOICE 77"], debit=D("100000.00")),
        Txn("Jan 02", "Jan 01", ["NACH/DEMO FINANCE LTD/EMI"], debit=D("5000.00")),
    ],
    [
        Txn("Jan 02", "Jan 02", ["UPI/300000000001/REFUND/", "EXAMPLE SHOP/"], credit=D("78000.00")),
        Txn("Jan 05", "Jan 05", ["ATM WDL/TEST ATM"], debit=D("2000.00")),
    ],
]


def all_txns() -> list[Txn]:
    return [t for page in PAGES for t in page]


def build_sc_pdf(swap_amount_columns: bool = False) -> bytes:
    """Render PAGES in the Standard Chartered layout (coordinates as on the real statement)."""
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setFont("Helvetica", FONT)

    def put(x: float, top: float, text: str, right: bool = False) -> None:
        y = PAGE_H - top - FONT
        (c.drawRightString if right else c.drawString)(x, y, text)

    # Amount columns: (header x0, right edge of amounts)
    first, second = ("Deposit", "Withdrawal") if not swap_amount_columns else ("Withdrawal", "Deposit")
    right_edge = {first: 436.0, second: 502.8}
    credit_right, debit_right = right_edge["Deposit"], right_edge["Withdrawal"]

    balance = OPENING
    total_c = total_d = D("0")
    n_pages = len(PAGES)
    spill: list[str] = []
    for number, page in enumerate(PAGES, start=1):
        c.setFont("Helvetica", FONT)
        put(28.8, 24, "ACCOUNT STATEMENT")
        put(52.8, 60, "MR TEST HOLDER")
        put(316.8, 60, "BRANCH"); put(400.8, 60, ":"); put(406.8, 60, "Testnagar")
        put(316.8, 72, "STATEMENT DATE"); put(400.8, 72, ":"); put(406.8, 72, PERIOD)
        put(316.8, 84, "CURRENCY"); put(400.8, 84, ":"); put(406.8, 84, "INR")
        put(316.8, 96, "ACCOUNT TYPE"); put(400.8, 96, ":"); put(406.8, 96, "SMART BANKING SAVINGS ACCOUNT")
        put(316.8, 108, "ACCOUNT NO"); put(400.8, 108, ":"); put(406.8, 108, "12345678901")
        put(52.8, 174, "BRANCH ADDRESS :")
        put(52.8, 198, "IFSC: SCBL0099999   MICR CODE: 000000000")
        put(43.7, 220, "Date"); put(84.4, 220, "Value"); put(86.2, 228, "Date")
        put(180.5, 220, "Description"); put(338.5, 220, "Cheque")
        put(395.5, 220, first); put(460.2, 220, second); put(525.4, 220, "Balance")
        top = 246.0
        put(120.0, top, "Balance Brought Forward"); put(562.8, top, money(balance), right=True)
        top = 260.0
        for line in spill:
            put(118.8, top, line); top += 10.4
        spill = []
        for t in page:
            balance = balance + t.credit - t.debit
            total_c += t.credit
            total_d += t.debit
            if t.posting:
                put(38.9, top, t.posting)
            put(80.9, top, t.value)
            if t.cheque:
                put(340.0, top, t.cheque)
            if t.credit:
                put(credit_right, top, money(t.credit), right=True)
            if t.debit:
                put(debit_right, top, money(t.debit), right=True)
            put(562.8, top, money(balance), right=True)
            for line in t.narration:
                put(118.8, top, line); top += 10.4
            spill = list(t.spill)
        if number == n_pages:
            put(120.0, top + 4, "Total")
            put(credit_right, top + 4, money(total_c), right=True)
            put(debit_right, top + 4, money(total_d), right=True)
        put(28.8, 693, f"Page {number} of {n_pages}")
        put(28.8, 705, "Date : 10 Jan 2026")
        put(28.8, 722, "Bank deposits are covered under the insurance scheme offered by DICGC")
        c.showPage()
    c.save()
    return buf.getvalue()


def expected_balances() -> list[Decimal]:
    out, bal = [], OPENING
    for t in all_txns():
        bal = bal + t.credit - t.debit
        out.append(bal)
    return out


@pytest.fixture(scope="module")
def sc_result(analyzer):
    return analyzer.analyze(build_sc_pdf())


# ---------------------------------------------------------------- synthetic: parsing


def test_detected_and_parsed_by_dedicated_parser(sc_result):
    meta = sc_result.metadata
    assert meta.bank_code == "SCB"
    assert meta.bank_name == "Standard Chartered Bank"
    assert meta.parser == "standard_chartered"
    assert meta.ifsc == "SCBL0099999"
    assert meta.branch == "Testnagar"
    assert meta.account_number_masked.endswith("8901") and "1234567" not in meta.account_number_masked
    assert meta.account_type == "SAVINGS"
    assert (meta.period_start, meta.period_end) == (date(2025, 12, 20), date(2026, 1, 10))


def test_every_row_extracted_in_order_and_verified(sc_result):
    txns = sc_result.transactions
    assert len(txns) == len(all_txns())
    assert sc_result.status == "VERIFIED"
    assert sc_result.validation.balance_chain_breaks == 0
    assert [t.balance for t in txns] == expected_balances()
    assert [t.source_page for t in txns] == [p for p, page in enumerate(PAGES, 1) for _ in page]


def test_debit_and_credit_never_swapped(sc_result):
    for txn, src in zip(sc_result.transactions, all_txns()):
        assert txn.debit == src.debit and txn.credit == src.credit, txn.narration
        assert txn.transaction_type == ("CREDIT" if src.credit else "DEBIT")
        assert (txn.debit > 0) != (txn.credit > 0)


def test_year_less_dates_resolved_across_year_boundary(sc_result):
    dates = [(t.date, t.value_date) for t in sc_result.transactions]
    assert dates == [
        (date(2025, 12, 20), date(2025, 12, 20)),
        (date(2025, 12, 20), date(2025, 12, 20)),   # blank Date cell: posting date carried
        (date(2025, 12, 22), date(2025, 12, 21)),   # posting date differs from value date
        (date(2025, 12, 22), date(2025, 12, 22)),
        (date(2025, 12, 31), date(2025, 12, 31)),
        (date(2025, 12, 31), date(2025, 12, 31)),
        (date(2026, 1, 2), date(2026, 1, 1)),       # December -> January moves to the next year
        (date(2026, 1, 2), date(2026, 1, 2)),
        (date(2026, 1, 5), date(2026, 1, 5)),
    ]


def test_multiline_narration_reference_and_page_continuation(sc_result):
    txns = sc_result.transactions
    assert txns[0].narration == "UPI/100000000001/FICTIONAL GROCERS/grocer@okbank/PAYMENT FROM APP/"
    assert txns[1].narration == "NEFT/N0000001/EXAMPLE EMPLOYER PVT LTD/SALARY DEC"
    assert txns[2].reference_number == "000123"
    assert all(t.reference_number is None for i, t in enumerate(txns) if i != 2)
    # The narration of the last row on page 1 continues at the top of page 2.
    assert txns[3].narration == "UPI/100000000002/SAMPLE CAFE/cafe@okbank/COFFEE/"
    assert "NARRATION_CONTINUED_ACROSS_PAGE" in txns[3].flags
    assert txns[3].source_page == 1


def test_negative_balance_and_large_indian_grouped_amounts(sc_result):
    txns = sc_result.transactions
    assert txns[5].debit == D("100000.00")
    assert txns[5].balance < 0                       # printed "-…" balance keeps its sign
    assert txns[5].balance == expected_balances()[5]


def test_opening_totals_and_closing_reconcile(sc_result):
    v = sc_result.validation
    printed = sc_result.metadata.printed_summary
    credits = sum((t.credit for t in all_txns()), D("0"))
    debits = sum((t.debit for t in all_txns()), D("0"))
    assert printed.opening_balance == OPENING and printed.pages["opening_balance"] == 1
    assert printed.total_credits == credits and printed.total_debits == debits
    assert v.opening_balance == OPENING and v.opening_balance_source == "statement"
    assert v.closing_balance == expected_balances()[-1]
    assert v.computed_closing_balance == OPENING + credits - debits == v.closing_balance
    checks = {c.name: c.passed for c in v.checks}
    for name in ("running_balance_chain", "opening_balance_links_first_transaction",
                 "opening_plus_credits_minus_debits_equals_closing", "printed_total_debits",
                 "printed_total_credits"):
        assert checks[name], name


def test_amount_columns_follow_header_positions(analyzer):
    """Withdrawal printed left of Deposit: amounts must still land in the right column."""
    result = analyzer.analyze(build_sc_pdf(swap_amount_columns=True))
    assert result.status == "VERIFIED"
    for txn, src in zip(result.transactions, all_txns()):
        assert (txn.debit, txn.credit) == (src.debit, src.credit)


# ---------------------------------------------------------------- units


@pytest.mark.parametrize("month,day,ref,expected", [
    (3, 2, date(2026, 3, 1), date(2026, 3, 2)),
    (1, 2, date(2025, 12, 31), date(2026, 1, 2)),
    (12, 31, date(2026, 1, 2), date(2025, 12, 31)),    # value date just before the year turned
    (2, 29, date(2024, 2, 28), date(2024, 2, 29)),     # leap day
])
def test_resolve_year(month, day, ref, expected):
    assert resolve_year(month, day, ref, (date(2024, 1, 1), date(2026, 12, 31))) == expected


def _page(words: list[tuple[str, float, float]]) -> PageContent:
    return PageContent(1, PAGE_W, PAGE_H, [Word(t, x, y, x + 6 * len(t), y + 8) for t, x, y in words])


def test_detect_requires_bank_code_header_and_period():
    header = [("Date", 43, 220), ("Value", 84, 220), ("Description", 180, 220), ("Cheque", 338, 220),
              ("Deposit", 395, 220), ("Withdrawal", 460, 220), ("Balance", 525, 220)]
    period = [("01", 400, 72), ("Mar", 415, 72), ("2026", 435, 72), ("To", 465, 72),
              ("26", 480, 72), ("Nov", 495, 72), ("2026", 515, 72)]
    assert StandardCharteredParser.detect(_page(header + period), "SCB")
    assert not StandardCharteredParser.detect(_page(header + period), "HDFC")
    assert not StandardCharteredParser.detect(_page(header), "SCB")            # no period: no year source
    assert not StandardCharteredParser.detect(_page(header[:-1] + period), "SCB")  # unknown table layout


def test_unknown_standard_chartered_layout_falls_back_to_generic():
    assert isinstance(registry.select(_page([("Hello", 50, 50)]), "SCB"), GenericTableParser)
    assert not isinstance(registry.select(_page([("Hello", 50, 50)]), "SCB"), StandardCharteredParser)
    assert not isinstance(registry.select(_page([("Hello", 50, 50)]), "HDFC"), StandardCharteredParser)


# ---------------------------------------------------------------- real sample (aggregate checks only)


@pytest.fixture(scope="module")
def real_result(analyzer):
    if not SC_SAMPLE.is_file():
        pytest.skip("sc.bank.pdf sample not present")
    original = SC_SAMPLE.read_bytes()
    result = analyzer.analyze(file_path=SC_SAMPLE)
    assert SC_SAMPLE.read_bytes() == original
    return result


def test_real_sample_reconciles_exactly(real_result):
    r, v = real_result, real_result.validation
    assert r.metadata.bank_code == "SCB" and r.metadata.parser == "standard_chartered"
    assert r.status == "VERIFIED" and v.balance_chain_breaks == 0
    assert len(r.transactions) == 619
    assert v.opening_balance_source == "statement"
    checks = {c.name: c.passed for c in v.checks}
    assert checks["printed_total_debits"] and checks["printed_total_credits"]
    assert checks["opening_plus_credits_minus_debits_equals_closing"]
    debits = sum((t.debit for t in r.transactions), D("0"))
    credits = sum((t.credit for t in r.transactions), D("0"))
    assert v.opening_balance + credits - debits == v.closing_balance == r.transactions[-1].balance


def test_real_sample_rows_well_formed(real_result):
    txns = real_result.transactions
    assert all((t.debit > 0) != (t.credit > 0) for t in txns)
    assert all(t.balance is not None and t.value_date is not None for t in txns)
    assert all(a.date <= b.date for a, b in zip(txns, txns[1:]))
    # Posting date is never before the value date (blank Date cells carry the posting date).
    assert all(t.value_date <= t.date for t in txns)
    assert any(t.value_date < t.date for t in txns)
    assert {t.transaction_type for t in txns} == {"CREDIT", "DEBIT"}
    assert len({t.channel for t in txns}) >= 2
    p = real_result.metadata
    assert all(p.period_start <= t.date <= p.period_end for t in txns)
    # Transactions on every page, none dropped by the per-page census.
    dq = real_result.data_quality
    assert dq["table_pages_without_rows"] == [] and dq["pages_with_transactions"] == p.page_count
    assert not [i for i in real_result.validation.issues if i.code == "POSSIBLE_MISSED_ROWS"]
    assert not any(f.startswith("UNPARSED_") for t in txns for f in t.flags)
