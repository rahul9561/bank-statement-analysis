"""Income and recurring-credit analysis.

External inflows exclude own-account transfers, refunds/reversals, loan
disbursals and investment redemptions. Salary is never reported as verified: a
credit whose narration mentions salary is "salary-like (narration)". Recurring
inflows are estimated with transparent rules (same counterparty, similar
amount, at least ``recurring_min_months`` distinct months).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..config import AnalysisConfig
from ..schemas import Transaction
from .common import ZERO, is_external_income, amount_clusters, cv_pct, group_key, median, pct, q2, recurrence, total, txn_ref
from .monthly import full_months

def _bucket(txns: list[Transaction], whole: Decimal) -> dict:
    amount = total(t.credit for t in txns)
    return {"amount": amount, "count": len(txns), "pct_of_credits": pct(amount, whole)}


def source_type(t: Transaction) -> str:
    if "RETURNED_UNPAID" in t.flags:
        return "RETURNED"
    if t.is_self_transfer:
        return "SELF"
    if t.category == "CASH_DEPOSIT":
        return "CASH"
    if t.category in ("REFUND_REVERSAL", "LOAN_DISBURSAL", "INVESTMENT_INCOME", "INTEREST"):
        return t.category
    return "EXTERNAL"


def find_recurring(txns: list[Transaction], config: AnalysisConfig, total_months: int) -> list[dict]:
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        groups[group_key(t)].append(t)
    out = []
    for key, items in groups.items():
        for cluster in amount_clusters(items, config.recurring_amount_tolerance_pct):
            rec = recurrence(cluster)
            if rec["month_count"] < config.recurring_min_months or rec["median_amount"] < config.recurring_min_amount:
                continue
            # Recurring means roughly once a month, not many payments in a few months.
            if rec["occurrences"] > 2.5 * rec["month_count"]:
                continue
            rec.update({
                "source": key,
                "monthly_estimate": rec["median_amount"],
                "coverage_pct": pct(Decimal(rec["month_count"]), Decimal(max(total_months, 1))),
                "is_salary_labelled": any(t.category == "SALARY" for t in cluster),
                "evidence": [txn_ref(t) for t in sorted(cluster, key=lambda t: t.date)[:12]],
            })
            out.append(rec)
    out.sort(key=lambda r: -r["total"])
    return out


def analyze_income(
    txns: list[Transaction], monthly: list[dict], config: AnalysisConfig, months_covered: Decimal
) -> dict:
    credits = [t for t in txns if t.is_credit]
    whole = total(t.credit for t in credits)
    months = months_covered if months_covered > 0 else Decimal(1)
    external = [t for t in credits if is_external_income(t)]
    external_total = total(t.credit for t in external)
    cash = [t for t in credits if t.category == "CASH_DEPOSIT"]

    salary = [t for t in credits if t.category == "SALARY" and not t.is_self_transfer]
    salary_months: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in salary:
        salary_months[f"{t.date.year:04d}-{t.date.month:02d}"] += t.credit
    if salary and len(salary_months) >= config.recurring_min_months:
        salary_status = "SALARY_LIKE_RECURRING"
        salary_label = (f"Credits whose narration mentions salary were found in {len(salary_months)} months. "
                        "This indicates salary-like income; it is not verified employment income.")
    elif salary:
        salary_status = "SALARY_LIKE_OCCASIONAL"
        salary_label = "Salary-labelled credits appear in fewer months than required to treat them as regular."
    else:
        salary_status = "NONE_IDENTIFIED"
        salary_label = "No credits with salary-related narration were identified."

    total_months = len(monthly)
    recurring = find_recurring([t for t in external if t.category != "CASH_DEPOSIT"], config, total_months)

    fm = full_months(monthly)
    monthly_external = [m["external_credits"] for m in fm]
    stability_cv = cv_pct(monthly_external)
    if stability_cv is None:
        stability = "INSUFFICIENT_DATA"
    elif stability_cv < 25:
        stability = "STABLE"
    elif stability_cv < config.income_cv_unstable_pct:
        stability = "MODERATE"
    else:
        stability = "VOLATILE"

    if salary_status == "SALARY_LIKE_RECURRING":
        estimate = median(list(salary_months.values()))
        basis = "Median monthly total of salary-labelled credits."
    else:
        estimate = median(monthly_external)
        basis = ("Median monthly external inflow over full months (excludes own-account transfers, refunds, "
                 "loan disbursals and investment redemptions; includes cash deposits).")

    sources: dict[str, list[Transaction]] = defaultdict(list)
    for t in credits:
        sources[group_key(t)].append(t)
    top_sources = []
    for key, items in sorted(sources.items(), key=lambda kv: -total(t.credit for t in kv[1]))[:10]:
        amount = total(t.credit for t in items)
        types = {source_type(t) for t in items}
        top_sources.append({
            "source": key,
            "amount": amount,
            "count": len(items),
            "pct_of_credits": pct(amount, whole),
            "type": types.pop() if len(types) == 1 else "MIXED",
        })

    return {
        "total_credits": whole,
        "credit_count": len(credits),
        "gross_monthly_credit": q2(whole / months),
        "external_inflow_total": external_total,
        "average_monthly_external_inflow": q2(external_total / months),
        "estimated_monthly_income": estimate,
        "estimated_monthly_income_basis": basis,
        "income_stability": {"cv_pct": stability_cv, "label": stability, "months_used": len(fm)},
        "self_transfers_in": _bucket([t for t in credits if t.is_self_transfer], whole),
        "refunds_reversals": _bucket([t for t in credits if t.category == "REFUND_REVERSAL"], whole),
        "loan_disbursals": _bucket([t for t in credits if t.category == "LOAN_DISBURSAL"], whole),
        "investment_income": _bucket([t for t in credits if t.category == "INVESTMENT_INCOME"], whole),
        "interest": _bucket([t for t in credits if t.category == "INTEREST"], whole),
        "pension": _bucket([t for t in credits if t.category == "PENSION"], whole),
        "cash_deposits": _bucket(cash, whole),
        "salary_like": {
            "status": salary_status,
            "description": salary_label,
            "count": len(salary),
            "total": total(t.credit for t in salary),
            "months": sorted(salary_months),
            "monthly_median": median(list(salary_months.values())),
            "evidence": [txn_ref(t) for t in salary[:12]],
        },
        "recurring_credits": recurring[:10],
        "top_income_sources": top_sources,
    }
