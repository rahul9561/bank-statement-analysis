"""Public entry point: ``BankStatementAnalyzer``.

    from bank_statement_engine import BankStatementAnalyzer

    analyzer = BankStatementAnalyzer()
    result = analyzer.analyze(file_path="statement.pdf")
    analyzer.generate_pdf(result, output_path="analysis.pdf")
    payload = result.to_dict()          # JSON-compatible

The pipeline is synchronous, framework-independent and makes no network calls.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path

from . import __version__
from .analysis.balances import analyze_balances
from .analysis.bounces import analyze_bounces, link_returns
from .analysis.cashflow import analyze_cashflow
from .analysis.categorize import categorize, enrich_transactions, pick_holder_name
from .analysis.common import months_between, total
from .analysis.data_quality import assess_data_quality
from .analysis.emi import analyze_emi
from .analysis.expenses import analyze_expenses
from .analysis.income import analyze_income
from .analysis.monthly import analyze_monthly
from .analysis.reconciliation import reconcile
from .analysis.risk_indicators import build_indicators, indicator_counts
from .config import EngineConfig
from .exceptions import (
    BankStatementError,
    NoExtractableTextError,
    NoTransactionsFoundError,
    OCRUnavailableError,
    ProcessingTimeoutError,
    ReportGenerationError,
    UnsupportedStatementFormatError,
)
from .extractors.base import OCRProvider, PageContent
from .extractors.ocr import TesseractOCRProvider
from .extractors.pdf_text import DocumentExtractor
from .normalizers.transactions import build_transactions
from .parsers.base import ParsedStatement
from .parsers.layout import context_text
from .parsers.metadata import extract_metadata, holder_name_candidates
from .parsers.registry import ParserRegistry, detect_bank_by_text, profile_for_ifsc, registry as default_registry
from .schemas import AnalysisResult
from .utils.io import PdfSource, read_source, sha256_hex

logger = logging.getLogger("bank_statement_engine")

STANDARD_LIMITATIONS = [
    "This analysis is generated automatically from the uploaded bank statement only. It is not issued or "
    "endorsed by any bank and does not replace independent verification.",
    "Counterparty names are read from transaction narrations on a best-effort basis; banks often truncate them.",
    "Categories come from deterministic narration rules and are indicative, with a confidence per transaction.",
    "Salary-like income, recurring inflows and EMI obligations are estimates from transaction patterns, not "
    "verified income or loan liabilities.",
    "No credit score, eligibility or lending decision is produced. Indicators describe observed patterns and "
    "do not establish fraud, creditworthiness or default.",
]


class BankStatementAnalyzer:
    """Analyse Indian bank statement PDFs and render analysis reports.

    :param config: ``EngineConfig`` or a dict accepted by ``EngineConfig.from_dict``.
    :param ocr_provider: custom OCR engine; defaults to local Tesseract when OCR is enabled.
    :param parser_registry: custom parser registry (bank-specific parsers).
    """

    def __init__(
        self,
        config: EngineConfig | dict | None = None,
        *,
        ocr_provider: OCRProvider | None = None,
        parser_registry: ParserRegistry | None = None,
    ):
        self.config = config if isinstance(config, EngineConfig) else EngineConfig.from_dict(config)
        if ocr_provider is None and self.config.ocr.mode != "off":
            ocr_provider = TesseractOCRProvider(
                self.config.ocr.language, self.config.ocr.tesseract_cmd, self.config.ocr.page_timeout_seconds
            )
        self.ocr_provider = ocr_provider
        self.registry = parser_registry or default_registry

    # ------------------------------------------------------------------ public API
    def analyze(
        self,
        source: PdfSource | None = None,
        *,
        file_path: str | Path | None = None,
        password: str | None = None,
        holder_names: list[str] | None = None,
    ) -> AnalysisResult:
        """Analyse a statement given as a path, bytes or a binary file object.

        :param password: password for protected statements (never logged or stored).
        :param holder_names: account holder name(s), used only to recognise own-account transfers.
        :raises BankStatementError: subclasses describe the specific failure.
        """
        if source is None and file_path is None:
            raise ValueError("Provide a source (path, bytes or file object) or file_path.")
        started = time.monotonic()
        deadline = started + self.config.limits.max_processing_seconds
        data = read_source(file_path if file_path is not None else source, self.config.limits.max_file_size_bytes)
        digest = sha256_hex(data)

        parsed, extraction = self._extract_and_parse(data, password, deadline)
        result = self._analyze_parsed(parsed, extraction, digest, holder_names or [], deadline)
        logger.info(
            "Analysed statement report=%s bank=%s pages=%s txns=%s status=%s in %.2fs",
            result.report_id, result.metadata.bank_code, result.metadata.page_count,
            len(result.transactions), result.status, time.monotonic() - started,
        )
        return result

    def generate_pdf(self, result: AnalysisResult, output_path: str | Path | None = None) -> Path | bytes:
        """Render the PDF report. Returns the path written, or the PDF bytes when ``output_path`` is None."""
        from .reports.pdf_report import render_report

        try:
            pdf = render_report(result, self.config.report)
        except BankStatementError:
            raise
        except Exception as exc:
            logger.exception("Report generation failed for report=%s", result.report_id)
            raise ReportGenerationError() from exc
        if output_path is None:
            return pdf
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf)
        return path

    def analyze_to_pdf(self, source: PdfSource, output_path: str | Path, **kwargs) -> tuple[AnalysisResult, Path]:
        result = self.analyze(source, **kwargs)
        return result, self.generate_pdf(result, output_path)

    # ------------------------------------------------------------------ internals
    def _extract_and_parse(self, data: bytes, password: str | None, deadline: float) -> tuple[ParsedStatement, dict]:
        with DocumentExtractor(data, self.config, self.ocr_provider, password, deadline) as extractor:
            parsed, pages_seen = self._parse(extractor, force_ocr=False)
            method = "ocr" if extractor.ocr_pages and len(extractor.ocr_pages) == extractor.page_count else (
                "mixed" if extractor.ocr_pages else "text")
            ocr_ok = self.ocr_provider is not None and self.ocr_provider.is_available()
            if not parsed.rows and self.config.ocr.mode == "auto" and ocr_ok and not extractor.ocr_pages:
                logger.info("Text extraction yielded no transactions; retrying with OCR")
                parsed, pages_seen = self._parse(extractor, force_ocr=True)
                method = "ocr"
            if not parsed.rows:
                no_text = all(p.method == "none" for p in pages_seen)
                if no_text:
                    raise NoExtractableTextError() if ocr_ok or self.config.ocr.mode == "off" else OCRUnavailableError()
                if not parsed.header_found and not parsed.layout_inferred:
                    raise UnsupportedStatementFormatError()
                raise NoTransactionsFoundError()
            extraction = {
                "page_count": extractor.page_count,
                "ocr_pages": list(extractor.ocr_pages),
                "unreadable_pages": list(extractor.unreadable_pages),
                "blank_pages": list(extractor.blank_pages),
                "damaged_pages": list(extractor.damaged_pages),
                "row_census_shortfall": {p: c for p, c in parsed.row_census.items() if c[0] > c[1]},
                "method": method,
                "parser": parsed.parser,
                "layout_columns": parsed.layout_columns,
                "layout_inferred": parsed.layout_inferred,
                "row_modes": parsed.row_modes,
                "pages_with_rows": parsed.pages_with_rows,
                "table_pages_without_rows": parsed.table_pages_without_rows,
                "warnings": parsed.warnings,
            }
            return parsed, extraction

    def _parse(self, extractor: DocumentExtractor, force_ocr: bool) -> tuple[ParsedStatement, list[PageContent]]:
        seen: list[PageContent] = []

        def tracked(pages):
            for page in pages:
                # Keep only lightweight page facts, not words, to bound memory.
                seen.append(PageContent(page.number, page.width, page.height, method=page.method))
                yield page

        pages = tracked(extractor.pages(force_ocr=force_ocr))
        first = next(pages)
        bank_code = _quick_bank_code(first)
        parser = self.registry.select(first, bank_code, keep_source_text=self.config.analysis.keep_source_text)
        return parser.parse(chain([first], pages)), seen

    def _analyze_parsed(
        self, parsed: ParsedStatement, extraction: dict, digest: str, holder_names: list[str], deadline: float
    ) -> AnalysisResult:
        cfg = self.config.analysis
        meta = extract_metadata(parsed.context_words, parsed.page_count)
        meta.parser = parsed.parser
        meta.extraction_method = extraction["method"]
        meta.ocr_pages = extraction["ocr_pages"]
        sections = parsed.sections()
        if sections:
            meta.period_start = min(p[0] for _, p in sections)
            meta.period_end = max(p[1] for _, p in sections)

        txns, pre_issues, build_info = build_transactions(
            parsed.rows, cfg.balance_tolerance, layout_inferred=parsed.layout_inferred,
            keep_source_text=cfg.keep_source_text,
        )
        if not txns:
            raise NoTransactionsFoundError()
        meta.reverse_chronological = build_info["reverse_chronological"]
        enrich_transactions(txns, cfg)

        names = [n.upper() for n in (list(cfg.holder_names) + holder_names) if n and n.strip()]
        if not names and cfg.detect_holder_name:
            if meta.holder_name:
                names = [meta.holder_name]
            else:
                picked = pick_holder_name(holder_name_candidates(parsed.context_words.get(min(parsed.context_words, default=1))), txns)
                if picked:
                    meta.holder_name = picked
                    names = [picked]
        elif names:
            meta.holder_name = names[0]
        categorize(txns, cfg, names)
        # Link returned instruments to their charges and originals (flags only; amounts unchanged).
        return_incidents = link_returns(txns)
        _check_deadline(deadline)

        validation = reconcile(txns, meta, cfg, sections=sections, extraction=extraction, pre_issues=pre_issues)

        start = min(filter(None, [meta.period_start, txns[0].date]))
        end = max(filter(None, [meta.period_end, txns[-1].date]))
        months_covered = months_between(start, end)

        balances = analyze_balances(txns, validation, cfg, start, end)
        eod = [(e["date"], e["balance"]) for e in balances.get("eod_series", [])]
        monthly = analyze_monthly(txns, eod, validation.opening_balance, start, end)
        income = analyze_income(txns, monthly, cfg, months_covered)
        expenses = analyze_expenses(txns, months_covered)
        emi = analyze_emi(txns, cfg, end, income["estimated_monthly_income"], income=income)
        bounces = analyze_bounces(txns, return_incidents)
        cashflow = analyze_cashflow(txns, monthly, cfg)
        indicators = build_indicators(
            validation=validation, balances=balances, income=income, expenses=expenses, emi=emi,
            bounces=bounces, cashflow=cashflow, monthly=monthly, config=cfg,
        )
        _check_deadline(deadline)

        total_c = total(t.credit for t in txns)
        total_d = total(t.debit for t in txns)
        overview = {
            "bank_name": meta.bank_name,
            "account_number_masked": meta.account_number_masked,
            "account_type": meta.account_type,
            "period_start": start,
            "period_end": end,
            "period_source": "statement" if meta.period_start else "transactions",
            "first_transaction_date": txns[0].date,
            "last_transaction_date": txns[-1].date,
            "months_covered": months_covered,
            "calendar_months": len(monthly),
            "transaction_count": len(txns),
            "credit_count": sum(1 for t in txns if t.credit > 0),
            "debit_count": sum(1 for t in txns if t.debit > 0),
            "opening_balance": validation.opening_balance,
            "opening_balance_source": validation.opening_balance_source,
            "closing_balance": validation.closing_balance,
            "closing_balance_source": validation.closing_balance_source,
            "total_credits": total_c,
            "total_debits": total_d,
            "net_cash_flow": total_c - total_d,
            "average_daily_balance": balances.get("average_daily_balance") if balances.get("reliable") else None,
            "average_daily_balance_estimate": balances.get("average_daily_balance"),
            "min_observed_balance": balances.get("min_observed_balance"),
            "max_observed_balance": balances.get("max_observed_balance"),
            "reconciliation_status": validation.status,
            "indicator_counts": indicator_counts(indicators),
        }
        limitations = list(STANDARD_LIMITATIONS)
        if validation.status != "VERIFIED":
            limitations.insert(0, "The extracted transactions did not fully reconcile with the statement balances; "
                                  "treat all figures as requiring review.")
        if parsed.layout_inferred:
            limitations.append("The statement's table header is not machine-readable; columns were inferred "
                               "from the data and checked against the running balance.")
        if len(sections) > 1:
            limitations.append(f"The file combines {len(sections)} statement periods; they were analysed as one "
                               "continuous account history.")
        if extraction["ocr_pages"]:
            limitations.append(f"{len(extraction['ocr_pages'])} page(s) were read with OCR, which can misread "
                               "characters; OCR-derived values carry lower confidence.")

        offset = timezone(timedelta(minutes=self.config.report.utc_offset_minutes))
        now = datetime.now(offset)
        result = AnalysisResult(
            report_id=f"BSA-{now:%Y%m%d}-{uuid.uuid4().hex[:10].upper()}",
            engine_version=__version__,
            generated_at=now.replace(microsecond=0),
            source_sha256=digest,
            metadata=meta,
            transactions=txns,
            validation=validation,
            overview=overview,
            monthly=monthly,
            balances=balances,
            income=income,
            expenses=expenses,
            emi=emi,
            bounces=bounces,
            cashflow=cashflow,
            risk_indicators=indicators,
            data_quality=assess_data_quality(txns, validation, extraction, build_info),
            limitations=limitations,
            include_holder_name=self.config.report.show_holder_name,
        )
        return result


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise ProcessingTimeoutError()


_IFSC_NEAR_LABEL = re.compile(r"IFS[C]?\s*(?:CODE)?\s*[:/\-]?\s*([A-Z]{4}0[A-Z0-9]{6})", re.I)


def _quick_bank_code(first: PageContent) -> str | None:
    """Bank code from the first page (used only to choose a parser)."""
    text = context_text(first.words)
    m = _IFSC_NEAR_LABEL.search(re.sub(r"\s+", " ", text))
    profile = profile_for_ifsc(m.group(1)) if m else None
    profile = profile or detect_bank_by_text(text)
    return profile.code if profile else None


__all__ = ["BankStatementAnalyzer", "STANDARD_LIMITATIONS"]
