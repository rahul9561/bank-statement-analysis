"""Categorisation evidence/confidence and returned-instrument linking.

Narrations follow the formats seen on a real ICICI statement; personal names, VPAs and
amounts are fictional.
"""

from datetime import date
from decimal import Decimal

import pytest

from bank_statement_engine.analysis.bounces import analyze_bounces, link_returns
from bank_statement_engine.analysis.categorize import categorize, enrich_transactions
from bank_statement_engine.analysis.common import is_external_income
from bank_statement_engine.config import AnalysisConfig
from bank_statement_engine.schemas import CREDIT, DEBIT, Transaction
from conftest import INPUT_DIR

D = Decimal
_seq = iter(range(1, 10_000))


def txn(narration, debit="0", credit="0", day=1, month=10, page=1):
    debit, credit = D(debit), D(credit)
    return Transaction(id=f"T{next(_seq):05d}", date=date(2025, month, day), narration=narration,
                       transaction_type=CREDIT if credit > 0 else DEBIT, debit=debit, credit=credit, source_page=page)


def run(txns, holder_names=()):
    cfg = AnalysisConfig()
    enrich_transactions(txns, cfg)
    categorize(txns, cfg, list(holder_names))
    return link_returns(txns)


# ------------------------------------------------------------------------------- categorisation

def test_utility_bill_by_biller_name_not_channel():
    t = txn("BSESDELHI UPI/BSESDELHI/bsesdelhi.esbz/Pay/HDFC BANK/113375278714/HDFe0aaaf95cb", debit="310.00")
    g = txn("Indraprast UPI/Indraprast/billdeskpg.ind/IGL/HDFC BANK/102277529773/HDF350B375D", debit="582.79")
    run([t, g])
    for x in (t, g):
        assert x.channel == "UPI"                    # channel kept separate
        assert x.category == "UTILITIES" and x.category_basis == "MERCHANT_NAME"
        assert x.category_confidence == 0.75


def test_person_to_person_upi_stays_generic_and_low_confidence():
    out = txn("SAMPLE PERSON UPI/SAMPLE PERSON/sp12345@ic/UPI/ICICI Bank/113375381983/HDF0795", debit="5000.00")
    inn = txn("OTHER PERSON UPI/OTHER PERSON/op09250/UPI/ICICI Bank/568030627803/ICIb7cb1b", credit="3600.00")
    run([out, inn])
    assert (out.category, out.category_basis) == ("UPI_TRANSFER", "CHANNEL_ONLY")
    assert (inn.category, inn.category_basis) == ("UPI_RECEIPT", "CHANNEL_ONLY")
    assert out.category_confidence <= 0.5 and inn.category_confidence <= 0.5   # purpose not inferred
    assert out.counterparty == "SAMPLE PERSON"


def test_card_spend_keeps_card_category_and_extracts_merchant():
    t = txn("VISA trxn VPS/RAJ MANDIR/202511041945/530814071311/NEW DELHI", debit="5165.00")
    run([t])
    assert t.channel == "CARD" and t.category == "CARD_SPEND" and t.category_basis == "CHANNEL_ONLY"
    assert t.counterparty == "RAJ MANDIR"


def test_nach_interpretation_depends_on_payee():
    lic = txn("NACH trxn ACH/LIC OF INDIA/ICIC0000000017371079/1598173441025", debit="11062.00")
    lender = txn("NACH trxn ACH/TOYOTAFINANCIALSERVI/ICIC702200525200 9208/NDEL1417237", debit="16762.00")
    other = txn("NACH trxn ACH/SAMPLE CORP/ICIC000000123/NDEL99", debit="999.00")
    run([lic, lender, other])
    assert (lic.category, lic.category_basis) == ("INSURANCE", "NARRATION_RULE")
    assert lender.category == "EMI_LOAN" and lender.category_rule == "nach_lender_name"
    # An unexplained mandate debit is not labelled EMI.
    assert other.category == "NACH_AUTO_DEBIT" and other.category_basis == "CHANNEL_ONLY"
    assert other.category_confidence < lender.category_confidence < lic.category_confidence


def test_cheque_credit_is_not_salary():
    t = txn("Clearance of cheque CLG/SAMPLE DRAWER/413909/SBI/30. 09.2025", credit="150000.00")
    run([t])
    assert t.channel == "CHEQUE" and t.category == "CHEQUE_DEPOSIT" and t.category != "SALARY"
    assert t.category_basis == "CHANNEL_ONLY"


def test_unknown_narration_stays_other_with_zero_confidence():
    t = txn("ADJ 99812 MISC", debit="10.00")
    run([t])
    assert (t.category, t.category_basis, t.category_confidence) == ("OTHER", "NONE", 0.0)


def test_specific_rules_outrank_channel_fallbacks():
    salary = txn("NEFT-ACME LTD-SALARY OCT", credit="50000.00")
    p2p = txn("UPI/SOMEONE/someone@ybl/Payment", debit="100.00")
    run([salary, p2p])
    assert salary.category_confidence > p2p.category_confidence
    assert salary.category_basis == "NARRATION_RULE" and p2p.category_basis == "CHANNEL_ONLY"


# ------------------------------------------------------------------------------ returns/bounces

def _icici_bounce_case():
    deposits = [txn(f"Clearance of cheque CLG/SAMPLE DRAWER/{ref}/SBI/30. 09.2025", credit=amt, day=30, month=9)
                for ref, amt in (("413915", "50000.00"), ("413910", "100000.00"), ("413909", "50000.00"))]
    rejects = [txn(f"Debit trxn REJECT:{ref}:FUNDS INSUFFICIENT", debit=amt, day=31, page=1)
               for ref, amt in (("413915", "50000.00"), ("413909", "50000.00"), ("413910", "100000.00"))]
    charges = [txn(f"Debit trxn RTN CHG-{ref}/FUNDS INSUFFICIENT/31.10.25", debit="236.00", day=31, page=2)
               for ref in ("413909", "413910", "413915")]
    return deposits, rejects, charges


