"""Engine configuration.

All thresholds used by extraction, validation and analysis live here so that a
deployment can tune them without code changes. ``EngineConfig.from_dict`` accepts
plain JSON-compatible values (strings or numbers for money).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from typing import Any


@dataclass
class OCRConfig:
    #: "auto" runs OCR only on pages without usable text, "off" never runs OCR,
    #: "force" OCRs every page.
    mode: str = "auto"
    language: str = "eng"
    dpi: int = 300
    #: Optional explicit path to the tesseract binary. When unset: $TESSERACT_CMD /
    #: $BSA_TESSERACT_CMD, then ``tesseract`` on PATH, then the standard install locations
    #: (``C:\Program Files\Tesseract-OCR\tesseract.exe``, ``/usr/bin/tesseract`` ...).
    tesseract_cmd: str | None = None
    #: Optional directory holding ``*.traineddata`` (passed as ``--tessdata-dir``).
    #: When unset Tesseract uses $TESSDATA_PREFIX or its built-in location.
    tessdata_dir: str | None = None
    #: Tesseract page segmentation mode (6 = uniform block of text, best for tables).
    psm: int = 6
    #: A page with fewer extractable characters than this is treated as image-only.
    min_text_chars: int = 40
    #: Maximum pages OCR'd per statement; further image-only pages are reported as unreadable.
    max_pages: int = 200
    page_timeout_seconds: int = 60
    #: Pages OCR'd in parallel (each runs its own tesseract process). 0 = automatic
    #: (half the CPU cores, at most 4).
    workers: int = 0
    #: Blank out vector table ruling lines in the page image before OCR. Borders are
    #: otherwise read as "|" / "[" and can swallow neighbouring digits.
    mask_ruling_lines: bool = True


# @dataclass
# class LimitsConfig:
#     max_file_size_bytes: int = 25 * 1024 * 1024
#     max_pages: int = 600
#     #: Wall-clock budget for one analysis run (extraction + parsing + analysis).
#     max_processing_seconds: int = 300
    
@dataclass
class LimitsConfig:
    max_file_size_bytes: int = 100 * 1024 * 1024
    max_pages: int = 600
    max_processing_seconds: int = 300


@dataclass
class AnalysisConfig:
    #: Maximum absolute difference (INR) tolerated when validating running balances.
    #: Text extraction yields exact paise, so the default is zero: any non-zero
    #: tolerance can hide a missed one-paisa row. Raise it only for OCR'd statements.
    balance_tolerance: Decimal = Decimal("0.00")
    #: Share of balance links allowed to break before the statement is reported as FAILED
    #: rather than REQUIRES_REVIEW.
    max_chain_break_ratio_for_review: Decimal = Decimal("0.05")
    low_balance_threshold: Decimal = Decimal("1000")
    low_balance_threshold_secondary: Decimal = Decimal("10000")
    #: Proportion of tracked days below ``low_balance_threshold`` that raises an indicator.
    low_balance_days_ratio: Decimal = Decimal("0.20")
    large_txn_min_amount: Decimal = Decimal("50000")
    large_txn_median_multiple: Decimal = Decimal("10")
    recurring_min_months: int = 3
    recurring_amount_tolerance_pct: Decimal = Decimal("20")
    #: Recurring inflows below this median amount are ignored (small personal transfers).
    recurring_min_amount: Decimal = Decimal("1000")
    emi_min_occurrences: int = 3
    emi_amount_tolerance_pct: Decimal = Decimal("5")
    #: Recurring repayment-like debits below this amount are not treated as EMIs.
    emi_min_amount: Decimal = Decimal("500")
    high_cash_share_pct: Decimal = Decimal("30")
    income_cv_unstable_pct: Decimal = Decimal("50")
    high_emi_to_income_pct: Decimal = Decimal("50")
    high_inflow_concentration_pct: Decimal = Decimal("60")
    pass_through_min_amount: Decimal = Decimal("50000")
    #: Names of the account holder(s). Used only to recognise self transfers.
    holder_names: list[str] = field(default_factory=list)
    #: Extra narration fragments that identify the customer's own accounts.
    own_account_hints: list[str] = field(default_factory=list)
    #: Attempt to read the holder name from the statement header for self-transfer matching.
    detect_holder_name: bool = True
    #: Extra categorisation rules evaluated before the built-in rules. Each item is a dict:
    #: {"id": "...", "category": "RENT", "pattern": "regex", "direction": "DEBIT"|"CREDIT"|None,
    #:  "confidence": 0.8}
    extra_category_rules: list[dict] = field(default_factory=list)
    #: Keep the raw row text on each transaction (useful for debugging; sensitive).
    keep_source_text: bool = False


@dataclass
class ReportConfig:
    title: str = "Bank Statement Analysis Report"
    subtitle: str = "Transaction analysis · Cash flow · Obligations · Risk indicators"
    #: Name shown in the header band and footer, e.g. your organisation. Optional.
    organization_name: str = ""
    #: Report template name (see reports/templates/*.json) or a path to a JSON template.
    template: str = "default"
    include_transactions: bool = True
    max_transactions_in_report: int = 400
    show_holder_name: bool = False
    #: Offset from UTC used for timestamps printed on the report (default IST).
    utc_offset_minutes: int = 330
    timezone_label: str = "IST"
    #: Explicit logo file. When unset, ``av-logo.png`` is looked up in the asset directories.
    logo_path: str | None = None
    show_logo: bool = True
    #: Directory holding ``av-logo.png`` and the Montserrat/Poppins/Roboto folders. When unset:
    #: $BSA_ASSETS_DIR, then <project>/assets, then the copy bundled in the package.
    assets_dir: str | None = None


@dataclass
class EngineConfig:
    ocr: OCRConfig = field(default_factory=OCRConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EngineConfig":
        cfg = cls()
        for section, values in (data or {}).items():
            target = getattr(cfg, section, None)
            if target is None or not is_dataclass(target) or not isinstance(values, dict):
                raise ValueError(f"Unknown configuration section: {section!r}")
            _apply(target, values)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        from .utils.serialization import to_jsonable

        return {
            name: to_jsonable({f.name: getattr(getattr(self, name), f.name) for f in fields(getattr(self, name))})
            for name in ("ocr", "limits", "analysis", "report")
        }


def _apply(target: Any, values: dict[str, Any]) -> None:
    known = {f.name: f for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"Unknown configuration option: {type(target).__name__}.{key}")
        current = getattr(target, key)
        if isinstance(current, Decimal) and value is not None:
            value = Decimal(str(value))
        elif isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, int) and value is not None:
            value = int(value)
        setattr(target, key, value)
