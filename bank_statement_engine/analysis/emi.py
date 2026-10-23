"""Loan / EMI repayment indicators.

Only debit evidence is available, so the module estimates *recurring repayment
obligations*. It never infers outstanding principal, interest rate or sanctioned
amount. A pattern counts as recurring when the same lender key and a similar
amount appear in at least ``emi_min_occurrences`` distinct months.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from ..config import AnalysisConfig
from ..schemas import Transaction
from .common import ZERO, amount_clusters, group_key, pct, recurrence, total, txn_ref

ACTIVE_WINDOW_DAYS = 45


def _evidence_level(category: str, basis: str | None) -> str:
    """How strongly the narration supports a loan EMI (never 'confirmed': a statement cannot verify a loan)."""
    if category == "CREDIT_CARD_PAYMENT":
        return "CARD_PAYMENT"
    return "POSSIBLE" if category == "NACH_AUTO_DEBIT" or basis == "nach_debit_unspecified" else "PROBABLE"


def analyze_emi(
    txns: list[Transaction],
    config: AnalysisConfig,
    statement_end,
    monthly_income: Decimal | None,
    income: dict | None = None,
) -> dict:
    """``income`` (the income analysis result) supplies the FOIR income basis and stability."""
    # Unspecified mandate debits are included as *possible* obligations; returned (failed) debits are not payments.
    candidates = [t for t in txns if t.debit > 0 and t.category in ("EMI_LOAN", "CREDIT_CARD_PAYMENT", "NACH_AUTO_DEBIT")
                  and "RETURNED_UNPAID" not in t.flags]
    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for t in candidates:
        groups[(t.category, group_key(t))].append(t)

    obligations, irregular = [], []
    for (category, key), items in groups.items():
        for cluster in amount_clusters(items, config.emi_amount_tolerance_pct):
            rec = recurrence(cluster)
            recurring = (rec["month_count"] >= config.emi_min_occurrences
                         and rec["occurrences"] <= 2 * rec["month_count"]
                         and rec["median_amount"] >= config.emi_min_amount)
            last = rec["last_date"]
            status = "SEEN_RECENTLY" if statement_end - last <= timedelta(days=ACTIVE_WINDOW_DAYS) else "NOT_SEEN_RECENTLY"
            entry = {
                "lender_or_payee": key,
                "type": "CREDIT_CARD" if category == "CREDIT_CARD_PAYMENT" else "LOAN_EMI",
                "basis": cluster[0].category_rule,
                "confidence": round(min(0.95, max(t.category_confidence for t in cluster) + (0.1 if recurring else 0)), 2),
                "estimated_monthly_amount": rec["median_amount"],
                "occurrences": rec["occurrences"],
                "months_seen": rec["month_count"],
                "total_paid": rec["total"],
                "first_date": rec["first_date"],
                "last_date": last,
                "typical_day_of_month": rec["typical_day_of_month"],
                "status": status,
                "evidence_level": _evidence_level(category, cluster[0].category_rule),
                "evidence": [txn_ref(t) for t in sorted(cluster, key=lambda t: t.date)[:12]],
            }
            (obligations if recurring else irregular).append(entry)
    obligations.sort(key=lambda e: -e["total_paid"])
    irregular.sort(key=lambda e: -e["total_paid"])

    active_loans = [o for o in obligations if o["type"] == "LOAN_EMI" and o["status"] == "SEEN_RECENTLY"]
    monthly_emi = total(o["estimated_monthly_amount"] for o in active_loans)
    card_payments = [t for t in candidates if t.category == "CREDIT_CARD_PAYMENT"]
    loan_debits = [t for t in candidates if t.category == "EMI_LOAN"]
    auto_debits = [t for t in candidates if t.category == "NACH_AUTO_DEBIT"]

    # FOIR (%) = monthly EMI / monthly income x 100, using the same EMI and income estimates as above.
    # Status makes "not detected" and "not computable" explicit instead of reporting 0 %.
    income = income or {}
    probable = [o for o in active_loans if o["evidence_level"] == "PROBABLE"]
    if not active_loans:
        foir_status, foir = "NO_RECURRING_EMI_DETECTED", None
    elif not monthly_income or monthly_income <= 0:
        foir_status, foir = "INCOME_UNAVAILABLE", None
    else:
        foir_status, foir = "CALCULATED", pct(monthly_emi, monthly_income)
    foir_probable = (pct(total(o["estimated_monthly_amount"] for o in probable), monthly_income)
                     if probable and monthly_income and monthly_income > 0 else None)
    stability = (income.get("income_stability") or {}).get("label")
    salary_based = (income.get("salary_like") or {}).get("status") == "SALARY_LIKE_RECURRING"
    if foir_status != "CALCULATED":
        foir_confidence = None
    elif len(probable) == len(active_loans) and stability in ("STABLE", "MODERATE"):
        foir_confidence = "HIGH" if salary_based and stability == "STABLE" else "MEDIUM"
    else:
        foir_confidence = "LOW"
    notes = ["FOIR = estimated monthly EMI ÷ estimated monthly income × 100."]
    if any(o["evidence_level"] == "POSSIBLE" for o in active_loans):
        notes.append("Includes recurring NACH/ECS debits with no lender or EMI wording (possible EMIs); "
                     "these may also be SIPs, insurance or subscriptions.")
    if stability == "VOLATILE":
        notes.append("Monthly income is volatile, so the income used for FOIR is uncertain.")
    notes.append("Only debits from this account are visible; obligations paid from other accounts are not included.")

    return {
        "emi_detected": bool(obligations),
        "recurring_obligations": obligations,
        "other_repayment_debits": irregular[:15],
        "active_recurring_loan_count": len(active_loans),
        "estimated_monthly_emi": monthly_emi if active_loans else ZERO,
        "monthly_emi_status": "DETECTED" if active_loans else "NONE_DETECTED",
        "estimated_emi_to_income_pct": foir,
        "foir_pct": foir,
        "foir_status": foir_status,
        "foir_probable_only_pct": foir_probable,
        "foir_confidence": foir_confidence,
        "foir_income_used": monthly_income,
        "foir_income_basis": income.get("estimated_monthly_income_basis"),
        "foir_notes": notes,
        "recurring_loan_obligation_count": sum(1 for o in obligations if o["type"] == "LOAN_EMI"),
        "loan_repayment_debits": {"count": len(loan_debits), "amount": total(t.debit for t in loan_debits)},
        "unspecified_auto_debits": {"count": len(auto_debits), "amount": total(t.debit for t in auto_debits)},
        "credit_card_payments": {"count": len(card_payments), "amount": total(t.debit for t in card_payments)},
        "total_repayment_debits": total(t.debit for t in candidates),
        "note": ("Obligations are estimated from recurring debits only. They are not verified loan liabilities; "
                 "outstanding principal, interest rate and sanctioned amount cannot be derived from a bank statement."),
    }

