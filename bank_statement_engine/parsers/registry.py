"""Bank profiles and parser registry.

Bank identification uses statement content only (the IFSC printed next to its
label, then bank-name patterns in the header/footer) — never the file name.
Parsers are selected per bank; the generic table parser is the fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import StatementParser
from .generic import GenericTableParser


@dataclass(frozen=True)
class BankProfile:
    code: str
    name: str
    ifsc_prefixes: tuple[str, ...]
    patterns: tuple[str, ...] = field(default_factory=tuple)
    #: True when the layout has been verified against real sample statements.
    tested: bool = False

    def matches(self, text: str) -> int:
        return sum(len(re.findall(p, text, re.I)) for p in self.patterns)


BANK_PROFILES: tuple[BankProfile, ...] = (
    BankProfile("HDFC", "HDFC Bank", ("HDFC",), (r"\bHDFC\s+BANK\b", r"hdfcbank\.com"), True),
    BankProfile("AXIS", "Axis Bank", ("UTIB",), (r"\bAXIS\s+BANK\b", r"statement\s+of\s+axis\s+account"), True),
    BankProfile("ICICI", "ICICI Bank", ("ICIC",), (r"\bICICI\s+BANK\b", r"icici\.bank\.in", r"icicibank\.com"), True),
    BankProfile("SBI", "State Bank of India", ("SBIN",), (r"\bSTATE\s+BANK\s+OF\s+INDIA\b", r"\bsbi\.co\.in\b", r"onlinesbi"), True),
    BankProfile("KOTAK", "Kotak Mahindra Bank", ("KKBK",), (r"\bKOTAK\s+MAHINDRA\b", r"kotak\.com", r"\bKeya\s+Chatbot\b"), True),
    BankProfile("IDFC", "IDFC FIRST Bank", ("IDFB",), (r"\bIDFC\s+FIRST\s+BANK\b",), True),
    BankProfile("BOI", "Bank of India", ("BKID",), (r"(?<!STATE\s)\bBANK\s+OF\s+INDIA\b", r"bankofindia\.co\.in"), True),
    BankProfile("EQUITAS", "Equitas Small Finance Bank", ("ESFB",), (r"\bEQUITAS\s+SMALL\s+FINANCE\s+BANK\b", r"equitas\.bank\.in"), True),
    BankProfile("PNB", "Punjab National Bank", ("PUNB",), (r"\bPUNJAB\s+NATIONAL\s+BANK\b", r"pnbindia\.in"), True),
    BankProfile("UTKARSH", "Utkarsh Small Finance Bank", ("UTKS",), (r"\bUTKARSH\s+SMALL\s+FINANCE\s+BANK\b", r"utkarsh\.bank\.in"), True),
    BankProfile("BOB", "Bank of Baroda", ("BARB",), (r"\bBANK\s+OF\s+BARODA\b",)),
    BankProfile("CANARA", "Canara Bank", ("CNRB",), (r"\bCANARA\s+BANK\b",)),
    BankProfile("UNION", "Union Bank of India", ("UBIN",), (r"\bUNION\s+BANK\s+OF\s+INDIA\b",)),
    BankProfile("INDIAN", "Indian Bank", ("IDIB",), (r"(?<!SOUTH\s)\bINDIAN\s+BANK\b",)),
    BankProfile("YES", "Yes Bank", ("YESB",), (r"\bYES\s+BANK\b",)),
    BankProfile("INDUSIND", "IndusInd Bank", ("INDB",), (r"\bINDUSIND\s+BANK\b",)),
    BankProfile("CENTRAL", "Central Bank of India", ("CBIN",), (r"\bCENTRAL\s+BANK\s+OF\s+INDIA\b",)),
    BankProfile("IOB", "Indian Overseas Bank", ("IOBA",), (r"\bINDIAN\s+OVERSEAS\s+BANK\b",)),
    BankProfile("UCO", "UCO Bank", ("UCBA",), (r"\bUCO\s+BANK\b",)),
    BankProfile("BOM", "Bank of Maharashtra", ("MAHB",), (r"\bBANK\s+OF\s+MAHARASHTRA\b",)),
    BankProfile("PSB", "Punjab & Sind Bank", ("PSIB",), (r"\bPUNJAB\s+(&|AND)\s+SIND\s+BANK\b",)),
    BankProfile("IDBI", "IDBI Bank", ("IBKL",), (r"\bIDBI\s+BANK\b",)),
    BankProfile("FEDERAL", "Federal Bank", ("FDRL",), (r"\bFEDERAL\s+BANK\b",)),
    BankProfile("RBL", "RBL Bank", ("RATN",), (r"\bRBL\s+BANK\b",)),
    BankProfile("AU", "AU Small Finance Bank", ("AUBL",), (r"\bAU\s+SMALL\s+FINANCE\s+BANK\b",)),
    BankProfile("BANDHAN", "Bandhan Bank", ("BDBL",), (r"\bBANDHAN\s+BANK\b",)),
    BankProfile("KVB", "Karur Vysya Bank", ("KVBL",), (r"\bKARUR\s+VYSYA\s+BANK\b",)),
    BankProfile("SIB", "South Indian Bank", ("SIBL",), (r"\bSOUTH\s+INDIAN\s+BANK\b",)),
    BankProfile("UJJIVAN", "Ujjivan Small Finance Bank", ("UJVN",), (r"\bUJJIVAN\s+SMALL\s+FINANCE\s+BANK\b",)),
    BankProfile("JK", "Jammu & Kashmir Bank", ("JAKA",), (r"\bJ\s*&\s*K\s+BANK\b", r"\bJAMMU\s+(&|AND)\s+KASHMIR\s+BANK\b")),
)

_BY_PREFIX = {p: profile for profile in BANK_PROFILES for p in profile.ifsc_prefixes}
_BY_CODE = {profile.code: profile for profile in BANK_PROFILES}


def profile_for_ifsc(ifsc: str | None) -> BankProfile | None:
    return _BY_PREFIX.get(ifsc[:4].upper()) if ifsc else None


def profile_for_code(code: str | None) -> BankProfile | None:
    return _BY_CODE.get(code) if code else None


def detect_bank_by_text(text: str) -> BankProfile | None:
    scored = [(profile.matches(text), profile) for profile in BANK_PROFILES]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous
    return scored[0][1]


class ParserRegistry:
    """Maps bank codes to parser classes; falls back to the generic parser."""

    def __init__(self) -> None:
        self._parsers: list[type[StatementParser]] = []

    def register(self, parser_cls: type[StatementParser]) -> type[StatementParser]:
        self._parsers.append(parser_cls)
        return parser_cls

    def select(self, first_page, bank_code: str | None, **kwargs) -> StatementParser:
        for parser_cls in self._parsers:
            try:
                if parser_cls.detect(first_page, bank_code):
                    return parser_cls(**kwargs)
            except Exception:  # a faulty detector must never block the generic fallback
                continue
        return GenericTableParser(**kwargs)

    @property
    def parsers(self) -> list[type[StatementParser]]:
        return list(self._parsers)


#: Default registry. No bank-specific parser is currently required: all sample
#: layouts (including SBI, whose header is drawn as graphics) are handled by the
#: generic parser with data-driven column calibration/inference.
registry = ParserRegistry()
