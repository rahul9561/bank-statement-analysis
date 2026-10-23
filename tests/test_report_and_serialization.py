import json
from decimal import Decimal

import pymupdf
import pytest

import helpers
from bank_statement_engine import BankStatementAnalyzer, EngineConfig
from bank_statement_engine.reports.formatting import inr, inr_compact
from bank_statement_engine.utils.serialization import to_jsonable


def _text(pdf: bytes) -> str:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return "\n".join(page.get_text() for page in doc), doc.page_count


def test_pdf_report_contains_calculated_values(analyzer, known_result, tmp_path):
    path = analyzer.generate_pdf(known_result, tmp_path / "out" / "report.pdf")
    assert path.exists()
    text, pages = _text(path.read_bytes())
    assert pages >= 3
    for section in ("ACCOUNT FINANCIAL SUMMARY", "EXECUTIVE SUMMARY", "EXTRACTION & RECONCILIATION STATUS",
                    "MONTHLY CASH FLOW", "FOIR & REPAYMENT OBLIGATIONS", "CHEQUE RETURN & PAYMENT BOUNCE ANALYSIS",
                    "RISK INDICATORS", "TRANSACTION DETAILS", "METHODOLOGY & LIMITATIONS"):
        assert section in text, section
    assert known_result.report_id in text
    assert inr(helpers.EXPECTED_CLOSING) in text           # ₹1,07,808.39
    assert "BAJAJ FINANCE LTD" in text
    assert "Page 1 of" in text
    assert "certified" not in text.lower()
    assert "501000123454321" not in text                 # full account number never printed


def test_generate_pdf_returns_bytes(analyzer, known_result):
    pdf = analyzer.generate_pdf(known_result)
    assert pdf.startswith(b"%PDF-")
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert "Bank Statement Analysis" in (doc.metadata.get("title") or "")
    assert helpers.HOLDER not in (doc.metadata.get("title") or "")


def test_report_for_unreconciled_statement(analyzer):
    result = analyzer.analyze(helpers.build_statement_pdf(tamper_balance_at=3))
    text, _ = _text(analyzer.generate_pdf(result))
    assert "REVIEW" in text or "NOT RELIABLE" in text


def test_report_transaction_limit_and_holder_toggle(known_pdf):
    a = BankStatementAnalyzer({"ocr": {"mode": "off"}, "report": {"max_transactions_in_report": 5, "show_holder_name": True}})
    r = a.analyze(known_pdf, holder_names=[helpers.HOLDER])
    text, _ = _text(a.generate_pdf(r))
    assert f"most recent 5 of {len(helpers.KNOWN_ROWS)}" in text
    assert helpers.HOLDER in text


def test_inr_formatting():
    assert inr(Decimal("1234567.5")) == "₹12,34,567.50"
    assert inr(Decimal("-999.999")) == "-₹1,000.00"
    assert inr_compact(Decimal("911000")) == "₹9.11 L"
    assert inr_compact(Decimal("25000000")) == "₹2.50 Cr"
    assert inr(None) == "Not available"


def test_json_serialization(known_result):
    data = json.loads(known_result.to_json())
    assert data["summary"]["total_credits"] == "204422.45"
    assert data["summary"]["transaction_count"] == len(helpers.KNOWN_ROWS)
    txn = data["transactions"][0]
    assert txn["date"] == "2026-01-01" and txn["credit"] == "50000.00" and txn["debit"] == "0.00"
    assert txn["transaction_type"] == "CREDIT" and txn["currency"] == "INR"
    for key in ("date", "value_date", "narration", "reference_number", "transaction_type", "debit", "credit",
                "balance", "currency", "category", "category_confidence", "source_page", "source_text"):
        assert key in txn
    assert "holder_name" not in data["metadata"]          # minimised by default
    assert data["generated_at"].endswith("+05:30")
    assert "transactions" not in known_result.to_dict(include_transactions=False)


def test_to_jsonable_rules():
    from datetime import date, datetime, timezone
    assert to_jsonable(Decimal("1.5")) == "1.50"
    assert to_jsonable(date(2026, 1, 2)) == "2026-01-02"
    assert to_jsonable(datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)) == "2026-01-02T03:04:00+00:00"
    assert to_jsonable({"a": (Decimal("2"),)}) == {"a": ["2.00"]}


def test_config_from_dict_validation():
    cfg = EngineConfig.from_dict({"analysis": {"balance_tolerance": "0.05", "holder_names": ["A B"]},
                                  "ocr": {"mode": "off"}})
    assert cfg.analysis.balance_tolerance == Decimal("0.05")
    with pytest.raises(ValueError):
        EngineConfig.from_dict({"analysis": {"not_an_option": 1}})
    with pytest.raises(ValueError):
        EngineConfig.from_dict({"unknown_section": {}})
    assert json.dumps(cfg.to_dict())


def test_custom_category_rule(known_pdf):
    a = BankStatementAnalyzer({"ocr": {"mode": "off"}, "analysis": {"extra_category_rules": [
        {"id": "chai", "category": "FOOD_DINING", "pattern": "CHAI POINT", "direction": "DEBIT", "confidence": 0.99}]}})
    r = a.analyze(known_pdf)
    chai = [t for t in r.transactions if "CHAI POINT" in t.narration]
    assert all(t.category_rule == "chai" and t.category_confidence == 0.99 for t in chai)
