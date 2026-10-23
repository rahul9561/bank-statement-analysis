"""Cash-flow and account-behaviour metrics.

Unusual activity is described, never labelled fraudulent: a large or fast-moving
transaction has many legitimate explanations.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from ..config import AnalysisConfig
from ..schemas import Transaction
from .common import ZERO, group_key, median, pct, q2, total, txn_ref
from .monthly import full_months

ROUND_TRIP_WINDOW_DAYS = 3


def analyze_cashflow(txns: list[Transaction], monthly: list[dict], config: AnalysisConfig) -> dict:
    credits = [t for t in txns if t.credit > 0]
    debits = [t for t in txns if t.debit > 0]
    total_c = total(t.credit for t in credits)
    total_d = total(t.debit for t in debits)

    surplus_months = [m["month"] for m in monthly if m["net_cash_flow"] > 0]
    deficit_months = [m["month"] for m in monthly if m["net_cash_flow"] < 0]

    # Large transactions relative to the account's own typical activity.
    large = []
    for side, items, field in (("CREDIT", credits, "credit"), ("DEBIT", debits, "debit")):
        typical = median([getattr(t, field) for t in items])
        if not typical:
            continue
        threshold = max(config.large_txn_min_amount, typical * config.large_txn_median_multiple)
        for t in items:
            if getattr(t, field) >= threshold:
                large.append(dict(txn_ref(t), counterparty=t.counterparty, threshold=q2(threshold),
                                  multiple_of_median=q2(getattr(t, field) / typical)))
    large.sort(key=lambda e: -e["amount"])

    # Inflow concentration (external sources only).
    sources: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in credits:
        if not t.is_self_transfer:
            sources[group_key(t)] += t.credit
    ranked = sorted(sources.values(), reverse=True)
    external_total = sum(ranked, ZERO)

    # Large inflow moved out within a day (pass-through), excluding own-account transfers.
    pass_through = []
    for c in credits:
        if c.credit < config.pass_through_min_amount or c.is_self_transfer:
            continue
        window = [d for d in debits if timedelta(0) <= d.date - c.date <= timedelta(days=1) and not d.is_self_transfer]
        moved = total(d.debit for d in window)
        if moved >= c.credit * Decimal("0.8"):
            pass_through.append({"credit": txn_ref(c), "outflow_within_1_day": moved,
                                 "outflow_pct": pct(moved, c.credit),
                                 "debit_ids": [d.id for d in window][:20]})

    # Possible round trips: money sent to and received back from the same counterparty quickly.
    round_trips = []
    by_cp: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.counterparty and t.channel not in ("ATM", "CASH"):
            by_cp[t.counterparty].append(t)
    for cp, items in by_cp.items():
        outs = [t for t in items if t.debit > 0]
        ins = [t for t in items if t.credit > 0]
        for o in outs:
            for i in ins:
                if abs((i.date - o.date).days) <= ROUND_TRIP_WINDOW_DAYS and o.debit >= Decimal("1000") and \
                        abs(i.credit - o.debit) <= o.debit * Decimal("0.05"):
                    round_trips.append({"counterparty": cp, "out": txn_ref(o), "in": txn_ref(i),
                                        "is_self": o.is_self_transfer or i.is_self_transfer})
                    break

    # Month-over-month changes in transaction volume.
    volume_changes = []
    fm = full_months(monthly)
    for prev, cur in zip(fm, fm[1:]):
        if prev["transaction_count"] >= 10:
            change = pct(Decimal(cur["transaction_count"] - prev["transaction_count"]), Decimal(prev["transaction_count"]))
            if change is not None and abs(change) >= 60:
                volume_changes.append({"from": prev["month"], "to": cur["month"], "change_pct": change,
                                       "from_count": prev["transaction_count"], "to_count": cur["transaction_count"]})

    self_in = total(t.credit for t in credits if t.is_self_transfer)
    self_out = total(t.debit for t in debits if t.is_self_transfer)
    cash_w = total(t.debit for t in debits if t.category == "ATM_WITHDRAWAL")
    cash_d = total(t.credit for t in credits if t.category == "CASH_DEPOSIT")
    return {
        "credit_to_debit_ratio": q2(total_c / total_d) if total_d else None,
        "net_cash_flow": total_c - total_d,
        "surplus_months": surplus_months,
        "deficit_months": deficit_months,
        "large_transactions": large[:25],
        "large_transaction_count": len(large),
        "top_source_share_pct": pct(ranked[0], external_total) if ranked else None,
        "top3_source_share_pct": pct(sum(ranked[:3], ZERO), external_total) if ranked else None,
        "self_transfer_in": self_in,
        "self_transfer_in_pct": pct(self_in, total_c),
        "self_transfer_out": self_out,
        "self_transfer_out_pct": pct(self_out, total_d),
        "cash_withdrawals": cash_w,
        "cash_withdrawal_pct_of_debits": pct(cash_w, total_d),
        "cash_withdrawal_count": sum(1 for t in debits if t.category == "ATM_WITHDRAWAL"),
        "cash_deposits": cash_d,
        "cash_deposit_pct_of_credits": pct(cash_d, total_c),
        "cash_deposit_count": sum(1 for t in credits if t.category == "CASH_DEPOSIT"),
        "pass_through_events": pass_through[:25],
        "pass_through_count": len(pass_through),
        "round_trip_events": round_trips[:25],
        "round_trip_count": len(round_trips),
        "volume_changes": volume_changes,
        "monthly_cash_trend": [{"month": m["month"], "cash_withdrawals": m["cash_withdrawals"],
                                "cash_deposits": m["cash_deposits"]} for m in monthly],
    }
