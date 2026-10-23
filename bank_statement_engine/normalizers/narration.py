"""Narration normalisation, payment-channel detection and counterparty extraction.

Counterparty extraction is best-effort: banks truncate names (e.g. "BAIDYANAT")
and wrap narrations across lines. Results are used for grouping and are always
presented as "as printed / best effort", never as verified identities.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")

# Order matters: the first matching channel wins.
CHANNEL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ATM", re.compile(r"\b(ATM|NWD|ATW|ATL|CASH\s*WDL|CASH\s*WITHDRAWAL|CCWD)\b|^NWD-|^NFS/|^VAT/|^MAT/|ATM\s*WD", re.I)),
    ("CASH", re.compile(r"\bBY\s+CASH\b|CASH\s*DEP|\bCSH\s*DEP|\bCDM\b|CASH-?BNA|\bBNA\s*DEP|\bCASH\s+CR\b", re.I)),
    ("UPI", re.compile(r"^UPI[-/_ ]|/UPI/|\bUPIFIN|\bUPI\s*REF", re.I)),
    ("NACH", re.compile(r"\b(NACH|ACH|ECS|MANDATE|SI\s*DR|STANDING\s+INSTRUCTION)\b|^ACH\s*D-|ECSRTN|ACHDR", re.I)),
    ("UPI", re.compile(r"\bUPI\b", re.I)),
    ("IMPS", re.compile(r"\b(IMPS|MMT)\b|^IMPS|\bIMP\s+P2A", re.I)),
    ("NEFT", re.compile(r"\bNEFT\b|^NEFT|IBNEFT", re.I)),
    ("RTGS", re.compile(r"\bRTGS\b|^RTGS|IBRTGS", re.I)),
    ("CARD", re.compile(r"\b(POS|PCD|PCI|VPS|IPS|DEBIT\s*CARD|ECOM|VISA|MASTERCARD|RUPAY)\b", re.I)),
    ("CHEQUE", re.compile(r"\b(CHQ|CHEQUE|CLG|CLEARING|INWARD|OUTWARD|I/W|O/W|MICR)\b", re.I)),
    ("INTERNAL", re.compile(r"\b(IFT|TRF|TRANSFER|TPT|INF|INFT|FT|BIL|IB|MB|SWEEP\w*)\b", re.I)),
    ("CHARGES", re.compile(r"\b(CHGS?|CHRGS?|CHARGES?|FEE|GST|SMS\s*ALERT|AMC|MAINTENANCE)\b", re.I)),
    ("INTEREST", re.compile(r"\b(INT\.?\s*PD|INTEREST|INT\s*CR|CREDIT\s*INTEREST|INT\s*PAID)\b|SB[-\s]*INT", re.I)),
]

#: Words that carry no counterparty information (channel codes, filler, bank jargon).
_KEYWORDS = {
    "UPI", "DR", "CR", "P2A", "P2M", "P2P", "IMPS", "IMP", "NEFT", "RTGS", "MMT", "ACH", "ACHDR", "NACH", "ECS",
    "IFT", "IBNEFT", "IBRTGS", "INFT", "BIL", "BILLPAY", "BLKIFT", "XUTR", "EASY", "REV", "RECD", "RECH", "SAL", "TRXN",
    "TRFR", "TFR", "WDL", "DEP", "DEBIT", "CREDIT", "CMP", "MANDATE", "PAYMENT", "PAYMEN", "PAYMENTS", "PAY",
    "PAID", "SENT", "USING", "VIA", "FROM", "TO", "BY", "FOR", "REF", "NO", "NA", "IN", "OUT", "TRANSFER",
    "TRANSACTIO", "TRANSACTION", "REQ", "REQUEST", "COLLECT", "CLEARANCE", "CHEQUE", "CHQ", "CLG", "CLEA",
    "INWARD", "OUTWARD", "HEAD", "OFFICE", "AUTOPAY", "SI", "MAD", "CC", "NOT", "AC", "A/C", "VERIFIED",
    "VERIFICA", "REMARKS", "UTR", "CUST", "ATTN", "NORMAL", "SB", "CHRGS", "INCL", "GST", "AT", "ON", "QR",
    "INWARDS", "RTN", "RET", "INB", "MOB", "MB", "IB", "TPT", "FT", "BULK", "POS", "ECOM", "OF",
    # card schemes / card transaction codes (the merchant follows them)
    "VISA", "VPS", "IPS", "PCD", "PCI", "MASTERCARD", "RUPAY", "MAESTRO",
    # return / rejection wording (these rows have no counterparty of their own)
    "REJECT", "REJECTED", "CHG", "CHGS", "FUNDS", "INSUFFICIENT", "UNPAID", "BOUNCE",
}
_BANKISH = re.compile(
    r"^(STATE BAN.*|STATE BANK.*|BANK OF.*|BANK|HDFC( BANK)?|ICICI( BANK)?|SBIN|AXIS( BANK)?|KOTAK.*|YES ?B(ANK)?|"
    r"PUNB|PUNJAB.*|UNION.*|INDUSIND.*|FINO.*|AIRTEL PA.*|PAYTM PA.*|IDFC.*|KKBK|UTIB|YESB|ICIC|BARB|CNRB|UBIN|"
    r"BKID|IDIB|CBIN|IOBA|MAHB|UCBA|PSIB|IBKL|AUBL|ESFB|UTKS|FDRL|KVBL|SIBL|RATN|AIRP|JAKA|HSBC.*|CENTRAL B.*|"
    r"CANARA.*|INDIAN B.*|UCO BANK|.*BANK ?LTD|.*BANKLTD|.* BANK)$",
    re.I,
)
_HONORIFIC = re.compile(r"^(MR|MRS|MS|M/S|MISS|DR|SHRI|SMT|KUM)\.?\s+", re.I)
_VPA = re.compile(r"[\w.\-]+@[a-z]+", re.I)
_HAS_DIGIT = re.compile(r"\d")
_SEGMENT_SPLIT = re.compile(r"[/:*|_]+|\s-\s|-(?=\S)|(?<=\S)-")
_SBI_TAIL = re.compile(r"\s+\d{8,}\s+AT\s+\d{4,6}\s+[A-Z ]+$")


def normalize_narration(text: str) -> str:
    """Uppercase, collapse whitespace; keeps the original narration untouched elsewhere."""
    return _WS.sub(" ", (text or "").upper()).strip()


def detect_channel(narration: str) -> str:
    for name, pattern in CHANNEL_RULES:
        if pattern.search(narration):
            return name
    return "OTHER"


def clean_name(name: str, allow_bank: bool = False) -> str | None:
    """Return an upper-cased name, or None when ``name`` is not a plausible counterparty."""
    words = [w.strip(" .,;:()") for w in _WS.split(name.replace("_", " "))]
    words = [w for w in words if w and not _HAS_DIGIT.search(w) and "@" not in w and w.upper() not in _KEYWORDS]
    candidate = _HONORIFIC.sub("", " ".join(words)).strip(" -/.:,*")
    if not candidate or not any(len(w) >= 3 for w in candidate.split()):
        return None
    if not allow_bank and _BANKISH.match(candidate):
        return None
    return candidate.upper()


def extract_counterparty(narration: str, channel: str | None = None) -> str | None:
    """Best-effort counterparty name from a narration, or ``None``.

    Cash and ATM entries return ``None``; they are labelled from their category.
    """
    n = _SBI_TAIL.sub("", normalize_narration(narration))
    channel = channel or detect_channel(n)
    if channel in ("ATM", "CASH"):
        return None

    # Equitas style: "UPI REF NO 1195... P2P-VISU-PAYMENT FROM-..."
    m = re.search(r"\bP2[PM]-\s*([^-]+?)\s*-", n)
    if m and "UPI REF" in n:
        return clean_name(m.group(1))
    # HDFC style: "UPI-NAME-VPA@BANK-..."
    m = re.match(r"^UPI-(.+?)-[\w.]*@", n) or re.match(r"^UPI-([^-]+)-", n)
    if m:
        return clean_name(m.group(1))
    # ACH/NACH style: "ACH D- ADITYA BIRLA HOUSING-7021..."
    m = re.match(r"^(?:ACH|NACH|ECS)\s*[DC]?\s*-\s*([^-]+)", n)
    if m and clean_name(m.group(1), allow_bank=True):
        return clean_name(m.group(1), allow_bank=True)

    for segment in _SEGMENT_SPLIT.split(n):
        name = clean_name(segment, allow_bank=channel == "NACH")
        if name:
            return name
    return None


def extract_reference(narration: str) -> str | None:
    m = re.search(r"\b(\d{10,16})\b", narration or "")
    return m.group(1) if m else None
