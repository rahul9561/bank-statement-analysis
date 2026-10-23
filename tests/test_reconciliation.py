from datetime import date
from decimal import Decimal

from bank_statement_engine.analysis.reconciliation import reconcile
from bank_statement_engine.config import AnalysisConfig
from bank_statement_engine.normalizers.transactions import build_transactions
from bank_statement_engine.parsers.base import RawRow
from bank_statement_engine.schemas import PrintedSummary, StatementMetadata

D = Decimal
TOL = D("0.01")


def raw(day, debit=None, credit=None, balance=None, narration="TXN", amount=None, drcr=None):
    return RawRow(date=date(2026, 1, day), page=1, narration=narration, debit=debit, credit=credit,
                  balance=balance, amount=amount, drcr=drcr)


def build(rows, **kwargs):
    txns, issues, info = build_transactions(rows, TOL, **kwargs)
    for t in txns:
        t.narration_normalized = t.narration.upper()
    return txns, issues, info


def test_consistent_chain_is_verified():
    rows = [raw(1, credit=D("1000"), balance=D("6000")), raw(2, debit=D("250.50"), balance=D("5749.50")),
            raw(3, debit=D("49.50"), balance=D("5700.00"))]
    txns, issues, _ = build(rows)
    meta = StatementMetadata(printed_summary=PrintedSummary(opening_balance=D("5000"), closing_balance=D("5700")))
    report = reconcile(txns, meta, AnalysisConfig(), pre_issues=issues)
    assert report.status == "VERIFIED"
    assert report.reconciled
    assert report.balance_chain_breaks == 0
    assert report.opening_balance == D("5000") and report.opening_balance_source == "statement"
    assert report.computed_closing_balance == D("5700.00")


def test_chain_break_is_reported_not_repaired():
    rows = [raw(1, credit=D("1000"), balance=D("6000")), raw(2, debit=D("100"), balance=D("5800")),  # should be 5900
            raw(3, debit=D("100"), balance=D("5700"))]
    txns, issues, _ = build(rows)
    report = reconcile(txns, StatementMetadata(), AnalysisConfig(), pre_issues=issues)
    assert report.status in ("REQUIRES_REVIEW", "FAILED")
    assert report.balance_chain_breaks == 1
    breaks = [i for i in report.issues if i.code == "BALANCE_CHAIN_BREAK"]
    assert breaks and breaks[0].details["difference"] == D("-100")
    # Amounts were not altered.
    assert txns[1].debit == D("100") and txns[1].balance == D("5800")


def test_opening_balance_mismatch_detected():
    rows = [raw(1, credit=D("1000"), balance=D("6000"))]
    txns, issues, _ = build(rows)
    meta = StatementMetadata(printed_summary=PrintedSummary(opening_balance=D("4000")))
    report = reconcile(txns, meta, AnalysisConfig(), pre_issues=issues)
    assert any(i.code == "OPENING_BALANCE_MISMATCH" for i in report.issues)
    assert report.status != "VERIFIED"


def test_printed_totals_compared():
    rows = [raw(1, credit=D("1000"), balance=D("1000")), raw(2, debit=D("400"), balance=D("600"))]
    txns, issues, _ = build(rows)
    meta = StatementMetadata(printed_summary=PrintedSummary(
        opening_balance=D("0"), closing_balance=D("600"), total_debits=D("400"), total_credits=D("1000"),
        debit_count=1, credit_count=2))
    report = reconcile(txns, meta, AnalysisConfig(), pre_issues=issues)
    failed = {c.name for c in report.checks if c.passed is False}
    assert failed == {"printed_credit_count"}
    assert report.status == "REQUIRES_REVIEW"


def test_same_day_reordering_is_flagged():
    # Printed order breaks the chain; posting order 2,1 works (bank prints same-day items out of order).
    rows = [raw(1, credit=D("100"), balance=D("1100")),
            raw(2, debit=D("50"), balance=D("1000")),   # posted second
            raw(2, debit=D("50"), balance=D("1050")),   # posted first
            raw(3, credit=D("10"), balance=D("1010"))]
    txns, issues, _ = build(rows)
    report = reconcile(txns, StatementMetadata(), AnalysisConfig(), pre_issues=issues)
    assert report.balance_chain_breaks == 0
    assert any(i.code == "SAME_DAY_ORDER_ADJUSTED" for i in report.issues)
    assert [t.balance for t in txns] == [D("1100"), D("1050"), D("1000"), D("1010")]
    assert all("REORDERED_WITHIN_DAY" in t.flags for t in txns[1:3])


def test_reverse_chronological_rows_are_reordered():
    rows = [raw(3, debit=D("10"), balance=D("990")), raw(2, credit=D("500"), balance=D("1000")),
            raw(1, debit=D("100"), balance=D("500"))]
    txns, issues, info = build(rows)
    assert info["reverse_chronological"]
    assert [t.date.day for t in txns] == [1, 2, 3]
    assert any(i.code == "REVERSE_CHRONOLOGICAL" for i in issues)


def test_missing_balances_reported():
    rows = [raw(1, credit=D("100"), balance=D("100")), raw(2, debit=D("10"), balance=None),
            raw(3, debit=D("10"), balance=D("80"))]
    txns, issues, _ = build(rows)
    report = reconcile(txns, StatementMetadata(), AnalysisConfig(), pre_issues=issues)
    assert any(i.code == "MISSING_BALANCE" for i in report.issues)
    assert report.status != "VERIFIED"


def test_duplicate_looking_transactions_are_kept():
    rows = [raw(1, credit=D("100"), balance=D("100")),
            raw(2, debit=D("20"), balance=D("80"), narration="UPI/CHAI/TEA"),
            raw(2, debit=D("20"), balance=D("60"), narration="UPI/CHAI/TEA")]
    txns, issues, _ = build(rows)
    report = reconcile(txns, StatementMetadata(), AnalysisConfig(), pre_issues=issues)
    assert len(txns) == 3
    dup = [i for i in report.issues if i.code == "POSSIBLE_DUPLICATES"]
    assert dup and dup[0].severity == "INFO"
    assert report.status == "VERIFIED"


def test_debit_credit_direction_and_sign_conventions():
    rows = [raw(1, credit=D("100"), balance=D("100")),
            raw(2, amount=D("30"), drcr="DR", balance=D("70")),
            raw(3, amount=D("5"), balance=D("75")),                    # no marker: inferred from balance
            raw(4, debit=D("-25.00"), balance=D("100"))]               # negative withdrawal = credit
    txns, issues, info = build(rows)
    assert [t.transaction_type for t in txns] == ["CREDIT", "DEBIT", "CREDIT", "CREDIT"]
    assert txns[2].credit == D("5") and "DIRECTION_INFERRED_FROM_BALANCE" in txns[2].flags
    assert txns[3].credit == D("25.00") and txns[3].debit == D("0")
    assert info["direction_inferred"] == 1
    for t in txns:
        assert (t.debit > 0) != (t.credit > 0)
        assert t.transaction_type == ("CREDIT" if t.credit > 0 else "DEBIT")
    report = reconcile(txns, StatementMetadata(), AnalysisConfig(), pre_issues=issues)
    assert report.balance_chain_breaks == 0


def test_rows_without_amount_are_excluded_with_note():
    rows = [raw(1, credit=D("100"), balance=D("100")), raw(2, balance=D("100")), raw(3, debit=D("1"), balance=D("99"))]
    txns, issues, info = build(rows)
    assert len(txns) == 2 and info["rows_without_amount"] == 1
    assert any(i.code == "ROW_WITHOUT_AMOUNT" for i in issues)
