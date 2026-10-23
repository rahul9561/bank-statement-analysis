"""End-to-end tests on a synthetic text-based statement with known values."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

import helpers
from helpers import (
    EXPECTED_CLOSING,
    EXPECTED_NET,
    EXPECTED_TOTAL_CREDITS,
    EXPECTED_TOTAL_DEBITS,
    KNOWN_ROWS,
    OPENING,
    build_statement_pdf,
)

D = Decimal


def test_exact_totals_and_balances(known_result):
    r = known_result
    o = r.overview
    assert r.status == "VERIFIED"
    assert o["transaction_count"] == len(KNOWN_ROWS)
    assert o["total_credits"] == EXPECTED_TOTAL_CREDITS
    assert o["total_debits"] == EXPECTED_TOTAL_DEBITS
    assert o["net_cash_flow"] == EXPECTED_NET
    assert o["opening_balance"] == OPENING and o["opening_balance_source"] == "statement"
    assert o["closing_balance"] == EXPECTED_CLOSING
    assert [t.balance for t in r.transactions] == helpers.balances(KNOWN_ROWS)
    assert [(t.debit, t.credit) for t in r.transactions] == [
        (row.debit.quantize(D("0.01")), row.credit.quantize(D("0.01"))) for row in KNOWN_ROWS]
    for t in r.transactions:
        assert isinstance(t.debit, Decimal) and isinstance(t.credit, Decimal) and isinstance(t.balance, Decimal)


def test_printed_summary_checks_pass(known_result):
    checks = {c.name: c.passed for c in known_result.validation.checks}
    for name in ("running_balance_chain", "opening_balance_links_first_transaction",
                 "closing_balance_matches_last_transaction", "opening_plus_credits_minus_debits_equals_closing",
                 "printed_total_debits", "printed_total_credits", "printed_debit_count", "printed_credit_count"):
        assert checks[name] is True, name


def test_metadata(known_result):
    m = known_result.metadata
    assert m.bank_code == "HDFC" and m.bank_detection_method == "ifsc"
    assert m.ifsc == "HDFC0001234"
    assert m.account_number_masked == "XXXXXXXXXXX4321"
    assert m.period_start == date(2026, 1, 1) and m.period_end == date(2026, 4, 30)
    assert m.account_type == "SAVINGS"


def test_monthly_aggregation(known_result):
    months = {m["month"]: m for m in known_result.monthly}
    assert list(months) == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert months["2026-01"]["total_credits"] == D("52000.00")
    assert months["2026-01"]["total_debits"] == D("32950.50")
    assert months["2026-02"]["total_debits"] == D("32389.00")
    assert months["2026-03"]["total_debits"] == D("28774.56")
    assert months["2026-04"]["total_credits"] == D("51422.45")
    assert months["2026-04"]["closing_balance"] == EXPECTED_CLOSING
    assert sum(m["transaction_count"] for m in known_result.monthly) == len(KNOWN_ROWS)


def test_average_daily_balance_matches_independent_calculation(known_result):
    eod, bal = {}, OPENING
    for row, b in zip(KNOWN_ROWS, helpers.balances(KNOWN_ROWS)):
        eod[row.date] = b
    values, day = [], date(2026, 1, 1)
    while day <= date(2026, 4, 30):
        bal = eod.get(day, bal)
        values.append(bal)
        day += timedelta(days=1)
    expected = (sum(values, D(0)) / len(values)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    b = known_result.balances
    assert b["reliable"] is True
    assert b["days_tracked"] == 120
    assert b["average_daily_balance"] == expected


def test_categories_and_self_transfer(known_result):
    by_narr = {t.narration: t for t in known_result.transactions}
    def cat(prefix):
        return next(t.category for n, t in by_narr.items() if n.startswith(prefix))
    assert cat("NEFT-ACME") == "SALARY"
    assert cat("ACH D- BAJAJ") == "EMI_LOAN"
    assert cat("UPI/SURESH NAIR") == "RENT"
    assert cat("NWD-") == "ATM_WITHDRAWAL"
    assert cat("UPI/SWIGGY") == "FOOD_DINING"
    assert cat("ECS RTN") == "RETURN_CHARGES"
    assert cat("UPI/AMAZON") == "SHOPPING"
    assert cat("BBPS/BESCOM") == "UTILITIES"
    assert cat("UPI/REV/AMAZON") == "REFUND_REVERSAL"
    assert cat("INT.PD") == "INTEREST"
    self_txn = next(t for t in known_result.transactions if t.narration.startswith("UPI/RAVI KUMAR SHARMA"))
    assert self_txn.is_self_transfer and self_txn.category == "SELF_TRANSFER"
    for t in known_result.transactions:
        assert t.category_rule and 0 <= t.category_confidence <= 1


def test_income_analysis(known_result):
    inc = known_result.income
    assert inc["salary_like"]["status"] == "SALARY_LIKE_RECURRING"
    assert "not verified" in inc["salary_like"]["description"]
    assert inc["estimated_monthly_income"] == D("50000.00")
    assert inc["self_transfers_in"]["amount"] == D("2000.00")
    assert inc["refunds_reversals"]["amount"] == D("1299.00")
    sources = {r["source"]: r for r in inc["recurring_credits"]}
    assert "ACME TECHNOLOGIES PVT LTD" in sources
    assert sources["ACME TECHNOLOGIES PVT LTD"]["month_count"] == 4


def test_emi_detection(known_result):
    emi = known_result.emi
    assert emi["emi_detected"]
    ob = emi["recurring_obligations"][0]
    assert ob["lender_or_payee"] == "BAJAJ FINANCE LTD"
    assert ob["estimated_monthly_amount"] == D("12500.00")
    assert ob["occurrences"] == 4 and ob["status"] == "SEEN_RECENTLY"
    assert emi["estimated_monthly_emi"] == D("12500.00")
    assert emi["estimated_emi_to_income_pct"] == D("25.00")


def test_bounce_indicators(known_result):
    b = known_result.bounces
    assert b["confirmed_event_count"] == 1
    assert b["return_charge_count"] == 1 and b["return_charges_total"] == D("590.00")
    codes = {r.code for r in known_result.risk_indicators}
    assert "PAYMENT_RETURN_EVIDENCE" in codes
    ind = next(r for r in known_result.risk_indicators if r.code == "PAYMENT_RETURN_EVIDENCE")
    assert ind.evidence and ind.limitations
    assert any("default" in lim for lim in ind.limitations)


def test_duplicates_flagged_but_kept(known_result):
    chai = [t for t in known_result.transactions if t.narration.startswith("UPI/CHAI POINT")]
    assert len(chai) == 2 and all("DUPLICATE_LOOKING" in t.flags for t in chai)


def test_no_score_or_decision_in_output(known_result):
    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k.lower()
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    data = known_result.to_dict()
    for key in keys(data):
        assert not any(word in key for word in ("score", "eligib", "decision", "approv")), key
    assert "certified" not in known_result.to_json().lower()


@pytest.mark.parametrize("kwargs,mode", [
    ({"reverse": True}, None),
    ({"ruled": True}, "bands"),
    ({"header_text": False}, None),
    ({"date_style": "dd-mon-yyyy"}, None),
    ({"date_style": "dd mon yyyy"}, None),
    ({"date_style": "dd/mm/yy"}, None),
    ({"rows_per_page": 5}, None),
])
def test_layout_variants_reconcile(analyzer, kwargs, mode):
    result = analyzer.analyze(build_statement_pdf(**kwargs))
    assert result.status == "VERIFIED", [i.code for i in result.validation.issues]
    assert result.overview["total_credits"] == EXPECTED_TOTAL_CREDITS
    assert result.overview["total_debits"] == EXPECTED_TOTAL_DEBITS
    assert result.overview["closing_balance"] == EXPECTED_CLOSING
    assert [t.date for t in result.transactions] == [r.date for r in KNOWN_ROWS]
    if kwargs.get("reverse"):
        assert result.metadata.reverse_chronological
    if kwargs.get("header_text") is False:
        assert result.data_quality["layout_inferred"]
    if mode:
        assert mode in result.data_quality["row_segmentation"]


def test_tampered_balance_requires_review(analyzer):
    result = analyzer.analyze(build_statement_pdf(tamper_balance_at=5))
    assert result.status in ("REQUIRES_REVIEW", "FAILED")
    assert result.validation.balance_chain_breaks >= 1
    assert not result.validation.reconciled
    assert any(r.code == "DATA_NOT_FULLY_RECONCILED" for r in result.risk_indicators)


def test_bank_detected_from_content_not_filename(analyzer):
    pdf = build_statement_pdf(bank_line="STATE BANK OF INDIA", ifsc="SBIN0004321")
    result = analyzer.analyze(pdf)
    assert result.metadata.bank_code == "SBI"


def test_analyze_accepts_path_bytes_and_file_object(analyzer, known_pdf, tmp_path):
    path = tmp_path / "statement.pdf"
    path.write_bytes(known_pdf)
    import io
    for source in (str(path), path, known_pdf, io.BytesIO(known_pdf)):
        assert analyzer.analyze(source).overview["total_credits"] == EXPECTED_TOTAL_CREDITS
    assert analyzer.analyze(file_path=path).status == "VERIFIED"
