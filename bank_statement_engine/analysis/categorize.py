"""Deterministic transaction categorisation.

Rules are ordered regular expressions over the normalised narration, optionally
restricted to a direction (DEBIT/CREDIT) and/or payment channel. The first
matching rule wins and its id and confidence are stored on the transaction.
Deployments can prepend their own rules via ``AnalysisConfig.extra_category_rules``.
No machine learning or external service is involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import AnalysisConfig
from ..normalizers.narration import detect_channel, extract_counterparty, normalize_narration
from ..schemas import CREDIT, DEBIT, Transaction

CATEGORY_LABELS = {
    "SALARY": "Salary (narration)",
    "PENSION": "Pension",
    "INTEREST": "Interest",
    "INVESTMENT_INCOME": "Investment income / redemption",
    "REFUND_REVERSAL": "Refund / reversal",
    "LOAN_DISBURSAL": "Loan disbursal",
    "GOVERNMENT_BENEFIT": "Government benefit / subsidy",
    "CASH_DEPOSIT": "Cash deposit",
    "SELF_TRANSFER": "Own-account transfer",
    "UPI_RECEIPT": "UPI receipt",
    "TRANSFER_IN": "Bank transfer in",
    "CHEQUE_DEPOSIT": "Cheque deposit",
    "EMI_LOAN": "EMI / loan repayment",
    "NACH_AUTO_DEBIT": "Auto-debit (purpose not stated)",
    "RETURNED_INSTRUMENT": "Returned cheque / payment reversal",
    "CREDIT_CARD_PAYMENT": "Credit card payment",
    "RENT": "Rent",
    "UTILITIES": "Utilities & bills",
    "INSURANCE": "Insurance",
    "INVESTMENT": "Investments",
    "TAX": "Taxes",
    "BANK_CHARGES": "Bank charges",
    "RETURN_CHARGES": "Return / bounce charges",
    "ATM_WITHDRAWAL": "ATM / cash withdrawal",
    "SHOPPING": "Shopping & groceries",
    "FOOD_DINING": "Food & dining",
    "FUEL_TRANSPORT": "Fuel, transport & travel",
    "HEALTHCARE": "Healthcare",
    "EDUCATION": "Education",
    "GAMING_BETTING": "Gaming / betting platforms",
    "UPI_MERCHANT": "UPI merchant payment",
    "UPI_TRANSFER": "UPI transfer to individuals",
    "CARD_SPEND": "Card spend",
    "TRANSFER_OUT": "Bank transfer out",
    "CHEQUE_PAYMENT": "Cheque payment",
    "OTHER": "Other",
}


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    pattern: re.Pattern[str]
    direction: str | None = None
    confidence: float = 0.85
    channels: tuple[str, ...] = ()
    #: What the category rests on (see CATEGORY_BASES); stored on the transaction.
    basis: str = "NARRATION_RULE"

    def matches(self, t: Transaction) -> bool:
        if self.direction and t.transaction_type != self.direction:
            return False
        if self.channels and t.channel not in self.channels:
            return False
        return bool(self.pattern.search(t.narration_normalized))


#: Evidence behind a category, from strongest to weakest. A payment channel (UPI, card, NACH, …)
#: alone never establishes purpose, so channel-only categories keep confidence <= 0.55.
CATEGORY_BASES = (
    "NARRATION_RULE",       # explicit wording, e.g. "SALARY", "EMI", "LIC OF INDIA", "RTN CHG"
    "MERCHANT_NAME",        # a known merchant/biller name, e.g. "SWIGGY", "BSES"
    "LINKED_EVENT",         # derived from linked transactions (e.g. a rejected cheque reversal)
    "SELF_TRANSFER_MATCH",  # holder-name / own-account match
    "CUSTOM_RULE",          # deployment-supplied rule
    "CHANNEL_ONLY",         # only the payment channel and direction are known
    "NONE",                 # nothing matched (category OTHER)
)


def _r(id_: str, category: str, pattern: str, direction: str | None = None, confidence: float = 0.85,
       channels: tuple[str, ...] = (), basis: str = "NARRATION_RULE") -> Rule:
    return Rule(id_, category, re.compile(pattern, re.I), direction, confidence, channels, basis)


#: Rejected / returned-unpaid instruments: the reversal of the instrument amount itself, as opposed
#: to the bank's return charge. Charge wording (CHG, FEE, …) excludes a narration.
REJECTION_PATTERN = (
    r"^(?!.*\b(CHG|CHGS|CHRG|CHRGS|CHARGES?|FEES?|PENAL|GST)\b).*"
    r"(\bREJECT(ED)?\b|RETURNED\s*UNPAID|\bUNPAID\b|DISHONOU?RED)"
)

RETURN_PATTERN = (
    r"ECSRTN|ECS\s*RTN|NACH\s*RTN|ACH\s*RTN|RTN\s*CHG|RETURN\s*CHG|CHQ\s*RET|CHEQUE\s*RET|I/W\s*RET|O/W\s*RET|"
    r"BOUNCE|INSUFFICIENT|INSUFF\s*BAL|FUNDS\s*INSUFF|DISHONOU?R|MANDATE\s*(RETURN|REJECT|FAIL)|"
    r"RETURN\s*CHARGES|RET\s*CHGS|EMI\s*BOUNCE|PENAL\s*CHARGES?\s*(FOR\s*)?(EMI|NACH|ECS)"
)
#: Merchant-name rules apply only to spends; NEFT/RTGS narrations contain branch
#: names (e.g. "...-HOMEOPATHY UNIVERSITY-") that would otherwise mislead them.
SPEND_CHANNELS = ("UPI", "CARD", "OTHER", "INTERNAL", "NACH")

P2M_PATTERN = (
    r"P2M|PAYTMQR|BHARATPE|\bQR\b|MERCHANT|MERCHA\b|RAZORPAY|\bPAYU\b|CCAVENUE|BILLDESK|CASHFREE|PINELABS|"
    r"EZETAP|MSWIPE|GPAY-?\d{6,}|\bPOS\b"
)

BUILTIN_RULES: tuple[Rule, ...] = (
    _r("returned_instrument_debit", "RETURNED_INSTRUMENT", REJECTION_PATTERN, DEBIT, 0.8),
    _r("return_charges", "RETURN_CHARGES", RETURN_PATTERN, DEBIT, 0.9),
    _r("reversal_credit", "REFUND_REVERSAL",
       r"\b(REFUND|REVERSAL|REVERSED|RVSL|REV\b|REV-|CASHBACK|CHARGEBACK|RETURNED|RETURN|BOUNCE)\b|/RET/|UPI/REV|UPI_RET|_RET_",
       CREDIT, 0.85),
    _r("salary_keyword", "SALARY", r"\b(SALARY|SALARIES|SAL\s+CR|SAL\s+FOR|PAYROLL|STIPEND)\b|SALARYFILE|\bSAL\b", CREDIT, 0.85),
    _r("pension_keyword", "PENSION", r"\bPENSION\b|\bPENSN\b", CREDIT, 0.9),
    _r("interest_credit", "INTEREST",
       r"\b(INT\.?\s*PD|INTEREST|INT\s*CR|INT\.COLL|CREDIT\s*INT|INT\s*PAID)\b|^INT\b|\bSB[-\s]*INT", CREDIT, 0.9),
    _r("government_benefit", "GOVERNMENT_BENEFIT",
       r"\bDBT\b|\bDBL\b|SUBSIDY|PM\s*KISAN|SCHOLARSHIP|\bNREGA\b|MGNREGA|LADLI|KALIA|RYTHU", CREDIT, 0.8),
    _r("loan_disbursal", "LOAN_DISBURSAL", r"\b(LOAN\s*DISB\w*|DISBURSE\w*|DISB)\b", CREDIT, 0.8),
    _r("investment_income", "INVESTMENT_INCOME",
       r"\b(DIVIDEND|DIV\b|REDEMPTION|REDEEM|MATURITY|MUTUAL\s*FUND|\bMF\b|ICCL|INDIAN\s*CLEARING|ZERODHA|GROWW|UPSTOX)\b|SWEEP\s*TRANSFER\s*FROM",
       CREDIT, 0.75),
    _r("bank_charges", "BANK_CHARGES",
       r"CHARGES?\b|\bCHGS?\b|\bCHRGS?\b|\bFEES?\b|\bAMC\b|SMS\s*ALERT|MIN\s*BAL|NON[\s-]*MAINT|MAINTENANCE|\bMAINT\b|"
       r"DCARDFEE|DEBIT\s*CARD\s*FEE|ANNUAL\s*FEE|\bMDR\b|SERVICE\s*CHARGE|CASH\s*HANDLING|\bGST\b.*\b(CHG|CHARGE|FEE)|"
       r"CONSOLIDATED\s*CHARGES|\bPENAL\b|LOCKER|SHORTFAL",
       DEBIT, 0.85),
    _r("atm_channel", "ATM_WITHDRAWAL", r".", DEBIT, 0.9, ("ATM",)),
    _r("cash_withdrawal_self", "ATM_WITHDRAWAL", r"\b(CASH\s*WDL|CASH\s*WITHDRAWAL|SELF\s*CHQ|TO\s*SELF\s*CASH|CASH\s*PAID)\b", DEBIT, 0.85),
    _r("cash_deposit_channel", "CASH_DEPOSIT", r".", CREDIT, 0.9, ("CASH",)),
    _r("atm_credit_reversal", "REFUND_REVERSAL", r".", CREDIT, 0.6, ("ATM",)),
    _r("credit_card_payment", "CREDIT_CARD_PAYMENT",
       r"CREDIT\s*CARD|\bCC\s*(PAY|PAYMENT|BILL)|^CC\s+\d|AUTOPAY\s*SI|CARD\s*BILL|VISACCPAY|\bPAVC\b|\bCRED\b|"
       r"CRED\s*CLUB|CREDCLUB|DREAMPLUG|SBI\s*CARD|\bAMEX\b|ONECARD|BOBCARD",
       DEBIT, 0.85),
    _r("emi_loan_keyword", "EMI_LOAN",
       r"\bEMI\b|EMI\d|\bLOAN\b|LN\s*REPAY|\bLNPY\b|REPAYMENT|HOUSING\s*FIN|BAJAJ\s*FIN|HDB\s*FIN|TATA\s*CAPITAL|"
       r"ADITYA\s*BIRLA\s*(HOUSING|FIN|CAP)|MUTHOOT|MANAPPURAM|FULLERTON|KREDITBEE|MONEYVIEW|LENDINGKART|"
       r"L\s*&\s*T\s*FIN|\bIIFL\b|CHOLAMANDALAM|SHRIRAM\s*(FIN|TRANSPORT)|MAHINDRA\s*FIN|PIRAMAL|POONAWALLA|"
       r"DMI\s*FIN|INDIABULLS|HERO\s*FINCORP|\bSMFG\b|\bFIBE\b|KISSHT|CASHE|TRUECREDITS|DECFIN|NAVI\s*FIN|"
       r"PAYSENSE|EARLYSALARY|LAZYPAY|SIMPL|ZESTMONEY|AXIS\s*FIN|IDFC\s*FIRST\s*BANK\s*LTD.*(EMI|LOAN)|FINANCE\s*LTD|FINSERV",
       DEBIT, 0.8),
    _r("insurance", "INSURANCE",
       r"INSURANCE|\bLIC\b|LIFE\s*INS|\bPOLICY\b|PREMIUM|PMSBY|PMJJBY|HDFC\s*ERGO|ICICI\s*LOMBARD|STAR\s*HEALTH|"
       r"MAX\s*LIFE|BAJAJ\s*ALLIANZ|TATA\s*AIA|SBI\s*LIFE|\bACKO\b|GO\s*DIGIT|NIVA\s*BUPA|CARE\s*HEALTH|POLICYBAZAAR",
       DEBIT, 0.85),
    _r("investment", "INVESTMENT",
       r"MUTUAL\s*FUND|\bMF\b|\bSIP\b|ZERODHA|GROWW|UPSTOX|ANGEL\s*(ONE|BROKING)|\bCDSL\b|\bNSDL\b|DEMAT|"
       r"SAFE\s*GOLD|SAFEGOLD|DIGI\s*GOLD|\bPPF\b|\bNPS\b|FIXED\s*DEP|RECURRING\s*DEP|ICCL|INDIAN\s*CLEARING|"
       r"SMALLCASE|KUVERA|PAYTM\s*MONEY|SWEEP\s*TRANSFER\s*TO|\bSGB\b|JAR\s*APP|\bJAR\b|\bBSE\b|BSE\s*STAR|NSE\s*CLEARING|"
       r"\b(LARGE|MID|SMALL|FLEXI|MULTI)\s*CAP\b|\bELSS\b|BLUECHIP|INDEX\s*FUND",
       DEBIT, 0.8),
    _r("rent", "RENT", r"\bRENT\b|HOUSE\s*RENT|NOBROKER|NESTAWAY|\bRENTAL\b", DEBIT, 0.75),
    _r("tax", "TAX", r"INCOME\s*TAX|\bTDS\b|\bCBDT\b|TIN\s*NSDL|TAX\s*PAYMENT|CHALLAN|PROFESSIONAL\s*TAX|ADVANCE\s*TAX|\bGSTN?\s*PAYMENT", DEBIT, 0.85),
    _r("utilities", "UTILITIES",
       r"ELECTRICITY|ELECTRIC|BESCOM|MSEDCL|MAHADISCOM|TATA\s*POWER|ADANI\s*(ELEC|GAS|POWER)|TORRENT\s*POWER|BSES\w*|INDRAPRAST\w*|\bIGL\b|MAHANAGAR\s*GAS|\bMGL\b|PIPED\s*GAS|"
       r"\bWATER\b|\bGAS\b|INDANE|HP\s*GAS|BHARAT\s*GAS|BROADBAND|\bJIO\b|AIRTEL|VODAFONE|\bVI\b|BSNL|RECHARGE|"
       r"\bDTH\b|TATA\s*PLAY|TATASKY|BILL\s*PAY|\bBBPS\b|POSTPAID|PREPAID|MOBIREC|EURONET|HEXACOM",
       DEBIT, 0.75, basis="MERCHANT_NAME"),
    _r("gaming_betting", "GAMING_BETTING",
       r"DREAM\s*11|DREAM11|\bMPL\b|RUMMY|POKER|\bBET\b|BETTING|CASINO|1XBET|PARIMATCH|\bSTAKE\b|WINZO|MY11CIRCLE|"
       r"JUNGLEE|\bA23\b|POKERBAAZI|ADDA52|HOWZAT|FANTASY|LOTTERY",
       None, 0.75, basis="MERCHANT_NAME"),
    _r("food_dining", "FOOD_DINING",
       r"SWIGGY|ZOMATO|RESTAURANT|\bHOTEL\b|\bCAFE\b|DOMINOS|PIZZA|MCDONALD|\bKFC\b|BURGER|STARBUCKS|DHABA|BAKERY|"
       r"SWEETS|MISTHAN|\bFOOD|EATS\b|BIRYANI|CANTEEN|\bTEA\b|\bCHAI\b|JUICE|HALDIRAM|BIKANERVALA|CHAAYOS",
       DEBIT, 0.7, SPEND_CHANNELS, basis="MERCHANT_NAME"),
    _r("fuel_transport", "FUEL_TRANSPORT",
       r"PETROL|DIESEL|\bFUEL|\bHPCL\b|\bBPCL\b|\bIOCL\b|INDIAN\s*OIL|BHARAT\s*PETROLEUM|HINDUSTAN\s*PETROLEUM|\bSHELL\b|"
       r"NAYARA|FASTAG|\bUBER\b|\bOLA\b|RAPIDO|IRCTC|\bMETRO\b|REDBUS|MAKEMYTRIP|GOIBIBO|INDIGO|AIR\s*INDIA|"
       r"SPICEJET|AKASA|CLEARTRIP|\bYATRA\b|PARKING|\bTOLL\b|FILLING\s*STATION|AUTO\s*FUEL",
       DEBIT, 0.75, SPEND_CHANNELS, basis="MERCHANT_NAME"),
    _r("healthcare", "HEALTHCARE",
       r"HOSPITAL|CLINIC|MEDICAL|MEDICOS|PHARMA|CHEMIST|APOLLO|\b1MG\b|PHARMEASY|NETMEDS|DIAGNOSTIC|PATHOLOGY|DENTAL|\bLAB\b",
       DEBIT, 0.7, SPEND_CHANNELS, basis="MERCHANT_NAME"),
    _r("education", "EDUCATION", r"SCHOOL|COLLEGE|UNIVERSITY|TUITION|\bFEES?\s*PAY|ACADEMY|BYJU|UNACADEMY|COACHING|EDUCATION",
       DEBIT, 0.7, SPEND_CHANNELS, basis="MERCHANT_NAME"),
    _r("shopping", "SHOPPING",
       r"AMAZON|FLIPKART|MYNTRA|\bAJIO\b|MEESHO|NYKAA|BIGBASKET|BLINKIT|ZEPTO|\bDMART\b|AVENUE\s*S|RELIANCE\s*(RETAIL|SMART|TRENDS|DIGITAL)|"
       r"JIOMART|INSTAMART|GROFERS|TATA\s*CLIQ|SNAPDEAL|SHOPPERS|LIFESTYLE|PANTALOONS|DECATHLON|\bIKEA\b|CROMA|"
       r"VIJAY\s*SALES|SUPERMARKET|SUPER\s*MARKET|\bMART\b|\bSTORES?\b|KIRANA|GENERAL\s*STORE|"
       r"GARMENTS|FASHION|FOOTWEAR|ELECTRONICS|JEWELL",
       DEBIT, 0.6, SPEND_CHANNELS, basis="MERCHANT_NAME"),
    # A mandate debit to a lender-like payee (bank, NBFC, "FINANCIAL SERVICES") is a probable EMI.
    _r("nach_lender_name", "EMI_LOAN",
       r"FINANCIAL\s*SERV|\bFINANCE\b|FINSERV|FINCORP|FINVEST|\bCAPITAL\b|\bLOANS?\b|\bBANK\b|\bNBFC\b|HOUSING",
       DEBIT, 0.65, ("NACH",)),
    # Any other mandate debit: the purpose is not stated. Not labelled EMI; the EMI analysis
    # still reports recurring ones as *possible* obligations.
    _r("nach_debit_unspecified", "NACH_AUTO_DEBIT", r".", DEBIT, 0.3, ("NACH",), basis="CHANNEL_ONLY"),
)


def _channel_fallback(t: Transaction) -> tuple[str, str, float]:
    n = t.narration_normalized
    if t.transaction_type == CREDIT:
        if t.channel == "UPI":
            return "UPI_RECEIPT", "channel:upi_credit", 0.5
        if t.channel in ("NEFT", "RTGS", "IMPS", "INTERNAL", "NACH"):
            return "TRANSFER_IN", f"channel:{t.channel.lower()}_credit", 0.5
        if t.channel == "CHEQUE":
            return "CHEQUE_DEPOSIT", "channel:cheque_credit", 0.5
        return "OTHER", "fallback:other", 0.0
    if t.channel == "UPI":
        if re.search(P2M_PATTERN, n):
            return "UPI_MERCHANT", "channel:upi_p2m", 0.55
        return "UPI_TRANSFER", "channel:upi_p2p", 0.5
    if t.channel == "CARD":
        return "CARD_SPEND", "channel:card", 0.55
    if t.channel in ("NEFT", "RTGS", "IMPS", "INTERNAL"):
        return "TRANSFER_OUT", f"channel:{t.channel.lower()}_debit", 0.5
    if t.channel == "CHEQUE":
        return "CHEQUE_PAYMENT", "channel:cheque_debit", 0.5
    if t.channel == "CHARGES":
        return "BANK_CHARGES", "channel:charges", 0.6
    return "OTHER", "fallback:other", 0.0


def compile_extra_rules(extra: list[dict]) -> list[Rule]:
    rules = []
    for i, spec in enumerate(extra or []):
        category = spec["category"].upper()
        if category not in CATEGORY_LABELS:
            CATEGORY_LABELS[category] = spec.get("label", category.replace("_", " ").title())
        rules.append(_r(spec.get("id", f"custom_{i}"), category, spec["pattern"], spec.get("direction"),
                        float(spec.get("confidence", 0.9)), tuple(spec.get("channels", ())), basis="CUSTOM_RULE"))
    return rules


# ---------------------------------------------------------------- self transfers

_NAME_TOKEN = re.compile(r"[A-Z]{2,}")
_HONORIFICS = {"MR", "MRS", "MS", "MISS", "DR", "SHRI", "SMT", "KUM", "M/S"}
_SELF_NARRATION = re.compile(r"\bSELF\s*(TRANSFER|TRF|A/?C)\b|\bOWN\s*(A/?C|ACCOUNT)\b|\bTO\s*SELF\b|\bFROM\s*SELF\b|SWEEP", re.I)


def _tokens(name: str) -> list[str]:
    return [t for t in _NAME_TOKEN.findall(name.upper()) if t not in _HONORIFICS]


def name_matches(candidate: str | None, holder: str) -> bool:
    """True when ``candidate`` plausibly names ``holder`` (handles bank truncation).

    Requires two matching name tokens (or the only token of a one-word name).
    A shared surname alone is not enough, so relatives are not treated as "self".
    """
    if not candidate:
        return False
    ct, ht = _tokens(candidate), _tokens(holder)
    if not ct or not ht:
        return False

    def tok_match(h: str) -> bool:
        return any(c == h or (len(c) >= 4 and h.startswith(c)) or (len(h) >= 4 and c.startswith(h)) for c in ct)

    hits = sum(1 for h in ht if tok_match(h))
    return hits >= 2 or (len(ht) == 1 and hits == 1)


def pick_holder_name(candidates: list[str], txns: list[Transaction], min_hits: int = 3) -> str | None:
    """Choose the page-1 name candidate that recurs as a counterparty (own-account transfers)."""
    best, best_hits = None, 0
    for cand in candidates:
        hits = sum(1 for t in txns if name_matches(t.counterparty, cand))
        if hits > best_hits:
            best, best_hits = cand, hits
    return best if best_hits >= min_hits else None


def enrich_transactions(txns: list[Transaction], config: AnalysisConfig) -> None:
    """Fill narration_normalized, channel and counterparty (no categories yet)."""
    for t in txns:
        t.narration_normalized = normalize_narration(t.narration)
        t.channel = detect_channel(t.narration_normalized)
        t.counterparty = extract_counterparty(t.narration_normalized, t.channel)


def categorize(txns: list[Transaction], config: AnalysisConfig, holder_names: list[str]) -> None:
    rules = compile_extra_rules(config.extra_category_rules) + list(BUILTIN_RULES)
    hints = [h.upper() for h in config.own_account_hints if h]
    for t in txns:
        n = t.narration_normalized
        self_by_name = any(name_matches(t.counterparty, h) for h in holder_names)
        self_by_text = bool(_SELF_NARRATION.search(n)) and t.channel not in ("ATM", "CASH")
        self_by_hint = any(h in n for h in hints)
        t.is_self_transfer = self_by_name or self_by_text or self_by_hint
        for rule in rules:
            if rule.matches(t):
                # Return charges and reversals take precedence over the self-transfer label.
                if t.is_self_transfer and rule.category not in (
                        "RETURN_CHARGES", "RETURNED_INSTRUMENT", "REFUND_REVERSAL", "ATM_WITHDRAWAL", "CASH_DEPOSIT"):
                    break
                t.category, t.category_rule, t.category_confidence = rule.category, rule.id, rule.confidence
                t.category_basis = rule.basis
                break
        else:
            if not t.is_self_transfer:
                t.category, t.category_rule, t.category_confidence = _channel_fallback(t)
                t.category_basis = "NONE" if t.category == "OTHER" else "CHANNEL_ONLY"
        if t.is_self_transfer and t.category_rule is None:
            basis = "holder_name" if self_by_name else ("narration" if self_by_text else "own_account_hint")
            t.category, t.category_rule, t.category_confidence = "SELF_TRANSFER", f"self_transfer:{basis}", 0.75
            t.category_basis = "SELF_TRANSFER_MATCH"
        if t.counterparty is None:
            if t.category == "ATM_WITHDRAWAL":
                t.counterparty = "CASH WITHDRAWAL"
            elif t.category == "CASH_DEPOSIT":
                t.counterparty = "CASH DEPOSIT"
            elif t.channel == "ATM" and t.is_credit:
                t.counterparty = "ATM REVERSAL"
