"""Statement metadata from non-table text: bank, IFSC, masked account number,
statement period, account type, holder-name candidates and printed summary figures.

Only text outside the transaction table is used, so counterparties' bank names
and IFSCs inside narrations cannot influence bank identification.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..extractors.base import Word
from ..normalizers.amounts import parse_signed_balance
from ..normalizers.dates import find_statement_period
from ..schemas import PrintedSummary, StatementMetadata
from ..utils.redaction import mask_account_number
from .layout import Line, cluster_lines, context_text, median_word_height, segments
from .registry import detect_bank_by_text, profile_for_ifsc

_IFSC_LABELLED = re.compile(r"IFS[C]?\s*(?:CODE)?\s*[:/\-]?\s*(?:MICR\s*)?[:\-]?\s*([A-Z]{4}0[A-Z0-9]{6})", re.I)
_IFSC = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
_ACCOUNT_NO = re.compile(
    r"(?:A/?C|ACCOUNT)\s*(?:NO\.?|NUMBER|#)\s*[:.\-]?\s*(?:OF\s+\w+\s+)?([0-9Xx*]{6,20})\b", re.I
)
_ACCOUNT_TYPE = re.compile(r"(?:ACCOUNT\s*TYPE|PRODUCT(?:\s*NAME)?|SCHEME)\s*[:\-]?\s*([A-Za-z][A-Za-z ()\-/]{1,40})", re.I)
_HOLDER_LABEL = re.compile(
    r"^(?:ACCOUNT\s*HOLDER\s*NAME|ACCOUNT\s*NAME|CUSTOMER\s*NAME|NAME\s+OF\s+(?:THE\s+)?CUSTOMER|WELCOME)\s*[:\-]?\s*(.*)$",
    re.I,
)
_HONORIFIC_LINE = re.compile(r"^(MR|MRS|MS|M/S|MISS|SHRI|SMT)\.?\s+([A-Z][A-Za-z .&]{2,60})$", re.I)
_NAME_ONLY = re.compile(r"^[A-Za-z][A-Za-z.&' ]{4,60}$")
_NOT_NAME = re.compile(
    r"\b(BANK|BRANCH|STATEMENT|ACCOUNT|ADDRESS|ROAD|NAGAR|INDIA|STREET|FLOOR|CITY|STATE|DISTRICT|DIST|"
    r"SAVINGS|CURRENT|NOMINEE|CUSTOMER|PERIOD|SUMMARY|TRANSACTION|BALANCE|BEHIND|NEAR|OPP|COLONY|SECTOR|"
    r"VILLAGE|TOWER|PLOT|LANE|MARG|HOUSE|LIMITED|PRIVATE)\b",
    re.I,
)


def extract_metadata(context: dict[int, list[Word]], page_count: int) -> StatementMetadata:
    meta = StatementMetadata(page_count=page_count)
    first_pages = [n for n in sorted(context) if n <= 2]
    head_text = "\n".join(context_text(context[n]) for n in first_pages)
    all_text = "\n".join(context_text(context[n]) for n in sorted(context))
    head_flat = re.sub(r"\s+", " ", head_text)

    m = _IFSC_LABELLED.search(head_flat)
    ifsc = m.group(1).upper() if m else None
    if ifsc is None:
        candidates = {c for c in _IFSC.findall(head_text)}
        prefixes = {c[:4] for c in candidates}
        if len(prefixes) == 1:
            ifsc = sorted(candidates)[0]
    meta.ifsc = ifsc

    profile = profile_for_ifsc(ifsc)
    if profile is not None:
        meta.bank_detection_method = "ifsc"
    else:
        profile = detect_bank_by_text(all_text)
        if profile is not None:
            meta.bank_detection_method = "statement_text"
    if profile is not None:
        meta.bank_name, meta.bank_code = profile.name, profile.code

    m = _ACCOUNT_NO.search(head_flat)
    if m:
        meta.account_number_masked = mask_account_number(m.group(1))

    m = _ACCOUNT_TYPE.search(head_text)
    if m:
        value = m.group(1).upper()
        if re.search(r"\b(SAVING|SB)\b|SAVINGS", value):
            meta.account_type = "SAVINGS"
        elif re.search(r"\bCURRENT\b|\bCA\b", value):
            meta.account_type = "CURRENT"
        elif re.search(r"\b(OD|OVERDRAFT|CC|CASH\s*CREDIT)\b", value):
            meta.account_type = "OVERDRAFT/CASH CREDIT"

    period = find_statement_period(head_text)
    if period:
        meta.period_start, meta.period_end = period
    meta.printed_summary = extract_printed_summary(context)
    meta.holder_name = labelled_holder_name(context.get(min(context)) if context else [])
    return meta


def labelled_holder_name(words: list[Word] | None) -> str | None:
    segs = segments(words or [])
    for i, seg in enumerate(segs):
        text = seg.text.strip()
        m = _HOLDER_LABEL.match(text)
        if m:
            value = m.group(1).strip(" :-")
            if not value and i + 1 < len(segs):
                value = segs[i + 1].text.strip(" :-")
            value = re.sub(r"^(MR|MRS|MS|M/S|MISS|SHRI|SMT)\.?\s+", "", value, flags=re.I)
            if value and not any(ch.isdigit() for ch in value) and len(value) <= 60:
                return re.sub(r"\s+", " ", value).upper()
    for seg in segs:
        m = _HONORIFIC_LINE.match(seg.text.strip())
        if m and not _NOT_NAME.search(m.group(2)):
            return re.sub(r"\s+", " ", m.group(2)).upper()
    return None


def holder_name_candidates(words: list[Word] | None) -> list[str]:
    """Unlabelled lines on page 1 that look like a person's or firm's name."""
    out = []
    for seg in segments(words or []):
        text = seg.text.strip()
        if _NAME_ONLY.match(text) and 2 <= len(text.split()) <= 5 and not _NOT_NAME.search(text):
            out.append(re.sub(r"\s+", " ", text).upper())
    return out


# ---------------------------------------------------------------- printed summary

_SUMMARY_LABELS: dict[str, tuple[str, ...]] = {
    "opening_balance": ("opening balance", "brought forward", "balance b/f", "b/f balance", "opening bal"),
    "closing_balance": ("closing balance", "closing bal", "balance c/f", "carried forward"),
    "total_debits": ("total debits", "total debit", "total withdrawals", "total withdrawal"),
    "total_credits": ("total credits", "total credit", "total deposits", "total deposit"),
    "debit_count": ("dr count", "debit count", "no. of debits", "no of debits"),
    "credit_count": ("cr count", "credit count", "no. of credits", "no of credits"),
}


def _find_phrase(line: Line, phrase: str) -> list[Word] | None:
    tokens = phrase.split()
    texts = [w.text.lower().strip(":") for w in line.words]
    for i in range(len(texts) - len(tokens) + 1):
        if texts[i:i + len(tokens)] == tokens:
            return line.words[i:i + len(tokens)]
    return None


def _value_for(label: list[Word], lines: list[Line], idx: int, integer: bool, h: float):
    if integer:
        def parse(t: str):
            return int(t) if re.fullmatch(r"\d{1,6}", t) else None
    else:
        def parse(t: str):
            return parse_signed_balance(t) if "." in t else None
    x0, x1 = label[0].x0, label[-1].x1
    # Same line, to the right; stop at the next textual label.
    for w in lines[idx].words:
        if w.x0 <= x1:
            continue
        if w.text in {":", "-", "₹", "Rs.", "INR"}:
            continue
        value = parse(w.text)
        if value is not None:
            return value
        if re.search(r"[A-Za-z]{3,}", w.text) and not re.fullmatch(r"(cr|dr)\.?", w.text, re.I):
            break
    # Directly below, overlapping horizontally.
    for line in lines[idx + 1: idx + 5]:
        if line.top - lines[idx].bottom > 6 * h:
            break
        for w in line.words:
            if w.x1 >= x0 - h and w.x0 <= x1 + h:
                value = parse(w.text)
                if value is not None:
                    return value
    return None


def extract_printed_summary(context: dict[int, list[Word]]) -> PrintedSummary:
    summary = PrintedSummary()
    pages = sorted(context)
    for field_name, phrases in _SUMMARY_LABELS.items():
        integer = field_name.endswith("_count")
        # Opening figures come from the first pages, closing/totals preferably from the last pages.
        order = pages if field_name == "opening_balance" else list(reversed(pages))
        for page in order:
            words = context[page]
            if not words:
                continue
            lines = cluster_lines(words)
            h = median_word_height(words)
            found = None
            for idx, line in enumerate(lines):
                for phrase in phrases:
                    label = _find_phrase(line, phrase)
                    if label:
                        found = _value_for(label, lines, idx, integer, h)
                        if found is not None:
                            break
                if found is not None:
                    break
            if found is not None:
                value = found if integer else Decimal(found)
                setattr(summary, field_name, value)
                summary.pages[field_name] = page
                break
    return summary