def test_rejections_and_charges_form_one_incident_per_cheque():
    deposits, rejects, charges = _icici_bounce_case()
    txns = deposits + rejects + charges
    before = [(t.id, t.date, t.debit, t.credit, t.transaction_type) for t in txns]
    incidents = run(txns)
    b = analyze_bounces(txns, incidents)

    assert [(t.id, t.date, t.debit, t.credit, t.transaction_type) for t in txns] == before  # nothing altered
    assert b["confirmed_incident_count"] == 3                  # three cheques, not six events or one date
    assert b["confirmed_event_count"] == 6
    assert b["rejected_instrument_count"] == 3 and b["charge_only_incident_count"] == 0
    assert b["returned_payment_amount"] == D("200000.00")       # each cheque amount once
    assert b["return_charge_count"] == 3 and b["return_charges_total"] == D("708.00")
    assert b["cheque_return_count"] == 3 and b["deposited_instrument_return_count"] == 3
    by_ref = {i["reference"]: i for i in b["incidents"]}
    assert set(by_ref) == {"413909", "413910", "413915"}
    inc = by_ref["413910"]
    assert inc["returned_amount"] == D("100000.00") and inc["charges_total"] == D("236.00")
    assert inc["instrument"] == "CHEQUE" and inc["direction"] == "DEPOSITED_INSTRUMENT_RETURNED"
    assert inc["reason"] == "FUNDS INSUFFICIENT" and inc["confidence"] == 0.95
    assert inc["original"]["amount"] == D("100000.00") and inc["source_pages"] == [1, 2]
    assert sorted(e["role"] for e in inc["entries"]) == ["REJECTED_INSTRUMENT", "RETURN_CHARGE"]
    for e in inc["entries"]:
        assert {"date", "amount", "source_page", "transaction_id", "reference"} <= set(e)


def test_categories_and_income_after_linking():
    deposits, rejects, charges = _icici_bounce_case()
    run(deposits + rejects + charges)
    assert all(t.category == "RETURNED_INSTRUMENT" and t.category_basis == "LINKED_EVENT" for t in rejects)
    assert all(t.category == "RETURN_CHARGES" for t in charges)        # charges are not instrument amounts
    assert all("RETURNED_UNPAID" in t.flags for t in deposits)
    assert not any(is_external_income(t) for t in deposits)             # bounced deposits are not income
    assert all(t.category == "CHEQUE_DEPOSIT" for t in deposits)         # cheque-credit classification kept


def test_rejection_without_cheque_evidence_is_not_called_a_cheque():
    t = txn("Debit trxn REJECT:999111:FUNDS INSUFFICIENT", debit="2500.00")
    incidents = run([t])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["instrument"] == "UNKNOWN" and inc["original"] is None and inc["confidence"] == 0.85
    assert t.category == "RETURNED_INSTRUMENT" and t.category_basis == "NARRATION_RULE"


def test_charge_only_return_has_no_amount_and_partial_charges_group():
    a = txn("Debit trxn ECSRTN1_0511257031207251006136", debit="58.68", month=11)
    b = txn("Debit trxn ECSRTN1_0511257031207251006136", debit="526.29", month=12)  # partial recovery, same return
    c = txn("Debit trxn ECSRTN1_0512257031207251006136", debit="590.00", month=12)
    incidents = run([a, b, c])
    result = analyze_bounces([a, b, c], incidents)
    assert result["confirmed_incident_count"] == 2 and result["charge_only_incident_count"] == 2
    assert result["returned_payment_amount"] is None and result["return_charges_total"] == D("1174.97")
    assert all(i["instrument"] == "NACH_ECS" and i["evidence"] == "RETURN_CHARGE_ONLY" for i in incidents)


def test_refund_pattern_is_possible_not_confirmed():
    d = txn("UPI/SAMPLE SHOP/shop@ybl/Order", debit="777.00", day=5)
    c = txn("UPI/REV/SAMPLE SHOP/Refund", credit="777.00", day=6)
    incidents = run([d, c])
    b = analyze_bounces([d, c], incidents)
    assert b["confirmed_incident_count"] == 0 and b["pattern_based_count"] == 1
    assert b["pattern_based_reversals"][0]["status"] == "POSSIBLE"


# --------------------------------------------------------------------- real statement regression

REAL = INPUT_DIR / "newreporticic.pdf"


@pytest.mark.skipif(not REAL.is_file(), reason="input/newreporticic.pdf not present")
def test_real_icici_statement_regression(analyzer):
    r = analyzer.analyze(file_path=REAL)
    o, b = r.overview, r.bounces
    # Extraction and reconciliation unchanged by the categorisation work.
    assert r.status == "VERIFIED" and r.validation.balance_chain_breaks == 0
    assert o["transaction_count"] == 154
    assert o["total_credits"] == D("762403.00") and o["total_debits"] == D("888144.42")
    assert b["confirmed_incident_count"] == 3 and b["returned_payment_amount"] == D("200000.00")
    assert b["return_charges_total"] == D("708.00") and b["deposited_instrument_return_count"] == 3
    assert not any(t.category == "UPI_TRANSFER" for t in r.transactions if "BSES" in t.narration)
    assert r.data_quality["low_confidence_categories"] == r.data_quality["channel_only_categories"]
