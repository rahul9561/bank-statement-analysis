"""Expense categorisation summaries and money-flow breakdowns."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..schemas import Transaction
from .categorize import CATEGORY_LABELS
from .common import group_key, pct, q2, total, txn_ref

CHANNEL_LABELS = {
    "UPI": "UPI", "NEFT": "NEFT", "IMPS": "IMPS", "RTGS": "RTGS", "ATM": "ATM / cash withdrawal",
    "CASH": "Cash deposit", "NACH": "NACH / ECS / auto-debit", "CARD": "Card / POS", "CHEQUE": "Cheque",
    "INTERNAL": "Internal / fund transfer", "CHARGES": "Bank charges", "INTEREST": "Interest", "OTHER": "Other",
}


def destination_type(t: Transaction) -> str:
    if t.is_self_transfer:
        return "SELF"
    if t.category == "ATM_WITHDRAWAL":
        return "CASH"
    return "THIRD_PARTY"


def by_channel(txns: list[Transaction], field: str) -> list[dict]:
    whole = total(getattr(t, field) for t in txns)
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        groups[t.channel].append(t)
    rows = []
    for channel, items in groups.items():
        amount = total(getattr(t, field) for t in items)
        rows.append({"channel": channel, "label": CHANNEL_LABELS.get(channel, channel), "count": len(items),
                     "amount": amount, "pct": pct(amount, whole)})
    return sorted(rows, key=lambda r: -r["amount"])


def analyze_expenses(txns: list[Transaction], months_covered: Decimal) -> dict:
    debits = [t for t in txns if t.debit > 0]
    credits = [t for t in txns if t.credit > 0]
    whole = total(t.debit for t in debits)
    months = months_covered if months_covered > 0 else Decimal(1)

    cats: dict[str, list[Transaction]] = defaultdict(list)
    for t in debits:
        cats[t.category].append(t)
    categories = []
    for cat, items in cats.items():
        amount = total(t.debit for t in items)
        categories.append({
            "category": cat,
            "label": CATEGORY_LABELS.get(cat, cat.title()),
            "amount": amount,
            "count": len(items),
            "pct_of_debits": pct(amount, whole),
            "monthly_average": q2(amount / months),
            "average_confidence": round(sum(t.category_confidence for t in items) / len(items), 2),
        })
    categories.sort(key=lambda c: -c["amount"])

    dests: dict[str, list[Transaction]] = defaultdict(list)
    for t in debits:
        dests[group_key(t)].append(t)
    destinations = []
    for key, items in sorted(dests.items(), key=lambda kv: -total(t.debit for t in kv[1]))[:10]:
        amount = total(t.debit for t in items)
        types = {destination_type(t) for t in items}
        destinations.append({"destination": key, "count": len(items), "amount": amount,
                             "pct_of_debits": pct(amount, whole),
                             "type": types.pop() if len(types) == 1 else "MIXED"})

    share = {c["category"]: c["pct_of_debits"] or Decimal(0) for c in categories}
    insights = []
    spending = [c for c in categories if c["category"] not in ("RETURNED_INSTRUMENT", "SELF_TRANSFER")]
    if spending:
        top = spending[0]
        insights.append(f"Largest outflow category: {top['label']} ({top['pct_of_debits']}% of debits).")
    if share.get("RETURNED_INSTRUMENT", 0):
        insights.append(f"{share['RETURNED_INSTRUMENT']}% of debits are reversals of returned (bounced) "
                        "instruments, not spending.")
    if share.get("EMI_LOAN", 0) + share.get("CREDIT_CARD_PAYMENT", 0) >= 10:
        insights.append(f"{q2(share.get('EMI_LOAN', 0) + share.get('CREDIT_CARD_PAYMENT', 0))}% of outflows go to "
                        "EMI, loan or credit-card repayments.")
    if share.get("ATM_WITHDRAWAL", 0) >= 10:
        insights.append(f"{share['ATM_WITHDRAWAL']}% of outflows are cash withdrawals, whose end use is not visible.")
    if share.get("SELF_TRANSFER", 0) >= 10:
        insights.append(f"{share['SELF_TRANSFER']}% of outflows are transfers to the holder's own accounts.")

    return {
        "total_debits": whole,
        "debit_count": len(debits),
        "average_monthly_debits": q2(whole / months),
        "categories": categories,
        "top_destinations": destinations,
        "outflow_modes": by_channel(debits, "debit"),
        "inflow_modes": by_channel(credits, "credit"),
        "largest_debits": [dict(txn_ref(t), counterparty=t.counterparty, category=t.category)
                           for t in sorted(debits, key=lambda t: -t.debit)[:10]],
        "insights": insights,
    }
