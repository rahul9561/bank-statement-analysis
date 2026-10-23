"""Canonical data structures produced by the engine.

Money is always ``decimal.Decimal`` inside the engine. ``AnalysisResult.to_dict``
converts the whole result to JSON-compatible types using the strategy documented
in ``utils/serialization.py``: Decimal -> string with two decimals, date -> ISO
``YYYY-MM-DD``, datetime -> ISO 8601 with offset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .utils.serialization import to_jsonable

CREDIT = "CREDIT"
DEBIT = "DEBIT"
ZERO = Decimal("0.00")


@dataclass
class Transaction:
    id: str
    date: date
    narration: str
    transaction_type: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    balance: Decimal | None = None
    value_date: date | None = None
    reference_number: str | None = None
    currency: str = "INR"
    narration_normalized: str = ""
    category: str = "OTHER"
    category_confidence: float = 0.0
    category_rule: str | None = None
    #: Evidence the category rests on: NARRATION_RULE, MERCHANT_NAME, LINKED_EVENT,
    #: SELF_TRANSFER_MATCH, CUSTOM_RULE, CHANNEL_ONLY or NONE (see analysis.categorize).
    category_basis: str = "NONE"
    channel: str = "OTHER"
    counterparty: str | None = None
    is_self_transfer: bool = False
    source_page: int | None = None
    source_text: str | None = None
    #: Machine-readable notes such as "DIRECTION_INFERRED_FROM_BALANCE".
    flags: list[str] = field(default_factory=list)

    @property
    def amount(self) -> Decimal:
        return self.credit if self.transaction_type == CREDIT else self.debit

    @property
    def is_credit(self) -> bool:
        return self.transaction_type == CREDIT

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(
            {
                "id": self.id,
                "date": self.date,
                "value_date": self.value_date,
                "narration": self.narration,
                "narration_normalized": self.narration_normalized,
                "reference_number": self.reference_number,
                "transaction_type": self.transaction_type,
                "debit": self.debit,
                "credit": self.credit,
                "balance": self.balance,
                "currency": self.currency,
                "category": self.category,
                "category_confidence": self.category_confidence,
                "category_rule": self.category_rule,
                "category_basis": self.category_basis,
                "channel": self.channel,
                "counterparty": self.counterparty,
                "is_self_transfer": self.is_self_transfer,
                "source_page": self.source_page,
                "source_text": self.source_text,
                "flags": list(self.flags),
            }
        )


@dataclass
class PrintedSummary:
    """Figures printed by the bank on the statement itself (when present)."""

    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    total_debits: Decimal | None = None
    total_credits: Decimal | None = None
    debit_count: int | None = None
    credit_count: int | None = None
    #: Page on which each figure was printed.
    pages: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


@dataclass
class StatementMetadata:
    bank_name: str | None = None
    bank_code: str | None = None
    bank_detection_method: str | None = None
    ifsc: str | None = None
    account_number_masked: str | None = None
    account_type: str | None = None
    currency: str = "INR"
    period_start: date | None = None
    period_end: date | None = None
    #: Used internally for self-transfer recognition. Only serialised when explicitly allowed.
    holder_name: str | None = None
    page_count: int = 0
    extraction_method: str = "text"
    ocr_pages: list[int] = field(default_factory=list)
    parser: str | None = None
    reverse_chronological: bool = False
    printed_summary: PrintedSummary = field(default_factory=PrintedSummary)

    def to_dict(self, include_holder_name: bool = False) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k not in ("printed_summary", "holder_name")}
        if include_holder_name:
            data["holder_name"] = self.holder_name
        data["printed_summary"] = self.printed_summary.to_dict()
        return to_jsonable(data)


@dataclass
class ValidationIssue:
    code: str
    severity: str  # INFO | WARNING | ERROR
    message: str
    transaction_ids: list[str] = field(default_factory=list)
    page: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


@dataclass
class ValidationCheck:
    name: str
    passed: bool | None  # None = not applicable / insufficient data
    expected: Any = None
    actual: Any = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


VERIFIED = "VERIFIED"
REQUIRES_REVIEW = "REQUIRES_REVIEW"
FAILED = "FAILED"


@dataclass
class ValidationReport:
    status: str = REQUIRES_REVIEW
    reconciled: bool = False
    opening_balance: Decimal | None = None
    opening_balance_source: str | None = None  # "statement" | "derived" | None
    closing_balance: Decimal | None = None
    closing_balance_source: str | None = None
    computed_closing_balance: Decimal | None = None
    balance_links_checked: int = 0
    balance_chain_breaks: int = 0
    #: CONFIRMED_BY_PRINTED_BALANCES when printed opening and closing balances bound the chain,
    #: CHAIN_ONLY when completeness at a boundary rests on the running balance alone.
    completeness: str = "CHAIN_ONLY"
    checks: list[ValidationCheck] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(
            {
                "status": self.status,
                "reconciled": self.reconciled,
                "opening_balance": self.opening_balance,
                "opening_balance_source": self.opening_balance_source,
                "closing_balance": self.closing_balance,
                "closing_balance_source": self.closing_balance_source,
                "computed_closing_balance": self.computed_closing_balance,
                "balance_links_checked": self.balance_links_checked,
                "balance_chain_breaks": self.balance_chain_breaks,
                "completeness": self.completeness,
                "checks": [c.to_dict() for c in self.checks],
                "issues": [i.to_dict() for i in self.issues],
            }
        )


@dataclass
class RiskIndicator:
    code: str
    severity: str  # LOW | MEDIUM | HIGH | INFO
    title: str
    description: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


@dataclass
class AnalysisResult:
    report_id: str
    engine_version: str
    generated_at: datetime
    source_sha256: str
    metadata: StatementMetadata
    transactions: list[Transaction]
    validation: ValidationReport
    overview: dict[str, Any] = field(default_factory=dict)
    monthly: list[dict[str, Any]] = field(default_factory=list)
    balances: dict[str, Any] = field(default_factory=dict)
    income: dict[str, Any] = field(default_factory=dict)
    expenses: dict[str, Any] = field(default_factory=dict)
    emi: dict[str, Any] = field(default_factory=dict)
    bounces: dict[str, Any] = field(default_factory=dict)
    cashflow: dict[str, Any] = field(default_factory=dict)
    risk_indicators: list[RiskIndicator] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    include_holder_name: bool = False

    @property
    def status(self) -> str:
        return self.validation.status

    def summary(self) -> dict[str, Any]:
        """Compact summary suitable for API responses."""
        o = self.overview
        return to_jsonable(
            {
                "bank_name": self.metadata.bank_name,
                "period_start": o.get("period_start"),
                "period_end": o.get("period_end"),
                "opening_balance": o.get("opening_balance"),
                "closing_balance": o.get("closing_balance"),
                "total_credits": o.get("total_credits"),
                "total_debits": o.get("total_debits"),
                "net_cash_flow": o.get("net_cash_flow"),
                "transaction_count": o.get("transaction_count"),
                "months_covered": o.get("months_covered"),
            }
        )

    def to_dict(self, include_transactions: bool = True) -> dict[str, Any]:
        data = {
            "report_id": self.report_id,
            "engine_version": self.engine_version,
            "generated_at": self.generated_at,
            "status": self.status,
            "source_sha256": self.source_sha256,
            "metadata": self.metadata.to_dict(include_holder_name=self.include_holder_name),
            "summary": self.summary(),
            "overview": self.overview,
            "validation": self.validation.to_dict(),
            "monthly": self.monthly,
            "balances": self.balances,
            "income": self.income,
            "expenses": self.expenses,
            "emi": self.emi,
            "bounces": self.bounces,
            "cashflow": self.cashflow,
            "risk_indicators": [r.to_dict() for r in self.risk_indicators],
            "data_quality": self.data_quality,
            "limitations": self.limitations,
        }
        if include_transactions:
            data["transactions"] = [t.to_dict() for t in self.transactions]
        return to_jsonable(data)

    def to_json(self, include_transactions: bool = True, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.to_dict(include_transactions), indent=indent, ensure_ascii=False)
