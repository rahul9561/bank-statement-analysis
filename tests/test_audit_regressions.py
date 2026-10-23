"""Regression tests for defects found in the production-readiness audit (2026-10-23).

Each test names the defect it guards against. Synthetic data only.
"""

import random
import subprocess
import sys
from datetime import date
from decimal import Decimal

import pymupdf
import pytest

import helpers
from bank_statement_engine import BankStatementAnalyzer, BankStatementError, InvalidPDFError
from bank_statement_engine.config import AnalysisConfig
from bank_statement_engine.normalizers.amounts import parse_amount
from bank_statement_engine.normalizers.transactions import build_transactions
from bank_statement_engine.parsers.base import RawRow

D = Decimal
ROOT = helpers.__file__.rsplit("tests", 1)[0]


# --- Defect 1: ".01" (no leading zero) was not parsed; Axis dropped a one-paisa credit -----------------
@pytest.mark.parametrize("text,expected", [(".01", D("0.01")), (".5", D("0.50")), ("-.25", D("-0.25"))])
def test_amount_without_leading_zero(text, expected):
    assert parse_amount(text) == expected


def test_penny_row_is_kept_and_reconciles(analyzer):
    rows = helpers.KNOWN_ROWS[:3] + [helpers.Row(date(2026, 1, 4), "UPI/P2A/111/SAMPLE VERIFY/Verification", credit=D("0.01"))] \
        + helpers.KNOWN_ROWS[3:]
    result = analyzer.analyze(helpers.build_statement_pdf(rows, penny_format=True))
    assert result.status == "VERIFIED"
    assert len(result.transactions) == len(rows)
    assert result.overview["total_credits"] == helpers.EXPECTED_TOTAL_CREDITS + D("0.01")


# --- Defect 2: a 0.01 default tolerance hid the missing paisa in both chain and totals checks ----------
def test_default_tolerance_is_exact():
    assert AnalysisConfig().balance_tolerance == D("0")


def test_one_paisa_gap_is_a_chain_break():
    rows = [RawRow(date(2026, 1, 1), 1, "A", credit=D("100.00"), balance=D("100.00")),
            RawRow(date(2026, 1, 2), 1, "B", debit=D("10.00"), balance=D("90.01"))]
    from bank_statement_engine.analysis.reconciliation import reconcile
    from bank_statement_engine.schemas import StatementMetadata

    txns, issues, _ = build_transactions(rows, AnalysisConfig().balance_tolerance)
    report = reconcile(txns, StatementMetadata(), AnalysisConfig(), pre_issues=issues)
    assert report.balance_chain_breaks == 1 and report.status != "VERIFIED"


# --- Defect 3: unreadable text in an amount cell was silently treated as "no amount" ------------------
def test_unparseable_amount_is_an_error(analyzer):
    # Row 4 (a debit) prints "4,5O.50" (letter O) in its amount cell.
    result = analyzer.analyze(helpers.build_statement_pdf(amount_text={4: "4,5O.50"}))
    codes = {i.code: i.severity for i in result.validation.issues}
    assert codes.get("UNPARSED_AMOUNT") == "ERROR"
    assert result.status != "VERIFIED"


# --- Defect 4: very long digit strings raised decimal.InvalidOperation (unhandled crash) ---------------
@pytest.mark.parametrize("text", ["1" * 30, "12345678901234567890123456789.00", "9" * 16])
def test_long_digit_strings_are_not_money(text):
    assert parse_amount(text) is None


