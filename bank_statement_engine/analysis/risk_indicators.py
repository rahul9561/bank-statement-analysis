"""Transparent, rule-based financial indicators.

Each indicator states its rule, the evidence (transaction ids, dates, figures),
a confidence and its limitations. The engine deliberately produces no credit
score, eligibility percentage or lending decision: indicators are analytical
observations to support, not replace, human review.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import AnalysisConfig
from ..reports.formatting import inr, inr0
from ..schemas import VERIFIED, RiskIndicator, ValidationReport

HIGH, MEDIUM, LOW, INFO = "HIGH", "MEDIUM", "LOW", "INFO"
SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3}

GENERAL_LIMITATION = "Derived from bank statement data only; not verified against other documents."


def build_indicators(
    *,
    validation: ValidationReport,
    balances: dict,
    income: dict,
    expenses: dict,
    emi: dict,
    bounces: dict,
    cashflow: dict,
    monthly: list[dict],
    config: AnalysisConfig,
) -> list[RiskIndicator]:
    out: list[RiskIndicator] = []

    if validation.status != VERIFIED:
        errors = [i for i in validation.issues if i.severity == "ERROR"]
        out.append(RiskIndicator(
            "DATA_NOT_FULLY_RECONCILED", HIGH, "Statement data requires review",
            f"Extracted transactions did not fully reconcile (status {validation.status}); "
            f"{validation.balance_chain_breaks} balance-chain break(s) and {len(errors)} error-level issue(s).",
            evidence=[{"issue": i.code, "transaction_ids": i.transaction_ids[:4]} for i in errors[:8]],
            confidence=0.95,
            limitations=["All figures in this report may be affected until the issues are reviewed."],
        ))

    if balances.get("available"):
        ratio = Decimal(balances["days_below_low_threshold"]) / Decimal(max(1, balances["days_tracked"]))
        if ratio >= config.low_balance_days_ratio:
            out.append(RiskIndicator(
                "LOW_BALANCE_FREQUENCY", MEDIUM if ratio < Decimal("0.5") else HIGH, "Frequent low balances",
                f"End-of-day balance was below {inr0(config.low_balance_threshold)} on "
                f"{balances['days_below_low_threshold']} of {balances['days_tracked']} days "
                f"({balances['pct_days_below_low_threshold']}%).",
                evidence=[{"min_eod_balance": balances["min_eod_balance"], "min_eod_date": balances["min_eod_date"]}],
                confidence=0.9 if balances["reliable"] else 0.6,
                limitations=["Intraday balances may differ from end-of-day balances.", GENERAL_LIMITATION],
            ))
        if balances["negative_balance_days"]:
            out.append(RiskIndicator(
                "NEGATIVE_BALANCE", HIGH, "Negative end-of-day balance",
                f"The account closed the day with a negative balance on {balances['negative_balance_days']} day(s).",
                evidence=[{"first_negative_date": balances["first_negative_date"]}],
                confidence=0.85,
                limitations=["Overdraft or cash-credit accounts can legitimately run negative balances."],
            ))

    incidents = bounces.get("incidents") or []
    if incidents:
        # Returns of the holder's own payments (or of unknown direction) bear on repayment behaviour;
        # cheques the holder *received* that bounced reflect the drawer's funds, not the holder's.
        own = [i for i in incidents if i["direction"] != "DEPOSITED_INSTRUMENT_RETURNED"]
        received = len(incidents) - len(own)
        severity = (HIGH if len(own) >= 3 else MEDIUM) if own else LOW
        parts = []
        if own:
            parts.append(f"{len(own)} returned payment(s) made by the account holder or of unstated direction")
        if received:
            parts.append(f"{received} cheque(s)/payment(s) received by the holder returned unpaid "
                         f"({inr(bounces['deposited_instrument_return_amount'])})")
        out.append(RiskIndicator(
            "PAYMENT_RETURN_EVIDENCE", severity, "Returned payments / return charges",
            "; ".join(parts) + f". {bounces['return_charge_count']} return charge(s) totalling "
            f"{inr(bounces['return_charges_total'])}; each returned instrument is counted once.",
            evidence=[{"incident_id": i["incident_id"], "reference": i["reference"], "date": i["date"],
                       "direction": i["direction"], "returned_amount": i["returned_amount"]} for i in incidents[:10]],
            confidence=0.85,
            limitations=["A return charge alone does not identify which obligation was missed or prove a loan default.",
                         "A bounced cheque received from a third party reflects the drawer's funds."],
        ))
    if bounces["pattern_based_count"]:
        out.append(RiskIndicator(
            "POSSIBLE_REVERSED_PAYMENTS", LOW, "Possible reversed or failed payments",
            f"{bounces['pattern_based_count']} debit(s) were followed within "
            "3 days by a credit of the same amount from the same counterparty.",
            evidence=bounces["pattern_based_reversals"][:10],
            confidence=0.5,
            limitations=["Pattern-based; refunds and corrected payments produce the same pattern."],
        ))

    cash_w = cashflow.get("cash_withdrawal_pct_of_debits") or Decimal(0)
    if cash_w >= config.high_cash_share_pct:
        out.append(RiskIndicator(
            "HIGH_CASH_WITHDRAWALS", MEDIUM, "High share of cash withdrawals",
            f"Cash withdrawals are {cash_w}% of total debits ({inr(cashflow['cash_withdrawals'])} in "
            f"{cashflow['cash_withdrawal_count']} withdrawals).",
            confidence=0.85, limitations=["The end use of cash cannot be determined from the statement."],
        ))
    cash_d = cashflow.get("cash_deposit_pct_of_credits") or Decimal(0)
    if cash_d >= config.high_cash_share_pct:
        out.append(RiskIndicator(
            "HIGH_CASH_DEPOSITS", MEDIUM, "High share of cash deposits",
            f"Cash deposits are {cash_d}% of total credits ({inr(cashflow['cash_deposits'])}).",
            confidence=0.85, limitations=["The source of cash deposits cannot be verified from the statement."],
        ))

    stab = income["income_stability"]
    if stab["label"] == "VOLATILE":
        out.append(RiskIndicator(
            "INCOME_VOLATILITY", MEDIUM, "Volatile monthly inflows",
            f"Monthly external inflows vary widely (coefficient of variation {stab['cv_pct']}% over "
            f"{stab['months_used']} months).",
            confidence=0.7,
            limitations=["Seasonal businesses and irregular bonuses also produce high variability."],
        ))

    ratio = emi.get("estimated_emi_to_income_pct")
    if ratio is not None and ratio >= config.high_emi_to_income_pct:
        out.append(RiskIndicator(
            "HIGH_REPAYMENT_BURDEN", HIGH if ratio >= 75 else MEDIUM, "High recurring repayment burden",
            f"Estimated recurring EMI debits ({inr(emi['estimated_monthly_emi'])}/month) are {ratio}% of the "
            "estimated monthly income.",
            evidence=[{"lender_or_payee": o["lender_or_payee"], "monthly": o["estimated_monthly_amount"]}
                      for o in emi["recurring_obligations"][:6]],
            confidence=0.6,
            limitations=["Obligations and income are both estimates from statement patterns.",
                         "Loans repaid from other accounts are not visible."],
        ))

    full = [m for m in monthly if not m["partial_month"]] or monthly
    deficits = [m["month"] for m in full if m["net_cash_flow"] < 0]
    if len(full) >= 3 and len(deficits) * 2 > len(full):
        out.append(RiskIndicator(
            "FREQUENT_MONTHLY_DEFICITS", MEDIUM, "Outflows exceed inflows in most months",
            f"Net cash flow was negative in {len(deficits)} of {len(full)} full months.",
            evidence=[{"months": deficits}], confidence=0.8,
            limitations=["Drawing down accumulated savings also produces monthly deficits."],
        ))

    top = cashflow.get("top_source_share_pct")
    if top is not None and top >= config.high_inflow_concentration_pct and income["credit_count"] >= 10:
        out.append(RiskIndicator(
            "INFLOW_CONCENTRATION", LOW, "Inflows concentrated in one source",
            f"The largest external source accounts for {top}% of external inflows.",
            evidence=[{"source": s["source"], "pct": s["pct_of_credits"]} for s in income["top_income_sources"][:3]],
            confidence=0.7, limitations=["A single employer or main customer is common and not adverse by itself."],
        ))

    self_pct = cashflow.get("self_transfer_in_pct") or Decimal(0)
    if self_pct >= 30:
        out.append(RiskIndicator(
            "HIGH_SELF_TRANSFER_INFLOWS", LOW, "Large share of inflows from own accounts",
            f"{self_pct}% of credits appear to come from the holder's own accounts; these are excluded from income.",
            confidence=0.6, limitations=["Own-account matching is based on names in narrations (best effort)."],
        ))

    if cashflow["pass_through_count"]:
        out.append(RiskIndicator(
            "PASS_THROUGH_ACTIVITY", MEDIUM, "Large inflows moved out quickly",
            f"{cashflow['pass_through_count']} large credit(s) of at least {inr0(config.pass_through_min_amount)} were "
            "followed within one day by outflows of 80% or more of the amount.",
            evidence=cashflow["pass_through_events"][:10], confidence=0.6,
            limitations=["Business payments, property or vehicle purchases legitimately show this pattern.",
                         "This is not an indication of fraud by itself."],
        ))

    gaming = [c for c in expenses["categories"] if c["category"] == "GAMING_BETTING"]
    if gaming:
        g = gaming[0]
        out.append(RiskIndicator(
            "GAMING_BETTING_PAYMENTS", MEDIUM, "Payments to gaming / betting platforms",
            f"{g['count']} payment(s) totalling {inr(g['amount'])} matched gaming or betting platform names.",
            confidence=0.6, limitations=["Name-based matching; some platforms offer non-betting services."],
        ))

    if cashflow["large_transaction_count"]:
        out.append(RiskIndicator(
            "LARGE_TRANSACTIONS", INFO, "Transactions much larger than usual",
            f"{cashflow['large_transaction_count']} transaction(s) are at least "
            f"{config.large_txn_median_multiple}x the account's median transaction and {inr0(config.large_txn_min_amount)}+.",
            evidence=cashflow["large_transactions"][:10], confidence=0.9,
            limitations=["Unusual size alone does not indicate anything improper."],
        ))

    if cashflow["volume_changes"]:
        out.append(RiskIndicator(
            "TRANSACTION_VOLUME_CHANGE", INFO, "Sharp change in transaction volume",
            f"{len(cashflow['volume_changes'])} month-to-month change(s) of 60% or more in transaction count.",
            evidence=cashflow["volume_changes"][:6], confidence=0.8,
            limitations=["Volume changes can reflect seasonality or a change of primary account."],
        ))

    dupes = [i for i in validation.issues if i.code == "POSSIBLE_DUPLICATES"]
    if dupes:
        out.append(RiskIndicator(
            "DUPLICATE_LOOKING_TRANSACTIONS", INFO, "Duplicate-looking transactions",
            dupes[0].message, evidence=[{"transaction_ids": dupes[0].transaction_ids[:10]}], confidence=0.5,
            limitations=["Repeated identical payments are often legitimate."],
        ))

    out.sort(key=lambda r: SEVERITY_ORDER[r.severity])
    return out


def indicator_counts(indicators: list[RiskIndicator]) -> dict[str, int]:
    counts = {HIGH: 0, MEDIUM: 0, LOW: 0, INFO: 0}
    for r in indicators:
        counts[r.severity] += 1
    return counts
