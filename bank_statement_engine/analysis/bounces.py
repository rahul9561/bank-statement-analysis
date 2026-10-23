"""Cheque return, ECS/NACH failure and reversal analysis.

Entries are classified by *role* from explicit narration:

* ``REJECTED_INSTRUMENT``     – reversal of a returned instrument's amount, e.g.
  ``REJECT:413915:FUNDS INSUFFICIENT`` (category ``RETURNED_INSTRUMENT``);
* ``RETURN_CHARGE``           – the bank's fee for a return, e.g. ``RTN CHG-413909/…``, ``ECSRTN…``;
* ``RETURNED_PAYMENT_CREDIT`` – a credit returning the holder's failed outgoing payment;
* ``RETURN_DEBIT``            – a debit with return/bounce wording whose role is not clear.

Entries that share an instrument/mandate reference form one **incident**: a rejected cheque
and its return charge are one bounce, not two, and the instrument amount is counted once.
Where the original instrument can be found (same reference and amount, opposite direction,
earlier date) it is linked, which also tells *whose* payment failed:

* ``DEPOSITED_INSTRUMENT_RETURNED`` – a cheque/payment the holder *received* was returned
  unpaid (the drawer's funds were insufficient), so the original credit is not income;
* ``OUTGOING_PAYMENT_RETURNED``     – a payment *made by* the holder was returned;
* ``UNKNOWN``                       – the statement does not show which.

Pattern-based reversals (same amount back within a few days with reversal wording) remain
*possible* events, and credits with reversal wording but no matching debit remain *unclear*.
A return charge alone shows that an instrument was returned; it does not identify which
obligation was missed and is not proof of a loan default.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from ..schemas import Transaction
from .categorize import REJECTION_PATTERN, RETURN_PATTERN
from .common import month_key, total, txn_ref

REVERSAL_WINDOW_DAYS = 3
ORIGINAL_LOOKBACK_DAYS = 60
_RETURN = re.compile(RETURN_PATTERN, re.I)
_REJECTION = re.compile(REJECTION_PATTERN, re.I)
_CHARGE_WORDING = re.compile(r"CHG|CHARGE|FEE|PENAL|ECSRTN|RTN", re.I)
_NACH = re.compile(r"\b(NACH|ACH|ECS|MANDATE)\b|ECSRTN|NACH\s*RTN|ACH\s*RTN", re.I)
_CHEQUE = re.compile(r"\b(CHQ|CHEQUE|I/W|O/W|CLG|CLEARING)\b|CHQ\s*RET", re.I)
_RETURN_CREDIT = re.compile(r"RETURN|RTN|REJECT|REVERS|REV\b|BOUNCE|UNPAID|DISHONOU?R", re.I)
#: Instrument / mandate references printed in return narrations (only these are used for linking).
_REFERENCE_PATTERNS = (
    re.compile(r"\b(?:REJECT(?:ED)?|RTN\s*CHG|RETURN\s*CHG|CHQ\s*RET\w*|CHEQUE\s*RET\w*|RET\s*CHGS?)\W{0,3}(\d{4,})"),
    re.compile(r"ECSRTN\d?_?(\d{8,})"),
    re.compile(r"\b(?:NACH|ACH|ECS)\s*RTN\W{0,3}([A-Z0-9]{6,})"),
    re.compile(r"\bBOUNCE\s+([A-Z0-9]{8,})"),
)
_REASON = re.compile(
    r"FUNDS\s*INSUFFICIENT|INSUFFICIENT\s*(FUNDS|BAL\w*)|INSUFF\s*BAL|EXCEEDS\s*ARRANGEMENT|ACCOUNT\s*CLOSED|"
    r"SIGNATURE\s*(DIFFERS|MISMATCH)|PAYMENT\s*STOPPED|STOP\s*PAYMENT|REFER\s*TO\s*DRAWER|MANDATE\s*(NOT\s*\w+|CANCELL?ED)",
    re.I,
)

DEPOSITED = "DEPOSITED_INSTRUMENT_RETURNED"
OUTGOING = "OUTGOING_PAYMENT_RETURNED"
UNKNOWN = "UNKNOWN"


def return_reference(narration: str) -> str | None:
    for pattern in _REFERENCE_PATTERNS:
        m = pattern.search(narration)
        if m:
            return m.group(1)
    return None


def entry_role(t: Transaction) -> str | None:
    n = t.narration_normalized
    if t.debit > 0:
        if t.category == "RETURNED_INSTRUMENT" or _REJECTION.search(n):
            return "REJECTED_INSTRUMENT"
        if _RETURN.search(n):
            return "RETURN_CHARGE" if _CHARGE_WORDING.search(n) else "RETURN_DEBIT"
    elif t.credit > 0 and _RETURN.search(n):
        return "RETURNED_PAYMENT_CREDIT"
    return None


def _entry(t: Transaction, role: str, ref: str | None) -> dict:
    n = t.narration_normalized
    return dict(txn_ref(t), kind=role, role=role, reference=ref, source_page=t.source_page, status="CONFIRMED",
                channel=t.channel, nach_ecs=t.channel == "NACH" or bool(_NACH.search(n)), cheque=bool(_CHEQUE.search(n)))


def _find_original(txns: list[Transaction], rejection: Transaction, ref: str | None, want_credit: bool):
    """Earlier transaction of the same amount and opposite direction that carries ``ref``."""
    if not ref:
        return None
    token = re.compile(rf"(?<![0-9A-Z]){re.escape(ref)}(?![0-9A-Z])")
    best = None
    for t in txns:
        if t.id == rejection.id or t.date > rejection.date or (rejection.date - t.date).days > ORIGINAL_LOOKBACK_DAYS:
            continue
        if t.is_credit != want_credit or t.amount != rejection.amount:
            continue
        if entry_role(t) is not None:
            continue
        if token.search(t.narration_normalized) or (t.reference_number and token.search(t.reference_number.upper())):
            best = t  # transactions are chronological: keep the latest match
    return best


def build_return_incidents(txns: list[Transaction]) -> tuple[list[dict], list[dict]]:
    """Return ``(incidents, entries)`` from explicit return narrations (pure; no mutation)."""
    entries: list[dict] = []
    by_txn: dict[str, Transaction] = {}
    for t in txns:
        role = entry_role(t)
        if role:
            entries.append(_entry(t, role, return_reference(t.narration_normalized)))
            by_txn[t.id] = t

    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[f"REF:{e['reference']}" if e["reference"] else f"TXN:{e['transaction_id']}"].append(e)

    incidents: list[dict] = []
    for key, items in groups.items():
        items.sort(key=lambda e: e["date"])
        rejections = [e for e in items if e["role"] == "REJECTED_INSTRUMENT"]
        credits = [e for e in items if e["role"] == "RETURNED_PAYMENT_CREDIT"]
        charges = [e for e in items if e["role"] == "RETURN_CHARGE"]
        ref = items[0]["reference"]
        original = None
        if rejections:
            original = _find_original(txns, by_txn[rejections[0]["transaction_id"]], ref, want_credit=True)
            direction, evidence = DEPOSITED, "REJECTED_ENTRY"
            returned = rejections[0]["amount"]
            confidence = 0.95 if original else 0.85
        elif credits:
            original = _find_original(txns, by_txn[credits[0]["transaction_id"]], ref, want_credit=False)
            direction, evidence = OUTGOING, "RETURNED_PAYMENT_CREDIT"
            returned = credits[0]["amount"]
            confidence = 0.9 if original else 0.8
        elif charges:
            n = " ".join(by_txn[e["transaction_id"]].narration_normalized for e in items)
            direction = OUTGOING if "INWARD" in n else UNKNOWN
            evidence, returned, confidence = "RETURN_CHARGE_ONLY", None, 0.75
        else:
            direction, evidence, returned, confidence = UNKNOWN, "RETURN_WORDED_DEBIT", None, 0.6
        texts = [by_txn[e["transaction_id"]].narration_normalized for e in items]
        if original is not None:
            texts.append(original.narration_normalized)
        joined = " ".join(texts)
        if _NACH.search(joined) or (original is not None and original.channel == "NACH"):
            instrument = "NACH_ECS"
        elif _CHEQUE.search(joined) or (original is not None and original.channel == "CHEQUE"):
            instrument = "CHEQUE"
        else:
            instrument = "UNKNOWN"
        reason = _REASON.search(joined)
        incidents.append({
            "incident_id": "",
            "reference": ref,
            "date": items[0]["date"],
            "instrument": instrument,
            "direction": direction,
            "status": "CONFIRMED",
            "evidence": evidence,
            "returned_amount": returned,
            "charges_total": total(e["amount"] for e in charges),
            "charge_count": len(charges),
            "reason": reason.group(0) if reason else None,
            "original": dict(txn_ref(original), source_page=original.source_page) if original is not None else None,
            "entries": items,
            "source_pages": sorted({e["source_page"] for e in items if e["source_page"]}),
            "confidence": confidence,
        })
    incidents.sort(key=lambda i: (i["date"], i["reference"] or ""))
    for n, inc in enumerate(incidents, 1):
        inc["incident_id"] = f"R{n:03d}"
        for e in inc["entries"]:
            e["incident_id"] = inc["incident_id"]
    return incidents, entries


def link_returns(txns: list[Transaction]) -> list[dict]:
    """Annotate transactions from return incidents (call after categorisation).

    * a rejected-instrument entry linked to its original keeps category ``RETURNED_INSTRUMENT``
      with basis ``LINKED_EVENT``;
    * the original instrument is flagged ``RETURNED_UNPAID`` so it is not counted as income
      (or, for an outgoing payment, as a completed repayment);
    * return-charge entries are flagged ``RETURN_CHARGE``.
    Amounts, dates and directions are never changed.
    """
    incidents, _ = build_return_incidents(txns)
    by_id = {t.id: t for t in txns}
    for inc in incidents:
        for e in inc["entries"]:
            t = by_id[e["transaction_id"]]
            if e["role"] == "RETURN_CHARGE":
                _flag(t, "RETURN_CHARGE")
            elif e["role"] == "REJECTED_INSTRUMENT":
                _flag(t, "RETURN_REJECTION")
                t.category = "RETURNED_INSTRUMENT"
                if inc["original"] is not None:
                    t.category_rule, t.category_basis, t.category_confidence = "returned_instrument_linked", "LINKED_EVENT", 0.9
        if inc["original"] is not None:
            _flag(by_id[inc["original"]["transaction_id"]], "RETURNED_UNPAID")
    return incidents


def _flag(t: Transaction, flag: str) -> None:
    if flag not in t.flags:
        t.flags.append(flag)


def analyze_bounces(txns: list[Transaction], incidents: list[dict] | None = None) -> dict:
    built, confirmed = build_return_incidents(txns)
    incidents = built if incidents is None else incidents

    # Pattern: a debit followed within a few days by a same-amount credit whose narration
    # indicates a reversal/return. Same-amount transfers back and forth between people are
    # common and are deliberately not counted. Entries already part of an incident are excluded.
    in_incident = {e["transaction_id"] for inc in incidents for e in inc["entries"]}
    in_incident |= {inc["original"]["transaction_id"] for inc in incidents if inc["original"]}
    patterns = []
    by_amount: dict = defaultdict(list)
    for t in txns:
        if t.credit > 0 and t.id not in in_incident and (
                t.category == "REFUND_REVERSAL" or _RETURN_CREDIT.search(t.narration_normalized)):
            by_amount[t.credit].append(t)
    used: set[str] = set()
    for d in (t for t in txns if t.debit > 0 and t.category != "SELF_TRANSFER" and t.id not in in_incident):
        for c in by_amount.get(d.debit, []):
            if c.id not in used and timedelta(0) <= c.date - d.date <= timedelta(days=REVERSAL_WINDOW_DAYS):
                used.add(c.id)
                patterns.append({"status": "POSSIBLE", "debit": txn_ref(d), "credit": txn_ref(c),
                                 "counterparty": d.counterparty,
                                 "same_counterparty": bool(c.counterparty and c.counterparty == d.counterparty)})
                break

    per_month: dict[str, int] = defaultdict(int)
    for inc in incidents:
        per_month[month_key(inc["date"])] += 1
    charges = [e for e in confirmed if e["role"] == "RETURN_CHARGE"]
    paired_credits = {p["credit"]["transaction_id"] for p in patterns}
    unclear = [dict(txn_ref(t), kind="REVERSAL_WORDING", status="UNCLEAR", channel=t.channel, source_page=t.source_page)
               for t in txns if t.credit > 0 and t.id not in in_incident and t.id not in paired_credits
               and _RETURN_CREDIT.search(t.narration_normalized)]
    known = [i for i in incidents if i["returned_amount"] is not None]
    deposited = [i for i in incidents if i["direction"] == DEPOSITED]
    outgoing = [i for i in incidents if i["direction"] == OUTGOING]
    return {
        # Incidents: one per returned instrument (entries sharing a reference are grouped).
        "incidents": incidents,
        "confirmed_incident_count": len(incidents),
        "rejected_instrument_count": sum(1 for i in incidents if i["evidence"] == "REJECTED_ENTRY"),
        "charge_only_incident_count": sum(1 for i in incidents if i["evidence"] == "RETURN_CHARGE_ONLY"),
        "deposited_instrument_return_count": len(deposited),
        "deposited_instrument_return_amount": total(i["returned_amount"] for i in deposited if i["returned_amount"]),
        "outgoing_payment_return_count": len(outgoing),
        "outgoing_payment_return_amount": total(i["returned_amount"] for i in outgoing if i["returned_amount"]),
        "incident_dates": sorted({i["date"] for i in incidents}),
        "nach_ecs_return_count": sum(1 for i in incidents if i["instrument"] == "NACH_ECS"),
        "cheque_return_count": sum(1 for i in incidents if i["instrument"] == "CHEQUE"),
        # Amount of returned instruments, counted once per incident; None when no amount is shown.
        "returned_payment_amount": total(i["returned_amount"] for i in known) if known else None,
        "returned_payment_count": len(known),
        # Entry-level figures (each statement line once).
        "confirmed_event_count": len(confirmed),
        "confirmed_events": confirmed[:100],
        "return_charge_count": len(charges),
        "return_charges_total": total(e["amount"] for e in charges),
        "nach_ecs_related_count": sum(1 for e in confirmed if e["nach_ecs"]),
        "unclear_events": unclear[:50],
        "unclear_count": len(unclear),
        "pattern_based_reversals": patterns[:50],
        "pattern_based_count": len(patterns),
        "events_by_month": dict(sorted(per_month.items())),
        "note": ("Incidents group the entries of one returned instrument by its reference, so a rejected cheque and "
                 "its return charge count as one bounce and the amount is counted once. Confirmed means explicit "
                 "return wording. Pattern-based reversals are indicative only. A return charge alone does not "
                 "establish which obligation was missed or prove a loan default."),
    }