# --- Defect 5: damaged PDFs raised raw PyMuPDF errors; damaged pages could yield a false VERIFIED -------
def test_corrupted_pdfs_never_crash_or_falsely_verify(analyzer):
    base = helpers.build_statement_pdf()
    rng = random.Random(42)
    false_verified = []
    for i in range(120):
        data = bytearray(base)
        for _ in range(rng.randint(1, 40)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        try:
            r = analyzer.analyze(bytes(data))
        except BankStatementError:
            continue  # any other exception type fails the test
        o = r.overview
        if r.status == "VERIFIED" and (o["total_credits"], o["closing_balance"], o["transaction_count"]) != (
                helpers.EXPECTED_TOTAL_CREDITS, helpers.EXPECTED_CLOSING, len(helpers.KNOWN_ROWS)):
            false_verified.append(i)
    assert false_verified == []


def test_damaged_page_tree_is_invalid_pdf(analyzer):
    pdf = helpers.build_statement_pdf()
    broken = pdf.replace(b"/Type /Pages", b"/Type /Pagez", 1)
    try:
        analyzer.analyze(broken)
    except InvalidPDFError:
        pass
    except BankStatementError:
        pass  # MuPDF may repair the tree; any safe engine error is acceptable
    # reaching here without an unhandled exception is the requirement


def _damaged_stream_pdf() -> bytes:
    """Synthetic statement whose second content stream (page 2) is corrupted."""
    import re

    pdf = bytearray(helpers.build_statement_pdf())
    pos = [m.end() for m in re.finditer(rb"stream\r?\n", bytes(pdf))][1]
    for j in range(30, 60):
        pdf[pos + j] ^= 0x5A
    return bytes(pdf)


def test_damaged_content_stream_blocks_verification(analyzer):
    result = analyzer.analyze(_damaged_stream_pdf())
    assert {i.code: i.severity for i in result.validation.issues}.get("DAMAGED_PAGES") == "ERROR"
    assert result.status != "VERIFIED"
    assert len(result.transactions) < len(helpers.KNOWN_ROWS)


def test_damage_detection_is_thread_safe(analyzer, known_pdf):
    """Concurrent clean/damaged analyses keep their own results.

    Smoke test: the race this guards (MuPDF's process-global warning store) could not be
    reproduced without the lock in this environment, so the lock is defensive.
    """
    from concurrent.futures import ThreadPoolExecutor

    damaged = _damaged_stream_pdf()
    jobs = [known_pdf if i % 2 == 0 else damaged for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(analyzer.analyze, jobs))
    for i, r in enumerate(results):
        codes = {x.code for x in r.validation.issues}
        if i % 2 == 0:
            assert r.status == "VERIFIED" and "DAMAGED_PAGES" not in codes
        else:
            assert "DAMAGED_PAGES" in codes and r.status != "VERIFIED"


# --- Defect 6: rows missing at the end of a statement went unnoticed (layout-free row census) ----------
def test_unanchored_trailing_row_is_reported(analyzer):
    last = len(helpers.KNOWN_ROWS) - 1
    result = analyzer.analyze(helpers.build_statement_pdf(misplace_date_rows=frozenset({last}), printed_summary=False))
    issues = {i.code: i for i in result.validation.issues}
    assert "POSSIBLE_MISSED_ROWS" in issues and issues["POSSIBLE_MISSED_ROWS"].severity == "ERROR"
    assert result.status != "VERIFIED"


def test_completeness_level_is_reported(analyzer, known_result):
    assert known_result.validation.completeness == "CONFIRMED_BY_PRINTED_BALANCES"
    result = analyzer.analyze(helpers.build_statement_pdf(printed_summary=False))
    assert result.validation.completeness == "CHAIN_ONLY"
    assert {"CLOSING_BALANCE_NOT_PRINTED"} <= {i.code for i in result.validation.issues}


# --- Defect 7: scanned pages were not flagged when OCR was off; blank pages were treated like scans ----
def _with_extra_page(pdf: bytes, scanned: bool) -> bytes:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc.new_page(pno=1)
    if scanned:
        pix = doc[0].get_pixmap(dpi=60)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
    return doc.tobytes()


def test_scanned_page_without_ocr_blocks_verification(analyzer, known_pdf):
    result = analyzer.analyze(_with_extra_page(known_pdf, scanned=True))
    assert {i.code: i.severity for i in result.validation.issues}.get("UNREADABLE_PAGES") == "ERROR"
    assert result.status != "VERIFIED"


def test_blank_page_is_only_noted(analyzer, known_pdf):
    result = analyzer.analyze(_with_extra_page(known_pdf, scanned=False))
    codes = {i.code: i.severity for i in result.validation.issues}
    assert codes.get("BLANK_PAGES") == "INFO" and "UNREADABLE_PAGES" not in codes
    assert result.status == "VERIFIED"


# --- Defect 8: EMI/bounce disclaimers could be orphaned on the next page --------------------------------
def test_emi_note_stays_with_its_table(analyzer, known_result):
    doc = pymupdf.open(stream=analyzer.generate_pdf(known_result), filetype="pdf")
    pages = [p.get_text() for p in doc]
    note_pages = [i for i, t in enumerate(pages) if "Obligations are estimated from recurring debits" in t]
    table_pages = [i for i, t in enumerate(pages) if "BAJAJ FINANCE LTD" in t and "LENDER / PAYEE" in t]
    assert note_pages and set(note_pages) & set(table_pages)


# --- Independence: the engine must import and run without Django -----------------------------------------
def test_engine_runs_without_django(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(helpers.build_statement_pdf())
    code = (
        "import sys; sys.modules['django'] = None; sys.modules['rest_framework'] = None\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "from bank_statement_engine import BankStatementAnalyzer\n"
        f"r = BankStatementAnalyzer({{'ocr': {{'mode': 'off'}}}}).analyze(file_path={str(pdf)!r})\n"
        "pdf = BankStatementAnalyzer().generate_pdf(r)\n"
        "print(r.status, r.overview['transaction_count'], pdf[:5])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith(f"VERIFIED {len(helpers.KNOWN_ROWS)} b'%PDF-'")
